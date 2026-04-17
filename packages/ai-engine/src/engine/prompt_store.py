"""Read prompt and runtime catalog data from MySQL-backed config tables."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote

import pymysql

from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from .runtime_profile_ids import (
    DEFAULT_RUNTIME_PROFILE_ID,
    runtime_profile_lookup_candidates,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_cache: Dict[str, str] = {}
_prompt_bundles: Dict[Tuple[str, int], Dict[str, Any]] = {}
_runtime_profiles: Dict[str, Dict[str, Any]] = {}
_loaded: bool = False

# All known prompt keys (for documentation; the store is not limited to these)
PROMPT_KEYS = [
    "prompt.intent_parse_system",
    "prompt.slot_json_repair_system",
    "prompt.code_gen_system",
    "prompt.game_design_template",
    "prompt.platform_standard",
    "prompt.iterate_classify",
    "prompt.param_adjust",
    "prompt.element_change",
    "prompt.mechanic_change",
    "prompt.code_review_system",
    "prompt.code_review_template",
    "prompt.expand_prompt_system",
    "prompt.slot_output_contract",
    "prompt.slot_json_repair_user_template",
    "prompt.intent_detail_template",
    "prompt.mobile_layout_guardrails",
    "prompt.iteration_spec_context_template",
    "prompt.generate_request_context_template",
    "prompt.generate_alignment_reminder",
    "prompt.runtime_contract_summary",
    "prompt.qa_runtime_contract_block",
    "bundle.runtime.locked_contract",
    "bundle.product.policy",
    "bundle.product.logic_generate",
    "bundle.product.intent_parse",
    "bundle.repair.syntax_structural",
    "bundle.runtime.profile.casual_arcade",
    "bundle.runtime.profile.casual_lane",
    "bundle.runtime.profile.puzzle_grid",
    "bundle.runtime.profile.casual_action",
    "bundle.runtime.profile.tap_challenge",
]


class PromptConfigError(RuntimeError):
    """Raised when required prompt config is unavailable."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_database_url(url: str) -> dict:
    """Parse a MySQL URL like mysql://user:password@host:port/database.

    Handles passwords that contain '@' by matching host:port/db from the right.
    """
    # Strip optional query string (?connection_limit=2 etc.)
    base = url.split("?")[0]

    prefix = "mysql://"
    if not base.startswith(prefix):
        raise ValueError(f"Cannot parse DATABASE_URL (expected mysql:// prefix): {url!r}")
    rest = base[len(prefix):]  # user:password@host:port/database

    # Match host:port/database from the RIGHT (avoids ambiguity when password contains '@')
    m_tail = re.search(r"@([^@]+):(\d+)/(.+)$", rest)
    if not m_tail:
        raise ValueError(f"Cannot parse DATABASE_URL: {url!r}")

    host = m_tail.group(1)
    port = int(m_tail.group(2))
    database = m_tail.group(3)

    # Everything before the last @host:port/ is user:password
    creds = rest[: m_tail.start()]
    colon_idx = creds.find(":")
    if colon_idx == -1:
        raise ValueError(f"Cannot parse DATABASE_URL (missing user:password): {url!r}")

    user = unquote(creds[:colon_idx])
    password = unquote(creds[colon_idx + 1 :])

    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "database": database,
    }


