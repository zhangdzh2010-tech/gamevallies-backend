"""Coverage for OpenAI-compatible URL normalization."""

import os
import sys
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config.settings import settings
from src.services import llm_client as llm_client_module
from src.services.llm_client import (
    LLMClient,
    EmptyOpenAICompatibleTextError,
    LLMResponseTruncatedError,
    _build_anthropic_base_url,
    _build_openai_compatible_chat_url,
    _extract_openai_choice_text,
    _extract_openai_message_text,
    _is_anthropic_protocol_mismatch,
)


def test_build_chat_url_keeps_explicit_chat_completions_path():
    url = _build_openai_compatible_chat_url("https://api.minimaxi.com/v1/chat/completions")
    assert url == "https://api.minimaxi.com/v1/chat/completions"


def test_build_chat_url_appends_v1_for_bare_minimax_host():
    url = _build_openai_compatible_chat_url("https://api.minimaxi.com")
    assert url == "https://api.minimaxi.com/v1/chat/completions"


def test_build_chat_url_keeps_non_minimax_provider_shape():
    url = _build_openai_compatible_chat_url("https://api.deepseek.com")
    assert url == "https://api.deepseek.com/chat/completions"


def test_detects_anthropic_protocol_mismatch_from_openai_compatible_400():
    request = httpx.Request("POST", "https://api.gptsapi.net/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        text='{"error":{"message":"This request uses an OpenAI-compatible format. Anthropic-compatible free routing does not support /v1/chat/completions."}}',
    )
    exc = httpx.HTTPStatusError("bad request", request=request, response=response)
    assert _is_anthropic_protocol_mismatch(exc) is True


def test_build_anthropic_base_url_strips_openai_style_suffixes():
    assert _build_anthropic_base_url("https://api.gptsapi.net/v1") == "https://api.gptsapi.net"
    assert _build_anthropic_base_url("https://api.gptsapi.net/v1/messages") == "https://api.gptsapi.net"


def test_extract_openai_message_text_falls_back_to_reasoning_content():
    message = {
        "content": "",
        "reasoning_content": '{"game_type":"dodge","core_mechanic":"躲避障碍"}',
    }
    assert _extract_openai_message_text(message) == '{"game_type":"dodge","core_mechanic":"躲避障碍"}'


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
            "get_timeout_int",
            side_effect=lambda key, default, **kwargs: 0.01 if key == "timeout.ai_engine.llm_activity_heartbeat_s" else default,
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
        route_snapshot={"step_key": "code_generate.full"},
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
                    step_key="code_generate.full",
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


def test_complete_retries_openai_provider_with_anthropic_protocol_when_upstream_demands_it():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-1",
        provider_name="Claude-opus4.6-cn-上海",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://api.gptsapi.net/v1",
        api_key="secret",
        model="wild-sonnet-4-6",
        fast_model="wild-sonnet-4-6",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "intent_parse"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        async def fake_complete_openai(**_kwargs):
            request = httpx.Request("POST", "https://api.gptsapi.net/v1/chat/completions")
            response = httpx.Response(
                400,
                request=request,
                text='{"error":{"message":"This request uses an OpenAI-compatible format. Anthropic-compatible free routing does not support /v1/chat/completions."}}',
            )
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

        def fake_complete_anthropic(**_kwargs):
            return '{"game_type":"puzzle"}'

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
        ), patch.object(
            client,
            "_complete_anthropic",
            new=fake_complete_anthropic,
        ):
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "make me a puzzle game"}],
                max_tokens=16,
                step_key="intent_parse",
                stage="intent_parsing",
                prefer_fast=True,
            ))

        assert result == '{"game_type":"puzzle"}'
        activity_states = [call.args[0]["details"]["activityState"] for call in emit_activity.await_args_list]
        assert activity_states[0] == "started"
        assert activity_states[-1] == "completed"
        assert emit_log.await_count == 1
        payload = emit_log.await_args_list[0].args[0]
        assert payload["success"] is True
        assert payload["providerType"] == "anthropic"
        assert payload["routeSnapshot"]["protocol_fallback"] == "anthropic"
    finally:
        settings.LLM_MODE = old_mode


