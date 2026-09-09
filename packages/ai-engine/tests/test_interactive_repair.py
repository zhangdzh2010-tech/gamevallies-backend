import json
import pytest
from src.engine.interactive_repair import apply_interactive_patch


SOURCE = '<html><body><h1>标题</h1><p>说明</p><button>重置</button></body></html>'


def test_exact_patch_changes_only_requested_fragment():
    result = apply_interactive_patch(SOURCE, json.dumps({'patches':[{'search':'<h1>标题</h1>','replace':'<h1>新标题</h1>'}]}))
    assert result == SOURCE.replace('标题','新标题')


@pytest.mark.parametrize('patches', [[], [{'search':'不存在','replace':'a'}],
    [{'search':SOURCE,'replace':'<html>replacement</html>'}],
    [{'search':'<p>说明</p>','replace':'<p>修改</p>'},{'search':'missing','replace':''}],
    [{'search':'<p>说明</p>','replace':'<p>说明</p>'}]])
def test_invalid_batches_fail_closed_without_partial_output(patches):
    with pytest.raises(ValueError):
        apply_interactive_patch(SOURCE,json.dumps({'patches':patches}))
