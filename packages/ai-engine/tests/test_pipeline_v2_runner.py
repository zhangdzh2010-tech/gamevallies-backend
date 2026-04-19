"""Regression tests for v2 pipeline runtime-contract validation."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    GDD,
    GameRuntimeContract,
    GameSpec,
    GenerateCodeResult,
    IterationType,
    IterateV2Request,
    QACheckError,
    RunPipelineV2Request,
    SourceBundleContext,
    SourceBundleRevision,
)
from src.config.settings import settings
from src.engine.code_generator import CodeGenerator
from src.engine.pipeline_v2_runner import V2PipelineRunner
from src.engine.pipeline_orchestrator import PipelineExecutionError
from src.engine.quality_scorer import LLMReviewResult


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


def test_runtime_contract_accepts_numeric_enum_constant_for_playing_state():
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
          var STATE_BOOT = 0, STATE_READY = 1, STATE_PLAYING = 2, STATE_GAME_OVER = 3;
          var gameState = STATE_BOOT;
          function restartGame() { gameState = STATE_READY; }
          function startGame() { gameState = STATE_PLAYING; }
          canvas.addEventListener('pointerdown', function () {
            startGame();
            ctx.fillRect(0, 0, 24, 24);
          });
        </script>
      </body>
    </html>
    """

    errors = runner._validate_runtime_contract(code, contract)
    assert not any(
        error.type == "contract_state" and "playing" in error.message.lower()
        for error in errors
    )


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


def test_runtime_contract_accepts_canvas_2d_context_with_options_object():
    runner = V2PipelineRunner()
    contract = GameRuntimeContract()
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
          const ctx = canvas.getContext('2d', { alpha: false });
          canvas.width = 360;
          canvas.height = 640;
          let state = 'ready';
          function restartGame() { state = 'ready'; }
          function startGame() { state = 'playing'; }
          canvas.addEventListener('pointerdown', function () {
            startGame();
            ctx.fillRect(0, 0, 10, 10);
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


def test_contract_qa_loop_attempts_syntax_repair_before_signaling_regeneration():
    runner = V2PipelineRunner()
    error = QACheckError(
        type="L1_syntax",
        message="Missing required HTML tag: </body>",
        severity="error",
    )
    prompt_bundle_snapshot = {
        "layers": {
            "resolved_prompts": {
                "repair_syntax_structural": {"content": "SYNTAX_ONLY::{error_list}::{code}"}
            }
        }
    }

    with patch.object(
        runner,
        "_validate_contract_bundle",
        return_value=[error],
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fixed</body></html>"),
    ) as mock_repair:
        result = asyncio.run(
            runner._run_contract_qa_loop(
                code="<!DOCTYPE html><html><head></head><body><script>function draw(){</script>",
                spec=GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot=prompt_bundle_snapshot,
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
                max_retries=1,
            )
        )

    assert result.success is False
    assert result.needs_regeneration is True
    assert result.retries == 1
    assert mock_repair.await_count == 1


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
    assert result.retries == 0
    assert result.needs_regeneration is True
    assert mock_repair.await_count == 0


def test_create_generation_attempt_plan_uses_latency_safe_retry_budgets():
    assert V2PipelineRunner._build_create_generation_attempt_plan("standard") == ("standard", "standard")
    assert V2PipelineRunner._build_create_generation_attempt_plan("simple") == ("simple", "standard")
    assert V2PipelineRunner._build_create_generation_attempt_plan("complex") == ("complex", "standard")
    assert V2PipelineRunner._build_create_generation_attempt_plan("showcase") == ("showcase", "complex")


def test_create_generation_uses_full_document_output_class():
    assert CodeGenerator._create_response_size_hint() == "full_document"


def test_code_generation_retry_cap_stays_close_to_selected_budget_profile():
    assert CodeGenerator._select_truncation_retry_cap(budget_override="safe") == 4096
    assert CodeGenerator._select_truncation_retry_cap(budget_override="simple") == 8192
    assert CodeGenerator._select_truncation_retry_cap(budget_override="standard") == 14336
    assert CodeGenerator._select_truncation_retry_cap(budget_override="complex") == settings.LLM_LONG_GENERATION_MAX_TOKENS


def test_runtime_qa_loop_fails_immediately_without_targeted_remediation():
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
        new=AsyncMock(return_value=runtime_fail),
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body>fix-1</body></html>"),
    ) as mock_repair:
        with pytest.raises(PipelineExecutionError) as exc_info:
            asyncio.run(
                runner._run_runtime_qa_loop(
                    code="<!DOCTYPE html><html><body>initial</body></html>",
                    runtime_contract=GameRuntimeContract(),
                    progress_cb=None,
                    game_id="game-1",
                    user_id="user-1",
                )
            )

    assert "failed runtime QA" in str(exc_info.value)
    assert mock_repair.await_count == 0


def test_runtime_qa_timeout_scales_with_candidate_size_only():
    runner = V2PipelineRunner()
    small_code = "<!DOCTYPE html><html><body>tiny</body></html>"
    large_code = "<!DOCTYPE html><html><body>" + ("A" * 20000) + "</body></html>"
    very_large_code = "<!DOCTYPE html><html><body>" + ("B" * 36000) + "</body></html>"

    with patch(
        "src.engine.pipeline_v2_runner.settings.RUNTIME_QA_TIMEOUT_S",
        8.0,
    ):
        small_timeout = runner._resolve_runtime_qa_timeout(small_code)
        large_timeout = runner._resolve_runtime_qa_timeout(large_code)
        very_large_timeout = runner._resolve_runtime_qa_timeout(very_large_code)

    assert small_timeout == 8.0
    assert large_timeout == 10.0
    assert very_large_timeout == 22.0


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
    ):
        final_code, runtime_qa, retries, qa_warnings = asyncio.run(
            runner._run_runtime_qa_loop(
                code=code,
                runtime_contract=GameRuntimeContract(),
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
    ):
        final_code, runtime_qa, retries, qa_warnings = asyncio.run(
            runner._run_runtime_qa_loop(
                code=code,
                runtime_contract=GameRuntimeContract(),
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


def test_select_runtime_profile_prefers_tap_challenge_for_showcase_quiz_show_brief():
    runner = V2PipelineRunner()

    spec = GameSpec(
        game_type="educational",
        generation_tier="showcase",
        source_description="Create a history quiz show for mobile web with a playful host, stage lights, and combo streak rewards.",
        intent_summary="A timed game show trivia challenge with combo streak rewards.",
    )

    assert runner._select_runtime_profile(spec, None) == "tap_challenge_combo"


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

    assert first in {
        "casual_action",
        "casual_action_arena",
        "casual_action_survival",
        "casual_arcade",
        "casual_arcade_burst",
        "casual_arcade_orbit",
        "casual_arcade_rescue",
        "casual_lane",
    }
    assert second in {
        "casual_action",
        "casual_action_arena",
        "casual_action_survival",
        "casual_arcade",
        "casual_arcade_burst",
        "casual_arcade_orbit",
        "casual_arcade_rescue",
        "casual_lane",
    }
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
                    runtime_contract=GameRuntimeContract(),
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


def test_runtime_qa_interaction_timeout_fails_without_runtime_remediation():
    runner = V2PipelineRunner()
    timeout_result = SimpleNamespace(
        ran=False,
        unavailable_reason="runtime_qa_timeout:interaction:6.00s",
        unavailable_kind="timeout",
        unavailable_phase="interaction",
        phase_metrics={"interaction_timeout_s": 6.0},
        js_errors=[],
    )

    with patch(
        "src.engine.pipeline_v2_runner.run_runtime_qa",
        new=AsyncMock(return_value=timeout_result),
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="unused"),
    ) as mock_repair, patch(
        "src.engine.pipeline_v2_runner.settings.ENVIRONMENT",
        "production",
    ):
        with pytest.raises(PipelineExecutionError) as exc_info:
            asyncio.run(
                runner._run_runtime_qa_loop(
                    code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
                    runtime_contract=GameRuntimeContract(),
                    progress_cb=None,
                    game_id="game-1",
                    user_id="user-1",
                    allow_runtime_qa_unavailable=False,
                )
            )

    assert "synthetic interaction" in str(exc_info.value).lower()
    assert mock_repair.await_count == 0


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
                runtime_contract=GameRuntimeContract(),
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


def test_select_generation_budget_override_uses_actual_complexity_not_only_tier():
    runner = V2PipelineRunner()

    simple_spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "tap_clear", "input": "tap"}],
    )
    complex_spec = GameSpec(
        game_type="educational",
        generation_tier="standard",
        special_rules=["rule1", "rule2", "rule3", "rule4"],
        core_mechanics=[{"type": "route"}, {"type": "quiz"}],
    )

    assert runner._select_generation_budget_override(simple_spec) == "simple"
    assert runner._select_generation_budget_override(complex_spec) == "complex"


