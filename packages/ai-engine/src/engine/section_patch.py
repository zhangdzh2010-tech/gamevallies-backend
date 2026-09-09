from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

PATCH_SECTION_STYLE = "STYLE"
PATCH_SECTION_BODY = "BODY"
PATCH_SECTION_SCRIPT = "SCRIPT"
PATCH_SECTION_HTML_SHELL = "HTML_SHELL"
PATCH_SECTION_HUD = "HUD"
PATCH_SCRIPT_ANCHOR_CONFIG = "CONFIG"
PATCH_SCRIPT_ANCHOR_INPUT = "INPUT"
PATCH_SCRIPT_ANCHOR_GAME_LOOP = "GAME_LOOP"
PATCH_SCRIPT_ANCHOR_LEVEL_DATA = "LEVEL_DATA"
PATCHABLE_SECTION_ORDER: tuple[str, ...] = (
    PATCH_SECTION_STYLE,
    PATCH_SECTION_BODY,
    PATCH_SECTION_SCRIPT,
)
PATCHABLE_HTML_ANCHORS: tuple[str, ...] = (
    PATCH_SECTION_HTML_SHELL,
    PATCH_SECTION_HUD,
)
PATCHABLE_SCRIPT_ANCHORS: tuple[str, ...] = (
    PATCH_SCRIPT_ANCHOR_CONFIG,
    PATCH_SCRIPT_ANCHOR_INPUT,
    PATCH_SCRIPT_ANCHOR_GAME_LOOP,
    PATCH_SCRIPT_ANCHOR_LEVEL_DATA,
)
SCRIPT_HELPER_MARKERS: tuple[str, ...] = (
    "__playforgeInputBridgeInstalled",
    "__playforgeScoreBridgeInstalled",
    "__playforgeMobileLayoutBridgeInstalled",
    "__playforgeResolveTouchPointInstalled",
    "__playforgeTerminalFallback",
)

