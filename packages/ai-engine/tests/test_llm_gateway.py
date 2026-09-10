import asyncio
import os
import sys
import httpx
import pytest
from unittest.mock import patch
from unittest.mock import AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.llm_gateway import LLMGateway, ProviderRecord, RouteRecord, llm_request_context, LLMBusinessConfigurationError


def _provider(
    provider_id: str,
    name: str,
    *,
    max_tokens: int | None = None,
    updated_at: float = 100.0,
) -> ProviderRecord:
    return ProviderRecord(
        id=provider_id,
        name=name,
        provider_type="openai_compatible",
        region="cn_shanghai",
        base_url=f"https://{provider_id}.example.com/v1",
        api_key=f"secret-{provider_id}",
        model=f"model-{provider_id}",
        fast_model=f"fast-{provider_id}",
        request_timeout_s=600,
        connect_timeout_s=15,
        enabled=True,
        priority=100,
        description=None,
        capability_flags={},
        extra_config=({"maxTokens": max_tokens} if max_tokens is not None else {}),
        context_window=None,
        max_tokens=max_tokens,
        tokenizer_family=None,
        strict_admission=False,
        safety_margin_tokens=None,
        updated_at=updated_at,
    )


def _route(route_id: str, step_key: str, provider_id: str, *, updated_at: float = 100.0) -> RouteRecord:
    return RouteRecord(
        id=route_id,
        step_key=step_key,
        region="cn_shanghai",
        provider_id=provider_id,
        fallback_provider_ids=[],
        model_override=None,
        fast_model_override=None,
        request_timeout_s=None,
        connect_timeout_s=None,
        enabled=True,
        updated_at=updated_at,
    )


def _gateway(*, providers: list[ProviderRecord], routes: list[RouteRecord]) -> LLMGateway:
    gateway = LLMGateway()
    gateway._providers = {provider.id: provider for provider in providers}
    gateway._routes = routes
    gateway._config_version = 123
    gateway._loaded_at = 1e12
    return gateway


def test_route_model_override_does_not_leak_to_backup_provider():
    primary, backup = _provider("primary", "Primary"), _provider("backup", "Backup")
    route = _route("route", "intent_parse", primary.id)
    route.model_override = "primary-custom"
    route.fast_model_override = "primary-custom"
    gateway = _gateway(providers=[primary, backup], routes=[route])
    first = gateway._build_resolved_route(provider=primary, route=route, step_key="intent_parse", prefer_fast=True)
    second = gateway._build_resolved_route(provider=backup, route=route, step_key="intent_parse", prefer_fast=True)
    assert first.model == "primary-custom"
    assert second.model == backup.model


def test_smoke_test_uses_saved_primary_not_fast_model():
    provider = _provider("primary", "Primary")
    gateway = _gateway(providers=[provider], routes=[])
    with patch.object(gateway, "refresh"), patch.object(gateway, "_persist_test_record"), \
         patch.object(gateway, "_invoke_test_completion", new=AsyncMock(return_value=("PONG", 200, provider.base_url))) as invoke:
        result = asyncio.run(gateway.test_provider(provider.id))
    assert result["model"] == provider.model
    assert invoke.await_args.kwargs["route"].model != provider.fast_model


def test_smoke_failure_keeps_real_http_status_in_record():
    provider = _provider("primary", "Primary")
    gateway = _gateway(providers=[provider], routes=[])
    request = httpx.Request("POST", provider.base_url + "/chat/completions")
    response = httpx.Response(504, request=request)
    exc = httpx.HTTPStatusError("timeout", request=request, response=response)
    with patch.object(gateway, "refresh"), patch.object(gateway, "_persist_test_record") as persist, \
         patch.object(gateway, "_invoke_test_completion", new=AsyncMock(side_effect=exc)):
        result = asyncio.run(gateway.test_provider(provider.id))
    assert result["httpStatus"] == 504
    assert result["success"] is False
    assert result["transportEvidence"]["source"] == "upstream_http_response"
    assert persist.call_args.kwargs["http_status"] == 504


def test_smoke_empty_reply_is_not_success():
    provider = _provider("primary", "Primary")
    gateway = _gateway(providers=[provider], routes=[])
    with patch.object(gateway, "refresh"), patch.object(gateway, "_persist_test_record"), \
         patch.object(gateway, "_invoke_test_completion", new=AsyncMock(return_value=("", 200, provider.base_url))):
        result = asyncio.run(gateway.test_provider(provider.id))
    assert result["success"] is False


