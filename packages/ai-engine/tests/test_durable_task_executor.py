import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from fakeredis.aioredis import FakeRedis
from fakeredis import FakeServer
from src.api.models import AsyncTaskResponse
from src.services.async_task_store import AsyncTaskRedisStore
from src.services.durable_task_executor import DurableTaskExecutor, command_key, execution_owner


class DurableTasks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = FakeServer()
        self.workers = []

    async def asyncTearDown(self):
        for worker in self.workers:
            await worker.close()

    def worker(self, runner):
        worker = DurableTaskExecutor(AsyncTaskRedisStore(client=FakeRedis(server=self.server, decode_responses=True)),
            lease_s=5, heartbeat_s=.025)
        worker.runners['pipeline_run'] = runner
        self.workers.append(worker)
        return worker

    async def submit(self, worker, *, key='request-1', user='user', timeout=20):
        snapshot = AsyncTaskResponse(task_id='', task_type='pipeline_run', status='queued',
            game_id='game', user_id=user, timeout_s=timeout, created_at=time.time(), ws_channel='game:game')
        return await worker.submit(snapshot, {'game_id':'game', 'task_id':'sql-task'}, key)

    async def wait_status(self, worker, task_id, statuses):
        for _ in range(100):
            await worker.sweep()
            snapshot = await worker.get(task_id)
            if snapshot.status in statuses: return snapshot
            await asyncio.sleep(.015)
        self.fail('task did not reach expected status')

    async def test_restart_recovers_command_same_id_deadline_and_checkpoint(self):
        entered = asyncio.Event()
        calls = []
        async def first(payload, task_id):
            calls.append(payload['timeout_s'])
            await one.checkpoint_put('code-output', '<html>saved output</html>')
            entered.set()
            await asyncio.Event().wait()
        one = self.worker(first)
        snapshot, _ = await self.submit(one)
        await entered.wait()
        deadline = (await one.record(snapshot.task_id))['deadline']
        await one.close()  # process shutdown, without writing a terminal state
        # Advance the persisted lease explicitly instead of relying on a
        # subsecond wall-clock lease that flakes under concurrent build load.
        def expire(record):
            record['lease_until'] = 0
            return record
        await one.change(snapshot.task_id, expire)
        async def recovered(payload, task_id):
            calls.append(payload['timeout_s'])
            return {'html_code': await two.checkpoint_get('code-output')}
        two = self.worker(recovered)
        result = await self.wait_status(two, snapshot.task_id, {'succeeded'})
        record = await two.record(snapshot.task_id)
        self.assertEqual(record['attempt'], 2)
        self.assertEqual(record['deadline'], deadline)
        self.assertLessEqual(calls[1], calls[0])
        self.assertEqual(result.result['html_code'], '<html>saved output</html>')

    async def test_two_workers_and_duplicate_delivery_only_execute_once(self):
        run = AsyncMock(return_value={'ok':True})
        one, two = self.worker(run), self.worker(run)
        (first, _), (duplicate, dedup) = await asyncio.gather(self.submit(one), self.submit(two))
        self.assertEqual(first.task_id, duplicate.task_id)
        await self.wait_status(one, first.task_id, {'succeeded'})
        self.assertEqual(run.await_count, 1)
        other, _ = await self.submit(two, user='another-user')
        self.assertNotEqual(first.task_id, other.task_id)

    async def test_cancel_from_other_replica_fences_late_result(self):
        started, released = asyncio.Event(), asyncio.Event()
        async def run(*args):
            started.set()
            await released.wait()
            return {'must_not_publish':True}
        one, two = self.worker(run), self.worker(run)
        snapshot, _ = await self.submit(one)
        await started.wait()
        await two.cancel(snapshot.task_id)
        released.set()
        await asyncio.sleep(.07)
        final = await one.get(snapshot.task_id)
        self.assertEqual(final.status, 'canceled')
        self.assertIsNone(final.result)

    async def test_expired_deadline_does_not_start_model_call(self):
        run = AsyncMock()
        one = self.worker(run)
        snapshot, _ = await self.submit(one, timeout=0)
        final = await self.wait_status(one, snapshot.task_id, {'failed'})
        self.assertEqual(final.error.failure_family, 'timeout')
        run.assert_not_awaited()

    async def test_redis_failure_does_not_acknowledge_or_execute(self):
        run = AsyncMock()
        one = self.worker(run)
        self.server.connected = False
        with self.assertRaises(Exception): await self.submit(one)
        run.assert_not_awaited()
        self.assertIsNone(one.loop)

    async def test_old_owner_cannot_overwrite_recovered_attempt(self):
        one = self.worker(AsyncMock(return_value={'ok':True}))
        snapshot, _ = await self.submit(one)
        await self.wait_status(one, snapshot.task_id, {'succeeded'})
        touched = await one.owned_change(snapshot.task_id, 'stale-owner', lambda record: {**record,'attempt':999})
        self.assertIsNone(touched)
        self.assertEqual((await one.record(snapshot.task_id))['attempt'], 1)
