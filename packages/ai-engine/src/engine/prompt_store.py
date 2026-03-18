"""
prompt_store  --  Read prompt texts from MySQL `system_configs` table.

Usage:
    from engine.prompt_store import get_prompt, refresh

    system_msg = get_prompt("prompt.dialogue_system", default="You are a helpful assistant.")
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional
from urllib.parse import unquote

import pymysql

from ..config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_cache: Dict[str, str] = {}
_loaded: bool = False

# All known prompt keys (for documentation; the store is not limited to these)
PROMPT_KEYS = [
    "prompt.slot_extraction_system",
    "prompt.dialogue_system",
    "prompt.code_gen_system",
    "prompt.game_design_template",
    "prompt.platform_standard",
    "prompt.iterate_classify",
    "prompt.param_adjust",
    "prompt.element_change",
    "prompt.mechanic_change",
    "prompt.qa_fix",
]


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


def _load_from_db() -> Dict[str, str]:
    """Fetch all prompt rows from system_configs and return as a dict."""
    params = _parse_database_url(settings.DATABASE_URL)
    conn = pymysql.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
        charset="utf8mb4",
        connect_timeout=5,
        read_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT config_key, config_value FROM system_configs WHERE category = %s",
                ("prompt",),
            )
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refresh() -> None:
    """Reload all prompts from the database into the in-memory cache.

    If the database is unavailable the existing cache is kept and a warning
    is logged.
    """
    global _cache, _loaded
    try:
        _cache = _load_from_db()
        _loaded = True
        logger.info("prompt_store: loaded %d prompts from DB", len(_cache))
    except Exception:
        _loaded = True  # mark loaded so we don't retry on every call
        logger.warning("prompt_store: failed to load prompts from DB, using defaults", exc_info=True)


def get_prompt(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return the prompt text for *key*, or *default* if not found.

    On the first call the cache is automatically populated from the database.
    """
    global _loaded
    if not _loaded:
        refresh()
    return _cache.get(key, default)
