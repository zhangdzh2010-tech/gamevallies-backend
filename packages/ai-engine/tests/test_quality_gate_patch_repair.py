"""Tests for quality-gate targeted patch repair on the v2 create pipeline."""

import asyncio
import json
import os
import sys
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    GDD,
    GameEntity,
    GameRuntimeContract,
    GameSpec,
    GenerateCodeResult,
    RunPipelineV2Request,
)
from src.config.settings import settings
from src.engine.pipeline_v2_runner import V2PipelineRunner
from src.engine.quality_scorer import LLMReviewResult

BASE_SCRIPT = (
    "const canvas=document.getElementById('gameCanvas');"
    "const ctx=canvas.getContext('2d');"
    "canvas.width=360;canvas.height=640;"
    + "ctx.fillRect(0,0,canvas.width,canvas.height);" * 20
)
BASE_CODE = (
    "<!DOCTYPE html><html><head><style>body{margin:0;background:#111;}</style></head>"
    "<body><canvas id='gameCanvas'></canvas><script>"
    + BASE_SCRIPT
    + "</script></body></html>"
)
PATCHED_SCRIPT = "// PATCHED_QUALITY_FIX\n" + BASE_SCRIPT
PATCH_RESPONSE_TEXT = json.dumps(
    {
        "patches": [
            {
                "section": "SCRIPT",
                "operation": "replace_section",
                "content": PATCHED_SCRIPT,
            }
        ]
    }
)


def _spec() -> GameSpec:
    return GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="a neon dodge arcade run with glowing orbs",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "runner"}],
    )


def test_catch_and_many_are_not_cat_and_man_characters():
    spec = _spec().model_copy(update={
        'source_description': 'Move a boat to catch as many stars as possible',
        'intent_summary': 'catch stars',
    })
    assert not V2PipelineRunner._is_character_driven_spec(spec)
    for description in ['a cat hero catches stars', 'a man sailing a boat', '主角是小狐狸']:
        assert V2PipelineRunner._is_character_driven_spec(spec.model_copy(update={'source_description': description}))
    review = LLMReviewResult(ran=True, is_complete_game=True, has_real_gameplay=True,
        difficulty_balanced=True, fun_score=6, visual_polish_score=6, character_quality_score=4)
    assert V2PipelineRunner._should_attempt_quality_patch_repair(spec, review, SimpleNamespace(final_score=6.5))


def test_scenery_mislabeled_as_npc_does_not_require_character_art():
    spec = _spec().model_copy(update={"entities": [
        GameEntity(name="crescent moon", role="npc"),
        GameEntity(name="distant mountains", role="npc"),
        GameEntity(name="glowing star dust", role="npc"),
    ]})
    assert not V2PipelineRunner._is_character_driven_spec(spec)
    assert V2PipelineRunner._is_character_driven_spec(spec.model_copy(update={
        "entities": [GameEntity(name="village merchant", role="npc")]}))


def _near_miss_review(**overrides) -> LLMReviewResult:
    values = dict(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=6.0,
        visual_polish_score=5.8,
        character_quality_score=5.5,
        issues=["Visual feedback feels flat"],
    )
    values.update(overrides)
    return LLMReviewResult(**values)


def _passing_review() -> LLMReviewResult:
    return LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=7.4,
        visual_polish_score=7.2,
        character_quality_score=6.6,
        issues=[],
    )


def _quality(final_score: float) -> SimpleNamespace:
    return SimpleNamespace(
        final_score=final_score,
        review_bonus=0.0,
        details={},
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.0,
        retry_penalty=0.0,
        runtime_bonus=0.0,
        gameplay_depth_bonus=0.0,
    )


def _generated(html_code: str, provider_id: str) -> GenerateCodeResult:
    return GenerateCodeResult(
        html_code=html_code,
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=len(html_code),
        route_snapshot={"provider_id": provider_id},
    )


def _qa_success(code: str) -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        code=code,
        retries=0,
        needs_regeneration=False,
        issue_list=None,
    )


