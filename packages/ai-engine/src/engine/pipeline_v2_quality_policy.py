"""Quality policy extracted from pipeline_v2_runner.py."""

from __future__ import annotations
import logging
import json
import re
from typing import Any, Optional
from ..api.models import GameRuntimeContract, GameSpec, QACheckError, RunPipelineV2Request
from ..config.settings import settings
from .code_generator import CodeGenerator
from .pipeline_errors import PipelineExecutionError
from .generated_quality_policy import QUALITY_POLICY
from .quality_scorer import LLMReviewResult, QAStaticResult
from .runtime_profile_ids import normalize_runtime_profile_id
try:  # pragma: no cover
    from .p2_telemetry import emit as _p2_emit  # type: ignore
except Exception:  # pragma: no cover
    def _p2_emit(event: str, **fields):  # type: ignore
        return None

from .section_patch import (
    PATCH_SECTION_SCRIPT,
    PATCH_SECTION_STYLE,
    apply_section_patches,
    build_patch_protocol,
    build_section_context,
    extract_body_content,
    extract_patchable_sections,
    ensure_structured_section_markers,
    parse_patch_response,
    validate_patch_candidate,
)
from .pipeline_v2_support import (
    DEFAULT_CREATE_FULL_GENERATION_ATTEMPTS,
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
    def _validate_quality_patch_extent(code, patches):
        from .source_references import locate_source_edit
        sections = extract_patchable_sections(code)
        for section in {p.section for p in patches}:
            source = sections.get(section) or ''
            edits = [p for p in patches if p.section == section]
            try:
                ranges = [locate_source_edit(source,search=p.search,source_ref=p.source_ref) for p in edits]
            except ValueError as exc:
                raise ValueError('patch_validation_failed:'+str(exc)) from exc
            if sum(end-start for start,end in ranges) > len(source) * .6:
                raise ValueError('patch_validation_failed:local_patch_replaces_too_much')
            if sum(len(p.content) for p in edits) > max(4096, len(source) * .6):
                raise ValueError('patch_validation_failed:local_patch_output_too_large')

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
        # Parsers sometimes label non-interactive scenery as NPCs. A moon or
        # mountain silhouette does not introduce a character drawing requirement.
        scenery = re.compile(r"\b(?:moon|mountains?|sky|clouds?|star\s*dust|particles?|scenery|background)\b|月牙|月亮|远山|山脉|天空|云朵|星屑|背景", re.I)
        if any(entity.role == "enemy" or (entity.role == "npc" and not scenery.search(entity.name))
               for entity in (spec.entities or [])):
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
        if review.evidence_verified and review.findings:
            for finding in review.findings:
                lines.append(f"- {finding['issue']}: {finding['reason']} Correction: {finding['correction']}")
            lines.append('- Preserve all unrelated behavior and visual design; do not add optional features to chase a score.')
            return "\n".join(lines)
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
        for issue in (review.issues or [])[:10]:
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
        """Repair source-grounded local defects before replacing working code.

        Call only after contract/runtime QA. Evidence can establish a local
        repair even when missing pause/reset behavior lowers completeness and
        aggregate scores. A missing playable loop still needs regeneration.
        Existing near-miss eligibility is retained for older review callers.
        """
        if not getattr(review, "ran", False):
            return False
        if not getattr(review, "has_real_gameplay", False):
            return False
        if review.evidence_verified and review.findings:
            return all(finding['repair_scope'] == 'local'
                       and finding['section'] in (PATCH_SECTION_SCRIPT, PATCH_SECTION_STYLE)
                       for finding in review.findings)
        if not getattr(review, "is_complete_game", False):
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
    def _quality_patch_regressed(cls, spec: GameSpec, review: LLMReviewResult,
                                 quality: Any, outcome: _QualityGatePatchOutcome) -> bool:
        if not outcome.review.ran:
            return True
        for field in ("is_complete_game", "has_real_gameplay", "difficulty_balanced"):
            if getattr(review, field) and not getattr(outcome.review, field):
                return True
        thresholds = cls._quality_gate_thresholds(spec)
        # Compare deficits, not bonus points above the gate. Fixing a failing
        # dimension may trade excess polish, but may not introduce a new miss.
        for field, threshold in (
            ("fun_score", thresholds["fun_score"]),
            ("visual_polish_score", thresholds["visual_polish_score"]),
            ("character_quality_score", cls._quality_patch_character_threshold(spec)),
        ):
            if min(getattr(outcome.review, field), threshold) + 1e-6 < min(getattr(review, field), threshold):
                return True
        return min(outcome.quality.final_score, thresholds["final_score"]) + 1e-6 < min(quality.final_score, thresholds["final_score"])

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
        if review.evidence_verified and review.findings:
            sections = {finding['section'] for finding in review.findings}
            return tuple(section for section in (PATCH_SECTION_STYLE, PATCH_SECTION_SCRIPT) if section in sections)
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
        progress_cb: ProgressCallback,
        allow_runtime_qa_unavailable: bool,
        patch_attempt: int = 1,
        max_patch_attempts: int = 1,
    ) -> _QualityGatePatchOutcome:
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
                "maxPatchAttempts": max_patch_attempts,
                "reviewIssues": list(review.issues or [])[:10],
                "qualityGateErrors": quality_gate_errors[:6],
            },
        )
        _p2_emit(
            "quality_gate_patch_attempt",
            tier=getattr(getattr(spec, "generation_tier", None), "value", None),
            game_type=getattr(spec, "game_type", None),
            sections=",".join(allowed_sections),
            final_score=float(getattr(quality, "final_score", 0.0) or 0.0),
        )
        failure_stage = "request_prepare"
        response_chars = 0
        patch_count = 0
        try:
            normalized_code = ensure_structured_section_markers(code)
            body_context = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", extract_body_content(normalized_code) or "", flags=re.I)
            repair_context = "\n\n".join(
                part
                for part in [
                    self._build_review_quality_guidance(spec, review, quality, quality_gate_errors),
                    "ORIGINAL USER REQUIREMENTS (preserve):\n" + str(spec.source_description or ""),
                    "REPAIR DISCIPLINE: Preserve working behavior and composition. Fix concrete defects before optional polish. "
                    "Use only local exact replacements, never rewrite a complete section. Check every reported defect against the source; "
                    "retain fixes from previous rounds, including timing, pause, coordinates and restart.",
                    "SOURCE-GROUNDED FINDINGS:\n" + json.dumps(review.findings, ensure_ascii=False),
                    "UNCHANGED BODY STRUCTURE (read-only; reuse these element IDs, do not invent missing controls):\n" + body_context,
                    build_section_context(normalized_code, allowed_sections, indexed=True),
                ]
                if part
            )
            prompt = build_patch_protocol(allowed_sections, task_label="quality gate repair", strict=True, exact_only=True) + "\n\n" + repair_context
            failure_stage = "provider_request"
            text = await self._request_quality_gate_patch_text(
                prompt=prompt,
                spec=spec,
                prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
            )
            response_chars = len(text or "")
            for correction in range(2):
                try:
                    failure_stage = 'patch_correction_parse' if correction else 'patch_parse'
                    patches, _ = parse_patch_response(text, allowed_sections=allowed_sections, strict=True, exact_only=True)
                    patch_count = len(patches or [])
                    failure_stage = 'patch_application'
                    self._validate_quality_patch_extent(normalized_code, patches)
                    candidate = apply_section_patches(normalized_code, patches)
                    # A parseable batch is not yet a valid candidate. Validate
                    # its boundaries and syntax before spending runtime/review
                    # or the orchestrator's full-generation recovery budget.
                    failure_stage = 'patch_validation'
                    validation_errors = validate_patch_candidate(
                        normalized_code, candidate, allowed_sections=allowed_sections)
                    if validation_errors:
                        raise ValueError('patch_validation_failed:' + ','.join(validation_errors))
                    failure_stage = 'contract_qa'
                    contract_errors = self._validate_contract_bundle(candidate, runtime_contract)
                    if contract_errors:
                        raise ValueError('patch_static_qa_failed:' + '; '.join(
                            error.message for error in contract_errors[:3]))
                    break
                except ValueError as patch_error:
                    # The failed batch is atomic: correct against the ORIGINAL source,
                    # never against a partially applied batch. One bounded format retry.
                    if correction or not str(patch_error).startswith((
                        'patch_validation_failed:', 'patch_static_qa_failed:')):
                        raise
                    self._notify(progress_cb, "code_review", 95, "Correcting invalid patch references", {
                        "gameId": request.game_id, "userId": request.user_id,
                        "rejectionReason": str(patch_error)[:300], "correctionAttempt": 1,
                        "failureStage": failure_stage, "candidateRetained": True,
                    })
                    failure_stage = "patch_correction_request"
                    text = await self._request_quality_gate_patch_text(
                        prompt=build_patch_protocol(allowed_sections, task_label="quality gate repair correction",
                            strict=True, exact_only=True)
                        + "\n\n" + repair_context + "\n\nPATCH APPLICATION REJECTED: " + str(patch_error)[:300]
                        + "\nNo edits were applied. Return a corrected JSON batch against the ORIGINAL "
                        "sections above. Expand the exact search context until unique; correct any reported syntax or contract errors. "
                        "Use only small replace_exact changes; do not fall back to rewriting complete sections.",
                        spec=spec,
                        prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
                    )
                    response_chars = len(text or "")
            touched_sections = {patch.section for patch in patches}

            # CSS can hide controls or intercept clicks, so every changed
            # document needs runtime QA, including STYLE-only patches.
            failure_stage = "runtime_qa"
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
            # Runtime QA can repair code. Validate and score that exact output.
            contract_errors = self._validate_contract_bundle(candidate, runtime_contract)
            if contract_errors:
                raise RuntimeError("patch_static_qa_failed:" + "; ".join(error.message for error in contract_errors[:3]))
            static_check = self.qa_pipeline.check(candidate)

            failure_stage = "review"
            patched_review = await self.code_reviewer.review(candidate, user_requirements=spec.source_description or "")
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
            failure_stage = "quality_gate"
            remaining_errors = self._quality_gate_errors(
                spec,
                patched_review,
                patched_quality,
                review_required=self._is_structured_review_required(spec),
            )
            if not patched_review.ran:
                remaining_errors.append("Repair must receive a structured review before it can replace the previous candidate.")
            await self._remember_code(candidate, label=f"quality_patch_candidate_{patch_attempt}")
        except Exception as exc:  # noqa: BLE001 - any failure falls back to regeneration
            # Provider exceptions may contain request credentials; expose only
            # our known validation failures, never raw provider response bodies.
            raw_reason = str(exc)
            reason = raw_reason[:700] if isinstance(exc, PipelineExecutionError) or raw_reason.startswith((
                "patch_parse_empty", "patch_validation_failed:", "patch_static_qa_failed:",
            )) else type(exc).__name__
            self._notify(progress_cb, "code_review", 95, "Targeted quality repair rejected", {
                "gameId": request.game_id,
                "userId": request.user_id,
                "patchAttempt": patch_attempt,
                "failureStage": failure_stage,
                "rejectionReason": reason,
                "exceptionClass": type(exc).__name__,
                "responseChars": response_chars,
                "patchCount": patch_count,
                "allowedSections": list(allowed_sections),
            })
            logger.warning(
                "Quality repair for game %s failed; returning failure to orchestrator: %s",
                request.game_id,
                reason,
            )
            _p2_emit(
                "quality_gate_patch_failed",
                tier=getattr(getattr(spec, "generation_tier", None), "value", None),
                game_type=getattr(spec, "game_type", None),
                reason=reason,
            )
            # The orchestrator owns retries. Preserve the exact failed candidate,
            # structured report and stage instead of replacing them with stale scores.
            if isinstance(exc, PipelineExecutionError):
                raise
            raise PipelineExecutionError(
                "Quality repair failed during " + failure_stage + ": " + reason,
                stage="code_review", failure_family=('repair_protocol' if failure_stage in {
                    'patch_parse','patch_correction_parse','patch_application'} else 'repair_contract'),
                artifacts=[
                    self._build_text_artifact(artifact_type='failed_quality_candidate', payload=code,
                        metadata={'stage':'code_review','retained':True}),
                    self._build_json_artifact(artifact_type='quality_repair_report', payload={
                        'failureStage':failure_stage,'reason':reason,'patchAttempt':patch_attempt,
                        'responseChars':response_chars,'patchCount':patch_count,
                    }, metadata={'stage':'code_review'}),
                ],
            ) from exc

        logger.info(
            "Quality-gate patch candidate for game %s assessed (sections=%s)",
            request.game_id,
            ",".join(sorted(touched_sections)),
        )
        _p2_emit(
            "quality_gate_patch_assessed" if remaining_errors else "quality_gate_patch_success",
            tier=getattr(getattr(spec, "generation_tier", None), "value", None),
            game_type=getattr(spec, "game_type", None),
            sections=",".join(sorted(touched_sections)),
            final_score=float(getattr(patched_quality, "final_score", 0.0) or 0.0),
        )
        self._notify(
            progress_cb,
            "code_review",
            96,
            "Targeted quality fixes need another pass" if remaining_errors else "Targeted quality fixes passed the quality gate",
            {
                "gameId": request.game_id,
                "userId": request.user_id,
                "runtimeProfile": runtime_contract.runtime_profile,
                "patchedSections": sorted(touched_sections),
                "patchAttempt": patch_attempt,
                "qualityGateErrors": remaining_errors,
                "reviewIssues": patched_review.issues,
            },
        )
        return _QualityGatePatchOutcome(
            code=candidate,
            review=patched_review,
            quality=patched_quality,
            runtime_qa=runtime_qa,
            runtime_retries=runtime_retries,
            qa_warnings=qa_warnings,
            gate_errors=remaining_errors,
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
