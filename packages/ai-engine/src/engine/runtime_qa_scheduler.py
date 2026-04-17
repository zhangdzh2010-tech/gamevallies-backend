"""PR-11: Runtime QA scheduler — fire-and-forget entry + state tracking.

Before P1, `run_runtime_qa` was always awaited inline. On iterate calls
and safe-tier create calls this added 3-8 seconds to every generation
*purely* so we could log a pass/fail result that the UI never displayed
in real time. Moving this off the hot path is the single biggest P1 p50
latency win.

API surface (additive — legacy inline calls continue to work)
-------------------------------------------------------------
* `should_defer(tier, operation)` → bool
* `schedule_runtime_qa(task_id, coroutine_factory)` → awaitable handle
* `get_state(task_id)` → dict: one of pending/running/success/failed/skipped

The scheduler is in-process and does not persist to disk — we rely on the
game metadata blob (updated by the pipeline runner) for durable state.
This module is the lightweight runtime coordination layer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Literal, Optional


logger = logging.getLogger(__name__)

State = Literal["pending", "running", "success", "failed", "skipped"]


# ----------------------------------------------------------------------
# In-process state store
# ----------------------------------------------------------------------


class _StateStore:
    def __init__(self) -> None:
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def set_state(
        self,
        task_id: str,
        state: State,
        *,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not task_id:
            return
        async with self._lock:
            entry = self._entries.setdefault(task_id, {})
            entry["state"] = state
            entry["updated_at"] = time.time()
            if details:
                entry.setdefault("history", []).append(
                    {"state": state, "ts": entry["updated_at"], **details}
                )

    async def get(self, task_id: str) -> Dict[str, Any]:
        async with self._lock:
            snap = self._entries.get(task_id)
            return dict(snap) if snap else {}

    async def clear(self, task_id: str) -> None:
        async with self._lock:
            self._entries.pop(task_id, None)

    async def snapshot(self) -> Dict[str, Dict[str, Any]]:
        async with self._lock:
            return {k: dict(v) for k, v in self._entries.items()}


_STORE = _StateStore()


# ----------------------------------------------------------------------
# Deferral policy
# ----------------------------------------------------------------------


# tier/operation pairs that should defer runtime_qa off the hot path.
# (Wire-up in pipeline_v2_runner consults this through should_defer.)
_DEFERRED_PAIRS = {
    ("safe", "create"),
    ("safe", "iterate"),
    ("standard", "iterate"),
    ("draft", "iterate"),
    ("draft", "create"),
}


def should_defer(tier: Optional[str], operation: Optional[str]) -> bool:
    """Return True iff runtime_qa should fire-and-forget for this call."""
    tier = (tier or "").lower()
    operation = (operation or "").lower()
    return (tier, operation) in _DEFERRED_PAIRS


# ----------------------------------------------------------------------
# Scheduler
# ----------------------------------------------------------------------


async def schedule_runtime_qa(
    task_id: str,
    coroutine_factory: Callable[[], Awaitable[Any]],
    *,
    tier: Optional[str] = None,
    operation: Optional[str] = None,
) -> asyncio.Task:
    """Kick off runtime_qa on a background task; return a handle.

    The caller does not need to await the returned handle; it is exposed
    mainly so tests can observe completion. Exceptions inside the
    coroutine are caught and recorded as state=failed.
    """

    await _STORE.set_state(
        task_id,
        "pending",
        details={"tier": tier, "operation": operation},
    )

    async def _runner() -> None:
        await _STORE.set_state(task_id, "running")
        try:
            result = await coroutine_factory()
            await _STORE.set_state(
                task_id,
                "success",
                details={"result_summary": _summarize(result)},
            )
        except asyncio.CancelledError:
            await _STORE.set_state(task_id, "failed", details={"reason": "cancelled"})
            raise
        except Exception as exc:  # noqa: BLE001 — runtime_qa must not bubble
            logger.exception(
                "runtime_qa background task failed task_id=%s tier=%s operation=%s",
                task_id, tier, operation,
            )
            await _STORE.set_state(
                task_id, "failed", details={"reason": type(exc).__name__}
            )

    loop = asyncio.get_event_loop()
    return loop.create_task(_runner(), name=f"runtime_qa:{task_id}")


async def mark_skipped(task_id: str, *, reason: str) -> None:
    await _STORE.set_state(task_id, "skipped", details={"reason": reason})


async def get_state(task_id: str) -> Dict[str, Any]:
    """Read current state for a task. Empty dict if not tracked."""
    return await _STORE.get(task_id)


async def clear_state(task_id: str) -> None:
    await _STORE.clear(task_id)


async def snapshot_all() -> Dict[str, Dict[str, Any]]:
    return await _STORE.snapshot()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _summarize(result: Any) -> Any:
    """Trim potentially huge runtime_qa payloads for state logs."""
    if result is None:
        return None
    if isinstance(result, dict):
        out: Dict[str, Any] = {}
        for k in ("passed", "status", "failure_count", "error", "errors"):
            if k in result:
                out[k] = result[k]
        if not out:
            # Fall back to first two keys so we don't leak the whole blob.
            keys = list(result.keys())[:2]
            out = {k: _truncate(result[k]) for k in keys}
        return out
    return _truncate(result)


def _truncate(value: Any, *, limit: int = 200) -> Any:
    try:
        s = repr(value)
    except Exception:
        return "<unrepr>"
    if len(s) > limit:
        return s[:limit] + "…"
    return s


__all__ = [
    "State",
    "should_defer",
    "schedule_runtime_qa",
    "mark_skipped",
    "get_state",
    "clear_state",
    "snapshot_all",
]
