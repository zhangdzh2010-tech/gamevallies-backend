"""Small, credential-free transport evidence; never store response bodies."""
from datetime import datetime, timezone
import hashlib
import re

import httpx


def failure_transport_evidence(exc: Exception, *, api_key: str = "") -> dict:
    if not isinstance(exc, (httpx.HTTPStatusError, httpx.RequestError)):
        return {}
    response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
    try:
        request = exc.request
    except RuntimeError:
        request = None
    evidence = {
        "source": "upstream_http_response" if response is not None else (
            "client_timeout" if isinstance(exc, httpx.TimeoutException) else "client_transport_error"
        ),
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "httpStatus": response.status_code if response is not None else None,
    }
    if request is not None:
        # Drop userinfo, query, fragment and arbitrary path segments (which may
        # themselves contain credentials). Retain known API endpoint suffixes.
        url = request.url
        suffix = next((p for p in ("/chat/completions", "/messages", "/responses")
                       if url.path.endswith(p)), "/[redacted-path]")
        host = url.host
        if ":" in host:
            host = f"[{host}]"
        port = f":{url.port}" if url.port is not None else ""
        evidence.update(method=request.method, endpoint=f"{url.scheme}://{host}{port}{suffix}")
    if response is not None:
        # Strict allowlist: never Authorization, Cookie, Set-Cookie or Location.
        headers = {}
        for name in ("x-request-id", "request-id", "x-trace-id", "trace-id", "cf-ray",
                     "server", "date", "content-type", "retry-after"):
            value = response.headers.get(name)
            if value:
                if api_key:
                    value = value.replace(api_key, "[REDACTED]")
                value = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "[REDACTED]", value)
                headers[name] = re.sub(r"[\x00-\x1f\x7f]", " ", value)[:256]
        evidence["responseHeaders"] = headers
        try:
            body = response.content
        except httpx.ResponseNotRead:
            # Do not consume a streaming response merely to produce diagnostics.
            evidence["bodyCaptured"] = False
        else:
            evidence.update(bodyCaptured=True, bodyBytes=len(body),
                            bodySha256=hashlib.sha256(body).hexdigest())
    return evidence


def transport_error_message(evidence: dict, fallback: str) -> str:
    if not evidence:
        return fallback
    if evidence["source"] == "upstream_http_response":
        return f"Upstream HTTP {evidence['httpStatus']} from {evidence.get('endpoint', 'provider')}"
    return f"{evidence['source']} contacting {evidence.get('endpoint', 'provider')} (no HTTP response received)"
