"""Regression tests for v2 pipeline runtime-contract validation."""

import asyncio
import os
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    GDD,
    GameRuntimeContract,
    GameSpec,
    GenerateCodeResult,
    GenerationTier,
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
from src.engine.pipeline_errors import PipelineExecutionError
from src.engine.quality_scorer import LLMReviewResult, QualityScoreBreakdown


@pytest.mark.parametrize('handler', [
    "canvas.addEventListener('click', handleClick);",
    'canvas.addEventListener("mousedown", function(e) { startGame(); });',
    "canvas.onclick = handleClick;",
    "canvas.addEventListener /* comment */ ('click', () => startGame());",
])
def test_desktop_contract_honors_registered_mouse_fallback(handler):
    runner = V2PipelineRunner()
    spec = GameSpec(game_type='puzzle', source_description='桌面横屏，鼠标点击拼块，重新开始')
    contract = runner._compose_runtime_contract(base_contract=GameRuntimeContract(),
        spec=spec, runtime_profile='puzzle_grid', entrypoint='create')
    assert contract.input.required_modes == ['pointer']
    assert contract.input.allow_mouse_fallback
    code = '<html><script>' + handler + '</script></html>'
    assert not any(e.type == 'contract_input' for e in runner._validate_runtime_contract(code, contract))


@pytest.mark.parametrize('code', [
    '<html><!-- canvas.addEventListener("click", handler); --></html>',
    '<html><script>// canvas.addEventListener("click", handler);\n</script></html>',
    '<html><script>const example = "canvas.onclick = handler";</script></html>',
    '<html><script type="application/json">{"sample": "canvas.onclick = handler"}</script></html>',
    '<html><script>canvas.addEventListener("click", null);</script></html>',
    '<html><script>canvas.onclick = null;</script></html>',
    '<html><button onclick="">Start</button></html>',
])
def test_mouse_fallback_requires_registration_not_prose_or_null(code):
    runner = V2PipelineRunner()
    contract = GameRuntimeContract(input={'required_modes': ['pointer'], 'allow_mouse_fallback': True})
    assert any(e.type == 'contract_input' for e in runner._validate_runtime_contract(code, contract))


@pytest.mark.parametrize('modes,allow', [(['touch'], True), (['touch','pointer'], True), (['pointer'], False)])
def test_mouse_fallback_preserves_strict_input_contracts(modes, allow):
    runner = V2PipelineRunner()
    contract = GameRuntimeContract(input={'required_modes': modes, 'allow_mouse_fallback': allow})
    code = '<html><script>canvas.addEventListener("click", handleClick);</script></html>'
    assert any(e.type == 'contract_input' for e in runner._validate_runtime_contract(code, contract))


def test_mouse_fallback_accepts_an_explicit_inline_handler():
    from src.engine.mouse_input_detection import has_registered_mouse_handler
    assert has_registered_mouse_handler('<button onclick="startGame()">Start</button>')


def _transport_generate_error(status: int, provider_id: str = "primary"):
    import httpx
    http_request = httpx.Request("POST", "https://www.zltokens.example/v1/chat/completions")
    upstream = httpx.HTTPStatusError(
        f"Server error '{status} Gateway Timeout' for url '{http_request.url}'",
        request=http_request,
        response=httpx.Response(status, request=http_request),
    )
    wrapped = RuntimeError("Full LLM generation failed")
    wrapped.__cause__ = upstream
    wrapped.route_snapshot = {"provider_id": provider_id, "fallback_provider_ids": []}
    return wrapped


def _run_create_with_generate_error(generate, *, sleep=None):
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(game_id="transport-failure", user_id="user", raw_user_input="make a puzzle game")
    spec = GameSpec(game_type="puzzle", generation_tier="standard")
    contract = GameRuntimeContract(runtime_profile="puzzle_grid")
    with ExitStack() as stack:
        for name, value in (("_build_create_spec",spec), ("_build_gdd",GDD()),
                            ("_remember_spec",None), ("_remember_runtime_contract",None)):
            stack.enter_context(patch.object(runner, name, new=AsyncMock(return_value=value)))
        stack.enter_context(patch.object(runner, "_select_runtime_profile", return_value="puzzle_grid"))
        stack.enter_context(patch.object(runner, "_compose_runtime_contract", return_value=contract))
        stack.enter_context(patch.object(runner.pre_gen_validator, "validate", return_value=[]))
        stack.enter_context(patch("src.engine.pipeline_v2_runner.task_memory.append_decision", new=AsyncMock()))
        stack.enter_context(patch.object(runner.code_generator, "generate", new=generate))
        if sleep is not None:
            stack.enter_context(patch("src.engine.pipeline_v2_runner.asyncio.sleep", new=sleep))
        with pytest.raises(PipelineExecutionError) as caught:
            asyncio.run(runner._run_create_impl(request, None, {"stage":"spec_build"}))
    return caught.value


