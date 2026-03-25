"""Stage 01 + 02: Dialogue engine and single-shot intent parsing."""

from __future__ import annotations

import ast
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
from ..services.llm_client import LLMClient
from .prompt_store import require_prompt

logger = logging.getLogger(__name__)

_sessions: Dict[str, DialogueSession] = {}

GAME_TYPE_DEFAULTS: Dict[str, Dict[str, str]] = {
    "dodge": {
        "core_mechanic": "Move to avoid hazards and survive.",
        "win_condition": "Survive as long as possible.",
        "input_method": "touch",
    },
    "platformer": {
        "core_mechanic": "Jump across platforms and reach the goal.",
        "win_condition": "Reach the finish point.",
        "input_method": "tap",
    },
    "runner": {
        "core_mechanic": "Run continuously and dodge obstacles.",
        "win_condition": "Travel as far as possible.",
        "input_method": "tap",
    },
    "shooter": {
        "core_mechanic": "Aim and defeat enemies.",
        "win_condition": "Defeat all enemies or reach target score.",
        "input_method": "touch",
    },
    "puzzle": {
        "core_mechanic": "Solve the puzzle through pattern matching or logic.",
        "win_condition": "Clear the board or solve the puzzle.",
        "input_method": "touch",
    },
    "rhythm": {
        "core_mechanic": "Tap notes in time with the beat.",
        "win_condition": "Finish the track with a passing score.",
        "input_method": "tap",
    },
    "tower_defense": {
        "core_mechanic": "Place defenses to stop incoming enemies.",
        "win_condition": "Defend all waves.",
        "input_method": "touch",
    },
    "idle": {
        "core_mechanic": "Accumulate resources and upgrade automation.",
        "win_condition": "Reach the target progression milestone.",
        "input_method": "tap",
    },
    "rpg": {
        "core_mechanic": "Explore, battle, and grow the character.",
        "win_condition": "Complete the main quest objective.",
        "input_method": "touch",
    },
}

ENTITY_DEFAULTS: Dict[str, List[Dict[str, Any]]] = {
    "dodge": [
        {"name": "player", "role": "player", "shape": "triangle", "color": "#6366f1"},
        {"name": "hazard", "role": "obstacle", "shape": "square", "color": "#f43f5e"},
        {"name": "pickup", "role": "collectible", "shape": "diamond", "color": "#22c55e"},
    ],
    "platformer": [
        {"name": "hero", "role": "player", "shape": "square", "color": "#6366f1"},
        {"name": "platform", "role": "obstacle", "shape": "rectangle"},
        {"name": "pickup", "role": "collectible", "shape": "circle", "color": "#22c55e"},
    ],
    "runner": [
        {"name": "runner", "role": "player", "shape": "square", "color": "#6366f1"},
        {"name": "obstacle", "role": "obstacle", "shape": "rectangle", "color": "#f43f5e"},
        {"name": "coin", "role": "collectible", "shape": "circle", "color": "#22c55e"},
    ],
    "shooter": [
        {"name": "ship", "role": "player", "shape": "triangle", "color": "#6366f1"},
        {"name": "enemy", "role": "enemy", "shape": "square", "color": "#f43f5e"},
        {"name": "powerup", "role": "collectible", "shape": "diamond", "color": "#22c55e"},
    ],
    "puzzle": [
        {"name": "piece", "role": "player", "shape": "square", "color": "#6366f1"},
        {"name": "goal", "role": "collectible", "shape": "square", "color": "#22c55e"},
    ],
    "rhythm": [
        {"name": "note", "role": "collectible", "shape": "rectangle", "color": "#6366f1"},
        {"name": "marker", "role": "obstacle", "shape": "rectangle", "color": "#f43f5e"},
    ],
}

GENERIC_ENTITY_DEFAULTS: List[Dict[str, Any]] = [
    {"name": "player", "role": "player", "shape": "circle", "color": "#6366f1"},
    {"name": "hazard", "role": "obstacle", "shape": "square", "color": "#f43f5e"},
    {"name": "goal", "role": "collectible", "shape": "diamond", "color": "#22c55e"},
]

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


