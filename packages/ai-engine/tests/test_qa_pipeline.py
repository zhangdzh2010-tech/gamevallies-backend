"""Unit tests for QA Pipeline – all 6 checkpoint layers (P0 improved)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from src.engine.qa_pipeline import QAPipeline
from src.engine.section_patch import ensure_structured_section_markers
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

    def test_localstorage_is_allowed(self):
        code = VALID_GAME.replace("game.score += 1;", "localStorage.clear();")
        assert not any("localStorage" in e.message for e in qa._check_l2_security(code))

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

    def test_webgl_draw_commands_count_as_rendering(self):
        code = VALID_GAME.replace(
            "const ctx = canvas.getContext('2d');",
            "const gl = canvas.getContext('webgl');",
        ).replace(
            "ctx.clearRect(0, 0, canvas.width, canvas.height);\n    ctx.fillStyle = '#22c55e';\n    ctx.fillRect(180, 520, 60, 60);",
            "gl.viewport(0, 0, canvas.width, canvas.height);\n    gl.clearColor(0.13, 0.77, 0.37, 1.0);\n    gl.clear(gl.COLOR_BUFFER_BIT);",
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
        errors, warnings = qa._check_l4_playability(code)
        assert not any("terminal or completion state" in e.message.lower() for e in errors)
        assert any("terminal or completion state" in w.message.lower() for w in warnings)

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
    errors = [QACheckError(type="L1_syntax", message="Missing required HTML tag: </html>", severity="error")]

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
    assert "Missing required HTML tag: </html>" in prompt


def test_repair_code_falls_back_when_db_prompt_template_is_invalid():
    pipeline = QAPipeline()
    errors = [QACheckError(type="L1_syntax", message="Missing required HTML tag: </html>", severity="error")]

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="Broken template {",
    ), patch("src.engine.qa_pipeline.settings.LLM_MODE", "real"), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ):
        with pytest.raises(RuntimeError, match="QA fix prompt template is invalid"):
            asyncio.run(
                pipeline.repair_code(
                    "<!DOCTYPE html><html>",
                    errors,
                    GameSpec(game_type="casual"),
                )
            )












def test_structural_regression_guard_allows_short_document_script_only_repairs():
    pipeline = QAPipeline()
    previous = (
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas>"
        "<script>fetch('https://example.com'); const safe = 0;</script></body></html>"
    )
    candidate = (
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas>"
        "<script>const safe = 1;</script></body></html>"
    )

    assert pipeline._introduces_structural_regression(previous, candidate) is False


def test_l1_structure_marker_check_rejects_incomplete_marker_pairs():
    code = ensure_structured_section_markers(VALID_GAME).replace(
        "/* SECTION:INPUT END */",
        "",
        1,
    )

    errors, warnings = qa._check_l1_structure_markers(code)

    assert warnings == []
    assert any("Structured section marker set is incomplete" in error.message for error in errors)


def test_repair_code_preserves_structured_markers_when_input_already_has_them():
    pipeline = QAPipeline()
    code = ensure_structured_section_markers(
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const safe = 0;</script></body></html>"
    )
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Unexpected end of input",
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
        new=AsyncMock(return_value="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const safe = 1;</script></body></html>"),
    ):
        repaired = asyncio.run(
            pipeline.repair_code(
                code,
                errors,
                GameSpec(game_type="casual"),
            )
        )

    assert "<!-- SECTION:HUD START -->" in repaired
    assert "/* SECTION:CONFIG START */" in repaired
    assert "const safe = 1;" in repaired




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












def test_classify_visible_scoring_loop_as_score_feedback():
    error = QACheckError(
        type="contract_gameplay",
        message="Runtime contract requires a visible scoring loop",
        severity="error",
    )

    assert QAPipeline._classify_error_family(error) == "score_feedback"






















def test_repair_code_uses_bundle_prompt_for_syntax_structural_family():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Unexpected end of input",
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
        type="L1_syntax",
        message="Conflict markers detected in generated output",
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


def test_check_populates_issue_list_with_family_blocking_and_repair_hint():
    pipeline = QAPipeline()
    failed = pipeline.check(VALID_GAME.replace(
        "canvas.addEventListener('touchstart', function(e) {\n    if (game.gameOver) restart();\n});",
        "// no input",
    ))

    assert failed.passed is False
    assert failed.issue_list.issues
    input_issue = next(
        issue
        for issue in failed.issue_list.issues
        if issue.family == "input_contract"
    )
    assert input_issue.blocking is True
    assert "touch/pointer handlers" in input_issue.repair_hint
    assert failed.issue_list.blocking_count >= 1
    assert "input_contract" in failed.issue_list.families








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

    assert mock_fix.await_args.kwargs["max_tokens"] >= 6144


def test_fix_with_llm_retries_truncated_syntax_repair_with_larger_budget():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="Missing required HTML tag: </html>",
            severity="error",
        ),
    ]

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="FIX::{code}",
    ), patch.object(
        pipeline._client,
        "complete_with_truncation_retry",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fixed</body></html>"),
    ) as mock_complete:
        repaired = asyncio.run(
            pipeline._fix_with_llm(
                "<!DOCTYPE html><html><body><script>function draw(){</script></body></html>",
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=None,
                max_tokens=4096,
            )
        )

    assert repaired == "<!DOCTYPE html><html><body>fixed</body></html>"
    assert mock_complete.await_count == 1
    assert mock_complete.await_args.kwargs["max_tokens"] >= 4096


def test_fix_with_llm_prefers_script_only_repair_for_inline_script_syntax_errors():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 82: Unexpected token ;",
            severity="error",
        ),
    ]
    code = "<!DOCTYPE html><html><body><script>function boot(){ const value = ; }</script></body></html>"

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="FULL::{code}",
    ), patch.object(
        pipeline,
        "_complete_script_repair_prompt_with_retry",
        new=AsyncMock(return_value="function boot(){ const value = 1; }"),
    ) as mock_script_repair, patch.object(
        pipeline,
        "_complete_repair_prompt_with_retry",
        new=AsyncMock(return_value="<html>should not run</html>"),
    ) as mock_full_repair:
        repaired = asyncio.run(
            pipeline._fix_with_llm(
                code,
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=None,
                max_tokens=4096,
            )
        )

    assert "const value = 1;" in repaired
    assert mock_script_repair.await_count == 1
    assert mock_full_repair.await_count == 0


def test_fix_with_llm_repairs_script_window_before_whole_script_fallback():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 2: Unexpected token ;",
            severity="error",
        ),
    ]
    code = (
        "<!DOCTYPE html><html><body><script>"
        "const score = 0;\n"
        "const value = ;\n"
        "console.log(score + value);"
        "</script></body></html>"
    )

    class _FakeEsprima:
        @staticmethod
        def parseScript(script_content, tolerant=False):
            assert tolerant is False
            if "const value = ;" in script_content:
                raise Exception("Unexpected token ;")

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="FULL::{code}",
    ), patch(
        "src.engine.qa_pipeline.esprima",
        _FakeEsprima(),
    ), patch.object(
        pipeline,
        "_complete_script_repair_prompt_with_retry",
        new=AsyncMock(return_value="const value = 1;"),
    ) as mock_script_repair, patch.object(
        pipeline,
        "_complete_repair_prompt_with_retry",
        new=AsyncMock(return_value="<html>should not run</html>"),
    ) as mock_full_repair:
        repaired = asyncio.run(
            pipeline._fix_with_llm(
                code,
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=None,
                max_tokens=4096,
            )
        )

    assert "const value = 1;" in repaired
    assert mock_script_repair.await_count == 1
    assert "SCRIPT WINDOW SYNTAX REPAIR" in mock_script_repair.await_args.kwargs["prompt"]
    assert "const value = ;" in mock_script_repair.await_args.kwargs["prompt"]
    assert mock_full_repair.await_count == 0


def test_fix_with_llm_skips_full_document_fallback_when_script_repair_fails():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 2: Unexpected token ;",
            severity="error",
        ),
    ]
    code = (
        "<!DOCTYPE html><html><body><script>"
        "const score = 0;\n"
        "const value = ;\n"
        "console.log(score + value);"
        "</script></body></html>"
    )

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="FULL::{code}",
    ), patch.object(
        pipeline,
        "_complete_script_repair_prompt_with_retry",
        new=AsyncMock(side_effect=[TimeoutError("window timeout"), TimeoutError("script timeout")]),
    ) as mock_script_repair, patch.object(
        pipeline,
        "_complete_repair_prompt_with_retry",
        new=AsyncMock(return_value="<html>should not run</html>"),
    ) as mock_full_repair:
        repaired = asyncio.run(
            pipeline._fix_with_llm(
                code,
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=None,
                max_tokens=4096,
            )
        )

    assert repaired == code
    assert mock_script_repair.await_count == 2
    assert mock_full_repair.await_count == 0


def test_syntax_repair_token_budget_is_capped_for_full_document_fix():
    large_code = "const x = 1;\n" * 8000
    assert qa._estimate_syntax_repair_max_tokens(large_code, truncation_risk=False) <= 8192
    assert qa._estimate_syntax_repair_max_tokens(large_code, truncation_risk=True) <= 12288


def test_syntax_repair_honors_configured_long_timeout():
    with patch("src.engine.qa_pipeline.get_timeout_int", return_value=1800):
        assert qa._estimate_repair_timeout_s(max_tokens=4096) == 1800
        assert qa._estimate_repair_timeout_s(max_tokens=16384) == 1800
        assert qa._estimate_script_repair_timeout_s(max_tokens=4096) == 1800


def test_syntax_only_repair_guard_rejects_mixed_errors():
    errors = [
        QACheckError(type="L1_syntax", message="Missing </script> tag", severity="error"),
        QACheckError(
            type="contract_mobile",
            message="Runtime contract requires portrait-first short-edge UI scaling",
            severity="error",
        ),
    ]

    assert qa._should_attempt_syntax_only_repair(errors) is False


def test_syntax_only_repair_guard_rejects_non_truncation_syntax_errors():
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript local static declarations are not valid in plain browser JS; use outer-scope let/const state instead",
            severity="error",
        )
    ]

    assert qa._should_attempt_syntax_only_repair(errors) is False


def test_syntax_repair_enables_fast_provider_fallback():
    pipeline = QAPipeline()

    with patch.object(
        pipeline._client,
        "complete_with_truncation_retry",
        new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
    ) as mock_complete:
        result = asyncio.run(
            pipeline._complete_repair_prompt_raw_with_retry(
                prompt="repair this",
                code="<!DOCTYPE html><html></html>",
                step_key="qa_fix.syntax_structural",
                request_timeout_s=150,
                max_tokens=8192,
            )
        )

    assert result == "<!DOCTYPE html><html></html>"
    assert mock_complete.await_args.kwargs["allow_provider_fallback"] is True
    assert mock_complete.await_args.kwargs["hedge_provider_fallback_after_s"] == 15
    assert mock_complete.await_args.kwargs["response_size_hint"] == "full_document"
    assert mock_complete.await_args.kwargs["timeout_retry_attempts"] == 0
    assert mock_complete.await_args.kwargs["provider_retry_attempts"] == 0
    assert mock_complete.await_args.kwargs["provider_retry_on_timeout_errors"] is False
    assert mock_complete.await_args.kwargs["context_scope"] == "request"
    assert mock_complete.await_args.kwargs["prefer_fast"] is True


def test_run_with_retries_signals_regeneration_when_repair_returns_unchanged_code():
    from types import SimpleNamespace

    pipeline = QAPipeline()
    broken = """<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>
