import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.llm_http_evidence import failure_transport_evidence, transport_error_message
from src.services.llm_client import LLMClient, gateway


def http_failure(unread=False):
    request = httpx.Request("POST", "https://user:password@proxy.example/private-key/v1/chat/completions?api_key=query-secret")
    kwargs = {"stream": httpx.ByteStream(b"private body")} if unread else {"content": b"prompt and api-secret"}
    response = httpx.Response(504, request=request, headers={
        "x-request-id": "request-123", "server": "nginx",
        "set-cookie": "session=private", "authorization": "Bearer api-secret",
        "location": "https://example/?token=private", "x-trace-id": "api-secret",
    }, **kwargs)
    return httpx.HTTPStatusError("gateway timeout", request=request, response=response)


def test_http_response_evidence_is_specific_and_credential_free():
    evidence = failure_transport_evidence(http_failure(), api_key="api-secret")
    assert evidence["source"] == "upstream_http_response"
    assert evidence["httpStatus"] == 504
    assert evidence["endpoint"] == "https://proxy.example/chat/completions"
    assert evidence["bodySha256"] == hashlib.sha256(b"prompt and api-secret").hexdigest()
    assert evidence["bodyBytes"] == len(b"prompt and api-secret")
    assert evidence["responseHeaders"]["x-request-id"] == "request-123"
    assert evidence["responseHeaders"]["x-trace-id"] == "[REDACTED]"
    serialized = json.dumps(evidence)
    for secret in ("api-secret", "query-secret", "password", "private-key", "prompt", "set-cookie", "authorization", "location"):
        assert secret not in serialized


@pytest.mark.parametrize("error,source", [(httpx.ReadTimeout, "client_timeout"), (httpx.ConnectError, "client_transport_error")])
def test_client_errors_never_claim_an_http_status(error, source):
    evidence = failure_transport_evidence(error("secret diagnostic"))
    assert evidence["source"] == source
    assert evidence["httpStatus"] is None
    assert "no HTTP response received" in transport_error_message(evidence, "unused")


def test_unread_stream_does_not_mask_original_http_error():
    evidence = failure_transport_evidence(http_failure(unread=True))
    assert evidence["httpStatus"] == 504
    assert evidence["bodyCaptured"] is False
    assert "bodySha256" not in evidence


@pytest.mark.parametrize("streaming", [False, True])
def test_transport_evidence_reaches_call_log_for_both_transports(streaming):
    client = LLMClient()
    route = SimpleNamespace(provider_id="p", provider_name="Proxy", provider_type="openai_compatible",
                            region="test", model="model", config_version=1, route_snapshot={},
                            api_key="api-secret", request_timeout_s=1800, connect_timeout_s=10)
    exc = http_failure(unread=streaming)

    async def fail_stream(**kwargs):
        raise exc
        yield "unreachable"

    async def run():
        kwargs = dict(route=route, messages=[], max_tokens=16, system=None,
                      step_key="code_generate.full", stage="code_generating")
        with patch.object(gateway, "emit_llm_call_log", new=AsyncMock()) as log, \
             patch.object(gateway, "emit_task_activity", new=AsyncMock()), \
             patch.object(client, "_complete_openai_compatible", new=AsyncMock(side_effect=exc)), \
             patch.object(client, "_stream_openai_compatible", new=fail_stream):
            with pytest.raises(httpx.HTTPStatusError) as raised:
                if streaming:
                    async for _ in client._stream_with_route(**kwargs):
                        pass
                else:
                    await client._complete_with_route(**kwargs)
            assert raised.value is exc
            payload = log.await_args.args[0]
            assert payload["httpStatus"] == 504
            assert payload["upstreamRequestId"] == "request-123"
            assert payload["errorBodyExcerpt"] is None
            assert payload["routeSnapshot"]["transportEvidence"]["source"] == "upstream_http_response"
            assert "api-secret" not in json.dumps(payload)
    asyncio.run(run())
