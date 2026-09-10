import os
import sys
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.code_reviewer import CodeReviewer, _build_code_preview
from src.engine.pipeline_errors import PipelineExecutionError

PASSING = dict(is_complete_game=True, has_real_gameplay=True, difficulty_balanced=True,
               fun_score=8, visual_polish_score=8, character_quality_score=8, issues=[], findings=[])


def test_build_code_preview_keeps_short_code_unchanged():
    html = "<html><body>Hello</body></html>"
    assert _build_code_preview(html, limit=100) == html


def test_build_code_preview_keeps_middle_gameplay_for_long_code():
    html = "<html>" + ("A" * 5000) + ("B" * 5000) + "</html>"
    preview = _build_code_preview(html, limit=100)

    assert preview.startswith("<html>")
    assert preview.endswith("</html>")
    assert preview == html
    assert "middle omitted" not in preview


def test_review_uses_safe_prompt_format_for_literal_json_examples():
    reviewer = CodeReviewer()

    def fake_require_prompt(key: str) -> str:
        if key == "prompt.code_review_system":
            return "Return JSON only."
        if key == "prompt.code_review_template":
            return (
                "Return JSON with these fields exactly:\n"
                "{\n"
                '  "is_complete_game": false,\n'
                '  "has_real_gameplay": false,\n'
                '  "difficulty_balanced": false,\n'
                '  "fun_score": 5,\n'
                '  "visual_polish_score": 5,\n'
                '  "character_quality_score": 5,\n'
                '  "issues": []\n'
                "}\n"
                "Preview:\n{code_preview}"
            )
        raise AssertionError(key)

    with patch("src.engine.code_reviewer.require_prompt", side_effect=fake_require_prompt), patch.object(
        reviewer._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        reviewer._client,
        "complete_with_truncation_retry",
        new=AsyncMock(return_value=json.dumps(PASSING)),
    ):
        result = asyncio.run(reviewer.review("<html><body>ok</body></html>"))

    assert result.ran is True
    assert result.is_complete_game is True
    assert result.has_real_gameplay is True
    assert result.visual_polish_score == 8.0
    assert result.character_quality_score == 8.0
    assert result.evidence_verified


def test_review_receives_original_desktop_requirements_and_evidence_rules():
    reviewer = CodeReviewer()
    client = AsyncMock(return_value=json.dumps(PASSING))
    with patch('src.engine.code_reviewer.require_prompt', side_effect=lambda key: '{code_preview}' if key.endswith('template') else 'mobile reviewer'), patch.object(reviewer._client,'is_enabled',return_value=True), patch.object(reviewer._client,'complete_with_truncation_retry',new=client):
        asyncio.run(reviewer.review('<html>game</html>', user_requirements='桌面小游戏，鼠标和方向键操作，数字倒计时'))
    call = client.await_args.kwargs
    assert '桌面小游戏' in call['messages'][0]['content']
    assert 'Do not require touch gestures or haptics for desktop games' in call['system']
    assert 'concrete code-supported defect' in call['system']
    assert '<html>game</html>' in call['messages'][0]['content']


def test_scoring_rubric_separates_required_behavior_from_optional_polish():
    reviewer = CodeReviewer()
    client = AsyncMock(return_value=json.dumps(PASSING))
    with patch('src.engine.code_reviewer.require_prompt', side_effect=lambda key: '{code_preview}' if key.endswith('template') else 'premium mobile reviewer'), patch.object(reviewer._client, 'is_enabled', return_value=True), patch.object(reviewer._client, 'complete_with_truncation_retry', new=client):
        asyncio.run(reviewer.review('<html>game</html>', user_requirements='桌面小船接星星，失焦暂停'))
    system = client.await_args.kwargs['system']
    assert 'supersedes generic premium-mobile' in system
    assert 'elapsed-time movement' in system
    assert 'is_complete_game=false' in system
    assert 'Exclude optional enhancements from issues and score deductions' in system


