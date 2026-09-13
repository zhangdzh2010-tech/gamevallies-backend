import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import GameRuntimeContract
from src.engine.code_preflight import CodePreflightIssue, CodePreflightValidator


def test_touch_fallback_repair_is_local_idempotent_and_preserves_scopes():
    validator = CodePreflightValidator()
    html = '''<html><script>(()=>{function point(e){
      return e.touches ? e.touches[0] : e;
    }function ended(event){return event.changedTouches ? event.changedTouches[0] : event;}
    })();</script></html>'''
    repaired = validator.auto_repair(html)
    assert 'e.touches && e.touches.length' in repaired
    assert 'e.changedTouches && e.changedTouches.length' in repaired
    assert 'event.changedTouches && event.changedTouches.length' in repaired
    assert repaired.startswith('<html><script>(()=>{function point(e){')
    assert validator.auto_repair(repaired) == repaired
    assert not validator._check_touch_access(repaired)


def test_touch_fallback_repair_does_not_edit_data_comments_or_nonmatching_expressions():
    validator = CodePreflightValidator()
    html = '''<html><p>e.touches ? e.touches[0] : e</p><script>
    const message="e.touches ? e.touches[0] : e";
    const literal=`e.touches ? e.touches[0] : e`;
    const pattern=/e.touches ? e.touches[0] : e/;
    // e.touches ? e.touches[0] : e
    /* e.touches ? e.touches[0] : e */
    const different = e.touches ? other.touches[0] : e;
    const member = e.touches ? e.touches[0] : e.point;
    const spacedMember = e.touches ? e.touches[0] : e .point;
    const nestedFallback = e.touches ? e.touches[0] : e || other;
    const direct = e.touches[0];
    </script></html>'''
    assert validator.auto_repair(html) == html


def test_touch_fallback_repair_leaves_existing_guard_untouched():
    html = '<script>function point(e){return e.touches && e.touches.length ? e.touches[0] : e;}</script>'
    assert CodePreflightValidator().auto_repair(html) == html


def test_element_existence_guard_is_equivalent_to_length_check():
    validator = CodePreflightValidator()
    for kind in ('touches', 'changedTouches'):
        script = f'''function coords(e) {{
          const x=e.clientX !== undefined ? e.clientX : (e.{kind} && e.{kind}[0] ? e.{kind}[0].clientX : 0);
          const y=e.clientY !== undefined ? e.clientY : (e.{kind} && e.{kind}[0] ? e.{kind}[0].clientY : 0);
          return [x,y]; }}'''
        assert validator._check_touch_access(script) == []
        assert validator.auto_repair('<script>'+script+'</script>') == '<script>'+script+'</script>'


def test_element_guard_does_not_hide_different_or_unprotected_touch_reads():
    validator = CodePreflightValidator()
    safe = 'e.touches && e.touches[0] ? e.touches[0].clientX : 0'
    unsafe = [safe.replace('&&', '||'), '!'+safe, 'other || '+safe,
        safe.replace('? e.touches', '? other.touches'),
        safe.replace('e.touches &&', 'other.touches &&'),
        safe+'; const y=e.touches[0].clientY;',
        safe+'; const y=nested.event.touches[0].clientY;']
    for expression in unsafe:
        assert validator._check_touch_access('const x='+expression), expression


def test_partial_touch_repair_does_not_hide_another_unsafe_access():
    html = '<script>const point = e.touches ? e.touches[0] : e; const unsafe = other.touches[0];</script>'
    validator = CodePreflightValidator()
    assert validator.auto_repair(html) == html
    assert validator._check_touch_access(html)


def test_touch_repair_executes_pointer_touchstart_touchend_and_empty_lists():
    import asyncio
    from playwright.async_api import async_playwright
    html = '<script>function point(e){return e.touches ? e.touches[0] : e;}</script>'
    repaired = CodePreflightValidator().auto_repair(html)
    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(repaired)
                actual = await page.evaluate('''()=>[
                  point({clientX:1}).clientX,
                  point({touches:[{clientX:2}],clientX:1}).clientX,
                  point({touches:[],changedTouches:[{clientX:3}],clientX:1}).clientX,
                  point({touches:[],changedTouches:[],clientX:4}).clientX
                ]''')
                assert actual == [1,2,3,4]
            finally:
                await browser.close()
    asyncio.run(check())


