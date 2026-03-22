"""Tests for async task endpoints and manager behavior."""

import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import AsyncTaskStatus, AsyncTaskType, GameSpec, RunPipelineResponse
from src.main import app
from src.services.async_task_manager import AsyncTaskManager, task_manager


class TestAsyncTaskManager(unittest.TestCase):
    def test_list_total_counts_matches_before_limit(self):
        manager = AsyncTaskManager()

        async def scenario():
            async def runner(_task_id: str):
                await asyncio.sleep(0)
                return {"ok": True}

            await manager.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-1",
                user_id="user-1",
                timeout_s=600,
                runner=runner,
            )
            await manager.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-2",
                user_id="user-1",
                timeout_s=600,
                runner=runner,
            )
            await manager.create_task(
                task_type=AsyncTaskType.pipeline_iterate,
                game_id="game-3",
                user_id="user-1",
                timeout_s=600,
                runner=runner,
            )

            await asyncio.sleep(0.05)
            listing = await manager.list_tasks(user_id="user-1", limit=2)

            self.assertEqual(listing.total, 3)
            self.assertEqual(len(listing.items), 2)
            self.assertTrue(all(item.status == AsyncTaskStatus.succeeded for item in listing.items))

        asyncio.run(scenario())

    def test_cancel_marks_running_task(self):
        manager = AsyncTaskManager()

        async def scenario():
            started = asyncio.Event()

            async def runner(_task_id: str):
                started.set()
                await asyncio.sleep(10)

            handle = await manager.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-cancel",
                user_id="user-cancel",
                timeout_s=600,
                runner=runner,
            )
            await started.wait()

            snapshot = await manager.cancel_task(handle.task_id)
            await asyncio.sleep(0)

            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.status, AsyncTaskStatus.canceled)

        asyncio.run(scenario())


class TestAsyncTaskApi(unittest.TestCase):
    def tearDown(self):
        asyncio.run(task_manager.clear())

    def test_run_pipeline_async_returns_task_handle_and_result(self):
        fake_result = RunPipelineResponse(
            game_id="game-async",
            html_code="<!DOCTYPE html><html></html>",
            game_spec=GameSpec(game_type="runner"),
            strategy="llm",
            qa_passed=True,
            qa_retries=0,
            generation_time_ms=1234,
            code_size_bytes=42,
            quality_score=8.8,
            quality_breakdown={"qa_penalty": 0},
        )

        with patch(
            "src.api.endpoints.generate._run_pipeline_internal",
            new=AsyncMock(return_value=fake_result),
        ) as mock_run:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/pipeline/run/async",
                    json={
                        "game_id": "game-async",
                        "description": "make a runner game",
                        "user_id": "user-async",
                        "timeout_s": 120,
                    },
                )

                self.assertEqual(response.status_code, 202)
                handle = response.json()
                self.assertEqual(handle["task_type"], "pipeline_run")
                self.assertEqual(handle["timeout_s"], 120)
                self.assertIn("/api/v1/ai/tasks/", handle["poll_url"])

                time.sleep(0.05)
                task_response = client.get(handle["poll_url"])

        self.assertEqual(task_response.status_code, 200)
        payload = task_response.json()
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["result"]["game_id"], "game-async")
        self.assertIsNotNone(mock_run.await_args)
        self.assertEqual(mock_run.await_args.kwargs["task_id"], handle["task_id"])

    def test_sync_pipeline_run_accepts_timeout_override(self):
        fake_result = RunPipelineResponse(
            game_id="game-sync",
            html_code="<!DOCTYPE html><html></html>",
            game_spec=GameSpec(game_type="runner"),
            strategy="llm",
            qa_passed=True,
            qa_retries=0,
            generation_time_ms=1234,
            code_size_bytes=42,
            quality_score=8.8,
            quality_breakdown={"qa_penalty": 0},
        )

        with patch(
            "src.api.endpoints.generate._run_pipeline_internal",
            new=AsyncMock(return_value=fake_result),
        ) as mock_run:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/pipeline/run",
                    json={
                        "game_id": "game-sync",
                        "description": "make a runner game",
                        "user_id": "user-sync",
                        "timeout_s": 150,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["game_id"], "game-sync")
        self.assertEqual(mock_run.await_args.args[0].timeout_s, 150)


if __name__ == "__main__":
    unittest.main()
