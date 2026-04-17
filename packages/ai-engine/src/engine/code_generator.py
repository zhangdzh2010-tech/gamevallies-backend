"""Stage 05: LLM-only HTML5 game code generation."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..api.models import (
    GDD,
    GameRuntimeContract,
    GameSpec,
    GenerateCodeResult,
    IterationType,
)
from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from ..services.llm_client import LLMClient
from .code_template_cache import CodeTemplateCache
from .prompt_store import get_prompt, get_runtime_profile, require_prompt
from .runtime_profile_ids import normalize_runtime_profile_id
from .section_patch import (
    PATCH_SECTION_BODY,
    PATCH_SECTION_SCRIPT,
    PATCH_SECTION_STYLE,
    SectionPatch,
    apply_section_patches,
    build_patch_protocol,
    build_section_context,
    ensure_structured_section_markers,
    parse_patch_response,
    validate_patch_candidate,
)
from .visual_pack_catalog import get_visual_pack, visual_pack_direction_lines

# P1.1 GAP-1b / P1.3 PR-12: optional DiversityPlanner + template-inspiration
# imports. Wrapped in try/except to keep code_generator importable in minimal
# environments where the P1 modules might not be present yet.
try:  # pragma: no cover - import guard
    from .diversity_planner import plan_for as _p1_plan_for  # type: ignore
except Exception:  # pragma: no cover
    _p1_plan_for = None  # type: ignore

try:  # pragma: no cover - import guard
    from .template_inspiration import (  # type: ignore
        decide_lane as _p1_decide_lane,
        select_inspiration as _p1_select_inspiration,
        render_inspiration_block as _p1_render_inspiration_block,
    )
except Exception:  # pragma: no cover
    _p1_decide_lane = None  # type: ignore
    _p1_select_inspiration = None  # type: ignore
    _p1_render_inspiration_block = None  # type: ignore

try:  # pragma: no cover - import guard (PR-07 wire-up)
    from .creative_anchors import (  # type: ignore
        CreativeAnchors as _p1_CreativeAnchors,
        build_anchors_fallback as _p1_build_anchors_fallback,
    )
except Exception:  # pragma: no cover
    _p1_CreativeAnchors = None  # type: ignore
    _p1_build_anchors_fallback = None  # type: ignore

try:  # pragma: no cover - import guard (P2.3 inspiration guard)
    from .p2_inspiration_guard import (  # type: ignore
        should_skip as _p2_inspiration_should_skip,
        note_lane_decision as _p2_inspiration_note_decision,
    )
except Exception:  # pragma: no cover
    def _p2_inspiration_should_skip(tier):  # type: ignore
        return False
    def _p2_inspiration_note_decision(key, tier, *, hit):  # type: ignore
        return None

try:  # pragma: no cover - import guard (P2.1 telemetry)
    from .p2_telemetry import emit as _p2_emit  # type: ignore
except Exception:  # pragma: no cover
    def _p2_emit(event: str, **fields):  # type: ignore
        return None

logger = logging.getLogger(__name__)


class _SafePromptFormatDict(dict):
    """Preserve unknown placeholders instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"

GAME_TYPE_CORE_MECHANIC_SUMMARY: Dict[str, str] = {
    "casual": "Deliver one readable arcade loop with quick feedback and a clear round goal.",
    "puzzle": "Solve a compact logic or board problem with clear player feedback.",
    "educational": "Teach or reinforce one learning objective through a short interactive challenge.",
    "funny": "Build around one surprising or comedic interaction that stays readable on mobile.",
}

LOCALIZED_CORE_MECHANIC_SUMMARY: Dict[str, Dict[str, str]] = {
    "zh-CN": {
        "casual": "用简洁清晰的休闲玩法循环，让反馈快、目标明确。",
        "puzzle": "通过紧凑的逻辑或棋盘挑战来完成目标。",
        "educational": "把学习目标变成一个短平快的交互挑战。",
        "funny": "围绕一个好懂又有梗的搞笑交互展开。",
    },
}

UI_LANGUAGE_LABELS: Dict[str, str] = {
    "en-US": "English",
    "zh-CN": "Simplified Chinese",
}

EDUCATIONAL_REQUEST_MARKERS: tuple[str, ...] = (
    "classroom",
    "teacher",
    "lesson",
    "quiz",
    "worksheet",
    "practice",
    "practice question",
    "learning game",
    "teaching",
    "knowledge point",
    "课堂",
    "教学",
    "老师",
    "练习题",
    "知识点",
    "问答",
    "测验",
    "小测",
    "学习游戏",
    "教学游戏",
)

PLAYER_SIZE_BY_GAME_TYPE: Dict[str, tuple[int, int]] = {
    "casual": (40, 40),
    "puzzle": (56, 56),
    "educational": (52, 52),
    "funny": (44, 44),
}

_GENERIC_SPECIAL_RULE_PHRASES: tuple[str, ...] = (
    "restart after losing",
    "restart available",
    "restart available anytime",
    "restart button",
    "restart function",
    "click to start",
    "tap to start",
    "start hint",
    "win state",
    "lose state",
    "victory screen",
    "failure screen",
    "failure settlement",
    "game over screen",
    "show final score",
    "novice operation prompts",
    "operation prompts",
    "clear novice operation prompts",
    "provide clear novice operation prompts",
    "点击开始",
    "开始提示",
    "失败后可以重新开始",
    "重新开始按钮",
    "重新开始功能",
    "失败页",
    "胜利页",
    "失败结算",
    "新手提示",
    "关卡提示",
    "阶段进度",
    "剩余步数",
)

_STANDARD_COMPLEXITY_KEYWORDS: tuple[str, ...] = (
    "combo",
    "meter",
    "wave",
    "waves",
    "projectile",
    "projectiles",
    "shoot",
    "shooter",
    "boss",
    "attack",
    "enemy",
    "enemies",
    "stage progress",
    "health bar",
    "combo meter",
    "三波",
    "波次",
    "boss",
    "攻击",
    "子弹",
    "敌人",
    "血量",
    "进度",
)

_COMPLEX_COMPLEXITY_KEYWORDS: tuple[str, ...] = (
    "boss",
    "mini boss",
    "three waves",
    "consecutive enemy waves",
    "energy is used to attack",
    "defeat all",
    "escort",
    "rescue",
    "quiz",
    "classroom",
    "worksheet",
    "pathfinding",
    "route planning",
    "inventory",
    "craft",
    "procedural",
    "mini boss",
    "击败",
    "boss",
    "三波敌人",
    "能量",
    "课堂",
    "问答",
)

