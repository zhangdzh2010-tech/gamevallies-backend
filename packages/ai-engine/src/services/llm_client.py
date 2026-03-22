"""Unified LLM client with DB-backed gateway routing and call logging."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from ..config.settings import settings
from .llm_gateway import gateway

logger = logging.getLogger(__name__)

Message = Dict[str, str]
LLM_ACTIVITY_HEARTBEAT_S = 15


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


def _strip_think_tags(text: str) -> str:
    text = re.sub(r"<think(?:ing)?[^>]*>.*?</think(?:ing)?>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"\[thinking\].*?\[/thinking\]", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _response_request_id(headers: httpx.Headers) -> Optional[str]:
    for key in ("x-request-id", "request-id", "x-trace-id", "trace-id"):
        value = headers.get(key)
        if value:
            return value
    return None


def _summarize_error_message(message: str, limit: int = 160) -> str:
    return re.sub(r"\s+", " ", (message or "")).strip()[:limit] or "unknown error"


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
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=LLM_ACTIVITY_HEARTBEAT_S)
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
    ) -> str:
        if not self.is_enabled():
            raise RuntimeError("Real LLM mode is not configured")

        route = gateway.resolve(
            step_key=step_key,
            prefer_fast=prefer_fast,
            model_override=model,
        )
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
            if route.provider_type == "anthropic":
                text = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._complete_anthropic(
                        route=route,
                        messages=messages,
                        max_tokens=max_tokens,
                        system=system,
                    ),
                )
            elif route.provider_type == "openai_compatible":
                text = await self._complete_openai_compatible(
                    route=route,
                    messages=messages,
                    max_tokens=max_tokens,
                    system=system,
                )
            else:
                raise RuntimeError(f"Unsupported provider type: {route.provider_type}")

            latency_ms = int((time.time() - started_at) * 1000)
            stop_event.set()
            await heartbeat_task
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
                "providerType": route.provider_type,
                "region": route.region,
                "model": route.model,
                "requestTimeoutS": route.request_timeout_s,
                "connectTimeoutS": route.connect_timeout_s,
                "latencyMs": latency_ms,
                "success": True,
                "configVersion": route.config_version,
                "routeSnapshot": route.route_snapshot,
            })
            return text
        except Exception as exc:
            http_status = None
            upstream_request_id = None
            error_body_excerpt = None

            if isinstance(exc, httpx.HTTPStatusError):
                http_status = exc.response.status_code
                upstream_request_id = _response_request_id(exc.response.headers)
                error_body_excerpt = exc.response.text[:1000]
            elif isinstance(exc, httpx.RequestError):
                error_body_excerpt = str(exc)

            latency_ms = int((time.time() - started_at) * 1000)
            stop_event.set()
            await heartbeat_task
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
                "configVersion": route.config_version,
                "routeSnapshot": route.route_snapshot,
            })
            raise

    def _get_anthropic_client(self, *, api_key: str, base_url: Optional[str]):
        import anthropic
        return anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url or None,
        )

    def _complete_anthropic(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
    ) -> str:
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
        return _strip_think_tags("\n".join(parts).strip())

    async def _complete_openai_compatible(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
    ) -> str:
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
            data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected OpenAI-compatible response: %s", data)
            raise ValueError("Unexpected LLM response payload") from exc

        if isinstance(content, list):
            text_parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            ]
            return _strip_think_tags("".join(text_parts).strip())

        return _strip_think_tags(str(content).strip())
