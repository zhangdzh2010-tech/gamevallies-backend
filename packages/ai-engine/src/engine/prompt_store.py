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
    """Parse a MySQL URL like mysql://user:password@host:port/database."""
    pattern = r"mysql://(?P<user>[^:]+):(?P<password>[^@]*)@(?P<host>[^:]+):(?P<port>\d+)/(?P<database>.+)"
    m = re.match(pattern, url)
    if not m:
        raise ValueError(f"Cannot parse DATABASE_URL: {url!r}")
    return {
        "user": unquote(m.group("user")),
        "password": unquote(m.group("password")),
        "host": m.group("host"),
        "port": int(m.group("port")),
        "database": m.group("database"),
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
