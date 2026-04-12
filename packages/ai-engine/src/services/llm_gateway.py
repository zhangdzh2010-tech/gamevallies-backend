"""Database-backed LLM gateway with per-step routing and request-scoped context."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import unquote

import httpx
import pymysql

from ..api.models import ProviderCatalogPreviewRequest, ProviderCatalogPreviewResponse, ProviderTestChatRequest, ProviderTestChatResponse
from ..config.settings import settings
from ..config.timeout_store import get_float as get_timeout_float, get_int as get_timeout_int

logger = logging.getLogger(__name__)

_request_context_var: ContextVar[dict[str, Optional[str]]] = ContextVar(
    "llm_request_context",
    default={"game_id": None, "user_id": None, "task_id": None},
)


@contextmanager
def llm_request_context(
    *,
    game_id: Optional[str] = None,
    user_id: Optional[str] = None,
    task_id: Optional[str] = None,
):
    token = _request_context_var.set({
        "game_id": game_id,
        "user_id": user_id,
        "task_id": task_id,
    })
    try:
        yield
    finally:
        _request_context_var.reset(token)


def get_request_context() -> dict[str, Optional[str]]:
    return _request_context_var.get()


def _parse_database_url(url: str) -> dict[str, Any]:
    base = url.split("?")[0]
    prefix = "mysql://"
    if not base.startswith(prefix):
        raise ValueError(f"Cannot parse DATABASE_URL (expected mysql:// prefix): {url!r}")
    rest = base[len(prefix):]

    match = re.search(r"@([^@]+):(\d+)/(.+)$", rest)
    if not match:
        raise ValueError(f"Cannot parse DATABASE_URL: {url!r}")

    host = match.group(1)
    port = int(match.group(2))
    database = match.group(3)

    creds = rest[: match.start()]
    colon_idx = creds.find(":")
    if colon_idx == -1:
        raise ValueError(f"Cannot parse DATABASE_URL (missing user:password): {url!r}")

    user = unquote(creds[:colon_idx])
    password = unquote(creds[colon_idx + 1 :])

    return {
      "host": host,
      "port": port,
      "database": database,
      "user": user,
      "password": password,
    }


def _loads_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _coerce_optional_positive_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return None
    return parsed if parsed > 0 else None


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _extract_provider_numeric_cap(extra_config: Any, *keys: str) -> Optional[int]:
    if not isinstance(extra_config, dict):
        return None
    for key in keys:
        if key in extra_config:
            normalized = _coerce_optional_positive_int(extra_config.get(key))
            if normalized is not None:
                return normalized
    return None


def _extract_provider_bool(extra_config: Any, *keys: str) -> Optional[bool]:
    if not isinstance(extra_config, dict):
        return None
    for key in keys:
        if key in extra_config:
            normalized = _coerce_optional_bool(extra_config.get(key))
            if normalized is not None:
                return normalized
    return None


def _extract_provider_string(extra_config: Any, *keys: str) -> Optional[str]:
    if not isinstance(extra_config, dict):
        return None
    for key in keys:
        raw = extra_config.get(key)
        if raw is None:
            continue
        normalized = str(raw).strip()
        if normalized:
            return normalized
    return None


def _is_anthropic_protocol_mismatch_response(response: httpx.Response) -> bool:
    if response.status_code != 400:
        return False
    body = (response.text or "").casefold()
    return (
        "anthropic-compatible" in body
        and "/v1/chat/completions" in body
    )


def _build_anthropic_base_url(base_url: Optional[str]) -> Optional[str]:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return None

    parsed = httpx.URL(normalized)
    path = parsed.path.rstrip("/")

    if path.endswith("/messages"):
        path = path[: -len("/messages")]
    if path.endswith("/v1"):
        path = path[: -len("/v1")]

    normalized_url = str(parsed.copy_with(path=path or "/")).rstrip("/")
    return normalized_url or None


def _build_openai_chat_endpoint(base_url: Optional[str]) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("Provider base URL is required")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/chat/completions"


def _extract_openai_choice_text(choice: Any) -> str:
    if not isinstance(choice, dict):
        return ""
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    text_parts.append(item.strip())
                elif isinstance(item, dict):
                    text_value = item.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        text_parts.append(text_value.strip())
            return "\n".join(text_parts).strip()
    text = choice.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ProviderRecord:
    id: str
    name: str
    provider_type: str
    region: str
    base_url: str
    api_key: str
    model: str
    fast_model: Optional[str]
    request_timeout_s: int
    connect_timeout_s: int
    enabled: bool
    priority: int
    description: Optional[str]
    extra_config: dict[str, Any]
    context_window: Optional[int]
    max_tokens: Optional[int]
    tokenizer_family: Optional[str]
    strict_admission: bool
    safety_margin_tokens: Optional[int]
    capability_flags: dict[str, Any]
    updated_at: float


def _provider_has_capability(provider: ProviderRecord, capability: str) -> bool:
    """Check if provider has a required capability.

    Empty or missing capability_flags means all capabilities are assumed present
    (backward compatible).
    """
    flags = provider.capability_flags
    if not flags:
        return True
    # Check unsafe_for_steps list
    unsafe_steps = flags.get("unsafe_for_steps", [])
    if capability in unsafe_steps:
        return False
    return flags.get(capability, True)


STEP_REQUIRED_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "code_generate.full": ("supports_full_html_rewrite",),
    "iterate.mechanic_change": ("supports_patch_generation",),
    "iterate.element_change": ("supports_patch_generation",),
    "iterate.param_adjust": ("supports_patch_generation",),
    "qa_fix.syntax_structural": ("supports_full_html_rewrite",),
    "intent_parse": ("supports_dialogue",),
    "dialogue.reply": ("supports_dialogue",),
    "dialogue.slot_extract": ("supports_dialogue",),
    "iterate.classify": ("supports_dialogue",),
}


@dataclass
class RouteRecord:
    id: str
    step_key: str
    region: str
    provider_id: str
    fallback_provider_ids: list[str]
    model_override: Optional[str]
    fast_model_override: Optional[str]
    request_timeout_s: Optional[int]
    connect_timeout_s: Optional[int]
    enabled: bool
    updated_at: float


@dataclass
class ResolvedRoute:
    provider_id: Optional[str]
    provider_name: str
    provider_type: str
    region: str
    base_url: str
    api_key: str
    model: str
    fast_model: Optional[str]
    request_timeout_s: int
    connect_timeout_s: int
    context_window: Optional[int]
    max_tokens: Optional[int]
    tokenizer_family: Optional[str]
    strict_admission: bool
    safety_margin_tokens: Optional[int]
    step_key: str
    config_version: int
    route_snapshot: dict[str, Any]


class LLMGateway:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderRecord] = {}
        self._routes: list[RouteRecord] = []
        self._config_version = 0
        self._loaded_at = 0.0

    def _connect(self):
        params = _parse_database_url(settings.DATABASE_URL)
        return pymysql.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=params["database"],
            charset="utf8mb4",
            connect_timeout=get_timeout_int(
                "timeout.ai_engine.gateway_db_connect_s",
                5,
                min_value=1,
            ),
            read_timeout=get_timeout_int(
                "timeout.ai_engine.gateway_db_read_s",
                5,
                min_value=1,
            ),
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _load_from_db(self) -> tuple[dict[str, ProviderRecord], list[RouteRecord], int]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      id, name, provider_type, region, base_url, api_key, model, fast_model,
                      request_timeout_s, connect_timeout_s, enabled, priority, description,
                      extra_config, capability_flags, updated_at
                    FROM llm_gateway_providers
                    WHERE enabled = 1
                    ORDER BY priority ASC, updated_at DESC
                    """
                )
                provider_rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT
                      id, step_key, region, provider_id, fallback_provider_ids,
                      model_override, fast_model_override, request_timeout_s,
                      connect_timeout_s, enabled, updated_at
                    FROM llm_step_routes
                    WHERE enabled = 1
                    ORDER BY updated_at DESC
                    """
                )
                route_rows = cur.fetchall()
        finally:
            conn.close()

        providers: dict[str, ProviderRecord] = {}
        version_candidates: list[int] = []
        for row in provider_rows:
            updated_at = row.get("updated_at")
            updated_ts = int(updated_at.timestamp()) if updated_at else int(time.time())
            version_candidates.append(updated_ts)
            extra_config = _loads_json(row.get("extra_config"), {})
            context_window = _extract_provider_numeric_cap(extra_config, "contextWindow", "context_window")
            max_tokens = _extract_provider_numeric_cap(extra_config, "maxTokens", "max_tokens")
            strict_admission = _extract_provider_bool(extra_config, "strictAdmission", "strict_admission")
            if strict_admission is None:
                strict_admission = bool(context_window and max_tokens)
            # Parse capability_flags from dedicated column, falling back to extra_config
            raw_capability_flags = _loads_json(row.get("capability_flags"), None)
            if raw_capability_flags is None:
                raw_capability_flags = extra_config.get("capability_flags", {})
            if not isinstance(raw_capability_flags, dict):
                raw_capability_flags = {}
            providers[row["id"]] = ProviderRecord(
                id=row["id"],
                name=row["name"],
                provider_type=row["provider_type"],
                region=row.get("region") or "cn_shanghai",
                base_url=row["base_url"],
                api_key=row["api_key"],
                model=row["model"],
                fast_model=row.get("fast_model"),
                request_timeout_s=int(row.get("request_timeout_s") or 600),
                connect_timeout_s=int(row.get("connect_timeout_s") or 15),
                enabled=bool(row.get("enabled", True)),
                priority=int(row.get("priority") or 100),
                description=row.get("description"),
                extra_config=extra_config,
                context_window=context_window,
                max_tokens=max_tokens,
                tokenizer_family=_extract_provider_string(extra_config, "tokenizerFamily", "tokenizer_family"),
                strict_admission=strict_admission,
                safety_margin_tokens=_extract_provider_numeric_cap(extra_config, "safetyMarginTokens", "safety_margin_tokens"),
                capability_flags=raw_capability_flags,
                updated_at=float(updated_ts),
            )

        routes: list[RouteRecord] = []
        for row in route_rows:
            updated_at = row.get("updated_at")
            updated_ts = int(updated_at.timestamp()) if updated_at else int(time.time())
            version_candidates.append(updated_ts)
            routes.append(RouteRecord(
                id=row["id"],
                step_key=row["step_key"],
                region=row.get("region") or "cn_shanghai",
                provider_id=row["provider_id"],
                fallback_provider_ids=_loads_json(row.get("fallback_provider_ids"), []),
                model_override=row.get("model_override"),
                fast_model_override=row.get("fast_model_override"),
                request_timeout_s=int(row["request_timeout_s"]) if row.get("request_timeout_s") is not None else None,
                connect_timeout_s=int(row["connect_timeout_s"]) if row.get("connect_timeout_s") is not None else None,
                enabled=bool(row.get("enabled", True)),
                updated_at=float(updated_ts),
            ))

        return providers, routes, max(version_candidates or [0])

    def refresh(self, *, raise_on_error: bool = False) -> int:
        try:
            providers, routes, config_version = self._load_from_db()
            self._providers = providers
            self._routes = routes
            self._config_version = config_version
            self._loaded_at = time.time()
            logger.info(
                "llm_gateway: loaded %d providers and %d routes",
                len(providers),
                len(routes),
            )
            return len(providers)
        except Exception:
            logger.warning("llm_gateway: failed to load providers from DB, using env fallback", exc_info=True)
            self._loaded_at = time.time()
            if raise_on_error:
                raise
            return len(self._providers)

    def _ensure_loaded(self) -> None:
        ttl = get_timeout_int(
            "timeout.ai_engine.llm_gateway_cache_ttl_s",
            10,
            min_value=1,
        )
        if not self._loaded_at or (time.time() - self._loaded_at) > ttl:
            self.refresh()

    def _fallback_route(
        self,
        *,
        step_key: str,
        prefer_fast: bool = False,
        model_override: Optional[str] = None,
    ) -> ResolvedRoute:
        provider_type = "openai_compatible" if settings.LLM_API_KEY and settings.LLM_BASE_URL else "anthropic"
        base_url = settings.LLM_BASE_URL if provider_type == "openai_compatible" else ""
        api_key = settings.LLM_API_KEY if provider_type == "openai_compatible" else settings.ANTHROPIC_API_KEY
        model = model_override or (
            settings.LLM_FAST_MODEL if provider_type == "openai_compatible" and prefer_fast and settings.LLM_FAST_MODEL
            else settings.LLM_MODEL if provider_type == "openai_compatible"
            else settings.CLAUDE_FAST_MODEL if prefer_fast
            else settings.CLAUDE_MODEL
        )
        fast_model = settings.LLM_FAST_MODEL if provider_type == "openai_compatible" else settings.CLAUDE_FAST_MODEL

        return ResolvedRoute(
            provider_id=None,
            provider_name=f"env-{provider_type}",
            provider_type=provider_type,
            region=settings.SERVICE_REGION or "cn_shanghai",
            base_url=base_url,
            api_key=api_key,
            model=model,
            fast_model=fast_model,
            request_timeout_s=get_timeout_int(
                "timeout.pipeline.default_s",
                1200,
                min_value=30,
            ),
            connect_timeout_s=get_timeout_int(
                "timeout.ai_engine.llm_fallback_connect_s",
                15,
                min_value=1,
            ),
            context_window=None,
            max_tokens=None,
            tokenizer_family=None,
            strict_admission=False,
            safety_margin_tokens=None,
            step_key=step_key,
            config_version=self._config_version,
            route_snapshot={
                "step_key": step_key,
                "source": "env",
                "region": settings.SERVICE_REGION or "cn_shanghai",
                "context_window": None,
                "max_tokens": None,
                "strict_admission": False,
            },
        )

    def _route_lookup_candidates(self, *, step_key: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = [(step_key, "exact")]
        parent_step_key = step_key
        while "." in parent_step_key:
            parent_step_key = parent_step_key.rsplit(".", 1)[0]
            candidates.append((parent_step_key, "parent_step"))
        return candidates

    def _find_route_for_step(
        self,
        *,
        step_key: str,
        region: str,
    ) -> tuple[Optional[RouteRecord], Optional[str], Optional[str]]:
        for candidate_key, strategy in self._route_lookup_candidates(step_key=step_key):
            route = next(
                (
                    candidate
                    for candidate in self._routes
                    if candidate.step_key == candidate_key and candidate.region == region
                ),
                None,
            )
            if route:
                return route, candidate_key, strategy
        return None, None, None

    def _ordered_provider_candidates(
        self,
        *,
        route: Optional[RouteRecord],
        service_region: str,
        step_key: str = "",
        excluded_provider_ids: Optional[list[str]] = None,
    ) -> tuple[list[ProviderRecord], list[str]]:
        ordered: list[ProviderRecord] = []
        seen: set[str] = set()
        excluded = {str(provider_id).strip() for provider_id in (excluded_provider_ids or []) if str(provider_id).strip()}

        def add_provider(provider_id: Optional[str]) -> None:
            if not provider_id or provider_id in seen or provider_id in excluded:
                return
            provider = self._providers.get(provider_id)
            if provider is None:
                return
            seen.add(provider_id)
            ordered.append(provider)

        if route:
            # When a route exists, only use explicitly configured providers.
            # This prevents unrelated enabled providers from being called
            # as implicit fallbacks.
            add_provider(route.provider_id)
            for fallback_id in route.fallback_provider_ids:
                add_provider(fallback_id)
        else:
            # No route matched — fall back to all enabled providers,
            # preferring same-region first.
            for provider in self._providers.values():
                if provider.region == service_region:
                    add_provider(provider.id)

            for provider in self._providers.values():
                add_provider(provider.id)

        # Filter by capability requirements
        required_caps = self._resolve_required_capabilities(step_key)
        if required_caps:
            filtered: list[ProviderRecord] = []
            rejections: list[str] = []
            for provider in ordered:
                missing = [cap for cap in required_caps if not _provider_has_capability(provider, cap)]
                if missing:
                    rejections.append(f"{provider.name}:{','.join(missing)}")
                    logger.debug(
                        "Provider %s (%s) excluded from step %s: missing capabilities %s",
                        provider.name, provider.id, step_key, missing,
                    )
                else:
                    filtered.append(provider)
            return filtered, rejections
        return ordered, []

    @staticmethod
    def _resolve_required_capabilities(step_key: str) -> tuple[str, ...]:
        """Look up required capabilities for a step, including parent fallback."""
        caps = STEP_REQUIRED_CAPABILITIES.get(step_key)
        if caps:
            return caps
        parent = step_key
        while "." in parent:
            parent = parent.rsplit(".", 1)[0]
            caps = STEP_REQUIRED_CAPABILITIES.get(parent)
            if caps:
                return caps
        return ()

    @staticmethod
    def _provider_meets_output_floor(
        provider: ProviderRecord,
        required_output_tokens: Optional[int],
        *,
        allow_unknown: bool,
    ) -> bool:
        floor = _coerce_optional_positive_int(required_output_tokens)
        if floor is None:
            return True
        provider_cap = _coerce_optional_positive_int(provider.max_tokens)
        if provider_cap is None:
            return allow_unknown
        return provider_cap >= floor

    def _prioritize_provider_candidates_for_output_floor(
        self,
        providers: list[ProviderRecord],
        *,
        required_output_tokens: Optional[int],
    ) -> list[ProviderRecord]:
        floor = _coerce_optional_positive_int(required_output_tokens)
        if floor is None:
            return providers

        capable: list[ProviderRecord] = []
        uncertain: list[ProviderRecord] = []
        insufficient: list[ProviderRecord] = []
        for provider in providers:
            provider_cap = _coerce_optional_positive_int(provider.max_tokens)
            if provider_cap is None:
                uncertain.append(provider)
            elif provider_cap >= floor:
                capable.append(provider)
            else:
                insufficient.append(provider)
        if capable:
            return capable + uncertain + insufficient
        return uncertain + insufficient

    def _build_resolved_route(
        self,
        *,
        provider: ProviderRecord,
        route: Optional[RouteRecord],
        step_key: str,
        matched_step_key: Optional[str] = None,
        route_match_strategy: Optional[str] = None,
        prefer_fast: bool = False,
        model_override: Optional[str] = None,
        explicit_fallback_only: Optional[bool] = None,
        extra_route_snapshot: Optional[dict[str, Any]] = None,
    ) -> ResolvedRoute:
        if model_override:
            resolved_model = model_override
        elif prefer_fast and route and route.fast_model_override:
            resolved_model = route.fast_model_override
        elif route and route.model_override:
            resolved_model = route.model_override
        elif prefer_fast and provider.fast_model:
            resolved_model = provider.fast_model
        else:
            resolved_model = provider.model

        return ResolvedRoute(
            provider_id=provider.id,
            provider_name=provider.name,
            provider_type=provider.provider_type,
            region=provider.region,
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=resolved_model,
            fast_model=provider.fast_model,
            request_timeout_s=int(route.request_timeout_s if route and route.request_timeout_s is not None else provider.request_timeout_s),
            connect_timeout_s=int(route.connect_timeout_s if route and route.connect_timeout_s is not None else provider.connect_timeout_s),
            context_window=provider.context_window,
            max_tokens=provider.max_tokens,
            tokenizer_family=provider.tokenizer_family,
            strict_admission=provider.strict_admission,
            safety_margin_tokens=provider.safety_margin_tokens,
            step_key=step_key,
            config_version=self._config_version,
            route_snapshot={
                "step_key": step_key,
                "region": provider.region,
                "provider_id": provider.id,
                "provider_name": provider.name,
                "provider_type": provider.provider_type,
                "route_id": route.id if route else None,
                "prefer_fast": prefer_fast,
                "fallback_provider_ids": list(route.fallback_provider_ids) if route else [],
                "requested_step_key": step_key,
                "matched_step_key": matched_step_key or step_key,
                "route_match_strategy": route_match_strategy or ("exact" if route else "none"),
                "explicit_fallback_only": bool(route is not None) if explicit_fallback_only is None else bool(explicit_fallback_only),
                "context_window": provider.context_window,
                "max_tokens": provider.max_tokens,
                "tokenizer_family": provider.tokenizer_family,
                "strict_admission": provider.strict_admission,
                "safety_margin_tokens": provider.safety_margin_tokens,
                "provider_capability_flags": {
                    k: v for k, v in provider.capability_flags.items()
                    if isinstance(v, (bool, str, int, float))
                } if provider.capability_flags else {},
                **(extra_route_snapshot or {}),
            },
        )

    def has_enabled_provider(self) -> bool:
        self._ensure_loaded()
        if self._providers:
            return True
        return bool(
            (settings.LLM_API_KEY and settings.LLM_BASE_URL)
            or settings.ANTHROPIC_API_KEY
        )

    def resolve(
        self,
        *,
        step_key: str,
        prefer_fast: bool = False,
        model_override: Optional[str] = None,
        excluded_provider_ids: Optional[list[str]] = None,
    ) -> ResolvedRoute:
        return self.resolve_candidates(
            step_key=step_key,
            prefer_fast=prefer_fast,
            model_override=model_override,
            excluded_provider_ids=excluded_provider_ids,
        )[0]

    def resolve_candidates(
        self,
        *,
        step_key: str,
        prefer_fast: bool = False,
        model_override: Optional[str] = None,
        required_output_tokens: Optional[int] = None,
        excluded_provider_ids: Optional[list[str]] = None,
    ) -> list[ResolvedRoute]:
        self._ensure_loaded()
        if not self._providers:
            return [self._fallback_route(step_key=step_key, prefer_fast=prefer_fast, model_override=model_override)]

        service_region = (settings.SERVICE_REGION or "cn_shanghai").strip() or "cn_shanghai"
        route, matched_step_key, route_match_strategy = self._find_route_for_step(
            step_key=step_key,
            region=service_region,
        )
        providers, capability_rejections = self._ordered_provider_candidates(
            route=route,
            service_region=service_region,
            step_key=step_key,
            excluded_provider_ids=excluded_provider_ids,
        )
        base_provider_ids = {provider.id for provider in providers}
        providers = self._prioritize_provider_candidates_for_output_floor(
            providers,
            required_output_tokens=required_output_tokens,
        )
        if not providers:
            return [self._fallback_route(step_key=step_key, prefer_fast=prefer_fast, model_override=model_override)]

        return [
            self._build_resolved_route(
                provider=provider,
                route=route,
                step_key=step_key,
                matched_step_key=matched_step_key,
                route_match_strategy=route_match_strategy,
                prefer_fast=prefer_fast,
                model_override=model_override,
                explicit_fallback_only=(route is not None and provider.id in base_provider_ids),
                extra_route_snapshot={
                    "implicit_provider_failover": False,
                    "required_output_tokens": _coerce_optional_positive_int(required_output_tokens),
                    "capability_rejections": capability_rejections,
                },
            )
            for provider in providers
        ]

    async def emit_llm_call_log(self, payload: dict[str, Any]) -> None:
        base_url = settings.GAME_SERVICE_UPSTREAM_URL.rstrip("/")
        context = get_request_context()
        if not base_url or not context.get("game_id") or not context.get("user_id"):
            return

        body = {
            "taskId": context.get("task_id"),
            "gameId": context.get("game_id"),
            "userId": context.get("user_id"),
            **payload,
        }
        headers = {}
        if settings.ADMIN_TOKEN:
            headers["x-admin-token"] = settings.ADMIN_TOKEN

        try:
            async with httpx.AsyncClient(
                timeout=get_timeout_float(
                    "timeout.ai_engine.llm_call_log_relay_s",
                    3.0,
                    min_value=0.1,
                )
            ) as client:
                await client.post(
                    f"{base_url}/api/v1/internal/generation/llm-call-log",
                    json=body,
                    headers=headers,
                )
        except Exception as exc:
            logger.debug("Failed to relay llm call log to game-service: %s", exc)

    async def emit_task_activity(self, payload: dict[str, Any]) -> None:
        base_url = settings.GAME_SERVICE_UPSTREAM_URL.rstrip("/")
        context = get_request_context()
        if not base_url or not context.get("game_id") or not context.get("user_id"):
            return

        body = {
            "taskId": context.get("task_id"),
            "gameId": context.get("game_id"),
            "userId": context.get("user_id"),
            **payload,
        }
        headers = {}
        if settings.ADMIN_TOKEN:
            headers["x-admin-token"] = settings.ADMIN_TOKEN

        try:
            async with httpx.AsyncClient(
                timeout=get_timeout_float(
                    "timeout.ai_engine.task_activity_relay_s",
                    3.0,
                    min_value=0.1,
                )
            ) as client:
                await client.post(
                    f"{base_url}/api/v1/internal/generation/task-activity",
                    json=body,
                    headers=headers,
                )
        except Exception as exc:
            logger.debug("Failed to relay task activity to game-service: %s", exc)

    async def verify_provider_capabilities(
        self,
        provider_id: str,
        *,
        timeout_s: int = 30,
    ) -> dict[str, Any]:
        """Run capability verification tests on a provider."""
        self._ensure_loaded()
        provider = self._providers.get(provider_id)
        if not provider:
            return {"provider_id": provider_id, "passed": False, "error": "provider_not_found"}

        from .llm_client import LLMClient
        client = LLMClient()
        results: dict[str, Any] = {
            "provider_id": provider_id,
            "provider_name": provider.name,
            "tests": {},
            "verified_at": _utc_now_iso(),
        }
        passed_all = True

        # Test 1: Basic reachability (PONG)
        try:
            route = self._build_resolved_route(
                provider=provider, route=None, step_key="verification.pong",
                prefer_fast=True,
            )
            text = await asyncio.wait_for(
                client._complete_single(
                    route=route,
                    messages=[{"role": "user", "content": "Reply with PONG"}],
                    max_tokens=32,
                    system=None,
                ),
                timeout=timeout_s,
            )
            results["tests"]["reachability"] = {"passed": bool(text and "PONG" in text.upper()), "output_preview": (text or "")[:100]}
        except Exception as exc:
            results["tests"]["reachability"] = {"passed": False, "error": str(exc)[:200]}
            passed_all = False

        # Test 2: Large output (1K+ tokens)
        try:
            route = self._build_resolved_route(
                provider=provider, route=None, step_key="verification.large_output",
            )
            prompt = "Write a detailed step-by-step guide with at least 20 numbered steps on how to build a simple web page with HTML and CSS. Include code examples for each step. Be very detailed and thorough."
            text = await asyncio.wait_for(
                client._complete_single(
                    route=route,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096,
                    system="You are a helpful coding tutor. Always provide complete, detailed responses.",
                ),
                timeout=timeout_s * 2,
            )
            output_len = len(text or "")
            results["tests"]["large_output"] = {
                "passed": output_len >= 1000,
                "output_chars": output_len,
            }
            if output_len < 1000:
                passed_all = False
        except Exception as exc:
            results["tests"]["large_output"] = {"passed": False, "error": str(exc)[:200]}
            passed_all = False

        # Test 3: Structured JSON output
        try:
            route = self._build_resolved_route(
                provider=provider, route=None, step_key="verification.structured_json",
                prefer_fast=True,
            )
            prompt = 'Return ONLY valid JSON with this exact structure: {"patches":[{"section":"SCRIPT","operation":"replace_section","content":"console.log(1)"}]}'
            text = await asyncio.wait_for(
                client._complete_single(
                    route=route,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=256,
                    system="You are a JSON-only response bot. Return ONLY valid JSON, no markdown, no explanation.",
                ),
                timeout=timeout_s,
            )
            import json as json_mod
            try:
                parsed = json_mod.loads(text.strip().strip("`").strip())
                has_patches = "patches" in parsed and isinstance(parsed["patches"], list)
                results["tests"]["structured_json"] = {"passed": has_patches}
            except Exception:
                results["tests"]["structured_json"] = {"passed": False, "output_preview": (text or "")[:200]}
                passed_all = False
        except Exception as exc:
            results["tests"]["structured_json"] = {"passed": False, "error": str(exc)[:200]}
            passed_all = False

        results["passed"] = passed_all

        # Persist verification status to capability_flags via DB update
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    # Update capability_flags JSON to include verified status
                    cur.execute(
                        """
                        UPDATE llm_gateway_providers
                        SET extra_config = JSON_SET(
                            COALESCE(extra_config, '{}'),
                            '$.capability_flags.verified', %s,
                            '$.capability_flags.verified_at', %s
                        )
                        WHERE id = %s
                        """,
                        (passed_all, results["verified_at"], provider_id),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Failed to persist verification status for %s: %s", provider_id, exc)

        return results

    def _resolved_route_for_provider(
        self,
        provider: ProviderRecord,
        *,
        prefer_fast: bool = False,
        model_override: Optional[str] = None,
    ) -> ResolvedRoute:
        resolved_model = model_override or (
            provider.fast_model if prefer_fast and provider.fast_model else provider.model
        )
        return ResolvedRoute(
            provider_id=provider.id,
            provider_name=provider.name,
            provider_type=provider.provider_type,
            region=provider.region,
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=resolved_model,
            fast_model=provider.fast_model,
            request_timeout_s=provider.request_timeout_s,
            connect_timeout_s=provider.connect_timeout_s,
            context_window=provider.context_window,
            max_tokens=provider.max_tokens,
            tokenizer_family=provider.tokenizer_family,
            strict_admission=provider.strict_admission,
            safety_margin_tokens=provider.safety_margin_tokens,
            step_key="admin.test",
            config_version=self._config_version,
            route_snapshot={
                "provider_id": provider.id,
                "provider_name": provider.name,
                "context_window": provider.context_window,
                "max_tokens": provider.max_tokens,
                "tokenizer_family": provider.tokenizer_family,
                "strict_admission": provider.strict_admission,
                "safety_margin_tokens": provider.safety_margin_tokens,
            },
        )

    async def _invoke_test_completion(
        self,
        *,
        route: ResolvedRoute,
        messages: list[dict[str, str]],
        max_tokens: int,
        system: Optional[str] = None,
    ) -> tuple[str, Optional[int], str]:
        effective_max_tokens = max(1, min(max_tokens, route.max_tokens)) if route.max_tokens else max_tokens
        if route.provider_type == "anthropic":
            from anthropic import Anthropic

            client = Anthropic(api_key=route.api_key, base_url=_build_anthropic_base_url(route.base_url))
            kwargs: dict[str, Any] = {
                "model": route.model,
                "max_tokens": effective_max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            response = client.messages.create(**kwargs)
            text_parts: list[str] = []
            for block in response.content:
                text = getattr(block, "text", "")
                if text:
                    text_parts.append(text)
            return "\n".join(text_parts).strip(), 200, _build_anthropic_base_url(route.base_url) or route.base_url

        payload_messages: list[dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)
        payload = {
            "model": route.model,
            "messages": payload_messages,
            "max_tokens": effective_max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {route.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = _build_openai_chat_endpoint(route.base_url)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(route.request_timeout_s, connect=route.connect_timeout_s)
        ) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            http_status = response.status_code
            if _is_anthropic_protocol_mismatch_response(response):
                from anthropic import Anthropic

                client = Anthropic(api_key=route.api_key, base_url=_build_anthropic_base_url(route.base_url))
                kwargs = {
                    "model": route.model,
                    "max_tokens": effective_max_tokens,
                    "messages": messages,
                }
                if system:
                    kwargs["system"] = system
                anth_response = client.messages.create(**kwargs)
                text_parts: list[str] = []
                for block in anth_response.content:
                    text = getattr(block, "text", "")
                    if text:
                        text_parts.append(text)
                return "\n".join(text_parts).strip(), 200, _build_anthropic_base_url(route.base_url) or route.base_url

            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0] if isinstance(data, dict) else {}
            return _extract_openai_choice_text(choice), http_status, endpoint

    def _persist_test_record(
        self,
        *,
        provider: ProviderRecord,
        resolved_endpoint: str,
        model: str,
        latency_ms: int,
        success: bool,
        http_status: Optional[int],
        error_message: Optional[str],
    ) -> None:
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO llm_gateway_test_records (
                          id, provider_id, success, region, resolved_endpoint, model,
                          latency_ms, http_status, error_message, tested_at
                        ) VALUES (
                          REPLACE(UUID(), '-', ''), %s, %s, %s, %s, %s,
                          %s, %s, %s, NOW(3)
                        )
                        """,
                        (
                            provider.id,
                            1 if success else 0,
                            provider.region,
                            resolved_endpoint,
                            model,
                            latency_ms,
                            http_status,
                            error_message,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("Failed to persist llm gateway test record", exc_info=True)

    async def preview_model_catalog(
        self,
        request: ProviderCatalogPreviewRequest,
    ) -> ProviderCatalogPreviewResponse:
        vendor_preset = (request.vendor_preset or "generic").strip() or "generic"
        catalog_api_url = (request.catalog_api_url or "").strip()
        if not catalog_api_url:
            if vendor_preset == "modelverse":
                catalog_api_url = "https://api.modelverse.cn/v1/models"
            else:
                base_url = (request.base_url or "").strip().rstrip("/")
                if base_url.endswith("/chat/completions"):
                    base_url = base_url[: -len("/chat/completions")]
                if not base_url:
                    raise ValueError("catalogApiUrl is required when no vendor preset default is available")
                catalog_api_url = f"{base_url}/models"

        headers = {
            "User-Agent": "GameVallies-Admin/1.0",
        }
        auth_mode = request.catalog_auth_mode if request.catalog_auth_mode == "bearer_token" else "inherit_provider"
        auth_token = (
            (request.catalog_api_key or "").strip()
            if auth_mode == "bearer_token"
            else (request.api_key or "").strip()
        )
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0)
        ) as client:
            response = await client.get(catalog_api_url, headers=headers)
            response.raise_for_status()
            payload = response.json()

        if isinstance(payload, list):
            raw_models = payload
        elif isinstance(payload, dict):
            raw_models = payload.get("data") or payload.get("models") or payload.get("result") or []
        else:
            raw_models = []

        models: list[dict[str, Any]] = []
        if isinstance(raw_models, list):
            for item in raw_models:
                if isinstance(item, str):
                    models.append({
                        "id": item,
                        "label": item,
                        "owned_by": None,
                        "created": None,
                    })
                    continue
                if not isinstance(item, dict):
                    continue
                model_id = item.get("id") or item.get("name") or item.get("model")
                if not model_id:
                    continue
                label = item.get("label") or item.get("display_name") or model_id
                models.append({
                    "id": str(model_id),
                    "label": str(label),
                    "owned_by": item.get("owned_by"),
                    "created": item.get("created"),
                })

        models.sort(key=lambda item: item["id"])
        return ProviderCatalogPreviewResponse(
            models=models,
            fetched_at=_utc_now_iso(),
            resolved_catalog_api_url=catalog_api_url,
            vendor_preset=vendor_preset,
        )

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        self.refresh(raise_on_error=True)
        provider = self._providers.get(provider_id)
        if not provider:
            raise ValueError("Provider not found")

        route = self._resolved_route_for_provider(provider, prefer_fast=True)
        start = time.time()
        success = False
        http_status = None
        error_message = None
        output = ""
        resolved_endpoint = route.base_url
        try:
            output, http_status, resolved_endpoint = await self._invoke_test_completion(
                route=route,
                messages=[{"role": "user", "content": "Reply with PONG"}],
                max_tokens=32,
            )
            success = True
        except Exception as exc:
            error_message = str(exc)
        latency_ms = int((time.time() - start) * 1000)

        self._persist_test_record(
            provider=provider,
            resolved_endpoint=resolved_endpoint,
            model=route.model,
            latency_ms=latency_ms,
            success=success,
            http_status=http_status,
            error_message=error_message,
        )

        return {
            "providerId": provider.id,
            "providerName": provider.name,
            "providerType": provider.provider_type,
            "region": provider.region,
            "resolvedEndpoint": resolved_endpoint,
            "model": route.model,
            "latencyMs": latency_ms,
            "httpStatus": http_status,
            "success": success,
            "errorMessage": error_message,
            "outputPreview": output[:200],
            "testedAt": _utc_now_iso(),
        }

    async def test_provider_chat(
        self,
        provider_id: str,
        request: ProviderTestChatRequest,
    ) -> ProviderTestChatResponse:
        self.refresh(raise_on_error=True)
        provider = self._providers.get(provider_id)
        if not provider:
            raise ValueError("Provider not found")
        if not request.messages:
            raise ValueError("messages are required")

        route = self._resolved_route_for_provider(
            provider,
            prefer_fast=request.use_fast_model,
            model_override=request.model,
        )
        start = time.time()
        success = False
        http_status = None
        error_message = None
        reply = ""
        resolved_endpoint = route.base_url
        try:
            reply, http_status, resolved_endpoint = await self._invoke_test_completion(
                route=route,
                messages=[{"role": item.role, "content": item.content} for item in request.messages],
                max_tokens=request.max_tokens,
                system=request.system,
            )
            success = True
        except Exception as exc:
            error_message = str(exc)
        latency_ms = int((time.time() - start) * 1000)

        self._persist_test_record(
            provider=provider,
            resolved_endpoint=resolved_endpoint,
            model=route.model,
            latency_ms=latency_ms,
            success=success,
            http_status=http_status,
            error_message=error_message,
        )

        return ProviderTestChatResponse(
            provider_id=provider.id,
            provider_name=provider.name,
            provider_type=provider.provider_type,
            region=provider.region,
            resolved_endpoint=resolved_endpoint,
            model=route.model,
            latency_ms=latency_ms,
            http_status=http_status,
            success=success,
            error_message=error_message,
            reply=reply,
            tested_at=_utc_now_iso(),
        )


gateway = LLMGateway()