class CodeGenerator:
    """Stage 05: LLM-only HTML5 game code generator."""

    def __init__(self, llm_mode: str = "real") -> None:
        self.llm_mode = llm_mode
        self._client = LLMClient()
        self.template_cache = CodeTemplateCache()

    @staticmethod
    def _long_generation_timeout_s() -> int:
        return get_timeout_int(
            "timeout.ai_engine.llm_long_generation_s",
            300,
            min_value=30,
        )

    @staticmethod
    def _generation_request_timeout_budget_s(
        spec: Optional[GameSpec] = None,
        budget_override: Optional[str] = None,
    ) -> int:
        # Keep create-generation timeout semantics aligned with the single
        # coarse-grained admin control. If the configured value is 300s, the
        # create mainline should really get 300s instead of being silently
        # clipped down by hidden budget-profile caps like 105s.
        return max(30, CodeGenerator._long_generation_timeout_s())

    @classmethod
    def _generation_overall_timeout_budget_s(
        cls,
        spec: Optional[GameSpec] = None,
        budget_override: Optional[str] = None,
    ) -> int:
        request_timeout_s = cls._generation_request_timeout_budget_s(spec, budget_override)
        return max(request_timeout_s, cls._long_generation_timeout_s())

    @staticmethod
    def _step_timeout_override_key(step_key: str, timeout_kind: str) -> Optional[str]:
        return {
            "iterate.classify": f"timeout.ai_engine.iterate.classify_{timeout_kind}_s",
            "iterate.param_adjust": f"timeout.ai_engine.iterate.param_adjust_{timeout_kind}_s",
            "iterate.element_change": f"timeout.ai_engine.iterate.element_change_{timeout_kind}_s",
            "iterate.mechanic_change": f"timeout.ai_engine.iterate.mechanic_change_{timeout_kind}_s",
        }.get(str(step_key or "").strip())

    @classmethod
    def _resolve_step_request_timeout_s(
        cls,
        step_key: str,
        *,
        spec: Optional[GameSpec] = None,
        budget_override: Optional[str] = None,
        default_timeout_s: Optional[int] = None,
    ) -> int:
        fallback = max(
            30,
            int(default_timeout_s or cls._generation_request_timeout_budget_s(spec, budget_override)),
        )
        timeout_key = cls._step_timeout_override_key(step_key, "request")
        if not timeout_key:
            return fallback
        return get_timeout_int(timeout_key, fallback, min_value=30)

    @classmethod
    def _resolve_step_overall_timeout_s(
        cls,
        step_key: str,
        *,
        spec: Optional[GameSpec] = None,
        budget_override: Optional[str] = None,
        request_timeout_s: Optional[int] = None,
        default_timeout_s: Optional[int] = None,
    ) -> int:
        effective_request_timeout_s = max(
            30,
            int(
                request_timeout_s
                or cls._resolve_step_request_timeout_s(
                    step_key,
                    spec=spec,
                    budget_override=budget_override,
                )
            ),
        )
        fallback = max(
            effective_request_timeout_s,
            int(default_timeout_s or cls._generation_overall_timeout_budget_s(spec, budget_override)),
        )
        timeout_key = cls._step_timeout_override_key(step_key, "overall")
        if not timeout_key:
            return fallback
        return get_timeout_int(timeout_key, fallback, min_value=effective_request_timeout_s)

    @classmethod
    def _generation_provider_hedge_delay_s(
        cls,
        spec: Optional[GameSpec] = None,
        budget_override: Optional[str] = None,
    ) -> Optional[int]:
        if not getattr(settings, "LLM_PROVIDER_HEDGING_ENABLED", False):
            return None
        budget = str(budget_override or cls._resolve_budget_profile(spec)).strip().lower() or "standard"
        if budget in {"safe", "simple"}:
            return None
        return max(1, int(getattr(settings, "LLM_PROVIDER_HEDGING_DELAY_S", 45) or 45))

    @staticmethod
    def _resolve_generation_tier(spec: Optional[GameSpec]) -> str:
        raw_value = getattr(spec, "generation_tier", "standard")
        value = str(getattr(raw_value, "value", raw_value) or "standard").strip().lower()
        if value in {"safe", "showcase"}:
            return value
        return "standard"

    @staticmethod
    def _is_support_rule(rule: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(rule or "").strip().lower())
        if not normalized:
            return True
        if any(phrase in normalized for phrase in _GENERIC_SPECIAL_RULE_PHRASES):
            return True
        if ("score" in normalized or "分数" in normalized) and any(
            marker in normalized
            for marker in ("display", "shown", "show", "counter", "final score", "显示", "实时", "结算")
        ):
            return True
        if ("health" in normalized or "血量" in normalized) and any(
            marker in normalized for marker in ("display", "bar", "shown", "show", "显示")
        ):
            return True
        if ("wave" in normalized or "波次" in normalized) and any(
            marker in normalized for marker in ("prompt", "display", "shown", "提示", "显示")
        ):
            return True
        return False

    @classmethod
    def _meaningful_rule_count(cls, spec: Optional[GameSpec]) -> int:
        if spec is None:
            return 0
        return sum(1 for rule in (spec.special_rules or []) if not cls._is_support_rule(rule))

    @classmethod
    def _resolve_budget_profile(cls, spec: Optional[GameSpec]) -> str:
        if spec is None:
            return "standard"

        generation_tier = cls._resolve_generation_tier(spec)
        if generation_tier in {"safe", "showcase"}:
            return generation_tier

        entity_count = len(spec.entities or [])
        mechanic_count = len(spec.core_mechanics or [])
        meaningful_rule_count = cls._meaningful_rule_count(spec)
        game_type = (spec.game_type or "").strip().lower()
        context_text = " ".join(
            part
            for part in (
                spec.source_description,
                spec.intent_summary,
                spec.rules.win_condition if spec.rules else "",
                spec.reward_loop,
                spec.signature_moment,
                spec.reference_game,
                " ".join(spec.special_rules or []),
            )
            if str(part or "").strip()
        ).lower()

        score = 0
        if game_type == "educational":
            score += 2
        elif game_type == "puzzle":
            score += 1

        if entity_count >= 5:
            score += 2
        elif entity_count >= 4:
            score += 1

        if mechanic_count >= 2:
            score += 2

        if meaningful_rule_count >= 4:
            score += 2
        elif meaningful_rule_count >= 2:
            score += 1

        if any(keyword in context_text for keyword in _STANDARD_COMPLEXITY_KEYWORDS):
            score += 1
        if any(keyword in context_text for keyword in _COMPLEX_COMPLEXITY_KEYWORDS):
            score += 2

        simple_candidate = (
            game_type in {"casual", "funny"}
            and entity_count <= 3
            and mechanic_count <= 1
            and score <= 1
        )
        puzzle_simple_candidate = (
            game_type == "puzzle"
            and entity_count <= 2
            and mechanic_count <= 1
            and score <= 2
        )
        if simple_candidate or puzzle_simple_candidate:
            return "simple"
        if score >= 5:
            return "complex"
        return "standard"

    @staticmethod
    def _select_token_budget(spec: Optional[GameSpec] = None, budget_override: Optional[str] = None) -> int:
        """Select token budget based on game complexity or explicit override."""
        if budget_override:
            mapping = {
                "simple": settings.LLM_GENERATION_TOKEN_BUDGET_SIMPLE,
                "standard": settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD,
                "complex": settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX,
                "safe": min(settings.LLM_GENERATION_TOKEN_BUDGET_SIMPLE, 4096),
                "showcase": max(
                    settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX,
                    settings.LLM_LONG_GENERATION_MAX_TOKENS,
                ),
            }
            return max(1024, mapping.get(budget_override, settings.LLM_LONG_GENERATION_MAX_TOKENS))

        if spec is None:
            return max(1024, settings.LLM_LONG_GENERATION_MAX_TOKENS)

        budget_profile = CodeGenerator._resolve_budget_profile(spec)
        mapping = {
            "safe": min(settings.LLM_GENERATION_TOKEN_BUDGET_SIMPLE, 4096),
            "simple": settings.LLM_GENERATION_TOKEN_BUDGET_SIMPLE,
            "standard": settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD,
            "complex": settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX,
            "showcase": max(
                settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX,
                settings.LLM_LONG_GENERATION_MAX_TOKENS,
            ),
        }
        return max(1024, mapping.get(budget_profile, settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD))

    @classmethod
    def _select_truncation_retry_cap(
        cls,
        spec: Optional[GameSpec] = None,
        budget_override: Optional[str] = None,
    ) -> int:
        """Keep truncation retries aligned with the selected budget profile instead of always expanding to the max cap."""
        token_budget = cls._select_token_budget(spec, budget_override)
        profile = str(budget_override or cls._resolve_budget_profile(spec)).strip().lower() or "standard"
        retry_cap_mapping = {
            "safe": 4096,
            "simple": 8192,
            "standard": 14336,
            "complex": settings.LLM_LONG_GENERATION_MAX_TOKENS,
            "showcase": settings.LLM_LONG_GENERATION_MAX_TOKENS,
        }
        return max(token_budget, retry_cap_mapping.get(profile, token_budget))

    @staticmethod
    def _response_size_hint_from_budget(token_budget: int) -> str:
        if token_budget >= max(
            settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX,
            settings.LLM_LONG_GENERATION_MAX_TOKENS,
        ):
            return "xlarge"
        if token_budget >= settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD:
            return "large"
        if token_budget <= settings.LLM_GENERATION_TOKEN_BUDGET_SIMPLE:
            return "medium"
        return "large"

    @staticmethod
    def _create_response_size_hint() -> str:
        return "full_document"

    @staticmethod
    def _is_prompt_bullet_line(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("- ") or stripped.startswith("* ")

    # PR-03: keywords to strip when computing a bullet's dedup key so that
    # near-duplicates like "MUST use X" and "Use X" collapse together.
    _BULLET_KEY_PREFIX_STRIP = re.compile(
        r"^(?:[-*]\s+)?"
        r"(?:must(?:\s+not)?|should(?:\s+not)?|required|requirement|important|note|tip|warning|caution|"
        r"避免|禁止|必须|需要|确保|保证|注意|重要)"
        r"[:：,， ]\s*",
        re.IGNORECASE,
    )
    _BULLET_KEY_PUNCT_STRIP = re.compile(r"[\s。\.,;:!?\-\*\_`]+")

    @classmethod
    def _bullet_dedup_key(cls, line: str) -> str:
        """Produce a normalized dedup key for a bullet line (PR-03).

        Strips leading markers, common imperative prefixes (MUST / 必须 / etc.),
        collapses whitespace, and removes trailing punctuation so that
        stylistic variations of the same rule collapse into a single key.
        """
        key = line.strip()
        key = re.sub(r"^[-*]\s+", "", key)
        key = cls._BULLET_KEY_PREFIX_STRIP.sub("", key)
        key = re.sub(r"\s+", " ", key).lower()
        key = cls._BULLET_KEY_PUNCT_STRIP.sub(" ", key).strip()
        return key

    @classmethod
    def _compact_prompt_section(
        cls,
        text: str,
        *,
        seen_bullets: Optional[set[str]] = None,
    ) -> str:
        lines = text.splitlines()
        compacted: List[str] = []
        pending_blank = False
        in_code_fence = False

        for raw_line in lines:
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                pending_blank = bool(compacted)
                continue
            if stripped.startswith("```"):
                if pending_blank and compacted and compacted[-1] != "":
                    compacted.append("")
                pending_blank = False
                compacted.append(line)
                in_code_fence = not in_code_fence
                continue
            if pending_blank and compacted and compacted[-1] != "":
                compacted.append("")
            pending_blank = False

            if not in_code_fence and seen_bullets is not None and cls._is_prompt_bullet_line(line):
                # PR-03: use more aggressive normalization for bullet-dedup
                normalized_bullet = cls._bullet_dedup_key(line)
                if not normalized_bullet:
                    normalized_bullet = re.sub(r"\s+", " ", stripped).lower()
                if normalized_bullet in seen_bullets:
                    continue
                seen_bullets.add(normalized_bullet)
            compacted.append(line)

        while compacted and not compacted[0].strip():
            compacted.pop(0)
        while compacted and not compacted[-1].strip():
            compacted.pop()
        return "\n".join(compacted)

    # PR-03: threshold above which two sections are considered semantic duplicates
    # and the later occurrence will be dropped entirely.
    _SECTION_JACCARD_DEDUP_THRESHOLD = 0.85

    @classmethod
    def _section_fingerprint(cls, section: str) -> frozenset[str]:
        """Fingerprint a section as the set of normalized bullet-keys it contains."""
        keys: set[str] = set()
        for raw_line in section.splitlines():
            line = raw_line.strip()
            if not cls._is_prompt_bullet_line(line):
                continue
            key = cls._bullet_dedup_key(line)
            if key:
                keys.add(key)
        return frozenset(keys)

    @classmethod
    def _compose_prompt_sections(
        cls,
        sections: List[str],
        *,
        dedupe_bullets: bool = True,
    ) -> str:
        seen_sections: set[str] = set()
        # PR-03: track bullet-set fingerprints of already-composed sections so we
        # can drop a later section that substantially duplicates an earlier one
        # (e.g. locked_contract vs product_policy often share 80%+ of their rules).
        seen_fingerprints: List[frozenset[str]] = []
        seen_bullets: Optional[set[str]] = set() if dedupe_bullets else None
        composed: List[str] = []

        for section in sections:
            normalized = (section or "").strip()
            if not normalized:
                continue
            if normalized in seen_sections:
                continue
            seen_sections.add(normalized)

            # Semantic / bullet-set similarity dedup at section granularity.
            fingerprint = cls._section_fingerprint(normalized)
            if fingerprint:
                is_duplicate = False
                for prior in seen_fingerprints:
                    if not prior:
                        continue
                    union = prior | fingerprint
                    if not union:
                        continue
                    intersection = prior & fingerprint
                    similarity = len(intersection) / len(union)
                    if similarity >= cls._SECTION_JACCARD_DEDUP_THRESHOLD:
                        is_duplicate = True
                        break
                if is_duplicate:
                    continue
                seen_fingerprints.append(fingerprint)

            compacted = cls._compact_prompt_section(
                normalized,
                seen_bullets=seen_bullets,
            )
            if compacted:
                composed.append(compacted)
        return "\n\n".join(composed)

    async def generate(
        self,
        spec: GameSpec,
        gdd: GDD,
        description: str = "",
        runtime_contract: Optional[GameRuntimeContract] = None,
        runtime_profile: Optional[str] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        budget_override: Optional[str] = None,
        generation_guidance: Optional[str] = None,
        excluded_provider_ids: Optional[List[str]] = None,
    ) -> GenerateCodeResult:
        if self.llm_mode != "real" or not self._client.is_enabled():
            raise RuntimeError("Real LLM mode is required for game generation")

        start = time.time()
        llm_result = await self._llm_generate(
            spec,
            gdd,
            description=description,
            runtime_contract=runtime_contract,
            runtime_profile=runtime_profile,
            prompt_bundle_snapshot=prompt_bundle_snapshot,
            budget_override=budget_override,
            generation_guidance=generation_guidance,
            excluded_provider_ids=excluded_provider_ids,
        )
        if isinstance(llm_result, tuple):
            html, route_snapshot = llm_result
        else:
            html, route_snapshot = llm_result, None
        html = ensure_structured_section_markers(html)
        elapsed = int((time.time() - start) * 1000)
        return GenerateCodeResult(
            html_code=html,
            strategy="llm",
            template_id=None,
            generation_time_ms=elapsed,
            code_size_bytes=len(html.encode("utf-8")),
            route_snapshot=route_snapshot,
        )

    async def _llm_generate(
        self,
        spec: GameSpec,
        gdd: GDD,
        description: str = "",
        runtime_contract: Optional[GameRuntimeContract] = None,
        runtime_profile: Optional[str] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        budget_override: Optional[str] = None,
        generation_guidance: Optional[str] = None,
        excluded_provider_ids: Optional[List[str]] = None,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        request_text = self._resolve_request_context(spec, gdd, description)
        prompt_values = self._build_game_design_prompt_values(
            spec=spec,
            gdd=gdd,
            description=request_text,
        )
        prompt_template = require_prompt("prompt.game_design_template")
        structured_design = prompt_template.format_map(_SafePromptFormatDict(prompt_values))
        logic_generate_policy = self._resolved_bundle_prompt(prompt_bundle_snapshot, "logic_generate")
        profile_few_shot = self._resolve_profile_few_shot(
            prompt_bundle_snapshot,
            runtime_profile,
            runtime_contract=runtime_contract,
        )
        generation_tier_block = self._build_generation_tier_block(spec)
        visual_pack_block = self._build_visual_pack_block(spec)
        implementation_budget = self._build_implementation_budget_block(spec, request_text)
        design_program_block = self._build_design_program_block(spec, gdd)
        mechanic_diversity_block = self._build_mechanic_diversity_block(
            spec,
            request_text,
            runtime_profile,
        )
        critical_intent_block = self._build_critical_intent_block(
            spec,
            request_text,
            runtime_profile=runtime_profile,
            structured_design=structured_design,
        )
        full_prompt = self._compose_prompt_sections(
            [
                logic_generate_policy,
                self._build_generation_guidance_block(generation_guidance),
                self._build_preflight_safety_block(spec, runtime_contract, runtime_profile),
                generation_tier_block,
                visual_pack_block,
                profile_few_shot,
                structured_design,
                mechanic_diversity_block,
                design_program_block,
                critical_intent_block,
                "" if self._structured_design_has_ui_language(structured_design) else self._build_ui_language_block(spec.ui_language),
                self._build_runtime_contract_block(runtime_contract, runtime_profile, prompt_bundle_snapshot),
                self._build_contract_implementation_checklist(runtime_contract, runtime_profile),
                implementation_budget,
                self._build_mobile_layout_guardrails(gdd, runtime_contract),
                self._build_platform_standard_fallback(),
            ],
        )

        skeleton = self.template_cache.get_skeleton(spec, runtime_profile or "")
        if self._should_include_reference_skeleton(
            spec,
            request_text=request_text,
            skeleton=skeleton,
            design_program_block=design_program_block,
        ):
            full_prompt = self._compose_prompt_sections(
                [
                    "REFERENCE SKELETON (follow this HTML structure, replace game-specific content):\n"
                    f"```html\n{skeleton}\n```",
                    full_prompt,
                ],
                dedupe_bullets=False,
            )

        # P1.3 PR-12: optional inspiration-lane injection — route a configurable
        # fraction of requests to include top-k cached templates as
        # reference-only snippets in the system prompt. Deterministic per
        # variation_seed; no-op when flag disabled or helpers missing.
        sampling_profile: Optional[Dict[str, Any]] = None
        inspiration_block: str = ""
        try:
            if (
                getattr(settings, "P1_TEMPLATE_INSPIRATION_ENABLED", False)
                and _p1_decide_lane is not None
                and _p1_select_inspiration is not None
                and _p1_render_inspiration_block is not None
            ):
                tier_share = float(getattr(settings, "P1_TEMPLATE_LANE_SHARE", 0.2) or 0.2)
                seed_for_lane = str(getattr(spec, "variation_seed", "") or "")
                # P2.3 inspiration guard: if a regression has tripped the
                # circuit for this tier, short-circuit to the miss branch
                # regardless of what decide_lane would say. Always safe:
                # should_skip returns False on any error / when flag is off.
                _tier_for_guard = getattr(spec, "generation_tier", None)
                _tier_value = getattr(_tier_for_guard, "value", None)
                # R-3 correlation key: prefer the request-scoped task_id
                # (unique per request, stable across hedging/retry within
                # the same request context); fall back to variation_seed
                # only if context lookup fails. Import is local to keep
                # this path guarded — never break generation on telemetry.
                _corr_key = seed_for_lane
                try:
                    from ..services.llm_gateway import (
                        get_request_context as _p2_req_ctx,
                    )
                    _task_id_ctx = _p2_req_ctx().get("task_id")
                    if _task_id_ctx:
                        _corr_key = str(_task_id_ctx)
                except Exception:  # noqa: BLE001
                    _corr_key = seed_for_lane
                _guard_skip = False
                try:
                    _guard_skip = bool(_p2_inspiration_should_skip(_tier_for_guard))
                except Exception:  # noqa: BLE001 - guard must never break lane
                    _guard_skip = False
                if _guard_skip:
                    # R-1 fix: emit ONLY the guard_skip event. Do not also
                    # fall through to the inspiration_lane_miss branch —
                    # doing so double-counts the request as both a
                    # guard_skip and a natural miss, which inflates the miss
                    # curve during a trip window. The guard window is still
                    # fed via note_lane_decision(hit=False) below so
                    # recovery logic keeps working.
                    _p2_emit(
                        "inspiration_guard_skip",
                        tier=_tier_value,
                        lane_share=tier_share,
                    )
                    try:
                        _p2_inspiration_note_decision(
                            _corr_key, _tier_for_guard, hit=False,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                elif _p1_decide_lane(seed_for_lane, tier_share):
                    search_fn = getattr(self.template_cache, "search", None)
                    candidates = []
                    if callable(search_fn):
                        try:
                            candidates = list(search_fn(spec, runtime_profile or "") or [])
                        except Exception:  # noqa: BLE001 - reference-only
                            candidates = []
                    snippets = _p1_select_inspiration(
                        candidates,
                        k=int(getattr(settings, "P1_TEMPLATE_INSPIRATION_K", 2) or 2),
                    )
                    inspiration_block = _p1_render_inspiration_block(snippets)
                    # P2.1 telemetry: emit lane-hit with candidate / snippet counts.
                    _p2_emit(
                        "inspiration_lane_hit",
                        tier=_tier_value,
                        lane_share=tier_share,
                        candidate_count=len(candidates),
                        snippet_count=len(snippets) if snippets else 0,
                        rendered=bool(inspiration_block),
                    )
                    # P2.3: note the hit decision so runner can commit
                    # fun_score once available. Keyed by task_id (R-3).
                    try:
                        _p2_inspiration_note_decision(
                            _corr_key, _tier_for_guard, hit=True,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    _p2_emit(
                        "inspiration_lane_miss",
                        tier=_tier_value,
                        lane_share=tier_share,
                    )
                    # P2.3: note the miss decision symmetrically.
                    try:
                        _p2_inspiration_note_decision(
                            _corr_key, _tier_for_guard, hit=False,
                        )
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001 - never block generation on inspiration path
            logger.debug("PR-12 inspiration lane skipped due to unexpected error", exc_info=True)

        # PR-07 wire-up: optional CreativeAnchors block. When
        # P1_CREATIVE_ANCHORS_ENABLED is on, either parse a pre-supplied
        # spec.creative_anchors dict or synthesize deterministically via
        # build_anchors_fallback from source_description. The rendered block
        # is bounded (<= ~500 chars) and appended to the system prompt to
        # give the model structured pace/style/entity cues.
        anchors_block: str = ""
        try:
            if (
                getattr(settings, "P1_CREATIVE_ANCHORS_ENABLED", False)
                and _p1_CreativeAnchors is not None
            ):
                anchors_obj = None
                raw = getattr(spec, "creative_anchors", None)
                if isinstance(raw, dict) and raw:
                    try:
                        anchors_obj = _p1_CreativeAnchors(**raw)
                    except Exception:  # noqa: BLE001 - fall through to fallback
                        anchors_obj = None
                if anchors_obj is None and _p1_build_anchors_fallback is not None:
                    try:
                        anchors_obj = _p1_build_anchors_fallback(
                            getattr(spec, "source_description", "") or "",
                            expanded_prompt=getattr(spec, "intent_summary", "") or None,
                        )
                    except Exception:  # noqa: BLE001 - never block on fallback
                        anchors_obj = None
                if anchors_obj is not None:
                    mood_txt = ", ".join(anchors_obj.mood) if anchors_obj.mood else "—"
                    hints_txt = ", ".join(anchors_obj.entity_pool_hints) if anchors_obj.entity_pool_hints else "—"
                    anchors_block = (
                        "### Creative Anchors (advisory, reference only)\n"
                        f"- Genre: {anchors_obj.genre}\n"
                        f"- Pace: {anchors_obj.pace_axis}\n"
                        f"- Style: {anchors_obj.style_axis}\n"
                        f"- Mood: {mood_txt}\n"
                        f"- Entity hints: {hints_txt}\n"
                        "Use these as soft guidance; the GameSpec fields remain authoritative."
                    )
                    # P2.1 telemetry: distinguish spec-supplied anchors from
                    # fallback-synthesized so we can measure upstream integration.
                    _p2_emit(
                        "creative_anchors_applied",
                        source="spec" if (isinstance(raw, dict) and raw) else "fallback",
                        genre=anchors_obj.genre,
                        pace=anchors_obj.pace_axis,
                        style=anchors_obj.style_axis,
                        mood_n=len(anchors_obj.mood or []),
                        hints_n=len(anchors_obj.entity_pool_hints or []),
                    )
        except Exception:  # noqa: BLE001 - never block generation on anchors path
            logger.debug("PR-07 creative anchors skipped due to unexpected error", exc_info=True)
            anchors_block = ""

        # P1.1 GAP-1b: compute a DiversityPlan for this tier/seed and convert
        # into a provider sampling_profile. Feature-flagged; when disabled the
        # profile is None and providers use their built-in defaults.
        try:
            if (
                getattr(settings, "P1_DIVERSITY_PLANNER_ENABLED", False)
                and _p1_plan_for is not None
            ):
                plan = _p1_plan_for(
                    tier=getattr(spec, "generation_tier", None),
                    variation_seed=getattr(spec, "variation_seed", None),
                )
                if plan is not None:
                    to_sp = getattr(plan, "to_sampling_profile", None)
                    if callable(to_sp):
                        sampling_profile = to_sp()
                        # P2.1 telemetry: record the knobs that will actually
                        # be passed to provider so we can correlate fun_score
                        # deltas with sampling changes.
                        if sampling_profile:
                            _p2_emit(
                                "sampling_profile_applied",
                                tier=getattr(getattr(spec, "generation_tier", None), "value", None),
                                temperature=sampling_profile.get("temperature"),
                                top_p=sampling_profile.get("top_p"),
                                top_k=sampling_profile.get("top_k"),
                                seed=sampling_profile.get("seed"),
                            )
        except Exception:  # noqa: BLE001 - never block generation on planner errors
            logger.debug("GAP-1b diversity planner skipped due to unexpected error", exc_info=True)
            sampling_profile = None

        try:
            request_timeout_s = self._generation_request_timeout_budget_s(spec, budget_override)
            overall_timeout_s = self._generation_overall_timeout_budget_s(spec, budget_override)
            hedge_after_s = self._generation_provider_hedge_delay_s(spec, budget_override)
            token_budget = self._select_token_budget(spec, budget_override)
            truncation_retry_cap = self._select_truncation_retry_cap(spec, budget_override)
            # P1.3 PR-12: compose inspiration block (if any) into the system
            # prompt. Empty string is safe to append.
            # PR-07 wire-up: additionally append the CreativeAnchors block if
            # one was produced (order: base → anchors → inspiration, so
            # concrete reference snippets come last for recency bias).
            base_system = self._build_system_prompt(prompt_bundle_snapshot, spec=spec)
            _parts = [base_system]
            if anchors_block:
                _parts.append(anchors_block)
            if inspiration_block:
                _parts.append(inspiration_block)
            effective_system = "\n\n".join(_parts) if len(_parts) > 1 else base_system
            completion_result = await self._client.complete_with_truncation_retry(
                max_tokens=token_budget,
                system=effective_system,
                messages=[{"role": "user", "content": full_prompt}],
                step_key="code_generate.full",
                stage="code_generating",
                request_timeout_s=request_timeout_s,
                overall_timeout_s=overall_timeout_s,
                allow_provider_fallback=True,
                response_size_hint=self._create_response_size_hint(),
                context_scope="request",
                compression_policy="code_generation",
                truncation_retry_attempts=1,
                truncation_retry_increment=2048,
                truncation_retry_max_tokens=truncation_retry_cap,
                timeout_retry_attempts=0,
                provider_retry_attempts=1,
                provider_retry_on_timeout_errors=False,
                provider_retry_base_delay_s=1,
                provider_retry_max_delay_s=2,
                excluded_provider_ids=excluded_provider_ids,
                return_route_snapshot=True,
                hedge_provider_fallback_after_s=hedge_after_s,
                sampling_profile=sampling_profile,  # P1.1 GAP-1b
            )
            if isinstance(completion_result, tuple):
                text, route_snapshot = completion_result
            else:
                text, route_snapshot = completion_result, None
            return _extract_html(text), route_snapshot
        except Exception as exc:
            logger.error("Full LLM generation failed: %s", exc)
            wrapped = RuntimeError(f"Full LLM generation failed: {exc}")
            route_snapshot = getattr(exc, "route_snapshot", None)
            if route_snapshot is not None:
                try:
                    setattr(wrapped, "route_snapshot", route_snapshot)
                except Exception:
                    pass
            raise wrapped from exc

    @staticmethod
    def _build_generation_guidance_block(generation_guidance: Optional[str]) -> str:
        normalized = str(generation_guidance or "").strip()
        if not normalized:
            return ""
        return normalized

    @staticmethod
    def _build_preflight_safety_block(
        spec: Optional[GameSpec],
        runtime_contract: Optional[GameRuntimeContract],
        runtime_profile: Optional[str],
    ) -> str:
        lines = [
            "CODE SAFETY CHECKLIST (FIRST PRIORITY):",
            "- Declare every live helper, alias, and loop variable before use; never reference undeclared short aliases such as `line`, `cell`, `dot`, `nr`, `nc`, `viewWidth`, or `viewHeight`.",
            "- Set explicit non-zero `canvas.width` and `canvas.height` during init/resize before the first render.",
            "- Acquire `ctx = canvas.getContext('2d')` immediately after the canvas is created, and never call `ctx.setTransform(...)`, `ctx.clearRect(...)`, or similar APIs before that initialization succeeds.",
            "- Do not initialize `ctx`, `player`, `touchState`, `dragState`, `dragStart`, or other live runtime objects to `null` if the main loop can run before they are assigned; prefer safe default objects or guard every property read until initialization completes.",
            "- Keep collection item aliases scoped to the loop or callback that declares them; if code reads `star.x`, `particle.alpha`, or similar live members, declare `const star = stars[i]` / `const particle = particles[i]` in the same block before use.",
            "- For decorative loops such as `dots`, `stars`, `particles`, or `lines`, use a concrete loop shape like `for (let i = 0; i < dots.length; i += 1) { const dot = dots[i]; if (!dot) continue; ... }` and never read `dot.*` outside that declaring block.",
            "- Declare `update()`, `render()`, and any loop helper before the first direct call or `requestAnimationFrame(loop)` callback that invokes them; never rely on undeclared function expressions being available earlier in the file.",
            "- If resize or layout logic uses `scaleX`, `scaleY`, `uiScale`, `viewWidth`, or `viewHeight`, define those aliases inside the same resize/init block before the first HUD or canvas draw that reads them.",
            "- Do not begin a statement with a bare `.` or split property chains across lines; every canvas or object call must be a complete JavaScript statement on its own line.",
            "- The first canvas/document interaction must be able to start gameplay from `boot` / `ready`; never write a primary input handler that only says `if (state !== 'playing') return` unless that same handler can call `startGame()` first.",
            "- When reading input coordinates, prefer `const point = touch || e;` and only read `point.clientX` / `point.clientY` after guarding touch arrays and null cases.",
            "- Do not build translucent gradient or fill colors by concatenating alpha suffixes onto dynamic color strings such as `light.color + '80'`; use explicit `rgba(...)` / `hsla(...)` values or full `#RRGGBBAA` literals.",
        ]

        profile = str(runtime_profile or "").strip().lower()
        input_contract = getattr(runtime_contract, "input", None)
        input_modes = [
            str(mode or "").strip().lower()
            for mode in (getattr(input_contract, "required_modes", None) or [])
        ]
        gestures = [
            str(gesture or "").strip().lower()
            for gesture in (getattr(input_contract, "gestures", None) or [])
        ]
        platform_input_mode = str(getattr(getattr(spec, "platform_constraints", None), "input_mode", "") or "").strip().lower()
        if platform_input_mode:
            input_modes.append(platform_input_mode)
        input_modes = [mode for mode in dict.fromkeys(input_modes) if mode]
        gestures = [gesture for gesture in dict.fromkeys(gestures) if gesture]
        orientation = (
            runtime_contract.mobile_layout.orientation
            if runtime_contract and runtime_contract.mobile_layout
            else "portrait_first"
        )

        touch_input_present = any("touch" in mode for mode in input_modes)
        if touch_input_present or any("drag" in gesture or "tap" in gesture or "swipe" in gesture for gesture in gestures):
            lines.append(
                "- Centralize touch extraction in one guarded helper, for example "
                "`const touch = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : null)); if (!touch) return;`."
            )
            lines.append(
                "- Never read `e.touches[0]` or `e.changedTouches[0]` directly inside gameplay handlers; define one helper such as "
                "`function getInputPoint(e) { const point = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : e)); "
                "if (!point || point.clientX == null || point.clientY == null) return null; return { x: point.clientX, y: point.clientY }; }` and reuse that exact shape."
            )
            lines.append(
                "- If gameplay stores live touch or drag state, initialize it to a safe object such as "
                "`let touchState = { active: false, x: 0, y: 0, startX: 0, startY: 0 };` instead of `null`, "
                "or guard every `touchState.*` / `touch.*` read before the first animation frame."
            )
            lines.append(
                "- Prefer one shared coordinate helper such as "
                "`const point = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : e)); "
                "if (!point || point.clientX == null || point.clientY == null) return;` and reuse it in start/move/end handlers."
            )
        if orientation == "landscape_first":
            lines.append(
                "- For landscape-first layouts, declare explicit landscape reference constants such as "
                "`const REF_W = 640; const REF_H = 360; const scaleX = canvas.width / REF_W; const scaleY = canvas.height / REF_H; const uiScale = Math.min(scaleX, scaleY);` "
                "before laying out HUD or gameplay."
            )
            lines.append(
                "- Keep a resize scaffold equivalent to "
                "`function resizeCanvas() { canvas.width = Math.max(1, Math.round(window.innerWidth || REF_W)); canvas.height = Math.max(1, Math.round(window.innerHeight || REF_H)); "
                "const scaleX = canvas.width / REF_W; const scaleY = canvas.height / REF_H; const uiScale = Math.min(scaleX, scaleY); const viewWidth = canvas.width; const viewHeight = canvas.height; return { scaleX, scaleY, uiScale, viewWidth, viewHeight }; }`."
            )
        else:
            lines.append(
                "- For portrait-first layouts, declare explicit portrait reference constants such as "
                "`const REF_W = 360; const REF_H = 640; const scaleX = canvas.width / REF_W; const scaleY = canvas.height / REF_H; const uiScale = Math.min(scaleX, scaleY);` "
                "before laying out HUD or gameplay."
            )
            lines.append(
                "- Keep a resize scaffold equivalent to "
                "`function resizeCanvas() { canvas.width = Math.max(1, Math.round(window.innerWidth || REF_W)); canvas.height = Math.max(1, Math.round(window.innerHeight || REF_H)); "
                "const scaleX = canvas.width / REF_W; const scaleY = canvas.height / REF_H; const uiScale = Math.min(scaleX, scaleY); const viewWidth = canvas.width; const viewHeight = canvas.height; return { scaleX, scaleY, uiScale, viewWidth, viewHeight }; }`."
            )
        if "puzzle_grid" in profile or "grid" in profile:
            lines.append(
                "- Never read `grid[row][col].prop` directly; define `getCell(row, col)` or local guards like "
                "`const rowBucket = grid[row]; const cell = rowBucket && rowBucket[col]; if (!cell) return;` before every `cell.type`, `cell.anim`, or neighbor read."
            )
            lines.append(
                "- Start every puzzle-grid implementation with a concrete safe accessor such as "
                "`function getCell(grid, row, col) { const rowBucket = grid[row]; return rowBucket ? rowBucket[col] : null; }` "
                "and reuse it everywhere instead of ad-hoc indexing."
            )
            lines.append(
                "- In match-finding, gravity, hint, and neighbor scans, first read "
                "`const cell = getCell(grid, row, col); if (!cell) continue;` and only then access `cell.type`, "
                "`cell.fruit`, `cell.targetY`, `cell.anim`, `cell.animProgress`, or adjacent cells."
            )
            lines.append(
                "- The final HTML must contain zero raw `grid[row][col].*` reads; route every grid lookup through "
                "`getCell(grid, row, col)` or an equivalent guarded `rowBucket/cell` pattern."
            )
        if "lane" in profile:
            lines.append(
                "- If lane helpers are used, declare them explicitly, for example `function laneX(index) { ... }`, before the render or input loop references them."
            )

        lines.append("- If any draft pattern conflicts with these rules, rewrite it before finalizing the HTML output.")
        return "\n".join(lines)

    def _build_game_design_prompt_values(
        self,
        *,
        spec: GameSpec,
        gdd: GDD,
        description: str,
    ) -> Dict[str, Any]:
        palette = spec.visual_style.palette or ["#0a0a2e", "#6366f1", "#22c55e", "#f43f5e", "#ffffff"]
        visual_pack = get_visual_pack(spec.visual_style.visual_pack) or {}
        player_entity = next((entity for entity in spec.entities if entity.role == "player"), None)
        player_shape = (player_entity.shape or "circle") if player_entity else "circle"
        player_color = (
            player_entity.color
            if player_entity and player_entity.color
            else self._palette_value(palette, 1, "#6366f1")
        )
        player_w, player_h = PLAYER_SIZE_BY_GAME_TYPE.get(spec.game_type, (40, 40))
        primary_mechanic = spec.core_mechanics[0] if spec.core_mechanics else None
        core_mechanic = self._derive_core_mechanic_text(spec, description)
        input_type = primary_mechanic.input if primary_mechanic and primary_mechanic.input else spec.platform_constraints.input_mode
        spawn_interval = gdd.numerics.spawn_interval_ms
        min_spawn_interval = max(200, int(spawn_interval * 0.45)) if spawn_interval > 0 else 0
        max_speed = round(max(gdd.numerics.base_obstacle_speed * 2.5, gdd.numerics.player_speed * 1.5, 6.0), 1)
        score_layout = gdd.ui_layout.get("score", {}) if isinstance(gdd.ui_layout, dict) else {}
        score_font = str(score_layout.get("font", "bold 18px Arial"))
        font_match = re.search(r"(\d+)", score_font)
        ui_font_size = int(font_match.group(1)) if font_match else 18

        return {
            "game_type": spec.game_type,
            "core_mechanic": core_mechanic,
            "core_mechanics": core_mechanic,
            "ui_language": spec.ui_language,
            "ui_text_examples": self._build_ui_copy_examples(gdd, spec.ui_language),
            "theme": spec.visual_style.theme,
            "art_style": spec.visual_style.art_style,
            "visual_pack": spec.visual_style.visual_pack or "none",
            "render_style_intensity": spec.visual_style.render_style_intensity or "balanced",
            "font_family": visual_pack.get("fontFamily", "system-ui, sans-serif"),
            "hud_style": visual_pack.get("hudStyle", "clean_cards"),
            "button_style": visual_pack.get("buttonStyle", "rounded_button"),
            "background_style": visual_pack.get("backgroundStyle", spec.visual_style.background),
            "motion_style": visual_pack.get("motionStyle", "responsive"),
            "particle_style": visual_pack.get("particleStyle", "minimal"),
            "accent_shapes": ", ".join(visual_pack.get("accentShapes", [])) or "none",
            "reference_game": spec.reference_game or "none",
            "palette": ", ".join(palette),
            "canvas_w": gdd.canvas.width,
            "canvas_h": gdd.canvas.height,
            "player_speed": gdd.numerics.player_speed,
            "hitbox_ratio": gdd.collision.hitbox_ratio,
            "obstacle_speed": gdd.numerics.base_obstacle_speed,
            "spawn_interval": spawn_interval,
            "speed_formula": gdd.numerics.speed_formula,
            "score_per_second": gdd.numerics.score_per_second,
            "score_per_collect": gdd.numerics.score_per_collect,
            "lives": spec.rules.lives,
            "expected_s": gdd.numerics.expected_survival_s,
            "win_condition": spec.rules.win_condition,
            "lose_condition": spec.rules.lose_condition,
            "entities_desc": self._build_entities_desc(spec.entities),
            "entities_yaml": self._format_entities_yaml(
                [entity for entity in spec.entities if entity.role in ("obstacle", "enemy")],
                fallback_speed=gdd.numerics.base_obstacle_speed,
                fallback_spawn_interval=spawn_interval,
            ),
            "collectibles_yaml": self._format_entities_yaml(
                [entity for entity in spec.entities if entity.role == "collectible"],
                fallback_speed=0,
                fallback_spawn_interval=max(spawn_interval + 400, 1200) if spawn_interval > 0 else 1500,
            ),
            "input_map": self._format_input_map_lines(gdd.input_map),
            "input_map_yaml": self._format_input_map_yaml(gdd.input_map),
            "input_type": input_type,
            "color_bg": self._palette_value(palette, 0, "#0a0a2e"),
            "color_primary": self._palette_value(palette, 1, "#6366f1"),
            "color_accent": self._palette_value(palette, 2, "#22c55e"),
            "color_danger": self._palette_value(palette, 3, "#f43f5e"),
            "color_text": self._palette_value(palette, 4, "#ffffff"),
            "player_visual": f"{player_shape} avatar with {player_color} fill",
            "player_draw_method": self._derive_player_draw_method(player_shape),
            "player_w": player_w,
            "player_h": player_h,
            "player_init_pos": self._derive_player_init_pos(spec.game_type, gdd.canvas.width, gdd.canvas.height),
            "difficulty_initial": spec.difficulty_curve,
            "session_length": spec.session_length or "short_bursts",
            "progression_shape": spec.progression_shape or "score_chase",
            "reward_loop": spec.reward_loop or "none",
            "signature_moment": spec.signature_moment or "none",
            "target_audience": spec.target_audience or "general mobile players",
            "tone": spec.tone or "readable and playful",
            "reference_style": spec.reference_style or "none",
            "complexity_budget": spec.complexity_budget or self._resolve_generation_tier(spec),
            "teaching_mode": spec.teaching_mode or "none",
            "comedy_device": spec.comedy_device or "none",
            "design_goals": self._format_compact_list(spec.design_goals, max_items=4, max_chars=64),
            "level_structure_json": self._compact_json_block(gdd.level_structure),
            "phase_plan_json": self._compact_json_block(gdd.phase_plan),
            "reward_plan_json": self._compact_json_block(gdd.reward_plan),
            "tutorial_beats_json": self._compact_json_block(gdd.tutorial_beats),
            "signature_interactions_json": self._compact_json_block(gdd.signature_interactions),
            "feedback_moments_json": self._compact_json_block(gdd.feedback_moments),
            "failure_recovery_json": self._compact_json_block(gdd.failure_recovery_plan),
            "max_speed": max_speed,
            "spawn_decay_formula": (
                f"max({min_spawn_interval}, {spawn_interval} - elapsed_s * 8)"
                if spawn_interval > 0
                else "not_applicable"
            ),
            "min_spawn_interval": min_spawn_interval,
            "combo_desc": "none",
            "invincible_frames": 45 if spec.game_type not in ("puzzle", "educational") else 0,
            "ui_score_x": score_layout.get("x", 16),
            "ui_score_y": score_layout.get("y", 36),
            "ui_font_size": ui_font_size,
            "state_flow": self._format_state_flow(gdd.state_machine),
            "special_rules_list": self._format_special_rules(spec),
        }

    @staticmethod
    def _json_block(value: Any) -> str:
        if value is None or value == "":
            return "[]"
        if value == {}:
            return "{}"
        if value == []:
            return "[]"
        return json.dumps(value, ensure_ascii=False, indent=2)

    @classmethod
    def _compact_text_items(
        cls,
        items: List[str],
        *,
        max_items: int,
        max_chars: int,
    ) -> tuple[List[str], int]:
        normalized = [
            cls._truncate_prompt_snippet(str(item), max_chars=max_chars)
            for item in items
            if str(item or "").strip()
        ]
        visible = normalized[:max_items]
        overflow = max(0, len(normalized) - len(visible))
        return visible, overflow

    @classmethod
    def _format_compact_list(
        cls,
        items: List[str],
        *,
        max_items: int = 4,
        max_chars: int = 72,
    ) -> str:
        visible, overflow = cls._compact_text_items(items, max_items=max_items, max_chars=max_chars)
        if not visible:
            return "none"
        if overflow:
            visible.append(f"+{overflow} more")
        return "; ".join(visible)

    @classmethod
    def _compact_json_value(
        cls,
        value: Any,
        *,
        max_items: int = 4,
        max_chars: int = 64,
        depth: int = 0,
        max_depth: int = 2,
    ) -> Any:
        if isinstance(value, dict):
            if depth >= max_depth:
                keys = [str(key) for key in value.keys()]
                return cls._format_compact_list(keys, max_items=max_items, max_chars=max_chars)
            items = list(value.items())
            compacted = {
                str(key): cls._compact_json_value(
                    child,
                    max_items=max_items,
                    max_chars=max_chars,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
                for key, child in items[:max_items]
            }
            overflow = len(items) - min(len(items), max_items)
            if overflow:
                compacted["_omitted_keys"] = f"+{overflow} more"
            return compacted
        if isinstance(value, list):
            visible = [
                cls._compact_json_value(
                    item,
                    max_items=max_items,
                    max_chars=max_chars,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
                for item in value[:max_items]
            ]
            overflow = len(value) - len(visible)
            if overflow:
                visible.append(f"+{overflow} more")
            return visible
        if isinstance(value, str):
            return cls._truncate_prompt_snippet(value, max_chars=max_chars)
        return value

    @classmethod
    def _compact_json_block(
        cls,
        value: Any,
        *,
        max_items: int = 4,
        max_chars: int = 320,
    ) -> str:
        if value is None or value == "":
            return "[]"
        if value == {}:
            return "{}"
        if value == []:
            return "[]"
        compacted = cls._compact_json_value(value, max_items=max_items)
        compact_text = json.dumps(compacted, ensure_ascii=False, indent=2)
        if len(compact_text) <= max_chars:
            return compact_text
        return cls._truncate_prompt_snippet(
            json.dumps(compacted, ensure_ascii=False, separators=(",", ": ")),
            max_chars=max_chars,
        )

    def _build_entities_desc(
        self,
        entities: List[Any],
        *,
        max_items: int = 6,
    ) -> str:
        if not entities:
            return "  - none"
        visible: List[str] = []
        for entity in entities[:max_items]:
            extras: List[str] = [f"shape={entity.shape or 'auto'}", f"color={entity.color or 'auto'}"]
            if entity.spawn_rate:
                extras.append(f"spawn={entity.spawn_rate}ms")
            visible.append(f"  - {entity.name} ({entity.role}): {', '.join(extras)}")
        overflow = len(entities) - len(visible)
        if overflow:
            visible.append(f"  - +{overflow} more entities")
        return "\n".join(visible)

    @classmethod
    def _format_input_map_lines(
        cls,
        input_map: Dict[str, str],
        *,
        max_items: int = 4,
    ) -> str:
        if not input_map:
            return "  touchstart -> start or primary interaction"
        pairs = [f"{event} -> {action}" for event, action in input_map.items()]
        visible, overflow = cls._compact_text_items(pairs, max_items=max_items, max_chars=72)
        lines = [f"  {item}" for item in visible]
        if overflow:
            lines.append(f"  +{overflow} more inputs")
        return "\n".join(lines)

    def _derive_core_mechanic_text(self, spec: GameSpec, description: str) -> str:
        if spec.intent_summary.strip():
            return spec.intent_summary.strip()
        if description.strip():
            return re.sub(r"\s+", " ", description.strip())[:120]
        if spec.source_description.strip():
            return re.sub(r"\s+", " ", spec.source_description.strip())[:120]
        if spec.core_mechanics:
            mechanic = spec.core_mechanics[0]
            return f"{mechanic.type} gameplay using {mechanic.input} controls."
        localized_summary = LOCALIZED_CORE_MECHANIC_SUMMARY.get(spec.ui_language or "", {})
        if spec.game_type in localized_summary:
            return localized_summary[spec.game_type]
        if spec.game_type in GAME_TYPE_CORE_MECHANIC_SUMMARY:
            return GAME_TYPE_CORE_MECHANIC_SUMMARY[spec.game_type]
        if spec.ui_language == "zh-CN":
            return "适合移动端的玩法循环，目标明确，操控灵敏。"
        return "Mobile-friendly gameplay loop with clear goals and responsive controls."

    @staticmethod
    def _resolve_request_context(spec: GameSpec, gdd: GDD, description: str) -> str:
        candidates = [
            description.strip(),
            spec.source_description.strip(),
            gdd.raw_description.strip(),
        ]
        for candidate in candidates:
            if candidate:
                return re.sub(r"\s+", " ", candidate)
        return ""

    @staticmethod
    def _truncate_prompt_snippet(text: str, *, max_chars: int = 180) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 3].rstrip() + "..."

    @classmethod
    def _build_distinctive_loop_hint(
        cls,
        spec: GameSpec,
        request_text: str,
        runtime_profile: Optional[str],
    ) -> str:
        runtime_profile = normalize_runtime_profile_id(runtime_profile)
        generation_tier = cls._resolve_generation_tier(spec)
        diversity_rules = [
            rule for rule in (spec.special_rules or [])
            if "distinctive gameplay loop" in rule.lower() or "avoid the stock" in rule.lower()
        ]
        wants_distinctive_loop = generation_tier == "showcase" or bool(diversity_rules)
        if runtime_profile in {
            "casual_arcade",
            "casual_action",
            "casual_arcade_burst",
            "casual_arcade_orbit",
            "casual_arcade_rescue",
            "casual_action_arena",
            "casual_action_survival",
        }:
            wants_distinctive_loop = True

        lines: List[str] = []
        if request_text.strip():
            lines.append(f"- Original brief anchor: {cls._truncate_prompt_snippet(request_text, max_chars=160)}")
        if wants_distinctive_loop:
            lines.append("- Prefer a distinctive loop framing instead of the stock default when it still matches the brief.")
        if generation_tier == "showcase":
            lines.append("- Showcase tier: spend extra budget on one signature mechanic framing, not generic polish alone.")
        for rule in diversity_rules[:2]:
            lines.append(f"- {rule}")
        return "\n".join(lines)

    def _build_critical_intent_block(
        self,
        spec: GameSpec,
        request_text: str,
        *,
        runtime_profile: Optional[str] = None,
        structured_design: str = "",
    ) -> str:
        reference_line = (
            f"- Reference game: {spec.reference_game}"
            if spec.reference_game
            else "- Reference game: none"
        )
        special_rules_block = (
            "- Must preserve these special rules:\n"
            + "\n".join(f"  - {rule}" for rule in spec.special_rules)
            if spec.special_rules
            else "- Special rules: none"
        )
        base_block = require_prompt("prompt.intent_detail_template").format(
            core_mechanic=self._derive_core_mechanic_text(spec, request_text),
            theme=spec.visual_style.theme,
            win_condition=spec.rules.win_condition,
            reference_line=reference_line,
            special_rules_block=special_rules_block,
            ui_language=self._describe_ui_language(spec.ui_language),
        )
        base_block = self._strip_ui_language_line(base_block)
        base_block = self._strip_reference_and_special_rules(base_block, structured_design)
        distinctive_hint = self._build_distinctive_loop_hint(spec, request_text, runtime_profile)
        if distinctive_hint:
            return "\n".join([base_block, distinctive_hint])
        return base_block

    @staticmethod
    def _strip_ui_language_line(prompt: str) -> str:
        lines = [
            line for line in (prompt or "").splitlines()
            if not line.strip().lower().startswith("- ui language:")
        ]
        return "\n".join(lines).strip()

    @staticmethod
    def _structured_design_has_ui_language(prompt: str) -> bool:
        normalized = (prompt or "").lower()
        return "ui language:" in normalized or "visible ui copy examples:" in normalized

    @classmethod
    def _strip_reference_and_special_rules(cls, prompt: str, structured_design: str) -> str:
        normalized_design = (structured_design or "").lower()
        has_reference = "reference game:" in normalized_design
        has_special_rules = "special rules:" in normalized_design

        lines = (prompt or "").splitlines()
        stripped_lines: List[str] = []
        skip_special_rule_children = False
        for line in lines:
            normalized_line = line.strip().lower()
            if has_reference and normalized_line.startswith("- reference game:"):
                continue
            if has_special_rules and normalized_line.startswith("- must preserve these special rules:"):
                skip_special_rule_children = True
                continue
            if has_special_rules and normalized_line.startswith("- special rules:"):
                continue
            if skip_special_rule_children:
                if line.startswith("  - "):
                    continue
                skip_special_rule_children = False
            stripped_lines.append(line)

        return cls._strip_empty_prompt_lines("\n".join(stripped_lines))

    def _build_design_program_block(self, spec: GameSpec, gdd: GDD) -> str:
        lines: List[str] = []
        defaultish_fields = (
            ("Session length", spec.session_length, "short_bursts"),
            ("Progression shape", spec.progression_shape, "score_chase"),
            ("Reward loop", spec.reward_loop, "none"),
            ("Signature moment", spec.signature_moment, "none"),
            ("Target audience", spec.target_audience, "general mobile players"),
            ("Tone", spec.tone, "readable and playful"),
            ("Reference style", spec.reference_style, "none"),
            ("Teaching mode", spec.teaching_mode, "none"),
            ("Comedy device", spec.comedy_device, "none"),
        )
        for label, value, default_value in defaultish_fields:
            normalized = (value or "").strip()
            if not normalized or normalized == default_value:
                continue
            lines.append(f"- {label}: {normalized}")

        complexity_budget = (spec.complexity_budget or "").strip() or self._resolve_generation_tier(spec)
        if complexity_budget == "showcase" or (spec.complexity_budget or "").strip():
            lines.append(f"- Complexity budget: {complexity_budget}")

        design_goals, goal_overflow = self._compact_text_items(
            spec.design_goals or [],
            max_items=4,
            max_chars=84,
        )
        if design_goals:
            lines.extend(["- Design goals:", *(f"  - {goal}" for goal in design_goals)])
            if goal_overflow:
                lines.append(f"  - +{goal_overflow} more goals")

        structured_sections = [
            ("Level structure", gdd.level_structure),
            ("Phase plan", gdd.phase_plan),
            ("Reward plan", gdd.reward_plan),
            ("Tutorial beats", gdd.tutorial_beats),
            ("Signature interactions", gdd.signature_interactions),
            ("Feedback moments", gdd.feedback_moments),
            ("Failure recovery plan", gdd.failure_recovery_plan),
        ]
        for label, value in structured_sections:
            if not value:
                continue
            lines.extend([f"- {label}:", self._compact_json_block(value, max_chars=360)])

        if not lines:
            return ""
        return "\n".join(
            [
                "DESIGN PROGRAM (HIGH PRIORITY):",
                *lines,
                "- Preserve this design program unless a requirement directly conflicts with it.",
            ]
        )

    @staticmethod
    def _describe_ui_language(ui_language: str) -> str:
        normalized = (ui_language or "en-US").strip() or "en-US"
        return f"{normalized} ({UI_LANGUAGE_LABELS.get(normalized, normalized)})"

    def _build_ui_language_block(self, ui_language: str) -> str:
        return (
            "UI LANGUAGE (NON-NEGOTIABLE):\n"
            f"- Visible UI language: {self._describe_ui_language(ui_language)}\n"
            "- All player-visible text must use this language, including title, HUD labels, buttons, overlays, tutorials, and win/lose copy.\n"
            "- Keep code identifiers, variable names, function names, and JSON keys in English.\n"
            "- Do not silently fall back to English UI copy unless the visible UI language itself is English."
        )

    @staticmethod
    def _build_visual_pack_block(spec: Optional[GameSpec]) -> str:
        if spec is None:
            return ""
        pack = get_visual_pack(spec.visual_style.visual_pack)
        lines = visual_pack_direction_lines(pack)
        if not lines:
            return ""
        intensity = spec.visual_style.render_style_intensity or "balanced"
        pack_id = spec.visual_style.visual_pack or "none"
        return "\n".join([
            "VISUAL PACK DIRECTION:",
            f"- Pack id: {pack_id}",
            f"- Render style intensity: {intensity}",
            *lines,
            "- Follow this direction across HUD, buttons, overlays, backgrounds, and feedback animation.",
            "- Keep the playfield readable, but avoid generic default UI if the selected pack suggests a stronger style.",
        ])

    @classmethod
    def _build_generation_tier_block(cls, spec: Optional[GameSpec]) -> str:
        generation_tier = cls._resolve_generation_tier(spec)
        if generation_tier == "safe":
            return (
                "GENERATION TIER: SAFE\n"
                "- Prioritize stability, clarity, and QA-friendly structure.\n"
                "- Keep the implementation compact, but do not collapse the brief into a generic stock loop."
            )
        if generation_tier == "showcase":
            custom = get_prompt("prompt.generation_tier_showcase")
            if custom:
                return custom.strip()
            return (
                "GENERATION TIER: SHOWCASE\n"
                "- Aim for a premium-feeling result with stronger presentation, richer feedback, and a more distinctive loop.\n"
                "- It is acceptable to add 2-3 linked subsystems as long as they share one main update/render loop.\n"
                "- Prefer a memorable mechanic framing, stronger pacing, and more expressive HUD/FX instead of the smallest generic implementation."
            )
        custom = get_prompt("prompt.generation_tier_standard")
        if custom:
            return custom.strip()
        return (
            "GENERATION TIER: STANDARD\n"
            "- Balance stability with delight.\n"
            "- Build a more polished and distinctive result than the minimal safe baseline.\n"
            "- Allow one supporting subsystem, stronger presentation, and clearer progression when they fit the brief."
        )

    @staticmethod
    def _build_mechanic_diversity_block(
        spec: GameSpec,
        request_text: str,
        runtime_profile: Optional[str],
    ) -> str:
        runtime_profile = normalize_runtime_profile_id(runtime_profile)
        generation_tier = CodeGenerator._resolve_generation_tier(spec)
        diversity_rules = [
            rule for rule in (spec.special_rules or [])
            if "distinctive gameplay loop" in rule.lower() or "avoid the stock" in rule.lower()
        ]
        if generation_tier == "safe" and not diversity_rules:
            return ""
        prompt_lines = [
            "MECHANIC DIVERSITY GOAL:",
            "- Treat this brief as intentionally open-ended.",
            "- Do not fall back to the most common stock implementation for the selected genre/profile unless the request explicitly requires it.",
            "- Vary the objective loop, pacing, failure condition, and spatial structure while staying readable on mobile.",
        ]
        if runtime_profile in {"casual_arcade", "casual_action", "casual_arcade_burst", "casual_arcade_orbit", "casual_arcade_rescue", "casual_action_arena", "casual_action_survival"}:
            prompt_lines.append(
                "- Prefer a more distinctive loop such as rescue, delivery, orbit control, area capture, chase, escort, or combo routing if it still fits the brief."
            )
        if generation_tier == "showcase":
            prompt_lines.append(
                "- Because this is a showcase-tier generation, prefer a signature mechanic framing and stronger pacing instead of the safest default structure."
            )
        if request_text.strip():
            prompt_lines.append(f"- Keep alignment with the user brief: {request_text.strip()[:160]}")
        return "\n".join(prompt_lines + [f"- {rule}" for rule in diversity_rules])

    @classmethod
    def _looks_like_educational_request(cls, *texts: str) -> bool:
        combined = " ".join((text or "").strip().lower() for text in texts if (text or "").strip())
        if not combined:
            return False
        return any(marker in combined for marker in EDUCATIONAL_REQUEST_MARKERS)

    @classmethod
    def _build_implementation_budget_block(
        cls,
        spec: Optional[GameSpec],
        request_text: str,
        *,
        fallback_game_type: str = "casual",
    ) -> str:
        game_type = spec.game_type if spec else fallback_game_type
        source_description = spec.source_description if spec else ""
        intent_summary = spec.intent_summary if spec else ""
        generation_tier = cls._resolve_generation_tier(spec)
        lines = [
            "IMPLEMENTATION SHAPE:",
            "- Use one canvas and one primary requestAnimationFrame loop.",
            "- Keep the whole experience coherent inside one HTML file and one shared state model.",
            "- Reuse the same controls and state machine across the whole experience instead of creating disconnected subsystems.",
        ]
        if generation_tier == "safe":
            lines.extend([
                "- Keep one main HUD and at most one overlay screen for ready/game-over or level-complete states.",
                "- Avoid scene managers, dialogue trees, worksheet generators, inventories, or parallel mini-games unless absolutely required for the core mechanic.",
                "- Choose the most engaging compact mechanic that satisfies the request and runtime contract before layering extra polish.",
            ])
        elif generation_tier == "showcase":
            lines.extend([
                "- Allow a richer presentation layer, a stronger HUD, and 2-3 linked subsystems as long as they all plug into the same loop.",
                "- Favor one signature mechanic plus one support system such as combos, waves, rescue targets, route goals, risk-reward pickups, or finale beats.",
                "- Showcase briefs may use 5-8 active entities or families when they stay legible and share the same core loop.",
                "- Spend budget on clarity, juice, pacing, progression, and memorable payoff once boot, input, restart, and visible feedback are secure.",
            ])
        else:
            lines.extend([
                "- Allow 2-4 supporting subsystems and a more expressive HUD when they improve the brief.",
                "- Standard briefs may use roughly 4-6 active entities or families when they reinforce the same mechanic.",
                "- Build beyond the minimal safe demo when the brief supports it, while keeping the loop readable and QA-friendly.",
            ])

        if game_type == "casual":
            lines.extend([
                "- Keep the round structure readable and avoid spawning multiple unrelated subsystems.",
                "- Use one main action loop, but supporting pickups, rescue targets, combo chains, chase goals, or escort targets are allowed when they share the same controls.",
            ])
        if game_type in {"puzzle", "educational"}:
            lines.extend([
                "- Keep one board or playfield, but it may support layered goals such as route building, merge progression, or timed challenge beats.",
                "- Prefer concise tap/drag interactions over modal UI sprawl.",
            ])
        if game_type == "funny":
            lines.extend([
                "- Build one memorable comic payoff or absurd loop, and allow one supporting gag system if it improves the pacing.",
                "- Keep the humor readable through gameplay, feedback, and staging rather than long text setup.",
            ])
        if cls._looks_like_educational_request(request_text, source_description, intent_summary):
            lines.extend([
                "- For classroom or knowledge-check requests, build one touch-friendly challenge flow instead of a lesson plan or long teaching document.",
                "- Keep the challenge set compact, such as 3-5 levels or prompts on one shared board/layout.",
                "- Use short player-visible prompts and immediate feedback instead of long explanatory text blocks.",
            ])
        return "\n".join(lines)

    @classmethod
    def _should_include_reference_skeleton(
        cls,
        spec: GameSpec,
        *,
        request_text: str,
        skeleton: Optional[str],
        design_program_block: str,
    ) -> bool:
        normalized_skeleton = (skeleton or "").strip()
        if not normalized_skeleton:
            return False
        generation_tier = cls._resolve_generation_tier(spec)
        skeleton_char_budget = 2400 if generation_tier == "safe" else 1400
        if len(normalized_skeleton) > skeleton_char_budget:
            return False
        if design_program_block.strip() and generation_tier != "safe":
            return False
        return len((request_text or "").strip()) <= 80 or generation_tier == "safe"

    @staticmethod
    def _build_ui_copy_examples(gdd: GDD, ui_language: str) -> str:
        labels = gdd.ui_layout.get("labels", {}) if isinstance(gdd.ui_layout, dict) else {}
        if not isinstance(labels, dict) or not labels:
            defaults = {
                "en-US": {
                    "score": "Score",
                    "lives": "Lives",
                    "ready": "Tap to Start",
                    "game_over": "Game Over",
                    "restart": "Restart",
                },
                "zh-CN": {
                    "score": "得分",
                    "lives": "生命",
                    "ready": "点击开始",
                    "game_over": "游戏结束",
                    "restart": "重新开始",
                },
            }
            labels = defaults.get(ui_language, defaults["en-US"])
        return ", ".join(f"{key}={value}" for key, value in labels.items())

    def _build_mobile_layout_guardrails(
        self,
        gdd: GDD,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> str:
        score_layout = gdd.ui_layout.get("score", {}) if isinstance(gdd.ui_layout, dict) else {}
        score_font = str(score_layout.get("font", "bold 16px Arial"))
        match = re.search(r"(\d+)", score_font)
        hud_font = int(match.group(1)) if match else 16
        hud_font = max(14, min(hud_font, 18))
        orientation = self._resolve_layout_orientation(runtime_contract)
        reference_label = self._layout_reference_label(orientation)
        prompt = require_prompt("prompt.mobile_layout_guardrails").format(
            canvas_w=gdd.canvas.width,
            canvas_h=gdd.canvas.height,
            hud_font=hud_font,
            reference_orientation=reference_label,
            orientation_label=reference_label,
        )
        prompt = self._rewrite_layout_prompt_for_orientation(prompt, orientation)
        return self._compact_mobile_layout_guardrails(
            prompt,
            reference_label=reference_label,
            canvas_w=gdd.canvas.width,
            canvas_h=gdd.canvas.height,
        )

    @staticmethod
    def _resolve_layout_orientation(runtime_contract: Optional[GameRuntimeContract]) -> str:
        orientation = (
            runtime_contract.mobile_layout.orientation
            if runtime_contract and runtime_contract.mobile_layout
            else "portrait_first"
        )
        return "landscape_first" if orientation == "landscape_first" else "portrait_first"

    @staticmethod
    def _layout_reference_label(orientation: str) -> str:
        return "landscape-first" if orientation == "landscape_first" else "portrait-first"

    @staticmethod
    def _rewrite_layout_prompt_for_orientation(prompt: str, orientation: str) -> str:
        if orientation != "landscape_first":
            return prompt
        replacements = (
            ("portrait-first", "landscape-first"),
            ("portrait first", "landscape first"),
            ("portrait reference playfield", "landscape reference playfield"),
            ("portrait reference size", "landscape reference size"),
            ("portrait reference", "landscape reference"),
            ("portrait layout", "landscape layout"),
            ("portrait sizing", "landscape sizing"),
        )
        updated = prompt
        for source, target in replacements:
            updated = updated.replace(source, target)
        return updated

    @classmethod
    def _compact_mobile_layout_guardrails(
        cls,
        prompt: str,
        *,
        reference_label: str,
        canvas_w: int,
        canvas_h: int,
    ) -> str:
        keep_markers = (
            "side padding",
            "hud text",
            "overlay title",
            "restart/help text",
        )
        kept_lines: List[str] = []
        for raw_line in (prompt or "").splitlines():
            stripped = raw_line.strip()
            if not stripped.startswith("- "):
                continue
            lowered = stripped.lower()
            if any(marker in lowered for marker in keep_markers):
                kept_lines.append(stripped)

        recipe_lines = [
            "MOBILE LAYOUT IMPLEMENTATION RECIPE:",
            f"- Use a {reference_label} reference size of {canvas_w}x{canvas_h} during resize calculations.",
            "- Read both viewport width and height before deriving scale.",
            "- Compute scaleX/scaleY once, derive uiScale from Math.min(scaleX, scaleY) or an equivalent short-edge fit, then center the canvas and HUD.",
            *kept_lines,
        ]
        return cls._compose_prompt_sections(["\n".join(recipe_lines)])

    @staticmethod
    def _format_state_flow(state_machine: Dict[str, Any]) -> str:
        states = state_machine.get("states") if isinstance(state_machine, dict) else None
        if isinstance(states, list) and states:
            return " -> ".join(str(state) for state in states)
        return "boot -> ready -> playing -> game_over -> ready"

    def _build_runtime_contract_block(
        self,
        runtime_contract: Optional[GameRuntimeContract],
        runtime_profile: Optional[str],
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        profile_value = runtime_profile or (
            runtime_contract.runtime_profile
            if runtime_contract
            else "standard_mobile_canvas"
        )
        contract_version = runtime_contract.version if runtime_contract else "1.0"
        required_states = (
            runtime_contract.state.required_states
            if runtime_contract and runtime_contract.state and runtime_contract.state.required_states
            else ["boot", "ready", "playing", "game_over"]
        )
        input_modes = (
            runtime_contract.input.required_modes
            if runtime_contract and runtime_contract.input and runtime_contract.input.required_modes
            else ["pointer", "touch"]
        )
        gestures = (
            runtime_contract.input.gestures
            if runtime_contract and runtime_contract.input and runtime_contract.input.gestures
            else ["tap"]
        )
        forbidden_apis = (
            runtime_contract.safety.forbidden_apis
            if runtime_contract and runtime_contract.safety and runtime_contract.safety.forbidden_apis
            else ["eval", "Function", "import", "require"]
        )
        terminal_state_aliases = (
            runtime_contract.gameplay.terminal_state_aliases
            if runtime_contract and runtime_contract.gameplay and runtime_contract.gameplay.terminal_state_aliases
            else ["game_over"]
        )
        orientation = (
            runtime_contract.mobile_layout.orientation
            if runtime_contract and runtime_contract.mobile_layout
            else "portrait_first"
        )
        ui_scale_mode = (
            runtime_contract.mobile_layout.ui_scale_mode
            if runtime_contract and runtime_contract.mobile_layout
            else "short_edge"
        )
        hud_min = (
            runtime_contract.mobile_layout.font_clamp.hud_min
            if runtime_contract and runtime_contract.mobile_layout and runtime_contract.mobile_layout.font_clamp
            else 14
        )
        hud_max = (
            runtime_contract.mobile_layout.font_clamp.hud_max
            if runtime_contract and runtime_contract.mobile_layout and runtime_contract.mobile_layout.font_clamp
            else 20
        )
        title_min = (
            runtime_contract.mobile_layout.font_clamp.title_min
            if runtime_contract and runtime_contract.mobile_layout and runtime_contract.mobile_layout.font_clamp
            else 28
        )
        title_max = (
            runtime_contract.mobile_layout.font_clamp.title_max
            if runtime_contract and runtime_contract.mobile_layout and runtime_contract.mobile_layout.font_clamp
            else 36
        )
        normalized_aliases = [alias for alias in terminal_state_aliases if (alias or "").strip()]
        bundle_id = str((prompt_bundle_snapshot or {}).get("bundle_id") or "").strip()
        template = get_prompt(
            "prompt.runtime_contract_summary",
            (
                "RUNTIME CONTRACT (MUST STAY FUNCTIONAL):\n"
                "- Runtime profile: {runtime_profile} (contract v{contract_version})\n"
                "- Core state flow must support {required_states} with a restart path back into active play.\n"
                "- Input must work through {input_modes}; expected gestures: {gestures}.\n"
                "- Forbidden APIs: {forbidden_apis}.\n"
                "- Mobile layout: {orientation}, {ui_scale_mode} scaling, HUD {hud_min}-{hud_max}px, title {title_min}-{title_max}px.\n"
                "- Platform target: mobile H5 browser / WebView with a single main canvas.\n"
                "- Prevent accidental page scrolling during play and keep gameplay local with no external network or asset requests.\n"
                "- Accepted terminal/completion state aliases: {terminal_state_aliases}\n"
                "- Prompt bundle: {bundle_id}\n"
                "- Prompt layers: {layer_keys}\n"
                "- The final code must respect every contract rule explicitly, not implicitly."
            ),
        )
        rendered = template.format_map(_SafePromptFormatDict({
            "runtime_profile": profile_value,
            "contract_version": contract_version,
            "bundle_id": bundle_id,
            "layer_keys": self._resolve_bundle_layer_keys(prompt_bundle_snapshot),
            "required_states": self._format_compact_contract_items(required_states, max_items=5),
            "input_modes": self._format_compact_contract_items(input_modes, max_items=4),
            "gestures": self._format_compact_contract_items(gestures, max_items=4),
            "forbidden_apis": self._format_compact_contract_items(forbidden_apis, max_items=6),
            "orientation": orientation,
            "ui_scale_mode": ui_scale_mode,
            "hud_min": hud_min,
            "hud_max": hud_max,
            "title_min": title_min,
            "title_max": title_max,
            "terminal_state_aliases": (
                self._format_compact_contract_items(normalized_aliases, max_items=8)
                if normalized_aliases and normalized_aliases != ["game_over"]
                else ""
            ),
        }))
        return self._strip_empty_prompt_lines(rendered)

    def _build_contract_implementation_checklist(
        self,
        runtime_contract: Optional[GameRuntimeContract],
        runtime_profile: Optional[str],
    ) -> str:
        profile_value = normalize_runtime_profile_id(
            runtime_profile
            or (
                runtime_contract.runtime_profile
                if runtime_contract
                else ""
            )
        )
        input_modes = [
            str(mode).strip().lower()
            for mode in (
                runtime_contract.input.required_modes
                if runtime_contract and runtime_contract.input and runtime_contract.input.required_modes
                else ["pointer", "touch"]
            )
            if str(mode).strip()
        ]
        orientation = self._resolve_layout_orientation(runtime_contract)
        restart_required = (
            runtime_contract.gameplay.requires_restart_entry
            if runtime_contract and runtime_contract.gameplay
            else True
        )
        orientation_label = "portrait-first" if orientation == "portrait_first" else "landscape-first"
        lines: List[str] = [
            "CONTRACT IMPLEMENTATION CHECKLIST (CODE SHAPE, NOT JUST INTENT):",
            (
                "- Declare named scaleX and scaleY variables from viewport-to-reference dimensions, "
                "then compute uiScale = Math.min(scaleX, scaleY) before laying out gameplay or HUD."
            ),
            (
                f"- Keep the canvas and HUD {orientation_label}; do not rely on one unnamed `scale` value "
                "without separate width and height factors."
            ),
            "- Obtain a 2D context from the main canvas up front and drive visible gameplay through that single canvas.",
            "- During init and resize, set canvas.width and canvas.height to explicit non-zero values before the first render frame.",
            (
                "- When drawing rounded UI cards or buttons, do not chain `ctx.roundRect(...).fill()` or "
                "`ctx.roundRect(...).stroke()`; call roundRect first, then fill/stroke as separate statements."
            ),
            (
                "- If gameplay, camera, or HUD code reads viewWidth/viewHeight-style aliases, declare them from the live "
                "canvas dimensions in the same init/resize path before render, update, or spawn code uses them."
            ),
            (
                "- Every helper or property referenced from input, update, render, spawn, or scoring code must be "
                "declared before use; never invent missing methods or state accessors."
            ),
            (
                "- Do not reference bare placeholder locals such as type, line, touch, pointer, cell, or anim "
                "unless they are explicitly declared in the same scope before use."
            ),
            (
                "- If you use helper functions such as generateBackgroundLayers(), declare them before the first call, "
                "or inline the layer construction during top-level initialization."
            ),
            (
                "- When animation or effect progress belongs to an entity or cell, keep it on a declared object field "
                "such as cell.anim or particle.anim; never read a bare anim identifier unless it is explicitly declared in scope."
            ),
            "- Do not use shorthand aliases like w, h, sx, or sy unless they are declared in the same scope that reads them.",
            (
                "- The first primary interaction must immediately leave boot/ready, start play, or visibly mutate "
                "the canvas or HUD within the same frame or the next animation frame."
            ),
            (
                "- Primary canvas/document pointer or touch handlers must handle ready/boot input too; do not early-return "
                "before `playing` unless that same handler can call startGame() or switch state into `playing`."
            ),
            (
                "- Overlay buttons may complement the UX, but a first tap or pointerdown on the play surface must also "
                "dismiss the intro state and produce an immediate visible state change for runtime QA."
            ),
            (
            "- Maintain a declared gameplay state variable that can reach `playing`; transition into `playing` "
            "during auto-start or the first valid gameplay interaction instead of staying in boot/ready forever."
            ),
            "- Auto-advance boot/loading into ready without requiring a tap, and do not require two separate taps before gameplay starts.",
            "- Call resize/setup, render at least one visible first frame, and start the main requestAnimationFrame loop from top-level initialization.",
        ]
        if orientation == "portrait_first":
            lines.append(
                "- For portrait-first code shape, use `const REF_W = 360; const REF_H = 640;`, then derive `scaleX`, `scaleY`, `uiScale`, `viewWidth`, and `viewHeight` from live canvas dimensions inside resize/init."
            )
        else:
            lines.append(
                "- For landscape-first code shape, use `const REF_W = 640; const REF_H = 360;`, then derive `scaleX`, `scaleY`, `uiScale`, `viewWidth`, and `viewHeight` from live canvas dimensions inside resize/init."
            )
        if "touch" in input_modes:
            lines.append(
                "- Register gameplay touchstart, touchmove, and touchend handlers on the canvas or primary input target; "
                "prevent accidental page scrolling during active play."
            )
            lines.append(
                "- When reading touch coordinates, use touches[0] for active touches and changedTouches[0] for touchend/touchcancel; "
                "never assume touches[0] exists on release events."
            )
            lines.append(
                "- Guard every touch read with a length check, for example `const touch = (e.touches && e.touches.length ? e.touches[0] "
                ": (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : null)); if (!touch) return;`."
            )
            lines.append(
                "- Prefer a shared helper such as `function getInputPoint(e) { const point = (e.touches && e.touches.length ? e.touches[0] : (e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : e)); if (!point || point.clientX == null || point.clientY == null) return null; return { x: point.clientX, y: point.clientY }; }` and call it from every touchstart/touchmove/touchend handler."
            )
        if "pointer" in input_modes:
            lines.append(
                "- Register gameplay pointerdown, pointermove, and pointerup handlers on the canvas or primary input target."
            )
        if restart_required:
            lines.append(
                "- Provide an explicit restart entry such as restartGame(), resetGame(), or restart() that returns "
                "terminal states back into ready or playing."
            )
        if profile_value.startswith("casual_lane"):
            lines.append(
                "- For lane games, keep lane positions in declared data and compute x from lane index with a declared helper "
                "or array lookup; never call an undefined method like player.laneX()."
            )
        if profile_value.startswith("puzzle_grid"):
            lines.append(
                "- Grid puzzle interaction must support direct touch on the board; pointer support may complement touch, "
                "but touch handlers are mandatory."
            )
            lines.append(
                "- Drag or selection state such as dragStartCell, dragTarget, selectedCell, or hoveredCell must either be "
                "initialized to a safe object shape like `{ active: false, row: -1, col: -1 }` before the loop starts "
                "or be guarded before every property access."
            )
            lines.append(
                "- Initialize a full rectangular grid before scanning for matches or neighbors, then read cells through "
                "named locals such as `const rowBucket = grid[row]; const cell = rowBucket && rowBucket[col];`; never "
                "access `grid[row][col].prop` directly without first proving both the row bucket and cell exist."
            )
            lines.append(
                "- Define a safe accessor like `function getCell(grid, row, col) { const rowBucket = grid[row]; return rowBucket ? rowBucket[col] : null; }` before any match-finding, merge, gravity, or hint "
                "logic, and route every read of `cell.type`, `cell.fruit`, `cell.anim`, or neighbor cells through that "
                "helper instead of raw `grid[row][col]` indexing."
            )
            lines.append(
                "- The first valid tap on a puzzle cell must cause an immediate visible board-state change such as a selection highlight, "
                "focus ring, hint pulse, or committed swap; never use no-op selection toggles."
            )
        return "\n".join(lines)

    @staticmethod
    def _format_compact_contract_items(items: List[str], *, max_items: int) -> str:
        normalized = [str(item).strip() for item in (items or []) if str(item).strip()]
        if not normalized:
            return "none"
        if len(normalized) <= max_items:
            return ", ".join(normalized)
        remaining = len(normalized) - max_items
        return ", ".join(normalized[:max_items]) + f", +{remaining} more"

    @staticmethod
    def _resolve_bundle_layer_keys(prompt_bundle_snapshot: Optional[Dict[str, Any]]) -> str:
        resolved_prompts = ((prompt_bundle_snapshot or {}).get("layers") or {}).get("resolved_prompts")
        if not isinstance(resolved_prompts, dict):
            return ""
        keys = [str(key).strip() for key in resolved_prompts.keys() if str(key).strip()]
        return ", ".join(keys[:8])

    @staticmethod
    def _strip_empty_prompt_lines(prompt: str) -> str:
        cleaned: List[str] = []
        for raw_line in (prompt or "").splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if stripped.startswith("- ") and stripped.endswith(":"):
                continue
            cleaned.append(line)

        compacted: List[str] = []
        previous_blank = False
        for line in cleaned:
            is_blank = not line.strip()
            if is_blank and previous_blank:
                continue
            compacted.append(line)
            previous_blank = is_blank
        return "\n".join(compacted).strip()

    def _build_platform_standard_fallback(self) -> str:
        runtime_contract_template = get_prompt("prompt.runtime_contract_summary", "").lower()
        if "mobile h5 browser" in runtime_contract_template or "platform target" in runtime_contract_template:
            return ""
        return get_prompt("prompt.platform_standard", "")

    @staticmethod
    def _resolved_bundle_prompt(
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
        slot: str,
    ) -> str:
        resolved_prompts = ((prompt_bundle_snapshot or {}).get("layers") or {}).get("resolved_prompts")
        if not isinstance(resolved_prompts, dict):
            return ""

        entry = resolved_prompts.get(slot)
        if isinstance(entry, dict):
            return str(entry.get("content") or "").strip()
        if isinstance(entry, str):
            return entry.strip()
        return ""

    @classmethod
    def _resolve_profile_few_shot(
        cls,
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
        runtime_profile: Optional[str],
        *,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> str:
        prompt = ""
        profile_id = normalize_runtime_profile_id((runtime_profile or "").strip())
        if profile_id:
            profile = get_runtime_profile(profile_id)
            if isinstance(profile, dict):
                prompt = str(profile.get("few_shot_prompt") or "").strip()
        if not prompt:
            prompt = cls._resolved_bundle_prompt(prompt_bundle_snapshot, "profile_few_shot")
        orientation = cls._resolve_layout_orientation(runtime_contract)
        rewritten = cls._rewrite_profile_few_shot_for_orientation(prompt, orientation)
        return cls._compact_profile_few_shot(rewritten)

    @classmethod
    def _rewrite_profile_few_shot_for_orientation(cls, prompt: str, orientation: str) -> str:
        normalized = (prompt or "").strip()
        if not normalized:
            return ""
        if orientation != "landscape_first":
            return normalized

        replacements = (
            ("centered portrait canvas", "centered landscape canvas"),
            ("portrait canvas", "landscape canvas"),
            ("portrait playfield", "landscape playfield"),
            ("portrait-first", "landscape-first"),
            ("portrait first", "landscape first"),
            ("portrait", "landscape"),
        )
        updated = normalized
        for source, target in replacements:
            updated = updated.replace(source, target)

        if "landscape" not in updated.lower():
            updated += (
                " Adapt the same interaction model to a landscape-first playfield, "
                "wider camera framing, and side-friendly HUD placement."
            )
        return updated

    @classmethod
    def _compact_profile_few_shot(cls, prompt: str) -> str:
        normalized = (prompt or "").strip()
        if not normalized:
            return ""

        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if len(lines) <= 4 and len(normalized) <= 280:
            return normalized

        if len(lines) >= 3:
            kept_lines = lines[:3]
            if lines[-1] not in kept_lines:
                kept_lines.append(lines[-1])
            compact = "\n".join(kept_lines)
        else:
            compact = cls._truncate_prompt_snippet(normalized, max_chars=260)

        if len(compact) > 300:
            compact = cls._truncate_prompt_snippet(compact, max_chars=300)
        return compact

    @classmethod
    def _rewrite_system_prompt_for_generation_tier(
        cls,
        system_prompt: str,
        spec: Optional[GameSpec] = None,
    ) -> str:
        generation_tier = cls._resolve_generation_tier(spec)
        rewritten = system_prompt or ""
        if generation_tier == "safe":
            return rewritten

        replacements: List[Tuple[str, str]] = [
            (
                r"(?im)^.*smallest implementation.*$",
                (
                    "Choose the smallest implementation that still feels polished, intentional, "
                    "and distinct from stock examples."
                    if generation_tier == "standard"
                    else "Choose the most distinctive implementation that still stays stable, readable, and mobile-friendly."
                ),
            ),
            (
                r"(?im)^.*one clear gameplay loop.*$",
                (
                    "Prefer one clear primary loop, but a supporting subsystem is allowed when it improves pacing or delight."
                    if generation_tier == "standard"
                    else "Prefer one signature primary loop with up to two linked support systems when they enhance pacing, progression, or spectacle."
                ),
            ),
            (
                r"(?im)^.*avoid optional polish before core loop.*$",
                (
                    "Secure the core loop first, then spend remaining budget on stronger feedback, pacing, and presentation."
                    if generation_tier == "standard"
                    else "Secure boot, input, restart, and visible feedback first, then actively spend budget on presentation, juice, and memorable payoff."
                ),
            ),
        ]
        for pattern, replacement in replacements:
            rewritten = re.sub(pattern, replacement, rewritten)

        extra_block = get_prompt(f"prompt.code_gen_system_{generation_tier}")
        if extra_block:
            extra = extra_block.strip()
        elif generation_tier == "showcase":
            extra = (
                "SHOWCASE OVERRIDE:\n"
                "- A premium-feeling result is preferred over the smallest generic implementation.\n"
                "- Strong visual hierarchy, richer feedback, and clearer progression are encouraged.\n"
                "- Preserve mobile readability, restartability, and performance while aiming for a more memorable result."
            )
        else:
            extra = (
                "STANDARD OVERRIDE:\n"
                "- Do not collapse the brief into the safest stock demo.\n"
                "- Favor clearer progression, stronger feedback, and a more intentional presentation when they fit the request."
            )

        if extra:
            rewritten = "\n\n".join(part for part in [rewritten.strip(), extra] if part)
        return rewritten

    def _build_system_prompt(
        self,
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
        spec: Optional[GameSpec] = None,
    ) -> str:
        return self._compose_prompt_sections(
            [
                self._resolved_bundle_prompt(prompt_bundle_snapshot, "locked_contract"),
                self._resolved_bundle_prompt(prompt_bundle_snapshot, "product_policy"),
                self._rewrite_system_prompt_for_generation_tier(
                    require_prompt("prompt.code_gen_system"),
                    spec=spec,
                ),
            ],
        )

    @staticmethod
    def _palette_value(palette: List[str], index: int, fallback: str) -> str:
        if 0 <= index < len(palette) and palette[index]:
            return palette[index]
        return fallback

    @staticmethod
    def _derive_player_draw_method(shape: str) -> str:
        shape_key = (shape or "").lower()
        if shape_key == "triangle":
            return "Canvas path triangle with filled color and subtle outline"
        if shape_key in {"square", "rectangle"}:
            return "Filled rounded rectangle drawn with inline canvas primitives"
        if shape_key == "diamond":
            return "Rotated square diamond drawn with Canvas path commands"
        return "Filled circle or simple geometric sprite drawn with inline canvas primitives"

    @staticmethod
    def _derive_player_init_pos(game_type: str, canvas_w: int, canvas_h: int) -> str:
        if game_type in {"casual", "funny"}:
            return f"bottom-center ({canvas_w // 2}, {canvas_h - 72})"
        return f"center ({canvas_w // 2}, {canvas_h // 2})"

    def _format_entities_yaml(
        self,
        entities: List[Any],
        *,
        fallback_speed: float,
        fallback_spawn_interval: int,
        max_entities: int = 3,
    ) -> str:
        if not entities:
            return "  - none"

        lines: List[str] = []
        for entity in entities[:max_entities]:
            shape = entity.shape or "auto"
            color = entity.color or "#ffffff"
            spawn_interval = entity.spawn_rate or fallback_spawn_interval
            lines.extend([
                f"  - name: {entity.name}",
                f"    visual: {shape} shape, color {color}",
                "    size: 32-56px",
                f"    behavior: {fallback_speed} px/s, every {spawn_interval} ms",
                f"    effect: {'damage player' if entity.role in ('obstacle', 'enemy') else 'award score'}",
            ])
        overflow = len(entities) - min(len(entities), max_entities)
        if overflow:
            lines.append(f"  - note: +{overflow} more {entities[0].role} entities")
        return "\n".join(lines)

    @classmethod
    def _format_input_map_yaml(cls, input_map: Dict[str, str], *, max_items: int = 4) -> str:
        if not input_map:
            return "  touchstart: start or primary interaction"
        pairs = [f"{event}: {action}" for event, action in input_map.items()]
        visible, overflow = cls._compact_text_items(pairs, max_items=max_items, max_chars=72)
        lines = [f"  {item}" for item in visible]
        if overflow:
            lines.append(f"  note: +{overflow} more inputs")
        return "\n".join(lines)

    @classmethod
    def _format_special_rules(cls, spec: GameSpec, *, max_items: int = 5) -> str:
        rules: List[str] = []
        if spec.special_rules:
            rules.extend(f"- {rule}" for rule in spec.special_rules)
        if spec.reference_game:
            rules.append(f"- Reference game inspiration: {spec.reference_game}")
        if spec.visual_style.effects:
            rules.extend(f"- Visual effect: {effect}" for effect in spec.visual_style.effects)
        if spec.platform_constraints.platform:
            rules.append(f"- Target platform: {spec.platform_constraints.platform}")
        visible, overflow = cls._compact_text_items(rules, max_items=max_items, max_chars=84)
        if overflow:
            visible.append(f"- +{overflow} more rules")
        return "\n".join(visible) if visible else "- none"

    async def iterate(
        self,
        current_code: str,
        feedback: str,
        conversation: List[dict],
        runtime_contract: Optional[GameRuntimeContract] = None,
        runtime_profile: Optional[str] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        game_spec: Optional[GameSpec] = None,
    ) -> Tuple[str, IterationType]:
        if self.llm_mode != "real" or not self._client.is_enabled():
            raise RuntimeError("Real LLM mode is required for game iteration")

        normalized_code = ensure_structured_section_markers(current_code)
        iter_type = await self._classify_iteration(feedback)
        if iter_type == IterationType.param_adjust:
            updated = self._param_adjust(normalized_code, feedback)
            if updated != normalized_code:
                return ensure_structured_section_markers(updated), iter_type

        updated = await self._llm_iterate(
            code=normalized_code,
            feedback=feedback,
            conversation=conversation,
            iter_type=iter_type,
            runtime_contract=runtime_contract,
            runtime_profile=runtime_profile,
            prompt_bundle_snapshot=prompt_bundle_snapshot,
            game_spec=game_spec,
        )
        return ensure_structured_section_markers(updated), iter_type

    async def _classify_iteration(self, feedback: str) -> IterationType:
        try:
            request_timeout_s = self._resolve_step_request_timeout_s(
                "iterate.classify",
                default_timeout_s=30,
            )
            overall_timeout_s = self._resolve_step_overall_timeout_s(
                "iterate.classify",
                request_timeout_s=request_timeout_s,
                default_timeout_s=60,
            )
            text = await self._client.complete_with_truncation_retry(
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": require_prompt("prompt.iterate_classify").format(feedback=feedback),
                }],
                step_key="iterate.classify",
                stage="code_generating",
                prefer_fast=True,
                response_size_hint="small",
                context_scope="task",
                compression_policy="iteration_classify",
                request_timeout_s=request_timeout_s,
                overall_timeout_s=overall_timeout_s,
                truncation_retry_attempts=1,
                truncation_retry_increment=256,
                truncation_retry_max_tokens=1024,
                timeout_retry_attempts=1,
                timeout_retry_increment_s=30,
                timeout_retry_max_s=120,
            )
            label = text.strip().lower()
            for iter_type in IterationType:
                if iter_type.value in label:
                    return iter_type
        except Exception:
            pass
        return IterationType.element_change

    def _param_adjust(self, code: str, feedback: str) -> str:
        fb = feedback.lower()
        speed_pattern = r"(player[._]?speed\s*[:=]\s*|const\s+SPEED\s*=\s*)(\d+\.?\d*)"

        if "faster" in fb or "加速" in fb or "更快" in fb:
            code = re.sub(
                speed_pattern,
                lambda match: match.group(1) + str(round(float(match.group(2)) * 1.5, 1)),
                code,
                flags=re.IGNORECASE,
            )
        if "slower" in fb or "减速" in fb or "更慢" in fb:
            code = re.sub(
                speed_pattern,
                lambda match: match.group(1) + str(round(float(match.group(2)) * 0.7, 1)),
                code,
                flags=re.IGNORECASE,
            )

        lives_match = re.search(r"(\d+)\s*(?:lives|生命|命)", fb)
        if lives_match:
            lives = lives_match.group(1)
            code = re.sub(
                r"(lives\s*[:=]\s*)\d+",
                lambda match: match.group(1) + lives,
                code,
                flags=re.IGNORECASE,
            )

        primary_colors = r"#(?:6366f1|6e56ff|4f46e5|7c3aed)"
        if "red" in fb or "红" in fb:
            code = re.sub(primary_colors, "#ef4444", code, flags=re.IGNORECASE)
        if "green" in fb or "绿" in fb:
            code = re.sub(primary_colors, "#22c55e", code, flags=re.IGNORECASE)
        if "blue" in fb or "蓝" in fb:
            code = re.sub(primary_colors, "#3b82f6", code, flags=re.IGNORECASE)
        if "yellow" in fb or "黄" in fb:
            code = re.sub(primary_colors, "#eab308", code, flags=re.IGNORECASE)

        return code

    @staticmethod
    def _select_iteration_patch_sections(
        iter_type: IterationType,
        feedback: str,
    ) -> tuple[str, ...]:
        sections: List[str] = [PATCH_SECTION_SCRIPT]
        if iter_type == IterationType.mechanic_change:
            sections.insert(0, PATCH_SECTION_BODY)
        elif iter_type == IterationType.element_change and _feedback_involves_markup(feedback):
            sections.insert(0, PATCH_SECTION_BODY)
        if _feedback_involves_style(feedback):
            sections.insert(0, PATCH_SECTION_STYLE)
        deduped: List[str] = []
        for section in sections:
            if section not in deduped:
                deduped.append(section)
        return tuple(deduped)

    async def _llm_iterate(
        self,
        code: str,
        feedback: str,
        conversation: List[dict],
        iter_type: IterationType,
        runtime_contract: Optional[GameRuntimeContract] = None,
        runtime_profile: Optional[str] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        game_spec: Optional[GameSpec] = None,
    ) -> str:
        history_text = "\n".join(
            f"{item.get('role', 'user')}: {item.get('content', '')}"
            for item in conversation[-4:]
        )
        allowed_sections = self._select_iteration_patch_sections(iter_type, feedback)
        section_context = build_section_context(code, allowed_sections)
        patch_protocol = build_patch_protocol(allowed_sections, task_label="iteration")

        if iter_type == IterationType.param_adjust:
            prompt = require_prompt("prompt.param_adjust").format(
                feedback=feedback,
                code=section_context,
            )
            step_key = "iterate.param_adjust"
        elif iter_type == IterationType.element_change:
            prompt = require_prompt("prompt.element_change").format(
                feedback=feedback,
                code=section_context,
            )
            step_key = "iterate.element_change"
        else:
            prompt = require_prompt("prompt.mechanic_change").format(
                feedback=feedback,
                history=history_text,
                code=section_context,
            )
            step_key = "iterate.mechanic_change"

        contract_block = self._build_runtime_contract_block(
            runtime_contract,
            runtime_profile,
            prompt_bundle_snapshot,
        )
        contract_implementation_block = self._build_contract_implementation_checklist(
            runtime_contract,
            runtime_profile,
        )
        ui_language_block = self._build_ui_language_block(game_spec.ui_language if game_spec else "en-US")

        if iter_type == IterationType.mechanic_change:
            prompt = "\n\n".join(
                part
                for part in [
                    patch_protocol,
                    contract_block,
                    contract_implementation_block,
                    ui_language_block,
                    prompt,
                ]
                if part
            )
        else:
            prompt = "\n\n".join(
                part
                for part in [
                    patch_protocol,
                    contract_block,
                    contract_implementation_block,
                    ui_language_block,
                    prompt,
                ]
                if part
            )

        try:
            default_request_timeout_s = self._generation_request_timeout_budget_s(game_spec)
            request_timeout_s = self._resolve_step_request_timeout_s(
                step_key,
                spec=game_spec,
                default_timeout_s=default_request_timeout_s,
            )
            overall_timeout_s = self._resolve_step_overall_timeout_s(
                step_key,
                spec=game_spec,
                request_timeout_s=request_timeout_s,
                default_timeout_s=self._generation_overall_timeout_budget_s(game_spec),
            )
            hedge_after_s = self._generation_provider_hedge_delay_s(game_spec)
            token_budget = self._select_token_budget(game_spec)
            truncation_retry_cap = self._select_truncation_retry_cap(game_spec)
            text = await self._client.complete_with_truncation_retry(
                max_tokens=token_budget,
                system=self._build_system_prompt(prompt_bundle_snapshot, spec=game_spec),
                messages=[{"role": "user", "content": prompt}],
                step_key=step_key,
                stage="code_generating",
                request_timeout_s=request_timeout_s,
                overall_timeout_s=overall_timeout_s,
                allow_provider_fallback=True,
                response_size_hint=self._response_size_hint_from_budget(token_budget),
                context_scope="request",
                compression_policy="iteration_rewrite",
                truncation_retry_attempts=1,
                truncation_retry_increment=2048,
                truncation_retry_max_tokens=truncation_retry_cap,
                timeout_retry_attempts=0,
                provider_retry_attempts=1,
                provider_retry_on_timeout_errors=False,
                provider_retry_base_delay_s=1,
                provider_retry_max_delay_s=2,
                hedge_provider_fallback_after_s=hedge_after_s,
            )
            patches, full_html = parse_patch_response(text, allowed_sections=allowed_sections)
            if full_html:
                candidate = ensure_structured_section_markers(_extract_html(full_html))
                validation_errors = validate_patch_candidate(
                    code,
                    candidate,
                    allowed_sections=allowed_sections,
                )
                if validation_errors:
                    logger.warning(
                        "Iteration full-document fallback rejected; keeping previous stable code: %s",
                        ", ".join(validation_errors),
                    )
                    return code
                return candidate
            if patches:
                candidate = apply_section_patches(code, patches)
                validation_errors = validate_patch_candidate(
                    code,
                    candidate,
                    allowed_sections=allowed_sections,
                )
                if validation_errors:
                    logger.warning(
                        "Iteration patch candidate rejected; keeping previous stable code: %s",
                        ", ".join(validation_errors),
                    )
                    return code
                return candidate

            raw = _extract_code_block(text)
            if re.search(r"<!DOCTYPE\s+html|<html", raw, re.IGNORECASE):
                candidate = ensure_structured_section_markers(_extract_html(text))
                validation_errors = validate_patch_candidate(
                    code,
                    candidate,
                    allowed_sections=allowed_sections,
                )
                if validation_errors:
                    logger.warning(
                        "Iteration raw HTML fallback rejected; keeping previous stable code: %s",
                        ", ".join(validation_errors),
                    )
                    return code
                return candidate
            if PATCH_SECTION_SCRIPT in allowed_sections:
                candidate = apply_section_patches(
                    code,
                    [SectionPatch(section=PATCH_SECTION_SCRIPT, content=raw)],
                )
                validation_errors = validate_patch_candidate(
                    code,
                    candidate,
                    allowed_sections=allowed_sections,
                )
                if validation_errors:
                    logger.warning(
                        "Iteration raw script patch rejected; keeping previous stable code: %s",
                        ", ".join(validation_errors),
                    )
                    return code
                return candidate
            candidate = ensure_structured_section_markers(_extract_html(text))
            validation_errors = validate_patch_candidate(
                code,
                candidate,
                allowed_sections=allowed_sections,
            )
            if validation_errors:
                logger.warning(
                    "Iteration candidate rejected after parse fallback; keeping previous stable code: %s",
                    ", ".join(validation_errors),
                )
                return code
            return candidate
        except Exception as exc:
            logger.error("LLM iterate failed: %s", exc)
            raise RuntimeError(f"LLM iterate failed: {exc}") from exc

def _extract_html(text: str) -> str:
    """Extract clean HTML from LLM output."""
    text = text.lstrip("\ufeff")
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"```(?:html)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*(?:$|\n)", "", text, flags=re.MULTILINE)
    match = re.search(r"(<!DOCTYPE\s+html|<html)", text, re.IGNORECASE)
    if match:
        text = text[match.start():]
    return text.strip()
_STYLE_FEEDBACK_KEYWORDS = (
    "color", "colour", "font", "background", "border",
    "css", "dark mode", "light mode",
    "颜色", "背景", "字体", "主题色", "深色模式", "浅色模式",
)


def _feedback_involves_style(feedback: str) -> bool:
    """Check if feedback mentions visual/CSS concerns."""
    lower = feedback.lower()
    return any(keyword in lower for keyword in _STYLE_FEEDBACK_KEYWORDS)


_MARKUP_FEEDBACK_KEYWORDS = (
    "hud", "button", "overlay", "menu", "title", "label", "scoreboard",
    "score hud", "ui", "layout", "panel", "text", "tutorial",
    "buttons", "title screen", "restart button",
    "按钮", "标题", "文本", "布局", "面板", "教程", "菜单", "分数",
)


def _feedback_involves_markup(feedback: str) -> bool:
    lower = feedback.lower()
    return any(keyword in lower for keyword in _MARKUP_FEEDBACK_KEYWORDS)


def _extract_code_block(text: str) -> str:
    """Extract content from markdown code fences, or return text as-is."""
    text = text.lstrip("\ufeff")
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"```(?:javascript|js|css|html)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*(?:$|\n)", "", text, flags=re.MULTILINE)
    return text.strip()
