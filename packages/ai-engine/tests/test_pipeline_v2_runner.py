"""Regression tests for v2 pipeline runtime-contract validation."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import GameRuntimeContract, GameSpec, QACheckError
from src.engine.pipeline_v2_runner import V2PipelineRunner


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
                spec=GameSpec(game_type="runner"),
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


def test_runtime_qa_loop_allows_second_targeted_remediation_attempt():
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
    runtime_pass = SimpleNamespace(
        ran=True,
        js_errors=[],
        canvas_renders=True,
        registered_input_handlers=["pointerdown"],
        direct_input_handlers=[],
        triggered_input_handlers=["pointerdown"],
        interaction_performed=True,
        canvas_changed_after_input=True,
        dom_changed_after_input=False,
    )

    with patch(
        "src.engine.pipeline_v2_runner.run_runtime_qa",
        new=AsyncMock(side_effect=[runtime_fail, runtime_fail, runtime_pass]),
    ), patch(
        "src.engine.pipeline_v2_runner.settings.RUNTIME_QA_REMEDIATION_MAX_RETRIES",
        2,
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(side_effect=[
            "<!DOCTYPE html><html><body>fix-1</body></html>",
            "<!DOCTYPE html><html><body>fix-2</body></html>",
        ]),
    ) as mock_repair, patch.object(
        runner,
        "_run_contract_qa_loop",
        new=AsyncMock(side_effect=[
            SimpleNamespace(success=True, code="<!DOCTYPE html><html><body>fix-1-pass</body></html>", retries=0),
            SimpleNamespace(success=True, code="<!DOCTYPE html><html><body>fix-2-pass</body></html>", retries=0),
        ]),
    ):
        final_code, runtime_qa, retries = asyncio.run(
            runner._run_runtime_qa_loop(
                code="<!DOCTYPE html><html><body>initial</body></html>",
                spec=GameSpec(game_type="runner"),
                runtime_contract=GameRuntimeContract(),
                prompt_bundle_snapshot={"layers": {}},
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
            )
        )

    assert final_code == "<!DOCTYPE html><html><body>fix-2-pass</body></html>"
    assert runtime_qa.registered_input_handlers == ["pointerdown"]
    assert retries == 2
    assert mock_repair.await_count == 2


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
        final_code, runtime_qa, retries = asyncio.run(
            runner._run_runtime_qa_loop(
                code=code,
                spec=GameSpec(game_type="runner"),
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
        final_code, runtime_qa, retries = asyncio.run(
            runner._run_runtime_qa_loop(
                code=code,
                spec=GameSpec(game_type="runner"),
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


def test_select_runtime_profile_normalizes_descriptive_game_type_labels():
    runner = V2PipelineRunner()

    assert runner._select_runtime_profile(GameSpec(game_type="endless runner"), "portrait_arcade") == "lane_runner"
    assert runner._select_runtime_profile(GameSpec(game_type="top-down shooter"), "portrait_arcade") == "topdown_action"
    assert runner._select_runtime_profile(GameSpec(game_type="grid puzzle"), "portrait_arcade") == "grid_puzzle"


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


def test_runtime_qa_unavailable_in_production_persists_candidate_artifacts():
    runner = V2PipelineRunner()
    runtime_unavailable = SimpleNamespace(
        ran=False,
        unavailable_reason="runtime_qa_timeout:12.00s",
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
                    spec=GameSpec(game_type="runner"),
                    runtime_contract=GameRuntimeContract(),
                    prompt_bundle_snapshot={"layers": {}},
                    progress_cb=None,
                    game_id="game-1",
                    user_id="user-1",
                )
            )
            assert False, "expected PipelineExecutionError"
        except Exception as exc:
            assert "Runtime QA unavailable" in str(exc)
            artifacts = getattr(exc, "artifacts", [])
            assert any(item.get("artifact_type") == "failed_runtime_candidate" for item in artifacts)
            assert any(item.get("artifact_type") == "runtime_qa_report" for item in artifacts)
