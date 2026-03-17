"""Stage 01 + 02: Dialogue Engine with Slot Filling and Intent Parser.

Implements:
  - 4-state conversation state machine (greeting / describing / clarifying / confirmed)
  - Slot Filling: 6 required + 4 optional slots extracted by Claude
  - Convert confirmed SlotState → GameSpec (structured data contract)
"""

from __future__ import annotations

import json
import logging
import re
import uuid
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
from ..config.settings import settings
from ..services.llm_client import LLMClient
from .prompt_store import get_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory session store (replace with Redis in production)
# ---------------------------------------------------------------------------
_sessions: Dict[str, DialogueSession] = {}


# ---------------------------------------------------------------------------
# Default derivation tables
# ---------------------------------------------------------------------------

GAME_TYPE_DEFAULTS: Dict[str, Dict] = {
    "dodge": {
        "core_mechanic": "左右移动躲避下落物体",
        "win_condition": "存活指定时间",
        "input_method": "touch",
    },
    "platformer": {
        "core_mechanic": "跳跃穿越平台到达终点",
        "win_condition": "到达终点",
        "input_method": "tap",
    },
    "runner": {
        "core_mechanic": "自动奔跑，跳跃躲避障碍",
        "win_condition": "跑得尽量远",
        "input_method": "tap",
    },
    "shooter": {
        "core_mechanic": "瞄准并消灭敌人",
        "win_condition": "消灭所有敌人",
        "input_method": "tap",
    },
    "puzzle": {
        "core_mechanic": "移动方块完成匹配",
        "win_condition": "清空棋盘",
        "input_method": "swipe",
    },
    "rhythm": {
        "core_mechanic": "在正确时机点击音符",
        "win_condition": "完成全部音符",
        "input_method": "tap",
    },
    "tower_defense": {
        "core_mechanic": "建造防御塔阻止敌人通过",
        "win_condition": "防守所有波次",
        "input_method": "tap",
    },
    "idle": {
        "core_mechanic": "点击积累资源，自动升级",
        "win_condition": "达到资源上限",
        "input_method": "tap",
    },
}

ENTITY_DEFAULTS: Dict[str, List[Dict]] = {
    "dodge": [
        {"name": "ship", "role": "player", "shape": "triangle", "color": "#6366f1"},
        {"name": "asteroid", "role": "obstacle", "shape": "circle", "spawn_rate": 18},
        {"name": "crystal", "role": "collectible", "shape": "diamond", "spawn_rate": 50},
    ],
    "platformer": [
        {"name": "hero", "role": "player", "shape": "square", "color": "#6366f1"},
        {"name": "platform", "role": "obstacle", "shape": "rectangle"},
        {"name": "spike", "role": "obstacle", "shape": "triangle", "color": "#f43f5e"},
    ],
    "runner": [
        {"name": "runner", "role": "player", "shape": "square", "color": "#6366f1"},
        {"name": "obstacle", "role": "obstacle", "shape": "rectangle"},
        {"name": "coin", "role": "collectible", "shape": "circle", "color": "#fbbf24"},
    ],
    "shooter": [
        {"name": "turret", "role": "player", "shape": "circle", "color": "#6366f1"},
        {"name": "bullet", "role": "collectible", "shape": "circle"},
        {"name": "enemy", "role": "enemy", "shape": "square", "color": "#f43f5e"},
    ],
    "puzzle": [
        {"name": "block", "role": "player", "shape": "square"},
        {"name": "target", "role": "obstacle", "shape": "square"},
    ],
    "rhythm": [
        {"name": "note", "role": "collectible", "shape": "rectangle", "color": "#6366f1"},
        {"name": "lane", "role": "obstacle", "shape": "rectangle"},
    ],
}


# ---------------------------------------------------------------------------
# JSON safe parser — handles markdown fences and surrounding text
# ---------------------------------------------------------------------------

def _parse_json_safe(text: str) -> Optional[Any]:
    """Extract and parse the first JSON object from LLM output.

    Handles:
    - Markdown code fences (```json ... ```)
    - Surrounding prose ("Here's the JSON: {...}")
    - Nested braces by using json.JSONDecoder.raw_decode
    """
    # Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text).strip()

    # Try raw_decode from the first { — handles surrounding text cleanly
    start = text.find("{")
    if start == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, start)
        return obj
    except json.JSONDecodeError:
        pass

    # Last resort: grab everything between first { and last }
    end = text.rfind("}")
    if end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Slot Filling extraction prompt
