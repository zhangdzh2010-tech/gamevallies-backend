"""Pipeline Orchestrator: central controller for the game generation pipeline."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from ..api.models import (
    GDD,
    GameSpec,
    PipelineStage,
    QACheckError,
    QAResult,
    RunPipelineRequest,
    RunPipelineResponse,
    TemplateMatchResult,
)
from ..config.settings import settings
from .code_generator import CodeGenerator
from .code_reviewer import CodeReviewer
from .dialogue_engine import DialogueEngine
from .game_designer import GameDesigner
from .qa_pipeline import QAPipeline
from .quality_scorer import LLMReviewResult, QAStaticResult, QualityScorer, RuntimeQAResult
from .runtime_qa import run_runtime_qa
from .template_engine import TemplateEngine

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str, int, str], None]]


class PipelineOrchestrator:
    """Central controller: runs generation and QA for a single request."""

    def __init__(self) -> None:
        self.dialogue_engine = DialogueEngine()
        self.game_designer = GameDesigner()
        self.template_engine = TemplateEngine()
        self.code_generator = CodeGenerator(llm_mode=settings.LLM_MODE)
        self.qa_pipeline = QAPipeline()
        self.code_reviewer = CodeReviewer()
        self.quality_scorer = QualityScorer()

    async def run(
        self,
        request: RunPipelineRequest,
        progress_cb: ProgressCallback = None,
    ) -> RunPipelineResponse:
        try:
            return await asyncio.wait_for(
                self._run_stages(request, progress_cb),
                timeout=settings.PIPELINE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error(f"Pipeline timed out after {settings.PIPELINE_TIMEOUT_S}s")
            self._notify(progress_cb, PipelineStage.failed, -1, "Pipeline timeout")
            raise RuntimeError(f"Pipeline timed out after {settings.PIPELINE_TIMEOUT_S}s")

    async def _run_stages(
        self,
        request: RunPipelineRequest,
        progress_cb: ProgressCallback,
    ) -> RunPipelineResponse:
        start_ms = int(time.time() * 1000)

        from .dialogue_engine import _mock_parse

        game_spec = _mock_parse(request.description)
        gdd = await self._stage_design(game_spec)

        self._notify(progress_cb, PipelineStage.code_generating, 20, "Generating game code")
        match = TemplateMatchResult(path="llm")
        code_result = await self._stage_generate_code(
            game_spec,
            gdd,
            match,
            description=request.description,
        )

        self._notify(progress_cb, PipelineStage.qa_checking, 75, "Validating and repairing generated code")
        qa_result = await self._stage_qa(code_result.html_code, game_spec)
        if not qa_result.success:
            error_messages = self._format_errors(qa_result.last_errors)
            logger.error(f"Pipeline QA failed: {error_messages}")
            self._notify(progress_cb, PipelineStage.failed, 90, "AI did not produce a usable game version")
            raise RuntimeError(f"Generated code failed QA: {error_messages}")

        qa_result, runtime_qa = await self._repair_runtime_failures(qa_result, game_spec, progress_cb)
        qa_result, review_result = await self._run_code_review(qa_result, game_spec, progress_cb)

        self._notify(progress_cb, PipelineStage.completed, 100, "Generation completed")

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
            return qa_result, runtime_qa

        runtime_errors = [
            QACheckError(type="runtime_qa", message=f"Runtime JS error: {message}", severity="error")
            for message in runtime_qa.js_errors[:3]
        ]
        if not runtime_qa.canvas_renders:
            runtime_errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected that the canvas never rendered",
                severity="error",
            ))

        if not runtime_errors:
            return qa_result, runtime_qa

        logger.warning(f"Runtime QA found {len(runtime_errors)} issue(s), attempting repair")
        repaired_code = await self.qa_pipeline.repair_code(qa_result.code, runtime_errors, game_spec)
        repaired_result = await self.qa_pipeline.run_with_auto_fix(
            code=repaired_code,
            game_spec=game_spec,
            max_retries=1,
        )
        if not repaired_result.success:
            error_messages = self._format_errors(repaired_result.last_errors)
            self._notify(progress_cb, PipelineStage.failed, 90, "AI did not produce a usable game version")
            raise RuntimeError(f"Generated code failed QA after runtime repair: {error_messages}")

        runtime_qa = await run_runtime_qa(repaired_result.code)
        if runtime_qa.ran and (runtime_qa.js_errors or not runtime_qa.canvas_renders):
            runtime_messages = runtime_qa.js_errors[:3]
            if not runtime_qa.canvas_renders:
                runtime_messages.append("Canvas never rendered during runtime QA")
            self._notify(progress_cb, PipelineStage.failed, 90, "AI did not produce a usable game version")
            raise RuntimeError(f"Generated code failed runtime QA: {'; '.join(runtime_messages)}")

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
            self._notify(progress_cb, PipelineStage.qa_checking, 82, "Repairing clearly incomplete game output")
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
                self._notify(progress_cb, PipelineStage.failed, 90, "AI did not produce a usable game version")
                raise RuntimeError(f"Generated code failed QA after code review repair: {error_messages}")

            rerun_review = await self.code_reviewer.review(repaired_result.code)
            return repaired_result, rerun_review

        review_errors = self._review_errors(review_result)
        if review_errors:
            logger.info(
                "Code review found %s non-blocking issue(s); keeping evaluation-only result",
                len(review_errors),
            )
        return qa_result, review_result

    async def _stage_intent_parse(self, description: str) -> GameSpec:
        """Stage 02: description to GameSpec."""
        last_exc = None
        for attempt in range(3):
            try:
                spec = await self.dialogue_engine.parse_description_to_spec(description)
                logger.info(f"Intent parse succeeded on attempt {attempt}: game_type={spec.game_type}")
                return spec
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Intent parse attempt {attempt} failed: {exc}")
                await asyncio.sleep(0.5)

        logger.error(f"Intent parse failed after retries, using defaults: {last_exc}")
        from .dialogue_engine import _mock_parse

        return _mock_parse(description)

    async def _stage_design(self, spec: GameSpec) -> GDD:
        """Stage 03: GameSpec to GDD."""
        try:
            return await self.game_designer.design(spec)
        except Exception as exc:
            logger.warning(f"Game designer failed ({exc}), using defaults")
            return GDD()

    def _stage_match_template(self, spec: GameSpec) -> TemplateMatchResult:
        """Stage 04: template matching."""
        try:
            template_id, confidence = self.template_engine.match(spec)
            path = (
                "template" if confidence >= settings.TEMPLATE_CONFIDENCE_THRESHOLD
                else "hybrid" if confidence >= settings.HYBRID_CONFIDENCE_THRESHOLD
                else "llm"
            )
            logger.info(f"Template match: {template_id} (confidence={confidence:.2f}, path={path})")
            return TemplateMatchResult(
                template_id=template_id,
                confidence=confidence,
                path=path,
            )
        except Exception as exc:
            logger.warning(f"Template match failed ({exc}), using LLM path")
            return TemplateMatchResult(path="llm")

    async def _stage_generate_code(
        self,
        spec: GameSpec,
        gdd: GDD,
        match: TemplateMatchResult,
        description: str = "",
    ):
        """Stage 05: generate HTML code."""
        result = await self.code_generator.generate(
            spec=spec,
            gdd=gdd,
            template_id=match.template_id,
            confidence=match.confidence,
            description=description,
        )
        logger.info(f"Code generated: strategy={result.strategy}, size={result.code_size_bytes}B")
        return result

    async def _stage_qa(self, code: str, spec: GameSpec) -> QAResult:
        """Stage 06: QA with auto-fix."""
        try:
            return await self.qa_pipeline.run_with_auto_fix(
                code=code,
                game_spec=spec,
                max_retries=settings.QA_MAX_RETRIES,
            )
        except Exception as exc:
            logger.error(f"QA pipeline error: {exc}")
            return QAResult(success=False, code=code, retries=0)

    async def iterate(
        self,
        game_id: str,
        current_code: str,
        feedback: str,
        conversation: list,
        progress_cb: ProgressCallback = None,
    ) -> dict:
        """Stage 07: Incremental code modification."""
        self._notify(progress_cb, PipelineStage.code_generating, 30, "Analyzing requested changes")

        try:
            updated_code, iter_type = await self.code_generator.iterate(
                current_code=current_code,
                feedback=feedback,
                conversation=conversation,
            )
        except Exception as exc:
            logger.error(f"Iteration failed: {exc}")
            updated_code = current_code
            iter_type = None

        self._notify(progress_cb, PipelineStage.qa_checking, 70, "Validating iterated code")
        qa = await self.qa_pipeline.run_with_auto_fix(
            code=updated_code,
            max_retries=1,
        )

        self._notify(progress_cb, PipelineStage.completed, 100, "Iteration completed")
        return {
            "html_code": qa.code,
            "iteration_type": iter_type.value if iter_type else "element_change",
            "qa_passed": qa.success,
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
    ) -> None:
        if cb:
            try:
                cb(stage.value, pct, msg)
            except Exception:
                pass
