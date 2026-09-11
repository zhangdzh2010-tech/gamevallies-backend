"""Unified LLM client with DB-backed gateway routing and call logging."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import copy
from dataclasses import dataclass, field
import json
import hashlib
import logging
import math
import re
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from .llm_gateway import gateway, get_request_context
from .llm_http_evidence import failure_transport_evidence, transport_error_message
from .prompt_dedup import build_prompt_fingerprint, deep_dedupe_prompt
from .task_memory import task_memory

logger = logging.getLogger(__name__)

Message = Dict[str, str]


def _apply_model_output_mode(payload: dict, route: Any) -> None:
    """Use documented native DeepSeek fields only on the official endpoint."""
    if (urlparse(route.base_url).hostname == "api.deepseek.com"
            and str(route.model).lower().startswith("deepseek-v4-")):
        payload["thinking"] = {"type": settings.LLM_DEEPSEEK_V4_THINKING}


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
        partial_text: Optional[str] = None,
        upstream_request_id: Optional[str] = None,
        stop_reason: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.response_excerpt = response_excerpt
        self.partial_text = partial_text if partial_text is not None else response_excerpt
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
        prompt_fingerprint: Optional[str] = None,
        prompt_dedup_summary: Optional[list[str]] = None,
        prompt_dedup_saved_tokens_estimate: Optional[int] = None,
        prompt_dedup_removed_block_count: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.estimated_input_tokens = estimated_input_tokens
        self.allowed_input_tokens = allowed_input_tokens
        self.context_window = context_window
        self.compression_summary = list(compression_summary or [])
        self.prompt_fingerprint = prompt_fingerprint
        self.prompt_dedup_summary = list(prompt_dedup_summary or [])
        self.prompt_dedup_saved_tokens_estimate = prompt_dedup_saved_tokens_estimate
        self.prompt_dedup_removed_block_count = prompt_dedup_removed_block_count


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
    prompt_fingerprint: str = ""
    prompt_dedup_applied: bool = False
    prompt_dedup_base_input_tokens: int = 0
    prompt_dedup_saved_tokens_estimate: int = 0
    prompt_dedup_removed_block_count: int = 0
    prompt_dedup_removed_blocks: List[Dict[str, Any]] = field(default_factory=list)
    prompt_dedup_summary: list[str] = field(default_factory=list)


@dataclass
class _PreparedCompletionAttempt:
    route: Any
    messages: List[Message]
    system: Optional[str]
    max_tokens: int
    # P1.1 GAP-1a: sampling_profile carries diversity knobs (temperature,
    # top_p, top_k, frequency_penalty, presence_penalty, seed) from
    # DiversityPlanner → provider payload. Optional: when None, providers
    # fall back to their own defaults, preserving pre-P1 behavior.
    sampling_profile: Optional[Dict[str, Any]] = None


def _build_openai_compatible_chat_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ValueError("LLM base URL is empty")

    if normalized.endswith("/chat/completions"):
        return normalized

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    if host in {"api.minimaxi.com", "api.minimax.io", "api.deepseek.com"} and not path:
        logger.warning("%s base URL missing /v1, auto-normalizing to /v1/chat/completions", host)
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


def _is_retryable_provider_error(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.CancelledError):
        return True
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException, httpx.RequestError)):
        return True
    if isinstance(exc, LLMResponseTruncatedError):
        return True
    if isinstance(exc, OpenAICompatibleResponseParseError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 409, 425, 429} or exc.response.status_code >= 500
    return False


def _provider_retry_after_seconds(exc: Exception) -> Optional[float]:
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    retry_after = (exc.response.headers.get("retry-after") or "").strip()
    if not retry_after:
        return None
    try:
        return max(float(retry_after), 0.0)
    except ValueError:
        return None


def _provider_retry_backoff_seconds(
    exc: Exception,
    *,
    attempt: int,
    base_delay_s: float,
    max_delay_s: float,
) -> float:
    capped_base = max(0.5, float(base_delay_s))
    capped_max = max(capped_base, float(max_delay_s))
    computed = min(capped_max, capped_base * (2 ** max(0, int(attempt) - 1)))
    retry_after = _provider_retry_after_seconds(exc)
    if retry_after is not None:
        computed = max(computed, min(capped_max, retry_after))
    return computed


def _attach_attempt_chain_to_exception(
    exc: BaseException,
    routes: list[Any],
    *,
    current_route_snapshot: Optional[dict[str, Any]] = None,
) -> None:
    snapshots = [dict(getattr(route, "route_snapshot", {}) or {}) for route in routes]
    provider_ids = [
        str(snapshot.get("provider_id") or getattr(route, "provider_id", "") or "").strip()
        for route, snapshot in zip(routes, snapshots)
    ]
    provider_names = [
        str(snapshot.get("provider_name") or getattr(route, "provider_name", "") or "").strip()
        for route, snapshot in zip(routes, snapshots)
    ]
    merged_snapshot = dict(current_route_snapshot or getattr(exc, "route_snapshot", {}) or {})
    merged_snapshot["attempted_provider_ids"] = [provider_id for provider_id in provider_ids if provider_id]
    merged_snapshot["attempted_provider_names"] = [provider_name for provider_name in provider_names if provider_name]
    if snapshots:
        merged_snapshot["attempt_route_snapshots"] = snapshots
    try:
        setattr(exc, "route_snapshot", merged_snapshot)
    except Exception:
        pass


_LLM_HTTP_CLIENTS: dict[asyncio.AbstractEventLoop, httpx.AsyncClient] = {}
_LLM_HTTP_CLIENTS_LOCK = threading.Lock()
_LLM_CALL_SEMAPHORES: dict[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = {}
_LLM_CALL_SEMAPHORES_LOCK = threading.Lock()


def _llm_max_concurrency() -> int:
    return max(get_timeout_int("timeout.ai_engine.llm.max_concurrency", 10, min_value=1), 1)


def _llm_http_limits() -> httpx.Limits:
    max_connections = max(
        get_timeout_int("timeout.ai_engine.llm.http_max_connections", 100, min_value=1),
        1,
    )
    max_keepalive_connections = max(
        get_timeout_int(
            "timeout.ai_engine.llm.http_max_keepalive_connections",
            40,
            min_value=1,
        ),
        1,
    )
    keepalive_expiry_s = max(
        get_timeout_int("timeout.ai_engine.llm.http_keepalive_expiry_s", 30, min_value=1),
        1,
    )
    return httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=min(max_keepalive_connections, max_connections),
        keepalive_expiry=float(keepalive_expiry_s),
    )


def _llm_http_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    with _LLM_HTTP_CLIENTS_LOCK:
        client = _LLM_HTTP_CLIENTS.get(loop)
        if client is not None and not client.is_closed:
            return client
        client = httpx.AsyncClient(limits=_llm_http_limits())
        _LLM_HTTP_CLIENTS[loop] = client
        return client


def _llm_call_semaphore(max_concurrency: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    with _LLM_CALL_SEMAPHORES_LOCK:
        cached = _LLM_CALL_SEMAPHORES.get(loop)
        if cached is not None:
            cached_limit, semaphore = cached
            if cached_limit == max_concurrency:
                return semaphore
        semaphore = asyncio.Semaphore(max_concurrency)
        _LLM_CALL_SEMAPHORES[loop] = (max_concurrency, semaphore)
        return semaphore


# ── Output class taxonomy ──────────────────────────────────────────────
VALID_OUTPUT_CLASSES = frozenset({
    "small_text", "small_json", "medium_structured", "large_patch", "full_document",
})

_LEGACY_HINT_TO_OUTPUT_CLASS: dict[str, str] = {
    "small": "small_text",
    "medium": "medium_structured",
    "large": "large_patch",
    "xlarge": "full_document",
}

# Hardcoded step→output_class fallback when DB catalog is unavailable
STEP_OUTPUT_CLASS_DEFAULTS: dict[str, str] = {
    "intent_parse": "small_json",
    "iterate.classify": "small_json",
    "generate_game_spec": "small_json",
    "generate_game_spec.draft": "small_json",
    "code_review": "medium_structured",
    "iterate.param_adjust": "large_patch",
    "iterate.element_change": "large_patch",
    "iterate.mechanic_change": "large_patch",
    "qa_fix.syntax_structural": "full_document",
    "code_generate.full": "full_document",
}

OUTPUT_CLASS_TOKEN_DEFAULTS: dict[str, int] = {
    "small_text": 512,
    "small_json": 1024,
    "medium_structured": 2048,
    "large_patch": 4096,
    "full_document": 8192,
}

_ADAPTIVE_TOKEN_BUDGET_DISABLED_STEPS = frozenset({
    "code_generate.full",
    "qa_fix.syntax_structural",
})


def _normalize_output_class(value: Optional[str]) -> str:
    """Normalize output class, supporting both new 5-level and legacy 4-level hints."""
    normalized = (value or "").strip().lower()
    if normalized in VALID_OUTPUT_CLASSES:
        return normalized
    # Legacy backward compatibility
    legacy = _LEGACY_HINT_TO_OUTPUT_CLASS.get(normalized)
    if legacy:
        return legacy
    return "medium_structured"


def _default_hint_tokens(output_class: Optional[str]) -> int:
    """Return default token budget for an output class."""
    normalized = _normalize_output_class(output_class)
    if normalized == "full_document":
        return max(OUTPUT_CLASS_TOKEN_DEFAULTS["full_document"], settings.LLM_LONG_GENERATION_MAX_TOKENS)
    return OUTPUT_CLASS_TOKEN_DEFAULTS.get(normalized, 2048)


# Keep backward-compatible alias
def _normalize_response_size_hint(value: Optional[str]) -> str:
    """Deprecated: use _normalize_output_class instead."""
    return _normalize_output_class(value)


def _required_output_floor(
    requested_max_tokens: Optional[int],
    response_size_hint: Optional[str],
) -> Optional[int]:
    output_class = _normalize_output_class(response_size_hint)
    if output_class not in {"large_patch", "full_document"}:
        return None
    floor = _coerce_optional_int(requested_max_tokens)
    if output_class == "full_document":
        floor = max(
            floor or 0,
            max(
                settings.LLM_LONG_GENERATION_MAX_TOKENS,
                settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD,
            ),
        )
    return floor if floor and floor > 0 else None


from collections import deque

class _OutputTokenTracker:
    """Track recent output token usage per step_key for adaptive budgeting."""

    def __init__(self, max_history: int = 20):
        self._history: dict[str, deque] = {}
        self._max_history = max_history

    def record(self, step_key: str, output_tokens: int) -> None:
        if step_key not in self._history:
            self._history[step_key] = deque(maxlen=self._max_history)
        self._history[step_key].append(output_tokens)

    def suggest_budget(self, step_key: str, default: int) -> int:
        """Suggest token budget based on recent usage. Returns P90 * 1.3 or default."""
        history = self._history.get(step_key)
        if not history or len(history) < 3:
            return default
        sorted_vals = sorted(history)
        p90_idx = max(0, int(len(sorted_vals) * 0.9) - 1)
        p90 = sorted_vals[p90_idx]
        suggested = int(p90 * 1.3)
        # Never go below default, never above 2x default
        return max(default, min(suggested, default * 2))


_output_token_tracker = _OutputTokenTracker(
    max_history=getattr(settings, "LLM_ADAPTIVE_TOKEN_BUDGET_HISTORY_SIZE", 20),
)


def _adaptive_token_budget_enabled_for_step(step_key: str) -> bool:
    if not getattr(settings, "LLM_ADAPTIVE_TOKEN_BUDGET_ENABLED", False):
        return False
    return (step_key or "").strip().lower() not in _ADAPTIVE_TOKEN_BUDGET_DISABLED_STEPS


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
    if "reference skeleton" in lower:
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
    overridden.request_timeout_s = desired_timeout
    overridden.route_snapshot = {
        **dict(getattr(route, "route_snapshot", {}) or {}),
        "base_request_timeout_s": int(current_timeout),
        "request_timeout_override_s": overridden.request_timeout_s,
        "request_timeout_override_applied": True,
    }
    return overridden


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
        effective = min(requested, provider_max_tokens)
        if requested_max_tokens is None:
            limit_source = "hint_capped_by_gateway" if effective < requested else "hint_fallback"
        else:
            limit_source = "caller_capped_by_gateway" if effective < requested else "caller_requested"
        return effective, requested, limit_source
    if requested_max_tokens is not None:
        # The second value is the caller's request, not the hint default.
        # complete_with_truncation_retry uses it as its initial budget; returning
        # 512 here silently reduced a 2048-token structured review to 512.
        requested = max(1, int(requested_max_tokens))
        return requested, requested, "caller_fallback"
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
        effective_system = base_system
        prompt_dedup_applied = False
        prompt_dedup_base_input_tokens = 0
        prompt_dedup_saved_tokens_estimate = 0
        prompt_dedup_removed_block_count = 0
        prompt_dedup_removed_blocks: list[dict[str, Any]] = []
        prompt_dedup_summary: list[str] = []

        def _apply_prompt_dedup(
            current_system: Optional[str],
            current_messages: List[Message],
        ) -> tuple[Optional[str], List[Message], int, int, str, bool, int, list[dict[str, Any]], list[str]]:
            before_tokens = _estimate_messages_tokens(current_system, current_messages, tokenizer_family)
            deduped = deep_dedupe_prompt(
                system=current_system,
                messages=current_messages,
                compression_policy=compression_policy or "generic",
            )
            after_tokens = _estimate_messages_tokens(deduped.system, deduped.messages, tokenizer_family)
            return (
                deduped.system,
                deduped.messages,
                before_tokens,
                after_tokens,
                max(0, before_tokens - after_tokens),
                deduped.metrics.prompt_fingerprint,
                deduped.metrics.applied,
                deduped.metrics.removed_block_count,
                list(deduped.metrics.removed_blocks),
                list(deduped.metrics.summary),
            )

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

        (
            effective_system,
            admitted_messages,
            requested_input_tokens,
            deduped_input_tokens,
            prompt_dedup_saved_tokens_estimate,
            prompt_fingerprint,
            prompt_dedup_applied,
            prompt_dedup_removed_block_count,
            prompt_dedup_removed_blocks,
            prompt_dedup_summary,
        ) = _apply_prompt_dedup(effective_system, admitted_messages)
        prompt_dedup_base_input_tokens = requested_input_tokens
        compression_summary: list[str] = list(prompt_dedup_summary)

        if not strict_admission:
            return PromptAdmissionResult(
                system=effective_system,
                messages=admitted_messages,
                estimated_input_tokens=deduped_input_tokens,
                requested_input_tokens=requested_input_tokens,
                allowed_input_tokens=None,
                reserved_output_tokens=reserved_output_tokens,
                safety_margin_tokens=safety_margin_tokens,
                task_memory_injected=task_memory_injected,
                task_memory_compact=task_memory_compact,
                compression_summary=compression_summary,
                strict_admission=False,
                limit_source=limit_source,
                prompt_fingerprint=prompt_fingerprint,
                prompt_dedup_applied=prompt_dedup_applied,
                prompt_dedup_base_input_tokens=prompt_dedup_base_input_tokens,
                prompt_dedup_saved_tokens_estimate=prompt_dedup_saved_tokens_estimate,
                prompt_dedup_removed_block_count=prompt_dedup_removed_block_count,
                prompt_dedup_removed_blocks=prompt_dedup_removed_blocks,
                prompt_dedup_summary=prompt_dedup_summary,
            )

        allowed_input_tokens = max(512, context_window - reserved_output_tokens - safety_margin_tokens)
        estimated_input_tokens = deduped_input_tokens
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
                compression_summary=compression_summary,
                strict_admission=True,
                limit_source=limit_source,
                prompt_fingerprint=prompt_fingerprint,
                prompt_dedup_applied=prompt_dedup_applied,
                prompt_dedup_base_input_tokens=prompt_dedup_base_input_tokens,
                prompt_dedup_saved_tokens_estimate=prompt_dedup_saved_tokens_estimate,
                prompt_dedup_removed_block_count=prompt_dedup_removed_block_count,
                prompt_dedup_removed_blocks=prompt_dedup_removed_blocks,
                prompt_dedup_summary=prompt_dedup_summary,
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
                (
                    deduped_compact_system,
                    deduped_compact_messages,
                    compact_before_tokens,
                    compact_after_tokens,
                    compact_saved_tokens,
                    compact_prompt_fingerprint,
                    compact_prompt_dedup_applied,
                    compact_removed_block_count,
                    compact_removed_blocks,
                    compact_prompt_dedup_summary,
                ) = _apply_prompt_dedup(compact_system, admitted_messages)
                compact_tokens = compact_after_tokens
                if compact_tokens < estimated_input_tokens:
                    effective_system = deduped_compact_system
                    admitted_messages = deduped_compact_messages
                    estimated_input_tokens = compact_tokens
                    task_memory_compact = True
                    prompt_dedup_base_input_tokens = compact_before_tokens
                    prompt_dedup_saved_tokens_estimate = compact_saved_tokens
                    prompt_fingerprint = compact_prompt_fingerprint
                    prompt_dedup_applied = compact_prompt_dedup_applied
                    prompt_dedup_removed_block_count = compact_removed_block_count
                    prompt_dedup_removed_blocks = compact_removed_blocks
                    prompt_dedup_summary = compact_prompt_dedup_summary
                    compression_summary = list(prompt_dedup_summary)
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
            prompt_fingerprint = build_prompt_fingerprint(effective_system, admitted_messages)
            raise LLMContextWindowExceededError(
                (
                    f"Prompt for {step_key} exceeds provider context window after compression "
                    f"({estimated_input_tokens} input tokens > allowed {allowed_input_tokens}, context {context_window})"
                ),
                estimated_input_tokens=estimated_input_tokens,
                allowed_input_tokens=allowed_input_tokens,
                context_window=context_window,
                compression_summary=compression_summary,
                prompt_fingerprint=prompt_fingerprint,
                prompt_dedup_summary=prompt_dedup_summary,
                prompt_dedup_saved_tokens_estimate=prompt_dedup_saved_tokens_estimate,
                prompt_dedup_removed_block_count=prompt_dedup_removed_block_count,
            )

        prompt_fingerprint = build_prompt_fingerprint(effective_system, admitted_messages)
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
            prompt_fingerprint=prompt_fingerprint,
            prompt_dedup_applied=prompt_dedup_applied,
            prompt_dedup_base_input_tokens=prompt_dedup_base_input_tokens,
            prompt_dedup_saved_tokens_estimate=prompt_dedup_saved_tokens_estimate,
            prompt_dedup_removed_block_count=prompt_dedup_removed_block_count,
            prompt_dedup_removed_blocks=prompt_dedup_removed_blocks,
            prompt_dedup_summary=prompt_dedup_summary,
        )

    async def _prepare_completion_attempt(
        self,
        *,
        resolved_route: Any,
        attempt_index: int,
        total_attempts: int,
        previous_provider_id: Optional[str],
        request_timeout_s: Optional[int],
        overall_timeout_s: Optional[int],
        deadline: Optional[float],
        max_tokens: Optional[int],
        system: Optional[str],
        messages: List[Message],
        step_key: str,
        response_size_hint: Optional[str],
        context_scope: str,
        compression_policy: Optional[str],
        sampling_profile: Optional[Dict[str, Any]] = None,  # P1.1 GAP-1a
    ) -> _PreparedCompletionAttempt:
        effective_request_timeout_s = request_timeout_s
        if deadline is not None:
            remaining_budget_s = int(deadline - time.monotonic())
            if remaining_budget_s <= 0:
                raise asyncio.TimeoutError(
                    f"LLM call {step_key} exhausted overall timeout budget of {int(overall_timeout_s or 0)}s"
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
            "reserved_output_tokens": int(effective_max_tokens),
            **(
                {"overall_timeout_s": int(overall_timeout_s)}
                if overall_timeout_s is not None
                else {}
            ),
        }
        try:
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
        except LLMContextWindowExceededError as exc:
            route.route_snapshot = {
                **route.route_snapshot,
                "estimated_input_tokens": exc.estimated_input_tokens,
                "allowed_input_tokens": exc.allowed_input_tokens,
                "strict_admission": True,
                "compression_summary": list(exc.compression_summary),
                "prompt_fingerprint": exc.prompt_fingerprint,
                "prompt_dedup_summary": list(exc.prompt_dedup_summary),
                "prompt_dedup_saved_tokens_estimate": exc.prompt_dedup_saved_tokens_estimate,
                "prompt_dedup_removed_block_count": exc.prompt_dedup_removed_block_count,
            }
            try:
                setattr(exc, "route_snapshot", dict(route.route_snapshot or {}))
            except Exception:
                pass
            raise
        route.route_snapshot = {
            **route.route_snapshot,
            "requested_input_tokens": admission.requested_input_tokens,
            "estimated_input_tokens": admission.estimated_input_tokens,
            "allowed_input_tokens": admission.allowed_input_tokens,
            "reserved_output_tokens": admission.reserved_output_tokens,
            "safety_margin_tokens": admission.safety_margin_tokens,
            "strict_admission": admission.strict_admission,
            "task_memory_injected": admission.task_memory_injected,
            "task_memory_compact": admission.task_memory_compact,
            "compression_summary": list(admission.compression_summary),
            "prompt_fingerprint": admission.prompt_fingerprint,
            "prompt_dedup_applied": admission.prompt_dedup_applied,
            "prompt_dedup_base_input_tokens": admission.prompt_dedup_base_input_tokens,
            "prompt_dedup_saved_tokens_estimate": admission.prompt_dedup_saved_tokens_estimate,
            "prompt_dedup_removed_block_count": admission.prompt_dedup_removed_block_count,
            "prompt_dedup_removed_blocks": list(admission.prompt_dedup_removed_blocks),
            "prompt_dedup_summary": list(admission.prompt_dedup_summary),
        }
        # P1.1 GAP-1a: log sampling_profile into route_snapshot for diagnostics.
        if sampling_profile:
            route.route_snapshot = {
                **route.route_snapshot,
                "sampling_profile": dict(sampling_profile),
            }
        return _PreparedCompletionAttempt(
            route=route,
            messages=admission.messages,
            system=admission.system,
            max_tokens=effective_max_tokens,
            sampling_profile=sampling_profile,  # P1.1 GAP-1a
        )

    async def _run_prepared_completion_attempt(
        self,
        *,
        prepared: _PreparedCompletionAttempt,
        step_key: str,
        stage: str,
        return_route_snapshot: bool,
    ) -> Any:
        text = await self._complete_with_route(
            route=prepared.route,
            messages=prepared.messages,
            max_tokens=prepared.max_tokens,
            system=prepared.system,
            step_key=step_key,
            stage=stage,
            sampling_profile=prepared.sampling_profile,  # P1.1 GAP-1a
        )
        if return_route_snapshot:
            return text, dict(prepared.route.route_snapshot or {})
        return text

    async def _complete_with_hedged_routes(
        self,
        *,
        routes: list[Any],
        hedge_after_s: float,
        messages: List[Message],
        max_tokens: Optional[int],
        system: Optional[str],
        step_key: str,
        stage: str,
        request_timeout_s: Optional[int],
        overall_timeout_s: Optional[int],
        response_size_hint: Optional[str],
        context_scope: str,
        compression_policy: Optional[str],
        return_route_snapshot: bool,
        sampling_profile: Optional[Dict[str, Any]] = None,  # P1.1 GAP-1a
    ) -> Any:
        total_attempts = len(routes)
        deadline = None
        if overall_timeout_s is not None:
            deadline = time.monotonic() + max(1, int(overall_timeout_s))

        launched_routes: list[Any] = []
        task_to_route: dict[asyncio.Task[Any], Any] = {}
        pending_tasks: set[asyncio.Task[Any]] = set()
        first_task: Optional[asyncio.Task[Any]] = None
        next_route_index = 0
        last_exc: Optional[BaseException] = None
        hedge_launched = False

        async def _launch_attempt(route_index: int) -> asyncio.Task[Any]:
            previous_provider_id = None
            if launched_routes:
                previous_provider_id = str(getattr(launched_routes[-1], "provider_id", "") or "").strip() or None
            prepared = await self._prepare_completion_attempt(
                resolved_route=routes[route_index],
                attempt_index=route_index + 1,
                total_attempts=total_attempts,
                previous_provider_id=previous_provider_id,
                request_timeout_s=request_timeout_s,
                overall_timeout_s=overall_timeout_s,
                deadline=deadline,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                step_key=step_key,
                response_size_hint=response_size_hint,
                context_scope=context_scope,
                compression_policy=compression_policy,
                sampling_profile=sampling_profile,  # P1.1 GAP-1a
            )
            launched_routes.append(prepared.route)
            task = asyncio.create_task(
                self._run_prepared_completion_attempt(
                    prepared=prepared,
                    step_key=step_key,
                    stage=stage,
                    return_route_snapshot=return_route_snapshot,
                )
            )
            pending_tasks.add(task)
            task_to_route[task] = prepared.route
            return task

        try:
            first_task = await _launch_attempt(0)
            next_route_index = 1
            hedge_deadline = time.monotonic() + max(0.0, float(hedge_after_s))

            while pending_tasks:
                wait_timeout: Optional[float] = None
                if (
                    not hedge_launched
                    and first_task is not None
                    and first_task in pending_tasks
                    and next_route_index < total_attempts
                ):
                    wait_timeout = max(0.0, hedge_deadline - time.monotonic())

                done, _ = await asyncio.wait(
                    pending_tasks,
                    timeout=wait_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done:
                    await _launch_attempt(next_route_index)
                    next_route_index += 1
                    hedge_launched = True
                    continue

                for task in list(done):
                    pending_tasks.discard(task)
                    route = task_to_route.pop(task, None)
                    try:
                        result = task.result()
                    except BaseException as exc:
                        last_exc = exc
                        if route is not None:
                            _attach_attempt_chain_to_exception(exc, launched_routes)
                        if not pending_tasks and next_route_index < total_attempts:
                            await _launch_attempt(next_route_index)
                            next_route_index += 1
                            hedge_launched = True
                        continue

                    for pending in list(pending_tasks):
                        pending.cancel()
                    if pending_tasks:
                        await asyncio.gather(*pending_tasks, return_exceptions=True)
                        pending_tasks.clear()
                    return result
        finally:
            for pending in list(pending_tasks):
                pending.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

        if last_exc is not None:
            _attach_attempt_chain_to_exception(last_exc, launched_routes)
            raise last_exc
        raise RuntimeError(f"LLM hedged call failed without attempts for step {step_key}")

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
        excluded_provider_ids: Optional[list[str]] = None,
        return_route_snapshot: bool = False,
        hedge_provider_fallback_after_s: Optional[float] = None,
        # P1.1 GAP-1a: optional sampling knobs from DiversityPlanner.
        # Keys: temperature, top_p, top_k, frequency_penalty,
        # presence_penalty, seed. Passed verbatim into provider payload.
        sampling_profile: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not self.is_enabled():
            raise RuntimeError("Real LLM mode is not configured")

        required_output_tokens = _required_output_floor(max_tokens, response_size_hint)
        routes = (
            gateway.resolve_candidates(
                step_key=step_key,
                prefer_fast=prefer_fast,
                model_override=model,
                required_output_tokens=required_output_tokens,
                excluded_provider_ids=excluded_provider_ids,
            )
            if gateway.has_business_binding(step_key) or (allow_provider_fallback and settings.LLM_PROVIDER_FAILOVER_ENABLED)
            else [gateway.resolve(
                step_key=step_key,
                prefer_fast=prefer_fast,
                model_override=model,
                excluded_provider_ids=excluded_provider_ids,
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

        last_exc: Optional[BaseException] = None
        previous_provider_id: Optional[str] = None
        total_attempts = len(routes)
        attempted_routes: list[Any] = []
        deadline = None
        if overall_timeout_s is not None:
            deadline = time.monotonic() + max(1, int(overall_timeout_s))
        hedge_after_s = None
        if (
            allow_provider_fallback
            and settings.LLM_PROVIDER_FAILOVER_ENABLED
            and getattr(settings, "LLM_PROVIDER_HEDGING_ENABLED", False)
            and hedge_provider_fallback_after_s is not None
            and len(routes) > 1
        ):
            hedge_after_s = max(0.0, float(hedge_provider_fallback_after_s))
        if hedge_after_s is not None:
            return await self._complete_with_hedged_routes(
                routes=routes,
                hedge_after_s=hedge_after_s,
                messages=messages,
                max_tokens=max_tokens,
                system=system,
                step_key=step_key,
                stage=stage,
                request_timeout_s=request_timeout_s,
                overall_timeout_s=overall_timeout_s,
                response_size_hint=response_size_hint,
                context_scope=context_scope,
                compression_policy=compression_policy,
                return_route_snapshot=return_route_snapshot,
                sampling_profile=sampling_profile,  # P1.1 GAP-1a
            )
        for attempt_index, resolved_route in enumerate(routes, start=1):
            try:
                prepared = await self._prepare_completion_attempt(
                    resolved_route=resolved_route,
                    attempt_index=attempt_index,
                    total_attempts=total_attempts,
                    previous_provider_id=previous_provider_id,
                    request_timeout_s=request_timeout_s,
                    overall_timeout_s=overall_timeout_s,
                    deadline=deadline,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    step_key=step_key,
                    response_size_hint=response_size_hint,
                    context_scope=context_scope,
                    compression_policy=compression_policy,
                    sampling_profile=sampling_profile,  # P1.1 GAP-1a
                )
                attempted_routes.append(prepared.route)
            except Exception as exc:
                route_snapshot = dict(getattr(exc, "route_snapshot", {}) or dict(getattr(resolved_route, "route_snapshot", {}) or {}))
                await gateway.emit_llm_call_log({
                    "stage": stage,
                    "stepKey": step_key,
                    "providerId": getattr(resolved_route, "provider_id", None),
                    "providerName": getattr(resolved_route, "provider_name", None),
                    "providerType": getattr(resolved_route, "provider_type", None),
                    "region": getattr(resolved_route, "region", None),
                    "model": getattr(resolved_route, "model", None),
                    "requestTimeoutS": route_snapshot.get("request_timeout_override_s", getattr(resolved_route, "request_timeout_s", None)),
                    "connectTimeoutS": getattr(resolved_route, "connect_timeout_s", None),
                    "success": False,
                    "errorCode": exc.__class__.__name__,
                    "errorMessage": str(exc),
                    "configVersion": getattr(resolved_route, "config_version", None),
                    "routeSnapshot": route_snapshot,
                    "outputClass": route_snapshot.get("output_class_for_step", ""),
                    "isPrimaryProvider": route_snapshot.get("is_primary_provider", True),
                    "failoverReason": route_snapshot.get("failover_reason"),
                    "providerVerified": route_snapshot.get("provider_verified"),
                })
                raise
            try:
                return await self._run_prepared_completion_attempt(
                    prepared=prepared,
                    step_key=step_key,
                    stage=stage,
                    return_route_snapshot=return_route_snapshot,
                )
            except asyncio.CancelledError as exc:
                last_exc = exc
                _attach_attempt_chain_to_exception(exc, [prepared.route])
                if attempt_index >= total_attempts or not _is_retryable_provider_error(exc):
                    raise
                logger.warning(
                    "LLM call %s was canceled on provider %s (attempt %s/%s), trying fallback",
                    step_key,
                    prepared.route.provider_name or prepared.route.provider_id or prepared.route.base_url,
                    attempt_index,
                    total_attempts,
                )
                previous_provider_id = prepared.route.provider_id
            except Exception as exc:
                last_exc = exc
                _attach_attempt_chain_to_exception(exc, [prepared.route])
                if attempt_index >= total_attempts or not _is_retryable_provider_error(exc):
                    raise
                logger.warning(
                    "LLM call %s failed on provider %s (attempt %s/%s), trying fallback: %s",
                    step_key,
                    prepared.route.provider_name or prepared.route.provider_id or prepared.route.base_url,
                    attempt_index,
                    total_attempts,
                    exc,
                )
                previous_provider_id = prepared.route.provider_id

        if last_exc is not None:
            _attach_attempt_chain_to_exception(last_exc, attempted_routes)
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
        truncation_retry_guidance: Optional[str] = None,
        timeout_retry_attempts: int = 0,
        timeout_retry_increment_s: int = 60,
        timeout_retry_max_s: Optional[int] = None,
        timeout_retry_min_s: Optional[int] = None,
        provider_retry_attempts: int = 1,
        provider_retry_on_timeout_errors: bool = True,
        provider_retry_base_delay_s: float = 2.0,
        provider_retry_max_delay_s: float = 12.0,
        response_size_hint: Optional[str] = None,
        context_scope: str = "request",
        compression_policy: Optional[str] = None,
        excluded_provider_ids: Optional[list[str]] = None,
        return_route_snapshot: bool = False,
        hedge_provider_fallback_after_s: Optional[float] = None,
        sampling_profile: Optional[Dict[str, Any]] = None,  # P1.1 GAP-1a
    ) -> Any:
        resolved_route = None
        try:
            if self.is_enabled():
                resolved_route = gateway.resolve(
                    step_key=step_key,
                    prefer_fast=prefer_fast,
                    model_override=model,
                    excluded_provider_ids=excluded_provider_ids,
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
        if limit_source in {"caller_capped_by_gateway", "hint_capped_by_gateway"} and not allow_provider_fallback:
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
        provider_retry_limit = max(0, int(provider_retry_attempts))
        provider_retry_attempt = 0
        size_guidance = str(truncation_retry_guidance or "").strip()
        base_messages = list(messages or [])

        while True:
            # Adaptive token budget: keep it off for full-document generation paths so
            # budgets do not ratchet upward and silently blow up latency/cost.
            if _adaptive_token_budget_enabled_for_step(step_key) and requested_max_tokens:
                suggested = _output_token_tracker.suggest_budget(step_key, requested_max_tokens)
                if suggested > requested_max_tokens:
                    requested_max_tokens = suggested

            current_overall_timeout_s = overall_timeout_s
            if current_overall_timeout_s is not None and requested_request_timeout_s is not None:
                current_overall_timeout_s = max(
                    int(current_overall_timeout_s),
                    int(requested_request_timeout_s),
                )
            retry_messages = list(base_messages)
            if truncation_attempt > 0 and size_guidance:
                retry_messages.append({"role": "user", "content": size_guidance})
            try:
                return await self.complete(
                    messages=retry_messages,
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
                    excluded_provider_ids=excluded_provider_ids,
                    return_route_snapshot=return_route_snapshot,
                    hedge_provider_fallback_after_s=hedge_provider_fallback_after_s,
                    sampling_profile=sampling_profile,  # P1.1 GAP-1a
                )
            except LLMResponseTruncatedError as exc:
                if truncation_attempt >= max_retry_attempts:
                    raise
                next_requested = max(requested_max_tokens + retry_increment, retry_floor or 0)
                if retry_ceiling is not None:
                    next_requested = min(next_requested, retry_ceiling)
                grew = next_requested > requested_max_tokens
                if not grew:
                    next_requested = requested_max_tokens
                if not grew and not size_guidance:
                    raise
                logger.warning(
                    "LLM %s response was truncated (stop_reason=%s, outputTokens=%s); retrying with %s (%s -> %s)",
                    step_key,
                    exc.stop_reason,
                    exc.output_tokens,
                    "larger budget and size guidance" if grew and size_guidance
                    else "larger budget" if grew
                    else "stricter size guidance",
                    requested_max_tokens,
                    next_requested,
                )
                requested_max_tokens = next_requested
                truncation_attempt += 1
                continue
            except asyncio.CancelledError as exc:
                if _is_retryable_provider_error(exc) and provider_retry_attempt < provider_retry_limit:
                    provider_retry_attempt += 1
                    delay_s = _provider_retry_backoff_seconds(
                        exc,
                        attempt=provider_retry_attempt,
                        base_delay_s=provider_retry_base_delay_s,
                        max_delay_s=provider_retry_max_delay_s,
                    )
                    logger.warning(
                        "LLM %s was canceled by upstream; retrying after %.1fs (attempt %s/%s)",
                        step_key,
                        delay_s,
                        provider_retry_attempt,
                        provider_retry_limit,
                    )
                    await asyncio.sleep(delay_s)
                    continue

                raise
            except Exception as exc:
                timeout_like_error = _is_timeout_like_error(exc)
                if (
                    timeout_like_error
                    and timeout_attempt < timeout_retry_limit
                    and requested_request_timeout_s is not None
                ):
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
                    continue

                if (
                    _is_retryable_provider_error(exc)
                    and provider_retry_attempt < provider_retry_limit
                    and (provider_retry_on_timeout_errors or not timeout_like_error)
                ):
                    provider_retry_attempt += 1
                    delay_s = _provider_retry_backoff_seconds(
                        exc,
                        attempt=provider_retry_attempt,
                        base_delay_s=provider_retry_base_delay_s,
                        max_delay_s=provider_retry_max_delay_s,
                    )
                    logger.warning(
                        "LLM %s hit retryable provider error; retrying after %.1fs (attempt %s/%s): %s",
                        step_key,
                        delay_s,
                        provider_retry_attempt,
                        provider_retry_limit,
                        exc,
                    )
                    await asyncio.sleep(delay_s)
                    continue

                raise

    async def stream_complete(
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
        response_size_hint: Optional[str] = None,
        context_scope: str = "request",
        compression_policy: Optional[str] = None,
    ) -> AsyncIterator[str]:
        if not self.is_enabled():
            raise RuntimeError("Real LLM mode is not configured")

        route = gateway.resolve(
            step_key=step_key,
            prefer_fast=prefer_fast,
            model_override=model,
        )
        effective_route = _apply_request_timeout_override(route, request_timeout_s)
        effective_max_tokens, requested_max_tokens, limit_source = _resolve_gateway_output_limit(
            effective_route,
            requested_max_tokens=max_tokens,
            response_size_hint=response_size_hint,
        )
        effective_route.route_snapshot = {
            **dict(getattr(effective_route, "route_snapshot", {}) or {}),
            "requested_max_tokens": int(requested_max_tokens),
            "effective_max_tokens": int(effective_max_tokens),
            "provider_max_tokens": _coerce_optional_int(getattr(effective_route, "max_tokens", None)),
            "provider_context_window": _coerce_optional_int(getattr(effective_route, "context_window", None)),
            "limit_source": limit_source,
            "response_size_hint": _normalize_response_size_hint(response_size_hint),
            "context_scope": context_scope,
            "compression_policy": compression_policy,
            "reserved_output_tokens": int(effective_max_tokens),
            "stream": True,
            **(
                {"overall_timeout_s": int(overall_timeout_s)}
                if overall_timeout_s is not None
                else {}
            ),
        }

        admission = await self._prepare_prompt_admission(
            route=effective_route,
            system=system,
            messages=messages,
            step_key=step_key,
            compression_policy=compression_policy,
            context_scope=context_scope,
            reserved_output_tokens=effective_max_tokens,
            limit_source=limit_source,
        )
        effective_route.route_snapshot = {
            **effective_route.route_snapshot,
            "requested_input_tokens": admission.requested_input_tokens,
            "estimated_input_tokens": admission.estimated_input_tokens,
            "allowed_input_tokens": admission.allowed_input_tokens,
            "reserved_output_tokens": admission.reserved_output_tokens,
            "safety_margin_tokens": admission.safety_margin_tokens,
            "strict_admission": admission.strict_admission,
            "task_memory_injected": admission.task_memory_injected,
            "task_memory_compact": admission.task_memory_compact,
            "compression_summary": list(admission.compression_summary),
            "prompt_fingerprint": admission.prompt_fingerprint,
            "prompt_dedup_applied": admission.prompt_dedup_applied,
            "prompt_dedup_base_input_tokens": admission.prompt_dedup_base_input_tokens,
            "prompt_dedup_saved_tokens_estimate": admission.prompt_dedup_saved_tokens_estimate,
            "prompt_dedup_removed_block_count": admission.prompt_dedup_removed_block_count,
            "prompt_dedup_removed_blocks": list(admission.prompt_dedup_removed_blocks),
            "prompt_dedup_summary": list(admission.prompt_dedup_summary),
        }

        async for delta in self._stream_with_route(
            route=effective_route,
            messages=admission.messages,
            max_tokens=effective_max_tokens,
            system=admission.system,
            step_key=step_key,
            stage=stage,
        ):
            yield delta

    async def _complete_with_route(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
        step_key: str,
        stage: str,
        sampling_profile: Optional[Dict[str, Any]] = None,  # P1.1 GAP-1a
    ) -> str:
        from .async_task_manager import task_manager
        checkpoint = hashlib.sha256(json.dumps({
            "step": step_key, "provider": route.provider_id, "model": route.model,
            "config": route.config_version, "messages": messages, "system": system,
            "max_tokens": max_tokens, "sampling": sampling_profile,
            "thinking": settings.LLM_DEEPSEEK_V4_THINKING,
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        restored = await task_manager.durable.checkpoint_get(checkpoint)
        if restored is not None:
            logger.warning("llm_checkpoint_restored task_id=%s step=%s", get_request_context().get("task_id"), step_key)
            return restored
        semaphore = _llm_call_semaphore(_llm_max_concurrency())
        queue_started = time.perf_counter()
        await semaphore.acquire()
        queue_wait_ms = int((time.perf_counter() - queue_started) * 1000)
        started_at = time.time()
        stop_event = asyncio.Event()
        heartbeat_task: Optional[asyncio.Task[Any]] = None
        try:
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
            effective_provider_type = route.provider_type
            route_snapshot = dict(route.route_snapshot or {})
            if queue_wait_ms > 0:
                route_snapshot["queue_wait_ms"] = queue_wait_ms
            completion_result: LLMCompletionResult
            if route.provider_type == "anthropic":
                completion_result = _normalize_completion_result(await self._run_anthropic_with_timeout(
                    route=route,
                    messages=messages,
                    max_tokens=max_tokens,
                    system=system,
                    sampling_profile=sampling_profile,  # P1.1 GAP-1a
                ))
            elif route.provider_type == "openai_compatible":
                try:
                    completion_result = _normalize_completion_result(await self._complete_openai_compatible(
                        route=route,
                        messages=messages,
                        max_tokens=max_tokens,
                        system=system,
                        sampling_profile=sampling_profile,  # P1.1 GAP-1a
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
                        sampling_profile=sampling_profile,  # P1.1 GAP-1a
                    ))
            else:
                raise RuntimeError(f"Unsupported provider type: {route.provider_type}")

            latency_ms = int((time.time() - started_at) * 1000)
            if heartbeat_task is not None:
                await self._finish_heartbeat(stop_event=stop_event, heartbeat_task=heartbeat_task)
            await self._emit_task_activity(
                route=route,
                stage=stage,
                step_key=step_key,
                state="completed",
                elapsed_ms=latency_ms,
            )

            if getattr(settings, "LLM_ADAPTIVE_TOKEN_BUDGET_ENABLED", False):
                _output_token_tracker.record(step_key, completion_result.usage.output_tokens or 0)

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
                "outputClass": route.route_snapshot.get("output_class_for_step", ""),
                "isPrimaryProvider": route.route_snapshot.get("is_primary_provider", True),
                "failoverReason": route.route_snapshot.get("failover_reason"),
                "providerVerified": route.route_snapshot.get("provider_verified"),
            })
            await task_manager.durable.checkpoint_put(checkpoint, completion_result.text)
            return completion_result.text
        except asyncio.CancelledError:
            latency_ms = int((time.time() - started_at) * 1000)
            if heartbeat_task is not None:
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
            cancelled_exc = asyncio.CancelledError("LLM call canceled before completion")
            try:
                setattr(cancelled_exc, "route_snapshot", dict(route.route_snapshot or {}))
            except Exception:
                pass
            raise cancelled_exc
        except Exception as exc:
            http_status = None
            upstream_request_id = None
            error_body_excerpt = None
            input_tokens = None
            output_tokens = None
            total_tokens = None
            transport_evidence = failure_transport_evidence(exc, api_key=route.api_key)
            safe_error = transport_error_message(transport_evidence, str(exc))
            if transport_evidence:
                route.route_snapshot = {**dict(route.route_snapshot or {}), "transportEvidence": transport_evidence}
                logger.warning("LLM transport failure: %s", json.dumps({
                    **transport_evidence, "stepKey": step_key,
                    "providerId": route.provider_id, "model": route.model,
                    "taskId": get_request_context().get("task_id"),
                }, ensure_ascii=False))

            if isinstance(exc, httpx.HTTPStatusError):
                http_status = exc.response.status_code
                upstream_request_id = _response_request_id(httpx.Headers(transport_evidence.get("responseHeaders", {})))
                # Response bodies can echo prompts or credentials. Retain only
                # their size/hash in transportEvidence, not a raw excerpt.
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
                error_body_excerpt = safe_error

            latency_ms = int((time.time() - started_at) * 1000)
            if heartbeat_task is not None:
                await self._finish_heartbeat(stop_event=stop_event, heartbeat_task=heartbeat_task)
            await self._emit_task_activity(
                route=route,
                stage=stage,
                step_key=step_key,
                state="failed",
                elapsed_ms=latency_ms,
                error_message=safe_error,
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
                "errorMessage": safe_error,
                "errorBodyExcerpt": error_body_excerpt,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": total_tokens,
                "configVersion": route.config_version,
                "routeSnapshot": route.route_snapshot,
                "outputClass": route.route_snapshot.get("output_class_for_step", ""),
                "isPrimaryProvider": route.route_snapshot.get("is_primary_provider", True),
                "failoverReason": route.route_snapshot.get("failover_reason"),
                "providerVerified": route.route_snapshot.get("provider_verified"),
            })
            try:
                setattr(exc, "route_snapshot", dict(route.route_snapshot or {}))
            except Exception:
                pass
            raise
        finally:
            semaphore.release()

    async def _stream_with_route(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
        step_key: str,
        stage: str,
    ) -> AsyncIterator[str]:
        semaphore = _llm_call_semaphore(_llm_max_concurrency())
        queue_started = time.perf_counter()
        await semaphore.acquire()
        queue_wait_ms = int((time.perf_counter() - queue_started) * 1000)
        started_at = time.time()
        stop_event = asyncio.Event()
        heartbeat_task: Optional[asyncio.Task[Any]] = None
        emitted_chars = 0
        try:
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
            route_snapshot = dict(route.route_snapshot or {})
            if queue_wait_ms > 0:
                route_snapshot["queue_wait_ms"] = queue_wait_ms
            route.route_snapshot = route_snapshot

            if route.provider_type != "openai_compatible":
                raise RuntimeError(f"Streaming is not supported for provider type: {route.provider_type}")

            usage_snapshot = LLMUsageSnapshot()
            async for delta, usage_snapshot in self._stream_openai_compatible(
                route=route,
                messages=messages,
                max_tokens=max_tokens,
                system=system,
            ):
                emitted_chars += len(delta)
                yield delta

            latency_ms = int((time.time() - started_at) * 1000)
            if heartbeat_task is not None:
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
                "providerType": route.provider_type,
                "region": route.region,
                "model": route.model,
                "requestTimeoutS": route.request_timeout_s,
                "connectTimeoutS": route.connect_timeout_s,
                "latencyMs": latency_ms,
                "success": True,
                "inputTokens": usage_snapshot.input_tokens,
                "outputTokens": usage_snapshot.output_tokens,
                "totalTokens": usage_snapshot.total_tokens,
                "configVersion": route.config_version,
                "routeSnapshot": {
                    **route_snapshot,
                    "streamed_output_chars": emitted_chars,
                },
                "outputClass": route.route_snapshot.get("output_class_for_step", ""),
                "isPrimaryProvider": route.route_snapshot.get("is_primary_provider", True),
                "failoverReason": route.route_snapshot.get("failover_reason"),
                "providerVerified": route.route_snapshot.get("provider_verified"),
            })
        except Exception as exc:
            http_status = None
            upstream_request_id = None
            error_body_excerpt = None
            input_tokens = None
            output_tokens = None
            total_tokens = None
            transport_evidence = failure_transport_evidence(exc, api_key=route.api_key)
            safe_error = transport_error_message(transport_evidence, str(exc))
            if transport_evidence:
                route.route_snapshot = {**dict(route.route_snapshot or {}), "transportEvidence": transport_evidence}
                logger.warning("LLM transport failure: %s", json.dumps({
                    **transport_evidence, "stepKey": step_key,
                    "providerId": route.provider_id, "model": route.model,
                    "taskId": get_request_context().get("task_id"),
                }, ensure_ascii=False))

            if isinstance(exc, httpx.HTTPStatusError):
                http_status = exc.response.status_code
                upstream_request_id = _response_request_id(httpx.Headers(transport_evidence.get("responseHeaders", {})))
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
                error_body_excerpt = safe_error

            latency_ms = int((time.time() - started_at) * 1000)
            if heartbeat_task is not None:
                await self._finish_heartbeat(stop_event=stop_event, heartbeat_task=heartbeat_task)
            await self._emit_task_activity(
                route=route,
                stage=stage,
                step_key=step_key,
                state="failed",
                elapsed_ms=latency_ms,
                error_message=safe_error,
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
                "errorMessage": safe_error,
                "errorBodyExcerpt": error_body_excerpt,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "totalTokens": total_tokens,
                "configVersion": route.config_version,
                "routeSnapshot": {
                    **dict(route.route_snapshot or {}),
                    "streamed_output_chars": emitted_chars,
                },
                "outputClass": route.route_snapshot.get("output_class_for_step", ""),
                "isPrimaryProvider": route.route_snapshot.get("is_primary_provider", True),
                "failoverReason": route.route_snapshot.get("failover_reason"),
                "providerVerified": route.route_snapshot.get("provider_verified"),
            })
            try:
                setattr(exc, "route_snapshot", dict(route.route_snapshot or {}))
            except Exception:
                pass
            raise
        finally:
            semaphore.release()

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
        sampling_profile: Optional[Dict[str, Any]] = None,  # P1.1 GAP-1a
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
                        sampling_profile=sampling_profile,  # P1.1 GAP-1a
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
        sampling_profile: Optional[Dict[str, Any]] = None,  # P1.1 GAP-1a
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

        # P1.1 GAP-1a: inject diversity-planner sampling knobs that the
        # Anthropic SDK accepts. We whitelist per the Messages API to avoid
        # breaking forward-compatibility if the planner emits extras.
        if sampling_profile:
            for k in ("temperature", "top_p", "top_k"):
                v = sampling_profile.get(k)
                if v is not None:
                    kwargs[k] = v

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
                partial_text=rendered or None,
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
        sampling_profile: Optional[Dict[str, Any]] = None,  # P1.1 GAP-1a
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
        _apply_model_output_mode(payload, route)
        # P1.1 GAP-1a: whitelist common OpenAI-compatible sampling knobs.
        if sampling_profile:
            for k in (
                "temperature",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "seed",
            ):
                v = sampling_profile.get(k)
                if v is not None:
                    payload[k] = v
        headers = {
            "Authorization": f"Bearer {route.api_key}",
            "Content-Type": "application/json",
        }
        url = _build_openai_compatible_chat_url(route.base_url)

        client = _llm_http_client()
        response = await client.post(
            url,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(route.request_timeout_s, connect=route.connect_timeout_s),
        )
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
                partial_text=text or None,
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

    async def _stream_openai_compatible(
        self,
        *,
        route: Any,
        messages: List[Message],
        max_tokens: int,
        system: Optional[str],
    ) -> AsyncIterator[tuple[str, LLMUsageSnapshot]]:
        payload_messages: List[Message] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)

        payload = {
            "model": route.model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        _apply_model_output_mode(payload, route)
        headers = {
            "Authorization": f"Bearer {route.api_key}",
            "Content-Type": "application/json",
        }
        url = _build_openai_compatible_chat_url(route.base_url)
        client = _llm_http_client()

        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(route.request_timeout_s, connect=route.connect_timeout_s),
        ) as response:
            response.raise_for_status()
            upstream_request_id = _response_request_id(response.headers)
            accumulated = ""
            finish_reason = ""
            usage_snapshot = LLMUsageSnapshot()
            event_name = "message"
            data_lines: list[str] = []

            async def _flush_event() -> AsyncIterator[tuple[str, LLMUsageSnapshot]]:
                nonlocal event_name, data_lines, accumulated, finish_reason, usage_snapshot
                if not data_lines:
                    event_name = "message"
                    return
                raw_data = "\n".join(data_lines).strip()
                data_lines = []
                event_name = "message"
                if not raw_data or raw_data == "[DONE]":
                    return
                try:
                    data = json.loads(raw_data)
                except ValueError as exc:
                    raise OpenAICompatibleResponseParseError(
                        "OpenAI-compatible stream returned invalid JSON",
                        response_excerpt=raw_data[:1000] or None,
                        upstream_request_id=upstream_request_id,
                    ) from exc

                chunk_usage = _extract_openai_usage(data)
                if chunk_usage.input_tokens is not None:
                    usage_snapshot.input_tokens = chunk_usage.input_tokens
                if chunk_usage.output_tokens is not None:
                    usage_snapshot.output_tokens = chunk_usage.output_tokens
                if chunk_usage.total_tokens is not None:
                    usage_snapshot.total_tokens = chunk_usage.total_tokens
                if chunk_usage.raw:
                    usage_snapshot.raw = chunk_usage.raw

                try:
                    choice = data["choices"][0]
                except (KeyError, IndexError, TypeError) as exc:
                    raise OpenAICompatibleResponseParseError(
                        "Unexpected OpenAI-compatible stream payload",
                        response_excerpt=raw_data[:1000] or None,
                        upstream_request_id=upstream_request_id,
                        input_tokens=usage_snapshot.input_tokens,
                        output_tokens=usage_snapshot.output_tokens,
                        total_tokens=usage_snapshot.total_tokens,
                    ) from exc

                delta_text = _extract_openai_choice_text(choice)
                finish_reason = str(choice.get("finish_reason") or finish_reason or "").strip().lower()
                if delta_text:
                    accumulated += delta_text
                    yield delta_text, usage_snapshot

            async for raw_line in response.aiter_lines():
                line = raw_line.rstrip("\r")
                if not line:
                    async for event in _flush_event():
                        yield event
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip() or "message"
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue
                if event_name == "message":
                    data_lines.append(line)

            async for event in _flush_event():
                yield event

            rendered = accumulated.strip()
            if finish_reason in {"length", "max_tokens"}:
                if _looks_like_complete_html_document(rendered):
                    logger.warning(
                        "OpenAI-compatible stream reported %s but returned a complete HTML document; accepting response",
                        finish_reason,
                    )
                    return
                excerpt = rendered[:1000] or None
                raise LLMResponseTruncatedError(
                    "OpenAI-compatible streaming response hit the output length limit and may be truncated",
                    response_excerpt=excerpt,
                    partial_text=rendered or None,
                    upstream_request_id=upstream_request_id,
                    stop_reason=finish_reason,
                    input_tokens=usage_snapshot.input_tokens,
                    output_tokens=usage_snapshot.output_tokens,
                    total_tokens=usage_snapshot.total_tokens,
                )
            if not rendered:
                raise EmptyOpenAICompatibleTextError(
                    "OpenAI-compatible streaming response contained no usable text",
                    response_excerpt=None,
                    upstream_request_id=upstream_request_id,
                    input_tokens=usage_snapshot.input_tokens,
                    output_tokens=usage_snapshot.output_tokens,
                    total_tokens=usage_snapshot.total_tokens,
                )
