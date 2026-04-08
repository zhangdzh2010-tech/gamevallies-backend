"""Regression tests for v2 pipeline runtime-contract validation."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    GameRuntimeContract,
    GameSpec,
    IterateV2Request,
    QACheckError,
    RunPipelineV2Request,
    SourceBundleContext,
    SourceBundleRevision,
)
from src.engine.pipeline_v2_runner import V2PipelineRunner
from src.engine.pipeline_orchestrator import PipelineExecutionError


def test_function_keyword_is_not_flagged_as_function_constructor():
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <script>
          function startGame() {
            return true;
          }
        </script>
      </body>
    </html>
    """

    assert V2PipelineRunner._contains_forbidden_api(code, "Function") is False


def test_function_constructor_is_still_flagged():
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <script>
          const makeValue = new Function('return 42;');
        </script>
      </body>
    </html>
    """

    assert V2PipelineRunner._contains_forbidden_api(code, "Function") is True


def test_runtime_contract_accepts_completion_state_constant_alias():
    runner = V2PipelineRunner()
    code = """
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          const ctx = canvas.getContext('2d');
          canvas.width = 360;
          canvas.height = 640;
          const STATE_READY = 'ready';
          const STATE_WIN = 'win';
          let state = STATE_READY;
          let score = 0;
          function restartGame() { state = STATE_READY; }
          canvas.addEventListener('pointerdown', function () {
            state = STATE_WIN;
            score += 1;
          });
          function loop() {
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            requestAnimationFrame(loop);
          }
          requestAnimationFrame(loop);
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, GameRuntimeContract(runtime_profile="casual_action"))
    assert not any("terminal or completion state" in error.message.lower() for error in errors)


def test_runtime_contract_accepts_webgl_context_when_canvas2d_is_not_required():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract(runtime_profile="casual_action")
    code = """
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          const gl = canvas.getContext('webgl');
          canvas.width = 360;
          canvas.height = 640;
          let state = 'ready';
          function restartGame() { state = 'ready'; }
          function startGame() { state = 'playing'; }
          canvas.addEventListener('pointerdown', function () {
            startGame();
            gl.viewport(0, 0, canvas.width, canvas.height);
          });
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)
    assert not any(error.type == "contract_canvas" for error in errors)


def test_runtime_contract_treats_boot_and_ready_as_same_startup_phase():
    runner = V2PipelineRunner()
    code = """
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          const ctx = canvas.getContext('2d');
          canvas.width = 360;
          canvas.height = 640;
          let state = 'boot';
          let score = 0;
          function restartGame() { state = 'boot'; }
          function startGame() { state = 'playing'; }
          function finishLevel() { state = 'level_complete'; }
          canvas.addEventListener('pointerdown', function () { startGame(); score += 1; finishLevel(); });
          function loop() {
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            requestAnimationFrame(loop);
          }
          requestAnimationFrame(loop);
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, GameRuntimeContract(runtime_profile="puzzle_grid"))
    assert not any("requires state 'ready'" in error.message.lower() for error in errors)


def test_contract_qa_loop_passes_prompt_bundle_snapshot_to_repair_code():
    runner = V2PipelineRunner()
    error = QACheckError(
        type="contract_safety",
        message="Runtime contract forbids API usage: eval",
        severity="error",
    )
    prompt_bundle_snapshot = {
        "layers": {
            "resolved_prompts": {
                "repair_forbidden_api": {"content": "FORBIDDEN_ONLY::{error_list}::{code}"}
            }
        }
    }

    with patch.object(
        runner,
        "_validate_contract_bundle",
        side_effect=[[error], []],
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fixed</body></html>"),
    ) as mock_repair:
        result = asyncio.run(
            runner._run_contract_qa_loop(
                code="<!DOCTYPE html><html><body><script>eval('x')</script></body></html>",
                spec=GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot=prompt_bundle_snapshot,
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
                max_retries=1,
            )
        )

    assert result.success is True
    kwargs = mock_repair.await_args.kwargs
    assert kwargs["prompt_bundle_snapshot"] == prompt_bundle_snapshot


def test_source_bundle_context_summary_avoids_repeating_latest_feedback_text():
    summary = V2PipelineRunner._summarize_source_bundle_context(
        SourceBundleContext(
            title="Office Runner",
            latest_bundle_version=7,
            latest_game_type="funny",
            latest_feedback="make it faster",
            latest_iteration_type="param_adjust",
            summary="bright office chase",
            recent_revisions=[
                SourceBundleRevision(
                    version=7,
                    feedback="make it faster",
                    iteration_type="param_adjust",
                    summary="tightened movement speed",
                ),
                SourceBundleRevision(
                    version=6,
                    feedback="add coins",
                    iteration_type="element_change",
                    summary="added a coin lane",
                ),
            ],
        )
    )

    assert "latest_feedback=" not in summary
    assert "make it faster" not in summary
    assert "tightened movement speed" in summary
    assert "added a coin lane" in summary


