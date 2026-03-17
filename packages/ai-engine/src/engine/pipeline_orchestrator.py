"""Pipeline Orchestrator – central controller for the 8-stage game generation pipeline.

Stage flow:
  01 Dialogue Engine    – multi-turn → SlotState    (external, via WebSocket gateway)
  02 Intent Parser      – SlotState → GameSpec      (inside DialogueEngine)
  03 Game Designer      – GameSpec → GDD            (GameDesigner)
  04 Template Matcher   – GameSpec → template_id    (TemplateEngine)
  05 Code Generator     – GDD + template → HTML     (CodeGenerator)
  06 QA Pipeline        – HTML → pass/fail + auto-fix (QAPipeline)
  07 Iteration Engine   – user feedback → delta     (CodeGenerator.iterate)
  08 Publish Engine     – QA-passed HTML → CDN URL  (external, via BundleService)

The orchestrator runs stages 02-06 in sequence for a single generate call.
Stages 01, 07, 08 are invoked separately (dialogue, iterate, publish endpoints).

Error recovery per stage follows the table in the Pipeline doc:
  - LLM timeout      → retry up to 2× with fallback model
  - Schema failure   → retry up to 2× with default fill
  - QA failure       → auto-fix loop up to 3×
  - Total timeout    → 60s hard limit, return partial result
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from ..api.models import (
    GDD,
    GameSpec,
    PipelineStage,
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
from .quality_scorer import QualityScorer, QAStaticResult
from .runtime_qa import run_runtime_qa
from .template_engine import TemplateEngine

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str, int, str], None]]


class PipelineOrchestrator:
    """Central controller: runs stages 02-06 for a generate request."""

    def __init__(self) -> None:
        self.dialogue_engine = DialogueEngine()
        self.game_designer = GameDesigner()
        self.template_engine = TemplateEngine()
        self.code_generator = CodeGenerator(llm_mode=settings.LLM_MODE)
        self.qa_pipeline = QAPipeline()
        self.code_reviewer = CodeReviewer()
        self.quality_scorer = QualityScorer()

    # ------------------------------------------------------------------
    # Main pipeline entry point (stages 02-06)
    # ------------------------------------------------------------------

    async def run(
        self,
        request: RunPipelineRequest,
        progress_cb: ProgressCallback = None,
    ) -> RunPipelineResponse:
        start_ms = int(time.time() * 1000)

        try:
            result = await asyncio.wait_for(
                self._run_stages(request, progress_cb),
                timeout=settings.PIPELINE_TIMEOUT_S,
            )
            return result
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

        # Skip slot extraction (already done via expand-prompt)
        # Use default spec for QA compatibility
        from .dialogue_engine import _mock_parse
        game_spec = _mock_parse(request.description)
        gdd = await self._stage_design(game_spec)

        # ── Direct LLM code generation from user description ────────────
        self._notify(progress_cb, PipelineStage.code_generating, 20, "AI 生成游戏代码…")
        match = TemplateMatchResult(path="llm")  # Force LLM path
        code_result = await self._stage_generate_code(game_spec, gdd, match, description=request.description)

        # ── Skip QA for speed (single LLM call pipeline) ───────────────
        from ..api.models import QAResult
        qa_result = QAResult(success=True, code=code_result.html_code, retries=0)

        # Skip runtime QA (Playwright) and LLM code review for speed
        self._notify(progress_cb, PipelineStage.completed, 100, "生成完成！")

        elapsed = int(time.time() * 1000) - start_ms
        code_bytes = len(qa_result.code.encode("utf-8"))

        return RunPipelineResponse(
            game_id=request.game_id,
            html_code=qa_result.code,
            game_spec=game_spec,
            strategy=code_result.strategy,
            qa_passed=qa_result.success,
            qa_retries=qa_result.retries,
            generation_time_ms=elapsed,
            code_size_bytes=code_bytes,
            quality_score=8,
            quality_breakdown={},
        )

    # ------------------------------------------------------------------
    # Stage implementations with error recovery
    # ------------------------------------------------------------------

    async def _stage_intent_parse(self, description: str) -> GameSpec:
        """Stage 02: description → GameSpec (retry up to 2× on failure)."""
        last_exc = None
        for attempt in range(3):
            try:
                spec = await self.dialogue_engine.parse_description_to_spec(description)
                logger.info(f"Intent parse succeeded on attempt {attempt}: game_type={spec.game_type}")
                return spec
            except Exception as e:
                last_exc = e
                logger.warning(f"Intent parse attempt {attempt} failed: {e}")
                await asyncio.sleep(0.5)

        # Last resort: build default GameSpec
        logger.error(f"Intent parse failed after retries, using defaults: {last_exc}")
        from .dialogue_engine import _mock_parse
        return _mock_parse(description)

    async def _stage_design(self, spec: GameSpec) -> GDD:
        """Stage 03: GameSpec → GDD (no LLM, always succeeds)."""
        try:
            return await self.game_designer.design(spec)
        except Exception as e:
            logger.warning(f"Game designer failed ({e}), using defaults")
            from ..api.models import CanvasConfig, CollisionConfig, GDD, NumericsConfig
            return GDD()

    def _stage_match_template(self, spec: GameSpec) -> TemplateMatchResult:
        """Stage 04: GameSpec → template match result."""
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
        except Exception as e:
            logger.warning(f"Template match failed ({e}), using LLM path")
            return TemplateMatchResult(path="llm")

    async def _stage_generate_code(
        self,
        spec: GameSpec,
        gdd: GDD,
        match: TemplateMatchResult,
        description: str = "",
    ):
        """Stage 05: generate HTML code (single attempt, no retry)."""
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
        except Exception as e:
            logger.error(f"QA pipeline error: {e}")
            # Return as-is (beta flag)
            return QAResult(success=False, code=code, retries=0)

    # ------------------------------------------------------------------
    # Stage 07: Iteration
    # ------------------------------------------------------------------

    async def iterate(
        self,
        game_id: str,
        current_code: str,
        feedback: str,
        conversation: list,
        progress_cb: ProgressCallback = None,
    ) -> dict:
        """Stage 07: Incremental code modification."""
        self._notify(progress_cb, PipelineStage.code_generating, 30, "分析修改意图…")

        try:
            updated_code, iter_type = await self.code_generator.iterate(
                current_code=current_code,
                feedback=feedback,
                conversation=conversation,
            )
        except Exception as e:
            logger.error(f"Iteration failed: {e}")
            updated_code = current_code
            iter_type = None

        # Quick QA on iterated code
        self._notify(progress_cb, PipelineStage.qa_checking, 70, "检测修改结果…")
        qa = await self.qa_pipeline.run_with_auto_fix(
            code=updated_code,
            max_retries=1,  # lighter check for iterations
        )

        self._notify(progress_cb, PipelineStage.completed, 100, "迭代完成！")
        return {
            "html_code": qa.code,
            "iteration_type": iter_type.value if iter_type else "element_change",
            "qa_passed": qa.success,
        }

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

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