def test_select_generation_budget_override_ignores_generic_support_rules_for_simple_create():
    runner = V2PipelineRunner()

    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        entities=[
            {"name": "player", "role": "player"},
            {"name": "meteor", "role": "obstacle"},
            {"name": "star", "role": "collectible"},
        ],
        special_rules=[
            "Click to start",
            "Real-time score displayed during gameplay",
            "Game over screen shows final score and restart button",
            "Each collected star grants 10 points",
        ],
        core_mechanics=[{"type": "swipe_dodge", "input": "swipe"}],
        source_description="做一个太空躲避手机小游戏，坚持 30 秒获胜。",
    )

    assert runner._select_generation_budget_override(spec) == "simple"


def test_select_generation_budget_override_keeps_wave_shooter_in_standard_budget():
    runner = V2PipelineRunner()

    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        entities=[
            {"name": "ship", "role": "player"},
            {"name": "enemy", "role": "obstacle"},
            {"name": "beacon", "role": "collectible"},
        ],
        special_rules=[
            "Real-time health bar, score counter, and current wave prompt displayed on UI",
            "Enemy count and strength increase per wave",
            "Game over triggers when player health is depleted, shows failure settlement screen with restart button",
        ],
        core_mechanics=[{"type": "drag_shoot", "input": "drag"}],
        source_description="做一个竖屏俯视角动作射击小游戏，击败三波敌人后胜利。",
    )

    assert runner._select_generation_budget_override(spec) == "standard"

def test_generate_create_code_returns_preflight_issues_without_internal_retry():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-preflight",
        user_id="user-preflight",
        raw_user_input="make a simple dodge game",
    )
    spec = GameSpec(game_type="casual", core_mechanics=[{"type": "tap_dodge"}])
    generated_invalid = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); if (anim < 1) { render(); }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
    )
    generated_valid = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 360; canvas.height = 640; let anim = 0; function render() { anim += 1; }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
    )

    with patch.object(
        runner.code_generator,
        "generate",
        new=AsyncMock(return_value=generated_invalid),
    ) as mock_generate:
        result, preflight_issues = asyncio.run(
            runner._generate_create_code(
                request,
                spec,
                GDD(),
                GameRuntimeContract(),
                budget_override="simple",
            )
        )

    assert result.html_code == generated_invalid.html_code
    assert mock_generate.await_count == 1
    assert preflight_issues
    assert any("anim" in issue.message.lower() for issue in preflight_issues)


