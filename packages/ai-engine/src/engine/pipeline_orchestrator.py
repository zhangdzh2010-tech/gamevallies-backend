"""Pipeline Orchestrator: central controller for the game generation pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Optional

import httpx

from ..api.models import (
    GDD,
    GameSpec,
    PipelineStage,
    QACheckError,
    QAResult,
    RunPipelineRequest,
    RunPipelineResponse,
)
from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from .code_generator import CodeGenerator
from .code_reviewer import CodeReviewer
from .dialogue_engine import DialogueEngine
from .game_designer import GameDesigner
from .qa_pipeline import QAPipeline
from .quality_scorer import LLMReviewResult, QAStaticResult, QualityScorer, RuntimeQAResult
from .runtime_qa import run_runtime_qa

logger = logging.getLogger(__name__)

DEFAULT_STAGE_TOTAL_ATTEMPTS = 3

ProgressCallback = Optional[Callable[[str, int, str, Optional[dict[str, Any]]], None]]


class PipelineExecutionError(RuntimeError):
    """Runtime error carrying stage and retry metadata."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        retry_count: int = 0,
        fallback: Optional[str] = None,
        failure_family: Optional[str] = None,
        artifacts: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.retry_count = retry_count
        self.fallback = fallback
        self.failure_family = failure_family
        self.artifacts = artifacts or []


