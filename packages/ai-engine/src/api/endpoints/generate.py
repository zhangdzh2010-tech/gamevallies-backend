"""API endpoints for the V2 game generation pipeline.

Current endpoints:
  POST /api/v1/ai/pipeline/v2/run             – V2 full pipeline (description → HTML)
  POST /api/v1/ai/pipeline/v2/run/async       – async task wrapper for long-running generation
  POST /api/v1/ai/pipeline/v2/iterate         – V2 iteration (feedback → updated HTML)
  POST /api/v1/ai/pipeline/v2/iterate/async   – async task wrapper for iteration
  GET  /api/v1/ai/tasks/{task_id}             – async task status/result
  GET  /api/v1/ai/tasks                       – list async tasks
  POST /api/v1/ai/tasks/{task_id}/cancel      – cancel async task
  POST /api/v1/ai/parse-intent                – single-shot intent → spec compile (used by game-service)
  GET  /api/v1/ai/health

The legacy V1 pipeline endpoints (/pipeline/run, /pipeline/iterate and their
/async variants) and the pre-pipeline endpoints (/generate-code, /iterate-code,
/qa-check) were removed together with the V1 PipelineOrchestrator.
"""

from __future__ import annotations

import asyncio
import json
import time
import logging
import re
import traceback
import httpx
from fastapi import APIRouter, Header, HTTPException, Query, status as http_status
from typing import Optional, Any

from ..models import (
    AsyncTaskHandleResponse,
    AsyncTaskResponse,
    AsyncTaskStatus,
    AsyncTaskType,
    CoverCaptureRequest,
    CoverCaptureResponse,
    IterateResponse,
    IterateV2Request,
    ListAsyncTasksResponse,
    ParseIntentRequest,
    ParseIntentResponse,
    ProviderCatalogPreviewRequest,
    ProviderCatalogPreviewResponse,
    ProviderTestChatRequest,
    ProviderTestChatResponse,
    RunPipelineResponse,
    RunPipelineV2Request,
)
from ...engine.dialogue_engine import DialogueEngine
from ...engine.pipeline_errors import PipelineExecutionError
from ...engine.pipeline_v2_runner import V2PipelineRunner
from ...engine.prompt_bundle_resolver import resolve_prompt_bundle_snapshot
from ...engine.prompt_store import (
    cached_prompt_count,
    refresh as refresh_prompt_cache,
)
from ...engine.runtime_qa import capture_cover_artifact
from ...config.settings import settings
from ...config.timeout_store import (
    cached_count as cached_timeout_count,
    get_float as get_timeout_float,
    get_int as get_timeout_int,
    refresh as refresh_timeout_cache,
)
from ...services.async_task_manager import task_manager
from ...services.llm_gateway import gateway, llm_request_context
from ...services.task_memory import task_memory
from ...services.websocket_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

# Singleton instances
_v2_runner = V2PipelineRunner()
_dialogue_engine = DialogueEngine()


