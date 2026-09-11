"""Tests for quality-gate targeted patch repair on the v2 create pipeline."""

import asyncio
import json
import os
import sys
import pytest
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
                "operation": "replace_exact",
                "search": "const canvas=",
                "content": "// PATCHED_QUALITY_FIX\nconst canvas=",
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
    contract_side_effect=None,
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
        mock_contract = stack.enter_context(patch.object(runner, "_validate_contract_bundle",
            return_value=[], side_effect=contract_side_effect))
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
        contract=mock_contract,
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


def _local_incomplete_review():
    return _near_miss_review(is_complete_game=False, fun_score=4,
        issues=['pause does not stop elapsed time'], evidence_verified=True,
        findings=[dict(issue='pause does not stop elapsed time', dimension='is_complete_game',
            code_excerpt='const canvas=', reason='pause leaves the loop active',
            correction='pause the existing elapsed clock', section='SCRIPT', repair_scope='local')])


def test_verified_local_incompleteness_can_be_repaired_despite_large_score_gap():
    review = _local_incomplete_review()
    assert V2PipelineRunner._should_attempt_quality_patch_repair(_spec(), review, _quality(3))
    assert V2PipelineRunner._quality_patch_allowed_sections(_spec(), review) == ('SCRIPT',)
    # Eligibility is not acceptance: the same candidate still fails all gates.
    assert V2PipelineRunner._quality_gate_errors(_spec(), review, _quality(3))


@pytest.mark.parametrize('field,value', [('repair_scope','redesign'), ('section','BODY')])
def test_evidence_for_redesign_or_unpatchable_body_does_not_use_local_repair(field, value):
    review = _local_incomplete_review()
    review.findings[0][field] = value
    assert not V2PipelineRunner._should_attempt_quality_patch_repair(_spec(), review, _quality(6.5))


def test_missing_playable_loop_still_requires_regeneration():
    review = _local_incomplete_review()
    review.has_real_gameplay = False
    assert not V2PipelineRunner._should_attempt_quality_patch_repair(_spec(), review, _quality(3))


def test_verified_pause_defect_enters_patch_branch_without_full_regeneration():
    review = _local_incomplete_review()
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, 'provider-a'), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
        review_side_effect=[review, _passing_review()],
        compute_side_effect=[_quality(3), _quality(7.1)])
    assert mocks.generate.await_count == 1
    assert mocks.patch_text.await_count == 1
    assert mocks.runtime_loop.await_count == 1
    assert mocks.contract.call_count == 2
    assert response.qa_passed
    prompt = mocks.patch_text.await_args.kwargs['prompt']
    assert 'pause the existing elapsed clock' in prompt
    assert 'layered backgrounds/foregrounds' not in prompt


@pytest.mark.parametrize('family', ['review_evidence', 'review_actionability', 'review_infrastructure'])
def test_invalid_assessment_does_not_trigger_full_regeneration(family):
    from src.engine.pipeline_errors import PipelineExecutionError
    generated = []
    def generate(*args, **kwargs):
        generated.append(True)
        return _generated(BASE_CODE, 'provider-a'), []
    failure = PipelineExecutionError('review evidence unavailable', stage='code_review', failure_family=family)
    with pytest.raises(PipelineExecutionError) as caught:
        _run_create_with_mocks(generate_side_effect=generate,
            flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
            review_side_effect=[failure], compute_side_effect=[])
    assert caught.value is failure
    assert len(generated) == 1


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


def test_patch_format_failure_gets_one_correction_then_full_regeneration():
    events = []
    generated_first = _generated(BASE_CODE, "provider-a")
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(generated_first, []), (_generated(BASE_CODE, "provider-b"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])] * 2,
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        patch_text_return="<!DOCTYPE html><html><body>tiny</body></html>",
        progress_cb=lambda *event: events.append(event),
    )
    rejection = next(event[3] for event in events if event[2] == "Targeted quality repair rejected")
    assert rejection["failureStage"] == "patch_correction_parse"
    assert "invalid_json" in rejection["rejectionReason"]
    assert rejection["responseChars"] > 0
    assert len([event for event in events if event[2]=='Correcting invalid patch references']) == 1
    assert mocks.generate.await_count == 2
    assert response.quality_score == 7.1


def test_large_script_cannot_be_smuggled_as_a_local_patch():
    import pytest
    from src.engine.section_patch import SectionPatch
    with pytest.raises(ValueError,match='replaces_too_much'):
        V2PipelineRunner._validate_quality_patch_extent(BASE_CODE,[
            SectionPatch(section='SCRIPT',operation='replace_exact',search=BASE_SCRIPT,content=PATCHED_SCRIPT)])


