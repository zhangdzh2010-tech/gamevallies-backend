"""Unified LLM client with DB-backed gateway routing and call logging."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import copy
from dataclasses import dataclass, field
import json
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from .llm_gateway import gateway, get_request_context
from .task_memory import task_memory

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


class LLMContextWindowExceededError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        estimated_input_tokens: int,
        allowed_input_tokens: int,
        context_window: Optional[int],
        compression_summary: Optional[list[str]] = None,
    ) -> None:
        super().__init__(message)
        self.estimated_input_tokens = estimated_input_tokens
        self.allowed_input_tokens = allowed_input_tokens
        self.context_window = context_window
        self.compression_summary = list(compression_summary or [])


class LLMProviderCapacityError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        step_key: str,
        required_output_tokens: int,
        candidate_caps: list[dict[str, Any]],
        response_size_hint: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.step_key = step_key
        self.required_output_tokens = required_output_tokens
        self.candidate_caps = list(candidate_caps)
        self.response_size_hint = response_size_hint


@dataclass
class PromptAdmissionResult:
    system: Optional[str]
    messages: List[Message]
    estimated_input_tokens: int
    requested_input_tokens: int
    allowed_input_tokens: Optional[int]
    reserved_output_tokens: int
    safety_margin_tokens: int
    task_memory_injected: bool = False
    task_memory_compact: bool = False
    compression_summary: list[str] = field(default_factory=list)
    strict_admission: bool = False
    limit_source: str = "caller_fallback"


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


def _looks_like_complete_html_document(text: str) -> bool:
    rendered = _strip_think_tags(text or "")
    if not rendered:
        return False

    lower = rendered.lower()
    if "<html" not in lower or "<body" not in lower:
        return False
    if "</body>" not in lower or "</html>" not in lower:
        return False

    if len(re.findall(r"<script\b", lower)) != len(re.findall(r"</script>", lower)):
        return False

    if len(re.findall(r"<style\b", lower)) != len(re.findall(r"</style>", lower)):
        return False

    return True


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


def _normalize_response_size_hint(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"small", "medium", "large", "xlarge"}:
        return normalized
    return "medium"


def _default_hint_tokens(response_size_hint: Optional[str]) -> int:
    normalized = _normalize_response_size_hint(response_size_hint)
    if normalized == "small":
        return 1024
    if normalized == "large":
        return 4096
    if normalized == "xlarge":
        return max(8192, settings.LLM_LONG_GENERATION_MAX_TOKENS)
    return 2048


def _required_output_floor(
    requested_max_tokens: Optional[int],
    response_size_hint: Optional[str],
) -> Optional[int]:
    normalized_hint = _normalize_response_size_hint(response_size_hint)
    if normalized_hint not in {"large", "xlarge"}:
        return None
    floor = _coerce_optional_int(requested_max_tokens)
    if normalized_hint == "xlarge":
        floor = max(
            floor or 0,
            max(
                settings.LLM_LONG_GENERATION_MAX_TOKENS,
                settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD,
            ),
        )
    return floor if floor and floor > 0 else None


def _contains_cjk(text: str) -> int:
    return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text or ""))


def _estimate_text_tokens(text: str, tokenizer_family: Optional[str] = None) -> int:
    normalized = text or ""
    if not normalized:
        return 0

    ascii_chars = len(re.findall(r"[\x00-\x7F]", normalized))
    cjk_chars = _contains_cjk(normalized)
    whitespace_chars = len(re.findall(r"\s", normalized))
    other_chars = max(0, len(normalized) - ascii_chars - cjk_chars)
    family = (tokenizer_family or "").strip().lower()

    if family.startswith("anthropic"):
        ascii_divisor = 3.5
        other_divisor = 2.1
        cjk_multiplier = 1.18
    else:
        ascii_divisor = 3.8
        other_divisor = 2.4
        cjk_multiplier = 1.10

    ascii_non_space = max(0, ascii_chars - whitespace_chars)
    estimate = (
        math.ceil(ascii_non_space / ascii_divisor)
        + math.ceil(other_chars / other_divisor)
        + math.ceil(cjk_chars * cjk_multiplier)
        + math.ceil(whitespace_chars / 12)
        + 4
    )
    return max(estimate, math.ceil(len(normalized.encode("utf-8")) / 8))


def _estimate_messages_tokens(system: Optional[str], messages: List[Message], tokenizer_family: Optional[str]) -> int:
    total = 0
    if system:
        total += _estimate_text_tokens(system, tokenizer_family) + 8
    for message in messages:
        total += _estimate_text_tokens(str(message.get("content") or ""), tokenizer_family) + 8
    return total


def _head_tail(text: str, *, head: int, tail: int) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= head + tail + 48:
        return normalized
    omitted = len(normalized) - head - tail
    return f"{normalized[:head].rstrip()}\n...[omitted {omitted} chars]...\n{normalized[-tail:].lstrip()}"


def _split_prompt_blocks(text: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n{2,}", text or "") if block.strip()]
    return blocks or [text.strip()]


def _looks_like_code_or_html(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "<!doctype html" in lower
        or "<html" in lower
        or "<script" in lower
        or "function " in lower
        or "const " in lower
        or "let " in lower
        or "```" in lower
    )


def _block_priority(block: str, compression_policy: str) -> int:
    lower = (block or "").lower()
    policy = (compression_policy or "generic").lower()
    score = 0
    if _looks_like_code_or_html(lower):
        score += 6 if policy in {"iteration_rewrite", "qa_fix"} else 2
    if "runtime contract" in lower or "contract" in lower:
        score += 5
    if "error" in lower or "repair" in lower:
        score += 5 if policy == "qa_fix" else 2
    if "feedback" in lower:
        score += 5 if policy == "iteration_rewrite" else 2
    if "spec" in lower or "structured design" in lower or "critical intent" in lower:
        score += 4
    if "reference skeleton" in lower or "enriched design" in lower:
        score -= 1
    if "history" in lower or "conversation" in lower:
        score -= 2
    return score


def _compress_prompt_text(
    text: str,
    *,
    compression_policy: str,
    aggressive: bool,
) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return normalized

    blocks = _split_prompt_blocks(normalized)
    if len(blocks) == 1:
        return _head_tail(normalized, head=700 if aggressive else 1100, tail=320 if aggressive else 520)

    ordered = sorted(
        enumerate(blocks),
        key=lambda item: (_block_priority(item[1], compression_policy), -item[0]),
        reverse=True,
    )
    keep_count = 3 if aggressive else 5
    kept_indexes = {index for index, _ in ordered[:keep_count]}
    rendered: list[str] = []
    omitted_blocks = 0
    for index, block in enumerate(blocks):
        if index in kept_indexes:
            rendered.append(block)
        else:
            omitted_blocks += 1

    if omitted_blocks:
        rendered.append(f"[omitted {omitted_blocks} lower-priority prompt blocks for context fit]")

    shortened = "\n\n".join(rendered).strip()
    return _head_tail(shortened, head=1400 if aggressive else 2200, tail=420 if aggressive else 620)


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


def _apply_route_max_tokens_limit(route: Any, requested_max_tokens: int) -> int:
    provider_max_tokens = _coerce_optional_int(getattr(route, "max_tokens", None))
    requested = max(1, int(requested_max_tokens))
    if provider_max_tokens is None:
        return requested
    return max(1, min(requested, provider_max_tokens))


def _resolve_gateway_output_limit(
    route: Any,
    *,
    requested_max_tokens: Optional[int],
    response_size_hint: Optional[str],
) -> tuple[int, int, str]:
    provider_max_tokens = _coerce_optional_int(getattr(route, "max_tokens", None))
    hint_tokens = _default_hint_tokens(response_size_hint)
    if provider_max_tokens is not None:
        requested = max(1, int(requested_max_tokens)) if requested_max_tokens is not None else hint_tokens
        return provider_max_tokens, requested, "gateway_provider_max"
    if requested_max_tokens is not None:
        return max(1, int(requested_max_tokens)), hint_tokens, "caller_fallback"
    return hint_tokens, hint_tokens, "hint_fallback"


def _is_timeout_like_error(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return True
    message = str(exc or "").lower()
    return "timed out" in message or "timeout" in message


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

    async def _prepare_prompt_admission(
        self,
        *,
        route: Any,
        system: Optional[str],
        messages: List[Message],
        step_key: str,
        compression_policy: Optional[str],
        context_scope: str,
        reserved_output_tokens: int,
        limit_source: str,
    ) -> PromptAdmissionResult:
        base_system = system.strip() if isinstance(system, str) else None
        admitted_messages = [dict(message) for message in messages]
        tokenizer_family = getattr(route, "tokenizer_family", None)
        context_window = _coerce_optional_int(getattr(route, "context_window", None))
        configured_safety_margin = _coerce_optional_int(getattr(route, "safety_margin_tokens", None))
        safety_margin_tokens = configured_safety_margin or (
            max(1024, int(context_window * 0.05))
            if context_window is not None
            else 1024
        )
        strict_admission = bool(
            getattr(route, "strict_admission", False)
            and context_window is not None
            and reserved_output_tokens > 0
        )

        task_memory_injected = False
        task_memory_compact = False
        compression_summary: list[str] = []
        effective_system = base_system

        context = get_request_context()
        task_id = context.get("task_id")
        if context_scope == "task" and task_id:
            memory_block = task_memory.build_prompt_block(
                task_id,
                step_key=step_key,
                compression_policy=compression_policy or "generic",
                compact=False,
            )
            if memory_block:
                task_memory_injected = True
                effective_system = "\n\n".join(part for part in [base_system, memory_block] if part).strip() or None

        requested_input_tokens = _estimate_messages_tokens(effective_system, admitted_messages, tokenizer_family)
        if not strict_admission:
            return PromptAdmissionResult(
                system=effective_system,
                messages=admitted_messages,
                estimated_input_tokens=requested_input_tokens,
                requested_input_tokens=requested_input_tokens,
                allowed_input_tokens=None,
                reserved_output_tokens=reserved_output_tokens,
                safety_margin_tokens=safety_margin_tokens,
                task_memory_injected=task_memory_injected,
                task_memory_compact=task_memory_compact,
                compression_summary=[],
                strict_admission=False,
                limit_source=limit_source,
            )

        allowed_input_tokens = max(512, context_window - reserved_output_tokens - safety_margin_tokens)
        estimated_input_tokens = requested_input_tokens
        if estimated_input_tokens <= allowed_input_tokens:
            return PromptAdmissionResult(
                system=effective_system,
                messages=admitted_messages,
                estimated_input_tokens=estimated_input_tokens,
                requested_input_tokens=requested_input_tokens,
                allowed_input_tokens=allowed_input_tokens,
                reserved_output_tokens=reserved_output_tokens,
                safety_margin_tokens=safety_margin_tokens,
                task_memory_injected=task_memory_injected,
                task_memory_compact=task_memory_compact,
                compression_summary=[],
                strict_admission=True,
                limit_source=limit_source,
            )

        if task_memory_injected and task_id:
            compact_memory = task_memory.build_prompt_block(
                task_id,
                step_key=step_key,
                compression_policy=compression_policy or "generic",
                compact=True,
            )
            if compact_memory:
                compact_system = "\n\n".join(part for part in [base_system, compact_memory] if part).strip() or None
                compact_tokens = _estimate_messages_tokens(compact_system, admitted_messages, tokenizer_family)
                if compact_tokens < estimated_input_tokens:
                    effective_system = compact_system
                    estimated_input_tokens = compact_tokens
                    task_memory_compact = True
                    compression_summary.append("compact_task_memory")

        while estimated_input_tokens > allowed_input_tokens and len(admitted_messages) > 1:
            drop_index = next(
                (
                    index
                    for index, message in enumerate(admitted_messages[:-1])
                    if str(message.get("role") or "").lower() == "assistant"
                ),
                0,
            )
            del admitted_messages[drop_index]
            compression_summary.append("drop_old_message")
            estimated_input_tokens = _estimate_messages_tokens(effective_system, admitted_messages, tokenizer_family)

        for index in range(max(0, len(admitted_messages) - 1)):
            if estimated_input_tokens <= allowed_input_tokens:
                break
            content = str(admitted_messages[index].get("content") or "")
            shortened = _compress_prompt_text(
                content,
                compression_policy=compression_policy or "generic",
                aggressive=False,
            )
            if shortened != content:
                admitted_messages[index]["content"] = shortened
                compression_summary.append("compress_history_message")
                estimated_input_tokens = _estimate_messages_tokens(effective_system, admitted_messages, tokenizer_family)

        if admitted_messages and estimated_input_tokens > allowed_input_tokens:
            last_content = str(admitted_messages[-1].get("content") or "")
            shortened_last = _compress_prompt_text(
                last_content,
                compression_policy=compression_policy or "generic",
                aggressive=True,
            )
            if shortened_last != last_content:
                admitted_messages[-1]["content"] = shortened_last
                compression_summary.append("compress_current_message")
                estimated_input_tokens = _estimate_messages_tokens(effective_system, admitted_messages, tokenizer_family)

        if effective_system and estimated_input_tokens > allowed_input_tokens:
            shortened_system = _compress_prompt_text(
                effective_system,
                compression_policy="generic",
                aggressive=True,
            )
            if shortened_system != effective_system:
                effective_system = shortened_system
                compression_summary.append("compress_system_context")
                estimated_input_tokens = _estimate_messages_tokens(effective_system, admitted_messages, tokenizer_family)

        if estimated_input_tokens > allowed_input_tokens:
            raise LLMContextWindowExceededError(
                (
                    f"Prompt for {step_key} exceeds provider context window after compression "
                    f"({estimated_input_tokens} input tokens > allowed {allowed_input_tokens}, context {context_window})"
                ),
                estimated_input_tokens=estimated_input_tokens,
                allowed_input_tokens=allowed_input_tokens,
                context_window=context_window,
                compression_summary=compression_summary,
            )

        return PromptAdmissionResult(
            system=effective_system,
            messages=admitted_messages,
            estimated_input_tokens=estimated_input_tokens,
            requested_input_tokens=requested_input_tokens,
            allowed_input_tokens=allowed_input_tokens,
            reserved_output_tokens=reserved_output_tokens,
            safety_margin_tokens=safety_margin_tokens,
            task_memory_injected=task_memory_injected,
            task_memory_compact=task_memory_compact,
            compression_summary=compression_summary,
            strict_admission=True,
            limit_source=limit_source,
        )

    async def complete(
        self,
        *,
        messages: List[Message],
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        model: Optional[str] = None,
        step_key: str = "default",
        stage: str = "llm",
        prefer_fast: bool = False,
        request_timeout_s: Optional[int] = None,
        overall_timeout_s: Optional[int] = None,
        allow_provider_fallback: bool = False,
        response_size_hint: Optional[str] = None,
        context_scope: str = "request",
        compression_policy: Optional[str] = None,
    ) -> str:
        if not self.is_enabled():
            raise RuntimeError("Real LLM mode is not configured")

        required_output_tokens = _required_output_floor(max_tokens, response_size_hint)
        routes = (
            gateway.resolve_candidates(
                step_key=step_key,
                prefer_fast=prefer_fast,
                model_override=model,
                allow_implicit_fallbacks=True,
                required_output_tokens=required_output_tokens,
            )
            if allow_provider_fallback and settings.LLM_PROVIDER_FAILOVER_ENABLED
            else [gateway.resolve(
                step_key=step_key,
                prefer_fast=prefer_fast,
                model_override=model,
            )]
        )

        if required_output_tokens is not None:
            known_caps = []
            known_capable = False
            unknown_capacity_present = False
            for route in routes:
                provider_cap = _coerce_optional_int(getattr(route, "max_tokens", None))
                known_caps.append(
                    {
                        "providerId": getattr(route, "provider_id", None),
                        "providerName": getattr(route, "provider_name", None),
                        "maxTokens": provider_cap,
                    }
                )
                if provider_cap is None:
                    unknown_capacity_present = True
                elif provider_cap >= required_output_tokens:
                    known_capable = True

            if not known_capable and not unknown_capacity_present:
                raise LLMProviderCapacityError(
                    (
                        f"Step {step_key} requires at least {required_output_tokens} output tokens "
                        f"for response size hint {_normalize_response_size_hint(response_size_hint)!r}, "
                        "but no routed provider can satisfy that budget"
                    ),
                    step_key=step_key,
                    required_output_tokens=required_output_tokens,
                    candidate_caps=known_caps,
                    response_size_hint=response_size_hint,
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
            effective_max_tokens, requested_max_tokens, limit_source = _resolve_gateway_output_limit(
                route,
                requested_max_tokens=max_tokens,
                response_size_hint=response_size_hint,
            )
            admission = await self._prepare_prompt_admission(
                route=route,
                system=system,
                messages=messages,
                step_key=step_key,
                compression_policy=compression_policy,
                context_scope=context_scope,
                reserved_output_tokens=effective_max_tokens,
                limit_source=limit_source,
            )
            route.route_snapshot = {
                **dict(getattr(route, "route_snapshot", {}) or {}),
                "attempt": attempt_index,
                "attempt_count": total_attempts,
                "provider_fallback_from": previous_provider_id,
                "requested_max_tokens": int(requested_max_tokens),
                "effective_max_tokens": int(effective_max_tokens),
                "provider_max_tokens": _coerce_optional_int(getattr(route, "max_tokens", None)),
                "provider_context_window": _coerce_optional_int(getattr(route, "context_window", None)),
                "limit_source": limit_source,
                "response_size_hint": _normalize_response_size_hint(response_size_hint),
                "context_scope": context_scope,
                "compression_policy": compression_policy,
                "requested_input_tokens": admission.requested_input_tokens,
                "estimated_input_tokens": admission.estimated_input_tokens,
                "allowed_input_tokens": admission.allowed_input_tokens,
                "reserved_output_tokens": admission.reserved_output_tokens,
                "safety_margin_tokens": admission.safety_margin_tokens,
                "strict_admission": admission.strict_admission,
                "task_memory_injected": admission.task_memory_injected,
                "task_memory_compact": admission.task_memory_compact,
                "compression_summary": list(admission.compression_summary),
                **(
                    {"overall_timeout_s": int(overall_timeout_s)}
                    if overall_timeout_s is not None
                    else {}
                ),
            }
            try:
                return await self._complete_with_route(
                    route=route,
                    messages=admission.messages,
                    max_tokens=effective_max_tokens,
                    system=admission.system,
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

    async def complete_with_truncation_retry(
        self,
        *,
        messages: List[Message],
        max_tokens: Optional[int] = None,
        system: Optional[str] = None,
        model: Optional[str] = None,
        step_key: str = "default",
        stage: str = "llm",
        prefer_fast: bool = False,
        request_timeout_s: Optional[int] = None,
        overall_timeout_s: Optional[int] = None,
        allow_provider_fallback: bool = False,
        truncation_retry_attempts: int = 1,
        truncation_retry_increment: int = 2048,
        truncation_retry_max_tokens: Optional[int] = None,
        truncation_retry_min_tokens: Optional[int] = None,
        timeout_retry_attempts: int = 0,
        timeout_retry_increment_s: int = 60,
        timeout_retry_max_s: Optional[int] = None,
        timeout_retry_min_s: Optional[int] = None,
        response_size_hint: Optional[str] = None,
        context_scope: str = "request",
        compression_policy: Optional[str] = None,
    ) -> str:
        resolved_route = None
        try:
            if self.is_enabled():
                resolved_route = gateway.resolve(
                    step_key=step_key,
                    prefer_fast=prefer_fast,
                    model_override=model,
                )
        except Exception:
            resolved_route = None

        if resolved_route is not None:
            gateway_max_tokens, initial_requested_max_tokens, limit_source = _resolve_gateway_output_limit(
                resolved_route,
                requested_max_tokens=max_tokens,
                response_size_hint=response_size_hint,
            )
        else:
            gateway_max_tokens = max(1, int(max_tokens)) if max_tokens is not None else _default_hint_tokens(response_size_hint)
            initial_requested_max_tokens = gateway_max_tokens
            limit_source = "caller_fallback" if max_tokens is not None else "hint_fallback"
        requested_max_tokens = initial_requested_max_tokens
        max_retry_attempts = max(0, int(truncation_retry_attempts))
        retry_increment = max(1, int(truncation_retry_increment))
        retry_ceiling = (
            max(1, int(truncation_retry_max_tokens))
            if truncation_retry_max_tokens is not None
            else None
        )
        if limit_source == "gateway_provider_max" and not allow_provider_fallback:
            retry_ceiling = gateway_max_tokens
        retry_floor = (
            max(1, int(truncation_retry_min_tokens))
            if truncation_retry_min_tokens is not None
            else None
        )
        timeout_retry_limit = max(0, int(timeout_retry_attempts))
        timeout_retry_increment = max(1, int(timeout_retry_increment_s))
        requested_request_timeout_s = (
            max(1, int(request_timeout_s))
            if request_timeout_s is not None
            else None
        )
        timeout_retry_ceiling = (
            max(1, int(timeout_retry_max_s))
            if timeout_retry_max_s is not None
            else None
        )
        timeout_retry_floor = (
            max(1, int(timeout_retry_min_s))
            if timeout_retry_min_s is not None
            else None
        )
        truncation_attempt = 0
        timeout_attempt = 0

        while True:
            current_overall_timeout_s = overall_timeout_s
            if current_overall_timeout_s is not None and requested_request_timeout_s is not None:
                current_overall_timeout_s = max(
                    int(current_overall_timeout_s),
                    int(requested_request_timeout_s),
                )
            try:
                return await self.complete(
                    messages=messages,
                    max_tokens=requested_max_tokens,
                    system=system,
                    model=model,
                    step_key=step_key,
                    stage=stage,
                    prefer_fast=prefer_fast,
                    request_timeout_s=requested_request_timeout_s,
                    overall_timeout_s=current_overall_timeout_s,
                    allow_provider_fallback=allow_provider_fallback,
                    response_size_hint=response_size_hint,
                    context_scope=context_scope,
                    compression_policy=compression_policy,
                )
            except LLMResponseTruncatedError as exc:
                if truncation_attempt >= max_retry_attempts:
                    raise
                next_requested = max(requested_max_tokens + retry_increment, retry_floor or 0)
                if retry_ceiling is not None:
                    next_requested = min(next_requested, retry_ceiling)
                if next_requested <= requested_max_tokens:
                    raise
                logger.warning(
                    "LLM %s response was truncated (stop_reason=%s, outputTokens=%s); retrying with larger budget (%s -> %s)",
                    step_key,
                    exc.stop_reason,
                    exc.output_tokens,
                    requested_max_tokens,
                    next_requested,
                )
                requested_max_tokens = next_requested
                truncation_attempt += 1
                continue
            except Exception as exc:
                if (
                    not _is_timeout_like_error(exc)
                    or timeout_attempt >= timeout_retry_limit
                    or requested_request_timeout_s is None
                ):
                    raise
                next_timeout_s = max(
                    requested_request_timeout_s + timeout_retry_increment,
                    timeout_retry_floor or 0,
                )
                if timeout_retry_ceiling is not None:
                    next_timeout_s = min(next_timeout_s, timeout_retry_ceiling)
                if next_timeout_s <= requested_request_timeout_s:
                    raise
                logger.warning(
                    "LLM %s timed out after %ss; retrying with longer timeout (%s -> %s)",
                    step_key,
                    requested_request_timeout_s,
                    requested_request_timeout_s,
                    next_timeout_s,
                )
                requested_request_timeout_s = next_timeout_s
                timeout_attempt += 1

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
            if _looks_like_complete_html_document(rendered):
                logger.warning(
                    "Anthropic-compatible provider reported max_tokens but returned a complete HTML document; accepting response"
                )
                return LLMCompletionResult(text=rendered, usage=usage_snapshot)
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
            if _looks_like_complete_html_document(text):
                logger.warning(
                    "OpenAI-compatible provider reported %s but returned a complete HTML document; accepting response",
                    finish_reason,
                )
                return LLMCompletionResult(text=text, usage=usage_snapshot)
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