# ---------------------------------------------------------------------------

SLOT_EXTRACTION_SYSTEM = """You are PlayForge's Slot Filling agent. Extract game design information from the user conversation.

CRITICAL OUTPUT RULES:
- Return ONLY a valid JSON object. Nothing else.
- Do NOT include markdown code fences (```), comments, explanations, or any text before/after the JSON.
- Start your response with { and end with }.
- Use null for missing or uncertain values.

Required JSON format:
{
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
}

Field rules:
- game_type: one of: dodge, platformer, runner, shooter, puzzle, rhythm, tower_defense, sandbox, card, rpg, idle, racing
- core_mechanic: concise Chinese description of the primary gameplay loop
- theme: e.g. 太空, 海底, 森林, 西部, 未来
- input_method: one of: touch, tap, swipe, tilt
- win_condition: e.g. 存活60秒, 到达终点, 消灭所有敌人
- difficulty: one of: easy, medium, hard, progressive
- visual_style: one of: pixel, geometric, emoji, neon
- audio_style: one of: chiptune, ambient, none
- special_rules: array of strings, e.g. ["分裂机制"]
- reference_game: e.g. "Flappy Bird"

Keep existing non-null values unchanged unless the user explicitly corrects them."""

DIALOGUE_SYSTEM = """You are PlayForge's friendly game creation assistant. You help users describe their game idea in 2-4 conversational turns.

Current slot fill state: {slot_summary}
Missing required info: {missing_slots}

Your job:
- If state is "greeting": Welcome the user and invite them to describe their game idea
- If state is "describing": Acknowledge what they said, extract info, ask about the most important missing slot in a natural way (one question at a time)
- If state is "clarifying": Confirm what you understood, ask about remaining missing slots
- If state is "confirmed": Summarize the complete game design and ask for confirmation

Rules:
- Be concise, friendly, and enthusiastic
- Ask at most ONE clarifying question per turn
- Respond in the same language the user uses (Chinese or English)
- Never mention "slots" or "JSON" to the user
"""

SLOT_LABELS = {
    "game_type": "游戏类型",
    "core_mechanic": "核心玩法",
    "theme": "游戏主题",
    "input_method": "操作方式",
    "win_condition": "胜利条件",
    "difficulty": "难度设定",
    "visual_style": "视觉风格",
    "audio_style": "音效风格",
}


def _safe_parse_json(text: str) -> Optional[dict]:
    """Try to parse JSON from LLM output, handling common issues."""
    if not text:
        return None
    # Strip <think> tags if present
    text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to extract JSON object from markdown or surrounding text
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            # Try to fix truncated JSON by closing brackets
            fragment = m.group()
            for suffix in ['}', '"}', '"}}']:
                try:
                    return json.loads(fragment + suffix)
                except json.JSONDecodeError:
                    continue
    return None


