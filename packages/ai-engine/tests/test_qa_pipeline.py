"""Unit tests for QA Pipeline – all 6 checkpoint layers (P0 improved)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from src.engine.qa_pipeline import QAPipeline

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
function loop() { update(); requestAnimationFrame(loop); }
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

    def test_no_game_loop_is_warning(self):
        code = VALID_GAME.replace("requestAnimationFrame", "//RAF")
        _, warnings = qa._check_l3_startup(code)
        assert any("game loop" in w.message.lower() for w in warnings)

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
        assert any("never set to true" in e.message for e in errors)

    def test_state_machine_alternative_accepted(self):
        code = VALID_GAME.replace("game.gameOver = true;", "gameState = 'gameover';")
        errors, _ = qa._check_l4_playability(code)
        assert not any("never set to true" in e.message for e in errors)

    def test_no_restart_is_warning(self):
        code = VALID_GAME.replace("function restart()", "function doNothing()")
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