def business_gateway():
    provider = _provider('shared', 'Shared')
    gateway = _gateway(providers=[provider], routes=[_route('legacy', 'code_generate.full', provider.id)])
    gateway._models = {key: dict(id=key, provider_id=provider.id, model_id=name, enabled=True,
                                configuration_version=3, capability_flags={}, max_output_tokens=20000)
                       for key, name in [('model-a', 'large-model'), ('model-b', 'backup-model')]}
    gateway._business_bindings = [dict(id='binding', region='cn_shanghai', stage='generate', revision=2,
        step_keys=['code_generate.full', 'quality_gate.patch_fix', 'qa_fix.syntax_structural'],
        primary_model_id='model-a', fallback_model_id='model-b')]
    return gateway


@patch('src.services.llm_gateway.settings.SERVICE_REGION', 'cn_shanghai')
def test_business_binding_owns_model_and_supports_same_provider_backup():
    gateway = business_gateway()
    routes = gateway.resolve_candidates(step_key='code_generate.full', prefer_fast=True, model_override='stale-override')
    assert [route.model for route in routes] == ['large-model', 'backup-model']
    assert routes[0].provider_id == routes[1].provider_id == 'shared'
    assert routes[0].route_snapshot['model_config_id'] == 'model-a'
    assert routes[1].route_snapshot['is_primary_provider'] is False
    assert routes[0].route_snapshot['business_binding_revision'] == 2


@patch('src.services.llm_gateway.settings.SERVICE_REGION', 'cn_shanghai')
def test_business_binding_includes_dynamic_children_and_keeps_legacy_for_other_steps():
    gateway = business_gateway()
    assert gateway.resolve_candidates(step_key='code_generate.full.retry')[0].model == 'large-model'
    assert gateway.resolve_candidates(step_key='intent_parse')[0].model == 'model-shared'


@patch('src.services.llm_gateway.settings.SERVICE_REGION', 'cn_shanghai')
def test_configured_business_with_unavailable_models_fails_closed():
    gateway = business_gateway()
    gateway._models['model-a']['enabled'] = False
    gateway._models['model-b']['enabled'] = False
    with pytest.raises(LLMBusinessConfigurationError):
        gateway.resolve_candidates(step_key='code_generate.full')


@patch('src.services.llm_gateway.settings.SERVICE_REGION', 'cn_shanghai')
def test_business_output_budget_uses_explicit_backup_not_unrelated_provider():
    gateway = business_gateway()
    gateway._models['model-a']['max_output_tokens'] = 1000
    routes = gateway.resolve_candidates(step_key='code_generate.full', required_output_tokens=16384)
    assert [route.model for route in routes] == ['backup-model']
    assert routes[0].route_snapshot['model_rejections'][0]['reason'] == 'output_limit_insufficient'


@patch('src.services.llm_gateway.settings.SERVICE_REGION', 'cn_shanghai')
def test_business_keeps_explicit_legacy_capability_restrictions():
    gateway = business_gateway()
    gateway._models['model-a']['capability_flags'] = {'supports_full_html_rewrite': False}
    assert [route.model for route in gateway.resolve_candidates(step_key='code_generate.full')] == ['backup-model']


@patch('src.services.llm_gateway.settings.SERVICE_REGION', 'cn_shanghai')
def test_business_recovers_old_unchecked_flags_without_claiming_verified_support():
    gateway = business_gateway()
    flags = {'verified': False, 'supports_dialogue': False, 'supports_patch_generation': False, 'supports_full_html_rewrite': False}
    for model in gateway._models.values():
        model['capability_flags'] = dict(flags)
    routes = gateway.resolve_candidates(step_key='code_generate.full')
    assert len(routes) == 2
    assert routes[0].route_snapshot['provider_capability_flags'] == {'verified': False}
    assert routes[0].route_snapshot['raw_model_capability_flags'] == flags
    assert routes[0].route_snapshot['capability_interpretation'] == 'legacy_unchecked_unknown'
    assert gateway._models['model-a']['capability_flags'] == flags