def _require_admin_token(token: Optional[str]) -> None:
    expected = (settings.ADMIN_TOKEN or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin token is not configured")
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
        async with httpx.AsyncClient(timeout=get_timeout_float("timeout.ai_engine.progress_relay_s", 3.0, min_value=0.1)) as client:
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
    if value is not None:
        return int(value)
    return get_timeout_int("timeout.pipeline.default_s", 1800, min_value=30, max_value=3600)


def _game_service_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if settings.ADMIN_TOKEN:
        headers["x-admin-token"] = settings.ADMIN_TOKEN
    return headers


async def _relay_artifact_to_game_service(
    *,
    task_id: Optional[str],
    game_id: str,
    user_id: str,
    artifact_type: str,
    content_type: str,
    payload: Any,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    base_url = settings.GAME_SERVICE_UPSTREAM_URL.rstrip("/")
    if not base_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=get_timeout_float("timeout.ai_engine.artifact_relay_s", 5.0, min_value=0.1)) as client:
            response = await client.post(
                f"{base_url}/api/v1/internal/generation/artifact",
                json={
                    "taskId": task_id,
                    "gameId": game_id,
                    "userId": user_id,
                    "artifactType": artifact_type,
                    "contentType": content_type,
                    "payload": payload,
                    "metadata": metadata or {},
                },
                headers=_game_service_headers(),
            )
            body = response.json()
            if isinstance(body, dict):
                data = body.get("data")
                if isinstance(data, dict) and isinstance(data.get("id"), str):
                    return data["id"]
    except Exception as exc:
        logger.debug("Failed to relay artifact %s to game-service: %s", artifact_type, exc)
    return None


async def _maybe_capture_cover_artifact(
    *,
    html_code: str,
    orientation: Optional[str],
    timeout_s: Optional[int],
    title: Optional[str] = None,
    game_type: Optional[str] = None,
    theme: Optional[str] = None,
    runtime_profile: Optional[str] = None,
    visual_pack: Optional[str] = None,
    render_style_intensity: Optional[str] = None,
    updated: bool = False,
    require_game_service_upstream: bool = True,
) -> Optional[dict[str, Any]]:
    if require_game_service_upstream and not settings.GAME_SERVICE_UPSTREAM_URL.rstrip("/"):
        return None
    if not isinstance(html_code, str) or not html_code.strip():
        return None

    capture_timeout_s = max(
        get_timeout_float("timeout.ai_engine.cover_capture_s", 4.0, min_value=0.1),
        0.1,
    )
    if timeout_s is not None:
        capture_timeout_s = min(capture_timeout_s, max(float(timeout_s), 0.1))

    try:
        return await capture_cover_artifact(
            html_code,
            orientation=orientation,
            timeout_s=capture_timeout_s,
            title=title,
            game_type=game_type,
            theme=theme,
            runtime_profile=runtime_profile,
            visual_pack=visual_pack,
            render_style_intensity=render_style_intensity,
            updated=updated,
        )
    except Exception as exc:
        logger.debug("Failed to capture cover artifact: %s", exc)
        return None


async def _relay_stage_summary_to_game_service(
    *,
    task_id: Optional[str],
    stage: str,
    message: str,
    percentage: Optional[int] = None,
    conclusion_type: str = "summary",
    details: Optional[dict[str, Any]] = None,
    artifact_ids: Optional[list[str]] = None,
) -> None:
    base_url = settings.GAME_SERVICE_UPSTREAM_URL.rstrip("/")
    if not base_url or not task_id:
        return

    try:
        async with httpx.AsyncClient(timeout=get_timeout_float("timeout.ai_engine.stage_summary_relay_s", 5.0, min_value=0.1)) as client:
            await client.post(
                f"{base_url}/api/v1/internal/generation/stage-summary",
                json={
                    "taskId": task_id,
                    "stage": stage,
                    "message": message,
                    "percentage": percentage,
                    "conclusionType": conclusion_type,
                    "details": details or {},
                    "artifactIds": artifact_ids or [],
                },
                headers=_game_service_headers(),
            )
    except Exception as exc:
        logger.debug("Failed to relay stage summary %s to game-service: %s", stage, exc)


async def _relay_task_failure_to_game_service(
    *,
    task_id: Optional[str],
    failed_stage: str,
    error_message: str,
    retry_count: int = 0,
    fallback: Optional[str] = None,
    timed_out: bool = False,
    failure_family: Optional[str] = None,
    primary_artifact_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    base_url = settings.GAME_SERVICE_UPSTREAM_URL.rstrip("/")
    if not base_url or not task_id:
        return

    try:
        async with httpx.AsyncClient(timeout=get_timeout_float("timeout.ai_engine.task_failure_relay_s", 5.0, min_value=0.1)) as client:
            await client.post(
                f"{base_url}/api/v1/internal/generation/task-failure",
                json={
                    "taskId": task_id,
                    "failedStage": failed_stage,
                    "errorMessage": error_message,
                    "retryCount": retry_count,
                    "fallback": fallback,
                    "timedOut": timed_out,
                    "failureFamily": failure_family,
                    "primaryArtifactId": primary_artifact_id,
                    "details": details or {},
                },
                headers=_game_service_headers(),
            )
    except Exception as exc:
        logger.debug("Failed to relay task failure %s to game-service: %s", failed_stage, exc)


async def _persist_failure_artifacts(
    *,
    task_id: Optional[str],
    game_id: str,
    user_id: str,
    exc: Exception,
    pipeline_version: str,
    entrypoint: str,
) -> list[str]:
    raw_artifacts = getattr(exc, "artifacts", None)
    if not isinstance(raw_artifacts, list):
        return []

    artifact_ids: list[str] = []
    for item in raw_artifacts:
        if not isinstance(item, dict):
            continue
        artifact_type = str(item.get("artifact_type") or "").strip()
        content_type = str(item.get("content_type") or "").strip()
        if not artifact_type or not content_type:
            continue

        artifact_id = await _relay_artifact_to_game_service(
            task_id=task_id,
            game_id=game_id,
            user_id=user_id,
            artifact_type=artifact_type,
            content_type=content_type,
            payload=item.get("payload"),
            metadata={
                "pipelineVersion": pipeline_version,
                "entrypoint": entrypoint,
                **(item.get("metadata") if isinstance(item.get("metadata"), dict) else {}),
            },
        )
        if artifact_id:
            artifact_ids.append(artifact_id)

    return artifact_ids


def _normalize_v2_prompt_layers(layers: Optional[dict[str, Any]]) -> dict[str, Any]:
    return {
        **(layers or {}),
    }


async def _persist_v2_request_artifacts(
    *,
    task_id: Optional[str],
    game_id: str,
    user_id: str,
    normalized_request: dict[str, Any],
    runtime_contract: dict[str, Any],
    prompt_bundle_snapshot: dict[str, Any],
    source_spec: Optional[dict[str, Any]] = None,
    source_bundle_context: Optional[dict[str, Any]] = None,
) -> list[str]:
    artifact_ids: list[str] = []

    normalized_request_id = await _relay_artifact_to_game_service(
        task_id=task_id,
        game_id=game_id,
        user_id=user_id,
        artifact_type="normalized_request",
        content_type="application/json",
        payload=normalized_request,
        metadata={"pipelineVersion": "v2"},
    )
    if normalized_request_id:
        artifact_ids.append(normalized_request_id)

    runtime_contract_id = await _relay_artifact_to_game_service(
        task_id=task_id,
        game_id=game_id,
        user_id=user_id,
        artifact_type="runtime_contract",
        content_type="application/json",
        payload=runtime_contract,
        metadata={"pipelineVersion": "v2"},
    )
    if runtime_contract_id:
        artifact_ids.append(runtime_contract_id)

    prompt_bundle_id = await _relay_artifact_to_game_service(
        task_id=task_id,
        game_id=game_id,
        user_id=user_id,
        artifact_type="prompt_bundle_snapshot",
        content_type="application/json",
        payload=prompt_bundle_snapshot,
        metadata={"pipelineVersion": "v2"},
    )
    if prompt_bundle_id:
        artifact_ids.append(prompt_bundle_id)

    if source_spec:
        source_spec_id = await _relay_artifact_to_game_service(
            task_id=task_id,
            game_id=game_id,
            user_id=user_id,
            artifact_type="source_game_spec",
            content_type="application/json",
            payload=source_spec,
            metadata={"pipelineVersion": "v2"},
        )
        if source_spec_id:
            artifact_ids.append(source_spec_id)

    if source_bundle_context:
        source_bundle_context_id = await _relay_artifact_to_game_service(
            task_id=task_id,
            game_id=game_id,
            user_id=user_id,
            artifact_type="source_bundle_context",
            content_type="application/json",
            payload=source_bundle_context,
            metadata={"pipelineVersion": "v2"},
        )
        if source_bundle_context_id:
            artifact_ids.append(source_bundle_context_id)

    return artifact_ids


def _classify_failure_family(
    *,
    failed_stage: str,
    message: str,
    timed_out: bool,
) -> str:
    if timed_out:
        return "timeout"

    stage = (failed_stage or "failed").strip().lower()
    lower_message = (message or "").lower()
    if "forbidden api" in lower_message:
        return "forbidden_api"
    if "runtime" in lower_message and "qa" in lower_message:
        return "runtime_qa"
    if "contract" in lower_message:
        return "contract_qa"
    if stage == "request_normalized":
        return "request_normalize"
    if stage == "spec_build":
        return "spec_build"
    if stage == "runtime_profile_select":
        return "runtime_profile"
    if stage == "contract_compose":
        return "contract_compose"
    if stage == "logic_generate":
        return "code_generation"
    if stage == "contract_qa":
        return "contract_qa"
    if stage == "targeted_remediation":
        return "targeted_remediation"
    if stage == "runtime_simulation_qa":
        return "runtime_qa"
    if stage == "intent_parsing":
        return "intent_parse"
    if stage == "designing":
        return "spec_design"
    if stage == "template_matching":
        return "runtime_profile"
    if stage == "code_generating":
        return "code_generation"
    if stage == "qa_checking":
        return "qa_validation"
    if stage == "code_review":
        return "code_review"
    return "pipeline"


def _is_opaque_failure_message(message: str) -> bool:
    normalized = str(message or "").strip()
    if not normalized:
        return True
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", normalized):
        return True
    return len(normalized) <= 2


def _build_failure_diagnostics(exc: Exception) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "exceptionClass": exc.__class__.__name__,
        "exceptionRepr": repr(exc),
    }
    try:
        rendered_traceback = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if rendered_traceback.strip():
            diagnostics["tracebackExcerpt"] = rendered_traceback[-4000:]
    except Exception:
        pass

    if isinstance(exc, HTTPException):
        diagnostics["httpStatusCode"] = exc.status_code
        if exc.detail is not None:
            diagnostics["httpDetail"] = exc.detail
    else:
        detail = getattr(exc, "detail", None)
        if detail is not None:
            diagnostics["httpDetail"] = detail

    return {
        key: value
        for key, value in diagnostics.items()
        if value not in (None, "", [], {})
    }


def _extract_failure_context(exc: Exception, *, fallback_stage: str) -> dict[str, Any]:
    diagnostics = _build_failure_diagnostics(exc)
    detail = getattr(exc, "detail", None) if isinstance(exc, HTTPException) else None
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("error") or str(exc))
        failed_stage = str(detail.get("failed_stage") or detail.get("failedStage") or fallback_stage)
        retry_count = int(detail.get("retry_count") or detail.get("retryCount") or 0)
        fallback = detail.get("fallback")
        timed_out = bool(detail.get("timed_out") or detail.get("timedOut") or ("timed out" in message.lower()))
        failure_family = detail.get("failure_family") or detail.get("failureFamily")
        primary_artifact_id = detail.get("primary_artifact_id") or detail.get("primaryArtifactId")
    else:
        message = str(exc)
        failed_stage = str(getattr(exc, "stage", None) or fallback_stage)
        retry_count = int(getattr(exc, "retry_count", 0) or 0)
        fallback = getattr(exc, "fallback", None)
        timed_out = isinstance(exc, asyncio.TimeoutError) or ("timed out" in message.lower())
        failure_family = getattr(exc, "failure_family", None) or getattr(exc, "failureFamily", None)
        primary_artifact_id = getattr(exc, "primary_artifact_id", None) or getattr(exc, "primaryArtifactId", None)

    if _is_opaque_failure_message(message):
        message = str(diagnostics.get("exceptionRepr") or message)

    return {
        "message": message,
        "failed_stage": failed_stage,
        "retry_count": retry_count,
        "fallback": fallback,
        "timed_out": timed_out,
        "failure_family": failure_family or _classify_failure_family(
            failed_stage=failed_stage,
            message=message,
            timed_out=timed_out,
        ),
        "primary_artifact_id": primary_artifact_id,
        "diagnostics": diagnostics,
    }


