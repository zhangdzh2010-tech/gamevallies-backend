"""Stage 01 + 02: Dialogue engine and single-shot intent parsing."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from ..api.models import (
    AnalyzeDialogueTurnRequest,
    AnalyzeDialogueTurnResponse,
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    CoreMechanic,
    DialogueStreamDeltaPayload,
    DialogueStreamDonePayload,
    DialogueStreamFinalPayload,
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
from .prompt_format import safe_format_prompt
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
    "\u6478\u9c7c",
    "office",
    "boss",
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
                for item in re.split(r"\s*[;,|, /]+\s*", value)
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

def _shorten_text(text: str, *, limit: int = 36) -> str:
    normalized = _normalize_free_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip(" ,.!?;:") + "..."


def _canonical_reference_key(value: str) -> str:
    normalized = re.sub(r"[\u300a\u300b\"'\u201c\u201d\u2018\u2019]", "", _normalize_free_text(value)).strip().lower()
    return normalized


def _extract_reference_game_from_text(text: str) -> Optional[str]:
    source = _normalize_free_text(text)
    if not source:
        return None

    patterns = (
        r"(?:\u7c7b\u4f3c|\u50cf|\u53c2\u8003|\u501f\u9274|\u81f4\u656c|inspired by|similar to|based on|like)\s*[\u300a\"\u201c]?([^\u300a\u300b\"\u201c\u201d\n,\uFF0C\u3002\uFF1F\uFF01]{2,28})[\u300b\"\u201d]?",
        r"[\u300a\"\u201c]([^\u300a\u300b\"\u201c\u201d\n,\uFF0C\u3002\uFF1F\uFF01]{2,28})[\u300b\"\u201d]\s*(?:\u8fd9\u6837\u7684)?\u6e38\u620f",
        r"([^\u300a\u300b\"\u201c\u201d\n,\uFF0C\u3002\uFF1F\uFF01]{2,24})\s*\u7684\u6e38\u620f",
    )
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
        candidate = re.sub(r"\s*(?:\u90a3\u79cd|\u8fd9\u79cd|\u8fd9\u6b3e|\u90a3\u4e2a|\u7684\u6e38\u620f)$", "", candidate).strip(" \u300a\u300b\"\u201c\u201d.,;:!?")
        if len(candidate) < 2:
            continue
        if candidate.lower() in stop_words:
            continue
        return candidate
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


def _slot_summary_label(slot_key: str, *, zh: bool) -> str:
    labels = {
        "game_type": "\u6e38\u620f\u65b9\u5411" if zh else "game direction",
        "core_mechanic": "\u6838\u5fc3\u73a9\u6cd5" if zh else "core mechanic",
        "theme": "\u9898\u6750\u60c5\u5883" if zh else "theme",
        "input_method": "\u64cd\u4f5c\u65b9\u5f0f" if zh else "input method",
        "win_condition": "\u8fc7\u5173\u76ee\u6807" if zh else "win condition",
        "difficulty": "\u96be\u5ea6\u8282\u594f" if zh else "difficulty curve",
    }
    return labels.get(slot_key, SLOT_LABELS.get(slot_key, slot_key))


def _build_contextual_question_prompt(
    slot_key: str,
    slots: SlotState,
    *,
    zh: bool,
) -> str:
    reference_game = _normalize_free_text(str(getattr(slots, "reference_game", "") or ""))
    core_mechanic = _shorten_text(str(getattr(slots, "core_mechanic", "") or ""), limit=28)
    theme = _shorten_text(str(getattr(slots, "theme", "") or ""), limit=20)
    if slot_key == "theme":
        if reference_game:
            return (
                f"\u73a9\u6cd5\u53ef\u4ee5\u53c2\u8003\u300a{reference_game}\u300b\uff0c\u4f46\u9898\u6750\u4f60\u60f3\u6362\u6210\u4ec0\u4e48\u60c5\u5883\u6216\u4e16\u754c\u89c2\uff1f"
                if zh else
                f"We can borrow the feel of {reference_game}, but what setting or fantasy should this version use?"
            )
        if core_mechanic:
            return (
                f"\u8fd9\u4e2a\u201c{core_mechanic}\u201d\u60f3\u653e\u5728\u4ec0\u4e48\u60c5\u5883\u91cc\u6700\u5bf9\u5473\uff1f"
                if zh else
                f"What setting would make the '{core_mechanic}' loop feel most interesting?"
            )
    if slot_key == "win_condition":
        if reference_game:
            return (
                f"\u5982\u679c\u53c2\u8003\u300a{reference_game}\u300b\u7684\u611f\u89c9\uff0c\u4f60\u66f4\u60f3\u8ba9\u73a9\u5bb6\u901a\u8fc7\u6e05\u7a7a\u3001\u8fbe\u6210\u76ee\u6807\uff0c\u8fd8\u662f\u6491\u8fc7\u4e00\u8f6e\u6765\u8fc7\u5173\uff1f"
                if zh else
                f"If this nods to {reference_game}, what should count as clearing a round: clearing everything, hitting a target, or surviving the run?"
            )
        if core_mechanic:
            return (
                f"\u56f4\u7ed5\u201c{core_mechanic}\u201d\uff0c\u8fd9\u4e00\u5c40\u91cc\u73a9\u5bb6\u600e\u6837\u624d\u7b97\u771f\u6b63\u8fc7\u5173\uff1f"
                if zh else
                f"With '{core_mechanic}' as the loop, what exactly should count as a successful round?"
            )
    if slot_key == "core_mechanic":
        if reference_game:
            return (
                f"\u5982\u679c\u53c2\u8003\u300a{reference_game}\u300b\uff0c\u4f60\u6700\u60f3\u4fdd\u7559\u7684\u6838\u5fc3\u4ea4\u4e92\u662f\u4ec0\u4e48\uff1f"
                if zh else
                f"If this takes inspiration from {reference_game}, what is the one interaction you most want to preserve?"
            )
        if theme:
            return (
                f"\u5728\u201c{theme}\u201d\u8fd9\u4e2a\u60c5\u5883\u91cc\uff0c\u73a9\u5bb6\u6700\u5e38\u505a\u7684\u4e00\u4e2a\u52a8\u4f5c\u662f\u4ec0\u4e48\uff1f"
                if zh else
                f"In the {theme} setup, what does the player do over and over?"
            )
    if slot_key == "input_method":
        if reference_game:
            return (
                f"\u8fd9\u7248\u8fd8\u662f\u60f3\u4fdd\u6301\u300a{reference_game}\u300b\u90a3\u79cd\u70b9\u6309\u8282\u594f\uff0c\u8fd8\u662f\u6539\u6210\u6ed1\u52a8/\u62d6\u62fd\u66f4\u5408\u9002\uff1f"
                if zh else
                f"Should this stay close to {reference_game}'s main control feel, or would swipe/drag fit this version better?"
            )
        if core_mechanic:
            return (
                f"\u4e3a\u4e86\u8ba9\u201c{core_mechanic}\u201d\u66f4\u987a\u624b\uff0c\u73a9\u5bb6\u4e3b\u8981\u7528\u70b9\u51fb\u3001\u6ed1\u52a8\u8fd8\u662f\u62d6\u62fd\uff1f"
                if zh else
                f"To make '{core_mechanic}' feel right on mobile, should the main control be tap, swipe, or drag?"
            )
    if slot_key == "difficulty":
        if reference_game:
            return (
                f"\u4f60\u5e0c\u671b\u8fd9\u7248\u50cf\u300a{reference_game}\u300b\u90a3\u6837\u540e\u52b2\u8d8a\u6765\u8d8a\u5f3a\uff0c\u8fd8\u662f\u6574\u4f53\u66f4\u8f7b\u677e\u4e00\u70b9\uff1f"
                if zh else
                f"Do you want this to spike like {reference_game}, or stay more relaxed throughout?"
            )
        if theme:
            return (
                f"\u201c{theme}\u201d\u8fd9\u7248\u4f53\u9a8c\uff0c\u4f60\u66f4\u60f3\u505a\u6210\u8f7b\u677e\u3001\u6807\u51c6\uff0c\u8fd8\u662f\u9010\u6b65\u53d8\u96be\uff1f"
                if zh else
                f"For this {theme} version, should the difficulty feel easy, standard, or progressively tougher?"
            )
    if slot_key == "game_type":
        if reference_game:
            return (
                f"\u53c2\u8003\u300a{reference_game}\u300b\u7684\u524d\u63d0\u4e0b\uff0c\u4f60\u66f4\u60f3\u8981\u76ca\u667a\u3001\u4f11\u95f2\uff0c\u8fd8\u662f\u66f4\u6076\u641e\u7684\u7248\u672c\uff1f"
                if zh else
                f"With {reference_game} as a touchstone, should this lean more puzzle, casual, or more overtly comedic?"
            )

    default_prompts = {
        "game_type": (
            "\u8fd9\u4e2a\u6e38\u620f\u66f4\u504f\u76ca\u667a\u3001\u4f11\u95f2\u3001\u6559\u80b2\uff0c\u8fd8\u662f\u641e\u7b11\u65b9\u5411\uff1f"
            if zh else
            "Should this feel more puzzle, casual, educational, or funny?"
        ),
        "core_mechanic": (
            "\u73a9\u5bb6\u5728\u8fd9\u4e2a\u6e38\u620f\u91cc\u6700\u5e38\u505a\u7684\u4e00\u4e2a\u52a8\u4f5c\u662f\u4ec0\u4e48\uff1f"
            if zh else
            "What is the main thing the player does most of the time?"
        ),
        "theme": (
            "\u4f60\u5e0c\u671b\u6e38\u620f\u5448\u73b0\u4ec0\u4e48\u4e3b\u9898\u3001\u4e16\u754c\u89c2\u6216\u60c5\u5883\uff1f"
            if zh else
            "What theme, world, or situation should the game use?"
        ),
        "input_method": (
            "\u73a9\u5bb6\u4e3b\u8981\u901a\u8fc7\u70b9\u51fb\u3001\u6ed1\u52a8\u8fd8\u662f\u62d6\u62fd\u6765\u64cd\u4f5c\uff1f"
            if zh else
            "Should the player mainly tap, swipe, or drag?"
        ),
        "win_condition": (
            "\u8fd9\u4e00\u5c40\u91cc\u73a9\u5bb6\u600e\u6837\u7b97\u8d62\uff0c\u6216\u8005\u8fbe\u6210\u4ec0\u4e48\u76ee\u6807\uff1f"
            if zh else
            "What counts as winning or clearing the round?"
        ),
        "difficulty": (
            "\u6574\u4f53\u96be\u5ea6\u4f60\u66f4\u60f3\u8981\u8f7b\u677e\u3001\u6807\u51c6\uff0c\u8fd8\u662f\u9010\u6b65\u53d8\u96be\uff1f"
            if zh else
            "Should the difficulty feel easy, standard, or progressively harder?"
        ),
    }
    return default_prompts.get(
        slot_key,
        "\u8bf7\u518d\u8865\u5145\u4e00\u4e2a\u5173\u952e\u8bbe\u5b9a\u3002" if zh else "Please add one more key design detail.",
    )


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

    reference_game = _extract_reference_game_from_text(source)
    if reference_game:
        inferred["reference_game"] = reference_game
        reference_hints = _reference_game_hints(reference_game)
        for field in ("game_type", "core_mechanic", "input_method", "win_condition", "difficulty"):
            if reference_hints.get(field) and not inferred.get(field):
                inferred[field] = reference_hints[field]

        theme_rules = (
            ("space", ("\u592a\u7a7a", "\u5b87\u5b99", "space", "cosmic", "galaxy")),
            ("zoo", ("\u52a8\u7269\u56ed", "zoo", "animal")),
            ("neon", ("\u9713\u8679", "neon", "cyber")),
            ("fantasy", ("\u5947\u5e7b", "fantasy", "magic")),
            ("ocean", ("\u6d77\u6d0b", "ocean", "underwater", "water")),
            ("forest", ("\u68ee\u6797", "forest", "jungle")),
            ("city", ("\u57ce\u5e02", "city", "urban")),
            ("garden", ("\u82b1\u56ed", "garden", "farm")),
            ("food", ("\u98df\u7269", "\u53a8\u623f", "food", "kitchen", "chef")),
            ("candy", ("\u7cd6\u679c", "\u751c\u54c1", "candy", "dessert")),
            ("sports", ("\u8fd0\u52a8", "\u7403\u573a", "sports", "stadium")),
            ("toy", ("\u73a9\u5177", "toy", "block")),
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
            "\u5931\u8d25\u540e\u53ef\u4ee5\u91cd\u65b0\u5f00\u59cb" if ui_language == "zh-CN" else "Restart after losing",
        ]
    if "\u70b9\u51fb\u5f00\u59cb" in source or "tap to start" in lowered or "click to start" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + [
            "\u70b9\u51fb\u5f00\u59cb" if ui_language == "zh-CN" else "Tap to start",
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

    @staticmethod
    def _should_use_first_turn_fast_path(history: List[ConversationMessage]) -> bool:
        user_count = 0
        assistant_count = 0
        latest_user_message = ""
        for message in history:
            normalized = _normalize_free_text(message.content)
            if not normalized:
                continue
            if message.role == "user":
                user_count += 1
                latest_user_message = normalized
            elif message.role == "assistant":
                assistant_count += 1
        if user_count != 1 or assistant_count != 0:
            return False
        if _extract_reference_game_from_text(latest_user_message):
            return False
        if not _looks_like_sparse_request(latest_user_message):
            return False
        inferred = _infer_slots_from_text(latest_user_message)
        if any(
            inferred.get(key)
            for key in (
                "game_type",
                "core_mechanic",
                "theme",
                "input_method",
                "win_condition",
                "difficulty",
                "reference_game",
            )
        ):
            return False
        return True

    def _apply_heuristic_turn_slot_update(
        self,
        *,
        session: DialogueSession,
        source_text: str,
        title: Optional[str] = None,
        allow_sparse_fallback: bool = True,
    ) -> List[str]:
        old_slots = session.slots.model_copy()
        heuristic_slot_data = _build_heuristic_slot_payload(
            raw_text="",
            source_text=source_text,
            title=title,
            preferred_game_type=str(session.slots.game_type or "") or None,
            variation_seed=session.session_id,
            allow_sparse_fallback=allow_sparse_fallback,
        )
        for key, value in heuristic_slot_data.items():
            if value is not None and hasattr(session.slots, key):
                setattr(session.slots, key, value)

        return [
            key for key in SlotState.model_fields
            if getattr(session.slots, key) != getattr(old_slots, key)
        ]

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
        prefer_fast_route = False
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
                prefer_fast=prefer_fast_route,
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
                prefer_fast=prefer_fast_route,
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
        elif self._should_use_first_turn_fast_path(history):
            updated_slots = self._apply_heuristic_turn_slot_update(
                session=session,
                source_text=source_text,
                title=req.title,
                allow_sparse_fallback=True,
            )
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
        question_zh = _detect_ui_language(" ".join(part for part in [req.title or "", source_text] if part)).startswith("zh")
        if current_question:
            current_question.prompt = _build_contextual_question_prompt(
                current_question.slot_key,
                session.slots,
                zh=question_zh,
            )
            current_question.label = _slot_summary_label(
                current_question.slot_key,
                zh=question_zh,
            )
        fill_pct = session.slots.fill_pct()
        missing_required = session.slots.missing_required()
        has_interactive_context = bool(
            req.advance_only
            or answered_slot_key
            or sum(
                1 for message in history
                if message.role == "assistant" and _normalize_free_text(message.content)
            ) > 0
            or sum(
                1 for message in history
                if message.role == "user" and _normalize_free_text(message.content)
            ) > 1
        )
        ready_to_generate = bool(
            not missing_required
            or (
                fill_pct >= 0.67
                and (current_question is None or has_interactive_context)
            )
        )
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
        reply = _compose_creation_session_reply_v2(
            slots=session.slots,
            current_question=current_question,
            ready_to_generate=ready_to_generate,
            source_text=source_text,
            title=req.title,
            latest_user_answer=latest_user_answer,
            question_strategy=question_strategy,
            plan_draft=plan_draft,
        )

        return AnalyzeDialogueTurnResponse(
            reply=reply,
            slots=session.slots,
            slots_updated=updated_slots,
            missing_required=missing_required,
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

    async def analyze_turn_stream(
        self,
        req: AnalyzeDialogueTurnRequest,
    ) -> AsyncIterator[Dict[str, Any]]:
        analysis = await self.analyze_turn(req)
        reply_kind = "question" if analysis.current_question else "summary"
        fallback_reply = _normalize_free_text(analysis.reply)
        accumulated = ""
        emitted_delta = False
        stream_failed = False

        try:
            async for delta in self._stream_analyze_turn_reply(req=req, analysis=analysis):
                cleaned = str(delta or "")
                if not cleaned:
                    continue
                emitted_delta = True
                accumulated += cleaned
                yield {
                    "event": "delta",
                    "data": DialogueStreamDeltaPayload(
                        delta=cleaned,
                        accumulated=accumulated,
                        kind=reply_kind,
                    ).model_dump(mode="json"),
                }
        except Exception as exc:
            stream_failed = True
            logger.warning(
                "Dialogue reply stream failed for session=%s, using fallback reply: %s",
                req.session_id or "stateless",
                exc,
            )

        final_reply = fallback_reply if stream_failed else (_normalize_free_text(accumulated) or fallback_reply)
        if not emitted_delta and final_reply:
            for chunk in _chunk_reply_for_streaming(final_reply):
                accumulated += chunk if accumulated else chunk
                yield {
                    "event": "delta",
                    "data": DialogueStreamDeltaPayload(
                        delta=chunk,
                        accumulated=accumulated,
                        kind=reply_kind,
                    ).model_dump(mode="json"),
                }

        analysis.reply = final_reply
        yield {
            "event": "done",
            "data": DialogueStreamDonePayload(
                message=final_reply,
                kind=reply_kind,
            ).model_dump(mode="json"),
        }
        yield {
            "event": "final",
            "data": DialogueStreamFinalPayload(
                **analysis.model_dump(mode="json", exclude_none=True),
            ).model_dump(mode="json", exclude_none=True),
        }

    async def _stream_analyze_turn_reply(
        self,
        *,
        req: AnalyzeDialogueTurnRequest,
        analysis: AnalyzeDialogueTurnResponse,
    ) -> AsyncIterator[str]:
        source_text = _build_dialogue_source_text(
            req.initial_prompt,
            [
                ConversationMessage(
                    role=item.role,
                    content=item.content,
                    kind=getattr(item, "kind", None),
                )
                for item in (req.conversation or [])
            ],
        )
        system_prompt = _build_dialogue_reply_system_prompt_from_catalog(analysis)
        user_prompt = _build_dialogue_reply_user_prompt_from_catalog(
            request=req,
            analysis=analysis,
            source_text=source_text,
        )
        async for delta in self._client.stream_complete(
            max_tokens=640,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            step_key="dialogue.reply",
            stage="dialogue",
            prefer_fast=True,
            response_size_hint="medium",
            context_scope="task",
            compression_policy="dialogue",
        ):
            yield delta

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
        if self._should_use_first_turn_fast_path(session.history):
            source_text = _source_description_from_history(session.history)
            updated = self._apply_heuristic_turn_slot_update(
                session=session,
                source_text=source_text,
                title=None,
                allow_sparse_fallback=True,
            )
            current_question, _ = _build_dialogue_question(
                session.slots,
                skipped_slots=[],
                blocked_slots=[],
                source_text=source_text,
                entry_mode="create",
                confidence_by_slot={},
                ambiguity_flags=[],
            )
            question_zh = _detect_ui_language(source_text).startswith("zh")
            if current_question:
                current_question.prompt = _build_contextual_question_prompt(
                    current_question.slot_key,
                    session.slots,
                    zh=question_zh,
                )
                current_question.label = _slot_summary_label(
                    current_question.slot_key,
                    zh=question_zh,
                )
            missing_required = session.slots.missing_required()
            ready_to_generate = bool(
                not missing_required
                or (
                    session.slots.fill_pct() >= 0.67
                    and current_question is None
                )
            )
            reply = _compose_creation_session_reply_v2(
                slots=session.slots,
                current_question=current_question,
                ready_to_generate=ready_to_generate,
                source_text=source_text,
                title=None,
                latest_user_answer=_latest_user_answer_from_history(session.history),
                question_strategy=None,
                plan_draft=_build_plan_draft(
                    session.slots,
                    source_text=source_text,
                    title=None,
                    generation_tier="standard",
                    entry_mode="create",
                ),
            )
            return reply, updated

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
    if isinstance(value, list):
        value_text = " ".join(str(item).strip() for item in value if str(item).strip())
    else:
        value_text = str(value or "").strip()

    missing_evidence = (
        "当前描述里还没有提供这个信息。"
        if zh
        else "Missing from the current brief."
    )
    if not value_text:
        return 0.0, missing_evidence, False

    explicit_evidence = (
        "这个信息是用户在最新一轮里直接确认的。"
        if zh
        else "Explicitly confirmed in the latest user message."
    )
    if authoritative:
        return (0.99 if updated else 0.97), explicit_evidence, False

    lowered = (text or "").lower()
    value_lower = value_text.lower()
    evidence_terms = _slot_evidence_terms(field, value_text)
    matched_term = next((term for term in evidence_terms if term and term.lower() in lowered), None)
    ambiguous = False
    base = 0.68 if updated else 0.6

    if matched_term:
        base = 0.86 if updated else 0.8

    if field == "game_type":
        explicit_type = _infer_explicit_game_type(text)
        if explicit_type == value_lower:
            base = max(base, 0.91 if updated else 0.86)
        elif explicit_type:
            ambiguous = True
            base = min(base, 0.58)
    elif field == "input_method":
        input_hint = _infer_input_method_from_text(text)
        if input_hint == value_lower:
            base = max(base, 0.9 if updated else 0.84)
        elif input_hint:
            ambiguous = True
            base = min(base, 0.56)
    elif field == "difficulty":
        difficulty_hint = _infer_difficulty_from_text(text)
        if difficulty_hint == value_lower:
            base = max(base, 0.88 if updated else 0.83)
        elif difficulty_hint:
            ambiguous = True
            base = min(base, 0.56)
    elif field == "theme":
        theme_hint = _infer_theme_from_context(text)
        if theme_hint:
            hint_lower = theme_hint.lower()
            if hint_lower in value_lower or value_lower in hint_lower:
                base = max(base, 0.9 if updated else 0.84)
            elif matched_term is None:
                ambiguous = True
                base = min(base, 0.6)
        elif matched_term:
            base = max(base, 0.84 if updated else 0.76)
    elif field == "core_mechanic":
        if matched_term:
            base = max(base, 0.88 if updated else 0.82)
        elif len(value_text) >= 8:
            base = max(base, 0.74 if updated else 0.68)
    elif field == "win_condition":
        outcome_markers = ("win", "goal", "clear", "complete", "survive", "过关", "获胜", "目标", "完成")
        if matched_term or any(marker in lowered for marker in outcome_markers):
            base = max(base, 0.86 if updated else 0.8)

    if updated and not matched_term and not ambiguous:
        base = min(0.98, base + 0.04)

    if ambiguous:
        evidence = (
            "当前值主要是根据上下文推断的，但最新回复里出现了另一种信号。"
            if zh
            else "Inferred from context, but the latest message also suggests a different answer."
        )
    elif matched_term:
        evidence = (
            f"在最新上下文里命中了标记 `{matched_term}`。"
            if zh
            else f"Matched marker `{matched_term}` in the latest context."
        )
    else:
        evidence = (
            "这个值主要是根据整体想法和上下文推断出来的。"
            if zh
            else "Mainly inferred from the current idea and surrounding context."
        )
    return round(max(0.0, min(base, 0.99)), 3), evidence, ambiguous


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
            "tap": ("tap", "click", "touch", "点击", "点按", "轻触"),
            "touch": ("touch", "tap", "press", "触摸", "轻触"),
            "swipe": ("swipe", "slide", "sliding", "滑动", "滑屏", "划动"),
            "drag": ("drag", "move", "拖拽", "拖动"),
        }
        return list(marker_map.get(normalized, (value_text,)))
    if field == "difficulty":
        marker_map = {
            "easy": ("easy", "simple", "relaxed", "casual", "简单", "轻松", "休闲"),
            "medium": ("medium", "balanced", "normal", "moderate", "中等", "适中", "普通"),
            "hard": ("hard", "challenging", "difficult", "brutal", "困难", "硬核", "挑战"),
            "progressive": ("progressive", "ramping", "escalating", "ramp", "递进", "逐步升级", "越来越难"),
        }
        return list(marker_map.get(normalized, (value_text,)))
    return [value_text]


def _infer_explicit_game_type(text: str) -> Optional[str]:
    normalized = _normalize_free_text(text)
    if not normalized:
        return None
    for game_type, markers in EXPLICIT_GAME_TYPE_MARKERS.items():
        if any(_contains_marker(normalized, marker) for marker in markers):
            return game_type
    return None


def _infer_input_method_from_text(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    marker_map = {
        "tap": ("tap", "click", "touch", "点击", "点按", "轻触"),
        "touch": ("touch", "tap", "press", "触摸", "轻触"),
        "swipe": ("swipe", "slide", "sliding", "滑动", "滑屏", "划动"),
        "drag": ("drag", "move", "拖拽", "拖动"),
    }
    for input_method, markers in marker_map.items():
        if any(marker.lower() in lowered for marker in markers):
            return input_method
    return None


def _infer_difficulty_from_text(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    marker_map = {
        "easy": ("easy", "simple", "relaxed", "casual", "简单", "轻松", "休闲"),
        "medium": ("medium", "balanced", "normal", "moderate", "中等", "适中", "普通"),
        "hard": ("hard", "challenging", "difficult", "brutal", "困难", "硬核", "挑战"),
        "progressive": ("progressive", "ramping", "escalating", "ramp", "递进", "逐步升级", "越来越难"),
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
        or ("清晰世界观" if zh else "a clear world")
    ).strip()
    mechanic = str(slots.core_mechanic or ("一个好懂又有后劲的核心交互" if zh else "a readable core interaction")).strip()
    input_method = str(slots.input_method or ("touch" if not zh else "点按"))
    objective = str(slots.win_condition or ("完成当前回合目标" if zh else "complete the current round goal")).strip()
    difficulty = str(slots.difficulty or "medium").strip() or "medium"
    visual_style = str(slots.visual_style or ("鲜明易识别的手机游戏风格" if zh else "a bold, readable mobile look")).strip()

    title_value = str(title or "").strip() or _plan_title_fallback(game_type, theme, zh)
    summary = (
        f"这是一款{game_type_label}，核心围绕“{mechanic}”，放在“{theme}”这个情境中展开。"
        if zh else
        f"This is a {game_type_label} built around '{mechanic}' in a {theme} setting."
    )
    concept_anchor = str(title or "").strip() or theme
    concept = (
        f"?????{concept_anchor}????????????????????????"
        if zh else
        f"The concept is to turn {concept_anchor} into a mobile loop that is easy to read and satisfying within one round."
    )
    interaction = (
        f"玩家主要通过{input_method}去{mechanic}，并快速收到结果反馈。"
        if zh else
        f"The main interaction is to {mechanic} through {input_method} input with quick feedback."
    )
    objective_text = (
        f"每一局的目标是：{objective}。"
        if zh else
        f"The round goal is: {objective}."
    )
    pacing = _plan_pacing_text(difficulty, generation_tier, entry_mode, zh)
    visual_direction = (
        f"视觉上优先追求{visual_style}，保证小屏上也能一眼看懂状态和目标。"
        if zh else
        f"Visually, aim for {visual_style} with strong readability on small screens."
    )
    signature_moment = _plan_signature_moment(game_type, theme, mechanic, zh)

    return PlanDraft(
        title=title_value,
        summary=summary,
        concept=concept,
        interaction=interaction,
        objective=objective_text,
        pacing=pacing,
        visual_direction=visual_direction,
        signature_moment=signature_moment,
    )


def _localized_game_type_name(game_type: str, zh: bool) -> str:
    mapping = {
        "casual": ("休闲手机游戏", "casual mobile game"),
        "puzzle": ("益智手机游戏", "puzzle mobile game"),
        "educational": ("教育向手机游戏", "educational mobile game"),
        "funny": ("恶搞轻游戏", "comedic mobile game"),
    }
    pair = mapping.get((game_type or "").strip().lower(), ("手机游戏", "mobile game"))
    return pair[0] if zh else pair[1]


def _plan_title_fallback(game_type: str, theme: str, zh: bool) -> str:
    theme_text = _shorten_text(theme, limit=18) or ("新主题" if zh else "New Theme")
    if zh:
        return f"{theme_text}{_localized_game_type_name(game_type, True)}"
    return f"{theme_text.title()} {game_type.title()}"


def _plan_pacing_text(
    difficulty: str,
    generation_tier: str,
    entry_mode: str,
    zh: bool,
) -> str:
    difficulty = (difficulty or "medium").strip().lower()
    if zh:
        if difficulty == "easy":
            base = "节奏偏轻松，前几秒就要给到成功反馈。"
        elif difficulty == "hard":
            base = "节奏更紧，尽快建立压力但仍要说清楚规则。"
        elif difficulty == "progressive":
            base = "前面先让玩家进入状态，后面逐步加压形成起伏。"
        else:
            base = "节奏保持清晰稳定，每次操作都能看到明确回报。"
        if generation_tier == "showcase":
            base += " 可以多给一个高光节奏或系统升级点。"
        if entry_mode == "iterate":
            base += " 这次偏向在现有方向上做精修。"
        return base

    if difficulty == "easy":
        base = "Keep the pacing gentle and reward the player within the opening seconds."
    elif difficulty == "hard":
        base = "Build pressure quickly, but keep the rules readable and fair."
    elif difficulty == "progressive":
        base = "Start readable, then steadily ramp the pressure across the round."
    else:
        base = "Keep the pacing clear and even, with obvious feedback on each action."
    if generation_tier == "showcase":
        base += " Add one stronger escalation or highlight beat."
    if entry_mode == "iterate":
        base += " Treat this pass as a focused refinement of the current direction."
    return base


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


def _build_dialogue_question(
    slots: SlotState,
    *,
    skipped_slots: List[str],
    blocked_slots: List[str],
    source_text: str,
    entry_mode: str,
    confidence_by_slot: Optional[Dict[str, float]] = None,
    ambiguity_flags: Optional[List[str]] = None,
) -> Tuple[Optional[DialogueQuestion], Optional[QuestionStrategy]]:
    confidence_by_slot = confidence_by_slot or {}
    ambiguity_flags = ambiguity_flags or []
    skipped = set(skipped_slots or [])
    blocked = set(blocked_slots or [])
    zh = _detect_ui_language(source_text).startswith("zh")
    required_slots = ["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"]
    all_required_present = all(
        str(getattr(slots, slot_key, "") or "").strip()
        for slot_key in required_slots
        if slot_key not in skipped and slot_key not in blocked
    )
    low_confidence_thresholds = {
        "core_mechanic": 0.72,
        "win_condition": 0.72,
        "input_method": 0.72,
        "difficulty": 0.64,
    }
    fallback_refine_order = ["win_condition", "difficulty", "theme", "core_mechanic", "input_method", "game_type"]
    templates = {
        "game_type": (
            "这个游戏更偏益智、休闲、教育，还是恶搞方向？"
            if zh else
            "Should this feel more puzzle, casual, educational, or funny?"
        ),
        "core_mechanic": (
            "玩家在这个游戏里最常做的一个动作是什么？"
            if zh else
            "What is the main action the player repeats?"
        ),
        "theme": (
            "你想把它放在什么世界观或情境里？"
            if zh else
            "What world, theme, or situation should this use?"
        ),
        "input_method": (
            "玩家主要通过点击、滑动还是拖拽来操作？"
            if zh else
            "Should the main control be tap, swipe, or drag?"
        ),
        "win_condition": (
            "这一局里，玩家怎样才算真正过关？"
            if zh else
            "What exactly counts as clearing a round?"
        ),
        "difficulty": (
            "难度你更想要轻松、标准，还是逐步变难？"
            if zh else
            "Should the difficulty feel easy, standard, or progressively harder?"
        ),
    }

    candidates: List[Tuple[float, str, str, float, float, str]] = []
    for slot_key in required_slots:
        if slot_key in skipped or slot_key in blocked:
            continue
        value = str(getattr(slots, slot_key, "") or "").strip()
        impact = SLOT_IMPACT_WEIGHTS.get(slot_key, 0.4) + ENTRY_MODE_SLOT_BIAS.get(entry_mode, {}).get(slot_key, 0.0)
        confidence = float(confidence_by_slot.get(slot_key, 0.0) or 0.0)
        ambiguity_weight = 1.0 if f"{slot_key}:ambiguous" in ambiguity_flags else 0.0
        if not value:
            score = impact + 0.5
            reason = (
                f"「{_slot_summary_label(slot_key, zh=zh)}」还没有被确定，会直接影响生成结果。"
                if zh else
                f'The {SLOT_LABELS.get(slot_key, slot_key)} is still missing and will directly shape the result.'
            )
            candidates.append((score, slot_key, "missing_required", impact, confidence, reason))
            continue
        if ambiguity_weight > 0:
            score = impact + 0.35 + ambiguity_weight
            reason = (
                f"「{_slot_summary_label(slot_key, zh=zh)}」目前存在歧义，需要先消歧。"
                if zh else
                f'The {SLOT_LABELS.get(slot_key, slot_key)} still looks ambiguous and should be clarified.'
            )
            candidates.append((score, slot_key, "ambiguity_resolution", impact, confidence, reason))
            continue
        threshold = low_confidence_thresholds.get(slot_key)
        if threshold is not None and confidence < threshold:
            mode = "ambiguity_resolution" if all_required_present else "low_confidence"
            score = impact + (threshold - confidence)
            reason = (
                f"?{_slot_summary_label(slot_key, zh=zh)}???????????????"
                if zh else
                (
                    f'The {SLOT_LABELS.get(slot_key, slot_key)} exists, but it still needs one more confirmation pass.'
                    if all_required_present else
                    f'The {SLOT_LABELS.get(slot_key, slot_key)} exists, but confidence is still shaky.'
                )
            )
            candidates.append((score, slot_key, mode, impact, confidence, reason))

    if not candidates:
        for slot_key in fallback_refine_order:
            if slot_key in skipped or slot_key in blocked:
                continue
            if not str(getattr(slots, slot_key, "") or "").strip():
                continue
            question = DialogueQuestion(
                slot_key=slot_key,
                label=SLOT_LABELS.get(slot_key, slot_key),
                prompt=templates.get(slot_key, templates["core_mechanic"]),
                skippable=True,
            )
            strategy = QuestionStrategy(
                mode="polish",
                slot_key=slot_key,
                reason=(
                    f"?{_slot_summary_label(slot_key, zh=zh)}???????????????????"
                    if zh else
                    f'The {SLOT_LABELS.get(slot_key, slot_key)} is usable already, but still worth one more refinement pass.'
                ),
                impact=round(max(0.0, min(SLOT_IMPACT_WEIGHTS.get(slot_key, 0.4), 1.5)), 3),
                confidence=round(max(0.0, min(float(confidence_by_slot.get(slot_key, 0.0) or 0.0), 1.0)), 3),
                ambiguity_weight=0.0,
            )
            return question, strategy
        return None, None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, slot_key, mode, impact, confidence, reason = candidates[0]
    question = DialogueQuestion(
        slot_key=slot_key,
        label=SLOT_LABELS.get(slot_key, slot_key),
        prompt=templates.get(slot_key, templates["core_mechanic"]),
        skippable=True,
    )
    strategy = QuestionStrategy(
        mode=mode,
        slot_key=slot_key,
        reason=reason,
        impact=round(max(0.0, min(impact, 1.5)), 3),
        confidence=round(max(0.0, min(confidence, 1.0)), 3),
        ambiguity_weight=1.0 if mode == "ambiguity_resolution" else 0.0,
    )
    return question, strategy


def _compose_creation_session_reply_v2(
    *,
    slots: SlotState,
    current_question: Optional[DialogueQuestion],
    ready_to_generate: bool,
    source_text: str = "",
    title: Optional[str] = None,
    latest_user_answer: Optional[str] = None,
    question_strategy: Optional[QuestionStrategy] = None,
    plan_draft: Optional[PlanDraft] = None,
) -> str:
    language = _detect_ui_language(" ".join(part for part in [title or "", source_text] if part))
    zh = language.startswith("zh")
    reference_game = _normalize_free_text(str(getattr(slots, "reference_game", "") or ""))
    game_type = _normalize_free_text(str(getattr(slots, "game_type", "") or ""))
    theme = _shorten_text(str(getattr(slots, "theme", "") or ""), limit=18)
    core_mechanic = _shorten_text(str(getattr(slots, "core_mechanic", "") or getattr(plan_draft, "interaction", "") or ""), limit=28)
    objective = _shorten_text(str(getattr(slots, "win_condition", "") or getattr(plan_draft, "objective", "") or ""), limit=28)
    understanding_check = _looks_like_understanding_check(latest_user_answer or "")

    if reference_game:
        if zh:
            opener = (
                f"了解，我知道《{reference_game}》这种感觉。"
                if understanding_check else
                f"我会把这次方向理解成参考《{reference_game}》的核心体验。"
            )
        else:
            opener = (
                f"Yes, I know the feel of {reference_game}."
                if understanding_check else
                f"I am reading this as a game that takes inspiration from {reference_game}."
            )
    else:
        summary_bits = [bit for bit in [game_type, theme, core_mechanic] if bit]
        compact_summary = " / ".join(summary_bits) or ("这款游戏" if zh else "this game")
        opener = (
            f"我目前的理解是：{compact_summary}。"
            if zh else
            f"My read so far is: {compact_summary}."
        )

    if objective and not ready_to_generate:
        opener += (
            f" 目前的过关感更像是“{objective}”。"
            if zh else
            f" The current success beat feels like '{objective}'."
        )

    if ready_to_generate and current_question:
        label = _slot_summary_label(question_strategy.slot_key, zh=zh) if question_strategy and question_strategy.slot_key else None
        if zh:
            tail = "现在其实已经能开始生成了"
            if label:
                tail += f"，但我还想再确认一下「{label}」"
            return f"{opener} {tail}：{current_question.prompt}"
        tail = "This is already strong enough to generate"
        if label:
            tail += f", but I want to confirm the {label} once more"
        return f"{opener} {tail}: {current_question.prompt}"

    if ready_to_generate:
        if zh:
            return f"{opener} 这些信息已经够我出第一版了，如果方向对了，可以直接开始创作。"
        return f"{opener} I have enough to build a solid first version now, so you can start creating whenever this direction feels right."

    if current_question:
        if zh:
            return f"{opener} 为了别把关键体验做偏，我先追一个最重要的问题：{current_question.prompt}"
        return f"{opener} To avoid drifting away from the core experience, let me lock one key detail first: {current_question.prompt}"

    if zh:
        return f"{opener} 你可以再补一句你最在意的玩法或气质，我会继续帮你收口。"
    return f"{opener} Give me one more sentence about the mechanic or feeling you care about most, and I will tighten the brief from there."


def _build_dialogue_public_direction_summary(
    analysis: AnalyzeDialogueTurnResponse,
    *,
    zh: bool,
) -> str:
    slots = analysis.slots
    reference_game = _normalize_free_text(str(getattr(slots, "reference_game", "") or ""))
    theme = _normalize_free_text(str(getattr(slots, "theme", "") or ""))
    core_mechanic = _normalize_free_text(str(getattr(slots, "core_mechanic", "") or ""))
    objective = _normalize_free_text(str(getattr(slots, "win_condition", "") or ""))
    plan_summary = _normalize_free_text(str(getattr(analysis.plan_draft, "summary", "") or ""))

    fragments: List[str] = []
    if reference_game:
        fragments.append(
            f"参考《{reference_game}》的直觉反馈"
            if zh else
            f"capture some of the immediate feel of {reference_game}"
        )
    if theme:
        fragments.append(
            f"放在{theme}这个主题里"
            if zh else
            f"set it inside a {theme} theme"
        )
    if core_mechanic:
        fragments.append(
            f"核心交互围绕“{_shorten_text(core_mechanic, limit=34)}”展开"
            if zh else
            f"center it on {_shorten_text(core_mechanic, limit=40)}"
        )
    if objective:
        fragments.append(
            f"玩家目标是“{_shorten_text(objective, limit=28)}”"
            if zh else
            f"and give the player a clear goal: {_shorten_text(objective, limit=36)}"
        )

    if fragments:
        return (
            "目前我会把这个方向理解成：" + "，".join(fragments) + "。"
            if zh else
            "Current working direction: " + ", ".join(fragments) + "."
        )
    if plan_summary:
        return plan_summary
    return (
        "先顺着用户最新的想法继续收口，不要把语气写成内部分析报告。"
        if zh else
        "Stay grounded in the user's latest idea and avoid sounding like an internal analysis note."
    )


def _build_dialogue_public_follow_up_guidance(
    analysis: AnalyzeDialogueTurnResponse,
    *,
    zh: bool,
) -> str:
    question_text = _normalize_free_text(
        str(getattr(analysis.current_question, "prompt", "") or "")
    )
    if analysis.ready_to_generate and question_text:
        return (
            f"已经足够开始创建了；如果继续追问，只能把“{question_text}”当成可选打磨。"
            if zh else
            f"The brief is already strong enough to build; if you ask anything else, treat '{question_text}' as an optional polish question."
        )
    if analysis.ready_to_generate:
        return (
            "已经足够开始创建，不要再把语气写成还缺少必填信息。"
            if zh else
            "The brief is ready to build, so do not frame the reply like required information is still missing."
        )
    if question_text:
        return (
            f"如果要追问，只问这一个用户能直接回答的问题：{question_text}"
            if zh else
            f"If you ask a follow-up, make it exactly one user-facing question: {question_text}"
        )
    return (
        "只有在确实能明显帮助收口时才追问，而且一次只问一个问题。"
        if zh else
        "Only ask a follow-up if it clearly sharpens the brief, and never ask more than one question."
    )


def _build_dialogue_public_draft_summary(
    analysis: AnalyzeDialogueTurnResponse,
    *,
    zh: bool,
) -> str:
    plan_draft = analysis.plan_draft
    if not plan_draft:
        return "暂无" if zh else "none yet"

    pieces = [
        _normalize_free_text(str(getattr(plan_draft, "summary", "") or "")),
        _normalize_free_text(str(getattr(plan_draft, "interaction", "") or "")),
        _normalize_free_text(str(getattr(plan_draft, "objective", "") or "")),
    ]
    compact = " / ".join(piece for piece in pieces if piece)
    return compact or ("暂无" if zh else "none yet")


def _build_dialogue_public_readiness_hint(
    analysis: AnalyzeDialogueTurnResponse,
    *,
    zh: bool,
) -> str:
    if analysis.ready_to_generate and analysis.current_question:
        return (
            "可以开始创建了，但如果继续问，只能当成可选优化。"
            if zh else
            "Ready to build now; any follow-up must sound optional rather than required."
        )
    if analysis.ready_to_generate:
        return (
            "可以明确告诉用户：现在已经能开始创建。"
            if zh else
            "You can clearly tell the user the brief is ready and creation can start now."
        )
    return (
        "还没完全收口，不要说已经可以直接生成。"
        if zh else
        "The brief is not fully locked yet, so do not say generation is ready yet."
    )


def _build_dialogue_reply_system_prompt_from_catalog(
    analysis: AnalyzeDialogueTurnResponse,
) -> str:
    language = _detect_ui_language(
        " ".join(
            part for part in [
                str(getattr(analysis.slots, "reference_game", "") or ""),
                str(getattr(analysis.slots, "theme", "") or ""),
                str(getattr(analysis.slots, "core_mechanic", "") or ""),
            ] if part
        )
    )
    zh = language.startswith("zh")
    base_prompt = require_prompt("prompt.dialogue_system").format(
        slot_summary=_build_dialogue_public_direction_summary(analysis, zh=zh),
        missing_slots=_build_dialogue_public_follow_up_guidance(analysis, zh=zh),
    )
    return safe_format_prompt(
        require_prompt("prompt.dialogue_reply_system"),
        base_prompt=base_prompt,
    ).strip()


def _build_dialogue_reply_user_prompt_from_catalog(
    *,
    request: AnalyzeDialogueTurnRequest,
    analysis: AnalyzeDialogueTurnResponse,
    source_text: str,
) -> str:
    language = _detect_ui_language(" ".join(part for part in [request.title or "", source_text] if part))
    zh = language.startswith("zh")
    history = [
        f"{item.role}: {_normalize_free_text(item.content)}"
        for item in (request.conversation or [])[-4:]
        if _normalize_free_text(item.content)
    ]
    public_fallback_reply = _compose_creation_session_reply_v2(
        slots=analysis.slots,
        current_question=analysis.current_question,
        ready_to_generate=analysis.ready_to_generate,
        source_text=source_text,
        title=request.title,
        latest_user_answer=request.latest_user_answer,
        question_strategy=analysis.question_strategy,
        plan_draft=analysis.plan_draft,
    )

    return safe_format_prompt(
        require_prompt(
            "prompt.dialogue_reply_user_template_zh"
            if zh else
            "prompt.dialogue_reply_user_template_en"
        ),
        initial_idea=_normalize_free_text(source_text) or ("none yet" if zh else "none"),
        latest_user_message=_normalize_free_text(
            request.latest_user_answer or _latest_user_answer_from_history(request.conversation or [])
        ) or ("none yet" if zh else "none"),
        recent_conversation="\n".join(history) if history else ("none yet" if zh else "none"),
        inferred_direction=_build_dialogue_public_direction_summary(analysis, zh=zh),
        draft_summary=_build_dialogue_public_draft_summary(analysis, zh=zh),
        current_question=analysis.current_question.prompt if analysis.current_question else ("none" if zh else "none"),
        next_best_question_reason=_build_dialogue_public_follow_up_guidance(analysis, zh=zh),
        ready_to_generate=_build_dialogue_public_readiness_hint(analysis, zh=zh),
        safe_fallback_reply=public_fallback_reply,
    ).strip()


def _chunk_reply_for_streaming(message: str) -> List[str]:
    normalized = _normalize_free_text(message)
    if not normalized:
        return []
    chunks = [item.strip() for item in re.split(r"(?<=[。！？.!?])\s*", normalized) if item.strip()]
    if not chunks:
        chunks = [normalized]
    flattened: List[str] = []
    for chunk in chunks:
        if len(chunk) <= 48:
            flattened.append(chunk)
            continue
        for index in range(0, len(chunk), 24):
            flattened.append(chunk[index:index + 24])
    return [item for item in flattened if item]
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
