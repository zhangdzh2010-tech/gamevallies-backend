import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import timeout_store


def test_get_raw_uses_catalog_default_when_db_row_missing():
    with patch("src.config.timeout_store._ensure_loaded", return_value=None), patch.dict(
        timeout_store._CACHE,
        {},
        clear=True,
    ):
        value = timeout_store.get_raw("timeout.pipeline.default_s", 999)

    assert value == "1800"


def test_timeout_store_cache_ttl_falls_back_to_catalog_default_not_env():
    with patch.dict(
        timeout_store._CACHE,
        {"timeout.ai_engine.timeout_store_cache_ttl_s": "not-a-number"},
        clear=True,
    ), patch("src.config.timeout_store.settings.LLM_GATEWAY_CACHE_TTL_S", 77):
        ttl = timeout_store._cache_ttl_s()

    assert ttl == 10
