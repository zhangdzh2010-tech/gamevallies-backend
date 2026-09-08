"""Tests for logic_generate progress heartbeat and runtime-QA ∥ code-review parallelism.

Covers:
- A: pseudo-progress heartbeat during the long logic_generate LLM call
  (monotonic 60 -> 74, stops when generation returns, honors the
  GENERATION_PROGRESS_HEARTBEAT_ENABLED switch).
- B2: runtime_simulation_qa and code_review run concurrently on the create
  main path; both results feed QualityScorer; runtime QA failure keeps its
  original error semantics (review discarded); the PR-11 defer branch is
  unaffected.
"""

import asyncio
import os
import sys
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    GDD,
    GameRuntimeContract,
    GameSpec,
    GenerateCodeResult,
    QAIssueList,
    RunPipelineV2Request,
)
from src.config.settings import settings
from src.engine.pipeline_errors import PipelineExecutionError
from src.engine.pipeline_v2_runner import V2PipelineRunner
from src.engine.quality_scorer import LLMReviewResult

GOOD_CODE = (
    "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas><script>"
    "const canvas=document.getElementById('gameCanvas');"
    "canvas.width=360;canvas.height=640;"
    "</script></body></html>"
)


def _spec() -> GameSpec:
    return GameSpec(
        game_type="casual",
        generation_tier="standard",
        source_description="a neon dodge arcade game",
        entities=[],
        special_rules=[],
        core_mechanics=[{"type": "runner"}],
    )


def _request() -> RunPipelineV2Request:
    return RunPipelineV2Request(
        game_id="game-parallel-qa",
        user_id="user-parallel-qa",
        raw_user_input="make a neon dodge arcade game",
    )


def _generated() -> GenerateCodeResult:
    return GenerateCodeResult(
        html_code=GOOD_CODE,
        strategy="llm",
        generation_time_ms=10,
        code_size_bytes=len(GOOD_CODE),
        route_snapshot={"provider_id": "provider-a"},
    )


def _passing_review() -> LLMReviewResult:
    return LLMReviewResult(
        ran=True,
        is_complete_game=True,
        has_real_gameplay=True,
        difficulty_balanced=True,
        fun_score=7.4,
        visual_polish_score=7.2,
        character_quality_score=6.6,
        issues=[],
    )


def _quality(final_score: float = 7.0) -> SimpleNamespace:
    return SimpleNamespace(
        final_score=final_score,
        review_bonus=0.0,
        details={},
        qa_penalty=0.0,
        strategy_bonus=0.0,
        size_bonus=0.0,
        retry_penalty=0.0,
        runtime_bonus=0.0,
        gameplay_depth_bonus=0.0,
    )


def _base_create_stack(stack: ExitStack, runner: V2PipelineRunner, *, generate_mock) -> None:
    """Common heavy-mock harness for driving _run_create_impl with the REAL
    _run_contract_and_runtime_flow (fast path: static QA + contract pass)."""
    spec = _spec()
    runtime_contract = GameRuntimeContract(runtime_profile="casual_lane_dash")
    stack.enter_context(patch.object(runner, "_build_create_spec", new=AsyncMock(return_value=spec)))
    stack.enter_context(patch.object(runner, "_select_runtime_profile", return_value="casual_lane_dash"))
    stack.enter_context(patch.object(runner, "_compose_runtime_contract", return_value=runtime_contract))
    stack.enter_context(patch.object(runner, "_build_gdd", new=AsyncMock(return_value=GDD())))
    stack.enter_context(patch.object(runner, "_remember_spec", new=AsyncMock()))
    stack.enter_context(patch.object(runner, "_remember_runtime_contract", new=AsyncMock()))
    stack.enter_context(patch.object(runner, "_remember_code", new=AsyncMock()))
    stack.enter_context(
        patch("src.engine.pipeline_v2_runner.task_memory.append_decision", new=AsyncMock())
    )
    stack.enter_context(patch.object(runner.pre_gen_validator, "validate", return_value=[]))
    stack.enter_context(patch.object(runner, "_generate_create_code", new=generate_mock))
    # Real _run_contract_and_runtime_flow fast path: static QA passes and the
    # contract bundle reports no errors.
    stack.enter_context(
        patch.object(
            runner.qa_pipeline,
            "_apply_deterministic_repairs",
            side_effect=lambda code: code,
        )
    )
    stack.enter_context(
        patch.object(
            runner.qa_pipeline,
            "check",
            return_value=SimpleNamespace(
                passed=True, errors=[], warnings=[], issue_list=QAIssueList()
            ),
        )
    )
    stack.enter_context(patch.object(runner, "_validate_contract_bundle", return_value=[]))
    stack.enter_context(patch.object(runner, "_quality_gate_errors", return_value=[]))
    stack.enter_context(patch.object(runner.code_generator.template_cache, "store"))
    stack.enter_context(patch.object(runner, "_serialize_runtime_qa", return_value={}))


# ---------------------------------------------------------------------------
# A. logic_generate progress heartbeat
# ---------------------------------------------------------------------------