_STYLE_BLOCK_RE = re.compile(
    r"(<style\b[^>]*>)([\s\S]*?)(</style>)",
    re.IGNORECASE,
)
_BODY_BLOCK_RE = re.compile(
    r"(<body\b[^>]*>)([\s\S]*?)(</body>)",
    re.IGNORECASE,
)
_SCRIPT_BLOCK_RE = re.compile(
    r"(<script\b[^>]*>)([\s\S]*?)(</script>)",
    re.IGNORECASE,
)
_GAME_SCRIPT_SIGNAL_RE = re.compile(
    r"requestAnimationFrame|getContext\s*\(|document\.getElementById\s*\(\s*['\"]gameCanvas['\"]|querySelector\s*\(\s*['\"]canvas['\"]|const\s+canvas\s*=|let\s+gameState\b|function\s+boot\b|function\s+startGame\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SectionPatch:
    section: str
    content: str
    operation: str = "replace_section"
    anchor: Optional[str] = None
    search: Optional[str] = None


def _html_marker_start(name: str) -> str:
    return f"<!-- SECTION:{name} START -->"


def _html_marker_end(name: str) -> str:
    return f"<!-- SECTION:{name} END -->"


def _script_marker_start(name: str) -> str:
    return f"/* SECTION:{name} START */"


def _script_marker_end(name: str) -> str:
    return f"/* SECTION:{name} END */"


def extract_style_content(html: str) -> Optional[str]:
    match = _STYLE_BLOCK_RE.search(html or "")
    if not match:
        return None
    content = match.group(2)
    return content if content.strip() else None


def extract_body_content(html: str) -> Optional[str]:
    match = _BODY_BLOCK_RE.search(html or "")
    if not match:
        return None
    content = match.group(2)
    return content if content.strip() else ""


def _script_contains_helper_marker(content: str) -> bool:
    normalized = (content or "").casefold()
    return any(marker.casefold() in normalized for marker in SCRIPT_HELPER_MARKERS)


def _select_primary_script_match(html: str):
    matches = list(_SCRIPT_BLOCK_RE.finditer(html or ""))
    if not matches:
        return None

    def score(match: re.Match[str]) -> tuple[int, int, int, int, int]:
        content = match.group(2) or ""
        normalized = content.casefold()
        has_helper_marker = _script_contains_helper_marker(content)
        has_anchor_markers = any(
            _script_marker_start(anchor).casefold() in normalized
            or _script_marker_end(anchor).casefold() in normalized
            for anchor in PATCHABLE_SCRIPT_ANCHORS
        )
        has_game_signal = bool(_GAME_SCRIPT_SIGNAL_RE.search(content))
        return (
            0 if has_helper_marker else 1,
            1 if has_anchor_markers else 0,
            1 if has_game_signal else 0,
            len(content),
            match.start(),
        )

    return max(matches, key=score)


def extract_script_content(html: str) -> Optional[str]:
    match = _select_primary_script_match(html)
    if not match:
        return None
    content = match.group(2)
    return content if content.strip() else None


def replace_style_content(html: str, new_style: str) -> str:
    match = _STYLE_BLOCK_RE.search(html or "")
    if not match:
        return html
    return html[:match.start(2)] + new_style + html[match.end(2):]


def replace_body_content(html: str, new_body: str) -> str:
    match = _BODY_BLOCK_RE.search(html or "")
    if not match:
        return html
    return html[:match.start(2)] + new_body + html[match.end(2):]


def replace_script_content(html: str, new_script: str) -> str:
    match = _select_primary_script_match(html)
    if not match:
        return html
    return html[:match.start(2)] + new_script + html[match.end(2):]


def _ensure_html_shell_markers(html: str) -> str:
    updated = html
    start_marker = _html_marker_start(PATCH_SECTION_HTML_SHELL)
    end_marker = _html_marker_end(PATCH_SECTION_HTML_SHELL)

    if start_marker not in updated:
        head_match = re.search(r"<head\b[^>]*>", updated, re.IGNORECASE)
        if head_match:
            updated = updated[:head_match.start()] + start_marker + "\n" + updated[head_match.start():]
    if end_marker not in updated:
        head_end_match = re.search(r"</head>", updated, re.IGNORECASE)
        if head_end_match:
            updated = updated[:head_end_match.end()] + "\n" + end_marker + updated[head_end_match.end():]
    return updated


def _ensure_style_markers(html: str) -> str:
    updated = html
    start_marker = _html_marker_start(PATCH_SECTION_STYLE)
    end_marker = _html_marker_end(PATCH_SECTION_STYLE)
    match = _STYLE_BLOCK_RE.search(updated)
    if not match:
        return updated
    if start_marker not in updated:
        updated = updated[:match.start()] + start_marker + "\n" + updated[match.start():]
        match = _STYLE_BLOCK_RE.search(updated)
        if not match:
            return updated
    if end_marker not in updated:
        updated = updated[:match.end()] + "\n" + end_marker + updated[match.end():]
    return updated


def _ensure_hud_markers(html: str) -> str:
    updated = html
    start_marker = _html_marker_start(PATCH_SECTION_HUD)
    end_marker = _html_marker_end(PATCH_SECTION_HUD)
    if start_marker in updated and end_marker in updated:
        return updated
    body_match = re.search(r"<body\b[^>]*>", updated, re.IGNORECASE)
    if not body_match:
        return updated
    placeholder = f"\n{start_marker}\n{end_marker}\n"
    return updated[:body_match.end()] + placeholder + updated[body_match.end():]


def _ensure_script_anchor_markers(html: str) -> str:
    script = extract_script_content(html)
    if script is None:
        return html

    updated_script = script
    placeholder_blocks: List[str] = []
    for anchor in PATCHABLE_SCRIPT_ANCHORS:
        start_marker = _script_marker_start(anchor)
        end_marker = _script_marker_end(anchor)
        has_start = start_marker in updated_script
        has_end = end_marker in updated_script
        if has_start and has_end:
            continue
        if has_start and not has_end:
            updated_script = updated_script.replace(start_marker, f"{start_marker}\n{end_marker}", 1)
            continue
        if has_end and not has_start:
            updated_script = updated_script.replace(end_marker, f"{start_marker}\n{end_marker}", 1)
            continue
        placeholder_blocks.append(f"{start_marker}\n{end_marker}")

    if placeholder_blocks:
        prefix = "\n\n".join(placeholder_blocks).strip()
        if updated_script.strip():
            updated_script = prefix + "\n\n" + updated_script.lstrip("\n")
        else:
            updated_script = prefix
    return replace_script_content(html, updated_script)


def ensure_structured_section_markers(html: str) -> str:
    updated = html or ""
    if not updated.strip():
        return updated
    updated = _ensure_html_shell_markers(updated)
    updated = _ensure_style_markers(updated)
    updated = _ensure_hud_markers(updated)
    updated = _ensure_script_anchor_markers(updated)
    return updated


def extract_patchable_sections(html: str) -> Dict[str, Optional[str]]:
    return {
        PATCH_SECTION_STYLE: extract_style_content(html),
        PATCH_SECTION_BODY: extract_body_content(html),
        PATCH_SECTION_SCRIPT: extract_script_content(html),
    }


def _extract_html_anchor_content(html: str, anchor: str) -> Optional[str]:
    pattern = re.compile(
        re.escape(_html_marker_start(anchor)) + r"([\s\S]*?)" + re.escape(_html_marker_end(anchor)),
        re.IGNORECASE,
    )
    match = pattern.search(html or "")
    if not match:
        return None
    return match.group(1).strip("\n")


def extract_script_anchor_content(html: str, anchor: str) -> Optional[str]:
    script = extract_script_content(html)
    if script is None:
        return None
    pattern = re.compile(
        re.escape(_script_marker_start(anchor)) + r"([\s\S]*?)" + re.escape(_script_marker_end(anchor)),
        re.IGNORECASE,
    )
    match = pattern.search(script)
    if not match:
        return None
    return match.group(1).strip("\n")


def list_safe_patch_anchors(html: str, allowed_sections: Sequence[str]) -> List[str]:
    anchors: List[str] = []
    if PATCH_SECTION_BODY in allowed_sections:
        if _html_marker_start(PATCH_SECTION_HUD) in (html or "") and _html_marker_end(PATCH_SECTION_HUD) in (html or ""):
            anchors.append(PATCH_SECTION_HUD)
    if PATCH_SECTION_SCRIPT in allowed_sections:
        for anchor in PATCHABLE_SCRIPT_ANCHORS:
            if extract_script_anchor_content(html, anchor) is not None:
                anchors.append(anchor)
    return anchors


def find_incomplete_structured_markers(html: str) -> List[str]:
    code = html or ""
    incomplete: List[str] = []
    for name in (PATCH_SECTION_HTML_SHELL, PATCH_SECTION_STYLE, PATCH_SECTION_HUD):
        has_start = _html_marker_start(name) in code
        has_end = _html_marker_end(name) in code
        if has_start != has_end:
            incomplete.append(name)
    script = extract_script_content(code)
    if script is not None:
        for name in PATCHABLE_SCRIPT_ANCHORS:
            has_start = _script_marker_start(name) in script
            has_end = _script_marker_end(name) in script
            if has_start != has_end:
                incomplete.append(name)
    return incomplete


def has_structured_section_markers(html: str) -> bool:
    code = html or ""
    for name in (PATCH_SECTION_HTML_SHELL, PATCH_SECTION_STYLE, PATCH_SECTION_HUD):
        if _html_marker_start(name) in code or _html_marker_end(name) in code:
            return True
    script = extract_script_content(code)
    if script is None:
        return False
    return any(
        _script_marker_start(name) in script or _script_marker_end(name) in script
        for name in PATCHABLE_SCRIPT_ANCHORS
    )


def validate_patch_candidate(
    previous_html: str,
    candidate_html: str,
    *,
    allowed_sections: Sequence[str],
) -> List[str]:
    previous = ensure_structured_section_markers(previous_html).strip()
    candidate = ensure_structured_section_markers(candidate_html).strip()
    errors: List[str] = []
    if not candidate:
        return ["empty_candidate"]

    incomplete_markers = find_incomplete_structured_markers(candidate)
    if incomplete_markers:
        errors.append(
            "incomplete_markers:" + ",".join(sorted(dict.fromkeys(incomplete_markers)))
        )

    previous_lower = previous.lower()
    candidate_lower = candidate.lower()
    for required_tag in ("<html", "<body", "</body>", "</html>"):
        if required_tag in previous_lower and required_tag not in candidate_lower:
            errors.append(f"missing_tag:{required_tag}")
    if "<head" in previous_lower and "<head" not in candidate_lower:
        errors.append("missing_tag:<head")

    if (
        len(re.findall(r"<!DOCTYPE\s+html", candidate, re.IGNORECASE)) > 1
        or len(re.findall(r"<html\b", candidate, re.IGNORECASE)) > 1
        or len(re.findall(r"<body\b", candidate, re.IGNORECASE)) > 1
    ):
        errors.append("multiple_html_documents")

    previous_has_script = extract_script_content(previous) is not None
    candidate_has_script = extract_script_content(candidate) is not None
    if previous_has_script and not candidate_has_script:
        errors.append("missing_script")

    previous_has_canvas = bool(re.search(r"<canvas\b", previous, re.IGNORECASE))
    candidate_has_canvas = bool(re.search(r"<canvas\b", candidate, re.IGNORECASE))
    if previous_has_canvas and not candidate_has_canvas:
        errors.append("missing_canvas")

    previous_style = extract_style_content(previous)
    candidate_style = extract_style_content(candidate)
    if PATCH_SECTION_STYLE not in allowed_sections and previous_style != candidate_style:
        errors.append("unexpected_style_change")

    previous_body = extract_body_content(previous)
    candidate_body = extract_body_content(candidate)
    if (
        PATCH_SECTION_BODY not in allowed_sections
        and PATCH_SECTION_SCRIPT not in allowed_sections
        and previous_body != candidate_body
    ):
        errors.append("unexpected_body_change")

    previous_script = extract_script_content(previous)
    candidate_script = extract_script_content(candidate)
    if PATCH_SECTION_SCRIPT not in allowed_sections and previous_script != candidate_script:
        errors.append("unexpected_script_change")

    previous_len = len(previous)
    candidate_len = len(candidate)
    if previous_len >= 512:
        minimum_candidate_length = max(512, int(previous_len * 0.55))
    else:
        minimum_candidate_length = max(96, int(previous_len * 0.55))
    if candidate_len < minimum_candidate_length:
        errors.append("suspicious_shrink")

    return errors


def replace_html_anchor_content(html: str, anchor: str, new_content: str) -> str:
    pattern = re.compile(
        "(" + re.escape(_html_marker_start(anchor)) + r")([\s\S]*?)(" + re.escape(_html_marker_end(anchor)) + ")",
        re.IGNORECASE,
    )
    match = pattern.search(html or "")
    if not match:
        return html
    return html[:match.start(2)] + ("\n" + new_content.strip("\n") + "\n") + html[match.end(2):]


def replace_script_anchor_content(html: str, anchor: str, new_content: str) -> str:
    script = extract_script_content(html)
    if script is None:
        return html
    pattern = re.compile(
        "(" + re.escape(_script_marker_start(anchor)) + r")([\s\S]*?)(" + re.escape(_script_marker_end(anchor)) + ")",
        re.IGNORECASE,
    )
    match = pattern.search(script)
    if not match:
        return html
    updated_script = script[:match.start(2)] + ("\n" + new_content.strip("\n") + "\n") + script[match.end(2):]
    return replace_script_content(html, updated_script)


def build_patch_protocol(
    allowed_sections: Sequence[str],
    *,
    task_label: str,
    preferred_targets: Optional[Sequence[str]] = None,
    strict: bool = False,
    replace_sections_only: bool = False,
) -> str:
    section_list = ", ".join(allowed_sections)
    normalized_preferred_targets: List[str] = []
    for target in preferred_targets or ():
        value = str(target or "").strip().upper()
        if not value or value in normalized_preferred_targets:
            continue
        normalized_preferred_targets.append(value)
    lines = [
        f"PATCH-FIRST {task_label.upper()} OUTPUT CONTRACT (NON-NEGOTIABLE):",
        "- Return JSON only.",
        '- Use the shape: {"patches":[{"section":"SCRIPT","operation":"replace_section","content":"..."}]}',
        f"- Allowed sections: {section_list}",
        "- Omit unchanged sections.",
        '- Prefer surgical edits: {"section":"SCRIPT","operation":"replace_exact","search":"unique exact existing code","content":"replacement code"}.',
        "- replace_exact search must match exactly once in that section. Multiple non-overlapping surgical edits are allowed.",
        "- replace_section must contain the COMPLETE section, not one function or a fragment. Use at most one replace_section per section.",
        "- Never use unsupported operations or invent anchors. Preserve all unrelated code and DOM references.",
        "- STYLE content must be raw CSS inside the existing <style> block, without <style> tags.",
        "- BODY content must be raw HTML inside the existing <body> block, without <body> tags.",
        "- SCRIPT content must be raw JavaScript inside the main inline <script> block, without <script> tags.",
    ]
    if replace_sections_only:
        # A rejected exact search cannot be repaired by reusing a protocol that
        # still prefers exact searches. The correction has one operation only.
        lines = [line for line in lines if "replace_exact" not in line]
        lines.extend([
            "- The ONLY allowed operation is replace_section. Return at most one complete replacement per changed section.",
            "- Include every existing initialization, input handler and rendering function; modify only the necessary statements within that full source.",
            "- No anchors, search fields, fragments or full HTML documents. Return JSON only.",
        ])
        return "\n".join(lines)
    if normalized_preferred_targets:
        lines.append(f"- Preferred patch targets: {', '.join(normalized_preferred_targets)}.")
    if PATCH_SECTION_SCRIPT in allowed_sections:
        anchor_list = ", ".join(PATCHABLE_SCRIPT_ANCHORS)
        lines.append(f"- Safe SCRIPT anchors for replace_block: {anchor_list}.")
        lines.append('- You may also target a script anchor directly with {"section":"INPUT","operation":"replace_block","content":"..."} style patches.')
    if PATCH_SECTION_BODY in allowed_sections:
        lines.append(f"- Safe BODY anchor for replace_block: {PATCH_SECTION_HUD}.")
    lines.extend(
        [
            "- Prefer replace_section operations when you need to rewrite a whole section.",
            "- Use replace_block only when one safe anchor is enough.",
            ("- Never return a full HTML document; only the declared JSON patch format is accepted."
             if strict else "- Do NOT return a full HTML document unless patching is impossible."),
        ]
    )
    return "\n".join(lines)


def build_section_context(
    html: str,
    allowed_sections: Sequence[str],
    preferred_targets: Optional[Sequence[str]] = None,
) -> str:
    normalized_html = ensure_structured_section_markers(html)
    sections = extract_patchable_sections(normalized_html)
    blocks: List[str] = ["CURRENT PATCHABLE SECTIONS:"]
    normalized_preferred_targets: List[str] = []
    for target in preferred_targets or ():
        value = str(target or "").strip().upper()
        if not value or value in normalized_preferred_targets:
            continue
        normalized_preferred_targets.append(value)
    if normalized_preferred_targets:
        blocks.append(f"PREFERRED PATCH TARGETS: {', '.join(normalized_preferred_targets)}")
    for section in allowed_sections:
        content = sections.get(section)
        blocks.append(f"=== SECTION:{section} START ===")
        blocks.append(content if content is not None else "(section missing)")
        blocks.append(f"=== SECTION:{section} END ===")
    anchors = list_safe_patch_anchors(normalized_html, allowed_sections)
    if anchors:
        blocks.append("CURRENT SAFE PATCH ANCHORS:")
        if PATCH_SECTION_BODY in allowed_sections and PATCH_SECTION_HUD in anchors:
            blocks.append(f"=== ANCHOR:{PATCH_SECTION_HUD} START ===")
            blocks.append(_extract_html_anchor_content(normalized_html, PATCH_SECTION_HUD) or "")
            blocks.append(f"=== ANCHOR:{PATCH_SECTION_HUD} END ===")
        if PATCH_SECTION_SCRIPT in allowed_sections:
            for anchor in PATCHABLE_SCRIPT_ANCHORS:
                if anchor not in anchors:
                    continue
                blocks.append(f"=== ANCHOR:{anchor} START ===")
                blocks.append(extract_script_anchor_content(normalized_html, anchor) or "")
                blocks.append(f"=== ANCHOR:{anchor} END ===")
    return "\n".join(blocks)


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").lstrip("\ufeff").strip()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"```(?:json|javascript|js|css|html)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*(?:$|\n)", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()
    return cleaned


def _looks_like_full_html(text: str) -> bool:
    return bool(re.search(r"<!DOCTYPE\s+html|<html\b", text or "", re.IGNORECASE))


def _extract_html(text: str) -> str:
    cleaned = _strip_code_fences(text)
    match = re.search(r"(<!DOCTYPE\s+html|<html\b)", cleaned, re.IGNORECASE)
    if match:
        cleaned = cleaned[match.start():]
    return cleaned.strip()


def _extract_json_candidate(text: str) -> Optional[dict]:
    cleaned = _strip_code_fences(text)
    if not cleaned:
        return None
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_section_name(section: str) -> Optional[str]:
    value = str(section or "").strip().upper()
    return value if value in PATCHABLE_SECTION_ORDER else None


def _normalize_patch_target(target: str) -> tuple[Optional[str], Optional[str]]:
    value = str(target or "").strip().upper()
    if value in PATCHABLE_SECTION_ORDER:
        return value, None
    if value in PATCHABLE_SCRIPT_ANCHORS:
        return PATCH_SECTION_SCRIPT, value
    if value == PATCH_SECTION_HUD:
        return PATCH_SECTION_BODY, PATCH_SECTION_HUD
    if value == PATCH_SECTION_HTML_SHELL:
        return PATCH_SECTION_BODY, PATCH_SECTION_HTML_SHELL
    return None, None


def _parse_legacy_marker_blocks(text: str) -> List[SectionPatch]:
    cleaned = _strip_code_fences(text)
    patches: List[SectionPatch] = []
    style_marker = re.search(r"/\*\s*===\s*CURRENT\s*<style>\s*===\s*\*/", cleaned)
    script_marker = re.search(r"/\*\s*===\s*CURRENT\s*<script>\s*===\s*\*/", cleaned)
    if style_marker and script_marker:
        style_content = cleaned[style_marker.end():script_marker.start()].strip()
        script_content = cleaned[script_marker.end():].strip()
        if style_content:
            patches.append(SectionPatch(section=PATCH_SECTION_STYLE, content=style_content))
        if script_content:
            patches.append(SectionPatch(section=PATCH_SECTION_SCRIPT, content=script_content))
        return patches
    return []


def parse_patch_response(
    raw_text: str,
    *,
    allowed_sections: Sequence[str],
    strict: bool = False,
    replace_sections_only: bool = False,
) -> tuple[Optional[List[SectionPatch]], Optional[str]]:
    if strict:
        # Production quality repair has one wire format. Never reinterpret
        # prose, malformed JSON or a partial batch as an entire game script.
        cleaned = (raw_text or "").strip()
        if cleaned.startswith("```json") and cleaned.endswith("```"):
            cleaned = cleaned[7:-3].strip()
        try:
            payload = json.loads(cleaned)
        except (ValueError, TypeError) as exc:
            raise ValueError("patch_validation_failed:invalid_json") from exc
        items = payload.get("patches") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError("patch_validation_failed:missing_patches")
        patches = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("patch_validation_failed:invalid_patch")
            section, anchor = _normalize_patch_target(item.get("section"))
            operation = item.get("operation")
            if section not in allowed_sections or operation not in {"replace_section", "replace_exact", "replace_block"}:
                raise ValueError("patch_validation_failed:invalid_target_or_operation")
            if replace_sections_only and (operation != "replace_section" or anchor or item.get("anchor") or item.get("search")):
                raise ValueError("patch_validation_failed:complete_section_required")
            if not isinstance(item.get("content"), str):
                raise ValueError("patch_validation_failed:invalid_content")
            search = item.get("search")
            if operation == "replace_exact" and (not isinstance(search, str) or not search):
                raise ValueError("patch_validation_failed:invalid_search")
            patches.append(SectionPatch(section=section, operation=operation,
                content=item["content"], search=search, anchor=item.get("anchor") or anchor))
        return patches, None

    cleaned = _strip_code_fences(raw_text)
    if not cleaned:
        return None, None
    allowed = {_normalize_section_name(section) for section in allowed_sections}
    allowed.discard(None)

    parsed = _extract_json_candidate(cleaned)
    if isinstance(parsed, dict):
        patches_payload = parsed.get("patches")
        patches: List[SectionPatch] = []
        if isinstance(patches_payload, list):
            for item in patches_payload:
                if not isinstance(item, dict):
                    continue
                section, anchor = _normalize_patch_target(item.get("section"))
                if section is None or section not in allowed:
                    continue
                content = item.get("content")
                if not isinstance(content, str):
                    continue
                patches.append(
                    SectionPatch(
                        section=section,
                        content=content,
                        operation=str(item.get("operation") or "replace_section"),
                        search=item.get("search") if isinstance(item.get("search"), str) else None,
                        anchor=(
                            str(item.get("anchor")).strip().upper()
                            if item.get("anchor") is not None
                            else anchor
                        ),
                    )
                )
        else:
            for key, value in parsed.items():
                section, anchor = _normalize_patch_target(key)
                if section is None or section not in allowed or not isinstance(value, str):
                    continue
                patches.append(
                    SectionPatch(
                        section=section,
                        content=value,
                        operation="replace_block" if anchor else "replace_section",
                        anchor=anchor,
                    )
                )
        if patches:
            return patches, None

    # A valid JSON patch can itself contain HTML literals inside its JS.
    # Parse that envelope before considering the legacy full-document fallback.
    if _looks_like_full_html(cleaned):
        return None, _extract_html(cleaned)

    legacy_patches = [
        patch for patch in _parse_legacy_marker_blocks(cleaned)
        if patch.section in allowed
    ]
    if legacy_patches:
        return legacy_patches, None

    if PATCH_SECTION_SCRIPT in allowed:
        return [SectionPatch(section=PATCH_SECTION_SCRIPT, content=cleaned)], None
    if len(allowed) == 1:
        only_section = next(iter(allowed))
        return [SectionPatch(section=only_section, content=cleaned)], None
    return None, None


def apply_section_patches(
    html: str,
    patches: Iterable[SectionPatch],
) -> str:
    updated = ensure_structured_section_markers(html)
    whole_sections = set()
    for patch in patches:
        if patch.operation not in {"replace_section", "replace_block", "replace_exact"}:
            raise ValueError("patch_validation_failed:unsupported_operation")
        if patch.operation == "replace_section":
            if patch.section in whole_sections:
                raise ValueError("patch_validation_failed:duplicate_section_replacement:" + patch.section)
            whole_sections.add(patch.section)
        if patch.operation == "replace_exact":
            existing = extract_patchable_sections(updated).get(patch.section)
            if existing is None or not patch.search or existing.count(patch.search) != 1:
                raise ValueError("patch_validation_failed:search_not_unique:" + patch.section)
            replacement = existing.replace(patch.search, patch.content, 1)
            if patch.section == PATCH_SECTION_SCRIPT:
                updated = replace_script_content(updated, replacement)
            elif patch.section == PATCH_SECTION_STYLE:
                updated = replace_style_content(updated, replacement)
            elif patch.section == PATCH_SECTION_BODY:
                updated = replace_body_content(updated, replacement)
            continue
        if patch.operation == "replace_block":
            anchors = PATCHABLE_SCRIPT_ANCHORS if patch.section == PATCH_SECTION_SCRIPT else PATCHABLE_HTML_ANCHORS if patch.section == PATCH_SECTION_BODY else ()
            if patch.anchor not in anchors:
                raise ValueError("patch_validation_failed:unsupported_anchor")
        if patch.section == PATCH_SECTION_STYLE:
            updated = replace_style_content(updated, patch.content)
        elif patch.section == PATCH_SECTION_BODY:
            if patch.anchor in PATCHABLE_HTML_ANCHORS and patch.operation == "replace_block":
                updated = replace_html_anchor_content(updated, patch.anchor, patch.content)
            else:
                updated = replace_body_content(updated, patch.content)
        elif patch.section == PATCH_SECTION_SCRIPT:
            if patch.anchor in PATCHABLE_SCRIPT_ANCHORS and patch.operation == "replace_block":
                updated = replace_script_anchor_content(updated, patch.anchor, patch.content)
            else:
                updated = replace_script_content(updated, patch.content)
    return updated
