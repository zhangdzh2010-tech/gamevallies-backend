"""Best-effort Redis persistence for async task snapshots and idempotency keys.

The store is optional: when ``settings.REDIS_URL`` is empty (the default) every
method degrades to a no-op and the async task manager behaves exactly like the
historical pure in-memory implementation. When Redis is configured, task
snapshots are written to a Redis hash with a TTL aligned to the in-memory
completed-task TTL, so polling survives instance recycling (VeFaaS) and
multi-replica deployments. Any Redis failure is caught, logged as a warning
and degrades back to in-memory behavior — it must never break the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from redis.asyncio import Redis

from ..api.models import AsyncTaskResponse
from ..config.settings import settings

logger = logging.getLogger(__name__)

# A single task record (hash) larger than this is stored without the result
# body; the poller is expected to fetch large artifacts through the artifact
# relay channel instead (see generate.py `_relay_artifact_to_game_service`).
MAX_RESULT_BYTES = 1_000_000

# When the Redis connection fails we back off before probing again, instead of
# hammering an unavailable server on every task operation.
_RECONNECT_COOLDOWN_S = 30.0

_KEY_PREFIX = "ai-engine:async-task"


def _task_key(task_id: str) -> str:
    return f"{_KEY_PREFIX}:task:{task_id}"


def _idempotency_key(key: str) -> str:
    return f"{_KEY_PREFIX}:idem:{key}"


class AsyncTaskRedisStore:
    """Pluggable Redis-backed persistence layer for :class:`AsyncTaskManager`."""

    def __init__(self, *, redis_url: Optional[str] = None, client: Optional[Redis] = None) -> None:
        # redis_url=None means "resolve from settings at call time" so tests can
        # patch settings.REDIS_URL without rebuilding the singleton manager.
        # An explicit client (e.g. fakeredis) bypasses URL-based connection.
        self._redis_url = redis_url
        self._injected_client = client
        self._redis: Optional[Redis] = client
        self._connect_lock = asyncio.Lock()
        self._next_connect_attempt_at = 0.0

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------
    def _resolved_url(self) -> str:
        if self._redis_url is not None:
            return self._redis_url.strip()
        return (settings.REDIS_URL or "").strip()

    def enabled(self) -> bool:
        return self._injected_client is not None or bool(self._resolved_url())

    async def _get_redis(self) -> Optional[Redis]:
        if not self.enabled():
            return None
        if self._redis is not None:
            return self._redis
        now = time.monotonic()
        if now < self._next_connect_attempt_at:
            return None

        async with self._connect_lock:
            if self._redis is not None:
                return self._redis
            if time.monotonic() < self._next_connect_attempt_at:
                return None
            try:
                client: Redis = Redis.from_url(self._resolved_url(), decode_responses=True)
                await client.ping()
                self._redis = client
            except Exception as exc:
                self._next_connect_attempt_at = time.monotonic() + _RECONNECT_COOLDOWN_S
                logger.warning(
                    "async_task_store: redis unavailable, degrading to in-memory store: %s", exc
                )
                self._redis = None
        return self._redis

    def _drop_connection(self) -> None:
        if self._injected_client is not None:
            return
        self._redis = None
        self._next_connect_attempt_at = time.monotonic() + _RECONNECT_COOLDOWN_S

    async def close(self) -> None:
        client = self._redis
        self._redis = self._injected_client
        self._next_connect_attempt_at = 0.0
        if client is not None and client is not self._injected_client:
            try:
                await client.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Task snapshots (Redis hash + TTL)
    # ------------------------------------------------------------------
    async def save_snapshot(self, snapshot: AsyncTaskResponse, *, ttl_s: int) -> None:
        redis = await self._get_redis()
        if redis is None:
            return

        try:
            fields = self._snapshot_to_fields(snapshot)
            key = _task_key(snapshot.task_id)
            async with redis.pipeline(transaction=False) as pipe:
                pipe.hset(key, mapping=fields)
                if "result" not in fields:
                    pipe.hdel(key, "result")
                pipe.expire(key, max(60, int(ttl_s)))
                await pipe.execute()
        except Exception as exc:
            self._drop_connection()
            logger.warning(
                "async_task_store: failed to persist task %s to redis: %s", snapshot.task_id, exc
            )

    async def load_snapshot(self, task_id: str) -> Optional[AsyncTaskResponse]:
        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            fields = await redis.hgetall(_task_key(task_id))
        except Exception as exc:
            self._drop_connection()
            logger.warning("async_task_store: failed to load task %s from redis: %s", task_id, exc)
            return None

        if not fields or not fields.get("snapshot"):
            return None

        try:
            payload = json.loads(fields["snapshot"])
            raw_result = fields.get("result")
            if raw_result:
                payload["result"] = json.loads(raw_result)
            return AsyncTaskResponse.model_validate(payload)
        except Exception as exc:
            logger.warning("async_task_store: corrupt redis record for task %s: %s", task_id, exc)
            return None

    def _snapshot_to_fields(self, snapshot: AsyncTaskResponse) -> dict[str, str]:
        payload = snapshot.model_dump(mode="json")
        result = payload.pop("result", None)

        fields: dict[str, str] = {"snapshot": json.dumps(payload, ensure_ascii=False)}
        if result is not None:
            serialized_result = json.dumps(result, ensure_ascii=False)
            if len(serialized_result.encode("utf-8")) > MAX_RESULT_BYTES:
                serialized_result = json.dumps(
                    self._build_omitted_result_marker(result, len(serialized_result)),
                    ensure_ascii=False,
                )
            fields["result"] = serialized_result
        return fields

    @staticmethod
    def _build_omitted_result_marker(result: Any, size_bytes: int) -> dict[str, Any]:
        marker: dict[str, Any] = {
            "result_omitted": True,
            "omitted_reason": "redis_record_too_large",
            "omitted_size_bytes": size_bytes,
        }
        if isinstance(result, dict):
            # Keep small identifying scalars so the poller can recover the full
            # payload through the artifact relay channel.
            for key in ("game_id", "pipeline_version", "primary_artifact_id"):
                value = result.get(key)
                if isinstance(value, (str, int, float, bool)) or value is None:
                    marker[key] = value
        return marker

    # ------------------------------------------------------------------
    # Idempotency index
    # ------------------------------------------------------------------
    async def register_idempotency_key(self, key: str, task_id: str, *, ttl_s: int) -> str:
        """Register ``key -> task_id`` if absent; returns the winning task_id.

        Uses SET NX so concurrent submissions across replicas converge on the
        first created task. On any Redis failure the local task_id is returned
        (in-memory behavior).
        """
        redis = await self._get_redis()
        if redis is None:
            return task_id

        try:
            claimed = await redis.set(
                _idempotency_key(key), task_id, nx=True, ex=max(60, int(ttl_s))
            )
            if claimed:
                return task_id
            existing = await redis.get(_idempotency_key(key))
            return str(existing) if existing else task_id
        except Exception as exc:
            self._drop_connection()
            logger.warning(
                "async_task_store: failed to register idempotency key in redis: %s", exc
            )
            return task_id

    async def lookup_idempotency_key(self, key: str) -> Optional[str]:
        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            task_id = await redis.get(_idempotency_key(key))
            return str(task_id) if task_id else None
        except Exception as exc:
            self._drop_connection()
            logger.warning(
                "async_task_store: failed to look up idempotency key in redis: %s", exc
            )
            return None