@patch('src.services.llm_gateway.settings.SERVICE_REGION', 'cn_shanghai')
def test_business_error_reports_the_stage_step_and_concrete_rejection():
    gateway = business_gateway()
    for model in gateway._models.values():
        model['capability_flags'] = {'supports_full_html_rewrite': False}
    with pytest.raises(LLMBusinessConfigurationError, match='code_generate.full.*explicit_capability_restriction') as error:
        gateway.resolve_candidates(step_key='code_generate.full')
    assert error.value.route_snapshot['step_key'] == 'code_generate.full'
    assert len(error.value.route_snapshot['model_rejections']) == 2


def test_model_test_uses_model_record_instead_of_provider_default():
    gateway = business_gateway()
    with patch.object(gateway, 'refresh'), patch.object(gateway, '_persist_test_record'), \
         patch.object(gateway, '_invoke_test_completion', new=AsyncMock(return_value=('PONG', 200, 'https://shared.example'))) as call:
        result = asyncio.run(gateway.test_model('model-b'))
    assert result['model'] == 'backup-model'
    assert result['modelConfigVersion'] == 3
    assert result['modelConfigId'] == 'model-b'
    assert call.await_args.kwargs['route'].request_timeout_s <= 65


@patch('src.services.llm_gateway.settings.SERVICE_REGION', 'cn_shanghai')
def test_explicit_business_backup_works_without_legacy_failover_flags():
    from src.services.llm_client import LLMClient
    gateway = business_gateway()
    client = LLMClient()
    called = []
    async def complete(**kwargs):
        called.append(kwargs['route'].model)
        if len(called) == 1:
            request = httpx.Request('POST', 'https://fixture.invalid/v1/chat/completions')
            raise httpx.HTTPStatusError('timeout', request=request, response=httpx.Response(504, request=request))
        return 'backup success'
    with patch('src.services.llm_client.gateway', gateway), \
         patch('src.services.llm_client.settings.LLM_PROVIDER_FAILOVER_ENABLED', False), \
         patch.object(client, 'is_enabled', return_value=True), \
         patch.object(gateway, 'emit_task_activity', new=AsyncMock()), \
         patch.object(gateway, 'emit_llm_call_log', new=AsyncMock()), \
         patch.object(client, '_complete_openai_compatible', new=complete):
        result = asyncio.run(client.complete(messages=[{'role': 'user', 'content': 'fixture'}],
            step_key='code_generate.full', max_tokens=100, allow_provider_fallback=False))
    assert result == 'backup success'
    assert called == ['large-model', 'backup-model']


