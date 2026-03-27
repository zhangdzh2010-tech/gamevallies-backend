from __future__ import annotations

import re

_SCORE_TERM_GROUP = r"(?:score|points?|combo|multiplier|coins?|time|timer|moves?|steps?)"

_RAW_SCORE_TERM_RE = re.compile(rf"\b{_SCORE_TERM_GROUP}\b", re.IGNORECASE)
_SCORE_SELECTOR_RE = re.compile(
    rf"(getelementbyid|queryselector)\s*\(\s*['\"#.]?(?:{_SCORE_TERM_GROUP})",
    re.IGNORECASE,
)
_SCORE_TEXT_ASSIGN_RE = re.compile(
    rf"(?:{_SCORE_TERM_GROUP})\w*\.(textcontent|innertext|innerhtml)\s*=",
    re.IGNORECASE,
)
_SCORE_FILL_TEXT_RE = re.compile(
    rf"filltext\s*\(\s*['\"][^'\"]*(?:{_SCORE_TERM_GROUP})",
    re.IGNORECASE,
)
_SCORE_BRIDGE_MARKER_RE = re.compile(r"__playforgeScoreBridgeInstalled", re.IGNORECASE)


def has_visible_scoring_loop(code: str) -> bool:
    source = code or ""
    if not source:
        return False

    if _SCORE_BRIDGE_MARKER_RE.search(source):
        return True

    lower = source.lower()

    if _SCORE_SELECTOR_RE.search(source):
        return True

    if _SCORE_TEXT_ASSIGN_RE.search(lower):
        return True

    if _SCORE_FILL_TEXT_RE.search(lower):
        return True

    return bool(_RAW_SCORE_TERM_RE.search(source))