class DialogueEngine:
    """Stage 01: Multi-turn dialogue engine with Slot Filling.

    Uses Claude for:
    1. Extracting structured slots from conversation (Slot Filling)
    2. Generating contextual responses that guide the user
    """

    def __init__(self) -> None:
        self._client = LLMClient()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create_session(self, session_id: str, user_id: str) -> DialogueSession:
        if session_id not in _sessions:
            _sessions[session_id] = DialogueSession(
                session_id=session_id,
                user_id=user_id,
                state=DialogueState.greeting,
            )
        return _sessions[session_id]

    async def process_message(self, req: ChatRequest) -> ChatResponse:
        """Process one user turn; return assistant reply + updated state."""
        session = self.get_or_create_session(req.session_id, req.user_id)

        # Append user message to history
        session.history.append(ConversationMessage(role="user", content=req.content))

        if settings.LLM_MODE == "mock":
            reply, updated_slots = self._mock_process(session)
        else:
            reply, updated_slots = await self._llm_process(session)

        # Append assistant reply
        session.history.append(ConversationMessage(role="assistant", content=reply))

        # Determine state transition
        fill_pct = session.slots.fill_pct()
        old_state = session.state
        session.state = self._next_state(session.state, fill_pct)

        ready = session.state == DialogueState.confirmed

        return ChatResponse(
            session_id=req.session_id,
            reply=reply,
            state=session.state,
            slots_updated=updated_slots,
            slot_fill_pct=fill_pct,
            ready_to_generate=ready,
        )

    async def slots_to_game_spec(self, session_id: str) -> GameSpec:
        """Stage 02: Convert confirmed SlotState → GameSpec."""
        session = _sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        return _build_game_spec(session.slots)

    async def parse_description_to_spec(self, description: str) -> GameSpec:
        """Single-shot parse: description → GameSpec (no conversation)."""
        if settings.LLM_MODE == "mock":
            return _mock_parse(description)

        try:
            text = await self._client.complete(
                model=self._client.model_for(fast=True),
                max_tokens=1024,
                system=get_prompt("prompt.slot_extraction_system", SLOT_EXTRACTION_SYSTEM),
                messages=[{"role": "user", "content": description}],
            )
            raw = text.strip()
            # Try to extract JSON object from response
            slot_data = _safe_parse_json(raw)
            if slot_data:
                slots = SlotState(**{k: v for k, v in slot_data.items() if v is not None})
                return _build_game_spec(slots)
            logger.warning("LLM slot extraction returned no valid JSON, using mock")
            return _mock_parse(description)
        except Exception as e:
            logger.warning(f"LLM slot extraction failed, using mock: {e}")
            return _mock_parse(description)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _llm_process(self, session: DialogueSession) -> Tuple[str, List[str]]:
        # Step 1: Extract slots
        old_slots = session.slots.model_copy()
        try:
            slot_text = await self._client.complete(
                model=self._client.model_for(fast=True),
                max_tokens=1024,
                system=get_prompt("prompt.slot_extraction_system", SLOT_EXTRACTION_SYSTEM),
                messages=_history_to_anthropic(session.history),
            )
            slot_data = _safe_parse_json(slot_text.strip())
            if not slot_data:
                raise ValueError("No valid JSON in slot extraction response")
            for key, val in slot_data.items():
                if val is not None and hasattr(session.slots, key):
                    setattr(session.slots, key, val)
        except Exception as e:
            logger.warning(f"Slot extraction error: {e}")

        updated = [k for k in SlotState.model_fields if
                   getattr(session.slots, k) != getattr(old_slots, k)]

        # Step 2: Generate reply
        fill_pct = session.slots.fill_pct()
        missing = session.slots.missing_required()
        slot_summary = _format_slot_summary(session.slots)
        system = get_prompt("prompt.dialogue_system", DIALOGUE_SYSTEM).format(
            slot_summary=slot_summary,
            missing_slots=", ".join(SLOT_LABELS.get(s, s) for s in missing) or "无",
        )
        try:
            reply = await self._client.complete(
                model=self._client.model_for(fast=True),
                max_tokens=2048,
                system=system,
                messages=_history_to_anthropic(session.history),
            )
            reply = reply.strip()
        except Exception as e:
            logger.warning(f"Reply generation error: {e}")
            reply = _fallback_reply(session.state, session.slots)

        return reply, updated

    def _mock_process(self, session: DialogueSession) -> Tuple[str, List[str]]:
        """Keyword-based mock for development/testing."""
        text = session.history[-1].content.lower()
        updated = []

        if "太空" in text or "space" in text:
            session.slots.game_type = "dodge"
            session.slots.theme = "太空"
            updated += ["game_type", "theme"]
        if "森林" in text:
            session.slots.theme = "森林"
            updated.append("theme")
        if "躲避" in text or "dodge" in text:
            session.slots.core_mechanic = "左右移动躲避下落物体"
            updated.append("core_mechanic")
        if "跑酷" in text or "runner" in text:
            session.slots.game_type = "runner"
            session.slots.core_mechanic = "自动奔跑，跳跃躲避障碍"
            updated += ["game_type", "core_mechanic"]
        if session.slots.input_method is None:
            session.slots.input_method = "touch"
            updated.append("input_method")
        if session.slots.difficulty is None:
            session.slots.difficulty = "progressive"
            updated.append("difficulty")
        if session.slots.win_condition is None and session.slots.game_type:
            defaults = GAME_TYPE_DEFAULTS.get(session.slots.game_type, {})
            session.slots.win_condition = defaults.get("win_condition", "存活尽量长时间")
            updated.append("win_condition")

        reply = _fallback_reply(session.state, session.slots)
        return reply, list(set(updated))

    @staticmethod
    def _next_state(current: DialogueState, fill_pct: float) -> DialogueState:
        if current == DialogueState.greeting:
            return DialogueState.describing
        if current == DialogueState.describing and fill_pct >= 0.6:
            return DialogueState.clarifying
        if current == DialogueState.clarifying and fill_pct >= 1.0:
            return DialogueState.confirmed
        return current


