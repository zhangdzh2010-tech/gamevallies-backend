"""Coverage for OpenAI-compatible URL normalization."""

import os
import sys
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import GameSpec
from src.config.settings import settings
from src.services import llm_client as llm_client_module
from src.services.llm_gateway import llm_request_context
from src.services.llm_client import (
    LLMClient,
    EmptyOpenAICompatibleTextError,
    LLMContextWindowExceededError,
    LLMCompletionResult,
    LLMProviderCapacityError,
    LLMResponseTruncatedError,
    LLMUsageSnapshot,
    _build_anthropic_base_url,
    _build_openai_compatible_chat_url,
    _adaptive_token_budget_enabled_for_step,
    _apply_request_timeout_override,
    _extract_openai_choice_text,
    _extract_openai_message_text,
    _is_anthropic_protocol_mismatch,
)
from src.services.prompt_dedup import deep_dedupe_prompt
from src.services.task_memory import task_memory


def test_build_chat_url_keeps_explicit_chat_completions_path():
    url = _build_openai_compatible_chat_url("https://api.minimaxi.com/v1/chat/completions")
    assert url == "https://api.minimaxi.com/v1/chat/completions"


def test_build_chat_url_appends_v1_for_bare_minimax_host():
    url = _build_openai_compatible_chat_url("https://api.minimaxi.com")
    assert url == "https://api.minimaxi.com/v1/chat/completions"


def test_build_chat_url_appends_v1_for_bare_deepseek_host():
    url = _build_openai_compatible_chat_url("https://api.deepseek.com")
    assert url == "https://api.deepseek.com/v1/chat/completions"


def test_detects_anthropic_protocol_mismatch_from_openai_compatible_400():
    request = httpx.Request("POST", "https://api.gptsapi.net/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        text='{"error":{"message":"This request uses an OpenAI-compatible format. Anthropic-compatible free routing does not support /v1/chat/completions."}}',
    )
    exc = httpx.HTTPStatusError("bad request", request=request, response=response)
    assert _is_anthropic_protocol_mismatch(exc) is True


def test_apply_request_timeout_override_honors_explicit_caller_budget():
    route = SimpleNamespace(
        request_timeout_s=30,
        route_snapshot={"step_key": "intent_parse"},
    )

    overridden = _apply_request_timeout_override(route, 90)

    assert overridden is not route
    assert overridden.request_timeout_s == 90
    assert overridden.route_snapshot["base_request_timeout_s"] == 30
    assert overridden.route_snapshot["request_timeout_override_s"] == 90
    assert overridden.route_snapshot["request_timeout_override_applied"] is True


def test_adaptive_token_budget_is_disabled_for_full_document_generation_steps():
    assert _adaptive_token_budget_enabled_for_step("code_generate.full") is False
    assert _adaptive_token_budget_enabled_for_step("qa_fix.syntax_structural") is False
    assert _adaptive_token_budget_enabled_for_step("intent_parse") is True


def test_unconfigured_provider_limit_preserves_explicit_review_budget():
    route = SimpleNamespace(max_tokens=None)
    assert llm_client_module._resolve_gateway_output_limit(
        route, requested_max_tokens=2048, response_size_hint="small",
    ) == (2048, 2048, "caller_fallback")
    assert llm_client_module._resolve_gateway_output_limit(
        SimpleNamespace(max_tokens=1024), requested_max_tokens=2048,
        response_size_hint="medium_structured",
    ) == (1024, 2048, "caller_capped_by_gateway")


