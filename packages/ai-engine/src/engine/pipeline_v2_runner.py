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
from .code_generator import CodeGenerator
from .code_reviewer import CodeReviewer
from .dialogue_engine import DialogueEngine, SlotExtractionFailure, _looks_like_educational_request
from .game_designer import GameDesigner
from .pipeline_orchestrator import PipelineExecutionError
from .pre_generation_validator import PreGenerationValidator
from .prompt_store import get_default_runtime_profile, require_prompt
from .qa_pipeline import QAPipeline
from .quality_scorer import LLMReviewResult, QAStaticResult, QualityScorer, RuntimeQAResult
from .restart_entry import has_restart_entry
from .runtime_qa import run_runtime_qa
from .terminal_state import has_required_state_presence, has_terminal_state_transition

logger = logging.getLogger(__name__)

DEFAULT_STAGE_TOTAL_ATTEMPTS = 3
ProgressCallback = Optional[Callable[[str, int, str, Optional[dict[str, Any]]], None]]

PROFILE_BY_GAME_TYPE: dict[str, str] = {
    "runner": "lane_runner",
    "endless runner": "lane_runner",
    "lane runner": "lane_runner",
    "racing": "lane_runner",
    "platformer": "lane_runner",
    "puzzle": "grid_puzzle",
    "grid puzzle": "grid_puzzle",
    "match3": "grid_puzzle",
    "merge": "grid_puzzle",
    "shooter": "topdown_action",
    "top down shooter": "topdown_action",
    "top-down shooter": "topdown_action",
    "dodge": "topdown_action",
    "rhythm": "tap_timing",
}

PROFILE_TO_GAME_TYPE_HINT: dict[str, str] = {
    "portrait_arcade": "runner",
    "lane_runner": "runner",
    "grid_puzzle": "puzzle",
    "topdown_action": "dodge",
    "tap_timing": "rhythm",
}