def test_code_preflight_flags_missing_canvas_dimensions_and_undefined_symbols():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          const ctx = canvas.getContext('2d');
          function render() {
            if (anim < 1) {
              generateBackgroundLayers();
            }
            ctx.fillRect(0, 0, viewWidth, 10);
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    codes = {issue.code for issue in issues}

    assert "canvas_dimensions" in codes
    assert "undefined_symbol:anim" in codes
    assert "undefined_symbol:generateBackgroundLayers" in codes
    assert "undefined_symbol:viewWidth" in codes


def test_code_preflight_flags_unsafe_touch_access_without_length_guard():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          canvas.addEventListener('touchstart', (event) => {
            const point = event.touches[0];
            console.log(point.clientX);
          });
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert any(issue.code == "unsafe_touch_access" for issue in issues)


def test_code_preflight_does_not_flag_browser_alert_as_undefined_symbol():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          function onLose() {
            alert('Game Over');
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert not any(issue.code == "undefined_symbol:alert" for issue in issues)


def test_code_preflight_flags_ready_state_input_gate_on_primary_canvas_handler():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          let gameState = 'ready';
          function onPointerDown(event) {
            if (gameState !== 'playing') return;
            console.log(event.clientX);
          }
          canvas.addEventListener('pointerdown', onPointerDown);
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert any(issue.code == "ready_state_input_gate" for issue in issues)


def test_code_preflight_guidance_dedupes_messages():
    validator = CodePreflightValidator()
    guidance = validator.render_guidance(
        validator.validate(
            """
            <!DOCTYPE html>
            <html><body><canvas id="gameCanvas"></canvas><script>
            const canvas = document.getElementById('gameCanvas');
            if (anim < 1) { anim += 1; }
            </script></body></html>
            """,
            runtime_contract=GameRuntimeContract(),
        )
    )

    assert "PRE-FLIGHT CORRECTIONS" in guidance
    assert guidance.count("Declare or inline `anim`") == 1


def test_code_preflight_hoists_tdz_resize_and_loop_bindings():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          init();
          const resize = () => {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
          };
          const loop = (t) => {
            requestAnimationFrame(loop);
          };
          function init() {
            resize();
            loop();
          }
          window.addEventListener('resize', resize);
        </script>
      </body>
    </html>
    """
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {"tdz_symbol:resize", "tdz_symbol:loop"}
    repaired = validator.auto_repair(html, issues=issues)
    assert "function resize(" in repaired
    assert "function loop(" in repaired
    assert "const resize =" not in repaired
    assert "const loop =" not in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(issue.code.startswith(("tdz_symbol:", "undefined_symbol:resize", "undefined_symbol:loop")) for issue in remaining)
    guidance = validator.render_guidance(issues)
    assert "function declarations" in guidance
    assert "temporal dead zone" in " ".join(issue.message for issue in issues) or "function declarations" in guidance


def test_code_preflight_hoists_resetgame_update_const_bindings_like_resize_loop():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          init();
          const resetGame = () => {
            score = 0;
          };
          const update = (t) => {
            score += 1;
            return t;
          };
          function init() {
            resetGame();
            update(0);
          }
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {"tdz_symbol:resetGame", "tdz_symbol:update"}
    repaired = validator.auto_repair(html, issues=issues)
    assert "function resetGame(" in repaired
    assert "function update(" in repaired
    assert "const resetGame =" not in repaired
    assert "const update =" not in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(
        issue.code in {
            "tdz_symbol:resetGame",
            "tdz_symbol:update",
            "undefined_symbol:resetGame",
            "undefined_symbol:update",
        }
        for issue in remaining
    )


def test_code_preflight_ignores_object_method_shorthand_definitions_as_calls():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          const game = {
            resetGame() {
              this.score = 0;
            },
            update() {
              this.score += 1;
            },
            loop(t) {
              this.update();
              requestAnimationFrame((now) => this.loop(now));
            }
          };
          game.resetGame();
          game.loop(0);
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert not any(
        issue.code in {
            "undefined_symbol:resetGame",
            "undefined_symbol:update",
            "undefined_symbol:loop",
            "undefined_symbol:t",
        }
        for issue in issues
    )


def test_code_preflight_hoists_object_methods_used_as_free_calls():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          const game = {
            resetGame() { score = 0; },
            update() { score += 1; },
            loop(t) { update(); requestAnimationFrame(loop); }
          };
          resetGame();
          requestAnimationFrame(loop);
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {
        "undefined_symbol:resetGame",
        "undefined_symbol:update",
        "undefined_symbol:loop",
    }
    repaired = validator.auto_repair(html, issues=issues)
    assert "function resetGame(" in repaired
    assert "function update(" in repaired
    assert "function loop(" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(
        issue.code in {
            "undefined_symbol:resetGame",
            "undefined_symbol:update",
            "undefined_symbol:loop",
        }
        for issue in remaining
    )


def test_code_preflight_hoists_this_and_window_function_assigns():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          function init() {
            resetGame();
            update();
          }
          this.resetGame = function() { score = 0; };
          window.update = () => { score += 1; };
          init();
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {
        "undefined_symbol:resetGame",
        "undefined_symbol:update",
    }
    repaired = validator.auto_repair(html, issues=issues)
    assert "function resetGame(" in repaired
    assert "function update(" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(
        issue.code in {"undefined_symbol:resetGame", "undefined_symbol:update"}
        for issue in remaining
    )


def test_code_preflight_injects_resetgame_when_called_without_any_declaration():
    validator = CodePreflightValidator()
    html = _post_pr86_grid_puzzle_resetgame_residual_html()
    contract = GameRuntimeContract(runtime_profile="puzzle_grid_match")
    issues = validator.validate(html, runtime_contract=contract)
    assert any(issue.code == "undefined_symbol:resetGame" for issue in issues)
    assert any(
        "referenced as a function call" in issue.message
        for issue in issues
        if issue.code == "undefined_symbol:resetGame"
    )

    repaired = validator.auto_repair(html, runtime_contract=contract)
    remaining = validator.validate(repaired, runtime_contract=contract)
    _assert_post_pr86_resetgame_residual_cleared(repaired, remaining)
    assert validator.auto_repair(repaired, runtime_contract=contract, issues=remaining) == repaired


def test_code_preflight_copies_resetgame_from_continue_generated_secondary_script():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const ctx = canvas.getContext('2d');
          function init() {
            resetGame();
          }
          function loop() { requestAnimationFrame(loop); }
          init();
          requestAnimationFrame(loop);
        </script>
        <script>
          function resetGame() { score = 0; selected = null; }
        </script>
      </body>
    </html>
    """
    contract = GameRuntimeContract(runtime_profile="puzzle_grid_match")
    issues = validator.validate(html, runtime_contract=contract)
    assert any(issue.code == "undefined_symbol:resetGame" for issue in issues)
    repaired = validator.auto_repair(html, runtime_contract=contract, issues=issues)
    primary = repaired.split("</script>")[0]
    assert "function resetGame(" in primary
    assert "score = 0" in primary
    remaining = validator.validate(repaired, runtime_contract=contract)
    assert not any(issue.code == "undefined_symbol:resetGame" for issue in remaining)


def test_code_preflight_does_not_invent_non_lifecycle_missing_helpers():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          function tick() {
            missingHelper(1);
          }
          tick();
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    repaired = validator.auto_repair(html, issues=issues)
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert "function missingHelper" not in repaired
    assert any(issue.code == "undefined_symbol:missingHelper" for issue in remaining)


def test_code_preflight_declares_short_live_raf_timestamp_t():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          let last = 0;
          function resetGame() { last = 0; }
          function update() {}
          function loop() {
            if (t > last + 16) {
              last = t;
              update();
            }
            requestAnimationFrame(loop);
          }
          resetGame();
          requestAnimationFrame(loop);
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert any(issue.code == "undefined_symbol:t" for issue in issues)
    assert any("live expression" in issue.message for issue in issues if issue.code == "undefined_symbol:t")
    repaired = validator.auto_repair(html, issues=issues)
    assert "let t = 0;" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(issue.code == "undefined_symbol:t" for issue in remaining)
    guidance = validator.render_guidance(issues)
    assert "function loop(t)" in guidance
    assert "`t`" in guidance


def test_code_preflight_declares_t_used_as_update_argument():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          function resetGame() {}
          function update(dt) { return dt; }
          function loop() {
            update(t);
            requestAnimationFrame(loop);
          }
          resetGame();
          requestAnimationFrame(loop);
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert any(issue.code == "undefined_symbol:t" for issue in issues)
    repaired = validator.auto_repair(html, issues=issues)
    assert "let t = 0;" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(issue.code == "undefined_symbol:t" for issue in remaining)


def test_code_preflight_hoists_puzzle_helpers_declared_after_const_let_calls():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          function init() {
            initGrid();
            const pos = getEventPos({ clientX: 1, clientY: 2 });
            const cell = getCellAt(0, 0);
            return isAdjacent(0, 1) ? cell : pos;
          }
          init();
          const initGrid = () => {
            grid.length = 0;
          };
          const getEventPos = (e) => ({ x: e.clientX, y: e.clientY });
          let getCellAt = function(r, c) {
            return grid[r] && grid[r][c];
          };
          const isAdjacent = (a, b) => Math.abs(a - b) === 1;
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert {issue.code for issue in issues} >= {
        "tdz_symbol:initGrid",
        "tdz_symbol:getEventPos",
        "tdz_symbol:getCellAt",
        "tdz_symbol:isAdjacent",
    }
    repaired = validator.auto_repair(html, issues=issues, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert "function initGrid(" in repaired
    assert "function getEventPos(" in repaired
    assert "function getCellAt(" in repaired
    assert "function isAdjacent(" in repaired
    assert "const initGrid =" not in repaired
    assert "let getCellAt =" not in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert not any(
        issue.code.startswith(("tdz_symbol:", "undefined_symbol:"))
        and issue.code.split(":", 1)[-1] in {"initGrid", "getEventPos", "getCellAt", "isAdjacent"}
        for issue in remaining
    )


def test_code_preflight_hoists_puzzle_helpers_from_object_methods_and_properties():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          function handle(e) {
            initGrid();
            const pos = getEventPos(e);
            const cell = getCellAt(0, 0);
            return isAdjacent(0, 1) ? cell : pos;
          }
          handle({ clientX: 1, clientY: 2 });
          const board = {
            // puzzle helpers used as free calls before this object
            initGrid() { grid = []; },
            getEventPos: function(e) { return { x: e.clientX, y: e.clientY }; },
            getCellAt: (r, c) => grid[r] && grid[r][c],
            isAdjacent(a, b) { return Math.abs(a - b) === 1; }
          };
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert {issue.code for issue in issues} >= {
        "undefined_symbol:initGrid",
        "undefined_symbol:getEventPos",
        "undefined_symbol:getCellAt",
        "undefined_symbol:isAdjacent",
    }
    repaired = validator.auto_repair(html, issues=issues, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert "function initGrid(" in repaired
    assert "function getEventPos(" in repaired
    assert "function getCellAt(" in repaired
    assert "function isAdjacent(" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert not any(
        issue.code in {
            "undefined_symbol:initGrid",
            "undefined_symbol:getEventPos",
            "undefined_symbol:getCellAt",
            "undefined_symbol:isAdjacent",
            "tdz_symbol:initGrid",
            "tdz_symbol:getEventPos",
            "tdz_symbol:getCellAt",
            "tdz_symbol:isAdjacent",
        }
        for issue in remaining
    )


def test_code_preflight_guidance_names_resetgame_update_as_function_declarations():
    guidance = CodePreflightValidator().render_guidance(
        [
            CodePreflightIssue(
                code="undefined_symbol:resetGame",
                message="Declare or inline `resetGame` before use; it is referenced as a function call.",
            ),
            CodePreflightIssue(
                code="undefined_symbol:update",
                message="Declare or inline `update` before use; it is referenced as a function call.",
            ),
            CodePreflightIssue(
                code="undefined_symbol:t",
                message="Declare or inline `t` before use; it is referenced as a live expression.",
            ),
        ]
    )
    assert "function resetGame()" in guidance
    assert "function declarations" in guidance
    assert "function loop(t)" in guidance


def test_code_preflight_hoists_tdz_helper_beyond_hardcoded_resize_loop():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          init();
          const nc = (row, col) => {
            return Number(row) + Number(col);
          };
          function init() {
            const total = nc(1, 2);
            requestAnimationFrame(nc);
          }
        </script>
      </body>
    </html>
    """
    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert any(issue.code == "tdz_symbol:nc" for issue in issues)
    repaired = validator.auto_repair(html, issues=issues)
    assert "function nc(" in repaired
    assert "const nc =" not in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert not any(issue.code in {"tdz_symbol:nc", "undefined_symbol:nc"} for issue in remaining)


def test_code_preflight_declares_assigned_grid_neighbor_aliases():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const grid = [[{ type: 1 }]];
          function inBounds(row, col) {
            return row >= 0 && col >= 0;
          }
          function countNeighbors(r, c) {
            let n = 0;
            nr = r + 1;
            nc = c - 1;
            if (inBounds(nr, nc) && grid[0]) n += 1;
            return n;
          }
        </script>
      </body>
    </html>
    """
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {"undefined_symbol:nr", "undefined_symbol:nc"}
    assert any("live expression" in issue.message for issue in issues if issue.code == "undefined_symbol:nc")
    repaired = validator.auto_repair(html, issues=issues)
    assert "let nc, nr;" in repaired or "let nr, nc;" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(issue.code in {"undefined_symbol:nr", "undefined_symbol:nc"} for issue in remaining)
    guidance = validator.render_guidance(issues)
    assert "let nr, nc;" in guidance


def test_code_preflight_declares_destructured_neighbor_aliases():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          function inBounds(row, col) { return row >= 0 && col >= 0; }
          function walk(r, c, dr, dc) {
            [nr, nc] = [r + dr, c + dc];
            return inBounds(nr, nc);
          }
        </script>
      </body>
    </html>
    """
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {"undefined_symbol:nr", "undefined_symbol:nc"}
    repaired = validator.auto_repair(html, issues=issues)
    assert "let nc, nr;" in repaired or "let nr, nc;" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(issue.code in {"undefined_symbol:nr", "undefined_symbol:nc"} for issue in remaining)


def test_code_preflight_hoists_bare_function_assignment_used_as_live_expression():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          requestAnimationFrame(nc);
          nc = (stamp) => {
            return stamp;
          };
        </script>
      </body>
    </html>
    """
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert any(issue.code == "undefined_symbol:nc" for issue in issues)
    repaired = validator.auto_repair(html, issues=issues)
    assert "function nc(" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(issue.code == "undefined_symbol:nc" for issue in remaining)


def test_code_preflight_declares_incremented_live_counters_like_combo():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          function onMatch(count) {
            combo++;
            if (combo > 1) {
              updateHud(combo);
            }
            return combo;
          }
        </script>
      </body>
    </html>
    """
    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert any(issue.code == "undefined_symbol:combo" for issue in issues)
    assert any("live expression" in issue.message for issue in issues if issue.code == "undefined_symbol:combo")
    repaired = validator.auto_repair(html, issues=issues)
    assert "let combo = 0;" in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert not any(issue.code == "undefined_symbol:combo" for issue in remaining)
    guidance = validator.render_guidance(issues)
    assert "let combo = 0;" in guidance


def test_code_preflight_does_not_invent_unassigned_undefined_symbols():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          function tick() {
            if (anim < 1) {
              missingHelper(anim);
            }
          }
        </script>
      </body>
    </html>
    """
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    repaired = validator.auto_repair(html, issues=issues)
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert any(issue.code == "undefined_symbol:anim" for issue in remaining)
    assert any(issue.code == "undefined_symbol:missingHelper" for issue in remaining)
    assert "let anim" not in repaired
    assert "function missingHelper" not in repaired


def test_code_preflight_guidance_mentions_ready_state_input_gate():
    validator = CodePreflightValidator()
    guidance = validator.render_guidance(
        [
            CodePreflightIssue(
                code="ready_state_input_gate",
                message="Primary input handler returns unless the game is already in `playing`.",
            )
        ]
    )

    assert "primary canvas/document pointer or touch handler" in guidance
    assert "startGame()" in guidance


def test_code_preflight_flags_ctx_set_transform_before_context_init():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          function resize() {
            ctx.setTransform(1, 0, 0, 1, 0, 0);
          }
          const ctx = canvas.getContext('2d');
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert any(issue.code == "canvas_context_order" for issue in issues)


def test_code_preflight_flags_unsafe_canvas_path_chains():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 640;
          canvas.height = 360;
          const ctx = canvas.getContext('2d');
          function renderCard() {
            ctx.roundRect(10, 10, 40, 20, 8).fill();
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert any(issue.code == "unsafe_canvas_path_chain" for issue in issues)


def test_code_preflight_flags_null_initialized_runtime_object_used_in_loop():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const ctx = canvas.getContext('2d');
          var player = null;
          function loop() {
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            player.trail.push({ x: 0, y: 0 });
            requestAnimationFrame(loop);
          }
          requestAnimationFrame(loop);
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert any(issue.code == "nullable_runtime_object:player" for issue in issues)


def test_code_preflight_allows_guarded_nullable_runtime_object_access():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const ctx = canvas.getContext('2d');
          let hoveredCell = null;
          function loop() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (hoveredCell) {
              ctx.fillRect(hoveredCell.x, hoveredCell.y, 10, 10);
            }
            requestAnimationFrame(loop);
          }
          requestAnimationFrame(loop);
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))

    assert not any(issue.code == "nullable_runtime_object:hoveredCell" for issue in issues)


def test_code_preflight_auto_repair_wraps_nested_grid_reads_with_safe_helper():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          function inspectCell(grid, row, col) {
            return grid[row][col].type + ':' + grid[row][col].row + ',' + grid[row][col].col;
          }
        </script>
      </body>
    </html>
    """

    repaired = validator.auto_repair(
        html,
        runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"),
    )
    issues = validator.validate(
        repaired,
        runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"),
    )

    assert repaired.count("function __safeGridCell(") == 1
    assert "__safeGridCell(grid," in repaired
    assert "grid[row][col].type" not in repaired
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in issues)
    assert not any(issue.code == "undefined_symbol:__safeGridCell" for issue in issues)


def test_code_preflight_auto_repair_targets_primary_script_not_just_first_script():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          window.__BOOT = true;
        </script>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          function updateTween(grid, row, col) {
            return grid[row][col].targetX + ':' + grid[row][col].targetY;
          }
        </script>
      </body>
    </html>
    """

    repaired = validator.auto_repair(
        html,
        runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_merge"),
    )
    issues = validator.validate(
        repaired,
        runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_merge"),
    )

    assert "window.__BOOT = true;" in repaired
    assert repaired.count("function __safeGridCell(") == 1
    assert "__safeGridCell(grid," in repaired
    assert "grid[row][col].targetX" not in repaired
    assert "grid[row][col].targetY" not in repaired
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in issues)
    assert not any(issue.code == "undefined_symbol:__safeGridCell" for issue in issues)


def _post_pr86_grid_puzzle_resetgame_residual_html() -> str:
    """Continue-generate / MISS residual after PR #86 (grid_puzzle_en run 48).

    The boot script calls `resetGame()` from init, but truncation continuation
    closed the document without emitting a declaration. PR #83 hoist cannot
    fire: there is no const/let/method binding to rewrite.
    """
    return _preflight_canvas_html(
        """
          let score = 0;
          const grid = [[{ type: 1, anim: 0 }]];
          function init() {
            resetGame();
          }
          function update() {}
          function loop(t) {
            update(t);
            requestAnimationFrame(loop);
          }
          function initGrid() {
            grid[0][0].type = 1;
          }
          init();
          requestAnimationFrame(loop);
        """
    )


def _assert_post_pr86_resetgame_residual_cleared(repaired: str, remaining) -> None:
    match = re.search(r"function resetGame\([^)]*\)\s*\{(?P<body>[^{}]*)\}", repaired)
    assert match, repaired
    body = match.group("body")
    assert "initGrid();" in body
    assert "score = 0;" in body
    assert not any(issue.code == "undefined_symbol:resetGame" for issue in remaining)
    assert not any(issue.code == "tdz_symbol:resetGame" for issue in remaining)


def _post_pr84_grid_puzzle_residual_html() -> str:
    """Mirror the overnight yield residual after PR #84 (grid_puzzle_en run 48)."""
    return _preflight_canvas_html(
        """
          const grid = [[{ type: 1, anim: 0 }]];
          function drawBoard() {
            for (let row = 0; row < rows; row++) {
              for (let col = 0; col < cols; col++) {
                const kind = grid[row][col].type;
                const motion = grid[row][col].anim;
                if (kind && motion > 0) {
                  continue;
                }
              }
            }
          }
          drawBoard();
        """
    )


def _assert_post_pr84_grid_residual_cleared(repaired: str, remaining) -> None:
    assert repaired.count("function __safeGridCell(") == 1
    assert "__safeGridCell(grid," in repaired
    assert "grid[row][col].type" not in repaired
    assert "grid[row][col].anim" not in repaired
    assert "let cols = 0, rows = 0;" in repaired or (
        "let cols = 0;" in repaired and "let rows = 0;" in repaired
    )
    blocking = {
        "unsafe_nested_grid_read",
        "undefined_symbol:__safeGridCell",
        "undefined_symbol:cols",
        "undefined_symbol:rows",
    }
    assert not any(issue.code in blocking for issue in remaining)
    assert not any(issue.code.startswith(("tdz_symbol:", "undefined_symbol:")) for issue in remaining)


def test_code_preflight_repairs_post_pr84_nested_grid_helper_and_cols_rows_residual():
    validator = CodePreflightValidator()
    html = _post_pr84_grid_puzzle_residual_html()
    contract = GameRuntimeContract(runtime_profile="puzzle_grid_match")

    issues = validator.validate(html, runtime_contract=contract)
    messages = " ".join(issue.message for issue in issues)
    assert any(issue.code == "unsafe_nested_grid_read" for issue in issues)
    assert "grid[row][col].type" in messages
    assert "grid[row][col].anim" in messages
    assert any(issue.code == "undefined_symbol:__safeGridCell" for issue in issues) is False
    assert {issue.code for issue in issues} >= {
        "undefined_symbol:cols",
        "undefined_symbol:rows",
    }
    assert any("live expression" in issue.message for issue in issues if issue.code == "undefined_symbol:cols")
    assert any("live expression" in issue.message for issue in issues if issue.code == "undefined_symbol:rows")

    repaired = validator.auto_repair(html, runtime_contract=contract, issues=issues)
    remaining = validator.validate(repaired, runtime_contract=contract)
    _assert_post_pr84_grid_residual_cleared(repaired, remaining)
    assert validator.auto_repair(repaired, runtime_contract=contract, issues=remaining) == repaired

    guidance = validator.render_guidance(issues)
    assert "let rows = 0; let cols = 0;" in guidance
    assert "function __safeGridCell" not in guidance or "getCell" in guidance


def test_code_preflight_injects_safe_grid_helper_when_call_sites_exist_without_definition():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          const grid = [[{ type: 1, anim: 0 }]];
          function readCell(row, col) {
            return (__safeGridCell(grid, row, col)?.type) + ':' + (__safeGridCell(grid, row, col)?.anim);
          }
        """
    )
    contract = GameRuntimeContract(runtime_profile="puzzle_grid_match")
    issues = validator.validate(html, runtime_contract=contract)
    assert any(issue.code == "undefined_symbol:__safeGridCell" for issue in issues)
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in issues)

    repaired = validator.auto_repair(html, runtime_contract=contract, issues=issues)
    remaining = validator.validate(repaired, runtime_contract=contract)
    assert repaired.count("function __safeGridCell(") == 1
    assert not any(issue.code == "undefined_symbol:__safeGridCell" for issue in remaining)
    assert validator.auto_repair(repaired, runtime_contract=contract) == repaired


def test_code_preflight_does_not_duplicate_existing_safe_grid_helper_definition():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          function __safeGridCell(gridRef, row, col) {
            const rowBucket = gridRef && gridRef[row];
            return rowBucket ? rowBucket[col] : null;
          }
          const grid = [[{ type: 1, anim: 0 }]];
          function readCell(row, col) {
            return grid[row][col].type + ':' + grid[row][col].anim;
          }
        """
    )
    contract = GameRuntimeContract(runtime_profile="puzzle_grid_match")
    repaired = validator.auto_repair(html, runtime_contract=contract)
    remaining = validator.validate(repaired, runtime_contract=contract)
    assert repaired.count("function __safeGridCell(") == 1
    assert "grid[row][col].type" not in repaired
    assert "grid[row][col].anim" not in repaired
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in remaining)
    assert not any(issue.code == "undefined_symbol:__safeGridCell" for issue in remaining)