def _run_create_with_slow_generation(*, heartbeat_enabled: bool):
    runner = V2PipelineRunner()
    events: list[tuple[str, int, str, dict]] = []

    def progress_cb(stage, pct, message, details=None):
        events.append((stage, pct, message, details or {}))

    async def slow_generate(*args, **kwargs):
        await asyncio.sleep(0.4)
        return _generated(), []

    async def runtime_loop(**kwargs):
        return kwargs["code"], SimpleNamespace(ran=True, js_errors=[]), 0, []

    with ExitStack() as stack:
        _base_create_stack(stack, runner, generate_mock=AsyncMock(side_effect=slow_generate))
        stack.enter_context(patch.object(runner, "_run_runtime_qa_loop", new=AsyncMock(side_effect=runtime_loop)))
        stack.enter_context(patch.object(runner, "_should_run_code_review", return_value=False))
        stack.enter_context(patch.object(runner.quality_scorer, "compute", return_value=_quality()))
        stack.enter_context(
            patch("src.engine.pipeline_v2_runner.GENERATION_PROGRESS_HEARTBEAT_INTERVAL_S", 0.05)
        )
        stack.enter_context(
            patch("src.engine.pipeline_v2_runner.GENERATION_PROGRESS_HEARTBEAT_EXPECTED_DURATION_S", 0.3)
        )
        stack.enter_context(
            patch.object(settings, "GENERATION_PROGRESS_HEARTBEAT_ENABLED", heartbeat_enabled)
        )
        response = asyncio.run(
            runner._run_create_impl(
                _request(),
                progress_cb=progress_cb,
                stage_context={"stage": "spec_build"},
            )
        )
    return response, events


def test_generation_heartbeat_emits_monotonic_progress_and_stops():
    response, events = _run_create_with_slow_generation(heartbeat_enabled=True)

    assert response.html_code == GOOD_CODE
    heartbeats = [event for event in events if event[3].get("heartbeat")]
    assert len(heartbeats) >= 3, f"expected multiple heartbeats, got {len(heartbeats)}"
    assert all(event[0] == "logic_generate" for event in heartbeats)
    pcts = [event[1] for event in heartbeats]
    assert all(60 <= pct <= 74 for pct in pcts)
    assert pcts == sorted(pcts), "heartbeat progress must be monotonic non-decreasing"
    assert any(pct > 60 for pct in pcts), "heartbeat progress should advance beyond 60"

    # Heartbeats stop once generation returns: nothing tagged heartbeat may
    # appear after the runtime_simulation_qa notification.
    runtime_qa_index = next(
        index for index, event in enumerate(events) if event[0] == "runtime_simulation_qa"
    )
    assert not any(event[3].get("heartbeat") for event in events[runtime_qa_index:])
    assert events[-1][0] == "completed"


def test_generation_heartbeat_respects_disable_switch():
    response, events = _run_create_with_slow_generation(heartbeat_enabled=False)

    assert response.html_code == GOOD_CODE
    assert not any(event[3].get("heartbeat") for event in events)


# ---------------------------------------------------------------------------
# B2. runtime_simulation_qa ∥ code_review on the create main path
# ---------------------------------------------------------------------------


