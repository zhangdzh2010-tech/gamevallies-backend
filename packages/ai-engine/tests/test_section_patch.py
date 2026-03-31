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
    marked = ensure_structured_section_markers(HTML)
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
