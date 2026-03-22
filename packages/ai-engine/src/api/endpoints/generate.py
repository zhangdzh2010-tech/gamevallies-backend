"""API endpoints for the 8-stage game generation pipeline.

New endpoints (pipeline):
  POST /api/v1/ai/pipeline/run       – full stages 02-06 (description → HTML)
  POST /api/v1/ai/pipeline/run/async – async task wrapper for long-running generation
  POST /api/v1/ai/pipeline/iterate   – stage 07 (feedback → updated HTML)
  POST /api/v1/ai/pipeline/iterate/async – async task wrapper for iteration
  POST /api/v1/ai/dialogue/chat      – stage 01 (one dialogue turn)
  GET  /api/v1/ai/dialogue/session/{session_id} – get current session state
  GET  /api/v1/ai/tasks/{task_id}    – async task status/result
  GET  /api/v1/ai/tasks              – list async tasks
  POST /api/v1/ai/tasks/{task_id}/cancel – cancel async task

Legacy endpoints (kept for backward compatibility):
  POST /api/v1/ai/generate-code      – old format, now wraps pipeline
  POST /api/v1/ai/iterate-code       – old format, now wraps iteration
  POST /api/v1/ai/parse-intent
  POST /api/v1/ai/qa-check
  GET  /api/v1/ai/health
"""

from __future__ import annotations

import asyncio
import time
import logging
import httpx
from fastapi import APIRouter, Header, HTTPException, Query, status as http_status
from typing import Optional

from ..models import (
    AsyncTaskHandleResponse,
    AsyncTaskResponse,
    AsyncTaskStatus,
    AsyncTaskType,
    ChatRequest,
    ChatResponse,
    GenerateCodeRequest,
    GenerateCodeResponse,
    IterateRequest,
    IterateResponse,
    ListAsyncTasksResponse,
    ParseIntentRequest,
    ParseIntentResponse,
    QACheckRequest,
    QACheckResponse,
    RunPipelineRequest,
    RunPipelineResponse,
)
from ...engine.dialogue_engine import DialogueEngine, _sessions
from ...engine.pipeline_orchestrator import PipelineExecutionError, PipelineOrchestrator
from ...engine.prompt_store import cached_prompt_count, refresh as refresh_prompt_cache
from ...engine.qa_pipeline import QAPipeline
from ...config.settings import settings
from ...services.async_task_manager import task_manager
from ...services.llm_gateway import gateway, llm_request_context
from ...services.websocket_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

# Singleton instances
_orchestrator = PipelineOrchestrator()
_dialogue_engine = DialogueEngine()
_qa_pipeline = QAPipeline()


def _require_admin_token(token: Optional[str]) -> None:
    expected = settings.ADMIN_TOKEN or "admin123"
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _relay_progress_to_game_service(
    *,
    game_id: str,
    user_id: str,
    task_id: Optional[str],
    stage: str,
    pct: int,
    message: str,
    details: Optional[dict] = None,
) -> None:
    base_url = settings.GAME_SERVICE_UPSTREAM_URL.rstrip("/")
    if not base_url or stage == "completed":
        return

    headers = {}
    if settings.ADMIN_TOKEN:
        headers["x-admin-token"] = settings.ADMIN_TOKEN

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{base_url}/api/v1/internal/generation/progress",
                json={
                    "taskId": task_id,
                    "gameId": game_id,
                    "userId": user_id,
                    "stage": stage,
                    "percentage": pct,
                    "message": message,
                    "details": details or {},
                },
                headers=headers,
            )
    except Exception as exc:
        logger.debug("Failed to relay generation progress to game-service: %s", exc)


def _resolve_timeout_s(value: Optional[int]) -> int:
    return int(value or settings.PIPELINE_TIMEOUT_S)


def _make_progress_cb(
    *,
    game_id: str,
    user_id: str,
    task_id: Optional[str] = None,
):
    def progress_cb(stage: str, pct: int, message: str, details: Optional[dict] = None) -> None:
        loop = asyncio.get_running_loop()

        async def fanout() -> None:
            if task_id:
                await task_manager.update_progress(
                    task_id,
                    stage=stage,
                    pct=pct,
                    message=message,
                    details=details,
                )
            await manager.send_progress(
                game_id,
                stage,
                pct,
                message,
                details,
            )
            await _relay_progress_to_game_service(
                game_id=game_id,
                user_id=user_id,
                task_id=task_id,
                stage=stage,
                pct=pct,
                message=message,
                details=details,
            )

        loop.create_task(fanout())

    return progress_cb


