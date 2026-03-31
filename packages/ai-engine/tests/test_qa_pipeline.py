"""Unit tests for QA Pipeline – all 6 checkpoint layers (P0 improved)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from src.engine.qa_pipeline import QAPipeline
from src.api.models import GameRuntimeContract, GameSpec, GameplayContract, InputContract, QACheckError, StateContract
from src.services.llm_client import LLMResponseTruncatedError

qa = QAPipeline()

VALID_GAME = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Test Game</title>
</head>
<body>
<canvas id="gameCanvas"></canvas>
<script>
const canvas = document.getElementById('gameCanvas');
const ctx = canvas.getContext('2d');
canvas.width = 420;
canvas.height = 600;
const game = { score: 0, gameOver: false };
function restart() { game.score = 0; game.gameOver = false; }
canvas.addEventListener('touchstart', function(e) {
    if (game.gameOver) restart();
});
function update() {
    game.score += 1;
    if (game.score > 100) { game.gameOver = true; }
}
function render() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#22c55e';
    ctx.fillRect(180, 520, 60, 60);
}
function loop() { update(); render(); requestAnimationFrame(loop); }
requestAnimationFrame(loop);
</script>
</body>
</html>"""


class TestL1Syntax:
    def test_valid_html_passes(self):
        assert qa._check_l1_syntax(VALID_GAME) == []

    def test_missing_doctype(self):
        code = "<html><head><meta charset='UTF-8'></head><body></body></html>"
        errors = qa._check_l1_syntax(code)
        assert any("DOCTYPE" in e.message for e in errors)

    def test_missing_body(self):
        code = "<!DOCTYPE html><html><head><meta charset='UTF-8'></head></html>"
        errors = qa._check_l1_syntax(code)
        assert any("<body" in e.message for e in errors)

    def test_missing_charset(self):
        code = "<!DOCTYPE html><html><head></head><body></body></html>"
        errors = qa._check_l1_syntax(code)
        assert any("charset" in e.message for e in errors)

    def test_local_static_declaration_is_rejected_even_without_esprima(self):
        code = """<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body><script>
function update() {
    static lastSpawnTime = 0;
    lastSpawnTime += 1;
}
</script></body></html>"""
        with patch("src.engine.qa_pipeline.esprima", None):
            errors = qa._check_l1_syntax(code)
        assert any("local static declarations" in e.message for e in errors)

    def test_script_js_syntax_error_is_rejected_when_esprima_is_available(self):
        code = VALID_GAME.replace("game.score += 1;", "if (true) {")

        class _FakeEsprima:
            @staticmethod
            def parseScript(script_content, tolerant=False):
                assert tolerant is False
                if "if (true) {" in script_content:
                    raise Exception("Unexpected end of input")

        with patch("src.engine.qa_pipeline.esprima", _FakeEsprima()):
            errors = qa._check_l1_syntax(code)
        assert any("Unexpected end of input" in e.message for e in errors)


class TestL2Security:
    def test_clean_passes(self):
        assert qa._check_l2_security(VALID_GAME) == []

    def test_eval_detected(self):
        code = VALID_GAME.replace("game.score += 1;", "eval('bad');")
        assert any("eval" in e.message for e in qa._check_l2_security(code))

    def test_fetch_detected(self):
        code = VALID_GAME.replace("game.score += 1;", "fetch('http://x.com');")
        assert any("fetch" in e.message for e in qa._check_l2_security(code))

    def test_localstorage_detected(self):
        code = VALID_GAME.replace("game.score += 1;", "localStorage.clear();")
        assert any("localStorage" in e.message for e in qa._check_l2_security(code))

    def test_websocket_detected(self):
        code = VALID_GAME.replace("game.score += 1;", "new WebSocket('ws://x');")
        assert any("WebSocket" in e.message for e in qa._check_l2_security(code))

    def test_external_script_src_detected(self):
        code = VALID_GAME.replace(
            "</head>",
            '<script src="https://cdn.example.com/game.js"></script></head>',
        )
        assert any("external script src" in e.message for e in qa._check_l2_security(code))

    def test_external_css_asset_detected(self):
        code = VALID_GAME.replace(
            "</head>",
            "<style>body{background-image:url(https://cdn.example.com/bg.png);}</style></head>",
        )
        assert any("external CSS asset" in e.message for e in qa._check_l2_security(code))