def test_generate_create_code_auto_repairs_nested_grid_reads_before_preflight_failure():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-preflight-grid",
        user_id="user-preflight-grid",
        raw_user_input="make a fruit merge puzzle",
    )
    spec = GameSpec(game_type="puzzle", core_mechanics=[{"type": "merge"}])
    runtime_contract = GameRuntimeContract(runtime_profile="puzzle_grid_merge")
    generated = GenerateCodeResult(
        html_code=(
            "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>"
            "function inspectCell(grid, row, col) { return grid[row][col].type + ':' + grid[row][col].row; }"
            "</script></body></html>"
        ),
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
    )

    with patch.object(
        runner.code_generator,
        "generate",
        new=AsyncMock(return_value=generated),
    ):
        result, preflight_issues = asyncio.run(
            runner._generate_create_code(
                request,
                spec,
                GDD(),
                runtime_contract,
                budget_override="simple",
            )
        )

    assert "__safeGridCell" in result.html_code
    assert not any(issue.code == "unsafe_nested_grid_read" for issue in preflight_issues)


def test_generate_create_code_auto_repairs_dynamic_alpha_suffix_before_preflight_failure():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-preflight-alpha",
        user_id="user-preflight-alpha",
        raw_user_input="make a neon action game",
    )
    spec = GameSpec(game_type="casual", core_mechanics=[{"type": "tap_dodge"}])
    runtime_contract = GameRuntimeContract(runtime_profile="casual_arcade")
    generated = GenerateCodeResult(
        html_code=(
            "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>"
            "const canvas=document.getElementById('gameCanvas');"
            "canvas.width=640; canvas.height=360;"
            "const ctx=canvas.getContext('2d');"
            "const light={color:'hsl(0, 80%, 60%)'};"
            "function renderBeam(){"
            "const gradient=ctx.createLinearGradient(0,0,0,canvas.height);"
            "gradient.addColorStop(0, light.color + '80');"
            "gradient.addColorStop(1, light.color + '00');"
            "ctx.fillStyle=gradient;"
            "}"
            "</script></body></html>"
        ),
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
    )

    with patch.object(
        runner.code_generator,
        "generate",
        new=AsyncMock(return_value=generated),
    ):
        result, preflight_issues = asyncio.run(
            runner._generate_create_code(
                request,
                spec,
                GDD(),
                runtime_contract,
                budget_override="simple",
            )
        )

    assert "__withAlpha(light.color, 0.502)" in result.html_code
    assert not any(issue.code == "unsafe_color_alpha_concat" for issue in preflight_issues)


def test_contract_qa_loop_signals_regeneration_when_only_non_syntax_errors_exist():
    runner = V2PipelineRunner()
    runtime_contract = GameRuntimeContract(runtime_profile="puzzle_grid")
    errors = [
        QACheckError(
            type="contract_mobile",
            message="Runtime contract requires portrait-first short-edge UI scaling",
            severity="error",
        )
    ]

    with patch.object(
        runner,
        "_validate_contract_bundle",
        return_value=errors,
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="unused"),
    ) as mock_repair:
        result = asyncio.run(
            runner._run_contract_qa_loop(
                code="<!DOCTYPE html><html><body></body></html>",
                spec=GameSpec(game_type="puzzle"),
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot={},
                progress_cb=lambda *_args, **_kwargs: None,
                game_id="game-non-syntax",
                user_id="user-non-syntax",
            )
        )

    assert result.success is False
    assert result.needs_regeneration is True
    assert result.retries == 0
    assert mock_repair.await_count == 0


def test_contract_qa_loop_signals_regeneration_when_errors_are_mixed():
    runner = V2PipelineRunner()
    runtime_contract = GameRuntimeContract(runtime_profile="puzzle_grid")
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 69: Unexpected token .",
            severity="error",
        ),
        QACheckError(
            type="contract_mobile",
            message="Runtime contract requires portrait-first short-edge UI scaling",
            severity="error",
        ),
    ]

    with patch.object(
        runner,
        "_validate_contract_bundle",
        return_value=errors,
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="unused"),
    ) as mock_repair:
        result = asyncio.run(
            runner._run_contract_qa_loop(
                code="<!DOCTYPE html><html><body></body></html>",
                spec=GameSpec(game_type="puzzle"),
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot={},
                progress_cb=lambda *_args, **_kwargs: None,
                game_id="game-mixed",
                user_id="user-mixed",
            )
        )

    assert result.success is False
    assert result.needs_regeneration is True
    assert result.retries == 1
    assert mock_repair.await_count == 1


def test_contract_qa_loop_signals_regeneration_for_non_truncation_syntax_errors():
    runner = V2PipelineRunner()
    errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript local static declarations are not valid in plain browser JS; use outer-scope let/const state instead",
            severity="error",
        ),
    ]

    with patch.object(
        runner,
        "_validate_contract_bundle",
        return_value=errors,
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="unused"),
    ) as mock_repair:
        result = asyncio.run(
            runner._run_contract_qa_loop(
                code="<!DOCTYPE html><html><body><script>function update(){ static lastSpawnTime = 0; }</script></body></html>",
                spec=GameSpec(game_type="casual"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot={},
                progress_cb=lambda *_args, **_kwargs: None,
                game_id="game-static-local",
                user_id="user-static-local",
            )
        )

    assert result.success is False
    assert result.needs_regeneration is True
    assert result.retries == 1
    assert mock_repair.await_count == 1


def test_contract_qa_loop_repairs_syntax_only_errors_before_contract_regeneration():
    runner = V2PipelineRunner()
    syntax_errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Line 43: Unexpected token ;",
            severity="error",
        ),
    ]

    with patch.object(
        runner,
        "_validate_contract_bundle",
        side_effect=[syntax_errors, []],
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(return_value="<!DOCTYPE html><html><body><script>const ok = true;</script></body></html>"),
    ) as mock_repair:
        result = asyncio.run(
            runner._run_contract_qa_loop(
                code="<!DOCTYPE html><html><body><script>const broken = ;</script></body></html>",
                spec=GameSpec(game_type="puzzle"),
                runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid"),
                prompt_bundle_snapshot={},
                progress_cb=lambda *_args, **_kwargs: None,
                game_id="game-syntax-fix",
                user_id="user-syntax-fix",
            )
        )

    assert result.success is True
    assert result.retries == 1
    assert result.needs_regeneration is False
    assert mock_repair.await_count == 1