def test_review_retry_starts_with_caller_budget_when_gateway_has_no_limit():
    client = LLMClient()
    with patch.object(client, 'is_enabled', return_value=True), patch.object(
        llm_client_module.gateway, 'resolve', return_value=SimpleNamespace(max_tokens=None)
    ), patch.object(client, 'complete', new=AsyncMock(return_value='{}')) as call, patch.object(
        llm_client_module, '_adaptive_token_budget_enabled_for_step', return_value=False
    ):
        asyncio.run(client.complete_with_truncation_retry(
            messages=[{'role':'user','content':'review'}], max_tokens=2048,
            step_key='code_review', response_size_hint='small',
        ))
    assert call.call_args.kwargs['max_tokens'] == 2048


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
            return LLMCompletionResult(
                text="pong",
                usage=LLMUsageSnapshot(input_tokens=111, output_tokens=222, total_tokens=333),
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
        assert emit_log.await_args_list[0].args[0]["inputTokens"] == 111
        assert emit_log.await_args_list[0].args[0]["outputTokens"] == 222
        assert emit_log.await_args_list[0].args[0]["totalTokens"] == 333
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


def test_complete_fails_over_to_secondary_provider_on_cancelled_error():
    client = LLMClient()
    primary = SimpleNamespace(
        provider_id="provider-primary",
        provider_name="Code Preview Primary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://primary.example/v1",
        api_key="secret",
        model="code-preview",
        fast_model="code-preview",
        request_timeout_s=600,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    secondary = SimpleNamespace(
        provider_id="provider-secondary",
        provider_name="Code Pro Secondary",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://secondary.example/v1",
        api_key="secret-2",
        model="code-pro",
        fast_model="code-pro",
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

        async def fake_complete_with_route(**kwargs):
            route = kwargs["route"]
            attempts.append(route.provider_id)
            if route.provider_id == "provider-primary":
                raise asyncio.CancelledError()
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
                messages=[{"role": "user", "content": "make me a game"}],
                max_tokens=16,
                step_key="code_generate.full",
                stage="code_generating",
                allow_provider_fallback=True,
            ))

        assert result == "<html>fallback-ok</html>"
        assert attempts == ["provider-primary", "provider-secondary"]
        assert emit_log.await_count == 0
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
                    input_tokens=321,
                    output_tokens=123,
                    total_tokens=444,
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
        assert failure_payload["inputTokens"] == 321
        assert failure_payload["outputTokens"] == 123
        assert failure_payload["totalTokens"] == 444
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
                    input_tokens=2048,
                    output_tokens=4096,
                    total_tokens=6144,
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


def test_complete_uses_hedged_provider_fallback_when_requested():
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
    old_hedging = settings.LLM_PROVIDER_HEDGING_ENABLED
    settings.LLM_MODE = "real"
    settings.LLM_PROVIDER_FAILOVER_ENABLED = True
    settings.LLM_PROVIDER_HEDGING_ENABLED = True

    try:
        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve_candidates",
            return_value=[primary, secondary],
        ), patch.object(
            client,
            "_complete_with_hedged_routes",
            new=AsyncMock(return_value="ok"),
        ) as mock_hedged:
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "make me a game"}],
                max_tokens=1024,
                step_key="code_generate.full",
                stage="code_generating",
                request_timeout_s=105,
                overall_timeout_s=210,
                allow_provider_fallback=True,
                hedge_provider_fallback_after_s=45,
            ))

        assert result == "ok"
        assert mock_hedged.await_count == 1
        kwargs = mock_hedged.await_args.kwargs
        assert kwargs["routes"] == [primary, secondary]
        assert kwargs["hedge_after_s"] == 45
    finally:
        settings.LLM_MODE = old_mode
        settings.LLM_PROVIDER_FAILOVER_ENABLED = old_failover
        settings.LLM_PROVIDER_HEDGING_ENABLED = old_hedging


def test_complete_with_hedged_routes_returns_faster_secondary_provider():
    client = LLMClient()
    routes = [
        SimpleNamespace(provider_id="provider-primary", provider_name="Primary", route_snapshot={"provider_id": "provider-primary"}),
        SimpleNamespace(provider_id="provider-secondary", provider_name="Secondary", route_snapshot={"provider_id": "provider-secondary"}),
    ]

    async def fake_prepare(**kwargs):
        route = kwargs["resolved_route"]
        route.route_snapshot = {
            **dict(getattr(route, "route_snapshot", {}) or {}),
            "provider_id": route.provider_id,
            "provider_name": route.provider_name,
            "attempt": kwargs["attempt_index"],
        }
        return llm_client_module._PreparedCompletionAttempt(
            route=route,
            messages=[{"role": "user", "content": "ping"}],
            system=None,
            max_tokens=64,
        )

    async def fake_run(*, prepared, **_kwargs):
        if prepared.route.provider_id == "provider-primary":
            await asyncio.sleep(0.05)
            return "primary"
        await asyncio.sleep(0.01)
        return "secondary"

    with patch.object(client, "_prepare_completion_attempt", new=AsyncMock(side_effect=fake_prepare)), patch.object(
        client,
        "_run_prepared_completion_attempt",
        new=AsyncMock(side_effect=fake_run),
    ):
        result = asyncio.run(
            client._complete_with_hedged_routes(
                routes=routes,
                hedge_after_s=0,
                messages=[{"role": "user", "content": "make me a game"}],
                max_tokens=64,
                system=None,
                step_key="code_generate.full",
                stage="code_generating",
                request_timeout_s=105,
                overall_timeout_s=210,
                response_size_hint="large",
                context_scope="request",
                compression_policy="code_generation",
                return_route_snapshot=False,
            )
        )

    assert result == "secondary"