def _annotate_failure_exception(exc: Exception, failure: dict[str, Any]) -> None:
    for attr, key in (
        ("stage", "failed_stage"),
        ("retry_count", "retry_count"),
        ("fallback", "fallback"),
        ("failure_family", "failure_family"),
        ("primary_artifact_id", "primary_artifact_id"),
    ):
        try:
            setattr(exc, attr, failure.get(key))
        except Exception:
            pass

    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        detail["message"] = failure["message"]
        detail["failed_stage"] = failure["failed_stage"]
        detail["retry_count"] = failure["retry_count"]
        detail["fallback"] = failure["fallback"]
        detail["failure_family"] = failure["failure_family"]
        detail["primary_artifact_id"] = failure["primary_artifact_id"]
        exc.detail = detail


def _build_async_runner_failure_exception(
    *,
    message: str,
    failed_stage: str,
    failure_family: str,
) -> Exception:
    exc = RuntimeError(message)
    for attr, value in (
        ("stage", failed_stage),
        ("retry_count", 0),
        ("fallback", None),
        ("failure_family", failure_family),
        ("primary_artifact_id", None),
    ):
        try:
            setattr(exc, attr, value)
        except Exception:
            pass
    return exc


async def _handle_async_runner_cancellation(
    *,
    task_id: str,
    game_id: str,
    fallback_stage: str,
    default_message: str,
) -> Exception | None:
    snapshot = await task_manager.get_task(task_id)
    if snapshot and snapshot.status == AsyncTaskStatus.canceled:
        return None

    failed_stage = (
        snapshot.progress.stage
        if snapshot and snapshot.progress and snapshot.progress.stage
        else fallback_stage
    )
    failure_family = _classify_failure_family(
        failed_stage=failed_stage,
        message=default_message,
        timed_out=False,
    )

    await _relay_task_failure_to_game_service(
        task_id=task_id,
        failed_stage=failed_stage,
        error_message=default_message,
        retry_count=0,
        fallback=None,
        timed_out=False,
        failure_family=failure_family,
        primary_artifact_id=None,
        details={
            "pipelineVersion": "v2",
            "exceptionClass": "CancelledError",
            "reason": "unexpected_async_cancellation",
        },
    )
    await _relay_stage_summary_to_game_service(
        task_id=task_id,
        stage=failed_stage,
        message=default_message,
        conclusion_type="failure",
        details={
            "pipelineVersion": "v2",
            "failureFamily": failure_family,
            "exceptionClass": "CancelledError",
            "reason": "unexpected_async_cancellation",
        },
    )
    return _build_async_runner_failure_exception(
        message=default_message,
        failed_stage=failed_stage,
        failure_family=failure_family,
    )