def test_contract_qa_loop_caps_targeted_repairs_to_one_round():
    runner = V2PipelineRunner()
    error = QACheckError(
        type="contract_gameplay",
        message="Runtime contract requires primary touch or pointer gameplay handlers",
        severity="error",
    )

    with patch.object(
        runner,
        "_validate_contract_bundle",
        side_effect=[[error], [error], []],
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fix-1</body></html>"),
    ) as mock_repair:
        result = asyncio.run(
            runner._run_contract_qa_loop(
                code="<!DOCTYPE html><html><body>broken</body></html>",
                spec=GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot={"layers": {}},
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
                max_retries=3,
            )
        )

    assert result.success is False
    assert result.retries == 1
    assert mock_repair.await_count == 1


def test_runtime_qa_loop_caps_targeted_remediation_to_one_round():
    runner = V2PipelineRunner()
    runtime_fail = SimpleNamespace(
        ran=True,
        js_errors=[],
        canvas_renders=True,
        registered_input_handlers=[],
        direct_input_handlers=[],
        triggered_input_handlers=[],
        interaction_performed=True,
        canvas_changed_after_input=True,
        dom_changed_after_input=False,
    )
    with patch(
        "src.engine.pipeline_v2_runner.run_runtime_qa",
        new=AsyncMock(side_effect=[runtime_fail, runtime_fail, runtime_fail]),
    ), patch(
        "src.engine.pipeline_v2_runner.settings.RUNTIME_QA_REMEDIATION_MAX_RETRIES",
        2,
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fix-1</body></html>"),
    ) as mock_repair, patch.object(
        runner,
        "_run_contract_qa_loop",
        new=AsyncMock(return_value=SimpleNamespace(
            success=True,
            code="<!DOCTYPE html><html><body>fix-1-pass</body></html>",
            retries=0,
        )),
    ):
        with pytest.raises(PipelineExecutionError) as exc_info:
            asyncio.run(
                runner._run_runtime_qa_loop(
                    code="<!DOCTYPE html><html><body>initial</body></html>",
                    spec=GameSpec(game_type="casual"),
                    runtime_contract=GameRuntimeContract(),
                    prompt_bundle_snapshot={"layers": {}},
                    progress_cb=None,
                    game_id="game-1",
                    user_id="user-1",
                )
            )

    assert "failed runtime QA" in str(exc_info.value)
    assert mock_repair.await_count == 1


def test_runtime_qa_timeout_scales_with_candidate_size_and_remediation_round():
    runner = V2PipelineRunner()
    small_code = "<!DOCTYPE html><html><body>tiny</body></html>"
    large_code = "<!DOCTYPE html><html><body>" + ("A" * 20000) + "</body></html>"
    very_large_code = "<!DOCTYPE html><html><body>" + ("B" * 36000) + "</body></html>"

    with patch(
        "src.engine.pipeline_v2_runner.settings.RUNTIME_QA_TIMEOUT_S",
        8.0,
    ):
        small_timeout = runner._resolve_runtime_qa_timeout(small_code, attempt=0)
        large_timeout = runner._resolve_runtime_qa_timeout(large_code, attempt=0)
        remediated_timeout = runner._resolve_runtime_qa_timeout(large_code, attempt=1)
        very_large_timeout = runner._resolve_runtime_qa_timeout(very_large_code, attempt=1)

    assert small_timeout == 8.0
    assert large_timeout == 10.0
    assert remediated_timeout == 14.0
    assert very_large_timeout == 30.0


