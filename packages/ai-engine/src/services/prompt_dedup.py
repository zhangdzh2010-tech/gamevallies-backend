"""Deep prompt/input deduplication before LLM admission."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

Message = dict[str, str]

_SHORT_WORD_STOPWORDS = frozenset({
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "have",
    "must",
    "should",
    "need",
    "keep",
    "make",
    "final",
    "html",
    "game",
    "code",
    "only",
    "then",
    "than",
    "when",
    "where",
    "what",
    "will",
    "would",
    "about",
    "after",
    "before",
    "using",
    "ensure",
    "avoid",
})
_SEMANTIC_ALLOWED_PAIRS = {
    frozenset({"contract", "task_memory"}),
    frozenset({"contract", "source_context"}),
    frozenset({"spec", "task_memory"}),
    frozenset({"spec", "source_context"}),
    frozenset({"feedback", "task_memory"}),
    frozenset({"feedback", "source_context"}),
    frozenset({"feedback", "spec"}),
    frozenset({"qa", "repair"}),
    frozenset({"task_memory", "source_context"}),
}


@dataclass
class PromptDedupMetrics:
    prompt_fingerprint: str
    applied: bool = False
    removed_block_count: int = 0
    removed_blocks: list[dict[str, Any]] = field(default_factory=list)
    exact_removals: int = 0
    structured_removals: int = 0
    semantic_removals: int = 0
    summary: list[str] = field(default_factory=list)


@dataclass
class PromptDedupResult:
    system: Optional[str]
    messages: list[Message]
    metrics: PromptDedupMetrics


@dataclass
class _PromptOwner:
    kind: str
    index: int
    role: Optional[str]
    original_text: str
    blocks: list["_PromptBlock"] = field(default_factory=list)

    def render(self) -> str:
        rendered = [block.render() for block in self.blocks]
        return "\n\n".join(part for part in rendered if part).strip()


@dataclass
class _PromptBlock:
    owner: _PromptOwner
    block_index: int
    heading: Optional[str]
    items: list["_PromptItem"] = field(default_factory=list)

    def render(self) -> str:
        kept_items = [item.text for item in self.items if item.keep]
        if not kept_items:
            return ""
        if self.heading:
            return "\n".join([self.heading, *kept_items]).strip()
        if len(kept_items) == 1:
            return kept_items[0].strip()
        return "\n".join(kept_items).strip()


@dataclass
class _PromptItem:
    block: _PromptBlock
    item_index: int
    text: str
    family: str
    exact_key: str
    field_key: Optional[str]
    field_value: Optional[str]
    semantic_tokens: tuple[str, ...]
    numeric_tokens: tuple[str, ...]
    priority: int
    section: Optional[str]
    keep: bool = True

    @property
    def owner(self) -> _PromptOwner:
        return self.block.owner


def build_prompt_fingerprint(system: Optional[str], messages: list[Message]) -> str:
    payload = {
        "system": str(system or "").strip(),
        "messages": [
            {
                "role": str(message.get("role") or "").strip(),
                "content": str(message.get("content") or "").strip(),
            }
            for message in messages
        ],
    }
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def deep_dedupe_prompt(
    *,
    system: Optional[str],
    messages: list[Message],
    compression_policy: str = "generic",
) -> PromptDedupResult:
    normalized_system = str(system or "").strip() or None
    normalized_messages = [
        {
            **dict(message),
            "role": str(message.get("role") or "").strip(),
            "content": str(message.get("content") or "").strip(),
        }
        for message in messages
    ]

    owners: list[_PromptOwner] = []
    if normalized_system:
        owners.append(
            _parse_owner(
                text=normalized_system,
                kind="system",
                index=-1,
                role="system",
                compression_policy=compression_policy,
                message_count=len(normalized_messages),
            )
        )
    message_owners: list[_PromptOwner] = []
    for index, message in enumerate(normalized_messages):
        owner = _parse_owner(
            text=str(message.get("content") or ""),
            kind="message",
            index=index,
            role=str(message.get("role") or "") or None,
            compression_policy=compression_policy,
            message_count=len(normalized_messages),
        )
        owners.append(owner)
        message_owners.append(owner)

    candidates = [
        item
        for owner in owners
        for block in owner.blocks
        for item in block.items
    ]
    keepers: list[_PromptItem] = []
    removed_blocks: list[dict[str, Any]] = []
    exact_removals = 0
    structured_removals = 0
    semantic_removals = 0

    ordered_candidates = sorted(
        candidates,
        key=lambda item: (
            item.priority,
            item.owner.index if item.owner.kind == "message" else -1,
            len(item.text),
            -item.block.block_index,
            -item.item_index,
        ),
        reverse=True,
    )

    for candidate in ordered_candidates:
        duplicate_reason, similarity = _find_duplicate(candidate, keepers)
        if duplicate_reason is None:
            keepers.append(candidate)
            continue

        candidate.keep = False
        if duplicate_reason == "exact_duplicate":
            exact_removals += 1
        elif duplicate_reason == "structured_duplicate":
            structured_removals += 1
        else:
            semantic_removals += 1
        if len(removed_blocks) < 12:
            removed_blocks.append({
                "source": "system" if candidate.owner.kind == "system" else f"message[{candidate.owner.index}]",
                "role": candidate.owner.role,
                "section": candidate.section,
                "reason": duplicate_reason,
                "chars": len(candidate.text),
                **({"similarity": similarity} if similarity is not None else {}),
            })

    _drop_orphan_task_memory_header(owners)
    deduped_system = next((owner.render() for owner in owners if owner.kind == "system"), None)
    deduped_messages: list[Message] = []
    for message, owner in zip(normalized_messages, message_owners):
        rendered = owner.render()
        if rendered:
            deduped_messages.append({
                **message,
                "content": rendered,
            })

    if not deduped_messages and normalized_messages:
        fallback = dict(normalized_messages[-1])
        fallback["content"] = str(normalized_messages[-1].get("content") or "").strip()
        if fallback["content"]:
            deduped_messages.append(fallback)

    prompt_fingerprint = build_prompt_fingerprint(deduped_system, deduped_messages)
    removed_block_count = exact_removals + structured_removals + semantic_removals
    summary: list[str] = []
    if exact_removals:
        summary.append(f"prompt_dedup_exact:{exact_removals}")
    if structured_removals:
        summary.append(f"prompt_dedup_structured:{structured_removals}")
    if semantic_removals:
        summary.append(f"prompt_dedup_semantic:{semantic_removals}")

    return PromptDedupResult(
        system=deduped_system,
        messages=deduped_messages,
        metrics=PromptDedupMetrics(
            prompt_fingerprint=prompt_fingerprint,
            applied=removed_block_count > 0,
            removed_block_count=removed_block_count,
            removed_blocks=removed_blocks,
            exact_removals=exact_removals,
            structured_removals=structured_removals,
            semantic_removals=semantic_removals,
            summary=summary,
        ),
    )


def _drop_orphan_task_memory_header(owners: list[_PromptOwner]) -> None:
    for owner in owners:
        if owner.kind != "system":
            continue
        kept_items = [
            item
            for block in owner.blocks
            for item in block.items
            if item.keep
        ]
        if len(kept_items) != 1:
            continue
        candidate = kept_items[0]
        if candidate.exact_key == "task memory (shared task context)":
            candidate.keep = False


def _parse_owner(
    *,
    text: str,
    kind: str,
    index: int,
    role: Optional[str],
    compression_policy: str,
    message_count: int,
) -> _PromptOwner:
    owner = _PromptOwner(
        kind=kind,
        index=index,
        role=role,
        original_text=text,
    )
    for block_index, block_text in enumerate(_split_top_level_blocks(text)):
        owner.blocks.append(
            _parse_block(
                owner=owner,
                block_index=block_index,
                block_text=block_text,
                compression_policy=compression_policy,
                message_count=message_count,
            )
        )
    return owner


def _parse_block(
    *,
    owner: _PromptOwner,
    block_index: int,
    block_text: str,
    compression_policy: str,
    message_count: int,
) -> _PromptBlock:
    lines = block_text.splitlines()
    section_heading: Optional[str] = None
    item_lines: Optional[list[str]] = None
    code_like = _looks_like_code(block_text)

    if not code_like and len(lines) >= 2:
        cursor = 0
        heading_lines: list[str] = []
        while cursor < len(lines) and _looks_like_heading(lines[cursor]):
            heading_lines.append(lines[cursor].strip())
            cursor += 1
            if cursor >= len(lines):
                break
        remaining = [line.rstrip() for line in lines[cursor:]]
        if remaining and all(_is_structured_line(line) for line in remaining):
            section_heading = "\n".join(heading_lines).strip() or None
            item_lines = remaining
        elif all(_is_structured_line(line) for line in lines):
            item_lines = [line.rstrip() for line in lines]

    block = _PromptBlock(
        owner=owner,
        block_index=block_index,
        heading=section_heading,
    )

    if item_lines is None:
        item_text = block_text.strip()
        family = _infer_family(
            text=item_text,
            section=section_heading,
            kind=owner.kind,
            role=owner.role,
        )
        block.items.append(
            _build_item(
                block=block,
                item_index=0,
                text=item_text,
                family=family,
                section=section_heading,
                compression_policy=compression_policy,
                message_count=message_count,
            )
        )
        return block

    for item_index, item_text in enumerate(item_lines):
        family = _infer_family(
            text=item_text,
            section=section_heading,
            kind=owner.kind,
            role=owner.role,
        )
        block.items.append(
            _build_item(
                block=block,
                item_index=item_index,
                text=item_text.strip(),
                family=family,
                section=section_heading,
                compression_policy=compression_policy,
                message_count=message_count,
            )
        )
    return block


def _build_item(
    *,
    block: _PromptBlock,
    item_index: int,
    text: str,
    family: str,
    section: Optional[str],
    compression_policy: str,
    message_count: int,
) -> _PromptItem:
    field_key, field_value = _parse_field_key_value(text)
    exact_key = _normalize_signature_text(text)
    semantic_tokens = _extract_semantic_tokens(text)
    numeric_tokens = tuple(re.findall(r"\d+(?:\.\d+)?", text or ""))
    priority = _compute_priority(
        owner=block.owner,
        text=text,
        family=family,
        section=section,
        compression_policy=compression_policy,
        message_count=message_count,
    )
    return _PromptItem(
        block=block,
        item_index=item_index,
        text=text,
        family=family,
        exact_key=exact_key,
        field_key=field_key,
        field_value=field_value,
        semantic_tokens=semantic_tokens,
        numeric_tokens=numeric_tokens,
        priority=priority,
        section=section,
    )


def _split_top_level_blocks(text: str) -> list[str]:
    if not text.strip():
        return []

    blocks: list[str] = []
    current: list[str] = []
    in_code_fence = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            current.append(line)
            in_code_fence = not in_code_fence
            continue
        if not in_code_fence and not stripped:
            if any(part.strip() for part in current):
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)

    if any(part.strip() for part in current):
        blocks.append("\n".join(current).strip())
    return blocks


def _looks_like_heading(line: str) -> bool:
    stripped = re.sub(r"^#+\s*", "", str(line or "").strip())
    if not stripped or len(stripped) > 96:
        return False
    if _is_structured_line(stripped):
        return False
    if stripped[-1:] in {":", ";", "?", "!", "。", "；", "："}:
        return False
    token_count = len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", stripped))
    return 0 < token_count <= 9


def _is_structured_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if re.match(r"^(?:[-*+]\s+|\d+\.\s+)", stripped):
        return True
    field_key, _field_value = _parse_field_key_value(stripped)
    return field_key is not None


def _parse_field_key_value(text: str) -> tuple[Optional[str], Optional[str]]:
    stripped = _strip_list_marker(text)
    match = re.match(
        r"^(?P<key>[\w\u4e00-\u9fff][\w\u4e00-\u9fff /()\-]{0,64})\s*[:=：]\s*(?P<value>.+)$",
        stripped,
    )
    if not match:
        return None, None
    key = re.sub(r"\s+", "_", match.group("key").strip().lower()).strip("_")
    value = _normalize_signature_text(match.group("value"))
    if not key or not value:
        return None, None
    return key, value


def _strip_list_marker(text: str) -> str:
    return re.sub(r"^\s*(?:[-*+]\s+|\d+\.\s+)", "", str(text or "")).strip()


def _normalize_signature_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = re.sub(r"^#+\s*", "", normalized)
    normalized = _strip_list_marker(normalized)
    normalized = normalized.replace("：", ":")
    normalized = re.sub(r"`+", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _extract_semantic_tokens(text: str) -> tuple[str, ...]:
    normalized = _normalize_signature_text(text)
    if not normalized:
        return ()

    raw_tokens = re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", normalized)
    filtered = {
        token
        for token in raw_tokens
        if token not in _SHORT_WORD_STOPWORDS
    }
    return tuple(sorted(filtered))


def _infer_family(
    *,
    text: str,
    section: Optional[str],
    kind: str,
    role: Optional[str],
) -> str:
    combined = "\n".join(part for part in [section, text] if part).lower()
    if _looks_like_code(combined):
        return "code"
    if any(keyword in combined for keyword in ("runtime contract", "contract summary", "required_states", "gestures", "orientation", "primary_goal", "mobile layout")):
        return "contract"
    if any(keyword in combined for keyword in ("spec summary", "source_spec", "structured design", "critical intent", "game_type", "intent:")):
        return "spec"
    if any(keyword in combined for keyword in ("feedback", "requested_change", "change request", "iteration intent", "latest user request")):
        return "feedback"
    if "task memory" in combined:
        return "task_memory"
    if any(keyword in combined for keyword in ("source context", "source_title", "raw_user_input", "current_code", "source_bundle")):
        return "source_context"
    if any(keyword in combined for keyword in ("qa finding", "qa findings", "issue list", "blocking", "repair_hint", "error")):
        return "qa"
    if any(keyword in combined for keyword in ("repair history", "repair", "remediation", "patch")):
        return "repair"
    if kind == "system":
        return "prompt_bundle"
    if role == "assistant":
        return "history"
    return "generic"


def _compute_priority(
    *,
    owner: _PromptOwner,
    text: str,
    family: str,
    section: Optional[str],
    compression_policy: str,
    message_count: int,
) -> int:
    priority = 0
    if owner.kind == "system":
        priority += 35
    else:
        priority += 10 + max(0, owner.index) * 2
        if owner.role == "user":
            priority += 35
            if owner.index == max(0, message_count - 1):
                priority += 15
        elif owner.role == "assistant":
            priority += 5

    priority += {
        "contract": 35,
        "spec": 30,
        "feedback": 25,
        "qa": 22,
        "repair": 20,
        "code": 18,
        "source_context": 14,
        "task_memory": 10,
        "prompt_bundle": 6,
        "history": 2,
        "generic": 0,
    }.get(family, 0)

    policy = (compression_policy or "generic").strip().lower()
    if family == "code" and policy in {"iteration_rewrite", "qa_fix"}:
        priority += 12
    if family == "feedback" and policy == "iteration_rewrite":
        priority += 10
    if family in {"qa", "repair"} and policy == "qa_fix":
        priority += 10
    if family == "spec" and policy == "code_generation":
        priority += 6

    if section and _normalize_signature_text(section) == _normalize_signature_text(text):
        priority -= 12
    if len(_extract_semantic_tokens(text)) <= 1 and _parse_field_key_value(text)[0] is None:
        priority -= 10
    return priority


def _find_duplicate(candidate: _PromptItem, keepers: list[_PromptItem]) -> tuple[Optional[str], Optional[float]]:
    for keeper in keepers:
        if candidate.exact_key and candidate.exact_key == keeper.exact_key:
            return "exact_duplicate", None

        if candidate.field_key and keeper.field_key and candidate.field_key == keeper.field_key:
            if candidate.field_value == keeper.field_value:
                return "structured_duplicate", None
            similarity = _semantic_similarity(candidate, keeper)
            if similarity is not None:
                return "semantic_duplicate", similarity
            continue

        similarity = _semantic_similarity(candidate, keeper)
        if similarity is not None:
            return "semantic_duplicate", similarity

    return None, None


def _semantic_similarity(candidate: _PromptItem, keeper: _PromptItem) -> Optional[float]:
    if candidate.family == "code" or keeper.family == "code":
        return None
    if candidate.field_key and keeper.field_key and candidate.field_key != keeper.field_key:
        return None
    if not _families_allow_semantic_dedup(candidate.family, keeper.family):
        return None
    if not candidate.semantic_tokens or not keeper.semantic_tokens:
        return None
    if min(len(candidate.semantic_tokens), len(keeper.semantic_tokens)) < 3:
        return None
    if (candidate.numeric_tokens or keeper.numeric_tokens) and candidate.numeric_tokens != keeper.numeric_tokens:
        return None

    candidate_set = set(candidate.semantic_tokens)
    keeper_set = set(keeper.semantic_tokens)
    intersection = candidate_set & keeper_set
    if not intersection:
        return None

    shorter = min(len(candidate_set), len(keeper_set))
    coverage = len(intersection) / shorter
    jaccard = len(intersection) / len(candidate_set | keeper_set)
    contains_match = (
        candidate.exact_key in keeper.exact_key
        or keeper.exact_key in candidate.exact_key
    ) and min(len(candidate.exact_key), len(keeper.exact_key)) >= 32

    if candidate.field_key and keeper.field_key and candidate.field_key == keeper.field_key:
        if coverage >= 0.84 and (jaccard >= 0.72 or contains_match):
            return round(max(coverage, jaccard), 3)
        return None

    if coverage >= 0.92 and (jaccard >= 0.82 or contains_match):
        return round(max(coverage, jaccard), 3)
    return None


def _families_allow_semantic_dedup(left: str, right: str) -> bool:
    if left == right:
        return True
    return frozenset({left, right}) in _SEMANTIC_ALLOWED_PAIRS


def _looks_like_code(text: str) -> bool:
    lower = str(text or "").lower()
    return (
        "```" in lower
        or "<!doctype html" in lower
        or "<html" in lower
        or "<script" in lower
        or "function " in lower
        or "const " in lower
        or "let " in lower
        or "class " in lower
    )
