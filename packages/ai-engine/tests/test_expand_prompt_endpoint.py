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

    def test_expand_prompt_rejects_internal_slot_style_output_and_rewrites_in_user_language(self):
        low_quality_output = "\n".join(
            [
                "Game Type: 根据原始想法确定游戏方向",
                "Core Mechanic: 提炼玩家最常执行的核心动作",
                "Theme: 保留原始想法里的题材、场景或情绪",
                "Input Method: 采用适合手机的点击、滑动或拖拽操作",
                "Win Condition: 明确玩家这一局如何过关或获胜",
                "Difficulty Ramp: 说明难度如何逐步提升",
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
                    json={"description": "做一个上班摸鱼的游戏，5个关卡，要抓住现代办公室的梗，整体要好笑。"},
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("fallback_used"))
        self.assertEqual(data.get("fallback_reason"), "low_quality_llm_output")
        expanded_prompt = data.get("expanded_prompt", "")
        self.assertNotIn("Game Type:", expanded_prompt)
        self.assertNotIn("Core Mechanic:", expanded_prompt)
        self.assertIn("摸鱼", expanded_prompt)
        self.assertIn("手机", expanded_prompt)
        self.assertIn("5个关卡", expanded_prompt)


if __name__ == "__main__":
    unittest.main()
