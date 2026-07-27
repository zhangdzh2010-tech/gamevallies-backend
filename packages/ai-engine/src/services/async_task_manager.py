"""Async task manager for long-running AI generation jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from ..api.models import (
    AsyncTaskError,
    AsyncTaskHandleResponse,
    AsyncTaskProgress,
    AsyncTaskResponse,
    AsyncTaskStatus,
    AsyncTaskType,
    ListAsyncTasksResponse,
)
from ..config.timeout_store import get_int as get_timeout_int
from .async_task_store import AsyncTaskRedisStore

logger = logging.getLogger(__name__)

TaskRunner = Callable[[str], Awaitable[Any]]
FINAL_TASK_STATUSES = {
    AsyncTaskStatus.succeeded,
    AsyncTaskStatus.failed,
    AsyncTaskStatus.canceled,
}

# Minimum interval between Redis writes for high-frequency progress updates of
# a single task. Creation and terminal transitions are always persisted.
PROGRESS_PERSIST_INTERVAL_S = 0.5


@dataclass
class _TaskEntry:
    snapshot: AsyncTaskResponse
    handle: Optional[asyncio.Task[Any]] = None
    idempotency_key: Optional[str] = None


class AsyncTaskManager:
    """Tracks background AI generation jobs in memory.

    When ``settings.REDIS_URL`` is configured, task snapshots and the
    idempotency index are also persisted to Redis (best effort) so polling
    survives instance recycling and repeated submissions with the same
    idempotency key converge on a single task across replicas.
    """

    def __init__(
        self,
        *,
        completed_ttl_s: Optional[int] = None,
        max_tasks: int = 1000,
        store: Optional[AsyncTaskRedisStore] = None,
    ) -> None:
        self._default_completed_ttl_s = int(completed_ttl_s or 24 * 60 * 60)
        self._max_tasks = max_tasks
        self._tasks: "OrderedDict[str, _TaskEntry]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._store = store or AsyncTaskRedisStore()
        self._idempotency_index: dict[str, str] = {}
        self._progress_persisted_at: dict[str, float] = {}

    def _completed_ttl_s(self) -> int:
        return get_timeout_int(
            "timeout.ai_engine.async_task_completed_ttl_s",
            self._default_completed_ttl_s,
            min_value=60,
        )

    async def create_task(
        self,
        *,
        task_type: AsyncTaskType,
        game_id: str,
        user_id: str,
        timeout_s: int,
        runner: TaskRunner,
        idempotency_key: Optional[str] = None,
    ) -> AsyncTaskHandleResponse:
        idempotency_key = (idempotency_key or "").strip() or None

        if idempotency_key:
            existing = await self._resolve_idempotent_task(idempotency_key)
            if existing is not None:
                return self._to_handle_response(existing, deduplicated=True)

        task_id = uuid4().hex

        if idempotency_key and self._store.enabled():
            # Claim the key atomically (SET NX) so concurrent submissions
            # across replicas converge on the first created task.
            winner_task_id = await self._store.register_idempotency_key(
                idempotency_key, task_id, ttl_s=self._completed_ttl_s()
            )
            if winner_task_id != task_id:
                existing = await self._lookup_task_snapshot(winner_task_id)
                if existing is not None:
                    return self._to_handle_response(existing, deduplicated=True)
                logger.warning(
                    "async_task_manager: idempotency key mapped to unknown task %s; creating a new task",
                    winner_task_id,
                )

        now = time.time()
        snapshot = AsyncTaskResponse(
            task_id=task_id,
            task_type=task_type,
            status=AsyncTaskStatus.queued,
            game_id=game_id,
            user_id=user_id,
            timeout_s=timeout_s,
            created_at=now,
            ws_channel=f"game:{game_id}",
        )

        async with self._lock:
            self._prune_locked(now)
            self._tasks[task_id] = _TaskEntry(snapshot=snapshot, idempotency_key=idempotency_key)
            if idempotency_key:
                self._idempotency_index[idempotency_key] = task_id

        await self._persist_snapshot(snapshot)

        handle = asyncio.create_task(self._run_task(task_id, runner, timeout_s), name=f"ai-task-{task_id}")
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is not None:
                entry.handle = handle

        return self._to_handle_response(snapshot)

    async def _resolve_idempotent_task(self, idempotency_key: str) -> Optional[AsyncTaskResponse]:
        """Return the snapshot previously created for this idempotency key, if any."""
        async with self._lock:
            existing_task_id = self._idempotency_index.get(idempotency_key)
            if existing_task_id:
                entry = self._tasks.get(existing_task_id)
                if entry is not None:
                    return self._clone_snapshot(entry.snapshot)

        if not self._store.enabled():
            return None

        existing_task_id = await self._store.lookup_idempotency_key(idempotency_key)
        if not existing_task_id:
            return None
        # Tasks owned by other replicas are intentionally not cached in the
        # local index: their lifecycle (and TTL) lives in Redis.
        return await self._lookup_task_snapshot(existing_task_id)

    async def _lookup_task_snapshot(self, task_id: str) -> Optional[AsyncTaskResponse]:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is not None:
                return self._clone_snapshot(entry.snapshot)
        return await self._store.load_snapshot(task_id)

    async def get_task(self, task_id: str) -> Optional[AsyncTaskResponse]:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is not None:
                return self._clone_snapshot(entry.snapshot)
        # Memory miss: the instance may have been recycled (or another replica
        # owns the task) — fall back to the Redis snapshot.
        return await self._store.load_snapshot(task_id)

    async def list_tasks(
        self,
        *,
        user_id: Optional[str] = None,
        game_id: Optional[str] = None,
        status: Optional[AsyncTaskStatus] = None,
        limit: int = 20,
    ) -> ListAsyncTasksResponse:
        limit = max(1, min(limit, 100))
        async with self._lock:
            self._prune_locked(time.time())
            items = []
            total = 0
            for entry in reversed(self._tasks.values()):
                snapshot = entry.snapshot
                if user_id and snapshot.user_id != user_id:
                    continue
                if game_id and snapshot.game_id != game_id:
                    continue
                if status and snapshot.status != status:
                    continue
                total += 1
                if len(items) < limit:
                    items.append(self._clone_snapshot(snapshot))
            return ListAsyncTasksResponse(items=items, total=total)

    async def cancel_task(self, task_id: str) -> Optional[AsyncTaskResponse]:
        handle: Optional[asyncio.Task[Any]] = None
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return None
            if entry.snapshot.status in FINAL_TASK_STATUSES:
                return self._clone_snapshot(entry.snapshot)

            entry.snapshot.status = AsyncTaskStatus.canceled
            entry.snapshot.completed_at = time.time()
            entry.snapshot.error = AsyncTaskError(message="Task canceled by client")
            handle = entry.handle
            snapshot = self._clone_snapshot(entry.snapshot)

        if handle is not None:
            handle.cancel()

        await self._persist_snapshot(snapshot)
        return snapshot

    async def update_progress(
        self,
        task_id: str,
        *,
        stage: str,
        pct: int,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            if entry.snapshot.status in FINAL_TASK_STATUSES:
                return

            entry.snapshot.progress = AsyncTaskProgress(
                stage=stage,
                pct=pct,
                message=message,
                details=details or {},
                updated_at=time.time(),
            )
            snapshot = self._clone_snapshot(entry.snapshot)

        # Throttle Redis writes for high-frequency progress updates; creation
        # and terminal transitions are always persisted elsewhere.
        now = time.monotonic()
        last_persisted = self._progress_persisted_at.get(task_id, 0.0)
        if now - last_persisted >= PROGRESS_PERSIST_INTERVAL_S:
            self._progress_persisted_at[task_id] = now
            await self._persist_snapshot(snapshot)

    async def clear(self) -> None:
        async with self._lock:
            for entry in self._tasks.values():
                if entry.handle and not entry.handle.done():
                    entry.handle.cancel()
            self._tasks.clear()
            self._idempotency_index.clear()
            self._progress_persisted_at.clear()

    async def _run_task(self, task_id: str, runner: TaskRunner, timeout_s: int) -> None:
        await self._mark_running(task_id)
        try:
            result = await asyncio.wait_for(runner(task_id), timeout=max(1, int(timeout_s)))
        except asyncio.CancelledError:
            await self._mark_canceled(task_id)
            raise
        except Exception as exc:  # pragma: no cover - exact exceptions covered through public API
            await self._mark_failed(task_id, exc)
        else:
            await self._mark_succeeded(task_id, result)

    async def _mark_running(self, task_id: str) -> None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            entry.snapshot.status = AsyncTaskStatus.running
            entry.snapshot.started_at = time.time()
            snapshot = self._clone_snapshot(entry.snapshot)
        await self._persist_snapshot(snapshot)

    async def _mark_succeeded(self, task_id: str, result: Any) -> None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            entry.snapshot.status = AsyncTaskStatus.succeeded
            entry.snapshot.result = self._serialize_result(result)
            entry.snapshot.completed_at = time.time()
            entry.snapshot.error = None
            snapshot = self._clone_snapshot(entry.snapshot)
        await self._persist_snapshot(snapshot)

    async def _mark_failed(self, task_id: str, exc: Exception) -> None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            entry.snapshot.status = AsyncTaskStatus.failed
            entry.snapshot.completed_at = time.time()
            entry.snapshot.error = self._build_error(exc)
            snapshot = self._clone_snapshot(entry.snapshot)
        await self._persist_snapshot(snapshot)

    async def _mark_canceled(self, task_id: str) -> None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            if entry.snapshot.status == AsyncTaskStatus.canceled:
                if entry.snapshot.completed_at is None:
                    entry.snapshot.completed_at = time.time()
                snapshot = self._clone_snapshot(entry.snapshot)
            else:
                entry.snapshot.status = AsyncTaskStatus.canceled
                entry.snapshot.completed_at = time.time()
                entry.snapshot.error = AsyncTaskError(message="Task canceled by client")
                snapshot = self._clone_snapshot(entry.snapshot)
        await self._persist_snapshot(snapshot)

    async def _persist_snapshot(self, snapshot: AsyncTaskResponse) -> None:
        """Best-effort write-through to Redis; failures never break the flow."""
        try:
            await self._store.save_snapshot(snapshot, ttl_s=self._completed_ttl_s())
        except Exception as exc:  # pragma: no cover - store already degrades internally
            logger.warning(
                "async_task_manager: failed to persist task %s snapshot: %s",
                snapshot.task_id,
                exc,
            )

    def _to_handle_response(
        self,
        snapshot: AsyncTaskResponse,
        *,
        deduplicated: bool = False,
    ) -> AsyncTaskHandleResponse:
        return AsyncTaskHandleResponse(
            task_id=snapshot.task_id,
            task_type=snapshot.task_type,
            status=snapshot.status,
            game_id=snapshot.game_id,
            user_id=snapshot.user_id,
            timeout_s=snapshot.timeout_s,
            ws_channel=snapshot.ws_channel,
            poll_url=f"/api/v1/ai/tasks/{snapshot.task_id}",
            cancel_url=f"/api/v1/ai/tasks/{snapshot.task_id}/cancel",
            deduplicated=deduplicated,
        )

    def _clone_snapshot(self, snapshot: AsyncTaskResponse) -> AsyncTaskResponse:
        return AsyncTaskResponse.model_validate(snapshot.model_dump())

    def _serialize_result(self, result: Any) -> Any:
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, dict):
            return result
        return {"value": result}

    def _build_error(self, exc: Exception) -> AsyncTaskError:
        if isinstance(exc, asyncio.TimeoutError):
            return AsyncTaskError(message="Task timed out")

        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            return AsyncTaskError(
                message=str(
                    detail.get("message")
                    or detail.get("error")
                    or str(exc)
                ),
                failed_stage=detail.get("failed_stage") or detail.get("failedStage"),
                retry_count=int(detail.get("retry_count") or detail.get("retryCount") or 0),
                fallback=detail.get("fallback"),
                failure_family=detail.get("failure_family") or detail.get("failureFamily"),
                primary_artifact_id=detail.get("primary_artifact_id") or detail.get("primaryArtifactId"),
            )

        return AsyncTaskError(
            message=str(exc),
            failed_stage=getattr(exc, "stage", None),
            retry_count=int(getattr(exc, "retry_count", 0) or 0),
            fallback=getattr(exc, "fallback", None),
            failure_family=getattr(exc, "failure_family", None) or getattr(exc, "failureFamily", None),
            primary_artifact_id=getattr(exc, "primary_artifact_id", None)
            or getattr(exc, "primaryArtifactId", None),
        )

    def _prune_locked(self, now: float) -> None:
        stale_ids = []
        for task_id, entry in self._tasks.items():
            snapshot = entry.snapshot
            if snapshot.status not in FINAL_TASK_STATUSES:
                continue
            completed_at = snapshot.completed_at or snapshot.created_at
            if now - completed_at > self._completed_ttl_s():
                stale_ids.append(task_id)

        for task_id in stale_ids:
            self._remove_task_locked(task_id)

        overflow = len(self._tasks) - self._max_tasks
        if overflow <= 0:
            return

        removable_ids = [
            task_id
            for task_id, entry in self._tasks.items()
            if entry.snapshot.status in FINAL_TASK_STATUSES
        ]
        for task_id in removable_ids[:overflow]:
            self._remove_task_locked(task_id)

    def _remove_task_locked(self, task_id: str) -> None:
        entry = self._tasks.pop(task_id, None)
        self._progress_persisted_at.pop(task_id, None)
        if entry is not None and entry.idempotency_key:
            if self._idempotency_index.get(entry.idempotency_key) == task_id:
                self._idempotency_index.pop(entry.idempotency_key, None)


task_manager = AsyncTaskManager()
