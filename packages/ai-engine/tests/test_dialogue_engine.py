"""Focused tests for dialogue-engine routing behavior."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import ChatRequest
from src.engine.dialogue_engine import (
    DialogueEngine,
    _infer_game_type_from_sparse_context,
    _infer_slots_from_text,
    _safe_parse_json,
)
from src.services.llm_client import LLMResponseTruncatedError

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

    def test_educational_prompt_biases_to_puzzle_slots(self):
        inferred = _infer_slots_from_text(
            "请设计一个课堂小游戏，包含3道配套练习题，帮助学生巩固浮力知识点。",
        )
        self.assertEqual(inferred.get("game_type"), "puzzle")

    def test_sparse_educational_context_prefers_puzzle(self):
        game_type = _infer_game_type_from_sparse_context(
            "浮力的故事",
            "课堂互动小游戏，帮助老师讲解浮力知识点，并附练习题",
        )
        self.assertEqual(game_type, "puzzle")

    def test_sparse_non_explicit_action_prompt_can_vary_by_seed(self):
        first = _infer_game_type_from_sparse_context(
            "avoid asteroids",
            variation_seed="game-a",
        )
        second = _infer_game_type_from_sparse_context(
            "avoid asteroids",
            variation_seed="game-b",
        )

        self.assertIn(first, {"dodge", "shooter", "runner", "rhythm"})
        self.assertIn(second, {"dodge", "shooter", "runner", "rhythm"})
        self.assertNotEqual(first, second)

    def test_sparse_parse_adds_diversity_rules_for_open_briefs(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key in {"prompt.intent_parse_system", "prompt.slot_json_repair_system"}:
                return "INTENT_PARSE_PROMPT_FROM_DB"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch.object(engine._client, "is_enabled", return_value=True), patch(
            "src.engine.dialogue_engine.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            engine._client,
            "complete",
            new=AsyncMock(side_effect=["not json", "still not json"]),
        ):
            spec = asyncio.run(
                engine.parse_description_to_spec(
                    "avoid asteroids",
                    variation_seed="game-a",
                )
            )

        self.assertTrue(
            any("distinctive gameplay loop" in rule.lower() for rule in spec.special_rules)
        )
        self.assertIn(spec.game_type, {"dodge", "shooter", "runner", "rhythm"})

    def test_parse_description_to_spec_coerces_educational_runner_prompt_to_puzzle(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.intent_parse_system":
                return "INTENT_PARSE_PROMPT_FROM_DB"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch.object(engine._client, "is_enabled", return_value=True), patch(
            "src.engine.dialogue_engine.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            engine._client,
            "complete",
            new=AsyncMock(
                return_value=(
                    '{"game_type":"runner","core_mechanic":"move through buoyancy checkpoints",'
                    '"theme":"classroom","input_method":"touch","win_condition":"clear the lesson","difficulty":"medium"}'
                )
            ),
        ):
            spec = asyncio.run(
                engine.parse_description_to_spec(
                    "请围绕浮力知识点设计一个课堂小游戏，包含3道配套练习题和计分方式。",
                )
            )

        self.assertEqual(spec.game_type, "puzzle")
        self.assertEqual(spec.platform_constraints.input_mode, "touch")

    def test_parse_description_to_spec_salvages_truncated_intent_parse_excerpt(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.intent_parse_system":
                return "INTENT_PARSE_PROMPT_FROM_DB"
            if key == "prompt.slot_json_repair_system":
                return "SLOT_JSON_REPAIR_PROMPT_FROM_DB"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        truncated_excerpt = (
            '{"game_type":"puzzle","core_mechanic":"connect circuits",'
            '"theme":"classroom","input_method":"drag","win_condition":"light the bulb"}'
        )
        with patch.object(engine._client, "is_enabled", return_value=True), patch(
            "src.engine.dialogue_engine.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            engine._client,
            "complete",
            new=AsyncMock(
                side_effect=[
                    LLMResponseTruncatedError(
                        "OpenAI-compatible response hit the output length limit and may be truncated",
                        response_excerpt=truncated_excerpt,
                        stop_reason="length",
                    ),
                ]
            ),
        ) as mock_complete:
            spec = asyncio.run(
                engine.parse_description_to_spec(
                    "玩家通过拖拽电池、导线、开关、灯泡等元件组成正确电路并点亮灯泡。",
                )
            )

        self.assertEqual(spec.game_type, "puzzle")
        self.assertEqual(spec.platform_constraints.input_mode, "drag")
        kwargs = mock_complete.await_args.kwargs
        self.assertTrue(kwargs["allow_provider_fallback"])
        self.assertEqual(kwargs["max_tokens"], 640)


if __name__ == "__main__":
    unittest.main()
