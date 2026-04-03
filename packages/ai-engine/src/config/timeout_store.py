"""Database-backed timeout config store."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import unquote

import pymysql

from .settings import settings

logger = logging.getLogger(__name__)

_CACHE: dict[str, str] = {}
_LOADED_AT: float = 0.0
_LOAD_ERROR_BACKOFF_S = 5.0

TIMEOUT_DEFAULTS: dict[str, str] = {
    "timeout.pipeline.default_s": "1800",
    "timeout.pipeline.v2_min_s": "1800",
    "timeout.ai_engine.progress_relay_s": "3",
    "timeout.ai_engine.artifact_relay_s": "5",
    "timeout.ai_engine.stage_summary_relay_s": "5",
    "timeout.ai_engine.task_failure_relay_s": "5",
    "timeout.ai_engine.llm_call_log_relay_s": "3",
    "timeout.ai_engine.task_activity_relay_s": "3",
    "timeout.ai_engine.llm_long_generation_s": "240",
    "timeout.ai_engine.qa_repair_s": "180",
    "timeout.ai_engine.qa_fast_repair_s": "120",
    "timeout.ai_engine.runtime_qa.base_s": "8",
    "timeout.ai_engine.runtime_qa.max_s": "30",
    "timeout.ai_engine.runtime_qa.phase_launch_s": "10",
    "timeout.ai_engine.runtime_qa.max_concurrency": "2",
    "timeout.ai_engine.runtime_qa.bonus_ge_16000_s": "2",
    "timeout.ai_engine.runtime_qa.bonus_ge_24000_s": "2",
    "timeout.ai_engine.runtime_qa.bonus_ge_32000_s": "10",
    "timeout.ai_engine.runtime_qa.bonus_ge_40000_s": "4",
    "timeout.ai_engine.runtime_qa.remediation_bonus_per_attempt_s": "4",
    "timeout.ai_engine.runtime_qa.remediation_bonus_max_s": "8",
    "timeout.ai_engine.runtime_qa.remediation_large_code_floor_s": "8",
    "timeout.ai_engine.runtime_qa.load_wait_factor_ms_per_s": "250",
    "timeout.ai_engine.runtime_qa.load_wait_min_ms": "250",
    "timeout.ai_engine.runtime_qa.initial_wait_ratio": "0.25",
    "timeout.ai_engine.runtime_qa.initial_wait_min_s": "0.35",
    "timeout.ai_engine.runtime_qa.initial_wait_max_s": "2",
    "timeout.ai_engine.runtime_qa.post_wait_ratio": "0.15",
    "timeout.ai_engine.runtime_qa.post_wait_min_s": "0.15",
    "timeout.ai_engine.runtime_qa.post_wait_max_s": "0.8",
    "timeout.ai_engine.llm_activity_heartbeat_s": "15",
    "timeout.ai_engine.llm_fallback_connect_s": "15",
    "timeout.ai_engine.llm_gateway_cache_ttl_s": "10",
    "timeout.ai_engine.timeout_store_cache_ttl_s": "10",
    "timeout.ai_engine.prompt_store_db_connect_s": "5",
    "timeout.ai_engine.prompt_store_db_read_s": "5",
    "timeout.ai_engine.gateway_db_connect_s": "5",
    "timeout.ai_engine.gateway_db_read_s": "5",
    "timeout.ai_engine.async_task_completed_ttl_s": "86400",
}


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


def _connect():
    params = _parse_database_url(settings.DATABASE_URL)
    connect_timeout_s = max(int(float(TIMEOUT_DEFAULTS["timeout.ai_engine.prompt_store_db_connect_s"])), 1)
    read_timeout_s = max(int(float(TIMEOUT_DEFAULTS["timeout.ai_engine.prompt_store_db_read_s"])), 1)
    return pymysql.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
        charset="utf8mb4",
        connect_timeout=connect_timeout_s,
        read_timeout=read_timeout_s,
    )


def refresh(*, raise_on_error: bool = False) -> int:
    global _CACHE, _LOADED_AT
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT config_key, config_value FROM system_configs WHERE category = %s",
                    ("timeout",),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        next_cache: dict[str, str] = {}
        for row in rows:
            next_cache[str(row[0])] = str(row[1])
        _CACHE = next_cache
        _LOADED_AT = time.time()
        effective_count = len(set(TIMEOUT_DEFAULTS.keys()) | set(_CACHE.keys()))
        logger.info("timeout_store: loaded %d timeout configs from DB", len(_CACHE))
        return effective_count
    except Exception:
        _LOADED_AT = time.time()
        logger.warning("timeout_store: failed to load timeout configs from DB", exc_info=True)
        if raise_on_error:
            raise
        return len(set(TIMEOUT_DEFAULTS.keys()) | set(_CACHE.keys()))


def _cache_ttl_s() -> int:
    raw = (
        _CACHE.get("timeout.ai_engine.timeout_store_cache_ttl_s")
        or TIMEOUT_DEFAULTS["timeout.ai_engine.timeout_store_cache_ttl_s"]
    )
    try:
        return max(int(float(raw)), 1)
    except Exception:
        return max(int(float(TIMEOUT_DEFAULTS["timeout.ai_engine.timeout_store_cache_ttl_s"])), 1)


def _ensure_loaded() -> None:
    if not _LOADED_AT:
        refresh()
        return

    age = time.time() - _LOADED_AT
    if age >= _cache_ttl_s():
        refresh()


def get_raw(key: str, default: str | int | float) -> str:
    _ensure_loaded()
    if key in _CACHE:
        return _CACHE[key]
    if key in TIMEOUT_DEFAULTS:
        return TIMEOUT_DEFAULTS[key]
    return str(default)


def get_int(key: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    raw = get_raw(key, default)
    try:
        value = int(float(str(raw)))
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def get_float(key: str, default: float, *, min_value: float | None = None, max_value: float | None = None) -> float:
    raw = get_raw(key, default)
    try:
        value = float(str(raw))
    except Exception:
        value = float(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def cached_count() -> int:
    _ensure_loaded()
    return len(set(TIMEOUT_DEFAULTS.keys()) | set(_CACHE.keys()))
