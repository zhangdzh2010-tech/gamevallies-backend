import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from src.api.models import GDD, GameRuntimeContract, GameSpec, GenerateCodeResult, RunPipelineV2Request, QACheckError
from src.engine.pipeline_errors import PipelineExecutionError
from src.engine.pipeline_v2_runner import V2PipelineRunner
from src.engine.quality_scorer import LLMReviewResult


@pytest.mark.parametrize('stage', ['preflight', 'quality', 'contract'])
def test_failed_game_retains_source_and_diagnostics(stage):
    runner = V2PipelineRunner()
    request = RunPipelineV2Request(game_id='evidence',user_id='author',raw_user_input='益智游戏')
    spec = GameSpec(game_type='puzzle',generation_tier='standard')
    html = '<html><body><canvas></canvas><script>let ctx=null;</script></body></html>'
    generated = GenerateCodeResult(html_code=html,strategy='llm',generation_time_ms=1,code_size_bytes=len(html))
    issue = SimpleNamespace(code='nullable_runtime_object:ctx',message='ctx is not initialized')
    with ExitStack() as stack:
        for name,value in [('_build_create_spec',spec),('_build_gdd',GDD()),('_remember_spec',None),
                           ('_remember_runtime_contract',None),('_remember_code',None)]:
            stack.enter_context(patch.object(runner,name,new=AsyncMock(return_value=value)))
        for name,value in [('_select_runtime_profile','puzzle_grid'),('_compose_runtime_contract',GameRuntimeContract()),
                           ('_build_create_generation_attempt_plan',['standard']*3),('_should_run_code_review',True),
                           ('_quality_gate_errors',['required mechanic is broken']),('_should_attempt_quality_patch_repair',False),
                           ('_can_accept_showcase_near_miss',False)]:
            stack.enter_context(patch.object(runner,name,return_value=value))
        stack.enter_context(patch.object(runner.pre_gen_validator,'validate',return_value=[]))
        stack.enter_context(patch('src.engine.pipeline_v2_runner.task_memory.append_decision',new=AsyncMock()))
        stack.enter_context(patch('src.engine.pipeline_v2_runner.asyncio.sleep',new=AsyncMock()))
        generate = stack.enter_context(patch.object(runner,'_generate_create_code',new=AsyncMock(
            return_value=(generated,[issue] if stage=='preflight' else []))))
        stack.enter_context(patch.object(runner,'_run_contract_and_runtime_flow',new=AsyncMock(return_value=(
            SimpleNamespace(success=stage!='contract',code=html,retries=0,needs_regeneration=stage=='contract',
                last_errors=[QACheckError(type='contract_safety',message='forbidden storage access',severity='error')]),
            SimpleNamespace(ran=True),0,[]))))
        stack.enter_context(patch.object(runner.qa_pipeline,'check',return_value=SimpleNamespace(passed=True,errors=[],warnings=[])))
        stack.enter_context(patch.object(runner.code_reviewer,'review',new=AsyncMock(return_value=LLMReviewResult(ran=True))))
        stack.enter_context(patch.object(runner.quality_scorer,'compute',return_value=SimpleNamespace(final_score=1)))
        with pytest.raises(PipelineExecutionError) as caught:
            asyncio.run(runner._run_create_impl(request,None,{'stage':'spec_build'}))
    assert generate.call_count == 3
    artifacts = {a['artifact_type']:a for a in caught.value.artifacts}
    assert artifacts['failed_'+stage+'_candidate']['payload'] == html
    report_type = {'preflight':'preflight_report','quality':'quality_review_report','contract':'contract_qa_report'}[stage]
    assert artifacts[report_type]['payload']['attempt'] == 3
    if stage == 'contract':
        assert caught.value.failure_family == 'contract_qa'
        assert artifacts[report_type]['payload']['errors'][0]['message'] == 'forbidden storage access'