def test_quality_regeneration_guidance_adds_coordinate_guard_recipe_for_undefined_x_runtime_failures():
    guidance = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="runtime_simulation_qa",
        message="Generated code failed runtime QA: Runtime JS error: Cannot read properties of undefined (reading 'x')",
    )

    assert ".x` / `.y`" in guidance
    assert "Initialize moving entities" in guidance


def test_quality_regeneration_guidance_adds_dot_loop_scaffold():
    guidance = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="logic_generate",
        message="Generated code failed preflight: Declare or inline `dot` before use; it is referenced as a live expression.",
    )

    assert "const dot = dots[i];" in guidance
    assert "for (let i = 0; i < dots.length; i += 1)" in guidance


def test_quality_regeneration_guidance_adds_safe_grid_accessor_recipe():
    guidance = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="logic_generate",
        message=(
            "Generated code failed preflight: Guard nested grid reads before accessing `grid[row][col].type`; "
            "check that both the row bucket and cell exist."
        ),
    )

    assert "function getCell(grid, row, col)" in guidance
    assert "const cell = getCell(grid, row, col); if (!cell) continue;" in guidance
    assert "`cell.fruit`" in guidance


def test_quality_regeneration_guidance_adds_ready_state_and_ctx_boot_recipes():
    guidance = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="logic_generate",
        message=(
            "Generated code failed preflight: Do not leave `ctx` initialized as null while the main loop can run; "
            "Primary input handler `handleInputStart` returns unless the game is already in `playing`."
        ),
    )

    assert "Do not keep `ctx` as `null`" in guidance
    assert "boot/ready input can call `startGame()`" in guidance


def test_quality_regeneration_guidance_adds_touch_guard_and_orientation_specific_scaling_recipes():
    guidance = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="contract_qa",
        message=(
            "Runtime contract requires landscape-first short-edge UI scaling. "
            "Generated code failed runtime QA: Cannot read properties of undefined (reading 'clientX') "
            "because it uses touches[0] during touchend."
        ),
    )

    assert "const REF_W = 640; const REF_H = 360;" in guidance
    assert "scaleX = canvas.width / REF_W" in guidance
    assert "const viewWidth = canvas.width; const viewHeight = canvas.height;" in guidance
    assert "const point = (e.touches && e.touches.length ? e.touches[0]" in guidance
    assert "function getInputPoint(e)" in guidance
    assert "touchstart/touchmove/touchend" in guidance