class TestL3Startup:
    def test_valid_passes(self):
        errors, _ = qa._check_l3_startup(VALID_GAME)
        assert errors == []

    def test_missing_canvas_errors(self):
        code = VALID_GAME.replace('<canvas id="gameCanvas"></canvas>', '<div></div>')
        errors, _ = qa._check_l3_startup(code)
        assert any("canvas" in e.message.lower() for e in errors)

    def test_missing_getcontext_errors(self):
        code = VALID_GAME.replace("getContext", "REMOVED")
        errors, _ = qa._check_l3_startup(code)
        assert any("getContext" in e.message for e in errors)

    def test_canvas_size_not_set_errors(self):
        code = VALID_GAME.replace("canvas.width = 420;", "//noop").replace("canvas.height = 600;", "//noop")
        errors, _ = qa._check_l3_startup(code)
        assert any("width" in e.message.lower() or "0" in e.message for e in errors)

    def test_canvas_html_width_and_height_attributes_count_as_dimensions(self):
        code = (
            VALID_GAME
            .replace("<canvas id=\"gameCanvas\"></canvas>", "<canvas id=\"gameCanvas\" width=\"360\" height=\"640\"></canvas>")
            .replace("canvas.width = 420;", "// dimension comes from markup")
            .replace("canvas.height = 600;", "// dimension comes from markup")
        )
        errors, _ = qa._check_l3_startup(code)
        assert not any("renders at 0" in e.message.lower() for e in errors)

    def test_canvas_alias_width_and_height_assignment_is_accepted(self):
        code = (
            VALID_GAME
            .replace("const canvas = document.getElementById('gameCanvas');", "const C = document.getElementById('gameCanvas');")
            .replace("const ctx = canvas.getContext('2d');", "const ctx = C.getContext('2d');")
            .replace("canvas.width = 420;", "C.width = 420;")
            .replace("canvas.height = 600;", "C.height = 600;")
            .replace("canvas.addEventListener('touchstart', function(e) {", "C.addEventListener('touchstart', function(e) {")
            .replace("ctx.clearRect(0, 0, canvas.width, canvas.height);", "ctx.clearRect(0, 0, C.width, C.height);")
        )
        errors, _ = qa._check_l3_startup(code)
        assert not any("renders at 0×0" in e.message for e in errors)

    def test_no_game_loop_is_warning(self):
        code = VALID_GAME.replace("requestAnimationFrame", "//RAF")
        _, warnings = qa._check_l3_startup(code)
        assert any("game loop" in w.message.lower() for w in warnings)

    def test_missing_draw_commands_errors(self):
        code = VALID_GAME.replace(
            "function render() {\n    ctx.clearRect(0, 0, canvas.width, canvas.height);\n    ctx.fillStyle = '#22c55e';\n    ctx.fillRect(180, 520, 60, 60);\n}\n",
            "",
        )
        errors, _ = qa._check_l3_startup(code)
        assert any("blank screen" in e.message.lower() for e in errors)

    def test_array_fill_does_not_count_as_rendering(self):
        code = VALID_GAME.replace(
            "ctx.fillRect(180, 520, 60, 60);",
            "const samples = new Array(10).fill(0);",
        )
        errors, _ = qa._check_l3_startup(code)
        assert any("blank screen" in e.message.lower() for e in errors)

    def test_property_ctx_assignment_counts_as_rendering(self):
        code = VALID_GAME.replace(
            "const ctx = canvas.getContext('2d');",
            "this.ctx = canvas.getContext('2d');",
        ).replace(
            "ctx.clearRect(0, 0, canvas.width, canvas.height);\n    ctx.fillStyle = '#22c55e';\n    ctx.fillRect(180, 520, 60, 60);",
            "this.ctx.clearRect(0, 0, canvas.width, canvas.height);\n    this.ctx.fillStyle = '#22c55e';\n    this.ctx.fillRect(180, 520, 60, 60);",
        )
        errors, _ = qa._check_l3_startup(code)
        assert not any("blank screen" in e.message.lower() for e in errors)

    def test_unmatched_braces(self):
        code = VALID_GAME + "{" * 20
        errors, _ = qa._check_l3_startup(code)
        assert any("brace" in e.message.lower() for e in errors)


