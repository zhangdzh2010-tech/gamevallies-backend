"""API endpoints for the 8-stage game generation pipeline.

Current endpoints:
  POST /api/v1/ai/pipeline/run              – full stages 02-06 (description → HTML)
  POST /api/v1/ai/pipeline/run/async        – async task wrapper for long-running generation
  POST /api/v1/ai/pipeline/iterate          – stage 07 (feedback → updated HTML)
  POST /api/v1/ai/pipeline/iterate/async    – async task wrapper for iteration
  POST /api/v1/ai/expand-prompt             – expand a short create brief for user confirmation
  GET  /api/v1/ai/tasks/{task_id}           – async task status/result
  GET  /api/v1/ai/tasks                     – list async tasks
  POST /api/v1/ai/tasks/{task_id}/cancel    – cancel async task

Legacy endpoints (kept for backward compatibility):
  POST /api/v1/ai/generate-code      – old format, now wraps pipeline
  POST /api/v1/ai/iterate-code       – old format, now wraps iteration
  POST /api/v1/ai/parse-intent
  POST /api/v1/ai/qa-check
  GET  /api/v1/ai/health
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
    GenerateCodeRequest,
    GenerateCodeResponse,
    IterateRequest,
    IterateResponse,
    IterateV2Request,
    ListAsyncTasksResponse,
    ParseIntentRequest,
    ParseIntentResponse,
    ProviderCatalogPreviewRequest,
    ProviderCatalogPreviewResponse,
    ProviderTestChatRequest,
    ProviderTestChatResponse,
    QACheckRequest,
    QACheckResponse,
    RunPipelineRequest,
    RunPipelineResponse,
    RunPipelineV2Request,
)
from ...engine.dialogue_engine import (
    DialogueEngine,
    _build_heuristic_slot_payload,
    _detect_ui_language,
    _normalize_free_text,
)
from ...engine.pipeline_orchestrator import PipelineExecutionError, PipelineOrchestrator
from ...engine.pipeline_v2_runner import V2PipelineRunner
from ...engine.prompt_bundle_resolver import resolve_prompt_bundle_snapshot
from ...engine.prompt_store import (
    cached_prompt_count,
    get_active_prompt_bundle,
    get_default_runtime_profile,
    require_prompt,
    refresh as refresh_prompt_cache,
)
from ...engine.qa_pipeline import QAPipeline
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
_orchestrator = PipelineOrchestrator()
_v2_runner = V2PipelineRunner()
_dialogue_engine = DialogueEngine()
_qa_pipeline = QAPipeline()


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


def _resolve_v2_region() -> str:
    region = (settings.SERVICE_REGION or "").strip()
    return region or "cn_shanghai"


def _should_upgrade_legacy_pipeline_endpoints_to_v2() -> bool:
    return bool(settings.PIPELINE_UPGRADE_LEGACY_ENDPOINTS_TO_V2)


def _default_v2_prompt_bundle_snapshot(*, entrypoint: str, source: str) -> dict[str, Any]:
    bundle = get_active_prompt_bundle()
    if not bundle:
        raise HTTPException(status_code=503, detail="No active prompt bundle configured")
    return {
        "bundle_id": bundle["id"],
        "bundle_version": int(bundle["version"]),
        "resolved_at": None,
        "layers": _normalize_v2_prompt_layers({
            "entrypoint": entrypoint,
            "source": source,
        }),
    }


def _default_v2_runtime_contract(*, entrypoint: str, source: str) -> dict[str, Any]:
    profile = get_default_runtime_profile()
    if not profile:
        raise HTTPException(status_code=503, detail="No enabled runtime profile configured")

    contract_schema = profile.get("contract_schema") if isinstance(profile, dict) else {}
    if not isinstance(contract_schema, dict):
        contract_schema = {}

    return {
        "version": "1.0",
        "runtime_profile": str(profile["id"]),
        **contract_schema,
        "metadata": {
            "adapter": "compat_v1",
            "entrypoint": entrypoint,
            "source": source,
            "profile_id": str(profile["id"]),
            "profile_display_name": profile.get("display_name"),
        },
    }


def _upgrade_run_request_to_v2(
    request: RunPipelineRequest,
    *,
    source: str,
) -> RunPipelineV2Request:
    description = request.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="description is required")

    return RunPipelineV2Request(
        game_id=request.game_id,
        user_id=request.user_id,
        raw_user_input=description,
        platform=request.platform,
        timeout_s=_resolve_timeout_s(request.timeout_s),
        task_id=request.task_id,
        request_context={
            "source": source,
            "entrypoint": "create",
            "region": _resolve_v2_region(),
            "pipeline_version": "v2",
            "metadata": {
                "compat_source": source,
                "upgraded_from": "RunPipelineRequest",
            },
        },
        prompt_bundle_snapshot=_default_v2_prompt_bundle_snapshot(
            entrypoint="create",
            source=source,
        ),
        runtime_contract=_default_v2_runtime_contract(
            entrypoint="create",
            source=source,
        ),
        normalized_request={
            "description": description,
            "entrypoint": "create",
            "compat_source": source,
        },
        metadata={
            "adapter": "compat_v1",
            "compat_source": source,
            "upgraded_from": "RunPipelineRequest",
        },
    )


def _upgrade_iterate_request_to_v2(
    request: IterateRequest,
    *,
    source: str,
) -> IterateV2Request:
    feedback = request.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="feedback is required")

    return IterateV2Request(
        game_id=request.game_id,
        user_id=request.user_id,
        current_code=request.current_code,
        iteration_intent={
            "feedback": feedback,
            "conversation": request.conversation,
        },
        platform="wechat_webview",
        timeout_s=_resolve_timeout_s(request.timeout_s),
        task_id=request.task_id,
        request_context={
            "source": source,
            "entrypoint": "iterate",
            "region": _resolve_v2_region(),
            "pipeline_version": "v2",
            "metadata": {
                "compat_source": source,
                "upgraded_from": "IterateRequest",
            },
        },
        prompt_bundle_snapshot=_default_v2_prompt_bundle_snapshot(
            entrypoint="iterate",
            source=source,
        ),
        runtime_contract=_default_v2_runtime_contract(
            entrypoint="iterate",
            source=source,
        ),
        normalized_request={
            "feedback": feedback,
            "entrypoint": "iterate",
            "compat_source": source,
            "conversation_length": len(request.conversation),
        },
        metadata={
            "adapter": "compat_v1",
            "compat_source": source,
            "upgraded_from": "IterateRequest",
        },
    )


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


async def _run_pipeline_internal(
    request: RunPipelineRequest,
    *,
    task_id: Optional[str] = None,
) -> RunPipelineResponse:
    effective_task_id = task_id or request.task_id
    progress_cb = _make_progress_cb(
        game_id=request.game_id,
        user_id=request.user_id,
        task_id=effective_task_id,
    )
    await task_memory.begin_task(
        effective_task_id,
        task_meta={
            "entrypoint": "legacy_create",
            "game_id": request.game_id,
            "user_id": request.user_id,
            "platform": request.platform,
        },
        source_context_summary=f"- raw_user_input: {str(request.description or '').strip()[:280]}",
    )
    try:
        with llm_request_context(
            game_id=request.game_id,
            user_id=request.user_id,
            task_id=effective_task_id,
        ):
            return await _orchestrator.run(
                request,
                progress_cb=progress_cb,
                timeout_s=_resolve_timeout_s(request.timeout_s),
            )
    finally:
        await task_memory.clear_task(effective_task_id)


async def _run_iteration_internal(
    request: IterateRequest,
    *,
    task_id: Optional[str] = None,
) -> IterateResponse:
    start = time.time()
    effective_task_id = task_id or request.task_id
    progress_cb = _make_progress_cb(
        game_id=request.game_id,
        user_id=request.user_id,
        task_id=effective_task_id,
    )
    await task_memory.begin_task(
        effective_task_id,
        task_meta={
            "entrypoint": "legacy_iterate",
            "game_id": request.game_id,
            "user_id": request.user_id,
        },
    )
    await task_memory.remember_source_context(
        effective_task_id,
        current_code=request.current_code,
        feedback=request.feedback,
    )
    try:
        with llm_request_context(
            game_id=request.game_id,
            user_id=request.user_id,
            task_id=effective_task_id,
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
    finally:
        await task_memory.clear_task(effective_task_id)
    elapsed = int((time.time() - start) * 1000)
    return IterateResponse(
        html_code=result["html_code"],
        changes=[f"Applied: {request.feedback}", f"Type: {result['iteration_type']}"],
        iteration_type=result["iteration_type"],
        generation_time_ms=elapsed,
        qa_retries=result.get("qa_retries", 0),
        iteration_retries=result.get("iteration_retries", 0),
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


# ===========================================================================
# Prompt expansion - user idea -> detailed game design prompt
# ===========================================================================

def _legacy_build_expand_prompt_fallback_unused(description: str) -> str:
    normalized = re.sub(r"\s+", " ", str(description or "").strip())
    if re.search(r"[\u4e00-\u9fff]", normalized):
        return "\n".join(
            [
                f"原始想法：{normalized}",
                "",
                "请把这条想法整理成一个适合移动端小游戏生成的确认稿，并至少覆盖这些要素：",
                "Game Type: 根据原始想法确定游戏方向",
                "Core Mechanic: 提炼玩家最常执行的核心动作",
                "Theme: 保留原始想法里的题材、场景或情绪",
                "Input Method: 采用适合手机的点击、滑动或拖拽操作",
                "Win Condition: 明确玩家这一局如何过关或获胜",
                "Difficulty Ramp: 说明难度如何逐步提升",
                "Scoring / Rewards: 补充积分、连击、奖励或解锁节奏",
                "Visual Direction: 给出匹配题材的视觉风格",
                "Special Rules or Reference Inspiration: 仅在确有帮助时补充",
            ]
        )

    return "\n".join(
        [
            f"Original Idea: {normalized}",
            "",
            "Please turn this brief into a mobile-friendly game generation prompt that covers at least these elements:",
            "Game Type: Choose the most fitting direction from the original idea",
            "Core Mechanic: Describe the main repeated player action",
            "Theme: Preserve the setting, fantasy, or mood implied by the brief",
            "Input Method: Use touch-friendly tap, swipe, or drag controls",
            "Win Condition: Define a clear round objective or victory condition",
            "Difficulty Ramp: Explain how the challenge escalates over time",
            "Scoring / Rewards: Add points, streaks, rewards, or unlocks that reinforce the loop",
            "Visual Direction: Suggest an art direction that matches the brief",
            "Special Rules or Reference Inspiration: Add only when it materially helps the concept",
        ]
    )


_EXPAND_PROMPT_INTERNAL_LABELS = (
    "game type:",
    "core mechanic:",
    "theme:",
    "input method:",
    "win condition:",
    "difficulty ramp:",
    "scoring / rewards:",
    "visual direction:",
    "special rules or reference inspiration:",
    "\u6e38\u620f\u7c7b\u578b\uff1a",
    "\u6838\u5fc3\u73a9\u6cd5\uff1a",
    "\u4e3b\u9898\uff1a",
    "\u64cd\u4f5c\u65b9\u5f0f\uff1a",
    "\u80dc\u5229\u6761\u4ef6\uff1a",
    "\u96be\u5ea6\u8282\u594f\uff1a",
)

_EXPAND_PROMPT_GENERIC_MARKERS = (
    "determine based on the original idea",
    "choose the most fitting direction",
    "describe the main repeated player action",
    "preserve the setting, fantasy, or mood implied by the brief",
    "use touch-friendly tap, swipe, or drag controls",
    "define a clear round objective or victory condition",
    "explain how the challenge escalates over time",
    "add points, streaks, rewards, or unlocks that reinforce the loop",
    "suggest an art direction that matches the brief",
    "add only when it materially helps the concept",
    "\u6839\u636e\u539f\u59cb\u60f3\u6cd5\u786e\u5b9a",
    "\u63d0\u70bc\u73a9\u5bb6\u6700\u5e38\u6267\u884c",
    "\u4fdd\u7559\u539f\u59cb\u60f3\u6cd5",
    "\u91c7\u7528\u9002\u5408\u624b\u673a",
    "\u660e\u786e\u73a9\u5bb6\u8fd9\u4e00\u5c40\u5982\u4f55",
    "\u8bf4\u660e\u96be\u5ea6\u5982\u4f55",
    "\u8865\u5145\u79ef\u5206",
    "\u7ed9\u51fa\u5339\u914d\u9898\u6750",
    "\u4ec5\u5728\u786e\u6709\u5e2e\u52a9\u65f6\u8865\u5145",
)

_EXPAND_PROMPT_META_RULE_MARKERS = (
    "favor a distinctive gameplay loop instead of the most common default for this genre.",
    "avoid turning every open brief into a match-3 clone.",
    "avoid turning every learning brief into a worksheet or plain flash-card list.",
    "avoid reducing the joke to a generic survive-and-score loop with only reskinned art.",
    "avoid defaulting to the same hazard-dodging survival loop when another clear arcade objective can fit.",
    "preserve the core theme while expanding the content",
    "\u4f18\u5148\u4fdd\u7559\u539f\u6709\u4e3b\u9898\u5e76\u6269\u5c55\u5185\u5bb9",
)

_EXPAND_PROMPT_TEMPLATE_MARKERS = (
    "original idea:",
    "please turn this brief into a mobile-friendly game generation prompt",
    "covers at least these elements:",
    "\u539f\u59cb\u60f3\u6cd5\uff1a",
    "\u8bf7\u628a\u8fd9\u6761\u60f3\u6cd5\u6574\u7406\u6210",
    "\u81f3\u5c11\u8981\u8986\u76d6\u8fd9\u4e9b\u8981\u7d20",
)

_EXPAND_PROMPT_GAME_TYPE_DISPLAY = {
    "casual": {
        "en-US": "casual arcade game",
        "zh-CN": "\u8f7b\u677e\u4f11\u95f2\u5c0f\u6e38\u620f",
    },
    "puzzle": {
        "en-US": "puzzle game",
        "zh-CN": "\u76ca\u667a\u89e3\u8c1c\u5c0f\u6e38\u620f",
    },
    "educational": {
        "en-US": "educational mini-game",
        "zh-CN": "\u6559\u80b2\u4e92\u52a8\u5c0f\u6e38\u620f",
    },
    "funny": {
        "en-US": "comedy mini-game",
        "zh-CN": "\u641e\u7b11\u4e92\u52a8\u5c0f\u6e38\u620f",
    },
}

_EXPAND_PROMPT_INPUT_DISPLAY = {
    "tap": {"en-US": "tap controls", "zh-CN": "\u70b9\u51fb\u64cd\u4f5c"},
    "swipe": {"en-US": "swipe controls", "zh-CN": "\u6ed1\u52a8\u64cd\u4f5c"},
    "drag": {"en-US": "drag controls", "zh-CN": "\u62d6\u62fd\u64cd\u4f5c"},
    "hold": {"en-US": "press-and-hold controls", "zh-CN": "\u957f\u6309\u64cd\u4f5c"},
    "touch": {"en-US": "touch-first controls", "zh-CN": "\u89e6\u6478\u64cd\u4f5c"},
}

_EXPAND_PROMPT_THEME_DISPLAY = {
    "space": {"en-US": "a space setting", "zh-CN": "\u592a\u7a7a\u9898\u6750"},
    "zoo": {"en-US": "an animal setting", "zh-CN": "\u52a8\u7269\u4e3b\u9898"},
    "ocean": {"en-US": "an ocean setting", "zh-CN": "\u6d77\u6d0b\u4e3b\u9898"},
    "forest": {"en-US": "a forest setting", "zh-CN": "\u68ee\u6797\u4e3b\u9898"},
    "city": {"en-US": "an urban setting", "zh-CN": "\u57ce\u5e02\u6216\u529e\u516c\u5ba4\u573a\u666f"},
    "food": {"en-US": "a food-themed setting", "zh-CN": "\u98df\u7269\u6216\u53a8\u623f\u9898\u6750"},
    "toy": {"en-US": "a toy-like setting", "zh-CN": "\u73a9\u5177\u611f\u9898\u6750"},
    "fantasy": {"en-US": "a fantasy setting", "zh-CN": "\u5947\u5e7b\u9898\u6750"},
    "garden": {"en-US": "a garden setting", "zh-CN": "\u82b1\u56ed\u4e3b\u9898"},
    "neon": {"en-US": "a neon comic setting", "zh-CN": "\u9700\u8981\u9707\u76ee\u7684\u9700\u8679\u6c14\u8d28"},
}


def _short_expand_prompt_excerpt(text: str, *, limit: int = 96) -> str:
    normalized = _normalize_free_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip(" ,.;:!?") + "..."


def _localized_expand_game_type(game_type: str, ui_language: str) -> str:
    normalized = str(game_type or "").strip().lower()
    default_value = (
        "\u624b\u673a\u5c0f\u6e38\u620f" if ui_language == "zh-CN" else "mobile mini-game"
    )
    return _EXPAND_PROMPT_GAME_TYPE_DISPLAY.get(normalized, {}).get(ui_language, default_value)


def _localized_expand_input_method(input_method: str, ui_language: str) -> str:
    normalized = str(input_method or "").strip().lower()
    default_value = (
        "\u89e6\u6478\u4f18\u5148\u7684\u624b\u673a\u64cd\u4f5c"
        if ui_language == "zh-CN"
        else "touch-first mobile controls"
    )
    return _EXPAND_PROMPT_INPUT_DISPLAY.get(normalized, {}).get(ui_language, default_value)


def _localized_expand_difficulty_phrase(difficulty: str, ui_language: str) -> str:
    normalized = str(difficulty or "").strip().lower()
    if ui_language == "zh-CN":
        mapping = {
            "easy": "\u4ece\u8f7b\u677e\u4e0a\u624b\u5f00\u59cb\uff0c\u4fdd\u6301\u6bd4\u8f83\u53cb\u597d\u7684\u538b\u529b",
            "medium": "\u524d\u9762\u597d\u4e0a\u624b\uff0c\u540e\u9762\u9010\u6e10\u589e\u52a0\u6311\u6218",
            "hard": "\u524d\u671f\u5148\u8ba9\u73a9\u5bb6\u770b\u61c2\uff0c\u540e\u7eed\u5f88\u5feb\u63d0\u9ad8\u538b\u529b",
            "progressive": "\u5148\u8ba9\u73a9\u5bb6\u5feb\u901f\u4e0a\u624b\uff0c\u518d\u4e00\u6b65\u6b65\u5f80\u4e0a\u52a0\u538b",
        }
        return mapping.get(
            normalized,
            "\u5148\u8ba9\u73a9\u5bb6\u5feb\u901f\u4e0a\u624b\uff0c\u518d\u4e00\u6b65\u6b65\u5f80\u4e0a\u52a0\u538b",
        )

    mapping = {
        "easy": "start easy and stay relaxed",
        "medium": "stay readable at first and become moderately challenging",
        "hard": "start readable but become demanding quickly",
        "progressive": "start simple and ramp up step by step",
    }
    return mapping.get(normalized, "start simple and ramp up step by step")


def _localized_expand_reward_sentence(game_type: str, ui_language: str) -> str:
    normalized = str(game_type or "").strip().lower()
    if ui_language == "zh-CN":
        mapping = {
            "casual": "\u53cd\u9988\u548c\u5956\u52b1\u8981\u56f4\u7ed5\u77ed\u5c40\u6210\u5c31\uff0c\u5206\u6570\u4e0a\u6da8\uff0c\u4ee5\u53ca\u5931\u8d25\u540e\u80fd\u7acb\u5373\u518d\u6765\u4e00\u5c40\u7684\u723d\u611f\u5c55\u5f00\u3002",
            "puzzle": "\u53cd\u9988\u548c\u5956\u52b1\u8981\u4f53\u73b0\u5728\u68cb\u76d8\u8d8a\u6765\u8d8a\u6e05\u723d\uff0c\u8fde\u9501\u89e3\u51b3\uff0c\u4ee5\u53ca\u8fc7\u5173\u540e\u7684\u660e\u663e\u6210\u5c31\u611f\u4e0a\u3002",
            "educational": "\u53cd\u9988\u548c\u5956\u52b1\u8981\u4f53\u73b0\u5728\u7b54\u5bf9\u540e\u7684\u7acb\u5373\u786e\u8ba4\uff0c\u8fdb\u5ea6\u91cc\u7a0b\u7891\uff0c\u4ee5\u53ca\u77ed\u5e73\u5feb\u7684\u6b63\u5411\u9f13\u52b1\u3002",
            "funny": "\u53cd\u9988\u548c\u5956\u52b1\u8981\u56f4\u7ed5\u7b11\u70b9\u9012\u8fdb\uff0c\u8fd1\u4e4e\u7ffb\u8f66\u7684\u60ca\u9669\u65f6\u523b\uff0c\u4ee5\u53ca\u5938\u5f20\u7684\u6210\u529f\u6216\u5931\u8d25\u6f14\u51fa\u5c55\u5f00\u3002",
        }
        return mapping.get(
            normalized,
            "\u53cd\u9988\u548c\u5956\u52b1\u8981\u8ba9\u73a9\u5bb6\u6bcf\u4e00\u5c40\u90fd\u80fd\u611f\u5230\u8fdb\u5ea6\u3001\u723d\u70b9\u548c\u9a6c\u4e0a\u60f3\u518d\u6765\u4e00\u5c40\u7684\u52a8\u673a\u3002",
        )

    mapping = {
        "casual": "Rewards and feedback should lean on quick score climbs, short-round payoffs, and fast retries that make the loop feel immediately replayable.",
        "puzzle": "Rewards and feedback should come from visible board progress, satisfying chain reactions, and a clear sense of level completion.",
        "educational": "Rewards and feedback should come from instant correctness feedback, visible progress beats, and short celebratory milestones.",
        "funny": "Rewards and feedback should come from escalating punchlines, near-miss tension, and exaggerated success or failure animations.",
    }
    return mapping.get(
        normalized,
        "Rewards and feedback should make every round feel readable, rewarding, and easy to replay immediately.",
    )


def _localized_expand_visual_sentence(
    *,
    game_type: str,
    theme: str,
    visual_style: str,
    ui_language: str,
) -> str:
    normalized_game_type = str(game_type or "").strip().lower()
    normalized_theme = str(theme or "").strip().lower()
    theme_display = _EXPAND_PROMPT_THEME_DISPLAY.get(normalized_theme, {}).get(ui_language, "")
    if ui_language == "zh-CN":
        default_styles = {
            "casual": "\u6e05\u723d\u76f4\u89c2\u7684\u624b\u673a\u4f11\u95f2\u753b\u9762",
            "puzzle": "\u68cb\u76d8\u4fe1\u606f\u6e05\u695a\u7684\u76ca\u667a\u98ce\u683c",
            "educational": "\u4eb2\u5207\u3001\u6613\u8bfb\u7684\u6559\u5b66\u4e92\u52a8\u98ce\u683c",
            "funny": "\u8868\u60c5\u548c\u52a8\u4f5c\u5938\u5f20\u7684\u6f2b\u753b\u559c\u5267\u98ce",
        }
        visual_text = _normalize_free_text(visual_style) or default_styles.get(
            normalized_game_type,
            "\u6e05\u6670\u6613\u8bfb\u7684\u624b\u673a\u53cb\u597d\u753b\u9762",
        )
        if theme_display:
            return f"\u89c6\u89c9\u4e0a\u56f4\u7ed5{theme_display}\u5c55\u5f00\uff0c\u91c7\u7528{visual_text}\u7684\u8868\u73b0\u65b9\u5f0f\uff0c\u5e76\u4e14\u8ba9\u73a9\u5bb6\u4e00\u773c\u5c31\u80fd\u770b\u6e05\u89d2\u8272\u72b6\u6001\u3001\u76ee\u6807\u548c\u5371\u9669\u3002"
        return f"\u89c6\u89c9\u4e0a\u91c7\u7528{visual_text}\u7684\u8868\u73b0\u65b9\u5f0f\uff0c\u5e76\u4e14\u8ba9\u73a9\u5bb6\u4e00\u773c\u5c31\u80fd\u770b\u6e05\u89d2\u8272\u72b6\u6001\u3001\u76ee\u6807\u548c\u5371\u9669\u3002"

    default_styles = {
        "casual": "a bright, readable mobile arcade look",
        "puzzle": "a clean puzzle-first presentation with readable board state",
        "educational": "a warm, readable educational presentation",
        "funny": "an expressive comic presentation with exaggerated reactions",
    }
    visual_text = _normalize_free_text(visual_style) or default_styles.get(
        normalized_game_type,
        "a clean, mobile-friendly visual style",
    )
    if theme_display:
        return f"Visually, lean into {theme_display} with {visual_text}, and make the player state, goal, and threats readable at a glance on mobile."
    return f"Visually, use {visual_text} and make the player state, goal, and threats readable at a glance on mobile."


def _filter_user_facing_expand_rules(rules: list[str]) -> list[str]:
    filtered: list[str] = []
    for raw_rule in rules:
        rule = _normalize_free_text(str(raw_rule or ""))
        if not rule:
            continue
        lowered = rule.lower()
        if any(marker in lowered for marker in _EXPAND_PROMPT_META_RULE_MARKERS):
            continue
        if any(marker in rule for marker in _EXPAND_PROMPT_META_RULE_MARKERS):
            continue
        filtered.append(rule)
    return filtered


def _build_expand_prompt_notes_sentence(
    *,
    reference_game: str,
    special_rules: list[str],
    ui_language: str,
) -> str:
    normalized_reference = _normalize_free_text(reference_game)
    rules = _filter_user_facing_expand_rules(special_rules)
    if ui_language == "zh-CN":
        notes: list[str] = []
        for rule in rules[:3]:
            if "\u5173\u5361" in rule:
                notes.append(f"\u8bf7\u4fdd\u7559{rule}\uff0c\u5e76\u8ba9\u540e\u9762\u7684\u5185\u5bb9\u6709\u660e\u663e\u65b0\u53d8\u5316\u6216\u66f4\u9ad8\u538b\u529b\u3002")
                continue
            if "\u91cd\u65b0\u5f00\u59cb" in rule:
                notes.append("\u5931\u8d25\u540e\u8981\u80fd\u7acb\u5373\u91cd\u5f00\uff0c\u907f\u514d\u957f\u7b49\u5f85\u3002")
                continue
            if "\u70b9\u51fb\u5f00\u59cb" in rule:
                notes.append("\u5f00\u5c40\u8981\u80fd\u901a\u8fc7\u4e00\u6b21\u660e\u663e\u7684\u70b9\u51fb\u76f4\u63a5\u8fdb\u5165\u6e38\u620f\u3002")
                continue
            notes.append(f"\u53e6\u5916\uff0c\u8bf7\u4fdd\u7559\u8fd9\u4e2a\u660e\u786e\u8981\u6c42\uff1a{rule}\u3002")
        if normalized_reference:
            notes.append(f"\u53ef\u4ee5\u53c2\u8003\u300a{normalized_reference}\u300b\u7684\u8282\u594f\u6216\u624b\u611f\uff0c\u4f46\u4e0d\u8981\u76f4\u63a5\u7167\u642c\u3002")
        return " ".join(notes)

    notes = []
    for rule in rules[:3]:
        lowered = rule.lower()
        if "level" in lowered or "stage" in lowered:
            notes.append(f"Keep this explicit requirement in the build: {rule}, and make later content feel meaningfully escalated.")
            continue
        if "restart" in lowered:
            notes.append("Let the player restart immediately after failure instead of waiting through a long recovery.")
            continue
        if "tap to start" in lowered or "click to start" in lowered:
            notes.append("The game should enter play through one obvious tap from the opening screen.")
            continue
        notes.append(f"Also preserve this explicit requirement: {rule}.")
    if normalized_reference:
        notes.append(f"You can borrow pacing or feel cues from {normalized_reference}, but do not copy it directly.")
    return " ".join(notes)


def _build_expand_prompt_fallback(description: str) -> str:
    normalized = _normalize_free_text(str(description or ""))
    ui_language = _detect_ui_language(normalized)
    slots = _build_heuristic_slot_payload(
        raw_text=normalized,
        source_text=normalized,
        allow_sparse_fallback=True,
    )

    game_type = _localized_expand_game_type(str(slots.get("game_type") or ""), ui_language)
    input_method = _localized_expand_input_method(str(slots.get("input_method") or ""), ui_language)
    difficulty_phrase = _localized_expand_difficulty_phrase(str(slots.get("difficulty") or ""), ui_language)
    visual_sentence = _localized_expand_visual_sentence(
        game_type=str(slots.get("game_type") or ""),
        theme=str(slots.get("theme") or ""),
        visual_style=str(slots.get("visual_style") or ""),
        ui_language=ui_language,
    )
    reward_sentence = _localized_expand_reward_sentence(str(slots.get("game_type") or ""), ui_language)
    notes_sentence = _build_expand_prompt_notes_sentence(
        reference_game=str(slots.get("reference_game") or ""),
        special_rules=list(slots.get("special_rules") or []),
        ui_language=ui_language,
    )

    idea_excerpt = _short_expand_prompt_excerpt(normalized)
    core_mechanic = _normalize_free_text(str(slots.get("core_mechanic") or "")).rstrip(".\u3002\uff1b; ")
    win_condition = _normalize_free_text(str(slots.get("win_condition") or "")).rstrip(".\u3002\uff1b; ")

    if ui_language == "zh-CN":
        if not core_mechanic:
            core_mechanic = f"\u628a\u201c{idea_excerpt}\u201d\u53d8\u6210\u4e00\u4e2a\u4e0a\u624b\u51e0\u79d2\u5185\u5c31\u80fd\u770b\u61c2\u7684\u624b\u673a\u73a9\u6cd5"
        if not win_condition:
            win_condition = "\u5728\u4e00\u5c40\u5185\u5b8c\u6210\u660e\u786e\u76ee\u6807\uff0c\u5e76\u7ed9\u51fa\u6e05\u695a\u7684\u6210\u8d25\u53cd\u9988"
        lines = [
            f"\u8bf7\u751f\u6210\u4e00\u6b3e\u56f4\u7ed5\u201c{idea_excerpt}\u201d\u5c55\u5f00\u7684{game_type}\u3002",
            f"\u6838\u5fc3\u4f53\u9a8c\u805a\u7126\u5728{core_mechanic}\uff0c\u73a9\u5bb6\u4e3b\u8981\u901a\u8fc7{input_method}\u64cd\u4f5c\uff0c\u5e76\u4e14\u5728\u624b\u673a\u4e0a\u51e0\u79d2\u5185\u5c31\u80fd\u660e\u767d\u81ea\u5df1\u8981\u505a\u4ec0\u4e48\u3002",
            f"\u8fd9\u4e00\u5c40\u7684\u4e3b\u8981\u76ee\u6807\u662f{win_condition}\uff0c\u6574\u4f53\u8282\u594f{difficulty_phrase}\u3002",
            reward_sentence,
            visual_sentence,
        ]
        if notes_sentence:
            lines.append(notes_sentence)
        return "\n".join(lines)

    if not core_mechanic:
        core_mechanic = f'turn "{idea_excerpt}" into one clear, replayable mobile gameplay loop'
    if not win_condition:
        win_condition = "finish a short round with a clear success state and visible failure feedback"

    lines = [
        f'Create a compact {game_type} based on this idea: "{idea_excerpt}".',
        f"The core loop should focus on {core_mechanic}, using {input_method} as the main control so the player can understand the game almost immediately on mobile.",
        f"The round objective is to {win_condition}, and the pacing should {difficulty_phrase}.",
        reward_sentence,
        visual_sentence,
    ]
    if notes_sentence:
        lines.append(notes_sentence)
    return "\n".join(lines)


def _looks_like_low_quality_expand_prompt(text: str, description: str) -> bool:
    normalized = _normalize_free_text(text)
    if not normalized:
        return True

    requested_language = _detect_ui_language(description)
    has_chinese = bool(re.search(r"[\u4e00-\u9fff]", normalized))
    if requested_language == "zh-CN" and not has_chinese:
        return True
    if requested_language == "en-US" and has_chinese:
        return True

    lowered = normalized.lower()
    if any(marker in lowered or marker in normalized for marker in _EXPAND_PROMPT_TEMPLATE_MARKERS):
        return True

    heading_line_hits = sum(
        1
        for raw_line in normalized.splitlines()
        if any(
            _normalize_free_text(raw_line).lower().startswith(marker.lower())
            for marker in _EXPAND_PROMPT_INTERNAL_LABELS
        )
    )
    if heading_line_hits >= 3:
        return True

    internal_label_hits = sum(
        1 for marker in _EXPAND_PROMPT_INTERNAL_LABELS if marker in lowered or marker in normalized
    )
    generic_marker_hits = sum(
        1 for marker in _EXPAND_PROMPT_GENERIC_MARKERS if marker in lowered or marker in normalized
    )
    return internal_label_hits >= 3 or generic_marker_hits >= 2


@router.post("/expand-prompt")
async def expand_prompt(request: dict):
    """Expand a short user description into a detailed game design prompt."""
    from ...services.llm_client import LLMClient
    client = LLMClient()
    description = request.get("description", "")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")

    if not client.is_enabled():
        raise HTTPException(status_code=503, detail="Real LLM mode is required for prompt expansion")

    try:
        # Prompt expansion is a once-per-session, high-leverage creative call
        # (its output feeds every downstream generation step). Spend the
        # budget on the full-quality model and a larger token window so the
        # LLM can actually produce content-rich prose instead of short stubs
        # that trigger the low-quality fallback.
        text = await client.complete(
            max_tokens=1600,
            system=require_prompt("prompt.expand_prompt_system"),
            messages=[{"role": "user", "content": description}],
            step_key="expand_prompt",
            stage="prompt_expand",
            prefer_fast=False,
        )
        expanded_prompt = text.strip()
        if _looks_like_low_quality_expand_prompt(expanded_prompt, description):
            logger.warning("Using deterministic expand-prompt fallback after low-quality LLM output")
            return {
                "expanded_prompt": _build_expand_prompt_fallback(description),
                "fallback_used": True,
                "fallback_reason": "low_quality_llm_output",
            }
        return {"expanded_prompt": expanded_prompt}
    except Exception as e:
        logger.error(f"Prompt expansion failed: {e}")
        fallback = _build_expand_prompt_fallback(description)
        logger.warning("Using deterministic expand-prompt fallback after LLM failure")
        return {
            "expanded_prompt": fallback,
            "fallback_used": True,
            "fallback_reason": str(e),
        }


@router.post("/v2/creative-anchors")
async def creative_anchors_v2(request: dict):
    """PR-07: Merged expand_prompt + intent endpoint.

    Produces a CreativeAnchors structured brief in a single LLM call.
    Falls back to the deterministic builder on any error. Additive —
    clients may still call the legacy /expand-prompt endpoint.
    """
    from ...engine.creative_anchors import CreativeAnchors, build_anchors_fallback
    from ...services.llm_client import LLMClient

    description = (request or {}).get("description", "")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")

    client = LLMClient()
    if not client.is_enabled():
        # Deterministic fallback path — cheap, offline-safe.
        return build_anchors_fallback(description).model_dump()

    try:
        text = await client.complete(
            max_tokens=1024,
            system=(
                "You merge prompt expansion and intent parsing into a single "
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
            stage="prompt_expand",
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


# ===========================================================================
# Stages 02-06 – Full pipeline run
# ===========================================================================

@router.post("/pipeline/run", response_model=RunPipelineResponse)
async def run_pipeline(request: RunPipelineRequest) -> RunPipelineResponse:
    """Run stages 02-06: description → GameSpec → GDD → code → QA → HTML."""
    try:
        if _should_upgrade_legacy_pipeline_endpoints_to_v2():
            return await _run_pipeline_v2_internal(
                _upgrade_run_request_to_v2(request, source="pipeline_run")
            )
        return await _run_pipeline_internal(request)
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
    upgrade_to_v2 = _should_upgrade_legacy_pipeline_endpoints_to_v2()
    upgraded_request = (
        _upgrade_run_request_to_v2(request, source="pipeline_run_async")
        if upgrade_to_v2
        else None
    )
    timeout_s = _resolve_timeout_s(upgraded_request.timeout_s if upgraded_request else request.timeout_s)

    async def runner(task_id: str) -> RunPipelineResponse:
        try:
            if upgrade_to_v2 and upgraded_request is not None:
                return await _run_pipeline_v2_internal(
                    upgraded_request,
                    task_id=upgraded_request.task_id or task_id,
                )
            return await _run_pipeline_internal(request, task_id=request.task_id or task_id)
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
    )


# ===========================================================================
# Stage 07 – Iteration
# ===========================================================================

@router.post("/pipeline/iterate", response_model=IterateResponse)
async def pipeline_iterate(request: IterateRequest) -> IterateResponse:
    """Stage 07: incremental code modification from user feedback."""
    try:
        if _should_upgrade_legacy_pipeline_endpoints_to_v2():
            return await _run_iteration_v2_internal(
                _upgrade_iterate_request_to_v2(request, source="pipeline_iterate")
            )
        return await _run_iteration_internal(request)
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
    upgrade_to_v2 = _should_upgrade_legacy_pipeline_endpoints_to_v2()
    upgraded_request = (
        _upgrade_iterate_request_to_v2(request, source="pipeline_iterate_async")
        if upgrade_to_v2
        else None
    )
    timeout_s = _resolve_timeout_s(upgraded_request.timeout_s if upgraded_request else request.timeout_s)

    async def runner(task_id: str) -> IterateResponse:
        try:
            if upgrade_to_v2 and upgraded_request is not None:
                return await _run_iteration_v2_internal(
                    upgraded_request,
                    task_id=upgraded_request.task_id or task_id,
                )
            return await _run_iteration_internal(request, task_id=request.task_id or task_id)
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
    )


# ===========================================================================
# Pipeline V2 compatibility entrypoints
# ===========================================================================

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
async def run_pipeline_v2_async(request: RunPipelineV2Request) -> AsyncTaskHandleResponse:
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
async def pipeline_iterate_v2_async(request: IterateV2Request) -> AsyncTaskHandleResponse:
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
        if _should_upgrade_legacy_pipeline_endpoints_to_v2():
            pipeline_req = RunPipelineV2Request(
                game_id=request.game_id,
                user_id="system",
                raw_user_input=description,
                platform=request.platform,
                timeout_s=_resolve_timeout_s(request.timeout_s),
                request_context={
                    "source": "generate_code_legacy",
                    "entrypoint": "create",
                    "region": _resolve_v2_region(),
                    "pipeline_version": "v2",
                    "metadata": {
                        "compat_source": "generate_code_legacy",
                        "upgraded_from": "GenerateCodeRequest",
                    },
                },
                prompt_bundle_snapshot=_default_v2_prompt_bundle_snapshot(
                    entrypoint="create",
                    source="generate_code_legacy",
                ),
                runtime_contract=_default_v2_runtime_contract(
                    entrypoint="create",
                    source="generate_code_legacy",
                ),
                normalized_request={
                    "description": description,
                    "entrypoint": "create",
                    "compat_source": "generate_code_legacy",
                    "legacy_spec": request.spec.model_dump() if request.spec else None,
                },
                metadata={
                    "adapter": "compat_v1",
                    "compat_source": "generate_code_legacy",
                    "upgraded_from": "GenerateCodeRequest",
                },
            )
            result = await _run_pipeline_v2_internal(pipeline_req)
        else:
            pipeline_req = RunPipelineRequest(
                game_id=request.game_id,
                description=description,
                user_id="system",
                platform=request.platform,
                timeout_s=_resolve_timeout_s(request.timeout_s),
            )
            result = await _run_pipeline_internal(pipeline_req)
        elapsed = int((time.time() - start) * 1000)
        return GenerateCodeResponse(
            html_code=result.html_code,
            strategy=result.strategy,
            template_id=None,
            generation_time_ms=elapsed,
            code_size_bytes=result.code_size_bytes,
        )
    except HTTPException:
        raise
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
