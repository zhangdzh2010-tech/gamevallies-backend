"""Shared pipeline error types.

``PipelineExecutionError`` was originally defined alongside the legacy V1
``PipelineOrchestrator``. The V1 pipeline has been removed; the error type is
kept here because the V2 pipeline runner and the API layer both rely on its
stage/retry/failure-family metadata.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx

from ..services.llm_client import LLMResponseTruncatedError


def is_provider_transport_failure(exc: BaseException) -> bool:
    """Inspect wrapped transport errors without guessing from user-facing text.

    The LLM client has already exhausted its bounded retries/fallbacks. A
    quality-regeneration loop cannot repair an HTTP or network failure.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (httpx.HTTPStatusError, httpx.TransportError)):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def is_retryable_provider_transport_failure(exc: BaseException) -> bool:
    """True for transient gateway/timeout transport, never for 4xx auth/contract errors.

    A bounded infra retry may repeat generate() with backoff and the same
    quality gates. This is not a creative/quality regeneration.
    """
    if not is_provider_transport_failure(exc):
        return False
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            status = int(current.response.status_code)
            return status in {408, 409, 425, 429} or status >= 500
        if isinstance(current, (httpx.TimeoutException, httpx.TransportError, httpx.RequestError)):
            return True
        current = current.__cause__ or current.__context__
    message = str(exc or "")
    return bool(
        getattr(exc, "failure_family", None) == "provider_transport"
        and re.search(r"\b(?:429|502|503|504|gateway timeout|bad gateway)\b", message, re.I)
    )


def is_truncation_failure(exc: BaseException) -> bool:
    """True when a provider response was cut off by an output-token limit."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, LLMResponseTruncatedError):
            return True
        if getattr(exc, "failure_family", None) == "qa_truncation":
            return True
        message = str(exc or "").lower()
        if "output length limit" in message or "hit max_tokens and may be truncated" in message:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class PipelineExecutionError(RuntimeError):
    """Runtime error carrying stage and retry metadata."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        retry_count: int = 0,
        fallback: Optional[str] = None,
        failure_family: Optional[str] = None,
        artifacts: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.retry_count = retry_count
        self.fallback = fallback
        self.failure_family = failure_family
        self.artifacts = artifacts or []