class TestL4Playability:
    def test_valid_passes(self):
        errors, _ = qa._check_l4_playability(VALID_GAME)
        assert errors == []

    def test_gameover_never_set_to_true(self):
        code = VALID_GAME.replace("game.gameOver = true;", "// not set")
        errors, _ = qa._check_l4_playability(code)
        assert any("terminal or completion state" in e.message.lower() for e in errors)

    def test_state_machine_alternative_accepted(self):
        code = VALID_GAME.replace("game.gameOver = true;", "gameState = 'gameover';")
        errors, _ = qa._check_l4_playability(code)
        assert not any("terminal or completion state" in e.message.lower() for e in errors)

    def test_enum_style_terminal_state_is_accepted(self):
        code = VALID_GAME.replace("game.gameOver = true;", "currentState = GAME_STATES.GAME_OVER;")
        errors, _ = qa._check_l4_playability(code)
        assert not any("terminal or completion state" in e.message.lower() for e in errors)

    def test_completion_state_constant_is_accepted(self):
        code = VALID_GAME.replace(
            "game.gameOver = true;",
            "const STATE_WIN = 'win'; currentState = STATE_WIN;",
        )
        errors, _ = qa._check_l4_playability(code)
        assert not any("terminal or completion state" in e.message.lower() for e in errors)

    def test_is_game_over_boolean_is_accepted(self):
        code = VALID_GAME.replace("game.gameOver = true;", "isGameOver = true;")
        errors, _ = qa._check_l4_playability(code)
        assert not any("terminal or completion state" in e.message.lower() for e in errors)

    def test_no_restart_is_warning(self):
        code = (
            VALID_GAME
            .replace("function restart() { game.score = 0; game.gameOver = false; }", "function doNothing() { return; }")
            .replace("if (game.gameOver) restart();", "if (game.gameOver) doNothing();")
        )
        _, warnings = qa._check_l4_playability(code)
        assert any("restart" in w.message.lower() for w in warnings)

    def test_score_not_incremented_warns(self):
        code = VALID_GAME.replace("game.score += 1;", "// no increment")
        _, warnings = qa._check_l4_playability(code)
        assert any("incremented" in w.message for w in warnings)

    def test_no_input_at_all_errors(self):
        code = VALID_GAME.replace(
            "canvas.addEventListener('touchstart', function(e) {\n    if (game.gameOver) restart();\n});",
            "// no input"
        )
        errors, _ = qa._check_l4_playability(code)
        assert any("input" in e.message.lower() for e in errors)

    def test_keyboard_only_warns_no_touch(self):
        code = VALID_GAME.replace(
            "canvas.addEventListener('touchstart', function(e) {\n    if (game.gameOver) restart();\n});",
            "window.addEventListener('keydown', function(e) {});"
        )
        errors, warnings = qa._check_l4_playability(code)
        input_errors = [e for e in errors if "input" in e.message.lower()]
        assert input_errors == []
        assert any("touch" in w.message.lower() for w in warnings)

    def test_pointer_events_count_as_interactive_mobile_input(self):
        code = VALID_GAME.replace(
            "canvas.addEventListener('touchstart', function(e) {\n    if (game.gameOver) restart();\n});",
            "canvas.addEventListener('pointerdown', function(e) { if (game.gameOver) restart(); });",
        )
        errors, warnings = qa._check_l4_playability(code)
        assert not any("input" in e.message.lower() for e in errors)
        assert not any("touch event handlers" in w.message.lower() for w in warnings)

    def test_property_input_handler_counts_as_interactive(self):
        code = VALID_GAME.replace(
            "canvas.addEventListener('touchstart', function(e) {\n    if (game.gameOver) restart();\n});",
            "canvas.ontouchstart = function(e) { if (game.gameOver) restart(); };",
        )
        errors, _ = qa._check_l4_playability(code)
        assert not any("input" in e.message.lower() for e in errors)

    def test_inline_onclick_counts_as_interactive(self):
        code = VALID_GAME.replace(
            "<canvas id=\"gameCanvas\"></canvas>",
            "<canvas id=\"gameCanvas\" onclick=\"restart()\"></canvas>",
        ).replace(
            "canvas.addEventListener('touchstart', function(e) {\n    if (game.gameOver) restart();\n});",
            "// click handler in markup",
        )
        errors, _ = qa._check_l4_playability(code)
        assert not any("input" in e.message.lower() for e in errors)

    def test_width_only_font_scaling_errors_for_responsive_mobile_layouts(self):
        code = VALID_GAME.replace(
            "<canvas id=\"gameCanvas\"></canvas>",
            "<canvas id=\"gameCanvas\"></canvas><style>canvas{width:100%;height:100%;}</style>",
        ).replace(
            "canvas.width = 420;\ncanvas.height = 600;",
            "let scaleX = 1;\nlet scaleY = 1;\nfunction resizeCanvas() {\n    canvas.width = window.innerWidth;\n    canvas.height = window.innerHeight;\n    scaleX = canvas.width / 360;\n    scaleY = canvas.height / 640;\n}\nwindow.addEventListener('resize', resizeCanvas);\nresizeCanvas();",
        ).replace(
            "ctx.fillStyle = '#22c55e';\n    ctx.fillRect(180, 520, 60, 60);",
            "ctx.fillStyle = '#22c55e';\n    ctx.font = `${18 * scaleX}px sans-serif`;\n    ctx.fillText('Score', 12, 24);\n    ctx.fillRect(180, 520, 60, 60);",
        )
        errors, _ = qa._check_l4_playability(code)
        assert any("width only" in e.message.lower() for e in errors)

    def test_short_edge_ui_scale_passes_mobile_layout_check(self):
        code = VALID_GAME.replace(
            "canvas.width = 420;\ncanvas.height = 600;",
            "let scaleX = 1;\nlet scaleY = 1;\nlet uiScale = 1;\nfunction resizeCanvas() {\n    canvas.width = window.innerWidth;\n    canvas.height = window.innerHeight;\n    scaleX = canvas.width / 360;\n    scaleY = canvas.height / 640;\n    uiScale = Math.min(scaleX, scaleY);\n}\nwindow.addEventListener('resize', resizeCanvas);\nresizeCanvas();",
        ).replace(
            "ctx.fillStyle = '#22c55e';\n    ctx.fillRect(180, 520, 60, 60);",
            "ctx.fillStyle = '#22c55e';\n    ctx.font = `${Math.min(20, Math.max(14, 18 * uiScale))}px sans-serif`;\n    ctx.fillText('Score', 12, 24);\n    ctx.fillRect(180, 520, 60, 60);",
        )
        errors, _ = qa._check_l4_playability(code)
        assert not any("width only" in e.message.lower() for e in errors)


class TestL5Performance:
    def test_valid_passes(self):
        errors, _ = qa._check_l5_performance(VALID_GAME)
        assert errors == []

    def test_oversized_errors(self):
        code = VALID_GAME + "x" * (510 * 1024)
        errors, _ = qa._check_l5_performance(code)
        assert any("500 KB" in e.message for e in errors)

    def test_wechat_size_warns(self):
        code = VALID_GAME + "x" * (310 * 1024)
        _, warnings = qa._check_l5_performance(code)
        assert any("300 KB" in w.message for w in warnings)

    def test_tiny_warns(self):
        code = "<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body><canvas id='c'></canvas><script>var c=document.getElementById('c');c.width=1;c.height=1;c.getContext('2d');</script></body></html>"
        _, warnings = qa._check_l5_performance(code)
        assert any("minimal" in w.message for w in warnings)

    def test_infinite_loop_no_break_errors(self):
        code = VALID_GAME.replace(
            "function update() {",
            "function bad() { while(true) { doX(); } } function update() {"
        )
        errors, _ = qa._check_l5_performance(code)
        assert any("infinite loop" in e.message.lower() for e in errors)

    def test_while_true_with_break_ok(self):
        code = VALID_GAME.replace(
            "function update() {",
            "function ok() { while(true) { if(x) break; } } function update() {"
        )
        errors, _ = qa._check_l5_performance(code)
        assert not any("infinite loop" in e.message.lower() for e in errors)


