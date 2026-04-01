"""Stage 01 + 02: Dialogue engine and single-shot intent parsing."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..api.models import (
    AnalyzeDialogueTurnRequest,
    AnalyzeDialogueTurnResponse,
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    CoreMechanic,
    DraftPlanFromInputRequest,
    DraftPlanFromInputResponse,
    DialogueQuestion,
    DialogueSession,
    DialogueState,
    GameEntity,
    GameRules,
    GameSpec,
    PlanDraft,
    PlatformConstraints,
    QuestionStrategy,
    SpecFromSlotsRequest,
    SpecFromSlotsResponse,
    SlotState,
    VisualStyle,
)
from ..config.settings import settings
from ..services.llm_client import LLMClient, LLMResponseTruncatedError
from .prompt_store import require_prompt

logger = logging.getLogger(__name__)

_sessions: Dict[str, DialogueSession] = {}

CURATED_GAME_TYPES = ("casual", "puzzle", "educational", "funny")
FAST_DIALOGUE_SLOT_REQUEST_TIMEOUT_S = max(
    1,
    int(getattr(settings, "DIALOGUE_SLOT_REQUEST_TIMEOUT_S", 4) or 4),
)
FAST_DIALOGUE_SLOT_OVERALL_TIMEOUT_S = max(
    FAST_DIALOGUE_SLOT_REQUEST_TIMEOUT_S,
    int(getattr(settings, "DIALOGUE_SLOT_OVERALL_TIMEOUT_S", 5) or 5),
)

LOCALIZED_GAME_TYPE_DEFAULTS: Dict[str, Dict[str, Dict[str, str]]] = {
    "casual": {
        "en-US": {
            "core_mechanic": "Use one readable arcade loop with fast feedback and a clear short-term goal.",
            "win_condition": "Beat the target score or complete the short challenge.",
            "input_method": "touch",
        },
        "zh-CN": {
            "core_mechanic": "\u7528\u4e00\u4e2a\u76f4\u89c2\u7684\u4f11\u95f2\u73a9\u6cd5\u5faa\u73af\uff0c\u4fdd\u6301\u53cd\u9988\u5feb\u3001\u76ee\u6807\u6e05\u6670\u3002",
            "win_condition": "\u8fbe\u6210\u76ee\u6807\u5206\u6570\u6216\u5b8c\u6210\u4e00\u8f6e\u77ed\u6311\u6218\u3002",
            "input_method": "touch",
        },
    },
    "puzzle": {
        "en-US": {
            "core_mechanic": "Solve one compact logic or board challenge through tap or drag interactions.",
            "win_condition": "Clear the board or satisfy the puzzle objective.",
            "input_method": "touch",
        },
        "zh-CN": {
            "core_mechanic": "\u901a\u8fc7\u70b9\u51fb\u6216\u62d6\u62fd\u89e3\u5f00\u4e00\u4e2a\u7d27\u51d1\u7684\u76ca\u667a\u6311\u6218\u3002",
            "win_condition": "\u6e05\u7a7a\u68cb\u76d8\u6216\u8fbe\u6210\u8c1c\u9898\u76ee\u6807\u3002",
            "input_method": "touch",
        },
    },
    "educational": {
        "en-US": {
            "core_mechanic": "Turn the learning goal into one mobile-friendly challenge with immediate feedback.",
            "win_condition": "Complete the learning objective with a short series of correct answers or actions.",
            "input_method": "tap",
        },
        "zh-CN": {
            "core_mechanic": "\u628a\u5b66\u4e60\u76ee\u6807\u8f6c\u6210\u4e00\u4e2a\u79fb\u52a8\u7aef\u53cb\u597d\u7684\u4ea4\u4e92\u6311\u6218\uff0c\u5e76\u7acb\u5373\u7ed9\u51fa\u53cd\u9988\u3002",
            "win_condition": "\u901a\u8fc7\u7b80\u77ed\u7684\u9898\u76ee\u6216\u64cd\u4f5c\u5b8c\u6210\u6559\u80b2\u76ee\u6807\u3002",
            "input_method": "tap",
        },
    },
    "funny": {
        "en-US": {
            "core_mechanic": "Build around one surprising or comedic interaction that stays easy to understand.",
            "win_condition": "Land enough funny moments, combos, or progress beats to finish the round.",
            "input_method": "touch",
        },
        "zh-CN": {
            "core_mechanic": "\u56f4\u7ed5\u4e00\u4e2a\u597d\u61c2\u53c8\u6709\u6897\u7684\u641e\u7b11\u4ea4\u4e92\u5c55\u5f00\u3002",
            "win_condition": "\u5728\u4e00\u5c40\u5185\u5b8c\u6210\u8db3\u591f\u7684\u7b11\u70b9\u3001\u8fde\u51fb\u6216\u8fdb\u5ea6\u76ee\u6807\u3002",
            "input_method": "touch",
        },
    },
}

ENTITY_VARIANTS: Dict[str, List[List[Dict[str, Any]]]] = {
    "casual": [
        [
            {"name": "glider", "role": "player", "shape": "triangle", "color": "#6366f1"},
            {"name": "meteor", "role": "obstacle", "shape": "diamond", "color": "#f43f5e"},
            {"name": "beacon", "role": "collectible", "shape": "circle", "color": "#22c55e"},
        ],
        [
            {"name": "hopper", "role": "player", "shape": "circle", "color": "#0ea5e9"},
            {"name": "vine", "role": "obstacle", "shape": "rectangle", "color": "#f97316"},
            {"name": "seed", "role": "collectible", "shape": "diamond", "color": "#84cc16"},
        ],
        [
            {"name": "skater", "role": "player", "shape": "square", "color": "#8b5cf6"},
            {"name": "barrier", "role": "obstacle", "shape": "rectangle", "color": "#ef4444"},
            {"name": "spark", "role": "collectible", "shape": "circle", "color": "#facc15"},
        ],
    ],
    "puzzle": [
        [
            {"name": "piece", "role": "player", "shape": "square", "color": "#6366f1"},
            {"name": "goal", "role": "collectible", "shape": "square", "color": "#22c55e"},
        ],
        [
            {"name": "tile", "role": "player", "shape": "diamond", "color": "#0ea5e9"},
            {"name": "target", "role": "collectible", "shape": "circle", "color": "#f97316"},
        ],
        [
            {"name": "node", "role": "player", "shape": "circle", "color": "#8b5cf6"},
            {"name": "marker", "role": "collectible", "shape": "rectangle", "color": "#14b8a6"},
        ],
    ],
    "educational": [
        [
            {"name": "student", "role": "player", "shape": "circle", "color": "#6366f1"},
            {"name": "prompt", "role": "collectible", "shape": "rectangle", "color": "#22c55e"},
            {"name": "hint", "role": "obstacle", "shape": "diamond", "color": "#f59e0b"},
        ],
        [
            {"name": "card", "role": "player", "shape": "rectangle", "color": "#0ea5e9"},
            {"name": "answer", "role": "collectible", "shape": "circle", "color": "#eab308"},
            {"name": "timer", "role": "obstacle", "shape": "triangle", "color": "#ef4444"},
        ],
        [
            {"name": "teacher", "role": "player", "shape": "square", "color": "#8b5cf6"},
            {"name": "badge", "role": "collectible", "shape": "diamond", "color": "#10b981"},
            {"name": "mistake", "role": "obstacle", "shape": "circle", "color": "#ef4444"},
        ],
    ],
    "funny": [
        [
            {"name": "office_hero", "role": "player", "shape": "square", "color": "#6366f1"},
            {"name": "boss_call", "role": "obstacle", "shape": "rectangle", "color": "#f43f5e"},
            {"name": "snack", "role": "collectible", "shape": "circle", "color": "#22c55e"},
        ],
        [
            {"name": "goose", "role": "player", "shape": "circle", "color": "#06b6d4"},
            {"name": "banana_peel", "role": "obstacle", "shape": "diamond", "color": "#ef4444"},
            {"name": "cheer", "role": "collectible", "shape": "rectangle", "color": "#f59e0b"},
        ],
        [
            {"name": "cat", "role": "player", "shape": "triangle", "color": "#8b5cf6"},
            {"name": "vacuum", "role": "obstacle", "shape": "square", "color": "#fb7185"},
            {"name": "meme_star", "role": "collectible", "shape": "diamond", "color": "#10b981"},
        ],
    ],
}

GENERIC_ENTITY_VARIANTS: List[List[Dict[str, Any]]] = [
    [
        {"name": "player", "role": "player", "shape": "circle", "color": "#6366f1"},
        {"name": "hazard", "role": "obstacle", "shape": "square", "color": "#f43f5e"},
        {"name": "goal", "role": "collectible", "shape": "diamond", "color": "#22c55e"},
    ],
    [
        {"name": "player", "role": "player", "shape": "diamond", "color": "#0ea5e9"},
        {"name": "hazard", "role": "obstacle", "shape": "triangle", "color": "#f97316"},
        {"name": "goal", "role": "collectible", "shape": "circle", "color": "#eab308"},
    ],
    [
        {"name": "player", "role": "player", "shape": "square", "color": "#8b5cf6"},
        {"name": "hazard", "role": "obstacle", "shape": "rectangle", "color": "#ef4444"},
        {"name": "goal", "role": "collectible", "shape": "circle", "color": "#14b8a6"},
    ],
]

THEME_STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "space": {"theme": "space", "palette": ["#081028", "#60a5fa", "#22c55e", "#f97316", "#f8fafc"], "background": "starfield", "art_style": "geometric", "effects": ["glow"]},
    "zoo": {"theme": "zoo", "palette": ["#14532d", "#22c55e", "#f59e0b", "#ef4444", "#fefce8"], "background": "park", "art_style": "cartoon", "effects": []},
    "neon": {"theme": "neon", "palette": ["#111827", "#8b5cf6", "#06b6d4", "#f43f5e", "#f8fafc"], "background": "dark_gradient", "art_style": "neon", "effects": ["glow"]},
    "fantasy": {"theme": "fantasy", "palette": ["#312e81", "#8b5cf6", "#22c55e", "#f59e0b", "#fef3c7"], "background": "mist", "art_style": "storybook", "effects": ["sparkles"]},
    "ocean": {"theme": "ocean", "palette": ["#0f172a", "#0ea5e9", "#14b8a6", "#facc15", "#ecfeff"], "background": "waves", "art_style": "soft_geometric", "effects": ["bubbles"]},
    "forest": {"theme": "forest", "palette": ["#14532d", "#22c55e", "#84cc16", "#f59e0b", "#f7fee7"], "background": "canopy", "art_style": "storybook", "effects": ["leaf_trails"]},
    "city": {"theme": "city", "palette": ["#1f2937", "#38bdf8", "#a3e635", "#fb7185", "#f9fafb"], "background": "skyline", "art_style": "flat", "effects": ["speed_lines"]},
    "garden": {"theme": "garden", "palette": ["#365314", "#4ade80", "#f472b6", "#facc15", "#fefce8"], "background": "meadow", "art_style": "soft_geometric", "effects": ["petals"]},
    "food": {"theme": "food", "palette": ["#7c2d12", "#fb923c", "#facc15", "#ef4444", "#fff7ed"], "background": "tabletop", "art_style": "cartoon", "effects": []},
    "candy": {"theme": "candy", "palette": ["#831843", "#f472b6", "#38bdf8", "#facc15", "#fff1f2"], "background": "sweetscape", "art_style": "playful", "effects": ["sparkles"]},
    "sports": {"theme": "sports", "palette": ["#0f172a", "#22c55e", "#38bdf8", "#f97316", "#f8fafc"], "background": "arena", "art_style": "bold_flat", "effects": ["streaks"]},
    "toy": {"theme": "toy", "palette": ["#1d4ed8", "#60a5fa", "#f97316", "#ef4444", "#fefce8"], "background": "playroom", "art_style": "playful", "effects": []},
}

VISUAL_VARIANTS_BY_GAME_TYPE: Dict[str, List[Dict[str, Any]]] = {
    "casual": [
        {"theme": "garden", "palette": ["#365314", "#4ade80", "#f472b6", "#facc15", "#fefce8"], "background": "meadow", "art_style": "soft_geometric", "effects": ["petals"]},
        {"theme": "city", "palette": ["#1f2937", "#38bdf8", "#a3e635", "#fb7185", "#f9fafb"], "background": "skyline", "art_style": "flat", "effects": ["speed_lines"]},
        {"theme": "toy", "palette": ["#1d4ed8", "#60a5fa", "#f97316", "#ef4444", "#fefce8"], "background": "playroom", "art_style": "playful", "effects": []},
    ],
    "puzzle": [
        {"theme": "candy", "palette": ["#831843", "#f472b6", "#38bdf8", "#facc15", "#fff1f2"], "background": "sweetscape", "art_style": "playful", "effects": ["sparkles"]},
        {"theme": "garden", "palette": ["#365314", "#4ade80", "#f472b6", "#facc15", "#fefce8"], "background": "meadow", "art_style": "soft_geometric", "effects": ["petals"]},
        {"theme": "toy", "palette": ["#1d4ed8", "#60a5fa", "#f97316", "#ef4444", "#fefce8"], "background": "playroom", "art_style": "playful", "effects": []},
    ],
    "educational": [
        {"theme": "ocean", "palette": ["#0f172a", "#0ea5e9", "#14b8a6", "#facc15", "#ecfeff"], "background": "waves", "art_style": "soft_geometric", "effects": ["bubbles"]},
        {"theme": "forest", "palette": ["#14532d", "#22c55e", "#84cc16", "#f59e0b", "#f7fee7"], "background": "canopy", "art_style": "storybook", "effects": ["leaf_trails"]},
        {"theme": "toy", "palette": ["#1d4ed8", "#60a5fa", "#f97316", "#ef4444", "#fefce8"], "background": "playroom", "art_style": "playful", "effects": []},
    ],
    "funny": [
        {"theme": "neon", "palette": ["#111827", "#8b5cf6", "#06b6d4", "#f43f5e", "#f8fafc"], "background": "dark_gradient", "art_style": "neon", "effects": ["glow"]},
        {"theme": "city", "palette": ["#1f2937", "#38bdf8", "#a3e635", "#fb7185", "#f9fafb"], "background": "skyline", "art_style": "flat", "effects": ["speed_lines"]},
        {"theme": "food", "palette": ["#7c2d12", "#fb923c", "#facc15", "#ef4444", "#fff7ed"], "background": "tabletop", "art_style": "cartoon", "effects": []},
    ],
}

SPARSE_REQUEST_MARKERS = (
    "continue",
    "another",
    "more",
    "add",
    "increase",
    "update",
    "adjust",
    "modify",
    "tweak",
    "improve",
    "expand",
    "set to",
    "\u7ee7\u7eed",
    "\u518d",
    "\u65b0\u589e",
    "\u6dfb\u52a0",
    "\u589e\u52a0",
    "\u6269\u5c55",
    "\u8bbe\u7f6e",
    "\u8c03\u6574",
    "\u4fee\u6539",
    "\u4f18\u5316",
    "\u5173\u5361",
)

EDUCATIONAL_REQUEST_MARKERS = (
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
    "study guide",
    "\u8bfe\u5802",
    "\u6559\u5b66",
    "\u8001\u5e08",
    "\u7ec3\u4e60\u9898",
    "\u7ec3\u4e60",
    "\u77e5\u8bc6\u70b9",
    "\u95ee\u7b54",
    "\u6d4b\u9a8c",
    "\u5c0f\u6d4b",
    "\u6559\u5177",
    "\u5b66\u4e60\u6e38\u620f",
    "\u6559\u5b66\u6e38\u620f",
)

FUNNY_REQUEST_MARKERS = (
    "funny",
    "comedy",
    "joke",
    "jokes",
    "meme",
    "memes",
    "parody",
    "goofy",
    "silly",
    "prank",
    "\u641e\u7b11",
    "\u6076\u641e",
    "\u6574\u6d3b",
    "\u6897",
    "\u5e7d\u9ed8",
)

PUZZLE_REQUEST_MARKERS = (
    "puzzle",
    "match",
    "merge",
    "sort",
    "solve",
    "logic",
    "2048",
    "sudoku",
    "circuit",
    "wire",
    "battery",
    "bulb",
    "switch",
    "connect",
    "assemble",
    "\u8c1c\u9898",
    "\u76ca\u667a",
    "\u6d88\u9664",
    "\u914d\u5bf9",
    "\u8fde\u7ebf",
    "\u7535\u8def",
    "\u5bfc\u7ebf",
    "\u5f00\u5173",
    "\u7ec4\u88c5",
)

CASUAL_REQUEST_MARKERS = (
    "casual",
    "arcade",
    "action",
    "run",
    "race",
    "chase",
    "catch",
    "avoid",
    "survive",
    "escape",
    "jump",
    "platform",
    "shoot",
    "tap",
    "drag",
    "\u4f11\u95f2",
    "\u8857\u673a",
    "\u8dd1",
    "\u8ffd",
    "\u6293",
    "\u8df3",
    "\u95ea",
    "\u907f",
    "\u751f\u5b58",
    "\u5c04\u51fb",
)

LEGACY_GAME_TYPE_ALIASES = {
    "dodge": "casual",
    "runner": "casual",
    "platformer": "casual",
    "shooter": "casual",
    "rhythm": "funny",
    "tower_defense": "puzzle",
    "idle": "casual",
    "rpg": "casual",
    "lane runner": "casual",
    "endless runner": "casual",
    "top down shooter": "casual",
    "top-down shooter": "casual",
}

CONTEXT_GAME_TYPE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("educational", EDUCATIONAL_REQUEST_MARKERS),
    ("funny", FUNNY_REQUEST_MARKERS),
    ("puzzle", PUZZLE_REQUEST_MARKERS),
    ("casual", CASUAL_REQUEST_MARKERS),
)

CONTEXT_THEME_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("space", ("space", "galaxy", "star", "planet", "\u592a\u7a7a", "\u5b87\u5b99", "\u661f")),
    ("zoo", ("animal", "zoo", "pig", "cat", "dog", "panda", "\u52a8\u7269", "\u5c0f\u732a", "\u732a", "\u732b", "\u72d7", "\u718a\u732b")),
    ("ocean", ("ocean", "sea", "water", "fish", "\u6d77", "\u6d0b", "\u6c34", "\u9c7c")),
    ("forest", ("forest", "jungle", "tree", "\u68ee\u6797", "\u4e1b\u6797", "\u6811")),
    ("city", ("city", "street", "car", "traffic", "\u57ce\u5e02", "\u8857", "\u6c7d\u8f66", "\u4ea4\u901a")),
    ("food", ("food", "kitchen", "chef", "candy", "dessert", "\u98df\u7269", "\u53a8\u623f", "\u7cd6", "\u751c\u54c1")),
    ("toy", ("toy", "block", "brick", "\u73a9\u5177", "\u79ef\u6728", "\u65b9\u5757")),
    ("fantasy", ("fantasy", "magic", "dragon", "\u5947\u5e7b", "\u9b54\u6cd5", "\u9f99")),
)

EXPLICIT_GAME_TYPE_MARKERS: Dict[str, Tuple[str, ...]] = {
    "casual": ("casual", "arcade", "\u4f11\u95f2", "\u8857\u673a"),
    "puzzle": ("puzzle", "logic", "brain teaser", "\u8c1c\u9898", "\u76ca\u667a"),
    "educational": ("educational", "education", "learning game", "quiz game", "\u6559\u80b2", "\u5b66\u4e60", "\u95ee\u7b54"),
    "funny": ("funny", "comedy", "meme", "\u641e\u7b11", "\u6076\u641e"),
}

SPARSE_GAME_TYPE_VARIANTS: Dict[str, Tuple[str, ...]] = {
    "casual": ("casual", "funny", "puzzle"),
    "puzzle": ("puzzle", "casual", "educational"),
    "educational": ("educational", "puzzle", "casual"),
    "funny": ("funny", "casual", "puzzle"),
}

SPARSE_DEFAULT_GAME_TYPES = CURATED_GAME_TYPES
SPARSE_DEFAULT_THEMES = ("arcade", "neon", "fantasy", "ocean", "forest", "city", "food", "toy")

SLOT_LABELS = {
    "game_type": "Game Type",
    "core_mechanic": "Core Mechanic",
    "theme": "Theme",
    "input_method": "Input Method",
    "win_condition": "Win Condition",
    "difficulty": "Difficulty",
    "visual_style": "Visual Style",
    "audio_style": "Audio Style",
}

SLOT_IMPACT_WEIGHTS: Dict[str, float] = {
    "core_mechanic": 1.0,
    "win_condition": 0.95,
    "input_method": 0.9,
    "game_type": 0.82,
    "theme": 0.74,
    "difficulty": 0.55,
    "visual_style": 0.42,
    "audio_style": 0.18,
}

ENTRY_MODE_SLOT_BIAS: Dict[str, Dict[str, float]] = {
    "create": {},
    "fork": {
        "theme": 0.08,
        "visual_style": 0.06,
        "game_type": -0.2,
        "difficulty": -0.15,
    },
    "iterate": {
        "core_mechanic": 0.12,
        "win_condition": 0.08,
        "theme": 0.04,
        "game_type": -0.22,
        "difficulty": -0.18,
    },
}

SLOT_JSON_SCHEMA = """{
  "game_type": null,
  "core_mechanic": null,
  "theme": null,
  "input_method": null,
  "win_condition": null,
  "difficulty": null,
  "visual_style": null,
  "audio_style": null,
  "special_rules": null,
  "reference_game": null
}"""

SLOT_FIELD_ALIASES = {
    "game_type": "game_type",
    "game type": "game_type",
    "游戏类型": "game_type",
    "类型": "game_type",
    "type": "game_type",
    "core_mechanic": "core_mechanic",
    "core mechanic": "core_mechanic",
    "core gameplay": "core_mechanic",
    "核心玩法": "core_mechanic",
    "玩法": "core_mechanic",
    "theme": "theme",
    "主题": "theme",
    "input_method": "input_method",
    "input method": "input_method",
    "controls": "input_method",
    "control method": "input_method",
    "输入方式": "input_method",
    "操作方式": "input_method",
    "控制方式": "input_method",
    "win_condition": "win_condition",
    "win condition": "win_condition",
    "goal": "win_condition",
    "胜利条件": "win_condition",
    "目标": "win_condition",
    "difficulty": "difficulty",
    "难度": "difficulty",
    "visual_style": "visual_style",
    "visual style": "visual_style",
    "art style": "visual_style",
    "视觉风格": "visual_style",
    "美术风格": "visual_style",
    "audio_style": "audio_style",
    "audio style": "audio_style",
    "music": "audio_style",
    "sound": "audio_style",
    "音频风格": "audio_style",
    "音乐风格": "audio_style",
    "special_rules": "special_rules",
    "special rules": "special_rules",
    "rules": "special_rules",
    "特殊规则": "special_rules",
    "规则": "special_rules",
    "reference_game": "reference_game",
    "reference game": "reference_game",
    "reference": "reference_game",
    "参考游戏": "reference_game",
    "参考": "reference_game",
}

NULLISH_TEXT = {"", "null", "none", "unknown", "n/a", "na", "not specified", "unspecified"}

# Override legacy mojibake aliases with an explicit UTF-8-safe map.
SLOT_FIELD_ALIASES = {
    "game type": "game_type",
    "type": "game_type",
    "\u6e38\u620f\u7c7b\u578b": "game_type",
    "\u7c7b\u578b": "game_type",
    "core mechanic": "core_mechanic",
    "core gameplay": "core_mechanic",
    "\u6838\u5fc3\u73a9\u6cd5": "core_mechanic",
    "\u73a9\u6cd5": "core_mechanic",
    "theme": "theme",
    "\u4e3b\u9898": "theme",
    "input method": "input_method",
    "controls": "input_method",
    "control method": "input_method",
    "\u8f93\u5165\u65b9\u5f0f": "input_method",
    "\u64cd\u4f5c\u65b9\u5f0f": "input_method",
    "\u63a7\u5236\u65b9\u5f0f": "input_method",
    "\u64cd\u63a7\u65b9\u5f0f": "input_method",
    "win condition": "win_condition",
    "goal": "win_condition",
    "\u80dc\u5229\u6761\u4ef6": "win_condition",
    "\u76ee\u6807": "win_condition",
    "difficulty": "difficulty",
    "\u96be\u5ea6": "difficulty",
    "visual style": "visual_style",
    "art style": "visual_style",
    "\u89c6\u89c9\u98ce\u683c": "visual_style",
    "\u7f8e\u672f\u98ce\u683c": "visual_style",
    "audio style": "audio_style",
    "music": "audio_style",
    "sound": "audio_style",
    "\u97f3\u9891\u98ce\u683c": "audio_style",
    "\u97f3\u4e50\u98ce\u683c": "audio_style",
    "\u97f3\u6548\u98ce\u683c": "audio_style",
    "special rules": "special_rules",
    "rules": "special_rules",
    "\u7279\u6b8a\u89c4\u5219": "special_rules",
    "\u89c4\u5219": "special_rules",
    "reference game": "reference_game",
    "reference": "reference_game",
    "\u53c2\u8003\u6e38\u620f": "reference_game",
    "\u53c2\u8003": "reference_game",
}

NULLISH_TEXT = {
    "",
    "null",
    "none",
    "unknown",
    "n/a",
    "na",
    "not specified",
    "unspecified",
    "\u65e0",
    "\u6ca1\u6709",
    "\u672a\u6307\u5b9a",
    "\u672a\u77e5",
    "\u4e0d\u786e\u5b9a",
}

NORMALIZED_INPUT_METHOD_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("swipe", ("swipe", "slide", "flick", "touch tap swipe", "touch swipe")),
    ("drag", ("drag", "dragging", "pull")),
    ("touch", ("touch only", "touch control", "touch")),
    ("tap", ("tap", "click", "touch tap")),
    ("hold", ("hold", "press", "long press")),
)

NORMALIZED_DIFFICULTY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("easy", ("easy", "relaxed", "simple", "casual")),
    ("medium", ("medium", "normal", "standard")),
    ("hard", ("hard", "difficult", "challenging")),
    ("progressive", ("progressive", "escalating", "ramping")),
)

SPARSE_REQUEST_MARKERS = (
    "continue",
    "another",
    "more",
    "add",
    "increase",
    "update",
    "adjust",
    "modify",
    "tweak",
    "improve",
    "expand",
    "set to",
    "\u7ee7\u7eed",
    "\u518d",
    "\u65b0\u589e",
    "\u6dfb\u52a0",
    "\u589e\u52a0",
    "\u6269\u5c55",
    "\u8bbe\u7f6e",
    "\u8c03\u6574",
    "\u4fee\u6539",
    "\u4f18\u5316",
    "\u5173\u5361",
)

class SlotExtractionFailure(ValueError):
    """Raised when slot extraction cannot produce a minimally valid payload."""

    def __init__(self, message: str, *, diagnostics: Dict[str, Any]) -> None:
        super().__init__(message)
        self.stage = "spec_build"
        self.retry_count = 0
        self.failure_family = "spec_build"
        self.diagnostics = diagnostics
        self.artifacts = [
            {
                "artifact_type": "spec_build_diagnostics",
                "content_type": "application/json",
                "payload": diagnostics,
                "metadata": {"stage": "spec_build"},
            }
        ]


def _with_slot_json_contract(prompt: str) -> str:
    strict_contract = "\n\n" + require_prompt("prompt.slot_output_contract").format(
        slot_json_schema=SLOT_JSON_SCHEMA,
    )
    if SLOT_JSON_SCHEMA in prompt and "NON-NEGOTIABLE OUTPUT CONTRACT" in prompt:
        return prompt
    return f"{prompt.rstrip()}{strict_contract}"


def _clean_llm_output(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text).strip()
    return text


def _normalize_slot_label(label: str) -> str:
    normalized = (label or "").strip().strip(":：-")
    normalized = normalized.replace("_", " ").replace("-", " ").replace("：", ":")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower()


def _normalize_slot_label(label: str) -> str:
    normalized = (label or "").strip()
    for marker in (":", "-", chr(0xFF1A)):
        normalized = normalized.strip(marker)
    normalized = normalized.replace("_", " ").replace("-", " ").replace(chr(0xFF1A), ":")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower()


def _extract_braced_candidates(text: str) -> List[str]:
    candidates: List[str] = []
    stack: List[int] = []
    quote_char: Optional[str] = None
    escape = False

    for index, char in enumerate(text):
        if quote_char:
            if escape:
                escape = False
                continue
            if char == "\\":
                escape = True
                continue
            if char == quote_char:
                quote_char = None
            continue

        if char in {'"', "'"}:
            quote_char = char
            continue
        if char == "{":
            stack.append(index)
            continue
        if char == "}" and stack:
            start = stack.pop()
            if not stack:
                candidates.append(text[start:index + 1])

    return candidates


def _parse_json_like_candidate(candidate: str) -> Optional[dict]:
    text = candidate.strip()
    if not text:
        return None

    text = (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    python_like = re.sub(r"\bnull\b", "None", text, flags=re.IGNORECASE)
    python_like = re.sub(r"\btrue\b", "True", python_like, flags=re.IGNORECASE)
    python_like = re.sub(r"\bfalse\b", "False", python_like, flags=re.IGNORECASE)

    try:
        parsed = ast.literal_eval(python_like)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_labeled_slot_lines(text: str) -> Optional[dict]:
    parsed: Dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = re.sub(r"^[\s>*-]+", "", raw_line).strip().replace("：", ":")
        if ":" not in line:
            continue
        raw_label, raw_value = line.split(":", 1)
        key = SLOT_FIELD_ALIASES.get(raw_label.strip().lower())
        if not key:
            key = SLOT_FIELD_ALIASES.get(raw_label.strip())
        if not key:
            continue

        value = raw_value.strip().strip(",")
        lowered = value.lower()
        if lowered in NULLISH_TEXT:
            parsed[key] = None
            continue

        if key == "special_rules":
            if value.startswith("["):
                wrapped = _parse_json_like_candidate(f'{{"special_rules": {value}}}')
                if isinstance(wrapped, dict) and "special_rules" in wrapped:
                    parsed[key] = wrapped["special_rules"]
                    continue
            rules = [
                item.strip(" -")
                for item in re.split(r"\s*[;,|、，]\s*", value)
                if item.strip(" -")
            ]
            parsed[key] = rules or [value]
            continue

        parsed[key] = value.strip().strip('"').strip("'")

    return parsed if parsed else None


def _parse_labeled_slot_lines(text: str) -> Optional[dict]:
    parsed: Dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = re.sub(r"^[\s>*\-•]+", "", raw_line).strip()
        line = line.replace("\uFF1A", ":")
        if ":" not in line:
            continue

        raw_label, raw_value = line.split(":", 1)
        key = SLOT_FIELD_ALIASES.get(_normalize_slot_label(raw_label))
        if not key:
            continue

        value = raw_value.strip().strip(",，；;。")
        lowered = value.lower()
        if lowered in NULLISH_TEXT:
            parsed[key] = None
            continue

        if key == "special_rules":
            if value.startswith("["):
                wrapped = _parse_json_like_candidate(f'{{"special_rules": {value}}}')
                if isinstance(wrapped, dict) and "special_rules" in wrapped:
                    parsed[key] = wrapped["special_rules"]
                    continue
            rules = [
                item.strip(" -")
                for item in re.split(r"\s*[;,|、，/]\s*", value)
                if item.strip(" -")
            ]
            parsed[key] = rules or [value]
            continue

        parsed[key] = value.strip().strip('"').strip("'")

    return parsed if parsed else None


def _parse_labeled_slot_lines(text: str) -> Optional[dict]:
    parsed: Dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = re.sub(r"^[\s>*\-]+", "", raw_line).strip()
        line = line.replace(chr(0xFF1A), ":")
        if ":" not in line:
            continue

        raw_label, raw_value = line.split(":", 1)
        key = SLOT_FIELD_ALIASES.get(_normalize_slot_label(raw_label))
        if not key:
            continue

        value = raw_value.strip().strip(",")
        value = value.strip(chr(0xFF0C)).strip(chr(0xFF1B)).strip(chr(0x3002))
        lowered = value.lower()
        if lowered in NULLISH_TEXT:
            parsed[key] = None
            continue

        if key == "special_rules":
            if value.startswith("["):
                wrapped = _parse_json_like_candidate(f'{{"special_rules": {value}}}')
                if isinstance(wrapped, dict) and "special_rules" in wrapped:
                    parsed[key] = wrapped["special_rules"]
                    continue
            rules = [
                item.strip(" -")
                for item in re.split(r"\s*[;,|/]\s*", value.replace(chr(0x3001), ",").replace(chr(0xFF0C), ","))
                if item.strip(" -")
            ]
            parsed[key] = rules or [value]
            continue

        parsed[key] = value.strip().strip('"').strip("'")

    return parsed if parsed else None


def _safe_parse_json(text: str) -> Optional[dict]:
    text = _clean_llm_output(text)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    parsed_labeled = _parse_labeled_slot_lines(text)
    if parsed_labeled is not None:
        return parsed_labeled

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            candidate, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate

    for candidate in _extract_braced_candidates(text):
        parsed = _parse_json_like_candidate(candidate)
        if parsed is not None:
            return parsed
    return None


def _normalize_slot_payload(slot_data: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in slot_data.items():
        if key not in SlotState.model_fields or value is None:
            continue
        if key == "special_rules":
            if isinstance(value, str):
                rule = value.strip()
                if rule:
                    normalized[key] = [rule]
                continue
            if isinstance(value, list):
                rules = [str(item).strip() for item in value if str(item).strip()]
                if rules:
                    normalized[key] = rules
                continue
            continue
        scalar = SlotState._normalize_scalar_slot_value(value)
        if scalar:
            normalized[key] = _normalize_slot_text_value(key, scalar)
    return normalized


def _normalize_slot_text_value(field: str, value: str) -> str:
    text = re.sub(r"[_\-]+", " ", str(value or "").strip())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    lowered = text.lower()
    if field == "input_method":
        for normalized, hints in NORMALIZED_INPUT_METHOD_HINTS:
            if any(hint in lowered for hint in hints):
                return normalized
    if field == "difficulty":
        for normalized, hints in NORMALIZED_DIFFICULTY_HINTS:
            if any(hint in lowered for hint in hints):
                return normalized
    if field == "game_type":
        normalized_game_type = _normalize_game_type_label(text, "")
        if normalized_game_type:
            return normalized_game_type
    return text


def _merge_slot_payloads(*payloads: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for payload in payloads:
        for key, value in payload.items():
            if value is None:
                continue
            if key == "special_rules":
                existing = list(merged.get(key) or [])
                incoming = value if isinstance(value, list) else [value]
                for item in incoming:
                    item_text = str(item).strip()
                    if item_text and item_text not in existing:
                        existing.append(item_text)
                if existing:
                    merged[key] = existing
                continue
            merged[key] = value
    return merged


def _normalize_free_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _contains_marker(text: str, marker: str) -> bool:
    normalized_text = text.lower()
    normalized_marker = marker.lower()
    if re.search(r"[a-z0-9]", normalized_marker):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_marker)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None
    return marker in text


def _contains_any_marker(text: str, markers: Tuple[str, ...]) -> bool:
    return any(_contains_marker(text, marker) for marker in markers)


def _looks_like_sparse_request(text: str) -> bool:
    normalized = _normalize_free_text(text)
    if not normalized:
        return True
    if len(normalized) <= 24:
        return True
    return any(_contains_marker(normalized, marker) for marker in SPARSE_REQUEST_MARKERS)


def _looks_like_educational_request(*texts: str) -> bool:
    normalized_texts = [_normalize_free_text(text) for text in texts if _normalize_free_text(text)]
    if not normalized_texts:
        return False
    combined = " ".join(normalized_texts)
    return any(_contains_marker(combined, marker) for marker in EDUCATIONAL_REQUEST_MARKERS)


def _looks_like_funny_request(*texts: str) -> bool:
    normalized_texts = [_normalize_free_text(text) for text in texts if _normalize_free_text(text)]
    if not normalized_texts:
        return False
    combined = " ".join(normalized_texts)
    return any(_contains_marker(combined, marker) for marker in FUNNY_REQUEST_MARKERS)


def _looks_like_puzzle_request(*texts: str) -> bool:
    normalized_texts = [_normalize_free_text(text) for text in texts if _normalize_free_text(text)]
    if not normalized_texts:
        return False
    combined = " ".join(normalized_texts)
    return any(_contains_marker(combined, marker) for marker in PUZZLE_REQUEST_MARKERS)


def _normalize_game_type_label(game_type: str, *texts: str) -> str:
    normalized = re.sub(r"[^a-z_-]+", " ", (game_type or "").strip().lower()).strip()
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    mapped = LEGACY_GAME_TYPE_ALIASES.get(normalized, normalized)

    context = " ".join(_normalize_free_text(text) for text in texts if _normalize_free_text(text))
    if _looks_like_educational_request(context):
        return "educational"
    if _looks_like_funny_request(context):
        return "funny"
    if _looks_like_puzzle_request(context):
        return "puzzle"
    if mapped in CURATED_GAME_TYPES:
        return mapped
    return "casual"


def _build_intent_parse_input(description: str, *, title: Optional[str] = None) -> str:
    normalized_description = _normalize_free_text(description)
    normalized_title = _normalize_free_text(title or "")
    if not normalized_title:
        return normalized_description

    lines = [
        f"Game title: {normalized_title}",
        f"User request: {normalized_description or '(empty)'}",
    ]
    if _looks_like_sparse_request(normalized_description):
        lines.append(
            "Treat short change-style requests as requirements for a full mobile game spec. "
            "Infer a grounded mobile-friendly base loop, but keep room for a less common mechanic when the brief is open-ended."
        )
    return "\n".join(lines)


def _has_explicit_game_type_marker(text: str, game_type: str) -> bool:
    markers = EXPLICIT_GAME_TYPE_MARKERS.get(game_type, ())
    if not markers:
        return False
    return _contains_any_marker(text, markers)


def _infer_game_type_from_sparse_context(
    *texts: str,
    preferred_game_type: Optional[str] = None,
    variation_seed: Optional[str] = None,
) -> Optional[str]:
    normalized_texts = [_normalize_free_text(text) for text in texts if _normalize_free_text(text)]
    combined = " ".join(normalized_texts)
    if not combined:
        return preferred_game_type

    if _looks_like_educational_request(*normalized_texts):
        return "educational"
    if _looks_like_funny_request(*normalized_texts):
        return "funny"
    if _looks_like_puzzle_request(*normalized_texts):
        return "puzzle"

    sparse = any(_looks_like_sparse_request(text) for text in normalized_texts) or _looks_like_sparse_request(combined)
    for game_type, markers in CONTEXT_GAME_TYPE_RULES:
        if not _contains_any_marker(combined, markers):
            continue
        if preferred_game_type and preferred_game_type == game_type:
            return preferred_game_type
        if sparse and not _has_explicit_game_type_marker(combined, game_type):
            variants = SPARSE_GAME_TYPE_VARIANTS.get(game_type)
            if variants:
                index = _stable_variant_index(
                    *normalized_texts,
                    preferred_game_type or "",
                    variation_seed or "",
                    count=len(variants),
                )
                return variants[index]
        return game_type

    if preferred_game_type:
        return preferred_game_type

    if re.search(r"(?<!\d)(\d{1,2})\s*(?:levels?|stages?)\b", combined, flags=re.IGNORECASE) or re.search(
        r"(?<!\d)(\d{1,2})\s*(?:\u4e2a)?\u5173\u5361",
        combined,
    ):
        if _looks_like_educational_request(combined):
            return "educational"
        if _looks_like_puzzle_request(combined):
            return "puzzle"
        if _looks_like_funny_request(combined):
            return "funny"
        return "puzzle"

    index = _stable_variant_index(
        *normalized_texts,
        preferred_game_type or "",
        variation_seed or "",
        count=len(SPARSE_DEFAULT_GAME_TYPES),
    )
    return SPARSE_DEFAULT_GAME_TYPES[index]


def _infer_theme_from_context(*texts: str) -> Optional[str]:
    normalized_texts = [_normalize_free_text(text) for text in texts if _normalize_free_text(text)]
    combined = " ".join(normalized_texts)
    if not combined:
        return None

    for theme, markers in CONTEXT_THEME_RULES:
        if _contains_any_marker(combined, markers):
            return theme
    return None


def _select_sparse_theme(
    *texts: str,
    game_type: str,
    variation_seed: Optional[str] = None,
) -> str:
    normalized_texts = [_normalize_free_text(text) for text in texts if _normalize_free_text(text)]
    explicit = _infer_theme_from_context(*normalized_texts)
    if explicit:
        return explicit
    index = _stable_variant_index(
        game_type,
        *normalized_texts,
        variation_seed or "",
        count=len(SPARSE_DEFAULT_THEMES),
    )
    return SPARSE_DEFAULT_THEMES[index]


def _extract_sparse_special_rules(text: str, *, ui_language: str) -> List[str]:
    normalized = _normalize_free_text(text)
    if not normalized:
        return []

    rules: List[str] = []
    match = re.search(r"(?<!\d)(\d{1,2})\s*(?:levels?|stages?)\b", normalized, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"(?<!\d)(\d{1,2})\s*(?:\u4e2a)?\u5173\u5361", normalized)
    if match:
        level_count = int(match.group(1))
        if ui_language == "zh-CN":
            rules.append(f"\u5305\u542b{level_count}\u4e2a\u5173\u5361")
        else:
            rules.append(f"Include {level_count} levels")

    if _looks_like_sparse_request(normalized):
        if ui_language == "zh-CN":
            rules.append("\u4f18\u5148\u4fdd\u7559\u539f\u6709\u4e3b\u9898\u5e76\u6269\u5c55\u5185\u5bb9")
        else:
            rules.append("Preserve the core theme while expanding the content")

    deduped: List[str] = []
    for item in rules:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _build_sparse_diversity_rules(game_type: str) -> List[str]:
    shared = [
        "Favor a distinctive gameplay loop instead of the most common default for this genre.",
    ]
    genre_specific = {
        "puzzle": "Avoid turning every open brief into a match-3 clone.",
        "educational": "Avoid turning every learning brief into a worksheet or plain flash-card list.",
        "funny": "Avoid reducing the joke to a generic survive-and-score loop with only reskinned art.",
        "casual": "Avoid defaulting to the same hazard-dodging survival loop when another clear arcade objective can fit.",
    }
    rule = genre_specific.get(game_type)
    return shared + ([rule] if rule else [])


def _build_sparse_slot_fallback(
    *,
    source_text: str,
    title: Optional[str],
    raw_text: str,
    repaired_text: str,
    preferred_game_type: Optional[str],
    variation_seed: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_source = _normalize_free_text(source_text)
    normalized_title = _normalize_free_text(title or "")
    ui_language = _detect_ui_language(" ".join(item for item in [normalized_title, normalized_source] if item))
    merged = _merge_slot_payloads(
        _normalize_slot_payload(_infer_slots_from_text(raw_text)),
        _normalize_slot_payload(_infer_slots_from_text(repaired_text)),
        _normalize_slot_payload(_infer_slots_from_text(normalized_source)),
        _normalize_slot_payload(_infer_slots_from_text(normalized_title)),
    )

    if not merged.get("game_type"):
        merged["game_type"] = _infer_game_type_from_sparse_context(
            normalized_title,
            normalized_source,
            raw_text,
            repaired_text,
            preferred_game_type=preferred_game_type,
            variation_seed=variation_seed,
        )

    if not merged.get("theme"):
        merged["theme"] = _select_sparse_theme(
            normalized_title,
            normalized_source,
            raw_text,
            repaired_text,
            game_type=str(merged.get("game_type") or ""),
            variation_seed=variation_seed,
        )

    game_type = str(merged.get("game_type") or "").strip()
    if not game_type:
        return {}

    defaults = _localized_game_type_defaults(game_type, ui_language)
    merged.setdefault("core_mechanic", defaults.get("core_mechanic") or "")
    merged.setdefault("win_condition", defaults.get("win_condition") or "")
    merged.setdefault("input_method", defaults.get("input_method") or "touch")
    merged.setdefault("difficulty", "progressive")
    merged.setdefault("theme", _select_sparse_theme(
        normalized_title,
        normalized_source,
        raw_text,
        repaired_text,
        game_type=game_type,
        variation_seed=variation_seed,
    ))

    special_rules = list(merged.get("special_rules") or [])
    for item in _extract_sparse_special_rules(normalized_source, ui_language=ui_language):
        if item not in special_rules:
            special_rules.append(item)
    if _looks_like_sparse_request(normalized_source):
        for item in _build_sparse_diversity_rules(game_type):
            if item not in special_rules:
                special_rules.append(item)
    if special_rules:
        merged["special_rules"] = special_rules

    return _normalize_slot_payload(merged)


def _has_minimum_viable_slot_payload(slot_data: Dict[str, Any]) -> bool:
    return bool(str(slot_data.get("game_type") or "").strip())


def _detect_ui_language(text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text or ""):
        return "zh-CN"
    return "en-US"


def _coerce_game_type_for_request(game_type: str, source_description: str) -> str:
    normalized_description = _normalize_free_text(source_description)
    return _normalize_game_type_label(game_type, normalized_description)


def _stable_variant_index(*parts: str, count: int) -> int:
    if count <= 1:
        return 0
    seed = "|".join((part or "").strip() for part in parts if part is not None)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % count


def _localized_game_type_defaults(game_type: str, ui_language: str) -> Dict[str, str]:
    defaults = LOCALIZED_GAME_TYPE_DEFAULTS.get(game_type, {})
    if ui_language == "zh-CN" and "zh-CN" in defaults:
        return defaults["zh-CN"]
    return defaults.get("en-US", {})


def _select_entity_variant(
    game_type: str,
    *,
    source_description: str,
    theme: str,
    variation_seed: Optional[str] = None,
) -> List[Dict[str, Any]]:
    variants = ENTITY_VARIANTS.get(game_type, GENERIC_ENTITY_VARIANTS)
    index = _stable_variant_index(game_type, theme, source_description, variation_seed or "", count=len(variants))
    return variants[index]


def _select_visual_variant(
    game_type: str,
    *,
    explicit_theme: str,
    explicit_art_style: str,
    source_description: str,
    variation_seed: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_theme = (explicit_theme or "").strip().lower()
    if normalized_theme in THEME_STYLE_PRESETS:
        preset = THEME_STYLE_PRESETS[normalized_theme]
        return {
            "theme": preset["theme"],
            "palette": list(preset["palette"]),
            "background": preset["background"],
            "art_style": explicit_art_style or preset.get("art_style", "geometric"),
            "effects": list(preset.get("effects", [])),
        }

    variants = VISUAL_VARIANTS_BY_GAME_TYPE.get(game_type, VISUAL_VARIANTS_BY_GAME_TYPE.get("casual", []))
    if not variants:
        return {
            "theme": normalized_theme or "arcade",
            "palette": ["#1f2937", "#38bdf8", "#22c55e", "#f97316", "#f8fafc"],
            "background": "gradient",
            "art_style": explicit_art_style or "geometric",
            "effects": [],
        }

    index = _stable_variant_index(
        game_type,
        normalized_theme,
        source_description,
        variation_seed or "",
        count=len(variants),
    )
    preset = variants[index]
    return {
        "theme": normalized_theme or preset["theme"],
        "palette": list(preset["palette"]),
        "background": preset["background"],
        "art_style": explicit_art_style or preset.get("art_style", "geometric"),
        "effects": list(preset.get("effects", [])),
    }


def _infer_slots_from_text(text: str) -> Dict[str, Any]:
    source = (text or "").strip()
    if not source:
        return {}

    lowered = source.lower()
    inferred: Dict[str, Any] = {}
    ui_language = _detect_ui_language(source)

    game_type_rules = (
        ("educational", EDUCATIONAL_REQUEST_MARKERS),
        ("funny", FUNNY_REQUEST_MARKERS),
        ("puzzle", PUZZLE_REQUEST_MARKERS),
        ("casual", CASUAL_REQUEST_MARKERS),
    )
    for game_type, markers in game_type_rules:
        if any(_contains_marker(source, marker) or _contains_marker(lowered, marker) for marker in markers):
            inferred["game_type"] = game_type
            break

    input_rules = (
        ("swipe", ("\u6ed1\u52a8", "\u5de6\u53f3\u79fb\u52a8", "swipe", "drag left and right")),
        ("tap", ("\u70b9\u51fb", "\u8f7b\u70b9", "tap")),
        ("drag", ("\u62d6\u62fd", "drag")),
        ("hold", ("\u957f\u6309", "hold")),
        ("touch", ("\u89e6\u6478", "touch")),
    )
    for input_method, markers in input_rules:
        if any(_contains_marker(source, marker) or _contains_marker(lowered, marker) for marker in markers):
            inferred["input_method"] = input_method
            break

    if "theme" not in inferred:
        theme_rules = (
            ("space", ("太空", "宇宙", "space", "cosmic", "galaxy")),
            ("zoo", ("动物园", "zoo", "animal")),
            ("neon", ("霓虹", "neon", "cyber")),
            ("fantasy", ("奇幻", "fantasy", "magic")),
            ("ocean", ("海洋", "ocean", "underwater", "water")),
            ("forest", ("森林", "forest", "jungle")),
            ("city", ("城市", "city", "urban")),
            ("garden", ("花园", "garden", "farm")),
            ("food", ("美食", "厨房", "food", "kitchen", "chef")),
            ("candy", ("糖果", "甜品", "candy", "dessert")),
            ("sports", ("运动", "球场", "sports", "stadium")),
            ("toy", ("玩具", "toy", "block")),
        )
        for theme, markers in theme_rules:
            if any(_contains_marker(source, marker) or _contains_marker(lowered, marker) for marker in markers):
                inferred["theme"] = theme
                break

    game_type = inferred.get("game_type")
    defaults = _localized_game_type_defaults(str(game_type), ui_language) if game_type else {}

    if defaults.get("core_mechanic"):
        inferred.setdefault("core_mechanic", defaults["core_mechanic"])
    if defaults.get("win_condition"):
        inferred.setdefault("win_condition", defaults["win_condition"])
    if defaults.get("input_method"):
        inferred.setdefault("input_method", defaults["input_method"])

    if "\u91cd\u65b0\u5f00\u59cb" in source or "restart" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + [
            "失败后可重新开始" if ui_language == "zh-CN" else "Restart after losing",
        ]
    if "\u70b9\u51fb\u5f00\u59cb" in source or "tap to start" in lowered or "click to start" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + [
            "点击开始" if ui_language == "zh-CN" else "Tap to start",
        ]

    return inferred


def _source_description_from_history(history: List[ConversationMessage]) -> str:
    user_messages = [
        re.sub(r"\s+", " ", item.content.strip())
        for item in history
        if item.role == "user" and item.content and item.content.strip()
    ]
    return "\n".join(user_messages[-3:])


def _latest_user_answer_from_history(history: List[ConversationMessage]) -> str:
    for item in reversed(history):
        if item.role != "user":
            continue
        content = _normalize_free_text(item.content)
        if content:
            return content
    return ""


def _build_dialogue_source_text(initial_prompt: Optional[str], history: List[ConversationMessage]) -> str:
    combined: List[str] = []
    for part in [initial_prompt or "", *(item.content for item in history if item.role == "user")]:
        normalized = _normalize_free_text(part)
        if normalized and normalized not in combined:
            combined.append(normalized)
    return "\n".join(combined[-4:])


def _normalize_slot_key(slot_key: Optional[str]) -> str:
    normalized = str(slot_key or "").strip()
    return normalized if normalized in SlotState.model_fields else ""


def _build_heuristic_slot_payload(
    *,
    raw_text: str,
    source_text: str,
    title: Optional[str] = None,
    preferred_game_type: Optional[str] = None,
    variation_seed: Optional[str] = None,
    allow_sparse_fallback: bool,
) -> Dict[str, Any]:
    normalized_source_text = _normalize_free_text(source_text)
    normalized_title = _normalize_free_text(title or "")
    normalized_preferred_game_type = (
        _normalize_game_type_label(
            preferred_game_type,
            normalized_source_text,
            normalized_title,
            raw_text,
        )
        if preferred_game_type
        else None
    )
    heuristic_slot_data = _normalize_slot_payload({
        **_infer_slots_from_text(raw_text),
        **_infer_slots_from_text(normalized_source_text),
        **_infer_slots_from_text(normalized_title),
    })
    if normalized_preferred_game_type:
        heuristic_slot_data["game_type"] = normalized_preferred_game_type
    if allow_sparse_fallback and _looks_like_sparse_request(normalized_source_text):
        heuristic_slot_data = _merge_slot_payloads(
            _build_sparse_slot_fallback(
                source_text=normalized_source_text,
                title=normalized_title or None,
                raw_text=raw_text,
                repaired_text="",
                preferred_game_type=heuristic_slot_data.get("game_type") or normalized_preferred_game_type,
                variation_seed=variation_seed,
            ),
            heuristic_slot_data,
        )
    return heuristic_slot_data


def _merge_structured_slot_payload(
    *,
    heuristic_slot_data: Dict[str, Any],
    raw_slot_data: Dict[str, Any],
    source_text: str,
    title: Optional[str] = None,
    raw_text: str,
    repaired_text: str = "",
    preferred_game_type: Optional[str] = None,
) -> Dict[str, Any]:
    slot_data = _merge_slot_payloads(heuristic_slot_data, raw_slot_data)
    explicit_game_type = (
        _normalize_game_type_label(
            str(raw_slot_data.get("game_type") or ""),
            source_text,
            title or "",
            raw_text,
            repaired_text,
        )
        if raw_slot_data.get("game_type")
        else ""
    )
    preferred = (
        _normalize_game_type_label(
            preferred_game_type,
            source_text,
            title or "",
            raw_text,
            repaired_text,
        )
        if preferred_game_type
        else ""
    )
    if explicit_game_type:
        slot_data["game_type"] = explicit_game_type
    elif preferred:
        slot_data["game_type"] = preferred
    return slot_data


def _coerce_authoritative_slot_value(
    field: str,
    *,
    answer_text: str,
    inferred_slot_data: Dict[str, Any],
    source_text: str,
    title: Optional[str] = None,
) -> Optional[Any]:
    normalized_answer = _normalize_free_text(answer_text)
    if not normalized_answer:
        return None

    if field == "game_type":
        candidate = str(inferred_slot_data.get(field) or normalized_answer).strip()
        normalized = _normalize_game_type_label(
            candidate,
            normalized_answer,
            source_text,
            title or "",
        )
        return normalized or None
    if field in {"input_method", "difficulty"}:
        return inferred_slot_data.get(field) or _normalize_slot_text_value(field, normalized_answer)
    if field == "special_rules":
        return [normalized_answer]
    if field == "theme" and inferred_slot_data.get(field):
        return inferred_slot_data.get(field)
    if field in SlotState.model_fields:
        return _normalize_slot_text_value(field, normalized_answer)
    return None


class DialogueEngine:
    """Dialogue engine for slot-filling and single-shot parsing."""

    def __init__(self) -> None:
        self._client = LLMClient()

    async def _complete_slot_request(
        self,
        *,
        messages: List[Dict[str, str]],
        system: str,
        step_key: str,
        stage: str,
        max_tokens: int,
    ) -> str:
        is_fast_dialogue_slot_extract = step_key == "dialogue.slot_extract"
        request_timeout_s = (
            FAST_DIALOGUE_SLOT_REQUEST_TIMEOUT_S
            if is_fast_dialogue_slot_extract
            else None
        )
        overall_timeout_s = (
            FAST_DIALOGUE_SLOT_OVERALL_TIMEOUT_S
            if is_fast_dialogue_slot_extract
            else None
        )
        try:
            return await self._client.complete(
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                step_key=step_key,
                stage=stage,
                prefer_fast=False,
                allow_provider_fallback=True,
                response_size_hint="small",
                context_scope="task",
                compression_policy="intent_parse" if step_key == "intent_parse" else "dialogue",
                request_timeout_s=request_timeout_s,
                overall_timeout_s=overall_timeout_s,
            )
        except LLMResponseTruncatedError as exc:
            excerpt = _clean_llm_output(exc.response_excerpt or "")
            if excerpt:
                logger.warning(
                    "LLM %s response was truncated (reason=%s, outputTokens=%s); attempting slot recovery from excerpt",
                    step_key,
                    exc.stop_reason,
                    exc.output_tokens,
                )
                return excerpt
            if is_fast_dialogue_slot_extract:
                raise
            return await self._client.complete_with_truncation_retry(
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                step_key=step_key,
                stage=stage,
                prefer_fast=False,
                allow_provider_fallback=True,
                response_size_hint="small",
                context_scope="task",
                compression_policy="intent_parse" if step_key == "intent_parse" else "dialogue",
                request_timeout_s=request_timeout_s,
                overall_timeout_s=overall_timeout_s,
                truncation_retry_attempts=1,
                truncation_retry_increment=512,
                truncation_retry_max_tokens=max(max_tokens, 2048),
                timeout_retry_attempts=0 if is_fast_dialogue_slot_extract else 1,
                timeout_retry_increment_s=30,
                timeout_retry_max_s=120,
            )

    def get_or_create_session(self, session_id: str, user_id: str) -> DialogueSession:
        if session_id not in _sessions:
            _sessions[session_id] = DialogueSession(
                session_id=session_id,
                user_id=user_id,
                state=DialogueState.greeting,
            )
        return _sessions[session_id]

    async def process_message(self, req: ChatRequest) -> ChatResponse:
        session = self.get_or_create_session(req.session_id, req.user_id)
        session.history.append(ConversationMessage(role="user", content=req.content))

        if not self._client.is_enabled():
            raise RuntimeError("Real LLM mode is required for dialogue sessions")

        reply, updated_slots = await self._llm_process(session)
        session.history.append(ConversationMessage(role="assistant", content=reply))
        session.state = self._next_state(session.state, session.slots.fill_pct())

        return ChatResponse(
            session_id=req.session_id,
            reply=reply,
            state=session.state,
            slots_updated=updated_slots,
            slot_fill_pct=session.slots.fill_pct(),
            ready_to_generate=session.state == DialogueState.confirmed,
        )

    async def analyze_turn(self, req: AnalyzeDialogueTurnRequest) -> AnalyzeDialogueTurnResponse:
        if not self._client.is_enabled():
            raise RuntimeError("Real LLM mode is required for dialogue analysis")

        history = [
            ConversationMessage(
                role=item.role,
                content=item.content,
                kind=getattr(item, "kind", None),
            )
            for item in (req.conversation or [])
        ]
        session = DialogueSession(
            session_id=req.session_id or "stateless",
            user_id=req.user_id or "system",
            state=DialogueState.clarifying,
            slots=req.current_slots.model_copy(deep=True),
            history=history,
        )
        source_text = _build_dialogue_source_text(req.initial_prompt, history)
        answered_slot_key = _normalize_slot_key(req.answered_slot_key)
        latest_user_answer = _normalize_free_text(
            req.latest_user_answer or _latest_user_answer_from_history(history)
        )

        skipped_slots = {
            str(slot).strip()
            for slot in (req.skipped_slots or [])
            if str(slot).strip()
        }
        authoritative_slots: set[str] = set()
        blocked_slots: set[str] = set()

        if req.advance_only:
            updated_slots: list[str] = []
        elif answered_slot_key and latest_user_answer:
            updated_slots = self._apply_answer_turn_slot_update(
                session=session,
                answered_slot_key=answered_slot_key,
                latest_user_answer=latest_user_answer,
                source_text=source_text,
                title=req.title,
            )
            if (
                answered_slot_key in updated_slots
                and str(getattr(session.slots, answered_slot_key, "") or "").strip()
            ):
                authoritative_slots.add(answered_slot_key)
                blocked_slots.add(answered_slot_key)
        else:
            updated_slots = await self._extract_slots_from_conversation(
                session,
                source_text=source_text,
                title=req.title,
            )

        confidence_by_slot, evidence_by_slot, ambiguity_flags = _build_slot_confidence_report(
            session.slots,
            source_text=source_text,
            title=req.title,
            history=history,
            updated_slots=updated_slots,
            authoritative_slots=sorted(authoritative_slots),
        )
        current_question, question_strategy = _build_dialogue_question(
            session.slots,
            skipped_slots=sorted(skipped_slots),
            blocked_slots=sorted(blocked_slots),
            source_text=source_text,
            entry_mode=req.entry_mode,
            confidence_by_slot=confidence_by_slot,
            ambiguity_flags=ambiguity_flags,
        )
        fill_pct = session.slots.fill_pct()
        ready_to_generate = bool(fill_pct >= 0.67 or current_question is None)
        plan_draft = _build_plan_draft(
            session.slots,
            source_text=source_text,
            title=req.title,
            generation_tier=req.generation_tier,
            entry_mode=req.entry_mode,
        )
        question_reason = (
            question_strategy.reason
            if question_strategy and question_strategy.reason
            else None
        )
        reply = _compose_creation_session_reply(
            slots=session.slots,
            current_question=current_question,
            ready_to_generate=ready_to_generate,
            source_text=source_text,
            title=req.title,
        )

        return AnalyzeDialogueTurnResponse(
            reply=reply,
            slots=session.slots,
            slots_updated=updated_slots,
            missing_required=session.slots.missing_required(),
            slot_fill_pct=fill_pct,
            ready_to_generate=ready_to_generate,
            current_question=current_question,
            confidence_by_slot=confidence_by_slot,
            evidence_by_slot=evidence_by_slot,
            ambiguity_flags=ambiguity_flags,
            next_best_question_reason=question_reason,
            question_strategy=question_strategy,
            plan_draft=plan_draft,
        )

    async def draft_plan_from_input(self, req: DraftPlanFromInputRequest) -> DraftPlanFromInputResponse:
        slots = req.current_slots.model_copy(deep=True)
        source_text = (req.source_description or "").strip()
        title = (req.title or "").strip()
        if source_text or title:
            inferred = _normalize_slot_payload(
                _infer_slots_from_text(" ".join(part for part in [title, source_text] if part))
            )
            explicit_theme = _infer_theme_from_context(title, source_text)
            if not explicit_theme:
                inferred.pop("theme", None)
            for key, value in inferred.items():
                if not getattr(slots, key, None) and value is not None:
                    setattr(slots, key, value)

        confidence_by_slot, evidence_by_slot, ambiguity_flags = _build_slot_confidence_report(
            slots,
            source_text=source_text,
            title=title,
            history=[],
            updated_slots=[],
        )
        return DraftPlanFromInputResponse(
            plan_draft=_build_plan_draft(
                slots,
                source_text=source_text,
                title=title,
                generation_tier=req.generation_tier,
                entry_mode=req.entry_mode,
            ),
            confidence_by_slot=confidence_by_slot,
            evidence_by_slot=evidence_by_slot,
            ambiguity_flags=ambiguity_flags,
        )

    async def spec_from_slots(self, req: SpecFromSlotsRequest) -> SpecFromSlotsResponse:
        slots = req.slots.model_copy(deep=True)
        source_text = (req.source_description or "").strip()
        title = (req.title or "").strip()
        if source_text or title:
            inferred = _infer_slots_from_text(" ".join(part for part in [title, source_text] if part))
            normalized = _normalize_slot_payload(inferred)
            for key, value in normalized.items():
                if not getattr(slots, key, None) and value is not None:
                    setattr(slots, key, value)

        if not (slots.game_type or "").strip():
            preferred = (req.preferred_game_type or "").strip()
            if preferred:
                slots.game_type = preferred
            else:
                slots.game_type = "casual"

        spec = _build_game_spec(
            slots,
            source_description=source_text,
            variation_seed=req.variation_seed or req.session_id,
        )
        spec.generation_tier = req.generation_tier
        spec.complexity_budget = str(getattr(req.generation_tier, "value", req.generation_tier) or "standard")
        if title:
            if spec.intent_summary:
                spec.intent_summary = f"{title}: {spec.intent_summary}"
            if not spec.source_description:
                spec.source_description = source_text
        return SpecFromSlotsResponse(
            spec=spec,
            missing_required=slots.missing_required(),
            slot_fill_pct=slots.fill_pct(),
        )

    async def slots_to_game_spec(self, session_id: str) -> GameSpec:
        session = _sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        return _build_game_spec(
            session.slots,
            source_description=_source_description_from_history(session.history),
            variation_seed=session_id,
        )

    async def parse_description_to_spec(
        self,
        description: str,
        allow_fallback: bool = True,
        *,
        title: Optional[str] = None,
        preferred_game_type: Optional[str] = None,
        variation_seed: Optional[str] = None,
    ) -> GameSpec:
        if not self._client.is_enabled():
            raise RuntimeError("Real LLM mode is required for intent parsing")

        parse_input = _build_intent_parse_input(description, title=title)
        text = await self._complete_slot_request(
            max_tokens=640,
            system=_with_slot_json_contract(
                require_prompt("prompt.intent_parse_system")
            ),
            messages=[{"role": "user", "content": parse_input}],
            step_key="intent_parse",
            stage="intent_parsing",
        )
        slot_data = await self._extract_slot_payload_with_repair(
            raw_text=text,
            source_text=parse_input,
            step_key="intent_parse",
            stage="intent_parsing",
            allow_fallback=allow_fallback,
            title=title,
            preferred_game_type=preferred_game_type,
            variation_seed=variation_seed,
        )
        slots = SlotState(**slot_data)
        return _build_game_spec(
            slots,
            source_description=description,
            variation_seed=variation_seed,
        )

    async def _llm_process(self, session: DialogueSession) -> Tuple[str, List[str]]:
        old_slots = session.slots.model_copy()

        slot_text = await self._complete_slot_request(
            max_tokens=640,
            system=_with_slot_json_contract(
                require_prompt("prompt.slot_extraction_system")
            ),
            messages=_history_to_messages(session.history),
            step_key="dialogue.slot_extract",
            stage="dialogue",
        )
        slot_data = await self._extract_slot_payload_with_repair(
            raw_text=slot_text,
            source_text=_source_description_from_history(session.history),
            step_key="dialogue.slot_extract",
            stage="dialogue",
        )

        for key, value in slot_data.items():
            if value is not None and hasattr(session.slots, key):
                setattr(session.slots, key, value)

        updated = [
            key for key in SlotState.model_fields
            if getattr(session.slots, key) != getattr(old_slots, key)
        ]

        missing = session.slots.missing_required()
        slot_summary = _format_slot_summary(session.slots)
        system = require_prompt("prompt.dialogue_system").format(
            slot_summary=slot_summary,
            missing_slots=", ".join(SLOT_LABELS.get(item, item) for item in missing) or "None",
        )

        try:
            reply = await self._client.complete_with_truncation_retry(
                max_tokens=2048,
                system=system,
                messages=_history_to_messages(session.history),
                step_key="dialogue.reply",
                stage="dialogue",
                prefer_fast=True,
                response_size_hint="medium",
                context_scope="task",
                compression_policy="dialogue",
                truncation_retry_attempts=1,
                truncation_retry_increment=512,
                truncation_retry_max_tokens=3072,
                timeout_retry_attempts=1,
                timeout_retry_increment_s=30,
                timeout_retry_max_s=120,
            )
            reply = reply.strip()
        except Exception as exc:
            logger.warning("Reply generation error: %s", exc)
            reply = _fallback_reply(session.state, session.slots)

        return reply, updated

    async def _extract_slots_from_conversation(
        self,
        session: DialogueSession,
        *,
        source_text: str,
        title: Optional[str] = None,
    ) -> List[str]:
        old_slots = session.slots.model_copy()
        messages = _history_to_messages(session.history)
        if not messages and source_text.strip():
            messages = [{"role": "user", "content": source_text.strip()}]

        slot_text = ""
        try:
            slot_text = await self._complete_slot_request(
                max_tokens=640,
                system=_with_slot_json_contract(
                    require_prompt("prompt.slot_extraction_system")
                ),
                messages=messages,
                step_key="dialogue.slot_extract",
                stage="dialogue",
            )
        except Exception as exc:
            logger.warning(
                "Dialogue slot extraction request failed; using heuristic fallback for session=%s: %s",
                session.session_id,
                exc,
            )
        heuristic_slot_data = _build_heuristic_slot_payload(
            raw_text=slot_text,
            source_text=source_text,
            title=title,
            variation_seed=session.session_id,
            allow_sparse_fallback=True,
        )
        slot_data = _merge_structured_slot_payload(
            heuristic_slot_data=heuristic_slot_data,
            raw_slot_data=_normalize_slot_payload(_safe_parse_json(slot_text) or {}),
            source_text=source_text,
            title=title,
            raw_text=slot_text,
        )

        for key, value in slot_data.items():
            if value is not None and hasattr(session.slots, key):
                setattr(session.slots, key, value)

        return [
            key for key in SlotState.model_fields
            if getattr(session.slots, key) != getattr(old_slots, key)
        ]

    def _apply_answer_turn_slot_update(
        self,
        *,
        session: DialogueSession,
        answered_slot_key: str,
        latest_user_answer: str,
        source_text: str,
        title: Optional[str] = None,
    ) -> List[str]:
        normalized_slot_key = _normalize_slot_key(answered_slot_key)
        normalized_answer = _normalize_free_text(latest_user_answer)
        if (
            not normalized_slot_key
            or normalized_slot_key not in SlotState.model_fields
            or not normalized_answer
        ):
            return []

        old_slots = session.slots.model_copy()
        heuristic_slot_data = _build_heuristic_slot_payload(
            raw_text="",
            source_text=normalized_answer,
            title=title,
            preferred_game_type=str(session.slots.game_type or "") or None,
            variation_seed=session.session_id,
            allow_sparse_fallback=False,
        )
        slot_data = dict(heuristic_slot_data)
        authoritative_value = _coerce_authoritative_slot_value(
            normalized_slot_key,
            answer_text=normalized_answer,
            inferred_slot_data=heuristic_slot_data,
            source_text=source_text,
            title=title,
        )
        if authoritative_value is not None:
            slot_data[normalized_slot_key] = authoritative_value

        for key, value in slot_data.items():
            if value is not None and hasattr(session.slots, key):
                setattr(session.slots, key, value)

        return [
            key for key in SlotState.model_fields
            if getattr(session.slots, key) != getattr(old_slots, key)
        ]

    async def _extract_slot_payload_with_repair(
        self,
        *,
        raw_text: str,
        source_text: str,
        step_key: str,
        stage: str,
        allow_fallback: bool = False,
        title: Optional[str] = None,
        preferred_game_type: Optional[str] = None,
        variation_seed: Optional[str] = None,
    ) -> Dict[str, Any]:
        heuristic_slot_data = _build_heuristic_slot_payload(
            raw_text=raw_text,
            source_text=source_text,
            title=title,
            preferred_game_type=preferred_game_type,
            variation_seed=variation_seed,
            allow_sparse_fallback=False,
        )
        normalized_preferred_game_type = str(
            heuristic_slot_data.get("game_type") or preferred_game_type or ""
        ).strip() or None
        raw_slot_data = _normalize_slot_payload(_safe_parse_json(raw_text) or {})
        slot_data = _merge_structured_slot_payload(
            heuristic_slot_data=heuristic_slot_data,
            raw_slot_data=raw_slot_data,
            source_text=source_text,
            title=title,
            raw_text=raw_text,
            preferred_game_type=preferred_game_type,
        )
        if raw_slot_data and _has_minimum_viable_slot_payload(raw_slot_data):
            return slot_data

        repair_input = require_prompt("prompt.slot_json_repair_user_template").format(
            source_text=source_text or "(empty)",
            raw_parser_output=_clean_llm_output(raw_text) or "(empty)",
        )
        repaired_text = await self._complete_slot_request(
            max_tokens=640,
            system=_with_slot_json_contract(
                require_prompt("prompt.slot_json_repair_system")
            ),
            messages=[{"role": "user", "content": repair_input}],
            step_key=step_key,
            stage=stage,
        )
        repaired_slot_data = _merge_structured_slot_payload(
            heuristic_slot_data=heuristic_slot_data,
            raw_slot_data=_normalize_slot_payload(_safe_parse_json(repaired_text) or {}),
            source_text=source_text,
            title=title,
            raw_text=raw_text,
            repaired_text=repaired_text,
            preferred_game_type=preferred_game_type,
        )
        if allow_fallback and _looks_like_sparse_request(source_text):
            repaired_slot_data = _merge_slot_payloads(
                _build_sparse_slot_fallback(
                    source_text=source_text,
                    title=title,
                    raw_text=raw_text,
                    repaired_text=repaired_text,
                    preferred_game_type=normalized_preferred_game_type,
                    variation_seed=variation_seed,
                ),
                repaired_slot_data,
            )
        if _has_minimum_viable_slot_payload(repaired_slot_data):
            return repaired_slot_data

        if allow_fallback and _looks_like_sparse_request(source_text):
            fallback_slot_data = _build_sparse_slot_fallback(
                source_text=source_text,
                title=title,
                raw_text=raw_text,
                repaired_text=repaired_text,
                preferred_game_type=normalized_preferred_game_type,
                variation_seed=variation_seed,
            )
            merged_heuristic_fallback = _merge_slot_payloads(fallback_slot_data, heuristic_slot_data)
            if _has_minimum_viable_slot_payload(merged_heuristic_fallback):
                logger.warning(
                    "LLM slot extraction repair failed; enriching heuristic sparse inference for source=%s",
                    source_text[:120],
                )
                return merged_heuristic_fallback

        if heuristic_slot_data.get("game_type"):
            logger.warning(
                "LLM slot extraction repair failed; using heuristic slot inference for source=%s",
                source_text[:120],
            )
            return heuristic_slot_data

        if allow_fallback:
            fallback_slot_data = _build_sparse_slot_fallback(
                source_text=source_text,
                title=title,
                raw_text=raw_text,
                repaired_text=repaired_text,
                preferred_game_type=normalized_preferred_game_type,
                variation_seed=variation_seed,
            )
            merged_fallback_slot_data = _merge_slot_payloads(fallback_slot_data, repaired_slot_data)
            if _has_minimum_viable_slot_payload(merged_fallback_slot_data):
                logger.warning(
                    "LLM slot extraction repair failed; synthesized sparse-request fallback for source=%s",
                    source_text[:120],
                )
                return merged_fallback_slot_data

        raise SlotExtractionFailure(
            "LLM slot extraction returned no valid JSON",
            diagnostics={
                "sourceText": source_text,
                "title": title,
                "preferredGameType": normalized_preferred_game_type,
                "rawParserOutput": _clean_llm_output(raw_text),
                "repairedParserOutput": _clean_llm_output(repaired_text),
                "heuristicSlotData": heuristic_slot_data,
                "rawSlotData": raw_slot_data,
                "repairedSlotData": repaired_slot_data,
            },
        )

    @staticmethod
    def _next_state(current: DialogueState, fill_pct: float) -> DialogueState:
        if current == DialogueState.greeting:
            return DialogueState.describing
        if current == DialogueState.describing and fill_pct >= 0.6:
            return DialogueState.clarifying
        if current == DialogueState.clarifying and fill_pct >= 1.0:
            return DialogueState.confirmed
        return current


def _build_game_spec(
    slots: SlotState,
    *,
    source_description: str = "",
    variation_seed: Optional[str] = None,
) -> GameSpec:
    normalized_description = re.sub(r"\s+", " ", source_description.strip()) if source_description else ""
    game_type = _coerce_game_type_for_request((slots.game_type or "").strip(), normalized_description)
    if not game_type:
        raise ValueError("Missing required slot: game_type")

    ui_language = _detect_ui_language(normalized_description or " ".join(
        str(value or "") for value in [
            slots.core_mechanic,
            slots.theme,
            slots.win_condition,
            slots.visual_style,
            slots.reference_game,
        ]
    ))
    defaults = _localized_game_type_defaults(game_type, ui_language)
    theme = (slots.theme or "").strip()
    visual_variant = _select_visual_variant(
        game_type,
        explicit_theme=theme,
        explicit_art_style=(slots.visual_style or "").strip(),
        source_description=normalized_description,
        variation_seed=variation_seed,
    )
    entity_defs = _select_entity_variant(
        game_type,
        source_description=normalized_description,
        theme=visual_variant["theme"],
        variation_seed=variation_seed,
    )
    entities = [GameEntity(**entity) for entity in entity_defs]

    mechanics = [CoreMechanic(
        type=game_type,
        input=slots.input_method or defaults.get("input_method", "touch"),
        difficulty_scaling=slots.difficulty or "progressive",
    )]

    rules = GameRules(
        win_condition=slots.win_condition or defaults.get("win_condition", "Reach the target objective."),
        lose_condition="lives_zero",
        scoring="collect_plus_time",
        lives=3,
    )

    visual = VisualStyle(
        theme=visual_variant["theme"],
        palette=visual_variant["palette"],
        art_style=visual_variant["art_style"],
        background=visual_variant["background"],
        effects=visual_variant["effects"],
    )
    intent_summary = (slots.core_mechanic or defaults.get("core_mechanic", "")).strip()
    quality_fields = _derive_quality_spec_fields(
        game_type=game_type,
        source_description=normalized_description,
        ui_language=ui_language,
        theme=visual_variant["theme"],
        mechanic=intent_summary,
        art_style=visual_variant["art_style"],
        slots=slots,
    )
    special_rules = [
        re.sub(r"\s+", " ", str(rule).strip())
        for rule in (slots.special_rules or [])
        if str(rule).strip()
    ]
    reference_game = (slots.reference_game or "").strip() or None

    return GameSpec(
        game_type=game_type,
        source_description=normalized_description,
        intent_summary=intent_summary,
        ui_language=ui_language,
        core_mechanics=mechanics,
        entities=entities,
        rules=rules,
        visual_style=visual,
        audio_style=slots.audio_style or "none",
        difficulty_curve=slots.difficulty or "progressive",
        **quality_fields,
        special_rules=special_rules,
        reference_game=reference_game,
        platform_constraints=PlatformConstraints(
            input_mode=slots.input_method or defaults.get("input_method", "touch"),
        ),
    )


def _derive_quality_spec_fields(
    *,
    game_type: str,
    source_description: str,
    ui_language: str,
    theme: str,
    mechanic: str,
    art_style: str,
    slots: SlotState,
) -> Dict[str, Any]:
    zh = ui_language == "zh-CN"
    normalized = _normalize_free_text(source_description)
    session_length_defaults = {
        "casual": "short_bursts",
        "puzzle": "short_stages",
        "educational": "guided_mini_sessions",
        "funny": "rapid_sketch_rounds",
    }
    progression_defaults = {
        "casual": "score_chase",
        "puzzle": "level_ladder",
        "educational": "lesson_steps",
        "funny": "escalating_gags",
    }
    reward_defaults_zh = {
        "casual": "通过分数提升、收集反馈和短回合连胜获得满足感。",
        "puzzle": "通过解开下一步、清盘和关卡推进获得持续奖励。",
        "educational": "通过即时纠错、知识点强化和阶段完成获得正反馈。",
        "funny": "通过连续笑点、反转触发和夸张演出获得奖励感。",
    }
    reward_defaults_en = {
        "casual": "Keep the player engaged with quick score climbs, pickups, and short win streaks.",
        "puzzle": "Reward each solve step with clearer board progress and level advancement.",
        "educational": "Reinforce learning through immediate correctness feedback and visible milestone progress.",
        "funny": "Reward the player with escalating punchlines, reversals, and expressive payoffs.",
    }
    target_audience = (
        "students"
        if game_type == "educational"
        else "office meme players"
        if _contains_any_marker(normalized, ["office", "work", "boss", "上班", "办公室", "老板"])
        else "mobile casual players"
    )
    tone = (
        "light and playful"
        if game_type == "casual"
        else "curious and rewarding"
        if game_type == "puzzle"
        else "encouraging and clear"
        if game_type == "educational"
        else "absurd and punchy"
    )
    if zh:
        target_audience = (
            "课堂学习者"
            if game_type == "educational"
            else "职场梗用户"
            if _contains_any_marker(normalized, ["office", "work", "boss", "上班", "办公室", "老板"])
            else "泛移动休闲玩家"
        )
        tone = (
            "轻快有参与感"
            if game_type == "casual"
            else "清晰、解题反馈强"
            if game_type == "puzzle"
            else "鼓励式、讲解清楚"
            if game_type == "educational"
            else "荒诞、有梗、反转快"
        )

    teaching_mode = None
    if game_type == "educational":
        if _contains_any_marker(normalized, ["quiz", "question", "测验", "问答", "练习题"]):
            teaching_mode = "guided_quiz"
        elif _contains_any_marker(normalized, ["experiment", "lab", "实验", "操作"]):
            teaching_mode = "interactive_experiment"
        else:
            teaching_mode = "guided_exploration"

    comedy_device = None
    if game_type == "funny":
        if _contains_any_marker(normalized, ["boss", "巡查", "老板", "抓包"]):
            comedy_device = "near_miss_reversal"
        elif _contains_any_marker(normalized, ["mistake", "误会", "尴尬"]):
            comedy_device = "awkward_escalation"
        else:
            comedy_device = "surprise_punchline"

    signature_moment = (
        _plan_signature_moment(game_type, theme, mechanic, zh)
        if not getattr(slots, "reference_game", None)
        else (
            f"做出一个向“{slots.reference_game}”致敬、但更适合当前主题的高光时刻。"
            if zh else
            f"Create one standout payoff that nods to {slots.reference_game} while fitting this theme."
        )
    )

    design_goals = (
        [
            f"让玩家在 10 秒内理解“{mechanic or '核心玩法'}”。",
            f"通过 {theme} 主题建立第一眼识别度。",
            "确保每一轮都有明确的目标反馈和下一步驱动力。",
        ]
        if zh else
        [
            f"Teach the core loop of {mechanic or 'the main mechanic'} within the first 10 seconds.",
            f"Use the {theme} framing to create instant visual recognition.",
            "Give every round a clear payoff plus an obvious next action.",
        ]
    )

    return {
        "session_length": session_length_defaults.get(game_type, "short_bursts"),
        "progression_shape": progression_defaults.get(game_type, "score_chase"),
        "reward_loop": (reward_defaults_zh if zh else reward_defaults_en).get(game_type, ""),
        "signature_moment": signature_moment,
        "target_audience": target_audience,
        "tone": tone,
        "reference_style": (slots.visual_style or art_style or "").strip() or None,
        "complexity_budget": "standard",
        "teaching_mode": teaching_mode,
        "comedy_device": comedy_device,
        "design_goals": design_goals,
    }


def _history_to_messages(history: List[ConversationMessage]) -> List[Dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in history]


def _format_slot_summary(slots: SlotState) -> str:
    lines = []
    for field in ["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"]:
        value = getattr(slots, field)
        label = SLOT_LABELS.get(field, field)
        lines.append(f"  {label}: {value or '(unknown)'}")
    return "\n".join(lines)


def _build_slot_confidence_report(
    slots: SlotState,
    *,
    source_text: str = "",
    title: Optional[str] = None,
    history: Optional[List[ConversationMessage]] = None,
    updated_slots: Optional[List[str]] = None,
    authoritative_slots: Optional[List[str]] = None,
) -> Tuple[Dict[str, float], Dict[str, str], List[str]]:
    history = history or []
    updated_slots = updated_slots or []
    authoritative_slots = authoritative_slots or []
    authoritative = {slot for slot in authoritative_slots if slot}
    combined_text = " ".join(
        part
        for part in [title or "", source_text, *(item.content for item in history[-6:])]
        if str(part or "").strip()
    )
    language = _detect_ui_language(combined_text)
    zh = language.startswith("zh")
    confidence: Dict[str, float] = {}
    evidence: Dict[str, str] = {}
    ambiguity_flags: List[str] = []

    for field in ["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"]:
        value = getattr(slots, field, None)
        score, evidence_text, ambiguous = _score_slot_confidence(
            field,
            value,
            combined_text,
            updated=field in updated_slots,
            authoritative=field in authoritative,
            zh=zh,
        )
        confidence[field] = score
        if evidence_text:
            evidence[field] = evidence_text
        if value is None or str(value).strip() == "":
            ambiguity_flags.append(f"{field}:missing")
        elif ambiguous:
            ambiguity_flags.append(f"{field}:ambiguous")
        elif score < 0.72:
            ambiguity_flags.append(f"{field}:low_confidence")

    return confidence, evidence, ambiguity_flags


def _score_slot_confidence(
    field: str,
    value: Any,
    text: str,
    *,
    updated: bool,
    authoritative: bool,
    zh: bool,
) -> Tuple[float, str, bool]:
    value_text = ""
    if isinstance(value, list):
        value_text = " ".join(str(item).strip() for item in value if str(item).strip())
    else:
        value_text = str(value or "").strip()
    if not value_text:
        return 0.0, ("尚未明确" if zh else "Missing from the current brief."), False
    if authoritative:
        evidence = (
            "这是用户刚刚对当前问题给出的直接回答。"
            if zh else
            "This value comes directly from the user's latest answer to the current question."
        )
        return 0.97, evidence, False

    lowered_text = text.lower()
    explicit_terms = _slot_evidence_terms(field, value_text)
    matched_term = next((term for term in explicit_terms if term and term.lower() in lowered_text), None)
    base = 0.38
    ambiguous = False

    if matched_term:
        base = 0.88
    elif field == "game_type":
        explicit_type = _infer_explicit_game_type(text)
        if explicit_type == value_text:
            base = 0.82
        else:
            base = 0.58
            ambiguous = explicit_type is not None and explicit_type != value_text
    elif field == "input_method":
        input_hint = _infer_input_method_from_text(text)
        if input_hint and input_hint == value_text:
            base = 0.84
        elif input_hint and input_hint != value_text:
            base = 0.44
            ambiguous = True
        else:
            base = 0.52
    elif field == "difficulty":
        difficulty_hint = _infer_difficulty_from_text(text)
        if difficulty_hint and difficulty_hint == value_text:
            base = 0.8
        elif difficulty_hint and difficulty_hint != value_text:
            base = 0.4
            ambiguous = True
        else:
            base = 0.42
    elif field == "theme":
        base = 0.78 if matched_term else 0.56
    elif field == "win_condition":
        base = 0.78 if matched_term else 0.46
    elif field == "core_mechanic":
        base = 0.8 if matched_term else 0.6

    if updated:
        base += 0.04

    base = max(0.0, min(base, 0.97))
    if matched_term:
        evidence = (
            f"提到了“{matched_term}”，和当前{SLOT_LABELS.get(field, field)}一致。"
            if zh
            else f'Mentioned "{matched_term}", which matches the current {SLOT_LABELS.get(field, field)}.'
        )
    elif ambiguous:
        evidence = (
            "当前对话里出现了可能冲突的线索，系统先按更强信号做了归纳。"
            if zh
            else "The current brief contains conflicting hints, so this slot is only a best-effort guess."
        )
    else:
        evidence = (
            "主要根据当前创意和上下文做了推断。"
            if zh
            else "Mainly inferred from the current idea and surrounding context."
        )
    return round(base, 3), evidence, ambiguous


def _slot_evidence_terms(field: str, value_text: str) -> List[str]:
    normalized = value_text.lower()
    if field == "game_type":
        marker_map = {
            "casual": CASUAL_REQUEST_MARKERS,
            "puzzle": PUZZLE_REQUEST_MARKERS,
            "educational": EDUCATIONAL_REQUEST_MARKERS,
            "funny": FUNNY_REQUEST_MARKERS,
        }
        return list(marker_map.get(normalized, (value_text,)))
    if field == "input_method":
        marker_map = {
            "tap": ("tap", "click", "点击", "点按"),
            "touch": ("touch", "tap", "点击", "触屏"),
            "swipe": ("swipe", "slide", "滑动", "滑屏"),
            "drag": ("drag", "拖拽", "拖动"),
        }
        return list(marker_map.get(normalized, (value_text,)))
    if field == "difficulty":
        marker_map = {
            "easy": ("easy", "casual", "轻松", "简单", "休闲"),
            "medium": ("medium", "standard", "标准", "普通"),
            "hard": ("hard", "challenging", "困难", "硬核"),
            "progressive": ("progressive", "ramp", "越来越难", "逐步变难"),
        }
        return list(marker_map.get(normalized, (value_text,)))
    value_terms = [
        token.strip()
        for token in re.split(r"[\s,，、/]+", value_text)
        if token.strip()
    ]
    value_terms.append(value_text)
    return sorted(set(term for term in value_terms if len(term) >= 2), key=len, reverse=True)


def _infer_explicit_game_type(text: str) -> Optional[str]:
    lowered = text.lower()
    marker_map = {
        "funny": FUNNY_REQUEST_MARKERS,
        "educational": EDUCATIONAL_REQUEST_MARKERS,
        "puzzle": PUZZLE_REQUEST_MARKERS,
        "casual": CASUAL_REQUEST_MARKERS,
    }
    for game_type, markers in marker_map.items():
        if any(marker.lower() in lowered for marker in markers):
            return game_type
    return None


def _infer_input_method_from_text(text: str) -> Optional[str]:
    lowered = text.lower()
    marker_map = {
        "drag": ("drag", "拖拽", "拖动"),
        "swipe": ("swipe", "slide", "滑动", "滑屏"),
        "tap": ("tap", "click", "点击", "点按"),
        "touch": ("touch", "触屏"),
    }
    for input_method, markers in marker_map.items():
        if any(marker.lower() in lowered for marker in markers):
            return input_method
    return None


def _infer_difficulty_from_text(text: str) -> Optional[str]:
    lowered = text.lower()
    marker_map = {
        "easy": ("easy", "casual", "轻松", "简单", "休闲"),
        "medium": ("medium", "standard", "标准", "普通"),
        "hard": ("hard", "challenging", "困难", "硬核"),
        "progressive": ("progressive", "越来越难", "逐步变难", "ramp"),
    }
    for difficulty, markers in marker_map.items():
        if any(marker.lower() in lowered for marker in markers):
            return difficulty
    return None


def _build_plan_draft(
    slots: SlotState,
    *,
    source_text: str = "",
    title: Optional[str] = None,
    generation_tier: str = "standard",
    entry_mode: str = "create",
) -> PlanDraft:
    language = _detect_ui_language(" ".join(part for part in [title or "", source_text] if part))
    zh = language.startswith("zh")
    game_type = str(slots.game_type or "casual").strip() or "casual"
    game_type_label = _localized_game_type_name(game_type, zh)
    theme = str(
        slots.theme
        or _infer_theme_from_context(title or "", source_text)
        or (title or "").strip()
        or ("创意世界" if zh else "a clear world")
    ).strip()
    mechanic = str(
        slots.core_mechanic
        or (
            "一个直观、反馈快的主交互"
            if zh else
            "one readable main interaction with immediate feedback"
        )
    ).strip()
    objective = str(
        slots.win_condition
        or (
            "完成一轮短局目标"
            if zh else
            "finish a short round objective"
        )
    ).strip()
    input_method = str(slots.input_method or ("触屏" if zh else "touch controls")).strip()
    visual_style = str(slots.visual_style or ("鲜明移动端风格" if zh else "a bold mobile-first look")).strip()
    title_value = (title or "").strip() or _plan_title_fallback(game_type, theme, zh)
    pacing = _plan_pacing_text(game_type, slots.difficulty, generation_tier, entry_mode, zh)
    visual_direction = (
        f"以{theme}为主场景，整体视觉偏{visual_style}，强调移动端可读性和记忆点。"
        if zh else
        f"Set it in {theme} with a {visual_style} presentation that still reads clearly on mobile."
    )
    signature_moment = _plan_signature_moment(game_type, theme, mechanic, zh)
    summary = (
        f"这是一款{game_type_label}，围绕“{mechanic}”展开，在“{theme}”设定下，玩家通过{input_method}操作去{objective}。"
        if zh else
        f"This is a {game_type_label} game built around {mechanic}, set in {theme}, where the player uses {input_method} to {objective}."
    )
    concept = (
        f"把“{(title or '').strip() or theme}”这个创意包装成一局上手快、目标清晰的{game_type_label}体验。"
        if zh else
        f"Package {(title or '').strip() or theme} into a {game_type_label} experience that is easy to grasp and has a clear objective."
    )
    interaction = (
        f"核心交互是“{mechanic}”，输入方式以{input_method}为主，保证玩家每 3 到 5 秒都能获得一次明确反馈。"
        if zh else
        f"The core interaction is {mechanic}, primarily controlled through {input_method}, with clear feedback every few seconds."
    )

    return PlanDraft(
        title=title_value,
        summary=summary,
        concept=concept,
        interaction=interaction,
        objective=objective,
        pacing=pacing,
        visual_direction=visual_direction,
        signature_moment=signature_moment,
    )


def _localized_game_type_name(game_type: str, zh: bool) -> str:
    mapping = {
        "casual": "休闲小游戏" if zh else "casual mobile game",
        "puzzle": "益智小游戏" if zh else "puzzle mobile game",
        "educational": "教育小游戏" if zh else "educational mobile game",
        "funny": "搞笑小游戏" if zh else "comedic mobile game",
    }
    return mapping.get(game_type, game_type)


def _plan_title_fallback(game_type: str, theme: str, zh: bool) -> str:
    if zh:
        prefix = {
            "casual": "灵感挑战",
            "puzzle": "谜题实验室",
            "educational": "知识小挑战",
            "funny": "整活现场",
        }.get(game_type, "创意小游戏")
        return f"{prefix}·{theme}"
    prefix = {
        "casual": "Arcade Spark",
        "puzzle": "Puzzle Lab",
        "educational": "Learning Quest",
        "funny": "Comedy Jam",
    }.get(game_type, "Creative Game")
    return f"{prefix}: {theme}"


def _plan_pacing_text(
    game_type: str,
    difficulty: Optional[str],
    generation_tier: str,
    entry_mode: str,
    zh: bool,
) -> str:
    difficulty = str(difficulty or "progressive").strip() or "progressive"
    if zh:
        base = {
            "casual": "短局快反馈，玩家很快就能进入循环并看到分数或进度变化。",
            "puzzle": "每局都要快速进入题面，再逐步制造“差一步就成功”的解题张力。",
            "educational": "用短回合和即时纠错保证学习节奏，避免信息灌输感。",
            "funny": "以短局和高密度笑点推进，让每个回合都像一个小段子。",
        }.get(game_type, "保持移动端友好的短局节奏。")
        tier_line = {
            "safe": "整体结构保持克制，优先保证上手顺滑。",
            "standard": "节奏上增加一层递进或阶段变化，让游戏更完整。",
            "showcase": "加入更明显的阶段推进、高潮时刻和演出反馈，形成更像成品的节奏。",
        }.get(str(generation_tier or "standard"), "")
        mode_line = "如果是基于已有作品继续优化，优先围绕本次改动目标集中打磨。" if entry_mode in {"fork", "iterate"} else ""
        diff_line = f"难度目标偏“{difficulty}”。"
        return " ".join(part for part in [base, tier_line, mode_line, diff_line] if part)

    base = {
        "casual": "Keep the session short and punchy so the player quickly enters the loop and sees visible progress.",
        "puzzle": "Start each round quickly, then build tension around the final few moves.",
        "educational": "Use short rounds and immediate correction so the learning rhythm stays light.",
        "funny": "Drive the experience with short rounds and a dense cadence of comedic beats.",
    }.get(game_type, "Keep the pacing readable and mobile-friendly.")
    tier_line = {
        "safe": "Stay conservative and prioritize a smooth first-play experience.",
        "standard": "Add one extra layer of progression or phase changes so the game feels fuller.",
        "showcase": "Add clearer phase transitions, highlight moments, and stronger presentation beats.",
    }.get(str(generation_tier or "standard"), "")
    mode_line = "Since this starts from an existing game, focus the pacing around the requested change." if entry_mode in {"fork", "iterate"} else ""
    diff_line = f"Target difficulty: {difficulty}."
    return " ".join(part for part in [base, tier_line, mode_line, diff_line] if part)


def _plan_signature_moment(game_type: str, theme: str, mechanic: str, zh: bool) -> str:
    if zh:
        mapping = {
            "casual": f"在{theme}主题里，让“{mechanic}”触发一次明显的连击、险胜或收集高潮。",
            "puzzle": "制造一个只差一步就解开的关键盘面，让玩家记住破解瞬间。",
            "educational": "把关键知识点变成一次“答对就立刻被点亮/解锁”的高光时刻。",
            "funny": f"把“{mechanic}”做成一次夸张反转或连锁笑点，让玩家愿意分享。",
        }
        return mapping.get(game_type, "安排一个足够清晰、值得记住的高光时刻。")
    mapping = {
        "casual": f"Turn {mechanic} into a clear combo, clutch dodge, or collection spike inside the {theme} theme.",
        "puzzle": "Create a final near-solved board state that makes the breakthrough memorable.",
        "educational": "Turn the key learning beat into an immediate unlock or reveal moment.",
        "funny": f"Use {mechanic} to trigger an exaggerated reversal or comedic chain reaction.",
    }
    return mapping.get(game_type, "Create one obvious signature moment the player can remember.")


def _build_dialogue_question(
    slots: SlotState,
    *,
    skipped_slots: Optional[List[str]] = None,
    blocked_slots: Optional[List[str]] = None,
    source_text: str = "",
    entry_mode: str = "create",
    confidence_by_slot: Optional[Dict[str, float]] = None,
    ambiguity_flags: Optional[List[str]] = None,
) -> Tuple[Optional[DialogueQuestion], Optional[QuestionStrategy]]:
    skipped = {
        str(item).strip()
        for item in (skipped_slots or [])
        if str(item).strip()
    }
    blocked = {
        str(item).strip()
        for item in (blocked_slots or [])
        if str(item).strip()
    }
    confidence_by_slot = confidence_by_slot or {}
    ambiguity_flags = ambiguity_flags or []
    best_slot: Optional[str] = None
    best_score = -1.0
    best_mode = "missing_required"
    best_confidence = 0.0
    best_ambiguity_weight = 0.0
    language = _detect_ui_language(source_text or " ".join(
        str(getattr(slots, field, "") or "")
        for field in ["theme", "core_mechanic", "win_condition"]
    ))
    zh = language.startswith("zh")
    for slot_key in ["core_mechanic", "win_condition", "input_method", "theme", "game_type", "difficulty"]:
        if slot_key in skipped or slot_key in blocked:
            continue
        value = getattr(slots, slot_key, None)
        missing = value is None or str(value).strip() == ""
        confidence = float(confidence_by_slot.get(slot_key, 0.0 if missing else 0.5))
        ambiguity_weight = 0.25 if any(flag.startswith(f"{slot_key}:") for flag in ambiguity_flags) else 0.0
        impact = SLOT_IMPACT_WEIGHTS.get(slot_key, 0.4) + ENTRY_MODE_SLOT_BIAS.get(entry_mode, {}).get(slot_key, 0.0)
        if missing:
            score = impact + 0.45 + ambiguity_weight
            mode = "missing_required"
        else:
            threshold = 0.72 if slot_key in {"core_mechanic", "win_condition", "input_method", "game_type"} else 0.64
            if confidence >= threshold:
                continue
            score = impact + max(0.0, threshold - confidence) + ambiguity_weight
            mode = "ambiguity_resolution" if ambiguity_weight > 0 else "low_confidence"
        if score > best_score:
            best_score = score
            best_slot = slot_key
            best_mode = mode
            best_confidence = confidence
            best_ambiguity_weight = ambiguity_weight

    if not best_slot:
        return None, None

    slot_key = best_slot
    prompts = {
        "game_type": (
            "这个游戏更偏休闲、益智、教育还是搞笑？",
            "Game Type",
        ),
        "core_mechanic": (
            "玩家在这个游戏里最核心、最常做的操作是什么？",
            "Core Mechanic",
        ),
        "theme": (
            "你希望游戏呈现什么主题、世界观或情境？",
            "Theme",
        ),
        "input_method": (
            "玩家主要通过点击、滑动还是拖拽来操作？",
            "Input Method",
        ),
        "win_condition": (
            "这一局里玩家怎样算赢，或者达成了什么目标？",
            "Win Condition",
        ),
        "difficulty": (
            "整体难度你更想要轻松、标准还是逐步变难？",
            "Difficulty",
        ),
    }
    en_prompts = {
        "game_type": ("Should this feel more casual, puzzle, educational, or funny?", "Game Type"),
        "core_mechanic": ("What is the main thing the player does most of the time?", "Core Mechanic"),
        "theme": ("What theme, world, or situation should the game use?", "Theme"),
        "input_method": ("Should the player mainly tap, swipe, or drag?", "Input Method"),
        "win_condition": ("What counts as winning or clearing the round?", "Win Condition"),
        "difficulty": ("Should the difficulty feel easy, standard, or progressively harder?", "Difficulty"),
    }
    prompt, label = (prompts if zh else en_prompts).get(slot_key, (
        "请再补充一个关键设定。",
        SLOT_LABELS.get(slot_key, slot_key),
    ))
    if zh:
        if best_mode == "missing_required":
            reason = f"因为“{label}”会直接决定玩法能否成型，而当前还没有明确答案。"
        elif best_mode == "ambiguity_resolution":
            reason = f"因为“{label}”目前存在歧义，我想先把方向锁准，避免后续生成跑偏。"
        else:
            reason = f"因为“{label}”会明显影响生成质量，但当前把握度只有 {round(best_confidence * 100)}%。"
    else:
        if best_mode == "missing_required":
            reason = f'I am asking about "{label}" because it directly shapes the game and is still missing.'
        elif best_mode == "ambiguity_resolution":
            reason = f'I am asking about "{label}" because there are conflicting hints and I want to lock the direction before generation.'
        else:
            reason = f'I am asking about "{label}" because it strongly affects quality and the current confidence is only {round(best_confidence * 100)}%.'

    return (
        DialogueQuestion(
            slot_key=slot_key,
            label=label,
            prompt=prompt,
            skippable=True,
        ),
        QuestionStrategy(
            mode=best_mode,
            slot_key=slot_key,
            reason=reason,
            impact=round(max(0.0, min(SLOT_IMPACT_WEIGHTS.get(slot_key, 0.4), 1.5)), 3),
            confidence=round(max(0.0, min(best_confidence, 1.0)), 3),
            ambiguity_weight=round(max(0.0, min(best_ambiguity_weight, 1.0)), 3),
        ),
    )


def _compose_creation_session_reply(
    *,
    slots: SlotState,
    current_question: Optional[DialogueQuestion],
    ready_to_generate: bool,
    source_text: str = "",
    title: Optional[str] = None,
) -> str:
    language = _detect_ui_language(" ".join(part for part in [title or "", source_text] if part))
    zh = language.startswith("zh")
    summary_bits = [
        str(slots.game_type or "").strip(),
        str(slots.theme or "").strip(),
        str(slots.core_mechanic or "").strip(),
    ]
    compact_summary = " / ".join(bit for bit in summary_bits if bit) or ("这款游戏" if zh else "this game")

    if ready_to_generate and current_question:
        if zh:
            return (
                f"我已经整理出一版可以开始生成的方案了，目前方向是：{compact_summary}。"
                f"如果你愿意继续打磨，我还想确认一个点：{current_question.prompt}"
            )
        return (
            f"I already have a solid draft for generation: {compact_summary}. "
            f"If you want to refine it a bit more, one helpful detail is: {current_question.prompt}"
        )

    if ready_to_generate:
        if zh:
            return f"我已经整理出一版可生成方案了，当前方向是：{compact_summary}。如果满意，可以直接开始创作。"
        return f"I have enough to generate a strong first version: {compact_summary}. You can start creating now."

    if current_question:
        if zh:
            return f"我先补一个最关键的信息：{current_question.prompt}"
        return f"Let me lock one key detail first: {current_question.prompt}"

    return "请再补充一点你想要的游戏方向。" if zh else "Tell me one more thing about the direction you want."


def _fallback_reply(state: DialogueState, slots: SlotState) -> str:
    missing = slots.missing_required()
    if state == DialogueState.greeting:
        return "Tell me the kind of game you want to build."
    if not missing:
        game_type = slots.game_type or "game"
        return (
            f"I understand the direction: a {game_type} game themed around "
            f"{slots.theme or 'your idea'}. I can generate it once you confirm."
        )
    next_field = missing[0]
    prompts = {
        "game_type": "What kind of game is it: casual, puzzle, educational, funny, or another light mobile-friendly direction?",
        "core_mechanic": "What does the player do most of the time?",
        "theme": "What theme or world should the game use?",
        "input_method": "How should the player control it on mobile?",
        "win_condition": "What counts as winning or clearing the game?",
        "difficulty": "Should the difficulty be easy, medium, hard, or progressive?",
    }
    return prompts.get(next_field, "Tell me a bit more about the game you want.")
