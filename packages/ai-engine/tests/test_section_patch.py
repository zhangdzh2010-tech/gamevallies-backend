import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.section_patch import (
    PATCH_SCRIPT_ANCHOR_INPUT,
    PATCH_SECTION_BODY,
    PATCH_SECTION_SCRIPT,
    PATCH_SECTION_STYLE,
    SectionPatch,
    apply_section_patches,
    build_patch_protocol,
    build_section_context,
    ensure_structured_section_markers,
    extract_script_content,
    parse_patch_response,
    validate_patch_candidate,
)


HTML = (
    "<!DOCTYPE html><html><head><style>body{color:#fff;}</style></head>"
    "<body><div id='hud'>HUD</div><script>const score = 1;</script></body></html>"
)


def test_build_section_context_lists_selected_sections():
    context = build_section_context(HTML, (PATCH_SECTION_STYLE, PATCH_SECTION_SCRIPT))
    assert "=== SECTION:STYLE START ===" in context
    assert "body{color:#fff;}" in context
    assert "=== SECTION:SCRIPT START ===" in context
    assert "const score = 1;" in context


def test_parse_patch_response_accepts_json_payload():
    patches, full_html = parse_patch_response(
        '{"patches":[{"section":"SCRIPT","content":"const score = 3;"}]}',
        allowed_sections=(PATCH_SECTION_SCRIPT,),
    )
    assert full_html is None
    assert patches == [SectionPatch(section=PATCH_SECTION_SCRIPT, content="const score = 3;")]


def test_apply_section_patches_replaces_requested_sections_only():
    updated = apply_section_patches(
        HTML,
        [
            SectionPatch(section=PATCH_SECTION_SCRIPT, content="const score = 5;"),
            SectionPatch(section=PATCH_SECTION_BODY, content="<div id='hud'>NEW</div><script>const score = 5;</script>"),
        ],
    )
    assert "const score = 5;" in updated
    assert "<div id='hud'>NEW</div>" in updated
    assert "body{color:#fff;}" in updated


def test_parse_patch_response_treats_full_html_as_fallback_document():
    patches, full_html = parse_patch_response(
        "<!DOCTYPE html><html><body>ok</body></html>",
        allowed_sections=(PATCH_SECTION_SCRIPT, PATCH_SECTION_BODY),
    )
    assert patches is None
    assert full_html == "<!DOCTYPE html><html><body>ok</body></html>"


def test_build_patch_protocol_mentions_allowed_sections():
    protocol = build_patch_protocol((PATCH_SECTION_BODY, PATCH_SECTION_SCRIPT), task_label="iteration")
    assert "Allowed sections: BODY, SCRIPT" in protocol
    assert "Return JSON only." in protocol


def test_ensure_structured_section_markers_injects_expected_anchor_comments():
    marked = ensure_structured_section_markers(HTML)
    assert "<!-- SECTION:HTML_SHELL START -->" in marked
    assert "<!-- SECTION:STYLE START -->" in marked
    assert "<!-- SECTION:HUD START -->" in marked
    assert "/* SECTION:CONFIG START */" in marked
    assert "/* SECTION:INPUT START */" in marked
    assert "/* SECTION:GAME_LOOP START */" in marked
    assert "/* SECTION:LEVEL_DATA START */" in marked


def test_parse_patch_response_accepts_direct_script_anchor_target():
    patches, full_html = parse_patch_response(
        '{"patches":[{"section":"INPUT","operation":"replace_block","content":"bindInput();"}]}',
        allowed_sections=(PATCH_SECTION_SCRIPT,),
    )
    assert full_html is None
    assert patches == [
        SectionPatch(
            section=PATCH_SECTION_SCRIPT,
            content="bindInput();",
            operation="replace_block",
            anchor=PATCH_SCRIPT_ANCHOR_INPUT,
        )
    ]


def test_apply_section_patches_replaces_script_anchor_block_when_requested():
    marked = ensure_structured_section_markers(HTML.replace("const score = 1;", "const score = 1; /* SECTION:INPUT START */ bindInput(); /* SECTION:INPUT END */"))
    updated = apply_section_patches(
        marked,
        [
            SectionPatch(
                section=PATCH_SECTION_SCRIPT,
                content="canvas.addEventListener('pointerdown', startGame);",
                operation="replace_block",
                anchor=PATCH_SCRIPT_ANCHOR_INPUT,
            ),
        ],
    )
    assert "canvas.addEventListener('pointerdown', startGame);" in updated
    assert "const score = 1;" in updated


def test_synthetic_anchors_are_not_advertised_or_accepted_as_insertion_points():
    import pytest
    from src.engine.section_patch import list_safe_patch_anchors
    html = HTML.replace("const score = 1;", "(() => { let inputBound = false; function boot(){ inputBound = true; } boot(); })();")
    marked = ensure_structured_section_markers(html)
    assert list_safe_patch_anchors(marked, (PATCH_SECTION_SCRIPT,)) == []
    context = build_section_context(marked, (PATCH_SECTION_SCRIPT,))
    assert "No safe SCRIPT anchors exist" in context
    assert "=== ANCHOR:INPUT START ===" not in context
    with pytest.raises(ValueError, match="empty_script_anchor:INPUT"):
        apply_section_patches(marked, [SectionPatch(section="SCRIPT", operation="replace_block", anchor="INPUT", content="function bindInput(){ if(inputBound)return; }")])