class TestL6ContentSafety:
    def test_clean_passes(self):
        assert qa._check_l6_content_safety(VALID_GAME) == []

    def test_skill_not_flagged(self):
        # P0 fix: "skill" must NOT match word-boundary "kill"
        code = VALID_GAME.replace("game.score += 1;", "// skillful play")
        assert qa._check_l6_content_safety(code) == []

    def test_porn_detected(self):
        code = VALID_GAME.replace("game.score += 1;", "// porn game")
        errors = qa._check_l6_content_safety(code)
        assert len(errors) > 0

    def test_chinese_gambling_detected(self):
        code = VALID_GAME.replace("game.score += 1;", "// 赌博")
        errors = qa._check_l6_content_safety(code)
        assert any("gambling" in e.message for e in errors)

    def test_chinese_adult_detected(self):
        code = VALID_GAME.replace("game.score += 1;", "// 色情")
        errors = qa._check_l6_content_safety(code)
        assert any("adult" in e.message for e in errors)

    def test_nonviolence_not_flagged(self):
        code = VALID_GAME.replace("game.score += 1;", "// nonviolence theme")
        assert qa._check_l6_content_safety(code) == []


class TestFullCheck:
    def test_valid_passes_all_layers(self):
        result = qa.check(VALID_GAME)
        assert result.passed is True
        assert result.errors == []
        for key in ("L1_syntax", "L2_security", "L3_startup", "L4_playability", "L5_performance", "L6_content"):
            assert result.validation_summary[key] is True

    def test_broken_game_fails(self):
        broken = """<!DOCTYPE html>
<html><head><meta charset='UTF-8'></head><body>
<div>No canvas, no game</div>
<script>eval('hack'); localStorage.clear(); fetch('http://evil.com');</script>
</body></html>"""
        result = qa.check(broken)
        assert result.passed is False
        assert len(result.errors) >= 3
        assert result.validation_summary["L2_security"] is False
        assert result.validation_summary["L3_startup"] is False


def test_repair_code_supports_fix_round_prompt_variables():
    pipeline = QAPipeline()
    errors = [QACheckError(type="L1_syntax", message="Missing </html>", severity="error")]

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="Round {fix_round}/{max_fix_rounds}::{game_type}::{error_list}::{code}",
    ), patch(
        "src.engine.qa_pipeline.settings.LLM_MODE",
        "real",
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value=VALID_GAME),
    ) as mock_complete:
        asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html>",
                errors,
                GameSpec(game_type="casual"),
                fix_round=2,
                max_fix_rounds=3,
            )
        )

    prompt = mock_complete.await_args.kwargs["messages"][0]["content"]
    assert "Round 2/3::casual" in prompt
    assert "Missing </html>" in prompt


def test_repair_code_falls_back_when_db_prompt_template_is_invalid():
    pipeline = QAPipeline()
    errors = [QACheckError(type="L1_syntax", message="Missing </html>", severity="error")]

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="Broken template {",
    ), patch("src.engine.qa_pipeline.settings.LLM_MODE", "real"):
        with pytest.raises(RuntimeError, match="QA fix prompt template is invalid"):
            asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html>",
                errors,
                GameSpec(game_type="casual"),
            )
        )


def test_repair_code_includes_runtime_contract_block_for_forbidden_api_repairs():
    pipeline = QAPipeline()
    errors = [QACheckError(type="contract_safety", message="Runtime contract forbids API usage: fetch", severity="error")]
    runtime_contract = GameRuntimeContract()

    def fake_require_prompt(key: str):
        if key == "prompt.qa_runtime_contract_block":
            return (
                "Runtime contract (must still hold after the repair):\n"
                "- Contract version: {contract_version}\n"
                "- Runtime profile: {runtime_profile}\n"
                "- Required states: {required_states}\n"
                "- Required input modes: {input_modes}\n"
                "- Forbidden APIs: {forbidden_apis}\n"
                "- The repaired output must remove forbidden APIs instead of hiding them behind wrappers."
            )
        if key == "prompt.qa_instruction_forbidden_api":
            return (
                "- Remove every forbidden dynamic-code or network API usage from the final HTML.\n"
                "- Replace eval/new Function/import/require patterns with plain named functions and static control flow.\n"
                "- Keep gameplay logic self-contained; do not fetch remote assets or open sockets."
            )
        return "PROMPT::{runtime_contract_block}::{targeted_instructions}"

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        side_effect=fake_require_prompt,
    ), patch(
        "src.engine.qa_pipeline.settings.LLM_MODE",
        "real",
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body><script>fetch('https://example.com')</script></body></html>",
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=runtime_contract,
            )
        )

    prompt = mock_complete.await_args.kwargs["messages"][0]["content"]
    assert "Runtime contract (must still hold after the repair):" in prompt
    assert "Forbidden APIs: localStorage, sessionStorage, fetch, XMLHttpRequest, WebSocket, eval, Function" in prompt
    assert "Remove every forbidden dynamic-code or network API usage" in prompt


