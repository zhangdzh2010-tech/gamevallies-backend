"""Unittest coverage for prompt-store integrations."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import IterationType
from src.engine.code_generator import CodeGenerator
from src.main import app


class TestPromptIntegration(unittest.TestCase):
    def test_param_adjust_llm_fallback_uses_prompt_config(self):
        generator = CodeGenerator(llm_mode="real")

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.param_adjust":
                return "PARAM_PROMPT::{feedback}::{code}"
            if key == "prompt.code_gen_system":
                return "CODE_GEN_SYSTEM_FROM_DB"
            return default

        with patch(
            "src.engine.code_generator.get_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            generator._client,
            "complete",
            new=AsyncMock(return_value="<!DOCTYPE html><html></html>"),
        ) as mock_complete:
            result = asyncio.run(
                generator._llm_iterate(
                    code="<!DOCTYPE html><html><body>old</body></html>",
                    feedback="make it faster",
                    conversation=[],
                    iter_type=IterationType.param_adjust,
                )
            )

        self.assertEqual(result, "<!DOCTYPE html><html></html>")
        kwargs = mock_complete.await_args.kwargs
        self.assertEqual(kwargs["system"], "CODE_GEN_SYSTEM_FROM_DB")
        self.assertIn(
            "PARAM_PROMPT::make it faster::<!DOCTYPE html><html><body>old</body></html>",
            kwargs["messages"][0]["content"],
        )

    def test_prompt_refresh_endpoint_requires_admin_token(self):
        with TestClient(app) as client:
            response = client.post("/api/v1/ai/prompts/refresh")
        self.assertEqual(response.status_code, 401)

    def test_prompt_refresh_endpoint_reloads_cache(self):
        with patch(
            "src.api.endpoints.generate.refresh_prompt_cache",
            return_value=10,
        ) as mock_refresh, patch(
            "src.api.endpoints.generate.cached_prompt_count",
            return_value=10,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/prompts/refresh",
                    headers={"x-admin-token": "admin123"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["prompt_count"], 10)
        mock_refresh.assert_called_once_with(raise_on_error=True)


if __name__ == "__main__":
    unittest.main()