# ---------------------------------------------------------------------------
# GameSpec builder (Stage 02)
# ---------------------------------------------------------------------------

def _build_game_spec(slots: SlotState) -> GameSpec:
    game_type = slots.game_type or "dodge"
    defaults = GAME_TYPE_DEFAULTS.get(game_type, GAME_TYPE_DEFAULTS["dodge"])

    # Entities
    entity_defs = ENTITY_DEFAULTS.get(game_type, ENTITY_DEFAULTS["dodge"])
    entities = [GameEntity(**e) for e in entity_defs]

    # Core mechanics
    mechanics = [CoreMechanic(
        type=game_type,
        input=slots.input_method or defaults.get("input_method", "touch"),
        difficulty_scaling=slots.difficulty or "progressive",
    )]

    # Rules
    rules = GameRules(
        win_condition=slots.win_condition or defaults.get("win_condition", "存活尽量长时间"),
        lose_condition="lives_zero",
        scoring="collect_plus_time",
        lives=3,
    )

    # Visual
    art_style = slots.visual_style or "geometric"
    visual = VisualStyle(
        theme=slots.theme or "space",
        art_style=art_style,
        background="starfield" if "太空" in (slots.theme or "") else "gradient",
        effects=["glow"] if art_style == "neon" else [],
    )

    return GameSpec(
        game_type=game_type,
        core_mechanics=mechanics,
        entities=entities,
        rules=rules,
        visual_style=visual,
        audio_style=slots.audio_style or "none",
        difficulty_curve=slots.difficulty or "progressive",
        platform_constraints=PlatformConstraints(),
    )


def _mock_parse(description: str) -> GameSpec:
    """Keyword-based single-shot parse for mock mode."""
    desc = description.lower()
    game_type = "dodge"
    theme = "太空"
    for gt, kws in {
        "dodge": ["太空", "躲避", "飞船", "陨石"],
        "runner": ["跑酷", "奔跑", "runner"],
        "platformer": ["跳台", "平台", "platformer"],
        "shooter": ["射击", "shoot"],
        "puzzle": ["拼图", "解谜", "puzzle"],
        "rhythm": ["节奏", "音乐", "rhythm"],
    }.items():
        if any(k in desc for k in kws):
            game_type = gt
            break
    if "森林" in desc:
        theme = "森林"
    elif "海底" in desc:
        theme = "海底"
    slots = SlotState(
        game_type=game_type,
        theme=theme,
        input_method="touch",
        difficulty="progressive",
        core_mechanic=GAME_TYPE_DEFAULTS.get(game_type, {}).get("core_mechanic", ""),
        win_condition=GAME_TYPE_DEFAULTS.get(game_type, {}).get("win_condition", "存活尽量长时间"),
    )
    return _build_game_spec(slots)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _history_to_anthropic(history: List[ConversationMessage]) -> List[Dict]:
    return [{"role": m.role, "content": m.content} for m in history]


def _format_slot_summary(slots: SlotState) -> str:
    lines = []
    for field in ["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"]:
        val = getattr(slots, field)
        label = SLOT_LABELS.get(field, field)
        lines.append(f"  {label}: {val or '(未知)'}")
    return "\n".join(lines)


def _fallback_reply(state: DialogueState, slots: SlotState) -> str:
    missing = slots.missing_required()
    if state == DialogueState.greeting:
        return "你好！欢迎来到PlayForge！请告诉我你想做什么样的游戏？比如「太空躲避」、「跑酷游戏」或者「节奏音乐」之类的，你的创意是什么呢？"
    if not missing:
        gt = slots.game_type or "游戏"
        return (
            f"明白了！你想做一个「{slots.theme or ''}」主题的{gt}游戏，"
            f"玩法是{slots.core_mechanic or ''}，难度{slots.difficulty or 'progressive'}。"
            f"现在开始为你生成！"
        )
    next_q = {
        "game_type": "你想做哪种类型的游戏？比如躲避、跑酷、射击、解谜……",
        "theme": "游戏的主题或世界观是什么？比如太空、海底、森林、西部……",
        "core_mechanic": "游戏的核心玩法是什么？玩家主要做什么操作？",
        "input_method": "玩家怎么操作？点击、滑动还是其他方式？",
        "win_condition": "怎样才算赢？存活一定时间、到达终点还是消灭所有敌人？",
        "difficulty": "难度怎么设定？简单、中等、困难还是逐渐增加？",
    }
    q = next_q.get(missing[0], "还有什么特别的想法吗？")
    return f"好的！{q}"