def test_repair_code_uses_family_specific_bundle_prompt_and_scopes_to_one_family():
    pipeline = QAPipeline()
    runtime_contract = GameRuntimeContract()
    errors = [
        QACheckError(type="contract_safety", message="Runtime contract forbids API usage: fetch", severity="error"),
        QACheckError(type="contract_input", message="Runtime contract requires primary touch or pointer gameplay handlers", severity="error"),
    ]
    prompt_bundle_snapshot = {
        "layers": {
            "resolved_prompts": {
                "repair_forbidden_api": {
                    "content": "FORBIDDEN_ONLY::{error_list}::{targeted_instructions}::{code}"
                }
            }
        }
    }

    def fake_require_prompt(key: str):
        if key == "prompt.qa_runtime_contract_block":
            return (
                "Runtime contract (must still hold after the repair):\n"
                "- Contract version: {contract_version}\n"
                "- Runtime profile: {runtime_profile}\n"
                "- Required states: {required_states}\n"
                "- Required input modes: {input_modes}\n"
                "- Forbidden APIs: {forbidden_apis}\n"
                "- The repaired output must remove forbidden APIs instead of hiding them behind wrappers."
            )
        if key == "prompt.qa_instruction_forbidden_api":
            return (
                "- Remove every forbidden dynamic-code or network API usage from the final HTML.\n"
                "- Replace eval/new Function/import/require patterns with plain named functions and static control flow.\n"
                "- Keep gameplay logic self-contained; do not fetch remote assets or open sockets."
            )
        raise AssertionError(f"Unexpected prompt lookup: {key}")

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        side_effect=fake_require_prompt,
    ), patch(
        "src.engine.qa_pipeline.settings.LLM_MODE",
        "real",
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body><script>fetch('https://example.com')</script></body></html>",
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot=prompt_bundle_snapshot,
            )
        )

    kwargs = mock_complete.await_args.kwargs
    prompt = kwargs["messages"][0]["content"]
    assert kwargs["step_key"] == "qa_fix.forbidden_api"
    assert "Runtime contract forbids API usage: fetch" in prompt
    assert "primary touch or pointer gameplay handlers" not in prompt
    assert "FORBIDDEN_ONLY::" in prompt


def test_input_bridge_injects_dom_start_control_scan_and_dom_feedback_badge():
    code = "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>"

    bridged = QAPipeline._inject_input_bridge(code)

    assert "invokeVisibleDomStartControls" in bridged
    assert "__playforgeInputBridgeBadge" in bridged
    assert "startHints" in bridged


def test_l4_playability_accepts_named_game_over_state_transition_helpers():
    pipeline = QAPipeline()
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          let state = 'ready';
          function restartGame() {}
          function setState(nextState) {
            state = nextState;
          }
          function endRun() {
            setState('game_over');
          }
          const canvas = document.getElementById('gameCanvas');
          canvas.addEventListener('pointerdown', function handleTap() {});
        </script>
      </body>
    </html>
    """

    errors, _warnings = pipeline._check_l4_playability(code)

    assert not any("terminal or completion state" in error.message.lower() for error in errors)


def test_l4_playability_accepts_puzzle_completion_state_without_score_loop_warning():
    pipeline = QAPipeline()
    runtime_contract = GameRuntimeContract(
        runtime_profile="puzzle_grid",
        state=StateContract(required_states=["boot", "ready", "playing", "level_complete"]),
        input=InputContract(required_modes=["touch"], gestures=["tap", "drag"]),
        gameplay=GameplayContract(
            requires_player_entity=False,
            requires_scoring=False,
            requires_terminal_state=True,
            requires_restart_entry=True,
            terminal_state_aliases=["level_complete", "completed", "solved", "success"],
            primary_goal="grid_completion",
        ),
    )
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          let state = 'ready';
          let levelComplete = false;
          function restartLevel() {
            levelComplete = false;
            state = 'ready';
          }
          function completeLevel() {
            levelComplete = true;
            state = 'level_complete';
          }
          const canvas = document.getElementById('gameCanvas');
          canvas.addEventListener('touchstart', function handlePick() {});
          canvas.addEventListener('touchmove', function handleDrag() {});
          canvas.addEventListener('touchend', function handleDrop() {});
        </script>
      </body>
    </html>
    """

    errors, warnings = pipeline._check_l4_playability(code, runtime_contract=runtime_contract)

    assert not any("terminal or completion state" in error.message.lower() for error in errors)
    assert not any("Score variable exists but is never incremented" in warning.message for warning in warnings)


def disabled_test_repair_code_uses_fast_prompt_and_fast_route_for_known_single_issue():
    pipeline = QAPipeline()
    errors = [QACheckError(type="L4_playability", message="No user input handlers – game is not interactive", severity="error")]

    def fake_get_prompt(key: str, default=None):
        if key == "prompt.qa_fix_fast":
            return "FAST::{targeted_instructions}::{code}"
        return default

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        side_effect=fake_get_prompt,
    ), patch(
        "src.engine.qa_pipeline.settings.LLM_MODE",
        "real",
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body></body></html>",
                errors,
                GameSpec(game_type="casual"),
            )
        )

    kwargs = mock_complete.await_args.kwargs
    assert kwargs["prefer_fast"] is True
    assert kwargs["max_tokens"] < 8192
    prompt = kwargs["messages"][0]["content"]
    assert "FAST::" in prompt
    assert "Add a dedicated input binding function" in prompt


