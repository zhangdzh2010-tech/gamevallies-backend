"""Regression coverage for the LLM design pass."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import CanvasConfig, CoreMechanic, GDD, GameSpec, NumericsConfig
from src.engine.llm_game_designer import LLMGameDesigner


class TestLLMGameDesigner(unittest.TestCase):
    def test_build_prompt_uses_core_mechanic_type(self):
        designer = LLMGameDesigner()
        spec = GameSpec(
            game_type="runner",
            core_mechanics=[CoreMechanic(type="lane_switch", input="swipe")],
        )
        gdd = GDD(
            canvas=CanvasConfig(width=420, height=600),
            numerics=NumericsConfig(),
        )

        prompt = designer._build_prompt(spec, gdd)

        self.assertIn("- Core Mechanic: lane_switch", prompt)

    def test_design_falls_back_when_prompt_build_fails(self):
        designer = LLMGameDesigner()
        spec = GameSpec(game_type="runner")
        gdd = GDD(
            canvas=CanvasConfig(width=420, height=600),
            numerics=NumericsConfig(player_speed=9.0),
        )

        with patch.object(designer._client, "is_enabled", return_value=True), patch.object(
            designer,
            "_build_prompt",
            side_effect=AttributeError("'CoreMechanic' object has no attribute 'name'"),
        ), patch.object(
            designer._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="{}"),
        ) as mock_complete:
            result = asyncio.run(designer.design(spec, gdd))

        self.assertEqual(result.numerics.player_speed, 9.0)
        self.assertEqual(result.level_design, [])
        self.assertEqual(mock_complete.await_count, 0)


if __name__ == "__main__":
    unittest.main()