def test_runtime_qa_loop_treats_static_input_handlers_as_valid_signal():
    runner = V2PipelineRunner()
    runtime_pass = SimpleNamespace(
        ran=True,
        js_errors=[],
        canvas_renders=True,
        registered_input_handlers=[],
        direct_input_handlers=[],
        triggered_input_handlers=[],
        interaction_performed=True,
        canvas_changed_after_input=True,
        dom_changed_after_input=False,
        unavailable_reason=None,
    )
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.addEventListener('pointerdown', function handleTap() {});
        </script>
      </body>
    </html>
    """

    with patch(
        "src.engine.pipeline_v2_runner.run_runtime_qa",
        new=AsyncMock(return_value=runtime_pass),
    ), patch(
        "src.engine.pipeline_v2_runner.settings.RUNTIME_QA_REMEDIATION_MAX_RETRIES",
        0,
    ):
        final_code, runtime_qa, retries, qa_warnings = asyncio.run(
            runner._run_runtime_qa_loop(
                code=code,
                spec=GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot={"layers": {}},
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
            )
        )

    assert final_code == code
    assert runtime_qa.canvas_changed_after_input is True
    assert retries == 0
    assert qa_warnings == []


def test_runtime_qa_loop_accepts_dom_visible_feedback_when_canvas_pixels_do_not_change():
    runner = V2PipelineRunner()
    runtime_pass = SimpleNamespace(
        ran=True,
        js_errors=[],
        canvas_renders=True,
        registered_input_handlers=["pointerdown"],
        direct_input_handlers=[],
        triggered_input_handlers=["pointerdown"],
        interaction_performed=True,
        canvas_changed_after_input=False,
        dom_changed_after_input=True,
        unavailable_reason=None,
    )
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <div id="hud">Ready</div>
        <script>
          const canvas = document.getElementById('gameCanvas');
          canvas.addEventListener('pointerdown', function handleTap() {
            document.getElementById('hud').textContent = 'Started';
          });
        </script>
      </body>
    </html>
    """

    with patch(
        "src.engine.pipeline_v2_runner.run_runtime_qa",
        new=AsyncMock(return_value=runtime_pass),
    ), patch(
        "src.engine.pipeline_v2_runner.settings.RUNTIME_QA_REMEDIATION_MAX_RETRIES",
        0,
    ):
        final_code, runtime_qa, retries, qa_warnings = asyncio.run(
            runner._run_runtime_qa_loop(
                code=code,
                spec=GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot={"layers": {}},
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
            )
        )

    assert final_code == code
    assert runtime_qa.dom_changed_after_input is True
    assert retries == 0
    assert qa_warnings == []


def test_select_runtime_profile_normalizes_descriptive_game_type_labels():
    runner = V2PipelineRunner()

    assert runner._select_runtime_profile(GameSpec(game_type="casual", source_description="race through traffic"), "casual_arcade") == "casual_lane_dash"
    assert runner._select_runtime_profile(GameSpec(game_type="casual", source_description="shoot targets quickly"), "casual_arcade") == "casual_action_arena"
    assert runner._select_runtime_profile(GameSpec(game_type="puzzle"), "casual_arcade") == "puzzle_grid_route"


def test_select_runtime_profile_biases_educational_requests_to_puzzle_grid():
    runner = V2PipelineRunner()

    spec = GameSpec(
        game_type="casual",
        source_description="请围绕浮力知识点设计一个课堂小游戏，包含3道配套练习题和计分方式。",
        intent_summary="课堂小游戏 + 练习题",
    )

    assert runner._select_runtime_profile(spec, "casual_arcade") == "puzzle_grid"


def test_select_runtime_profile_can_vary_for_sparse_diversity_seed():
    runner = V2PipelineRunner()
    spec = GameSpec(
        game_type="casual",
        source_description="avoid asteroids",
        intent_summary="avoid hazards and stay alive",
        special_rules=[
            "Favor a distinctive gameplay loop instead of the most common default for this genre.",
        ],
    )

    with patch("src.engine.pipeline_v2_runner._default_runtime_profile_id", return_value="casual_arcade"):
        first = runner._select_runtime_profile(spec, "casual_arcade", variation_seed="game-a")
        second = runner._select_runtime_profile(spec, "casual_arcade", variation_seed="game-b")

    assert first in {"casual_action", "casual_arcade", "casual_lane", "casual_arcade_orbit", "casual_arcade_burst"}
    assert second in {"casual_action", "casual_arcade", "casual_lane", "casual_arcade_orbit", "casual_arcade_burst"}
    assert first != second


def test_validate_runtime_contract_accepts_generic_short_edge_scaling_patterns():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract()
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
          let scale = 1;
          function resizeCanvas() {
            const containerWidth = window.innerWidth;
            const containerHeight = window.innerHeight;
            scale = Math.min(containerWidth / 360, containerHeight / 640);
            canvas.width = 360;
            canvas.height = 640;
          }
          function restartGame() {}
          const state = 'ready';
          let gameOver = false;
          let score = 0;
          canvas.addEventListener('pointerdown', function() {});
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)

    assert not any(
        error.type == "contract_mobile"
        and "portrait-first short-edge UI scaling" in error.message
        for error in errors
    )


def test_validate_runtime_contract_requires_landscape_short_edge_scaling_when_requested():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract(
        canvas={"orientation": "landscape_first"},
        mobile_layout={"orientation": "landscape_first"},
    )
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
          canvas.width = 640;
          canvas.height = 360;
          let gameOver = false;
          let score = 0;
          function restartGame() {}
          canvas.addEventListener('pointerdown', function() {
            score += 1;
            gameOver = true;
          });
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)

    assert any(
        error.type == "contract_mobile"
        and "landscape-first short-edge UI scaling" in error.message
        for error in errors
    )