class PipelineOrchestrator:
    """Central controller: runs generation and QA for a single request."""

    def __init__(self) -> None:
        self.dialogue_engine = DialogueEngine()
        self.game_designer = GameDesigner()
        self.code_generator = CodeGenerator(llm_mode=settings.LLM_MODE)
        self.qa_pipeline = QAPipeline()
        self.code_reviewer = CodeReviewer()
        self.quality_scorer = QualityScorer()

    def _runtime_qa_required(self) -> bool:
        return settings.RUNTIME_QA_REQUIRED or settings.ENVIRONMENT == "production"

    async def run(
        self,
        request: RunPipelineRequest,
        progress_cb: ProgressCallback = None,
        timeout_s: Optional[int] = None,
    ) -> RunPipelineResponse:
        stage_context: dict[str, str] = {"stage": PipelineStage.intent_parsing.value}
        effective_timeout_s = int(timeout_s) if timeout_s is not None else get_timeout_int(
            "timeout.pipeline.default_s",
            1200,
            min_value=30,
            max_value=3600,
        )
        try:
            return await asyncio.wait_for(
                self._run_stages(request, progress_cb, stage_context),
                timeout=effective_timeout_s,
            )
        except asyncio.TimeoutError:
            timed_out_stage = stage_context.get("stage", PipelineStage.failed.value)
            logger.error(f"Pipeline timed out during {timed_out_stage} after {effective_timeout_s}s")
            self._log_stage_failure(
                game_id=request.game_id,
                user_id=request.user_id,
                stage=PipelineStage.failed.value,
                error=f"Pipeline timed out during {timed_out_stage} after {effective_timeout_s}s",
            )
            self._notify(
                progress_cb,
                PipelineStage.failed,
                -1,
                "生成超时",
                {"gameId": request.game_id, "userId": request.user_id, "failedStage": timed_out_stage},
            )
            raise RuntimeError(f"Pipeline timed out during {timed_out_stage} after {effective_timeout_s}s")

    async def _run_stages(
        self,
        request: RunPipelineRequest,
        progress_cb: ProgressCallback,
        stage_context: Optional[dict[str, str]] = None,
    ) -> RunPipelineResponse:
        start_ms = int(time.time() * 1000)
        if stage_context is not None:
            stage_context["stage"] = PipelineStage.intent_parsing.value
        self._notify(
            progress_cb,
            PipelineStage.intent_parsing,
            15,
            "解析游戏意图",
            {"gameId": request.game_id, "userId": request.user_id, "attempt": 1, "maxAttempts": DEFAULT_STAGE_TOTAL_ATTEMPTS},
        )
        game_spec = await self._stage_intent_parse(
            request.description,
            request.game_id,
            request.user_id,
            progress_cb,
        )

        if stage_context is not None:
            stage_context["stage"] = PipelineStage.designing.value
        self._notify(
            progress_cb,
            PipelineStage.designing,
            30,
            "设计游戏参数",
            {"gameId": request.game_id, "userId": request.user_id},
        )
        gdd = await self._stage_design(
            game_spec,
            request.game_id,
            request.user_id,
            progress_cb,
        )

        if stage_context is not None:
            stage_context["stage"] = PipelineStage.code_generating.value
        self._notify(
            progress_cb,
            PipelineStage.code_generating,
            60,
            "生成游戏代码",
            {"gameId": request.game_id, "userId": request.user_id, "attempt": 1, "maxAttempts": DEFAULT_STAGE_TOTAL_ATTEMPTS},
        )
        code_result = await self._stage_generate_code(
            game_spec,
            gdd,
            request.game_id,
            request.user_id,
            progress_cb,
            description=request.description,
        )

        if stage_context is not None:
            stage_context["stage"] = PipelineStage.qa_checking.value
        self._notify(
            progress_cb,
            PipelineStage.qa_checking,
            75,
            "质量检查与自动修复",
            {"gameId": request.game_id, "userId": request.user_id, "attempt": 1, "maxAttempts": settings.QA_MAX_RETRIES + 1},
        )
        qa_result = await self._stage_qa(
            code_result.html_code,
            game_spec,
            request.game_id,
            request.user_id,
            progress_cb,
        )
        if not qa_result.success:
            error_messages = self._format_errors(qa_result.last_errors)
            logger.error(f"Pipeline QA failed: {error_messages}")
            self._log_stage_failure(
                game_id=request.game_id,
                user_id=request.user_id,
                stage=PipelineStage.qa_checking.value,
                error=error_messages,
                retry_count=qa_result.retries,
            )
            self._notify(
                progress_cb,
                PipelineStage.failed,
                90,
                "生成失败，未得到可用版本",
                {"gameId": request.game_id, "userId": request.user_id, "failedStage": PipelineStage.qa_checking.value, "retryCount": qa_result.retries},
            )
            raise PipelineExecutionError(
                f"Generated code failed QA: {error_messages}",
                stage=PipelineStage.qa_checking.value,
                retry_count=qa_result.retries,
            )

        qa_result, runtime_qa = await self._repair_runtime_failures(qa_result, game_spec, progress_cb)
        qa_result, review_result = await self._run_code_review(qa_result, game_spec, progress_cb)

        if stage_context is not None:
            stage_context["stage"] = PipelineStage.completed.value
        self._notify(
            progress_cb,
            PipelineStage.completed,
            100,
            "生成完成",
            {"gameId": request.game_id, "userId": request.user_id},
        )

        elapsed = int(time.time() * 1000) - start_ms
        code_bytes = len(qa_result.code.encode("utf-8"))
        quality = self._compute_quality(
            qa_result=qa_result,
            strategy=code_result.strategy,
            code_size_bytes=code_bytes,
            runtime_qa=runtime_qa,
            review_result=review_result,
        )
        return RunPipelineResponse(
            game_id=request.game_id,
            html_code=qa_result.code,
            game_spec=game_spec,
            strategy=code_result.strategy,
            qa_passed=qa_result.success,
            qa_retries=qa_result.retries,
            generation_time_ms=elapsed,
            code_size_bytes=code_bytes,
            quality_score=quality.final_score,
            quality_breakdown=quality.details | {
                "qa_penalty": quality.qa_penalty,
                "strategy_bonus": quality.strategy_bonus,
                "size_bonus": quality.size_bonus,
                "retry_penalty": quality.retry_penalty,
                "runtime_bonus": quality.runtime_bonus,
                "review_bonus": quality.review_bonus,
            },
        )

    async def _repair_runtime_failures(
        self,
        qa_result: QAResult,
        game_spec: GameSpec,
        progress_cb: ProgressCallback,
    ) -> tuple[QAResult, RuntimeQAResult]:
        runtime_qa = await run_runtime_qa(qa_result.code)
        if not runtime_qa.ran:
            if self._runtime_qa_required():
                self._notify(
                    progress_cb,
                    PipelineStage.failed,
                    90,
                    "运行时检查不可用，已中止发布",
                    {"failedStage": "runtime_qa_unavailable", "retryCount": qa_result.retries},
                )
                raise PipelineExecutionError(
                    "Runtime QA unavailable: Playwright is not installed or browser launch failed",
                    stage=PipelineStage.qa_checking.value,
                    retry_count=qa_result.retries,
                )
            return qa_result, runtime_qa

        runtime_errors = [
            QACheckError(type="runtime_qa", message=f"Runtime JS error: {message}", severity="error")
            for message in runtime_qa.js_errors[:3]
        ]
        runtime_input_signals = sorted(
            set(runtime_qa.registered_input_handlers) | set(runtime_qa.direct_input_handlers)
        )
        if not runtime_qa.canvas_renders:
            runtime_errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected that the canvas never rendered",
                severity="error",
            ))
        if not runtime_input_signals:
            runtime_errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected no registered user input handlers",
                severity="error",
            ))

        if not runtime_errors:
            return qa_result, runtime_qa

        logger.warning(f"Runtime QA found {len(runtime_errors)} issue(s), attempting repair")
        self._notify(
            progress_cb,
            PipelineStage.qa_checking,
            84,
            "运行时检查失败，正在修复（1/1）",
            {"retry": 1, "maxRetries": 1, "errorCount": len(runtime_errors)},
        )
        repaired_code = await self.qa_pipeline.repair_code(qa_result.code, runtime_errors, game_spec)
        repaired_result = await self.qa_pipeline.run_with_auto_fix(
            code=repaired_code,
            game_spec=game_spec,
            max_retries=1,
        )
        if not repaired_result.success:
            error_messages = self._format_errors(repaired_result.last_errors)
            self._notify(
                progress_cb,
                PipelineStage.failed,
                90,
                "生成失败，未得到可用版本",
                {"failedStage": "runtime_qa", "retryCount": repaired_result.retries},
            )
            raise PipelineExecutionError(
                f"Generated code failed QA after runtime repair: {error_messages}",
                stage=PipelineStage.qa_checking.value,
                retry_count=repaired_result.retries,
            )

        runtime_qa = await run_runtime_qa(repaired_result.code)
        runtime_input_signals = sorted(
            set(runtime_qa.registered_input_handlers) | set(runtime_qa.direct_input_handlers)
        )
        if runtime_qa.ran and (runtime_qa.js_errors or not runtime_qa.canvas_renders or not runtime_input_signals):
            runtime_messages = runtime_qa.js_errors[:3]
            if not runtime_qa.canvas_renders:
                runtime_messages.append("Canvas never rendered during runtime QA")
            if not runtime_input_signals:
                runtime_messages.append("No registered input handlers detected during runtime QA")
            self._notify(
                progress_cb,
                PipelineStage.failed,
                90,
                "生成失败，未得到可用版本",
                {"failedStage": "runtime_qa", "retryCount": repaired_result.retries},
            )
            raise PipelineExecutionError(
                f"Generated code failed runtime QA: {'; '.join(runtime_messages)}",
                stage=PipelineStage.qa_checking.value,
                retry_count=repaired_result.retries,
            )

        return repaired_result, runtime_qa

    async def _run_code_review(
        self,
        qa_result: QAResult,
        game_spec: GameSpec,
        progress_cb: ProgressCallback,
    ) -> tuple[QAResult, LLMReviewResult]:
        review_result = await self.code_reviewer.review(qa_result.code)
        if not review_result.ran:
            return qa_result, review_result

        repair_errors = self._review_repair_errors(review_result)
        if repair_errors:
            logger.warning(
                "Code review found %s clearly incomplete issue(s), triggering one targeted repair pass",
                len(repair_errors),
            )
            self._notify(progress_cb, PipelineStage.qa_checking, 82, "正在修复明显不完整的输出")
            repaired_code = await self.qa_pipeline.repair_code(
                qa_result.code,
                repair_errors,
                game_spec,
                max_tokens=settings.REVIEW_REPAIR_MAX_TOKENS,
            )
            repaired_result = await self.qa_pipeline.run_with_auto_fix(
                code=repaired_code,
                game_spec=game_spec,
                max_retries=settings.REVIEW_REPAIR_MAX_RETRIES,
            )
            if not repaired_result.success:
                error_messages = self._format_errors(repaired_result.last_errors)
                self._notify(
                    progress_cb,
                    PipelineStage.failed,
                    90,
                    "生成失败，未得到可用版本",
                    {"failedStage": "code_review", "retryCount": repaired_result.retries},
                )
                raise PipelineExecutionError(
                    f"Generated code failed QA after code review repair: {error_messages}",
                    stage=PipelineStage.qa_checking.value,
                    retry_count=repaired_result.retries,
                )

            rerun_review = await self.code_reviewer.review(repaired_result.code)
            return repaired_result, rerun_review

        review_errors = self._review_errors(review_result)
        if review_errors:
            logger.info(
                "Code review found %s non-blocking issue(s); keeping evaluation-only result",
                len(review_errors),
            )
        return qa_result, review_result

    async def _stage_intent_parse(
        self,
        description: str,
        game_id: str,
        user_id: str,
        progress_cb: ProgressCallback,
    ) -> GameSpec:
        """Stage 02: description to GameSpec."""
        last_exc = None
        max_attempts = DEFAULT_STAGE_TOTAL_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            try:
                spec = await self.dialogue_engine.parse_description_to_spec(
                    description,
                    allow_fallback=True,
                    variation_seed=game_id,
                )
                logger.info(f"Intent parse succeeded on attempt {attempt}: game_type={spec.game_type}")
                return spec
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Intent parse attempt {attempt} failed: {exc}")
                if attempt < max_attempts:
                    self._notify(
                        progress_cb,
                        PipelineStage.intent_parsing,
                        15,
                        f"意图解析失败，重试中（{attempt}/{max_attempts - 1}）",
                        self._retry_details(game_id, user_id, attempt, max_attempts, exc),
                    )
                await asyncio.sleep(0.5)

        logger.error(f"Intent parse failed after retries: {last_exc}")
        self._log_stage_failure(
            game_id=game_id,
            user_id=user_id,
            stage=PipelineStage.intent_parsing.value,
            error=self._error_message(last_exc),
            retry_count=max_attempts - 1,
        )
        self._notify(
            progress_cb,
            PipelineStage.intent_parsing,
            18,
            "意图解析失败，生成已终止",
            {"gameId": game_id, "userId": user_id, "failedStage": PipelineStage.intent_parsing.value},
        )
        raise PipelineExecutionError(
            f"Intent parsing failed after {max_attempts} attempts: {self._error_message(last_exc)}",
            stage=PipelineStage.intent_parsing.value,
            retry_count=max_attempts - 1,
            failure_family="spec_build",
            artifacts=getattr(last_exc, "artifacts", None),
        )

    async def _stage_design(
        self,
        spec: GameSpec,
        game_id: str,
        user_id: str,
        progress_cb: ProgressCallback,
    ) -> GDD:
        """Stage 03: GameSpec to GDD."""
        max_attempts = DEFAULT_STAGE_TOTAL_ATTEMPTS
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                return await self.game_designer.design(spec)
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Game designer attempt {attempt} failed ({exc})")
                if attempt < max_attempts:
                    self._notify(
                        progress_cb,
                        PipelineStage.designing,
                        30,
                        f"游戏数值设计失败，重试中（{attempt}/{max_attempts - 1}）",
                        self._retry_details(game_id, user_id, attempt, max_attempts, exc),
                    )
                    await asyncio.sleep(0.5)

        self._log_stage_failure(
            game_id=game_id,
            user_id=user_id,
            stage=PipelineStage.designing.value,
            error=self._error_message(last_exc),
            retry_count=max_attempts - 1,
        )
        self._notify(
            progress_cb,
            PipelineStage.designing,
            33,
            "Game design failed, generation stopped",
            {"gameId": game_id, "userId": user_id, "failedStage": PipelineStage.designing.value},
        )
        raise PipelineExecutionError(
            f"Game design failed after {max_attempts} attempts: {self._error_message(last_exc)}",
            stage=PipelineStage.designing.value,
            retry_count=max_attempts - 1,
        )

    async def _stage_generate_code(
        self,
        spec: GameSpec,
        gdd: GDD,
        game_id: str,
        user_id: str,
        progress_cb: ProgressCallback,
        description: str = "",
    ):
        """Stage 05: generate HTML code."""
        max_attempts = DEFAULT_STAGE_TOTAL_ATTEMPTS
        last_exc: Exception | None = None
        attempts_used = 0

        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            try:
                result = await self.code_generator.generate(
                    spec=spec,
                    gdd=gdd,
                    description=description,
                )
                logger.info(f"Code generated: strategy={result.strategy}, size={result.code_size_bytes}B")
                return result
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Code generation attempt {attempt} failed: {exc}")
                if attempt < max_attempts and self._is_retryable_generation_error(exc):
                    self._notify(
                        progress_cb,
                        PipelineStage.code_generating,
                        60,
                        f"代码生成失败，重试中（{attempt}/{max_attempts - 1}）",
                        self._retry_details(game_id, user_id, attempt, max_attempts, exc),
                    )
                    await asyncio.sleep(min(attempt, 2))
                    continue
                break

        self._log_stage_failure(
            game_id=game_id,
            user_id=user_id,
            stage=PipelineStage.code_generating.value,
            error=self._error_message(last_exc),
            retry_count=max(0, attempts_used - 1),
        )
        raise PipelineExecutionError(
            f"Code generation failed after {attempts_used} attempts: {self._error_message(last_exc)}",
            stage=PipelineStage.code_generating.value,
            retry_count=max(0, attempts_used - 1),
        )

    async def _stage_qa(
        self,
        code: str,
        spec: GameSpec,
        game_id: str,
        user_id: str,
        progress_cb: ProgressCallback,
    ) -> QAResult:
        """Stage 06: QA with auto-fix."""
        try:
            def on_retry(retry_index: int, max_retries: int, errors: list[QACheckError]) -> None:
                self._notify(
                    progress_cb,
                    PipelineStage.qa_checking,
                    78,
                    f"质量检查未通过，正在自动修复（{retry_index}/{max_retries}）",
                    {
                        "gameId": game_id,
                        "userId": user_id,
                        "retry": retry_index,
                        "maxRetries": max_retries,
                        "errorCount": len(errors),
                        "errors": [error.message for error in errors[:3]],
                    },
                )

            return await self.qa_pipeline.run_with_auto_fix(
                code=code,
                game_spec=spec,
                max_retries=settings.QA_MAX_RETRIES,
                retry_cb=on_retry,
            )
        except Exception as exc:
            logger.error(f"QA pipeline error: {exc}")
            self._log_stage_failure(
                game_id=game_id,
                user_id=user_id,
                stage=PipelineStage.qa_checking.value,
                error=self._error_message(exc),
            )
            return QAResult(
                success=False,
                code=code,
                retries=0,
                last_errors=[
                    QACheckError(
                        type="qa_pipeline",
                        message=self._error_message(exc),
                        severity="error",
                    )
                ],
            )

    async def iterate(
        self,
        game_id: str,
        current_code: str,
        feedback: str,
        conversation: list,
        user_id: str = "system",
        progress_cb: ProgressCallback = None,
        timeout_s: Optional[int] = None,
    ) -> dict:
        effective_timeout_s = int(timeout_s) if timeout_s is not None else get_timeout_int(
            "timeout.pipeline.default_s",
            1200,
            min_value=30,
            max_value=3600,
        )
        stage_context: dict[str, str] = {"stage": PipelineStage.code_generating.value}
        try:
            return await asyncio.wait_for(
                self._iterate_impl(
                    game_id=game_id,
                    current_code=current_code,
                    feedback=feedback,
                    conversation=conversation,
                    user_id=user_id,
                    progress_cb=progress_cb,
                    stage_context=stage_context,
                ),
                timeout=effective_timeout_s,
            )
        except asyncio.TimeoutError as exc:
            timed_out_stage = stage_context.get("stage", PipelineStage.failed.value)
            raise PipelineExecutionError(
                f"Iteration timed out during {timed_out_stage} after {effective_timeout_s}s",
                stage=timed_out_stage,
            ) from exc

    async def _iterate_impl(
        self,
        game_id: str,
        current_code: str,
        feedback: str,
        conversation: list,
        user_id: str = "system",
        progress_cb: ProgressCallback = None,
        stage_context: Optional[dict[str, str]] = None,
    ) -> dict:
        """Stage 07: Incremental code modification."""
        max_attempts = DEFAULT_STAGE_TOTAL_ATTEMPTS
        if stage_context is not None:
            stage_context["stage"] = PipelineStage.code_generating.value
        self._notify(
            progress_cb,
            PipelineStage.code_generating,
            30,
            "分析修改意图",
            {"gameId": game_id, "userId": user_id, "attempt": 1, "maxAttempts": max_attempts},
        )

        last_exc: Exception | None = None
        updated_code = current_code
        iter_type = None

        for attempt in range(1, max_attempts + 1):
            try:
                updated_code, iter_type = await self.code_generator.iterate(
                    current_code=current_code,
                    feedback=feedback,
                    conversation=conversation,
                )
                break
            except Exception as exc:
                last_exc = exc
                logger.error(f"Iteration attempt {attempt} failed: {exc}")
                if attempt < max_attempts and self._is_retryable_generation_error(exc):
                    self._notify(
                        progress_cb,
                        PipelineStage.code_generating,
                        40,
                        f"代码修改失败，正在重试（{attempt}/{max_attempts - 1}）",
                        self._retry_details(game_id, user_id, attempt, max_attempts, exc),
                    )
                    await asyncio.sleep(min(attempt, 2))
                    continue

                self._log_stage_failure(
                    game_id=game_id,
                    user_id=user_id,
                    stage=PipelineStage.code_generating.value,
                    error=self._error_message(last_exc),
                    retry_count=max_attempts - 1,
                )
                raise PipelineExecutionError(
                    f"Iteration failed after {attempt} attempts: {self._error_message(last_exc)}",
                    stage=PipelineStage.code_generating.value,
                    retry_count=max(0, attempt - 1),
                )

        self._notify(
            progress_cb,
            PipelineStage.qa_checking,
            70,
            "质量检查与自动修复",
            {"gameId": game_id, "userId": user_id, "attempt": 1, "maxAttempts": settings.QA_MAX_RETRIES + 1},
        )
        if stage_context is not None:
            stage_context["stage"] = PipelineStage.qa_checking.value

        def on_retry(retry_index: int, max_retries: int, errors: list[QACheckError]) -> None:
            self._notify(
                progress_cb,
                PipelineStage.qa_checking,
                78,
                f"修改后的质量检查未通过，正在修复（{retry_index}/{max_retries}）",
                {
                    "gameId": game_id,
                    "userId": user_id,
                    "retry": retry_index,
                    "maxRetries": max_retries,
                    "errorCount": len(errors),
                    "errors": [error.message for error in errors[:3]],
                },
            )

        qa = await self.qa_pipeline.run_with_auto_fix(
            code=updated_code,
            max_retries=settings.QA_MAX_RETRIES,
            retry_cb=on_retry,
        )

        if not qa.success:
            error_messages = self._format_errors(qa.last_errors)
            self._log_stage_failure(
                game_id=game_id,
                user_id=user_id,
                stage=PipelineStage.qa_checking.value,
                error=error_messages,
                retry_count=qa.retries,
            )
            raise PipelineExecutionError(
                f"Iterated code failed QA: {error_messages}",
                stage=PipelineStage.qa_checking.value,
                retry_count=qa.retries,
            )

        self._notify(progress_cb, PipelineStage.completed, 100, "迭代完成")
        if stage_context is not None:
            stage_context["stage"] = PipelineStage.completed.value
        return {
            "html_code": qa.code,
            "iteration_type": iter_type.value if iter_type else "element_change",
            "qa_passed": qa.success,
            "qa_retries": qa.retries,
            "iteration_retries": max(0, attempt - 1),
        }

    def _compute_quality(
        self,
        qa_result: QAResult,
        strategy: str,
        code_size_bytes: int,
        runtime_qa: RuntimeQAResult,
        review_result: LLMReviewResult,
    ):
        final_check = self.qa_pipeline.check(qa_result.code)
        static = QAStaticResult(
            passed=final_check.passed,
            error_count=len(final_check.errors),
            warning_count=len(final_check.warnings),
            retries=qa_result.retries,
            strategy=strategy,
            code_size_bytes=code_size_bytes,
        )
        return self.quality_scorer.compute(static=static, runtime=runtime_qa, review=review_result)

    @staticmethod
    def _review_errors(review_result: LLMReviewResult) -> list[QACheckError]:
        errors: list[QACheckError] = []
        if not review_result.is_complete_game:
            errors.append(QACheckError(
                type="code_review",
                message="Code review found that the output is not a complete playable game",
                severity="error",
            ))
        if not review_result.has_real_gameplay:
            errors.append(QACheckError(
                type="code_review",
                message="Code review found that the output lacks real gameplay mechanics",
                severity="error",
            ))
        for issue in review_result.issues[:5]:
            errors.append(QACheckError(
                type="code_review",
                message=f"Code review issue: {issue}",
                severity="error",
            ))
        return errors

    @classmethod
    def _review_repair_errors(cls, review_result: LLMReviewResult) -> list[QACheckError]:
        errors: list[QACheckError] = []
        if not review_result.is_complete_game:
            errors.append(QACheckError(
                type="code_review",
                message="Code review found that the output is not a complete playable game",
                severity="error",
            ))
            return errors

        if not review_result.has_real_gameplay and cls._review_issues_look_incomplete(review_result.issues):
            errors.append(QACheckError(
                type="code_review",
                message="Code review found that the output still looks incomplete and lacks real gameplay",
                severity="error",
            ))

        return errors

    @staticmethod
    def _review_issues_look_incomplete(issues: list[str]) -> bool:
        incomplete_markers = (
            "stub",
            "demo",
            "placeholder",
            "incomplete",
            "unfinished",
            "truncated",
            "missing",
            "not interactive",
            "no gameplay",
            "lacks gameplay",
            "not playable",
            "todo",
        )
        return any(
            marker in issue.lower()
            for issue in issues
            for marker in incomplete_markers
        )

    @staticmethod
    def _format_errors(errors: list[QACheckError]) -> str:
        return "; ".join(error.message for error in errors[:3]) or "QA validation failed"

    @staticmethod
    def _notify(
        cb: ProgressCallback,
        stage: PipelineStage,
        pct: int,
        msg: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        if cb:
            try:
                cb(stage.value, pct, msg, details)
            except Exception:
                pass

    @staticmethod
    def _error_message(error: Optional[Exception | BaseException | str]) -> str:
        if error is None:
            return "unknown error"
        if isinstance(error, str):
            return error
        return str(error)

    @classmethod
    def _retry_details(
        cls,
        game_id: str,
        user_id: str,
        retry_index: int,
        max_attempts: int,
        error: Optional[Exception | BaseException | str],
    ) -> dict[str, Any]:
        return {
            "gameId": game_id,
            "userId": user_id,
            "retry": retry_index,
            "maxRetries": max_attempts - 1,
            "attempt": retry_index + 1,
            "maxAttempts": max_attempts,
            "error": cls._error_message(error),
        }

    @staticmethod
    def _is_retryable_generation_error(error: Exception | BaseException | str | None) -> bool:
        if error is None:
            return False

        if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
            return True

        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code if error.response is not None else None
            return status_code == 429 or bool(status_code and status_code >= 500)

        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None) or getattr(response, "status", None)
        if isinstance(status_code, int):
            return status_code == 429 or status_code >= 500

        error_code = str(getattr(error, "code", "") or "").upper()
        if error_code in {"ECONNRESET", "ECONNREFUSED", "EHOSTUNREACH", "ENETUNREACH", "ETIMEDOUT"}:
            return True

        message = str(error).lower()
        retryable_markers = (
            "temporary",
            "transient",
            "timeout",
            "timed out",
            "temporarily unavailable",
            "service unavailable",
            "connection reset",
            "socket hang up",
            "rate limit",
            "too many requests",
        )
        return any(marker in message for marker in retryable_markers)

    def _log_stage_failure(
        self,
        *,
        game_id: str,
        user_id: str,
        stage: str,
        error: str,
        retry_count: int = 0,
        fallback: Optional[str] = None,
    ) -> None:
        payload = {
            "game_id": game_id,
            "user_id": user_id,
            "stage": stage,
            "retry_count": retry_count,
            "error": error,
        }
        if fallback:
            payload["fallback"] = fallback
        logger.error("PIPELINE_STAGE_FAILURE %s", json.dumps(payload, ensure_ascii=False))