def test_complete_clamps_max_tokens_to_provider_limit_and_records_metadata():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-capped",
        provider_name="MiniMax Capped",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://provider.example/v1",
        api_key="secret",
        model="MiniMax-M2.7",
        fast_model="MiniMax-M2.7",
        request_timeout_s=600,
        connect_timeout_s=15,
        context_window=128000,
        max_tokens=4096,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        captured = {}

        async def fake_complete_with_route(**kwargs):
            captured["max_tokens"] = kwargs["max_tokens"]
            captured["route_snapshot"] = kwargs["route"].route_snapshot
            return "ok"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve",
            return_value=route,
        ), patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=fake_complete_with_route),
        ):
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "make me a game"}],
                max_tokens=8192,
                step_key="code_generate.full",
                stage="code_generating",
            ))

        assert result == "ok"
        assert captured["max_tokens"] == 4096
        assert captured["route_snapshot"]["requested_max_tokens"] == 8192
        assert captured["route_snapshot"]["effective_max_tokens"] == 4096
        assert captured["route_snapshot"]["provider_max_tokens"] == 4096
        assert captured["route_snapshot"]["provider_context_window"] == 128000
        assert captured["route_snapshot"]["limit_source"] == "caller_capped_by_gateway"
    finally:
        settings.LLM_MODE = old_mode


def test_complete_keeps_requested_output_budget_when_provider_cap_is_higher():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-1",
        provider_name="Doubao Code",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://example.com/v1",
        api_key="secret",
        model="doubao-seed-code-preview",
        fast_model="doubao-seed-code-preview",
        request_timeout_s=600,
        connect_timeout_s=15,
        context_window=256000,
        max_tokens=128000,
        tokenizer_family=None,
        strict_admission=True,
        safety_margin_tokens=12800,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        captured = {}

        async def fake_complete_with_route(**kwargs):
            captured["max_tokens"] = kwargs["max_tokens"]
            captured["route_snapshot"] = kwargs["route"].route_snapshot
            return "ok"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve",
            return_value=route,
        ), patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=fake_complete_with_route),
        ):
            result = asyncio.run(client.complete(
                messages=[{"role": "user", "content": "make me a game"}],
                max_tokens=12288,
                step_key="code_generate.full",
                stage="code_generating",
            ))

        assert result == "ok"
        assert captured["max_tokens"] == 12288
        assert captured["route_snapshot"]["requested_max_tokens"] == 12288
        assert captured["route_snapshot"]["effective_max_tokens"] == 12288
        assert captured["route_snapshot"]["provider_max_tokens"] == 128000
        assert captured["route_snapshot"]["limit_source"] == "caller_requested"
    finally:
        settings.LLM_MODE = old_mode


def test_complete_fails_fast_when_all_routed_providers_are_below_required_output_floor():
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
        context_window=128000,
        max_tokens=8192,
        config_version=123,
        route_snapshot={"step_key": "iterate.mechanic_change"},
    )
    old_mode = settings.LLM_MODE
    old_failover = settings.LLM_PROVIDER_FAILOVER_ENABLED
    settings.LLM_MODE = "real"
    settings.LLM_PROVIDER_FAILOVER_ENABLED = True

    try:
        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve_candidates",
            return_value=[primary],
        ), patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=AssertionError("should fail before upstream call")),
        ):
            try:
                asyncio.run(client.complete(
                    messages=[{"role": "user", "content": "Rewrite the whole game into a richer version."}],
                    max_tokens=8192,
                    step_key="iterate.mechanic_change",
                    stage="code_generating",
                    allow_provider_fallback=True,
                    response_size_hint="xlarge",
                ))
                raise AssertionError("expected provider capacity failure")
            except LLMProviderCapacityError as exc:
                assert exc.required_output_tokens == llm_client_module._required_output_floor(8192, "xlarge")
                assert exc.step_key == "iterate.mechanic_change"
                assert exc.candidate_caps[0]["maxTokens"] == 8192
    finally:
        settings.LLM_MODE = old_mode
        settings.LLM_PROVIDER_FAILOVER_ENABLED = old_failover