def test_compose_runtime_contract_preserves_landscape_orientation():
    runner = V2PipelineRunner()
    contract = runner._compose_runtime_contract(
        base_contract=GameRuntimeContract(
            canvas={"orientation": "landscape_first"},
            mobile_layout={"orientation": "landscape_first"},
        ),
        spec=GameSpec(game_type="casual"),
        runtime_profile="casual_lane",
        entrypoint="create",
    )

    assert contract.canvas.orientation == "landscape_first"
    assert contract.mobile_layout.orientation == "landscape_first"
    assert contract.metadata["orientation"] == "landscape_first"


def test_build_gdd_uses_landscape_canvas_for_landscape_contracts():
    runner = V2PipelineRunner()
    gdd = asyncio.run(
        runner._build_gdd(
            GameSpec(game_type="casual"),
            GameRuntimeContract(
                canvas={"orientation": "landscape_first"},
                mobile_layout={"orientation": "landscape_first"},
            ),
        )
    )

    assert gdd.canvas.width > gdd.canvas.height


def test_validate_runtime_contract_accepts_aspect_ratio_fit_without_explicit_ui_scale_token():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract()
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
          const REF_W = 360;
          const REF_H = 640;
          let renderW = REF_W;
          let renderH = REF_H;
          let state = 'boot';
          let score = 0;

          function resizeCanvas() {
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            const ratio = REF_W / REF_H;
            const viewportRatio = vw / vh;
            if (viewportRatio > ratio) {
              renderH = vh;
              renderW = Math.floor(renderH * ratio);
            } else {
              renderW = vw;
              renderH = Math.floor(renderW / ratio);
            }
            canvas.width = renderW;
            canvas.height = renderH;
            canvas.style.width = renderW + 'px';
            canvas.style.height = renderH + 'px';
          }

          function restartGame() { state = 'ready'; }
          function startGame() { state = 'playing'; }
          function finishGame() { state = 'game_over'; }

          window.addEventListener('resize', resizeCanvas);
          resizeCanvas();
          canvas.addEventListener('pointerdown', function handleTap() {
            startGame();
            score += 1;
            finishGame();
          });
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)

    assert not any(
        error.type == "contract_mobile"
        and "portrait-first short-edge UI scaling" in error.message
        for error in errors
    )


def test_validate_runtime_contract_accepts_visible_combo_hud_as_scoring_loop():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract()
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <div id="combo">0</div>
        <script>
          let combo = 0;
          function restartGame() {}
          function setState(nextState) {
            window.gameState = nextState;
          }
          const comboEl = document.getElementById('combo');
          comboEl.textContent = String(combo);
          const canvas = document.getElementById('gameCanvas');
          canvas.addEventListener('pointerdown', function handleTap() {
            combo += 1;
            comboEl.textContent = String(combo);
            setState('game_over');
          });
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)

    assert not any(
        error.type == "contract_gameplay"
        and "visible scoring loop" in error.message
        for error in errors
    )


def test_validate_runtime_contract_accepts_playforge_score_bridge_as_scoring_loop():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract()
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          window.__playforgeScoreBridgeInstalled = true;
          const playforgeScoreHud = document.createElement('div');
          playforgeScoreHud.id = 'playforgeScoreHud';
          playforgeScoreHud.textContent = 'Score: 0';
          document.body.appendChild(playforgeScoreHud);
          function restartGame() {}
          window.gameState = 'playing';
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)

    assert not any(
        error.type == "contract_gameplay"
        and "visible scoring loop" in error.message
        for error in errors
    )


def test_validate_runtime_contract_accepts_object_mode_terminal_state_transition():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract()
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const state = { mode: 'ready', score: 0 };
          function restartGame() {}
          function startGame() { state.mode = 'playing'; }
          function finishRun() { state.mode = 'game_over'; }
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          canvas.addEventListener('pointerdown', function handleTap() {
            startGame();
            state.score += 1;
            finishRun();
          });
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)

    assert not any(
        error.type == "contract_gameplay"
        and "terminal or completion state" in error.message.lower()
        for error in errors
    )


