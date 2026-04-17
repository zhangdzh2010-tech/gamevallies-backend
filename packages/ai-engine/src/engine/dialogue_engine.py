"""Intent parsing and slot-normalization utilities."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..api.models import (
    CoreMechanic,
    GameEntity,
    GameRules,
    GameSpec,
    PlatformConstraints,
    SlotState,
    VisualStyle,
)
from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from ..services.llm_client import LLMClient, LLMResponseTruncatedError
from .prompt_store import require_prompt

logger = logging.getLogger(__name__)

CURATED_GAME_TYPES = ("casual", "puzzle", "educational", "funny")


def _intent_parse_request_timeout_s() -> int:
    return get_timeout_int(
        "timeout.ai_engine.intent_parse.request_s",
        int(getattr(settings, "INTENT_PARSE_REQUEST_TIMEOUT_S", 45) or 45),
        min_value=1,
    )


def _intent_parse_overall_timeout_s() -> int:
    request_timeout_s = _intent_parse_request_timeout_s()
    return get_timeout_int(
        "timeout.ai_engine.intent_parse.overall_s",
        max(request_timeout_s, int(getattr(settings, "INTENT_PARSE_OVERALL_TIMEOUT_S", 90) or 90)),
        min_value=request_timeout_s,
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
    "\u6478\u9c7c",
    "office",
)

REFERENCE_GAME_HINTS: Dict[str, Dict[str, str]] = {
    "\u7f8a\u4e86\u4e2a\u7f8a": {
        "game_type": "puzzle",
        "core_mechanic": "\u70b9\u6309\u5c42\u53e0\u5361\u7247\uff0c\u628a\u540c\u7c7b\u5143\u7d20\u6536\u7eb3\u6210\u4e09\u6d88\u7ec4\u5408",
        "input_method": "tap",
        "win_condition": "\u6e05\u7a7a\u5f53\u524d\u5c42\u53e0\u724c\u9762\u6216\u5b8c\u6210\u5173\u5361\u76ee\u6807",
        "difficulty": "progressive",
        "signature_zh": "\u5c42\u53e0\u5361\u7247 + \u4e09\u6d88\u6536\u7eb3 + \u524d\u677e\u540e\u7d27\u7684\u5173\u5361\u8282\u594f",
        "signature_en": "stacked tiles, triple-match collection, and a gentle-to-spiky level curve",
    },
    "2048": {
        "game_type": "puzzle",
        "core_mechanic": "\u6ed1\u52a8\u5408\u5e76\u76f8\u540c\u6570\u5b57\u5757\uff0c\u6301\u7eed\u505a\u5927\u724c\u9762",
        "input_method": "swipe",
        "win_condition": "\u5408\u6210\u76ee\u6807\u6570\u5b57\u6216\u5237\u51fa\u66f4\u9ad8\u5206\u6570",
        "difficulty": "progressive",
        "signature_zh": "\u6ed1\u52a8\u5408\u5e76 + \u724c\u9762\u7a7a\u95f4\u538b\u529b",
        "signature_en": "swipe-based merging with escalating board pressure",
    },
    "flappy bird": {
        "game_type": "casual",
        "core_mechanic": "\u8282\u594f\u70b9\u6309\u7ef4\u6301\u98de\u884c\uff0c\u7a7f\u8fc7\u72ed\u7a84\u969c\u788d\u7f1d\u9699",
        "input_method": "tap",
        "win_condition": "\u8fde\u7eed\u901a\u8fc7\u66f4\u591a\u969c\u788d\u83b7\u5f97\u66f4\u9ad8\u5206\u6570",
        "difficulty": "progressive",
        "signature_zh": "\u4e00\u952e\u8282\u594f\u98de\u884c + \u9ad8\u5931\u8bef\u6210\u672c",
        "signature_en": "one-tap rhythm flight with punishing obstacle gaps",
    },
    "temple run": {
        "game_type": "casual",
        "core_mechanic": "\u8fb9\u8dd1\u8fb9\u95ea\u907f\u969c\u788d\uff0c\u901a\u8fc7\u8f6c\u5411\u3001\u8df3\u8dc3\u548c\u4e0b\u6ed1\u4fdd\u6301\u8fde\u7eed\u524d\u8fdb",
        "input_method": "swipe",
        "win_condition": "\u5c3d\u53ef\u80fd\u8dd1\u5f97\u66f4\u8fdc\u5e76\u6536\u96c6\u66f4\u591a\u5956\u52b1",
        "difficulty": "progressive",
        "signature_zh": "\u65e0\u5c3d\u5954\u8dd1 + \u524d\u65b9\u5371\u9669\u9884\u5224",
        "signature_en": "endless running with rapid forward hazard reads",
    },
}

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
    ("ocean", ("ocean", "sea", "underwater", "reef", "coast", "marine", "\u6d77", "\u6d0b", "\u6d77\u5e95", "\u6d77\u5cb8")),
    ("forest", ("forest", "jungle", "tree", "\u68ee\u6797", "\u4e1b\u6797", "\u6811")),
    ("city", ("city", "urban", "metro", "subway", "street", "traffic", "commute", "\u57ce\u5e02", "\u8857", "\u4ea4\u901a", "\u901a\u52e4", "\u5730\u94c1", "\u90fd\u5e02")),
    ("food", ("food", "kitchen", "chef", "candy", "dessert", "\u98df\u7269", "\u53a8\u623f", "\u7cd6", "\u751c\u54c1")),
    ("toy", ("toy", "block", "brick", "\u73a9\u5177", "\u79ef\u6728", "\u65b9\u5757")),
    ("fantasy", ("fantasy", "magic", "dragon", "\u5947\u5e7b", "\u9b54\u6cd5", "\u9f99")),
)

EXPLICIT_GAME_TYPE_MARKERS: Dict[str, Tuple[str, ...]] = {
    "casual": (
        "casual",
        "arcade",
        "runner",
        "lane runner",
        "endless runner",
        "parkour",
        "\u4f11\u95f2",
        "\u8857\u673a",
        "\u8dd1\u9177",
    ),
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
    ("swipe", ("swipe", "slide", "flick", "touch tap swipe", "touch swipe", "滑动", "滑屏", "划动")),
    ("drag", ("drag", "dragging", "pull", "拖拽", "拖动")),
    ("touch", ("touch only", "touch control", "touch", "纯触摸", "触摸")),
    ("tap", ("tap", "click", "touch tap", "点击", "点按", "轻触")),
    ("hold", ("hold", "press", "long press", "长按")),
)

NORMALIZED_DIFFICULTY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("easy", ("easy", "relaxed", "simple", "casual", "简单", "轻松", "休闲")),
    ("medium", ("medium", "normal", "standard", "balanced", "标准", "适中", "普通", "中等")),
    ("hard", ("hard", "difficult", "challenging", "brutal", "困难", "很难", "硬核", "挑战")),
    ("progressive", ("progressive", "escalating", "ramping", "递进", "逐步升级", "越来越难", "逐步变难")),
)

INPUT_METHOD_PRIMARY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tap", (
        "主要是点击",
        "主要用点击",
        "主要通过点击",
        "以点击为主",
        "点击为主",
        "点击选项为主",
        "点按为主",
        "以点按为主",
        "轻触为主",
        "mainly tap",
        "primarily tap",
        "mainly click",
        "primarily click",
        "tap is the main control",
        "click is the main control",
    )),
    ("swipe", (
        "主要是滑动",
        "主要用滑动",
        "主要通过滑动",
        "以滑动为主",
        "滑动为主",
        "mainly swipe",
        "primarily swipe",
        "swipe is the main control",
    )),
    ("drag", (
        "主要是拖拽",
        "主要用拖拽",
        "主要通过拖拽",
        "以拖拽为主",
        "拖拽为主",
        "mainly drag",
        "primarily drag",
        "drag is the main control",
    )),
    ("touch", (
        "只用触摸",
        "纯触摸",
        "touch only",
        "only touch",
    )),
)

DIFFICULTY_PRIORITY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("progressive", (
        "逐步变难",
        "越来越难",
        "难度递进",
        "递进变难",
        "逐步升级",
        "前松后紧",
        "先简单后变难",
        "前面轻松后面变难",
        "后面逐步变难",
        "越往后越难",
        "ramping up",
        "ramps up",
        "gets harder over time",
    )),
)

NEGATED_MARKER_PREFIXES: tuple[str, ...] = (
    "不",
    "别",
    "无",
    "非",
    "没",
    "不用",
    "不要",
    "不做",
    "不是",
    "无需",
    "not ",
    "no ",
    "without ",
    "dont ",
    "don't ",
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
                for item in re.split(r"\s*(?:[;|,\n]+|[；、])\s*", value)
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

    if field == "input_method":
        normalized_input_method = _infer_input_method_from_text(text)
        if normalized_input_method:
            return normalized_input_method
    if field == "difficulty":
        normalized_difficulty = _infer_difficulty_from_text(text)
        if normalized_difficulty:
            return normalized_difficulty
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

def _shorten_text(text: str, *, limit: int = 36) -> str:
    normalized = _normalize_free_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip(" ,.!?;:") + "..."


def _canonical_reference_key(value: str) -> str:
    normalized = re.sub(r"[\u300a\u300b\"'\u201c\u201d\u2018\u2019]", "", _normalize_free_text(value)).strip().lower()
    return normalized


_REFERENCE_GAME_LOWERCASE_ARTICLES = {"a", "an", "the", "this", "that", "these", "those"}
_REFERENCE_GAME_BANNED_PROSE_WORDS = {
    "advanced",
    "readable",
    "mobile",
    "prototype",
    "premium",
    "rough",
    "jam",
    "style",
    "vibe",
    "feel",
    "feels",
    "feeling",
    "mechanic",
    "mechanics",
    "resource",
    "resources",
    "boost",
    "checkpoint",
    "checkpoints",
    "mission",
    "missions",
    "objective",
    "objectives",
}


def _sanitize_reference_game_candidate(candidate: str) -> Optional[str]:
    normalized = _normalize_free_text(candidate)
    if not normalized:
        return None
    canonical_key = _canonical_reference_key(normalized)
    if any(canonical_key == _canonical_reference_key(name) for name in REFERENCE_GAME_HINTS):
        return normalized

    for name in REFERENCE_GAME_HINTS:
        if canonical_key.startswith(_canonical_reference_key(name)):
            suffix = normalized[len(name):].strip()
            if not suffix:
                return name
            if (
                len(suffix) <= 12
                and re.fullmatch(r"[\s\u4e00-\u9fffA-Za-z0-9]+", suffix)
                and re.search(r"(?:小游戏|游戏|玩法|跑酷|消除|闯关|版本|那种|这种|这款|那个)", suffix)
            ):
                return name

    if re.search(r"[\u4e00-\u9fff]", normalized):
        simplified = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", normalized)
        if not simplified or len(simplified) > 16:
            return None
        return normalized

    words = re.findall(r"[A-Za-z0-9]+", normalized)
    if not words or len(words) > 4:
        return None
    if normalized == normalized.lower() and words[0].lower() in _REFERENCE_GAME_LOWERCASE_ARTICLES:
        return None
    if any(word.lower() in _REFERENCE_GAME_BANNED_PROSE_WORDS for word in words):
        return None
    return normalized


def _extract_reference_game_from_text(text: str) -> Optional[str]:
    source = _normalize_free_text(text)
    if not source:
        return None

    patterns = [
        r"(?:\u7c7b\u4f3c|\u50cf|\u53c2\u8003|\u501f\u9274|\u81f4\u656c)\s*[\u300a\"\u201c]?([^\u300a\u300b\"\u201c\u201d\n,\uFF0C\u3002\uFF1F\uFF01]{2,28})[\u300b\"\u201d]?",
        r"(?:^|[\s(\uff08])(?:inspired by|similar to|based on|like)\s+[\u300a\"\u201c]?([^\u300a\u300b\"\u201c\u201d\n,\uFF0C\u3002\uFF1F\uFF01;:]{2,32})[\u300b\"\u201d]?",
        r"[\u300a\"\u201c]([^\u300a\u300b\"\u201c\u201d\n,\uFF0C\u3002\uFF1F\uFF01]{2,28})[\u300b\"\u201d]\s*(?:\u8fd9\u6837\u7684)?\u6e38\u620f",
    ]
    if _looks_like_understanding_check(source):
        patterns.append(r"([^\u300a\u300b\"\u201c\u201d\n,\uFF0C\u3002\uFF1F\uFF01]{2,24})\s*\u7684\u6e38\u620f")
    stop_words = {
        "\u6e38\u620f",
        "\u8fd9\u4e2a",
        "\u8fd9\u79cd",
        "\u4e00\u4e2a",
        "\u4e00\u6b3e",
        "game",
        "games",
        "one",
    }
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = re.sub(r"^(?:\u50cf|\u7c7b\u4f3c|\u53c2\u8003|like|similar to|inspired by)\s+", "", match.group(1), flags=re.IGNORECASE)
        candidate = re.sub(
            r"\s*(?:\u90a3\u79cd|\u8fd9\u79cd|\u8fd9\u6b3e|\u90a3\u4e2a)(?:[\u4e00-\u9fffA-Za-z0-9\s]{0,12})?(?:\u5c0f?\u6e38\u620f|\u73a9\u6cd5)?$",
            "",
            candidate,
        ).strip(" \u300a\u300b\"\u201c\u201d.,;:!?")
        candidate = re.sub(r"\s*\u7684\u6e38\u620f$", "", candidate).strip(" \u300a\u300b\"\u201c\u201d.,;:!?")
        candidate = re.sub(r"\s+(?:style|vibe|feel|prototype)\b.*$", "", candidate, flags=re.IGNORECASE)
        if len(candidate) < 2:
            continue
        if candidate.lower() in stop_words:
            continue
        sanitized = _sanitize_reference_game_candidate(candidate)
        if sanitized:
            return sanitized
    return None


def _reference_game_hints(reference_game: Optional[str]) -> Dict[str, str]:
    if not reference_game:
        return {}
    key = _canonical_reference_key(reference_game)
    for name, hints in REFERENCE_GAME_HINTS.items():
        if key == _canonical_reference_key(name):
            return hints
    return {}


def _looks_like_understanding_check(text: str) -> bool:
    normalized = _normalize_free_text(text).lower()
    if not normalized:
        return False
    markers = (
        "\u4f60\u4e86\u89e3\u5417",
        "\u4f60\u61c2\u5417",
        "\u4f60\u77e5\u9053\u5417",
        "\u4f60\u6709\u6982\u5ff5\u5417",
        "\u4f60\u660e\u767d\u5417",
        "do you know",
        "are you familiar with",
        "do you understand",
        "you know",
        "familiar with",
    )
    return any(marker in normalized for marker in markers)


def _contains_marker(text: str, marker: str) -> bool:
    normalized_text = text.lower()
    normalized_marker = marker.lower()
    if re.search(r"[a-z0-9]", normalized_marker):
        pattern = rf"(?<![a-z0-9]){re.escape(normalized_marker)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None
    return marker in text


def _contains_any_marker(text: str, markers: Tuple[str, ...]) -> bool:
    return any(_contains_marker(text, marker) for marker in markers)


def _count_marker_matches(text: str, markers: Tuple[str, ...]) -> int:
    return sum(1 for marker in markers if _contains_marker(text, marker))


def _infer_contextual_game_type(text: str) -> Optional[str]:
    normalized = _normalize_free_text(text)
    if not normalized:
        return None
    explicit = _infer_explicit_game_type(normalized)
    if explicit:
        return explicit

    best_game_type: Optional[str] = None
    best_score = 0
    best_priority = len(CURATED_GAME_TYPES)
    for priority, game_type in enumerate(("educational", "funny", "casual", "puzzle")):
        markers = {
            "educational": EDUCATIONAL_REQUEST_MARKERS,
            "funny": FUNNY_REQUEST_MARKERS,
            "casual": CASUAL_REQUEST_MARKERS,
            "puzzle": PUZZLE_REQUEST_MARKERS,
        }[game_type]
        score = _count_marker_matches(normalized, markers)
        if score <= 0:
            continue
        if score > best_score or (score == best_score and priority < best_priority):
            best_score = score
            best_priority = priority
            best_game_type = game_type
    return best_game_type


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


def _looks_like_casual_request(*texts: str) -> bool:
    normalized_texts = [_normalize_free_text(text) for text in texts if _normalize_free_text(text)]
    if not normalized_texts:
        return False
    combined = " ".join(normalized_texts)
    return any(_contains_marker(combined, marker) for marker in CASUAL_REQUEST_MARKERS)


def _normalize_game_type_label(game_type: str, *texts: str) -> str:
    normalized = re.sub(r"[^a-z_-]+", " ", (game_type or "").strip().lower()).strip()
    normalized = normalized.replace("-", " ")
    normalized = re.sub(r"\s+", " ", normalized)
    mapped = LEGACY_GAME_TYPE_ALIASES.get(normalized, normalized)

    context = " ".join(_normalize_free_text(text) for text in texts if _normalize_free_text(text))
    explicit_context = _infer_explicit_game_type(context) if context else None
    educational_context = _looks_like_educational_request(context) if context else False
    funny_context = _looks_like_funny_request(context) if context else False
    puzzle_context = _looks_like_puzzle_request(context) if context else False
    casual_context = _looks_like_casual_request(context) if context else False
    if mapped in CURATED_GAME_TYPES:
        if explicit_context and explicit_context != mapped:
            return explicit_context
        if mapped == "casual":
            if educational_context:
                return "educational"
            if funny_context:
                return "funny"
            if puzzle_context:
                return "puzzle"
            return mapped
        if mapped == "puzzle" and not puzzle_context:
            if educational_context:
                return "educational"
            if funny_context:
                return "funny"
            if casual_context:
                return "casual"
        if mapped == "funny" and not funny_context:
            if educational_context:
                return "educational"
            if puzzle_context:
                return "puzzle"
            if casual_context:
                return "casual"
        return mapped
    if educational_context:
        return "educational"
    if funny_context:
        return "funny"
    if puzzle_context:
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
        reliable_markers = tuple(
            marker
            for marker in markers
            if re.search(r"[a-z0-9]", marker.lower()) or len(marker.strip()) > 1
        )
        if reliable_markers and _contains_any_marker(combined, reliable_markers):
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

    contextual_game_type = _infer_contextual_game_type(source)
    if contextual_game_type:
        inferred["game_type"] = contextual_game_type

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

    reference_game = _extract_reference_game_from_text(source)
    if reference_game:
        inferred["reference_game"] = reference_game
        reference_hints = _reference_game_hints(reference_game)
        for field in ("game_type", "core_mechanic", "input_method", "win_condition", "difficulty"):
            if reference_hints.get(field) and not inferred.get(field):
                inferred[field] = reference_hints[field]
    explicit_theme = _infer_theme_from_context(source)
    if explicit_theme:
        inferred["theme"] = explicit_theme

    game_type = inferred.get("game_type")
    defaults = _localized_game_type_defaults(str(game_type), ui_language) if game_type else {}
    should_apply_generic_defaults = bool(game_type) and (
        bool(reference_game)
        or _looks_like_sparse_request(source)
        or len(_normalize_free_text(source)) <= 120
    )

    if should_apply_generic_defaults and defaults.get("core_mechanic"):
        inferred.setdefault("core_mechanic", defaults["core_mechanic"])
    if should_apply_generic_defaults and defaults.get("win_condition"):
        inferred.setdefault("win_condition", defaults["win_condition"])
    if should_apply_generic_defaults and defaults.get("input_method"):
        inferred.setdefault("input_method", defaults["input_method"])
    if "\u91cd\u65b0\u5f00\u59cb" in source or "restart" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + [
            "\u5931\u8d25\u540e\u53ef\u4ee5\u91cd\u65b0\u5f00\u59cb" if ui_language == "zh-CN" else "Restart after losing",
        ]
    if "\u70b9\u51fb\u5f00\u59cb" in source or "tap to start" in lowered or "click to start" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + [
            "\u70b9\u51fb\u5f00\u59cb" if ui_language == "zh-CN" else "Tap to start",
        ]

    return inferred


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
    if _looks_like_understanding_check(normalized_answer):
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
    if field == "input_method":
        return _normalize_slot_text_value(field, normalized_answer) or inferred_slot_data.get(field)
    if field == "difficulty":
        return _normalize_slot_text_value(field, normalized_answer) or inferred_slot_data.get(field)
    if field == "special_rules":
        return [normalized_answer]
    if field == "theme" and inferred_slot_data.get(field):
        return inferred_slot_data.get(field)
    if field in SlotState.model_fields:
        return _normalize_slot_text_value(field, normalized_answer)
    return None


class DialogueEngine:
    """Intent parser with slot repair and GameSpec synthesis."""

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
        request_timeout_s: Optional[int] = None,
        overall_timeout_s: Optional[int] = None,
        timeout_retry_attempts: int = 0,
        timeout_retry_increment_s: int = 30,
        timeout_retry_max_s: Optional[int] = None,
    ) -> str:
        effective_request_timeout_s = (
            max(1, int(request_timeout_s))
            if request_timeout_s is not None
            else None
        )
        effective_overall_timeout_s = (
            max(effective_request_timeout_s or 1, int(overall_timeout_s))
            if overall_timeout_s is not None
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
                compression_policy="intent_parse",
                request_timeout_s=effective_request_timeout_s,
                overall_timeout_s=effective_overall_timeout_s,
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
                compression_policy="intent_parse",
                request_timeout_s=effective_request_timeout_s,
                overall_timeout_s=effective_overall_timeout_s,
                truncation_retry_attempts=1,
                truncation_retry_increment=512,
                truncation_retry_max_tokens=max(max_tokens, 2048),
                timeout_retry_attempts=max(1, int(timeout_retry_attempts or 0)),
                timeout_retry_increment_s=max(1, int(timeout_retry_increment_s)),
                timeout_retry_max_s=timeout_retry_max_s or 120,
            )
        except Exception as exc:
            normalized_message = str(exc or "").lower()
            is_timeout_like = "timed out" in normalized_message or "timeout" in normalized_message
            if not is_timeout_like or int(timeout_retry_attempts or 0) <= 0:
                raise
            logger.warning(
                "LLM %s request timed out; retrying with extended timeout budget (request=%ss overall=%ss)",
                step_key,
                effective_request_timeout_s,
                effective_overall_timeout_s,
            )
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
                compression_policy="intent_parse",
                request_timeout_s=effective_request_timeout_s,
                overall_timeout_s=effective_overall_timeout_s,
                truncation_retry_attempts=1,
                truncation_retry_increment=512,
                truncation_retry_max_tokens=max(max_tokens, 2048),
                timeout_retry_attempts=max(1, int(timeout_retry_attempts)),
                timeout_retry_increment_s=max(1, int(timeout_retry_increment_s)),
                timeout_retry_max_s=timeout_retry_max_s or 120,
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
        intent_parse_request_timeout_s = _intent_parse_request_timeout_s()
        intent_parse_overall_timeout_s = _intent_parse_overall_timeout_s()
        text = await self._complete_slot_request(
            max_tokens=640,
            system=_with_slot_json_contract(
                require_prompt("prompt.intent_parse_system")
            ),
            messages=[{"role": "user", "content": parse_input}],
            step_key="intent_parse",
            stage="intent_parsing",
            request_timeout_s=intent_parse_request_timeout_s,
            overall_timeout_s=intent_parse_overall_timeout_s,
            timeout_retry_attempts=1,
            timeout_retry_increment_s=30,
            timeout_retry_max_s=max(intent_parse_overall_timeout_s, intent_parse_request_timeout_s + 30),
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
        "casual": "\u7528\u5feb\u901f\u53cd\u9988\u3001\u5f97\u5206\u4e0a\u6da8\u548c\u77ed\u5c40\u80dc\u5229\u7ef4\u6301\u8282\u594f\u3002",
        "puzzle": "\u8ba9\u6bcf\u4e00\u6b65\u89e3\u9898\u90fd\u5e26\u6765\u66f4\u6e05\u6670\u7684\u68cb\u76d8\u8fdb\u5c55\u548c\u5173\u5361\u63a8\u8fdb\u3002",
        "educational": "\u7528\u5373\u65f6\u53cd\u9988\u548c\u9636\u6bb5\u6027\u91cc\u7a0b\u7891\u5f3a\u5316\u5b66\u4e60\u6210\u5c31\u611f\u3002",
        "funny": "\u7528\u4e0d\u65ad\u5347\u7ea7\u7684\u7b11\u70b9\u3001\u53cd\u8f6c\u548c\u5938\u5f20\u53cd\u9988\u5236\u9020\u723d\u611f\u3002",
    }
    reward_defaults_en = {
        "casual": "Keep the player engaged with quick score climbs, pickups, and short win streaks.",
        "puzzle": "Reward each solve step with clearer board progress and level advancement.",
        "educational": "Reinforce learning through immediate correctness feedback and visible milestone progress.",
        "funny": "Reward the player with escalating punchlines, reversals, and expressive payoffs.",
    }

    office_markers = ["office", "work", "boss", "\u529e\u516c\u5ba4", "\u4e0a\u73ed", "\u8001\u677f"]
    quiz_markers = ["quiz", "question", "\u95ee\u7b54", "\u7b54\u9898", "\u9009\u62e9\u9898"]
    experiment_markers = ["experiment", "lab", "\u5b9e\u9a8c", "\u5b9e\u9a8c\u5ba4"]
    boss_markers = ["boss", "manager", "\u8001\u677f", "\u4e3b\u7ba1"]
    mistake_markers = ["mistake", "error", "\u5931\u8bef", "\u5c34\u5c2c", "\u793e\u6b7b"]

    target_audience = (
        "students"
        if game_type == "educational"
        else "office meme players"
        if _contains_any_marker(normalized, office_markers)
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
            "\u5b66\u751f\u548c\u8bfe\u5802\u7528\u6237"
            if game_type == "educational"
            else "\u529e\u516c\u5ba4\u6897\u6e38\u620f\u73a9\u5bb6"
            if _contains_any_marker(normalized, office_markers)
            else "\u79fb\u52a8\u4f11\u95f2\u73a9\u5bb6"
        )
        tone = (
            "\u8f7b\u677e\u6d3b\u6cfc"
            if game_type == "casual"
            else "\u597d\u5947\u4e14\u6709\u5956\u52b1\u611f"
            if game_type == "puzzle"
            else "\u9f13\u52b1\u6027\u5f3a\u4e14\u8bf4\u660e\u6e05\u6670"
            if game_type == "educational"
            else "\u8352\u8bde\u5938\u5f20"
        )

    teaching_mode = None
    if game_type == "educational":
        if _contains_any_marker(normalized, quiz_markers):
            teaching_mode = "guided_quiz"
        elif _contains_any_marker(normalized, experiment_markers):
            teaching_mode = "interactive_experiment"
        else:
            teaching_mode = "guided_exploration"

    comedy_device = None
    if game_type == "funny":
        if _contains_any_marker(normalized, boss_markers):
            comedy_device = "near_miss_reversal"
        elif _contains_any_marker(normalized, mistake_markers):
            comedy_device = "awkward_escalation"
        else:
            comedy_device = "surprise_punchline"

    signature_moment = (
        _plan_signature_moment(game_type, theme, mechanic, zh)
        if not getattr(slots, "reference_game", None)
        else (
            f"\u505a\u51fa\u4e00\u4e2a\u80fd\u8ba9\u73a9\u5bb6\u7acb\u523b\u60f3\u5230\u300a{slots.reference_game}\u300b\u7684\u6807\u5fd7\u6027\u9ad8\u5149\u65f6\u523b\u3002"
            if zh else
            f"Create one standout payoff that nods to {slots.reference_game} while fitting this theme."
        )
    )

    design_goals = (
        [
            f"\u5728\u524d 10 \u79d2\u5185\u6559\u4f1a\u73a9\u5bb6{mechanic or '\u6838\u5fc3\u73a9\u6cd5'}\u3002",
            f"\u7528 {theme or '\u5f53\u524d\u4e3b\u9898'}\u5efa\u7acb\u7acb\u5373\u53ef\u611f\u77e5\u7684\u89c6\u89c9\u8bb0\u5fc6\u70b9\u3002",
            "\u6bcf\u4e00\u5c40\u90fd\u8981\u6709\u6e05\u6670\u56de\u62a5\u548c\u4e0b\u4e00\u6b65\u52a8\u4f5c\u63d0\u793a\u3002",
        ]
        if zh else
        [
            f"Teach the core loop of {mechanic or 'the main mechanic'} within the first 10 seconds.",
            f"Use the {theme or 'current theme'} framing to create instant visual recognition.",
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


def _infer_explicit_game_type(text: str) -> Optional[str]:
    normalized = _normalize_free_text(text)
    if not normalized:
        return None
    for game_type, markers in EXPLICIT_GAME_TYPE_MARKERS.items():
        if any(_contains_marker(normalized, marker) for marker in markers):
            return game_type
    return None


def _match_priority_hint(
    text: str,
    hint_groups: tuple[tuple[str, tuple[str, ...]], ...],
) -> Optional[str]:
    for normalized, hints in hint_groups:
        if any(hint.lower() in text for hint in hints):
            return normalized
    return None


def _match_exact_normalized_hint(
    text: str,
    hint_groups: tuple[tuple[str, tuple[str, ...]], ...],
) -> Optional[str]:
    normalized_text = re.sub(r"\s+", " ", text.strip().lower())
    for normalized, hints in hint_groups:
        if any(normalized_text == hint.lower() for hint in hints):
            return normalized
    return None


def _count_affirmative_marker_occurrences(text: str, markers: tuple[str, ...]) -> int:
    total = 0
    for marker in markers:
        lowered_marker = marker.lower()
        start = 0
        while True:
            idx = text.find(lowered_marker, start)
            if idx == -1:
                break
            prefix = text[max(0, idx - 8):idx]
            if not any(prefix.endswith(token) for token in NEGATED_MARKER_PREFIXES):
                total += 1
            start = idx + len(lowered_marker)
    return total


def _pick_unique_top_signal(scores: Dict[str, int]) -> Optional[str]:
    positive_scores = {key: value for key, value in scores.items() if value > 0}
    if not positive_scores:
        return None
    top_score = max(positive_scores.values())
    winners = [key for key, value in positive_scores.items() if value == top_score]
    if len(winners) != 1:
        return None
    return winners[0]


def _infer_input_method_from_text(text: str) -> Optional[str]:
    lowered = _normalize_free_text(text).lower()
    if not lowered:
        return None

    priority_match = _match_priority_hint(lowered, INPUT_METHOD_PRIMARY_HINTS)
    if priority_match:
        return priority_match

    exact_match = _match_exact_normalized_hint(lowered, NORMALIZED_INPUT_METHOD_HINTS)
    if exact_match:
        return exact_match

    signal_scores = {
        input_method: _count_affirmative_marker_occurrences(lowered, markers)
        for input_method, markers in NORMALIZED_INPUT_METHOD_HINTS
    }
    return _pick_unique_top_signal(signal_scores)


def _infer_difficulty_from_text(text: str) -> Optional[str]:
    lowered = _normalize_free_text(text).lower()
    if not lowered:
        return None

    priority_match = _match_priority_hint(lowered, DIFFICULTY_PRIORITY_HINTS)
    if priority_match:
        return priority_match

    exact_match = _match_exact_normalized_hint(lowered, NORMALIZED_DIFFICULTY_HINTS)
    if exact_match:
        return exact_match

    signal_scores = {
        difficulty: _count_affirmative_marker_occurrences(lowered, markers)
        for difficulty, markers in NORMALIZED_DIFFICULTY_HINTS
    }
    return _pick_unique_top_signal(signal_scores)


def _plan_signature_moment(game_type: str, theme: str, mechanic: str, zh: bool) -> str:
    theme_text = _shorten_text(theme, limit=16) or ("当前主题" if zh else "the theme")
    mechanic_text = _shorten_text(mechanic, limit=24) or ("核心交互" if zh else "the core interaction")
    if zh:
        if game_type == "funny":
            return f"让玩家在{theme_text}里用“{mechanic_text}”做出一个又好笑又解压的高光瞬间。"
        if game_type == "educational":
            return f"让玩家在{theme_text}里用“{mechanic_text}”完成一次明显的理解跃迁。"
        return f"在{theme_text}里，通过“{mechanic_text}”完成一次让人想立刻再来一局的关键瞬间。"
    if game_type == "funny":
        return f"Use {mechanic_text} in {theme_text} to land one payoff that feels genuinely funny, not just noisy."
    if game_type == "educational":
        return f"Use {mechanic_text} in {theme_text} to create one visible learning payoff."
    return f"Create one standout beat in {theme_text} where {mechanic_text} feels instantly replayable."