def _decorate_v2_run_response(
    response: RunPipelineResponse,
    request: RunPipelineV2Request,
    *,
    primary_artifact_id: Optional[str] = None,
) -> RunPipelineResponse:
    response.pipeline_version = "v2"
    response.prompt_bundle_id = request.prompt_bundle_snapshot.bundle_id
    response.prompt_bundle_version = request.prompt_bundle_snapshot.bundle_version
    response.runtime_profile = response.runtime_profile or request.runtime_contract.runtime_profile
    response.contract_version = response.contract_version or request.runtime_contract.version
    response.primary_artifact_id = primary_artifact_id
    return response


def _decorate_v2_iterate_response(
    response: IterateResponse,
    request: IterateV2Request,
    *,
    primary_artifact_id: Optional[str] = None,
) -> IterateResponse:
    response.pipeline_version = "v2"
    response.prompt_bundle_id = request.prompt_bundle_snapshot.bundle_id
    response.prompt_bundle_version = request.prompt_bundle_snapshot.bundle_version
    response.runtime_profile = response.runtime_profile or request.runtime_contract.runtime_profile
    response.contract_version = response.contract_version or request.runtime_contract.version
    response.primary_artifact_id = primary_artifact_id
    return response


def _make_progress_cb(
    *,
    game_id: str,
    user_id: str,
    task_id: Optional[str] = None,
):
    loop = asyncio.get_running_loop()
    chain: asyncio.Future[None] = loop.create_future()
    chain.set_result(None)
    progress_seq = 0

    def progress_cb(stage: str, pct: int, message: str, details: Optional[dict] = None) -> None:
        nonlocal chain, progress_seq
        progress_seq += 1
        previous = chain
        payload_details = {
            **(details or {}),
            "progressSeq": progress_seq,
        }

        async def fanout() -> None:
            try:
                await previous
            except Exception:
                pass
            if task_id:
                await task_manager.update_progress(
                    task_id,
                    stage=stage,
                    pct=pct,
                    message=message,
                    details=payload_details,
                )
            await manager.send_progress(
                game_id,
                stage,
                pct,
                message,
                payload_details,
            )
            await _relay_progress_to_game_service(
                game_id=game_id,
                user_id=user_id,
                task_id=task_id,
                stage=stage,
                pct=pct,
                message=message,
                details=payload_details,
            )

        chain = loop.create_task(fanout())

    return progress_cb


async def _initialize_task_memory_for_create(
    request: RunPipelineV2Request,
    *,
    task_id: Optional[str],
) -> None:
    if not task_id:
        return

    await task_memory.begin_task(
        task_id,
        task_meta={
            "entrypoint": request.request_context.entrypoint,
            "game_id": request.game_id,
            "user_id": request.user_id,
            "title": request.title or "",
            "platform": request.platform,
        },
        source_context_summary=f"- raw_user_input: {str(request.raw_user_input or '').strip()[:280]}",
    )


async def _initialize_task_memory_for_iteration(
    request: IterateV2Request,
    *,
    task_id: Optional[str],
) -> None:
    if not task_id:
        return

    await task_memory.begin_task(
        task_id,
        task_meta={
            "entrypoint": request.request_context.entrypoint,
            "game_id": request.game_id,
            "user_id": request.user_id,
            "title": getattr(request.source_bundle_context, "title", "") or "",
            "platform": request.platform,
            "existing_status": request.existing_game.status,
        },
    )
    await task_memory.remember_source_context(
        task_id,
        source_bundle_context=request.source_bundle_context,
        source_spec=request.source_spec,
        current_code=request.current_code,
        feedback=request.iteration_intent.feedback,
    )