def test_validate_runtime_contract_allows_sandbox_loops_without_blocking_score_or_terminal_errors():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract(runtime_profile="casual_action")
    code = """
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
      </head>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const canvas = document.getElementById('gameCanvas');
          const ctx = canvas.getContext('2d');
          canvas.width = 360;
          canvas.height = 640;
          let state = 'boot';
          function restartGame() { state = 'ready'; }
          function startGame() { state = 'playing'; }
          canvas.addEventListener('pointerdown', function handleTap() {
            startGame();
            ctx.fillRect(0, 0, 32, 32);
          });
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)
    assert not any(
        error.type == "contract_gameplay"
        and "visible scoring loop" in error.message.lower()
        for error in errors
    )
    assert not any(
        error.type == "contract_gameplay"
        and "terminal or completion state" in error.message.lower()
        for error in errors
    )


def test_validate_runtime_contract_accepts_object_phase_completion_state_transition():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract(
        runtime_profile="puzzle_grid",
        gameplay={
            "requires_player_entity": False,
            "requires_scoring": False,
            "requires_terminal_state": True,
            "requires_restart_entry": True,
            "terminal_state_aliases": ["level_complete", "complete", "completed"],
        },
    )
    code = """
    <!DOCTYPE html>
    <html>
      <body>
        <canvas id="gameCanvas"></canvas>
        <script>
          const state = { phase: 'ready', moves: 0 };
          function restartLevel() { state.phase = 'ready'; }
          function startLevel() { state.phase = 'playing'; }
          function completeLevel() { state.phase = 'level_complete'; }
          const canvas = document.getElementById('gameCanvas');
          canvas.width = 360;
          canvas.height = 640;
          canvas.addEventListener('touchstart', function handleTouch() {
            startLevel();
            completeLevel();
          });
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)

    assert not any(
        error.type == "contract_gameplay"
        and "terminal or completion state" in error.message.lower()
        for error in errors
    )


def test_validate_runtime_contract_accepts_puzzle_grid_completion_state_without_score_loop():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract(
        runtime_profile="puzzle_grid",
        state={
            "required_states": ["boot", "ready", "playing", "level_complete"],
            "required_flags": ["levelComplete", "currentLevel", "showHint"],
            "restartable": True,
        },
        input={
            "required_modes": ["touch"],
            "gestures": ["tap", "drag"],
            "allow_mouse_fallback": True,
        },
        gameplay={
            "requires_player_entity": False,
            "requires_scoring": False,
            "requires_terminal_state": True,
            "requires_restart_entry": True,
            "terminal_state_aliases": [
                "level_complete",
                "completed",
                "complete",
                "solved",
            ],
            "primary_goal": "grid_completion",
        },
    )
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
          let state = 'boot';
          let levelComplete = false;
          let currentLevel = 1;
          let showHint = false;
          function restartLevel() {
            state = 'ready';
            levelComplete = false;
          }
          function beginLevel() {
            state = 'playing';
          }
          function solveLevel() {
            levelComplete = true;
            state = 'level_complete';
          }
          canvas.addEventListener('touchstart', function handleTouch() {
            beginLevel();
            solveLevel();
          });
          canvas.width = 360;
          canvas.height = 640;
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)

    assert not any(
        error.type == "contract_gameplay"
        and "visible scoring loop" in error.message
        for error in errors
    )
    assert not any(
        error.type == "contract_gameplay"
        and "terminal or completion state" in error.message.lower()
        for error in errors
    )


def test_build_create_spec_passes_title_and_runtime_profile_hint_into_parser():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-1",
        user_id="user-1",
        raw_user_input="继续增加关卡，设置5个关卡",
        title="逮小猪",
        runtime_contract=GameRuntimeContract(runtime_profile="casual_arcade"),
    )

    with patch.object(
        runner,
        "_parse_spec_with_retries",
        new=AsyncMock(return_value=GameSpec(game_type="casual")),
    ) as mock_parse:
        spec = asyncio.run(runner._build_create_spec(request))

    assert spec.game_type == "casual"
    kwargs = mock_parse.await_args.kwargs
    assert kwargs["description"] == "继续增加关卡，设置5个关卡"
    assert kwargs["title"] == "逮小猪"
    assert kwargs["preferred_game_type"] == "casual"


