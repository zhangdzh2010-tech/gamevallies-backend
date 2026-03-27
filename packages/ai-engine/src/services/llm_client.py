"""Unified LLM client with DB-backed gateway routing and call logging."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import copy
from dataclasses import dataclass, field
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from .llm_gateway import gateway

logger = logging.getLogger(__name__)

Message = Dict[str, str]


@dataclass
class LLMUsageSnapshot:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMCompletionResult:
    text: str
    usage: LLMUsageSnapshot = field(default_factory=LLMUsageSnapshot)


class OpenAICompatibleResponseParseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        response_excerpt: Optional[str] = None,
        upstream_request_id: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.response_excerpt = response_excerpt
        self.upstream_request_id = upstream_request_id
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens


class EmptyOpenAICompatibleTextError(OpenAICompatibleResponseParseError):
    """Provider returned a syntactically valid payload, but no usable text."""


class LLMResponseTruncatedError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        response_excerpt: Optional[str] = None,
        upstream_request_id: Optional[str] = None,
        stop_reason: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.response_excerpt = response_excerpt
        self.upstream_request_id = upstream_request_id
        self.stop_reason = stop_reason
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens


def _build_openai_compatible_chat_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("LLM base URL is empty")

    if normalized.endswith("/chat/completions"):
        return normalized

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    if host in {"api.minimaxi.com", "api.minimax.io"} and not path:
        logger.warning("MiniMax base URL missing /v1, auto-normalizing to /v1/chat/completions")
        normalized = urlunparse(parsed._replace(path="/v1"))

    return f"{normalized.rstrip('/')}/chat/completions"


def _build_anthropic_base_url(base_url: Optional[str]) -> Optional[str]:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return None

    parsed = urlparse(normalized)
    path = parsed.path.rstrip("/")

    if path.endswith("/messages"):
        path = path[: -len("/messages")]
    if path.endswith("/v1"):
        path = path[: -len("/v1")]

    normalized_url = urlunparse(parsed._replace(path=path, params="", query="", fragment=""))
    return normalized_url.rstrip("/") or None


def _strip_think_tags(text: str) -> str:
    text = re.sub(r"<think(?:ing)?[^>]*>.*?</think(?:ing)?>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\[thinking\].*?\[/thinking\]", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _extract_openai_text_fragment(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        text = "".join(_extract_openai_text_fragment(item) for item in value).strip()
        return text
    if isinstance(value, dict):
        for key in ("text", "output_text", "content", "value"):
            text = _extract_openai_text_fragment(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, (int, float, bool)):
        return str(value).strip()
    return ""


def _extract_openai_message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""

    for key in ("content", "reasoning_content"):
        text = _extract_openai_text_fragment(message.get(key))
        if text:
            return _strip_think_tags(text)

    return ""


def _extract_openai_choice_text(choice: Any) -> str:
    if not isinstance(choice, dict):
        return ""

    message_text = _extract_openai_message_text(choice.get("message"))
    if message_text:
        return message_text

    for key in ("text", "output_text"):
        text = _extract_openai_text_fragment(choice.get(key))
        if text:
            return _strip_think_tags(text)

    delta = choice.get("delta")
    if isinstance(delta, dict):
        delta_text = _extract_openai_message_text(delta)
        if delta_text:
            return delta_text

    return ""


def _summarize_openai_response_excerpt(data: Any, limit: int = 1000) -> str:
    try:
        rendered = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        rendered = str(data)
    return rendered[:limit]


def _response_request_id(headers: httpx.Headers) -> Optional[str]:
    for key in ("x-request-id", "request-id", "x-trace-id", "trace-id"):
        value = headers.get(key)
        if value:
            return value
    return None


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _extract_openai_usage(data: Any) -> LLMUsageSnapshot:
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return LLMUsageSnapshot()

    input_tokens = _coerce_optional_int(usage.get("prompt_tokens"))
    if input_tokens is None:
        input_tokens = _coerce_optional_int(usage.get("input_tokens"))

    output_tokens = _coerce_optional_int(usage.get("completion_tokens"))
    if output_tokens is None:
        output_tokens = _coerce_optional_int(usage.get("output_tokens"))

    total_tokens = _coerce_optional_int(usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return LLMUsageSnapshot(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        raw=usage,
    )


def _extract_anthropic_usage(usage: Any) -> LLMUsageSnapshot:
    if usage is None:
        return LLMUsageSnapshot()

    raw = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = getattr(usage, key, None)
        if value is not None:
            raw[key] = value

    base_input_tokens = _coerce_optional_int(getattr(usage, "input_tokens", None))
    cache_creation_tokens = _coerce_optional_int(getattr(usage, "cache_creation_input_tokens", None)) or 0
    cache_read_tokens = _coerce_optional_int(getattr(usage, "cache_read_input_tokens", None)) or 0
    output_tokens = _coerce_optional_int(getattr(usage, "output_tokens", None))

    input_tokens = None
    if base_input_tokens is not None:
        input_tokens = base_input_tokens + cache_creation_tokens + cache_read_tokens

    total_tokens = None
    if input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return LLMUsageSnapshot(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        raw=raw,
    )


def _normalize_completion_result(result: Any) -> LLMCompletionResult:
    if isinstance(result, LLMCompletionResult):
        return result
    if isinstance(result, str):
        return LLMCompletionResult(text=result)
    raise TypeError(f"Unsupported LLM completion result type: {type(result)!r}")


def _is_anthropic_protocol_mismatch(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    if exc.response.status_code != 400:
        return False

    body = (exc.response.text or "").casefold()
    return (
        "anthropic-compatible" in body
        and "/v1/chat/completions" in body
    )


def _summarize_error_message(message: str, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", (message or "")).strip()[:limit] or "unknown error"


def _is_retryable_provider_error(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException, httpx.RequestError)):
        return True
    if isinstance(exc, LLMResponseTruncatedError):
        return True
    if isinstance(exc, OpenAICompatibleResponseParseError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 409, 425, 429} or exc.response.status_code >= 500
    return False


def _apply_request_timeout_override(route: Any, request_timeout_s: Optional[int]) -> Any:
    if request_timeout_s is None:
        return route

    overridden = copy(route)
    desired_timeout = max(1, int(request_timeout_s))
    current_timeout = getattr(route, "request_timeout_s", desired_timeout)
    overridden.request_timeout_s = min(int(current_timeout), desired_timeout)
    overridden.route_snapshot = {
        **dict(getattr(route, "route_snapshot", {}) or {}),
        "request_timeout_override_s": overridden.request_timeout_s,
    }
    return overridden


class LLMClient:
    """Small wrapper around the configured real-model provider."""

    def __init__(self) -> None:
        self._anthropic = None

    def provider(self) -> Optional[str]:
        if settings.LLM_API_KEY and settings.LLM_BASE_URL:
            return "openai_compatible"
        if settings.ANTHROPIC_API_KEY:
            return "anthropic"
        if gateway.has_enabled_provider():
            route = gateway.resolve(step_key="default")
            return route.provider_type
        return None

    def is_enabled(self) -> bool:
        return settings.LLM_MODE == "real" and gateway.has_enabled_provider()

    def model_for(self, fast: bool = False) -> str:
        if self.provider() == "openai_compatible":
            if fast and settings.LLM_FAST_MODEL:
                return settings.LLM_FAST_MODEL
            return settings.LLM_MODEL
        if fast:
            return settings.CLAUDE_FAST_MODEL
        return settings.CLAUDE_MODEL

    async def _emit_task_activity(
        self,
        *,
        route: Any,
        stage: str,
        step_key: str,
        state: str,
        elapsed_ms: int,
        error_message: Optional[str] = None,
    ) -> None:
        provider_label = route.provider_name or route.provider_type or "LLM"
        if state == "started":
            message = f"{step_key} 正在调用 {provider_label}"
        elif state == "heartbeat":
            message = f"{step_key} 仍在调用 {provider_label}（已等待 {max(1, elapsed_ms // 1000)}s）"
        elif state == "completed":
            message = f"{step_key} 调用完成（{max(1, elapsed_ms // 1000)}s）"
        else:
            message = f"{step_key} 调用失败：{_summarize_error_message(error_message or '')}"

        await gateway.emit_task_activity({
            "stage": stage,
            "stepKey": step_key,
            "message": message,
            "details": {
                "activityState": state,
                "stepKey": step_key,
                "providerId": route.provider_id,
                "providerName": route.provider_name,
                "providerType": route.provider_type,
                "region": route.region,
                "model": route.model,
                "elapsedMs": elapsed_ms,
                "requestTimeoutS": route.request_timeout_s,
                "connectTimeoutS": route.connect_timeout_s,
                "errorMessage": _summarize_error_message(error_message or "") if error_message else None,
            },
        })

    async def _heartbeat_loop(
        self,
        *,
        route: Any,
        stage: str,
        step_key: str,
        started_at: float,
        stop_event: asyncio.Event,
    ) -> None:
        heartbeat_timeout_s = get_timeout_int(
            "timeout.ai_engine.llm_activity_heartbeat_s",
            15,
            min_value=1,
        )
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=heartbeat_timeout_s)
                break
            except asyncio.TimeoutError:
                await self._emit_task_activity(
                    route=route,
                    stage=stage,
                    step_key=step_key,
                    state="heartbeat",
                    elapsed_ms=int((time.time() - started_at) * 1000),
                )

    async def complete(
        self,
        *,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str] = None,
        model: Optional[str] = None,
        step_key: str = "default",
        stage: str = "llm",
        prefer_fast: bool = False,
        request_timeout_s: Optional[int] = None,
        overall_timeout_s: Optional[int] = None,
        allow_provider_fallback: bool = False,
    ) -> str:
        if not self.is_enabled():
            raise RuntimeError("Real LLM mode is not configured")

        routes = (
            gateway.resolve_candidates(
                step_key=step_key,
                prefer_fast=prefer_fast,
                model_override=model,
            )
            if allow_provider_fallback and settings.LLM_PROVIDER_FAILOVER_ENABLED
            else [gateway.resolve(
                step_key=step_key,
                prefer_fast=prefer_fast,
                model_override=model,
            )]
        )

        last_exc: Optional[Exception] = None
        previous_provider_id: Optional[str] = None
        total_attempts = len(routes)
        deadline = None
        if overall_timeout_s is not None:
            deadline = time.monotonic() + max(1, int(overall_timeout_s))
        for attempt_index, resolved_route in enumerate(routes, start=1):
            effective_request_timeout_s = request_timeout_s
            if deadline is not None:
                remaining_budget_s = int(deadline - time.monotonic())
                if remaining_budget_s <= 0:
                    raise asyncio.TimeoutError(
                        f"LLM call {step_key} exhausted overall timeout budget of {int(overall_timeout_s)}s"
                    )
                effective_request_timeout_s = min(
                    max(1, remaining_budget_s),
                    effective_request_timeout_s if effective_request_timeout_s is not None else max(1, remaining_budget_s),
                )

            route = _apply_request_timeout_override(resolved_route, effective_request_timeout_s)
            route.route_snapshot = {
                **dict(getattr(route, "route_snapshot", {}) or {}),
                "attempt": attempt_index,
                "attempt_count": total_attempts,
                "provider_fallback_from": previous_provider_id,
                **(
                    {"overall_timeout_s": int(overall_timeout_s)}
                    if overall_timeout_s is not None
                    else {}
                ),
            }
            try:
                return await self._complete_with_route(
                    route=route,
                    messages=messages,
                    max_tokens=max_tokens,
                    system=system,
                    step_key=step_key,
                    stage=stage,
                )
            except Exception as exc:
                last_exc = exc
                if attempt_index >= total_attempts or not _is_retryable_provider_error(exc):
                    raise
                logger.warning(
                    "LLM call %s failed on provider %s (attempt %s/%s), trying fallback: %s",
                    step_key,
                    route.provider_name or route.provider_id or route.base_url,
                    attempt_index,
                    total_attempts,
                    exc,
                )
                previous_provider_id = route.provider_id

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"LLM call failed without attempts for step {step_key}")

    async def _complete_with_route(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
        step_key: str,
        stage: str,
    ) -> str:
        started_at = time.time()
        stop_event = asyncio.Event()
        await self._emit_task_activity(
            route=route,
            stage=stage,
            step_key=step_key,
            state="started",
            elapsed_ms=0,
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                route=route,
                stage=stage,
                step_key=step_key,
                started_at=started_at,
                stop_event=stop_event,
            )
        )

        try:
            effective_provider_type = route.provider_type
            route_snapshot = dict(route.route_snapshot or {})
            completion_result: LLMCompletionResult
            if route.provider_type == "anthropic":
                completion_result = _normalize_completion_result(await self._run_anthropic_with_timeout(
                    route=route,
                    messages=messages,
                    max_tokens=max_tokens,
                    system=system,
                ))
            elif route.provider_type == "openai_compatible":
                try:
                    completion_result = _normalize_completion_result(await self._complete_openai_compatible(
                        route=route,
                        messages=messages,
                        max_tokens=max_tokens,
                        system=system,
                    ))
                except Exception as exc:
                    if not _is_anthropic_protocol_mismatch(exc):
                        raise

                    logger.warning(
                        "Provider %s rejected OpenAI-compatible chat/completions; retrying with Anthropic protocol",
                        route.provider_name or route.provider_id or route.base_url,
                    )
                    effective_provider_type = "anthropic"
                    route_snapshot["protocol_fallback"] = "anthropic"
                    completion_result = _normalize_completion_result(await self._run_anthropic_with_timeout(
                        route=route,
                        messages=messages,
                        max_tokens=max_tokens,
                        system=system,
                    ))
            else:
                raise RuntimeError(f"Unsupported provider type: {route.provider_type}")

            latency_ms = int((time.time() - started_at) * 1000)
            await self._finish_heartbeat(stop_event=stop_event, heartbeat_task=heartbeat_task)
            await self._emit_task_activity(
                route=route,
                stage=stage,
                step_key=step_key,
                state="completed",
                elapsed_ms=latency_ms,
            )

            await gateway.emit_llm_call_log({
                "stage": stage,
                "stepKey": step_key,
                "providerId": route.provider_id,
                "providerName": route.provider_name,
                "providerType": effective_provider_type,
                "region": route.region,
                "model": route.model,
                "requestTimeoutS": route.request_timeout_s,
                "connectTimeoutS": route.connect_timeout_s,
                "latencyMs": latency_ms,
                "success": True,
                "inputTokens": completion_result.usage.input_tokens,
                "outputTokens": completion_result.usage.output_tokens,
                "totalTokens": completion_result.usage.total_tokens,
                "configVersion": route.config_version,
                "routeSnapshot": route_snapshot,
            })
            return completion_result.text
        except asyncio.CancelledError:
            latency_ms = int((time.time() - started_at) * 1000)
            await self._finish_heartbeat(stop_event=stop_event, heartbeat_task=heartbeat_task)
            await self._emit_task_activity(
                route=route,
                stage=stage,
                step_key=step_key,
                state="failed",
                elapsed_ms=latency_ms,
                error_message="LLM call canceled before completion",
            )
            await gateway.emit_llm_call_log({
                "stage": stage,
                "stepKey": step_key,
                "providerId": route.provider_id,
                "providerName": route.provider_name,
                "providerType": route.provider_type,
                "region": route.region,
                "model": route.model,
                "requestTimeoutS": route.request_timeout_s,
                "connectTimeoutS": route.connect_timeout_s,
                "latencyMs": latency_ms,
                "success": False,
                "errorCode": "CancelledError",
                "errorMessage": "LLM call canceled before completion",
                "configVersion": route.config_version,
                "routeSnapshot": route.route_snapshot,
            })
            raise
        except Exception as exc:
            http_status = None
            upstream_request_id = None
            error_body_excerpt = None
            input_tokens = None
            output_tokens = None
            total_tokens = None

            if isinstance(exc, httpx.HTTPStatusError):
                http_status = exc.response.status_code
                upstream_request_id = _response_request_id(exc.response.headers)
                error_body_excerpt = exc.response.text[:1000]
            elif isinstance(exc, LLMResponseTruncatedError):
                upstream_request_id = exc.upstream_request_id
                error_body_excerpt = exc.response_excerpt
                input_tokens = exc.input_tokens
                output_tokens = exc.output_tokens
                total_tokens = exc.total_tokens
            elif isinstance(exc, OpenAICompatibleResponseParseError):
                upstream_request_id = exc.upstream_request_id
                error_body_excerpt = exc.response_excerpt
                input_tokens = exc.input_tokens
                output_tokens = exc.output_tokens
                total_tokens = exc.total_tokens
            elif isinstance(exc, httpx.RequestError):
                error_body_excerpt = str(exc)

            latency_ms = int((time.time() - started_at) * 1000)
            await self._finish_heartbeat(stop_event=stop_event, heartbeat_task=heartbeat_task)
            await self._emit_task_activity(
                route=route,
                stage=stage,
                step_key=step_key,
                state="failed",
                elapsed_ms=latency_ms,
                error_message=str(exc),
            )

            await gateway.emit_llm_call_log({
                "stage": stage,
                "stepKey": step_key,
                "providerId": route.provider_id,
                "providerName": route.provider_name,
                "providerType": route.provider_type,
                "region": route.region,
                "model": route.model,
                "requestTimeoutS": route.request_timeout_s,
                "connectTimeoutS": route.connect_timeout_s,
                "latencyMs": latency_ms,
                "httpStatus": http_status,
                "success": False,
                "upstreamRequestId": upstream_request_id,
                "errorCode": exc.__class__.__name__,
                "errorMessage": str(exc),
                "errorBodyExcerpt": error_body_excerpt,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": total_tokens,
                "configVersion": route.config_version,
                "routeSnapshot": route.route_snapshot,
            })
            raise

    async def _finish_heartbeat(
        self,
        *,
        stop_event: asyncio.Event,
        heartbeat_task: asyncio.Task[Any],
    ) -> None:
        stop_event.set()
        if heartbeat_task.done():
            with suppress(Exception, asyncio.CancelledError):
                await heartbeat_task
            return
        with suppress(Exception, asyncio.CancelledError):
            await heartbeat_task

    async def _run_anthropic_with_timeout(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
    ) -> LLMCompletionResult:
        timeout_s = max(1, int(getattr(route, "request_timeout_s", 0) or 1))
        try:
            return await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._complete_anthropic(
                        route=route,
                        messages=messages,
                        max_tokens=max_tokens,
                        system=system,
                    ),
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise asyncio.TimeoutError(
                f"Anthropic-compatible request timed out after {timeout_s}s"
            ) from exc

    def _get_anthropic_client(self, *, api_key: str, base_url: Optional[str]):
        import anthropic
        return anthropic.Anthropic(
            api_key=api_key,
            base_url=_build_anthropic_base_url(base_url),
        )

    def _complete_anthropic(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
    ) -> LLMCompletionResult:
        client = self._get_anthropic_client(
            api_key=route.api_key,
            base_url=route.base_url,
        )
        kwargs = {
            "model": route.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)
        parts = []
        for block in response.content:
            text = getattr(block, "text", "")
            if text:
                parts.append(text)
        rendered = _strip_think_tags("\n".join(parts).strip())
        stop_reason = str(getattr(response, "stop_reason", "") or "").strip().lower()
        usage_snapshot = _extract_anthropic_usage(getattr(response, "usage", None))
        if stop_reason == "max_tokens":
            raise LLMResponseTruncatedError(
                "Anthropic response hit max_tokens and may be truncated",
                response_excerpt=rendered[:1000] or None,
                upstream_request_id=getattr(response, "id", None),
                stop_reason=stop_reason,
                input_tokens=usage_snapshot.input_tokens,
                output_tokens=usage_snapshot.output_tokens,
                total_tokens=usage_snapshot.total_tokens,
            )
        return LLMCompletionResult(text=rendered, usage=usage_snapshot)

    async def _complete_openai_compatible(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
    ) -> LLMCompletionResult:
        payload_messages: List[Message] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)

        payload = {
            "model": route.model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {route.api_key}",
            "Content-Type": "application/json",
        }
        url = _build_openai_compatible_chat_url(route.base_url)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(route.request_timeout_s, connect=route.connect_timeout_s),
        ) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            upstream_request_id = _response_request_id(response.headers)
            try:
                data = response.json()
            except ValueError as exc:
                excerpt = (response.text or "")[:1000] or None
                logger.error("OpenAI-compatible provider returned non-JSON payload: %s", excerpt)
                raise OpenAICompatibleResponseParseError(
                    "OpenAI-compatible response was not valid JSON",
                    response_excerpt=excerpt,
                    upstream_request_id=upstream_request_id,
                ) from exc

        try:
            choice = data["choices"][0]
        except (KeyError, IndexError, TypeError) as exc:
            excerpt = _summarize_openai_response_excerpt(data)
            usage_snapshot = _extract_openai_usage(data)
            logger.error("Unexpected OpenAI-compatible response: %s", excerpt)
            raise OpenAICompatibleResponseParseError(
                "Unexpected LLM response payload",
                response_excerpt=excerpt,
                upstream_request_id=upstream_request_id,
                input_tokens=usage_snapshot.input_tokens,
                output_tokens=usage_snapshot.output_tokens,
                total_tokens=usage_snapshot.total_tokens,
            ) from exc

        text = _extract_openai_choice_text(choice)
        usage_snapshot = _extract_openai_usage(data)
        finish_reason = str(choice.get("finish_reason") or "").strip().lower()
        if finish_reason in {"length", "max_tokens"}:
            excerpt = text[:1000] or _summarize_openai_response_excerpt(data)
            raise LLMResponseTruncatedError(
                "OpenAI-compatible response hit the output length limit and may be truncated",
                response_excerpt=excerpt,
                upstream_request_id=upstream_request_id,
                stop_reason=finish_reason,
                input_tokens=usage_snapshot.input_tokens,
                output_tokens=usage_snapshot.output_tokens,
                total_tokens=usage_snapshot.total_tokens,
            )
        if not text:
            excerpt = _summarize_openai_response_excerpt(data)
            logger.error("OpenAI-compatible response contained no usable text: %s", excerpt)
            raise EmptyOpenAICompatibleTextError(
                "OpenAI-compatible response contained no usable text",
                response_excerpt=excerpt,
                upstream_request_id=upstream_request_id,
                input_tokens=usage_snapshot.input_tokens,
                output_tokens=usage_snapshot.output_tokens,
                total_tokens=usage_snapshot.total_tokens,
            )

        return LLMCompletionResult(text=text, usage=usage_snapshot)