def test_code_preflight_declares_live_cols_rows_without_redeclaring_existing_bindings():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          const rows = 8;
          const cols = 8;
          const grid = [[{ type: 1 }]];
          function readCell(row, col) {
            if (row < rows && col < cols) {
              return grid[row][col].type;
            }
            return null;
          }
        """
    )
    contract = GameRuntimeContract(runtime_profile="puzzle_grid_merge")
    repaired = validator.auto_repair(html, runtime_contract=contract)
    remaining = validator.validate(repaired, runtime_contract=contract)
    assert "const rows = 8;" in repaired
    assert "const cols = 8;" in repaired
    assert "let cols = 0" not in repaired
    assert "let rows = 0" not in repaired
    assert repaired.count("function __safeGridCell(") == 1
    assert not any(
        issue.code in {
            "unsafe_nested_grid_read",
            "undefined_symbol:__safeGridCell",
            "undefined_symbol:cols",
            "undefined_symbol:rows",
        }
        for issue in remaining
    )


def test_code_preflight_keeps_puzzle_helper_hoist_when_repairing_nested_grid_residual():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          function init() {
            initGrid();
            const pos = getEventPos({ clientX: 1, clientY: 2 });
            const cell = getCellAt(0, 0);
            return isAdjacent(0, 1) ? cell : pos;
          }
          init();
          const initGrid = () => {
            for (let row = 0; row < rows; row++) {
              for (let col = 0; col < cols; col++) {
                grid[row][col].type = 1;
              }
            }
          };
          const getEventPos = (e) => ({ x: e.clientX, y: e.clientY });
          let getCellAt = function(r, c) {
            return grid[r] && grid[r][c];
          };
          const isAdjacent = (a, b) => Math.abs(a - b) === 1;
          function paint() {
            return grid[0][0].anim;
          }
        """
    )
    contract = GameRuntimeContract(runtime_profile="puzzle_grid_match")
    issues = validator.validate(html, runtime_contract=contract)
    assert {issue.code for issue in issues} >= {
        "tdz_symbol:initGrid",
        "tdz_symbol:getEventPos",
        "tdz_symbol:getCellAt",
        "tdz_symbol:isAdjacent",
        "undefined_symbol:cols",
        "undefined_symbol:rows",
        "unsafe_nested_grid_read",
    }
    repaired = validator.auto_repair(html, runtime_contract=contract, issues=issues)
    remaining = validator.validate(repaired, runtime_contract=contract)
    assert "function initGrid(" in repaired
    assert "function getEventPos(" in repaired
    assert "function getCellAt(" in repaired
    assert "function isAdjacent(" in repaired
    assert "const initGrid =" not in repaired
    assert "let getCellAt =" not in repaired
    assert repaired.count("function __safeGridCell(") == 1
    assert "grid[0][0].anim" not in repaired
    assert not any(
        issue.code.startswith(("tdz_symbol:", "undefined_symbol:"))
        and issue.code.split(":", 1)[-1]
        in {"initGrid", "getEventPos", "getCellAt", "isAdjacent", "__safeGridCell", "cols", "rows"}
        for issue in remaining
    )
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in remaining)


