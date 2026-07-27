"""Tests for async task endpoints and manager behavior."""

import asyncio
import os
import sys
import time
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    AsyncTaskStatus,
    AsyncTaskType,
    GameSpec,
    IterateResponse,
    IterateV2Request,
    RunPipelineResponse,
    RunPipelineV2Request,
    VisualStyle,
)
from src.api.endpoints import generate as generate_api
from src.engine.pipeline_errors import PipelineExecutionError
from src.main import app
from src.services.async_task_manager import AsyncTaskManager, task_manager
from src.services.task_memory import task_memory

TEST_V2_PROMPT_BUNDLE_SNAPSHOT = {
    "bundle_id": "runtime-v2-default",
    "bundle_version": 1,
    "resolved_at": "2026-03-24T00:00:00+00:00",
    "layers": {
        "entrypoint": "test",
        "source": "unit-test",
    },
}

TEST_V2_RUNTIME_CONTRACT = {
    "version": "1.0",
    "runtime_profile": "casual_arcade",
    "canvas": {
        "requires_canvas_2d": True,
        "must_render_within_ms": 1500,
        "orientation": "portrait_first",
        "ui_scale_mode": "short_edge",
        "target_fps": 60,
    },
    "input": {
        "required_modes": ["pointer", "touch"],
        "gestures": ["tap"],
    },
    "state": {
        "required_states": ["boot", "ready", "playing", "game_over"],
        "restartable": True,
    },
    "mobile_layout": {
        "orientation": "portrait_first",
        "ui_scale_mode": "short_edge",
        "font_clamp": {
            "hud_min": 14,
            "hud_max": 20,
            "title_min": 28,
            "title_max": 36,
        },
    },
    "safety": {
        "forbidden_apis": ["eval", "Function", "fetch", "XMLHttpRequest"],
    },
    "gameplay": {},
    "metadata": {
        "source": "unit-test",
    },
}


def _resolve_test_prompt_bundle(snapshot, runtime_profile=None):
    layers = dict(snapshot.layers or {})
    layers["profile_few_shot"] = runtime_profile or "casual_arcade"
    layers["resolved_prompts"] = {
        "locked_contract": {"key": "bundle.runtime.locked_contract", "content": "LOCKED CONTRACT"},
        "product_policy": {"key": "bundle.product.policy", "content": "PRODUCT POLICY"},
        "intent_parse": {"key": "bundle.product.intent_parse", "content": "INTENT PARSE"},
        "logic_generate": {"key": "bundle.product.logic_generate", "content": "LOGIC GENERATE"},
        "profile_few_shot": {
            "key": f"bundle.runtime.profile.{runtime_profile or 'casual_arcade'}",
            "content": "PROFILE FEW SHOT",
        },
        "repair_syntax_structural": {
            "key": "bundle.repair.syntax_structural",
            "content": "REPAIR SYNTAX STRUCTURAL",
        },
    }
    return snapshot.model_copy(update={"resolved_at": snapshot.resolved_at or TEST_V2_PROMPT_BUNDLE_SNAPSHOT["resolved_at"], "layers": layers})


@contextmanager
def patch_v2_prompt_defaults():
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "src.api.endpoints.generate.resolve_prompt_bundle_snapshot",
                side_effect=_resolve_test_prompt_bundle,
            )
        )
        yield


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

    def test_task_timeout_marks_task_failed(self):
        manager = AsyncTaskManager()

        async def scenario():
            async def runner(_task_id: str):
                await asyncio.sleep(2)

            handle = await manager.create_task(
                task_type=AsyncTaskType.pipeline_run,
                game_id="game-timeout",
                user_id="user-timeout",
                timeout_s=1,
                runner=runner,
            )

            await asyncio.sleep(1.1)
            snapshot = await manager.get_task(handle.task_id)

            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.status, AsyncTaskStatus.failed)
            self.assertEqual(snapshot.error.message, "Task timed out")

        asyncio.run(scenario())


