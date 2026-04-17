"""PR-07: CreativeAnchors — merged expand_prompt + intent structured output.

This module introduces a single structured schema that fuses what were
previously two LLM roundtrips (prompt expansion + intent parsing) into one.
The schema is *additive*: it does not replace the existing `expand_prompt`
endpoint. It is only consumed when the feature flag
`settings.P1_CREATIVE_ANCHORS_ENABLED` is True or when a caller explicitly
opts into `/v2/creative-anchors`.

Design notes
------------
* We keep `expanded_prompt` as a first-class field so downstream intent/
  designer code can fall back to string-level behavior unchanged.
* `pace_axis` and `style_axis` are enumerations — not free-form strings —
  so the DiversityPlanner (PR-09) can key off them deterministically.
* `entity_pool_hints` feeds PR-08's `sample_entities` and is advisory only:
  the deterministic seed still governs the actual selection.
* `mood` is a short, deduplicated list to keep prompt tokens bounded.
"""

from __future__ import annotations

import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


PaceAxis = Literal["slow", "steady", "fast", "chaotic"]
StyleAxis = Literal["pixel", "painterly", "neon", "minimal", "cartoon"]


_MOOD_MAX = 6
_HINT_MAX = 12
_GENRE_MAX_LEN = 48
_EXPANDED_MIN_LEN = 40
_EXPANDED_MAX_LEN = 4000


class CreativeAnchors(BaseModel):
    """Structured creative brief produced by the merged expand+intent call.

    Fields are stable; downstream code should treat unknown future fields as
    advisory and fall back to `expanded_prompt` for free-form context.
    """

    genre: str = Field(..., description="Short genre tag, e.g. 'arcade runner'")
    pace_axis: PaceAxis = Field("steady", description="High-level pacing bucket")
    mood: List[str] = Field(
        default_factory=list, description="Short adjectives, bounded list"
    )
    style_axis: StyleAxis = Field(
        "minimal", description="Visual style bucket consumed by DiversityPlanner"
    )
    entity_pool_hints: List[str] = Field(
        default_factory=list, description="Names / themes to bias entity sampling"
    )
    expanded_prompt: str = Field(..., description="Human-readable expanded brief")

    # --- validators ---------------------------------------------------

    @field_validator("genre")
    @classmethod
    def _genre_bounded(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("genre must be non-empty")
        if len(v) > _GENRE_MAX_LEN:
            v = v[:_GENRE_MAX_LEN].rstrip()
        return v

    @field_validator("mood")
    @classmethod
    def _mood_bounded(cls, v: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in v or []:
            s = (item or "").strip().lower()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if len(out) >= _MOOD_MAX:
                break
        return out

    @field_validator("entity_pool_hints")
    @classmethod
    def _hints_bounded(cls, v: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in v or []:
            s = (item or "").strip()
            key = s.lower()
            if not s or key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= _HINT_MAX:
                break
        return out

    @field_validator("expanded_prompt")
    @classmethod
    def _expanded_sane(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) < _EXPANDED_MIN_LEN:
            raise ValueError(
                f"expanded_prompt must be at least {_EXPANDED_MIN_LEN} chars"
            )
        if len(v) > _EXPANDED_MAX_LEN:
            v = v[:_EXPANDED_MAX_LEN]
        return v


# ---------------------------------------------------------------------
# Fallback builder — used when the merged LLM call fails.
# Kept cheap and deterministic; never raises.
# ---------------------------------------------------------------------


_PACE_KEYWORDS = {
    "chaotic": ("chaos", "crazy", "疯狂", "混乱", "chaotic"),
    "fast": ("fast", "快", "急", "race", "racing", "sprint"),
    "slow": ("slow", "relax", "放松", "休闲", "calm", "zen"),
}

_STYLE_KEYWORDS = {
    "pixel": ("pixel", "像素", "8-bit", "retro"),
    "neon": ("neon", "cyberpunk", "霓虹", "synthwave"),
    "painterly": ("painterly", "watercolor", "painted", "手绘"),
    "cartoon": ("cartoon", "卡通", "comic"),
}


def build_anchors_fallback(
    description: str,
    *,
    expanded_prompt: Optional[str] = None,
    genre: Optional[str] = None,
) -> CreativeAnchors:
    """Deterministic fallback; never raises on ordinary input."""

    text = (description or "").strip()
    lowered = text.lower()

    pace: PaceAxis = "steady"
    for axis, kws in _PACE_KEYWORDS.items():
        if any(kw in lowered for kw in kws):
            pace = axis  # type: ignore[assignment]
            break

    style: StyleAxis = "minimal"
    for axis, kws in _STYLE_KEYWORDS.items():
        if any(kw in lowered for kw in kws):
            style = axis  # type: ignore[assignment]
            break

    expanded = (expanded_prompt or "").strip()
    if len(expanded) < _EXPANDED_MIN_LEN:
        # Synthesize a minimal-but-valid expansion.
        expanded = (
            f"A concise arcade-style game based on this idea: {text[:200]}. "
            "It should be easy to learn, have clear win/lose states, and "
            "work on mobile touch and desktop mouse input."
        )

    resolved_genre = (genre or "").strip() or _guess_genre(text)

    return CreativeAnchors(
        genre=resolved_genre,
        pace_axis=pace,
        mood=_guess_mood(text),
        style_axis=style,
        entity_pool_hints=_guess_hints(text),
        expanded_prompt=expanded,
    )


def _guess_genre(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ("puzzle", "拼图", "match")):
        return "puzzle"
    if any(k in lowered for k in ("race", "runner", "dash", "sprint")):
        return "arcade runner"
    if any(k in lowered for k in ("quiz", "trivia", "学习", "教育")):
        return "educational"
    if any(k in lowered for k in ("funny", "meme", "搞笑")):
        return "funny arcade"
    return "arcade"


def _guess_mood(text: str) -> List[str]:
    lowered = text.lower()
    moods: List[str] = []
    if any(k in lowered for k in ("relax", "calm", "休闲", "zen")):
        moods.append("relaxing")
    if any(k in lowered for k in ("intense", "hard", "难")):
        moods.append("intense")
    if any(k in lowered for k in ("funny", "silly", "搞笑", "幽默")):
        moods.append("funny")
    if any(k in lowered for k in ("cute", "kawaii", "可爱")):
        moods.append("cute")
    if not moods:
        moods = ["accessible", "colorful"]
    return moods[:_MOOD_MAX]


_HINT_TOKEN_RE = re.compile(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{1,6}")


def _guess_hints(text: str) -> List[str]:
    """Extract up to _HINT_MAX distinct content tokens from the description."""
    tokens = _HINT_TOKEN_RE.findall(text or "")
    seen = set()
    hints: List[str] = []
    for t in tokens:
        norm = t.lower()
        if norm in seen:
            continue
        # skip trivial stopwords
        if norm in {
            "the", "and", "with", "for", "but", "that", "this",
            "game", "play", "player", "have", "you", "your",
            "一个", "游戏", "玩家",
        }:
            continue
        seen.add(norm)
        hints.append(t)
        if len(hints) >= _HINT_MAX:
            break
    return hints