def _run_create_with_mocks(
    *,
    generate_side_effect,
    flow_side_effect,
    review_side_effect,
    compute_side_effect,
    patch_text_return=PATCH_RESPONSE_TEXT,
    patch_text_side_effect=None,
    patch_repair_enabled=True,
    progress_cb=None,
    runtime_loop_error=None,
):
    """Drive _run_create_impl with the standard heavy-mock harness.

    Returns (response, mocks) where mocks exposes the generate / patch-text /
    runtime-loop AsyncMocks for assertions.
    """
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(
        game_id="game-quality-patch",
        user_id="user-quality-patch",
        raw_user_input="make a neon dodge arcade game",
    )
    spec = _spec()
    runtime_contract = GameRuntimeContract(runtime_profile="casual_lane_dash")

    def _runtime_loop(**kwargs):
        if runtime_loop_error is not None:
            raise runtime_loop_error
        return (kwargs["code"], SimpleNamespace(ran=True, js_errors=[]), 0, [])

    with ExitStack() as stack:
        stack.enter_context(patch.object(runner, "_build_create_spec", new=AsyncMock(return_value=spec)))
        stack.enter_context(patch.object(runner, "_select_runtime_profile", return_value="casual_lane_dash"))
        stack.enter_context(patch.object(runner, "_compose_runtime_contract", return_value=runtime_contract))
        stack.enter_context(patch.object(runner, "_build_gdd", new=AsyncMock(return_value=GDD())))
        stack.enter_context(patch.object(runner, "_remember_spec", new=AsyncMock()))
        stack.enter_context(patch.object(runner, "_remember_runtime_contract", new=AsyncMock()))
        stack.enter_context(patch.object(runner, "_remember_code", new=AsyncMock()))
        stack.enter_context(
            patch("src.engine.pipeline_v2_runner.task_memory.append_decision", new=AsyncMock())
        )
        stack.enter_context(patch.object(runner.pre_gen_validator, "validate", return_value=[]))
        mock_generate = stack.enter_context(
            patch.object(runner, "_generate_create_code", new=AsyncMock(side_effect=generate_side_effect))
        )
        stack.enter_context(
            patch.object(runner, "_run_contract_and_runtime_flow", new=AsyncMock(side_effect=flow_side_effect))
        )
        stack.enter_context(
            patch.object(
                runner.qa_pipeline,
                "check",
                return_value=SimpleNamespace(passed=True, errors=[], warnings=[]),
            )
        )
        stack.enter_context(patch.object(runner, "_should_run_code_review", return_value=True))
        stack.enter_context(
            patch.object(runner.code_reviewer, "review", new=AsyncMock(side_effect=review_side_effect))
        )
        stack.enter_context(
            patch.object(runner.quality_scorer, "compute", side_effect=compute_side_effect)
        )
        mock_patch_text = stack.enter_context(
            patch.object(
                runner,
                "_request_quality_gate_patch_text",
                new=AsyncMock(return_value=patch_text_return, side_effect=patch_text_side_effect),
            )
        )
        mock_runtime_loop = stack.enter_context(
            patch.object(runner, "_run_runtime_qa_loop", new=AsyncMock(side_effect=_runtime_loop))
        )
        stack.enter_context(patch.object(runner.code_generator.template_cache, "store"))
        stack.enter_context(patch.object(runner, "_serialize_runtime_qa", return_value={}))
        stack.enter_context(
            patch.object(settings, "QUALITY_GATE_PATCH_REPAIR_ENABLED", patch_repair_enabled)
        )

        response = asyncio.run(
            runner._run_create_impl(
                request,
                progress_cb=progress_cb,
                stage_context={"stage": "spec_build"},
            )
        )

    return response, SimpleNamespace(
        generate=mock_generate,
        patch_text=mock_patch_text,
        runtime_loop=mock_runtime_loop,
    )


# ---------------------------------------------------------------------------
# Trigger-condition unit tests
# ---------------------------------------------------------------------------