def test_complete_injects_task_memory_for_task_scoped_calls():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-capped",
        provider_name="MiniMax Capped",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://provider.example/v1",
        api_key="secret",
        model="MiniMax-M2.7",
        fast_model="MiniMax-M2.7",
        request_timeout_s=600,
        connect_timeout_s=15,
        context_window=128000,
        max_tokens=4096,
        tokenizer_family="openai_cl100k_compatible",
        strict_admission=True,
        safety_margin_tokens=2048,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        captured = {}

        async def run_case():
            await task_memory.begin_task(
                "task-memory-1",
                task_meta={"entrypoint": "create", "title": "Memory Test"},
                source_context_summary="- raw_user_input: make a funny game",
            )
            await task_memory.remember_spec(
                "task-memory-1",
                GameSpec(game_type="funny", intent_summary="A goofy tap challenge"),
            )
            with llm_request_context(game_id="game-1", user_id="user-1", task_id="task-memory-1"):
                return await client.complete(
                    messages=[{"role": "user", "content": "Generate the final HTML."}],
                    step_key="code_generate.full",
                    stage="code_generating",
                    response_size_hint="large",
                    context_scope="task",
                    compression_policy="code_generation",
                )

        async def fake_complete_with_route(**kwargs):
            captured["system"] = kwargs["system"]
            captured["route_snapshot"] = kwargs["route"].route_snapshot
            return "ok"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve",
            return_value=route,
        ), patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=fake_complete_with_route),
        ):
            result = asyncio.run(run_case())

        assert result == "ok"
        assert "TASK MEMORY (shared task context)" in captured["system"]
        assert captured["route_snapshot"]["task_memory_injected"] is True
        assert captured["route_snapshot"]["strict_admission"] is True
        assert captured["route_snapshot"]["context_scope"] == "task"
    finally:
        asyncio.run(task_memory.clear_task("task-memory-1"))
        settings.LLM_MODE = old_mode


def test_deep_dedupe_prompt_preserves_conflicting_structured_values():
    result = deep_dedupe_prompt(
        system="RUNTIME CONTRACT\n- orientation: portrait_first",
        messages=[{
            "role": "user",
            "content": "RUNTIME CONTRACT\n- orientation: landscape",
        }],
        compression_policy="code_generation",
    )

    combined = "\n".join([
        result.system or "",
        *(message["content"] for message in result.messages),
    ])
    assert "portrait_first" in combined
    assert "landscape" in combined
    assert result.metrics.removed_block_count == 0


def test_deep_dedupe_prompt_preserves_one_sided_numeric_constraints():
    result = deep_dedupe_prompt(
        system="SPEC SUMMARY\n- enemy_count: spawn 3 enemies at once",
        messages=[{
            "role": "user",
            "content": "SPEC SUMMARY\n- enemy_count: spawn enemies at once",
        }],
        compression_policy="code_generation",
    )

    combined = "\n".join([
        result.system or "",
        *(message["content"] for message in result.messages),
    ])
    assert "spawn 3 enemies at once" in combined
    assert "spawn enemies at once" in combined
    assert result.metrics.removed_block_count == 0


def test_complete_deep_dedupes_duplicate_task_memory_blocks_and_records_metrics():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-capped",
        provider_name="MiniMax Capped",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://provider.example/v1",
        api_key="secret",
        model="MiniMax-M2.7",
        fast_model="MiniMax-M2.7",
        request_timeout_s=600,
        connect_timeout_s=15,
        context_window=128000,
        max_tokens=4096,
        tokenizer_family="openai_cl100k_compatible",
        strict_admission=True,
        safety_margin_tokens=2048,
        config_version=123,
        route_snapshot={"step_key": "iterate.mechanic_change"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        captured = {}

        async def run_case():
            await task_memory.begin_task(
                "task-memory-dedup-1",
                task_meta={"entrypoint": "iterate", "title": "Dedup Test"},
                source_context_summary=(
                    "LATEST USER REQUEST\n"
                    "- requested_change: make the player jump higher\n"
                    "- keep_controls: tap only"
                ),
            )
            with llm_request_context(game_id="game-1", user_id="user-1", task_id="task-memory-dedup-1"):
                return await client.complete(
                    messages=[{
                        "role": "user",
                        "content": (
                            "LATEST USER REQUEST\n"
                            "- requested_change: make the player jump higher\n"
                            "- keep_controls: tap only\n"
                            "- unique_goal: add floating coins"
                        ),
                    }],
                    step_key="iterate.mechanic_change",
                    stage="code_generating",
                    response_size_hint="large",
                    context_scope="task",
                    compression_policy="iteration_rewrite",
                )

        async def fake_complete_with_route(**kwargs):
            captured["system"] = kwargs["system"]
            captured["messages"] = kwargs["messages"]
            captured["route_snapshot"] = kwargs["route"].route_snapshot
            return "ok"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve",
            return_value=route,
        ), patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=fake_complete_with_route),
        ):
            result = asyncio.run(run_case())

        assert result == "ok"
        combined = "\n".join([
            captured["system"] or "",
            *(message["content"] for message in captured["messages"]),
        ])
        assert combined.count("requested_change: make the player jump higher") == 1
        assert combined.count("keep_controls: tap only") == 1
        assert captured["route_snapshot"]["prompt_dedup_applied"] is True
        assert captured["route_snapshot"]["prompt_dedup_removed_block_count"] >= 2
        assert captured["route_snapshot"]["prompt_dedup_saved_tokens_estimate"] > 0
        assert captured["route_snapshot"]["requested_input_tokens"] > captured["route_snapshot"]["estimated_input_tokens"]
        assert captured["route_snapshot"]["prompt_dedup_base_input_tokens"] == captured["route_snapshot"]["requested_input_tokens"]
        assert captured["route_snapshot"]["prompt_fingerprint"]
        assert any(
            block["reason"] == "exact_duplicate"
            for block in captured["route_snapshot"]["prompt_dedup_removed_blocks"]
        )
    finally:
        asyncio.run(task_memory.clear_task("task-memory-dedup-1"))
        settings.LLM_MODE = old_mode


