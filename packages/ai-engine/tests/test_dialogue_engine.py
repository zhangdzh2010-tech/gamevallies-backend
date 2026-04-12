"""Focused tests for dialogue-engine routing behavior."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.api.models import (
    AnalyzeDialogueTurnRequest,
    AnalyzeDialogueTurnResponse,
    ChatRequest,
    ConversationMessage,
    DraftPlanFromInputRequest,
    DialogueQuestion,
    PlanDraft,
    SpecFromSlotsRequest,
    SlotState,
)
from src.api.endpoints import generate as generate_api
from src.engine.dialogue_engine import (
    DialogueEngine,
    FAST_DIALOGUE_SLOT_OVERALL_TIMEOUT_S,
    FAST_DIALOGUE_SLOT_REQUEST_TIMEOUT_S,
    INTENT_PARSE_OVERALL_TIMEOUT_S,
    INTENT_PARSE_REQUEST_TIMEOUT_S,
    _build_dialogue_reply_system_prompt_from_catalog,
    _build_dialogue_reply_user_prompt_from_catalog,
    _infer_game_type_from_sparse_context,
    _infer_slots_from_text,
    _infer_theme_from_context,
    _normalize_game_type_label,
    _normalize_slot_payload,
    _safe_parse_json,
)
from src.main import app
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
    "prompt.dialogue_reply_system": (
        "{base_prompt}\n\n"
        "DIALOGUE_REPLY_STYLE_FROM_DB"
    ),
    "prompt.dialogue_reply_user_template_zh": (
        "ZH_TEMPLATE_FROM_DB\n"
        "{reply_context}"
    ),
    "prompt.dialogue_reply_user_template_en": (
        "EN_TEMPLATE_FROM_DB\n"
        "{reply_context}"
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
        self.assertIn("最重要的问题", response.reply)

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

    def test_infer_slots_from_reference_game_applies_reference_defaults(self):
        inferred = _infer_slots_from_text("帮我创建一个类似羊了个羊的游戏")

        self.assertEqual(inferred["reference_game"], "羊了个羊")
        self.assertEqual(inferred["game_type"], "puzzle")
        self.assertEqual(inferred["input_method"], "tap")
        self.assertIn("三消", inferred["core_mechanic"])

    def test_infer_theme_from_context_ignores_single_character_false_positive_markers(self):
        self.assertNotEqual(_infer_theme_from_context("做一个接水果游戏"), "ocean")

    def test_analyze_turn_reference_meta_question_acknowledges_reference_without_echoing_bad_slot_text(self):
        engine = DialogueEngine()

        with patch.object(engine._client, "is_enabled", return_value=True), patch.object(
            engine,
            "_extract_slots_from_conversation",
            new=AsyncMock(),
        ) as mock_extract:
            response = asyncio.run(
                engine.analyze_turn(
                    AnalyzeDialogueTurnRequest(
                        session_id="creation-ref-1",
                        user_id="user-ref-1",
                        conversation=[
                            ConversationMessage(role="user", content="帮我创建一个类似羊了个羊的游戏", kind="prompt"),
                            ConversationMessage(role="assistant", content="这一局里玩家怎样才算过关？", kind="question"),
                            ConversationMessage(role="user", content="羊了个羊的游戏你了解吗", kind="answer"),
                        ],
                        initial_prompt="帮我创建一个类似羊了个羊的游戏",
                        answered_slot_key="win_condition",
                        answered_slot_prompt="这一局里玩家怎样才算过关？",
                        latest_user_answer="羊了个羊的游戏你了解吗",
                    )
                )
            )

        mock_extract.assert_not_called()
        self.assertEqual(response.slots.reference_game, "羊了个羊")
        self.assertNotEqual(response.slots.win_condition, "羊了个羊的游戏你了解吗")
        self.assertIn("羊了个羊", response.reply)
        self.assertIn("了解", response.reply)
        self.assertIsNotNone(response.current_question)
        self.assertEqual(response.current_question.slot_key, "theme")
        self.assertIn("羊了个羊", response.current_question.prompt)

    def test_analyze_turn_stream_emits_reply_deltas_and_final_result(self):
        engine = DialogueEngine()

        async def fake_extract_slots(session, *, source_text, title=None):
            session.slots.game_type = "funny"
            session.slots.core_mechanic = "tap to hide"
            session.slots.input_method = "tap"
            return ["game_type", "core_mechanic", "input_method"]

        async def fake_stream_reply(*, req, analysis):
            yield "了解，"
            yield "我先按办公室摸鱼喜剧来理解。"

        async def collect_events():
            items = []
            async for item in engine.analyze_turn_stream(
                AnalyzeDialogueTurnRequest(
                    session_id="creation-stream-1",
                    user_id="user-stream-1",
                    conversation=[
                        ConversationMessage(role="user", content="做一个办公室摸鱼游戏"),
                    ],
                    initial_prompt="做一个办公室摸鱼游戏",
                )
            ):
                items.append(item)
            return items

        with patch.object(engine._client, "is_enabled", return_value=True), patch.object(
            engine,
            "_extract_slots_from_conversation",
            new=AsyncMock(side_effect=fake_extract_slots),
        ), patch.object(
            engine,
            "_stream_analyze_turn_reply",
            new=fake_stream_reply,
        ):
            events = asyncio.run(collect_events())

        self.assertGreaterEqual(len(events), 3)
        self.assertEqual(events[0]["event"], "final")
        self.assertEqual(events[1]["event"], "delta")
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[0]["data"]["slots"]["game_type"], "funny")
        self.assertEqual(events[0]["data"]["slots"]["core_mechanic"], "tap to hide")
        joined_deltas = "".join(item["data"]["delta"] for item in events if item["event"] == "delta")
        self.assertTrue(joined_deltas)
        self.assertEqual(joined_deltas, events[-1]["data"]["message"])

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
        self.assertEqual(response.missing_required, [])
        self.assertEqual(response.slot_fill_pct, 1.0)

    def test_spec_from_slots_backfills_sparse_zero_question_brief(self):
        engine = DialogueEngine()

        response = asyncio.run(
            engine.spec_from_slots(
                SpecFromSlotsRequest(
                    session_id="creation-sparse-1",
                    slots={
                        "core_mechanic": "dodge obstacles",
                    },
                    source_description="Make a simple dodge game.",
                    title="Quick Dodge",
                    generation_tier="standard",
                )
            )
        )

        self.assertIsNotNone(response.spec)
        self.assertEqual(response.missing_required, [])
        self.assertGreaterEqual(response.slot_fill_pct, 0.8)
        self.assertTrue(response.spec.game_type)
        self.assertTrue(response.spec.intent_summary)
        self.assertTrue(response.spec.platform_constraints.input_mode)

    def test_spec_from_slots_rich_runner_brief_does_not_use_sparse_random_game_type_fallback(self):
        engine = DialogueEngine()

        response = asyncio.run(
            engine.spec_from_slots(
                SpecFromSlotsRequest(
                    session_id="creation-rich-runner-1",
                    slots={},
                    source_description=(
                        "做一个竖屏跑酷战斗小游戏。点击开始后玩家通过左右滑动切换跑道、上滑跳跃、下滑滑铲，"
                        "途中需要躲避障碍、收集能量、击败一个小 Boss，最终在 60 秒内通关。"
                    ),
                    title="E2E Complex Runner CN",
                    generation_tier="standard",
                )
            )
        )

        self.assertEqual(response.spec.game_type, "casual")
        self.assertEqual(response.spec.platform_constraints.input_mode, "swipe")

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
        self.assertEqual(kwargs["request_timeout_s"], INTENT_PARSE_REQUEST_TIMEOUT_S)
        self.assertEqual(kwargs["overall_timeout_s"], INTENT_PARSE_OVERALL_TIMEOUT_S)
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
        self.assertIn("DIALOGUE_REPLY_PROMPT_FROM_DB", mock_complete.await_args_list[1].kwargs["system"])

    def test_dialogue_reply_catalog_helpers_build_config_driven_prompts(self):
        analysis = AnalyzeDialogueTurnResponse(
            reply="鍏堟寜鍔ㄧ墿鍥€冭劚璺戦叿杩欎釜鏂瑰悜鏀跺彛銆?",
            slots=SlotState(
                game_type="casual",
                core_mechanic="swipe to dodge",
                theme="zoo escape",
                input_method="swipe",
                win_condition="rescue all animals",
                difficulty="easy",
                reference_game="Temple Run",
            ),
            missing_required=[],
            ready_to_generate=False,
            current_question=DialogueQuestion(
                slot_key="win_condition",
                label="鑳滃埄鐩爣",
                prompt="杩欏眬鐨勯€氬叧鐩爣鏄粈涔堬紵",
            ),
            next_best_question_reason="Need to lock the success beat.",
            plan_draft=PlanDraft(
                summary="鍔ㄧ墿鍥€冭劚",
                interaction="婊戝姩韬查伩闅滅",
                objective="鎶婂姩鐗╁甫鍑哄姩鐗╁洯",
            ),
        )
        request = AnalyzeDialogueTurnRequest(
            session_id="creation-catalog-1",
            user_id="user-1",
            conversation=[
                ConversationMessage(role="user", content="鍋氫竴涓姩鐗╁洯閫冭劚璺戦叿娓告垙"),
            ],
            initial_prompt="鍋氫竴涓姩鐗╁洯閫冭劚璺戦叿娓告垙",
            latest_user_answer="鍋氫竴涓姩鐗╁洯閫冭劚璺戦叿娓告垙",
        )

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.dialogue_system":
                return "DIALOGUE_REPLY_PROMPT_FROM_DB\nCurrent slot fill state: {slot_summary}\nMissing required info: {missing_slots}"
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch("src.engine.dialogue_engine.require_prompt", side_effect=fake_get_prompt):
            system_prompt = _build_dialogue_reply_system_prompt_from_catalog(analysis)
            user_prompt = _build_dialogue_reply_user_prompt_from_catalog(
                request=request,
                analysis=analysis,
                source_text=request.initial_prompt or "",
            )

        self.assertIn("DIALOGUE_REPLY_PROMPT_FROM_DB", system_prompt)
        self.assertIn("DIALOGUE_REPLY_STYLE_FROM_DB", system_prompt)
        self.assertIn("ZH_TEMPLATE_FROM_DB", user_prompt)
        self.assertIn("\"working_direction\":", user_prompt)
        self.assertIn("\"response_mode\":", user_prompt)
        self.assertNotIn("- game_type:", user_prompt)
        self.assertNotIn("Need to lock the success beat.", user_prompt)
        self.assertNotIn(analysis.reply, user_prompt)
        self.assertNotIn("Safe fallback wording:", user_prompt)

    def test_dialogue_reply_prompt_ignores_legacy_db_template_without_reply_context(self):
        analysis = AnalyzeDialogueTurnResponse(
            reply="我已经明确你的需求啦，接下来只差一个过关目标就能开做。",
            slots=SlotState(
                game_type="funny",
                core_mechanic="tap to hide from the boss",
                theme="office",
                input_method="tap",
                win_condition="survive the shift",
                difficulty="medium",
            ),
            slots_updated=["game_type", "core_mechanic", "theme"],
            missing_required=["win_condition"],
            slot_fill_pct=0.83,
            ready_to_generate=False,
            current_question=DialogueQuestion(
                slot_key="win_condition",
                label="Win Condition",
                prompt="你更希望玩家通过清空所有元素，还是撑过一轮来达成过关条件？",
            ),
            next_best_question_reason="Need to lock the success beat.",
            plan_draft=PlanDraft(
                summary="办公室摸鱼喜剧",
                interaction="点击伪装摸鱼，老板巡查时快速切回工作状态",
                objective="避开巡查并累计摸鱼进度",
            ),
        )
        request = AnalyzeDialogueTurnRequest(
            session_id="creation-catalog-legacy-1",
            user_id="user-legacy-1",
            conversation=[
                ConversationMessage(role="user", content="帮我做一个类似羊了个羊，但把羊换成牛的小游戏"),
            ],
            initial_prompt="帮我做一个类似羊了个羊，但把羊换成牛的小游戏",
            latest_user_answer="羊了个羊的玩法你知道吧",
        )

        def fake_get_prompt(key: str, default=None):
            if key == "prompt.dialogue_system":
                return "DIALOGUE_REPLY_PROMPT_FROM_DB\nCurrent slot fill state: {slot_summary}\nMissing required info: {missing_slots}"
            if key == "prompt.dialogue_reply_user_template_zh":
                return (
                    "ZH_LEGACY_TEMPLATE_FROM_DB\n"
                    "Initial idea: {initial_idea}\n"
                    "Safe fallback wording: {safe_fallback_reply}"
                )
            return DIALOGUE_TEST_PROMPTS.get(key, default)

        with patch("src.engine.dialogue_engine.require_prompt", side_effect=fake_get_prompt):
            user_prompt = _build_dialogue_reply_user_prompt_from_catalog(
                request=request,
                analysis=analysis,
                source_text=request.initial_prompt or "",
            )

        self.assertNotIn("ZH_LEGACY_TEMPLATE_FROM_DB", user_prompt)
        self.assertNotIn("Safe fallback wording:", user_prompt)
        self.assertIn("\"working_direction\":", user_prompt)
        self.assertIn("\"follow_up_question\":", user_prompt)
        self.assertNotIn("Need to lock the success beat.", user_prompt)

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

    def test_runner_boss_prompt_does_not_bias_to_funny(self):
        inferred = _infer_slots_from_text(
            "做一个竖屏跑酷战斗小游戏。点击开始后玩家通过左右滑动切换跑道、上滑跳跃、下滑滑铲，"
            "途中需要躲避障碍、收集能量、击败一个小 Boss，最终在 60 秒内通关。"
        )
        self.assertEqual(inferred.get("game_type"), "casual")

    def test_normalize_game_type_preserves_curated_action_direction_without_explicit_funny_marker(self):
        normalized = _normalize_game_type_label(
            "casual",
            "做一个竖屏跑酷战斗小游戏。玩家要击败一个小 Boss 并在 60 秒内通关。",
        )
        self.assertEqual(normalized, "casual")

    def test_normalize_game_type_respects_explicit_funny_request_even_when_parser_returns_casual(self):
        normalized = _normalize_game_type_label(
            "casual",
            "做一个搞笑办公室小游戏，玩家点按摸鱼并躲开老板巡查。",
        )
        self.assertEqual(normalized, "funny")

    def test_normalize_game_type_coerces_runner_prompt_back_to_casual_when_parser_returns_puzzle(self):
        normalized = _normalize_game_type_label(
            "puzzle",
            "Create a portrait endless lane runner for mobile web. The player swipes left or right to dodge traffic cones and survive for 45 seconds.",
        )
        self.assertEqual(normalized, "casual")

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

    def test_parse_description_to_spec_retries_timeout_with_dedicated_intent_parse_budget(self):
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
            new=AsyncMock(side_effect=[TimeoutError("timeout of 30000ms exceeded")]),
        ) as mock_complete, patch.object(
            engine._client,
            "complete_with_truncation_retry",
            new=AsyncMock(
                return_value=(
                    '{"game_type":"casual","core_mechanic":"lane dodge","theme":"city",'
                    '"input_method":"swipe","win_condition":"survive 45 seconds","difficulty":"progressive"}'
                )
            ),
        ) as mock_retry:
            spec = asyncio.run(
                engine.parse_description_to_spec(
                    "Create a portrait endless lane runner for mobile web. The player swipes left or right to dodge traffic cones and survive for 45 seconds.",
                )
            )

        self.assertEqual(spec.game_type, "casual")
        self.assertEqual(mock_complete.await_count, 1)
        retry_kwargs = mock_retry.await_args.kwargs
        self.assertEqual(retry_kwargs["request_timeout_s"], INTENT_PARSE_REQUEST_TIMEOUT_S)
        self.assertEqual(retry_kwargs["overall_timeout_s"], INTENT_PARSE_OVERALL_TIMEOUT_S)
        self.assertEqual(retry_kwargs["timeout_retry_attempts"], 1)

    def test_analyze_turn_stream_falls_back_to_full_reply_after_partial_stream_failure(self):
        engine = DialogueEngine()
        fallback_reply = "鎴戝厛鎶婅繖灞€瀹氫箟鎴愬姙鍏鎽搁奔鍠滃墽銆傛渶鍚庡啀甯垜纭涓€涓嬬帺瀹舵€庝箞鎵嶇畻杩囧叧锛?"

        async def fake_stream_reply(*, req, analysis):
            yield "鍗婂彞"
            raise RuntimeError("stream interrupted")

        async def collect_events():
            items = []
            async for item in engine.analyze_turn_stream(
                AnalyzeDialogueTurnRequest(
                    session_id="creation-stream-fallback",
                    user_id="user-stream-fallback",
                    conversation=[
                        ConversationMessage(role="user", content="鍋氫竴涓姙鍏鎽搁奔娓告垙"),
                    ],
                    initial_prompt="鍋氫竴涓姙鍏鎽搁奔娓告垙",
                )
            ):
                items.append(item)
            return items

        fake_analysis = AnalyzeDialogueTurnResponse(
            reply=fallback_reply,
            slots=SlotState(
                game_type="funny",
                core_mechanic="tap to hide",
                theme="office",
                input_method="tap",
                win_condition="survive the shift",
                difficulty="medium",
            ),
            slots_updated=["game_type", "core_mechanic"],
            missing_required=["win_condition"],
            slot_fill_pct=0.83,
            ready_to_generate=False,
            current_question=DialogueQuestion(
                slot_key="win_condition",
                label="Win Condition",
                prompt="鐜╁鎬庝箞鎵嶇畻杩囧叧锛?",
                skippable=True,
            ),
            plan_draft=PlanDraft(
                title="涓婄彮鎽搁奔",
                summary="鍔炲叕瀹ゆ懜楸兼悶绗戝皬娓告垙",
                concept="鍔炲叕瀹ゆ懜楸",
                interaction="鐐瑰嚮鍒囨崲鎽搁奔鍔ㄤ綔",
                objective="鎾戝埌涓嬪姙",
                pacing="蹇妭濂?",
                visual_direction="neon office",
                signature_moment="鑰佹澘绐佺劧宸℃煡",
            ),
        )

        with patch.object(engine, "analyze_turn", new=AsyncMock(return_value=fake_analysis)), patch.object(
            engine,
            "_stream_analyze_turn_reply",
            new=fake_stream_reply,
        ):
            events = asyncio.run(collect_events())

        self.assertEqual(events[0]["event"], "final")
        self.assertEqual(events[1]["event"], "delta")
        self.assertNotEqual(events[1]["data"]["delta"], "鍗婂彞")
        self.assertNotIn(
            "鍗婂彞",
            "".join(item["data"]["delta"] for item in events if item["event"] == "delta"),
        )
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["data"]["message"], fallback_reply)
        self.assertEqual(events[0]["data"]["reply"], fallback_reply)

    def test_analyze_turn_stream_blocks_internal_reply_leak_and_uses_fallback(self):
        engine = DialogueEngine()
        fallback_reply = (
            "我已经理解你的方向了，会按参考玩法做成轻松上手的牛主题消除小游戏。"
            "还想再确认一个细节：你更希望玩家通过清空所有元素，还是撑过一轮来过关？"
        )

        async def fake_stream_reply(*, req, analysis):
            yield "用户现在要求我按照要求用简体中文回复，先理清楚，再调整下顺序。"

        async def collect_events():
            items = []
            async for item in engine.analyze_turn_stream(
                AnalyzeDialogueTurnRequest(
                    session_id="creation-stream-leak-guard",
                    user_id="user-stream-leak-guard",
                    conversation=[
                        ConversationMessage(role="user", content="帮我做一个类似羊了个羊，但把羊换成牛的小游戏"),
                    ],
                    initial_prompt="帮我做一个类似羊了个羊，但把羊换成牛的小游戏",
                )
            ):
                items.append(item)
            return items

        fake_analysis = AnalyzeDialogueTurnResponse(
            reply=fallback_reply,
            slots=SlotState(
                game_type="puzzle",
                core_mechanic="tap to match and clear layers",
                theme="cattle ranch",
                input_method="tap",
                win_condition="clear all layers",
                difficulty="medium",
                reference_game="羊了个羊",
            ),
            slots_updated=["game_type", "core_mechanic", "theme", "reference_game"],
            missing_required=["win_condition"],
            slot_fill_pct=0.9,
            ready_to_generate=False,
            current_question=DialogueQuestion(
                slot_key="win_condition",
                label="Win Condition",
                prompt="你更希望玩家通过清空所有元素，还是撑过一轮来过关？",
                skippable=True,
            ),
        )

        with patch.object(engine, "analyze_turn", new=AsyncMock(return_value=fake_analysis)), patch.object(
            engine,
            "_stream_analyze_turn_reply",
            new=fake_stream_reply,
        ):
            events = asyncio.run(collect_events())

        joined_deltas = "".join(item["data"]["delta"] for item in events if item["event"] == "delta")
        self.assertEqual(events[0]["event"], "final")
        self.assertEqual(events[-1]["event"], "done")
        self.assertEqual(events[-1]["data"]["message"], fallback_reply)
        self.assertEqual(events[0]["data"]["reply"], fallback_reply)
        self.assertNotIn("用户现在要求我", joined_deltas)
        self.assertNotIn("先理清楚", joined_deltas)
        self.assertNotIn("调整下顺序", joined_deltas)

    def test_dialogue_stream_endpoint_emits_sse_events(self):
        async def fake_stream(_request):
            yield {
                "event": "delta",
                "data": {
                    "delta": "浜嗚В锛?",
                    "accumulated": "浜嗚В锛?",
                    "kind": "question",
                },
            }
            yield {
                "event": "done",
                "data": {
                    "message": "浜嗚В锛屾垜鍏堢‘璁や竴涓嬭儨鍒╂柟寮忋€?",
                    "kind": "question",
                },
            }
            yield {
                "event": "final",
                "data": {
                    "reply": "浜嗚В锛屾垜鍏堢‘璁や竴涓嬭儨鍒╂柟寮忋€?",
                    "slots": {
                        "game_type": "funny",
                    },
                    "slots_updated": ["game_type"],
                    "missing_required": ["win_condition"],
                    "slot_fill_pct": 0.33,
                    "ready_to_generate": False,
                },
            }

        with patch.object(generate_api._dialogue_engine, "analyze_turn_stream", side_effect=fake_stream):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/ai/dialogue/analyze-turn/stream",
                    json={
                        "session_id": "creation-stream-api",
                        "user_id": "user-stream-api",
                        "initial_prompt": "鍋氫竴涓姙鍏鎽搁奔娓告垙",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))
        self.assertIn("event: delta", response.text)
        self.assertIn("event: done", response.text)
        self.assertIn("event: final", response.text)


if __name__ == "__main__":
    unittest.main()
