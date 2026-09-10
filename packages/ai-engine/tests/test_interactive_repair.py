import json
import pytest
from src.engine.interactive_repair import apply_interactive_patch


SOURCE = '<html><body><h1>标题</h1><p>说明</p><button>重置</button></body></html>'


def test_revision_bound_edits_handle_duplicate_spans_without_fuzzy_matching():
    from src.engine.source_references import source_reference_catalog
    source = 'a' * 1800
    ref = list(source_reference_catalog(source))[1]
    raw = json.dumps({'patches':[{'source_ref':ref,'replace':'b'*600}]})
    assert apply_interactive_patch(source,raw) == 'a'*600+'b'*600+'a'*600
    with pytest.raises(ValueError,match='stale'):
        apply_interactive_patch(source+'changed',raw)


def test_indexed_edits_cannot_overlap_or_replace_whole_document():
    from src.engine.source_references import source_reference_catalog
    source = 'a'*1800
    refs = list(source_reference_catalog(source))
    for chosen in ([refs[0],refs[0]],refs):
        with pytest.raises(ValueError):
            apply_interactive_patch(source,json.dumps({'patches':[
                {'source_ref':ref,'replace':'b'} for ref in chosen]}))


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


@pytest.mark.parametrize('payload', [[], None, {'patches':[None]}, {'patches':[[]]},
    {'patches':[], 'html':SOURCE}, {'patches':[{'search':'标题','replace':'修改','operation':'delete'}]}])
def test_unrecognized_protocol_is_rejected(payload):
    with pytest.raises(ValueError):
        apply_interactive_patch(SOURCE, json.dumps(payload))


def test_all_edits_reference_original_offsets_even_if_replacements_contain_later_searches():
    patches = [{'search':'标题','replace':'说明标题'}, {'search':'说明','replace':'新的介绍'}]
    result = apply_interactive_patch(SOURCE, json.dumps({'patches':patches}))
    assert result == SOURCE.replace('说明', '新的介绍').replace('标题', '说明标题')


@pytest.mark.parametrize('patches', [
    [{'search':'<h1>标题</h1>','replace':'<h1>新</h1>'},{'search':'标题','replace':'新'}],
    [{'search':'标题','replace':'新标题'},{'search':'新标题','replace':'其他标题'}],
    [{'search':SOURCE[:30],'replace':'a'},{'search':SOURCE[30:60],'replace':'b'}],
    [{'search':'标题','replace':'x'*4097}],
])
def test_overlapping_dependent_and_disguised_full_rewrites_fail_atomically(patches):
    with pytest.raises(ValueError):
        apply_interactive_patch(SOURCE, json.dumps({'patches':patches}))