def test_complete_semantically_dedupes_near_duplicate_task_memory_blocks():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-capped",
        provider_name="MiniMax Capped",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://provider.example/v1",
        api_key="secret",
        model="MiniMax-M2.7",
        fast_model="MiniMax-M2.7",
        request_timeout_s=600,
        connect_timeout_s=15,
        context_window=128000,
        max_tokens=4096,
        tokenizer_family="openai_cl100k_compatible",
        strict_admission=True,
        safety_margin_tokens=2048,
        config_version=123,
        route_snapshot={"step_key": "iterate.mechanic_change"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        captured = {}

        async def run_case():
            await task_memory.begin_task(
                "task-memory-dedup-2",
                task_meta={"entrypoint": "iterate", "title": "Dedup Semantic Test"},
                source_context_summary=(
                    "LATEST USER REQUEST\n"
                    "- requested_change: make the hero move faster and jump higher"
                ),
            )
            with llm_request_context(game_id="game-2", user_id="user-2", task_id="task-memory-dedup-2"):
                return await client.complete(
                    messages=[{
                        "role": "user",
                        "content": (
                            "LATEST USER REQUEST\n"
                            "- requested_change: make the hero move faster, jump higher\n"
                            "- unique_goal: add floating coins"
                        ),
                    }],
                    step_key="iterate.mechanic_change",
                    stage="code_generating",
                    response_size_hint="large",
                    context_scope="task",
                    compression_policy="iteration_rewrite",
                )

        async def fake_complete_with_route(**kwargs):
            captured["system"] = kwargs["system"]
            captured["messages"] = kwargs["messages"]
            captured["route_snapshot"] = kwargs["route"].route_snapshot
            return "ok"

        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve",
            return_value=route,
        ), patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=fake_complete_with_route),
        ):
            result = asyncio.run(run_case())

        assert result == "ok"
        combined = "\n".join([
            captured["system"] or "",
            *(message["content"] for message in captured["messages"]),
        ])
        assert combined.count("requested_change: make the hero move faster") == 1
        assert captured["route_snapshot"]["prompt_dedup_applied"] is True
        assert any(
            block["reason"] == "semantic_duplicate"
            for block in captured["route_snapshot"]["prompt_dedup_removed_blocks"]
        )
    finally:
        asyncio.run(task_memory.clear_task("task-memory-dedup-2"))
        settings.LLM_MODE = old_mode


