"""API endpoints for the 8-stage game generation pipeline.

New endpoints (pipeline):
  POST /api/v1/ai/pipeline/run       – full stages 02-06 (description → HTML)
  POST /api/v1/ai/pipeline/iterate   – stage 07 (feedback → updated HTML)
  POST /api/v1/ai/dialogue/chat      – stage 01 (one dialogue turn)
  GET  /api/v1/ai/dialogue/session/{session_id} – get current session state

Legacy endpoints (kept for backward compatibility):
  POST /api/v1/ai/generate-code      – old format, now wraps pipeline
  POST /api/v1/ai/iterate-code       – old format, now wraps iteration
  POST /api/v1/ai/parse-intent
  POST /api/v1/ai/qa-check
  GET  /api/v1/ai/health
"""

from __future__ import annotations

import time
import logging
from fastapi import APIRouter, HTTPException
from typing import Optional

from ..models import (
    ChatRequest,
    ChatResponse,
    GenerateCodeRequest,
    GenerateCodeResponse,
    IterateRequest,
    IterateResponse,
    ParseIntentRequest,
    ParseIntentResponse,
    QACheckRequest,
    QACheckResponse,
    RunPipelineRequest,
    RunPipelineResponse,
)
from ...engine.dialogue_engine import DialogueEngine, _sessions
from ...engine.pipeline_orchestrator import PipelineOrchestrator
from ...engine.qa_pipeline import QAPipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

# Singleton instances
_orchestrator = PipelineOrchestrator()
_dialogue_engine = DialogueEngine()
_qa_pipeline = QAPipeline()


# ===========================================================================
# Stage 01 – Dialogue endpoints
# ===========================================================================

@router.post("/dialogue/chat", response_model=ChatResponse)
async def dialogue_chat(request: ChatRequest) -> ChatResponse:
    """Process one dialogue turn (Stage 01: Slot Filling).

    When ready_to_generate=true the client should call /pipeline/run.
    """
    try:
        return await _dialogue_engine.process_message(request)
    except Exception as e:
        logger.exception("Dialogue chat error")
        raise HTTPException(status_code=500, detail=f"Dialogue error: {str(e)}")


@router.get("/dialogue/session/{session_id}")
async def get_dialogue_session(session_id: str):
    """Return current session state (slots + dialogue state)."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "state": session.state,
        "slots": session.slots.model_dump(exclude_none=False),
        "slot_fill_pct": session.slots.fill_pct(),
        "missing_required": session.slots.missing_required(),
        "history_length": len(session.history),
    }


# ===========================================================================
# Stages 02-06 – Full pipeline run
# ===========================================================================

@router.post("/pipeline/run", response_model=RunPipelineResponse)
async def run_pipeline(request: RunPipelineRequest) -> RunPipelineResponse:
    """Run stages 02-06: description → GameSpec → GDD → code → QA → HTML."""
    try:
        return await _orchestrator.run(request)
    except RuntimeError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.exception("Pipeline run error")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")


# ===========================================================================
# Stage 07 – Iteration
# ===========================================================================

@router.post("/pipeline/iterate", response_model=IterateResponse)
async def pipeline_iterate(request: IterateRequest) -> IterateResponse:
    """Stage 07: incremental code modification from user feedback."""
    start = time.time()
    try:
        result = await _orchestrator.iterate(
            game_id=request.game_id,
            current_code=request.current_code,
            feedback=request.feedback,
            conversation=request.conversation,
        )
        elapsed = int((time.time() - start) * 1000)
        return IterateResponse(
            html_code=result["html_code"],
            changes=[f"Applied: {request.feedback}", f"Type: {result['iteration_type']}"],
            iteration_type=result["iteration_type"],
            generation_time_ms=elapsed,
        )
    except Exception as e:
        logger.exception("Pipeline iterate error")
        raise HTTPException(status_code=500, detail=f"Iteration error: {str(e)}")


# ===========================================================================
# Legacy endpoints (backward compatibility)
# ===========================================================================

@router.post("/generate-code", response_model=GenerateCodeResponse)
async def generate_code_legacy(request: GenerateCodeRequest) -> GenerateCodeResponse:
    """Legacy generate-code endpoint – wraps the pipeline internally.

    Accepts either:
      - New format: {game_id, spec, platform}
      - Old game-service format: {game_id, description}
    """
    start = time.time()
    description = request.description or ""
    if not description and request.spec:
        description = (
            f"{request.spec.game_type} game, theme: {request.spec.visual_style.theme}, "
            f"win: {request.spec.rules.win_condition}"
        )
    if not description:
        raise HTTPException(status_code=400, detail="Either 'description' or 'spec' must be provided")

    try:
        pipeline_req = RunPipelineRequest(
            game_id=request.game_id,
            description=description,
            user_id="system",
            platform=request.platform,
        )
        result = await _orchestrator.run(pipeline_req)
        elapsed = int((time.time() - start) * 1000)
        return GenerateCodeResponse(
            html_code=result.html_code,
            strategy=result.strategy,
            template_id=None,
            generation_time_ms=elapsed,
            code_size_bytes=result.code_size_bytes,
        )
    except Exception as e:
        logger.exception("Legacy generate-code error")
        raise HTTPException(status_code=500, detail=f"Generation error: {str(e)}")


@router.post("/iterate-code", response_model=IterateResponse)
async def iterate_code_legacy(request: IterateRequest) -> IterateResponse:
    """Legacy iterate-code – wraps /pipeline/iterate."""
    return await pipeline_iterate(request)


@router.post("/parse-intent", response_model=ParseIntentResponse)
async def parse_intent(request: ParseIntentRequest) -> ParseIntentResponse:
    """Parse description into GameSpec (legacy)."""
    try:
        spec = await _dialogue_engine.parse_description_to_spec(request.description)
        return ParseIntentResponse(spec=spec, confidence=0.85)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse intent: {str(e)}")


@router.post("/qa-check", response_model=QACheckResponse)
async def qa_check(request: QACheckRequest) -> QACheckResponse:
    """Run QA checks on HTML code."""
    try:
        return _qa_pipeline.check(request.html_code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"QA check error: {str(e)}")


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "PlayForge AI Engine",
        "timestamp": time.time(),
    }