async def _run_pipeline_internal(
    request: RunPipelineRequest,
    *,
    task_id: Optional[str] = None,
) -> RunPipelineResponse:
    progress_cb = _make_progress_cb(
        game_id=request.game_id,
        user_id=request.user_id,
        task_id=task_id or request.task_id,
    )
    with llm_request_context(
        game_id=request.game_id,
        user_id=request.user_id,
        task_id=task_id or request.task_id,
    ):
        return await _orchestrator.run(
            request,
            progress_cb=progress_cb,
            timeout_s=_resolve_timeout_s(request.timeout_s),
        )


async def _run_iteration_internal(
    request: IterateRequest,
    *,
    task_id: Optional[str] = None,
) -> IterateResponse:
    start = time.time()
    progress_cb = _make_progress_cb(
        game_id=request.game_id,
        user_id=request.user_id,
        task_id=task_id or request.task_id,
    )
    with llm_request_context(
        game_id=request.game_id,
        user_id=request.user_id,
        task_id=task_id or request.task_id,
    ):
        result = await _orchestrator.iterate(
            game_id=request.game_id,
            current_code=request.current_code,
            feedback=request.feedback,
            conversation=request.conversation,
            user_id=request.user_id,
            progress_cb=progress_cb,
            timeout_s=_resolve_timeout_s(request.timeout_s),
        )
    elapsed = int((time.time() - start) * 1000)
    return IterateResponse(
        html_code=result["html_code"],
        changes=[f"Applied: {request.feedback}", f"Type: {result['iteration_type']}"],
        iteration_type=result["iteration_type"],
        generation_time_ms=elapsed,
        qa_retries=result.get("qa_retries", 0),
        iteration_retries=result.get("iteration_retries", 0),
    )


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
# ===========================================================================
# Prompt expansion – user idea → detailed game design prompt
# ===========================================================================

@router.post("/expand-prompt")
async def expand_prompt(request: dict):
    """Expand a short user description into a detailed game design prompt."""
    from ...services.llm_client import LLMClient
    client = LLMClient()
    description = request.get("description", "")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")

    if not client.is_enabled():
        # Mock mode fallback
        return {"expanded_prompt": description}

    system = """你是一个资深游戏策划专家。用户会给你一个简短的游戏想法，你需要将它扩展为一个详细的HTML5手机游戏设计方案。

要求：
1. 保留用户的核心创意和主题
2. 补充完整的游戏机制（玩法规则、操作方式、胜负条件、计分系统）
3. 设计视觉风格（配色、美术风格、特效）
4. 规划游戏节奏（难度曲线、关卡/波次设计）
5. 适配手机触屏操作（点击、滑动、长按等）
6. 控制在200字以内，用中文描述

直接输出游戏设计方案，不要有任何前缀说明。"""

    try:
        text = await client.complete(
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": f"游戏想法：{description}"}],
            step_key="expand_prompt",
            stage="prompt_expand",
            prefer_fast=True,
        )
        return {"expanded_prompt": text.strip()}
    except Exception as e:
        logger.error(f"Prompt expansion failed: {e}")
        return {"expanded_prompt": description}


