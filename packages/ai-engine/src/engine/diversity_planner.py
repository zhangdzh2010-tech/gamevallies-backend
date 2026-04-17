"""PR-09: DiversityPlanner — convert tier + anchors + seed into a sampling profile.

Before P1 the generation tier (draft / safe / standard / showcase) only
influenced retry budgets. Temperature / top_p were hardcoded per call site,
so "showcase" and "draft" requests shared the same distribution.

This module maps (tier, anchors, variation_seed) → a DiversityPlan
structure consumed by the LLM gateway as a `sampling_profile`. The LLM
client passes the profile through to the provider; existing integrations
treat it as additive so providers that don't support top_p can ignore it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple


Tier = Literal["draft", "safe", "standard", "showcase"]


# ----------------------------------------------------------------------
# Tier gradient table
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class TierGradient:
    temperature: float
    top_p: float
    jitter: float  # ± applied deterministically per seed
    template_lane_share: float  # 0.0 → never, 1.0 → always inspiration lane


_TIER_GRADIENTS: Dict[str, TierGradient] = {
    "draft":     TierGradient(temperature=0.90, top_p=0.95, jitter=0.05, template_lane_share=0.30),
    "safe":      TierGradient(temperature=0.75, top_p=0.90, jitter=0.02, template_lane_share=0.10),
    "standard":  TierGradient(temperature=0.85, top_p=0.92, jitter=0.04, template_lane_share=0.20),
    "showcase":  TierGradient(temperature=1.00, top_p=0.98, jitter=0.08, template_lane_share=0.40),
}


# ----------------------------------------------------------------------
# Plan output
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class DiversityPlan:
    tier: str
    temperature: float
    top_p: float
    variation_jitter: float
    template_lane: bool  # True → PR-12 inspiration lane; False → blank-canvas
    sampling_profile: Dict[str, Any]  # passed to LLM gateway verbatim

    def to_sampling_profile(self) -> Dict[str, Any]:
        """Shape consumed by llm_client / llm_gateway."""
        return dict(self.sampling_profile)


# ----------------------------------------------------------------------
# Seed helpers
# ----------------------------------------------------------------------


def _det_float_from_seed(variation_seed: str, *parts: str) -> float:
    """Map seed triple → float in [0, 1) deterministically."""
    material = "|".join([variation_seed or "", *[p or "" for p in parts]])
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    num = int.from_bytes(digest[:8], "big", signed=False)
    return (num % 10_000_000) / 10_000_000.0


def _apply_jitter(value: float, jitter: float, seed_fraction: float) -> float:
    """Shift `value` by ± jitter according to seed_fraction ∈ [0,1)."""
    if jitter <= 0.0:
        return value
    # Map [0,1) → [-1, +1]
    signed = (seed_fraction * 2.0) - 1.0
    return value + signed * jitter


# ----------------------------------------------------------------------
# DiversityPlanner
# ----------------------------------------------------------------------


class DiversityPlanner:
    """Pure planner — takes anchors + tier + seed, emits a DiversityPlan.

    `anchors` is optional: if None, we fall back to tier defaults and an
    even template-lane coin-flip at the tier's configured share. When
    anchors are present, pace_axis / style_axis can bias the output:
    `chaotic` pace bumps temperature slightly, `minimal` style pulls
    temperature down slightly. These biases are capped to ±0.05.
    """

    _PACE_BIAS: Dict[str, float] = {
        "slow": -0.03,
        "steady": 0.0,
        "fast": +0.02,
        "chaotic": +0.05,
    }
    _STYLE_BIAS: Dict[str, float] = {
        "minimal": -0.02,
        "pixel": 0.0,
        "cartoon": +0.01,
        "painterly": +0.02,
        "neon": +0.03,
    }

    def plan(
        self,
        *,
        tier: str,
        variation_seed: str,
        pace_axis: Optional[str] = None,
        style_axis: Optional[str] = None,
        provider_supports_top_p: bool = True,
    ) -> DiversityPlan:
        gradient = _TIER_GRADIENTS.get(tier, _TIER_GRADIENTS["standard"])

        pace_bias = self._PACE_BIAS.get((pace_axis or "").lower(), 0.0)
        style_bias = self._STYLE_BIAS.get((style_axis or "").lower(), 0.0)

        seed_frac_temp = _det_float_from_seed(variation_seed, tier, "temp")
        seed_frac_topp = _det_float_from_seed(variation_seed, tier, "top_p")
        seed_frac_lane = _det_float_from_seed(variation_seed, tier, "lane")

        base_temp = gradient.temperature + pace_bias + style_bias
        temperature = _apply_jitter(base_temp, gradient.jitter, seed_frac_temp)
        # Clamp to provider-accepted range.
        temperature = max(0.0, min(1.5, round(temperature, 3)))

        # top_p jitter is half of the temperature jitter — small on purpose.
        top_p_raw = _apply_jitter(
            gradient.top_p, gradient.jitter * 0.5, seed_frac_topp
        )
        top_p = max(0.05, min(1.0, round(top_p_raw, 3)))

        template_lane = seed_frac_lane < gradient.template_lane_share

        profile: Dict[str, Any] = {
            "temperature": temperature,
            "tier": tier,
            "variation_seed_hash": _short_hash(variation_seed),
        }
        if provider_supports_top_p:
            profile["top_p"] = top_p

        return DiversityPlan(
            tier=tier,
            temperature=temperature,
            top_p=top_p,
            variation_jitter=gradient.jitter,
            template_lane=template_lane,
            sampling_profile=profile,
        )


def _short_hash(material: str) -> str:
    """8-char hex tag useful for log correlation, never used for security."""
    return hashlib.sha256((material or "").encode("utf-8")).hexdigest()[:8]


# Convenience factory so callers don't juggle class lifecycles.
_PLANNER_SINGLETON = DiversityPlanner()


def plan_for(
    *,
    tier: str,
    variation_seed: str,
    pace_axis: Optional[str] = None,
    style_axis: Optional[str] = None,
    provider_supports_top_p: bool = True,
) -> DiversityPlan:
    return _PLANNER_SINGLETON.plan(
        tier=tier,
        variation_seed=variation_seed,
        pace_axis=pace_axis,
        style_axis=style_axis,
        provider_supports_top_p=provider_supports_top_p,
    )


__all__ = [
    "DiversityPlanner",
    "DiversityPlan",
    "TierGradient",
    "plan_for",
]