def _default_runtime_profile_id() -> str:
    profile = get_default_runtime_profile()
    if isinstance(profile, dict) and profile.get("id"):
        return str(profile["id"])
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
    "localStorage": r"\blocalStorage\b",
    "sessionStorage": r"\bsessionStorage\b",
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
        try:
            return await asyncio.wait_for(
                self._run_create_impl(request, progress_cb, stage_context),
                timeout=effective_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise PipelineExecutionError(
                f"Pipeline timed out during {stage_context.get('stage', 'failed')} after {effective_timeout_s}s",
                stage=stage_context.get("stage", "failed"),
            ) from exc

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
        try:
            return await asyncio.wait_for(
                self._run_iterate_impl(request, progress_cb, stage_context),
                timeout=effective_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise PipelineExecutionError(
                f"Iteration timed out during {stage_context.get('stage', 'failed')} after {effective_timeout_s}s",
                stage=stage_context.get("stage", "failed"),
            ) from exc

    async def _run_create_impl(
        self,
        request: RunPipelineV2Request,
        progress_cb: ProgressCallback,
        stage_context: dict[str, str],
    ) -> RunPipelineResponse:
        start_ms = int(time.time() * 1000)
        self._notify(progress_cb, "spec_build", 15, "Building structured game spec", {
            "gameId": request.game_id,
            "userId": request.user_id,
        })
        stage_context["stage"] = "spec_build"
        spec = await self._build_create_spec(request)

        self._notify(progress_cb, "runtime_profile_select", 30, "Selecting runtime profile", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "gameType": spec.game_type,
        })
        stage_context["stage"] = "runtime_profile_select"
        runtime_profile = self._select_runtime_profile(spec, request.runtime_contract.runtime_profile)

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
        gdd = await self._build_gdd(spec, runtime_contract)

        pre_issues = self.pre_gen_validator.validate(spec, gdd, runtime_contract)
        if pre_issues:
            logger.warning("Pre-generation issues detected: %s", pre_issues)
            spec, gdd = self.pre_gen_validator.auto_fix(spec, gdd, pre_issues)

        self._notify(progress_cb, "logic_generate", 60, "Generating runtime-bound game logic", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
        })
        stage_context["stage"] = "logic_generate"
        generated = await self._generate_create_code(request, spec, gdd, runtime_contract)

        qa_result, runtime_qa, runtime_retries, qa_warnings = await self._run_contract_and_runtime_flow(
            code=generated.html_code,
            spec=spec,
            runtime_contract=runtime_contract,
            prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
            progress_cb=progress_cb,
            game_id=request.game_id,
            user_id=request.user_id,
            stage_context=stage_context,
            allow_runtime_qa_unavailable=False,
        )

        if qa_result.needs_regeneration:
            logger.info("QA signaled regeneration needed; retrying code generation with complex budget")
            self._notify(progress_cb, "logic_generate", 65, "Regenerating with higher token budget", {
                "gameId": request.game_id,
                "userId": request.user_id,
                "runtimeProfile": runtime_profile,
            })
            stage_context["stage"] = "logic_generate"
            generated = await self._generate_create_code(
                request, spec, gdd, runtime_contract, budget_override="complex",
            )
            qa_result, runtime_qa, runtime_retries, qa_warnings = await self._run_contract_and_runtime_flow(
                code=generated.html_code,
                spec=spec,
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
                progress_cb=progress_cb,
                game_id=request.game_id,
                user_id=request.user_id,
                stage_context=stage_context,
                allow_runtime_qa_unavailable=False,
            )

        elapsed = int(time.time() * 1000) - start_ms
        code_bytes = len(qa_result.code.encode("utf-8"))
        final_check = self.qa_pipeline.check(qa_result.code)
        review = await self.code_reviewer.review(qa_result.code)
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
        self._notify(progress_cb, "spec_build", 15, "Compiling iteration spec", {
            "gameId": request.game_id,
            "userId": request.user_id,
        })
        stage_context["stage"] = "spec_build"
        spec = await self._build_iteration_spec(request)

        self._notify(progress_cb, "runtime_profile_select", 30, "Selecting runtime profile", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "gameType": spec.game_type,
        })
        stage_context["stage"] = "runtime_profile_select"
        runtime_profile = self._select_runtime_profile(spec, request.runtime_contract.runtime_profile)

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

        self._notify(progress_cb, "logic_generate", 60, "Applying spec-driven iteration", {
            "gameId": request.game_id,
            "userId": request.user_id,
            "runtimeProfile": runtime_profile,
        })
        stage_context["stage"] = "logic_generate"
        updated_code, iteration_type = await self._generate_iteration_code(request, spec, runtime_contract)

        qa_result, runtime_qa, runtime_retries, qa_warnings = await self._run_contract_and_runtime_flow(
            code=updated_code,
            spec=spec,
            runtime_contract=runtime_contract,
            prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
            progress_cb=progress_cb,
            game_id=request.game_id,
            user_id=request.user_id,
            stage_context=stage_context,
            allow_runtime_qa_unavailable=False,
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
        )

    async def _build_create_spec(self, request: RunPipelineV2Request) -> GameSpec:
        description = request.raw_user_input.strip() or str(
            request.normalized_request.get("description", "")
        ).strip()
        if not description:
            raise PipelineExecutionError("raw_user_input is required", stage="spec_build")

        return await self._parse_spec_with_retries(
            description=description,
            stage="spec_build",
            title=request.title,
            preferred_game_type=PROFILE_TO_GAME_TYPE_HINT.get(
                (request.runtime_contract.runtime_profile or "").strip(),
            ),
        )

    async def _build_iteration_spec(self, request: IterateV2Request) -> GameSpec:
        feedback = request.iteration_intent.feedback.strip()
        if not feedback:
            raise PipelineExecutionError("iteration_intent.feedback is required", stage="spec_build")

        base_spec = request.source_spec.model_copy(deep=True) if request.source_spec else None
        current_summary = self._summarize_current_code(request.current_code)
        conversation_text = " ".join(
            item.get("content", "")
            for item in request.iteration_intent.conversation[-3:]
            if isinstance(item, dict)
        ).strip()
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
            )
        except PipelineExecutionError as exc:
            if base_spec and self._should_fallback_iteration_spec(exc):
                return self._build_iteration_fallback_spec(
                    base_spec=base_spec,
                    feedback=feedback,
                    title=title,
                    source_bundle_context=request.source_bundle_context,
                )
            raise

        return self._merge_iteration_spec(
            base_spec=base_spec,
            parsed_spec=parsed_spec,
            feedback=feedback,
            title=title,
            source_bundle_context=request.source_bundle_context,
        )

    async def _parse_spec_with_retries(
        self,
        *,
        description: str,
        stage: str,
        title: Optional[str],
        preferred_game_type: Optional[str] = None,
    ) -> GameSpec:
        last_exc: Exception | None = None
        for attempt in range(1, DEFAULT_STAGE_TOTAL_ATTEMPTS + 1):
            try:
                spec = await self.dialogue_engine.parse_description_to_spec(
                    description,
                    allow_fallback=True,
                    title=title,
                    preferred_game_type=preferred_game_type,
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

    def _select_runtime_profile(self, spec: GameSpec, requested_profile: Optional[str]) -> str:
        requested = (requested_profile or "").strip()
        if requested:
            try:
                default_profile = _default_runtime_profile_id()
            except PipelineExecutionError:
                default_profile = requested
            if requested != default_profile:
                return requested
        if _looks_like_educational_request(
            spec.source_description,
            spec.intent_summary,
            " ".join(spec.special_rules or []),
        ):
            return "grid_puzzle"
        normalized = re.sub(r"[^a-z0-9]+", " ", (spec.game_type or "").lower()).strip()
        if normalized in PROFILE_BY_GAME_TYPE:
            return PROFILE_BY_GAME_TYPE[normalized]
        for token, profile in (
            ("runner", "lane_runner"),
            ("race", "lane_runner"),
            ("platform", "lane_runner"),
            ("puzzle", "grid_puzzle"),
            ("match", "grid_puzzle"),
            ("merge", "grid_puzzle"),
            ("shooter", "topdown_action"),
            ("top down", "topdown_action"),
            ("dodge", "topdown_action"),
            ("action", "topdown_action"),
            ("rhythm", "tap_timing"),
            ("timing", "tap_timing"),
        ):
            if token in normalized:
                return profile
        if requested:
            return requested
        return _default_runtime_profile_id()

    def _compose_runtime_contract(
        self,
        *,
        base_contract: GameRuntimeContract,
        spec: GameSpec,
        runtime_profile: str,
        entrypoint: str,
    ) -> GameRuntimeContract:
        contract = base_contract.model_copy(deep=True)
        contract.runtime_profile = runtime_profile
        contract.canvas = contract.canvas.model_copy(
            update={
                "requires_canvas_2d": True,
                "orientation": "portrait_first",
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
        }
        return contract

    def _profile_input_overrides(self, runtime_profile: str) -> dict[str, Any]:
        if runtime_profile == "grid_puzzle":
            return {
                "required_modes": ["touch"],
                "allow_mouse_fallback": True,
                "gestures": ["tap", "drag"],
            }
        if runtime_profile == "lane_runner":
            return {
                "required_modes": ["touch", "pointer"],
                "allow_mouse_fallback": True,
                "gestures": ["tap", "swipe"],
            }
        if runtime_profile == "tap_timing":
            return {
                "required_modes": ["touch", "pointer"],
                "allow_mouse_fallback": True,
                "gestures": ["tap"],
            }
        return {
            "required_modes": ["touch", "pointer"],
            "allow_mouse_fallback": True,
            "gestures": ["tap", "drag"],
        }

    def _profile_state_overrides(self, runtime_profile: str) -> dict[str, Any]:
        if runtime_profile == "grid_puzzle":
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
        if runtime_profile == "grid_puzzle":
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
                "primary_goal": "grid_completion",
            }
        if runtime_profile == "lane_runner":
            return {
                "requires_player_entity": True,
                "requires_scoring": True,
                "requires_terminal_state": True,
                "requires_restart_entry": True,
                "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
                "primary_goal": "lane_survival",
            }
        return {
            "requires_player_entity": True,
            "requires_scoring": True,
            "requires_terminal_state": True,
            "requires_restart_entry": True,
            "terminal_state_aliases": ["game_over", "over", "ended", "lost", "failed"],
            "primary_goal": "clear_feedback_loop",
        }

    async def _build_gdd(self, spec: GameSpec, runtime_contract: GameRuntimeContract) -> GDD:
        try:
            gdd = await self.game_designer.design(spec)
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
    ):
        last_exc: Exception | None = None
        for attempt in range(1, DEFAULT_STAGE_TOTAL_ATTEMPTS + 1):
            try:
                return await self.code_generator.generate(
                    spec=spec,
                    gdd=gdd,
                    description=request.raw_user_input,
                    allow_fallback=False,
                    runtime_contract=runtime_contract,
                    runtime_profile=runtime_contract.runtime_profile,
                    prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
                    budget_override=budget_override,
                )
            except Exception as exc:
                last_exc = exc
                if attempt == DEFAULT_STAGE_TOTAL_ATTEMPTS or not self._is_retryable_generation_error(exc):
                    break
                await asyncio.sleep(min(attempt, 2))

        raise PipelineExecutionError(
            f"Logic generation failed after {attempt} attempts: {last_exc}",
            stage="logic_generate",
            retry_count=max(0, attempt - 1),
        )

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
                    allow_fallback=False,
                    runtime_contract=runtime_contract,
                    runtime_profile=runtime_contract.runtime_profile,
                    prompt_bundle_snapshot=request.prompt_bundle_snapshot.model_dump(),
                    game_spec=spec,
                    source_bundle_context=request.source_bundle_context,
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
    ) -> tuple[QAResult, Any, int, list[dict[str, Any]]]:
        stage_context["stage"] = "contract_qa"
        self._notify(progress_cb, "contract_qa", 76, "Running contract QA", {
            "gameId": game_id,
            "userId": user_id,
            "runtimeProfile": runtime_contract.runtime_profile,
        })
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
        final_code, runtime_qa, runtime_retries, qa_warnings = await self._run_runtime_qa_loop(
            code=qa_result.code,
            spec=spec,
            runtime_contract=runtime_contract,
            prompt_bundle_snapshot=prompt_bundle_snapshot,
            progress_cb=progress_cb,
            game_id=game_id,
            user_id=user_id,
            allow_runtime_qa_unavailable=allow_runtime_qa_unavailable,
        )
        return QAResult(success=True, code=final_code, retries=qa_result.retries), runtime_qa, runtime_retries, qa_warnings

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
        retries_allowed = settings.QA_MAX_RETRIES if max_retries is None else max_retries
        current_code = self.qa_pipeline._apply_deterministic_repairs(code)
        repair_attempts = 0

        for attempt in range(retries_allowed + 1):
            errors = self._validate_contract_bundle(current_code, runtime_contract)
            if not errors:
                return QAResult(success=True, code=current_code, retries=repair_attempts)

            if attempt >= 1 and self.qa_pipeline._errors_look_like_truncation(errors):
                logger.warning("Contract QA: truncation persists after %d repair(s); signaling regeneration", repair_attempts)
                return QAResult(success=False, code=current_code, retries=repair_attempts, last_errors=errors, needs_regeneration=True)

            if attempt == retries_allowed:
                return QAResult(success=False, code=current_code, retries=repair_attempts, last_errors=errors)

            self._notify(progress_cb, "targeted_remediation", 84, f"Contract QA failed, applying targeted remediation ({attempt + 1}/{retries_allowed})", {
                "gameId": game_id,
                "userId": user_id,
                "retry": attempt + 1,
                "maxRetries": retries_allowed,
                "errorCount": len(errors),
                "errors": [error.message for error in errors[:3]],
            })
            current_code = await self.qa_pipeline.repair_code(
                current_code,
                errors,
                game_spec=spec,
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot=prompt_bundle_snapshot,
                fix_round=attempt + 1,
                max_fix_rounds=retries_allowed,
            )
            repair_attempts += 1

        return QAResult(success=False, code=current_code, retries=repair_attempts)

    async def _run_runtime_qa_loop(
        self,
        *,
        code: str,
        spec: GameSpec,
        runtime_contract: GameRuntimeContract,
        prompt_bundle_snapshot: Optional[dict[str, Any]],
        progress_cb: ProgressCallback,
        game_id: str,
        user_id: str,
        allow_runtime_qa_unavailable: bool = False,
    ) -> tuple[str, Any, int, list[dict[str, Any]]]:
        current_code = code
        remediation_attempts = max(0, int(settings.RUNTIME_QA_REMEDIATION_MAX_RETRIES or 1))
        total_retries = 0
        qa_warnings: list[dict[str, Any]] = []

        for attempt in range(remediation_attempts + 1):
            runtime_qa_timeout_s = self._resolve_runtime_qa_timeout(current_code, attempt=attempt)
            runtime_qa = await run_runtime_qa(current_code, timeout_s=runtime_qa_timeout_s)
            if not runtime_qa.ran:
                unavailable_reason = getattr(runtime_qa, "unavailable_reason", None)
                unavailable_kind = getattr(runtime_qa, "unavailable_kind", None)
                unavailable_phase = getattr(runtime_qa, "unavailable_phase", None)
                runtime_qa_report = self._serialize_runtime_qa(runtime_qa, [])
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
                    return current_code, runtime_qa, total_retries, qa_warnings
                if settings.RUNTIME_QA_REQUIRED or settings.ENVIRONMENT == "production":
                    raise PipelineExecutionError(
                        "Runtime QA unavailable: {}".format(
                            unavailable_reason or "Playwright is not installed or browser launch failed"
                        ),
                        stage="runtime_simulation_qa",
                        retry_count=total_retries,
                        failure_family="qa_infra_unavailable",
                        artifacts=[
                            self._build_text_artifact(
                                artifact_type="failed_runtime_candidate",
                                payload=current_code,
                                metadata={
                                    "stage": "runtime_simulation_qa",
                                    "retryCount": total_retries,
                                    "attempt": attempt,
                                    "timeoutS": runtime_qa_timeout_s,
                                    "unavailableReason": unavailable_reason,
                                },
                            ),
                            self._build_json_artifact(
                                artifact_type="runtime_qa_report",
                                payload={
                                    **runtime_qa_report,
                                    "retryCount": total_retries,
                                    "attempt": attempt,
                                    "timeoutS": runtime_qa_timeout_s,
                                },
                                metadata={"stage": "runtime_simulation_qa"},
                            ),
                        ],
                    )
                return current_code, runtime_qa, total_retries, qa_warnings

            errors = self._runtime_qa_errors(runtime_qa, current_code)
            if not errors:
                return current_code, runtime_qa, total_retries, qa_warnings

            if attempt == remediation_attempts:
                error_messages = "; ".join(error.message for error in errors[:5])
                raise PipelineExecutionError(
                    f"Generated code failed runtime QA: {error_messages}",
                    stage="runtime_simulation_qa",
                    retry_count=total_retries,
                    failure_family="runtime_qa",
                    artifacts=[
                        self._build_text_artifact(
                            artifact_type="failed_runtime_candidate",
                            payload=current_code,
                            metadata={
                                "stage": "runtime_simulation_qa",
                                "retryCount": total_retries,
                            },
                        ),
                        self._build_json_artifact(
                            artifact_type="runtime_qa_report",
                            payload=self._serialize_runtime_qa(runtime_qa, errors),
                            metadata={"stage": "runtime_simulation_qa"},
                        ),
                    ],
                )

            self._notify(progress_cb, "targeted_remediation", 95, f"Runtime QA failed, applying targeted remediation ({attempt + 1}/{remediation_attempts})", {
                "gameId": game_id,
                "userId": user_id,
                "errorCount": len(errors),
                "errors": [error.message for error in errors[:3]],
            })
            repaired_code = await self.qa_pipeline.repair_code(
                current_code,
                errors,
                game_spec=spec,
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot=prompt_bundle_snapshot,
                fix_round=attempt + 1,
                max_fix_rounds=remediation_attempts,
            )
            contract_qa = await self._run_contract_qa_loop(
                code=repaired_code,
                spec=spec,
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot=prompt_bundle_snapshot,
                progress_cb=progress_cb,
                game_id=game_id,
                user_id=user_id,
                max_retries=1,
            )
            if not contract_qa.success:
                error_messages = "; ".join(error.message for error in contract_qa.last_errors[:5])
                raise PipelineExecutionError(
                    f"Runtime remediation regressed contract QA: {error_messages}",
                    stage="contract_qa",
                    retry_count=total_retries + contract_qa.retries,
                    failure_family="contract_qa",
                    artifacts=[
                        self._build_text_artifact(
                            artifact_type="failed_runtime_candidate",
                            payload=contract_qa.code,
                            metadata={
                                "stage": "contract_qa",
                                "retryCount": total_retries + contract_qa.retries,
                                "reason": "runtime_remediation_regression",
                            },
                        ),
                        self._build_json_artifact(
                            artifact_type="contract_qa_report",
                            payload={
                                "passed": False,
                                "retryCount": contract_qa.retries,
                                "errors": self._serialize_errors(contract_qa.last_errors),
                                "regressedFromRuntimeRemediation": True,
                            },
                            metadata={"stage": "contract_qa"},
                        ),
                    ],
                )

            current_code = contract_qa.code
            total_retries += 1 + contract_qa.retries

        raise PipelineExecutionError(
            "Generated code failed runtime QA after targeted remediation",
            stage="runtime_simulation_qa",
            retry_count=total_retries,
        )

    def _resolve_runtime_qa_timeout(self, code: str, *, attempt: int) -> float:
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

        remediation_bonus = min(
            get_timeout_float("timeout.ai_engine.runtime_qa.remediation_bonus_max_s", 8.0, min_value=0.0),
            max(0, attempt)
            * get_timeout_float("timeout.ai_engine.runtime_qa.remediation_bonus_per_attempt_s", 4.0, min_value=0.0),
        )
        if code_size_bytes >= 32_000 and attempt > 0:
            remediation_bonus = max(
                remediation_bonus,
                get_timeout_float("timeout.ai_engine.runtime_qa.remediation_large_code_floor_s", 8.0, min_value=0.0),
            )
        return min(
            get_timeout_float("timeout.ai_engine.runtime_qa.max_s", 30.0, min_value=0.1),
            base_timeout + complexity_bonus + remediation_bonus,
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

        if runtime_contract.canvas.requires_canvas_2d and not re.search(
            r"getcontext\s*\(\s*['\"]2d['\"]\s*\)",
            code,
            re.IGNORECASE,
        ):
            errors.append(QACheckError(
                type="contract_canvas",
                message="Runtime contract requires an explicit Canvas 2D context",
                severity="error",
            ))

        if "viewport" not in lower:
            errors.append(QACheckError(
                type="contract_mobile",
                message="Runtime contract requires a mobile viewport meta tag",
                severity="error",
            ))

        if runtime_contract.mobile_layout.orientation == "portrait_first":
            has_portrait_guard = any(token in code for token in (
                "Math.min(scaleX, scaleY)",
                "shortEdge",
                "portrait",
                "uiScale",
            )) or bool(re.search(
                r"Math\.min\s*\([^)]*(?:scaleX|containerWidth|innerWidth|width)[^)]*,[^)]*(?:scaleY|containerHeight|innerHeight|height)[^)]*\)",
                code,
                re.IGNORECASE,
            ))
            if not has_portrait_guard:
                errors.append(QACheckError(
                    type="contract_mobile",
                    message="Runtime contract requires portrait-first short-edge UI scaling",
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

        has_visible_scoring_loop = bool(re.search(
            r"\b(score|points?|combo|multiplier|coins?)\b",
            lower,
            re.IGNORECASE,
        ))
        if not has_visible_scoring_loop:
            has_visible_scoring_loop = bool(re.search(
                r"(getelementbyid|queryselector)\s*\(\s*['\"#.]?(score|points?|combo|multiplier|coins?|time|timer)",
                code,
                re.IGNORECASE,
            ))
        if not has_visible_scoring_loop:
            has_visible_scoring_loop = bool(re.search(
                r"(score|points?|combo|multiplier|coins?|time|timer)\w*\.(textcontent|innertext|innerhtml)\s*=",
                lower,
                re.IGNORECASE,
            ))

        if runtime_contract.gameplay.requires_scoring and not has_visible_scoring_loop:
            errors.append(QACheckError(
                type="contract_gameplay",
                message="Runtime contract requires a visible scoring loop",
                severity="error",
            ))

        if runtime_contract.gameplay.requires_terminal_state and not has_terminal_state_transition(code, runtime_contract):
            errors.append(QACheckError(
                type="contract_gameplay",
                message="Runtime contract requires an explicit terminal or completion state",
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
    def _serialize_errors(errors: list[QACheckError]) -> list[dict[str, str]]:
        return [
            {
                "type": error.type,
                "message": error.message,
                "severity": error.severity,
            }
            for error in errors
        ]

    @staticmethod
    def _build_runtime_qa_warning(runtime_qa: Any) -> dict[str, Any]:
        unavailable_reason = getattr(runtime_qa, "unavailable_reason", None) or "runtime QA unavailable"
        return {
            "type": "runtime_qa_unavailable",
            "severity": "warning",
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
            f"latest_feedback={source_bundle_context.latest_feedback}" if source_bundle_context.latest_feedback else "",
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
                    revision.feedback or "",
                    revision.iteration_type or "",
                    revision.summary or "",
                ]
                if item
            )
            for revision in (source_bundle_context.recent_revisions or [])[:4]
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
            "temporarily unavailable",
            "rate limit",
            "connection reset",
            "socket",
            "network",
        )
        return any(marker in message for marker in retryable_markers)

    @staticmethod
    def _notify(
        progress_cb: ProgressCallback,
        stage: str,
        pct: int,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        if progress_cb:
            progress_cb(stage, pct, message, details or {})