def test_complete_fails_over_to_secondary_provider_on_timeout():
    client = LLMClient()
    primary = SimpleNamespace(
        provider_id="provider-primary",
        provider_name="DeepSeek Primary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://primary.example/v1",
        api_key="secret",
        model="deepseek-chat",
        fast_model="deepseek-chat",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    secondary = SimpleNamespace(
        provider_id="provider-secondary",
        provider_name="MiniMax Secondary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://secondary.example/v1",
        api_key="secret-2",
        model="MiniMax-M2.5",
        fast_model="MiniMax-M2.5",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    old_mode = settings.LLM_MODE
    old_failover = settings.LLM_PROVIDER_FAILOVER_ENABLED
    settings.LLM_MODE = "real"
    settings.LLM_PROVIDER_FAILOVER_ENABLED = True

    try:
        attempts = []

        async def fake_complete_openai(**kwargs):
            route = kwargs["route"]
            attempts.append(route.provider_id)
            if route.provider_id == "provider-primary":
                raise httpx.ReadTimeout(
                    "timed out",
                    request=httpx.Request("POST", "https://primary.example/v1/chat/completions"),
                )
            return "fallback-ok"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve_candidates",
            return_value=[primary, secondary],
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
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "make me a game"}],
                max_tokens=16,
                step_key="code_generate.full",
                stage="code_generating",
                request_timeout_s=240,
                allow_provider_fallback=True,
            ))

        assert result == "fallback-ok"
        assert attempts == ["provider-primary", "provider-secondary"]
        assert emit_log.await_count == 2
        failure_payload = emit_log.await_args_list[0].args[0]
        success_payload = emit_log.await_args_list[1].args[0]
        assert failure_payload["success"] is False
        assert success_payload["success"] is True
        assert success_payload["providerId"] == "provider-secondary"
        assert success_payload["requestTimeoutS"] == 240
        activity_states = [call.args[0]["details"]["activityState"] for call in emit_activity.await_args_list]
        assert activity_states[0] == "started"
        assert activity_states[-1] == "completed"
    finally:
        settings.LLM_MODE = old_mode
        settings.LLM_PROVIDER_FAILOVER_ENABLED = old_failover


def test_complete_fails_over_when_openai_provider_returns_no_usable_text():
    client = LLMClient()
    primary = SimpleNamespace(
        provider_id="provider-primary",
        provider_name="MiniMax Primary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://primary.example/v1",
        api_key="secret",
        model="MiniMax-M2.7",
        fast_model="MiniMax-M2.7",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    secondary = SimpleNamespace(
        provider_id="provider-secondary",
        provider_name="DeepSeek Secondary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://secondary.example/v1",
        api_key="secret-2",
        model="deepseek-chat",
        fast_model="deepseek-chat",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    old_mode = settings.LLM_MODE
    old_failover = settings.LLM_PROVIDER_FAILOVER_ENABLED
    settings.LLM_MODE = "real"
    settings.LLM_PROVIDER_FAILOVER_ENABLED = True

    try:
        attempts = []

        async def fake_complete_openai(**kwargs):
            route = kwargs["route"]
            attempts.append(route.provider_id)
            if route.provider_id == "provider-primary":
                raise EmptyOpenAICompatibleTextError(
                    "OpenAI-compatible response contained no usable text",
                    response_excerpt='{"choices":[{"message":{"content":[]}}]}',
                    upstream_request_id="req-empty",
                )
            return "<html>fallback-ok</html>"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve_candidates",
            return_value=[primary, secondary],
        ), patch.object(
            llm_client_module.gateway,
            "emit_task_activity",
            new=AsyncMock(),
        ), patch.object(
            llm_client_module.gateway,
            "emit_llm_call_log",
            new=AsyncMock(),
        ) as emit_log, patch.object(
            client,
            "_complete_openai_compatible",
            new=fake_complete_openai,
        ):
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "make me a game"}],
                max_tokens=16,
                step_key="code_generate.full",
                stage="code_generating",
                allow_provider_fallback=True,
            ))

        assert result == "<html>fallback-ok</html>"
        assert attempts == ["provider-primary", "provider-secondary"]
        assert emit_log.await_count == 2
        failure_payload = emit_log.await_args_list[0].args[0]
        success_payload = emit_log.await_args_list[1].args[0]
        assert failure_payload["success"] is False
        assert failure_payload["errorCode"] == "EmptyOpenAICompatibleTextError"
        assert failure_payload["upstreamRequestId"] == "req-empty"
        assert failure_payload["errorBodyExcerpt"] == '{"choices":[{"message":{"content":[]}}]}'
        assert success_payload["success"] is True
        assert success_payload["providerId"] == "provider-secondary"
    finally:
        settings.LLM_MODE = old_mode
        settings.LLM_PROVIDER_FAILOVER_ENABLED = old_failover


