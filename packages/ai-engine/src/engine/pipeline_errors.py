"""Shared pipeline error types.

``PipelineExecutionError`` was originally defined alongside the legacy V1
``PipelineOrchestrator``. The V1 pipeline has been removed; the error type is
kept here because the V2 pipeline runner and the API layer both rely on its
stage/retry/failure-family metadata.
"""

from __future__ import annotations

from typing import Any, Optional


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