def test_complete_rejects_requests_that_still_exceed_context_after_compression():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-tight",
        provider_name="Tight Window",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://provider.example/v1",
        api_key="secret",
        model="deepseek-chat",
        fast_model="deepseek-chat",
        request_timeout_s=600,
        connect_timeout_s=15,
        context_window=900,
        max_tokens=512,
        tokenizer_family="openai_cl100k_compatible",
        strict_admission=True,
        safety_margin_tokens=256,
        config_version=123,
        route_snapshot={"step_key": "iterate.mechanic_change"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        with patch.object(llm_client_module.gateway, "has_enabled_provider", return_value=True), patch.object(
            llm_client_module.gateway,
            "resolve",
            return_value=route,
        ), patch.object(
            llm_client_module.gateway,
            "emit_llm_call_log",
            new=AsyncMock(),
        ) as emit_log, patch.object(
            llm_client_module.gateway,
            "emit_task_activity",
            new=AsyncMock(),
        ), patch.object(
            client,
            "_complete_with_route",
            new=AsyncMock(side_effect=AssertionError("request should fail before upstream call")),
        ):
            try:
                asyncio.run(client.complete(
                    messages=[{
                        "role": "user",
                        "content": "CURRENT CODE\n\n" + ("摸鱼小游戏逻辑需要完整保留。 " * 1800),
                    }],
                    step_key="iterate.mechanic_change",
                    stage="code_generating",
                    response_size_hint="large",
                    context_scope="request",
                    compression_policy="iteration_rewrite",
                ))
                raise AssertionError("expected context overflow")
            except LLMContextWindowExceededError as exc:
                assert exc.allowed_input_tokens < exc.estimated_input_tokens
                assert exc.context_window == 900
        assert emit_log.await_count == 1
        payload = emit_log.await_args_list[0].args[0]
        assert payload["success"] is False
        assert payload["errorCode"] == "LLMContextWindowExceededError"
        assert payload["routeSnapshot"]["estimated_input_tokens"] > payload["routeSnapshot"]["allowed_input_tokens"]
        assert payload["routeSnapshot"]["prompt_fingerprint"]
    finally:
        settings.LLM_MODE = old_mode


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


def test_complete_openai_compatible_accepts_complete_html_even_when_finish_reason_is_length():
    client = LLMClient()
    route = SimpleNamespace(
        api_key="secret",
        base_url="https://api.example.com/v1",
        model="MiniMax-M2.7",
        request_timeout_s=60,
        connect_timeout_s=15,
    )
    payload = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "content": "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>",
                },
            }
        ],
        "usage": {
            "prompt_tokens": 111,
            "completion_tokens": 222,
            "total_tokens": 333,
        },
    }

    class _FakeResponse:
        headers = httpx.Headers({"x-request-id": "req-complete-html"})
        text = '{"choices":[{"finish_reason":"length"}]}'

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return payload

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _FakeResponse()

    with patch("src.services.llm_client.httpx.AsyncClient", _FakeAsyncClient):
        result = asyncio.run(
            client._complete_openai_compatible(
                route=route,
                messages=[{"role": "user", "content": "fix layout"}],
                max_tokens=1024,
                system="SYSTEM",
            )
        )

    assert result.text == "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>"
    assert result.usage.input_tokens == 111
    assert result.usage.output_tokens == 222
    assert result.usage.total_tokens == 333


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


def test_complete_anthropic_accepts_complete_html_even_when_stop_reason_is_max_tokens():
    client = LLMClient()

    class _FakeUsage:
        output_tokens = 4096

    class _FakeBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeResponse:
        id = "msg_complete"
        stop_reason = "max_tokens"
        usage = _FakeUsage()
        content = [_FakeBlock("<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>")]

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
        result = client._complete_anthropic(
            route=route,
            messages=[{"role": "user", "content": "fix code"}],
            max_tokens=1024,
            system="SYSTEM",
        )

    assert result.text == "<!DOCTYPE html><html><body><canvas id='gameCanvas'></canvas></body></html>"
    assert result.usage.output_tokens == 4096


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


def test_complete_with_truncation_retry_appends_size_guidance_even_when_budget_cannot_grow():
    client = LLMClient()

    with patch.object(
        client,
        "complete",
        new=AsyncMock(side_effect=[
            LLMResponseTruncatedError(
                "OpenAI-compatible response hit the output length limit and may be truncated",
                stop_reason="length",
                output_tokens=4096,
                partial_text="<!DOCTYPE html><html><body><script>const x=1;",
            ),
            "<!DOCTYPE html><html><body>ok</body></html>",
        ]),
    ) as mock_complete:
        result = asyncio.run(
            client.complete_with_truncation_retry(
                messages=[{"role": "user", "content": "generate"}],
                max_tokens=4096,
                step_key="code_generate.full",
                stage="code_generating",
                truncation_retry_attempts=1,
                truncation_retry_increment=2048,
                truncation_retry_max_tokens=4096,
                truncation_retry_guidance="OUTPUT SIZE CONSTRAINT (HARD): finish a compact HTML document.",
            )
        )

    assert result == "<!DOCTYPE html><html><body>ok</body></html>"
    assert mock_complete.await_count == 2
    assert mock_complete.await_args_list[0].kwargs["max_tokens"] == 4096
    assert mock_complete.await_args_list[1].kwargs["max_tokens"] == 4096
    second_messages = mock_complete.await_args_list[1].kwargs["messages"]
    assert second_messages[-1]["content"].startswith("OUTPUT SIZE CONSTRAINT")


