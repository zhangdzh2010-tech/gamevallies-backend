"""Credential-free provider transport evidence for diagnostics and retries.

Persists status, truncated/redacted response body, request ids, and route
metadata. Never stores Authorization, cookies, API keys, or JWTs.
"""
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

import httpx

BODY_EXCERPT_LIMIT = 1024
RETRYABLE_PROVIDER_HTTP_STATUSES = frozenset({403, 408, 409, 425, 429})
_HEADER_ALLOWLIST = (
    "x-request-id", "request-id", "x-trace-id", "trace-id", "cf-ray",
    "server", "date", "content-type", "retry-after",
)


def is_retryable_provider_http_status(status: int | None) -> bool:
    if status is None:
        return False
    code = int(status)
    return code in RETRYABLE_PROVIDER_HTTP_STATUSES or code >= 500


def _redact_secret_text(value: str, api_key: str = "") -> str:
    cleaned = str(value or "")
    if api_key:
        cleaned = cleaned.replace(api_key, "[REDACTED]")
    cleaned = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "[REDACTED]", cleaned)
    cleaned = re.sub(r"(?i)(bearer\s+)[\w\-._~+/]+=*", r"\1[REDACTED]", cleaned)
    cleaned = re.sub(r"(?i)(sk-|api[_-]?key[=:]\s*)\S+", r"\1[REDACTED]", cleaned)
    return re.sub(r"[\x00-\x1f\x7f]", " ", cleaned)


def _safe_header_value(value: str, api_key: str = "") -> str:
    return _redact_secret_text(value, api_key)[:256]


def _body_excerpt(body: bytes, api_key: str = "") -> str:
    text = body.decode("utf-8", errors="replace")
    return _redact_secret_text(text, api_key)[:BODY_EXCERPT_LIMIT]


def _request_endpoint(request: httpx.Request) -> str:
    url = request.url
    suffix = next((p for p in ("/chat/completions", "/messages", "/responses")
                   if url.path.endswith(p)), "/[redacted-path]")
    host = url.host
    if ":" in host:
        host = f"[{host}]"
    port = f":{url.port}" if url.port is not None else ""
    return f"{url.scheme}://{host}{port}{suffix}"


def failure_transport_evidence(
    exc: Exception,
    *,
    api_key: str = "",
    model: str | None = None,
    provider_id: str | None = None,
    step_key: str | None = None,
) -> dict[str, Any]:
    if not isinstance(exc, (httpx.HTTPStatusError, httpx.RequestError)):
        return {}
    response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
    try:
        request = exc.request
    except RuntimeError:
        request = None
    evidence: dict[str, Any] = {
        "source": "upstream_http_response" if response is not None else (
            "client_timeout" if isinstance(exc, httpx.TimeoutException) else "client_transport_error"
        ),
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "httpStatus": response.status_code if response is not None else None,
        "retryable": is_retryable_provider_http_status(
            response.status_code if response is not None else None
        ) or response is None,
    }
    if model:
        evidence["model"] = str(model)[:128]
    if provider_id:
        evidence["providerId"] = str(provider_id)[:128]
    if step_key:
        evidence["stepKey"] = str(step_key)[:128]
    if request is not None:
        evidence.update(method=request.method, endpoint=_request_endpoint(request))
    if response is not None:
        headers = {}
        for name in _HEADER_ALLOWLIST:
            value = response.headers.get(name)
            if value:
                headers[name] = _safe_header_value(value, api_key)
        evidence["responseHeaders"] = headers
        try:
            body = response.content
        except httpx.ResponseNotRead:
            evidence["bodyCaptured"] = False
        else:
            evidence.update(
                bodyCaptured=True,
                bodyBytes=len(body),
                bodySha256=hashlib.sha256(body).hexdigest(),
                bodyExcerpt=_body_excerpt(body, api_key),
            )
    return evidence


def collect_transport_evidence(exc: BaseException | None, *, api_key: str = "") -> dict[str, Any]:
    """Walk cause/context and attached snapshots for the first transport evidence."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        attached = getattr(current, "transport_evidence", None)
        if isinstance(attached, dict) and attached:
            return dict(attached)
        snapshot = getattr(current, "route_snapshot", None)
        if isinstance(snapshot, dict):
            nested = snapshot.get("transportEvidence")
            if isinstance(nested, dict) and nested:
                return dict(nested)
        built = failure_transport_evidence(current, api_key=api_key) if isinstance(current, Exception) else {}
        if built:
            return built
        current = current.__cause__ or current.__context__
    return {}


def attach_transport_evidence(exc: BaseException, evidence: dict[str, Any]) -> None:
    if not evidence:
        return
    try:
        setattr(exc, "transport_evidence", dict(evidence))
    except Exception:
        pass
    snapshot = dict(getattr(exc, "route_snapshot", None) or {})
    snapshot["transportEvidence"] = dict(evidence)
    try:
        setattr(exc, "route_snapshot", snapshot)
    except Exception:
        pass


def transport_error_message(evidence: dict, fallback: str) -> str:
    if not evidence:
        return fallback
    if evidence.get("source") == "upstream_http_response":
        parts = [f"Upstream HTTP {evidence['httpStatus']} from {evidence.get('endpoint', 'provider')}"]
        if evidence.get("model"):
            parts.append(f"model={evidence['model']}")
        headers = evidence.get("responseHeaders") or {}
        request_id = headers.get("x-request-id") or headers.get("request-id")
        if request_id:
            parts.append(f"request_id={request_id}")
        excerpt = str(evidence.get("bodyExcerpt") or "").strip()
        if excerpt:
            parts.append(f"body={excerpt[:240]}")
        return " ".join(parts)
    return (
        f"{evidence.get('source', 'client_transport_error')} contacting "
        f"{evidence.get('endpoint', 'provider')} (no HTTP response received)"
    )