def test_all_patch_protocols_preserve_closure_scope_and_empty_markers():
    for correction in (False, True):
        protocol = build_patch_protocol((PATCH_SECTION_SCRIPT,), task_label="repair", replace_sections_only=correction)
        assert "Preserve lexical scope" in protocol
        assert "NOT insertion points" in protocol


def test_complete_script_repair_cannot_move_helpers_into_synthetic_prefix():
    import pytest
    html = HTML.replace("const score = 1;", "(() => { let inputBound = false; function boot(){ inputBound = true; } boot(); })();")
    marked = ensure_structured_section_markers(html)
    script = extract_script_content(marked)
    unsafe = script.replace("/* SECTION:INPUT START */", "/* SECTION:INPUT START */\nfunction bindInput(){ if(inputBound)return; }")
    with pytest.raises(ValueError, match="synthetic_anchor_populated:INPUT"):
        apply_section_patches(marked, [SectionPatch(section="SCRIPT", content=unsafe)])
    candidate = marked.replace(script, unsafe)
    assert "synthetic_anchor_populated:INPUT" in validate_patch_candidate(marked, candidate, allowed_sections=(PATCH_SECTION_SCRIPT,))
    safe = script.replace("inputBound = true;", "inputBound = !inputBound;")
    assert "inputBound = !inputBound;" in apply_section_patches(marked, [SectionPatch(section="SCRIPT", content=safe)])


def test_validate_patch_candidate_rejects_missing_canvas_regression():
    previous = ensure_structured_section_markers(
        "<!DOCTYPE html><html><head><style>body{color:#fff;}</style></head><body><canvas id='gameCanvas'></canvas><script>const score = 1;</script></body></html>"
    )
    candidate = ensure_structured_section_markers(
        "<!DOCTYPE html><html><head><style>body{color:#fff;}</style></head><body><script>const score = 2;</script></body></html>"
    )

    errors = validate_patch_candidate(
        previous,
        candidate,
        allowed_sections=(PATCH_SECTION_BODY, PATCH_SECTION_SCRIPT),
    )

    assert "missing_canvas" in errors


def test_extract_script_content_prefers_main_game_script_over_helper_bridge():
    html = (
        "<!DOCTYPE html><html><body>"
        "<canvas id='gameCanvas'></canvas>"
        "<script>const canvas = document.getElementById('gameCanvas'); function boot(){ return canvas; }</script>"
        "<script>(() => { if (window.__playforgeMobileLayoutBridgeInstalled) return; window.__playforgeMobileLayoutBridgeInstalled = true; })();</script>"
        "</body></html>"
    )

    script = extract_script_content(html)

    assert "function boot()" in script
    assert "__playforgeMobileLayoutBridgeInstalled" not in script


def test_apply_section_patches_updates_main_script_and_keeps_helper_bridge():
    html = (
        "<!DOCTYPE html><html><body>"
        "<canvas id='gameCanvas'></canvas>"
        "<script>const canvas = document.getElementById('gameCanvas'); const score = 1;</script>"
        "<script>(() => { if (window.__playforgeMobileLayoutBridgeInstalled) return; window.__playforgeMobileLayoutBridgeInstalled = true; })();</script>"
        "</body></html>"
    )

    updated = apply_section_patches(
        html,
        [SectionPatch(section=PATCH_SECTION_SCRIPT, content="const canvas = document.getElementById('gameCanvas'); const score = 2;")],
    )

    assert updated.count("const score = 2;") == 1
    assert "const score = 1;" not in updated
    assert "__playforgeMobileLayoutBridgeInstalled" in updated


def test_validate_patch_candidate_rejects_multiple_html_documents():
    previous = ensure_structured_section_markers(
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const score = 1;</script></body></html>"
    )
    candidate = ensure_structured_section_markers(
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const score = 1;</script></body></html>"
        "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>const score = 2;</script></body></html>"
    )

    errors = validate_patch_candidate(
        previous,
        candidate,
        allowed_sections=(PATCH_SECTION_BODY, PATCH_SECTION_SCRIPT),
    )

    assert "multiple_html_documents" in errors


def test_json_patch_with_embedded_html_literal_is_not_a_full_document():
    import json
    script = "const label = '<html lang=\"zh\">';"
    patches, full_html = parse_patch_response(json.dumps({"patches":[{"section":"SCRIPT","content":script}]}), allowed_sections=(PATCH_SECTION_SCRIPT,))
    assert full_html is None
    assert patches == [SectionPatch(section=PATCH_SECTION_SCRIPT, content=script)]


