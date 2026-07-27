"""V2 pipeline runner: spec-first execution flow for create/iterate."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..api.models import (
    GDD,
    GenerationTier,
    GameEntity,
    GameRuntimeContract,
    GameSpec,
    IterationType,
    IterateResponse,
    IterateV2Request,
    QACheckError,
    QAResult,
    RunPipelineResponse,
    RunPipelineV2Request,
    SourceBundleContext,
)
from ..config.settings import settings
from ..config.timeout_store import get_float as get_timeout_float, get_int as get_timeout_int
from ..services.llm_gateway import get_request_context
from ..services.task_memory import task_memory
from .code_preflight import CodePreflightValidator
from .code_generator import CodeGenerator
from .code_reviewer import CodeReviewer
from .dialogue_engine import (
    DialogueEngine,
    SlotExtractionFailure,
    _looks_like_educational_request,
)
from .game_designer import GameDesigner
from .mobile_layout import has_short_edge_scaling
from .pipeline_errors import PipelineExecutionError
from .pre_generation_validator import PreGenerationValidator
from .prompt_store import get_default_runtime_profile, require_prompt
from .qa_pipeline import QAPipeline, SYNTAX_REPAIR_FAMILY
from .quality_scorer import LLMReviewResult, QAStaticResult, QualityScorer, RuntimeQAResult
from .restart_entry import has_restart_entry
from .runtime_profile_ids import DEFAULT_RUNTIME_PROFILE_ID, normalize_runtime_profile_id
from .runtime_qa import run_runtime_qa
# P1.3 PR-11 wire-up: optional fire-and-forget scheduler for runtime_qa.
# Guarded import so pipeline runner still loads in stripped deploys.
try:  # pragma: no cover
    from .runtime_qa_scheduler import (  # type: ignore
        should_defer as _p1_should_defer,
        schedule_runtime_qa as _p1_schedule_runtime_qa,
    )
except Exception:  # pragma: no cover
    _p1_should_defer = None  # type: ignore
    _p1_schedule_runtime_qa = None  # type: ignore
# P2.1 telemetry — guarded import so runner still loads in stripped deploys.
try:  # pragma: no cover
    from .p2_telemetry import emit as _p2_emit  # type: ignore
except Exception:  # pragma: no cover
    def _p2_emit(event: str, **fields):  # type: ignore
        return None

# P2.3 inspiration-quality guard — guarded import so runner still loads in
# stripped deploys. commit_fun_score pops the pending decision recorded by
# code_generator and feeds the observed fun_score into the guard window.
try:  # pragma: no cover
    from .p2_inspiration_guard import commit_fun_score as _p2_guard_commit  # type: ignore
except Exception:  # pragma: no cover
    def _p2_guard_commit(key, fun_score):  # type: ignore
        return {}
from .section_patch import (
    PATCH_SECTION_BODY,
    PATCH_SECTION_SCRIPT,
    PATCH_SECTION_STYLE,
    apply_section_patches,
    build_patch_protocol,
    build_section_context,
    ensure_structured_section_markers,
    parse_patch_response,
    validate_patch_candidate,
)
from .scoring_loop import has_visible_scoring_loop
from .terminal_state import has_required_state_presence, has_terminal_state_transition
from .visual_pack_catalog import apply_visual_pack_defaults

logger = logging.getLogger(__name__)

DEFAULT_STAGE_TOTAL_ATTEMPTS = 3
DEFAULT_CREATE_FULL_GENERATION_ATTEMPTS = 3

# Quality-gate targeted patch repair (create pipeline): when a candidate
# narrowly misses the quality gate, try a section patch before spending a
# full regeneration round (240-300s LLM + full QA).
QUALITY_GATE_PATCH_STEP_KEY = "quality_gate.patch_fix"
QUALITY_GATE_PATCH_ATTEMPTS_PER_FAILURE = 1
QUALITY_GATE_PATCH_MAX_FINAL_SCORE_GAP = 1.5
QUALITY_GATE_PATCH_MAX_DIMENSION_GAP = 2.0

# Pseudo-progress heartbeat for the long logic_generate LLM call (create
# pipeline). The main generation call is non-streaming (hedged multi-route
# with truncation retry), so without this the progress bar sits at 60% for
# 240-300s. The heartbeat advances 60% -> 74% based on elapsed time vs. an
# expected p50-ish duration, monotonic within one generation attempt and
# capped so it never crosses into the contract_qa band (76%).
GENERATION_PROGRESS_HEARTBEAT_INTERVAL_S = 15.0
GENERATION_PROGRESS_HEARTBEAT_BASE_PCT = 60
GENERATION_PROGRESS_HEARTBEAT_MAX_PCT = 74
GENERATION_PROGRESS_HEARTBEAT_EXPECTED_DURATION_S = 150.0

# Iterate-path non-blocking quality assessment: a fast LLM review + quality
# scoring pass that runs after all iterate validations succeed. It has its
# own budget (independent of the pipeline timeout) so a slow review can never
# delay the iterate result for long; on timeout the assessment is abandoned.
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


class V2PipelineRunner:
    """Spec-first runner used by v2 create and iterate flows."""

    def __init__(self) -> None:
        self.dialogue_engine = DialogueEngine()
        self.game_designer = GameDesigner()
        self.code_generator = CodeGenerator(llm_mode=settings.LLM_MODE)
        self.qa_pipeline = QAPipeline()
        self.quality_scorer = QualityScorer()
        self.code_reviewer = CodeReviewer()
        self.pre_gen_validator = PreGenerationValidator()
        self.code_preflight = CodePreflightValidator()

    @staticmethod
    def _current_task_id() -> Optional[str]:
        return get_request_context().get("task_id")

    @staticmethod
    def _generation_tier_rank(tier: str) -> int:
        ranks = {
            "safe": 0,
            "standard": 1,
            "showcase": 2,
        }
        return ranks.get(str(tier or "standard").strip().lower(), 1)

    def _should_allow_runtime_qa_unavailable(self, spec: GameSpec) -> bool:
        if settings.RUNTIME_QA_REQUIRED:
            return False
        return CodeGenerator._resolve_generation_tier(spec) != "showcase"

    def _should_run_code_review(self, spec: GameSpec) -> bool:
        current_tier = CodeGenerator._resolve_generation_tier(spec)
        min_tier = str(getattr(settings, "LLM_CODE_REVIEW_MIN_TIER", "standard") or "standard")
        return self._generation_tier_rank(current_tier) >= self._generation_tier_rank(min_tier)

    @staticmethod
    def _is_structured_review_required(spec: GameSpec) -> bool:
        return CodeGenerator._resolve_generation_tier(spec) == "showcase"

    def _select_generation_budget_override(self, spec: GameSpec) -> str:
        return CodeGenerator._resolve_budget_profile(spec)

    @classmethod
    def _build_create_generation_attempt_plan(cls, budget_override: Optional[str]) -> tuple[str, ...]:
        normalized = str(budget_override or "standard").strip().lower() or "standard"
        plan_by_budget: dict[str, tuple[str, ...]] = {
            "safe": ("safe", "simple"),
            "simple": ("simple", "standard"),
            "standard": ("standard", "standard"),
            "complex": ("complex", "standard"),
            "showcase": ("showcase", "complex", "standard"),
        }
        return plan_by_budget.get(normalized, ("standard", "standard"))[:DEFAULT_CREATE_FULL_GENERATION_ATTEMPTS]

    @staticmethod
    def _quality_gate_thresholds(spec: GameSpec) -> dict[str, float]:
        generation_tier = CodeGenerator._resolve_generation_tier(spec)
        if generation_tier == "showcase":
            return {
                "fun_score": 8.0,
                "visual_polish_score": 8.0,
                "character_quality_score": 7.5,
                "abstract_character_floor": 7.0,
                "final_score": 8.5,
                "min_review_bonus": -1.5,
            }
        if generation_tier == "safe":
            return {
                "fun_score": 6.0,
                "visual_polish_score": 5.8,
                "character_quality_score": 5.2,
                "abstract_character_floor": 4.8,
                "final_score": 5.8,
            }
        return {
            "fun_score": 6.8,
            "visual_polish_score": 6.8,
            "character_quality_score": 6.4,
            "abstract_character_floor": 6.0,
            "final_score": 6.6,
        }

    @staticmethod
    def _is_character_driven_spec(spec: GameSpec) -> bool:
        searchable = " ".join(
            part
            for part in (
                spec.source_description,
                spec.intent_summary,
                getattr(spec.visual_style, "theme", ""),
                getattr(spec.visual_style, "art_style", ""),
                spec.reference_game,
                spec.reference_style,
                spec.signature_moment,
                spec.reward_loop,
                " ".join(spec.special_rules or []),
                " ".join(entity.name for entity in (spec.entities or [])),
            )
            if str(part or "").strip()
        ).lower()
        if any(
            marker in searchable
            for marker in (
                "character",
                "hero",
                "girl",
                "boy",
                "man",
                "woman",
                "fighter",
                "warrior",
                "soldier",
                "driver",
                "teacher",
                "student",
                "chef",
                "pirate",
                "ninja",
                "monster",
                "dragon",
                "cat",
                "dog",
                "animal",
                "人物",
                "角色",
                "主角",
                "怪物",
                "动物",
                "老师",
                "学生",
            )
        ):
            return True
        if any(entity.role in {"enemy", "npc"} for entity in (spec.entities or [])):
            return True
        return False

    @staticmethod
    def _profile_selection_text(spec: GameSpec) -> str:
        return " ".join(
            part
            for part in (
                spec.game_type,
                spec.source_description,
                spec.intent_summary,
                " ".join(spec.special_rules or []),
                spec.reference_game,
                " ".join(entity.name for entity in (spec.entities or [])),
            )
            if str(part or "").strip()
        ).lower()

    @classmethod
    def _looks_like_action_runtime_request(cls, spec: GameSpec) -> bool:
        searchable = cls._profile_selection_text(spec)
        return any(marker in searchable for marker in ACTION_RUNTIME_MARKERS)

    @classmethod
    def _looks_like_puzzle_runtime_request(cls, spec: GameSpec) -> bool:
        searchable = cls._profile_selection_text(spec)
        return any(marker in searchable for marker in PUZZLE_RUNTIME_MARKERS)

    @staticmethod
    def _looks_like_quiz_show_runtime_request(spec: GameSpec) -> bool:
        searchable = V2PipelineRunner._profile_selection_text(spec)
        return any(
            marker in searchable
            for marker in (
                "quiz show",
                "game show",
                "trivia show",
                "millionaire",
                "host",
                "buzzer",
                "streak",
                "combo",
                "stage",
                "spotlight",
                "答题秀",
                "答题节目",
                "节目答题",
                "综艺答题",
                "综艺节目",
                "舞台秀",
                "舞台答题",
                "主持人",
                "连击",
                "连胜",
                "节奏感",
                "演出效果",
            )
        )

    @classmethod
    def _preferred_quiz_show_profile(cls, spec: GameSpec) -> str:
        searchable = cls._profile_selection_text(spec)
        if any(marker in searchable for marker in ("combo", "streak", "连击", "连胜")):
            return "tap_challenge_combo"
        return "tap_challenge_timing"

    @staticmethod
    def _merge_profile_candidates(*candidate_groups: tuple[str, ...] | list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for group in candidate_groups:
            for candidate in group:
                normalized = normalize_runtime_profile_id(candidate)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                ordered.append(normalized)
        return ordered

    @classmethod
    def _quality_gate_errors(
        cls,
        spec: GameSpec,
        review: LLMReviewResult,
        quality: Any,
        *,
        review_required: bool = False,
    ) -> list[str]:
        if review_required and not getattr(review, "ran", False):
            return [
                "Structured code review did not return a valid quality assessment.",
            ]
        if not getattr(review, "ran", False):
            return []

        thresholds = cls._quality_gate_thresholds(spec)
        character_threshold = (
            thresholds["character_quality_score"]
            if cls._is_character_driven_spec(spec)
            else thresholds["abstract_character_floor"]
        )
        errors: list[str] = []

        if not review.is_complete_game:
            errors.append("Return a complete, polished game instead of an incomplete or placeholder output.")
        if not review.has_real_gameplay:
            errors.append("Strengthen the moment-to-moment gameplay so the result has a real playable loop.")
        if review.fun_score < thresholds["fun_score"]:
            errors.append(
                f"Raise gameplay excitement and payoff: fun_score {review.fun_score:.1f} is below the required {thresholds['fun_score']:.1f}."
            )
        if review.visual_polish_score < thresholds["visual_polish_score"]:
            errors.append(
                f"Raise visual polish: visual_polish_score {review.visual_polish_score:.1f} is below the required {thresholds['visual_polish_score']:.1f}."
            )
        if review.character_quality_score < character_threshold:
            errors.append(
                f"Raise character quality: character_quality_score {review.character_quality_score:.1f} is below the required {character_threshold:.1f}."
            )
        if float(getattr(quality, "final_score", 0.0) or 0.0) < thresholds["final_score"]:
            errors.append(
                f"Raise the overall quality score from {float(getattr(quality, 'final_score', 0.0) or 0.0):.1f} to at least {thresholds['final_score']:.1f}."
            )
        min_review_bonus = thresholds.get("min_review_bonus")
        review_bonus = float(getattr(quality, "review_bonus", 0.0) or 0.0)
        if min_review_bonus is not None and review_bonus < min_review_bonus:
            errors.append(
                f"Reduce the structured code review penalty: review_bonus {review_bonus:.1f} is below the allowed {min_review_bonus:.1f}."
            )
        return errors

    @classmethod
    def _can_accept_showcase_near_miss(
        cls,
        spec: GameSpec,
        review: LLMReviewResult,
        quality: Any,
        errors: list[str],
    ) -> bool:
        if CodeGenerator._resolve_generation_tier(spec) != "showcase":
            return False
        if not getattr(review, "ran", False):
            return False
        if not getattr(review, "is_complete_game", False):
            return False
        if not getattr(review, "has_real_gameplay", False):
            return False

        final_score = float(getattr(quality, "final_score", 0.0) or 0.0)
        review_bonus = float(getattr(quality, "review_bonus", 0.0) or 0.0)
        if final_score < 8.0 or review_bonus < -2.0:
            return False
        if float(getattr(review, "fun_score", 0.0) or 0.0) < 7.5:
            return False
        if float(getattr(review, "visual_polish_score", 0.0) or 0.0) < 7.5:
            return False

        hard_error_markers = (
            "complete, polished game",
            "real playable loop",
            "structured code review did not return",
        )
        normalized_errors = " ".join(str(error or "").lower() for error in errors)
        return not any(marker in normalized_errors for marker in hard_error_markers)

    @classmethod
    def _build_review_quality_guidance(
        cls,
        spec: GameSpec,
        review: LLMReviewResult,
        quality: Any,
        errors: list[str],
    ) -> str:
        thresholds = cls._quality_gate_thresholds(spec)
        character_driven = cls._is_character_driven_spec(spec)
        lines = [
            "QUALITY AND PRESENTATION CORRECTIONS (MUST FIX BEFORE RETURNING HTML):",
            f"- The previous candidate missed the quality gate for the {CodeGenerator._resolve_generation_tier(spec)} tier.",
        ]
        for error in errors[:6]:
            lines.append(f"- Fix this explicitly: {error}")
        if review.fun_score < thresholds["fun_score"]:
            lines.append(
                "- Strengthen the first 5-10 seconds with a clearer hook, faster reward loop, visible escalation, and a more satisfying payoff."
            )
        if review.visual_polish_score < thresholds["visual_polish_score"]:
            lines.append(
                "- Upgrade presentation with layered backgrounds/foregrounds, controlled palette choices, readable depth separation, and richer impact feedback."
            )
        if review.character_quality_score < (
            thresholds["character_quality_score"] if character_driven else thresholds["abstract_character_floor"]
        ):
            if character_driven:
                lines.append(
                    "- Make the hero, rival, creature, or NPC feel believable and intentionally designed: stronger silhouette, consistent proportions, expressive pose/facing, and reactive animation detail."
                )
            else:
                lines.append(
                    "- Even if the design is abstract, the main pieces and props must look premium and intentional rather than like stock rectangles or circles."
                )
        if float(getattr(quality, "final_score", 0.0) or 0.0) < thresholds["final_score"]:
            lines.append(
                "- Improve gameplay payoff and visual finish together; a technically valid prototype is not enough to pass."
            )
        for issue in (review.issues or [])[:4]:
            normalized_issue = str(issue or "").strip()
            if normalized_issue:
                lines.append(f"- Reviewer issue to address: {normalized_issue}")
        return "\n".join(lines)

    @classmethod
    def _quality_patch_character_threshold(cls, spec: GameSpec) -> float:
        thresholds = cls._quality_gate_thresholds(spec)
        return (
            thresholds["character_quality_score"]
            if cls._is_character_driven_spec(spec)
            else thresholds["abstract_character_floor"]
        )

    @classmethod
    def _should_attempt_quality_patch_repair(
        cls,
        spec: GameSpec,
        review: LLMReviewResult,
        quality: Any,
    ) -> bool:
        """Conservative near-miss detector for quality-gate patch repair.

        Only candidates without structural defects (complete game with real
        gameplay) whose scores sit close to the tier thresholds qualify;
        anything else keeps the existing full-regeneration behavior.
        """
        if not getattr(review, "ran", False):
            return False
        if not getattr(review, "is_complete_game", False):
            return False
        if not getattr(review, "has_real_gameplay", False):
            return False

        thresholds = cls._quality_gate_thresholds(spec)
        final_score = float(getattr(quality, "final_score", 0.0) or 0.0)
        if thresholds["final_score"] - final_score > QUALITY_GATE_PATCH_MAX_FINAL_SCORE_GAP:
            return False

        dimension_gaps = (
            thresholds["fun_score"] - float(getattr(review, "fun_score", 0.0) or 0.0),
            thresholds["visual_polish_score"] - float(getattr(review, "visual_polish_score", 0.0) or 0.0),
            cls._quality_patch_character_threshold(spec)
            - float(getattr(review, "character_quality_score", 0.0) or 0.0),
        )
        return all(gap <= QUALITY_GATE_PATCH_MAX_DIMENSION_GAP for gap in dimension_gaps)

    @classmethod
    def _quality_patch_allowed_sections(
        cls,
        spec: GameSpec,
        review: LLMReviewResult,
    ) -> tuple[str, ...]:
        """Map failing quality dimensions onto patchable sections.

        fun/gameplay misses stay SCRIPT-only (conservative: no BODY);
        visual polish or character quality misses also unlock STYLE. When
        only the aggregate final_score missed, allow STYLE + SCRIPT so the
        patch can lift presentation and gameplay payoff together.
        """
        thresholds = cls._quality_gate_thresholds(spec)
        fun_failed = float(getattr(review, "fun_score", 0.0) or 0.0) < thresholds["fun_score"]
        visual_failed = (
            float(getattr(review, "visual_polish_score", 0.0) or 0.0) < thresholds["visual_polish_score"]
        )
        character_failed = (
            float(getattr(review, "character_quality_score", 0.0) or 0.0)
            < cls._quality_patch_character_threshold(spec)
        )
        aggregate_only = not (fun_failed or visual_failed or character_failed)
        if visual_failed or character_failed or aggregate_only:
            return (PATCH_SECTION_STYLE, PATCH_SECTION_SCRIPT)
        return (PATCH_SECTION_SCRIPT,)

    async def _request_quality_gate_patch_text(
        self,
        *,
        prompt: str,
        spec: GameSpec,
        prompt_bundle_snapshot: Optional[dict[str, Any]],
    ) -> str:
        generator = self.code_generator
        step_key = QUALITY_GATE_PATCH_STEP_KEY
        request_timeout_s = CodeGenerator._resolve_step_request_timeout_s(
            step_key,
            spec=spec,
            default_timeout_s=CodeGenerator._generation_request_timeout_budget_s(spec),
        )
        overall_timeout_s = CodeGenerator._resolve_step_overall_timeout_s(
            step_key,
            spec=spec,
            request_timeout_s=request_timeout_s,
            default_timeout_s=CodeGenerator._generation_overall_timeout_budget_s(spec),
        )
        token_budget = CodeGenerator._select_token_budget(spec)
        # prefer_fast intentionally left off: quality repair needs the
        # primary model, not the fast lane.
        return await generator._client.complete_with_truncation_retry(
            max_tokens=token_budget,
            system=generator._build_system_prompt(prompt_bundle_snapshot, spec=spec),
            messages=[{"role": "user", "content": prompt}],
            step_key=step_key,
            stage="code_review",
            request_timeout_s=request_timeout_s,
            overall_timeout_s=overall_timeout_s,
            allow_provider_fallback=True,
            response_size_hint=CodeGenerator._response_size_hint_from_budget(token_budget),
            context_scope="request",
            compression_policy="iteration_rewrite",
            truncation_retry_attempts=1,
            truncation_retry_increment=2048,
            truncation_retry_max_tokens=CodeGenerator._select_truncation_retry_cap(spec),
            timeout_retry_attempts=0,
            provider_retry_attempts=1,
            provider_retry_on_timeout_errors=False,
            provider_retry_base_delay_s=1,
            provider_retry_max_delay_s=2,
            hedge_provider_fallback_after_s=CodeGenerator._generation_provider_hedge_delay_s(spec),
        )

    async def _attempt_quality_gate_patch_repair(
        self,
        *,
        request: RunPipelineV2Request,
        spec: GameSpec,
        runtime_contract: GameRuntimeContract,
        code: str,
        generation_strategy: str,
        base_retries: int,
        review: LLMReviewResult,
        quality: Any,
        quality_gate_errors: list[str],
        previous_runtime_qa: Any,
        progress_cb: ProgressCallback,
        allow_runtime_qa_unavailable: bool,
    ) -> Optional[_QualityGatePatchOutcome]:
        for patch_attempt in range(1, QUALITY_GATE_PATCH_ATTEMPTS_PER_FAILURE + 1):
            outcome = await self._attempt_quality_gate_patch_once(
                request=request,
                spec=spec,
                runtime_contract=runtime_contract,
                code=code,
                generation_strategy=generation_strategy,
                base_retries=base_retries,
                review=review,
                quality=quality,
                quality_gate_errors=quality_gate_errors,
                previous_runtime_qa=previous_runtime_qa,
                progress_cb=progress_cb,
                allow_runtime_qa_unavailable=allow_runtime_qa_unavailable,
                patch_attempt=patch_attempt,
            )
            if outcome is not None:
                return outcome
        return None

    async def _attempt_quality_gate_patch_once(
        self,
        *,
        request: RunPipelineV2Request,
        spec: GameSpec,
        runtime_contract: GameRuntimeContract,
        code: str,
        generation_strategy: str,
        base_retries: int,
        review: LLMReviewResult,
        quality: Any,
        quality_gate_errors: list[str],
        previous_runtime_qa: Any,
        progress_cb: ProgressCallback,
        allow_runtime_qa_unavailable: bool,
        patch_attempt: int,
    ) -> Optional[_QualityGatePatchOutcome]:
        allowed_sections = self._quality_patch_allowed_sections(spec, review)
        self._notify(
            progress_cb,
            "code_review",
            95,
            "Applying targeted quality fixes",
            {
                "gameId": request.game_id,
                "userId": request.user_id,
                "runtimeProfile": runtime_contract.runtime_profile,
                "allowedSections": list(allowed_sections),
                "patchAttempt": patch_attempt,
                "maxPatchAttempts": QUALITY_GATE_PATCH_ATTEMPTS_PER_FAILURE,
            },
        )
        _p2_emit(
            "quality_gate_patch_attempt",
            tier=getattr(getattr(spec, "generation_tier", None), "value", None),
            game_type=getattr(spec, "game_type", None),
            sections=",".join(allowed_sections),
            final_score=float(getattr(quality, "final_score", 0.0) or 0.0),
        )
        try:
            normalized_code = ensure_structured_section_markers(code)
            prompt = "\n\n".join(
                part
                for part in [
                    build_patch_protocol(allowed_sections, task_label="quality gate repair"),
                    self._build_review_quality_guidance(spec, review, quality, quality_gate_errors),
                    build_section_context(normalized_code, allowed_sections),
                ]
                if part
            )
            text = await self._request_quality_gate_patch_text(
                prompt=prompt,
                spec=spec,
                prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
            )
            patches, full_html = parse_patch_response(text, allowed_sections=allowed_sections)
            if patches:
                candidate = apply_section_patches(normalized_code, patches)
                touched_sections = {patch.section for patch in patches}
            elif full_html:
                candidate = ensure_structured_section_markers(full_html)
                touched_sections = set(allowed_sections)
            else:
                raise RuntimeError("patch_parse_empty")

            validation_errors = validate_patch_candidate(
                normalized_code,
                candidate,
                allowed_sections=allowed_sections,
            )
            if validation_errors:
                raise RuntimeError("patch_validation_failed:" + ",".join(validation_errors))

            static_check = self.qa_pipeline.check(candidate)
            if not static_check.passed:
                raise RuntimeError(
                    "patch_static_qa_failed:"
                    + "; ".join(error.message for error in static_check.errors[:3])
                )

            runtime_qa_reran = False
            runtime_retries = 0
            qa_warnings: list[dict[str, Any]] = []
            runtime_qa = previous_runtime_qa
            if touched_sections & {PATCH_SECTION_SCRIPT, PATCH_SECTION_BODY}:
                # SCRIPT/BODY changes can alter behavior; STYLE-only patches
                # keep the previous runtime QA verdict.
                candidate, runtime_qa, runtime_retries, qa_warnings = await self._run_runtime_qa_loop(
                    code=candidate,
                    runtime_contract=runtime_contract,
                    progress_cb=progress_cb,
                    game_id=request.game_id,
                    user_id=request.user_id,
                    allow_runtime_qa_unavailable=allow_runtime_qa_unavailable,
                    tier=getattr(getattr(spec, "generation_tier", None), "value", None)
                        or str(getattr(spec, "generation_tier", "") or ""),
                    operation="create",
                )
                runtime_qa_reran = True

            patched_review = await self.code_reviewer.review(candidate)
            patched_quality = self.quality_scorer.compute(
                static=QAStaticResult(
                    passed=static_check.passed,
                    error_count=len(static_check.errors),
                    warning_count=len(static_check.warnings),
                    retries=base_retries + runtime_retries,
                    strategy=generation_strategy,
                    code_size_bytes=len(candidate.encode("utf-8")),
                ),
                runtime=runtime_qa,
                review=patched_review,
                code=candidate,
            )
            remaining_errors = self._quality_gate_errors(
                spec,
                patched_review,
                patched_quality,
                review_required=self._is_structured_review_required(spec),
            )
            if remaining_errors:
                raise RuntimeError(
                    "patch_quality_gate_still_failing:" + "; ".join(remaining_errors[:3])
                )
        except Exception as exc:  # noqa: BLE001 - any failure falls back to regeneration
            reason = str(exc)[:300]
            logger.warning(
                "Quality-gate patch repair attempt %s/%s for game %s failed; falling back to full regeneration: %s",
                patch_attempt,
                QUALITY_GATE_PATCH_ATTEMPTS_PER_FAILURE,
                request.game_id,
                reason,
            )
            _p2_emit(
                "quality_gate_patch_failed",
                tier=getattr(getattr(spec, "generation_tier", None), "value", None),
                game_type=getattr(spec, "game_type", None),
                reason=reason,
            )
            return None

        logger.info(
            "Quality-gate patch repair for game %s passed the quality gate (sections=%s)",
            request.game_id,
            ",".join(sorted(touched_sections)),
        )
        _p2_emit(
            "quality_gate_patch_success",
            tier=getattr(getattr(spec, "generation_tier", None), "value", None),
            game_type=getattr(spec, "game_type", None),
            sections=",".join(sorted(touched_sections)),
            final_score=float(getattr(patched_quality, "final_score", 0.0) or 0.0),
        )
        self._notify(
            progress_cb,
            "code_review",
            96,
            "Targeted quality fixes passed the quality gate",
            {
                "gameId": request.game_id,
                "userId": request.user_id,
                "runtimeProfile": runtime_contract.runtime_profile,
                "patchedSections": sorted(touched_sections),
            },
        )
        return _QualityGatePatchOutcome(
            code=candidate,
            review=patched_review,
            quality=patched_quality,
            runtime_qa=runtime_qa,
            runtime_qa_reran=runtime_qa_reran,
            runtime_retries=runtime_retries,
            qa_warnings=qa_warnings,
        )

    @staticmethod
    def _normalize_provider_exclusions(excluded_provider_ids: Optional[list[str]]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_provider_id in excluded_provider_ids or []:
            provider_id = str(raw_provider_id or "").strip()
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            normalized.append(provider_id)
        return normalized

    @classmethod
    def _advance_generation_provider_exclusions(
        cls,
        route_snapshot: Optional[dict[str, Any]],
        excluded_provider_ids: Optional[list[str]],
    ) -> list[str] | None:
        snapshot = route_snapshot or {}
        current_provider_id = str(snapshot.get("provider_id") or "").strip()
        active_exclusions = cls._normalize_provider_exclusions(excluded_provider_ids)
        ordered_candidates = cls._normalize_provider_exclusions([
            current_provider_id,
            *(snapshot.get("fallback_provider_ids") or []),
        ])
        if not current_provider_id or current_provider_id in active_exclusions:
            return None
        remaining_candidates = [
            provider_id
            for provider_id in ordered_candidates
            if provider_id not in active_exclusions and provider_id != current_provider_id
        ]
        if not remaining_candidates:
            return None
        return cls._normalize_provider_exclusions([*active_exclusions, current_provider_id])

    @staticmethod
    def _build_quality_regeneration_guidance(
        *,
        stage: str,
        message: Optional[str] = None,
        errors: Optional[list[QACheckError]] = None,
    ) -> str:
        issue_lines: list[str] = []
        seen: set[str] = set()
        for error in errors or []:
            normalized = str(getattr(error, "message", "") or "").strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                issue_lines.append(normalized)
        fallback_message = str(message or "").strip()
        if not issue_lines and fallback_message:
            issue_lines.append(fallback_message)
        if not issue_lines:
            return ""
        lines = [
            "QUALITY GATE CORRECTIONS (MUST FIX BEFORE RETURNING HTML):",
            f"- The previous candidate failed during {stage}.",
        ]
        for issue in issue_lines[:6]:
            lines.append(f"- Fix this explicitly in code: {issue}")
        normalized_issue_blob = "\n".join(issue_lines).lower()
        extra_recipes: list[str] = []

        def _append_recipe(text: str) -> None:
            if text not in extra_recipes:
                extra_recipes.append(text)

        if "short-edge ui scaling" in normalized_issue_blob:
            _append_recipe(
                "- In init/resize, set non-zero canvas.width/canvas.height and declare short-edge layout aliases such as "
                "`viewWidth`, `viewHeight`, `scaleX`, `scaleY`, and `uiScale` before any HUD or draw code runs."
            )
        if "landscape-first short-edge ui scaling" in normalized_issue_blob:
            _append_recipe(
                "- For landscape-first contracts, declare `const REF_W = 640; const REF_H = 360;` and compute "
                "`scaleX = canvas.width / REF_W`, `scaleY = canvas.height / REF_H`, `uiScale = Math.min(scaleX, scaleY)` "
                "before HUD/gameplay layout. Keep gameplay coordinates and HUD anchoring aligned to that landscape reference, "
                "and also declare `const viewWidth = canvas.width; const viewHeight = canvas.height;` inside the same resize/init path."
            )
        if "portrait-first short-edge ui scaling" in normalized_issue_blob:
            _append_recipe(
                "- For portrait-first contracts, declare `const REF_W = 360; const REF_H = 640;` and compute "
                "`scaleX = canvas.width / REF_W`, `scaleY = canvas.height / REF_H`, `uiScale = Math.min(scaleX, scaleY)` "
                "before HUD/gameplay layout. Keep gameplay coordinates and HUD anchoring aligned to that portrait reference, "
                "and also declare `const viewWidth = canvas.width; const viewHeight = canvas.height;` inside the same resize/init path."
            )
        if "restart entry point" in normalized_issue_blob:
            _append_recipe(
                "- Provide an explicit restart entry such as `restartGame()`, `resetGame()`, or `restart()` and wire it to "
                "the visible settlement/restart UI."
            )
        if (
            "primary touch or pointer gameplay handlers" in normalized_issue_blob
            or "no registered user input handlers" in normalized_issue_blob
        ):
            _append_recipe(
                "- Register gameplay pointer/touch handlers on the main canvas or primary play surface during boot; do not "
                "wait for a later overlay flow before binding real gameplay input."
            )
        if "no visible state change after user interaction" in normalized_issue_blob:
            _append_recipe(
                "- The first tap or pointerdown on the main play surface must immediately call `startGame()` / enter "
                "`playing` or mutate visible HUD/canvas state so runtime QA can observe a state change without using an "
                "overlay-only start button."
            )
        if "returns unless the game is already in `playing`" in normalized_issue_blob or "returns unless the game is already in 'playing'" in normalized_issue_blob:
            _append_recipe(
                "- Rewrite the primary input handler so boot/ready input can call `startGame()` or switch state into "
                "`playing` before any early return; do not gate the first gameplay interaction behind `if (state !== 'playing') return`."
            )
        if "generatebackgroundlayers" in normalized_issue_blob:
            _append_recipe(
                "- Declare `generateBackgroundLayers()` before the first call, or inline the background layer array creation "
                "during top-level init."
            )
        if "initialized as null" in normalized_issue_blob and "ctx" in normalized_issue_blob:
            _append_recipe(
                "- Do not keep `ctx` as `null` while boot, resize, or the main loop can run. Acquire the 2D context during "
                "top-level init, store it in a non-null variable, and guard any fallback path before calling `ctx.*`."
            )
        if "cannot read properties of undefined" in normalized_issue_blob and any(
            axis_token in normalized_issue_blob for axis_token in ("reading 'x'", 'reading "x"', "reading 'y'", 'reading "y"')
        ):
            _append_recipe(
                "- Never read `.x` / `.y` from optional runtime objects before they exist. Initialize moving entities, touch state, "
                "drag state, and targets to safe defaults during boot, or guard with `if (!obj) return;` before reading coordinates."
            )
        if any(alias in normalized_issue_blob for alias in ("viewwidth", "viewheight", "scalex", "scaley", "uiscale")):
            _append_recipe(
                "- If render/layout code uses `viewWidth`, `viewHeight`, `scaleX`, `scaleY`, or `uiScale`, declare those "
                "aliases from `canvas.width` / `canvas.height` inside init/resize before the first render."
            )
        if "lanex" in normalized_issue_blob:
            _append_recipe(
                "- For lane games, compute lane coordinates from a declared helper like `function laneX(index) { ... }` or "
                "a lane-position array; never call undefined helpers such as `player.laneX()`."
            )
        if "dot" in normalized_issue_blob:
            _append_recipe(
                "- When rendering dots or particles, declare the current alias in the same scope, for example "
                "`const dot = dots[i];`, before reading `dot.x`, `dot.y`, or `dot.alpha`."
            )
            _append_recipe(
                "- Prefer a concrete loop scaffold such as `for (let i = 0; i < dots.length; i += 1) { const dot = dots[i]; if (!dot) continue; ... }` "
                "and never read `dot.*` outside the block that declares `const dot`."
            )
        if any(token in normalized_issue_blob for token in ("touchx", "clientx", "touches[0]", "changedtouches[0]")):
            _append_recipe(
                "- Guard touch extraction before reading `clientX` / `clientY`, for example "
                "`const point = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : e)); "
                "if (!point || point.clientX == null || point.clientY == null) return;`, and reuse that exact helper in touchstart/touchmove/touchend."
            )
            _append_recipe(
                "- Prefer a dedicated helper such as "
                "`function getInputPoint(e) { const point = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : e)); "
                "if (!point || point.clientX == null || point.clientY == null) return null; return { x: point.clientX, y: point.clientY }; }` "
                "and never read `e.touches[0]` or `e.changedTouches[0]` directly anywhere else."
            )
        if "cannot read properties of undefined" in normalized_issue_blob and "grid" in normalized_issue_blob:
            _append_recipe(
                "- Replace raw nested grid reads with a guarded helper such as `const rowBucket = grid[row]; const cell = "
                "rowBucket && rowBucket[col]; if (!cell) return;` before reading cell properties."
            )
        if "grid[row][col]" in normalized_issue_blob or "unsafe_nested_grid_read" in normalized_issue_blob:
            _append_recipe(
                "- Define `function getCell(grid, row, col) { const rowBucket = grid[row]; return rowBucket ? rowBucket[col] : null; }` "
                "before any match, gravity, hint, or render logic, and route every `cell.type`, `cell.fruit`, `cell.anim`, `cell.animProgress`, or neighbor read through "
                "`const cell = getCell(grid, row, col); if (!cell) continue;`."
            )
        lines.extend(extra_recipes)
        lines.append(
            "- Regenerate the full HTML so these issues are resolved in executable code, not comments, placeholders, or implied behavior."
        )
        return "\n".join(lines)

    async def _remember_spec(self, spec: Optional[GameSpec]) -> None:
        await task_memory.remember_spec(self._current_task_id(), spec)

    async def _remember_runtime_contract(
        self,
        *,
        runtime_profile: Optional[str],
        contract: Optional[GameRuntimeContract],
    ) -> None:
        await task_memory.remember_runtime_contract(
            self._current_task_id(),
            runtime_profile=runtime_profile,
            contract=contract,
        )

    async def _remember_code(self, code: Optional[str], *, label: str) -> None:
        await task_memory.remember_code(self._current_task_id(), code, label=label)

    async def run(
        self,
        request: RunPipelineV2Request,
        progress_cb: ProgressCallback = None,
        timeout_s: Optional[int] = None,
    ) -> RunPipelineResponse:
        effective_timeout_s = (
            int(timeout_s)
            if timeout_s is not None
            else int(request.timeout_s)
            if request.timeout_s is not None
            else get_timeout_int(
                "timeout.pipeline.default_s",
                1200,
                min_value=30,
                max_value=3600,
            )
        )
        stage_context: dict[str, str] = {"stage": "spec_build"}
        task_id_for_timing = self._current_task_id()
        pipeline_status = "unknown"
        try:
            result = await asyncio.wait_for(
                self._run_create_impl(request, progress_cb, stage_context),
                timeout=effective_timeout_s,
            )
            pipeline_status = "success"
            return result
        except asyncio.TimeoutError as exc:
            pipeline_status = "timeout"
            raise PipelineExecutionError(
                f"Pipeline timed out during {stage_context.get('stage', 'failed')} after {effective_timeout_s}s",
                stage=stage_context.get("stage", "failed"),
            ) from exc
        except Exception:
            pipeline_status = "error"
            raise
        finally:
            # PR-06: emit structured stage-duration breakdown regardless of outcome.
            self._flush_stage_timings(task_id_for_timing, status=pipeline_status)

    async def iterate(
        self,
        request: IterateV2Request,
        progress_cb: ProgressCallback = None,
        timeout_s: Optional[int] = None,
    ) -> IterateResponse:
        effective_timeout_s = (
            int(timeout_s)
            if timeout_s is not None
            else int(request.timeout_s)
            if request.timeout_s is not None
            else get_timeout_int(
                "timeout.pipeline.default_s",
                1200,
                min_value=30,
                max_value=3600,
            )
        )
        stage_context: dict[str, str] = {"stage": "spec_build"}
        task_id_for_timing = self._current_task_id()
        pipeline_status = "unknown"
        try:
            result = await asyncio.wait_for(
                self._run_iterate_impl(request, progress_cb, stage_context),
                timeout=effective_timeout_s,
            )
            pipeline_status = "success"
            return result
        except asyncio.TimeoutError as exc:
            pipeline_status = "timeout"
            raise PipelineExecutionError(
                f"Iteration timed out during {stage_context.get('stage', 'failed')} after {effective_timeout_s}s",
                stage=stage_context.get("stage", "failed"),
            ) from exc
        except Exception:
            pipeline_status = "error"
            raise
        finally:
            # PR-06: emit structured stage-duration breakdown regardless of outcome.
            self._flush_stage_timings(task_id_for_timing, status=pipeline_status)

    async def _run_create_impl(
        self,
        request: RunPipelineV2Request,
        progress_cb: ProgressCallback,
        stage_context: dict[str, str],
    ) -> RunPipelineResponse:
        start_ms = int(time.time() * 1000)
        # P1.2 GAP-3: reset per-request fun_score so PR-10's filter_fixable
        # starts from a clean slate instead of inheriting a prior request's
        # score when the same pipeline instance serves multiple games.
        try:
            self.qa_pipeline._last_fun_score = None
        except AttributeError:
            pass
        self._notify(progress_cb, "spec_build", 15, "Building structured game spec", {
            "gameId": request.game_id,
            "userId": request.user_id,
        })
        stage_context["stage"] = "spec_build"
        spec = await self._build_create_spec(request)
        await self._remember_spec(spec)
        await task_memory.append_decision(
            self._current_task_id(),
            f"Built create spec with game_type={spec.game_type}",
        )

        self._notify(progress_cb, "runtime_profile_select", 30, "Selecting runtime profile", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "gameType": spec.game_type,
        })
        stage_context["stage"] = "runtime_profile_select"
        runtime_profile = self._select_runtime_profile(
            spec,
            request.runtime_contract.runtime_profile,
            variation_seed=request.game_id,
        )
        await task_memory.append_decision(
            self._current_task_id(),
            f"Selected runtime profile {runtime_profile}",
        )

        self._notify(progress_cb, "contract_compose", 40, "Composing runtime contract", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
        })
        stage_context["stage"] = "contract_compose"
        runtime_contract = self._compose_runtime_contract(
            base_contract=request.runtime_contract,
            spec=spec,
            runtime_profile=runtime_profile,
            entrypoint="create",
        )
        await self._remember_runtime_contract(runtime_profile=runtime_profile, contract=runtime_contract)
        gdd = await self._build_gdd(spec, runtime_contract)

        initial_budget_override = self._select_generation_budget_override(spec)

        pre_issues = self.pre_gen_validator.validate(spec, gdd, runtime_contract)
        if pre_issues:
            logger.warning("Pre-generation issues detected: %s", pre_issues)
            spec, gdd = self.pre_gen_validator.auto_fix(spec, gdd, pre_issues)

        self._notify(progress_cb, "logic_generate", 60, "Generating runtime-bound game logic", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
            "budgetProfile": initial_budget_override,
        })
        stage_context["stage"] = "logic_generate"
        allow_runtime_qa_unavailable = self._should_allow_runtime_qa_unavailable(spec)
        attempt_plan = list(self._build_create_generation_attempt_plan(initial_budget_override))
        provider_exclusions: list[str] = []
        generated = None
        last_route_snapshot: Optional[dict[str, Any]] = None
        qa_result = None
        runtime_qa = None
        runtime_retries = 0
        qa_warnings: list[dict[str, Any]] = []
        review = LLMReviewResult(ran=False)
        quality = None
        code_bytes = 0
        last_quality_exc: Exception | None = None
        generation_guidance: Optional[str] = None
        extra_preflight_retry_granted = False

        quality_attempt = 0
        while quality_attempt < len(attempt_plan):
            quality_attempt += 1
            attempt_budget = attempt_plan[quality_attempt - 1]
            generated = None
            try:
                heartbeat_task = self._start_generation_progress_heartbeat(
                    progress_cb,
                    game_id=request.game_id,
                    user_id=request.user_id,
                    runtime_profile=runtime_profile,
                    attempt=quality_attempt,
                    max_attempts=len(attempt_plan),
                )
                try:
                    generated, preflight_issues = await self._generate_create_code(
                        request,
                        spec,
                        gdd,
                        runtime_contract,
                        budget_override=attempt_budget,
                        excluded_provider_ids=provider_exclusions,
                        generation_guidance=generation_guidance,
                    )
                finally:
                    await self._stop_generation_progress_heartbeat(heartbeat_task)
                last_route_snapshot = generated.route_snapshot if generated is not None else None
                await self._remember_code(generated.html_code, label=f"generated_candidate_{quality_attempt}")
                if preflight_issues:
                    generation_guidance = self.code_preflight.render_guidance(preflight_issues)
                    last_quality_exc = PipelineExecutionError(
                        "Generated code failed preflight: "
                        + "; ".join(issue.message for issue in preflight_issues),
                        stage="logic_generate",
                        retry_count=max(0, quality_attempt - 1),
                        failure_family="code_generation",
                    )
                    next_provider_exclusions = self._advance_generation_provider_exclusions(
                        last_route_snapshot,
                        provider_exclusions,
                    )
                    if next_provider_exclusions is not None:
                        provider_exclusions = next_provider_exclusions
                    if quality_attempt >= len(attempt_plan):
                        if not extra_preflight_retry_granted and len(attempt_plan) < DEFAULT_STAGE_TOTAL_ATTEMPTS:
                            attempt_plan.append(attempt_budget)
                            extra_preflight_retry_granted = True
                        else:
                            raise last_quality_exc
                    self._notify(
                        progress_cb,
                        "logic_generate",
                        66,
                        "Regenerating with consolidated preflight guidance",
                        {
                            "gameId": request.game_id,
                            "userId": request.user_id,
                            "runtimeProfile": runtime_profile,
                            "attempt": quality_attempt,
                            "maxAttempts": len(attempt_plan),
                            "failedProviderId": (last_route_snapshot or {}).get("provider_id"),
                        },
                    )
                    await asyncio.sleep(min(quality_attempt, 2))
                    continue

                review_requested = self._should_run_code_review(spec)
                # Run the LLM code review concurrently with runtime simulation
                # QA: both consume the same post-contract-QA HTML. The review
                # task is stashed in concurrent_review; if runtime QA fails,
                # the review is cancelled and its result discarded so error
                # priority stays identical to the serial flow.
                concurrent_review: dict[str, Any] = {}
                try:
                    qa_result, runtime_qa, runtime_retries, qa_warnings = await self._run_contract_and_runtime_flow(
                        code=generated.html_code,
                        spec=spec,
                        runtime_contract=runtime_contract,
                        prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
                        progress_cb=progress_cb,
                        game_id=request.game_id,
                        user_id=request.user_id,
                        stage_context=stage_context,
                        allow_runtime_qa_unavailable=allow_runtime_qa_unavailable,
                        operation="create",  # P1.3 PR-11
                        concurrent_review_factory=(
                            self.code_reviewer.review if review_requested else None
                        ),
                        concurrent_review_state=concurrent_review,
                    )
                except BaseException:
                    await self._discard_concurrent_review(concurrent_review)
                    raise
                if qa_result.needs_regeneration:
                    error_messages = "; ".join(error.message for error in qa_result.last_errors[:5])
                    last_quality_exc = PipelineExecutionError(
                        f"Generated code failed contract QA: {error_messages}",
                        stage="contract_qa",
                        retry_count=qa_result.retries,
                        failure_family="contract_qa",
                    )
                    next_provider_exclusions = self._advance_generation_provider_exclusions(
                        last_route_snapshot,
                        provider_exclusions,
                    )
                    if next_provider_exclusions is not None:
                        provider_exclusions = next_provider_exclusions
                    if quality_attempt >= len(attempt_plan):
                        raise last_quality_exc
                    generation_guidance = self._build_quality_regeneration_guidance(
                        stage="contract_qa",
                        errors=qa_result.last_errors,
                        message=str(last_quality_exc),
                    )
                    self._notify(
                        progress_cb,
                        "logic_generate",
                        66,
                        "Regenerating with contract quality guidance",
                        {
                            "gameId": request.game_id,
                            "userId": request.user_id,
                            "runtimeProfile": runtime_profile,
                            "attempt": quality_attempt,
                            "maxAttempts": len(attempt_plan),
                            "failedProviderId": (last_route_snapshot or {}).get("provider_id"),
                        },
                    )
                    await asyncio.sleep(min(quality_attempt, 2))
                    continue

                final_check = self.qa_pipeline.check(qa_result.code)
                code_bytes = len(qa_result.code.encode("utf-8"))
                review = LLMReviewResult(ran=False)
                if review_requested:
                    review = await self._resolve_concurrent_review(
                        concurrent_review,
                        qa_result.code,
                    )
                quality = self.quality_scorer.compute(
                    static=QAStaticResult(
                        passed=final_check.passed,
                        error_count=len(final_check.errors),
                        warning_count=len(final_check.warnings),
                        retries=qa_result.retries + runtime_retries,
                        strategy=generated.strategy,
                        code_size_bytes=code_bytes,
                    ),
                    runtime=runtime_qa,
                    review=review,
                    code=qa_result.code,
                )
                quality_gate_errors = self._quality_gate_errors(
                    spec,
                    review,
                    quality,
                    review_required=self._is_structured_review_required(spec),
                )
                if quality_gate_errors:
                    last_quality_exc = PipelineExecutionError(
                        "Generated code failed quality gate: " + "; ".join(quality_gate_errors[:4]),
                        stage="code_review",
                        retry_count=max(0, quality_attempt - 1),
                        failure_family="quality_gate",
                    )
                    if (
                        getattr(settings, "QUALITY_GATE_PATCH_REPAIR_ENABLED", True)
                        and self._should_attempt_quality_patch_repair(spec, review, quality)
                    ):
                        patch_outcome = await self._attempt_quality_gate_patch_repair(
                            request=request,
                            spec=spec,
                            runtime_contract=runtime_contract,
                            code=qa_result.code,
                            generation_strategy=generated.strategy,
                            base_retries=qa_result.retries + runtime_retries,
                            review=review,
                            quality=quality,
                            quality_gate_errors=quality_gate_errors,
                            previous_runtime_qa=runtime_qa,
                            progress_cb=progress_cb,
                            allow_runtime_qa_unavailable=allow_runtime_qa_unavailable,
                        )
                        if patch_outcome is not None:
                            # Patch repair passed the full gate; adopt the
                            # patched candidate without spending another
                            # full-generation attempt.
                            qa_result.code = patch_outcome.code
                            review = patch_outcome.review
                            quality = patch_outcome.quality
                            code_bytes = len(patch_outcome.code.encode("utf-8"))
                            if patch_outcome.runtime_qa_reran:
                                runtime_qa = patch_outcome.runtime_qa
                                runtime_retries += patch_outcome.runtime_retries
                            qa_warnings.extend(patch_outcome.qa_warnings)
                            last_quality_exc = None
                            break
                    if (
                        quality_attempt >= len(attempt_plan)
                        and self._can_accept_showcase_near_miss(
                            spec,
                            review,
                            quality,
                            quality_gate_errors,
                        )
                    ):
                        qa_warnings.append({
                            "type": "quality_gate_near_miss",
                            "message": "Accepted showcase near-miss after QA/runtime passed and scores stayed within the shippable band.",
                            "details": {
                                "final_score": float(getattr(quality, "final_score", 0.0) or 0.0),
                                "fun_score": float(getattr(review, "fun_score", 0.0) or 0.0),
                                "visual_polish_score": float(getattr(review, "visual_polish_score", 0.0) or 0.0),
                                "errors": quality_gate_errors[:4],
                            },
                        })
                        break
                    next_provider_exclusions = self._advance_generation_provider_exclusions(
                        last_route_snapshot,
                        provider_exclusions,
                    )
                    if next_provider_exclusions is not None:
                        provider_exclusions = next_provider_exclusions
                    if quality_attempt >= len(attempt_plan):
                        raise last_quality_exc
                    generation_guidance = self._build_review_quality_guidance(
                        spec,
                        review,
                        quality,
                        quality_gate_errors,
                    )
                    self._notify(
                        progress_cb,
                        "logic_generate",
                        68,
                        "Regenerating with gameplay and presentation quality guidance",
                        {
                            "gameId": request.game_id,
                            "userId": request.user_id,
                            "runtimeProfile": runtime_profile,
                            "attempt": quality_attempt,
                            "maxAttempts": len(attempt_plan),
                            "failedStage": "code_review",
                            "failedProviderId": (last_route_snapshot or {}).get("provider_id"),
                        },
                    )
                    await asyncio.sleep(min(quality_attempt, 2))
                    continue
                break
            except PipelineExecutionError as exc:
                last_quality_exc = exc
                last_route_snapshot = getattr(exc, "route_snapshot", None) or last_route_snapshot
                if quality_attempt >= len(attempt_plan) or exc.stage not in {"logic_generate", "contract_qa", "runtime_simulation_qa", "code_review"}:
                    raise
                generation_guidance = self._build_quality_regeneration_guidance(
                    stage=exc.stage,
                    message=str(exc),
                )
                if exc.stage == "logic_generate":
                    next_provider_exclusions = self._advance_generation_provider_exclusions(
                        last_route_snapshot,
                        provider_exclusions,
                    )
                    if next_provider_exclusions is not None:
                        provider_exclusions = next_provider_exclusions
                logger.warning(
                    "Create quality attempt %s/%s for game %s failed during %s; retrying one final full regeneration",
                    quality_attempt,
                    len(attempt_plan),
                    request.game_id,
                    exc.stage,
                )
                self._notify(
                    progress_cb,
                    "logic_generate",
                    66,
                    "Retrying one final full generation after quality gate failure",
                    {
                        "gameId": request.game_id,
                        "userId": request.user_id,
                        "runtimeProfile": runtime_profile,
                        "failedStage": exc.stage,
                        "failedProviderId": (last_route_snapshot or {}).get("provider_id"),
                        "attempt": quality_attempt,
                        "maxAttempts": len(attempt_plan),
                    },
                )
                await asyncio.sleep(min(quality_attempt, 2))
                continue

        if qa_result is None or runtime_qa is None or generated is None or quality is None:
            raise last_quality_exc or PipelineExecutionError(
                "Create generation failed before QA completed",
                stage=stage_context.get("stage", "logic_generate"),
            )

        elapsed = int(time.time() * 1000) - start_ms
        await self._remember_code(qa_result.code, label="final_code")
        # P1.2 GAP-3: persist fun_score into QAPipeline so PR-10's
        # filter_fixable can honor the CREATIVE-preserve threshold on
        # iterate-style follow-up runs. Only set when review actually ran;
        # otherwise leave as None so the filter defaults to "skip creative".
        if getattr(review, "ran", False):
            try:
                self.qa_pipeline._last_fun_score = float(review.fun_score)
            except (TypeError, ValueError, AttributeError):
                pass
            # P2.1 telemetry: record fun_score distribution by tier/operation
            # so downstream analysis can chart quality per cohort.
            try:
                _p2_emit(
                    "fun_score_observed",
                    tier=getattr(getattr(spec, "generation_tier", None), "value", None),
                    game_type=getattr(spec, "game_type", None),
                    fun_score=float(review.fun_score),
                    passes=bool(getattr(review, "passes", False)),
                    final_score=float(getattr(quality, "final_score", 0.0) or 0.0),
                )
            except Exception:  # noqa: BLE001 - telemetry must never crash runner
                pass

            # P2.3: commit the lane decision that code_generator stashed.
            # R-3 correlation key: prefer the request-scoped task_id
            # (matches the generator side, unique per request even across
            # hedge/retry); fall back to variation_seed for backward
            # compatibility with older notes still in _PENDING.
            try:
                _guard_key = str(self._current_task_id() or "") or str(
                    getattr(spec, "variation_seed", "") or ""
                )
                if _guard_key:
                    _guard_event = _p2_guard_commit(_guard_key, float(review.fun_score))
                    if _guard_event and _guard_event.get("event"):
                        _p2_emit(
                            _guard_event["event"],
                            tier=_guard_event.get("tier"),
                            hit_mean=_guard_event.get("hit_mean"),
                            miss_mean=_guard_event.get("miss_mean"),
                            gap=_guard_event.get("gap"),
                        )
            except Exception:  # noqa: BLE001 - guard must never crash runner
                pass

        if qa_result.success and quality.final_score >= 6.0:
            self.code_generator.template_cache.store(
                spec,
                runtime_profile,
                qa_result.code,
            )

        stage_context["stage"] = "completed"
        self._notify(progress_cb, "completed", 100, "V2 pipeline completed", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
        })
        return RunPipelineResponse(
            game_id=request.game_id,
            html_code=qa_result.code,
            game_spec=spec,
            strategy=generated.strategy,
            qa_passed=qa_result.success,
            qa_retries=qa_result.retries + runtime_retries,
            generation_time_ms=elapsed,
            code_size_bytes=code_bytes,
            quality_score=quality.final_score,
            runtime_profile=runtime_profile,
            contract_version=runtime_contract.version,
            qa_warnings=qa_warnings,
            runtime_qa_report=self._serialize_runtime_qa(runtime_qa, []),
            quality_breakdown=quality.details | {
                "qa_penalty": quality.qa_penalty,
                "strategy_bonus": quality.strategy_bonus,
                "size_bonus": quality.size_bonus,
                "retry_penalty": quality.retry_penalty,
                "runtime_bonus": quality.runtime_bonus,
                "review_bonus": quality.review_bonus,
                "gameplay_depth_bonus": quality.gameplay_depth_bonus,
                "runtime_profile": runtime_profile,
                "contract_version": runtime_contract.version,
            },
        )

    async def _run_iterate_impl(
        self,
        request: IterateV2Request,
        progress_cb: ProgressCallback,
        stage_context: dict[str, str],
    ) -> IterateResponse:
        start_ms = int(time.time() * 1000)
        # P1.2 GAP-3: mirror the create-path reset so iterate runs also start
        # with a clean fun_score slate for PR-10 filter_fixable.
        try:
            self.qa_pipeline._last_fun_score = None
        except AttributeError:
            pass
        self._notify(progress_cb, "spec_build", 15, "Compiling iteration spec", {
            "gameId": request.game_id,
            "userId": request.user_id,
        })
        stage_context["stage"] = "spec_build"
        spec = await self._build_iteration_spec(request)
        await self._remember_spec(spec)
        await task_memory.append_decision(
            self._current_task_id(),
            f"Built iteration spec with game_type={spec.game_type}",
        )

        self._notify(progress_cb, "runtime_profile_select", 30, "Selecting runtime profile", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "gameType": spec.game_type,
        })
        stage_context["stage"] = "runtime_profile_select"
        runtime_profile = self._select_runtime_profile(
            spec,
            request.runtime_contract.runtime_profile,
            variation_seed=request.game_id,
        )
        await task_memory.append_decision(
            self._current_task_id(),
            f"Selected runtime profile {runtime_profile}",
        )

        self._notify(progress_cb, "contract_compose", 40, "Refreshing runtime contract", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
        })
        stage_context["stage"] = "contract_compose"
        runtime_contract = self._compose_runtime_contract(
            base_contract=request.runtime_contract,
            spec=spec,
            runtime_profile=runtime_profile,
            entrypoint="iterate",
        )
        await self._remember_runtime_contract(runtime_profile=runtime_profile, contract=runtime_contract)

        self._notify(progress_cb, "logic_generate", 60, "Applying spec-driven iteration", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
        })
        stage_context["stage"] = "logic_generate"
        qa_result = None
        runtime_qa = None
        runtime_retries = 0
        qa_warnings: list[dict[str, Any]] = []
        iteration_type = IterationType.element_change
        retryable_validation_stages = {"contract_qa", "runtime_simulation_qa"}
        last_iteration_exc: PipelineExecutionError | None = None
        for iteration_attempt in range(1, DEFAULT_STAGE_TOTAL_ATTEMPTS + 1):
            updated_code, iteration_type = await self._generate_iteration_code(request, spec, runtime_contract)
            await self._remember_code(updated_code, label=f"iteration_{iteration_type.value}_{iteration_attempt}")
            await task_memory.append_decision(
                self._current_task_id(),
                f"Iteration classified as {iteration_type.value}",
            )

            try:
                qa_result, runtime_qa, runtime_retries, qa_warnings = await self._run_contract_and_runtime_flow(
                    code=updated_code,
                    spec=spec,
                    runtime_contract=runtime_contract,
                    prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
                    progress_cb=progress_cb,
                    game_id=request.game_id,
                    user_id=request.user_id,
                    stage_context=stage_context,
                    allow_runtime_qa_unavailable=self._should_allow_runtime_qa_unavailable(spec),
                    operation="iterate",  # P1.3 PR-11
                )
                break
            except PipelineExecutionError as exc:
                last_iteration_exc = exc
                if iteration_attempt >= DEFAULT_STAGE_TOTAL_ATTEMPTS or exc.stage not in retryable_validation_stages:
                    raise
                self._notify(progress_cb, "logic_generate", 66, "Regenerating iteration after validation failure", {
                    "gameId": request.game_id,
                    "userId": request.user_id,
                    "runtimeProfile": runtime_profile,
                    "attempt": iteration_attempt,
                    "maxAttempts": DEFAULT_STAGE_TOTAL_ATTEMPTS,
                    "failedStage": exc.stage,
                })
                await asyncio.sleep(min(iteration_attempt, 2))
                stage_context["stage"] = "logic_generate"

        if qa_result is None or runtime_qa is None:
            raise last_iteration_exc or PipelineExecutionError(
                "Iteration validation failed before a recoverable result was produced.",
                stage=stage_context.get("stage", "runtime_simulation_qa"),
                failure_family="runtime_qa",
            )

        await self._remember_code(qa_result.code, label="final_code")

        # Non-blocking quality assessment: never raises, never gates the
        # iterate result. Returns (None, None) when disabled, timed out, or
        # failed, in which case the response simply carries no quality fields.
        quality_score: Optional[float] = None
        quality_breakdown: Optional[dict[str, Any]] = None
        if getattr(settings, "ITERATE_QUALITY_REVIEW_ENABLED", True):
            quality_score, quality_breakdown = await self._assess_iterate_quality(
                qa_result=qa_result,
                runtime_qa=runtime_qa,
                runtime_retries=runtime_retries,
                iteration_type=iteration_type,
                runtime_profile=runtime_profile,
                contract_version=runtime_contract.version,
                spec=spec,
                game_id=request.game_id,
            )

        elapsed = int(time.time() * 1000) - start_ms
        stage_context["stage"] = "completed"
        self._notify(progress_cb, "completed", 100, "V2 iteration completed", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
        })
        return IterateResponse(
            html_code=qa_result.code,
            changes=[
                f"Applied: {request.iteration_intent.feedback}",
                f"Runtime profile: {runtime_profile}",
                (
                    f"Runtime QA unavailable: {runtime_qa.unavailable_reason}"
                    if qa_warnings
                    else f"Runtime QA jsErrors={len(runtime_qa.js_errors)}"
                ),
            ],
            iteration_type=iteration_type.value,
            game_spec=spec,
            generation_time_ms=elapsed,
            qa_retries=qa_result.retries + runtime_retries,
            iteration_retries=0,
            runtime_profile=runtime_profile,
            contract_version=runtime_contract.version,
            qa_warnings=qa_warnings,
            runtime_qa_report=self._serialize_runtime_qa(runtime_qa, []),
            quality_score=quality_score,
            quality_breakdown=quality_breakdown,
        )

    async def _assess_iterate_quality(
        self,
        *,
        qa_result: Any,
        runtime_qa: Any,
        runtime_retries: int,
        iteration_type: IterationType,
        runtime_profile: str,
        contract_version: str,
        spec: GameSpec,
        game_id: str,
    ) -> tuple[Optional[float], Optional[dict[str, Any]]]:
        """Post-iteration quality assessment. Never raises and never blocks
        the iterate result: any LLM failure or timeout logs a warning and
        returns (None, None) so the caller returns the response unchanged."""
        started = time.monotonic()
        timeout_s = get_timeout_float(
            ITERATE_QUALITY_REVIEW_TIMEOUT_KEY,
            ITERATE_QUALITY_REVIEW_DEFAULT_TIMEOUT_S,
            min_value=5.0,
            max_value=300.0,
        )
        timed_out = False
        try:
            code = qa_result.code
            try:
                # code_reviewer.review already prefers the fast model
                # (prefer_fast=True) and returns LLMReviewResult(ran=False)
                # on its own internal failures.
                review = await asyncio.wait_for(
                    self.code_reviewer.review(code),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                timed_out = True
                raise
            issue_list = getattr(qa_result, "issue_list", None)
            quality = self.quality_scorer.compute(
                static=QAStaticResult(
                    passed=bool(getattr(qa_result, "success", False)),
                    error_count=int(getattr(issue_list, "blocking_count", 0) or 0),
                    warning_count=int(getattr(issue_list, "warning_count", 0) or 0),
                    retries=int(getattr(qa_result, "retries", 0) or 0) + int(runtime_retries or 0),
                    strategy="llm",
                    code_size_bytes=len(code.encode("utf-8")),
                ),
                runtime=runtime_qa,
                review=review,
                code=code,
            )
            breakdown = quality.details | {
                "qa_penalty": quality.qa_penalty,
                "strategy_bonus": quality.strategy_bonus,
                "size_bonus": quality.size_bonus,
                "retry_penalty": quality.retry_penalty,
                "runtime_bonus": quality.runtime_bonus,
                "review_bonus": quality.review_bonus,
                "gameplay_depth_bonus": quality.gameplay_depth_bonus,
                "runtime_profile": runtime_profile,
                "contract_version": contract_version,
                "iteration_type": iteration_type.value,
            }
            elapsed_ms = int((time.monotonic() - started) * 1000)
            _p2_emit(
                "iterate_quality_assessed",
                tier=getattr(getattr(spec, "generation_tier", None), "value", None),
                game_type=getattr(spec, "game_type", None),
                final_score=quality.final_score,
                fun_score=float(review.fun_score) if getattr(review, "ran", False) else None,
                review_ran=bool(getattr(review, "ran", False)),
                elapsed_ms=elapsed_ms,
                timed_out=False,
            )
            return quality.final_score, breakdown
        except Exception as exc:  # noqa: BLE001 - assessment must never fail the iterate
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if timed_out:
                logger.warning(
                    "Iterate quality assessment for game %s abandoned after %.0fs timeout",
                    game_id,
                    timeout_s,
                )
            else:
                logger.warning(
                    "Iterate quality assessment for game %s failed (non-blocking): %s",
                    game_id,
                    exc,
                )
            try:
                _p2_emit(
                    "iterate_quality_assessed",
                    tier=getattr(getattr(spec, "generation_tier", None), "value", None),
                    game_type=getattr(spec, "game_type", None),
                    elapsed_ms=elapsed_ms,
                    timed_out=timed_out,
                    error=type(exc).__name__,
                )
            except Exception:  # pragma: no cover - telemetry must never crash runner
                pass
            return None, None

    async def _build_create_spec(self, request: RunPipelineV2Request) -> GameSpec:
        if request.source_spec:
            spec = request.source_spec.model_copy(deep=True)
            spec.generation_tier = self._resolve_generation_tier(
                request.generation_tier,
                request.metadata.get("generation_tier"),
                request.normalized_request.get("generation_tier"),
                request.request_context.metadata.get("generation_tier"),
                request.prompt_bundle_snapshot.layers.get("generation_tier"),
                request.runtime_contract.metadata.get("generation_tier"),
                getattr(spec, "generation_tier", None),
            )
            spec.complexity_budget = str(
                getattr(spec.generation_tier, "value", spec.generation_tier)
                or spec.complexity_budget
                or "standard"
            )
            if request.raw_user_input.strip():
                spec.source_description = request.raw_user_input.strip()
            if request.title and spec.intent_summary:
                spec.intent_summary = f"{request.title}: {spec.intent_summary}"
            spec = self._expand_spec_entities_for_budget(spec)
            return apply_visual_pack_defaults(spec, variation_seed=request.game_id)

        description = request.raw_user_input.strip() or str(
            request.normalized_request.get("description", "")
        ).strip()
        if not description:
            raise PipelineExecutionError("raw_user_input is required", stage="spec_build")

        spec = await self._parse_spec_with_retries(
            description=description,
            stage="spec_build",
            title=request.title,
            preferred_game_type=PROFILE_TO_GAME_TYPE_HINT.get(
                (request.runtime_contract.runtime_profile or "").strip(),
            ),
            variation_seed=request.game_id,
        )
        spec.generation_tier = self._resolve_generation_tier(
            request.generation_tier,
            request.metadata.get("generation_tier"),
            request.normalized_request.get("generation_tier"),
            request.request_context.metadata.get("generation_tier"),
            request.prompt_bundle_snapshot.layers.get("generation_tier"),
            request.runtime_contract.metadata.get("generation_tier"),
        )
        spec.complexity_budget = str(getattr(spec.generation_tier, "value", spec.generation_tier) or "standard")
        spec = self._expand_spec_entities_for_budget(spec)
        return apply_visual_pack_defaults(spec, variation_seed=request.game_id)

    async def _build_iteration_spec(self, request: IterateV2Request) -> GameSpec:
        feedback = request.iteration_intent.feedback.strip()
        if not feedback:
            raise PipelineExecutionError("iteration_intent.feedback is required", stage="spec_build")

        base_spec = request.source_spec.model_copy(deep=True) if request.source_spec else None
        current_summary = self._summarize_current_code(request.current_code)
        normalized_feedback = re.sub(r"\s+", " ", feedback).strip().lower()
        conversation_lines = []
        for item in request.iteration_intent.conversation[-4:]:
            if not isinstance(item, dict):
                continue
            content = re.sub(r"\s+", " ", str(item.get("content", "") or "")).strip()
            if not content:
                continue
            if content.lower() == normalized_feedback:
                continue
            role = str(item.get("role", "user") or "user").strip() or "user"
            conversation_lines.append(f"{role}: {content}")
        conversation_text = " | ".join(conversation_lines).strip()
        source_spec_summary = self._summarize_source_spec(base_spec)
        source_bundle_context_summary = self._summarize_source_bundle_context(request.source_bundle_context)
        spec_prompt = require_prompt("prompt.iteration_spec_context_template").format(
            current_summary=current_summary,
            status=request.existing_game.status,
            visibility=request.existing_game.visibility,
            feedback=feedback,
            conversation_text=conversation_text or "(none)",
            source_spec_summary=source_spec_summary or "(none)",
            source_bundle_context=source_bundle_context_summary or "(none)",
        )
        title = (request.source_bundle_context.title or "").strip() or None
        preferred_game_type = (
            (base_spec.game_type or "").strip()
            if base_spec and (base_spec.game_type or "").strip()
            else PROFILE_TO_GAME_TYPE_HINT.get((request.runtime_contract.runtime_profile or "").strip())
        )

        try:
            parsed_spec = await self._parse_spec_with_retries(
                description=spec_prompt,
                stage="spec_build",
                title=title,
                preferred_game_type=preferred_game_type,
                variation_seed=request.game_id,
            )
        except PipelineExecutionError as exc:
            if base_spec and self._should_fallback_iteration_spec(exc):
                spec = self._build_iteration_fallback_spec(
                    base_spec=base_spec,
                    feedback=feedback,
                    title=title,
                    source_bundle_context=request.source_bundle_context,
                )
                spec.generation_tier = self._resolve_generation_tier(
                    request.generation_tier,
                    request.metadata.get("generation_tier"),
                    request.normalized_request.get("generation_tier"),
                    request.request_context.metadata.get("generation_tier"),
                    request.prompt_bundle_snapshot.layers.get("generation_tier"),
                    request.runtime_contract.metadata.get("generation_tier"),
                    request.source_bundle_context.latest_generation_tier,
                    base_spec.generation_tier if base_spec else None,
                )
                spec.complexity_budget = str(getattr(spec.generation_tier, "value", spec.generation_tier) or "standard")
                spec = self._expand_spec_entities_for_budget(spec)
                return apply_visual_pack_defaults(spec, variation_seed=request.game_id)
            raise

        spec = self._merge_iteration_spec(
            base_spec=base_spec,
            parsed_spec=parsed_spec,
            feedback=feedback,
            title=title,
            source_bundle_context=request.source_bundle_context,
        )
        spec.generation_tier = self._resolve_generation_tier(
            request.generation_tier,
            request.metadata.get("generation_tier"),
            request.normalized_request.get("generation_tier"),
            request.request_context.metadata.get("generation_tier"),
            request.prompt_bundle_snapshot.layers.get("generation_tier"),
            request.runtime_contract.metadata.get("generation_tier"),
            request.source_bundle_context.latest_generation_tier,
            base_spec.generation_tier if base_spec else None,
        )
        spec.complexity_budget = str(getattr(spec.generation_tier, "value", spec.generation_tier) or "standard")
        spec = self._expand_spec_entities_for_budget(spec)
        return apply_visual_pack_defaults(spec, variation_seed=request.game_id)

    @staticmethod
    def _resolve_generation_tier(*candidates: Any) -> GenerationTier:
        for candidate in candidates:
            raw_value = getattr(candidate, "value", candidate)
            value = str(raw_value or "").strip().lower()
            if value == "safe":
                return GenerationTier.safe
            if value == "showcase":
                return GenerationTier.showcase
            if value == "standard":
                return GenerationTier.standard
        return GenerationTier.standard

    async def _parse_spec_with_retries(
        self,
        *,
        description: str,
        stage: str,
        title: Optional[str],
        preferred_game_type: Optional[str] = None,
        variation_seed: Optional[str] = None,
    ) -> GameSpec:
        last_exc: Exception | None = None
        for attempt in range(1, DEFAULT_STAGE_TOTAL_ATTEMPTS + 1):
            try:
                spec = await self.dialogue_engine.parse_description_to_spec(
                    description,
                    allow_fallback=True,
                    title=title,
                    preferred_game_type=preferred_game_type,
                    variation_seed=variation_seed,
                )
                spec.source_description = description
                if title and spec.intent_summary:
                    spec.intent_summary = f"{title}: {spec.intent_summary}"
                else:
                    spec.intent_summary = title or spec.intent_summary or description[:160]
                return spec
            except Exception as exc:
                last_exc = exc
                if attempt == DEFAULT_STAGE_TOTAL_ATTEMPTS:
                    break
                await asyncio.sleep(min(attempt, 2) * 0.5)

        raise PipelineExecutionError(
            f"Spec build failed after {DEFAULT_STAGE_TOTAL_ATTEMPTS} attempts: {last_exc}",
            stage=stage,
            retry_count=DEFAULT_STAGE_TOTAL_ATTEMPTS - 1,
            failure_family="spec_build",
            artifacts=getattr(last_exc, "artifacts", None),
        ) from last_exc

    @staticmethod
    def _should_fallback_iteration_spec(exc: PipelineExecutionError) -> bool:
        if isinstance(exc.__cause__, SlotExtractionFailure):
            return True

        artifacts = getattr(exc, "artifacts", None) or []
        if any(
            isinstance(artifact, dict) and artifact.get("artifact_type") == "spec_build_diagnostics"
            for artifact in artifacts
        ):
            return True

        return "llm slot extraction returned no valid json" in str(exc).lower()

    def _select_runtime_profile(
        self,
        spec: GameSpec,
        requested_profile: Optional[str],
        *,
        variation_seed: Optional[str] = None,
    ) -> str:
        requested = normalize_runtime_profile_id((requested_profile or "").strip())
        selection_text = self._profile_selection_text(spec)
        action_request = self._looks_like_action_runtime_request(spec)
        puzzle_request = self._looks_like_puzzle_runtime_request(spec)
        quiz_show_request = self._looks_like_quiz_show_runtime_request(spec)
        generation_tier = CodeGenerator._resolve_generation_tier(spec)
        if requested:
            try:
                default_profile = _default_runtime_profile_id()
            except PipelineExecutionError:
                default_profile = requested
            if requested != default_profile:
                should_override_requested_profile = (
                    quiz_show_request
                    and generation_tier == "showcase"
                    and requested == "puzzle_grid"
                )
                if not should_override_requested_profile:
                    return requested
        if quiz_show_request and not action_request:
            return self._preferred_quiz_show_profile(spec)
        if _looks_like_educational_request(
            spec.source_description,
            spec.intent_summary,
            " ".join(spec.special_rules or []),
        ) and not action_request:
            return "puzzle_grid"
        normalized = re.sub(r"[^a-z0-9]+", " ", (spec.game_type or "").lower()).strip()
        game_type_candidates = list(PROFILE_CANDIDATES_BY_GAME_TYPE.get(normalized, ()))
        keyword_candidates: list[str] = []
        for token, fallback_candidates in PROFILE_KEYWORD_FALLBACKS:
            if token in selection_text:
                keyword_candidates.extend(fallback_candidates)

        action_candidates: list[str] = []
        if action_request and not puzzle_request:
            action_candidates.extend(ACTION_FOCUSED_RUNTIME_PROFILES)

        candidates = self._merge_profile_candidates(
            action_candidates,
            keyword_candidates,
            game_type_candidates,
        )
        if action_request and not puzzle_request:
            filtered = [candidate for candidate in candidates if not candidate.startswith("puzzle_grid")]
            if filtered:
                candidates = filtered
        if candidates:
            ranked = self._rank_runtime_profile_candidates(spec, candidates)
            if len(ranked) == 1 or not self._should_allow_profile_variation(spec):
                return ranked[0]
            pool = ranked[: min(3, len(ranked))]
            return pool[self._profile_variant_index(spec, variation_seed=variation_seed, count=len(pool))]
        if requested:
            return requested
        return _default_runtime_profile_id()

    def _rank_runtime_profile_candidates(
        self,
        spec: GameSpec,
        candidates: list[str],
    ) -> list[str]:
        ordered: list[tuple[int, int, str]] = []
        for index, profile in enumerate(candidates):
            ordered.append((self._score_runtime_profile_candidate(spec, profile), -index, profile))
        ordered.sort(reverse=True)
        return [profile for _, _, profile in ordered]

    def _score_runtime_profile_candidate(self, spec: GameSpec, profile: str) -> int:
        combined = self._profile_selection_text(spec)
        input_mode = (spec.platform_constraints.input_mode or "").lower()
        sparse = self._should_allow_profile_variation(spec)
        raw_generation_tier = getattr(spec, "generation_tier", GenerationTier.standard)
        generation_tier = str(getattr(raw_generation_tier, "value", raw_generation_tier))
        action_request = self._looks_like_action_runtime_request(spec)
        puzzle_request = self._looks_like_puzzle_runtime_request(spec)
        quiz_show_request = self._looks_like_quiz_show_runtime_request(spec)
        character_driven = self._is_character_driven_spec(spec)
        score = 0

        base_scores = (
            ("puzzle_grid", {"puzzle", "educational"}, 4),
            ("puzzle_grid_match", {"puzzle", "educational"}, 7),
            ("puzzle_grid_merge", {"puzzle"}, 7),
            ("puzzle_grid_route", {"puzzle", "educational"}, 8),
            ("casual_lane", {"casual", "funny"}, 3),
            ("casual_lane_dash", {"casual", "funny"}, 6),
            ("casual_lane_chase", {"casual", "funny"}, 6),
            ("casual_action", {"casual", "funny"}, 3),
            ("casual_action_arena", {"casual", "funny"}, 6),
            ("casual_action_survival", {"casual", "funny"}, 6),
            ("tap_challenge", {"funny", "educational"}, 4),
            ("tap_challenge_timing", {"funny", "educational"}, 7),
            ("tap_challenge_combo", {"funny", "casual"}, 7),
            ("casual_arcade", {"casual", "funny", "puzzle"}, 4),
            ("casual_arcade_burst", {"casual", "funny"}, 7),
            ("casual_arcade_orbit", {"casual", "funny"}, 7),
            ("casual_arcade_rescue", {"casual", "funny", "educational"}, 7),
        )
        for candidate, game_types, value in base_scores:
            if profile == candidate and spec.game_type in game_types:
                score += value

        if "swipe" in input_mode and profile in {"casual_lane", "casual_lane_dash", "casual_lane_chase"}:
            score += 3
        if "drag" in input_mode and profile in {
            "casual_action",
            "casual_action_arena",
            "casual_action_survival",
            "puzzle_grid",
            "puzzle_grid_route",
            "casual_arcade_orbit",
        }:
            score += 2
        if "tap" in input_mode and profile in {
            "casual_arcade",
            "casual_arcade_burst",
            "casual_arcade_rescue",
            "tap_challenge",
            "tap_challenge_timing",
            "tap_challenge_combo",
            "puzzle_grid",
            "puzzle_grid_match",
            "puzzle_grid_merge",
        }:
            score += 2

        if any(token in combined for token in ("quiz", "lesson", "teacher", "learn", "math", "word", "spell", "answer")) and profile in {"puzzle_grid", "puzzle_grid_match", "puzzle_grid_route", "tap_challenge_timing"}:
            score += 4
        if quiz_show_request and profile in {"tap_challenge_timing", "tap_challenge_combo"}:
            score += 6
        if quiz_show_request and profile.startswith("puzzle_grid"):
            score -= 2
        if any(token in combined for token in ("match", "sort", "logic", "connect", "solve")) and profile in {"puzzle_grid", "puzzle_grid_match", "puzzle_grid_route"}:
            score += 3
        if "merge" in combined and profile in {"puzzle_grid_merge", "puzzle_grid_match"}:
            score += 4
        if any(token in combined for token in ("beat", "rhythm", "music", "timing", "tempo")) and profile in {"tap_challenge", "tap_challenge_timing", "tap_challenge_combo"}:
            score += 4
        if any(token in combined for token in ("run", "race", "dash")) and profile in {"casual_lane", "casual_lane_dash"}:
            score += 4
        if any(token in combined for token in ("chase", "pursuit", "escape")) and profile in {"casual_lane_chase", "casual_action_survival"}:
            score += 4
        if any(token in combined for token in ("shoot", "projectile", "weapon", "fire")) and profile in {"casual_action", "casual_action_arena"}:
            score += 5
        if any(token in combined for token in ("avoid", "survive", "hazard")) and profile in {"casual_action", "casual_action_survival", "casual_arcade_orbit"}:
            score += 2
        if any(token in combined for token in ("collect", "rescue", "delivery", "escort")) and profile in {"casual_arcade", "casual_arcade_rescue", "casual_action", "puzzle_grid_route"}:
            score += 2
        if any(token in combined for token in ("boss", "combat", "battle", "fight", "arena")) and profile in {"casual_action", "casual_action_arena"}:
            score += 2
        if any(token in combined for token in ("funny", "comedy", "meme", "prank", "office", "goose", "slacker")) and profile in {"casual_arcade", "casual_arcade_burst", "casual_action_arena", "tap_challenge_combo"}:
            score += 3

        if action_request:
            if profile in ACTION_FOCUSED_RUNTIME_PROFILES:
                score += 5
            if profile.startswith("puzzle_grid"):
                score -= 6
        if character_driven and profile.startswith("puzzle_grid"):
            score -= 4
        if puzzle_request and not action_request and profile.startswith("puzzle_grid"):
            score += 3

        if sparse and profile in {"casual_arcade_burst", "casual_arcade_orbit", "casual_arcade_rescue", "puzzle_grid_route", "tap_challenge_combo"}:
            score += 2
        if sparse and profile in {"casual_action", "puzzle_grid", "casual_arcade"}:
            score -= 1

        if generation_tier == "safe":
            score += 2 if profile in BASELINE_RUNTIME_PROFILES else -2
        elif generation_tier == "showcase":
            score += 3 if profile not in BASELINE_RUNTIME_PROFILES else -1
            if action_request and profile in ACTION_FOCUSED_RUNTIME_PROFILES:
                score += 2

        return score

    @staticmethod
    def _should_allow_profile_variation(spec: GameSpec) -> bool:
        raw_generation_tier = getattr(spec, "generation_tier", GenerationTier.standard)
        if str(getattr(raw_generation_tier, "value", raw_generation_tier)) == "showcase":
            return True
        return any(
            "Favor a distinctive gameplay loop" in rule
            for rule in (spec.special_rules or [])
        )

    @staticmethod
    def _profile_variant_index(spec: GameSpec, *, variation_seed: Optional[str], count: int) -> int:
        if count <= 1:
            return 0
        seed = "|".join(
            item.strip()
            for item in [
                spec.game_type or "",
                spec.source_description or "",
                spec.intent_summary or "",
                spec.visual_style.theme or "",
                variation_seed or "",
            ]
            if item and item.strip()
        )
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return int.from_bytes(digest, "big") % count

    def _compose_runtime_contract(
        self,
        *,
        base_contract: GameRuntimeContract,
        spec: GameSpec,
        runtime_profile: str,
        entrypoint: str,
    ) -> GameRuntimeContract:
        runtime_profile = normalize_runtime_profile_id(runtime_profile)
        contract = base_contract.model_copy(deep=True)
        contract.runtime_profile = runtime_profile
        requested_orientation = self._resolve_contract_orientation(base_contract)
        normalized_render_api = str(spec.platform_constraints.render_api or "").strip().lower()
        allow_webgl = normalized_render_api in {"", "webgl", "webgl2", "canvas2d_or_webgl", "canvas_or_webgl"}
        requires_canvas_2d = normalized_render_api == "canvas2d"
        contract.canvas = contract.canvas.model_copy(
            update={
                "requires_canvas_2d": requires_canvas_2d,
                "allow_webgl": allow_webgl,
                "orientation": requested_orientation,
                "ui_scale_mode": "short_edge",
                "target_fps": spec.platform_constraints.target_fps or contract.canvas.target_fps,
            }
        )
        contract.input = contract.input.model_copy(update=self._profile_input_overrides(runtime_profile))
        contract.state = contract.state.model_copy(update=self._profile_state_overrides(runtime_profile))
        contract.gameplay = contract.gameplay.model_copy(update=self._profile_gameplay_overrides(runtime_profile))
        contract.mobile_layout = contract.mobile_layout.model_copy(
            update={
                "orientation": contract.canvas.orientation,
                "ui_scale_mode": contract.canvas.ui_scale_mode,
            }
        )
        contract.metadata = {
            **contract.metadata,
            "entrypoint": entrypoint,
            "game_type": spec.game_type,
            "difficulty_curve": spec.difficulty_curve,
            "orientation": requested_orientation,
        }
        return contract

    @staticmethod
    def _resolve_contract_orientation(base_contract: GameRuntimeContract) -> str:
        orientation = str(
            getattr(base_contract.mobile_layout, "orientation", None)
            or getattr(base_contract.canvas, "orientation", None)
            or "portrait_first"
        ).strip()
        if orientation == "landscape_first":
            return "landscape_first"
        return "portrait_first"

    def _profile_input_overrides(self, runtime_profile: str) -> dict[str, Any]:
        if runtime_profile.startswith("puzzle_grid"):
            return {
                "required_modes": ["touch"],
                "allow_mouse_fallback": True,
                "gestures": ["tap", "drag"],
            }
        if runtime_profile.startswith("casual_lane"):
            return {
                "required_modes": ["touch", "pointer"],
                "allow_mouse_fallback": True,
                "gestures": ["tap", "swipe"],
            }
        if runtime_profile.startswith("tap_challenge"):
            return {
                "required_modes": ["touch", "pointer"],
                "allow_mouse_fallback": True,
                "gestures": ["tap"],
            }
        if runtime_profile in {"casual_arcade_orbit", "casual_arcade_rescue"}:
            return {
                "required_modes": ["touch", "pointer"],
                "allow_mouse_fallback": True,
                "gestures": ["drag", "tap"],
            }
        return {
            "required_modes": ["touch", "pointer"],
            "allow_mouse_fallback": True,
            "gestures": ["tap", "drag"],
        }

    def _profile_state_overrides(self, runtime_profile: str) -> dict[str, Any]:
        if runtime_profile.startswith("puzzle_grid"):
            return {
                "required_states": ["boot", "ready", "playing", "level_complete"],
                "required_flags": ["levelComplete", "currentLevel", "showHint"],
                "restartable": True,
            }
        return {
            "required_states": ["boot", "ready", "playing", "game_over"],
            "required_flags": ["score", "gameOver"],
            "restartable": True,
        }

    def _profile_gameplay_overrides(self, runtime_profile: str) -> dict[str, Any]:
        if runtime_profile.startswith("puzzle_grid"):
            primary_goal = "grid_completion"
            if runtime_profile == "puzzle_grid_merge":
                primary_goal = "merge_progression"
            elif runtime_profile == "puzzle_grid_route":
                primary_goal = "route_completion"
            return {
                "requires_player_entity": False,
                "requires_scoring": False,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": [
                    "level_complete",
                    "completed",
                    "complete",
                    "solved",
                    "success",
                    "win",
                    "cleared",
                ],
                "primary_goal": primary_goal,
            }
        if runtime_profile.startswith("casual_lane"):
            primary_goal = "lane_survival" if runtime_profile != "casual_lane_chase" else "lane_chase"
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": primary_goal,
            }
        if runtime_profile == "casual_action_arena":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "arena_clearance",
            }
        if runtime_profile == "casual_action_survival":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "survival_holdout",
            }
        if runtime_profile == "casual_arcade_rescue":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed", "rescued", "success"],
                "primary_goal": "rescue_route",
            }
        if runtime_profile == "casual_arcade_orbit":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "orbit_control",
            }
        if runtime_profile == "tap_challenge_combo":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "combo_target",
            }
        return {
            "requires_player_entity": True,
            "requires_scoring": True,
            "requires_terminal_state": True,
            "requires_restart_entry": True,
            "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
            "primary_goal": "clear_feedback_loop",
        }

    @staticmethod
    def _target_seed_entity_count(spec: GameSpec) -> int:
        tier_value = str(getattr(spec.generation_tier, "value", spec.generation_tier) or "standard").strip().lower()
        if tier_value == "showcase":
            return 7 if spec.game_type in {"casual", "funny", "educational"} else 6
        if tier_value == "safe":
            return 3
        return 6 if spec.game_type == "puzzle" else 5

    def _expand_spec_entities_for_budget(self, spec: GameSpec) -> GameSpec:
        expanded = spec.model_copy(deep=True)
        target_count = min(
            max(3, self._target_seed_entity_count(expanded)),
            max(3, int(expanded.platform_constraints.max_entities or 50)),
        )
        if len(expanded.entities) >= target_count:
            return expanded

        catalog = ENTITY_BUDGET_EXPANSION_LIBRARY.get(
            expanded.game_type,
            ENTITY_BUDGET_EXPANSION_LIBRARY["casual"],
        )
        existing_names = {str(entity.name or "").strip().lower() for entity in expanded.entities}
        for candidate in catalog:
            if len(expanded.entities) >= target_count:
                break
            candidate_name = str(candidate.get("name") or "").strip().lower()
            if not candidate_name or candidate_name in existing_names:
                continue
            expanded.entities.append(GameEntity(**candidate))
            existing_names.add(candidate_name)
        return expanded

    async def _build_gdd(self, spec: GameSpec, runtime_contract: GameRuntimeContract) -> GDD:
        try:
            gdd = await self.game_designer.design(
                spec,
                orientation=runtime_contract.mobile_layout.orientation,
            )
        except Exception as exc:
            raise PipelineExecutionError(
                f"Contract-aware design failed: {exc}",
                stage="contract_compose",
            ) from exc

        gdd.canvas.target_fps = runtime_contract.canvas.target_fps
        gdd.state_machine = {
            "states": runtime_contract.state.required_states,
            "initial": runtime_contract.state.required_states[0],
            "transitions": {
                runtime_contract.state.required_states[0]: runtime_contract.state.required_states[1],
                runtime_contract.state.required_states[1]: runtime_contract.state.required_states[2],
                runtime_contract.state.required_states[2]: runtime_contract.state.required_states[-1],
                runtime_contract.state.required_states[-1]: runtime_contract.state.required_states[1],
            },
        }
        if "touch" in runtime_contract.input.required_modes:
            gdd.input_map.setdefault("touchstart", "primary_action")
            gdd.input_map.setdefault("touchmove", "primary_drag")
            gdd.input_map.setdefault("touchend", "primary_release")
        if "pointer" in runtime_contract.input.required_modes:
            gdd.input_map.setdefault("pointerdown", "primary_action")
            gdd.input_map.setdefault("pointermove", "primary_drag")
            gdd.input_map.setdefault("pointerup", "primary_release")
        gdd.input_map.setdefault("click_game_over", "restart")
        return gdd

    async def _generate_create_code(
        self,
        request: RunPipelineV2Request,
        spec: GameSpec,
        gdd: GDD,
        runtime_contract: GameRuntimeContract,
        budget_override: Optional[str] = None,
        excluded_provider_ids: Optional[list[str]] = None,
        generation_guidance: Optional[str] = None,
    ) -> tuple[Any, list[Any]]:
        try:
            generated = await self.code_generator.generate(
                spec=spec,
                gdd=gdd,
                description=request.raw_user_input,
                runtime_contract=runtime_contract,
                runtime_profile=runtime_contract.runtime_profile,
                prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
                budget_override=budget_override,
                generation_guidance=generation_guidance,
                excluded_provider_ids=self._normalize_provider_exclusions(excluded_provider_ids),
            )
        except Exception as exc:
            wrapped = PipelineExecutionError(
                f"Full LLM generation failed: {exc}",
                stage="logic_generate",
                failure_family="code_generation",
            )
            route_snapshot = getattr(exc, "route_snapshot", None)
            if route_snapshot is not None:
                try:
                    setattr(wrapped, "route_snapshot", route_snapshot)
                except Exception:
                    pass
            raise wrapped from exc
        repaired_html = self.code_preflight.auto_repair(
            generated.html_code,
            runtime_contract=runtime_contract,
        )
        if repaired_html != generated.html_code:
            generated = generated.model_copy(update={"html_code": repaired_html})
        preflight_issues = self.code_preflight.validate(
            generated.html_code,
            runtime_contract=runtime_contract,
        )
        if preflight_issues:
            issue_repaired_html = self.code_preflight.auto_repair(
                generated.html_code,
                runtime_contract=runtime_contract,
                issues=preflight_issues,
            )
            if issue_repaired_html != generated.html_code:
                generated = generated.model_copy(update={"html_code": issue_repaired_html})
                preflight_issues = self.code_preflight.validate(
                    generated.html_code,
                    runtime_contract=runtime_contract,
                )
        return generated, preflight_issues

    async def _generate_iteration_code(
        self,
        request: IterateV2Request,
        spec: GameSpec,
        runtime_contract: GameRuntimeContract,
    ):
        last_exc: Exception | None = None
        for attempt in range(1, DEFAULT_STAGE_TOTAL_ATTEMPTS + 1):
            try:
                return await self.code_generator.iterate(
                    current_code=request.current_code,
                    feedback=request.iteration_intent.feedback,
                    conversation=request.iteration_intent.conversation,
                    runtime_contract=runtime_contract,
                    runtime_profile=runtime_contract.runtime_profile,
                    prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
                    game_spec=spec,
                )
            except Exception as exc:
                last_exc = exc
                if attempt == DEFAULT_STAGE_TOTAL_ATTEMPTS or not self._is_retryable_generation_error(exc):
                    break
                await asyncio.sleep(min(attempt, 2))

        raise PipelineExecutionError(
            f"Logic iteration failed after {attempt} attempts: {last_exc}",
            stage="logic_generate",
            retry_count=max(0, attempt - 1),
        )

    async def _run_contract_and_runtime_flow(
        self,
        *,
        code: str,
        spec: GameSpec,
        runtime_contract: GameRuntimeContract,
        prompt_bundle_snapshot: Optional[dict[str, Any]],
        progress_cb: ProgressCallback,
        game_id: str,
        user_id: str,
        stage_context: dict[str, str],
        allow_runtime_qa_unavailable: bool,
        # P1.3 PR-11: operation="create" | "iterate" — enables the runtime-QA
        # scheduler to consult should_defer() with an accurate tier/op pair.
        operation: str = "create",
        # Optional concurrent LLM code review (create main path only): when a
        # factory + state dict are supplied, the review task is launched right
        # before runtime simulation QA so both run in parallel. The caller is
        # responsible for awaiting/cancelling the stashed task.
        concurrent_review_factory: Optional[Callable[[str], Any]] = None,
        concurrent_review_state: Optional[dict[str, Any]] = None,
    ) -> tuple[QAResult, Any, int, list[dict[str, Any]]]:
        stage_context["stage"] = "contract_qa"
        self._notify(progress_cb, "contract_qa", 76, "Running contract QA", {
            "gameId": game_id,
            "userId": user_id,
            "runtimeProfile": runtime_contract.runtime_profile,
        })

        # Fast-path: if code passes static QA + contract on first check, skip repair loop
        pre_repaired = self.qa_pipeline._apply_deterministic_repairs(code)
        static_check = self.qa_pipeline.check(pre_repaired, runtime_contract=runtime_contract)
        contract_errors = self._validate_contract_bundle(pre_repaired, runtime_contract)
        if static_check.passed and not contract_errors:
            logger.info("Code passed all checks on first attempt; skipping repair loop and running runtime QA directly")
            qa_result = QAResult(
                success=True,
                code=pre_repaired,
                retries=0,
                issue_list=static_check.issue_list,
            )
            # Fast-path: jump directly to runtime QA, skip the contract_qa stage notification
            stage_context["stage"] = "runtime_simulation_qa"
            self._notify(progress_cb, "runtime_simulation_qa", 92, "Running runtime simulation QA", {
                "gameId": game_id,
                "userId": user_id,
                "runtimeProfile": runtime_contract.runtime_profile,
            })
            self._launch_concurrent_review(
                concurrent_review_factory,
                concurrent_review_state,
                qa_result.code,
            )
            final_code, runtime_qa, runtime_retries, qa_warnings = await self._run_runtime_qa_loop(
                code=qa_result.code,
                runtime_contract=runtime_contract,
                progress_cb=progress_cb,
                game_id=game_id,
                user_id=user_id,
                allow_runtime_qa_unavailable=allow_runtime_qa_unavailable,
                tier=getattr(getattr(spec, "generation_tier", None), "value", None)
                    or str(getattr(spec, "generation_tier", "") or ""),
                operation=operation,  # P1.3 PR-11
            )
            return QAResult(
                success=True,
                code=final_code,
                retries=qa_result.retries,
                issue_list=qa_result.issue_list,
            ), runtime_qa, runtime_retries, qa_warnings
        else:
            qa_result = await self._run_contract_qa_loop(
                code=code,
                spec=spec,
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot=prompt_bundle_snapshot,
                progress_cb=progress_cb,
                game_id=game_id,
                user_id=user_id,
            )
        if not qa_result.success and qa_result.needs_regeneration:
            return qa_result, RuntimeQAResult(), 0, []
        if not qa_result.success:
            error_messages = "; ".join(error.message for error in qa_result.last_errors[:5])
            raise PipelineExecutionError(
                f"Generated code failed contract QA: {error_messages}",
                stage="contract_qa",
                retry_count=qa_result.retries,
                failure_family="contract_qa",
                artifacts=[
                    self._build_text_artifact(
                        artifact_type="failed_contract_candidate",
                        payload=qa_result.code,
                        metadata={
                            "stage": "contract_qa",
                            "retryCount": qa_result.retries,
                        },
                    ),
                    self._build_json_artifact(
                        artifact_type="contract_qa_report",
                        payload={
                            "passed": False,
                            "retryCount": qa_result.retries,
                            "errors": self._serialize_errors(qa_result.last_errors),
                        },
                        metadata={"stage": "contract_qa"},
                    ),
                ],
            )

        stage_context["stage"] = "runtime_simulation_qa"
        self._notify(progress_cb, "runtime_simulation_qa", 92, "Running runtime simulation QA", {
            "gameId": game_id,
            "userId": user_id,
            "runtimeProfile": runtime_contract.runtime_profile,
        })
        self._launch_concurrent_review(
            concurrent_review_factory,
            concurrent_review_state,
            qa_result.code,
        )
        final_code, runtime_qa, runtime_retries, qa_warnings = await self._run_runtime_qa_loop(
            code=qa_result.code,
            runtime_contract=runtime_contract,
            progress_cb=progress_cb,
            game_id=game_id,
            user_id=user_id,
            allow_runtime_qa_unavailable=allow_runtime_qa_unavailable,
            tier=getattr(getattr(spec, "generation_tier", None), "value", None)
                or str(getattr(spec, "generation_tier", "") or ""),
            operation=operation,  # P1.3 PR-11
        )
        return QAResult(
            success=True,
            code=final_code,
            retries=qa_result.retries,
            issue_list=qa_result.issue_list,
        ), runtime_qa, runtime_retries, qa_warnings

    async def _run_contract_qa_loop(
        self,
        *,
        code: str,
        spec: GameSpec,
        runtime_contract: GameRuntimeContract,
        prompt_bundle_snapshot: Optional[dict[str, Any]],
        progress_cb: ProgressCallback,
        game_id: str,
        user_id: str,
        max_retries: Optional[int] = None,
    ) -> QAResult:
        current_code = self.qa_pipeline._apply_deterministic_repairs(code)
        errors = self._validate_contract_bundle(current_code, runtime_contract)
        repair_attempts = 0
        if errors:
            syntax_errors = [
                error
                for error in errors
                if self.qa_pipeline._classify_error_family(error) == SYNTAX_REPAIR_FAMILY
            ]
            if syntax_errors:
                repaired_code = await self.qa_pipeline.repair_code(
                    current_code,
                    syntax_errors,
                    game_spec=spec,
                    runtime_contract=runtime_contract,
                    prompt_bundle_snapshot=prompt_bundle_snapshot,
                    fix_round=1,
                    max_fix_rounds=1,
                )
                repaired_code = self.qa_pipeline._apply_deterministic_repairs(repaired_code)
                repaired_errors = self._validate_contract_bundle(repaired_code, runtime_contract)
                if len(repaired_errors) < len(errors):
                    current_code = repaired_code
                    errors = repaired_errors
                    repair_attempts = 1
                elif repaired_code != current_code:
                    current_code = repaired_code
                    errors = repaired_errors
                    repair_attempts = 1

        if not errors:
            return QAResult(
                success=True,
                code=current_code,
                retries=repair_attempts,
                issue_list=self.qa_pipeline.build_issue_list([], []),
            )

        logger.info(
            "Contract QA found %s issue(s); contract-stage remediation is disabled, signaling full regeneration",
            len(errors),
        )
        return QAResult(
            success=False,
            code=current_code,
            retries=repair_attempts,
            last_errors=errors,
            needs_regeneration=True,
            issue_list=self.qa_pipeline.build_issue_list(errors, []),
        )

    async def _run_runtime_qa_loop(
        self,
        *,
        code: str,
        runtime_contract: GameRuntimeContract,
        progress_cb: ProgressCallback,
        game_id: str,
        user_id: str,
        allow_runtime_qa_unavailable: bool = False,
        tier: Optional[str] = None,          # P1.3 PR-11
        operation: Optional[str] = None,     # P1.3 PR-11
    ) -> tuple[str, Any, int, list[dict[str, Any]]]:
        qa_warnings: list[dict[str, Any]] = []
        runtime_qa_timeout_s = self._resolve_runtime_qa_timeout(code)

        # P1.3 PR-11: when the flag is on AND should_defer approves this
        # (tier, operation) pair, fire-and-forget the runtime_qa coroutine
        # instead of awaiting it inline. The pipeline immediately proceeds
        # with an "all-clear" placeholder result so the user gets their code
        # without the 3-8s Playwright penalty. Background task records state
        # via runtime_qa_scheduler for later observation.
        try:
            deferred_enabled = getattr(settings, "P1_RUNTIME_QA_DEFERRED_ENABLED", False)
        except Exception:  # noqa: BLE001
            deferred_enabled = False
        if (
            deferred_enabled
            and _p1_should_defer is not None
            and _p1_schedule_runtime_qa is not None
            and _p1_should_defer(tier, operation)
        ):
            task_id = self._current_task_id() or f"{game_id}:runtime_qa"
            try:
                await _p1_schedule_runtime_qa(
                    task_id,
                    lambda code=code, to=runtime_qa_timeout_s: run_runtime_qa(
                        code, timeout_s=to
                    ),
                    tier=tier,
                    operation=operation,
                )
            except Exception:  # noqa: BLE001 - scheduler must never block hot path
                logger.debug(
                    "PR-11 schedule_runtime_qa failed; falling back to inline runtime QA",
                    exc_info=True,
                )
            else:
                self._notify(
                    progress_cb,
                    "runtime_simulation_qa",
                    99,
                    "Runtime QA deferred to background",
                    {
                        "gameId": game_id,
                        "userId": user_id,
                        "runtimeQaDeferred": True,
                        "tier": tier,
                        "operation": operation,
                    },
                )
                # P2.1 telemetry: count deferrals per (tier, operation).
                _p2_emit(
                    "runtime_qa_deferred",
                    tier=tier,
                    operation=operation,
                    task_id=task_id,
                )
                deferred_qa = RuntimeQAResult(
                    ran=True,
                    canvas_renders=True,
                    phase_metrics={"deferred": True, "reason": "pr11_scheduler"},
                )
                return code, deferred_qa, 0, qa_warnings

        runtime_qa = await run_runtime_qa(code, timeout_s=runtime_qa_timeout_s)
        if not runtime_qa.ran:
            unavailable_reason = getattr(runtime_qa, "unavailable_reason", None)
            unavailable_kind = getattr(runtime_qa, "unavailable_kind", None)
            unavailable_phase = getattr(runtime_qa, "unavailable_phase", None)
            if allow_runtime_qa_unavailable and unavailable_kind in {"timeout", "infra_unavailable", "exception"}:
                qa_warning = self._build_runtime_qa_warning(runtime_qa)
                qa_warnings = [qa_warning]
                self._notify(
                    progress_cb,
                    "runtime_simulation_qa",
                    98,
                    qa_warning["message"],
                    {
                        "gameId": game_id,
                        "userId": user_id,
                        "warningType": qa_warning["type"],
                        "unavailableKind": unavailable_kind,
                        "unavailablePhase": unavailable_phase,
                        "softFailed": True,
                    },
                )
                return code, runtime_qa, 0, qa_warnings
            unavailable_errors = self._runtime_qa_unavailable_errors(runtime_qa, code)
            if unavailable_errors:
                errors = unavailable_errors
            else:
                runtime_qa_report = self._serialize_runtime_qa(runtime_qa, [])
                if settings.RUNTIME_QA_REQUIRED or settings.ENVIRONMENT == "production":
                    raise PipelineExecutionError(
                        "Runtime QA unavailable: {}".format(
                            unavailable_reason or "Playwright is not installed or browser launch failed"
                        ),
                        stage="runtime_simulation_qa",
                        retry_count=0,
                        failure_family="qa_infra_unavailable",
                        artifacts=[
                            self._build_text_artifact(
                                artifact_type="failed_runtime_candidate",
                                payload=code,
                                metadata={
                                    "stage": "runtime_simulation_qa",
                                    "retryCount": 0,
                                    "timeoutS": runtime_qa_timeout_s,
                                    "unavailableReason": unavailable_reason,
                                },
                            ),
                            self._build_json_artifact(
                                artifact_type="runtime_qa_report",
                                payload={
                                    **runtime_qa_report,
                                    "retryCount": 0,
                                    "timeoutS": runtime_qa_timeout_s,
                                },
                                metadata={"stage": "runtime_simulation_qa"},
                            ),
                        ],
                    )
                return code, runtime_qa, 0, qa_warnings
        else:
            errors = self._runtime_qa_errors(runtime_qa, code)

        if not errors:
            return code, runtime_qa, 0, qa_warnings

        error_messages = "; ".join(error.message for error in errors[:5])
        raise PipelineExecutionError(
            f"Generated code failed runtime QA: {error_messages}",
            stage="runtime_simulation_qa",
            retry_count=0,
            failure_family="runtime_qa",
            artifacts=[
                self._build_text_artifact(
                    artifact_type="failed_runtime_candidate",
                    payload=code,
                    metadata={
                        "stage": "runtime_simulation_qa",
                        "retryCount": 0,
                    },
                ),
                self._build_json_artifact(
                    artifact_type="runtime_qa_report",
                    payload=self._serialize_runtime_qa(runtime_qa, errors),
                    metadata={"stage": "runtime_simulation_qa"},
                ),
            ],
        )

    def _resolve_runtime_qa_timeout(self, code: str) -> float:
        base_timeout = max(
            get_timeout_float(
                "timeout.ai_engine.runtime_qa.base_s",
                8.0,
                min_value=0.1,
            ),
            0.1,
        )
        code_size_bytes = len((code or "").encode("utf-8"))

        complexity_bonus = 0.0
        if code_size_bytes >= 16_000:
            complexity_bonus += get_timeout_float("timeout.ai_engine.runtime_qa.bonus_ge_16000_s", 2.0, min_value=0.0)
        if code_size_bytes >= 24_000:
            complexity_bonus += get_timeout_float("timeout.ai_engine.runtime_qa.bonus_ge_24000_s", 2.0, min_value=0.0)
        if code_size_bytes >= 32_000:
            complexity_bonus += get_timeout_float("timeout.ai_engine.runtime_qa.bonus_ge_32000_s", 10.0, min_value=0.0)
        if code_size_bytes >= 40_000:
            complexity_bonus += get_timeout_float("timeout.ai_engine.runtime_qa.bonus_ge_40000_s", 4.0, min_value=0.0)
        return min(
            get_timeout_float("timeout.ai_engine.runtime_qa.max_s", 30.0, min_value=0.1),
            base_timeout + complexity_bonus,
        )

    def _validate_contract_bundle(
        self,
        code: str,
        runtime_contract: GameRuntimeContract,
    ) -> list[QACheckError]:
        result = self.qa_pipeline.check(code)
        errors = list(result.errors)
        errors.extend(self._validate_runtime_contract(code, runtime_contract))
        deduped: list[QACheckError] = []
        seen: set[tuple[str, str]] = set()
        for error in errors:
            signature = (error.type, error.message)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(error)
        return deduped

    def _validate_runtime_contract(
        self,
        code: str,
        runtime_contract: GameRuntimeContract,
    ) -> list[QACheckError]:
        errors: list[QACheckError] = []
        lower = (code or "").lower()
        has_canvas_2d_context = re.search(
            r"getcontext\s*\(\s*['\"]2d['\"](?:\s*,[^\)]*)?\)",
            code,
            re.IGNORECASE,
        ) is not None
        has_webgl_context = re.search(
            r"getcontext\s*\(\s*['\"](?:webgl|webgl2)['\"](?:\s*,[^\)]*)?\)",
            code,
            re.IGNORECASE,
        ) is not None

        if runtime_contract.canvas.requires_canvas_2d:
            if not has_canvas_2d_context:
                errors.append(QACheckError(
                    type="contract_canvas",
                    message="Runtime contract requires an explicit Canvas 2D context",
                    severity="error",
                ))
        elif not (has_canvas_2d_context or (getattr(runtime_contract.canvas, "allow_webgl", False) and has_webgl_context)):
            errors.append(QACheckError(
                type="contract_canvas",
                message="Runtime contract requires an explicit canvas rendering context (Canvas 2D or WebGL)",
                severity="error",
            ))

        if "viewport" not in lower:
            errors.append(QACheckError(
                type="contract_mobile",
                message="Runtime contract requires a mobile viewport meta tag",
                severity="error",
            ))

        orientation = self._resolve_contract_orientation(runtime_contract)
        if not has_short_edge_scaling(code, orientation=orientation):
            requirement = "landscape-first" if orientation == "landscape_first" else "portrait-first"
            errors.append(QACheckError(
                type="contract_mobile",
                message=f"Runtime contract requires {requirement} short-edge UI scaling",
                severity="error",
            ))

        if any(mode in ("touch", "pointer") for mode in runtime_contract.input.required_modes):
            has_primary_input = any(
                any(pattern in lower for pattern in INPUT_EVENT_PATTERNS.get(mode, ()))
                for mode in runtime_contract.input.required_modes
                if mode in ("touch", "pointer")
            )
            if not has_primary_input:
                errors.append(QACheckError(
                    type="contract_input",
                    message="Runtime contract requires primary touch or pointer gameplay handlers",
                    severity="error",
                ))

        for required_state in runtime_contract.state.required_states:
            normalized_required_state = str(required_state or "").strip().lower()
            if normalized_required_state in {"game_over", "level_complete"}:
                continue
            if not has_required_state_presence(code, required_state, runtime_contract):
                errors.append(QACheckError(
                    type="contract_state",
                    message=f"Runtime contract requires state '{required_state}'",
                    severity="error",
                ))

        if runtime_contract.gameplay.requires_restart_entry and not has_restart_entry(code):
            errors.append(QACheckError(
                type="contract_gameplay",
                message="Runtime contract requires a restart entry point",
                severity="error",
            ))

        for api_name in runtime_contract.safety.forbidden_apis:
            if self._contains_forbidden_api(code, api_name):
                errors.append(QACheckError(
                    type="contract_safety",
                    message=f"Runtime contract forbids API usage: {api_name}",
                    severity="error",
                ))

        return errors

    @staticmethod
    def _contains_forbidden_api(code: str, api_name: str) -> bool:
        normalized = (api_name or "").strip()
        if not normalized:
            return False

        pattern = FORBIDDEN_API_PATTERNS.get(normalized)
        if pattern:
            if normalized == "Function":
                return re.search(pattern, code) is not None
            return re.search(pattern, code, re.IGNORECASE) is not None

        escaped = re.escape(normalized)
        return re.search(rf"(?<![\w$]){escaped}(?![\w$])", code, re.IGNORECASE) is not None

    def _runtime_qa_errors(self, runtime_qa: Any, code: str) -> list[QACheckError]:
        errors: list[QACheckError] = [
            QACheckError(type="runtime_qa", message=f"Runtime JS error: {message}", severity="error")
            for message in runtime_qa.js_errors[:3]
        ]
        if not runtime_qa.canvas_renders:
            errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected that the canvas never rendered",
                severity="error",
            ))
        input_handlers = (
            set(getattr(runtime_qa, "registered_input_handlers", []) or [])
            | set(getattr(runtime_qa, "direct_input_handlers", []) or [])
            | set(getattr(runtime_qa, "triggered_input_handlers", []) or [])
        )
        static_input_map = self.qa_pipeline._extract_input_handlers(code)
        has_static_inputs = any(
            static_input_map.get(family)
            for family in ("touch", "pointer", "mouse", "keyboard", "sensor")
        )
        if not input_handlers and not has_static_inputs:
            errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected no registered user input handlers",
                severity="error",
            ))
        visible_change_detected = bool(
            getattr(runtime_qa, "canvas_changed_after_input", False)
            or getattr(runtime_qa, "dom_changed_after_input", False)
        )
        if not visible_change_detected:
            errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected no visible state change after user interaction",
                severity="error",
            ))
        return errors

    def _runtime_qa_unavailable_errors(self, runtime_qa: Any, code: str) -> list[QACheckError]:
        unavailable_kind = str(getattr(runtime_qa, "unavailable_kind", "") or "").strip().lower()
        unavailable_phase = str(getattr(runtime_qa, "unavailable_phase", "") or "").strip().lower()
        if unavailable_kind != "timeout":
            return []

        errors: list[QACheckError] = []
        unavailable_reason = getattr(runtime_qa, "unavailable_reason", None) or "runtime QA timed out"
        static_input_map = self.qa_pipeline._extract_input_handlers(code)
        has_static_inputs = any(
            static_input_map.get(family)
            for family in ("touch", "pointer", "mouse", "keyboard", "sensor")
        )
        input_handlers = (
            set(getattr(runtime_qa, "registered_input_handlers", []) or [])
            | set(getattr(runtime_qa, "direct_input_handlers", []) or [])
            | set(getattr(runtime_qa, "triggered_input_handlers", []) or [])
        )
        visible_change_detected = bool(
            getattr(runtime_qa, "canvas_changed_after_input", False)
            or getattr(runtime_qa, "dom_changed_after_input", False)
        )
        interaction_performed = bool(getattr(runtime_qa, "interaction_performed", False))

        if unavailable_phase in {"interaction", "collect"}:
            if not input_handlers and not has_static_inputs:
                errors.append(QACheckError(
                    type="runtime_qa",
                    message="Runtime QA detected no registered user input handlers",
                    severity="error",
                ))
            if input_handlers or interaction_performed:
                message = (
                    "Runtime QA dispatched synthetic input but the game did not finish the first interaction quickly; "
                    "keep first-input handlers lightweight and make them produce an immediate visible state change"
                )
                if visible_change_detected:
                    message = (
                        "Runtime QA timed out while collecting post-interaction signals after input dispatch; "
                        "keep the first interaction lightweight and avoid long synchronous work on input handlers"
                    )
            else:
                message = (
                    "Runtime QA timed out during synthetic interaction; register gameplay handlers during boot "
                    "and make the first input cause an immediate visible state change"
                )
            errors.append(QACheckError(
                type="runtime_qa",
                message=message,
                severity="error",
            ))
        else:
            return []

        for message in list(getattr(runtime_qa, "js_errors", []) or [])[:3]:
            errors.insert(0, QACheckError(
                type="runtime_qa",
                message=f"Runtime JS error: {message}",
                severity="error",
            ))

        logger.warning(
            "Treating runtime QA timeout as repairable runtime failure: phase=%s reason=%s",
            unavailable_phase,
            unavailable_reason,
        )
        return errors

    @staticmethod
    def _build_text_artifact(
        *,
        artifact_type: str,
        payload: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "artifact_type": artifact_type,
            "content_type": "text/html",
            "payload": payload,
            "metadata": metadata or {},
        }

    @staticmethod
    def _build_json_artifact(
        *,
        artifact_type: str,
        payload: dict[str, Any],
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "artifact_type": artifact_type,
            "content_type": "application/json",
            "payload": payload,
            "metadata": metadata or {},
        }

    @staticmethod
    def _serialize_errors(errors: list[QACheckError]) -> list[dict[str, Any]]:
        return [
            {
                "type": enriched.type,
                "message": enriched.message,
                "severity": enriched.severity,
                "family": enriched.family or "generic",
                "blocking": bool(enriched.blocking if enriched.blocking is not None else True),
                "repairHint": enriched.repair_hint or "",
                **(
                    {"location": enriched.location.model_dump(exclude_none=True)}
                    if enriched.location is not None
                    else {}
                ),
            }
            for enriched in (QAPipeline.enrich_issue(error) for error in errors)
        ]

    @staticmethod
    def _build_runtime_qa_warning(runtime_qa: Any) -> dict[str, Any]:
        unavailable_reason = getattr(runtime_qa, "unavailable_reason", None) or "runtime QA unavailable"
        return {
            "type": "runtime_qa_unavailable",
            "severity": "warning",
            "family": "runtime_startup",
            "blocking": False,
            "repairHint": "Retry runtime QA later or inspect the runtime environment if Playwright/browser infrastructure is unavailable.",
            "message": f"Runtime QA unavailable: {unavailable_reason}",
            "kind": getattr(runtime_qa, "unavailable_kind", None),
            "phase": getattr(runtime_qa, "unavailable_phase", None),
            "softFailed": True,
        }

    def _serialize_runtime_qa(
        self,
        runtime_qa: Any,
        errors: list[QACheckError],
    ) -> dict[str, Any]:
        return {
            "ran": bool(getattr(runtime_qa, "ran", False)),
            "canvasRenders": bool(getattr(runtime_qa, "canvas_renders", False)),
            "canvasChangedAfterInput": bool(getattr(runtime_qa, "canvas_changed_after_input", False)),
            "domChangedAfterInput": bool(getattr(runtime_qa, "dom_changed_after_input", False)),
            "interactionPerformed": bool(getattr(runtime_qa, "interaction_performed", False)),
            "registeredInputHandlers": list(getattr(runtime_qa, "registered_input_handlers", []) or []),
            "directInputHandlers": list(getattr(runtime_qa, "direct_input_handlers", []) or []),
            "triggeredInputHandlers": list(getattr(runtime_qa, "triggered_input_handlers", []) or []),
            "jsErrors": list(getattr(runtime_qa, "js_errors", []) or []),
            "fps": float(getattr(runtime_qa, "fps", 0.0) or 0.0),
            "loadTimeMs": int(getattr(runtime_qa, "load_time_ms", 0) or 0),
            "unavailableReason": getattr(runtime_qa, "unavailable_reason", None),
            "unavailableKind": getattr(runtime_qa, "unavailable_kind", None),
            "unavailablePhase": getattr(runtime_qa, "unavailable_phase", None),
            "phaseMetrics": dict(getattr(runtime_qa, "phase_metrics", {}) or {}),
            "errors": self._serialize_errors(errors),
        }

    def _summarize_current_code(self, current_code: str) -> str:
        title_match = re.search(r"<title>([^<]{1,80})</title>", current_code or "", re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else "Untitled game"
        hints: list[str] = []
        lower = (current_code or "").lower()
        if "score" in lower:
            hints.append("has scoring")
        if "enemy" in lower or "obstacle" in lower:
            hints.append("contains hazards")
        if "touchstart" in lower or "pointerdown" in lower:
            hints.append("uses touch controls")
        if "requestanimationframe" in lower:
            hints.append("runs an animated loop")
        if "lane" in lower:
            hints.append("lane-based movement")
        if "grid" in lower:
            hints.append("grid interactions")
        return f"{title}; " + ", ".join(hints[:4]) if hints else title

    @staticmethod
    def _summarize_source_spec(source_spec: Optional[GameSpec]) -> str:
        if not source_spec:
            return ""

        mechanic = source_spec.intent_summary or (
            source_spec.core_mechanics[0].type if source_spec.core_mechanics else source_spec.game_type
        )
        return "; ".join(
            part
            for part in [
                f"type={source_spec.game_type}",
                f"theme={source_spec.visual_style.theme}",
                f"mechanic={mechanic}",
                f"win={source_spec.rules.win_condition}",
                f"progression={source_spec.progression_shape}" if source_spec.progression_shape else "",
                f"reward_loop={source_spec.reward_loop}" if source_spec.reward_loop else "",
                f"signature_moment={source_spec.signature_moment}" if source_spec.signature_moment else "",
                f"tone={source_spec.tone}" if source_spec.tone else "",
                f"complexity_budget={source_spec.complexity_budget}" if source_spec.complexity_budget else "",
                f"ui_language={source_spec.ui_language}",
            ]
            if part
        )

    @staticmethod
    def _summarize_source_bundle_context(source_bundle_context: Optional[SourceBundleContext]) -> str:
        if not source_bundle_context:
            return ""

        segments = [
            f"title={source_bundle_context.title}" if source_bundle_context.title else "",
            (
                f"bundle_version={source_bundle_context.latest_bundle_version}"
                if source_bundle_context.latest_bundle_version is not None
                else ""
            ),
            f"game_type={source_bundle_context.latest_game_type}" if source_bundle_context.latest_game_type else "",
            (
                f"latest_iteration_type={source_bundle_context.latest_iteration_type}"
                if source_bundle_context.latest_iteration_type
                else ""
            ),
            source_bundle_context.summary or "",
        ]
        revision_lines = [
            ", ".join(
                item
                for item in [
                    f"v{revision.version}" if revision.version is not None else "",
                    revision.iteration_type or "",
                    revision.summary or "",
                ]
                if item
            )
            for revision in (source_bundle_context.recent_revisions or [])[:3]
        ]
        if revision_lines:
            segments.append("recent_revisions=" + " | ".join(line for line in revision_lines if line))
        return "; ".join(segment for segment in segments if segment)

    @staticmethod
    def _feedback_mentions_any(feedback: str, markers: tuple[str, ...]) -> bool:
        lowered = (feedback or "").lower()
        return any(marker in lowered for marker in markers)

    def _build_iteration_fallback_spec(
        self,
        *,
        base_spec: GameSpec,
        feedback: str,
        title: Optional[str],
        source_bundle_context: SourceBundleContext,
    ) -> GameSpec:
        fallback = base_spec.model_copy(deep=True)
        fallback.source_description = feedback
        fallback.intent_summary = self._build_iteration_intent_summary(base_spec, feedback, title)
        fallback.special_rules = self._merge_special_rules(
            base_spec.special_rules,
            self._derive_feedback_rules(feedback, base_spec.ui_language),
            [source_bundle_context.latest_feedback] if source_bundle_context.latest_feedback else [],
        )
        return fallback

    def _merge_iteration_spec(
        self,
        *,
        base_spec: Optional[GameSpec],
        parsed_spec: GameSpec,
        feedback: str,
        title: Optional[str],
        source_bundle_context: SourceBundleContext,
    ) -> GameSpec:
        if not base_spec:
            parsed_spec.intent_summary = self._build_iteration_intent_summary(parsed_spec, feedback, title)
            parsed_spec.special_rules = self._merge_special_rules(
                parsed_spec.special_rules,
                self._derive_feedback_rules(feedback, parsed_spec.ui_language),
            )
            return parsed_spec

        merged = base_spec.model_copy(deep=True)
        merged.source_description = feedback
        merged.intent_summary = self._build_iteration_intent_summary(base_spec, feedback, title)
        merged.reference_game = parsed_spec.reference_game or merged.reference_game
        merged.ui_language = base_spec.ui_language or parsed_spec.ui_language
        merged.difficulty_curve = parsed_spec.difficulty_curve or merged.difficulty_curve
        merged.special_rules = self._merge_special_rules(
            base_spec.special_rules,
            parsed_spec.special_rules,
            self._derive_feedback_rules(feedback, merged.ui_language),
            [source_bundle_context.latest_feedback] if source_bundle_context.latest_feedback else [],
        )

        if parsed_spec.game_type != base_spec.game_type and self._feedback_mentions_any(
            feedback,
            ("redesign", "overhaul", "change genre", "different game", "改成", "换成", "重做", "大改"),
        ):
            merged.game_type = parsed_spec.game_type
            merged.core_mechanics = parsed_spec.core_mechanics or merged.core_mechanics
            merged.entities = parsed_spec.entities or merged.entities
            merged.visual_style = parsed_spec.visual_style or merged.visual_style
            merged.rules = parsed_spec.rules or merged.rules
            merged.platform_constraints = parsed_spec.platform_constraints or merged.platform_constraints
            return merged

        if self._feedback_mentions_any(
            feedback,
            ("theme", "style", "visual", "art", "skin", "look", "界面", "主题", "风格", "美术", "视觉"),
        ):
            merged.visual_style = parsed_spec.visual_style or merged.visual_style

        if self._feedback_mentions_any(
            feedback,
            ("control", "input", "tap", "swipe", "drag", "touch", "操作", "控制", "点击", "滑动", "拖拽"),
        ):
            merged.platform_constraints = parsed_spec.platform_constraints or merged.platform_constraints
            merged.core_mechanics = parsed_spec.core_mechanics or merged.core_mechanics

        if self._feedback_mentions_any(
            feedback,
            ("goal", "win", "lose", "score", "lives", "objective", "胜利", "失败", "得分", "生命", "目标"),
        ):
            merged.rules = parsed_spec.rules or merged.rules

        if self._feedback_mentions_any(
            feedback,
            ("mechanic", "rule", "level", "stage", "enemy", "obstacle", "玩法", "规则", "关卡", "敌人", "障碍"),
        ):
            merged.core_mechanics = parsed_spec.core_mechanics or merged.core_mechanics

        return merged

    @staticmethod
    def _build_iteration_intent_summary(base_spec: GameSpec, feedback: str, title: Optional[str]) -> str:
        feedback_summary = re.sub(r"\s+", " ", (feedback or "").strip())
        if title and feedback_summary:
            return f"{title}: {feedback_summary[:160]}"
        if feedback_summary:
            return feedback_summary[:160]
        return base_spec.intent_summary

    @staticmethod
    def _merge_special_rules(*groups: Optional[list[str]]) -> list[str]:
        merged: list[str] = []
        for group in groups:
            for rule in group or []:
                text = re.sub(r"\s+", " ", str(rule or "").strip())
                if text and text not in merged:
                    merged.append(text)
        return merged

    @staticmethod
    def _derive_feedback_rules(feedback: str, ui_language: str) -> list[str]:
        text = re.sub(r"\s+", " ", (feedback or "").strip())
        if not text:
            return []

        rules: list[str] = []
        level_match = re.search(r"(?<!\d)(\d{1,2})\s*(?:levels?|stages?)\b", text, re.IGNORECASE)
        if not level_match:
            level_match = re.search(r"(?<!\d)(\d{1,2})\s*(?:个)?关卡", text)
        if level_match:
            count = int(level_match.group(1))
            rules.append(f"包含{count}个关卡" if ui_language == "zh-CN" else f"Include {count} levels")
        return rules

    @staticmethod
    def _is_retryable_generation_error(exc: Exception) -> bool:
        message = str(exc).lower()
        retryable_markers = (
            "timeout",
            "timed out",
            "readtimeout",
            "temporarily unavailable",
            "rate limit",
            "connection reset",
            "socket",
            "network",
        )
        return any(marker in message for marker in retryable_markers)

    def _start_generation_progress_heartbeat(
        self,
        progress_cb: ProgressCallback,
        *,
        game_id: str,
        user_id: str,
        runtime_profile: str,
        attempt: int,
        max_attempts: int,
    ) -> Optional["asyncio.Task[None]"]:
        if progress_cb is None:
            return None
        if not getattr(settings, "GENERATION_PROGRESS_HEARTBEAT_ENABLED", True):
            return None
        return asyncio.create_task(
            self._generation_progress_heartbeat_loop(
                progress_cb,
                game_id=game_id,
                user_id=user_id,
                runtime_profile=runtime_profile,
                attempt=attempt,
                max_attempts=max_attempts,
            )
        )

    async def _generation_progress_heartbeat_loop(
        self,
        progress_cb: ProgressCallback,
        *,
        game_id: str,
        user_id: str,
        runtime_profile: str,
        attempt: int,
        max_attempts: int,
    ) -> None:
        started = time.monotonic()
        span = GENERATION_PROGRESS_HEARTBEAT_MAX_PCT - GENERATION_PROGRESS_HEARTBEAT_BASE_PCT
        last_pct = GENERATION_PROGRESS_HEARTBEAT_BASE_PCT
        while True:
            await asyncio.sleep(GENERATION_PROGRESS_HEARTBEAT_INTERVAL_S)
            elapsed_s = time.monotonic() - started
            pct = GENERATION_PROGRESS_HEARTBEAT_BASE_PCT + min(
                span,
                int(span * elapsed_s / GENERATION_PROGRESS_HEARTBEAT_EXPECTED_DURATION_S),
            )
            # Monotonic within one attempt, capped at MAX_PCT so heartbeats
            # never overrun the contract_qa progress band.
            pct = max(pct, last_pct)
            last_pct = pct
            try:
                # Intentionally bypass _notify: heartbeats must not reset the
                # PR-06 logic_generate stage-start timestamp on every tick.
                progress_cb(
                    "logic_generate",
                    pct,
                    f"Generating game code ({int(elapsed_s)}s elapsed)",
                    {
                        "gameId": game_id,
                        "userId": user_id,
                        "runtimeProfile": runtime_profile,
                        "heartbeat": True,
                        "attempt": attempt,
                        "maxAttempts": max_attempts,
                    },
                )
            except Exception:  # noqa: BLE001 - heartbeat must never crash the pipeline
                logger.debug(
                    "Generation progress heartbeat callback failed; stopping heartbeat",
                    exc_info=True,
                )
                return

    @staticmethod
    async def _stop_generation_progress_heartbeat(
        task: Optional["asyncio.Task[None]"],
    ) -> None:
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - heartbeat teardown must never raise
            pass

    @staticmethod
    def _launch_concurrent_review(
        review_factory: Optional[Callable[[str], Any]],
        review_state: Optional[dict[str, Any]],
        code: str,
    ) -> None:
        """Start the LLM code review concurrently with runtime simulation QA.

        Runtime QA never mutates the candidate code, so both consume the same
        post-contract-QA HTML. The launched task is stashed in review_state so
        the create path can await (or discard) it after runtime QA settles.
        """
        if review_factory is None or review_state is None:
            return
        if review_state.get("task") is not None:
            return
        try:
            review_state["task"] = asyncio.create_task(review_factory(code))
            review_state["code"] = code
        except Exception:  # noqa: BLE001 - fall back to serial review
            review_state.pop("task", None)
            logger.debug(
                "Failed to launch concurrent code review; serial review will run instead",
                exc_info=True,
            )

    @staticmethod
    async def _discard_concurrent_review(review_state: Optional[dict[str, Any]]) -> None:
        task = review_state.pop("task", None) if review_state else None
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _resolve_concurrent_review(
        self,
        review_state: Optional[dict[str, Any]],
        code: str,
    ) -> LLMReviewResult:
        task = review_state.pop("task", None) if review_state else None
        if task is not None:
            if review_state.get("code") == code:
                return await task
            # The candidate changed after the review was launched; discard the
            # stale review and re-run against the final code.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return await self.code_reviewer.review(code)

    def _notify(
        self,
        progress_cb: ProgressCallback,
        stage: str,
        pct: int,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        # PR-06: mark stage transition so we can emit structured stage-timings on completion.
        self._record_stage_start(self._current_task_id(), stage)
        if progress_cb:
            progress_cb(stage, pct, message, details or {})

    # --- PR-06: stage-timing observability baseline ----------------------------
    # Maintains a per-run map of stage → (started_ms, ended_ms) so we can emit
    # structured stage-duration log lines at the end of a pipeline run.  The
    # map is stored on the running request via a weak map keyed by task_id so
    # we never cross-contaminate between concurrent requests.
    _stage_timings_by_task: "dict[str, dict[str, dict[str, float]]]" = {}

    @classmethod
    def _record_stage_start(cls, task_id: Optional[str], stage: str) -> None:
        if not task_id:
            return
        now = time.time() * 1000
        bucket = cls._stage_timings_by_task.setdefault(task_id, {})
        # Close the previous open stage (if any) so adjacent stages have contiguous timings.
        for existing_stage, record in bucket.items():
            if record.get("ended_ms") is None:
                record["ended_ms"] = now
        bucket[stage] = {"started_ms": now, "ended_ms": None}

    @classmethod
    def _flush_stage_timings(cls, task_id: Optional[str], *, status: str) -> None:
        if not task_id:
            return
        bucket = cls._stage_timings_by_task.pop(task_id, None)
        if not bucket:
            return
        now = time.time() * 1000
        entries: list[dict[str, Any]] = []
        total_ms = 0.0
        for stage, record in bucket.items():
            started = float(record.get("started_ms") or 0.0)
            ended = float(record.get("ended_ms") or now)
            duration_ms = max(0.0, ended - started)
            total_ms += duration_ms
            entries.append({
                "stage": stage,
                "started_ms": int(started),
                "ended_ms": int(ended),
                "duration_ms": int(duration_ms),
            })
        entries.sort(key=lambda item: item.get("started_ms") or 0)
        logger.info(
            "pipeline_v2.stage_timings task_id=%s status=%s total_ms=%d breakdown=%s",
            task_id,
            status,
            int(total_ms),
            entries,
        )
