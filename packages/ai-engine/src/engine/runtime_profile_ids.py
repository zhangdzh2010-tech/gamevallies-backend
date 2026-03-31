"""Canonical runtime profile identifiers and legacy aliases."""

from __future__ import annotations

from typing import Iterable, Tuple

DEFAULT_RUNTIME_PROFILE_ID = "casual_arcade"

LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS = {
    "portrait_arcade": "casual_arcade",
    "lane_runner": "casual_lane",
    "grid_puzzle": "puzzle_grid",
    "topdown_action": "casual_action",
    "tap_timing": "tap_challenge",
    "topdown_dodge": "casual_action",
    "topdown_shooter": "casual_action",
}

CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS = {
    "casual_arcade": ("portrait_arcade",),
    "casual_arcade_burst": ("portrait_arcade",),
    "casual_arcade_orbit": ("portrait_arcade",),
    "casual_arcade_rescue": ("portrait_arcade",),
    "casual_lane": ("lane_runner",),
    "casual_lane_dash": ("lane_runner",),
    "casual_lane_chase": ("lane_runner",),
    "puzzle_grid": ("grid_puzzle",),
    "puzzle_grid_match": ("grid_puzzle",),
    "puzzle_grid_merge": ("grid_puzzle",),
    "puzzle_grid_route": ("grid_puzzle",),
    "casual_action": ("topdown_action", "topdown_dodge", "topdown_shooter"),
    "casual_action_arena": ("topdown_action", "topdown_dodge", "topdown_shooter"),
    "casual_action_survival": ("topdown_action", "topdown_dodge", "topdown_shooter"),
    "tap_challenge": ("tap_timing",),
    "tap_challenge_timing": ("tap_timing",),
    "tap_challenge_combo": ("tap_timing",),
}


def normalize_runtime_profile_id(profile_id: str | None) -> str:
    normalized = str(profile_id or "").strip()
    if not normalized:
        return DEFAULT_RUNTIME_PROFILE_ID
    return LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS.get(normalized, normalized)


def runtime_profile_lookup_candidates(profile_id: str | None) -> Tuple[str, ...]:
    canonical = normalize_runtime_profile_id(profile_id)
    legacy = CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS.get(canonical, ())
    seen: list[str] = []
    for candidate in (canonical, *legacy):
        if candidate and candidate not in seen:
            seen.append(candidate)
    return tuple(seen)


def prompt_key_candidates_for_profile(profile_id: str | None) -> Tuple[str, ...]:
    return tuple(
        f"bundle.runtime.profile.{candidate}"
        for candidate in runtime_profile_lookup_candidates(profile_id)
    )


def canonical_runtime_profiles() -> Iterable[str]:
    return CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS.keys()
