"""Regression coverage for robust slot JSON parsing and repair."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.dialogue_engine import DialogueEngine, _safe_parse_json

DIALOGUE_TEST_PROMPTS = {
    "prompt.slot_output_contract": (
        "NON-NEGOTIABLE OUTPUT CONTRACT:\n"
        "- Return ONLY valid JSON.\n"
        "- Do not wrap the JSON in markdown fences.\n"
        "- Do not add explanations before or after the JSON.\n"
        "- The JSON object must use exactly these keys:\n"
        "{slot_json_schema}"
    ),
    "prompt.slot_json_repair_user_template": (
        "Source user request:\n{source_text}\n\n"
        "Raw parser output:\n{raw_parser_output}\n\n"
        "Normalize the raw parser output into the required JSON object now."
    ),
}


class TestDialogueEngineJsonRepair(unittest.TestCase):
    def test_safe_parse_json_accepts_json_like_object_literals(self):
        parsed = _safe_parse_json(
            "{game_type: 'dodge', core_mechanic: 'avoid hazards', theme: 'space', special_rules: ['avoid black holes'],}"
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["game_type"], "dodge")
        self.assertEqual(parsed["core_mechanic"], "avoid hazards")
        self.assertEqual(parsed["special_rules"], ["avoid black holes"])

    def test_safe_parse_json_accepts_labeled_slot_text(self):
        parsed = _safe_parse_json(
            "\n".join([
                "Game Type: runner",
                "Core Mechanic: swipe left and right to dodge obstacles",
                "Theme: zoo",
                "Input Method: swipe",
                "Win Condition: survive for 60 seconds",
                "Difficulty: progressive",
                "Special Rules: rescue animals; avoid cages",
            ])
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["game_type"], "runner")
        self.assertEqual(parsed["theme"], "zoo")
        self.assertEqual(parsed["special_rules"], ["rescue animals", "avoid cages"])

    def test_parse_description_to_spec_repairs_non_json_response(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.intent_parse_system":
                return "INTENT_PARSE_PROMPT_FROM_DB"
            if key == "prompt.slot_json_repair_system":
                return "SLOT_JSON_REPAIR_PROMPT_FROM_DB"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch.object(engine._client, "is_enabled", return_value=True), patch(
            "src.engine.dialogue_engine.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            engine._client,
            "complete",
            new=AsyncMock(
                side_effect=[
                    (
                        "This is a neon space dodge game where the player swipes left and right "
                        "to avoid meteors and survive for 60 seconds."
                    ),
                    (
                        '{"game_type":"dodge","core_mechanic":"move left and right to avoid meteors",'
                        '"theme":"neon space","input_method":"swipe","win_condition":"survive for 60 seconds",'
                        '"difficulty":"progressive","visual_style":"neon","audio_style":"none",'
                        '"special_rules":["collect stars"],"reference_game":"space dodge"}'
                    ),
                ]
            ),
        ) as mock_complete:
            spec = asyncio.run(engine.parse_description_to_spec("make me a neon space dodge game"))

        self.assertEqual(spec.game_type, "dodge")
        self.assertEqual(spec.visual_style.theme, "neon space")
        self.assertEqual(spec.platform_constraints.input_mode, "swipe")
        self.assertEqual(spec.special_rules, ["collect stars"])
        self.assertEqual(mock_complete.await_count, 2)
        self.assertEqual(mock_complete.await_args_list[0].kwargs["step_key"], "intent_parse")
        self.assertEqual(mock_complete.await_args_list[1].kwargs["step_key"], "intent_parse")
        self.assertIn("SLOT_JSON_REPAIR_PROMPT_FROM_DB", mock_complete.await_args_list[1].kwargs["system"])
        self.assertIn("NON-NEGOTIABLE OUTPUT CONTRACT", mock_complete.await_args_list[1].kwargs["system"])

    def test_parse_description_to_spec_synthesizes_sparse_request_fallback(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.intent_parse_system":
                return "INTENT_PARSE_PROMPT_FROM_DB"
            if key == "prompt.slot_json_repair_system":
                return "SLOT_JSON_REPAIR_PROMPT_FROM_DB"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch.object(engine._client, "is_enabled", return_value=True), patch(
            "src.engine.dialogue_engine.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            engine._client,
            "complete",
            new=AsyncMock(
                side_effect=[
                    "The request only asks to add more levels for a pig-themed game.",
                    "Still not enough information to emit strict JSON.",
                ]
            ),
        ) as mock_complete:
            spec = asyncio.run(
                engine.parse_description_to_spec(
                    "继续增加关卡，设置5个关卡",
                    title="逮小猪",
                    preferred_game_type="runner",
                )
            )

        self.assertEqual(spec.game_type, "runner")
        self.assertEqual(spec.platform_constraints.input_mode, "tap")
        self.assertEqual(spec.visual_style.theme, "zoo")
        self.assertTrue(any("5个关卡" in rule for rule in spec.special_rules))
        self.assertEqual(mock_complete.await_count, 2)

    def test_parse_description_to_spec_backfills_partial_json_with_preferred_game_type(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.intent_parse_system":
                return "INTENT_PARSE_PROMPT_FROM_DB"
            if key == "prompt.slot_json_repair_system":
                return "SLOT_JSON_REPAIR_PROMPT_FROM_DB"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch.object(engine._client, "is_enabled", return_value=True), patch(
            "src.engine.dialogue_engine.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            engine._client,
            "complete",
            new=AsyncMock(
                side_effect=[
                    '{"theme":"lab","input_method":"drag"}',
                    '{"core_mechanic":"connect the pieces","win_condition":"complete the target"}',
                ]
            ),
        ) as mock_complete:
            spec = asyncio.run(
                engine.parse_description_to_spec(
                    "Arrange the components and make the setup work on mobile.",
                    title="Focused Lab",
                    preferred_game_type="puzzle",
                )
            )

        self.assertEqual(spec.game_type, "puzzle")
        self.assertEqual(spec.platform_constraints.input_mode, "drag")
        self.assertEqual(spec.rules.win_condition, "complete the target")
        self.assertEqual(mock_complete.await_count, 2)


if __name__ == "__main__":
    unittest.main()
