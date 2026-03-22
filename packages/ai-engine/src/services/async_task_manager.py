"""Async task manager for long-running AI generation jobs."""

from __future__ import annotations

import asyncio
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

TaskRunner = Callable[[str], Awaitable[Any]]
FINAL_TASK_STATUSES = {
    AsyncTaskStatus.succeeded,
    AsyncTaskStatus.failed,
    AsyncTaskStatus.canceled,
}


@dataclass
class _TaskEntry:
    snapshot: AsyncTaskResponse
    handle: Optional[asyncio.Task[Any]] = None


class AsyncTaskManager:
    """Tracks background AI generation jobs in memory."""

    def __init__(self, *, completed_ttl_s: int = 24 * 60 * 60, max_tasks: int = 1000) -> None:
        self._completed_ttl_s = completed_ttl_s
        self._max_tasks = max_tasks
        self._tasks: "OrderedDict[str, _TaskEntry]" = OrderedDict()
        self._lock = asyncio.Lock()

    async def create_task(
        self,
        *,
        task_type: AsyncTaskType,
        game_id: str,
        user_id: str,
        timeout_s: int,
        runner: TaskRunner,
    ) -> AsyncTaskHandleResponse:
        task_id = uuid4().hex
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
            self._tasks[task_id] = _TaskEntry(snapshot=snapshot)

        handle = asyncio.create_task(self._run_task(task_id, runner), name=f"ai-task-{task_id}")
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is not None:
                entry.handle = handle

        return self._to_handle_response(snapshot)

    async def get_task(self, task_id: str) -> Optional[AsyncTaskResponse]:
        async with self._lock:
            entry = self._tasks.get(task_id)
            return self._clone_snapshot(entry.snapshot) if entry else None

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

    async def clear(self) -> None:
        async with self._lock:
            for entry in self._tasks.values():
                if entry.handle and not entry.handle.done():
                    entry.handle.cancel()
            self._tasks.clear()

    async def _run_task(self, task_id: str, runner: TaskRunner) -> None:
        await self._mark_running(task_id)
        try:
            result = await runner(task_id)
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

    async def _mark_succeeded(self, task_id: str, result: Any) -> None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            entry.snapshot.status = AsyncTaskStatus.succeeded
            entry.snapshot.result = self._serialize_result(result)
            entry.snapshot.completed_at = time.time()
            entry.snapshot.error = None

    async def _mark_failed(self, task_id: str, exc: Exception) -> None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            entry.snapshot.status = AsyncTaskStatus.failed
            entry.snapshot.completed_at = time.time()
            entry.snapshot.error = self._build_error(exc)

    async def _mark_canceled(self, task_id: str) -> None:
        async with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
            if entry.snapshot.status == AsyncTaskStatus.canceled:
                if entry.snapshot.completed_at is None:
                    entry.snapshot.completed_at = time.time()
                return
            entry.snapshot.status = AsyncTaskStatus.canceled
            entry.snapshot.completed_at = time.time()
            entry.snapshot.error = AsyncTaskError(message="Task canceled by client")

    def _to_handle_response(self, snapshot: AsyncTaskResponse) -> AsyncTaskHandleResponse:
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

        return AsyncTaskError(
            message=str(exc),
            failed_stage=getattr(exc, "stage", None),
            retry_count=int(getattr(exc, "retry_count", 0) or 0),
            fallback=getattr(exc, "fallback", None),
        )

    def _prune_locked(self, now: float) -> None:
        stale_ids = []
        for task_id, entry in self._tasks.items():
            snapshot = entry.snapshot
            if snapshot.status not in FINAL_TASK_STATUSES:
                continue
            completed_at = snapshot.completed_at or snapshot.created_at
            if now - completed_at > self._completed_ttl_s:
                stale_ids.append(task_id)

        for task_id in stale_ids:
            self._tasks.pop(task_id, None)

        overflow = len(self._tasks) - self._max_tasks
        if overflow <= 0:
            return

        removable_ids = [
            task_id
            for task_id, entry in self._tasks.items()
            if entry.snapshot.status in FINAL_TASK_STATUSES
        ]
        for task_id in removable_ids[:overflow]:
            self._tasks.pop(task_id, None)


task_manager = AsyncTaskManager()
