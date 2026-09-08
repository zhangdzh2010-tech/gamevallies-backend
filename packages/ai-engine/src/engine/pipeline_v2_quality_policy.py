"""Quality policy extracted from pipeline_v2_runner.py."""

from __future__ import annotations
import logging
import re
from typing import Any, Optional
from ..api.models import GameRuntimeContract, GameSpec, QACheckError, RunPipelineV2Request
from ..config.settings import settings
from .code_generator import CodeGenerator
from .generated_quality_policy import QUALITY_POLICY
from .quality_scorer import LLMReviewResult, QAStaticResult
from .runtime_profile_ids import normalize_runtime_profile_id
try:  # pragma: no cover
    from .p2_telemetry import emit as _p2_emit  # type: ignore
except Exception:  # pragma: no cover
    def _p2_emit(event: str, **fields):  # type: ignore
        return None

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
from .pipeline_v2_support import (
    DEFAULT_CREATE_FULL_GENERATION_ATTEMPTS,
    QUALITY_GATE_PATCH_ATTEMPTS_PER_FAILURE,
    QUALITY_GATE_PATCH_MAX_FINAL_SCORE_GAP,
    QUALITY_GATE_PATCH_MAX_DIMENSION_GAP,
    ProgressCallback,
    _QualityGatePatchOutcome,
    ACTION_RUNTIME_MARKERS,
    PUZZLE_RUNTIME_MARKERS,
)

logger = logging.getLogger(__name__)

class PipelineV2QualityPolicyMixin:
    """Quality policy behavior; state remains owned by V2PipelineRunner."""

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
        return dict(QUALITY_POLICY["tiers"].get(generation_tier, QUALITY_POLICY["tiers"]["standard"]))


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
            (bool(re.search(r"\b" + re.escape(marker) + r"\b", searchable))
             if marker.isascii() else marker in searchable)
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