@pytest.mark.parametrize("status", [400, 401])
def test_non_retryable_provider_http_failure_does_not_enter_quality_regeneration(status):
    wrapped = _transport_generate_error(status)
    generate = AsyncMock(side_effect=wrapped)
    caught = _run_create_with_generate_error(generate)
    assert generate.await_count == 1
    assert generate.await_args.kwargs.get("generation_guidance") is None
    assert caught.failure_family == "provider_transport"
    assert caught.route_snapshot["provider_id"] == "primary"
    assert caught.__cause__ is wrapped


@pytest.mark.parametrize("status", [403, 429, 502, 503, 504])
def test_retryable_provider_http_failure_retries_once_without_creative_guidance(status):
    first = _transport_generate_error(status)
    second = _transport_generate_error(status)
    generate = AsyncMock(side_effect=[first, second])
    sleep = AsyncMock()
    caught = _run_create_with_generate_error(generate, sleep=sleep)
    assert generate.await_count == 2
    assert all(call.kwargs.get("generation_guidance") is None for call in generate.await_args_list)
    assert all(
        not call.kwargs.get("excluded_provider_ids")
        for call in generate.await_args_list
    )
    assert sleep.await_count == 1
    assert caught.failure_family == "provider_transport"
    assert caught.retry_count >= 1
    assert caught.__cause__ is second


def test_provider_403_retry_stays_on_same_primary_even_when_fallback_ids_exist():
    first = _transport_generate_error(403, provider_id="deepseek")
    first.route_snapshot = {"provider_id": "deepseek", "fallback_provider_ids": ["kimi-k3"]}
    second = _transport_generate_error(403, provider_id="deepseek")
    second.route_snapshot = {"provider_id": "deepseek", "fallback_provider_ids": ["kimi-k3"]}
    generate = AsyncMock(side_effect=[first, second])
    sleep = AsyncMock()
    caught = _run_create_with_generate_error(generate, sleep=sleep)
    assert generate.await_count == 2
    assert all(
        not call.kwargs.get("excluded_provider_ids")
        for call in generate.await_args_list
    )
    assert all(call.kwargs.get("generation_guidance") is None for call in generate.await_args_list)
    assert caught.failure_family == "provider_transport"
    assert caught.route_snapshot["provider_id"] == "deepseek"


def test_provider_transport_retry_is_not_a_creative_quality_loop():
    from src.engine.pipeline_errors import is_retryable_provider_transport_failure
    wrapped = _transport_generate_error(504)
    assert is_retryable_provider_transport_failure(wrapped)
    assert is_retryable_provider_transport_failure(_transport_generate_error(403))
    assert not is_retryable_provider_transport_failure(_transport_generate_error(401))
    assert not is_retryable_provider_transport_failure(ValueError("HTML code says 504; not an HTTP exception"))


def test_provider_transport_detection_preserves_semantic_errors_and_handles_cycles():
    import httpx
    from src.engine.pipeline_errors import is_provider_transport_failure
    assert is_provider_transport_failure(httpx.ReadTimeout("timeout"))
    assert not is_provider_transport_failure(ValueError("HTML code says 504; not an HTTP exception"))
    first, second = RuntimeError("first"), RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first
    assert not is_provider_transport_failure(first)


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


def test_reference_skeleton_is_safe_tier_only():
    skeleton = "<html><body><canvas id='gameCanvas'></canvas></body></html>"
    standard_spec = GameSpec(
        game_type="casual",
        generation_tier=GenerationTier.standard,
        source_description="Make a cat jumping game",
    )
    showcase_spec = GameSpec(
        game_type="casual",
        generation_tier=GenerationTier.showcase,
        source_description="Make a cat jumping game",
    )
    safe_spec = GameSpec(
        game_type="casual",
        generation_tier=GenerationTier.safe,
        source_description="Make a cat jumping game",
    )

    assert not CodeGenerator._should_include_reference_skeleton(
        standard_spec,
        request_text="cat jump",
        skeleton=skeleton,
        design_program_block="",
    )
    assert not CodeGenerator._should_include_reference_skeleton(
        showcase_spec,
        request_text="cat jump",
        skeleton=skeleton,
        design_program_block="",
    )
    assert CodeGenerator._should_include_reference_skeleton(
        safe_spec,
        request_text="cat jump",
        skeleton=skeleton,
        design_program_block="",
    )


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
    assert V2PipelineRunner._build_create_generation_attempt_plan("standard") == ("standard", "simple", "safe")
    assert V2PipelineRunner._build_create_generation_attempt_plan("simple") == ("simple", "standard")
    assert V2PipelineRunner._build_create_generation_attempt_plan("complex") == ("complex", "standard")
    assert V2PipelineRunner._build_create_generation_attempt_plan("showcase") == ("showcase", "complex", "standard")