def test_should_attempt_quality_patch_repair_accepts_near_miss():
    assert V2PipelineRunner._should_attempt_quality_patch_repair(
        _spec(),
        _near_miss_review(),
        _quality(5.9),
    )


def test_should_attempt_quality_patch_repair_rejects_structural_defects():
    assert not V2PipelineRunner._should_attempt_quality_patch_repair(
        _spec(),
        _near_miss_review(is_complete_game=False),
        _quality(5.9),
    )
    assert not V2PipelineRunner._should_attempt_quality_patch_repair(
        _spec(),
        _near_miss_review(has_real_gameplay=False),
        _quality(5.9),
    )
    assert not V2PipelineRunner._should_attempt_quality_patch_repair(
        _spec(),
        LLMReviewResult(ran=False),
        _quality(5.9),
    )


def test_should_attempt_quality_patch_repair_rejects_large_gaps():
    # standard tier final_score threshold is 6.6; gap 2.6 > 1.5
    assert not V2PipelineRunner._should_attempt_quality_patch_repair(
        _spec(),
        _near_miss_review(),
        _quality(4.0),
    )
    # standard tier fun_score threshold is 6.8; gap 2.8 > 2.0
    assert not V2PipelineRunner._should_attempt_quality_patch_repair(
        _spec(),
        _near_miss_review(fun_score=4.0),
        _quality(5.9),
    )


def test_quality_patch_allowed_sections_maps_failing_dimensions():
    spec = _spec()

    fun_only = _near_miss_review(fun_score=6.0, visual_polish_score=7.2, character_quality_score=6.5)
    assert V2PipelineRunner._quality_patch_allowed_sections(spec, fun_only) == ("SCRIPT",)

    visual_only = _near_miss_review(fun_score=7.2, visual_polish_score=6.0, character_quality_score=6.5)
    assert V2PipelineRunner._quality_patch_allowed_sections(spec, visual_only) == ("STYLE", "SCRIPT")

    character_only = _near_miss_review(fun_score=7.2, visual_polish_score=7.2, character_quality_score=5.5)
    assert V2PipelineRunner._quality_patch_allowed_sections(spec, character_only) == ("STYLE", "SCRIPT")

    aggregate_only = _near_miss_review(fun_score=7.2, visual_polish_score=7.2, character_quality_score=6.5)
    assert V2PipelineRunner._quality_patch_allowed_sections(spec, aggregate_only) == ("STYLE", "SCRIPT")


# ---------------------------------------------------------------------------
# Create-pipeline integration tests
# ---------------------------------------------------------------------------


def test_near_miss_uses_patch_repair_and_skips_full_regeneration():
    generated = _generated(BASE_CODE, "provider-a")

    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(generated, [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
    )

    assert mocks.generate.await_count == 1
    assert mocks.patch_text.await_count == 1
    # SCRIPT section was patched, so runtime QA must be re-run.
    assert mocks.runtime_loop.await_count == 1
    prompt = mocks.patch_text.await_args.kwargs["prompt"]
    assert "UNCHANGED BODY STRUCTURE" in prompt
    assert "id='gameCanvas'" in prompt
    assert _spec().source_description in prompt
    assert "PATCHED_QUALITY_FIX" in response.html_code
    assert response.quality_score == 7.1


def test_patch_validation_failure_falls_back_to_full_regeneration():
    events = []
    generated_first = _generated(BASE_CODE, "provider-a")
    second_code = BASE_CODE.replace("background:#111", "background:#222")
    generated_second = _generated(second_code, "provider-b")

    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(generated_first, []), (generated_second, [])],
        flow_side_effect=[
            (_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, []),
            (_qa_success(second_code), SimpleNamespace(ran=True, js_errors=[]), 0, []),
        ],
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        # Full document without canvas/script fails validate_patch_candidate.
        patch_text_return="<!DOCTYPE html><html><body>tiny</body></html>",
        progress_cb=lambda *event: events.append(event),
    )
    rejection = next(event[3] for event in events if event[2] == "Targeted quality repair rejected")
    assert rejection["failureStage"] == "patch_parse"
    assert "invalid_json" in rejection["rejectionReason"]
    assert rejection["responseChars"] > 0

    assert mocks.patch_text.await_count == 1
    assert mocks.generate.await_count == 2
    second_call = mocks.generate.await_args_list[1].kwargs
    assert "invalid_json" in second_call["generation_guidance"]
    assert response.html_code == second_code