def test_build_iteration_spec_merges_with_source_spec_history():
    runner = V2PipelineRunner()
    request = IterateV2Request(
        game_id="game-iter",
        user_id="user-iter",
        current_code="<!DOCTYPE html><html><head><title>逮小猪</title></head><body></body></html>",
        iteration_intent={
            "feedback": "继续增加关卡，设置5个关卡",
            "conversation": [{"role": "user", "content": "保留逮小猪主题"}],
        },
        source_spec=GameSpec(
            game_type="casual",
            intent_summary="逮住小猪并躲开障碍",
            ui_language="zh-CN",
        ),
        source_bundle_context=SourceBundleContext(
            title="逮小猪",
            latest_bundle_version=2,
            latest_game_type="casual",
            latest_feedback="把障碍再清楚一些",
        ),
        runtime_contract=GameRuntimeContract(runtime_profile="casual_arcade"),
    )

    parsed_spec = GameSpec(
        game_type="casual",
        intent_summary="新增五个关卡并提升节奏",
        ui_language="zh-CN",
        special_rules=["包含5个关卡"],
    )

    with patch(
        "src.engine.pipeline_v2_runner.require_prompt",
        return_value=(
            "Current game context: {current_summary}\n"
            "Source spec summary: {source_spec_summary}\n"
            "Historical bundle context: {source_bundle_context}\n"
            "Requested iteration: {feedback}\n"
            "Conversation context: {conversation_text}"
        ),
    ), patch.object(
        runner,
        "_parse_spec_with_retries",
        new=AsyncMock(return_value=parsed_spec),
    ):
        spec = asyncio.run(runner._build_iteration_spec(request))

    assert spec.game_type == "casual"
    assert spec.visual_style.theme == request.source_spec.visual_style.theme
    assert any("5个关卡" in rule for rule in spec.special_rules)
    assert "逮小猪" in spec.intent_summary


def test_build_iteration_spec_falls_back_to_source_spec_when_parse_fails():
    runner = V2PipelineRunner()
    request = IterateV2Request(
        game_id="game-iter-fallback",
        user_id="user-iter-fallback",
        current_code="<!DOCTYPE html><html><head><title>逮小猪</title></head><body></body></html>",
        iteration_intent={"feedback": "继续增加关卡，设置5个关卡", "conversation": []},
        source_spec=GameSpec(game_type="casual", intent_summary="逮住小猪并躲开障碍", ui_language="zh-CN"),
        source_bundle_context=SourceBundleContext(title="逮小猪"),
        runtime_contract=GameRuntimeContract(runtime_profile="casual_arcade"),
    )

    with patch(
        "src.engine.pipeline_v2_runner.require_prompt",
        return_value=(
            "Current game context: {current_summary}\n"
            "Source spec summary: {source_spec_summary}\n"
            "Historical bundle context: {source_bundle_context}\n"
            "Requested iteration: {feedback}\n"
            "Conversation context: {conversation_text}"
        ),
    ), patch.object(
        runner,
        "_parse_spec_with_retries",
        new=AsyncMock(
            side_effect=PipelineExecutionError(
                "Spec build failed after 3 attempts: LLM slot extraction returned no valid JSON",
                stage="spec_build",
                artifacts=[{"artifact_type": "spec_build_diagnostics"}],
            )
        ),
    ):
        spec = asyncio.run(runner._build_iteration_spec(request))

    assert spec.game_type == "casual"
    assert any("5个关卡" in rule for rule in spec.special_rules)


def test_build_iteration_spec_raises_on_generic_pipeline_failure():
    runner = V2PipelineRunner()
    request = IterateV2Request(
        game_id="game-iter-error",
        user_id="user-iter-error",
        current_code="<!DOCTYPE html><html><head><title>Pig Runner</title></head><body></body></html>",
        iteration_intent={"feedback": "add five levels", "conversation": []},
        source_spec=GameSpec(game_type="casual", intent_summary="Keep the pig runner core", ui_language="en-US"),
        source_bundle_context=SourceBundleContext(title="Pig Runner"),
        runtime_contract=GameRuntimeContract(runtime_profile="casual_arcade"),
    )

    with patch(
        "src.engine.pipeline_v2_runner.require_prompt",
        return_value=(
            "Current game context: {current_summary}\n"
            "Source spec summary: {source_spec_summary}\n"
            "Historical bundle context: {source_bundle_context}\n"
            "Requested iteration: {feedback}\n"
            "Conversation context: {conversation_text}"
        ),
    ), patch.object(
        runner,
        "_parse_spec_with_retries",
        new=AsyncMock(side_effect=PipelineExecutionError("upstream timed out", stage="spec_build")),
    ):
        try:
            asyncio.run(runner._build_iteration_spec(request))
            assert False, "expected PipelineExecutionError"
        except PipelineExecutionError as exc:
            assert str(exc) == "upstream timed out"