def _run_parallel_create(*, runtime_loop, review, attempt_plan=None):
    runner = V2PipelineRunner()
    compute_calls: list[dict] = []

    def compute_spy(**kwargs):
        compute_calls.append(kwargs)
        return _quality()

    with ExitStack() as stack:
        _base_create_stack(
            stack,
            runner,
            generate_mock=AsyncMock(side_effect=lambda *a, **kw: (_generated(), [])),
        )
        stack.enter_context(patch.object(runner, "_run_runtime_qa_loop", new=AsyncMock(side_effect=runtime_loop)))
        stack.enter_context(patch.object(runner, "_should_run_code_review", return_value=True))
        stack.enter_context(patch.object(runner.code_reviewer, "review", new=AsyncMock(side_effect=review)))
        stack.enter_context(patch.object(runner.quality_scorer, "compute", side_effect=compute_spy))
        if attempt_plan is not None:
            stack.enter_context(
                patch.object(runner, "_build_create_generation_attempt_plan", return_value=attempt_plan)
            )
        response = asyncio.run(
            runner._run_create_impl(
                _request(),
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )
    return response, compute_calls


def test_runtime_qa_and_code_review_run_concurrently_and_both_feed_quality_gate():
    timeline: dict[str, float] = {}
    runtime_result = SimpleNamespace(ran=True, js_errors=[])
    review_result = _passing_review()

    async def runtime_loop(**kwargs):
        timeline["runtime_start"] = time.monotonic()
        await asyncio.sleep(0.15)
        timeline["runtime_end"] = time.monotonic()
        return kwargs["code"], runtime_result, 0, []

    async def review(code, *, user_requirements=""):
        assert user_requirements == "a neon dodge arcade game"
        timeline["review_start"] = time.monotonic()
        await asyncio.sleep(0.15)
        timeline["review_end"] = time.monotonic()
        return review_result

    response, compute_calls = _run_parallel_create(runtime_loop=runtime_loop, review=review)

    assert response.html_code == GOOD_CODE
    # Concurrency: the review must start before runtime QA finishes. In the
    # old serial flow the review only started after runtime QA ended.
    assert timeline["review_start"] < timeline["runtime_end"], (
        "code review should run concurrently with runtime simulation QA"
    )
    # Both results feed the quality scorer / quality gate.
    assert len(compute_calls) == 1
    assert compute_calls[0]["runtime"] is runtime_result
    assert compute_calls[0]["review"] is review_result


def test_runtime_qa_failure_keeps_error_semantics_and_discards_review():
    review_state = {"cancelled": False, "completed": False}

    async def runtime_loop(**kwargs):
        await asyncio.sleep(0.01)
        raise PipelineExecutionError(
            "Generated code failed runtime QA: boom",
            stage="runtime_simulation_qa",
            retry_count=0,
            failure_family="runtime_qa",
        )

    async def review(code, *, user_requirements=""):
        assert user_requirements == "a neon dodge arcade game"
        try:
            await asyncio.sleep(5)
            review_state["completed"] = True
            return _passing_review()
        except asyncio.CancelledError:
            review_state["cancelled"] = True
            raise

    started = time.monotonic()
    with pytest.raises(PipelineExecutionError) as exc_info:
        _run_parallel_create(
            runtime_loop=runtime_loop,
            review=review,
            attempt_plan=("standard",),
        )
    elapsed = time.monotonic() - started

    assert exc_info.value.stage == "runtime_simulation_qa"
    assert exc_info.value.failure_family == "runtime_qa"
    # The in-flight review is discarded, not awaited to completion.
    assert review_state["cancelled"] is True
    assert review_state["completed"] is False
    assert elapsed < 3, "runtime QA failure must not wait for the slow review"


def test_runtime_qa_failure_retry_then_success_uses_fresh_review():
    """After a runtime-QA failure round, the retry round must launch a fresh
    review and still feed both results into the quality gate."""
    rounds = {"runtime": 0}
    runtime_result = SimpleNamespace(ran=True, js_errors=[])
    review_results = [_passing_review(), _passing_review()]
    review_calls: list[str] = []

    async def runtime_loop(**kwargs):
        rounds["runtime"] += 1
        if rounds["runtime"] == 1:
            raise PipelineExecutionError(
                "Generated code failed runtime QA: boom",
                stage="runtime_simulation_qa",
                retry_count=0,
                failure_family="runtime_qa",
            )
        return kwargs["code"], runtime_result, 0, []

    async def review(code, *, user_requirements=""):
        assert user_requirements == "a neon dodge arcade game"
        review_calls.append(code)
        return review_results[len(review_calls) - 1]

    response, compute_calls = _run_parallel_create(
        runtime_loop=runtime_loop,
        review=review,
        attempt_plan=("standard", "standard"),
    )

    assert response.html_code == GOOD_CODE
    assert rounds["runtime"] == 2
    assert len(compute_calls) == 1
    assert compute_calls[0]["runtime"] is runtime_result
    assert compute_calls[0]["review"] in review_results


def test_pr11_defer_branch_is_unaffected_by_parallel_review():
    """When PR-11 defers runtime QA, the placeholder result returns instantly
    and the concurrent review is still awaited and fed into the gate."""
    runner = V2PipelineRunner()
    compute_calls: list[dict] = []
    review_result = _passing_review()

    def compute_spy(**kwargs):
        compute_calls.append(kwargs)
        return _quality()

    schedule_mock = AsyncMock()

    with ExitStack() as stack:
        _base_create_stack(
            stack,
            runner,
            generate_mock=AsyncMock(side_effect=lambda *a, **kw: (_generated(), [])),
        )
        # Real _run_runtime_qa_loop with the defer branch forced on.
        stack.enter_context(patch.object(settings, "P1_RUNTIME_QA_DEFERRED_ENABLED", True))
        stack.enter_context(
            patch("src.engine.pipeline_v2_runner._p1_should_defer", new=lambda tier, op: True)
        )
        stack.enter_context(
            patch("src.engine.pipeline_v2_runner._p1_schedule_runtime_qa", new=schedule_mock)
        )
        stack.enter_context(patch.object(runner, "_should_run_code_review", return_value=True))
        stack.enter_context(
            patch.object(runner.code_reviewer, "review", new=AsyncMock(return_value=review_result))
        )
        stack.enter_context(patch.object(runner.quality_scorer, "compute", side_effect=compute_spy))
        response = asyncio.run(
            runner._run_create_impl(
                _request(),
                progress_cb=None,
                stage_context={"stage": "spec_build"},
            )
        )

    assert response.html_code == GOOD_CODE
    assert schedule_mock.await_count == 1
    assert len(compute_calls) == 1
    runtime_qa = compute_calls[0]["runtime"]
    assert getattr(runtime_qa, "phase_metrics", {}).get("deferred") is True
    assert compute_calls[0]["review"] is review_result
