import os
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

    assert "__safeGridCell" in repaired
    assert "grid[row][col].type" not in repaired
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in issues)


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
    assert "__safeGridCell" in repaired
    assert "grid[row][col].targetX" not in repaired
    assert "grid[row][col].targetY" not in repaired
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in issues)


def test_code_preflight_keeps_uninitialized_ctx_visible_to_validation():
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

    assert "__safeCanvasContext" not in repaired
    assert "let ctx = null" in repaired
    assert any(issue.code == "nullable_runtime_object:ctx" for issue in issues)


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