@router.post("/prompts/refresh")
async def refresh_prompts(
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    """Reload the in-memory prompt cache from system_configs."""
    _require_admin_token(x_admin_token)
    try:
        prompt_count = refresh_prompt_cache(raise_on_error=True)
    except Exception as exc:
        logger.exception("Prompt cache refresh failed")
        raise HTTPException(status_code=503, detail=f"Prompt refresh failed: {exc}") from exc

    return {
        "status": "ok",
        "message": "prompt cache refreshed",
        "prompt_count": prompt_count,
        "cached_prompt_count": cached_prompt_count(),
    }


@router.post("/llm-gateway/refresh")
async def refresh_llm_gateway(
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _require_admin_token(x_admin_token)
    provider_count = gateway.refresh(raise_on_error=True)
    return {
        "status": "ok",
        "message": "llm gateway refreshed",
        "provider_count": provider_count,
    }


@router.post("/llm-gateway/providers/{provider_id}/test")
async def test_llm_gateway_provider(
    provider_id: str,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _require_admin_token(x_admin_token)
    try:
        return await gateway.test_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("LLM gateway provider test failed")
        raise HTTPException(status_code=500, detail=f"Provider test failed: {exc}") from exc


# ===========================================================================
# Stages 02-06 – Full pipeline run
# ===========================================================================

@router.post("/pipeline/run", response_model=RunPipelineResponse)
async def run_pipeline(request: RunPipelineRequest) -> RunPipelineResponse:
    """Run stages 02-06: description → GameSpec → GDD → code → QA → HTML."""
    try:
        return await _run_pipeline_internal(request)
    except PipelineExecutionError as e:
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(
            status_code=504,
            detail={
                "message": str(e),
                "failed_stage": e.stage,
                "retry_count": e.retry_count,
                "fallback": e.fallback,
            },
        )
    except RuntimeError as e:
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.exception("Pipeline run error")
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")


@router.post(
    "/pipeline/run/async",
    response_model=AsyncTaskHandleResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def run_pipeline_async(request: RunPipelineRequest) -> AsyncTaskHandleResponse:
    """Create an async generation task for stages 02-06."""
    timeout_s = _resolve_timeout_s(request.timeout_s)

    async def runner(task_id: str) -> RunPipelineResponse:
        try:
            return await _run_pipeline_internal(request, task_id=task_id)
        except Exception as exc:
            await manager.send_error(request.game_id, str(exc))
            raise

    return await task_manager.create_task(
        task_type=AsyncTaskType.pipeline_run,
        game_id=request.game_id,
        user_id=request.user_id,
        timeout_s=timeout_s,
        runner=runner,
    )


# ===========================================================================
# Stage 07 – Iteration
# ===========================================================================

@router.post("/pipeline/iterate", response_model=IterateResponse)
async def pipeline_iterate(request: IterateRequest) -> IterateResponse:
    """Stage 07: incremental code modification from user feedback."""
    try:
        return await _run_iteration_internal(request)
    except PipelineExecutionError as e:
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(
            status_code=504 if "timed out" in str(e).lower() else 500,
            detail={
                "message": str(e),
                "failed_stage": e.stage,
                "retry_count": e.retry_count,
                "fallback": e.fallback,
            },
        )
    except Exception as e:
        logger.exception("Pipeline iterate error")
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(status_code=500, detail=f"Iteration error: {str(e)}")


@router.post(
    "/pipeline/iterate/async",
    response_model=AsyncTaskHandleResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def pipeline_iterate_async(request: IterateRequest) -> AsyncTaskHandleResponse:
    """Create an async iteration task for stage 07."""
    timeout_s = _resolve_timeout_s(request.timeout_s)

    async def runner(task_id: str) -> IterateResponse:
        try:
            return await _run_iteration_internal(request, task_id=task_id)
        except Exception as exc:
            await manager.send_error(request.game_id, str(exc))
            raise

    return await task_manager.create_task(
        task_type=AsyncTaskType.pipeline_iterate,
        game_id=request.game_id,
        user_id=request.user_id,
        timeout_s=timeout_s,
        runner=runner,
    )


@router.get("/tasks/{task_id}", response_model=AsyncTaskResponse)
async def get_async_task(task_id: str) -> AsyncTaskResponse:
    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks", response_model=ListAsyncTasksResponse)
async def list_async_tasks(
    user_id: Optional[str] = Query(default=None),
    game_id: Optional[str] = Query(default=None),
    status: Optional[AsyncTaskStatus] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> ListAsyncTasksResponse:
    return await task_manager.list_tasks(
        user_id=user_id,
        game_id=game_id,
        status=status,
        limit=limit,
    )


@router.post("/tasks/{task_id}/cancel", response_model=AsyncTaskResponse)
async def cancel_async_task(task_id: str) -> AsyncTaskResponse:
    task = await task_manager.cancel_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


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
            timeout_s=_resolve_timeout_s(request.timeout_s),
        )
        result = await _orchestrator.run(
            pipeline_req,
            timeout_s=_resolve_timeout_s(pipeline_req.timeout_s),
        )
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
