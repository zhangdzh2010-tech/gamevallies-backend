"""Focused regression coverage for the active intent-parse engine."""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.dialogue_engine import (
    DialogueEngine,
    _infer_game_type_from_sparse_context,
    _infer_slots_from_text,
    _infer_theme_from_context,
    _intent_parse_overall_timeout_s,
    _intent_parse_request_timeout_s,
    _normalize_game_type_label,
    _normalize_slot_payload,
    _normalize_slot_text_value,
    _safe_parse_json,
)
from src.services.llm_client import LLMResponseTruncatedError


def test_explicit_freeform_theme_never_inherits_unrelated_random_scenery():
    from src.engine.dialogue_engine import _select_visual_variant
    for seed in ['one', 'two', 'three']:
        visual = _select_visual_variant('casual', explicit_theme='靛蓝夜空、月牙、远山',
            explicit_art_style='Canvas程序绘图', source_description='主色 #121234', variation_seed=seed)
        assert visual['background'] == '靛蓝夜空、月牙、远山'
        assert visual['palette'] == ['#121234']
        assert visual['effects'] == []


def test_explicit_actors_survive_slot_normalization_and_spec_build():
    from src.api.models import SlotState
    from src.engine.dialogue_engine import _build_game_spec
    actors = [
        {'name': '小船', 'role': 'player', 'shape': 'curved hull with deck and rim', 'color': 'indigo'},
        {'name': '金色星星', 'role': 'collectible', 'shape': 'five pointed star', 'color': 'gold'},
        {'name': '紫色陨石', 'role': 'obstacle', 'shape': 'rock with flaming tail', 'color': 'purple'},
    ]
    normalized = _normalize_slot_payload({'game_type': 'casual', 'entities': actors})
    spec = _build_game_spec(SlotState(**normalized), source_description='移动小船接住金色星星并避开紫色陨石')
    assert [e.name for e in spec.entities] == ['小船', '金色星星', '紫色陨石']
    assert spec.entities[0].shape == actors[0]['shape']
    assert 'entities' not in _normalize_slot_payload({'entities': [None, {'role': 'player'}, {'name': 'x', 'role': 'invalid'}]})

TEST_PROMPTS = {
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


def fake_get_prompt(key: str, default=None):
    if key == "prompt.intent_parse_system":
        return "INTENT_PARSE_PROMPT_FROM_DB"
    if key == "prompt.slot_json_repair_system":
        return "SLOT_JSON_REPAIR_PROMPT_FROM_DB"
    return TEST_PROMPTS.get(key, default)


class TestDialogueEngine(unittest.TestCase):
    def test_normalize_slot_text_value_prefers_primary_control_and_progressive_difficulty(self):
        self.assertEqual(
            _normalize_slot_text_value(
                "input_method",
                "主要是点击选项，不做拖拽，滑动只是轻微辅助",
            ),
            "tap",
        )
        self.assertEqual(
            _normalize_slot_text_value(
                "difficulty",
                "前几题比较轻松，后面逐步变难",
            ),
            "progressive",
        )

    def test_infer_slots_from_reference_game_applies_reference_defaults(self):
        inferred = _infer_slots_from_text("做一个像 Temple Run 那种跑酷小游戏")

        self.assertEqual(inferred.get("reference_game"), "Temple Run")
        self.assertEqual(inferred.get("game_type"), "casual")
        self.assertEqual(inferred.get("input_method"), "swipe")

    def test_infer_slots_from_long_action_prompt_stays_casual_without_false_reference_defaults(self):
        inferred = _infer_slots_from_text(
            "做一个竖屏跑酷战斗小游戏。点击开始后玩家通过左右滑动切换跑道、上滑跳跃、下滑滑铲，"
            "途中需要躲避障碍、收集能量、击败一个小 Boss，最终在 60 秒内通关。"
        )

        self.assertEqual(inferred.get("game_type"), "casual")
        self.assertIsNone(inferred.get("reference_game"))

    def test_infer_theme_from_context_ignores_single_character_false_positive_markers(self):
        self.assertNotEqual(_infer_theme_from_context("做一个接水果游戏"), "ocean")

    def test_sparse_educational_context_prefers_educational(self):
        game_type = _infer_game_type_from_sparse_context(
            "浮力的故事",
            "课堂互动小游戏，帮助老师讲解浮力知识点，并附练习题。",
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

    def test_normalize_slot_payload_humanizes_machine_like_values(self):
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

    def test_normalize_game_type_preserves_curated_action_direction_without_explicit_funny_marker(self):
        normalized = _normalize_game_type_label(
            "casual",
            "做一个竖屏跑酷战斗小游戏。玩家要击败一个小 Boss 并在 60 秒内通关。",
        )

        self.assertEqual(normalized, "casual")

    def test_normalize_game_type_respects_explicit_funny_request_even_when_parser_returns_casual(self):
        normalized = _normalize_game_type_label(
            "casual",
            "做一个搞笑办公室小游戏，玩家点击摸鱼并躲开老板巡查。",
        )

        self.assertEqual(normalized, "funny")

    def test_safe_parse_json_extracts_embedded_object_from_verbose_text(self):
        parsed = _safe_parse_json(
            '分析如下：先理解用户意图。\n{"game_type":"casual","core_mechanic":"躲避障碍","theme":"space"}\n以上是结果。'
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["game_type"], "casual")
        self.assertEqual(parsed["theme"], "space")

    def test_parse_description_to_spec_uses_primary_model_not_fast_model(self):
        engine = DialogueEngine()

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
        self.assertEqual(kwargs["request_timeout_s"], _intent_parse_request_timeout_s())
        self.assertEqual(kwargs["overall_timeout_s"], _intent_parse_overall_timeout_s())
        self.assertIn("INTENT_PARSE_PROMPT_FROM_DB", kwargs["system"])
        self.assertIn("NON-NEGOTIABLE OUTPUT CONTRACT", kwargs["system"])

    def test_sparse_parse_adds_diversity_rules_for_open_briefs(self):
        engine = DialogueEngine()

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
                    "请围绕浮力知识点设计一个课堂小游戏，包含 8 道配套练习题和计分方式。",
                )
            )

        self.assertEqual(spec.game_type, "educational")
        self.assertEqual(spec.platform_constraints.input_mode, "touch")

    def test_parse_description_to_spec_salvages_truncated_intent_parse_excerpt(self):
        engine = DialogueEngine()
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
        self.assertEqual(kwargs["max_tokens"], 1800)

    def test_parse_description_to_spec_retries_timeout_with_dedicated_intent_parse_budget(self):
        engine = DialogueEngine()

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
        self.assertEqual(retry_kwargs["request_timeout_s"], _intent_parse_request_timeout_s())
        self.assertEqual(retry_kwargs["overall_timeout_s"], _intent_parse_overall_timeout_s())
        self.assertEqual(retry_kwargs["timeout_retry_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