def test_complete_with_truncation_retry_retries_with_larger_budget():
    client = LLMClient()

    with patch.object(
        client,
        "complete",
        new=AsyncMock(side_effect=[
            LLMResponseTruncatedError(
                "OpenAI-compatible response hit the output length limit and may be truncated",
                stop_reason="length",
                output_tokens=5207,
            ),
            "<!DOCTYPE html><html><body>ok</body></html>",
        ]),
    ) as mock_complete:
        result = asyncio.run(
            client.complete_with_truncation_retry(
                messages=[{"role": "user", "content": "repair"}],
                max_tokens=5207,
                step_key="qa_fix",
                stage="qa_checking",
                truncation_retry_attempts=1,
                truncation_retry_increment=2048,
                truncation_retry_max_tokens=12288,
            )
        )

    assert result == "<!DOCTYPE html><html><body>ok</body></html>"
    assert mock_complete.await_count == 2
    first_budget = mock_complete.await_args_list[0].kwargs["max_tokens"]
    second_budget = mock_complete.await_args_list[1].kwargs["max_tokens"]
    assert second_budget > first_budget


def test_complete_with_truncation_retry_retries_timeout_with_longer_request_timeout():
    client = LLMClient()

    with patch.object(
        client,
        "complete",
        new=AsyncMock(side_effect=[
            httpx.ReadTimeout("timed out"),
            "<!DOCTYPE html><html><body>ok</body></html>",
        ]),
    ) as mock_complete:
        result = asyncio.run(
            client.complete_with_truncation_retry(
                messages=[{"role": "user", "content": "repair"}],
                max_tokens=4096,
                step_key="qa_fix.syntax_structural",
                stage="qa_checking",
                request_timeout_s=180,
                overall_timeout_s=180,
                timeout_retry_attempts=1,
                timeout_retry_increment_s=60,
                timeout_retry_max_s=300,
            )
        )

    assert result == "<!DOCTYPE html><html><body>ok</body></html>"
    assert mock_complete.await_count == 2
    first_timeout = mock_complete.await_args_list[0].kwargs["request_timeout_s"]
    second_timeout = mock_complete.await_args_list[1].kwargs["request_timeout_s"]
    first_overall_timeout = mock_complete.await_args_list[0].kwargs["overall_timeout_s"]
    second_overall_timeout = mock_complete.await_args_list[1].kwargs["overall_timeout_s"]
    assert second_timeout > first_timeout
    assert second_overall_timeout >= second_timeout
    assert first_overall_timeout == 180


def test_complete_with_truncation_retry_retries_retryable_provider_errors_with_backoff():
    client = LLMClient()
    request = httpx.Request("POST", "https://ark.cn-beijing.volces.com/api/v1/chat/completions")
    response = httpx.Response(
        429,
        request=request,
        headers={"retry-after": "3"},
        text='{"error":{"message":"rate limit"}}',
    )
    rate_limit_exc = httpx.HTTPStatusError("rate limited", request=request, response=response)

    with patch.object(
        client,
        "complete",
        new=AsyncMock(side_effect=[
            rate_limit_exc,
            "<!DOCTYPE html><html><body>ok</body></html>",
        ]),
    ) as mock_complete, patch.object(
        llm_client_module.asyncio,
        "sleep",
        new=AsyncMock(),
    ) as mock_sleep:
        result = asyncio.run(
            client.complete_with_truncation_retry(
                messages=[{"role": "user", "content": "repair"}],
                max_tokens=4096,
                step_key="code_iterate.full",
                stage="code_generating",
                allow_provider_fallback=True,
                provider_retry_attempts=1,
                provider_retry_base_delay_s=2,
                provider_retry_max_delay_s=8,
            )
        )

    assert result == "<!DOCTYPE html><html><body>ok</body></html>"
    assert mock_complete.await_count == 2
    assert mock_sleep.await_count == 1
    assert mock_sleep.await_args_list[0].args[0] == 3.0


