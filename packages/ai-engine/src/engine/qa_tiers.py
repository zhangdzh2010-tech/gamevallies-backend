"""PR-10: QA rule tiering — classify a QACheckError into HARD / SOFT / CREATIVE.

Why
---
Prior to P1, every QA issue came out as `severity="error"` or
`severity="warning"` and we fed them all into `_fix_with_llm`. This
drowned the model in cosmetic complaints (inconsistent naming, non-ideal
color contrast) and frequently caused it to rewrite *working* gameplay
code to chase style issues.

Tier semantics
--------------
* HARD      — correctness: must pass. Syntax, undefined refs, crashes.
* SOFT      — quality: should pass but not blocking. Naming, formatting.
* CREATIVE  — creative-preserve: skip the fix if fun_score ≥ threshold.
              Examples: "minimalist style feels sparse", "palette could
              be more vibrant". We explicitly do not want the LLM to
              un-minimalize a minimalist game.

Classification is purely keyword-based — we do not parse the issue's AST
or hook into the QA rule registry. This is deliberate: tiers are advisory
to the fixer, not structural, and the heuristics are cheap and debuggable.
"""

from __future__ import annotations

from typing import Iterable, Literal, Optional, Protocol, Tuple


Tier = Literal["HARD", "SOFT", "CREATIVE"]


# Keyword patterns: (substring-lowercased, tier). First match wins.
# Order: HARD first (most specific), then CREATIVE (narrow), SOFT (catch-all).
_HARD_KEYWORDS: Tuple[str, ...] = (
    "syntaxerror", "syntax error",
    "is not defined", "undefined", "undeclared",
    "unexpected token",
    "cannot read", "cannot find", "missing required",
    "failed to parse", "invalid json",
    "runtime error", "reference error", "typeerror",
    "infinite loop", "stack overflow",
    "collision missing", "state machine invalid",
    "canvas missing", "no game loop",
    "broken import", "module not found",
)

_CREATIVE_KEYWORDS: Tuple[str, ...] = (
    "palette", "color contrast", "colours",
    "minimalist", "too sparse", "too busy",
    "mood", "atmosphere", "aesthetic",
    "feel is", "feels ", "vibe",
    "background art", "visual style could",
    "character design could", "could be more",
)

_SOFT_KEYWORDS: Tuple[str, ...] = (
    "naming", "variable name", "identifier",
    "indent", "formatting", "whitespace",
    "comment", "docstring",
    "magic number", "hard-coded",
    "unused", "dead code",
    "warning", "suggestion",
)


# ----------------------------------------------------------------------
# Protocol for the QACheckError shape so we do not import qa_pipeline.
# ----------------------------------------------------------------------


class _HasMessageAndType(Protocol):
    type: str
    message: str
    # Some versions of QACheckError carry .severity; we read it if present.


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def classify_issue(
    *,
    type: str = "",
    message: str = "",
    severity: Optional[str] = None,
) -> Tier:
    """Return one of HARD / SOFT / CREATIVE for a single QA issue.

    Heuristics:
    1. If severity is explicitly "error" AND the message contains any HARD
       keyword → HARD.
    2. If the type or message contains a CREATIVE keyword → CREATIVE.
    3. If severity is "warning" OR message contains a SOFT keyword → SOFT.
    4. Otherwise default to HARD (fail-safe: we'd rather over-block).
    """

    blob = f"{type or ''}\n{message or ''}".lower()

    for kw in _HARD_KEYWORDS:
        if kw in blob:
            return "HARD"

    for kw in _CREATIVE_KEYWORDS:
        if kw in blob:
            return "CREATIVE"

    sev = (severity or "").lower()
    if sev == "warning":
        return "SOFT"
    for kw in _SOFT_KEYWORDS:
        if kw in blob:
            return "SOFT"

    # Conservative default: errors default to HARD, unlabelled defaults SOFT.
    if sev == "error":
        return "HARD"
    return "SOFT"


def classify_issue_from(issue: _HasMessageAndType) -> Tier:
    """Convenience wrapper for a duck-typed QACheckError-like object."""
    return classify_issue(
        type=getattr(issue, "type", "") or "",
        message=getattr(issue, "message", "") or "",
        severity=getattr(issue, "severity", None),
    )


def filter_fixable(
    issues: Iterable[_HasMessageAndType],
    *,
    fun_score: Optional[float] = None,
    creative_preserve_threshold: float = 7.0,
) -> Tuple[list, dict]:
    """Split the issue stream into (to_fix, dropped_by_reason) lists.

    * HARD issues are always kept.
    * SOFT issues are always kept.
    * CREATIVE issues are dropped iff fun_score is None OR >= threshold.

    Returns:
        (to_fix_list, dropped_counts_by_tier)
    """
    to_fix: list = []
    dropped: dict = {"CREATIVE_preserved": 0}
    skip_creative = fun_score is None or fun_score >= creative_preserve_threshold

    for issue in issues:
        tier = classify_issue_from(issue)
        if tier == "CREATIVE" and skip_creative:
            dropped["CREATIVE_preserved"] += 1
            continue
        to_fix.append(issue)

    return to_fix, dropped


__all__ = [
    "Tier",
    "classify_issue",
    "classify_issue_from",
    "filter_fixable",
]