def test_code_preflight_does_not_flag_nested_grid_property_writes():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          const grid = [[{ type: 0, anim: 0 }]];
          function resetCell(row, col) {
            grid[row][col].type = 1;
            grid[row][col].anim = 0;
          }
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in issues)


def test_code_preflight_repairs_nested_grid_equality_compares_but_not_writes():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          const grid = [[{ type: 1, anim: 0 }]];
          function inspect(row, col) {
            grid[row][col].anim = 0;
            return grid[row][col].type === 1;
          }
        """
    )
    contract = GameRuntimeContract(runtime_profile="puzzle_grid_match")
    issues = validator.validate(html, runtime_contract=contract)
    assert any(issue.code == "unsafe_nested_grid_read" for issue in issues)
    assert any("grid[row][col].type" in issue.message for issue in issues)
    repaired = validator.auto_repair(html, runtime_contract=contract, issues=issues)
    remaining = validator.validate(repaired, runtime_contract=contract)
    assert "grid[row][col].anim = 0;" in repaired
    assert "grid[row][col].type" not in repaired
    assert "(__safeGridCell(grid, row, col)?.type) === 1" in repaired
    assert repaired.count("function __safeGridCell(") == 1
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in remaining)
    assert not any(issue.code == "undefined_symbol:__safeGridCell" for issue in remaining)


def test_code_preflight_auto_repairs_null_ctx_before_the_main_loop():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          let ctx = null;
          function init() {
            ctx = canvas.getContext('2d');
          }
          function loop() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            requestAnimationFrame(loop);
          }
          requestAnimationFrame(loop);
        </script>
      </body>
    </html>
    """

    repaired = validator.auto_repair(html, runtime_contract=GameRuntimeContract())
    issues = validator.validate(repaired, runtime_contract=GameRuntimeContract())

    assert "let ctx = null" in repaired
    assert "__bootCanvas" in repaired
    assert "getContext('2d')" in repaired
    assert not any(issue.code == "nullable_runtime_object:ctx" for issue in issues)


