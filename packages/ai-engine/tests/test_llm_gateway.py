import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.llm_gateway import LLMGateway, ProviderRecord, RouteRecord


def _provider(
    provider_id: str,
    name: str,
    *,
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
        extra_config={},
        context_window=None,
        max_tokens=None,
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


def test_resolve_candidates_falls_back_to_parent_route_for_dynamic_step():
    minimax = _provider("provider-minimax", "MiniMax Shanghai")
    deepseek = _provider("provider-deepseek", "DeepSeek Shanghai", updated_at=90.0)
    gateway = _gateway(
        providers=[minimax, deepseek],
        routes=[_route("route-qa-fix", "qa_fix", minimax.id)],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(step_key="qa_fix.mobile_layout")

    assert candidates[0].provider_id == minimax.id
    assert candidates[0].route_snapshot["requested_step_key"] == "qa_fix.mobile_layout"
    assert candidates[0].route_snapshot["matched_step_key"] == "qa_fix"
    assert candidates[0].route_snapshot["route_match_strategy"] == "parent_step"


def test_resolve_candidates_prefers_exact_route_over_parent_route():
    minimax = _provider("provider-minimax", "MiniMax Shanghai")
    deepseek = _provider("provider-deepseek", "DeepSeek Shanghai", updated_at=90.0)
    gateway = _gateway(
        providers=[minimax, deepseek],
        routes=[
            _route("route-qa-fix", "qa_fix", minimax.id),
            _route("route-qa-fix-mobile", "qa_fix.mobile_layout", deepseek.id, updated_at=101.0),
        ],
    )

    with patch("src.services.llm_gateway.settings.SERVICE_REGION", "cn_shanghai"), patch.object(
        gateway,
        "_ensure_loaded",
        return_value=None,
    ):
        candidates = gateway.resolve_candidates(step_key="qa_fix.mobile_layout")

    assert candidates[0].provider_id == deepseek.id
    assert candidates[0].route_snapshot["requested_step_key"] == "qa_fix.mobile_layout"
    assert candidates[0].route_snapshot["matched_step_key"] == "qa_fix.mobile_layout"
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
        extra_config={"contextWindow": 128000, "maxTokens": 8192},
        context_window=128000,
        max_tokens=8192,
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
        candidates = gateway.resolve_candidates(step_key="code_generate.full")

    assert candidates[0].context_window == 128000
    assert candidates[0].max_tokens == 8192
    assert candidates[0].route_snapshot["context_window"] == 128000
    assert candidates[0].route_snapshot["max_tokens"] == 8192


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
        extra_config={"maxTokens": 512},
        context_window=None,
        max_tokens=512,
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
