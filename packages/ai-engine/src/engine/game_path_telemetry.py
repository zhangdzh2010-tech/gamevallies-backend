"""Arcade/game full-path telemetry for yield ledgers.

Science/tool persist ``template_route`` + ``family_id`` via
``interactive_telemetry``. Games run pipeline_v2 ``logic_generate`` and
previously left those fields null, so the yield script recorded MISSING.

Game full generate is recorded as ``MISS`` — the existing enum meaning
"full logic_generate, not a failure" — with ``route_reason=game_full_generate``
so science-router misses stay distinguishable. No new route enum is introduced.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .runtime_profile_ids import normalize_runtime_profile_id

# Same HIT/SOFT/MISS enum as science. MISS = full generate, not a failure.
GAME_TEMPLATE_ROUTE = "MISS"
GAME_ROUTE_REASON = "game_full_generate"
INTERACTIVE_RUNTIME_PROFILE = "interactive_experience"

# Stable mechanic ids aligned with pipeline_v2_specification primary_goal.
# Do not invent HIT recipes here; these label the full-generate family only.
PROFILE_MECHANIC_IDS: Dict[str, str] = {
    "casual_arcade": "clear_feedback_loop",
    "casual_arcade_burst": "clear_feedback_loop",
    "casual_arcade_orbit": "orbit_control",
    "casual_arcade_rescue": "rescue_route",
    "casual_lane": "lane_survival",
    "casual_lane_dash": "lane_survival",
    "casual_lane_chase": "lane_chase",
    "casual_action": "clear_feedback_loop",
    "casual_action_arena": "arena_clearance",
    "casual_action_survival": "survival_holdout",
    "puzzle_grid": "grid_completion",
    "puzzle_grid_match": "grid_completion",
    "puzzle_grid_merge": "merge_progression",
    "puzzle_grid_route": "route_completion",
    "tap_challenge": "clear_feedback_loop",
    "tap_challenge_timing": "clear_feedback_loop",
    "tap_challenge_combo": "combo_target",
}


def family_id_for_runtime_profile(runtime_profile: Optional[str]) -> str:
    """Canonical runtime profile is the stable game family id."""
    return normalize_runtime_profile_id(runtime_profile)


def mechanic_id_for_profile(runtime_profile: Optional[str]) -> str:
    profile = family_id_for_runtime_profile(runtime_profile)
    if profile in PROFILE_MECHANIC_IDS:
        return PROFILE_MECHANIC_IDS[profile]
    if profile.startswith("puzzle_grid"):
        return "grid_completion"
    if profile.startswith("casual_lane"):
        return "lane_survival"
    if profile.startswith("casual_arcade"):
        return "clear_feedback_loop"
    if profile.startswith("casual_action"):
        return "clear_feedback_loop"
    if profile.startswith("tap_challenge"):
        return "clear_feedback_loop"
    return profile or "arcade_loop"


def mechanic_id_for_spec(spec: Any, runtime_profile: Optional[str] = None) -> str:
    """Prefer the advertised core mechanic type; else the profile mechanic id."""
    mechanics = getattr(spec, "core_mechanics", None) or []
    for mechanic in mechanics:
        raw = getattr(mechanic, "type", None)
        if raw is None and isinstance(mechanic, dict):
            raw = mechanic.get("type")
        text = str(raw or "").strip()
        if text:
            return text.lower().replace(" ", "_")[:64]
    return mechanic_id_for_profile(runtime_profile)


def game_path_yield_fields(
    *,
    runtime_profile: Optional[str],
    spec: Any = None,
) -> Dict[str, Any]:
    family_id = family_id_for_runtime_profile(runtime_profile)
    recipe_id = mechanic_id_for_profile(runtime_profile)
    mechanic_id = mechanic_id_for_spec(spec, runtime_profile)
    artifact_kind = str(getattr(spec, "artifact_kind", None) or "game").strip().lower() or "game"
    return {
        "template_route": GAME_TEMPLATE_ROUTE,
        "family_id": family_id,
        "recipe_id": recipe_id,
        "mechanic_id": mechanic_id,
        "route_reason": GAME_ROUTE_REASON,
        "short_path": False,
        "artifact_kind": artifact_kind,
    }


def merge_game_path_telemetry(
    breakdown: Optional[Dict[str, Any]],
    *,
    runtime_profile: Optional[str],
    spec: Any = None,
) -> Dict[str, Any]:
    """Always persist route + family on the game full path.

    Existing HIT/SOFT labels (science leaked onto this payload) are kept;
    only missing keys are filled so this helper cannot overwrite science.
    """
    fields = game_path_yield_fields(runtime_profile=runtime_profile, spec=spec)
    base = dict(breakdown or {})
    existing_route = str(base.get("template_route") or base.get("templateRoute") or "").strip()
    existing_family = base.get("family_id") or base.get("familyId")
    if existing_route in {"HIT", "SOFT"} and existing_family:
        for key, value in fields.items():
            base.setdefault(key, value)
        return base
    base.update(fields)
    return base


def is_interactive_runtime_profile(runtime_profile: Optional[str]) -> bool:
    return family_id_for_runtime_profile(runtime_profile) == INTERACTIVE_RUNTIME_PROFILE
