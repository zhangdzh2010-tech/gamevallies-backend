"""Unit tests for the user-facing expand-prompt sanitizer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.expand_prompt_sanitizer import (
    has_meaningful_brief,
    strip_user_facing_scaffolding,
)


class TestStripUserFacingScaffolding(unittest.TestCase):
    def test_returns_empty_string_for_none(self):
        self.assertEqual(strip_user_facing_scaffolding(None), "")

    def test_keeps_clean_prose_intact(self):
        text = (
            "做一款手机竖屏的植物大战僵尸：5 行绿草坪从左向右涌入一波波不同种类的僵尸，"
            "玩家点击时蔬菜卡从顶部选单摔到草地格子里种下植物。"
        )
        self.assertEqual(strip_user_facing_scaffolding(text), text)

    def test_strips_legacy_chinese_template(self):
        polluted = "\n".join(
            [
                "原始想法：以一个废弃的古堡为背景，内部藏着各种厉鬼，一个正义的道士用尽浑身解数（法术）来消灭厉鬼",
                "",
                "请把这条想法整理成一个适合移动端小游戏生成的确认稿，并至少覆盖这些要素：",
                "Game Type: 根据原始想法确定游戏方向",
                "Core Mechanic: 提炼玩家最常执行的核心动作",
                "Theme: 保留原始想法里的题材、场景或情绪",
                "Input Method: 采用适合手机的点击、滑动或拖拽操作",
                "Win Condition: 明确玩家这一局如何过关或获胜",
                "Difficulty Ramp: 说明难度如何逐步提升",
                "Scoring / Rewards: 补充积分、连击、奖励或解锁节奏",
                "Visual Direction: 给出匹配题材的视觉风格",
                "Special Rules or Reference Inspiration: 仅在确有帮助时补充",
            ]
        )
        cleaned = strip_user_facing_scaffolding(polluted)
        self.assertEqual(cleaned, "")
        self.assertFalse(has_meaningful_brief(cleaned))

    def test_strips_english_template(self):
        polluted = "\n".join(
            [
                "Original Idea: Make a cozy fruit merge game for mobile.",
                "",
                "Please turn this brief into a mobile-friendly game generation prompt "
                "that covers at least these elements:",
                "Game Type: Choose the most fitting direction from the original idea",
                "Core Mechanic: Describe the main repeated player action",
                "Theme: Preserve the setting, fantasy, or mood implied by the brief",
            ]
        )
        cleaned = strip_user_facing_scaffolding(polluted)
        self.assertEqual(cleaned, "")

    def test_keeps_prose_around_dropped_lines(self):
        polluted = (
            "原始想法：以一只猫为主角\n"
            "做一款手机猫咪跑酷小游戏，玩家在屋顶之间不断跳跃。\n"
            "Game Type: casual\n"
            "整体节奏要轻松，偶尔有一些惊险。"
        )
        cleaned = strip_user_facing_scaffolding(polluted)
        self.assertIn("做一款手机猫咪跑酷小游戏", cleaned)
        self.assertIn("整体节奏要轻松", cleaned)
        self.assertNotIn("原始想法", cleaned)
        self.assertNotIn("Game Type", cleaned)

    def test_chinese_label_lines_dropped(self):
        polluted = (
            "我们做一款消除小游戏。\n"
            "游戏类型：益智解谜\n"
            "核心玩法：三消\n"
            "整体氛围温馨可爱。"
        )
        cleaned = strip_user_facing_scaffolding(polluted)
        self.assertIn("我们做一款消除小游戏", cleaned)
        self.assertIn("整体氛围温馨可爱", cleaned)
        self.assertNotIn("游戏类型", cleaned)
        self.assertNotIn("核心玩法", cleaned)

    def test_does_not_drop_normal_sentence_with_colon(self):
        text = (
            "Reward feel: every successful merge triggers a soft chime, gentle camera "
            "zoom, and a pastel confetti burst from the jar mouth."
        )
        self.assertEqual(strip_user_facing_scaffolding(text), text)

    def test_collapses_blank_runs(self):
        text = "First line.\n\n\n\nSecond line.\n"
        self.assertEqual(
            strip_user_facing_scaffolding(text),
            "First line.\n\nSecond line.",
        )

    def test_idempotent(self):
        text = "做一款手机竖屏的小游戏，玩家通过点击屏幕收集星星。"
        once = strip_user_facing_scaffolding(text)
        twice = strip_user_facing_scaffolding(once)
        self.assertEqual(once, twice)


class TestHasMeaningfulBrief(unittest.TestCase):
    def test_blank_strings_are_not_meaningful(self):
        self.assertFalse(has_meaningful_brief(""))
        self.assertFalse(has_meaningful_brief("   \n  "))
        self.assertFalse(has_meaningful_brief(None))

    def test_short_text_is_not_meaningful(self):
        self.assertFalse(has_meaningful_brief("一句话。"))

    def test_longer_text_is_meaningful(self):
        self.assertTrue(
            has_meaningful_brief(
                "做一款手机竖屏的小游戏，玩家通过连续点击屏幕收集星星，"
                "关卡会越来越密集，整体氛围温馨可爱。"
            )
        )


if __name__ == "__main__":
    unittest.main()
