"""Stage 01 + 02: Dialogue engine and single-shot intent parsing."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..api.models import (
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    CoreMechanic,
    DialogueSession,
    DialogueState,
    GameEntity,
    GameRules,
    GameSpec,
    PlatformConstraints,
    SlotState,
    VisualStyle,
)
from ..services.llm_client import LLMClient, LLMResponseTruncatedError
from .prompt_store import require_prompt

logger = logging.getLogger(__name__)

_sessions: Dict[str, DialogueSession] = {}

LOCALIZED_GAME_TYPE_DEFAULTS: Dict[str, Dict[str, Dict[str, str]]] = {
    "dodge": {
        "en-US": {
            "core_mechanic": "Move through the arena, avoid hazards, and stay alive.",
            "win_condition": "Survive long enough to beat your best run.",
            "input_method": "touch",
        },
        "zh-CN": {
            "core_mechanic": "在场地中移动，躲开危险并尽量坚持更久。",
            "win_condition": "尽可能长时间存活并刷新自己的成绩。",
            "input_method": "touch",
        },
    },
    "platformer": {
        "en-US": {
            "core_mechanic": "Jump across gaps, use moving platforms, and reach the end safely.",
            "win_condition": "Reach the finish marker without losing all lives.",
            "input_method": "tap",
        },
        "zh-CN": {
            "core_mechanic": "跨越空隙、利用平台移动，并安全抵达终点。",
            "win_condition": "在生命耗尽前抵达终点标记。",
            "input_method": "tap",
        },
    },
    "runner": {
        "en-US": {
            "core_mechanic": "Keep running, switch lanes or dodge obstacles, and maintain momentum.",
            "win_condition": "Run as far as possible while collecting bonuses.",
            "input_method": "tap",
        },
        "zh-CN": {
            "core_mechanic": "持续前进，切换路线或躲开障碍，并保持节奏。",
            "win_condition": "尽可能跑得更远，同时收集加成道具。",
            "input_method": "tap",
        },
    },
    "shooter": {
        "en-US": {
            "core_mechanic": "Aim, fire at threats, and control space under pressure.",
            "win_condition": "Clear enough enemies or reach the target score.",
            "input_method": "touch",
        },
        "zh-CN": {
            "core_mechanic": "瞄准并攻击威胁目标，在压力中控制场面。",
            "win_condition": "消灭足够多的敌人或达到目标分数。",
            "input_method": "touch",
        },
    },
    "puzzle": {
        "en-US": {
            "core_mechanic": "Solve the board through matching, ordering, or spatial logic.",
            "win_condition": "Clear the board or satisfy the puzzle target.",
            "input_method": "touch",
        },
        "zh-CN": {
            "core_mechanic": "通过匹配、排序或空间逻辑来解开棋盘谜题。",
            "win_condition": "清空棋盘或完成谜题目标。",
            "input_method": "touch",
        },
    },
    "rhythm": {
        "en-US": {
            "core_mechanic": "Tap in rhythm and chain accurate hits for a combo.",
            "win_condition": "Finish the song or section with a passing score.",
            "input_method": "tap",
        },
        "zh-CN": {
            "core_mechanic": "按节奏点击，并通过精准命中维持连击。",
            "win_condition": "以达标分数完成当前曲段或整首曲目。",
            "input_method": "tap",
        },
    },
    "tower_defense": {
        "en-US": {
            "core_mechanic": "Place defenses and react to incoming waves efficiently.",
            "win_condition": "Hold the line until every wave is cleared.",
            "input_method": "touch",
        },
        "zh-CN": {
            "core_mechanic": "摆放防御单位，并高效应对不断来袭的波次。",
            "win_condition": "守住防线直到所有波次结束。",
            "input_method": "touch",
        },
    },
    "idle": {
        "en-US": {
            "core_mechanic": "Accumulate resources, automate production, and unlock upgrades.",
            "win_condition": "Reach the target progression milestone.",
            "input_method": "tap",
        },
        "zh-CN": {
            "core_mechanic": "积累资源、自动化产出，并逐步解锁升级。",
            "win_condition": "达成目标成长里程碑。",
            "input_method": "tap",
        },
    },
    "rpg": {
        "en-US": {
            "core_mechanic": "Explore encounters, fight threats, and grow the hero.",
            "win_condition": "Complete the main quest objective.",
            "input_method": "touch",
        },
        "zh-CN": {
            "core_mechanic": "探索遭遇、战胜敌人，并逐步强化主角。",
            "win_condition": "完成主要任务目标。",
            "input_method": "touch",
        },
    },
}

ENTITY_VARIANTS: Dict[str, List[List[Dict[str, Any]]]] = {
    "dodge": [
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
    "platformer": [
        [
            {"name": "hero", "role": "player", "shape": "square", "color": "#6366f1"},
            {"name": "platform", "role": "obstacle", "shape": "rectangle", "color": "#475569"},
            {"name": "pickup", "role": "collectible", "shape": "circle", "color": "#22c55e"},
        ],
        [
            {"name": "climber", "role": "player", "shape": "circle", "color": "#06b6d4"},
            {"name": "ledge", "role": "obstacle", "shape": "rectangle", "color": "#f59e0b"},
            {"name": "badge", "role": "collectible", "shape": "diamond", "color": "#f43f5e"},
        ],
        [
            {"name": "mascot", "role": "player", "shape": "triangle", "color": "#8b5cf6"},
            {"name": "crate", "role": "obstacle", "shape": "square", "color": "#f97316"},
            {"name": "gem", "role": "collectible", "shape": "diamond", "color": "#10b981"},
        ],
    ],
    "runner": [
        [
            {"name": "runner", "role": "player", "shape": "square", "color": "#6366f1"},
            {"name": "obstacle", "role": "obstacle", "shape": "rectangle", "color": "#f43f5e"},
            {"name": "coin", "role": "collectible", "shape": "circle", "color": "#22c55e"},
        ],
        [
            {"name": "courier", "role": "player", "shape": "circle", "color": "#0ea5e9"},
            {"name": "cone", "role": "obstacle", "shape": "triangle", "color": "#f97316"},
            {"name": "ticket", "role": "collectible", "shape": "rectangle", "color": "#eab308"},
        ],
        [
            {"name": "rocket", "role": "player", "shape": "diamond", "color": "#8b5cf6"},
            {"name": "gate", "role": "obstacle", "shape": "square", "color": "#ef4444"},
            {"name": "energy", "role": "collectible", "shape": "circle", "color": "#14b8a6"},
        ],
    ],
    "shooter": [
        [
            {"name": "ship", "role": "player", "shape": "triangle", "color": "#6366f1"},
            {"name": "enemy", "role": "enemy", "shape": "square", "color": "#f43f5e"},
            {"name": "powerup", "role": "collectible", "shape": "diamond", "color": "#22c55e"},
        ],
        [
            {"name": "ranger", "role": "player", "shape": "circle", "color": "#06b6d4"},
            {"name": "drone", "role": "enemy", "shape": "diamond", "color": "#ef4444"},
            {"name": "charge", "role": "collectible", "shape": "rectangle", "color": "#f59e0b"},
        ],
        [
            {"name": "turret", "role": "player", "shape": "square", "color": "#8b5cf6"},
            {"name": "phantom", "role": "enemy", "shape": "triangle", "color": "#fb7185"},
            {"name": "shield", "role": "collectible", "shape": "circle", "color": "#10b981"},
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
    "rhythm": [
        [
            {"name": "note", "role": "collectible", "shape": "rectangle", "color": "#6366f1"},
            {"name": "marker", "role": "obstacle", "shape": "rectangle", "color": "#f43f5e"},
        ],
        [
            {"name": "beat", "role": "collectible", "shape": "circle", "color": "#06b6d4"},
            {"name": "pulse", "role": "obstacle", "shape": "diamond", "color": "#f97316"},
        ],
        [
            {"name": "tone", "role": "collectible", "shape": "diamond", "color": "#8b5cf6"},
            {"name": "bar", "role": "obstacle", "shape": "rectangle", "color": "#e11d48"},
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
    "dodge": [
        {"theme": "garden", "palette": ["#365314", "#4ade80", "#f472b6", "#facc15", "#fefce8"], "background": "meadow", "art_style": "soft_geometric", "effects": ["petals"]},
        {"theme": "city", "palette": ["#1f2937", "#38bdf8", "#a3e635", "#fb7185", "#f9fafb"], "background": "skyline", "art_style": "flat", "effects": ["speed_lines"]},
        {"theme": "toy", "palette": ["#1d4ed8", "#60a5fa", "#f97316", "#ef4444", "#fefce8"], "background": "playroom", "art_style": "playful", "effects": []},
    ],
    "runner": [
        {"theme": "city", "palette": ["#1f2937", "#38bdf8", "#a3e635", "#fb7185", "#f9fafb"], "background": "skyline", "art_style": "flat", "effects": ["speed_lines"]},
        {"theme": "forest", "palette": ["#14532d", "#22c55e", "#84cc16", "#f59e0b", "#f7fee7"], "background": "canopy", "art_style": "storybook", "effects": ["leaf_trails"]},
        {"theme": "sports", "palette": ["#0f172a", "#22c55e", "#38bdf8", "#f97316", "#f8fafc"], "background": "arena", "art_style": "bold_flat", "effects": ["streaks"]},
    ],
    "platformer": [
        {"theme": "fantasy", "palette": ["#312e81", "#8b5cf6", "#22c55e", "#f59e0b", "#fef3c7"], "background": "mist", "art_style": "storybook", "effects": ["sparkles"]},
        {"theme": "garden", "palette": ["#365314", "#4ade80", "#f472b6", "#facc15", "#fefce8"], "background": "meadow", "art_style": "soft_geometric", "effects": ["petals"]},
        {"theme": "toy", "palette": ["#1d4ed8", "#60a5fa", "#f97316", "#ef4444", "#fefce8"], "background": "playroom", "art_style": "playful", "effects": []},
    ],
    "shooter": [
        {"theme": "city", "palette": ["#111827", "#8b5cf6", "#06b6d4", "#f43f5e", "#f8fafc"], "background": "night_grid", "art_style": "neon", "effects": ["glow"]},
        {"theme": "ocean", "palette": ["#0f172a", "#0ea5e9", "#14b8a6", "#facc15", "#ecfeff"], "background": "waves", "art_style": "soft_geometric", "effects": ["bubbles"]},
        {"theme": "fantasy", "palette": ["#312e81", "#8b5cf6", "#22c55e", "#f59e0b", "#fef3c7"], "background": "mist", "art_style": "storybook", "effects": ["sparkles"]},
    ],
    "puzzle": [
        {"theme": "candy", "palette": ["#831843", "#f472b6", "#38bdf8", "#facc15", "#fff1f2"], "background": "sweetscape", "art_style": "playful", "effects": ["sparkles"]},
        {"theme": "garden", "palette": ["#365314", "#4ade80", "#f472b6", "#facc15", "#fefce8"], "background": "meadow", "art_style": "soft_geometric", "effects": ["petals"]},
        {"theme": "toy", "palette": ["#1d4ed8", "#60a5fa", "#f97316", "#ef4444", "#fefce8"], "background": "playroom", "art_style": "playful", "effects": []},
    ],
    "rhythm": [
        {"theme": "neon", "palette": ["#111827", "#8b5cf6", "#06b6d4", "#f43f5e", "#f8fafc"], "background": "dark_gradient", "art_style": "neon", "effects": ["glow"]},
        {"theme": "festival", "palette": ["#581c87", "#a855f7", "#06b6d4", "#f59e0b", "#fdf4ff"], "background": "stage_lights", "art_style": "bold_flat", "effects": ["pulse"]},
        {"theme": "sports", "palette": ["#0f172a", "#22c55e", "#38bdf8", "#f97316", "#f8fafc"], "background": "arena", "art_style": "bold_flat", "effects": ["streaks"]},
    ],
}

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

CONTEXT_GAME_TYPE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("puzzle", EDUCATIONAL_REQUEST_MARKERS),
    ("tower_defense", ("tower defense", "defend", "\u5854\u9632", "\u9632\u5b88", "\u5b88\u536b")),
    ("idle", ("idle", "incremental", "auto", "\u653e\u7f6e", "\u6302\u673a", "\u81ea\u52a8")),
    ("rpg", ("rpg", "quest", "hero", "adventure", "\u5192\u9669", "\u89d2\u8272", "\u82f1\u96c4")),
    ("rhythm", ("rhythm", "beat", "music", "dance", "\u8282\u594f", "\u97f3\u4e50", "\u821e")),
    ("shooter", ("shoot", "shooter", "gun", "attack", "\u5c04\u51fb", "\u5f00\u706b", "\u653b\u51fb")),
    ("platformer", ("jump", "platform", "climb", "\u8df3", "\u5e73\u53f0", "\u722c")),
    ("runner", ("run", "runner", "race", "chase", "catch", "\u8dd1", "\u8ffd", "\u9017", "\u6293")),
    ("puzzle", ("puzzle", "match", "merge", "sort", "solve", "circuit", "wire", "battery", "bulb", "switch", "connect", "drag", "assemble", "\u8c1c\u9898", "\u6d88\u9664", "\u62fc", "\u914d\u5bf9", "\u7535\u8def", "\u5bfc\u7ebf", "\u7535\u6c60", "\u706f\u6ce1", "\u5f00\u5173", "\u8fde\u63a5", "\u62d6\u62fd", "\u7ec4\u88c5")),
    ("dodge", ("dodge", "avoid", "survive", "escape", "\u95ea", "\u8e32", "\u907f", "\u751f\u5b58", "\u9003")),
)

CONTEXT_THEME_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("space", ("space", "galaxy", "star", "planet", "\u592a\u7a7a", "\u5b87\u5b99", "\u661f")),
    ("zoo", ("animal", "zoo", "pig", "cat", "dog", "panda", "\u52a8\u7269", "\u5c0f\u732a", "\u732a", "\u732b", "\u72d7", "\u718a\u732b")),
    ("ocean", ("ocean", "sea", "water", "fish", "\u6d77", "\u6d0b", "\u6c34", "\u9c7c")),
    ("forest", ("forest", "jungle", "tree", "\u68ee\u6797", "\u4e1b\u6797", "\u6811")),
    ("city", ("city", "street", "car", "traffic", "\u57ce\u5e02", "\u8857", "\u6c7d\u8f66", "\u4ea4\u901a")),
    ("food", ("food", "kitchen", "chef", "candy", "dessert", "\u98df\u7269", "\u53a8\u623f", "\u539f\u70b9", "\u7cd6", "\u751c\u54c1")),
    ("toy", ("toy", "block", "brick", "\u73a9\u5177", "\u79ef\u6728", "\u65b9\u5757")),
    ("fantasy", ("fantasy", "magic", "dragon", "\u5947\u5e7b", "\u9b54\u6cd5", "\u9f99")),
)

EXPLICIT_GAME_TYPE_MARKERS: Dict[str, Tuple[str, ...]] = {
    "dodge": ("dodge", "\u8e32\u907f", "\u95ea\u907f"),
    "runner": ("runner", "endless runner", "\u8dd1\u9177"),
    "platformer": ("platformer", "\u5e73\u53f0\u8df3\u8dc3"),
    "shooter": ("shooter", "shoot", "\u5c04\u51fb"),
    "puzzle": ("puzzle", "match-3", "merge", "\u8c1c\u9898"),
    "rhythm": ("rhythm", "beat game", "\u97f3\u4e50\u8282\u594f"),
    "idle": ("idle", "incremental", "\u653e\u7f6e"),
    "rpg": ("rpg", "role playing", "\u89d2\u8272\u626e\u6f14"),
    "tower_defense": ("tower defense", "\u5854\u9632"),
}

SPARSE_GAME_TYPE_VARIANTS: Dict[str, Tuple[str, ...]] = {
    "dodge": ("dodge", "shooter", "runner", "rhythm"),
    "runner": ("runner", "platformer", "dodge", "rhythm"),
    "platformer": ("platformer", "runner", "dodge"),
    "shooter": ("shooter", "dodge", "runner"),
    "puzzle": ("puzzle", "idle", "rhythm"),
    "rhythm": ("rhythm", "runner", "puzzle"),
    "idle": ("idle", "puzzle", "rpg"),
    "rpg": ("rpg", "dodge", "idle"),
}

SPARSE_DEFAULT_GAME_TYPES = ("puzzle", "runner", "platformer", "dodge", "shooter", "rhythm", "idle", "rpg")
SPARSE_DEFAULT_THEMES = ("arcade", "neon", "fantasy", "ocean", "forest", "city", "food", "toy")


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
        if isinstance(value, str):
            text = value.strip()
            if text:
                normalized[key] = text
            continue
        normalized[key] = value
    return normalized


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
        if any(_contains_marker(combined, marker) for marker in ("run", "race", "catch", "\u8ffd", "\u6293", "\u9017")):
            return "runner"
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
        "dodge": "Avoid the stock meteor-survival setup unless the brief explicitly asks for it.",
        "runner": "Avoid defaulting to a plain three-lane endless runner when another readable loop can fit.",
        "platformer": "Avoid a generic left-to-right jump course if a more novel traversal loop fits the brief.",
        "shooter": "Avoid a stock top-down wave-survival arena unless the request explicitly asks for it.",
        "puzzle": "Avoid turning every open brief into a match-3 clone.",
        "rhythm": "Avoid a bare tap-on-beat lane if a more characterful rhythm loop still reads clearly on mobile.",
        "idle": "Avoid a bare number-increment loop if a clearer fantasy or objective can be surfaced.",
        "rpg": "Avoid reducing the loop to simple survive-and-score arcade play when a light quest structure can fit.",
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
    normalized_game_type = (game_type or "").strip()
    normalized_description = _normalize_free_text(source_description)
    if not normalized_game_type or not normalized_description:
        return normalized_game_type

    if not _looks_like_educational_request(normalized_description):
        return normalized_game_type

    if normalized_game_type in {"runner", "lane runner", "platformer", "dodge", "shooter", "top down shooter"}:
        return "puzzle"

    return normalized_game_type


def _stable_variant_index(*parts: str, count: int) -> int:
    if count <= 1:
        return 0
    seed = "|".join((part or "").strip() for part in parts if part is not None)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return digest[0] % count


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

    variants = VISUAL_VARIANTS_BY_GAME_TYPE.get(game_type, VISUAL_VARIANTS_BY_GAME_TYPE.get("dodge", []))
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
        ("puzzle", EDUCATIONAL_REQUEST_MARKERS),
        ("tower_defense", ("塔防", "defense", "tower defense", "守塔")),
        ("idle", ("放置", "idle", "挂机", "incremental")),
        ("rpg", ("角色扮演", "冒险", "rpg", "quest")),
        ("dodge", ("躲避", "闪避", "dodge", "avoid hazards")),
        ("runner", ("跑酷", "runner", "endless run", "endless runner")),
        ("platformer", ("平台", "跳跃", "platformer", "jump between")),
        ("shooter", ("射击", "枪战", "shooter", "shoot")),
        ("puzzle", ("谜题", "拼图", "消除", "puzzle", "match-3", "merge")),
        ("rhythm", ("节奏", "音游", "rhythm", "beat")),
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
        try:
            return await self._client.complete(
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                step_key=step_key,
                stage=stage,
                prefer_fast=False,
                allow_provider_fallback=True,
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
            raise

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
            reply = await self._client.complete(
                max_tokens=2048,
                system=system,
                messages=_history_to_messages(session.history),
                step_key="dialogue.reply",
                stage="dialogue",
                prefer_fast=True,
            )
            reply = reply.strip()
        except Exception as exc:
            logger.warning("Reply generation error: %s", exc)
            reply = _fallback_reply(session.state, session.slots)

        return reply, updated

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
        heuristic_slot_data = _normalize_slot_payload({
            **_infer_slots_from_text(raw_text),
            **_infer_slots_from_text(source_text),
            **_infer_slots_from_text(title or ""),
        })
        raw_slot_data = _normalize_slot_payload(_safe_parse_json(raw_text) or {})
        slot_data = _merge_slot_payloads(
            heuristic_slot_data,
            raw_slot_data,
        )
        if raw_slot_data and _has_minimum_viable_slot_payload(slot_data):
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
        repaired_slot_data = _merge_slot_payloads(
            heuristic_slot_data,
            _normalize_slot_payload(_safe_parse_json(repaired_text) or {}),
        )
        if _has_minimum_viable_slot_payload(repaired_slot_data):
            return repaired_slot_data

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
                preferred_game_type=preferred_game_type,
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
                "preferredGameType": preferred_game_type,
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
        special_rules=special_rules,
        reference_game=reference_game,
        platform_constraints=PlatformConstraints(
            input_mode=slots.input_method or defaults.get("input_method", "touch"),
        ),
    )


def _history_to_messages(history: List[ConversationMessage]) -> List[Dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in history]


def _format_slot_summary(slots: SlotState) -> str:
    lines = []
    for field in ["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"]:
        value = getattr(slots, field)
        label = SLOT_LABELS.get(field, field)
        lines.append(f"  {label}: {value or '(unknown)'}")
    return "\n".join(lines)


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
        "game_type": "What kind of game is it: runner, puzzle, RPG, shooter, or something else?",
        "core_mechanic": "What does the player do most of the time?",
        "theme": "What theme or world should the game use?",
        "input_method": "How should the player control it on mobile?",
        "win_condition": "What counts as winning or clearing the game?",
        "difficulty": "Should the difficulty be easy, medium, hard, or progressive?",
    }
    return prompts.get(next_field, "Tell me a bit more about the game you want.")