def test_run_create_impl_retries_preflight_once_with_consolidated_guidance():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-preflight-simple-cap",
        user_id="user-preflight-simple-cap",
        raw_user_input="make a simple dodge game",
    )
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "tap_dodge"}],
    )
    runtime_contract = GameRuntimeContract(runtime_profile="casual_arcade")
    generated_invalid = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); if (anim < 1) { render(); }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-a", "fallback_provider_ids": ["provider-b", "provider-c"]},
    )
    generated_valid = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 360; canvas.height = 640; let anim = 0; function render() { anim += 1; }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-b"},
    )
    qa_success = SimpleNamespace(
        success=True,
        code=generated_valid.html_code,
        retries=0,
        needs_regeneration=False,
        issue_list=None,
    )
    quality_result = SimpleNamespace(
        final_score=4.8,
        details={},
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.0,
        retry_penalty=0.0,
        runtime_bonus=0.0,
        review_bonus=0.0,
        gameplay_depth_bonus=0.0,
    )
    preflight_issue = SimpleNamespace(message="Declare or inline 'anim' before use", code="undefined_symbol")
    runtime_qa = SimpleNamespace(ran=True)

    with patch.object(
        runner,
        "_build_create_spec",
        new=AsyncMock(return_value=spec),
    ), patch.object(
        runner,
        "_select_runtime_profile",
        return_value="casual_arcade",
    ), patch.object(
        runner,
        "_compose_runtime_contract",
        return_value=runtime_contract,
    ), patch.object(
        runner,
        "_build_gdd",
        new=AsyncMock(return_value=GDD()),
    ), patch.object(
        runner,
        "_remember_spec",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_runtime_contract",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_code",
        new=AsyncMock(),
    ), patch(
        "src.engine.pipeline_v2_runner.task_memory.append_decision",
        new=AsyncMock(),
    ), patch.object(
        runner.pre_gen_validator,
        "validate",
        return_value=[],
    ), patch.object(
        runner,
        "_generate_create_code",
        new=AsyncMock(side_effect=[
            (generated_invalid, [preflight_issue]),
            (generated_valid, []),
        ]),
    ) as mock_generate, patch.object(
        runner,
        "_run_contract_and_runtime_flow",
        new=AsyncMock(return_value=(qa_success, runtime_qa, 0, [])),
    ), patch.object(
        runner.qa_pipeline,
        "check",
        return_value=SimpleNamespace(passed=True, errors=[], warnings=[]),
    ), patch.object(
        runner,
        "_should_run_code_review",
        return_value=False,
    ), patch.object(
        runner.quality_scorer,
        "compute",
        return_value=quality_result,
    ), patch.object(
        runner,
        "_serialize_runtime_qa",
        return_value={},
    ):
        response = asyncio.run(
            runner._run_create_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == generated_valid.html_code
    assert mock_generate.await_count == 2
    first_call = mock_generate.await_args_list[0].kwargs
    second_call = mock_generate.await_args_list[1].kwargs
    assert first_call["budget_override"] == "simple"
    assert second_call["budget_override"] == "standard"
    assert second_call["excluded_provider_ids"] == []
    assert "PRE-FLIGHT CORRECTIONS" in second_call["generation_guidance"]


def test_run_create_impl_grants_one_final_retry_when_last_attempt_hits_preflight():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-preflight-final-retry",
        user_id="user-preflight-final-retry",
        raw_user_input="make a fruit merge puzzle",
    )
    spec = GameSpec(
        game_type="puzzle",
        generation_tier="standard",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "merge"}],
    )
    runtime_contract = GameRuntimeContract(runtime_profile="puzzle_grid_merge")
    generated_first = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 360; canvas.height = 640; function draw() { return true; }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-a"},
    )
    generated_preflight_invalid = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>function inspect(grid,row,col){ return grid[row][col].fruit + ':' + grid[row][col].anim; }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-b"},
    )
    generated_final = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 360; canvas.height = 640; const ctx = canvas.getContext('2d'); function draw() { ctx.clearRect(0,0,canvas.width,canvas.height); }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-c"},
    )
    qa_needs_regeneration = SimpleNamespace(
        success=False,
        code=generated_first.html_code,
        retries=0,
        needs_regeneration=True,
        issue_list=None,
        last_errors=[SimpleNamespace(message="Missing deterministic merge cleanup helper")],
    )
    qa_success = SimpleNamespace(
        success=True,
        code=generated_final.html_code,
        retries=0,
        needs_regeneration=False,
        issue_list=None,
    )
    quality_result = SimpleNamespace(
        final_score=8.0,
        details={},
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.0,
        retry_penalty=0.0,
        runtime_bonus=0.0,
        review_bonus=0.0,
        gameplay_depth_bonus=0.0,
    )
    preflight_issue = SimpleNamespace(
        message="Guard nested grid reads before accessing `grid[row][col].fruit`; check that both the row bucket and cell exist, or read through a safe helper first.",
        code="unsafe_nested_grid_read",
    )
    runtime_qa = SimpleNamespace(ran=True)

    with patch.object(
        runner,
        "_build_create_spec",
        new=AsyncMock(return_value=spec),
    ), patch.object(
        runner,
        "_select_runtime_profile",
        return_value="puzzle_grid_merge",
    ), patch.object(
        runner,
        "_compose_runtime_contract",
        return_value=runtime_contract,
    ), patch.object(
        runner,
        "_build_gdd",
        new=AsyncMock(return_value=GDD()),
    ), patch.object(
        runner,
        "_remember_spec",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_runtime_contract",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_code",
        new=AsyncMock(),
    ), patch(
        "src.engine.pipeline_v2_runner.task_memory.append_decision",
        new=AsyncMock(),
    ), patch.object(
        runner.pre_gen_validator,
        "validate",
        return_value=[],
    ), patch.object(
        runner,
        "_generate_create_code",
        new=AsyncMock(side_effect=[
            (generated_first, []),
            (generated_preflight_invalid, [preflight_issue]),
            (generated_final, []),
        ]),
    ) as mock_generate, patch.object(
        runner,
        "_run_contract_and_runtime_flow",
        new=AsyncMock(side_effect=[
            (qa_needs_regeneration, runtime_qa, 0, []),
            (qa_success, runtime_qa, 0, []),
        ]),
    ), patch.object(
        runner.qa_pipeline,
        "check",
        return_value=SimpleNamespace(passed=True, errors=[], warnings=[]),
    ), patch.object(
        runner,
        "_should_run_code_review",
        return_value=False,
    ), patch.object(
        runner.quality_scorer,
        "compute",
        return_value=quality_result,
    ), patch.object(
        runner,
        "_serialize_runtime_qa",
        return_value={},
    ):
        response = asyncio.run(
            runner._run_create_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == generated_final.html_code
    assert mock_generate.await_count == 3
    first_call = mock_generate.await_args_list[0].kwargs
    second_call = mock_generate.await_args_list[1].kwargs
    third_call = mock_generate.await_args_list[2].kwargs
    assert first_call["budget_override"] == "simple"
    assert "QUALITY GATE CORRECTIONS" in second_call["generation_guidance"]
    assert second_call["budget_override"] == "standard"
    assert third_call["budget_override"] == "standard"
    assert "PRE-FLIGHT CORRECTIONS" in third_call["generation_guidance"]


def test_run_create_impl_excludes_failed_provider_when_logic_generate_transport_error_exposes_route_snapshot():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-provider-failover",
        user_id="user-provider-failover",
        raw_user_input="make a puzzle game",
    )
    spec = GameSpec(
        game_type="puzzle",
        generation_tier="standard",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "match"}],
    )
    runtime_contract = GameRuntimeContract(runtime_profile="puzzle_grid")
    generated_valid = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 360; canvas.height = 640; const ctx = canvas.getContext('2d'); function render() { ctx.clearRect(0,0,canvas.width,canvas.height); }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-b"},
    )
    qa_success = SimpleNamespace(
        success=True,
        code=generated_valid.html_code,
        retries=0,
        needs_regeneration=False,
        issue_list=None,
    )
    quality_result = SimpleNamespace(
        final_score=4.8,
        details={},
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.0,
        retry_penalty=0.0,
        runtime_bonus=0.0,
        review_bonus=0.0,
        gameplay_depth_bonus=0.0,
    )
    transport_exc = PipelineExecutionError(
        "Full LLM generation failed: LLM call canceled before completion",
        stage="logic_generate",
        failure_family="code_generation",
    )
    setattr(
        transport_exc,
        "route_snapshot",
        {"provider_id": "provider-a", "fallback_provider_ids": ["provider-b", "provider-c"]},
    )

    with patch.object(
        runner,
        "_build_create_spec",
        new=AsyncMock(return_value=spec),
    ), patch.object(
        runner,
        "_select_runtime_profile",
        return_value="puzzle_grid",
    ), patch.object(
        runner,
        "_compose_runtime_contract",
        return_value=runtime_contract,
    ), patch.object(
        runner,
        "_build_gdd",
        new=AsyncMock(return_value=GDD()),
    ), patch.object(
        runner,
        "_remember_spec",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_runtime_contract",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_code",
        new=AsyncMock(),
    ), patch(
        "src.engine.pipeline_v2_runner.task_memory.append_decision",
        new=AsyncMock(),
    ), patch.object(
        runner.pre_gen_validator,
        "validate",
        return_value=[],
    ), patch.object(
        runner,
        "_generate_create_code",
        new=AsyncMock(side_effect=[transport_exc, (generated_valid, [])]),
    ) as mock_generate, patch.object(
        runner,
        "_run_contract_and_runtime_flow",
        new=AsyncMock(return_value=(qa_success, SimpleNamespace(ran=True), 0, [])),
    ), patch.object(
        runner.qa_pipeline,
        "check",
        return_value=SimpleNamespace(passed=True, errors=[], warnings=[]),
    ), patch.object(
        runner,
        "_should_run_code_review",
        return_value=False,
    ), patch.object(
        runner.quality_scorer,
        "compute",
        return_value=quality_result,
    ), patch.object(
        runner,
        "_serialize_runtime_qa",
        return_value={},
    ):
        response = asyncio.run(
            runner._run_create_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == generated_valid.html_code
    assert mock_generate.await_count == 2
    second_call = mock_generate.await_args_list[1].kwargs
    assert second_call["excluded_provider_ids"] == ["provider-a"]


def test_run_create_impl_keeps_provider_pool_after_runtime_qa_regeneration():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-runtime-regen-failover",
        user_id="user-runtime-regen-failover",
        raw_user_input="make a parkour delivery game",
    )
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "runner"}],
    )
    runtime_contract = GameRuntimeContract(runtime_profile="casual_lane_dash")
    generated_first = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 640; canvas.height = 360; const ctx = canvas.getContext('2d'); function render() { ctx.clearRect(0,0,canvas.width,canvas.height); }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-a", "fallback_provider_ids": ["provider-b"]},
    )
    generated_second = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 640; canvas.height = 360; const ctx = canvas.getContext('2d'); function render() { ctx.clearRect(0,0,canvas.width,canvas.height); }</script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-b"},
    )
    qa_success = SimpleNamespace(
        success=True,
        code=generated_second.html_code,
        retries=0,
        needs_regeneration=False,
        issue_list=None,
    )
    quality_result = SimpleNamespace(
        final_score=4.8,
        details={},
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.0,
        retry_penalty=0.0,
        runtime_bonus=0.0,
        review_bonus=0.0,
        gameplay_depth_bonus=0.0,
    )
    runtime_exc = PipelineExecutionError(
        "Generated code failed runtime QA: Runtime JS error: Cannot read properties of undefined (reading 'x')",
        stage="runtime_simulation_qa",
        failure_family="runtime_qa",
    )

    with patch.object(
        runner,
        "_build_create_spec",
        new=AsyncMock(return_value=spec),
    ), patch.object(
        runner,
        "_select_runtime_profile",
        return_value="casual_lane_dash",
    ), patch.object(
        runner,
        "_compose_runtime_contract",
        return_value=runtime_contract,
    ), patch.object(
        runner,
        "_build_gdd",
        new=AsyncMock(return_value=GDD()),
    ), patch.object(
        runner,
        "_remember_spec",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_runtime_contract",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_code",
        new=AsyncMock(),
    ), patch(
        "src.engine.pipeline_v2_runner.task_memory.append_decision",
        new=AsyncMock(),
    ), patch.object(
        runner.pre_gen_validator,
        "validate",
        return_value=[],
    ), patch.object(
        runner,
        "_generate_create_code",
        new=AsyncMock(side_effect=[(generated_first, []), (generated_second, [])]),
    ) as mock_generate, patch.object(
        runner,
        "_run_contract_and_runtime_flow",
        new=AsyncMock(side_effect=[runtime_exc, (qa_success, SimpleNamespace(ran=True), 0, [])]),
    ), patch.object(
        runner.qa_pipeline,
        "check",
        return_value=SimpleNamespace(passed=True, errors=[], warnings=[]),
    ), patch.object(
        runner,
        "_should_run_code_review",
        return_value=False,
    ), patch.object(
        runner.quality_scorer,
        "compute",
        return_value=quality_result,
    ), patch.object(
        runner,
        "_serialize_runtime_qa",
        return_value={},
    ):
        response = asyncio.run(
            runner._run_create_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == generated_second.html_code
    assert mock_generate.await_count == 2
    second_call = mock_generate.await_args_list[1].kwargs
    assert second_call["excluded_provider_ids"] == []


def test_should_run_code_review_for_standard_tier_when_default_min_tier_is_standard():
    runner = V2PipelineRunner()
    spec = GameSpec(game_type="casual", generation_tier="standard")

    with patch.object(settings, "LLM_CODE_REVIEW_MIN_TIER", "standard"):
        assert runner._should_run_code_review(spec) is True


def test_select_runtime_profile_avoids_puzzle_grid_for_showcase_action_character_brief():
    runner = V2PipelineRunner()
    description = (
        "Make a premium-feeling landscape action game for mobile web where a cyber ronin "
        "hero dashes across neon rooftops, slices hunter drones, and collects energy shards."
    )
    spec = GameSpec(
        game_type="casual",
        generation_tier="showcase",
        source_description=description,
        intent_summary=description,
        entities=[],
        special_rules=[],
    )

    profile = runner._select_runtime_profile(spec, None)

    assert profile != "puzzle_grid"
    assert profile in {
        "casual_action_arena",
        "casual_action_survival",
        "casual_lane_dash",
        "casual_lane_chase",
        "casual_arcade_rescue",
    }


def test_select_runtime_profile_keeps_educational_brief_on_puzzle_grid():
    runner = V2PipelineRunner()
    description = "Create a classroom quiz game where students answer math questions before time runs out."
    spec = GameSpec(
        game_type="educational",
        generation_tier="standard",
        source_description=description,
        intent_summary=description,
        entities=[],
        special_rules=[],
    )

    profile = runner._select_runtime_profile(spec, None)

    assert profile == "puzzle_grid"


def test_select_runtime_profile_can_override_requested_puzzle_grid_for_showcase_quiz_show():
    runner = V2PipelineRunner()
    description = "Create a history quiz show with a host, stage lights, and combo streak rewards."
    spec = GameSpec(
        game_type="educational",
        generation_tier="showcase",
        source_description=description,
        intent_summary=description,
        entities=[],
        special_rules=[],
    )

    profile = runner._select_runtime_profile(spec, "puzzle_grid")

    assert profile == "tap_challenge_combo"


def test_quality_gate_errors_cover_visual_and_character_requirements():
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="A heroic cat rescues runners in a neon city.",
        entities=[],
    )
    review = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=6.0,
        visual_polish_score=5.0,
        character_quality_score=4.5,
        issues=["Visual feedback feels flat"],
    )
    quality = SimpleNamespace(final_score=5.8)

    errors = V2PipelineRunner._quality_gate_errors(spec, review, quality)

    assert any("fun_score" in error for error in errors)
    assert any("visual_polish_score" in error for error in errors)
    assert any("character_quality_score" in error for error in errors)
    assert any("overall quality score" in error for error in errors)