def disabled_test_repair_code_treats_runtime_qa_missing_registered_handlers_as_fast_input_issue():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="runtime_qa",
            message="Runtime QA detected no registered user input handlers",
            severity="error",
        )
    ]

    with patch(
        "src.engine.qa_pipeline.settings.LLM_MODE",
        "real",
    ), patch(
        "src.engine.qa_pipeline.settings.QA_FAST_REPAIR_TIMEOUT_S",
        111,
    ), patch(
        "src.engine.qa_pipeline.settings.LLM_PROVIDER_FAILOVER_ENABLED",
        True,
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body></body></html>",
                errors,
                GameSpec(game_type="casual"),
            )
        )

    kwargs = mock_complete.await_args.kwargs
    prompt = kwargs["messages"][0]["content"]
    assert kwargs["prefer_fast"] is True
    assert kwargs["request_timeout_s"] == 111
    assert kwargs["allow_provider_fallback"] is True
    assert "addEventListener-based pointer events or touch events" in prompt
    assert "runtime QA can observe the binding directly" in prompt


def test_repair_code_uses_fast_prompt_for_runtime_qa_missing_state_change():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="runtime_qa",
            message="Runtime QA detected no visible state change after user interaction",
            severity="error",
        )
    ]

    def fake_get_prompt(key: str, default=None):
        if key == "prompt.qa_fix_fast":
            return "FAST::{targeted_instructions}::{code}"
        return default

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        side_effect=fake_get_prompt,
    ), patch(
        "src.engine.qa_pipeline.settings.LLM_MODE",
        "real",
    ), patch(
        "src.engine.qa_pipeline.settings.QA_FAST_REPAIR_TIMEOUT_S",
        111,
    ), patch(
        "src.engine.qa_pipeline.settings.LLM_PROVIDER_FAILOVER_ENABLED",
        True,
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body></body></html>",
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert "__playforgeInputBridgeInstalled" in repaired
    assert "bindInputHandlers" in repaired
    assert mock_complete.await_count == 0


def test_repair_code_short_circuits_with_deterministic_input_bridge_for_missing_handlers():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="runtime_qa",
            message="Runtime QA detected no registered user input handlers",
            severity="error",
        )
    ]

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert "__playforgeInputBridgeInstalled" in repaired
    assert "addEventListener('pointerdown'" in repaired
    assert "window.__playforgeBridgeHandling = true;" in repaired
    assert "BRIDGE_EVENT_FLAG" in repaired
    assert "node.onclick = bridgeHandler" not in repaired
    assert "node.onpointerdown = bridgeHandler" not in repaired
    assert mock_complete.await_count == 0


def test_repair_code_short_circuits_with_deterministic_visible_feedback_bridge():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="runtime_qa",
            message="Runtime QA detected no visible state change after user interaction",
            severity="error",
        )
    ]

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert "__playforgeInputBridgeInstalled" in repaired
    assert "bindInputHandlers" in repaired
    assert "__playforgeInteractionFeedbackVersion" in repaired
    assert "Tap ' + stamp" in repaired
    assert "markEvent(event, BRIDGE_HANDLED_FLAG);" in repaired
    assert mock_complete.await_count == 0


def test_classify_visible_scoring_loop_as_score_feedback():
    error = QACheckError(
        type="contract_gameplay",
        message="Runtime contract requires a visible scoring loop",
        severity="error",
    )

    assert QAPipeline._classify_error_family(error) == "score_feedback"


def test_repair_code_short_circuits_with_deterministic_score_bridge():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="contract_gameplay",
            message="Runtime contract requires a visible scoring loop",
            severity="error",
        )
    ]

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
                errors,
                GameSpec(game_type="casual"),
            )
    )

    assert "__playforgeScoreBridgeInstalled" in repaired
    assert "playforgeScoreHud" in repaired
    assert "formatLabel(sample.label) + ': ' + sample.value" in repaired
    assert mock_complete.await_count == 0


def test_repair_code_short_circuits_with_deterministic_mobile_layout_bridge():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="contract_mobile",
            message="Runtime contract requires portrait-first short-edge UI scaling",
            severity="error",
        )
    ]
    code = """
    <!DOCTYPE html>
    <html>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          const ctx = canvas.getContext('2d');
          const W = 360;
          const H = 640;
          canvas.width = W;
          canvas.height = H;
        </script>
      </body>
    </html>
    """

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline.repair_code(
                code,
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert "__playforgeMobileLayoutBridgeInstalled" in repaired
    assert "const uiScale = Math.min(scaleX, scaleY);" in repaired
    assert "const shortEdge = Math.min(viewportWidth, viewportHeight);" in repaired
    assert "const applyResponsiveLayout = () => {" in repaired
    assert "window.__playforgeUiScale = uiScale;" in repaired
    assert mock_complete.await_count == 0


def test_repair_code_short_circuits_with_deterministic_landscape_mobile_layout_bridge():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="contract_mobile",
            message="Runtime contract requires landscape-first short-edge UI scaling",
            severity="error",
        )
    ]
    code = """
    <!DOCTYPE html>
    <html>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          const ctx = canvas.getContext('2d');
          const W = 640;
          const H = 360;
          canvas.width = W;
          canvas.height = H;
        </script>
      </body>
    </html>
    """

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline.repair_code(
                code,
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(
                    canvas={"orientation": "landscape_first"},
                    mobile_layout={"orientation": "landscape_first"},
                ),
            )
        )

    assert "__playforgeMobileLayoutBridgeInstalled" in repaired
    assert "const designWidth = Math.max(1, Number(canvas.width) || Number(canvas.getAttribute('width')) || 360);" in repaired
    assert "const applyResponsiveLayout = () => {" in repaired
    assert "window.__playforgeUiScale = uiScale;" in repaired
    assert mock_complete.await_count == 0