def test_structural_defect_skips_patch_and_goes_straight_to_regeneration():
    generated_first = _generated(BASE_CODE, "provider-a")
    second_code = BASE_CODE.replace("background:#111", "background:#333")
    generated_second = _generated(second_code, "provider-b")

    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(generated_first, []), (generated_second, [])],
        flow_side_effect=[
            (_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, []),
            (_qa_success(second_code), SimpleNamespace(ran=True, js_errors=[]), 0, []),
        ],
        review_side_effect=[_near_miss_review(is_complete_game=False), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
    )

    assert mocks.patch_text.await_count == 0
    assert mocks.runtime_loop.await_count == 0
    assert mocks.generate.await_count == 2
    assert response.html_code == second_code


def test_disabled_flag_keeps_existing_full_regeneration_behavior():
    generated_first = _generated(BASE_CODE, "provider-a")
    second_code = BASE_CODE.replace("background:#111", "background:#444")
    generated_second = _generated(second_code, "provider-b")

    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(generated_first, []), (generated_second, [])],
        flow_side_effect=[
            (_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, []),
            (_qa_success(second_code), SimpleNamespace(ran=True, js_errors=[]), 0, []),
        ],
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        patch_repair_enabled=False,
    )

    assert mocks.patch_text.await_count == 0
    assert mocks.runtime_loop.await_count == 0
    assert mocks.generate.await_count == 2
    second_call = mocks.generate.await_args_list[1].kwargs
    assert "QUALITY AND PRESENTATION CORRECTIONS" in second_call["generation_guidance"]
    assert response.html_code == second_code


def test_patch_request_overrides_full_document_system_output_contract():
    runner = V2PipelineRunner()
    client = AsyncMock(return_value='{"patches":[]}')
    with patch.object(runner.code_generator, '_build_system_prompt', return_value='Return ONLY one complete HTML document.'), patch.object(runner.code_generator._client, 'complete_with_truncation_retry', new=client):
        asyncio.run(runner._request_quality_gate_patch_text(prompt='repair', spec=_spec(), prompt_bundle_snapshot={}))
    system = client.await_args.kwargs['system']
    assert system.index('CURRENT REPAIR OUTPUT OVERRIDE') > system.index('Return ONLY one complete HTML')
    assert 'Return only a valid JSON object' in system
    assert 'All safety, gameplay, language and quality requirements' in system


def test_invalid_patch_reference_gets_one_correction_and_full_qa():
    bad = json.dumps({"patches": [{"section": "SCRIPT", "operation": "replace_exact",
        "search": "does not exist", "content": "unsafe fragment"}]})
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, "provider-a"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        patch_text_side_effect=[bad, PATCH_RESPONSE_TEXT],
    )
    assert mocks.patch_text.await_count == 2
    assert "PATCH APPLICATION REJECTED" in mocks.patch_text.await_args.kwargs["prompt"]
    assert "ORIGINAL" in mocks.patch_text.await_args.kwargs["prompt"]
    assert mocks.generate.await_count == 1
    assert mocks.runtime_loop.await_count == 1
    assert "PATCHED_QUALITY_FIX" in response.html_code


def test_explicit_desktop_requirements_override_touch_only_defaults():
    from src.engine.code_generator import CodeGenerator
    for brief in ["桌面小游戏，用鼠标移动和方向键", "Desktop game with mouse control"]:
        contract = CodeGenerator._build_requested_platform_contract(_spec().model_copy(update={"source_description": brief}))
        assert "not touchstart alone" in contract
        assert "without requiring a pressed button" in contract
    assert CodeGenerator._build_requested_platform_contract(_spec()) == ""