def test_complete_fails_over_when_provider_response_is_truncated():
    client = LLMClient()
    primary = SimpleNamespace(
        provider_id="provider-primary",
        provider_name="Claude Primary",
        provider_type="anthropic",
        region="cn-shanghai",
        base_url="https://primary.example",
        api_key="secret",
        model="claude-opus-4-6",
        fast_model="claude-opus-4-6",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "qa_fix.syntax_structural"},
    )
    secondary = SimpleNamespace(
        provider_id="provider-secondary",
        provider_name="MiniMax Secondary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://secondary.example/v1",
        api_key="secret-2",
        model="MiniMax-M2.5",
        fast_model="MiniMax-M2.5",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "qa_fix.syntax_structural"},
    )
    old_mode = settings.LLM_MODE
    old_failover = settings.LLM_PROVIDER_FAILOVER_ENABLED
    settings.LLM_MODE = "real"
    settings.LLM_PROVIDER_FAILOVER_ENABLED = True

    try:
        attempts = []

        async def fake_complete_with_route(**kwargs):
            route = kwargs["route"]
            attempts.append(route.provider_id)
            if route.provider_id == "provider-primary":
                raise LLMResponseTruncatedError(
                    "Anthropic response hit max_tokens and may be truncated",
                    response_excerpt="<html><body><script>function draw(){",
                    upstream_request_id="msg-truncated",
                    stop_reason="max_tokens",
                    output_tokens=4096,
                )
            return "<html>fallback-ok</html>"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve_candidates",
            return_value=[primary, secondary],
        ), patch.object(
            llm_client_module.gateway,
            "emit_task_activity",
            new=AsyncMock(),
        ), patch.object(
            llm_client_module.gateway,
            "emit_llm_call_log",
            new=AsyncMock(),
        ) as emit_log, patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=fake_complete_with_route),
        ):
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "fix the syntax"}],
                max_tokens=16,
                step_key="qa_fix.syntax_structural",
                stage="qa_checking",
                allow_provider_fallback=True,
            ))

        assert result == "<html>fallback-ok</html>"
        assert attempts == ["provider-primary", "provider-secondary"]
        assert emit_log.await_count == 0
    finally:
        settings.LLM_MODE = old_mode
        settings.LLM_PROVIDER_FAILOVER_ENABLED = old_failover


def test_complete_applies_overall_timeout_budget_across_provider_fallbacks():
    client = LLMClient()
    primary = SimpleNamespace(
        provider_id="provider-primary",
        provider_name="Primary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://primary.example/v1",
        api_key="secret",
        model="deepseek-chat",
        fast_model="deepseek-chat",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    secondary = SimpleNamespace(
        provider_id="provider-secondary",
        provider_name="Secondary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://secondary.example/v1",
        api_key="secret-2",
        model="MiniMax-M2.5",
        fast_model="MiniMax-M2.5",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    old_mode = settings.LLM_MODE
    old_failover = settings.LLM_PROVIDER_FAILOVER_ENABLED
    settings.LLM_MODE = "real"
    settings.LLM_PROVIDER_FAILOVER_ENABLED = True

    try:
        seen_timeouts = []

        async def fake_complete_with_route(**kwargs):
            route = kwargs["route"]
            seen_timeouts.append(route.request_timeout_s)
            if route.provider_id == "provider-primary":
                raise httpx.ReadTimeout(
                    "timed out",
                    request=httpx.Request("POST", "https://primary.example/v1/chat/completions"),
                )
            return "ok"

        monotonic_values = [100.0, 100.0, 339.0]

        def fake_monotonic():
            if monotonic_values:
                return monotonic_values.pop(0)
            return 339.0

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve_candidates",
            return_value=[primary, secondary],
        ), patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=fake_complete_with_route),
        ), patch(
            "src.services.llm_client.time.monotonic",
            side_effect=fake_monotonic,
        ):
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "make me a game"}],
                max_tokens=16,
                step_key="code_generate.full",
                stage="code_generating",
                request_timeout_s=240,
                overall_timeout_s=240,
                allow_provider_fallback=True,
            ))

        assert result == "ok"
        assert len(seen_timeouts) == 2
        assert all(timeout <= 240 for timeout in seen_timeouts)
        assert seen_timeouts[1] <= seen_timeouts[0]
    finally:
        settings.LLM_MODE = old_mode
        settings.LLM_PROVIDER_FAILOVER_ENABLED = old_failover


