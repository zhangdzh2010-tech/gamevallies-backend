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
from typing import Optional, Any

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
    IterateV2Request,
    ListAsyncTasksResponse,
    ParseIntentRequest,
    ParseIntentResponse,
    QACheckRequest,
    QACheckResponse,
    RunPipelineRequest,
    RunPipelineResponse,
    RunPipelineV2Request,
)
from ...engine.dialogue_engine import DialogueEngine, _sessions
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
from ...config.settings import settings
from ...config.timeout_store import (
    cached_count as cached_timeout_count,
    get_float as get_timeout_float,
    get_int as get_timeout_int,
    refresh as refresh_timeout_cache,
)
from ...services.async_task_manager import task_manager
from ...services.llm_gateway import gateway, llm_request_context
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
    return get_timeout_int("timeout.pipeline.default_s", 1200, min_value=30, max_value=3600)


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


def _adapt_v2_run_request(request: RunPipelineV2Request) -> RunPipelineRequest:
    description = request.raw_user_input.strip() or str(
        request.normalized_request.get("description", "")
    ).strip()
    if not description:
        raise HTTPException(status_code=400, detail="raw_user_input is required")

    return RunPipelineRequest(
        game_id=request.game_id,
        description=description,
        user_id=request.user_id,
        platform=request.platform,
        timeout_s=_resolve_timeout_s(request.timeout_s),
        task_id=request.task_id,
    )


def _adapt_v2_iterate_request(request: IterateV2Request) -> IterateRequest:
    feedback = request.iteration_intent.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="iteration_intent.feedback is required")

    return IterateRequest(
        game_id=request.game_id,
        feedback=feedback,
        user_id=request.user_id,
        conversation=request.iteration_intent.conversation,
        current_code=request.current_code,
        timeout_s=_resolve_timeout_s(request.timeout_s),
        task_id=request.task_id,
    )


async def _persist_v2_request_artifacts(
    *,
    task_id: Optional[str],
    game_id: str,
    user_id: str,
    normalized_request: dict[str, Any],
    runtime_contract: dict[str, Any],
    prompt_bundle_snapshot: dict[str, Any],
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


def _extract_failure_context(exc: Exception, *, fallback_stage: str) -> dict[str, Any]:
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
                for artifact_id in [spec_artifact_id, profile_artifact_id, primary_artifact_id]
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
            },
            artifact_ids=[
                artifact_id
                for artifact_id in [*failure_artifact_ids, failure["primary_artifact_id"]]
                if artifact_id
            ],
        )
        _annotate_failure_exception(exc, failure)
        raise


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
        await _relay_stage_summary_to_game_service(
            task_id=effective_task_id,
            stage="completed",
            message="V2 iteration completed",
            percentage=100,
            details={
                "pipelineVersion": "v2",
                "entrypoint": resolved_request.request_context.entrypoint,
            },
            artifact_ids=[artifact_id for artifact_id in [profile_artifact_id, primary_artifact_id] if artifact_id],
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
            },
            artifact_ids=[
                artifact_id
                for artifact_id in [*failure_artifact_ids, failure["primary_artifact_id"]]
                if artifact_id
            ],
        )
        _annotate_failure_exception(exc, failure)
        raise


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
        raise HTTPException(status_code=503, detail="Real LLM mode is required for prompt expansion")

    try:
        text = await client.complete(
            max_tokens=1024,
            system=require_prompt("prompt.expand_prompt_system"),
            messages=[{"role": "user", "content": description}],
            step_key="expand_prompt",
            stage="prompt_expand",
            prefer_fast=True,
        )
        return {"expanded_prompt": text.strip()}
    except Exception as e:
        logger.error(f"Prompt expansion failed: {e}")
        raise HTTPException(status_code=502, detail=f"Prompt expansion failed: {e}") from e


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