def _loads_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list, bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _load_from_db() -> tuple[Dict[str, str], Dict[Tuple[str, int], Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Fetch prompt configs plus v2 runtime catalogs."""
    params = _parse_database_url(settings.DATABASE_URL)
    conn = pymysql.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
        charset="utf8mb4",
        connect_timeout=get_timeout_int(
            "timeout.ai_engine.prompt_store_db_connect_s",
            5,
            min_value=1,
        ),
        read_timeout=get_timeout_int(
            "timeout.ai_engine.prompt_store_db_read_s",
            5,
            min_value=1,
        ),
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT config_key, config_value FROM system_configs WHERE category = %s",
                ("prompt",),
            )
            prompts = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute(
                """
                SELECT id, version, status, product_policy, locked_contract_override,
                       repair_playbook, profile_overrides, metadata
                FROM prompt_bundles
                """
            )
            prompt_bundles: Dict[Tuple[str, int], Dict[str, Any]] = {}
            for row in cur.fetchall():
                bundle_id, version, status, product_policy, locked_contract_override, repair_playbook, profile_overrides, metadata = row
                prompt_bundles[(str(bundle_id), int(version))] = {
                    "id": str(bundle_id),
                    "version": int(version),
                    "status": status,
                    "product_policy": product_policy or "",
                    "locked_contract_override": locked_contract_override or None,
                    "repair_playbook": repair_playbook or "",
                    "profile_overrides": _loads_json(profile_overrides) or {},
                    "metadata": _loads_json(metadata) or {},
                }

            cur.execute(
                """
                SELECT id, display_name, enabled, skeleton_version, contract_schema,
                       few_shot_prompt, metadata
                FROM runtime_profile_catalog
                """
            )
            runtime_profiles: Dict[str, Dict[str, Any]] = {}
            for row in cur.fetchall():
                profile_id, display_name, enabled, skeleton_version, contract_schema, few_shot_prompt, metadata = row
                runtime_profiles[str(profile_id)] = {
                    "id": str(profile_id),
                    "display_name": display_name,
                    "enabled": bool(enabled),
                    "skeleton_version": skeleton_version,
                    "contract_schema": _loads_json(contract_schema) or {},
                    "few_shot_prompt": few_shot_prompt or "",
                    "metadata": _loads_json(metadata) or {},
                }

            return prompts, prompt_bundles, runtime_profiles
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refresh(*, raise_on_error: bool = False) -> int:
    """Reload all prompts from the database into the in-memory cache.

    If the database is unavailable the existing cache is kept and a warning
    is logged.
    """
    global _cache, _prompt_bundles, _runtime_profiles, _loaded
    try:
        prompts, prompt_bundles, runtime_profiles = _load_from_db()
        _cache = prompts
        _prompt_bundles = prompt_bundles
        _runtime_profiles = runtime_profiles
        _loaded = True
        logger.info(
            "prompt_store: loaded %d prompts, %d prompt bundles, %d runtime profiles from DB",
            len(_cache),
            len(_prompt_bundles),
            len(_runtime_profiles),
        )
        return len(_cache)
    except Exception:
        _loaded = True  # mark loaded so we don't retry on every call
        logger.warning("prompt_store: failed to load prompt config from DB", exc_info=True)
        if raise_on_error:
            raise
        return len(_cache)


def get_prompt(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return the prompt text for *key*, or *default* if not found.

    On the first call the cache is automatically populated from the database.
    """
    global _loaded
    if not _loaded:
        refresh()
    return _cache.get(key, default)


def require_prompt(key: str) -> str:
    """Return the prompt text for *key* or raise when it is missing."""
    value = get_prompt(key)
    if value:
        return value
    raise PromptConfigError(f"Missing required prompt config: {key}")


def get_prompt_bundle(bundle_id: str, version: int) -> Optional[Dict[str, Any]]:
    """Return a prompt bundle row by id/version."""
    global _loaded
    if not _loaded:
        refresh()
    return _prompt_bundles.get((str(bundle_id), int(version)))


def get_active_prompt_bundle() -> Optional[Dict[str, Any]]:
    """Return the newest active prompt bundle."""
    global _loaded
    if not _loaded:
        refresh()

    active = [
        bundle for bundle in _prompt_bundles.values()
        if str(bundle.get("status") or "").lower() == "active"
    ]
    if not active:
        return None
    active.sort(key=lambda item: (int(item.get("version") or 0), str(item.get("id") or "")), reverse=True)
    return active[0]


def get_runtime_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    """Return a runtime profile row by id."""
    global _loaded
    if not _loaded:
        refresh()
    for candidate in runtime_profile_lookup_candidates(profile_id):
        profile = _runtime_profiles.get(candidate)
        if profile:
            if str(profile.get("id") or "") == candidate:
                return {**profile, "id": runtime_profile_lookup_candidates(profile_id)[0]}
            return profile
    return None


def get_default_runtime_profile() -> Optional[Dict[str, Any]]:
    """Return the default enabled runtime profile."""
    global _loaded
    if not _loaded:
        refresh()

    enabled = [
        profile for profile in _runtime_profiles.values()
        if profile.get("enabled")
    ]
    if not enabled:
        return None

    marked_default = [
        profile for profile in enabled
        if isinstance(profile.get("metadata"), dict) and profile["metadata"].get("default") is True
    ]
    candidates = marked_default or enabled
    candidates.sort(key=lambda item: str(item.get("id") or ""))
    profile = candidates[0]
    canonical_id = runtime_profile_lookup_candidates(profile.get("id"))[0] if profile.get("id") else DEFAULT_RUNTIME_PROFILE_ID
    return {**profile, "id": canonical_id}


def cached_prompt_count() -> int:
    """Return the number of prompts currently cached in memory."""
    return len(_cache)