def test_code_preflight_null_ctx_guidance_names_canvas_context_boot():
    guidance = CodePreflightValidator().render_guidance([
        CodePreflightIssue(
            code="nullable_runtime_object:ctx",
            message=(
                "Do not leave `ctx` initialized as null while the main loop can run; "
                "create a safe default canvas context before the loop starts."
            ),
        )
    ])
    assert "safe default canvas context" in guidance or "getContext('2d')" in guidance
    assert "let ctx = null" in guidance


def test_code_preflight_keeps_uninitialized_canvas_visible_to_validation():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <script>
          let canvas = null;
          function boot() {
            canvas = document.getElementById('gameCanvas');
          }
          function loop() {
            canvas.width = 360;
            requestAnimationFrame(loop);
          }
          requestAnimationFrame(loop);
        </script>
      </body>
    </html>
    """

    repaired = validator.auto_repair(html, runtime_contract=GameRuntimeContract())
    issues = validator.validate(repaired, runtime_contract=GameRuntimeContract())

    assert "__safeCanvasElement" not in repaired
    assert "let canvas = null" in repaired
    assert any(issue.code == "nullable_runtime_object:canvas" for issue in issues)


def test_code_preflight_allows_top_level_non_null_assignment_before_first_frame():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const ctx = canvas.getContext('2d');
          let matchedTarget = null;
          matchedTarget = { label: 'series-circuit', active: true };
          function loop() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.fillText(matchedTarget.label, 10, 10);
            requestAnimationFrame(loop);
          }
          requestAnimationFrame(loop);
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert not any(issue.code == "nullable_runtime_object:matchedTarget" for issue in issues)


def test_code_preflight_ignores_comment_text_strings_and_constructor_calls():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          class Player {}
          // MANDATORY LOGIC BUNDLES
          const label = "Arial";
          const hero = new Player();
          function loop() {
            return label + hero.constructor.name;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert not any(issue.code.startswith("undefined_symbol:MANDATORY") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:LOGIC") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:BUNDLES") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:Arial") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:Player") for issue in issues)


def test_code_preflight_understands_multi_declarators_and_destructuring():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          let scaleX = 1, scaleY = 1, uiScale = Math.min(scaleX, scaleY);
          const { lives, level } = { lives: 3, level: 1 };
          const [viewW, viewH] = [360, 640];
          function render() {
            return scaleX + scaleY + uiScale + lives + level + viewW + viewH;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert not any(issue.code.startswith("undefined_symbol:scaleX") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:scaleY") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:uiScale") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:lives") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:level") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:viewW") for issue in issues)
    assert not any(issue.code.startswith("undefined_symbol:viewH") for issue in issues)


def test_code_preflight_flags_undefined_lane_helper_calls():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const player = { lane: 1 };
          function render() {
            const x = player.laneX(player.lane);
            return x;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="casual_lane_dash"))

    assert any(issue.code == "undefined_lane_helper_call:laneX" for issue in issues)


def test_code_preflight_flags_unguarded_nested_grid_property_reads():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const grid = [[{ type: 1 }]];
          function readCell(row, col) {
            return grid[row][col].type;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))

    assert any(issue.code == "unsafe_nested_grid_read" for issue in issues)


def test_code_preflight_allows_guarded_nested_grid_property_reads():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const grid = [[{ type: 1 }]];
          function readCell(row, col) {
            return grid[row] && grid[row][col] ? grid[row][col].type : null;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))

    assert not any(issue.code == "unsafe_nested_grid_read" for issue in issues)


def test_code_preflight_allows_multiline_guarded_nested_grid_property_reads():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const grid = [[{ fruit: 'apple', anim: 0 }]];
          function readCell(row, col) {
            if (!grid[row] || !grid[row][col]) return null;
            const fruit = grid[row][col].fruit;
            return grid[row][col].anim > 0 ? fruit : null;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"))

    assert not any(issue.code == "unsafe_nested_grid_read" for issue in issues)


def test_code_preflight_allows_destructured_placeholder_binding_names():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          function useLaneMarker({ lane: _, speed }) {
            return _ + speed;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert not any(issue.code == "undefined_symbol:_" for issue in issues)


def test_code_preflight_guidance_adds_explicit_safe_grid_read_recipe():
    validator = CodePreflightValidator()
    guidance = validator.render_guidance(
        validator.validate(
            """
            <!DOCTYPE html>
            <html>
              <body>
                <canvas id="gameCanvas"></canvas>
                <script>
                  const canvas = document.getElementById('gameCanvas');
                  canvas.width = 360;
                  canvas.height = 640;
                  const grid = [[{ anim: 1 }]];
                  function readCell(row, col) {
                    return grid[row][col].anim;
                  }
                </script>
              </body>
            </html>
            """,
            runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"),
        )
    )

    assert "const rowBucket = grid[row]; const cell = rowBucket && rowBucket[col];" in guidance
    assert "cell.anim" in guidance
    assert "function getCell(row, col)" in guidance
    assert "cell.fruit" in guidance


def test_code_preflight_guidance_adds_runtime_object_and_layout_recipes():
    validator = CodePreflightValidator()
    guidance = validator.render_guidance(
        validator.validate(
            """
            <!DOCTYPE html>
            <html>
              <body>
                <canvas id="gameCanvas"></canvas>
                <script>
                  const canvas = document.getElementById('gameCanvas');
                  canvas.width = 360;
                  canvas.height = 640;
                  const ctx = canvas.getContext('2d');
                  let dragStartCell = null;
                  function loop() {
                    ctx.clearRect(0, 0, viewWidth, viewHeight);
                    dragStartCell.x += 1;
                    generateBackgroundLayers();
                    requestAnimationFrame(loop);
                  }
                  requestAnimationFrame(loop);
                </script>
              </body>
            </html>
            """,
            runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid_match"),
        )
    )

    assert "dragStartCell" in guidance
    assert "const viewWidth = canvas.width; const viewHeight = canvas.height;" in guidance
    assert "generateBackgroundLayers()" in guidance


def test_code_preflight_guidance_adds_drag_and_line_recipes():
    validator = CodePreflightValidator()
    guidance = validator.render_guidance(
        [
            CodePreflightIssue(code="nullable_runtime_object:dragTarget", message="dragTarget unsafe"),
            CodePreflightIssue(code="undefined_symbol:line", message="line unsafe"),
            CodePreflightIssue(code="undefined_symbol:dot", message="dot unsafe"),
            CodePreflightIssue(code="undefined_symbol:type", message="type unsafe"),
        ]
    )

    assert "let dragTarget = { active: false, row: -1, col: -1, pointerId: null };" in guidance
    assert "const line = lines[index];" in guidance
    assert "const dot = dots[index];" in guidance
    assert "const type = cell.type;" in guidance


def test_code_preflight_guidance_adds_canvas_touch_and_round_rect_recipes():
    validator = CodePreflightValidator()
    guidance = validator.render_guidance(
        [
            CodePreflightIssue(code="canvas_dimensions", message="canvas dims unsafe"),
            CodePreflightIssue(code="unsafe_touch_access", message="touch unsafe"),
            CodePreflightIssue(code="unsafe_canvas_path_chain", message="round rect unsafe"),
        ]
    )

    assert "canvas.width = Math.round(viewportWidth); canvas.height = Math.round(viewportHeight);" in guidance
    assert "const touch = (e.touches && e.touches.length ? e.touches[0]" in guidance
    assert "ctx.beginPath(); ctx.roundRect(...); ctx.fill();" in guidance


def test_code_preflight_flags_unsafe_gradient_alpha_suffix_concat():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 640;
          canvas.height = 360;
          const ctx = canvas.getContext('2d');
          const light = { color: 'hsl(0, 80%, 60%)' };
          function renderBeam() {
            const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
            gradient.addColorStop(0, light.color + '80');
            gradient.addColorStop(1, light.color + '00');
            ctx.fillStyle = gradient;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert any(issue.code == "unsafe_color_alpha_concat" for issue in issues)


def test_code_preflight_auto_repairs_unsafe_gradient_alpha_suffix_concat():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 640;
          canvas.height = 360;
          const ctx = canvas.getContext('2d');
          const light = { color: 'hsl(0, 80%, 60%)' };
          function renderBeam() {
            const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
            gradient.addColorStop(0, light.color + '80');
            gradient.addColorStop(1, light.color + '00');
            ctx.fillStyle = gradient;
          }
        </script>
      </body>
    </html>
    """

    repaired = validator.auto_repair(html, runtime_contract=GameRuntimeContract())
    issues = validator.validate(repaired, runtime_contract=GameRuntimeContract())

    assert "__withAlpha(light.color, 0.502)" in repaired
    assert "__withAlpha(light.color, 0)" in repaired
    assert not any(issue.code == "unsafe_color_alpha_concat" for issue in issues)