def test_runtime_qa_unavailable_in_production_persists_candidate_artifacts():
    runner = V2PipelineRunner()
    runtime_unavailable = SimpleNamespace(
        ran=False,
        unavailable_reason="runtime_qa_timeout:12.00s",
        unavailable_kind="timeout",
        unavailable_phase="content_load",
        phase_metrics={"content_load_timeout_s": 12.0},
    )

    with patch(
        "src.engine.pipeline_v2_runner.run_runtime_qa",
        new=AsyncMock(return_value=runtime_unavailable),
    ), patch(
        "src.engine.pipeline_v2_runner.settings.ENVIRONMENT",
        "production",
    ):
        try:
            asyncio.run(
                runner._run_runtime_qa_loop(
                    code="<!DOCTYPE html><html><body>candidate</body></html>",
                    spec=GameSpec(game_type="casual"),
                    runtime_contract=GameRuntimeContract(),
                    prompt_bundle_snapshot={"layers": {}},
                    progress_cb=None,
                    game_id="game-1",
                    user_id="user-1",
                    allow_runtime_qa_unavailable=False,
                )
            )
            assert False, "expected PipelineExecutionError"
        except PipelineExecutionError as exc:
            assert "Runtime QA unavailable" in str(exc)
            assert exc.failure_family == "qa_infra_unavailable"
            artifacts = getattr(exc, "artifacts", [])
            assert any(item.get("artifact_type") == "failed_runtime_candidate" for item in artifacts)
            assert any(item.get("artifact_type") == "runtime_qa_report" for item in artifacts)
            runtime_report = next(
                item.get("payload")
                for item in artifacts
                if item.get("artifact_type") == "runtime_qa_report"
            )
            assert runtime_report["unavailableKind"] == "timeout"
            assert runtime_report["unavailablePhase"] == "content_load"


def test_runtime_qa_interaction_timeout_is_treated_as_repairable_runtime_failure():
    runner = V2PipelineRunner()
    timeout_result = SimpleNamespace(
        ran=False,
        unavailable_reason="runtime_qa_timeout:interaction:6.00s",
        unavailable_kind="timeout",
        unavailable_phase="interaction",
        phase_metrics={"interaction_timeout_s": 6.0},
        js_errors=[],
    )
    repaired_runtime = SimpleNamespace(
        ran=True,
        canvas_renders=True,
        js_errors=[],
        registered_input_handlers=["pointerdown"],
        direct_input_handlers=["click"],
        triggered_input_handlers=["pointerdown"],
        interaction_performed=True,
        canvas_changed_after_input=True,
        dom_changed_after_input=True,
        fps=60.0,
        load_time_ms=1200,
    )
    repaired_candidate = """
    <!DOCTYPE html>
    <html><body><canvas id='gameCanvas'></canvas><script>
    const canvas = document.getElementById('gameCanvas');
    canvas.addEventListener('pointerdown', () => {});
    </script></body></html>
    """

    with patch(
        "src.engine.pipeline_v2_runner.run_runtime_qa",
        new=AsyncMock(side_effect=[timeout_result, repaired_runtime]),
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value=repaired_candidate),
    ) as repair_mock, patch.object(
        runner,
        "_run_contract_qa_loop",
        new=AsyncMock(return_value=SimpleNamespace(success=True, code=repaired_candidate, retries=0, last_errors=[])),
    ), patch(
        "src.engine.pipeline_v2_runner.settings.ENVIRONMENT",
        "production",
    ):
        final_code, runtime_qa, retries, qa_warnings = asyncio.run(
            runner._run_runtime_qa_loop(
                code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
                spec=GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot={"layers": {}},
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
                allow_runtime_qa_unavailable=False,
            )
        )

    assert final_code == repaired_candidate
    assert runtime_qa is repaired_runtime
    assert retries == 1
    assert qa_warnings == []
    repair_errors = repair_mock.await_args.args[1]
    assert any("synthetic interaction" in error.message.lower() for error in repair_errors)


def test_runtime_qa_unavailable_errors_flag_missing_input_handlers_for_interaction_timeout():
    runner = V2PipelineRunner()
    errors = runner._runtime_qa_unavailable_errors(
        SimpleNamespace(
            ran=False,
            unavailable_reason="runtime_qa_timeout:interaction:6.00s",
            unavailable_kind="timeout",
            unavailable_phase="interaction",
            js_errors=[],
        ),
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
    )

    messages = [error.message for error in errors]
    assert any("no registered user input handlers" in message.lower() for message in messages)
    assert any("synthetic interaction" in message.lower() for message in messages)


def test_runtime_qa_unavailable_errors_describe_heavy_first_interaction_when_handlers_exist():
    runner = V2PipelineRunner()
    errors = runner._runtime_qa_unavailable_errors(
        SimpleNamespace(
            ran=False,
            unavailable_reason="runtime_qa_timeout:interaction:6.00s",
            unavailable_kind="timeout",
            unavailable_phase="interaction",
            js_errors=[],
            registered_input_handlers=["pointerdown"],
            direct_input_handlers=["click"],
            triggered_input_handlers=["pointerdown"],
            interaction_performed=True,
            canvas_changed_after_input=False,
            dom_changed_after_input=False,
        ),
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>document.getElementById('gameCanvas').addEventListener('pointerdown',()=>{});</script></body></html>",
    )

    messages = [error.message for error in errors]
    assert not any("no registered user input handlers" in message.lower() for message in messages)
    assert any("keep first-input handlers lightweight" in message.lower() for message in messages)