def test_local_protocol_rejects_whole_sections_and_excessive_batches():
    import pytest
    from src.engine.section_patch import parse_patch_response, build_patch_protocol
    whole=json.dumps({'patches':[{'section':'SCRIPT','operation':'replace_section','content':BASE_SCRIPT}]})
    with pytest.raises(ValueError,match='local_exact_patch_required'):
        parse_patch_response(whole,allowed_sections=['SCRIPT'],strict=True,exact_only=True)
    many=json.dumps({'patches':[{'section':'SCRIPT','operation':'replace_exact','search':'x','content':'y'}]*13})
    with pytest.raises(ValueError,match='too_many_local_patches'):
        parse_patch_response(many,allowed_sections=['SCRIPT'],strict=True,exact_only=True)
    prompt=build_patch_protocol(['SCRIPT'],task_label='repair',strict=True,exact_only=True)
    assert 'Only replace_exact is allowed' in prompt
    assert '"operation":"replace_section"' not in prompt


def test_structural_defect_skips_patch_and_goes_straight_to_regeneration():
    generated_first = _generated(BASE_CODE, "provider-a")
    second_code = BASE_CODE.replace("background:#111", "background:#333")
    generated_second = _generated(second_code, "provider-b")
    events = []

    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(generated_first, []), (generated_second, [])],
        flow_side_effect=[
            (_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, []),
            (_qa_success(second_code), SimpleNamespace(ran=True, js_errors=[]), 0, []),
        ],
        review_side_effect=[_near_miss_review(is_complete_game=False), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        progress_cb=lambda stage, pct, message, details: events.append((message, details)),
    )

    assert mocks.patch_text.await_count == 0
    assert mocks.runtime_loop.await_count == 0
    assert mocks.generate.await_count == 2
    assert response.html_code == second_code
    details = next(data for message, data in events if message == "Regenerating with gameplay and presentation quality guidance")
    assert details["failureFamily"] == "quality_gate"
    assert details["reviewRan"] is True
    assert details["isCompleteGame"] is False
    assert details["qualityGateErrors"]
    assert details["scores"]["final"] == 5.9
    assert "reviewIssues" in details


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
    assert client.await_args.kwargs['max_tokens'] == 4096
    assert client.await_args.kwargs['truncation_retry_max_tokens'] == 8192
    assert client.await_args.kwargs['response_size_hint'] == 'large_patch'


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
    assert "Only replace_exact is allowed" in mocks.patch_text.await_args.kwargs["prompt"]
    assert "Prefer surgical edits" not in mocks.patch_text.await_args.kwargs["prompt"]
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


def test_patch_syntax_failure_corrects_original_before_runtime_or_regeneration():
    bad = json.dumps({'patches':[{'section':'SCRIPT','operation':'replace_exact',
        'search':'const canvas=', 'content':'const BROKEN_CANDIDATE = function; const canvas='}]})
    syntax_error = SimpleNamespace(message='JavaScript syntax error: Unexpected token function')
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, 'provider-a'), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        patch_text_side_effect=[bad, PATCH_RESPONSE_TEXT],
        contract_side_effect=[[syntax_error], [], []],
    )
    assert mocks.patch_text.await_count == 2
    correction = mocks.patch_text.await_args.kwargs['prompt']
    assert 'Unexpected token function' in correction
    assert 'BROKEN_CANDIDATE' not in correction
    assert 'ORIGINAL' in correction
    assert mocks.generate.await_count == 1
    assert mocks.runtime_loop.await_count == 1
    assert mocks.contract.call_count == 3
    assert 'PATCHED_QUALITY_FIX' in response.html_code


def test_repeated_patch_syntax_failure_is_bounded_and_never_runs_bad_code():
    syntax_error = SimpleNamespace(message='JavaScript syntax error: Unexpected token function')
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, 'provider-a'), []),
                              (_generated(BASE_CODE, 'provider-b'), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])]*2,
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        contract_side_effect=[[syntax_error], [syntax_error]],
    )
    assert mocks.patch_text.await_count == 2
    assert mocks.runtime_loop.await_count == 0
    assert mocks.generate.await_count == 2
    assert response.html_code == BASE_CODE


