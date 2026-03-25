"""Focused tests for dialogue-engine routing behavior."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import ChatRequest
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


class TestDialogueEngine(unittest.TestCase):
    def test_parse_description_to_spec_uses_primary_model_not_fast_model(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.intent_parse_system":
                return "INTENT_PARSE_PROMPT_FROM_DB"
            if key == "prompt.slot_extraction_system":
                return "DIALOGUE_SLOT_PROMPT_FROM_DB"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch.object(engine._client, "is_enabled", return_value=True), patch(
            "src.engine.dialogue_engine.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            engine._client,
            "complete",
            new=AsyncMock(
                return_value=(
                    '{"game_type":"dodge","core_mechanic":"躲避障碍","theme":"space",'
                    '"input_method":"swipe","win_condition":"survive","difficulty":"progressive",'
                    '"special_rules":"avoid black holes","reference_game":"星际闪避"}'
                )
            ),
        ) as mock_complete:
            spec = asyncio.run(engine.parse_description_to_spec("做一个太空躲避游戏"))

        self.assertEqual(spec.game_type, "dodge")
        self.assertEqual(spec.intent_summary, "躲避障碍")
        self.assertEqual(spec.special_rules, ["avoid black holes"])
        self.assertEqual(spec.reference_game, "星际闪避")
        self.assertEqual(spec.platform_constraints.input_mode, "swipe")
        self.assertEqual(spec.source_description, "做一个太空躲避游戏")
        kwargs = mock_complete.await_args.kwargs
        self.assertEqual(kwargs["step_key"], "intent_parse")
        self.assertEqual(kwargs["stage"], "intent_parsing")
        self.assertFalse(kwargs["prefer_fast"])
        self.assertIn("INTENT_PARSE_PROMPT_FROM_DB", kwargs["system"])
        self.assertIn("NON-NEGOTIABLE OUTPUT CONTRACT", kwargs["system"])

    def test_safe_parse_json_extracts_embedded_object_from_verbose_text(self):
        parsed = _safe_parse_json(
            '分析如下：先理解用户意图。\n{"game_type":"dodge","core_mechanic":"躲避障碍","theme":"space"}\n以上是结果。'
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["game_type"], "dodge")
        self.assertEqual(parsed["theme"], "space")

    def test_dialogue_slot_extraction_uses_primary_model_while_reply_keeps_fast_model(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.slot_extraction_system":
                return "DIALOGUE_SLOT_PROMPT_FROM_DB"
            if key == "prompt.dialogue_system":
                return "DIALOGUE_REPLY_PROMPT_FROM_DB"
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
                        '{"game_type":"runner","core_mechanic":"左右滑动躲避管理员","theme":"zoo",'
                        '"input_method":"swipe","win_condition":"rescue animals","difficulty":"easy"}'
                    ),
                    "明白了，我会做成动物园逃脱跑酷游戏。",
                ]
            ),
        ) as mock_complete:
            response = asyncio.run(engine.process_message(ChatRequest(
                session_id="dialogue-session-1",
                content="做一个动物园逃脱跑酷游戏",
                user_id="user-1",
            )))

        self.assertIn("动物园", response.reply)
        self.assertEqual(mock_complete.await_args_list[0].kwargs["step_key"], "dialogue.slot_extract")
        self.assertFalse(mock_complete.await_args_list[0].kwargs["prefer_fast"])
        self.assertEqual(mock_complete.await_args_list[1].kwargs["step_key"], "dialogue.reply")
        self.assertTrue(mock_complete.await_args_list[1].kwargs["prefer_fast"])


if __name__ == "__main__":
    unittest.main()