def test_showcase_near_miss_can_be_accepted_when_shippable():
    spec = GameSpec(game_type="casual", generation_tier=GenerationTier.showcase)
    review = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        fun_score=7.8,
        visual_polish_score=7.7,
        character_quality_score=7.5,
    )
    quality = SimpleNamespace(final_score=8.1, review_bonus=-1.8)

    assert V2PipelineRunner._can_accept_showcase_near_miss(
        spec,
        review,
        quality,
        ["Raise the overall quality score from 8.1 to at least 8.5."],
    )


def test_showcase_near_miss_rejects_missing_gameplay():
    spec = GameSpec(game_type="casual", generation_tier=GenerationTier.showcase)
    review = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=False,
        fun_score=8.0,
        visual_polish_score=8.0,
        character_quality_score=7.5,
    )
    quality = SimpleNamespace(final_score=8.3, review_bonus=-1.0)

    assert not V2PipelineRunner._can_accept_showcase_near_miss(
        spec,
        review,
        quality,
        ["Strengthen the moment-to-moment gameplay so the result has a real playable loop."],
    )


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


def test_generate_create_code_auto_repairs_null_ctx_before_preflight_failure():
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-preflight-ctx",
        user_id="user-preflight-ctx",
        raw_user_input="make a dodge game",
    )
    spec = GameSpec(game_type="casual", core_mechanics=[{"type": "tap_dodge"}])
    runtime_contract = GameRuntimeContract(runtime_profile="casual_arcade")
    generated = GenerateCodeResult(
        html_code=(
            "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>"
            "const canvas=document.getElementById('gameCanvas');"
            "canvas.width=360;canvas.height=640;"
            "let ctx=null;"
            "function loop(){ctx.clearRect(0,0,canvas.width,canvas.height);requestAnimationFrame(loop);}"
            "requestAnimationFrame(loop);"
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

    assert "__bootCanvas" in result.html_code
    assert not any(issue.code == "nullable_runtime_object:ctx" for issue in preflight_issues)


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


def test_contract_qa_loop_classifies_syntax_repair_truncation_without_leaking():
    from src.services.llm_client import LLMResponseTruncatedError

    runner = V2PipelineRunner()
    syntax_errors = [
        QACheckError(
            type="L1_syntax",
            message="JavaScript syntax error in <script>: Unexpected end of input",
            severity="error",
        ),
    ]

    with patch.object(
        runner,
        "_validate_contract_bundle",
        return_value=syntax_errors,
    ), patch.object(
        runner.qa_pipeline,
        "repair_code",
        new=AsyncMock(side_effect=LLMResponseTruncatedError(
            "OpenAI-compatible response hit the output length limit and may be truncated"
        )),
    ):
        result = asyncio.run(
            runner._run_contract_qa_loop(
                code="<!DOCTYPE html><html><body><script>" + ("const x=1;" * 200) + "</script></body></html>",
                spec=GameSpec(game_type="puzzle"),
                runtime_contract=GameRuntimeContract(runtime_profile="puzzle_grid"),
                prompt_bundle_snapshot={},
                progress_cb=lambda *_args, **_kwargs: None,
                game_id="game-qa-truncation",
                user_id="user-qa-truncation",
            )
        )

    assert result.success is False
    assert result.needs_regeneration is True
    assert result.truncated is True
    assert any(error.type == "qa_truncation" for error in result.last_errors)
    assert any("output length limit" in error.message for error in result.last_errors)


def test_quality_guidance_keeps_placeholder_and_fun_score_bars():
    from src.engine.generated_quality_policy import QUALITY_POLICY
    from src.engine.quality_scorer import LLMReviewResult

    assert QUALITY_POLICY["tiers"]["standard"]["fun_score"] == 6.8
    review = LLMReviewResult(
        ran=True,
        is_complete_game=False,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=7.0,
        visual_polish_score=7.0,
        character_quality_score=7.0,
        issues=[],
    )
    errors = V2PipelineRunner._quality_gate_errors(
        GameSpec(game_type="casual", generation_tier="standard"),
        review,
        SimpleNamespace(final_score=7.0, review_bonus=0.0),
    )
    assert "Return a complete, polished game instead of an incomplete or placeholder output." in errors
    guidance = V2PipelineRunner._build_review_quality_guidance(
        GameSpec(game_type="casual", generation_tier="standard"),
        review,
        SimpleNamespace(final_score=7.0),
        errors,
    )
    assert "incomplete or placeholder-like" in guidance
    assert "Keep the existing fun_score" in guidance


def test_quality_regeneration_guidance_adds_forbidden_api_recipe_without_removing_bans():
    guidance = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="contract_qa",
        message="Generated code failed contract QA: Forbidden API detected: XMLHttpRequest",
        errors=[QACheckError(type="L2_security", message="Forbidden API detected: WebSocket", severity="error")],
    )

    assert "XMLHttpRequest" in guidance
    assert "WebSocket" in guidance
    assert "Forbidden API" in guidance
    assert "in-memory variables" in guidance


def test_truncation_compactness_guidance_keeps_forbidden_api_bans():
    guidance = V2PipelineRunner._build_truncation_compactness_guidance()
    assert "OUTPUT LENGTH RECOVERY" in guidance
    assert "XMLHttpRequest" in guidance
    assert "WebSocket" in guidance
    assert "playable loop" in guidance


def test_run_create_impl_retries_truncated_generation_with_compactness_and_safe_budget():
    from src.services.llm_client import LLMResponseTruncatedError

    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-truncation",
        user_id="user-truncation",
        raw_user_input="做一个太空躲避手机小游戏",
    )
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="做一个太空躲避手机小游戏",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "dodge"}],
    )
    runtime_contract = GameRuntimeContract(runtime_profile="casual_arcade")
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

    def _truncation_error():
        inner = LLMResponseTruncatedError(
            "OpenAI-compatible response hit the output length limit and may be truncated",
            stop_reason="length",
        )
        wrapped = PipelineExecutionError(
            f"Full LLM generation failed: {inner}",
            stage="logic_generate",
            failure_family="code_generation",
        )
        wrapped.__cause__ = inner
        return wrapped

    with patch.object(runner, "_build_create_spec", new=AsyncMock(return_value=spec)), patch.object(
        runner, "_select_runtime_profile", return_value="casual_arcade",
    ), patch.object(
        runner, "_compose_runtime_contract", return_value=runtime_contract,
    ), patch.object(
        runner, "_build_gdd", new=AsyncMock(return_value=GDD()),
    ), patch.object(
        runner, "_remember_spec", new=AsyncMock(),
    ), patch.object(
        runner, "_remember_runtime_contract", new=AsyncMock(),
    ), patch.object(
        runner, "_remember_code", new=AsyncMock(),
    ), patch(
        "src.engine.pipeline_v2_runner.task_memory.append_decision", new=AsyncMock(),
    ), patch.object(
        runner.pre_gen_validator, "validate", return_value=[],
    ), patch.object(
        runner, "_build_create_generation_attempt_plan", return_value=("simple", "standard"),
    ), patch.object(
        runner, "_generate_create_code",
        new=AsyncMock(side_effect=[_truncation_error(), _truncation_error(), (generated_valid, [])]),
    ) as mock_generate, patch.object(
        runner, "_run_contract_and_runtime_flow",
        new=AsyncMock(return_value=(qa_success, SimpleNamespace(ran=True), 0, [])),
    ), patch.object(
        runner.qa_pipeline, "check", return_value=SimpleNamespace(passed=True, errors=[], warnings=[]),
    ), patch.object(
        runner, "_should_run_code_review", return_value=False,
    ), patch.object(
        runner.quality_scorer, "compute", return_value=quality_result,
    ), patch.object(
        runner, "_serialize_runtime_qa", return_value={},
    ), patch(
        "src.engine.pipeline_v2_runner.asyncio.sleep", new=AsyncMock(),
    ):
        response = asyncio.run(
            runner._run_create_impl(request, progress_cb=None, stage_context={"stage": "spec_build"})
        )

    assert response.html_code == generated_valid.html_code
    assert mock_generate.await_count == 3
    assert mock_generate.await_args_list[0].kwargs["budget_override"] == "simple"
    assert mock_generate.await_args_list[1].kwargs["budget_override"] == "standard"
    assert mock_generate.await_args_list[2].kwargs["budget_override"] == "safe"
    assert "OUTPUT LENGTH RECOVERY" in mock_generate.await_args_list[1].kwargs["generation_guidance"]
    assert "XMLHttpRequest" in mock_generate.await_args_list[2].kwargs["generation_guidance"]


