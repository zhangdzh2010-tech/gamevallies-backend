"""PR-12: Template cache as "inspiration" — soft reference, not hard gate.

Original problem
----------------
The Template Match stage previously ran as an either/or gate: if a cached
template matched tightly, we short-circuited the LLM; if not, the LLM had
no visibility into the cache. Tight matches produced obviously-clone
outputs; loose matches left all the cached engineering work on the table.

P1 approach
-----------
Route a deterministic fraction of requests (`template_lane_share`,
governed by tier via PR-09) into an "inspiration lane": we still call the
LLM, but we surface the top-k cached templates as *reference-only*
snippets. The LLM is explicitly told NOT to copy them but may use them
for structural ideas.

The 20/80 default split: share=0.2 keeps 80% of requests on the
blank-canvas lane where diversity is highest, while still reusing cached
engineering on 20% of requests where a tight match exists.

Public API
----------
* `decide_lane(variation_seed, tier_share)` → bool  (True = inspiration lane)
* `select_inspiration(candidates, *, k)` → trimmed list of reference snippets

The actual cache lookup is left to the existing `code_template_cache`
module; this file only owns *routing* and *snippet shaping*.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class InspirationSnippet:
    """A bounded, reference-only view onto a cached template."""

    template_id: str
    title: str
    score: float
    summary: str  # <= 400 chars; never the full code
    code_excerpt: str  # <= 800 chars; marked clearly as reference


_SUMMARY_MAX = 400
_CODE_MAX = 800
_DEFAULT_K = 2


# ----------------------------------------------------------------------
# Lane decision
# ----------------------------------------------------------------------


def decide_lane(variation_seed: str, tier_share: float) -> bool:
    """Return True iff this seed should route into the inspiration lane.

    Deterministic: same seed + share → same decision.
    """
    share = max(0.0, min(1.0, tier_share))
    if share <= 0.0:
        return False
    if share >= 1.0:
        return True
    digest = hashlib.sha256(
        (variation_seed or "no-seed" + "|lane").encode("utf-8")
    ).hexdigest()
    # Use the last 4 hex chars as an integer in [0, 65536).
    bucket = int(digest[-4:], 16)
    return (bucket % 10_000) < int(share * 10_000)


# ----------------------------------------------------------------------
# Snippet selection
# ----------------------------------------------------------------------


def select_inspiration(
    candidates: Sequence[Dict[str, Any]],
    *,
    k: int = _DEFAULT_K,
    min_score: float = 0.55,
) -> List[InspirationSnippet]:
    """Shape up to k cached-template rows into inspiration snippets.

    Candidate dicts are expected to carry at least:
        template_id, title, score, summary (or description), code (or body)

    We are deliberately lenient about the input shape because
    `code_template_cache.search` returns slightly different dicts in
    different deploy eras. Missing fields are replaced with empty strings.
    """

    if k <= 0 or not candidates:
        return []

    usable = [c for c in candidates if _score_of(c) >= min_score]
    # Sort by score descending, break ties on title for determinism.
    usable.sort(key=lambda c: (-_score_of(c), str(c.get("title", ""))))

    out: List[InspirationSnippet] = []
    for cand in usable[:k]:
        out.append(_shape(cand))
    return out


def render_inspiration_block(snippets: Sequence[InspirationSnippet]) -> str:
    """Format a list of snippets as a compact prompt appendix.

    The resulting string is meant to be appended to the system prompt
    inside a `## Reference only (do NOT copy verbatim)` section. If
    snippets is empty, returns an empty string.
    """
    if not snippets:
        return ""

    parts = [
        "## Reference templates (inspiration only — do NOT copy verbatim)",
        "The following cached templates are provided for structural ideas.",
        "Use their architecture/flow if helpful; do not reproduce their text.",
        "",
    ]
    for i, s in enumerate(snippets, 1):
        parts.append(f"### Reference {i}: {s.title} (score={s.score:.2f})")
        if s.summary:
            parts.append(f"- Summary: {s.summary}")
        if s.code_excerpt:
            parts.append("- Excerpt:")
            parts.append("```")
            parts.append(s.code_excerpt)
            parts.append("```")
        parts.append("")
    return "\n".join(parts).rstrip()


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


def _score_of(cand: Dict[str, Any]) -> float:
    try:
        return float(cand.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _shape(cand: Dict[str, Any]) -> InspirationSnippet:
    summary = (
        cand.get("summary")
        or cand.get("description")
        or cand.get("title")
        or ""
    )
    code = cand.get("code") or cand.get("body") or cand.get("source") or ""

    return InspirationSnippet(
        template_id=str(cand.get("template_id", cand.get("id", ""))),
        title=str(cand.get("title", "") or "template"),
        score=_score_of(cand),
        summary=_truncate(str(summary), _SUMMARY_MAX),
        code_excerpt=_truncate(str(code), _CODE_MAX),
    )


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    # Try to cut at a line break near the limit.
    cut = text[:limit]
    last_nl = cut.rfind("\n")
    if last_nl >= int(limit * 0.6):
        cut = cut[:last_nl]
    return cut.rstrip() + "\n…(truncated)"


__all__ = [
    "InspirationSnippet",
    "decide_lane",
    "select_inspiration",
    "render_inspiration_block",
]
