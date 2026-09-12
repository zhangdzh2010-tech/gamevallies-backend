import json
from types import SimpleNamespace
import pytest
from src.api.models import GameSpec, RunPipelineV2Request
from src.engine.artifact_quality import (
    infer_artifact_kind, request_artifact_kind, assess_review, preservation_errors,
    interactive_outcome_labels,
)
from src.engine.generated_quality_policy import QUALITY_POLICY
from src.engine.source_references import source_reference_catalog


def review(kind, **updates):
    metrics = QUALITY_POLICY['artifact_rubrics'][kind]['weights']
    result = dict(artifact_kind=kind, complete=True, critical_issues=[], issues=[],
        scores={key:8 for key in metrics}, evidence={key:'Verified requirement and implementation' for key in metrics})
    result.update(updates)
    return json.dumps(result)


@pytest.mark.parametrize('description,kind',[
    ('制作最小交互计数器，不添加游戏玩法。','tool'),
    ('制作中文五色配色工具。所有按钮有明确反馈，不做游戏计分，不添加倒计时。','tool'),
    ('做一个欧姆定律演示，不制作游戏玩法。','science'),
    ('制作计数器。创作领域：自由创意。请生成桌面浏览器中的可交互创意作品。涉及科学概念时展示模型。','tool'),
    ('制作待办清单。请生成桌面浏览器中的可交互创意作品。涉及科学概念时展示模型。','tool'),
    ('制作单位换算工具','tool'),('做一个欧姆定律电路模型，不需要游戏','science'),
    ('做一个捕食者与猎物种群模型','science'),('做一个单摆实验','science'),
    ('做一个电路教学小游戏，需要闯关','game'),('Make a memory matching game','game'),
    ('作品类型：工具。计数器','tool'),('artifact_kind: science\noscillation','science'),
    ('作品类型：游戏。科学主题闯关','game'),('做一个小游戏\n请生成桌面浏览器中的可交互创意作品','game'),
    ('波源干涉与波纹演示。呈现方式：动态演示','science'),
    ('日常换算器。呈现方式：交互实验','tool'),
    ('科学主题闯关。呈现方式：自由创意','game'),
    ('做一个牛顿第二定律演示，可调质量和力','science'),
    ('做一个光合作用实验','science'),
    ('Build an interactive physics lab for free fall','science'),
    ('制作一个生物酶活性随温度变化的实验','science'),
    ('做一个太空躲避手机竖屏小游戏','game'),
])
def test_classification(description, kind):
    assert infer_artifact_kind(description) == kind


def test_cosmetic_feedback_inherits_source_type_and_explicit_game_wins():
    request = RunPipelineV2Request(game_id='x',user_id='u',raw_user_input='仅改页脚',
        source_spec=GameSpec(game_type='interactive_experience',artifact_kind='science'))
    assert request_artifact_kind(request) == 'science'
    request.artifact_kind = 'game'
    assert request_artifact_kind(request) == 'game'


@pytest.mark.parametrize('kind',['tool','science'])
def test_distinct_weighted_scores_and_fail_closed(kind):
    assert assess_review(review(kind),kind)['score'] == 8
    assert assess_review(review(kind),kind)['passed']
    for raw in ['{}','not json',review(kind,complete='maybe'),review(kind,evidence={}),
        review(kind,scores={'fun_score':10}),review(kind,artifact_kind='game'),
        review(kind,critical_issues=['Core operation returns incorrect output'])]:
        assert not assess_review(raw,kind)['passed']
    metrics = {key:10 for key in QUALITY_POLICY['artifact_rubrics'][kind]['weights']}
    key = next(iter(metrics))
    metrics[key] = 0
    assert not assess_review(review(kind,scores=metrics),kind)['passed']
    for bad in [float('nan'),float('inf'),True,'high',11,-1]:
        metrics[key] = bad
        assert not assess_review(review(kind,scores=metrics),kind)['passed']


def test_cosmetic_iteration_cannot_change_model_scripts():
    source = '<html><body><footer>v1</footer><script>let a=1;</script></body></html>'
    assert not preservation_errors(source,source.replace('v1','v2'),'仅修改页脚文案')
    assert preservation_errors(source,source.replace('a=1','a=2'),'仅修改页脚文案')
    assert not preservation_errors(source,source.replace('a=1','a=2'),'将参数a改为2')


def test_layout_fix_does_not_freeze_all_scripts():
    source = '<script>resize(900); model(1);</script>'
    candidate = '<script>resize(320); model(1);</script>'
    assert not preservation_errors(source, candidate, '保留模型方程，调整标题区域和布局，修复Canvas尺寸')