def test_code_preflight_guidance_mentions_hsla_for_alpha_suffix_concat():
    validator = CodePreflightValidator()
    guidance = validator.render_guidance(
        [
            CodePreflightIssue(
                code="unsafe_color_alpha_concat",
                message="Do not concatenate alpha suffixes onto dynamic colors.",
            )
        ]
    )

    assert "light.color + '80'" in guidance
    assert "hsla(0, 80%, 60%, 0.5)" in guidance


def test_code_preflight_treats_catch_params_as_declared_symbols():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          function report() {
            try {
              riskyCall();
            } catch (p) {
              if (p) {
                console.warn(p.message || p);
              }
            }
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert not any(issue.code == "undefined_symbol:p" for issue in issues)


def test_code_preflight_allows_combined_guard_for_nullable_runtime_object():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const ctx = canvas.getContext('2d');
          let dragStart = null;
          let dragging = false;
          function loop() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (!dragging || !dragStart) return;
            dragStart.x += 1;
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert not any(issue.code == "nullable_runtime_object:dragStart" for issue in issues)


def test_code_preflight_treats_for_of_declarators_as_declared_symbols():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          const cells = [{ x: 1 }, { x: 2 }];
          function render() {
            for (const c of cells) {
              console.log(c.x);
            }
          }
        </script>
      </body>
    </html>
    """

    issues = validator.validate(html, runtime_contract=GameRuntimeContract())

    assert not any(issue.code == "undefined_symbol:c" for issue in issues)


def test_standard_global_functions_are_not_undefined_but_missing_helpers_are():
    html = """<html><body><script>
      const n = parseInt('12', 10);
      if (!isNaN(n) && isFinite(n)) console.log(n);
      encodeURI('hello'); encodeURIComponent('hello');
      decodeURI('hello'); decodeURIComponent('hello');
      missingGameHelper();
    </script></body></html>"""
    issues = CodePreflightValidator().validate(html, runtime_contract=GameRuntimeContract())
    undefined = {issue.code for issue in issues if issue.code.startswith('undefined_symbol:')}
    assert undefined == {'undefined_symbol:missingGameHelper'}


def test_preflight_preserves_lazy_canvas_initialization_and_real_ellipse_rendering():
    import asyncio
    from playwright.async_api import async_playwright
    source = """<!DOCTYPE html><html><body><canvas id="gameCanvas" width="80" height="80"></canvas>
    <script>
    let canvas = null;
    let ctx = null;
    function init() {
      if (ctx) return;
      canvas = document.getElementById('gameCanvas');
      ctx = canvas.getContext('2d');
    }
    init();
    ctx.fillStyle = '#ff0000';
    ctx.beginPath();
    ctx.ellipse(40,40,20,10,0,0,Math.PI*2);
    ctx.fill();
    </script></body></html>"""
    repaired = CodePreflightValidator().auto_repair(source, runtime_contract=GameRuntimeContract())
    assert "__safeCanvas" not in repaired
    async def check():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            try:
                page = await browser.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                await page.set_content(repaired)
                pixel = await page.evaluate("Array.from(document.getElementById('gameCanvas').getContext('2d').getImageData(40,40,1,1).data)")
                assert not errors
                assert pixel == [255,0,0,255]
            finally:
                await browser.close()
    asyncio.run(check())


def _preflight_canvas_html(script: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 640;
          canvas.height = 480;
          {script}
        </script>
      </body>
    </html>
    """


def test_code_preflight_repairs_concatenated_canvas_host_methods():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          const ctx = canvas.getContext('2d');
          function draw(snake) {
            ctxclearRect(0, 0, canvas.width, canvas.height);
            ctxbeginPath();
            ctxarc(snake.x, snake.y, 8, 0, Math.PI * 2);
            ctxfill();
          }
          draw({x: 20, y: 20});
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {
        "undefined_symbol:ctxbeginPath",
        "undefined_symbol:ctxclearRect",
        "undefined_symbol:ctxarc",
        "undefined_symbol:ctxfill",
    }
    repaired = validator.auto_repair(html, issues=issues)
    assert "ctx.beginPath(" in repaired
    assert "ctx.clearRect(" in repaired
    assert "ctx.arc(" in repaired
    assert "ctx.fill(" in repaired
    assert "ctxbeginPath" not in repaired
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(
        issue.code.startswith("undefined_symbol:ctx") for issue in remaining
    )
    guidance = validator.render_guidance(issues)
    assert "ctx.beginPath()" in guidance
    assert "ctxbeginPath()" in guidance


def test_code_preflight_declares_literal_compared_live_state_without_hardcoded_names():
    validator = CodePreflightValidator()
    html = _preflight_canvas_html(
        """
          function step(flag) { return flag; }
          function loop() {
            if (gameState === 'playing' || 'paused' === phase) {
              step(boost);
            }
          }
          document.addEventListener('keydown', (event) => {
            if (event.key === ' ') gameState = 'playing';
          });
        """
    )
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {
        "undefined_symbol:gameState",
        "undefined_symbol:phase",
        "undefined_symbol:boost",
    }
    repaired = validator.auto_repair(html, issues=issues)
    declared = re.search(r"\blet\s+([^;]+);", repaired)
    assert declared, repaired
    declared_names = {part.strip() for part in declared.group(1).split(",")}
    assert {"gameState", "phase"} <= declared_names
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(
        issue.code in {
            "undefined_symbol:gameState",
            "undefined_symbol:phase",
        }
        for issue in remaining
    )
    assert any(issue.code == "undefined_symbol:boost" for issue in remaining)


def test_code_preflight_declares_event_attribute_assigned_live_bindings():
    validator = CodePreflightValidator()
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <button onclick="gameState='playing'; hustle=true">开始</button>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 640;
          canvas.height = 480;
          function tick(flag) { return flag; }
          function loop() {
            if (gameState === 'playing') {
              tick(hustle);
            }
          }
        </script>
      </body>
    </html>
    """
    issues = validator.validate(html, runtime_contract=GameRuntimeContract())
    assert {issue.code for issue in issues} >= {
        "undefined_symbol:gameState",
        "undefined_symbol:hustle",
    }
    repaired = validator.auto_repair(html, issues=issues)
    declared = re.search(r"\blet\s+([^;]+);", repaired)
    assert declared, repaired
    declared_names = {part.strip() for part in declared.group(1).split(",")}
    assert {"gameState", "hustle"} <= declared_names
    remaining = validator.validate(repaired, runtime_contract=GameRuntimeContract())
    assert not any(
        issue.code in {"undefined_symbol:gameState", "undefined_symbol:hustle"}
        for issue in remaining
    )
