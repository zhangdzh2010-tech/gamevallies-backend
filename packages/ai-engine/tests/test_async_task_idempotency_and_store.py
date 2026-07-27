"""Tests for idempotent async task submission and the Redis-backed task store."""

import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    AsyncTaskStatus,
    AsyncTaskType,
    GameSpec,
    RunPipelineResponse,
)
from src.main import app
from src.services import async_task_store
from src.services.async_task_manager import AsyncTaskManager, task_manager
from src.services.async_task_store import AsyncTaskRedisStore


def _fake_run_result(game_id: str) -> RunPipelineResponse:
    return RunPipelineResponse(
        game_id=game_id,
        html_code="<!DOCTYPE html><html></html>",
        game_spec=GameSpec(game_type="casual"),
        strategy="llm",
        qa_passed=True,
        qa_retries=0,
        generation_time_ms=1234,
        code_size_bytes=42,
        quality_score=8.8,
        quality_breakdown={"qa_penalty": 0},
        pipeline_version="v2",
    )


def _fake_store(server: FakeServer) -> AsyncTaskRedisStore:
    return AsyncTaskRedisStore(client=FakeRedis(server=server, decode_responses=True))


class _BrokenRedis:
    """Stub client whose every command fails, to exercise degradation paths."""

    def __getattr__(self, name):
        async def failing(*args, **kwargs):
            raise ConnectionError("redis is down")

        return failing

    def pipeline(self, *args, **kwargs):
        raise ConnectionError("redis is down")


class TestIdempotentSubmissionInMemory(unittest.TestCase):
    """Idempotency behavior with the store disabled (pure in-memory path)."""

    def _create(self, manager, *, key=None, game_id="game-idem", calls=None):
        async def runner(_task_id: str):
            if calls is not None:
                calls.append(_task_id)
            return {"ok": True}

        return manager.create_task(
            task_type=AsyncTaskType.pipeline_run,
            game_id=game_id,
            user_id="user-idem",
            timeout_s=600,
            runner=runner,
            idempotency_key=key,
        )

    def test_same_key_returns_same_task_without_new_run(self):
        manager = AsyncTaskManager(store=AsyncTaskRedisStore(redis_url=""))

        async def scenario():
            calls: list[str] = []
            first = await self._create(manager, key="idem-key-1", calls=calls)
            await asyncio.sleep(0.05)
            second = await self._create(manager, key="idem-key-1", calls=calls)

            self.assertEqual(first.task_id, second.task_id)
            self.assertFalse(first.deduplicated)
            self.assertTrue(second.deduplicated)
            self.assertEqual(calls, [first.task_id])

        asyncio.run(scenario())

    def test_different_keys_create_different_tasks(self):
        manager = AsyncTaskManager(store=AsyncTaskRedisStore(redis_url=""))

        async def scenario():
            first = await self._create(manager, key="idem-key-a")
            second = await self._create(manager, key="idem-key-b")

            self.assertNotEqual(first.task_id, second.task_id)
            self.assertFalse(first.deduplicated)
            self.assertFalse(second.deduplicated)

        asyncio.run(scenario())

    def test_no_key_always_creates_new_tasks(self):
        manager = AsyncTaskManager(store=AsyncTaskRedisStore(redis_url=""))

        async def scenario():
            calls: list[str] = []
            first = await self._create(manager, calls=calls)
            second = await self._create(manager, calls=calls)
            blank = await self._create(manager, key="   ", calls=calls)
            await asyncio.sleep(0.05)

            self.assertEqual(len({first.task_id, second.task_id, blank.task_id}), 3)
            self.assertEqual(len(calls), 3)

        asyncio.run(scenario())


