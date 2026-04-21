"""User-facing sanitizer for the expand-prompt endpoint output.

Why this exists
---------------
`/api/v1/ai/expand-prompt` returns a short brief that the customer reads and
edits in the creation workspace. Historically the LLM (and an old
deterministic fallback) sometimes leaked engineering scaffolding into that
brief — things like ``原始想法：…`` prefixes, the meta-instruction
``请把这条想法整理成…``, or label-prefixed lines such as ``Game Type:`` or
``核心玩法：``. None of that should ever appear on the C-end UI.

The sanitizer in this module is a hard, defensive boundary: every code path
that returns text to the caller of `/expand-prompt` runs the LLM output (and
the deterministic fallback) through :func:`sanitize_user_facing_brief` so the
caller can rely on the response being free of scaffolding regardless of what
the upstream model produced.

Keep the patterns in this module in lockstep with the TypeScript helper at
``packages/game-service/src/common/sanitize-idea.ts`` and the frontend helper
at ``frontend/src/utils/sanitizeIdea.js``.
"""

from __future__ import annotations

import re
from typing import Iterable

# Lines whose ENTIRE purpose is to echo back the user's idea or restate the
# task to the model. They must never appear in the user-visible brief.
_PREFIX_DROP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*原始想法\s*[:：].*$"),
    re.compile(r"^\s*用户想法\s*[:：].*$"),
    re.compile(r"^\s*用户输入\s*[:：].*$"),
    re.compile(r"^\s*Original\s+Idea\s*[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*User('s)?\s+Idea\s*[:：].*$", re.IGNORECASE),
    re.compile(r"^\s*User\s+Input\s*[:：].*$", re.IGNORECASE),
)

# Meta-instruction lines: text that asks the model to do its job. The fact
# that we ever see these in the response means the model echoed the user
# template back — strip them.
_META_INSTRUCTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*请把这条想法整理成.*$"),
    re.compile(r"^\s*请将这条想法整理成.*$"),
    re.compile(r"^\s*请把以?下?想法整理成.*$"),
    re.compile(r"^\s*请将以下想法整理成.*$"),
    re.compile(r"^\s*请帮我把.{0,40}整理成.*$"),
    re.compile(r"^\s*至少(要)?覆盖这些要素.*$"),
    re.compile(r"^\s*请覆盖以下要素.*$"),
    re.compile(
        r"^\s*Please\s+turn\s+this\s+brief\s+into\s+a\s+mobile-friendly.*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*Please\s+expand\s+the\s+(user\s+)?(idea|brief)\s+into.*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*covers?\s+at\s+least\s+these\s+elements.*$", re.IGNORECASE
    ),
)

# Label prefixes that are the spec template the engineer hands to the LLM.
# When a line starts with one of these labels it is structural scaffolding,
# not prose the user should read.
_LABEL_PREFIXES: tuple[str, ...] = (
    # English spec labels (legacy expand-prompt template).
    "game type",
    "core mechanic",
    "theme",
    "input method",
    "win condition",
    "difficulty ramp",
    "scoring / rewards",
    "scoring/rewards",
    "scoring",
    "rewards",
    "visual direction",
    "special rules or reference inspiration",
    "special rules",
    "reference inspiration",
    # Chinese variants observed in the wild.
    "游戏类型",
    "核心玩法",
    "核心机制",
    "主题",
    "操作方式",
    "操作方法",
    "输入方式",
    "胜利条件",
    "通关条件",
    "难度节奏",
    "难度曲线",
    "积分",
    "奖励",
    "积分 / 奖励",
    "积分/奖励",
    "视觉方向",
    "视觉风格",
    "视觉",
    "特殊规则",
    "特殊规则或参考灵感",
    "参考灵感",
    "参考游戏",
)


def _line_starts_with_label(line: str) -> bool:
    """Return True when `line` looks like ``<label>:`` or ``<label>：``."""

    stripped = line.strip()
    if not stripped:
        return False
    # Find the first colon (ASCII or Chinese fullwidth).
    colon_pos = -1
    for index, char in enumerate(stripped):
        if char == ":" or char == "\uff1a":
            colon_pos = index
            break
    if colon_pos <= 0 or colon_pos > 40:
        # Either no colon, or the colon is so far from the start that this
        # is normal prose with a colon mid-sentence (e.g. a dialog line).
        return False
    head = stripped[:colon_pos].strip().lower()
    if not head:
        return False
    for prefix in _LABEL_PREFIXES:
        if head == prefix.lower():
            return True
    return False


def _matches_any(line: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.match(line) for pattern in patterns)


def strip_user_facing_scaffolding(text: str) -> str:
    """Remove engineering scaffolding from a user-facing expand-prompt brief.

    The result is plain prose, suitable for direct display in the creation
    workspace. The function is idempotent and safe to call on text that is
    already clean.
    """

    if text is None:
        return ""
    raw = str(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines: list[str] = []
    for raw_line in raw.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            cleaned_lines.append("")
            continue
        if _matches_any(line, _PREFIX_DROP_PATTERNS):
            continue
        if _matches_any(line, _META_INSTRUCTION_PATTERNS):
            continue
        if _line_starts_with_label(line):
            continue
        cleaned_lines.append(line)

    # Collapse runs of blank lines and trim.
    collapsed: list[str] = []
    blank_run = False
    for line in cleaned_lines:
        if not line.strip():
            if blank_run:
                continue
            blank_run = True
            collapsed.append("")
            continue
        blank_run = False
        collapsed.append(line)
    while collapsed and not collapsed[0].strip():
        collapsed.pop(0)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()
    return "\n".join(collapsed)


def has_meaningful_brief(text: str, *, min_chars: int = 40) -> bool:
    """Heuristic: does `text` contain enough prose to show to a user?

    The threshold is intentionally low — we only want to detect the case
    where sanitization wiped out everything because the model returned
    nothing but scaffolding.
    """

    if text is None:
        return False
    stripped = str(text).strip()
    return len(stripped) >= min_chars


__all__ = [
    "strip_user_facing_scaffolding",
    "has_meaningful_brief",
]