def test_resolve_candidates_falls_back_to_parent_route_for_dynamic_step():
    minimax = _provider("provider-minimax", "MiniMax Shanghai")
    deepseek = _provider("provider-deepseek", "DeepSeek Shanghai", updated_at=90.0)
    gateway = _gateway(
        providers=[minimax, deepseek],
        routes=[_route("route-codegen", "code_generate.full", minimax.id)],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(step_key="code_generate.full.variant")

    assert candidates[0].provider_id == minimax.id
    assert candidates[0].route_snapshot["requested_step_key"] == "code_generate.full.variant"
    assert candidates[0].route_snapshot["matched_step_key"] == "code_generate.full"
    assert candidates[0].route_snapshot["route_match_strategy"] == "parent_step"


def test_resolve_candidates_prefers_exact_route_over_parent_route():
    minimax = _provider("provider-minimax", "MiniMax Shanghai")
    deepseek = _provider("provider-deepseek", "DeepSeek Shanghai", updated_at=90.0)
    gateway = _gateway(
        providers=[minimax, deepseek],
        routes=[
            _route("route-codegen", "code_generate.full", minimax.id),
            _route("route-codegen-variant", "code_generate.full.variant", deepseek.id, updated_at=101.0),
        ],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(step_key="code_generate.full.variant")

    assert candidates[0].provider_id == deepseek.id
    assert candidates[0].route_snapshot["requested_step_key"] == "code_generate.full.variant"
    assert candidates[0].route_snapshot["matched_step_key"] == "code_generate.full.variant"
    assert candidates[0].route_snapshot["route_match_strategy"] == "exact"


def test_route_exists_empty_fallback_only_returns_primary_provider():
    """When a route exists with empty fallback_provider_ids,
    only the primary provider should be returned — no implicit
    regional or global providers."""
    primary = _provider("provider-primary", "Primary Provider")
    unrelated = _provider("provider-unrelated", "Unrelated Provider")
    gateway = _gateway(
        providers=[primary, unrelated],
        routes=[_route("route-intent", "intent_parse", primary.id)],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(step_key="intent_parse")

    assert len(candidates) == 1
    assert candidates[0].provider_id == primary.id
    assert candidates[0].route_snapshot["explicit_fallback_only"] is True


def test_route_exists_with_explicit_fallbacks_returns_primary_and_fallbacks():
    """When a route has explicit fallback_provider_ids,
    return primary + fallbacks only — no implicit providers."""
    primary = _provider("provider-primary", "Primary")
    fallback = _provider("provider-fallback", "Fallback")
    unrelated = _provider("provider-unrelated", "Unrelated")

    route = RouteRecord(
        id="route-codegen",
        step_key="code_generate.full",
        region="cn_shanghai",
        provider_id=primary.id,
        fallback_provider_ids=[fallback.id],
        model_override=None,
        fast_model_override=None,
        request_timeout_s=None,
        connect_timeout_s=None,
        enabled=True,
        updated_at=100.0,
    )
    gateway = _gateway(
        providers=[primary, fallback, unrelated],
        routes=[route],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(step_key="code_generate.full")

    assert len(candidates) == 2
    assert candidates[0].provider_id == primary.id
    assert candidates[1].provider_id == fallback.id
    assert candidates[0].route_snapshot["explicit_fallback_only"] is True


def test_resolve_candidates_keeps_primary_provider_first_for_code_generation():
    primary = _provider("provider-primary", "Primary")
    fallback = _provider("provider-fallback", "Fallback")
    tertiary = _provider("provider-tertiary", "Tertiary")

    route = RouteRecord(
        id="route-codegen",
        step_key="code_generate.full",
        region="cn_shanghai",
        provider_id=primary.id,
        fallback_provider_ids=[fallback.id, tertiary.id],
        model_override=None,
        fast_model_override=None,
        request_timeout_s=None,
        connect_timeout_s=None,
        enabled=True,
        updated_at=100.0,
    )
    gateway = _gateway(
        providers=[primary, fallback, tertiary],
        routes=[route],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        with llm_request_context(task_id="task-rotate"):
            candidates = gateway.resolve_candidates(step_key="code_generate.full")

    assert [candidate.provider_id for candidate in candidates] == [primary.id, fallback.id, tertiary.id]
    assert candidates[0].route_snapshot["explicit_fallback_only"] is True


def test_no_route_falls_back_to_all_regional_providers():
    """When no route matches the step_key, all enabled providers
    in the same region should be returned as candidates."""
    provider_a = _provider("provider-a", "Provider A")
    provider_b = _provider("provider-b", "Provider B")
    gateway = _gateway(
        providers=[provider_a, provider_b],
        routes=[],  # no routes configured
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(step_key="some_unknown_step")

    assert len(candidates) == 2
    provider_ids = {c.provider_id for c in candidates}
    assert provider_a.id in provider_ids
    assert provider_b.id in provider_ids
    assert candidates[0].route_snapshot["explicit_fallback_only"] is False


def test_resolve_candidates_exposes_provider_context_and_max_tokens_in_route_snapshot():
    provider = ProviderRecord(
        id="provider-capped",
        name="Provider Capped",
        provider_type="openai_compatible",
        region="cn_shanghai",
        base_url="https://provider-capped.example.com/v1",
        api_key="secret-provider-capped",
        model="model-provider-capped",
        fast_model="fast-provider-capped",
        request_timeout_s=600,
        connect_timeout_s=15,
        enabled=True,
        priority=100,
        description=None,
        capability_flags={},
        extra_config={"contextWindow": 128000, "maxTokens": 8192},
        context_window=128000,
        max_tokens=8192,
        tokenizer_family=None,
        strict_admission=True,
        safety_margin_tokens=None,
        updated_at=100.0,
    )
    gateway = _gateway(
        providers=[provider],
        routes=[_route("route-codegen", "code_generate.full", provider.id)],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(
            step_key="code_generate.full",
            required_output_tokens=12288,
        )

    assert candidates[0].context_window == 128000
    assert candidates[0].max_tokens == 8192
    assert candidates[0].strict_admission is True
    assert candidates[0].route_snapshot["context_window"] == 128000
    assert candidates[0].route_snapshot["max_tokens"] == 8192
    assert candidates[0].route_snapshot["strict_admission"] is True


def test_resolve_candidates_keeps_route_primary_when_implicit_failover_is_not_requested():
    deepseek = _provider("provider-deepseek", "DeepSeek Shanghai", max_tokens=8192)
    minimax = _provider("provider-minimax", "MiniMax Shanghai", max_tokens=16384, updated_at=90.0)
    gateway = _gateway(
        providers=[deepseek, minimax],
        routes=[_route("route-iterate", "iterate.mechanic_change", deepseek.id)],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(
            step_key="iterate.mechanic_change",
            required_output_tokens=12288,
        )

    assert len(candidates) == 1
    assert candidates[0].provider_id == deepseek.id
    assert candidates[0].route_snapshot["implicit_provider_failover"] is False
    assert candidates[0].route_snapshot["required_output_tokens"] == 12288
    assert candidates[0].route_snapshot["explicit_fallback_only"] is True


def test_resolve_candidates_uses_explicit_fallback_chain_without_implicit_provider_pool():
    primary = _provider("provider-primary", "Primary Routed", max_tokens=8192)
    fallback = _provider("provider-fallback", "Implicit Fallback", max_tokens=16384, updated_at=90.0)
    gateway = _gateway(
        providers=[primary, fallback],
        routes=[RouteRecord(
            id="route-codegen",
            step_key="code_generate.full",
            region="cn_shanghai",
            provider_id=primary.id,
            fallback_provider_ids=[fallback.id],
            model_override=None,
            fast_model_override=None,
            request_timeout_s=None,
            connect_timeout_s=None,
            enabled=True,
            updated_at=100.0,
        )],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(
            step_key="code_generate.full",
            required_output_tokens=12288,
        )

    assert len(candidates) == 2
    assert candidates[0].provider_id == fallback.id
    assert candidates[0].route_snapshot["implicit_provider_failover"] is False
    assert candidates[0].route_snapshot["explicit_fallback_only"] is True
    assert candidates[1].provider_id == primary.id
    assert candidates[1].route_snapshot["implicit_provider_failover"] is False


def test_resolve_candidates_can_exclude_primary_provider_to_advance_explicit_fallback_chain():
    primary = _provider("provider-primary", "Primary Routed", max_tokens=16384)
    fallback = _provider("provider-fallback", "Fallback Routed", max_tokens=16384, updated_at=90.0)
    gateway = _gateway(
        providers=[primary, fallback],
        routes=[RouteRecord(
            id="route-codegen",
            step_key="code_generate.full",
            region="cn_shanghai",
            provider_id=primary.id,
            fallback_provider_ids=[fallback.id],
            model_override=None,
            fast_model_override=None,
            request_timeout_s=None,
            connect_timeout_s=None,
            enabled=True,
            updated_at=100.0,
        )],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(
            step_key="code_generate.full",
            excluded_provider_ids=[primary.id],
        )

    assert len(candidates) == 1
    assert candidates[0].provider_id == fallback.id
    assert candidates[0].route_snapshot["fallback_provider_ids"] == [fallback.id]


def test_invoke_test_completion_clamps_max_tokens_to_provider_limit():
    gateway = LLMGateway()
    provider = ProviderRecord(
        id="provider-capped",
        name="Provider Capped",
        provider_type="openai_compatible",
        region="cn_shanghai",
        base_url="https://provider-capped.example.com/v1",
        api_key="secret-provider-capped",
        model="model-provider-capped",
        fast_model="fast-provider-capped",
        request_timeout_s=600,
        connect_timeout_s=15,
        enabled=True,
        priority=100,
        description=None,
        capability_flags={},
        extra_config={"maxTokens": 512},
        context_window=None,
        max_tokens=512,
        tokenizer_family=None,
        strict_admission=False,
        safety_margin_tokens=None,
        updated_at=100.0,
    )
    route = gateway._resolved_route_for_provider(provider)
    captured = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers=None, json=None):
            captured["endpoint"] = endpoint
            captured["max_tokens"] = json["max_tokens"]
            return _FakeResponse()

    with patch("src.services.llm_gateway.httpx.AsyncClient", _FakeAsyncClient):
        reply, http_status, endpoint = asyncio.run(
            gateway._invoke_test_completion(
                route=route,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=2048,
            )
        )

    assert reply == "ok"
    assert http_status == 200
    assert endpoint == "https://provider-capped.example.com/v1/chat/completions"
    assert captured["max_tokens"] == 512