def test_quality_gate_requires_structured_review_when_review_is_expected():
    spec = GameSpec(
        game_type="casual",
        generation_tier="showcase",
        source_description="A premium action game with a hero character.",
        entities=[],
    )
    review = LLMReviewResult(ran=False)
    quality = SimpleNamespace(final_score=9.0)

    errors = V2PipelineRunner._quality_gate_errors(
        spec,
        review,
        quality,
        review_required=True,
    )

    assert errors == [
        "Structured code review did not return a valid quality assessment.",
    ]


def test_quality_gate_allows_standard_tier_when_structured_review_is_missing():
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="A polished delivery runner.",
        entities=[],
    )
    review = LLMReviewResult(ran=False)
    quality = SimpleNamespace(final_score=7.2)

    errors = V2PipelineRunner._quality_gate_errors(
        spec,
        review,
        quality,
        review_required=V2PipelineRunner._is_structured_review_required(spec),
    )

    assert errors == []


def test_quality_gate_blocks_showcase_results_with_heavy_review_penalty():
    spec = GameSpec(
        game_type="casual",
        generation_tier="showcase",
        source_description="A premium cyber ronin action game with a realistic hero.",
        entities=[],
    )
    review = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=8.4,
        visual_polish_score=8.2,
        character_quality_score=8.1,
        issues=[],
    )
    quality = SimpleNamespace(final_score=8.7, review_bonus=-2.7)

    errors = V2PipelineRunner._quality_gate_errors(
        spec,
        review,
        quality,
        review_required=V2PipelineRunner._is_structured_review_required(spec),
    )

    assert any("review_bonus -2.7" in error for error in errors)