def test_runtime_qa_timeout_can_soft_fail_for_published_iteration():
    runner = V2PipelineRunner()
    runtime_unavailable = SimpleNamespace(
        ran=False,
        unavailable_reason="runtime_qa_timeout:60.00s",
        unavailable_kind="timeout",
        unavailable_phase="overall",
        phase_metrics={"total_elapsed_ms": 60000},
    )

    with patch(
        "src.engine.pipeline_v2_runner.run_runtime_qa",
        new=AsyncMock(return_value=runtime_unavailable),
    ), patch(
        "src.engine.pipeline_v2_runner.settings.ENVIRONMENT",
        "production",
    ):
        final_code, runtime_qa, retries, qa_warnings = asyncio.run(
            runner._run_runtime_qa_loop(
                code="<!DOCTYPE html><html><body>candidate</body></html>",
                spec=GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot={"layers": {}},
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
                allow_runtime_qa_unavailable=True,
            )
        )

    assert final_code == "<!DOCTYPE html><html><body>candidate</body></html>"
    assert runtime_qa.unavailable_kind == "timeout"
    assert retries == 0
    assert qa_warnings == [{
        "type": "runtime_qa_unavailable",
        "severity": "warning",
        "family": "runtime_startup",
        "blocking": False,
        "repairHint": "Retry runtime QA later or inspect the runtime environment if Playwright/browser infrastructure is unavailable.",
        "message": "Runtime QA unavailable: runtime_qa_timeout:60.00s",
        "kind": "timeout",
        "phase": "overall",
        "softFailed": True,
    }]


def test_build_create_spec_resolves_generation_tier_from_request_metadata():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-tier",
        user_id="user-tier",
        raw_user_input="make a flashy arcade game",
        generation_tier="showcase",
        runtime_contract=GameRuntimeContract(metadata={"generation_tier": "safe"}),
    )

    with patch.object(
        runner,
        "_parse_spec_with_retries",
        new=AsyncMock(return_value=GameSpec(game_type="casual")),
    ):
        spec = asyncio.run(runner._build_create_spec(request))

    assert spec.game_type == "casual"
    assert spec.generation_tier.value == "showcase"
    assert spec.visual_style.visual_pack is not None
    assert spec.visual_style.render_style_intensity == "high"


def test_build_iteration_spec_inherits_generation_tier_from_source_bundle_context():
    runner = V2PipelineRunner()
    request = IterateV2Request(
        game_id="game-iter-tier",
        user_id="user-iter-tier",
        current_code="<!DOCTYPE html><html><body></body></html>",
        iteration_intent={"feedback": "add a dramatic finale", "conversation": []},
        source_spec=GameSpec(game_type="casual", generation_tier="standard"),
        source_bundle_context=SourceBundleContext(
            title="Arcade Rescue",
            latest_generation_tier="showcase",
        ),
        runtime_contract=GameRuntimeContract(runtime_profile="casual_arcade"),
    )

    with patch(
        "src.engine.pipeline_v2_runner.require_prompt",
        return_value=(
            "Current game context: {current_summary}\n"
            "Source spec summary: {source_spec_summary}\n"
            "Historical bundle context: {source_bundle_context}\n"
            "Requested iteration: {feedback}\n"
            "Conversation context: {conversation_text}"
        ),
    ), patch.object(
        runner,
        "_parse_spec_with_retries",
        new=AsyncMock(return_value=GameSpec(game_type="casual")),
    ):
        spec = asyncio.run(runner._build_iteration_spec(request))

    assert spec.game_type == "casual"
    assert spec.generation_tier.value == "showcase"


def test_score_runtime_profile_candidate_rewards_showcase_variants():
    runner = V2PipelineRunner()
    showcase_spec = GameSpec(game_type="casual", generation_tier="showcase")
    safe_spec = GameSpec(game_type="casual", generation_tier="safe")

    showcase_variant = runner._score_runtime_profile_candidate(showcase_spec, "casual_arcade_rescue")
    showcase_baseline = runner._score_runtime_profile_candidate(showcase_spec, "casual_arcade")
    safe_variant = runner._score_runtime_profile_candidate(safe_spec, "casual_arcade_rescue")
    safe_baseline = runner._score_runtime_profile_candidate(safe_spec, "casual_arcade")

    assert showcase_variant > showcase_baseline
    assert safe_baseline > safe_variant