def test_quality_regeneration_guidance_adds_hex_adjacency_recipe():
    guidance = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="code_review",
        message="Raise gameplay excitement: fun_score 6.0 is below the required 6.8. Hex adjacency never groups same-color bubbles.",
    )
    assert "fun_score 6.0 is below the required 6.8" in guidance
    assert "six neighbors" in guidance
    assert "Flood-fill" in guidance


def test_quality_regeneration_guidance_adds_coordinate_guard_recipe_for_undefined_x_runtime_failures():
    guidance = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="runtime_simulation_qa",
        message="Generated code failed runtime QA: Runtime JS error: Cannot read properties of undefined (reading 'x')",
    )

    assert ".x` / `.y`" in guidance
    assert "Initialize moving entities" in guidance


def test_quality_regeneration_guidance_adds_generic_tdz_and_grid_alias_recipes():
    tdz = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="logic_generate",
        message=(
            "Generated code failed preflight: Declare or inline `nc` before use; "
            "`const nc = ...` is in the temporal dead zone when a hoisted init path runs first."
        ),
    )
    assert "function name() {}" in tdz
    assert "cannot hit TDZ" in tdz

    neighbors = V2PipelineRunner._build_quality_regeneration_guidance(
        stage="logic_generate",
        message="Generated code failed preflight: Declare or inline `nc` before use; it is referenced as a live expression.",
    )
    assert "let nr, nc;" in neighbors
    assert "Never read undeclared `nr` / `nc`" in neighbors


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
    assert second_call["excluded_provider_ids"] == ["provider-a"]
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
        last_errors=[QACheckError(type="contract_gameplay", message="Missing deterministic merge cleanup helper", severity="error")],
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


