"""Regression coverage for the expand-prompt endpoint."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.main import app


class TestExpandPromptEndpoint(unittest.TestCase):
    def test_expand_prompt_returns_user_facing_english_fallback_when_llm_fails(self):
        with patch(
            "src.services.llm_client.LLMClient.is_enabled",
            return_value=True,
        ), patch(
            "src.services.llm_client.LLMClient.complete",
            new=AsyncMock(side_effect=RuntimeError("provider exploded")),
        ), patch(
            "src.api.endpoints.generate.require_prompt",
            return_value="EXPAND_PROMPT_SYSTEM",
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/expand-prompt",
                    json={"description": "Make a cozy fruit merge game for mobile."},
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("fallback_used"))
        expanded_prompt = data.get("expanded_prompt", "")
        self.assertIn("Create a compact", expanded_prompt)
        self.assertIn("fruit merge", expanded_prompt.lower())
        self.assertNotIn("Game Type:", expanded_prompt)
        self.assertNotIn("Core Mechanic:", expanded_prompt)

    def test_expand_prompt_rejects_legacy_template_style_output(self):
        low_quality_output = "\n".join(
            [
                "Original Idea: Make a premium-feeling landscape action game for mobile web where a cyber ronin hero dashes across neon rooftops, slices hunter drones, and collects energy shards.",
                "",
                "Please turn this brief into a mobile-friendly game generation prompt that covers at least these elements:",
                "Game Type: Choose the most fitting direction from the original idea",
                "Core Mechanic: Describe the main repeated player action",
                "Theme: Preserve the setting, fantasy, or mood implied by the brief",
                "Input Method: Use touch-friendly tap, swipe, or drag controls",
            ]
        )

        with patch(
            "src.services.llm_client.LLMClient.is_enabled",
            return_value=True,
        ), patch(
            "src.services.llm_client.LLMClient.complete",
            new=AsyncMock(return_value=low_quality_output),
        ), patch(
            "src.api.endpoints.generate.require_prompt",
            return_value="EXPAND_PROMPT_SYSTEM",
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/expand-prompt",
                    json={
                        "description": "Make a premium-feeling landscape action game for mobile web where a cyber ronin hero dashes across neon rooftops and slices hunter drones.",
                    },
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("fallback_used"))
        self.assertEqual(data.get("fallback_reason"), "low_quality_llm_output")
        expanded_prompt = data.get("expanded_prompt", "")
        self.assertIn("cyber ronin", expanded_prompt.lower())
        self.assertNotIn("Original Idea:", expanded_prompt)
        self.assertNotIn("Please turn this brief", expanded_prompt)
        self.assertNotIn("Game Type:", expanded_prompt)
        self.assertNotIn("Core Mechanic:", expanded_prompt)


if __name__ == "__main__":
    unittest.main()