<script>const broken = ;</script></body></html>"""
    syntax_errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 2: Unexpected token ;",
            severity="error",
        )
    ]
    failed_check = SimpleNamespace(passed=False, errors=syntax_errors, issue_list=[])

    with patch.object(pipeline, "check", side_effect=[failed_check, failed_check]), patch.object(
        pipeline,
        "_syntax_repair_errors",
        return_value=syntax_errors,
    ), patch.object(
        pipeline,
        "_should_attempt_syntax_only_repair",
        return_value=True,
    ), patch.object(
        pipeline,
        "repair_code",
        new=AsyncMock(return_value=broken),
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ):
        result = asyncio.run(
            pipeline.run_with_auto_fix(
                broken,
                GameSpec(game_type="casual"),
                max_retries=2,
            )
        )

    assert result.success is False
    assert result.needs_regeneration is True
    assert result.retries == 1


def test_run_with_retries_signals_regeneration_when_syntax_repair_truncates():
    from types import SimpleNamespace

    pipeline = QAPipeline()
    broken = """<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>
<script>const broken = ;</script></body></html>"""
    syntax_errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 2: Unexpected token ;",
            severity="error",
        )
    ]
    failed_check = SimpleNamespace(passed=False, errors=syntax_errors, issue_list=[])

    with patch.object(pipeline, "check", return_value=failed_check), patch.object(
        pipeline,
        "_syntax_repair_errors",
        return_value=syntax_errors,
    ), patch.object(
        pipeline,
        "_should_attempt_syntax_only_repair",
        return_value=True,
    ), patch.object(
        pipeline,
        "repair_code",
        new=AsyncMock(side_effect=LLMResponseTruncatedError("truncated during qa_fix.syntax_structural")),
    ), patch.object(
        pipeline._client,
        "is_enabled",
        return_value=True,
    ):
        result = asyncio.run(
            pipeline.run_with_auto_fix(
                broken,
                GameSpec(game_type="casual"),
                max_retries=2,
            )
        )

    assert result.success is False
    assert result.needs_regeneration is True


def test_windowed_syntax_truncation_falls_back_to_whole_script_repair():
    pipeline = QAPipeline()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 2: Unexpected token ;",
            severity="error",
        ),
    ]
    code = (
        "<!DOCTYPE html><html><body><script>"
        "const score = 0;\n"
        "const value = ;\n"
        "console.log(score + value);"
        "</script></body></html>"
    )

    class _FakeEsprima:
        @staticmethod
        def parseScript(script_content, tolerant=False):
            assert tolerant is False
            if "const value = ;" in script_content:
                raise Exception("Unexpected token ;")

    with patch(
        "src.engine.qa_pipeline.require_prompt",
        return_value="FULL::{code}",
    ), patch(
        "src.engine.qa_pipeline.esprima",
        _FakeEsprima(),
    ), patch.object(
        pipeline,
        "_complete_script_repair_prompt_with_retry",
        new=AsyncMock(side_effect=[
            LLMResponseTruncatedError("window truncated"),
            "const score = 0;\nconst value = 1;\nconsole.log(score + value);",
        ]),
    ) as mock_script_repair, patch.object(
        pipeline,
        "_complete_repair_prompt_with_retry",
        new=AsyncMock(return_value="<html>should not run</html>"),
    ) as mock_full_repair:
        repaired = asyncio.run(
            pipeline._fix_with_llm(
                code,
                errors,
                GameSpec(game_type="casual"),
                runtime_contract=None,
                max_tokens=4096,
            )
        )

    assert "const value = 1;" in repaired
    assert mock_script_repair.await_count == 2
    assert "SCRIPT WINDOW SYNTAX REPAIR" in mock_script_repair.await_args_list[0].kwargs["prompt"]
    assert "SCRIPT SYNTAX REPAIR (RETURN JAVASCRIPT ONLY)" in mock_script_repair.await_args_list[1].kwargs["prompt"]
    assert mock_full_repair.await_count == 0