def test_extract_openai_choice_text_supports_nested_text_blocks_and_legacy_text():
    nested_choice = {
        "message": {
            "content": [
                {"type": "output_text", "text": {"value": "<think>ignore</think><html>ok</html>"}},
            ],
        },
    }
    legacy_choice = {
        "text": "```html\n<html>legacy</html>\n```",
    }

    assert _extract_openai_choice_text(nested_choice) == "<html>ok</html>"
    assert _extract_openai_choice_text(legacy_choice) == "```html\n<html>legacy</html>\n```"


def test_complete_anthropic_raises_truncation_error_on_max_tokens_stop_reason():
    client = LLMClient()

    class _FakeUsage:
        output_tokens = 4096

    class _FakeBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeResponse:
        id = "msg_123"
        stop_reason = "max_tokens"
        usage = _FakeUsage()
        content = [_FakeBlock("<!DOCTYPE html><html><body><script>function draw(){")]

    class _FakeMessages:
        @staticmethod
        def create(**_kwargs):
            return _FakeResponse()

    class _FakeClient:
        messages = _FakeMessages()

    route = SimpleNamespace(
        api_key="secret",
        base_url="https://api.example.com",
        model="claude-opus-4-6",
    )

    with patch.object(client, "_get_anthropic_client", return_value=_FakeClient()):
        try:
            client._complete_anthropic(
                route=route,
                messages=[{"role": "user", "content": "fix code"}],
                max_tokens=1024,
                system="SYSTEM",
            )
            raise AssertionError("expected truncation error")
        except LLMResponseTruncatedError as exc:
            assert exc.stop_reason == "max_tokens"
            assert exc.upstream_request_id == "msg_123"
            assert exc.output_tokens == 4096


def test_complete_enforces_request_timeout_for_anthropic_provider_and_logs_failure():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-claude",
        provider_name="Claude Long Running",
        provider_type="anthropic",
        region="cn-shanghai",
        base_url="https://api.anthropic.example",
        api_key="secret",
        model="claude-opus-4-6",
        fast_model="claude-opus-4-6",
        request_timeout_s=1,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        def fake_complete_anthropic(**_kwargs):
            time.sleep(1.2)
            return "<html>too-late</html>"

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
            "_complete_anthropic",
            new=fake_complete_anthropic,
        ):
            try:
                asyncio.run(client.complete(
                    messages=[{"role": "user", "content": "make me a game"}],
                    max_tokens=64,
                    step_key="code_generate.full",
                    stage="code_generating",
                ))
                raise AssertionError("expected anthropic timeout")
            except TimeoutError as exc:
                assert "timed out after 1s" in str(exc)

        activity_states = [call.args[0]["details"]["activityState"] for call in emit_activity.await_args_list]
        assert activity_states[0] == "started"
        assert activity_states[-1] == "failed"
        assert emit_log.await_count == 1
        payload = emit_log.await_args_list[0].args[0]
        assert payload["success"] is False
        assert payload["errorCode"] == "TimeoutError"
        assert "timed out after 1s" in payload["errorMessage"]
    finally:
        settings.LLM_MODE = old_mode


def test_complete_logs_failed_call_when_outer_wait_cancels_it():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-openai",
        provider_name="MiniMax Long Running",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://api.minimaxi.com/v1",
        api_key="secret",
        model="MiniMax-M2.5",
        fast_model="MiniMax-M2.5",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        async def fake_complete_openai(**_kwargs):
            await asyncio.sleep(1)
            return "<html>too-late</html>"

        async def run_and_cancel():
            await asyncio.wait_for(
                client.complete(
                    messages=[{"role": "user", "content": "make me a game"}],
                    max_tokens=64,
                    step_key="code_generate.full",
                    stage="code_generating",
                ),
                timeout=0.01,
            )

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
                asyncio.run(run_and_cancel())
                raise AssertionError("expected outer timeout")
            except TimeoutError:
                pass

        activity_states = [call.args[0]["details"]["activityState"] for call in emit_activity.await_args_list]
        assert activity_states[0] == "started"
        assert activity_states[-1] == "failed"
        assert emit_log.await_count == 1
        payload = emit_log.await_args_list[0].args[0]
        assert payload["success"] is False
        assert payload["errorCode"] == "CancelledError"
        assert payload["errorMessage"] == "LLM call canceled before completion"
    finally:
        settings.LLM_MODE = old_mode
