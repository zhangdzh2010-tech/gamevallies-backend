import json
from types import SimpleNamespace
import pytest
from src.api.models import GameSpec, RunPipelineV2Request
from src.engine.artifact_quality import infer_artifact_kind, request_artifact_kind, assess_review, preservation_errors
from src.engine.generated_quality_policy import QUALITY_POLICY


def review(kind, **updates):
    metrics = QUALITY_POLICY['artifact_rubrics'][kind]['weights']
    result = dict(artifact_kind=kind, complete=True, critical_issues=[], issues=[],
        scores={key:8 for key in metrics}, evidence={key:'Verified requirement and implementation' for key in metrics})
    result.update(updates)
    return json.dumps(result)


@pytest.mark.parametrize('description,kind',[
    ('制作最小交互计数器，不添加游戏玩法。','tool'),
    ('制作单位换算工具','tool'),('做一个欧姆定律电路模型，不需要游戏','science'),
    ('做一个捕食者与猎物种群模型','science'),('做一个单摆实验','science'),
    ('做一个电路教学小游戏，需要闯关','game'),('Make a memory matching game','game'),
    ('作品类型：工具。计数器','tool'),('artifact_kind: science\noscillation','science'),
    ('作品类型：游戏。科学主题闯关','game'),('做一个小游戏\n请生成桌面浏览器中的可交互创意作品','game'),
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
    for raw in ['{}','not json',review(kind,complete='true'),review(kind,evidence={}),
        review(kind,scores={'fun_score':10}),review(kind,artifact_kind='game'),
        review(kind,critical_issues=['Core operation returns incorrect output'])]:
        assert not assess_review(raw,kind)['passed']
    metrics = {key:10 for key in QUALITY_POLICY['artifact_rubrics'][kind]['weights']}
    key = next(iter(metrics))
    metrics[key] = 0
    assert not assess_review(review(kind,scores=metrics),kind)['passed']
    for bad in [float('nan'),float('inf'),True,'10',11,-1]:
        metrics[key] = bad
        assert not assess_review(review(kind,scores=metrics),kind)['passed']


def test_cosmetic_iteration_cannot_change_model_scripts():
    source = '<html><body><footer>v1</footer><script>let a=1;</script></body></html>'
    assert not preservation_errors(source,source.replace('v1','v2'),'仅修改页脚文案')
    assert preservation_errors(source,source.replace('a=1','a=2'),'仅修改页脚文案')
    assert not preservation_errors(source,source.replace('a=1','a=2'),'将参数a改为2')