class TestIdempotentSubmissionApi(unittest.TestCase):
    """API-level idempotency via X-Idempotency-Key header / body field."""

    def tearDown(self):
        asyncio.run(task_manager.clear())

    def _post_run_async(self, client, *, headers=None, extra_body=None):
        body = {
            "game_id": "game-idem-api",
            "raw_user_input": "make a runner game",
            "user_id": "user-idem-api",
            "timeout_s": 120,
            **(extra_body or {}),
        }
        return client.post("/api/v1/ai/pipeline/v2/run/async", json=body, headers=headers or {})

    def test_header_key_deduplicates_resubmission(self):
        with patch(
            "src.api.endpoints.generate._run_pipeline_v2_internal",
            new=AsyncMock(return_value=_fake_run_result("game-idem-api")),
        ) as mock_run:
            with TestClient(app) as client:
                first = self._post_run_async(client, headers={"X-Idempotency-Key": "req-123"})
                time.sleep(0.05)
                second = self._post_run_async(client, headers={"X-Idempotency-Key": "req-123"})
                third = self._post_run_async(client, headers={"X-Idempotency-Key": "req-456"})
                time.sleep(0.05)

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertFalse(first.json()["deduplicated"])
        self.assertTrue(second.json()["deduplicated"])
        self.assertNotEqual(first.json()["task_id"], third.json()["task_id"])
        # Existing handle fields must be untouched.
        for field in ("task_type", "status", "game_id", "user_id", "timeout_s", "ws_channel", "poll_url", "cancel_url"):
            self.assertIn(field, first.json())
        # The pipeline only ran once for the deduplicated key.
        run_task_ids = {call.kwargs["task_id"] for call in mock_run.await_args_list}
        self.assertEqual(len(mock_run.await_args_list), 2)
        self.assertIn(first.json()["task_id"], run_task_ids)
        self.assertIn(third.json()["task_id"], run_task_ids)

    def test_body_field_key_deduplicates_resubmission(self):
        with patch(
            "src.api.endpoints.generate._run_pipeline_v2_internal",
            new=AsyncMock(return_value=_fake_run_result("game-idem-api")),
        ):
            with TestClient(app) as client:
                first = self._post_run_async(client, extra_body={"idempotency_key": "body-key-1"})
                time.sleep(0.05)
                second = self._post_run_async(client, extra_body={"idempotency_key": "body-key-1"})

        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertTrue(second.json()["deduplicated"])

    def test_no_key_behavior_unchanged(self):
        with patch(
            "src.api.endpoints.generate._run_pipeline_v2_internal",
            new=AsyncMock(return_value=_fake_run_result("game-idem-api")),
        ):
            with TestClient(app) as client:
                first = self._post_run_async(client)
                second = self._post_run_async(client)
                time.sleep(0.05)

        self.assertNotEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertFalse(first.json()["deduplicated"])
        self.assertFalse(second.json()["deduplicated"])


