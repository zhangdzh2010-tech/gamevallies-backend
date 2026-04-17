"""P2.2 adaptive creative-preserve threshold.

Purpose
-------
PR-10 introduced a static ``P1_QA_CREATIVE_PRESERVE_THRESHOLD`` (default 7.0)
used by :func:`engine.qa_tiers.filter_fixable` to decide whether CREATIVE-tier
QA issues should be *preserved* (i.e. not fed back to the fixer). A single
fixed threshold is coarse:

* High-tier generations that routinely produce fun_score≈8 never actually
  exercise the creative-preserve branch because the threshold is already
  comfortably below their distribution mean.
* Lower-tier / SHORT game_type generations trend toward fun_score≈6 and
  therefore almost *never* trigger the preserve path even when the creative
  content is perfectly acceptable for that cohort.

This module offers a tier-aware deterministic adjustment on top of the base
threshold. It intentionally has no state, no rolling window, and no
randomness — the adjustment is a pure function of ``(base, tier, game_type)``
so behaviour is trivially reproducible.

The adjustment is **capped** to ``[base - 1.5, base + 1.0]`` to keep deltas
small and auditable; the intent is to nudge the preserve line, never to flip
a cohort wholesale.

Safety
------
All public functions are fully guarded: any unexpected input simply returns
the input base unchanged. This module is only consulted when
``P2_ADAPTIVE_THRESHOLD_ENABLED`` is True in settings.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple


# Per-tier delta (added to base). High tiers already run creative; we relax
# slightly. Low tiers are stricter by default so we *lower* the threshold a
# touch to let "good-enough" creative content pass the preserve branch.
_TIER_DELTA = {
    "ULTRA": 0.5,
    "HIGH": 0.25,
    "STANDARD": 0.0,
    "FAST": -0.5,
    "DRAFT": -1.0,
}

# Per-game-type delta (added on top of tier delta). SHORT games have
# a thinner creative surface so we are strict; NARRATIVE games benefit
# from preserving quirky creative choices.
_GAMETYPE_DELTA = {
    "narrative": 0.25,
    "puzzle": 0.0,
    "arcade": -0.25,
    "short": -0.5,
}

_MIN_CLAMP = -1.5
_MAX_CLAMP = 1.0


def _normalize_tier(tier: Any) -> Optional[str]:
    if tier is None:
        return None
    # Tier may be an enum with .value, a plain string, or something else.
    raw = getattr(tier, "value", tier)
    if not isinstance(raw, str):
        try:
            raw = str(raw)
        except Exception:
            return None
    return raw.strip().upper() or None


def _normalize_game_type(game_type: Any) -> Optional[str]:
    if game_type is None:
        return None
    raw = getattr(game_type, "value", game_type)
    if not isinstance(raw, str):
        try:
            raw = str(raw)
        except Exception:
            return None
    return raw.strip().lower() or None


def compute_adjusted_threshold(
    base: float,
    *,
    tier: Any = None,
    game_type: Any = None,
) -> Tuple[float, float]:
    """Return ``(adjusted, delta)``. Never raises.

    ``adjusted = clamp(base + tier_delta + gametype_delta, base - 1.5, base + 1.0)``

    If either label is unknown the respective delta is treated as 0.

    R-2 fix: when ``base`` cannot be coerced to float we return ``(base, 0.0)``
    unchanged instead of NaN. The caller's subsequent ``float(adjusted)`` call
    will then raise in the expected way and the guarded try/except upstream
    will fall back to the unmodified base threshold. This preserves the
    "never raises" contract of this function without introducing NaN (which
    silently inverts ``>=`` comparisons downstream).
    """
    try:
        base_f = float(base)
    except (TypeError, ValueError):
        # Pass-through: the caller retains whatever they passed in. The
        # only existing caller (qa_pipeline._fix_with_llm) wraps its
        # adaptive-threshold block in try/except and reverts to the base
        # threshold on any error, so this is safe.
        return base, 0.0  # type: ignore[return-value]

    tkey = _normalize_tier(tier)
    gkey = _normalize_game_type(game_type)

    tier_delta = _TIER_DELTA.get(tkey or "", 0.0)
    gt_delta = _GAMETYPE_DELTA.get(gkey or "", 0.0)

    raw_delta = float(tier_delta) + float(gt_delta)
    # Clamp delta, not the final threshold, to keep ±bounds symmetric w.r.t.
    # base regardless of its magnitude.
    if raw_delta < _MIN_CLAMP:
        clamped_delta = _MIN_CLAMP
    elif raw_delta > _MAX_CLAMP:
        clamped_delta = _MAX_CLAMP
    else:
        clamped_delta = raw_delta

    adjusted = base_f + clamped_delta
    return adjusted, clamped_delta


__all__ = ["compute_adjusted_threshold"]
