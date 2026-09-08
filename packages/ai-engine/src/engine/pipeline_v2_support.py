"""Shared constants and value types for V2PipelineRunner."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from .pipeline_errors import PipelineExecutionError
from .prompt_store import get_default_runtime_profile
from .runtime_profile_ids import normalize_runtime_profile_id

DEFAULT_STAGE_TOTAL_ATTEMPTS = 3


DEFAULT_CREATE_FULL_GENERATION_ATTEMPTS = 3


QUALITY_GATE_PATCH_STEP_KEY = "quality_gate.patch_fix"


QUALITY_GATE_PATCH_MAX_FINAL_SCORE_GAP = 1.5


QUALITY_GATE_PATCH_MAX_DIMENSION_GAP = 2.0


GENERATION_PROGRESS_HEARTBEAT_INTERVAL_S = 15.0


GENERATION_PROGRESS_HEARTBEAT_BASE_PCT = 60


GENERATION_PROGRESS_HEARTBEAT_MAX_PCT = 74


GENERATION_PROGRESS_HEARTBEAT_EXPECTED_DURATION_S = 150.0


ITERATE_QUALITY_REVIEW_TIMEOUT_KEY = "timeout.pipeline.iterate_quality_review_s"


ITERATE_QUALITY_REVIEW_DEFAULT_TIMEOUT_S = 45.0


ProgressCallback = Optional[Callable[[str, int, str, Optional[dict[str, Any]]], None]]


@dataclass
class _QualityGatePatchOutcome:
    """Result of a successful quality-gate patch repair attempt."""

    code: str
    review: Any
    quality: Any
    runtime_qa: Any
    runtime_qa_reran: bool
    runtime_retries: int
    qa_warnings: list[dict[str, Any]] = field(default_factory=list)


PROFILE_CANDIDATES_BY_GAME_TYPE: dict[str, tuple[str, ...]] = {
    "casual": (
        "casual_arcade_burst",
        "casual_arcade_orbit",
        "casual_arcade_rescue",
        "casual_lane_dash",
        "casual_lane_chase",
        "casual_action_arena",
        "casual_action_survival",
        "casual_arcade",
        "casual_action",
        "casual_lane",
    ),
    "puzzle": (
        "puzzle_grid_match",
        "puzzle_grid_merge",
        "puzzle_grid_route",
        "puzzle_grid",
        "tap_challenge_timing",
        "casual_arcade_rescue",
    ),
    "educational": (
        "puzzle_grid_route",
        "puzzle_grid_match",
        "tap_challenge_timing",
        "puzzle_grid",
        "casual_arcade_rescue",
    ),
    "funny": (
        "tap_challenge_combo",
        "casual_arcade_burst",
        "casual_action_arena",
        "casual_arcade_rescue",
        "casual_arcade",
        "casual_action",
    ),
}


PROFILE_KEYWORD_FALLBACKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("quiz", ("puzzle_grid_route", "tap_challenge_timing", "puzzle_grid_match")),
    ("learn", ("puzzle_grid_route", "tap_challenge_timing", "puzzle_grid")),
    ("teach", ("puzzle_grid_route", "puzzle_grid_match")),
    ("answer", ("puzzle_grid_route", "tap_challenge_timing")),
    ("puzzle", ("puzzle_grid_match", "puzzle_grid_merge", "puzzle_grid")),
    ("match", ("puzzle_grid_match", "puzzle_grid_merge")),
    ("merge", ("puzzle_grid_merge", "puzzle_grid_match")),
    ("logic", ("puzzle_grid_route", "puzzle_grid")),
    ("connect", ("puzzle_grid_route", "puzzle_grid")),
    ("route", ("puzzle_grid_route", "casual_arcade_rescue")),
    ("run", ("casual_lane_dash", "casual_lane", "casual_arcade_burst")),
    ("race", ("casual_lane_dash", "casual_lane_chase", "casual_lane")),
    ("chase", ("casual_lane_chase", "casual_lane_dash", "casual_action_survival")),
    ("rescue", ("casual_arcade_rescue", "casual_arcade_orbit")),
    ("orbit", ("casual_arcade_orbit", "casual_arcade_burst")),
    ("timing", ("tap_challenge_timing", "tap_challenge_combo")),
    ("rhythm", ("tap_challenge_timing", "tap_challenge_combo")),
    ("music", ("tap_challenge_timing", "tap_challenge_combo")),
    ("combo", ("tap_challenge_combo", "casual_arcade_burst")),
    ("funny", ("tap_challenge_combo", "casual_arcade_burst", "casual_action_arena")),
    ("meme", ("tap_challenge_combo", "casual_arcade_burst")),
    ("comedy", ("tap_challenge_combo", "casual_action_arena")),
    ("shoot", ("casual_action_arena", "casual_action_survival", "casual_action")),
    ("arena", ("casual_action_arena", "casual_action_survival")),
    ("survive", ("casual_action_survival", "casual_arcade_orbit", "casual_action")),
    ("drag", ("casual_arcade_orbit", "puzzle_grid_route", "casual_action")),
)


ACTION_RUNTIME_MARKERS: tuple[str, ...] = (
    "action",
    "battle",
    "combat",
    "fight",
    "slash",
    "sword",
    "katana",
    "ronin",
    "ninja",
    "warrior",
    "hero",
    "guardian",
    "boss",
    "enemy",
    "drone",
    "dash",
    "dodge",
    "rooftop",
    "leap",
    "jump",
    "survive",
    "hazard",
    "rescue",
    "escort",
    "spirit",
    "fox",
    "\u52a8\u4f5c",
    "\u6218\u6597",
    "\u4e3b\u89d2",
    "\u82f1\u96c4",
    "\u5b88\u62a4",
    "\u654c\u4eba",
    "\u51b2\u523a",
    "\u8e32\u907f",
    "\u6551\u63f4",
)


PUZZLE_RUNTIME_MARKERS: tuple[str, ...] = (
    "puzzle",
    "match",
    "merge",
    "quiz",
    "lesson",
    "teacher",
    "learn",
    "logic",
    "connect",
    "route",
    "word",
    "math",
    "answer",
    "sort",
    "\u8c1c\u9898",
    "\u6d88\u9664",
    "\u5408\u6210",
    "\u8fde\u7ebf",
    "\u95ee\u7b54",
    "\u6559\u5b66",
    "\u5b66\u4e60",
)


ACTION_FOCUSED_RUNTIME_PROFILES: tuple[str, ...] = (
    "casual_action_arena",
    "casual_action_survival",
    "casual_lane_dash",
    "casual_lane_chase",
    "casual_arcade_rescue",
)


PROFILE_TO_GAME_TYPE_HINT: dict[str, str] = {
    "casual_arcade": "casual",
    "casual_lane": "casual",
    "puzzle_grid": "puzzle",
    "casual_action": "casual",
    "tap_challenge": "funny",
    "casual_arcade_burst": "casual",
    "casual_arcade_orbit": "casual",
    "casual_arcade_rescue": "casual",
    "casual_lane_dash": "casual",
    "casual_lane_chase": "casual",
    "casual_action_arena": "casual",
    "casual_action_survival": "casual",
    "puzzle_grid_match": "puzzle",
    "puzzle_grid_merge": "puzzle",
    "puzzle_grid_route": "educational",
    "tap_challenge_timing": "funny",
    "tap_challenge_combo": "funny",
}


BASELINE_RUNTIME_PROFILES = {
    "casual_arcade",
    "casual_lane",
    "puzzle_grid",
    "casual_action",
    "tap_challenge",
}


ENTITY_BUDGET_EXPANSION_LIBRARY: dict[str, tuple[dict[str, Any], ...]] = {
    "casual": (
        {"name": "rival", "role": "enemy", "shape": "triangle", "color": "#fb7185"},
        {"name": "boost_orb", "role": "collectible", "shape": "circle", "color": "#22c55e"},
        {"name": "hazard_gate", "role": "obstacle", "shape": "rectangle", "color": "#f97316"},
        {"name": "route_marker", "role": "npc", "shape": "diamond", "color": "#38bdf8"},
        {"name": "bonus_token", "role": "collectible", "shape": "diamond", "color": "#facc15"},
    ),
    "puzzle": (
        {"name": "blocker", "role": "obstacle", "shape": "square", "color": "#ef4444"},
        {"name": "switch", "role": "collectible", "shape": "circle", "color": "#22c55e"},
        {"name": "booster", "role": "collectible", "shape": "diamond", "color": "#f59e0b"},
        {"name": "guide_tile", "role": "npc", "shape": "rectangle", "color": "#38bdf8"},
        {"name": "bonus_goal", "role": "collectible", "shape": "hexagon", "color": "#a855f7"},
    ),
    "educational": (
        {"name": "hint_badge", "role": "collectible", "shape": "diamond", "color": "#22c55e"},
        {"name": "challenge_card", "role": "obstacle", "shape": "rectangle", "color": "#f97316"},
        {"name": "teacher_note", "role": "npc", "shape": "square", "color": "#6366f1"},
        {"name": "milestone_star", "role": "collectible", "shape": "star", "color": "#facc15"},
        {"name": "timer_gate", "role": "obstacle", "shape": "triangle", "color": "#ef4444"},
    ),
    "funny": (
        {"name": "heckler", "role": "enemy", "shape": "triangle", "color": "#fb7185"},
        {"name": "prop_bonus", "role": "collectible", "shape": "circle", "color": "#22c55e"},
        {"name": "gag_trigger", "role": "npc", "shape": "diamond", "color": "#38bdf8"},
        {"name": "chaos_button", "role": "obstacle", "shape": "rectangle", "color": "#f97316"},
        {"name": "crowd_cheer", "role": "collectible", "shape": "star", "color": "#facc15"},
    ),
}


def _default_runtime_profile_id() -> str:
    profile = get_default_runtime_profile()
    if isinstance(profile, dict) and profile.get("id"):
        return normalize_runtime_profile_id(str(profile["id"]))
    raise PipelineExecutionError(
        "No enabled runtime profile is configured",
        stage="runtime_profile_select",
    )


STATE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "boot": ("boot", "init", "initialize", "loading"),
    "ready": ("ready", "menu", "start", "idle"),
    "playing": ("playing", "play", "running", "active"),
    "game_over": ("game_over", "gameover", "game over", "lose", "lost"),
    "level_complete": ("level_complete", "levelcomplete", "level complete", "completed", "solved", "success", "win", "cleared"),
}


INPUT_EVENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "touch": ("touchstart", "touchmove", "touchend", "touchcancel"),
    "pointer": ("pointerdown", "pointermove", "pointerup", "pointercancel"),
    "mouse": ("click", "mousedown", "mousemove", "mouseup"),
}


FORBIDDEN_API_PATTERNS: dict[str, str] = {
    "eval": r"\beval\s*\(",
    "Function": r"(?:\bnew\s+Function\s*\(|(?:window|globalThis|self|this)\.Function\s*\(|\bFunction\s*\()",
    "import": r"\bimport\s+",
    "require": r"\brequire\s*\(",
    "fetch": r"\bfetch\s*\(",
    "XMLHttpRequest": r"\bXMLHttpRequest\b",
    "WebSocket": r"\bWebSocket\b",
    "document.cookie": r"\bdocument\.cookie\b",
    "document.write": r"\bdocument\.write\b",
}