class TestRedisTaskStore(unittest.TestCase):
    """Redis persistence path exercised with fakeredis."""

    def test_snapshot_recovered_after_memory_loss(self):
        async def scenario():
            server = FakeServer()
            manager1 = AsyncTaskManager(store=_fake_store(server))

            async def runner(_task_id: str):
                return {"game_id": "game-redis", "html_code": "<html>redis</html>"}

            handle = await manager1.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-redis",
                user_id="user-redis",
                timeout_s=600,
                runner=runner,
            )
            await asyncio.sleep(0.05)

            # Simulate instance recycling: brand-new manager with empty memory.
            manager2 = AsyncTaskManager(store=_fake_store(server))
            snapshot = await manager2.get_task(handle.task_id)

            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.status, AsyncTaskStatus.succeeded)
            self.assertEqual(snapshot.result["game_id"], "game-redis")
            self.assertEqual(snapshot.result["html_code"], "<html>redis</html>")
            self.assertEqual(snapshot.ws_channel, "game:game-redis")

        asyncio.run(scenario())

    def test_idempotency_key_deduplicates_across_instances(self):
        async def scenario():
            server = FakeServer()
            manager1 = AsyncTaskManager(store=_fake_store(server))
            manager2 = AsyncTaskManager(store=_fake_store(server))
            second_runner_calls: list[str] = []

            async def runner1(_task_id: str):
                return {"ok": 1}

            async def runner2(_task_id: str):
                second_runner_calls.append(_task_id)
                return {"ok": 2}

            first = await manager1.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-cross",
                user_id="user-cross",
                timeout_s=600,
                runner=runner1,
                idempotency_key="cross-key-1",
            )
            await asyncio.sleep(0.05)

            second = await manager2.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-cross",
                user_id="user-cross",
                timeout_s=600,
                runner=runner2,
                idempotency_key="cross-key-1",
            )
            await asyncio.sleep(0.05)

            self.assertEqual(first.task_id, second.task_id)
            self.assertTrue(second.deduplicated)
            self.assertEqual(second_runner_calls, [])

        asyncio.run(scenario())

    def test_oversized_result_is_omitted_with_marker(self):
        async def scenario():
            server = FakeServer()
            manager = AsyncTaskManager(store=_fake_store(server))
            large_html = "x" * 4096

            async def runner(_task_id: str):
                return {
                    "game_id": "game-large",
                    "pipeline_version": "v2",
                    "primary_artifact_id": "artifact-large-1",
                    "html_code": large_html,
                }

            with patch.object(async_task_store, "MAX_RESULT_BYTES", 1024):
                handle = await manager.create_task(
                    task_type=AsyncTaskType.pipeline_run,
                    game_id="game-large",
                    user_id="user-large",
                    timeout_s=600,
                    runner=runner,
                )
                await asyncio.sleep(0.05)

            recovered = await AsyncTaskManager(store=_fake_store(server)).get_task(handle.task_id)

            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.status, AsyncTaskStatus.succeeded)
            self.assertTrue(recovered.result["result_omitted"])
            self.assertEqual(recovered.result["omitted_reason"], "redis_record_too_large")
            self.assertEqual(recovered.result["primary_artifact_id"], "artifact-large-1")
            self.assertNotIn("html_code", recovered.result)

            # In-memory snapshot keeps the full result.
            local = await manager.get_task(handle.task_id)
            self.assertEqual(local.result["html_code"], large_html)

        asyncio.run(scenario())

    def test_progress_writes_are_throttled(self):
        async def scenario():
            server = FakeServer()
            store = _fake_store(server)
            manager = AsyncTaskManager(store=store)
            release = asyncio.Event()

            async def runner(_task_id: str):
                await release.wait()
                return {"ok": True}

            handle = await manager.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-throttle",
                user_id="user-throttle",
                timeout_s=600,
                runner=runner,
            )
            await asyncio.sleep(0.02)

            await manager.update_progress(handle.task_id, stage="stage-one", pct=10, message="one")
            await manager.update_progress(handle.task_id, stage="stage-two", pct=20, message="two")

            persisted = await store.load_snapshot(handle.task_id)
            self.assertEqual(persisted.progress.stage, "stage-one")

            # In-memory state still tracks the latest progress.
            local = await manager.get_task(handle.task_id)
            self.assertEqual(local.progress.stage, "stage-two")

            # Once the throttle window elapses, the next update is persisted.
            manager._progress_persisted_at[handle.task_id] = 0.0
            await manager.update_progress(handle.task_id, stage="stage-three", pct=30, message="three")
            persisted = await store.load_snapshot(handle.task_id)
            self.assertEqual(persisted.progress.stage, "stage-three")

            release.set()
            await asyncio.sleep(0.02)

        asyncio.run(scenario())

    def test_redis_failure_degrades_to_in_memory(self):
        async def scenario():
            manager = AsyncTaskManager(store=AsyncTaskRedisStore(client=_BrokenRedis()))

            async def runner(_task_id: str):
                return {"ok": True}

            with self.assertLogs("src.services.async_task_store", level="WARNING"):
                handle = await manager.create_task(
                    task_type=AsyncTaskType.pipeline_run,
                    game_id="game-broken",
                    user_id="user-broken",
                    timeout_s=600,
                    runner=runner,
                    idempotency_key="broken-key-1",
                )
                await asyncio.sleep(0.05)

            snapshot = await manager.get_task(handle.task_id)
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.status, AsyncTaskStatus.succeeded)

            # Idempotent resubmission still works through the in-memory index.
            second = await manager.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-broken",
                user_id="user-broken",
                timeout_s=600,
                runner=runner,
                idempotency_key="broken-key-1",
            )
            self.assertEqual(second.task_id, handle.task_id)
            self.assertTrue(second.deduplicated)

        asyncio.run(scenario())

    def test_store_disabled_when_redis_url_empty(self):
        async def scenario():
            store = AsyncTaskRedisStore(redis_url="")
            self.assertFalse(store.enabled())
            self.assertIsNone(await store.load_snapshot("missing-task"))
            self.assertIsNone(await store.lookup_idempotency_key("missing-key"))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