def _infer_slots_from_text(text: str) -> Dict[str, Any]:
    source = (text or "").strip()
    if not source:
        return {}

    lowered = source.lower()
    inferred: Dict[str, Any] = {}

    game_type_rules = (
        ("dodge", ("躲避", "闪避", "dodg")),
        ("runner", ("跑酷", "runner", "endless run", "endless runner")),
        ("platformer", ("平台", "跳跃", "platformer", "jump between")),
        ("shooter", ("射击", "枪战", "shooter", "shoot")),
        ("puzzle", ("谜题", "拼图", "消除", "puzzle", "match-3", "merge")),
        ("rhythm", ("节奏", "音游", "rhythm", "beat")),
    )
    for game_type, markers in game_type_rules:
        if any(marker in source or marker in lowered for marker in markers):
            inferred["game_type"] = game_type
            break

    input_rules = (
        ("swipe", ("滑动", "左右移动", "swipe", "drag left and right")),
        ("tap", ("点击", "轻点", "tap")),
        ("drag", ("拖拽", "drag")),
        ("hold", ("长按", "hold")),
        ("touch", ("触摸", "touch")),
    )
    for input_method, markers in input_rules:
        if any(marker in source or marker in lowered for marker in markers):
            inferred["input_method"] = input_method
            break

    if "theme" not in inferred:
        theme_rules = (
            ("space", ("太空", "宇宙", "space", "cosmic")),
            ("zoo", ("动物园", "zoo")),
            ("neon", ("霓虹", "neon")),
            ("fantasy", ("奇幻", "fantasy")),
            ("ocean", ("海洋", "ocean", "underwater")),
        )
        for theme, markers in theme_rules:
            if any(marker in source or marker in lowered for marker in markers):
                inferred["theme"] = theme
                break

    game_type = inferred.get("game_type")
    defaults = GAME_TYPE_DEFAULTS.get(str(game_type), {}) if game_type else {}

    if defaults.get("core_mechanic"):
        inferred.setdefault("core_mechanic", defaults["core_mechanic"])
    if defaults.get("win_condition"):
        inferred.setdefault("win_condition", defaults["win_condition"])
    if defaults.get("input_method"):
        inferred.setdefault("input_method", defaults["input_method"])

    if "重新开始" in source or "restart" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + ["restart after losing"]
    if "点击开始" in source or "tap to start" in lowered or "click to start" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + ["tap to start"]

    return inferred


