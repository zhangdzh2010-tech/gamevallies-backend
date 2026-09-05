"""Shared constants and value types for CodeGenerator."""

from __future__ import annotations
import re
from typing import Dict

class _SafePromptFormatDict(dict):
    """Preserve unknown placeholders instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


GAME_TYPE_CORE_MECHANIC_SUMMARY: Dict[str, str] = {
    "casual": "Deliver one readable arcade loop with quick feedback and a clear round goal.",
    "puzzle": "Solve a compact logic or board problem with clear player feedback.",
    "educational": "Teach or reinforce one learning objective through a short interactive challenge.",
    "funny": "Build around one surprising or comedic interaction that stays readable on mobile.",
}


LOCALIZED_CORE_MECHANIC_SUMMARY: Dict[str, Dict[str, str]] = {
    "zh-CN": {
        "casual": "用简洁清晰的休闲玩法循环，让反馈快、目标明确。",
        "puzzle": "通过紧凑的逻辑或棋盘挑战来完成目标。",
        "educational": "把学习目标变成一个短平快的交互挑战。",
        "funny": "围绕一个好懂又有梗的搞笑交互展开。",
    },
}


UI_LANGUAGE_LABELS: Dict[str, str] = {
    "en-US": "English",
    "zh-CN": "Simplified Chinese",
}


EDUCATIONAL_REQUEST_MARKERS: tuple[str, ...] = (
    "classroom",
    "teacher",
    "lesson",
    "quiz",
    "worksheet",
    "practice",
    "practice question",
    "learning game",
    "teaching",
    "knowledge point",
    "课堂",
    "教学",
    "老师",
    "练习题",
    "知识点",
    "问答",
    "测验",
    "小测",
    "学习游戏",
    "教学游戏",
)


PLAYER_SIZE_BY_GAME_TYPE: Dict[str, tuple[int, int]] = {
    "casual": (40, 40),
    "puzzle": (56, 56),
    "educational": (52, 52),
    "funny": (44, 44),
}


_GENERIC_SPECIAL_RULE_PHRASES: tuple[str, ...] = (
    "restart after losing",
    "restart available",
    "restart available anytime",
    "restart button",
    "restart function",
    "click to start",
    "tap to start",
    "start hint",
    "win state",
    "lose state",
    "victory screen",
    "failure screen",
    "failure settlement",
    "game over screen",
    "show final score",
    "novice operation prompts",
    "operation prompts",
    "clear novice operation prompts",
    "provide clear novice operation prompts",
    "点击开始",
    "开始提示",
    "失败后可以重新开始",
    "重新开始按钮",
    "重新开始功能",
    "失败页",
    "胜利页",
    "失败结算",
    "新手提示",
    "关卡提示",
    "阶段进度",
    "剩余步数",
)


_STANDARD_COMPLEXITY_KEYWORDS: tuple[str, ...] = (
    "combo",
    "meter",
    "wave",
    "waves",
    "projectile",
    "projectiles",
    "shoot",
    "shooter",
    "boss",
    "attack",
    "enemy",
    "enemies",
    "stage progress",
    "health bar",
    "combo meter",
    "三波",
    "波次",
    "boss",
    "攻击",
    "子弹",
    "敌人",
    "血量",
    "进度",
)


_COMPLEX_COMPLEXITY_KEYWORDS: tuple[str, ...] = (
    "boss",
    "mini boss",
    "three waves",
    "consecutive enemy waves",
    "energy is used to attack",
    "defeat all",
    "escort",
    "rescue",
    "quiz",
    "classroom",
    "worksheet",
    "pathfinding",
    "route planning",
    "inventory",
    "craft",
    "procedural",
    "mini boss",
    "击败",
    "boss",
    "三波敌人",
    "能量",
    "课堂",
    "问答",
)


def _extract_html(text: str) -> str:
    """Extract clean HTML from LLM output."""
    text = text.lstrip("\ufeff")
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"```(?:html)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*(?:$|\n)", "", text, flags=re.MULTILINE)
    match = re.search(r"(<!DOCTYPE\s+html|<html)", text, re.IGNORECASE)
    if match:
        text = text[match.start():]
    return text.strip()


_STYLE_FEEDBACK_KEYWORDS = (
    "color", "colour", "font", "background", "border",
    "css", "dark mode", "light mode",
    "颜色", "背景", "字体", "主题色", "深色模式", "浅色模式",
)


def _feedback_involves_style(feedback: str) -> bool:
    """Check if feedback mentions visual/CSS concerns."""
    lower = feedback.lower()
    return any(keyword in lower for keyword in _STYLE_FEEDBACK_KEYWORDS)


_MARKUP_FEEDBACK_KEYWORDS = (
    "hud", "button", "overlay", "menu", "title", "label", "scoreboard",
    "score hud", "ui", "layout", "panel", "text", "tutorial",
    "buttons", "title screen", "restart button",
    "按钮", "标题", "文本", "布局", "面板", "教程", "菜单", "分数",
)


def _feedback_involves_markup(feedback: str) -> bool:
    lower = feedback.lower()
    return any(keyword in lower for keyword in _MARKUP_FEEDBACK_KEYWORDS)


def _extract_code_block(text: str) -> str:
    """Extract content from markdown code fences, or return text as-is."""
    text = text.lstrip("\ufeff")
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"```(?:javascript|js|css|html)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*(?:$|\n)", "", text, flags=re.MULTILINE)
    return text.strip()
