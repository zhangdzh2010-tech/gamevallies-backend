"""Redis-backed execution commands, leases and recovery for provisioned workers.

A 202 acknowledges a persisted command, never an in-process coroutine. Workers
claim commands with a fencing token; restart recovery retains the original
deadline and task ID. Redis failures stop execution rather than lose ownership.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import hashlib
import json
import logging
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

from redis.exceptions import WatchError
from ..api.models import AsyncTaskResponse, AsyncTaskStatus, AsyncTaskProgress

logger = logging.getLogger(__name__)
ACTIVE_KEY = "ai-engine:durable-tasks:pending"
RECENT_KEY = "ai-engine:durable-tasks:recent"
TTL_S = 86400
TERMINAL = {"succeeded", "failed", "canceled"}
execution_owner: ContextVar[str | None] = ContextVar("execution_owner", default=None)
execution_task_id: ContextVar[str | None] = ContextVar("execution_task_id", default=None)
execution_attempt: ContextVar[int] = ContextVar("execution_attempt", default=0)


def command_key(task_id: str) -> str:
    return f"ai-engine:durable-task:{task_id}"


class DurableTaskExecutor:
    def __init__(self, store, *, lease_s=90, heartbeat_s=15, concurrency=4, max_attempts=3):
        self.store = store
        self.lease_s = lease_s
        self.heartbeat_s = heartbeat_s
        self.concurrency = concurrency
        self.max_attempts = max_attempts
        self.runners: dict[str, Callable[[dict, str], Awaitable[Any]]] = {}
        self.loop: asyncio.Task | None = None
        self.handles: dict[str, asyncio.Task] = {}

    async def redis(self):
        client = await self.store._get_redis()
        if client is None:
            raise RuntimeError("Durable task store unavailable; command was not accepted")
        return client

    async def record(self, task_id):
        raw = await (await self.redis()).get(command_key(task_id))
        return json.loads(raw) if raw else None

    async def change(self, task_id, mutate):
        """CAS the complete record and pending index in one Redis transaction."""
        client = await self.redis()
        key = command_key(task_id)
        for _ in range(20):
            async with client.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    record = json.loads(raw) if raw else None
                    updated = mutate(record)
                    if updated is None:
                        return None
                    pipe.multi()
                    pipe.set(key, json.dumps(updated, ensure_ascii=False), ex=TTL_S)
                    pipe.zadd(RECENT_KEY, {task_id: updated['snapshot']['created_at']})
                    if updated['snapshot']['status'] in TERMINAL:
                        pipe.zrem(ACTIVE_KEY, task_id)
                    else:
                        pipe.zadd(ACTIVE_KEY, {task_id: updated.get('lease_until', 0)})
                    await pipe.execute()
                    return updated
                except WatchError:
                    continue
        raise RuntimeError("Task ownership contention; retry with the same task ID")

    async def submit(self, snapshot: AsyncTaskResponse, payload: dict, idempotency_key: str | None):
        # Scope idempotency by both owner and resource; another user must never
        # receive the first user's handle or result by guessing a key.
        identity = idempotency_key or payload.get('task_id') or uuid4().hex
        task_id = hashlib.sha256(f"{snapshot.user_id}:{snapshot.game_id}:{snapshot.task_type.value}:{identity}".encode()).hexdigest()[:32]
        snapshot.task_id = task_id
        created = False
        def admit(old):
            nonlocal created
            if old is not None:
                created = False
                return None
            created = True
            return {'snapshot':snapshot.model_dump(mode='json'), 'payload':payload,
                    'deadline':snapshot.created_at + snapshot.timeout_s,
                    'owner':None, 'lease_until':0, 'attempt':0}
        record = await self.change(task_id, admit)
        record = record or await self.record(task_id)
        if record is None:
            raise RuntimeError('Durable task command was not persisted')
        self.start()
        return AsyncTaskResponse.model_validate(record['snapshot']), not created

    def start(self):
        if self.loop is None or self.loop.done():
            self.loop = asyncio.create_task(self._watch(), name='durable-ai-worker')

    async def close(self):
        # Do not mark interrupted commands canceled. A new process claims them
        # after lease expiry and continues with the remaining deadline.
        tasks = [*self.handles.values(), *([self.loop] if self.loop else [])]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.handles.clear()
        self.loop = None

    async def _watch(self):
        while True:
            try:
                await self.sweep()
            except Exception as exc:
                logger.warning('durable_worker_poll_failed type=%s', type(exc).__name__)
            await asyncio.sleep(min(5, self.heartbeat_s))

    async def sweep(self):
        self.handles = {k:v for k,v in self.handles.items() if not v.done()}
        room = self.concurrency - len(self.handles)
        if room <= 0:
            return
        client = await self.redis()
        ids = await client.zrangebyscore(ACTIVE_KEY, '-inf', time.time(), start=0, num=room)
        for task_id in ids:
            record = await self.record(task_id)
            if record is None:
                await client.zrem(ACTIVE_KEY, task_id)
                continue
            if record['snapshot']['task_type'] not in self.runners:
                continue
            owner = uuid4().hex
            def claim(old):
                if old is None or old['snapshot']['status'] in TERMINAL or old['lease_until'] > time.time():
                    return None
                if old['deadline'] <= time.time() or old['attempt'] >= self.max_attempts:
                    old['snapshot'].update(status='failed', completed_at=time.time(), error={
                        'message':'Task timed out: deadline exceeded' if old['deadline'] <= time.time() else 'Worker interrupted repeatedly; recovery limit reached',
                        'failure_family':'timeout' if old['deadline'] <= time.time() else 'worker_interrupted',
                        'retry_count':max(0, old['attempt'] - 1)})
                    return old
                old.update(owner=owner, lease_until=time.time()+self.lease_s, attempt=old['attempt']+1)
                old['snapshot'].update(status='running', started_at=old['snapshot'].get('started_at') or time.time())
                return old
            claimed = await self.change(task_id, claim)
            if claimed and claimed['snapshot']['status'] == 'running':
                self.handles[task_id] = asyncio.create_task(self._run(task_id, owner, claimed), name=f'durable-ai-{task_id}')

    async def owned_change(self, task_id, owner, mutate):
        def fenced(record):
            if record is None or record['owner'] != owner or record['lease_until'] <= time.time() or record['snapshot']['status'] in TERMINAL:
                return None
            return mutate(record)
        return await self.change(task_id, fenced)

    async def _keep_lease(self, task_id, owner):
        while True:
            await asyncio.sleep(self.heartbeat_s)
            def renew(record):
                record['lease_until'] = time.time() + self.lease_s
                return record
            if not await self.owned_change(task_id, owner, renew):
                return

    async def _run(self, task_id, owner, record):
        context = execution_owner.set(owner)
        task_context = execution_task_id.set(task_id)
        attempt_context = execution_attempt.set(record['attempt'])
        runner = heartbeat = None
        try:
            payload = dict(record['payload'])
            payload['timeout_s'] = max(1, int(record['deadline'] - time.time()))
            payload['_execution_attempt'] = record['attempt']
            logger.warning('durable_task_start task_id=%s attempt=%s remaining_s=%s', task_id, record['attempt'], payload['timeout_s'])
            runner = asyncio.create_task(self.runners[record['snapshot']['task_type']](payload, task_id))
            heartbeat = asyncio.create_task(self._keep_lease(task_id, owner))
            done, _ = await asyncio.wait([runner, heartbeat], timeout=max(0, record['deadline']-time.time()), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                # Ownership loss, cancellation or Redis outage: do not commit.
                heartbeat.result()
                return
            if not done:
                result, error = None, {'message':'Task timed out: deadline exceeded', 'failure_family':'timeout'}
            else:
                try:
                    result = runner.result()
                    result = result.model_dump(mode='json') if hasattr(result, 'model_dump') else result
                    error = None
                except Exception as exc:
                    from .async_task_manager import task_manager
                    result, error = None, task_manager._build_error(exc).model_dump(mode='json')
            def finish(current):
                current['snapshot'].update(status='failed' if error else 'succeeded', result=result,
                                           error=error, completed_at=time.time())
                return current
            await self.owned_change(task_id, owner, finish)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('durable_task_interrupted task_id=%s type=%s', task_id, type(exc).__name__)
        finally:
            for handle in (runner, heartbeat):
                if handle and not handle.done(): handle.cancel()
            await asyncio.gather(*[h for h in (runner,heartbeat) if h], return_exceptions=True)
            execution_owner.reset(context)
            execution_task_id.reset(task_context)
            execution_attempt.reset(attempt_context)

    async def get(self, task_id):
        record = await self.record(task_id)
        return AsyncTaskResponse.model_validate(record['snapshot']) if record else None

    async def list(self):
        client = await self.redis()
        await client.zremrangebyscore(RECENT_KEY, '-inf', time.time() - TTL_S)
        ids = await client.zrevrange(RECENT_KEY, 0, 999)
        if not ids: return []
        records = await client.mget([command_key(task_id) for task_id in ids])
        return [AsyncTaskResponse.model_validate(json.loads(raw)['snapshot']) for raw in records if raw]

    async def cancel(self, task_id):
        def cancel(record):
            if record is None or record['snapshot']['status'] in TERMINAL: return None
            record['snapshot'].update(status='canceled', completed_at=time.time(), error={'message':'Task canceled by client'})
            return record
        changed = await self.change(task_id, cancel)
        if changed and task_id in self.handles: self.handles[task_id].cancel()
        return await self.get(task_id)

    async def progress(self, task_id, **kwargs):
        task_id = execution_task_id.get() or task_id
        owner = execution_owner.get()
        if not owner: return False
        def update(record):
            record['snapshot']['progress'] = AsyncTaskProgress(**kwargs, updated_at=time.time()).model_dump(mode='json')
            return record
        return bool(await self.owned_change(task_id, owner, update))

    async def assert_owned(self):
        task_id, owner = execution_task_id.get(), execution_owner.get()
        if not task_id: return
        record = await self.record(task_id)
        if not record or record['owner'] != owner or record['lease_until'] <= time.time() or record['snapshot']['status'] in TERMINAL:
            raise asyncio.CancelledError('Task execution lease is no longer owned')

    async def checkpoint_get(self, fingerprint):
        task_id = execution_task_id.get()
        if not task_id: return None
        await self.assert_owned()
        record = await self.record(task_id)
        return record.get('checkpoints', {}).get(fingerprint)

    async def checkpoint_put(self, fingerprint, text):
        task_id, owner = execution_task_id.get(), execution_owner.get()
        if not task_id: return
        def save(record):
            record.setdefault('checkpoints', {})[fingerprint] = text
            return record
        if not await self.owned_change(task_id, owner, save):
            raise asyncio.CancelledError('Task ownership lost before saving checkpoint')