def test_run_create_impl_retries_when_review_quality_gate_fails():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-quality-gate",
        user_id="user-quality-gate",
        raw_user_input="make a realistic animal rescue runner",
    )
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="A realistic animal rescue runner with a cat hero.",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "runner"}],
    )
    runtime_contract = GameRuntimeContract(runtime_profile="casual_lane_dash")
    generated_first = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 360; canvas.height = 640; const ctx = canvas.getContext('2d'); function render(){ctx.clearRect(0,0,canvas.width,canvas.height);} </script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-a"},
    )
    generated_second = GenerateCodeResult(
        html_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const canvas=document.getElementById('gameCanvas'); canvas.width = 360; canvas.height = 640; const ctx = canvas.getContext('2d'); function render(){ctx.fillRect(0,0,canvas.width,canvas.height);} </script></body></html>",
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=100,
        route_snapshot={"provider_id": "provider-b"},
    )
    qa_success_first = SimpleNamespace(
        success=True,
        code=generated_first.html_code,
        retries=0,
        needs_regeneration=False,
        issue_list=None,
    )
    qa_success_second = SimpleNamespace(
        success=True,
        code=generated_second.html_code,
        retries=0,
        needs_regeneration=False,
        issue_list=None,
    )
    low_review = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=6.0,
        visual_polish_score=5.0,
        character_quality_score=4.8,
        issues=["Character presentation feels like placeholder geometry"],
    )
    high_review = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=7.4,
        visual_polish_score=7.2,
        character_quality_score=6.6,
        issues=[],
    )
    low_quality = SimpleNamespace(
        final_score=5.9,
        details={},
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.0,
        retry_penalty=0.0,
        runtime_bonus=0.0,
        review_bonus=0.0,
        gameplay_depth_bonus=0.0,
    )
    high_quality = SimpleNamespace(
        final_score=7.1,
        details={},
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.0,
        retry_penalty=0.0,
        runtime_bonus=0.0,
        review_bonus=0.0,
        gameplay_depth_bonus=0.0,
    )

    with patch.object(
        runner,
        "_build_create_spec",
        new=AsyncMock(return_value=spec),
    ), patch.object(
        runner,
        "_select_runtime_profile",
        return_value="casual_lane_dash",
    ), patch.object(
        runner,
        "_compose_runtime_contract",
        return_value=runtime_contract,
    ), patch.object(
        runner,
        "_build_gdd",
        new=AsyncMock(return_value=GDD()),
    ), patch.object(
        runner,
        "_remember_spec",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_runtime_contract",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_code",
        new=AsyncMock(),
    ), patch(
        "src.engine.pipeline_v2_runner.task_memory.append_decision",
        new=AsyncMock(),
    ), patch.object(
        runner.pre_gen_validator,
        "validate",
        return_value=[],
    ), patch.object(
        runner,
        "_generate_create_code",
        new=AsyncMock(side_effect=[(generated_first, []), (generated_second, [])]),
    ) as mock_generate, patch.object(
        runner,
        "_run_contract_and_runtime_flow",
        new=AsyncMock(side_effect=[
            (qa_success_first, SimpleNamespace(ran=True), 0, []),
            (qa_success_second, SimpleNamespace(ran=True), 0, []),
        ]),
    ), patch.object(
        runner.qa_pipeline,
        "check",
        side_effect=[
            SimpleNamespace(passed=True, errors=[], warnings=[]),
            SimpleNamespace(passed=True, errors=[], warnings=[]),
        ],
    ), patch.object(
        runner,
        "_should_run_code_review",
        return_value=True,
    ), patch.object(
        runner.code_reviewer,
        "review",
        new=AsyncMock(side_effect=[low_review, high_review]),
    ), patch.object(
        runner.quality_scorer,
        "compute",
        side_effect=[low_quality, high_quality],
    ), patch.object(
        runner.code_generator.template_cache,
        "store",
    ), patch.object(
        runner,
        "_serialize_runtime_qa",
        return_value={},
    ):
        response = asyncio.run(
            runner._run_create_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == generated_second.html_code
    assert mock_generate.await_count == 2
    second_call = mock_generate.await_args_list[1].kwargs
    assert "QUALITY AND PRESENTATION CORRECTIONS" in second_call["generation_guidance"]


def test_run_iterate_impl_retries_after_runtime_qa_failure():
    runner = V2PipelineRunner()
    request = IterateV2Request(
        game_id="game-iterate-runtime-qa-retry",
        user_id="user-iterate-runtime-qa-retry",
        current_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
        iteration_intent={"feedback": "make the combo finale more dramatic", "conversation": []},
        source_spec=GameSpec(game_type="educational", generation_tier="showcase"),
        source_bundle_context=SourceBundleContext(title="History Quiz Show"),
        runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid"),
    )
    spec = GameSpec(
        game_type="educational",
        generation_tier="showcase",
        entities=[],
        special_rules=[],
    )
    runtime_contract = GameRuntimeContract(runtime_profile="puzzle_grid")
    qa_success = SimpleNamespace(
        success=True,
        code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>console.log('ok')</script></body></html>",
        retries=0,
        issue_list=None,
    )
    runtime_qa = SimpleNamespace(unavailable_reason=None, js_errors=[])
    runtime_failure = PipelineExecutionError(
        "Generated code failed runtime QA: Runtime QA detected no visible state change after user interaction",
        stage="runtime_simulation_qa",
        failure_family="runtime_qa",
    )

    with patch.object(
        runner,
        "_build_iteration_spec",
        new=AsyncMock(return_value=spec),
    ), patch.object(
        runner,
        "_select_runtime_profile",
        return_value="puzzle_grid",
    ), patch.object(
        runner,
        "_compose_runtime_contract",
        return_value=runtime_contract,
    ), patch.object(
        runner,
        "_remember_spec",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_runtime_contract",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_remember_code",
        new=AsyncMock(),
    ), patch(
        "src.engine.pipeline_v2_runner.task_memory.append_decision",
        new=AsyncMock(),
    ), patch.object(
        runner,
        "_generate_iteration_code",
        new=AsyncMock(side_effect=[
            ("<!DOCTYPE html><html><body>bad</body></html>", IterationType.element_change),
            ("<!DOCTYPE html><html><body>good</body></html>", IterationType.element_change),
        ]),
    ) as mock_generate_iteration, patch.object(
        runner,
        "_run_contract_and_runtime_flow",
        new=AsyncMock(side_effect=[
            runtime_failure,
            (qa_success, runtime_qa, 0, []),
        ]),
    ):
        response = asyncio.run(
            runner._run_iterate_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == qa_success.code
    assert response.iteration_type == IterationType.element_change.value
    assert mock_generate_iteration.await_count == 2


def test_retryable_generation_error_detects_wrapped_readtimeout():
    exc = RuntimeError("LLM iterate failed: ReadTimeout")

    assert V2PipelineRunner._is_retryable_generation_error(exc) is True