def test_complete_with_truncation_retry_retries_403_without_creative_guidance():
    client = LLMClient()
    request = httpx.Request("POST", "https://www.zltokens.com/v1/chat/completions")
    response = httpx.Response(
        403,
        request=request,
        headers={"x-request-id": "zl-403"},
        text='{"error":{"message":"Forbidden","code":"quota"}}',
    )
    forbidden = httpx.HTTPStatusError("forbidden", request=request, response=response)

    with patch.object(
        client,
        "complete",
        new=AsyncMock(side_effect=[
            forbidden,
            "<!DOCTYPE html><html><body>ok</body></html>",
        ]),
    ) as mock_complete, patch.object(
        llm_client_module.asyncio,
        "sleep",
        new=AsyncMock(),
    ) as mock_sleep:
        result = asyncio.run(
            client.complete_with_truncation_retry(
                messages=[{"role": "user", "content": "generate"}],
                max_tokens=4096,
                step_key="code_generate.full",
                stage="code_generating",
                provider_retry_attempts=1,
                provider_retry_base_delay_s=2,
                provider_retry_max_delay_s=8,
            )
        )

    assert result == "<!DOCTYPE html><html><body>ok</body></html>"
    assert mock_complete.await_count == 2
    assert mock_sleep.await_count == 1
    assert mock_complete.await_args_list[0].kwargs.get("generation_guidance") is None
    assert mock_complete.await_args_list[1].kwargs.get("generation_guidance") is None


def test_complete_with_truncation_retry_retries_cancelled_error_with_backoff():
    client = LLMClient()

    with patch.object(
        client,
        "complete",
        new=AsyncMock(side_effect=[
            asyncio.CancelledError(),
            "<!DOCTYPE html><html><body>ok</body></html>",
        ]),
    ) as mock_complete, patch.object(
        llm_client_module.asyncio,
        "sleep",
        new=AsyncMock(),
    ) as mock_sleep:
        result = asyncio.run(
            client.complete_with_truncation_retry(
                messages=[{"role": "user", "content": "repair"}],
                max_tokens=4096,
                step_key="code_generate.full",
                stage="code_generating",
                allow_provider_fallback=True,
                provider_retry_attempts=1,
                provider_retry_base_delay_s=2,
                provider_retry_max_delay_s=8,
            )
        )

    assert result == "<!DOCTYPE html><html><body>ok</body></html>"
    assert mock_complete.await_count == 2
    assert mock_sleep.await_count == 1
    assert mock_sleep.await_args_list[0].args[0] == 2.0


def test_complete_with_truncation_retry_does_not_provider_retry_timeout_when_disabled():
    client = LLMClient()

    with patch.object(
        client,
        "complete",
        new=AsyncMock(side_effect=httpx.ReadTimeout("timed out")),
    ) as mock_complete, patch.object(
        llm_client_module.asyncio,
        "sleep",
        new=AsyncMock(),
    ) as mock_sleep:
        try:
            asyncio.run(
                client.complete_with_truncation_retry(
                    messages=[{"role": "user", "content": "repair"}],
                    max_tokens=4096,
                    step_key="code_generate.full",
                    stage="code_generating",
                    provider_retry_attempts=1,
                    provider_retry_on_timeout_errors=False,
                    timeout_retry_attempts=0,
                )
            )
            raise AssertionError("expected ReadTimeout to be re-raised")
        except httpx.ReadTimeout:
            pass

    assert mock_complete.await_count == 1
    assert mock_sleep.await_count == 0


def test_complete_with_route_attaches_route_snapshot_to_transport_errors():
    client = LLMClient()
    route = SimpleNamespace(
        provider_id="provider-primary",
        provider_name="Primary Codegen",
        provider_type="openai_compatible",
        region="cn-shanghai",
        base_url="https://primary.example/v1",
        api_key="secret",
        model="codegen-primary",
        fast_model="codegen-primary",
        request_timeout_s=120,
        connect_timeout_s=15,
        config_version=123,
        route_snapshot={"step_key": "code_generate.full", "provider_id": "provider-primary"},
    )
    old_mode = settings.LLM_MODE
    settings.LLM_MODE = "real"

    try:
        async def fake_complete_openai(**_kwargs):
            raise httpx.ReadTimeout(
                "timed out",
                request=httpx.Request("POST", "https://primary.example/v1/chat/completions"),
            )

        with patch.object(
            llm_client_module.gateway,
            "emit_task_activity",
            new=AsyncMock(),
        ), patch.object(
            llm_client_module.gateway,
            "emit_llm_call_log",
            new=AsyncMock(),
        ), patch.object(
            client,
            "_complete_openai_compatible",
            new=fake_complete_openai,
        ):
            try:
                asyncio.run(
                    client._complete_with_route(
                        route=route,
                        messages=[{"role": "user", "content": "make a game"}],
                        max_tokens=2048,
                        system=None,
                        step_key="code_generate.full",
                        stage="code_generating",
                    )
                )
                raise AssertionError("expected ReadTimeout")
            except httpx.ReadTimeout as exc:
                assert getattr(exc, "route_snapshot", {}).get("step_key") == "code_generate.full"
                assert getattr(exc, "route_snapshot", {}).get("provider_id") == "provider-primary"
    finally:
        settings.LLM_MODE = old_mode