def test_invalid_patch_correction_is_bounded_and_falls_back():
    bad = json.dumps({"patches": [{"section": "SCRIPT", "operation": "replace_exact",
        "search": "does not exist", "content": "unsafe fragment"}]})
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, "provider-a"), []), (_generated(BASE_CODE, "provider-b"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])] * 2,
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        patch_text_side_effect=[bad, bad],
    )
    assert mocks.patch_text.await_count == 2
    assert mocks.generate.await_count == 2
    assert mocks.runtime_loop.await_count == 0


def test_repair_runtime_failure_reaches_regeneration_with_actual_reason():
    from src.engine.pipeline_errors import PipelineExecutionError
    failure = PipelineExecutionError("Generated code failed runtime QA: ReferenceError: ship is not defined",
        stage="runtime_simulation_qa", failure_family="runtime_qa",
        artifacts=[{"artifact_type": "runtime_qa_report", "payload": {"error": "ship is not defined"}}])
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, "provider-a"), []), (_generated(BASE_CODE, "provider-b"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])] * 2,
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)], runtime_loop_error=failure,
    )
    guidance = mocks.generate.await_args_list[1].kwargs["generation_guidance"]
    assert "ship is not defined" in guidance
    assert "runtime_simulation_qa" in guidance


def test_terminal_repair_failure_preserves_original_exception_and_artifacts():
    import pytest
    from src.engine.pipeline_errors import PipelineExecutionError
    failure = PipelineExecutionError("Generated code failed runtime QA: missing canvas",
        stage="runtime_simulation_qa", failure_family="runtime_qa",
        artifacts=[{"artifact_type": "failed_runtime_candidate", "payload": BASE_CODE}])
    with pytest.raises(PipelineExecutionError) as caught:
        _run_create_with_mocks(
            generate_side_effect=[(_generated(BASE_CODE, "provider-a"), [])] * 2,
            flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])] * 2,
            review_side_effect=[_near_miss_review()] * 2,
            compute_side_effect=[_quality(5.9)] * 2, runtime_loop_error=failure,
        )
    assert caught.value is failure
    assert caught.value.artifacts[0]["payload"] == BASE_CODE


def test_explicit_desktop_contract_survives_every_genre_profile():
    from src.engine.requested_platform import normalize_requested_platform
    original = _spec().model_copy(update={"source_description": "桌面游戏，鼠标移动或方向键控制小船，暂停按钮和失焦暂停"})
    spec = normalize_requested_platform(original)
    assert original.platform_constraints.input_mode == "touch_only"
    assert spec.platform_constraints.input_mode == "pointer_keyboard"
    runner = V2PipelineRunner()
    for profile in ["tap_challenge_combo", "casual_lane_dash", "puzzle_grid", "casual_arcade_orbit"]:
        contract = runner._compose_runtime_contract(base_contract=GameRuntimeContract(), spec=spec,
            runtime_profile=profile, entrypoint="create")
        assert contract.input.required_modes == ["pointer", "keyboard"]
        assert contract.input.gestures == ["click", "move"]
        assert "paused" in contract.state.required_states


def test_design_keeps_pause_distinct_from_terminal_and_honors_mouse_move():
    runner = V2PipelineRunner()
    spec = _spec().model_copy(update={"source_description": "桌面，鼠标移动和方向键，暂停"})
    contract = runner._compose_runtime_contract(base_contract=GameRuntimeContract(), spec=spec,
        runtime_profile="tap_challenge_combo", entrypoint="create")
    with patch.object(runner.game_designer, "design", new=AsyncMock(return_value=GDD())):
        gdd = asyncio.run(runner._build_gdd(spec, contract))
    assert gdd.state_machine["transitions"]["playing"] == "game_over"
    assert gdd.state_machine["transitions"]["paused"] == "playing"
    assert gdd.input_map["pointermove"] == "primary_move"
    assert "keydown" in gdd.input_map