def test_create_outcome_labels_distinguish_pipeline_success_from_seed_worthy():
    passing_review = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        fun_score=7.4,
        visual_polish_score=7.2,
        character_quality_score=6.6,
    )
    qa_ok = SimpleNamespace(success=True)
    labels = V2PipelineRunner._create_outcome_labels(
        review=passing_review,
        qa_result=qa_ok,
        qa_warnings=[],
        quality_gate_errors=[],
    )
    assert labels["pipeline_success"] is True
    assert labels["seed_worthy"] is True
    assert labels["seed_worthy_reason"] == "structured_review_passed"

    degraded = V2PipelineRunner._create_outcome_labels(
        review=LLMReviewResult(ran=False),
        qa_result=qa_ok,
        qa_warnings=[{"type": "review_infrastructure_degraded"}],
        quality_gate_errors=[],
    )
    assert degraded["pipeline_success"] is True
    assert degraded["seed_worthy"] is False
    assert degraded["seed_worthy_reason"] == "review_infrastructure_degraded"

    missing_review = V2PipelineRunner._create_outcome_labels(
        review=LLMReviewResult(ran=False),
        qa_result=qa_ok,
        qa_warnings=[],
        quality_gate_errors=[],
    )
    assert missing_review["pipeline_success"] is True
    assert missing_review["seed_worthy"] is False
    assert missing_review["seed_worthy_reason"] == "structured_review_missing"

    actionability = V2PipelineRunner._create_outcome_labels(
        review=LLMReviewResult(ran=False),
        qa_result=qa_ok,
        qa_warnings=[{"type": "review_actionability_degraded"}],
        quality_gate_errors=[],
    )
    assert actionability["pipeline_success"] is True
    assert actionability["seed_worthy"] is False
    assert actionability["seed_worthy_reason"] == "review_actionability_degraded"


def test_repair_fallback_guidance_keeps_quality_and_contract_signals():
    from src.engine.pipeline_errors import PipelineExecutionError

    base = V2PipelineRunner._build_review_quality_guidance(
        GameSpec(game_type="casual", generation_tier="standard", source_description="neon dodge"),
        LLMReviewResult(
            ran=True,
            is_complete_game=True,
            has_real_gameplay=True,
            fun_score=6.0,
            visual_polish_score=5.8,
            character_quality_score=5.5,
            issues=["Visual feedback feels flat"],
        ),
        SimpleNamespace(final_score=5.9),
        ["Raise gameplay excitement and payoff: fun_score 6.0 is below the required 6.8."],
    )
    guidance = V2PipelineRunner._append_repair_fallback_guidance(
        base,
        PipelineExecutionError(
            "Quality repair failed during contract_qa: patch_static_qa_failed:keyboard handler removed",
            stage="code_review",
            failure_family="repair_contract",
        ),
    )

    assert "QUALITY AND PRESENTATION CORRECTIONS" in guidance
    assert "keyboard handler removed" in guidance
    assert "LOCAL REPAIR FAILED" in guidance


