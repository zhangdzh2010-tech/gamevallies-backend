import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import GameRuntimeContract
from src.engine.pipeline_v2_runner import V2PipelineRunner
from src.engine.qa_pipeline import QAPipeline
from src.engine.restart_entry import has_restart_entry


BOOT_INIT_RESTART_CODE = """
<!DOCTYPE html>
<html>
  <body>
    <canvas id="gameCanvas"></canvas>
    <script>
      let state = 'ready';
      function boot() {}
      function init() {}
      function handleStart() {
        if (state === 'ready') {
          state = 'playing';
          return;
        }
        if (state === 'game_over') {
          boot();
          init();
          return;
        }
      }
      const canvas = document.getElementById('gameCanvas');
      canvas.addEventListener('pointerdown', handleStart);
      document.body.innerText = '点击重新开始';
    </script>
  </body>
</html>
"""

NAMED_RESTART_GAME_CODE = """
<!DOCTYPE html>
<html>
  <body>
    <canvas id="gameCanvas"></canvas>
    <script>
      const game = { state: 'game_over' };
      function restartGame() {
        game.state = 'ready';
      }
    </script>
  </body>
</html>
"""

TERMINAL_CREATE_RESET_CODE = """
<!DOCTYPE html>
<html>
  <body>
    <canvas id="gameCanvas"></canvas>
    <script>
      let state = 'ready';
      function create() {
        state = 'ready';
      }
      function handleTap() {
        if (state === 'level_complete' || state === 'game_over') {
          state = 'ready';
          create();
          return;
        }
        if (state === 'ready') {
          state = 'playing';
        }
      }
      const canvas = document.getElementById('gameCanvas');
      canvas.addEventListener('pointerdown', handleTap);
    </script>
  </body>
</html>
"""

BOUND_HANDLER_TERMINAL_RESET_CODE = """
<!DOCTYPE html>
<html>
  <body>
    <canvas id="gameCanvas"></canvas>
    <script>
      let state = 'ready';
      let gameOver = false;
      let level = 1;
      let score = 0;
      let lives = 3;

      function generateLevel(nextLevel) {
        level = nextLevel;
      }

      function handleTap() {
        if (state === 'level_complete') {
          if (gameOver) {
            state = 'ready';
            level = 1;
            score = 0;
            lives = 3;
            gameOver = false;
            return;
          }
          level += 1;
          generateLevel(level);
          state = 'playing';
          return;
        }
        if (state === 'ready') {
          state = 'playing';
        }
      }

      const canvas = document.getElementById('gameCanvas');
      canvas.addEventListener('pointerdown', handleTap);
    </script>
  </body>
</html>
"""


def test_restart_entry_helper_accepts_boot_init_terminal_branch():
    assert has_restart_entry(BOOT_INIT_RESTART_CODE) is True


def test_restart_entry_helper_accepts_named_restart_game_function():
    assert has_restart_entry(NAMED_RESTART_GAME_CODE) is True


def test_restart_entry_helper_accepts_terminal_branch_that_resets_state_and_recreates_board():
    assert has_restart_entry(TERMINAL_CREATE_RESET_CODE) is True


def test_restart_entry_helper_accepts_bound_handler_that_resets_terminal_progress_inline():
    assert has_restart_entry(BOUND_HANDLER_TERMINAL_RESET_CODE) is True


def test_contract_runtime_validation_accepts_boot_init_terminal_branch():
    runner = V2PipelineRunner()
    errors = runner._validate_runtime_contract(BOOT_INIT_RESTART_CODE, GameRuntimeContract())

    assert not any(
        error.type == "contract_gameplay"
        and "restart entry point" in error.message
        for error in errors
    )


def test_l4_playability_warning_accepts_boot_init_terminal_branch():
    pipeline = QAPipeline()
    _errors, warnings = pipeline._check_l4_playability(BOOT_INIT_RESTART_CODE)

    assert not any("restart/reset function" in warning.message for warning in warnings)


def test_contract_runtime_validation_accepts_terminal_branch_that_recreates_board():
    runner = V2PipelineRunner()
    errors = runner._validate_runtime_contract(TERMINAL_CREATE_RESET_CODE, GameRuntimeContract())

    assert not any(
        error.type == "contract_gameplay"
        and "restart entry point" in error.message
        for error in errors
    )


def test_l4_playability_warning_accepts_terminal_branch_that_recreates_board():
    pipeline = QAPipeline()
    _errors, warnings = pipeline._check_l4_playability(TERMINAL_CREATE_RESET_CODE)

    assert not any("restart/reset function" in warning.message for warning in warnings)


def test_contract_runtime_validation_accepts_bound_handler_terminal_reset_path():
    runner = V2PipelineRunner()
    errors = runner._validate_runtime_contract(BOUND_HANDLER_TERMINAL_RESET_CODE, GameRuntimeContract())

    assert not any(
        error.type == "contract_gameplay"
        and "restart entry point" in error.message
        for error in errors
    )