async def _run_pipeline_v2_internal(
    request: RunPipelineV2Request,
    *,
    task_id: Optional[str] = None,
) -> RunPipelineResponse:
    effective_task_id = task_id or request.task_id
    resolved_prompt_bundle = resolve_prompt_bundle_snapshot(
        request.prompt_bundle_snapshot.model_copy(
            update={
                "layers": _normalize_v2_prompt_layers(request.prompt_bundle_snapshot.layers),
            }
        ),
        runtime_profile=request.runtime_contract.runtime_profile,
    )
    resolved_request = request.model_copy(update={"prompt_bundle_snapshot": resolved_prompt_bundle})
    request_artifact_ids = await _persist_v2_request_artifacts(
        task_id=effective_task_id,
        game_id=resolved_request.game_id,
        user_id=resolved_request.user_id,
        normalized_request=resolved_request.normalized_request,
        runtime_contract=resolved_request.runtime_contract.model_dump(),
        prompt_bundle_snapshot=resolved_request.prompt_bundle_snapshot.model_dump(),
        source_spec=resolved_request.source_spec.model_dump(mode="json") if resolved_request.source_spec else None,
    )
    await _relay_stage_summary_to_game_service(
        task_id=effective_task_id,
        stage="request_normalized",
        message="V2 request normalized and artifacts captured",
        percentage=5,
        details={
            "pipelineVersion": "v2",
            "entrypoint": resolved_request.request_context.entrypoint,
        },
        artifact_ids=request_artifact_ids,
    )
    progress_cb = _make_progress_cb(
        game_id=resolved_request.game_id,
        user_id=resolved_request.user_id,
        task_id=effective_task_id,
    )
    await _initialize_task_memory_for_create(resolved_request, task_id=effective_task_id)
    try:
        with llm_request_context(
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            task_id=effective_task_id,
        ):
            response = await _v2_runner.run(
                resolved_request,
                progress_cb=progress_cb,
                timeout_s=_resolve_timeout_s(resolved_request.timeout_s),
            )
        spec_artifact_id = await _relay_artifact_to_game_service(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            artifact_type="compiled_game_spec",
            content_type="application/json",
            payload=response.game_spec.model_dump(mode="json"),
            metadata={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
        )
        profile_artifact_id = await _relay_artifact_to_game_service(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            artifact_type="runtime_profile_selection",
            content_type="application/json",
            payload={
                "requested_profile": resolved_request.runtime_contract.runtime_profile,
                "selected_profile": response.runtime_profile or resolved_request.runtime_contract.runtime_profile,
                "contract_version": response.contract_version or resolved_request.runtime_contract.version,
            },
            metadata={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
        )
        primary_artifact_id = await _relay_artifact_to_game_service(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            artifact_type="pipeline_response",
            content_type="application/json",
            payload=response.model_dump(mode="json"),
            metadata={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
        )
        cover_artifact_id = None
        cover_artifact = await _maybe_capture_cover_artifact(
            html_code=response.html_code,
            orientation=getattr(getattr(resolved_request.runtime_contract, "canvas", None), "orientation", None),
            timeout_s=resolved_request.timeout_s,
            title=resolved_request.title,
            game_type=getattr(response.game_spec, "game_type", None),
            theme=getattr(getattr(response.game_spec, "visual_style", None), "theme", None),
            runtime_profile=response.runtime_profile,
            visual_pack=getattr(getattr(response.game_spec, "visual_style", None), "visual_pack", None),
            render_style_intensity=getattr(
                getattr(response.game_spec, "visual_style", None),
                "render_style_intensity",
                None,
            ),
        )
        if cover_artifact:
            cover_artifact_id = await _relay_artifact_to_game_service(
                task_id=effective_task_id,
                game_id=resolved_request.game_id,
                user_id=resolved_request.user_id,
                artifact_type="cover_image",
                content_type=str(cover_artifact.get("content_type") or "image/jpeg"),
                payload=cover_artifact.get("payload"),
                metadata={
                    "pipelineVersion": "v2",
                    "entrypoint": resolved_request.request_context.entrypoint,
                    **dict(cover_artifact.get("metadata") or {}),
                },
            )
        await _relay_stage_summary_to_game_service(
            task_id=effective_task_id,
            stage="completed",
            message="V2 pipeline completed",
            percentage=100,
            details={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
            artifact_ids=[
                artifact_id
                for artifact_id in [spec_artifact_id, profile_artifact_id, primary_artifact_id, cover_artifact_id]
                if artifact_id
            ],
        )
        return _decorate_v2_run_response(
            response,
            resolved_request,
            primary_artifact_id=primary_artifact_id,
        )
    except Exception as exc:
        failure = _extract_failure_context(exc, fallback_stage="pipeline_run")
        failure_artifact_ids = await _persist_failure_artifacts(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            exc=exc,
            pipeline_version="v2",
            entrypoint=resolved_request.request_context.entrypoint,
        )
        if failure_artifact_ids and not failure.get("primary_artifact_id"):
            failure["primary_artifact_id"] = failure_artifact_ids[0]
        failure_artifact_id = await _relay_artifact_to_game_service(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            artifact_type="task_failure",
            content_type="application/json",
            payload={
                **failure,
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
            metadata={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
        )
        if failure_artifact_id and not failure.get("primary_artifact_id"):
            failure["primary_artifact_id"] = failure_artifact_id
        await _relay_task_failure_to_game_service(
            task_id=effective_task_id,
            failed_stage=failure["failed_stage"],
            error_message=failure["message"],
            retry_count=failure["retry_count"],
            fallback=failure["fallback"],
            timed_out=failure["timed_out"],
            failure_family=failure["failure_family"],
            primary_artifact_id=failure["primary_artifact_id"],
            details={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
                "exceptionClass": failure.get("diagnostics", {}).get("exceptionClass"),
                "httpStatusCode": failure.get("diagnostics", {}).get("httpStatusCode"),
            },
        )
        await _relay_stage_summary_to_game_service(
            task_id=effective_task_id,
            stage=failure["failed_stage"],
            message=failure["message"],
            conclusion_type="failure",
            details={
                "pipelineVersion": "v2",
                "failureFamily": failure["failure_family"],
                "exceptionClass": failure.get("diagnostics", {}).get("exceptionClass"),
            },
            artifact_ids=[
                artifact_id
                for artifact_id in [*failure_artifact_ids, failure["primary_artifact_id"]]
                if artifact_id
            ],
        )
        _annotate_failure_exception(exc, failure)
        raise
    finally:
        await task_memory.clear_task(effective_task_id)


async def _run_iteration_v2_internal(
    request: IterateV2Request,
    *,
    task_id: Optional[str] = None,
) -> IterateResponse:
    effective_task_id = task_id or request.task_id
    resolved_prompt_bundle = resolve_prompt_bundle_snapshot(
        request.prompt_bundle_snapshot.model_copy(
            update={
                "layers": _normalize_v2_prompt_layers(request.prompt_bundle_snapshot.layers),
            }
        ),
        runtime_profile=request.runtime_contract.runtime_profile,
    )
    resolved_request = request.model_copy(update={"prompt_bundle_snapshot": resolved_prompt_bundle})
    request_artifact_ids = await _persist_v2_request_artifacts(
        task_id=effective_task_id,
        game_id=resolved_request.game_id,
        user_id=resolved_request.user_id,
        normalized_request=resolved_request.normalized_request,
        runtime_contract=resolved_request.runtime_contract.model_dump(),
        prompt_bundle_snapshot=resolved_request.prompt_bundle_snapshot.model_dump(),
        source_spec=resolved_request.source_spec.model_dump(mode="json") if resolved_request.source_spec else None,
        source_bundle_context=resolved_request.source_bundle_context.model_dump(mode="json")
        if resolved_request.source_bundle_context
        else None,
    )
    await _relay_stage_summary_to_game_service(
        task_id=effective_task_id,
        stage="request_normalized",
        message="V2 iteration request normalized and artifacts captured",
        percentage=5,
        details={
            "pipelineVersion": "v2",
            "entrypoint": resolved_request.request_context.entrypoint,
        },
        artifact_ids=request_artifact_ids,
    )
    progress_cb = _make_progress_cb(
        game_id=resolved_request.game_id,
        user_id=resolved_request.user_id,
        task_id=effective_task_id,
    )
    await _initialize_task_memory_for_iteration(resolved_request, task_id=effective_task_id)
    try:
        with llm_request_context(
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            task_id=effective_task_id,
        ):
            response = await _v2_runner.iterate(
                resolved_request,
                progress_cb=progress_cb,
                timeout_s=_resolve_timeout_s(resolved_request.timeout_s),
            )
        spec_artifact_id = await _relay_artifact_to_game_service(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            artifact_type="compiled_game_spec",
            content_type="application/json",
            payload=response.game_spec.model_dump(mode="json") if response.game_spec else {},
            metadata={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
        )
        profile_artifact_id = await _relay_artifact_to_game_service(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            artifact_type="runtime_profile_selection",
            content_type="application/json",
            payload={
                "requested_profile": resolved_request.runtime_contract.runtime_profile,
                "selected_profile": response.runtime_profile or resolved_request.runtime_contract.runtime_profile,
                "contract_version": response.contract_version or resolved_request.runtime_contract.version,
                "entrypoint": resolved_request.request_context.entrypoint,
            },
            metadata={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
        )
        primary_artifact_id = await _relay_artifact_to_game_service(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            artifact_type="iteration_response",
            content_type="application/json",
            payload=response.model_dump(mode="json"),
            metadata={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
        )
        cover_artifact_id = None
        cover_game_spec = response.game_spec or resolved_request.source_spec
        cover_artifact = await _maybe_capture_cover_artifact(
            html_code=response.html_code,
            orientation=getattr(getattr(resolved_request.runtime_contract, "canvas", None), "orientation", None),
            timeout_s=resolved_request.timeout_s,
            title=resolved_request.source_bundle_context.title,
            game_type=(
                getattr(cover_game_spec, "game_type", None)
                or getattr(resolved_request.source_bundle_context, "latest_game_type", None)
            ),
            theme=getattr(getattr(cover_game_spec, "visual_style", None), "theme", None),
            runtime_profile=response.runtime_profile,
            visual_pack=getattr(getattr(cover_game_spec, "visual_style", None), "visual_pack", None),
            render_style_intensity=getattr(
                getattr(cover_game_spec, "visual_style", None),
                "render_style_intensity",
                None,
            ),
            updated=True,
        )
        if cover_artifact:
            cover_artifact_id = await _relay_artifact_to_game_service(
                task_id=effective_task_id,
                game_id=resolved_request.game_id,
                user_id=resolved_request.user_id,
                artifact_type="cover_image",
                content_type=str(cover_artifact.get("content_type") or "image/jpeg"),
                payload=cover_artifact.get("payload"),
                metadata={
                    "pipelineVersion": "v2",
                    "entrypoint": resolved_request.request_context.entrypoint,
                    **dict(cover_artifact.get("metadata") or {}),
                },
            )
        await _relay_stage_summary_to_game_service(
            task_id=effective_task_id,
            stage="completed",
            message="V2 iteration completed",
            percentage=100,
            details={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
            artifact_ids=[
                artifact_id
                for artifact_id in [spec_artifact_id, profile_artifact_id, primary_artifact_id, cover_artifact_id]
                if artifact_id
            ],
        )
        return _decorate_v2_iterate_response(
            response,
            resolved_request,
            primary_artifact_id=primary_artifact_id,
        )
    except Exception as exc:
        failure = _extract_failure_context(exc, fallback_stage="iteration")
        failure_artifact_ids = await _persist_failure_artifacts(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            exc=exc,
            pipeline_version="v2",
            entrypoint=resolved_request.request_context.entrypoint,
        )
        if failure_artifact_ids and not failure.get("primary_artifact_id"):
            failure["primary_artifact_id"] = failure_artifact_ids[0]
        failure_artifact_id = await _relay_artifact_to_game_service(
            task_id=effective_task_id,
            game_id=resolved_request.game_id,
            user_id=resolved_request.user_id,
            artifact_type="task_failure",
            content_type="application/json",
            payload={
                **failure,
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
            metadata={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
        )
        if failure_artifact_id and not failure.get("primary_artifact_id"):
            failure["primary_artifact_id"] = failure_artifact_id
        await _relay_task_failure_to_game_service(
            task_id=effective_task_id,
            failed_stage=failure["failed_stage"],
            error_message=failure["message"],
            retry_count=failure["retry_count"],
            fallback=failure["fallback"],
            timed_out=failure["timed_out"],
            failure_family=failure["failure_family"],
            primary_artifact_id=failure["primary_artifact_id"],
            details={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
                "exceptionClass": failure.get("diagnostics", {}).get("exceptionClass"),
                "httpStatusCode": failure.get("diagnostics", {}).get("httpStatusCode"),
            },
        )
        await _relay_stage_summary_to_game_service(
            task_id=effective_task_id,
            stage=failure["failed_stage"],
            message=failure["message"],
            conclusion_type="failure",
            details={
                "pipelineVersion": "v2",
                "failureFamily": failure["failure_family"],
                "exceptionClass": failure.get("diagnostics", {}).get("exceptionClass"),
            },
            artifact_ids=[
                artifact_id
                for artifact_id in [*failure_artifact_ids, failure["primary_artifact_id"]]
                if artifact_id
            ],
        )
        _annotate_failure_exception(exc, failure)
        raise
    finally:
        await task_memory.clear_task(effective_task_id)

@router.post("/v2/creative-anchors")
async def creative_anchors_v2(request: dict):
    """PR-07: Creative anchors + intent endpoint.

    Produces a CreativeAnchors structured brief in a single LLM call.
    Falls back to the deterministic builder on any error.

    """
    from ...engine.creative_anchors import CreativeAnchors, build_anchors_fallback
    from ...services.llm_client import LLMClient

    description = (request or {}).get("description", "")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")

    client = LLMClient()
    if not client.is_enabled():
        # Deterministic fallback path: cheap, offline-safe.
        return build_anchors_fallback(description).model_dump()

    try:
        text = await client.complete(
            max_tokens=1024,
            system=(
                "You derive creative anchors and intent parsing into a single "
                "JSON brief. Respond with ONLY a JSON object matching the "
                "CreativeAnchors schema: "
                "{genre, pace_axis, mood, style_axis, entity_pool_hints, expanded_prompt}. "
                "pace_axis must be one of: slow, steady, fast, chaotic. "
                "style_axis must be one of: pixel, painterly, neon, minimal, cartoon. "
                "mood is a short list of adjectives; entity_pool_hints is a short list of nouns. "
                "expanded_prompt is a 1-3 paragraph expansion of the user idea."
            ),
            messages=[{"role": "user", "content": description}],
            step_key="creative_anchors",
            stage="creative_anchors",
            prefer_fast=True,
        )
        cleaned = (text or "").strip()
        try:
            import json as _json
            payload = _json.loads(cleaned)
            anchors = CreativeAnchors(**payload)
            return anchors.model_dump()
        except Exception as parse_err:
            logger.warning(
                "creative_anchors parse failed, using fallback: %s", parse_err
            )
            return {
                **build_anchors_fallback(
                    description, expanded_prompt=cleaned
                ).model_dump(),
                "fallback_used": True,
                "fallback_reason": "llm_parse_failed",
            }
    except Exception as exc:
        logger.error(f"creative_anchors_v2 LLM call failed: {exc}")
        return {
            **build_anchors_fallback(description).model_dump(),
            "fallback_used": True,
            "fallback_reason": str(exc),
        }


@router.get("/games/{task_id}/runtime-qa")
async def get_runtime_qa_state(task_id: str):
    """PR-11: Read the deferred runtime_qa state for a task.

    Returns {state, updated_at, history?} where state is one of
    pending / running / success / failed / skipped. If the task is not
    tracked (e.g. the server restarted, or runtime_qa was never scheduled)
    returns state=unknown.
    """
    from ...engine.runtime_qa_scheduler import get_state
    snap = await get_state(task_id)
    if not snap:
        return {"task_id": task_id, "state": "unknown"}
    return {"task_id": task_id, **snap}


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


@router.post("/config/timeouts/refresh")
async def refresh_timeouts(
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _require_admin_token(x_admin_token)
    timeout_count = refresh_timeout_cache(raise_on_error=True)
    return {
        "status": "ok",
        "message": "timeout cache refreshed",
        "timeout_count": timeout_count,
        "cached_timeout_count": cached_timeout_count(),
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


@router.post("/llm-gateway/providers/catalog/preview", response_model=ProviderCatalogPreviewResponse)
async def preview_llm_gateway_provider_catalog(
    request: ProviderCatalogPreviewRequest,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _require_admin_token(x_admin_token)
    try:
      return await gateway.preview_model_catalog(request)
    except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
      logger.exception("LLM gateway provider catalog preview failed")
      raise HTTPException(status_code=500, detail=f"Provider catalog preview failed: {exc}") from exc


@router.post("/llm-gateway/providers/{provider_id}/test-chat", response_model=ProviderTestChatResponse)
async def test_llm_gateway_provider_chat(
    provider_id: str,
    request: ProviderTestChatRequest,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _require_admin_token(x_admin_token)
    try:
        return await gateway.test_provider_chat(provider_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("LLM gateway provider chat test failed")
        raise HTTPException(status_code=500, detail=f"Provider chat test failed: {exc}") from exc


@router.post("/llm-gateway/providers/{provider_id}/verify")
async def verify_llm_gateway_provider(
    provider_id: str,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
    timeout_s: int = Query(default=30, ge=5, le=300),
):
    _require_admin_token(x_admin_token)
    try:
        report = await gateway.verify_provider_capabilities(provider_id, timeout_s=timeout_s)
        return report
    except Exception as exc:
        logger.exception("LLM gateway provider verification failed")
        raise HTTPException(status_code=500, detail=f"Provider verification failed: {exc}") from exc


@router.get("/llm-gateway/providers/capabilities")
async def list_provider_capabilities(
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
):
    _require_admin_token(x_admin_token)
    from ...services.llm_gateway import STEP_REQUIRED_CAPABILITIES, _provider_has_capability
    gateway._ensure_loaded()

    providers_caps = []
    for pid, provider in gateway._providers.items():
        step_eligibility = {}
        for step_key, required_caps in STEP_REQUIRED_CAPABILITIES.items():
            missing = [cap for cap in required_caps if not _provider_has_capability(provider, cap)]
            step_eligibility[step_key] = {
                "eligible": len(missing) == 0,
                "missing_capabilities": missing,
            }
        providers_caps.append({
            "provider_id": provider.id,
            "provider_name": provider.name,
            "provider_type": provider.provider_type,
            "region": provider.region,
            "enabled": provider.enabled,
            "capability_flags": provider.capability_flags,
            "context_window": provider.context_window,
            "max_tokens": provider.max_tokens,
            "step_eligibility": step_eligibility,
        })

    return {
        "providers": providers_caps,
        "step_capability_requirements": {k: list(v) for k, v in STEP_REQUIRED_CAPABILITIES.items()},
    }


@router.post("/covers/capture", response_model=CoverCaptureResponse)
async def capture_cover(
    request: CoverCaptureRequest,
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
) -> CoverCaptureResponse:
    _require_admin_token(x_admin_token)
    artifact = await _maybe_capture_cover_artifact(
        html_code=request.html_code,
        orientation=request.orientation,
        timeout_s=request.timeout_s,
        title=request.title,
        game_type=request.game_type,
        theme=request.theme,
        runtime_profile=request.runtime_profile,
        visual_pack=request.visual_pack,
        render_style_intensity=request.render_style_intensity,
        updated=request.updated,
        require_game_service_upstream=False,
    )
    if not artifact:
        return CoverCaptureResponse(captured=False, metadata={})
    return CoverCaptureResponse(
        captured=True,
        content_type=str(artifact.get("content_type") or "image/jpeg"),
        payload=str(artifact.get("payload") or ""),
        metadata=dict(artifact.get("metadata") or {}),
    )


@router.post("/pipeline/v2/run", response_model=RunPipelineResponse)
async def run_pipeline_v2(request: RunPipelineV2Request) -> RunPipelineResponse:
    try:
        return await _run_pipeline_v2_internal(request)
    except HTTPException:
        raise
    except PipelineExecutionError as e:
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(
            status_code=504,
            detail={
                "message": str(e),
                "failed_stage": e.stage,
                "retry_count": e.retry_count,
                "fallback": e.fallback,
                "failure_family": getattr(e, "failure_family", None),
                "primary_artifact_id": getattr(e, "primary_artifact_id", None),
            },
        )
    except RuntimeError as e:
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.exception("Pipeline v2 run error")
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(status_code=500, detail=f"Pipeline v2 error: {str(e)}")


@router.post(
    "/pipeline/v2/run/async",
    response_model=AsyncTaskHandleResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def run_pipeline_v2_async(
    request: RunPipelineV2Request,
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> AsyncTaskHandleResponse:
    timeout_s = _resolve_timeout_s(request.timeout_s)

    async def runner(task_id: str) -> RunPipelineResponse:
        try:
            return await _run_pipeline_v2_internal(request, task_id=request.task_id or task_id)
        except asyncio.CancelledError:
            failure_exc = await _handle_async_runner_cancellation(
                task_id=task_id,
                game_id=request.game_id,
                fallback_stage="pipeline_run",
                default_message="Async create pipeline was canceled before completion",
            )
            if failure_exc is None:
                raise
            await manager.send_error(request.game_id, str(failure_exc))
            raise failure_exc
        except Exception as exc:
            await manager.send_error(request.game_id, str(exc))
            raise

    return await task_manager.create_task(
        task_type=AsyncTaskType.pipeline_run,
        game_id=request.game_id,
        user_id=request.user_id,
        timeout_s=timeout_s,
        runner=runner,
        idempotency_key=x_idempotency_key or request.idempotency_key,
    )


@router.post("/pipeline/v2/iterate", response_model=IterateResponse)
async def pipeline_iterate_v2(request: IterateV2Request) -> IterateResponse:
    try:
        return await _run_iteration_v2_internal(request)
    except HTTPException:
        raise
    except PipelineExecutionError as e:
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(
            status_code=504 if "timed out" in str(e).lower() else 500,
            detail={
                "message": str(e),
                "failed_stage": e.stage,
                "retry_count": e.retry_count,
                "fallback": e.fallback,
                "failure_family": getattr(e, "failure_family", None),
                "primary_artifact_id": getattr(e, "primary_artifact_id", None),
            },
        )
    except Exception as e:
        logger.exception("Pipeline v2 iterate error")
        await manager.send_error(request.game_id, str(e))
        raise HTTPException(status_code=500, detail=f"Iteration v2 error: {str(e)}")


@router.post(
    "/pipeline/v2/iterate/async",
    response_model=AsyncTaskHandleResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def pipeline_iterate_v2_async(
    request: IterateV2Request,
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
) -> AsyncTaskHandleResponse:
    timeout_s = _resolve_timeout_s(request.timeout_s)

    async def runner(task_id: str) -> IterateResponse:
        try:
            return await _run_iteration_v2_internal(request, task_id=request.task_id or task_id)
        except asyncio.CancelledError:
            failure_exc = await _handle_async_runner_cancellation(
                task_id=task_id,
                game_id=request.game_id,
                fallback_stage="iteration",
                default_message="Async iteration pipeline was canceled before completion",
            )
            if failure_exc is None:
                raise
            await manager.send_error(request.game_id, str(failure_exc))
            raise failure_exc
        except Exception as exc:
            await manager.send_error(request.game_id, str(exc))
            raise

    return await task_manager.create_task(
        task_type=AsyncTaskType.pipeline_iterate,
        game_id=request.game_id,
        user_id=request.user_id,
        timeout_s=timeout_s,
        runner=runner,
        idempotency_key=x_idempotency_key or request.idempotency_key,
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


@router.post("/parse-intent", response_model=ParseIntentResponse)
async def parse_intent(request: ParseIntentRequest) -> ParseIntentResponse:
    """Compile a description into a GameSpec (used by game-service source-spec compile)."""
    try:
        spec = await _dialogue_engine.parse_description_to_spec(
            request.description,
            title=request.title,
            preferred_game_type=request.preferred_game_type,
            variation_seed=request.variation_seed,
        )
        spec.generation_tier = request.generation_tier
        spec.complexity_budget = str(
            getattr(request.generation_tier, "value", request.generation_tier) or "standard"
        )
        return ParseIntentResponse(
            spec=spec,
            confidence=0.9,
            missing_required=[],
            slot_fill_pct=1.0,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse intent: {str(e)}")


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "PlayForge AI Engine",
        "timestamp": time.time(),
    }


@router.get("/llm-gateway/analytics/step-health")
async def get_step_health_analytics(
    x_admin_token: Optional[str] = Header(default=None, alias="x-admin-token"),
    hours: int = Query(default=24, ge=1, le=720),
):
    """Aggregate LLM call health metrics by step_key."""
    _require_admin_token(x_admin_token)
    from ...services.llm_gateway import gateway

    conn = gateway._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    step_key,
                    COUNT(*) AS total_calls,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failure_count,
                    ROUND(AVG(CASE WHEN success = 1 THEN latency_ms END)) AS avg_latency_ms,
                    ROUND(AVG(CASE WHEN success = 1 THEN output_tokens END)) AS avg_output_tokens,
                    SUM(CASE WHEN error_code = 'LLMResponseTruncatedError' THEN 1 ELSE 0 END) AS truncation_count,
                    SUM(CASE WHEN failover_reason IS NOT NULL AND failover_reason != '' THEN 1 ELSE 0 END) AS failover_count,
                    output_class
                FROM llm_call_logs
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                GROUP BY step_key, output_class
                ORDER BY total_calls DESC
                """,
                (hours,),
            )
            rows = cur.fetchall()
    except Exception as exc:
        # Graceful fallback if new columns don't exist yet
        conn2 = gateway._connect()
        try:
            with conn2.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        step_key,
                        COUNT(*) AS total_calls,
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                        SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failure_count,
                        ROUND(AVG(CASE WHEN success = 1 THEN latency_ms END)) AS avg_latency_ms,
                        ROUND(AVG(CASE WHEN success = 1 THEN output_tokens END)) AS avg_output_tokens,
                        SUM(CASE WHEN error_code = 'LLMResponseTruncatedError' THEN 1 ELSE 0 END) AS truncation_count
                    FROM llm_call_logs
                    WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                    GROUP BY step_key
                    ORDER BY total_calls DESC
                    """,
                    (hours,),
                )
                rows = cur.fetchall()
        finally:
            conn2.close()
    finally:
        conn.close()

    return {
        "hours": hours,
        "steps": [
            {
                "step_key": row["step_key"],
                "total_calls": row["total_calls"],
                "success_count": row.get("success_count", 0),
                "failure_count": row.get("failure_count", 0),
                "success_rate": round(row.get("success_count", 0) / max(row["total_calls"], 1), 3),
                "avg_latency_ms": row.get("avg_latency_ms"),
                "avg_output_tokens": row.get("avg_output_tokens"),
                "truncation_count": row.get("truncation_count", 0),
                "failover_count": row.get("failover_count", 0),
                "output_class": row.get("output_class", ""),
            }
            for row in rows
        ],
    }