def test_repair_code_short_circuits_with_deterministic_forbidden_api_cleanup():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="contract_safety",
            message="Runtime contract forbids API usage: Function",
            severity="error",
        )
    ]

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body><script>const fn = new Function('return 1');</script></body></html>",
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert "new Function(" not in repaired
    assert "const fn = ('return 1');" in repaired


def test_repair_code_short_circuits_with_touch_coordinate_guard_for_runtime_clientx_error():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="runtime_qa",
            message="Runtime JS error: Cannot read properties of undefined (reading 'clientX')",
            severity="error",
        )
    ]
    code = (
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>"
        "function getPos(e){ const touch = e.touches ? e.touches[0] : e; return touch.clientX; }"
        "</script></body></html>"
    )

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline.repair_code(
                code,
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert "__playforgeResolveTouchPointInstalled" in repaired
    assert "window.__playforgeResolveTouchPoint(e)" in repaired
    assert mock_complete.await_count == 0


def test_repair_code_uses_bundle_prompt_for_syntax_structural_family():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript local static declarations are not valid in plain browser JS; use outer-scope let/const state instead",
            severity="error",
        )
    ]
    prompt_bundle_snapshot = {
        "layers": {
            "resolved_prompts": {
                "repair_syntax_structural": {
                    "content": "BUNDLE_SYNTAX::{error_list}::{code}"
                }
            }
        }
    }

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="LEGACY::{error_list}::{code}",
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(return_value=VALID_GAME),
    ) as mock_complete:
        asyncio.run(
            pipeline.repair_code(
                "<!DOCTYPE html><html><body><script>function update(){ static lastSpawnTime = 0; }</script></body></html>",
                errors,
                GameSpec(game_type="casual"),
                prompt_bundle_snapshot=prompt_bundle_snapshot,
            )
        )

    prompt = mock_complete.await_args.kwargs["messages"][0]["content"]
    assert "BUNDLE_SYNTAX::" in prompt
    assert "LEGACY::" not in prompt


def test_repair_code_rejects_structurally_regressed_llm_candidate():
    pipeline = QAPipeline()
    code = "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>function startGame() { return true; }</script></body></html>"
    expected_stable_candidate = pipeline._apply_deterministic_repairs(code)
    errors = [
        QACheckError(
            type="runtime_qa",
            message="Runtime JS error: Unexpected end of input",
            severity="error",
        )
    ]

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline,
        "_fix_with_llm",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body><script>function gameLoop"),
    ):
        repaired = asyncio.run(
            pipeline.repair_code(
                code,
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert repaired == expected_stable_candidate


def test_run_with_auto_fix_breaks_after_repeated_single_issue():
    pipeline = QAPipeline()
    repeated_error = QACheckError(
        type="L4_playability",
        message="No user input handlers – game is not interactive",
        severity="error",
    )

    failed_response = pipeline.check(VALID_GAME.replace(
        "canvas.addEventListener('touchstart', function(e) {\n    if (game.gameOver) restart();\n});",
        "// no input",
    ))
    failed_response.errors = [repeated_error]
    failed_response.passed = False

    with patch.object(
        pipeline,
        "check",
        side_effect=[failed_response, failed_response, failed_response, failed_response],
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline,
        "repair_code",
        new=AsyncMock(side_effect=[
            "<!DOCTYPE html><html><body>fix-1</body></html>",
            "<!DOCTYPE html><html><body>fix-2</body></html>",
        ]),
    ) as mock_repair:
        result = asyncio.run(pipeline.run_with_auto_fix("<!DOCTYPE html><html></html>", GameSpec(game_type="casual"), max_retries=3))

    assert result.success is False
    assert result.retries == 2
    assert mock_repair.await_count == 2


def test_repair_code_uses_simplified_rewrite_when_syntax_fix_stays_broken():
    pipeline = QAPipeline()
    broken_code = VALID_GAME.replace("game.score += 1;", "if (true) {")
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Unexpected end of input",
            severity="error",
        ),
    ]

    class _FakeEsprima:
        @staticmethod
        def parseScript(script_content, tolerant=False):
            assert tolerant is False
            if "if (true) {" in script_content:
                raise Exception("Unexpected end of input")

    with patch(
        "src.engine.qa_pipeline.esprima",
        _FakeEsprima(),
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline,
        "_fix_with_llm",
        new=AsyncMock(return_value=broken_code),
    ) as mock_fix, patch.object(
        pipeline,
        "_rewrite_with_simplified_budget",
        new=AsyncMock(return_value=VALID_GAME),
    ) as mock_rewrite:
        repaired = asyncio.run(
            pipeline.repair_code(
                broken_code,
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert repaired == VALID_GAME
    assert mock_fix.await_count == 1
    assert mock_rewrite.await_count == 1


def test_repair_code_rebuilds_from_spec_when_syntax_repair_keeps_truncating():
    pipeline = QAPipeline()
    broken_code = VALID_GAME.replace("game.score += 1;", "if (true) {")
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Unexpected end of input",
            severity="error",
        ),
    ]

    class _FakeEsprima:
        @staticmethod
        def parseScript(script_content, tolerant=False):
            assert tolerant is False
            if "if (true) {" in script_content:
                raise Exception("Unexpected end of input")

    with patch(
        "src.engine.qa_pipeline.esprima",
        _FakeEsprima(),
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline,
        "_fix_with_llm",
        new=AsyncMock(return_value=broken_code),
    ) as mock_fix, patch.object(
        pipeline,
        "_rewrite_with_simplified_budget",
        new=AsyncMock(return_value=broken_code),
    ) as mock_rewrite, patch.object(
        pipeline,
        "_rebuild_from_spec_for_syntax_recovery",
        new=AsyncMock(return_value=VALID_GAME),
    ) as mock_rebuild:
        repaired = asyncio.run(
            pipeline.repair_code(
                broken_code,
                errors,
                GameSpec(game_type="casual", source_description="课堂浮力小游戏"),
                runtime_contract=GameRuntimeContract(),
            )
        )

    assert repaired == VALID_GAME
    assert mock_fix.await_count == 1
    assert mock_rewrite.await_count == 1
    assert mock_rebuild.await_count == 1