class TestAsyncTaskApi(unittest.TestCase):
    def tearDown(self):
        asyncio.run(task_manager.clear())

    def test_make_progress_cb_fanout_is_ordered(self):
        async def scenario():
            task_progress: list[tuple[str, int]] = []
            ws_progress: list[tuple[str, int]] = []
            relayed_progress: list[tuple[str, int]] = []

            async def fake_update_progress(_task_id, *, stage, pct, message, details=None):
                if stage == "stage-one":
                    await asyncio.sleep(0.02)
                task_progress.append((stage, details["progressSeq"]))

            async def fake_send_progress(_game_id, stage, pct, message, details=None):
                ws_progress.append((stage, details["progressSeq"]))

            async def fake_relay_progress(**kwargs):
                relayed_progress.append((kwargs["stage"], kwargs["details"]["progressSeq"]))

            with patch.object(generate_api.task_manager, "update_progress", new=AsyncMock(side_effect=fake_update_progress)), patch.object(
                generate_api.manager,
                "send_progress",
                new=AsyncMock(side_effect=fake_send_progress),
            ), patch.object(
                generate_api,
                "_relay_progress_to_game_service",
                new=AsyncMock(side_effect=fake_relay_progress),
            ):
                callback = generate_api._make_progress_cb(
                    game_id="game-progress",
                    user_id="user-progress",
                    task_id="task-progress",
                )
                callback("stage-one", 10, "first")
                callback("stage-two", 20, "second")
                await asyncio.sleep(0.08)

            self.assertEqual(task_progress, [("stage-one", 1), ("stage-two", 2)])
            self.assertEqual(ws_progress, [("stage-one", 1), ("stage-two", 2)])
            self.assertEqual(relayed_progress, [("stage-one", 1), ("stage-two", 2)])

        asyncio.run(scenario())

    def test_run_pipeline_v2_async_returns_task_handle_and_result(self):
        fake_result = RunPipelineResponse(
            game_id="game-async",
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
            primary_artifact_id="artifact-success-async",
        )

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._run_pipeline_v2_internal",
            new=AsyncMock(return_value=fake_result),
        ) as mock_run:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/pipeline/v2/run/async",
                    json={
                        "game_id": "game-async",
                        "raw_user_input": "make a runner game",
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
        self.assertEqual(payload["result"]["pipeline_version"], "v2")
        self.assertEqual(payload["result"]["primary_artifact_id"], "artifact-success-async")
        self.assertIsNotNone(mock_run.await_args)
        self.assertEqual(mock_run.await_args.kwargs["task_id"], handle["task_id"])
        self.assertEqual(mock_run.await_args.args[0].raw_user_input, "make a runner game")
        self.assertEqual(mock_run.await_args.args[0].request_context.entrypoint, "create")

    def test_sync_pipeline_v2_run_accepts_timeout_override(self):
        fake_result = RunPipelineResponse(
            game_id="game-sync",
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
            primary_artifact_id="artifact-success-sync",
        )

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._run_pipeline_v2_internal",
            new=AsyncMock(return_value=fake_result),
        ) as mock_run:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/pipeline/v2/run",
                    json={
                        "game_id": "game-sync",
                        "raw_user_input": "make a runner game",
                        "user_id": "user-sync",
                        "timeout_s": 150,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["game_id"], "game-sync")
        self.assertEqual(response.json()["pipeline_version"], "v2")
        self.assertEqual(response.json()["primary_artifact_id"], "artifact-success-sync")
        self.assertEqual(mock_run.await_args.args[0].timeout_s, 150)
        self.assertEqual(mock_run.await_args.args[0].raw_user_input, "make a runner game")
        self.assertEqual(mock_run.await_args.args[0].request_context.entrypoint, "create")

    def test_async_pipeline_failure_preserves_failure_family_and_primary_artifact(self):
        failure = HTTPException(
            status_code=500,
            detail={
                "message": "Contract QA failed",
                "failed_stage": "qa_checking",
                "retry_count": 2,
                "failure_family": "contract_qa",
                "primary_artifact_id": "artifact-failure-1",
            },
        )

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._run_pipeline_v2_internal",
            new=AsyncMock(side_effect=failure),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/pipeline/v2/run/async",
                    json={
                        "game_id": "game-fail",
                        "raw_user_input": "make a broken game",
                        "user_id": "user-fail",
                        "timeout_s": 120,
                    },
                )

                self.assertEqual(response.status_code, 202)
                handle = response.json()
                time.sleep(0.05)
                task_response = client.get(handle["poll_url"])

        self.assertEqual(task_response.status_code, 200)
        payload = task_response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["failed_stage"], "qa_checking")
        self.assertEqual(payload["error"]["failure_family"], "contract_qa")
        self.assertEqual(payload["error"]["primary_artifact_id"], "artifact-failure-1")

    def test_async_pipeline_unexpected_cancellation_is_relayed_as_failed(self):
        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._run_pipeline_v2_internal",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
        ), patch(
            "src.api.endpoints.generate._relay_task_failure_to_game_service",
            new=AsyncMock(),
        ) as relay_failure, patch(
            "src.api.endpoints.generate._relay_stage_summary_to_game_service",
            new=AsyncMock(),
        ) as relay_stage_summary:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/pipeline/v2/run/async",
                    json={
                        "game_id": "game-cancel-failed",
                        "raw_user_input": "make a runner game",
                        "user_id": "user-cancel-failed",
                        "timeout_s": 120,
                    },
                )

                self.assertEqual(response.status_code, 202)
                handle = response.json()
                time.sleep(0.05)
                task_response = client.get(handle["poll_url"])

        self.assertEqual(task_response.status_code, 200)
        payload = task_response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["failed_stage"], "pipeline_run")
        self.assertEqual(payload["error"]["failure_family"], "pipeline")
        self.assertIsNotNone(relay_failure.await_args)
        self.assertEqual(relay_failure.await_args.kwargs["task_id"], handle["task_id"])
        self.assertEqual(relay_failure.await_args.kwargs["failed_stage"], "pipeline_run")
        self.assertIsNotNone(relay_stage_summary.await_args)

    def test_async_cancellation_helper_skips_relay_for_user_canceled_tasks(self):
        async def scenario():
            manager = AsyncTaskManager()
            relay_failure = AsyncMock()
            relay_stage_summary = AsyncMock()

            async def runner(_task_id: str):
                await asyncio.sleep(10)

            with patch(
                "src.api.endpoints.generate.task_manager",
                manager,
            ), patch(
                "src.api.endpoints.generate._relay_task_failure_to_game_service",
                new=relay_failure,
            ), patch(
                "src.api.endpoints.generate._relay_stage_summary_to_game_service",
                new=relay_stage_summary,
            ):
                handle = await manager.create_task(
                    task_type=AsyncTaskType.pipeline_run,
                    game_id="game-user-canceled",
                    user_id="user-user-canceled",
                    timeout_s=600,
                    runner=runner,
                )
                await asyncio.sleep(0.02)
                await manager.cancel_task(handle.task_id)
                result = await generate_api._handle_async_runner_cancellation(
                    task_id=handle.task_id,
                    game_id="game-user-canceled",
                    fallback_stage="pipeline_run",
                    default_message="Async create pipeline was canceled before completion",
                )

            self.assertIsNone(result)
            relay_failure.assert_not_awaited()
            relay_stage_summary.assert_not_awaited()

        asyncio.run(scenario())

    def test_v2_failure_persists_runner_artifacts_and_promotes_candidate_as_primary(self):
        request = RunPipelineV2Request(
            game_id="game-v2-failure-artifacts",
            user_id="user-v2-failure-artifacts",
            raw_user_input="make a tiny tap game",
        )
        failure = PipelineExecutionError(
            "Generated code failed runtime QA: Runtime QA detected no registered user input handlers",
            stage="runtime_simulation_qa",
            failure_family="runtime_qa",
            artifacts=[
                {
                    "artifact_type": "failed_runtime_candidate",
                    "content_type": "text/html",
                    "payload": "<!DOCTYPE html><html><body>candidate</body></html>",
                    "metadata": {"stage": "runtime_simulation_qa"},
                },
                {
                    "artifact_type": "runtime_qa_report",
                    "content_type": "application/json",
                    "payload": {"errors": [{"message": "missing input handlers"}]},
                    "metadata": {"stage": "runtime_simulation_qa"},
                },
            ],
        )

        relay_artifact = AsyncMock(
            side_effect=[
                "artifact-failed-candidate",
                "artifact-runtime-report",
                "artifact-task-failure",
            ]
        )

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._persist_v2_request_artifacts",
            new=AsyncMock(return_value=[]),
        ), patch(
            "src.api.endpoints.generate._relay_stage_summary_to_game_service",
            new=AsyncMock(),
        ), patch(
            "src.api.endpoints.generate._relay_task_failure_to_game_service",
            new=AsyncMock(),
        ), patch(
            "src.api.endpoints.generate._relay_artifact_to_game_service",
            new=relay_artifact,
        ), patch(
            "src.api.endpoints.generate._v2_runner.run",
            new=AsyncMock(side_effect=failure),
        ):
            with self.assertRaises(PipelineExecutionError) as ctx:
                asyncio.run(
                    generate_api._run_pipeline_v2_internal(
                        request,
                        task_id="task-v2-failure-artifacts",
                    )
                )

        self.assertEqual(ctx.exception.failure_family, "runtime_qa")
        self.assertEqual(ctx.exception.primary_artifact_id, "artifact-failed-candidate")
        await_args = relay_artifact.await_args_list
        self.assertEqual(await_args[0].kwargs["artifact_type"], "failed_runtime_candidate")
        self.assertEqual(await_args[1].kwargs["artifact_type"], "runtime_qa_report")
        self.assertEqual(await_args[2].kwargs["artifact_type"], "task_failure")

    def test_v2_failure_records_structured_diagnostics_for_opaque_exception_messages(self):
        request = RunPipelineV2Request(
            game_id="game-v2-opaque-failure",
            user_id="user-v2-opaque-failure",
            raw_user_input="make a puzzle game",
        )
        relay_artifact = AsyncMock(return_value="artifact-task-failure")

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._persist_v2_request_artifacts",
            new=AsyncMock(return_value=[]),
        ), patch(
            "src.api.endpoints.generate._relay_stage_summary_to_game_service",
            new=AsyncMock(),
        ), patch(
            "src.api.endpoints.generate._relay_task_failure_to_game_service",
            new=AsyncMock(),
        ), patch(
            "src.api.endpoints.generate._relay_artifact_to_game_service",
            new=relay_artifact,
        ), patch(
            "src.api.endpoints.generate._v2_runner.run",
            new=AsyncMock(side_effect=KeyError(4)),
        ):
            with self.assertRaises(KeyError):
                asyncio.run(
                    generate_api._run_pipeline_v2_internal(
                        request,
                        task_id="task-v2-opaque-failure",
                    )
                )

        payload = relay_artifact.await_args.kwargs["payload"]
        self.assertIn("KeyError", payload["message"])
        self.assertEqual(payload["diagnostics"]["exceptionClass"], "KeyError")
        self.assertIn("KeyError", payload["diagnostics"]["exceptionRepr"])
        self.assertIn("KeyError", payload["diagnostics"]["tracebackExcerpt"])

    def test_v2_internal_uses_v2_runner(self):
        request = RunPipelineV2Request(
            game_id="game-v2-internal",
            user_id="user-v2-internal",
            raw_user_input="make a runner game",
        )
        fake_result = RunPipelineResponse(
            game_id="game-v2-internal",
            html_code="<!DOCTYPE html><html></html>",
            game_spec=GameSpec(game_type="casual"),
            strategy="llm",
            qa_passed=True,
            qa_retries=1,
            generation_time_ms=789,
            code_size_bytes=64,
            quality_score=8.9,
            quality_breakdown={"qa_penalty": 0},
            runtime_profile="casual_lane",
            contract_version="1.0",
        )

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._v2_runner.run",
            new=AsyncMock(return_value=fake_result),
        ) as mock_runner:
            response = asyncio.run(
                generate_api._run_pipeline_v2_internal(request, task_id="task-v2-internal")
            )

        self.assertEqual(response.pipeline_version, "v2")
        self.assertEqual(response.runtime_profile, "casual_lane")
        self.assertIsNotNone(mock_runner.await_args)

    def test_v2_internal_initializes_and_clears_task_memory(self):
        request = RunPipelineV2Request(
            game_id="game-v2-memory",
            user_id="user-v2-memory",
            raw_user_input="make a funny game about office chaos",
            title="Office Chaos",
        )
        fake_result = RunPipelineResponse(
            game_id="game-v2-memory",
            html_code="<!DOCTYPE html><html></html>",
            game_spec=GameSpec(game_type="funny"),
            strategy="llm",
            qa_passed=True,
            qa_retries=0,
            generation_time_ms=100,
            code_size_bytes=64,
            quality_score=8.5,
            quality_breakdown={"qa_penalty": 0},
            runtime_profile="casual_arcade",
            contract_version="1.0",
        )

        async def fake_run(*args, **kwargs):
            record = await task_memory.get("task-v2-memory")
            self.assertIsNotNone(record)
            self.assertEqual(record.task_meta.get("entrypoint"), "create")
            self.assertEqual(record.task_meta.get("title"), "Office Chaos")
            return fake_result

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._v2_runner.run",
            new=AsyncMock(side_effect=fake_run),
        ), patch(
            "src.api.endpoints.generate._relay_stage_summary_to_game_service",
            new=AsyncMock(),
        ):
            response = asyncio.run(
                generate_api._run_pipeline_v2_internal(
                    request,
                    task_id="task-v2-memory",
                )
            )

        self.assertEqual(response.runtime_profile, "casual_arcade")
        self.assertIsNone(asyncio.run(task_memory.get("task-v2-memory")))

    def test_v2_create_internal_does_not_persist_iteration_history_artifacts(self):
        request = RunPipelineV2Request(
            game_id="game-v2-create-artifacts",
            user_id="user-v2-create-artifacts",
            raw_user_input="make a runner game",
        )
        fake_result = RunPipelineResponse(
            game_id="game-v2-create-artifacts",
            html_code="<!DOCTYPE html><html></html>",
            game_spec=GameSpec(game_type="casual"),
            strategy="llm",
            qa_passed=True,
            qa_retries=1,
            generation_time_ms=789,
            code_size_bytes=64,
            quality_score=8.9,
            quality_breakdown={"qa_penalty": 0},
            runtime_profile="casual_lane",
            contract_version="1.0",
        )
        relay_artifact = AsyncMock(
            side_effect=lambda **kwargs: f"artifact-{kwargs['artifact_type']}"
        )

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._v2_runner.run",
            new=AsyncMock(return_value=fake_result),
        ), patch(
            "src.api.endpoints.generate._relay_artifact_to_game_service",
            new=relay_artifact,
        ), patch(
            "src.api.endpoints.generate._relay_stage_summary_to_game_service",
            new=AsyncMock(),
        ):
            asyncio.run(
                generate_api._run_pipeline_v2_internal(
                    request,
                    task_id="task-v2-create-artifacts",
                )
            )

        artifact_types = [call.kwargs["artifact_type"] for call in relay_artifact.await_args_list]
        self.assertNotIn("source_game_spec", artifact_types)
        self.assertNotIn("source_bundle_context", artifact_types)

    def test_v2_create_internal_relays_cover_image_when_capture_succeeds(self):
        request = RunPipelineV2Request(
            game_id="game-v2-cover-artifacts",
            user_id="user-v2-cover-artifacts",
            raw_user_input="make a runner game",
            title="Wide Runner",
        )
        fake_result = RunPipelineResponse(
            game_id="game-v2-cover-artifacts",
            html_code="<!DOCTYPE html><html><body>cover</body></html>",
            game_spec=GameSpec(
                game_type="casual",
                visual_style=VisualStyle(
                    theme="arcade",
                    visual_pack="neon_glass",
                    render_style_intensity="high",
                ),
            ),
            strategy="llm",
            qa_passed=True,
            qa_retries=1,
            generation_time_ms=789,
            code_size_bytes=64,
            quality_score=8.9,
            quality_breakdown={"qa_penalty": 0},
            runtime_profile="casual_lane",
            contract_version="1.0",
        )
        relay_artifact = AsyncMock(
            side_effect=lambda **kwargs: f"artifact-{kwargs['artifact_type']}"
        )
        capture_cover = AsyncMock(return_value={
            "payload": "ZmFrZS1jb3Zlcg==",
            "content_type": "image/jpeg",
            "metadata": {"selectedFrame": "settled_frame", "coverStyle": "runtime_frame_capture"},
        })

        with patch_v2_prompt_defaults(), patch.object(
            generate_api.settings,
            "GAME_SERVICE_UPSTREAM_URL",
            "http://game-service.test",
        ), patch(
            "src.api.endpoints.generate._v2_runner.run",
            new=AsyncMock(return_value=fake_result),
        ), patch(
            "src.api.endpoints.generate._relay_artifact_to_game_service",
            new=relay_artifact,
        ), patch(
            "src.api.endpoints.generate._relay_stage_summary_to_game_service",
            new=AsyncMock(),
        ), patch(
            "src.api.endpoints.generate._maybe_capture_cover_artifact",
            new=capture_cover,
        ):
            asyncio.run(
                generate_api._run_pipeline_v2_internal(
                    request,
                    task_id="task-v2-cover-artifacts",
                )
            )

        artifact_types = [call.kwargs["artifact_type"] for call in relay_artifact.await_args_list]
        self.assertIn("cover_image", artifact_types)
        self.assertEqual(capture_cover.await_args.kwargs["title"], "Wide Runner")
        self.assertEqual(capture_cover.await_args.kwargs["game_type"], "casual")
        self.assertEqual(capture_cover.await_args.kwargs["theme"], "arcade")
        self.assertEqual(capture_cover.await_args.kwargs["visual_pack"], "neon_glass")
        self.assertEqual(capture_cover.await_args.kwargs["render_style_intensity"], "high")
        self.assertFalse(capture_cover.await_args.kwargs.get("updated", False))

    def test_admin_cover_capture_endpoint_returns_captured_artifact(self):
        capture_cover = AsyncMock(return_value={
            "payload": "ZmFrZS1jb3Zlcg==",
            "content_type": "image/jpeg",
            "metadata": {
                "selectedFrame": "settled_frame",
                "coverStyle": "runtime_frame_capture",
                "coverVariant": "direct_runtime_frame_v2",
            },
        })

        with patch.object(generate_api.settings, "ADMIN_TOKEN", "admin-cover-token"), patch(
            "src.api.endpoints.generate._maybe_capture_cover_artifact",
            new=capture_cover,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/covers/capture",
                    headers={"x-admin-token": "admin-cover-token"},
                    json={
                        "game_id": "game-cover-capture",
                        "user_id": "user-cover-capture",
                        "html_code": "<!DOCTYPE html><html><body>cover</body></html>",
                        "orientation": "landscape_first",
                        "timeout_s": 12,
                        "title": "Orbital Office",
                        "game_type": "funny",
                        "theme": "neon_city",
                        "runtime_profile": "casual_arcade",
                        "visual_pack": "comic_bounce",
                        "render_style_intensity": "high",
                        "updated": False,
                    },
                )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["captured"])
        self.assertEqual(body["content_type"], "image/jpeg")
        self.assertEqual(body["payload"], "ZmFrZS1jb3Zlcg==")
        self.assertEqual(body["metadata"]["coverVariant"], "direct_runtime_frame_v2")
        self.assertEqual(capture_cover.await_args.kwargs["orientation"], "landscape_first")
        self.assertEqual(capture_cover.await_args.kwargs["title"], "Orbital Office")
        self.assertEqual(capture_cover.await_args.kwargs["visual_pack"], "comic_bounce")
        self.assertEqual(capture_cover.await_args.kwargs["render_style_intensity"], "high")

    def test_v2_iteration_internal_uses_v2_runner(self):
        request = IterateV2Request(
            game_id="game-v2-iter-internal",
            user_id="user-v2-iter-internal",
            current_code="<!DOCTYPE html><html><body>old</body></html>",
            iteration_intent={
                "feedback": "make it faster",
                "conversation": [],
            },
        )
        fake_result = IterateResponse(
            html_code="<!DOCTYPE html><html><body>new</body></html>",
            changes=["Applied: make it faster"],
            iteration_type="element_change",
            generation_time_ms=456,
            qa_retries=1,
            iteration_retries=0,
            runtime_profile="casual_lane",
            contract_version="1.0",
        )

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._v2_runner.iterate",
            new=AsyncMock(return_value=fake_result),
        ) as mock_runner:
            response = asyncio.run(
                generate_api._run_iteration_v2_internal(request, task_id="task-v2-iter-internal")
            )

        self.assertEqual(response.pipeline_version, "v2")
        self.assertEqual(response.runtime_profile, "casual_lane")
        self.assertIsNotNone(mock_runner.await_args)

    def test_v2_iteration_internal_passes_updated_cover_context(self):
        request = IterateV2Request(
            game_id="game-v2-iter-cover",
            user_id="user-v2-iter-cover",
            current_code="<!DOCTYPE html><html><body>old</body></html>",
            iteration_intent={
                "feedback": "add more hazards",
                "conversation": [],
            },
            source_bundle_context={
                "title": "Wide Runner",
                "latest_game_type": "runner",
            },
        )
        fake_result = IterateResponse(
            html_code="<!DOCTYPE html><html><body>new</body></html>",
            changes=["Applied: add more hazards"],
            iteration_type="element_change",
            game_spec=GameSpec(
                game_type="casual",
                visual_style=VisualStyle(
                    theme="arcade",
                    visual_pack="pixel_arcade",
                    render_style_intensity="balanced",
                ),
            ),
            generation_time_ms=456,
            qa_retries=1,
            iteration_retries=0,
            runtime_profile="casual_lane",
            contract_version="1.0",
        )
        capture_cover = AsyncMock(return_value=None)

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._v2_runner.iterate",
            new=AsyncMock(return_value=fake_result),
        ), patch(
            "src.api.endpoints.generate._relay_stage_summary_to_game_service",
            new=AsyncMock(),
        ), patch(
            "src.api.endpoints.generate._maybe_capture_cover_artifact",
            new=capture_cover,
        ):
            asyncio.run(
                generate_api._run_iteration_v2_internal(
                    request,
                    task_id="task-v2-iter-cover",
                )
            )

        self.assertEqual(capture_cover.await_args.kwargs["title"], "Wide Runner")
        self.assertEqual(capture_cover.await_args.kwargs["game_type"], "casual")
        self.assertEqual(capture_cover.await_args.kwargs["runtime_profile"], "casual_lane")
        self.assertEqual(capture_cover.await_args.kwargs["visual_pack"], "pixel_arcade")
        self.assertEqual(capture_cover.await_args.kwargs["render_style_intensity"], "balanced")
        self.assertTrue(capture_cover.await_args.kwargs["updated"])

    def test_v2_iteration_internal_persists_source_history_artifacts(self):
        request = IterateV2Request(
            game_id="game-v2-iter-artifacts",
            user_id="user-v2-iter-artifacts",
            current_code="<!DOCTYPE html><html><body>old</body></html>",
            iteration_intent={
                "feedback": "add five levels",
                "conversation": [],
            },
            source_spec=GameSpec(game_type="casual", intent_summary="keep runner"),
            source_bundle_context={
                "title": "Pig Runner",
                "latest_bundle_version": 3,
                "latest_game_type": "runner",
            },
        )
        fake_result = IterateResponse(
            html_code="<!DOCTYPE html><html><body>new</body></html>",
            changes=["Applied: add five levels"],
            iteration_type="element_change",
            game_spec=GameSpec(game_type="casual"),
            generation_time_ms=456,
            qa_retries=1,
            iteration_retries=0,
            runtime_profile="casual_lane",
            contract_version="1.0",
        )
        relay_artifact = AsyncMock(
            side_effect=lambda **kwargs: f"artifact-{kwargs['artifact_type']}"
        )

        with patch_v2_prompt_defaults(), patch(
            "src.api.endpoints.generate._v2_runner.iterate",
            new=AsyncMock(return_value=fake_result),
        ), patch(
            "src.api.endpoints.generate._relay_artifact_to_game_service",
            new=relay_artifact,
        ), patch(
            "src.api.endpoints.generate._relay_stage_summary_to_game_service",
            new=AsyncMock(),
        ):
            asyncio.run(
                generate_api._run_iteration_v2_internal(
                    request,
                    task_id="task-v2-iter-artifacts",
                )
            )

        artifact_types = [call.kwargs["artifact_type"] for call in relay_artifact.await_args_list]
        self.assertIn("source_game_spec", artifact_types)
        self.assertIn("source_bundle_context", artifact_types)


if __name__ == "__main__":
    unittest.main()
