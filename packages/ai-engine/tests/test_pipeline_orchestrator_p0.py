"""Built-in unittest coverage for P0 pipeline retry behavior."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import GDD, GameSpec, GenerateCodeResult, QACheckError, QAResult
from src.engine.pipeline_orchestrator import PipelineExecutionError, PipelineOrchestrator
from src.engine.quality_scorer import RuntimeQAResult


def make_spec() -> GameSpec:
    return GameSpec(game_type="runner")


def make_progress_sink():
    events = []

    def callback(stage: str, pct: int, message: str, details=None) -> None:
        events.append({
            "stage": stage,
            "pct": pct,
            "message": message,
            "details": details or {},
        })

    return events, callback


async def _noop_sleep(*_args, **_kwargs) -> None:
    return None


async def _timeout_wait_for(_awaitable, timeout):
    raise asyncio.TimeoutError()


async def _timeout_after_stage_update(awaitable, timeout):
    del timeout
    task = asyncio.create_task(awaitable)
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    raise asyncio.TimeoutError()


class TestPipelineOrchestratorP0(unittest.TestCase):
    def test_stage_design_retries_then_fails_closed(self):
        import src.engine.pipeline_orchestrator as orchestrator_module

        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()

        with patch.object(
            orchestrator.game_designer,
            "design",
            new=AsyncMock(side_effect=RuntimeError("designer unavailable")),
        ), patch.object(
            orchestrator_module.asyncio,
            "sleep",
            new=_noop_sleep,
        ):
            with self.assertRaises(PipelineExecutionError) as ctx:
                asyncio.run(
                    orchestrator._stage_design(make_spec(), "game-design", "user-design", callback)
                )

        self.assertIn("Game design failed after 3 attempts", str(ctx.exception))
        self.assertEqual(
            [event["message"] for event in events],
            [
                "游戏数值设计失败，重试中（1/2）",
                "游戏数值设计失败，重试中（2/2）",
                "Game design failed, generation stopped",
            ],
        )
        self.assertEqual(events[-1]["details"]["failedStage"], "designing")

    def test_stage_intent_parse_retries_then_fails_closed(self):
        import src.engine.pipeline_orchestrator as orchestrator_module

        orchestrator = PipelineOrchestrator()
        attempts = []

        async def fail_parse(_description: str, allow_fallback: bool = True, variation_seed: str | None = None):
            del variation_seed
            attempts.append(allow_fallback)
            raise RuntimeError("parser unavailable")

        events, callback = make_progress_sink()

        with patch.object(
            orchestrator.dialogue_engine,
            "parse_description_to_spec",
            new=AsyncMock(side_effect=fail_parse),
        ), patch.object(
            orchestrator_module.asyncio,
            "sleep",
            new=_noop_sleep,
        ):
            with self.assertRaises(PipelineExecutionError) as ctx:
                asyncio.run(
                    orchestrator._stage_intent_parse("desc", "game-1", "user-1", callback)
                )

        self.assertIn("Intent parsing failed after 3 attempts", str(ctx.exception))
        self.assertEqual(attempts, [True, True, True])
        self.assertEqual(
            [event["message"] for event in events if "重试中" in event["message"]],
            [
                "意图解析失败，重试中（1/2）",
                "意图解析失败，重试中（2/2）",
            ],
        )
        self.assertEqual(events[-1]["message"], "意图解析失败，生成已终止")
        self.assertEqual(events[-1]["details"]["failedStage"], "intent_parsing")

    def test_stage_generate_code_retries_three_times_without_fallback(self):
        import src.engine.pipeline_orchestrator as orchestrator_module

        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()
        call_kwargs = []

        async def fake_generate(**kwargs):
            call_kwargs.append(kwargs)
            if len(call_kwargs) < 3:
                raise RuntimeError("temporary upstream timeout")
            return GenerateCodeResult(
                html_code="<html></html>",
                strategy="llm",
                template_id=None,
                generation_time_ms=12,
                code_size_bytes=13,
            )

        with patch.object(
            orchestrator.code_generator,
            "generate",
            new=AsyncMock(side_effect=fake_generate),
        ), patch.object(
            orchestrator_module.asyncio,
            "sleep",
            new=_noop_sleep,
        ):
            result = asyncio.run(
                orchestrator._stage_generate_code(
                    make_spec(),
                    GDD(),
                    "game-3",
                    "user-3",
                    callback,
                    description="make a runner",
                )
            )

        self.assertEqual(result.html_code, "<html></html>")
        self.assertEqual(len(call_kwargs), 3)
        self.assertTrue(all("allow_fallback" not in kwargs for kwargs in call_kwargs))
        self.assertTrue(all("template_id" not in kwargs for kwargs in call_kwargs))
        self.assertTrue(all("confidence" not in kwargs for kwargs in call_kwargs))
        self.assertEqual(
            [event["message"] for event in events if "重试中" in event["message"]],
            [
                "代码生成失败，重试中（1/2）",
                "代码生成失败，重试中（2/2）",
            ],
        )

    def test_stage_generate_code_does_not_retry_non_retryable_errors(self):
        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()

        with patch.object(
            orchestrator.code_generator,
            "generate",
            new=AsyncMock(side_effect=ValueError("invalid prompt payload")),
        ):
            with self.assertRaises(PipelineExecutionError) as ctx:
                asyncio.run(
                    orchestrator._stage_generate_code(
                        make_spec(),
                        GDD(),
                        "game-no-retry",
                        "user-no-retry",
                        callback,
                        description="make a runner",
                    )
                )

        self.assertEqual(ctx.exception.retry_count, 0)
        self.assertEqual(
            [event["message"] for event in events if "重试中" in event["message"]],
            [],
        )

    def test_runtime_qa_unavailable_fails_closed_when_required(self):
        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()
        qa_result = QAResult(success=True, code="<!DOCTYPE html><html><body></body></html>", retries=0)

        with patch(
            "src.engine.pipeline_orchestrator.run_runtime_qa",
            new=AsyncMock(return_value=RuntimeQAResult(ran=False)),
        ), patch(
            "src.engine.pipeline_orchestrator.settings.RUNTIME_QA_REQUIRED",
            True,
        ):
            with self.assertRaises(PipelineExecutionError) as ctx:
                asyncio.run(
                    orchestrator._repair_runtime_failures(
                        qa_result,
                        make_spec(),
                        callback,
                    )
                )

        self.assertIn("Runtime QA unavailable", str(ctx.exception))
        self.assertEqual(events[-1]["message"], "运行时检查不可用，已中止发布")

    def test_runtime_qa_missing_input_handlers_fails_closed(self):
        orchestrator = PipelineOrchestrator()
        qa_result = QAResult(success=True, code="<!DOCTYPE html><html><body></body></html>", retries=0)

        with patch(
            "src.engine.pipeline_orchestrator.run_runtime_qa",
            new=AsyncMock(side_effect=[
                RuntimeQAResult(ran=True, canvas_renders=True, js_errors=[], registered_input_handlers=[], direct_input_handlers=[]),
                RuntimeQAResult(ran=True, canvas_renders=True, js_errors=[], registered_input_handlers=[], direct_input_handlers=[]),
            ]),
        ), patch.object(
            orchestrator.qa_pipeline,
            "repair_code",
            new=AsyncMock(return_value="<!DOCTYPE html><html><body></body></html>"),
        ), patch.object(
            orchestrator.qa_pipeline,
            "run_with_auto_fix",
            new=AsyncMock(return_value=QAResult(success=True, code="<!DOCTYPE html><html><body></body></html>", retries=1)),
        ):
            with self.assertRaises(PipelineExecutionError) as ctx:
                asyncio.run(
                    orchestrator._repair_runtime_failures(
                        qa_result,
                        make_spec(),
                        None,
                    )
                )

        self.assertIn("No registered input handlers", str(ctx.exception))

    def test_stage_qa_emits_retry_progress(self):
        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()

        async def fake_run_with_auto_fix(*, code: str, game_spec: GameSpec, max_retries: int, retry_cb=None):
            retry_cb(1, max_retries, [QACheckError(type="qa", message="missing closing html")])
            retry_cb(2, max_retries, [QACheckError(type="qa", message="unsafe api usage")])
            return QAResult(success=True, code=code, retries=2)

        with patch.object(
            orchestrator.qa_pipeline,
            "run_with_auto_fix",
            new=AsyncMock(side_effect=fake_run_with_auto_fix),
        ):
            result = asyncio.run(
                orchestrator._stage_qa("<html></html>", make_spec(), "game-4", "user-4", callback)
            )

        self.assertTrue(result.success)
        self.assertEqual(result.retries, 2)
        self.assertEqual(
            [event["message"] for event in events],
            [
                "质量检查未通过，正在自动修复（1/3）",
                "质量检查未通过，正在自动修复（2/3）",
            ],
        )
        self.assertEqual(events[0]["details"]["errorCount"], 1)

    def test_stage_qa_surfaces_pipeline_exception_message(self):
        orchestrator = PipelineOrchestrator()

        with patch.object(
            orchestrator.qa_pipeline,
            "run_with_auto_fix",
            new=AsyncMock(side_effect=RuntimeError("esprima parser crashed")),
        ):
            result = asyncio.run(
                orchestrator._stage_qa("<html></html>", make_spec(), "game-qa", "user-qa", None)
            )

        self.assertFalse(result.success)
        self.assertEqual(result.last_errors[0].type, "qa_pipeline")
        self.assertEqual(result.last_errors[0].message, "esprima parser crashed")

    def test_run_timeout_reports_current_stage(self):
        import src.engine.pipeline_orchestrator as orchestrator_module

        orchestrator = PipelineOrchestrator()

        async def fake_run_stages(_request, _progress_cb, stage_context=None):
            if stage_context is not None:
                stage_context["stage"] = "code_generating"
            await asyncio.sleep(0)

        request = type("Req", (), {"game_id": "game-timeout", "user_id": "user-timeout"})()

        with patch.object(
            orchestrator,
            "_run_stages",
            new=fake_run_stages,
        ), patch.object(
            orchestrator_module.asyncio,
            "wait_for",
            new=_timeout_after_stage_update,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(orchestrator.run(request))

        self.assertIn("Pipeline timed out during code_generating", str(ctx.exception))

    def test_iteration_retries_and_reports_qa_retries(self):
        import src.engine.pipeline_orchestrator as orchestrator_module
        from src.api.models import IterationType

        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()
        call_kwargs = []

        async def fake_iterate(**kwargs):
            call_kwargs.append(kwargs)
            if len(call_kwargs) < 3:
                raise RuntimeError("temporary iteration timeout")
            return "<html>updated</html>", IterationType.element_change

        async def fake_run_with_auto_fix(*, code: str, max_retries: int, retry_cb=None, **_kwargs):
            retry_cb(1, max_retries, [QACheckError(type="qa", message="missing input binding")])
            return QAResult(success=True, code=code, retries=1)

        with patch.object(
            orchestrator.code_generator,
            "iterate",
            new=AsyncMock(side_effect=fake_iterate),
        ), patch.object(
            orchestrator.qa_pipeline,
            "run_with_auto_fix",
            new=AsyncMock(side_effect=fake_run_with_auto_fix),
        ), patch.object(
            orchestrator_module.asyncio,
            "sleep",
            new=_noop_sleep,
        ):
            result = asyncio.run(
                orchestrator.iterate(
                    game_id="game-iter",
                    current_code="<html>old</html>",
                    feedback="make it faster",
                    conversation=[],
                    user_id="user-iter",
                    progress_cb=callback,
                )
            )

        self.assertEqual(result["html_code"], "<html>updated</html>")
        self.assertEqual(result["iteration_type"], "element_change")
        self.assertEqual(result["qa_retries"], 1)
        self.assertEqual(result["iteration_retries"], 2)
        self.assertEqual(len(call_kwargs), 3)
        self.assertTrue(all("allow_fallback" not in kwargs for kwargs in call_kwargs))
        self.assertIn("代码修改失败，正在重试（1/2）", [event["message"] for event in events])
        self.assertIn("代码修改失败，正在重试（2/2）", [event["message"] for event in events])
        self.assertIn("修改后的质量检查未通过，正在修复（1/3）", [event["message"] for event in events])


if __name__ == "__main__":
    unittest.main()
