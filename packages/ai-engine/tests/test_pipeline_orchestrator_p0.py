"""Built-in unittest coverage for P0 pipeline retry behavior."""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import GDD, GameSpec, GenerateCodeResult, QACheckError, QAResult, TemplateMatchResult
from src.engine.pipeline_orchestrator import PipelineExecutionError, PipelineOrchestrator


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


class TestPipelineOrchestratorP0(unittest.TestCase):
    def test_stage_design_retries_then_falls_back_to_default_gdd(self):
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
            result = asyncio.run(
                orchestrator._stage_design(make_spec(), "game-design", "user-design", callback)
            )

        self.assertIsInstance(result, GDD)
        self.assertEqual(
            [event["message"] for event in events],
            [
                "游戏数值设计失败，重试中（1/2）",
                "游戏数值设计失败，重试中（2/2）",
                "游戏数值设计失败，已切换默认参数继续生成",
            ],
        )

    def test_stage_intent_parse_retries_then_falls_back(self):
        import src.engine.dialogue_engine as dialogue_engine_module
        import src.engine.pipeline_orchestrator as orchestrator_module

        orchestrator = PipelineOrchestrator()
        attempts = []

        async def fail_parse(_description: str, allow_fallback: bool = True):
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
        ), patch.object(
            dialogue_engine_module,
            "_mock_parse",
            return_value=GameSpec(game_type="fallback"),
        ):
            result = asyncio.run(
                orchestrator._stage_intent_parse("desc", "game-1", "user-1", callback)
            )

        self.assertEqual(result.game_type, "fallback")
        self.assertEqual(attempts, [False, False, False])
        self.assertEqual(
            [event["message"] for event in events if "重试中" in event["message"]],
            [
                "意图解析失败，重试中（1/2）",
                "意图解析失败，重试中（2/2）",
            ],
        )
        self.assertEqual(events[-1]["message"], "意图解析失败，已切换默认解析继续生成")
        self.assertEqual(events[-1]["details"]["fallback"], "mock_parse")

    def test_stage_match_template_retries_then_falls_back_to_llm(self):
        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()

        with patch.object(
            orchestrator.template_engine,
            "match",
            side_effect=RuntimeError("template engine down"),
        ):
            result = orchestrator._stage_match_template(make_spec(), "game-2", "user-2", callback)

        self.assertEqual(result.path, "llm")
        self.assertEqual(
            [event["message"] for event in events],
            [
                "模板匹配失败，重试中（1/2）",
                "模板匹配失败，重试中（2/2）",
            ],
        )
        self.assertTrue(all(event["stage"] == "template_matching" for event in events))

    def test_stage_generate_code_retries_three_times_and_allows_fallback_on_last_attempt(self):
        import src.engine.pipeline_orchestrator as orchestrator_module

        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()
        allow_fallback_values = []

        async def fake_generate(**kwargs):
            allow_fallback_values.append(kwargs["allow_fallback"])
            if len(allow_fallback_values) < 3:
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
                    TemplateMatchResult(path="llm"),
                    "game-3",
                    "user-3",
                    callback,
                    description="make a runner",
                )
            )

        self.assertEqual(result.html_code, "<html></html>")
        self.assertEqual(allow_fallback_values, [False, False, True])
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
                        TemplateMatchResult(path="llm"),
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

    def test_iteration_retries_and_reports_qa_retries(self):
        import src.engine.pipeline_orchestrator as orchestrator_module
        from src.api.models import IterationType

        orchestrator = PipelineOrchestrator()
        events, callback = make_progress_sink()
        allow_fallback_values = []

        async def fake_iterate(**kwargs):
            allow_fallback_values.append(kwargs["allow_fallback"])
            if len(allow_fallback_values) < 3:
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
        self.assertEqual(allow_fallback_values, [False, False, True])
        self.assertIn("代码修改失败，正在重试（1/2）", [event["message"] for event in events])
        self.assertIn("代码修改失败，正在重试（2/2）", [event["message"] for event in events])
        self.assertIn("修改后的质量检查未通过，正在修复（1/3）", [event["message"] for event in events])


if __name__ == "__main__":
    unittest.main()