@pytest.mark.asyncio
async def test_resolve_create_review_degrades_infrastructure_failure_for_standard_tier():
    runner = V2PipelineRunner()
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="A polished delivery runner.",
        entities=[],
    )
    qa_warnings: list[dict] = []
    infra_error = PipelineExecutionError(
        "Code review evidence could not be validated: assessment unavailable",
        stage="code_review",
        failure_family="review_infrastructure",
    )

    resolve = AsyncMock(side_effect=infra_error)
    with patch.object(runner, "_resolve_concurrent_review", new=resolve), patch.object(
        runner, "_review_infrastructure_retry_backoff_s", return_value=0
    ):
        review = await runner._resolve_create_review(
            None,
            "<html></html>",
            spec=spec,
            review_requested=True,
            qa_warnings=qa_warnings,
            progress_cb=None,
            game_id="game-1",
            user_id="user-1",
        )

    assert resolve.await_count == 2
    assert review.ran is False
    assert qa_warnings[0]["type"] == "review_infrastructure_degraded"


@pytest.mark.asyncio
async def test_resolve_create_review_retries_infrastructure_and_returns_real_review():
    runner = V2PipelineRunner()
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="A polished delivery runner.",
        entities=[],
    )
    qa_warnings: list[dict] = []
    infra_error = PipelineExecutionError(
        "Code review evidence could not be validated: assessment unavailable",
        stage="code_review",
        failure_family="review_infrastructure",
    )
    recovered = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        fun_score=7.4,
        visual_polish_score=7.2,
        character_quality_score=6.6,
    )
    resolve = AsyncMock(side_effect=[infra_error, recovered])
    with patch.object(runner, "_resolve_concurrent_review", new=resolve), patch.object(
        runner, "_review_infrastructure_retry_backoff_s", return_value=0
    ):
        review = await runner._resolve_create_review(
            None,
            "<html></html>",
            spec=spec,
            review_requested=True,
            qa_warnings=qa_warnings,
            progress_cb=None,
            game_id="game-1",
            user_id="user-1",
        )

    assert resolve.await_count == 2
    assert review is recovered
    assert review.ran is True
    assert qa_warnings == []


@pytest.mark.asyncio
async def test_resolve_create_review_keeps_showcase_fail_closed_on_infrastructure_failure():
    runner = V2PipelineRunner()
    spec = GameSpec(
        game_type="casual",
        generation_tier="showcase",
        source_description="A premium showcase runner.",
        entities=[],
    )
    infra_error = PipelineExecutionError(
        "Code review evidence could not be validated: assessment unavailable",
        stage="code_review",
        failure_family="review_infrastructure",
    )

    resolve = AsyncMock(side_effect=infra_error)
    with patch.object(runner, "_resolve_concurrent_review", new=resolve), patch.object(
        runner, "_review_infrastructure_retry_backoff_s", return_value=0
    ):
        with pytest.raises(PipelineExecutionError) as caught:
            await runner._resolve_create_review(
                None,
                "<html></html>",
                spec=spec,
                review_requested=True,
                qa_warnings=[],
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
            )

    assert resolve.await_count == 2
    assert caught.value.failure_family == "review_infrastructure"


@pytest.mark.asyncio
async def test_resolve_create_review_degrades_actionability_failure_for_standard_tier():
    runner = V2PipelineRunner()
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="A polished delivery runner.",
        entities=[],
    )
    qa_warnings: list[dict] = []
    actionability_error = PipelineExecutionError(
        "Code review evidence could not be validated: unexplained_score:fun_score=6.0 requires a source-grounded defect",
        stage="code_review",
        failure_family="review_actionability",
    )

    with patch.object(
        runner,
        "_resolve_concurrent_review",
        new=AsyncMock(side_effect=actionability_error),
    ):
        review = await runner._resolve_create_review(
            None,
            "<html></html>",
            spec=spec,
            review_requested=True,
            qa_warnings=qa_warnings,
            progress_cb=None,
            game_id="game-1",
            user_id="user-1",
        )

    assert review.ran is False
    assert qa_warnings[0]["type"] == "review_actionability_degraded"