def test_mixed_syntax_failures_do_not_force_generic_repair():
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Unexpected end of input",
            severity="error",
        ),
        QACheckError(
            type="contract_input",
            message="Runtime contract requires primary touch or pointer gameplay handlers",
            severity="error",
        ),
    ]

    assert QAPipeline._should_force_full_repair(errors) is False


def test_repair_code_uses_larger_budget_for_truncation_prone_syntax_errors():
    pipeline = QAPipeline()
    broken_code = "<!DOCTYPE html><html><body><script>" + ("const value = 1;\n" * 500) + "ctx.fillText(score, REF_"
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Unexpected end of input",
            severity="error",
        ),
    ]

    with patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        pipeline,
        "_fix_with_llm",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fixed</body></html>"),
    ) as mock_fix:
        asyncio.run(
            pipeline.repair_code(
                broken_code,
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert mock_fix.await_args.kwargs["repair_family"] == "syntax_structural"
    assert mock_fix.await_args.kwargs["max_tokens"] >= 6144


def test_fix_with_llm_retries_truncated_syntax_repair_with_larger_budget():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Unexpected end of input",
            severity="error",
        ),
    ]

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="FIX::{code}",
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(side_effect=[
            LLMResponseTruncatedError(
                "Anthropic response hit max_tokens and may be truncated",
                response_excerpt="<html><body><script>function draw(){",
                stop_reason="max_tokens",
            ),
            "<!DOCTYPE html><html><body>fixed</body></html>",
        ]),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline._fix_with_llm(
                "<!DOCTYPE html><html><body><script>function draw(){</script></body></html>",
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=None,
                max_tokens=4096,
                repair_family="syntax_structural",
            )
        )

    assert repaired == "<!DOCTYPE html><html><body>fixed</body></html>"
    assert mock_complete.await_count == 2
    first_max_tokens = mock_complete.await_args_list[0].kwargs["max_tokens"]
    second_max_tokens = mock_complete.await_args_list[1].kwargs["max_tokens"]
    assert second_max_tokens > first_max_tokens


def test_fix_with_llm_retries_truncated_generic_repair_with_larger_budget():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="contract_gameplay",
            message="Runtime contract requires a restart entry point",
            severity="error",
        ),
    ]

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="FIX::{code}",
    ), patch.object(
        pipeline._client,
        "complete",
        new=AsyncMock(side_effect=[
            LLMResponseTruncatedError(
                "OpenAI-compatible response hit the output length limit and may be truncated",
                response_excerpt="<!DOCTYPE html><html><body><script>function fix(){",
                stop_reason="length",
                output_tokens=5207,
            ),
            "<!DOCTYPE html><html><body>fixed</body></html>",
        ]),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline._fix_with_llm(
                "<!DOCTYPE html><html><body><script>" + ("const tile = 1;\n" * 400) + "</script></body></html>",
                errors,
                GameSpec(game_type="puzzle"),
                runtime_contract=None,
                max_tokens=5207,
                repair_family="generic",
            )
        )

    assert repaired == "<!DOCTYPE html><html><body>fixed</body></html>"
    assert mock_complete.await_count == 2
    first_max_tokens = mock_complete.await_args_list[0].kwargs["max_tokens"]
    second_max_tokens = mock_complete.await_args_list[1].kwargs["max_tokens"]
    assert second_max_tokens > first_max_tokens


def test_rebuild_from_spec_for_syntax_recovery_uses_large_initial_budget_and_timeout():
    pipeline = QAPipeline()
    code = "<!DOCTYPE html><html><body><script>" + ("const tile = 1;\n" * 1500) + "</script></body></html>"
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 98: Unexpected token .",
            severity="error",
        ),
    ]
    spec = GameSpec(game_type="puzzle")

    with patch.object(
        pipeline,
        "_complete_repair_prompt_with_retry",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fixed</body></html>"),
    ) as mock_repair:
        repaired = asyncio.run(
            pipeline._rebuild_from_spec_for_syntax_recovery(
                code=code,
                errors=errors,
                game_spec=spec,
                runtime_contract=None,
            )
        )

    assert repaired == "<!DOCTYPE html><html><body>fixed</body></html>"
    assert mock_repair.await_count == 1
    kwargs = mock_repair.await_args.kwargs
    assert kwargs["step_key"] == "qa_fix.syntax_rebuild"
    assert kwargs["max_tokens"] > 8192
    assert kwargs["request_timeout_s"] >= 240
