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
from .prompt_store import get_runtime_profile, require_prompt
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


from .code_generation_prompts import CodeGenerationPromptsMixin
from .code_generation_support import (
    _SafePromptFormatDict,
    GAME_TYPE_CORE_MECHANIC_SUMMARY,
    LOCALIZED_CORE_MECHANIC_SUMMARY,
    UI_LANGUAGE_LABELS,
    EDUCATIONAL_REQUEST_MARKERS,
    PLAYER_SIZE_BY_GAME_TYPE,
    _GENERIC_SPECIAL_RULE_PHRASES,
    _STANDARD_COMPLEXITY_KEYWORDS,
    _COMPLEX_COMPLEXITY_KEYWORDS,
    _extract_html,
    _STYLE_FEEDBACK_KEYWORDS,
    _feedback_involves_style,
    _MARKUP_FEEDBACK_KEYWORDS,
    _feedback_involves_markup,
    _extract_code_block,
)

class CodeGenerator(CodeGenerationPromptsMixin):
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
        generation_tier = self._resolve_generation_tier(spec)
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
        from .creative_design import core_playability_contract
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
                core_playability_contract(spec),
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
                and generation_tier == "safe"
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
    def _build_visual_quality_hint(spec: GameSpec) -> str:
        theme = (spec.visual_style.theme or "the requested world").strip()
        art_style = (spec.visual_style.art_style or "stylized premium canvas art").strip()
        palette = ", ".join((spec.visual_style.palette or [])[:4]).strip()
        template = require_prompt("prompt.visual_quality_bar")
        return template.format_map(_SafePromptFormatDict({
            "theme": theme,
            "art_style": art_style,
            "palette_line": (f"- Keep the art cohesive around a controlled palette such as {palette}." if palette else ""),
        }))

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
        visual_quality_hint = self._build_visual_quality_hint(spec)
        extra_blocks = [block for block in (base_block, visual_quality_hint, distinctive_hint) if block]
        return "\n".join(extra_blocks)

    @classmethod
    def _build_generation_tier_block(cls, spec: Optional[GameSpec]) -> str:
        generation_tier = cls._resolve_generation_tier(spec)
        return require_prompt(f"prompt.generation_tier_{generation_tier}").strip()

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
            "- Give this run one signature interaction that is visible in the first 10 seconds and cannot be mistaken for a generic dodge/clicker/match clone.",
            "- Make the scoring, failure pressure, and reward feedback specific to the requested theme rather than reskinning a default loop.",
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
        template = require_prompt("prompt.runtime_contract_summary")
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

    def _build_platform_standard_fallback(self) -> str:
        runtime_contract_template = require_prompt("prompt.runtime_contract_summary").lower()
        if "mobile h5 browser" in runtime_contract_template or "platform target" in runtime_contract_template:
            return ""
        return require_prompt("prompt.platform_standard")

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
    def _rewrite_system_prompt_for_generation_tier(
        cls,
        system_prompt: str,
        spec: Optional[GameSpec] = None,
    ) -> str:
        generation_tier = cls._resolve_generation_tier(spec)
        rewritten = system_prompt or ""
        if generation_tier == "safe":
            return rewritten
        extra = require_prompt(f"prompt.code_gen_system_{generation_tier}").strip()
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
            exc_name = exc.__class__.__name__
            exc_message = str(exc).strip()
            detail = f"{exc_name}: {exc_message}" if exc_message else exc_name
            raise RuntimeError(f"LLM iterate failed: {detail}") from exc
