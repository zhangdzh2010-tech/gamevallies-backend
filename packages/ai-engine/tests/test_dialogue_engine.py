"""Focused tests for dialogue-engine routing behavior."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    AnalyzeDialogueTurnRequest,
    ChatRequest,
    ConversationMessage,
    DraftPlanFromInputRequest,
    SpecFromSlotsRequest,
)
from src.engine.dialogue_engine import (
    DialogueEngine,
    FAST_DIALOGUE_SLOT_OVERALL_TIMEOUT_S,
    FAST_DIALOGUE_SLOT_REQUEST_TIMEOUT_S,
    _infer_game_type_from_sparse_context,
    _infer_slots_from_text,
    _normalize_slot_payload,
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
    def test_analyze_turn_returns_next_question_and_slot_progress(self):
        engine = DialogueEngine()

        async def fake_extract_slots(session, *, source_text, title=None):
            session.slots.game_type = "funny"
            session.slots.core_mechanic = "tap to hide"
            session.slots.input_method = "tap"
            return ["game_type", "core_mechanic", "input_method"]

        with patch.object(engine._client, "is_enabled", return_value=True), patch.object(
            engine,
            "_extract_slots_from_conversation",
            new=AsyncMock(side_effect=fake_extract_slots),
        ):
            response = asyncio.run(
                engine.analyze_turn(
                    AnalyzeDialogueTurnRequest(
                        session_id="creation-1",
                        user_id="user-1",
                        conversation=[
                            ConversationMessage(role="user", content="做一个办公室摸鱼游戏"),
                        ],
                        initial_prompt="做一个办公室摸鱼游戏",
                    )
                )
            )

        self.assertEqual(response.slots.game_type, "funny")
        self.assertIn("game_type", response.slots_updated)
        self.assertFalse(response.ready_to_generate)
        self.assertIsNotNone(response.current_question)
        self.assertEqual(response.current_question.slot_key, "win_condition")
        self.assertIsNotNone(response.question_strategy)
        self.assertEqual(response.question_strategy.slot_key, "win_condition")
        self.assertIn("win_condition", response.confidence_by_slot)
        self.assertGreater(response.confidence_by_slot["core_mechanic"], 0.6)
        self.assertIsNotNone(response.plan_draft)
        self.assertTrue(response.plan_draft.summary)
        self.assertIn("最关键的信息", response.reply)

    def test_draft_plan_from_input_returns_plan_and_confidence_metadata(self):
        engine = DialogueEngine()

        response = asyncio.run(
            engine.draft_plan_from_input(
                DraftPlanFromInputRequest(
                    title="上班摸鱼",
                    source_description="做一个办公室摸鱼游戏，玩家点击伪装摸鱼，在老板巡查时快速切回工作。",
                    generation_tier="showcase",
                )
            )
        )

        self.assertIsNotNone(response.plan_draft)
        self.assertEqual(response.plan_draft.title, "上班摸鱼")
        self.assertIn("上班摸鱼", response.plan_draft.concept)
        self.assertIn("core_mechanic", response.confidence_by_slot)
        self.assertIn("theme", response.evidence_by_slot)
        self.assertIsInstance(response.ambiguity_flags, list)

    def test_analyze_turn_advance_only_skips_slot_extraction(self):
        engine = DialogueEngine()

        with patch.object(engine._client, "is_enabled", return_value=True), patch.object(
            engine,
            "_extract_slots_from_conversation",
            new=AsyncMock(),
        ) as mock_extract:
            response = asyncio.run(
                engine.analyze_turn(
                    AnalyzeDialogueTurnRequest(
                        session_id="creation-2",
                        user_id="user-2",
                        current_slots={
                            "game_type": "casual",
                            "core_mechanic": "tap to collect",
                            "theme": "fruit market",
                            "input_method": "tap",
                            "win_condition": "collect all fruit",
                            "difficulty": "easy",
                        },
                        skipped_slots=["difficulty"],
                        initial_prompt="做一个接水果游戏",
                        advance_only=True,
                    )
                )
            )

        mock_extract.assert_not_called()
        self.assertTrue(response.ready_to_generate)
        self.assertIsNotNone(response.current_question)
        self.assertEqual(response.current_question.slot_key, "win_condition")
        self.assertEqual(response.question_strategy.mode, "ambiguity_resolution")

    def test_analyze_turn_uses_explicit_answer_context_to_advance_state(self):
        engine = DialogueEngine()

        with patch.object(engine._client, "is_enabled", return_value=True), patch.object(
            engine,
            "_extract_slots_from_conversation",
            new=AsyncMock(),
        ) as mock_extract:
            response = asyncio.run(
                engine.analyze_turn(
                    AnalyzeDialogueTurnRequest(
                        session_id="creation-2b",
                        user_id="user-2",
                        current_slots={
                            "game_type": "funny",
                            "core_mechanic": "tap to hide from the boss",
                            "input_method": "tap",
                            "difficulty": "medium",
                        },
                        conversation=[
                            ConversationMessage(role="user", content="做一个办公室摸鱼游戏", kind="prompt"),
                            ConversationMessage(role="assistant", content="它发生在什么场景里？", kind="question"),
                            ConversationMessage(role="user", content="现代办公室，老板会突然巡查。", kind="answer"),
                        ],
                        initial_prompt="做一个办公室摸鱼游戏",
                        answered_slot_key="theme",
                        answered_slot_prompt="它发生在什么场景里？",
                        latest_user_answer="现代办公室，老板会突然巡查。",
                    )
                )
            )

        mock_extract.assert_not_called()
        self.assertIn("theme", response.slots_updated)
        self.assertIn("办公室", response.slots.theme)
        self.assertGreaterEqual(response.confidence_by_slot["theme"], 0.95)
        self.assertNotIn("theme:ambiguous", response.ambiguity_flags)
        self.assertIsNotNone(response.current_question)
        self.assertEqual(response.current_question.slot_key, "win_condition")
        self.assertTrue(response.ready_to_generate)

    def test_spec_from_slots_preserves_tier_and_uses_preferred_game_type_fallback(self):
        engine = DialogueEngine()

        response = asyncio.run(
            engine.spec_from_slots(
                SpecFromSlotsRequest(
                    session_id="creation-3",
                    slots={
                        "core_mechanic": "tap to hide",
                        "theme": "office",
                        "input_method": "tap",
                        "win_condition": "stay undiscovered",
                        "difficulty": "medium",
                    },
                    source_description="做一个办公室摸鱼游戏",
                    title="上班摸鱼",
                    generation_tier="showcase",
                    preferred_game_type="funny",
                )
            )
        )

        self.assertEqual(response.spec.game_type, "funny")
        self.assertEqual(response.spec.generation_tier, "showcase")
        self.assertEqual(response.spec.complexity_budget, "showcase")
        self.assertTrue(response.spec.progression_shape)
        self.assertTrue(response.spec.reward_loop)
        self.assertTrue(response.spec.design_goals)
        self.assertIn("上班摸鱼", response.spec.intent_summary)
        self.assertEqual(response.slot_fill_pct, 1.0)

    def test_spec_from_slots_accepts_list_core_mechanic_from_creation_session_slots(self):
        engine = DialogueEngine()

        response = asyncio.run(
            engine.spec_from_slots(
                SpecFromSlotsRequest(
                    session_id="creation-4",
                    slots={
                        "game_type": "casual",
                        "core_mechanic": ["dash", "avoid", "drop"],
                        "theme": "landscape delivery",
                        "input_method": "swipe",
                        "win_condition": "deliver parcels to target balconies",
                        "difficulty": "medium",
                        "special_rules": "drones patrol rooftops",
                    },
                    source_description="Make a landscape delivery game where the hero dashes across rooftops and drops parcels.",
                    title="Parkour Delivery",
                    generation_tier="showcase",
                )
            )
        )

        self.assertEqual(response.spec.game_type, "casual")
        self.assertIn("dash, avoid, drop", response.spec.intent_summary)
        self.assertIn("drones patrol rooftops", response.spec.special_rules)
        self.assertEqual(response.spec.generation_tier, "showcase")

    def test_normalize_slot_payload_humanizes_machine_like_creation_session_values(self):
        normalized = _normalize_slot_payload({
            "game_type": "funny",
            "core_mechanic": "stealth_timing",
            "theme": "modern_office_satire",
            "input_method": "touch_tap_swipe",
            "win_condition": "complete_5_levels",
            "difficulty": "standard",
        })

        self.assertEqual(normalized["game_type"], "funny")
        self.assertEqual(normalized["core_mechanic"], "stealth timing")
        self.assertEqual(normalized["theme"], "modern office satire")
        self.assertEqual(normalized["input_method"], "swipe")
        self.assertEqual(normalized["win_condition"], "complete 5 levels")
        self.assertEqual(normalized["difficulty"], "medium")

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
                    '{"game_type":"casual","core_mechanic":"躲避障碍","theme":"space",'
                    '"input_method":"swipe","win_condition":"survive","difficulty":"progressive",'
                    '"special_rules":"avoid black holes","reference_game":"星际闪避"}'
                )
            ),
        ) as mock_complete:
            spec = asyncio.run(engine.parse_description_to_spec("做一个太空躲避游戏"))

        self.assertEqual(spec.game_type, "casual")
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
            '分析如下：先理解用户意图。\n{"game_type":"casual","core_mechanic":"躲避障碍","theme":"space"}\n以上是结果。'
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["game_type"], "casual")
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
                        '{"game_type":"casual","core_mechanic":"左右滑动躲避管理员","theme":"zoo",'
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

    def test_dialogue_slot_extract_fast_path_skips_truncation_retry_without_excerpt(self):
        engine = DialogueEngine()

        with patch.object(
            engine._client,
            "complete",
            new=AsyncMock(
                side_effect=LLMResponseTruncatedError(
                    "response truncated",
                    stop_reason="length",
                )
            ),
        ), patch.object(
            engine._client,
            "complete_with_truncation_retry",
            new=AsyncMock(return_value="{}"),
        ) as mock_retry:
            with self.assertRaises(LLMResponseTruncatedError):
                asyncio.run(
                    engine._complete_slot_request(
                        messages=[{"role": "user", "content": "make a puzzle game"}],
                        system="SYSTEM",
                        step_key="dialogue.slot_extract",
                        stage="dialogue",
                        max_tokens=640,
                    )
                )

        mock_retry.assert_not_awaited()

    def test_analyze_turn_slot_timeout_uses_fast_budget_and_heuristic_fallback(self):
        engine = DialogueEngine()

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.slot_extraction_system":
                return "DIALOGUE_SLOT_PROMPT_FROM_DB"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch.object(engine._client, "is_enabled", return_value=True), patch(
            "src.engine.dialogue_engine.require_prompt",
            side_effect=fake_get_prompt,
        ), patch.object(
            engine._client,
            "complete",
            new=AsyncMock(side_effect=asyncio.TimeoutError("slot extract timed out")),
        ) as mock_complete:
            response = asyncio.run(
                engine.analyze_turn(
                    AnalyzeDialogueTurnRequest(
                        session_id="creation-fast-timeout",
                        user_id="user-1",
                        conversation=[
                            ConversationMessage(
                                role="user",
                                content="Make a funny office game where you tap to hide from the boss.",
                            ),
                        ],
                        initial_prompt="Make a funny office game where you tap to hide from the boss.",
                    )
                )
            )

        kwargs = mock_complete.await_args.kwargs
        self.assertEqual(kwargs["step_key"], "dialogue.slot_extract")
        self.assertEqual(kwargs["request_timeout_s"], FAST_DIALOGUE_SLOT_REQUEST_TIMEOUT_S)
        self.assertEqual(kwargs["overall_timeout_s"], FAST_DIALOGUE_SLOT_OVERALL_TIMEOUT_S)
        self.assertGreater(response.slot_fill_pct, 0.0)
        self.assertTrue(response.slots_updated)
        self.assertEqual(response.slots.input_method, "tap")
        self.assertEqual(response.slots.game_type, "funny")
        self.assertIsNotNone(response.current_question)
        self.assertIn(response.current_question.slot_key, {"win_condition", "difficulty", "theme"})

    def test_educational_prompt_biases_to_educational_slots(self):
        inferred = _infer_slots_from_text(
            "请设计一个课堂小游戏，包含3道配套练习题，帮助学生巩固浮力知识点。",
        )
        self.assertEqual(inferred.get("game_type"), "educational")

    def test_sparse_educational_context_prefers_educational(self):
        game_type = _infer_game_type_from_sparse_context(
            "浮力的故事",
            "课堂互动小游戏，帮助老师讲解浮力知识点，并附练习题",
        )
        self.assertEqual(game_type, "educational")

    def test_sparse_non_explicit_action_prompt_can_vary_by_seed(self):
        first = _infer_game_type_from_sparse_context(
            "avoid asteroids",
            variation_seed="game-a",
        )
        second = _infer_game_type_from_sparse_context(
            "avoid asteroids",
            variation_seed="game-b",
        )

        self.assertIn(first, {"casual", "funny", "puzzle"})
        self.assertIn(second, {"casual", "funny", "puzzle"})
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
        self.assertIn(spec.game_type, {"casual", "funny", "puzzle"})

    def test_parse_description_to_spec_coerces_educational_runner_prompt_to_educational(self):
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
                    '{"game_type":"casual","core_mechanic":"move through buoyancy checkpoints",'
                    '"theme":"classroom","input_method":"touch","win_condition":"clear the lesson","difficulty":"medium"}'
                )
            ),
        ):
            spec = asyncio.run(
                engine.parse_description_to_spec(
                    "请围绕浮力知识点设计一个课堂小游戏，包含3道配套练习题和计分方式。",
                )
            )

        self.assertEqual(spec.game_type, "educational")
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