def test_invalid_patch_correction_is_bounded_then_regenerates_from_source():
    bad = json.dumps({"patches": [{"section": "SCRIPT", "operation": "replace_exact",
        "search": "does not exist", "content": "unsafe fragment"}]})
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, "provider-a"), []), (_generated(BASE_CODE, "provider-b"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])] * 2,
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        patch_text_side_effect=[bad, bad],
    )
    assert mocks.generate.await_count == 2
    assert response.quality_score == 7.1


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


def test_second_repair_keeps_first_fix_and_uses_fresh_review():
    first_review = _near_miss_review(issues=["movement needs elapsed seconds"])
    second_review = _near_miss_review(fun_score=6.5, visual_polish_score=6.2,
        character_quality_score=6.2, issues=["pause must freeze the countdown"])
    second_patch = json.dumps({"patches": [{"section": "SCRIPT", "operation": "replace_exact",
        "search": "// PATCHED_QUALITY_FIX", "content": "// PATCHED_QUALITY_FIX\n// PAUSE_FIXED"}]})
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, "provider-a"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
        review_side_effect=[first_review, second_review, _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(6.4), _quality(7.1)],
        patch_text_side_effect=[PATCH_RESPONSE_TEXT, second_patch],
    )
    assert mocks.generate.await_count == 1
    assert mocks.patch_text.await_count == 2
    assert mocks.runtime_loop.await_count == 2
    assert mocks.contract.call_count == 4  # before and after each runtime validation
    prompt = mocks.patch_text.await_args.kwargs["prompt"]
    assert "// PATCHED_QUALITY_FIX" in prompt
    assert "pause must freeze the countdown" in prompt
    assert "movement needs elapsed seconds" not in prompt
    assert "// PAUSE_FIXED" in response.html_code


def test_regressing_patch_never_replaces_the_next_repair_base():
    rejected = json.dumps({"patches": [{"section": "SCRIPT", "operation": "replace_exact",
        "search":"const canvas=", "content": "// REGRESSED_CANDIDATE\nconst canvas="}]})
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, "provider-a"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
        review_side_effect=[_near_miss_review(), _near_miss_review(has_real_gameplay=False), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(5.0), _quality(7.1)],
        patch_text_side_effect=[rejected, PATCH_RESPONSE_TEXT],
    )
    assert "REGRESSED_CANDIDATE" not in mocks.patch_text.await_args.kwargs["prompt"]
    assert "discarded" in mocks.patch_text.await_args.kwargs["prompt"]
    assert "REGRESSED_CANDIDATE" not in response.html_code
    assert mocks.generate.await_count == 1


def test_exhausted_quality_repairs_preserve_candidate_without_full_regeneration():
    import pytest
    from src.engine.pipeline_errors import PipelineExecutionError
    with pytest.raises(PipelineExecutionError) as caught:
        _run_create_with_mocks(
            generate_side_effect=[(_generated(BASE_CODE, "provider-a"), [])],
            flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
            review_side_effect=[_near_miss_review()] * 3,
            compute_side_effect=[_quality(5.9)] * 3,
        )
    failure = caught.value
    assert failure.failure_family == "quality_repair_exhausted"
    assert "PATCHED_QUALITY_FIX" in failure.artifacts[0]["payload"]
    assert failure.artifacts[1]["payload"]["patchAttempts"] == 2
    assert failure.artifacts[1]["payload"]["fun_score"] == 6.0


def test_style_patch_also_runs_runtime_validation():
    style_patch = json.dumps({"patches": [{"section": "STYLE", "operation": "replace_exact",
        "search":"background:#111", "content": "background:linear-gradient(#111,#445)"}]})
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, "provider-a"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])],
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)], patch_text_return=style_patch,
    )
    assert mocks.runtime_loop.await_count == 1
    assert mocks.contract.call_count == 2
    assert "linear-gradient" in response.html_code


def test_contract_regression_after_runtime_repair_cannot_pass():
    from src.api.models import QACheckError
    response, mocks = _run_create_with_mocks(
        generate_side_effect=[(_generated(BASE_CODE, "provider-a"), []), (_generated(BASE_CODE, "provider-b"), [])],
        flow_side_effect=[(_qa_success(BASE_CODE), SimpleNamespace(ran=True, js_errors=[]), 0, [])] * 2,
        review_side_effect=[_near_miss_review(), _passing_review()],
        compute_side_effect=[_quality(5.9), _quality(7.1)],
        contract_side_effect=[[], [QACheckError(type="contract_input", message="keyboard handler removed", severity="error")]],
    )
    assert mocks.runtime_loop.await_count == 1
    assert mocks.generate.await_count == 2
    assert "keyboard handler removed" in mocks.generate.await_args.kwargs["generation_guidance"]
