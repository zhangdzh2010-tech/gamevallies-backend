"""V2 pipeline runner: spec-first execution flow for create/iterate."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable, Optional

from ..api.models import (
    GDD,
    GameRuntimeContract,
    GameSpec,
    IterationType,
    IterateResponse,
    IterateV2Request,
    QAResult,
    RunPipelineResponse,
    RunPipelineV2Request,
)
from ..config.settings import settings
from ..config.timeout_store import get_float as get_timeout_float, get_int as get_timeout_int
from ..services.llm_gateway import get_request_context
from ..services.task_memory import task_memory
from .code_preflight import CodePreflightValidator
from .code_generator import CodeGenerator
from .code_reviewer import CodeReviewer
from .dialogue_engine import DialogueEngine, _looks_like_educational_request
from .game_designer import GameDesigner
from .pipeline_errors import PipelineExecutionError, is_provider_transport_failure, is_truncation_failure
from .pre_generation_validator import PreGenerationValidator
from .prompt_store import require_prompt
from .qa_pipeline import QAPipeline
from .quality_scorer import LLMReviewResult, QAStaticResult, QualityScorer, RuntimeQAResult
from .runtime_profile_ids import normalize_runtime_profile_id
from .interactive_creation import is_interactive_request, normalize_interactive_request, run_interactive
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
from .visual_pack_catalog import apply_visual_pack_defaults

logger = logging.getLogger(__name__)


from .pipeline_v2_quality_policy import PipelineV2QualityPolicyMixin
from .pipeline_v2_specification import PipelineV2SpecificationMixin
from .pipeline_v2_validation import PipelineV2ValidationMixin
from .pipeline_v2_support import (
    DEFAULT_STAGE_TOTAL_ATTEMPTS,
    QUALITY_GATE_PATCH_STEP_KEY,
    QUALITY_GATE_PATCH_MAX_ATTEMPTS,
    GENERATION_PROGRESS_HEARTBEAT_INTERVAL_S,
    GENERATION_PROGRESS_HEARTBEAT_BASE_PCT,
    GENERATION_PROGRESS_HEARTBEAT_MAX_PCT,
    GENERATION_PROGRESS_HEARTBEAT_EXPECTED_DURATION_S,
    ITERATE_QUALITY_REVIEW_TIMEOUT_KEY,
    ITERATE_QUALITY_REVIEW_DEFAULT_TIMEOUT_S,
    ProgressCallback,
    _QualityGatePatchOutcome,
    PROFILE_CANDIDATES_BY_GAME_TYPE,
    PROFILE_KEYWORD_FALLBACKS,
    ACTION_FOCUSED_RUNTIME_PROFILES,
    PROFILE_TO_GAME_TYPE_HINT,
    _default_runtime_profile_id,
)

class V2PipelineRunner(PipelineV2QualityPolicyMixin, PipelineV2SpecificationMixin, PipelineV2ValidationMixin):
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
    def _looks_like_quiz_show_runtime_request(spec: GameSpec) -> bool:
        searchable = V2PipelineRunner._profile_selection_text(spec)
        return any(
            (bool(re.search(r"\b" + re.escape(marker) + r"\b", searchable))
             if marker.isascii() else marker in searchable)
            for marker in (
                "quiz show",
                "game show",
                "trivia show",
                "millionaire",
                "buzzer",
                "答题秀",
                "答题节目",
                "节目答题",
                "综艺答题",
                "综艺节目",
                "舞台秀",
                "舞台答题",
                "主持人",
            )
        )

    async def _request_quality_gate_patch_text(
        self,
        *,
        prompt: str,
        spec: GameSpec,
        prompt_bundle_snapshot: Optional[dict[str, Any]],
    ) -> str:
        generator = self.code_generator
        from .creative_design import core_playability_contract
        prompt = prompt + "\n\n" + core_playability_contract(spec)
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
        # A local repair is not another full script generation. Keep both the
        # first response and truncation recovery bounded independently of tier.
        token_budget = 4096
        # prefer_fast intentionally left off: quality repair needs the
        # primary model, not the fast lane.
        return await generator._client.complete_with_truncation_retry(
            max_tokens=token_budget,
            system=generator._build_system_prompt(prompt_bundle_snapshot, spec=spec) + (
                "\n\nCURRENT REPAIR OUTPUT OVERRIDE:\n"
                "For this repair turn, replace the full-HTML output requirement above with the PATCH-FIRST JSON contract in the request. "
                "Return only a valid JSON object containing patches for the allowed sections; do not return a complete HTML document. "
                "All safety, gameplay, language and quality requirements above still apply to the resulting document."
            ),
            messages=[{"role": "user", "content": prompt}],
            step_key=step_key,
            stage="code_review",
            request_timeout_s=request_timeout_s,
            overall_timeout_s=overall_timeout_s,
            allow_provider_fallback=True,
            response_size_hint='large_patch',
            context_scope="request",
            compression_policy="iteration_rewrite",
            truncation_retry_attempts=1,
            truncation_retry_increment=2048,
            truncation_retry_max_tokens=8192,
            timeout_retry_attempts=0,
            provider_retry_attempts=1,
            provider_retry_on_timeout_errors=False,
            provider_retry_base_delay_s=1,
            provider_retry_max_delay_s=2,
            hedge_provider_fallback_after_s=CodeGenerator._generation_provider_hedge_delay_s(spec),
        )

    async def _remember_spec(self, spec: Optional[GameSpec]) -> None:
        if spec is not None:
            from .creative_design import gameplay_fingerprint
            from .generated_quality_policy import QUALITY_POLICY
            spec.gameplay_fingerprint = gameplay_fingerprint(spec)
            spec.quality_policy_version = QUALITY_POLICY["version"]
        await task_memory.remember_spec(self._current_task_id(), spec)

    async def _repair_create_quality(self, **kwargs: Any) -> _QualityGatePatchOutcome:
        """Continue on validated code; only structural failures regenerate.

        This orchestrator owns the bounded repair budget. Regressing candidates
        never become the next base, and exhausted quality misses retain evidence.
        """
        spec = kwargs["spec"]
        current_code = kwargs["code"]
        current_review = kwargs["review"]
        current_quality = kwargs["quality"]
        current_errors = kwargs["quality_gate_errors"]
        total_retries = 0
        warnings: list[dict[str, Any]] = []
        for attempt in range(1, QUALITY_GATE_PATCH_MAX_ATTEMPTS + 1):
            outcome = await self._attempt_quality_gate_patch_repair(**{
                **kwargs, "code": current_code, "review": current_review,
                "quality": current_quality, "quality_gate_errors": current_errors,
                "base_retries": kwargs["base_retries"] + total_retries,
                "patch_attempt": attempt, "max_patch_attempts": QUALITY_GATE_PATCH_MAX_ATTEMPTS,
            })
            total_retries += outcome.runtime_retries
            warnings.extend(outcome.qa_warnings)
            regressed = self._quality_patch_regressed(spec, current_review, current_quality, outcome)
            if not outcome.gate_errors and not regressed:
                outcome.runtime_retries = total_retries
                outcome.qa_warnings = warnings
                return outcome
            self._notify(kwargs["progress_cb"], "code_review", 95,
                "Retaining previous candidate after repair regression" if regressed else "Continuing repair on validated candidate", {
                    "gameId": kwargs["request"].game_id, "patchAttempt": attempt,
                    "maxPatchAttempts": QUALITY_GATE_PATCH_MAX_ATTEMPTS,
                    "candidateRetained": not regressed, "qualityGateErrors": outcome.gate_errors,
                })
            if not regressed:
                current_code, current_review, current_quality = outcome.code, outcome.review, outcome.quality
                current_errors = outcome.gate_errors
            else:
                current_errors = list(current_errors) + [
                    "The last patch regressed quality and was discarded. Apply a smaller local correction to this retained source."
                ]
        await self._remember_code(current_code, label="retained_quality_candidate")
        raise PipelineExecutionError(
            "Quality repair budget exhausted: " + "; ".join(current_errors[:4])
            + " Review issues: " + "; ".join(current_review.issues[:10]),
            stage="code_review", failure_family="quality_repair_exhausted",
            artifacts=[
                self._build_text_artifact(artifact_type="failed_quality_candidate", payload=current_code,
                    metadata={"stage": "code_review"}),
                self._build_json_artifact(artifact_type="quality_review_report", payload={
                    "issues": current_review.issues, "gateErrors": current_errors,
                    "fun_score": current_review.fun_score, "visual_polish_score": current_review.visual_polish_score,
                    "character_quality_score": current_review.character_quality_score,
                    "findings": current_review.findings, "evidenceVerified": current_review.evidence_verified,
                    "final_score": current_quality.final_score, "patchAttempts": QUALITY_GATE_PATCH_MAX_ATTEMPTS,
                }, metadata={"stage": "code_review"}),
            ],
        )

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
        if is_interactive_request(request):
            return await self._run_interactive_with_stage(request, progress_cb, stage_context)
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
            self._requested_runtime_profile(request.runtime_contract),
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
        extra_truncation_retry_granted = False

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
                        artifacts=[
                            self._build_text_artifact(artifact_type="failed_preflight_candidate",
                                payload=generated.html_code, metadata={"attempt":quality_attempt,"stage":"logic_generate"}),
                            self._build_json_artifact(artifact_type="preflight_report", payload={
                                "attempt":quality_attempt,"issues":[{"code":issue.code,"message":issue.message} for issue in preflight_issues],
                            }, metadata={"stage":"logic_generate"}),
                        ],
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
                            "failureFamily": "code_generation",
                            "issues": [{"type": type(issue).__name__, "message": issue.message} for issue in preflight_issues],
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
                            (lambda html: self.code_reviewer.review(html, user_requirements=spec.source_description or "")) if review_requested else None
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
                        artifacts=[
                            self._build_text_artifact(
                                artifact_type="failed_contract_candidate", payload=qa_result.code,
                                metadata={"stage": "contract_qa", "attempt": quality_attempt},
                            ),
                            self._build_json_artifact(
                                artifact_type="contract_qa_report",
                                payload={"passed": False, "attempt": quality_attempt,
                                         "retryCount": qa_result.retries,
                                         "errors": self._serialize_errors(qa_result.last_errors)},
                                metadata={"stage": "contract_qa"},
                            ),
                        ],
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
                review = await self._resolve_create_review(
                    concurrent_review,
                    qa_result.code,
                    spec=spec,
                    user_requirements=spec.source_description or "",
                    review_requested=review_requested,
                    qa_warnings=qa_warnings,
                    progress_cb=progress_cb,
                    game_id=request.game_id,
                    user_id=request.user_id,
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
                        artifacts=[
                            self._build_text_artifact(artifact_type="failed_quality_candidate",
                                payload=qa_result.code, metadata={"attempt":quality_attempt,"stage":"code_review"}),
                            self._build_json_artifact(artifact_type="quality_review_report", payload={
                                "attempt":quality_attempt,"issues":review.issues,
                                "gateErrors":quality_gate_errors,"final_score":quality.final_score,
                                "findings":review.findings,"evidenceVerified":review.evidence_verified,
                            }, metadata={"stage":"code_review"}),
                        ],
                    )
                    if (
                        getattr(settings, "QUALITY_GATE_PATCH_REPAIR_ENABLED", True)
                        and self._should_attempt_quality_patch_repair(spec, review, quality)
                    ):
                        try:
                            patch_outcome = await self._repair_create_quality(
                                request=request,
                                spec=spec,
                                runtime_contract=runtime_contract,
                                code=qa_result.code,
                                generation_strategy=generated.strategy,
                                base_retries=qa_result.retries + runtime_retries,
                                review=review,
                                quality=quality,
                                quality_gate_errors=quality_gate_errors,
                                progress_cb=progress_cb,
                                allow_runtime_qa_unavailable=allow_runtime_qa_unavailable,
                            )
                        except PipelineExecutionError as patch_exc:
                            patch_family = getattr(patch_exc, "failure_family", None)
                            if patch_family not in {"repair_protocol", "repair_contract"}:
                                raise
                            qa_warnings.append({
                                "type": "quality_gate_patch_degraded",
                                "message": "Targeted patch repair failed; falling back to full regeneration.",
                                "details": {
                                    "failureFamily": patch_family,
                                    "error": str(patch_exc)[:500],
                                },
                            })
                            last_quality_exc = patch_exc
                        else:
                            # Patch repair passed the full gate; adopt the
                            # patched candidate without spending another
                            # full-generation attempt.
                            qa_result.code = patch_outcome.code
                            review = patch_outcome.review
                            quality = patch_outcome.quality
                            code_bytes = len(patch_outcome.code.encode("utf-8"))
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
                    repair_family = getattr(last_quality_exc, "failure_family", None)
                    if repair_family in {"repair_protocol", "repair_contract"}:
                        generation_guidance = self._append_repair_fallback_guidance(
                            generation_guidance,
                            last_quality_exc,
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
                            "failureFamily": repair_family or "quality_gate",
                            "qualityGateErrors": quality_gate_errors,
                            "reviewIssues": list(review.issues or [])[:10],
                            "reviewRan": review.ran,
                            "isCompleteGame": review.is_complete_game,
                            "hasRealGameplay": review.has_real_gameplay,
                            "repairFallbackReason": str(last_quality_exc)[:500] if repair_family in {"repair_protocol", "repair_contract"} else None,
                            "scores": {"fun": review.fun_score, "visual": review.visual_polish_score,
                                "character": review.character_quality_score, "final": quality.final_score},
                        },
                    )
                    await asyncio.sleep(min(quality_attempt, 2))
                    continue
                break
            except PipelineExecutionError as exc:
                last_quality_exc = exc
                last_route_snapshot = getattr(exc, "route_snapshot", None) or last_route_snapshot
                if getattr(exc, "failure_family", None) in {
                    "quality_repair_exhausted",
                    "provider_transport",
                    "route_configuration",
                    "review_evidence",
                    "review_actionability",
                    "review_infrastructure",
                } or exc.stage not in {"logic_generate", "contract_qa", "runtime_simulation_qa", "code_review"}:
                    raise
                if quality_attempt >= len(attempt_plan):
                    if (
                        exc.stage == "logic_generate"
                        and is_truncation_failure(exc)
                        and not extra_truncation_retry_granted
                        and len(attempt_plan) < DEFAULT_STAGE_TOTAL_ATTEMPTS
                    ):
                        attempt_plan.append("safe")
                        extra_truncation_retry_granted = True
                    else:
                        raise
                if is_truncation_failure(exc) and exc.stage == "logic_generate":
                    generation_guidance = self._build_truncation_compactness_guidance()
                else:
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
                        "failureFamily": getattr(exc, "failure_family", None),
                        "reason": str(exc)[:2000],
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

        remaining_gate_errors = self._quality_gate_errors(
            spec,
            review,
            quality,
            review_required=self._is_structured_review_required(spec),
        )
        outcome_labels = self._create_outcome_labels(
            review=review,
            qa_result=qa_result,
            qa_warnings=qa_warnings,
            quality_gate_errors=remaining_gate_errors,
        )
        try:
            _p2_emit(
                "create_outcome_labeled",
                tier=getattr(getattr(spec, "generation_tier", None), "value", None),
                game_type=getattr(spec, "game_type", None),
                pipeline_success=outcome_labels["pipeline_success"],
                seed_worthy=outcome_labels["seed_worthy"],
                seed_worthy_reason=outcome_labels["seed_worthy_reason"],
            )
        except Exception:  # noqa: BLE001 - telemetry must never crash runner
            pass

        stage_context["stage"] = "completed"
        self._notify(progress_cb, "completed", 100, "V2 pipeline completed", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
            "pipelineSuccess": outcome_labels["pipeline_success"],
            "seedWorthy": outcome_labels["seed_worthy"],
            "seedWorthyReason": outcome_labels["seed_worthy_reason"],
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
                "reviewRan": outcome_labels["review_ran"],
                "pipeline_success": outcome_labels["pipeline_success"],
                "seed_worthy": outcome_labels["seed_worthy"],
                "seed_worthy_reason": outcome_labels["seed_worthy_reason"],
            },
        )

    async def _run_iterate_impl(
        self,
        request: IterateV2Request,
        progress_cb: ProgressCallback,
        stage_context: dict[str, str],
    ) -> IterateResponse:
        if is_interactive_request(request):
            return await self._run_interactive_with_stage(request, progress_cb, stage_context)
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
            self._requested_runtime_profile(request.runtime_contract),
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

    async def _run_interactive_with_stage(self, request, progress_cb, stage_context):
        def progress(stage, percent, message, details=None):
            stage_context['stage'] = stage
            self._notify(progress_cb, stage, percent, message, details)
        return await run_interactive(normalize_interactive_request(request), progress)

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
                # Review uses the fast route; this legacy game-iteration
                # assessment remains non-blocking under its existing policy.
                review = await asyncio.wait_for(
                    self.code_reviewer.review(code, user_requirements=spec.source_description or ""),
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
    def _requested_runtime_profile(contract: GameRuntimeContract) -> Optional[str]:
        # Game-service contracts are automatically assembled defaults/hints,
        # including snapshots from older queued tasks. They are not user pins.
        # Only direct callers without that provenance can explicitly pin a profile.
        if (contract.metadata.get("source") == "game-service"
                or contract.metadata.get("profile_selection") == "auto"):
            return None
        return contract.runtime_profile

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
        states = list(runtime_contract.state.required_states)
        terminal = next((name for name in states if name in {"game_over", "level_complete"}), states[-1])
        gdd.state_machine = {
            "states": states,
            "initial": states[0],
            "transitions": {states[0]: states[1], states[1]: states[2],
                states[2]: terminal, terminal: states[1]},
        }
        if "paused" in states:
            gdd.state_machine["transitions"]["paused"] = "playing"
            gdd.state_machine["conditional_transitions"] = [
                {"from": "playing", "to": "paused", "on": "pause_or_blur"},
                {"from": "paused", "to": "playing", "on": "resume"},
            ]
        if "touch" in runtime_contract.input.required_modes:
            gdd.input_map.setdefault("touchstart", "primary_action")
            gdd.input_map.setdefault("touchmove", "primary_drag")
            gdd.input_map.setdefault("touchend", "primary_release")
        if "pointer" in runtime_contract.input.required_modes:
            gdd.input_map.setdefault("pointerdown", "primary_action")
            gdd.input_map["pointermove"] = "primary_move" if "move" in runtime_contract.input.gestures else "primary_drag"
            gdd.input_map.setdefault("pointerup", "primary_release")
        if "keyboard" in runtime_contract.input.required_modes:
            gdd.input_map["keydown"] = "primary_move"
            gdd.input_map["keyup"] = "primary_release"
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
                failure_family=getattr(exc, "failure_family", None) or ("provider_transport" if is_provider_transport_failure(exc) else "code_generation"),
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

    async def _resolve_create_review(
        self,
        review_state: Optional[dict[str, Any]],
        code: str,
        *,
        spec: GameSpec,
        user_requirements: str = "",
        review_requested: bool,
        qa_warnings: list[dict[str, Any]],
        progress_cb: ProgressCallback,
        game_id: str,
        user_id: str,
    ) -> LLMReviewResult:
        if not review_requested:
            return LLMReviewResult(ran=False)
        try:
            return await self._resolve_concurrent_review(
                review_state,
                code,
                user_requirements=user_requirements,
            )
        except PipelineExecutionError as exc:
            family = getattr(exc, "failure_family", None)
            if (
                family not in {"review_infrastructure", "review_actionability"}
                or self._is_structured_review_required(spec)
            ):
                raise
            degraded_type = (
                "review_actionability_degraded"
                if family == "review_actionability"
                else "review_infrastructure_degraded"
            )
            logger.warning(
                "Structured review %s for non-showcase create; degrading to static/runtime QA only: %s",
                "inconsistent" if family == "review_actionability" else "unavailable",
                exc,
            )
            qa_warnings.append({
                "type": degraded_type,
                "message": (
                    "Structured review stayed inconsistent after bounded reassessments; "
                    "proceeding with static and runtime QA only."
                    if family == "review_actionability"
                    else "Structured code review unavailable; proceeding with static and runtime QA only."
                ),
                "details": {
                    "failureFamily": family,
                    "stage": exc.stage,
                    "error": str(exc)[:500],
                },
            })
            self._notify(
                progress_cb,
                "code_review",
                96,
                "Structured review unavailable; using static and runtime QA only",
                {
                    "gameId": game_id,
                    "userId": user_id,
                    "failureFamily": family,
                    "degraded": True,
                },
            )
            return LLMReviewResult(ran=False)

    async def _resolve_concurrent_review(
        self,
        review_state: Optional[dict[str, Any]],
        code: str,
        *,
        user_requirements: str = "",
    ) -> LLMReviewResult:
        task = review_state.pop("task", None) if review_state else None
        if task is not None:
            if review_state.get("code") == code:
                return await task
            # The candidate changed after the review was launched; discard the
            # stale review and re-run against the final code.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return await self.code_reviewer.review(code, user_requirements=user_requirements)

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