def test_cosmetic_edit_restores_source_scripts():
    from src.engine.artifact_quality import preserve_cosmetic_scripts
    source = '<h1>旧名</h1><script>model(1);</script>'
    candidate = '<h1>新名</h1><script>model(2);</script>'
    result = preserve_cosmetic_scripts(source, candidate, '仅修改标题')
    assert result == '<h1>新名</h1><script>model(1);</script>'
    assert not preservation_errors(source, result, '仅修改标题')
    assert preserve_cosmetic_scripts(source, candidate, '修改模型参数') == candidate


def test_suggestions_cannot_trigger_repairs_or_change_pass_status():
    result = assess_review(review('tool', suggestions=['重置处于默认状态时可考虑添加提示']), 'tool')
    assert result['passed'] and result['issues'] == []
    assert result['suggestions']


def grounded_review(**updates):
    code = '<button onclick="value=2">加一</button>'
    brief = '点击加一按钮将当前值增加一。'
    issue = '加一操作赋值为2，重复点击不能递增。'
    finding = dict(issue=issue, dimension='complete', basis='requirement',
        requirement_quote=brief, source_ref=next(iter(source_reference_catalog(code))),
        reason='处理器使用常量赋值，第二次点击仍为2，违反递增要求。', correction='改为递增当前值。')
    finding.update(updates)
    return code, brief, review('tool', complete=False, critical_issues=[issue], findings=[finding])


def test_actual_requirement_defect_remains_blocking():
    code, brief, raw = grounded_review()
    result = assess_review(raw, 'tool', brief=brief, code=code)
    assert result['review_ran'] and not result['passed']
    assert result['issues'] == ['加一操作赋值为2，重复点击不能递增。', '未完整实现用户要求']


@pytest.mark.parametrize('updates', [
    {'requirement_quote':'必须保留全部历史记录'},
    {'source_ref':'0000000000000000:0'},
    {'basis':'rubric','rubric_dimension':'functional_correctness'},
    {'reason':''}, {'correction':''},
])
def test_unsupported_defect_requires_review_correction(updates):
    code, brief, raw = grounded_review(**updates)
    result = assess_review(raw, 'tool', brief=brief, code=code)
    assert not result['review_ran'] and not result['passed']


def test_source_reference_is_bound_to_exact_candidate():
    code, brief, raw = grounded_review()
    assert not assess_review(raw, 'tool', brief=brief, code=code+' ')['review_ran']


def test_low_score_requires_its_own_defect_evidence():
    weights = QUALITY_POLICY['artifact_rubrics']['tool']['weights']
    scores = {k:8 for k in weights}
    key = next(iter(weights))
    scores[key] = 6
    invalid = assess_review(review('tool', scores=scores), 'tool')
    assert not invalid['review_ran']
    code, brief, raw = grounded_review(dimension=key, basis='rubric', rubric_dimension=key)
    data = json.loads(raw)
    data.update(complete=True, scores=scores)
    result = assess_review(json.dumps(data), 'tool', brief=brief, code=code)
    assert result['review_ran'] and not result['passed']


def test_free_text_defect_is_not_an_actionable_repair_request():
    result = assess_review(review('tool', critical_issues=['应保留未要求的历史记录'],
        issues=['未观察到实际错误，可考虑优化']), 'tool')
    assert not result['review_ran']


def test_science_review_recovers_json_wrapped_in_prose():
    payload = review('science')
    raw = '审核如下：\n' + payload + '\n以上为完整评分。'
    result = assess_review(raw, 'science')
    assert result['review_ran'] and result['passed']
    assert result['score'] == 8


def test_tool_review_recovers_omitted_empty_lists_and_model_wrappers():
    data = json.loads(review('tool'))
    data.pop('issues')
    data.pop('critical_issues')
    data['complete'] = 'true'
    data['artifact_kind'] = '工具'
    data['scores'] = {key: '8' for key in data['scores']}
    wrapped = json.dumps({'assessment': data})
    result = assess_review(wrapped, 'tool')
    assert result['review_ran'] and result['passed']
    assert result['issues'] == []
    assert result['critical_issues'] == []
    assert result['score'] == 8


def test_science_review_recovers_object_shaped_issue_lists_without_inventing_scores():
    code, brief, raw = grounded_review()
    data = json.loads(raw)
    defect = data['critical_issues'][0]
    data['issues'] = [{'issue': defect, 'reason': 'wrapper'}]
    data['critical_issues'] = [{'text': defect}]
    result = assess_review(json.dumps(data), 'tool', brief=brief, code=code)
    assert result['review_ran'] and not result['passed']
    assert '加一操作赋值为2，重复点击不能递增。' in result['issues']


def test_missing_scores_are_never_invented_from_wrappers():
    data = json.loads(review('science'))
    data.pop('scores')
    result = assess_review(json.dumps({'review': data}), 'science')
    assert not result['review_ran'] and not result['passed']
    assert result['score'] == 0
    assert '分类审核未返回完整、有效且有依据的评分。' in result['issues']