def _infer_slots_from_text(text: str) -> Dict[str, Any]:
    source = (text or "").strip()
    if not source:
        return {}

    lowered = source.lower()
    inferred: Dict[str, Any] = {}

    game_type_rules = (
        ("dodge", ("\u8e32\u907f", "\u95ea\u907f", "dodge")),
        ("runner", ("\u8dd1\u9177", "runner", "endless run", "endless runner")),
        ("platformer", ("\u5e73\u53f0", "\u8df3\u8dc3", "platformer", "jump between")),
        ("shooter", ("\u5c04\u51fb", "\u67aa\u6218", "shooter", "shoot")),
        ("puzzle", ("\u8c1c\u9898", "\u62fc\u56fe", "\u6d88\u9664", "puzzle", "match-3", "merge")),
        ("rhythm", ("\u8282\u594f", "\u97f3\u6e38", "rhythm", "beat")),
    )
    for game_type, markers in game_type_rules:
        if any(marker in source or marker in lowered for marker in markers):
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
        if any(marker in source or marker in lowered for marker in markers):
            inferred["input_method"] = input_method
            break

    if "theme" not in inferred:
        theme_rules = (
            ("space", ("\u592a\u7a7a", "\u5b87\u5b99", "space", "cosmic")),
            ("zoo", ("\u52a8\u7269\u56ed", "zoo")),
            ("neon", ("\u9713\u8679", "neon")),
            ("fantasy", ("\u5947\u5e7b", "fantasy")),
            ("ocean", ("\u6d77\u6d0b", "ocean", "underwater")),
        )
        for theme, markers in theme_rules:
            if any(marker in source or marker in lowered for marker in markers):
                inferred["theme"] = theme
                break

    game_type = inferred.get("game_type")
    defaults = GAME_TYPE_DEFAULTS.get(str(game_type), {}) if game_type else {}

    if defaults.get("core_mechanic"):
        inferred.setdefault("core_mechanic", defaults["core_mechanic"])
    if defaults.get("win_condition"):
        inferred.setdefault("win_condition", defaults["win_condition"])
    if defaults.get("input_method"):
        inferred.setdefault("input_method", defaults["input_method"])

    if "\u91cd\u65b0\u5f00\u59cb" in source or "restart" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + ["restart after losing"]
    if "\u70b9\u51fb\u5f00\u59cb" in source or "tap to start" in lowered or "click to start" in lowered:
        inferred.setdefault("special_rules", [])
        inferred["special_rules"] = list(inferred["special_rules"]) + ["tap to start"]

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
        )

    async def parse_description_to_spec(
        self,
        description: str,
        allow_fallback: bool = True,
    ) -> GameSpec:
        del allow_fallback
        if not self._client.is_enabled():
            raise RuntimeError("Real LLM mode is required for intent parsing")

        text = await self._client.complete(
            max_tokens=1024,
            system=_with_slot_json_contract(
                require_prompt("prompt.intent_parse_system")
            ),
            messages=[{"role": "user", "content": description}],
            step_key="intent_parse",
            stage="intent_parsing",
            prefer_fast=False,
        )
        slot_data = await self._extract_slot_payload_with_repair(
            raw_text=text,
            source_text=description,
            step_key="intent_parse",
            stage="intent_parsing",
        )
        slots = SlotState(**slot_data)
        return _build_game_spec(slots, source_description=description)

    async def _llm_process(self, session: DialogueSession) -> Tuple[str, List[str]]:
        old_slots = session.slots.model_copy()

        slot_text = await self._client.complete(
            max_tokens=1024,
            system=_with_slot_json_contract(
                require_prompt("prompt.slot_extraction_system")
            ),
            messages=_history_to_messages(session.history),
            step_key="dialogue.slot_extract",
            stage="dialogue",
            prefer_fast=False,
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
    ) -> Dict[str, Any]:
        heuristic_slot_data = _normalize_slot_payload({
            **_infer_slots_from_text(raw_text),
            **_infer_slots_from_text(source_text),
        })
        raw_slot_data = _normalize_slot_payload(_safe_parse_json(raw_text) or {})
        slot_data = _merge_slot_payloads(
            heuristic_slot_data,
            raw_slot_data,
        )
        if raw_slot_data and slot_data:
            return slot_data

        repair_input = require_prompt("prompt.slot_json_repair_user_template").format(
            source_text=source_text or "(empty)",
            raw_parser_output=_clean_llm_output(raw_text) or "(empty)",
        )
        repaired_text = await self._client.complete(
            max_tokens=1024,
            system=_with_slot_json_contract(
                require_prompt("prompt.slot_json_repair_system")
            ),
            messages=[{"role": "user", "content": repair_input}],
            step_key=step_key,
            stage=stage,
            prefer_fast=False,
        )
        repaired_slot_data = _merge_slot_payloads(
            heuristic_slot_data,
            _normalize_slot_payload(_safe_parse_json(repaired_text) or {}),
        )
        if repaired_slot_data:
            return repaired_slot_data

        if heuristic_slot_data.get("game_type"):
            logger.warning(
                "LLM slot extraction repair failed; using heuristic slot inference for source=%s",
                source_text[:120],
            )
            return heuristic_slot_data

        raise ValueError("LLM slot extraction returned no valid JSON")

    @staticmethod
    def _next_state(current: DialogueState, fill_pct: float) -> DialogueState:
        if current == DialogueState.greeting:
            return DialogueState.describing
        if current == DialogueState.describing and fill_pct >= 0.6:
            return DialogueState.clarifying
        if current == DialogueState.clarifying and fill_pct >= 1.0:
            return DialogueState.confirmed
        return current


def _build_game_spec(slots: SlotState, *, source_description: str = "") -> GameSpec:
    game_type = (slots.game_type or "").strip()
    if not game_type:
        raise ValueError("Missing required slot: game_type")

    defaults = GAME_TYPE_DEFAULTS.get(game_type, {})
    entity_defs = ENTITY_DEFAULTS.get(game_type, GENERIC_ENTITY_DEFAULTS)
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

    art_style = slots.visual_style or "geometric"
    visual = VisualStyle(
        theme=slots.theme or "custom",
        art_style=art_style,
        background="gradient",
        effects=["glow"] if art_style == "neon" else [],
    )
    intent_summary = (slots.core_mechanic or defaults.get("core_mechanic", "")).strip()
    special_rules = [
        re.sub(r"\s+", " ", str(rule).strip())
        for rule in (slots.special_rules or [])
        if str(rule).strip()
    ]
    reference_game = (slots.reference_game or "").strip() or None
    normalized_description = re.sub(r"\s+", " ", source_description.strip()) if source_description else ""

    return GameSpec(
        game_type=game_type,
        source_description=normalized_description,
        intent_summary=intent_summary,
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