SOURCE = '<html><body><script>function pause(){state="running";}</script></body></html>'
FINDING = dict(issue='pause keeps the simulation running', dimension='is_complete_game',
    code_excerpt='function pause(){state="running";}', reason='pause assigns the active state',
    correction='set state to paused', section='SCRIPT', repair_scope='local')


def run_review_responses(responses, source=SOURCE):
    reviewer = CodeReviewer()
    client = AsyncMock(side_effect=responses)
    with patch('src.engine.code_reviewer.require_prompt', side_effect=lambda key:
               '{code_preview}' if key.endswith('template') else 'review'), patch.object(
               reviewer._client, 'is_enabled', return_value=True), patch.object(
               reviewer._client, 'complete_with_truncation_retry', new=client):
        result = asyncio.run(reviewer.review(source, user_requirements='桌面游戏须支持暂停'))
    return result, client


def test_valid_local_defect_keeps_incomplete_flag_and_source_evidence():
    rejected = PASSING | dict(is_complete_game=False, issues=[FINDING['issue']], findings=[FINDING])
    result, client = run_review_responses([json.dumps(rejected)])
    assert result.evidence_verified and not result.is_complete_game
    assert result.findings == [FINDING]
    assert client.await_count == 1


def test_missing_citation_corrects_assessment_without_changing_artifact():
    unsupported = PASSING | dict(is_complete_game=False, issues=['click bound missing'])
    result, client = run_review_responses([json.dumps(unsupported), json.dumps(PASSING)])
    assert result.is_complete_game and result.issues == []
    assert client.await_count == 2
    correction = client.await_args.kwargs
    assert SOURCE in correction['messages'][0]['content']
    assert 'Remove contradicted claims' in correction['messages'][0]['content']
    assert correction['step_key'] == 'code_review'


def test_correction_can_confirm_a_real_defect_instead_of_inflating_scores():
    unsupported = PASSING | dict(is_complete_game=False, issues=[FINDING['issue']])
    corrected = unsupported | dict(findings=[FINDING])
    result, _ = run_review_responses([json.dumps(unsupported), json.dumps(corrected)])
    assert not result.is_complete_game
    assert result.findings == [FINDING]


@pytest.mark.parametrize('update', [
    {'code_excerpt':'nonexistent handler()'}, {'code_excerpt':'state'},
    {'repair_scope':'anything'}, {'section':'unknown'}, {'correction':''},
    {'dimension':False}, {'issue':'different issue'},
])
def test_invalid_evidence_is_bounded_and_preserves_candidate(update):
    source = SOURCE + '<!-- state -->'
    rejected = PASSING | dict(is_complete_game=False, issues=[FINDING['issue']], findings=[FINDING | update])
    with pytest.raises(PipelineExecutionError) as caught:
        run_review_responses([json.dumps(rejected)] * 2, source)
    assert caught.value.failure_family == 'review_evidence'
    assert caught.value.artifacts[0]['payload'] == source


@pytest.mark.parametrize('update', [
    {'is_complete_game':'false'}, {'has_real_gameplay':1}, {'fun_score':True},
    {'fun_score':float('nan')}, {'fun_score':float('inf')}, {'fun_score':11},
    {'visual_polish_score':None}, {'issues':'bad'}, {'issues':[{}]},
])
def test_invalid_scores_and_flags_are_never_coerced_to_passing(update):
    assert not CodeReviewer()._parse_review(json.dumps(PASSING | update)).ran


def test_unexplained_low_score_is_rejected_without_automatic_pass():
    unsupported = PASSING | dict(fun_score=4)
    with pytest.raises(PipelineExecutionError) as caught:
        run_review_responses([json.dumps(unsupported)] * 2)
    assert 'missing deduction evidence for fun_score' in str(caught.value)


@pytest.mark.parametrize('responses', [[RuntimeError('provider error')], ['invalid', RuntimeError('provider error')]])
def test_review_outage_is_not_an_optional_review_success(responses):
    with pytest.raises(PipelineExecutionError) as caught:
        run_review_responses(responses)
    assert caught.value.failure_family == 'review_infrastructure'
    assert caught.value.artifacts[0]['payload'] == SOURCE