def test_surgical_edits_preserve_unrelated_code_and_reject_ambiguous_search():
    import pytest
    code = HTML.replace('const score = 1;', 'const score = 1; const lives = 3;')
    patches, _ = parse_patch_response('{"patches":[{"section":"SCRIPT","operation":"replace_exact","search":"const score = 1;","content":"const score = 10;"}]}', allowed_sections=(PATCH_SECTION_SCRIPT,))
    candidate = apply_section_patches(code, patches)
    assert 'const score = 10; const lives = 3;' in candidate
    assert "id='hud'" in candidate
    with pytest.raises(ValueError, match='search_not_unique'):
        apply_section_patches(code, [SectionPatch(section='SCRIPT', operation='replace_exact', search='const ', content='let ')])


def test_exact_section_batches_use_original_offsets_and_reject_cascading_edits():
    import pytest
    code = HTML.replace('const score = 1;', 'const score = 1; const lives = 3;')
    edits = [SectionPatch(section='SCRIPT',operation='replace_exact',search='const score = 1;',
                content='const score = 2; const lives = 3;'),
             SectionPatch(section='SCRIPT',operation='replace_exact',search='const lives = 3;',content='const lives = 4;')]
    result = apply_section_patches(code,edits)
    assert 'const score = 2; const lives = 3; const lives = 4;' in result
    with pytest.raises(ValueError,match='search_not_found'):
        apply_section_patches(code,[edits[0],SectionPatch(section='SCRIPT',operation='replace_exact',
            search='const score = 2;',content='const score = 5;')])


def test_indexed_section_patch_resolves_the_current_section_revision():
    import json
    import pytest
    from src.engine.source_references import source_reference_catalog
    from src.engine.section_patch import extract_patchable_sections
    source = extract_patchable_sections(ensure_structured_section_markers(HTML))['SCRIPT']
    ref, span = next(iter(source_reference_catalog(source).items()))
    patches,_ = parse_patch_response(json.dumps({'patches':[{'section':'SCRIPT','operation':'replace_exact',
        'source_ref':ref,'content':span.replace('const score = 1;','const score = 2;')}]}),
        allowed_sections=['SCRIPT'],strict=True,exact_only=True)
    assert 'const score = 2;' in apply_section_patches(HTML,patches)
    with pytest.raises(ValueError,match='stale'):
        apply_section_patches(HTML.replace('const score = 1;','const score = 3;'),patches)


def test_unknown_operations_and_duplicate_full_sections_never_overwrite_script():
    import pytest
    with pytest.raises(ValueError, match='unsupported_operation'):
        apply_section_patches(HTML, [SectionPatch(section='SCRIPT', operation='replace_function', content='fragment')])
    with pytest.raises(ValueError, match='unsupported_anchor'):
        apply_section_patches(HTML, [SectionPatch(section='SCRIPT', operation='replace_block', anchor='invented', content='fragment')])
    with pytest.raises(ValueError, match='duplicate_section_replacement'):
        apply_section_patches(HTML, [SectionPatch(section='SCRIPT', content='first'), SectionPatch(section='SCRIPT', content='second')])


def test_strict_quality_contract_rejects_partial_batches_and_prose():
    import pytest
    from src.engine.section_patch import parse_patch_response
    for response in ["Here is the corrected script", "<html><script>bad()</script></html>",
        '{"patches":[{"section":"SCRIPT","operation":"replace_section","content":"valid"},{"section":"UNAUTHORIZED","content":"bad"}]}']:
        with pytest.raises(ValueError, match="patch_validation_failed:"):
            parse_patch_response(response, allowed_sections=["SCRIPT"], strict=True)


def test_strict_contract_preserves_literal_markdown_inside_javascript():
    import json
    from src.engine.section_patch import parse_patch_response
    script = 'const example = "```json";'
    patches, document = parse_patch_response(json.dumps({"patches": [{"section": "SCRIPT",
        "operation": "replace_section", "content": script}]}), allowed_sections=["SCRIPT"], strict=True)
    assert patches[0].content == script
    assert document is None


def test_correction_protocol_cannot_repeat_an_ambiguous_search():
    import json
    import pytest
    from src.engine.section_patch import build_patch_protocol, parse_patch_response
    prompt = build_patch_protocol(['SCRIPT'], task_label='correction', strict=True, replace_sections_only=True)
    assert 'replace_exact' not in prompt
    assert 'replace_block' not in prompt
    with pytest.raises(ValueError, match='complete_section_required'):
        parse_patch_response(json.dumps({'patches':[{'section':'SCRIPT','operation':'replace_exact','search':'ctx.restore();','content':'ctx.restore();drawGlow();'}]}), allowed_sections=['SCRIPT'], strict=True, replace_sections_only=True)
    patches, _ = parse_patch_response(json.dumps({'patches':[{'section':'SCRIPT','operation':'replace_section','content':'function draw(){ctx.restore();}'}]}), allowed_sections=['SCRIPT'], strict=True, replace_sections_only=True)
    assert len(patches) == 1
