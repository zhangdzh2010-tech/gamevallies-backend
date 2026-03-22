"""Coverage for OpenAI-compatible URL normalization."""

import os
import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config.settings import settings
from src.services import llm_client as llm_client_module
from src.services.llm_client import LLMClient, _build_openai_compatible_chat_url


def test_build_chat_url_keeps_explicit_chat_completions_path():
    url = _build_openai_compatible_chat_url("https://api.minimaxi.com/v1/chat/completions")
    assert url == "https://api.minimaxi.com/v1/chat/completions"


def test_build_chat_url_appends_v1_for_bare_minimax_host():
    url = _build_openai_compatible_chat_url("https://api.minimaxi.com")
    assert url == "https://api.minimaxi.com/v1/chat/completions"


def test_build_chat_url_keeps_non_minimax_provider_shape():
    url = _build_openai_compatible_chat_url("https://api.deepseek.com")
    assert url == "https://api.deepseek.com/chat/completions"


def test_complete_emits_task_activity_heartbeats_for_long_running_calls():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-1",
        provider_name="MiniMax Shanghai",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://api.minimaxi.com/v1",
        api_key="secret",
        model="MiniMax-M2.5",
        fast_model="MiniMax-M2.5",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "qa_fix"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        async def fake_complete_openai(**_kwargs):
            await asyncio.sleep(0.03)
            return "pong"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve",
            return_value=route,
        ), patch.object(
            llm_client_module.gateway,
            "emit_task_activity",
            new=AsyncMock(),
        ) as emit_activity, patch.object(
            llm_client_module.gateway,
            "emit_llm_call_log",
            new=AsyncMock(),
        ) as emit_log, patch.object(
            llm_client_module,
            "LLM_ACTIVITY_HEARTBEAT_S",
            0.01,
        ), patch.object(
            client,
            "_complete_openai_compatible",
            new=fake_complete_openai,
        ):
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=16,
                step_key="qa_fix",
                stage="qa_checking",
            ))

        assert result == "pong"
        activity_states = [call.args[0]["details"]["activityState"] for call in emit_activity.await_args_list]
        assert activity_states[0] == "started"
        assert "heartbeat" in activity_states
        assert activity_states[-1] == "completed"
        assert emit_log.await_count == 1
        assert emit_log.await_args_list[0].args[0]["success"] is True
    finally:
        settings.LLM_MODE = old_mode


def test_complete_emits_failed_activity_for_request_errors():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-1",
        provider_name="MiniMax Shanghai",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://api.minimaxi.com/v1",
        api_key="secret",
        model="MiniMax-M2.5",
        fast_model="MiniMax-M2.5",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.hybrid"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        async def fake_complete_openai(**_kwargs):
            raise RuntimeError("gateway upstream reset")

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve",
            return_value=route,
        ), patch.object(
            llm_client_module.gateway,
            "emit_task_activity",
            new=AsyncMock(),
        ) as emit_activity, patch.object(
            llm_client_module.gateway,
            "emit_llm_call_log",
            new=AsyncMock(),
        ) as emit_log, patch.object(
            client,
            "_complete_openai_compatible",
            new=fake_complete_openai,
        ):
            try:
                asyncio.run(client.complete(
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=16,
                    step_key="code_generate.hybrid",
                    stage="code_generating",
                ))
                raise AssertionError("expected complete() to raise")
            except RuntimeError as exc:
                assert "gateway upstream reset" in str(exc)

        activity_states = [call.args[0]["details"]["activityState"] for call in emit_activity.await_args_list]
        assert activity_states[0] == "started"
        assert activity_states[-1] == "failed"
        assert emit_log.await_count == 1
        assert emit_log.await_args_list[0].args[0]["success"] is False
    finally:
        settings.LLM_MODE = old_mode