@pytest.mark.asyncio
async def test_resolve_create_review_keeps_showcase_fail_closed_on_actionability_failure():
    runner = V2PipelineRunner()
    spec = GameSpec(
        game_type="casual",
        generation_tier="showcase",
        source_description="A premium showcase runner.",
        entities=[],
    )
    actionability_error = PipelineExecutionError(
        "Code review evidence could not be validated: unexplained_score:fun_score=6.0",
        stage="code_review",
        failure_family="review_actionability",
    )

    with patch.object(
        runner,
        "_resolve_concurrent_review",
        new=AsyncMock(side_effect=actionability_error),
    ):
        with pytest.raises(PipelineExecutionError) as caught:
            await runner._resolve_create_review(
                None,
                "<html></html>",
                spec=spec,
                review_requested=True,
                qa_warnings=[],
                progress_cb=None,
                game_id="game-1",
                user_id="user-1",
            )

    assert caught.value.failure_family == "review_actionability"


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

    with patch.object(runner, "_should_attempt_quality_patch_repair", return_value=False), patch.object(
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


def _build_iterate_quality_fixtures():
    request = IterateV2Request(
        game_id="game-iterate-quality",
        user_id="user-iterate-quality",
        current_code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
        iteration_intent={"feedback": "add more particles", "conversation": []},
        source_spec=GameSpec(game_type="casual", generation_tier="standard"),
        source_bundle_context=SourceBundleContext(title="Particle Dash"),
        runtime_contract=GameRuntimeContract(runtime_profile="casual_arcade"),
    )
    spec = GameSpec(
        game_type="casual",
        generation_tier="standard",
        entities=[],
        special_rules=[],
    )
    runtime_contract = GameRuntimeContract(runtime_profile="casual_arcade")
    qa_success = SimpleNamespace(
        success=True,
        code="<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>console.log('ok')</script></body></html>",
        retries=0,
        issue_list=None,
    )
    runtime_qa = SimpleNamespace(unavailable_reason=None, js_errors=[])
    return request, spec, runtime_contract, qa_success, runtime_qa


def _enter_common_iterate_patches(stack, runner, spec, runtime_contract, qa_success, runtime_qa):
    stack.enter_context(patch.object(runner, "_build_iteration_spec", new=AsyncMock(return_value=spec)))
    stack.enter_context(patch.object(runner, "_select_runtime_profile", return_value="casual_arcade"))
    stack.enter_context(patch.object(runner, "_compose_runtime_contract", return_value=runtime_contract))
    stack.enter_context(patch.object(runner, "_remember_spec", new=AsyncMock()))
    stack.enter_context(patch.object(runner, "_remember_runtime_contract", new=AsyncMock()))
    stack.enter_context(patch.object(runner, "_remember_code", new=AsyncMock()))
    stack.enter_context(patch(
        "src.engine.pipeline_v2_runner.task_memory.append_decision",
        new=AsyncMock(),
    ))
    stack.enter_context(patch.object(
        runner,
        "_generate_iteration_code",
        new=AsyncMock(return_value=(
            "<!DOCTYPE html><html><body>updated</body></html>",
            IterationType.element_change,
        )),
    ))
    stack.enter_context(patch.object(
        runner,
        "_run_contract_and_runtime_flow",
        new=AsyncMock(return_value=(qa_success, runtime_qa, 0, [])),
    ))


def test_run_iterate_impl_attaches_quality_fields_when_assessment_succeeds():
    runner = V2PipelineRunner()
    request, spec, runtime_contract, qa_success, runtime_qa = _build_iterate_quality_fixtures()
    review = LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=8.0,
        visual_polish_score=7.5,
        character_quality_score=7.0,
    )
    quality = QualityScoreBreakdown(
        base_score=5.0,
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.5,
        retry_penalty=0.0,
        runtime_bonus=0.6,
        review_bonus=1.2,
        gameplay_depth_bonus=0.3,
        final_score=7.6,
        details={"review_ran": True, "review_fun_score": 8.0},
    )

    with ExitStack() as stack:
        _enter_common_iterate_patches(stack, runner, spec, runtime_contract, qa_success, runtime_qa)
        mock_review = stack.enter_context(patch.object(
            runner.code_reviewer,
            "review",
            new=AsyncMock(return_value=review),
        ))
        mock_compute = stack.enter_context(patch.object(
            runner.quality_scorer,
            "compute",
            return_value=quality,
        ))
        response = asyncio.run(
            runner._run_iterate_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == qa_success.code
    assert response.quality_score == 7.6
    assert response.quality_breakdown is not None
    assert response.quality_breakdown["review_fun_score"] == 8.0
    assert response.quality_breakdown["review_bonus"] == 1.2
    assert response.quality_breakdown["runtime_profile"] == "casual_arcade"
    assert response.quality_breakdown["iteration_type"] == IterationType.element_change.value
    assert mock_review.await_count == 1
    assert mock_compute.call_count == 1
    assert mock_compute.call_args.kwargs["review"] is review


def test_run_iterate_impl_succeeds_without_quality_fields_when_review_raises():
    runner = V2PipelineRunner()
    request, spec, runtime_contract, qa_success, runtime_qa = _build_iterate_quality_fixtures()

    with ExitStack() as stack:
        _enter_common_iterate_patches(stack, runner, spec, runtime_contract, qa_success, runtime_qa)
        stack.enter_context(patch.object(
            runner.code_reviewer,
            "review",
            new=AsyncMock(side_effect=RuntimeError("LLM exploded")),
        ))
        mock_compute = stack.enter_context(patch.object(runner.quality_scorer, "compute"))
        response = asyncio.run(
            runner._run_iterate_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == qa_success.code
    assert response.quality_score is None
    assert response.quality_breakdown is None
    assert mock_compute.call_count == 0


def test_run_iterate_impl_abandons_quality_assessment_on_timeout():
    runner = V2PipelineRunner()
    request, spec, runtime_contract, qa_success, runtime_qa = _build_iterate_quality_fixtures()

    async def slow_review(code):
        await asyncio.sleep(1.0)
        return LLMReviewResult(ran=True)

    with ExitStack() as stack:
        _enter_common_iterate_patches(stack, runner, spec, runtime_contract, qa_success, runtime_qa)
        stack.enter_context(patch.object(runner.code_reviewer, "review", new=slow_review))
        stack.enter_context(patch(
            "src.engine.pipeline_v2_runner.get_timeout_float",
            return_value=0.05,
        ))
        mock_compute = stack.enter_context(patch.object(runner.quality_scorer, "compute"))
        response = asyncio.run(
            runner._run_iterate_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == qa_success.code
    assert response.quality_score is None
    assert response.quality_breakdown is None
    assert mock_compute.call_count == 0


def test_run_iterate_impl_skips_quality_assessment_when_flag_disabled():
    runner = V2PipelineRunner()
    request, spec, runtime_contract, qa_success, runtime_qa = _build_iterate_quality_fixtures()

    with ExitStack() as stack:
        _enter_common_iterate_patches(stack, runner, spec, runtime_contract, qa_success, runtime_qa)
        stack.enter_context(patch.object(settings, "ITERATE_QUALITY_REVIEW_ENABLED", False))
        mock_review = stack.enter_context(patch.object(
            runner.code_reviewer,
            "review",
            new=AsyncMock(),
        ))
        mock_compute = stack.enter_context(patch.object(runner.quality_scorer, "compute"))
        response = asyncio.run(
            runner._run_iterate_impl(
                request,
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == qa_success.code
    assert response.quality_score is None
    assert response.quality_breakdown is None
    assert mock_review.await_count == 0
    assert mock_compute.call_count == 0


def test_generic_feedback_words_do_not_route_to_quiz_show():
    runner = V2PipelineRunner()
    for brief in [
        "Move a boat to catch falling stars with combo glow and a 60 second timer",
        "桌面小游戏，鼠标移动小船接星星，连击提示、远山舞台背景和节奏感",
        "A ghost flies through three stages with a spotlight and streak rewards",
    ]:
        spec = GameSpec(game_type="casual", source_description=brief)
        assert not runner._looks_like_quiz_show_runtime_request(spec)
        assert not runner._select_runtime_profile(spec, None).startswith("tap_challenge")
    spec = GameSpec(game_type="educational", source_description="A history quiz show with combo rewards")
    assert runner._looks_like_quiz_show_runtime_request(spec)
    assert runner._select_runtime_profile(spec, None) == "tap_challenge_combo"


def test_legacy_service_hint_cannot_pin_the_wrong_runtime():
    runner = V2PipelineRunner()
    spec = GameSpec(game_type="casual", source_description="Move a boat to catch falling stars with combo glow")
    for metadata in [{"source": "game-service"}, {"profile_selection": "auto"}]:
        contract = GameRuntimeContract(runtime_profile="tap_challenge_combo", metadata=metadata)
        hint = runner._requested_runtime_profile(contract)
        assert hint is None
        assert not runner._select_runtime_profile(spec, hint).startswith("tap_challenge")
    explicit = GameRuntimeContract(runtime_profile="tap_challenge_combo")
    assert runner._requested_runtime_profile(explicit) == "tap_challenge_combo"
    with patch("src.engine.pipeline_v2_runner._default_runtime_profile_id", return_value="casual_arcade"):
        assert runner._select_runtime_profile(spec, runner._requested_runtime_profile(explicit)) == "tap_challenge_combo"