def test_structural_field_errors_get_a_bounded_reask_without_code_rewrite():
    import asyncio
    from src.engine.review_recovery import recover_review

    valid = review('science')
    metrics = ['scientific_correctness', 'parameter_fidelity', 'explanation_integrity', 'visual_clarity']
    broken = json.dumps({
        'artifact_kind': 'science',
        'complete': True,
        'scores': {key: 8 for key in metrics},
        'evidence': {key: 'ok' for key in metrics},
        'issues': [{'bad': True}],
    })
    calls = []

    async def request(correction):
        calls.append(correction)
        return valid if correction else broken

    verified = asyncio.run(recover_review(
        request,
        lambda raw: assess_review(raw, 'science'),
        lambda parsed: [] if parsed['review_ran'] else parsed['issues'],
    ))
    assert verified.assessment['review_ran']
    assert verified.requests == 2
    assert calls[1] is not None
    assert 'Never invent or inflate scores' in calls[1]
    assert 'invalid list field: issues' in calls[1]


def test_science_in_span_source_ref_is_accepted():
    code, brief, raw = grounded_review()
    data = json.loads(raw)
    reference = data['findings'][0]['source_ref']
    revision = reference.split(':')[0]
    data['findings'][0]['source_ref'] = f'[{revision}:3]'
    result = assess_review(json.dumps(data), 'tool', brief=brief, code=code)
    assert result['review_ran']
    assert result['findings'][0]['source_ref'] == reference


def test_science_source_bound_finding_gap_gets_a_bounded_reask_without_inventing_scores():
    import asyncio
    from src.engine.review_recovery import recover_review

    code, brief, valid_raw = grounded_review()
    valid = json.loads(valid_raw)
    valid['scores'] = {key: 8 for key in QUALITY_POLICY['artifact_rubrics']['tool']['weights']}
    valid['evidence'] = {key: 'ok' for key in valid['scores']}
    broken = json.dumps({
        'artifact_kind': 'tool',
        'complete': True,
        'scores': valid['scores'],
        'evidence': valid['evidence'],
        'critical_issues': ['θ/L labels overlap and hide the period readout'],
        'issues': ['θ/L labels overlap and hide the period readout'],
        'findings': [],
    })
    calls = []

    async def request(correction):
        calls.append(correction)
        return json.dumps(valid) if correction else broken

    verified = asyncio.run(recover_review(
        request,
        lambda raw: assess_review(raw, 'tool', brief=brief, code=code),
        lambda parsed: [] if parsed['review_ran'] else parsed['issues'],
    ))
    assert verified.assessment['review_ran']
    assert verified.requests == 2
    assert calls[1] is not None
    assert 'defect lacks a source-bound finding' in calls[1]
    assert 'Never invent or inflate scores' in calls[1]
    assert 'source_ref' in calls[1]


def test_incomplete_science_scores_get_a_bounded_reask_without_inventing_values():
    import asyncio
    from src.engine.review_recovery import recover_review

    valid = review('science')
    data = json.loads(valid)
    data['scores'] = {key: 8 for key in data['scores'] if key != 'visual_clarity'}
    broken = json.dumps(data)
    calls = []

    async def request(correction):
        calls.append(correction)
        return valid if correction else broken

    verified = asyncio.run(recover_review(
        request,
        lambda raw: assess_review(raw, 'science'),
        lambda parsed: [] if parsed['review_ran'] else parsed['issues'],
    ))
    assert verified.assessment['review_ran']
    assert verified.requests == 2
    assert 'Never invent or inflate a missing score' in calls[1]
    assert 'visual_clarity' in calls[1]


def test_invalid_schema_gets_a_bounded_reask_without_inventing_scores():
    import asyncio
    from src.engine.review_recovery import recover_review

    valid = review('science')
    calls = []

    async def request(correction):
        calls.append(correction)
        return valid if correction else 'not-json'

    verified = asyncio.run(recover_review(
        request,
        lambda raw: assess_review(raw, 'science'),
        lambda parsed: [] if parsed['review_ran'] else parsed['issues'],
    ))
    assert verified.assessment['review_ran']
    assert verified.requests == 2
    assert calls[1] is not None
    assert 'Never invent or inflate scores' in calls[1]
    assert 'Do not change the artifact' in calls[1]


def test_interactive_outcome_labels_mark_tool_and_science_seed_worthy():
    assessment = assess_review(review('science'), 'science')
    labels = interactive_outcome_labels(assessment, {'passed': True})
    assert labels['pipeline_success'] is True
    assert labels['seed_worthy'] is True
    assert labels['seed_worthy_reason'] == 'structured_review_passed'
    missing = interactive_outcome_labels(
        {'review_ran': False, 'passed': False}, {'passed': True})
    assert missing['pipeline_success'] is False
    assert missing['seed_worthy'] is False
    assert missing['seed_worthy_reason'] == 'structured_review_missing'
