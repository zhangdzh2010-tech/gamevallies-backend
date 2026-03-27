from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set

from ..api.models import GameRuntimeContract

DEFAULT_TERMINAL_STATE_ALIASES = [
    "game_over",
    "gameover",
    "over",
    "ended",
    "lost",
    "failed",
    "dead",
]

DEFAULT_COMPLETION_STATE_ALIASES = [
    "win",
    "won",
    "victory",
    "complete",
    "completed",
    "level_complete",
    "clear",
    "cleared",
    "success",
    "succeeded",
    "solved",
]

STATE_GROUP_ALIASES: Dict[str, List[str]] = {
    "boot": ["boot", "init", "initialize", "loading"],
    "ready": ["ready", "menu", "start", "idle"],
    "playing": ["playing", "play", "running", "active"],
    "game_over": list(DEFAULT_TERMINAL_STATE_ALIASES),
    "level_complete": list(DEFAULT_COMPLETION_STATE_ALIASES),
}

STATE_GROUP_EQUIVALENCE: Dict[str, str] = {
    "boot": "startup",
    "ready": "startup",
    "playing": "playing",
    "game_over": "terminal",
    "level_complete": "terminal",
}

STATE_VARIABLE_RE = re.compile(
    r"(?:[A-Za-z_$][\w$]*\.)?(state|gameState|currentState|status|gameStatus|phase|mode)\s*=\s*(?:[A-Za-z_$][\w$]*\.)*([A-Za-z_$][\w$]*)\b",
    re.IGNORECASE,
)
STATE_TRANSITION_FN_RE = re.compile(
    r"(setState|changeState|transitionTo|enterState)\s*\(\s*(?:[A-Za-z_$][\w$]*\.)*([A-Za-z_$][\w$]*)\b",
    re.IGNORECASE,
)
STATE_LITERAL_ASSIGN_RE = re.compile(
    r"(?:[A-Za-z_$][\w$]*\.)?(state|gameState|currentState|status|gameStatus|phase|mode)\s*=\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
STATE_LITERAL_TRANSITION_RE = re.compile(
    r"(setState|changeState|transitionTo|enterState)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
STATE_CONSTANT_DECL_RE = re.compile(
    r"(?:const|let|var)\s+([^;]+);",
    re.IGNORECASE,
)
STATE_CONSTANT_PAIR_RE = re.compile(
    r"([A-Za-z_$][\w$]*)\s*=\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)


def get_terminal_state_aliases(runtime_contract: Optional[GameRuntimeContract]) -> List[str]:
    aliases = []
    if runtime_contract:
        aliases.extend(list(runtime_contract.gameplay.terminal_state_aliases or []))
    if not aliases:
        aliases.extend(DEFAULT_TERMINAL_STATE_ALIASES)
    aliases.extend(DEFAULT_COMPLETION_STATE_ALIASES)
    return _dedupe_aliases(aliases)


def has_terminal_state_transition(
    code: str,
    runtime_contract: Optional[GameRuntimeContract] = None,
) -> bool:
    source = code or ""
    aliases = get_terminal_state_aliases(runtime_contract)
    alias_patterns = [_build_alias_pattern(alias) for alias in aliases]
    if not alias_patterns:
        return False

    identifier_pattern = _build_identifier_group(alias_patterns)
    state_value_pattern = "|".join(alias_patterns)
    enum_value_pattern = "|".join(_build_enum_alias_pattern(alias) for alias in aliases)

    if identifier_pattern and re.search(
        rf"(?:^|[^\w$])(?:{identifier_pattern})\s*=\s*true\b",
        source,
        re.IGNORECASE,
    ):
        return True

    if re.search(
        rf"(?:[A-Za-z_$][\w$]*\.)?(state|gameState|currentState|status|gameStatus|phase|mode)\s*=\s*['\"]?(?:{state_value_pattern})['\"]?",
        source,
        re.IGNORECASE,
    ):
        return True

    if re.search(
        rf"(setState|changeState|transitionTo|enterState)\s*\(\s*['\"]?(?:{state_value_pattern})['\"]?",
        source,
        re.IGNORECASE,
    ):
        return True

    if enum_value_pattern and re.search(
        rf"(?:[A-Za-z_$][\w$]*\.)?(state|gameState|currentState|status|gameStatus|phase|mode)\s*=\s*(?:[A-Za-z_$][\w$]*\.)*(?:{enum_value_pattern})\b",
        source,
        re.IGNORECASE,
    ):
        return True

    constant_alias_map = _extract_state_constant_aliases(source)
    if _has_state_identifier_assignment(source, constant_alias_map, set(_dedupe_aliases(aliases))):
        return True

    return False


def has_required_state_presence(
    code: str,
    required_state: str,
    runtime_contract: Optional[GameRuntimeContract] = None,
) -> bool:
    source = code or ""
    normalized_required_state = _normalize_alias(required_state)
    if not normalized_required_state:
        return False

    equivalent_group = STATE_GROUP_EQUIVALENCE.get(normalized_required_state)
    aliases = _aliases_for_required_state(normalized_required_state, runtime_contract)
    alias_set = set(_dedupe_aliases(aliases))
    if not alias_set:
        return False

    if _has_state_literal_assignment(source, alias_set):
        return True

    constant_alias_map = _extract_state_constant_aliases(source)
    if _has_state_identifier_assignment(source, constant_alias_map, alias_set):
        return True

    if equivalent_group == "terminal":
        return has_terminal_state_transition(source, runtime_contract)

    return any(_build_alias_search(alias).search(source) for alias in alias_set)


def _dedupe_aliases(values: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for value in values:
        lowered = _normalize_alias(value)
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(lowered)
    return normalized


def _normalize_alias(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return lowered


def _split_alias(value: str) -> List[str]:
    normalized = _normalize_alias(value)
    return [part for part in normalized.split("_") if part]


def _build_alias_pattern(value: str) -> str:
    parts = _split_alias(value)
    if not parts:
        return ""
    separated = r"[\s._-]?".join(re.escape(part) for part in parts)
    compact = re.escape("".join(parts))
    camel = re.escape(parts[0] + "".join(part.title() for part in parts[1:]))
    variants = [separated, compact, camel]
    return "(?:" + "|".join(dict.fromkeys(variants)) + ")"


def _build_identifier_group(alias_patterns: Iterable[str]) -> str:
    forms: List[str] = []
    seen = set()
    for alias in alias_patterns:
        if not alias:
            continue
        raw = alias.replace(r"[\s._-]?", "_")
        parts = [part for part in raw.split("_") if part]
        if not parts:
            continue
        snake = "_".join(parts)
        compact = "".join(parts)
        camel = parts[0] + "".join(part.title() for part in parts[1:])
        pascal = "".join(part.title() for part in parts)
        for value in (snake, compact, camel, pascal):
            if value and value not in seen:
                seen.add(value)
                forms.append(re.escape(value))
    return "|".join(forms)


def _build_enum_alias_pattern(value: str) -> str:
    parts = _split_alias(value)
    if not parts:
        return ""
    return re.escape("_".join(parts).upper())


def _aliases_for_required_state(
    required_state: str,
    runtime_contract: Optional[GameRuntimeContract],
) -> List[str]:
    normalized_required_state = _normalize_alias(required_state)
    equivalent_group = STATE_GROUP_EQUIVALENCE.get(normalized_required_state)
    if equivalent_group == "startup":
        aliases = STATE_GROUP_ALIASES["boot"] + STATE_GROUP_ALIASES["ready"]
    elif equivalent_group == "terminal":
        aliases = get_terminal_state_aliases(runtime_contract)
    else:
        aliases = STATE_GROUP_ALIASES.get(normalized_required_state, [normalized_required_state])
    return _dedupe_aliases(aliases)


def _extract_state_constant_aliases(source: str) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for declaration in STATE_CONSTANT_DECL_RE.finditer(source):
        for match in STATE_CONSTANT_PAIR_RE.finditer(declaration.group(1)):
            identifier = match.group(1)
            value = _normalize_alias(match.group(2))
            if not identifier or not value:
                continue
            aliases[identifier] = value
    return aliases


def _has_state_literal_assignment(source: str, alias_set: Set[str]) -> bool:
    for match in STATE_LITERAL_ASSIGN_RE.finditer(source):
        if _normalize_alias(match.group(2)) in alias_set:
            return True
    for match in STATE_LITERAL_TRANSITION_RE.finditer(source):
        if _normalize_alias(match.group(2)) in alias_set:
            return True
    return False


def _has_state_identifier_assignment(
    source: str,
    constant_alias_map: Dict[str, str],
    alias_set: Set[str],
) -> bool:
    for pattern in (STATE_VARIABLE_RE, STATE_TRANSITION_FN_RE):
        for match in pattern.finditer(source):
            identifier = match.group(2)
            normalized_alias = constant_alias_map.get(identifier, "")
            if normalized_alias in alias_set:
                return True
    return False


def _build_alias_search(alias: str) -> re.Pattern[str]:
    alias_pattern = _build_alias_pattern(alias)
    return re.compile(rf"(?<![\w$])['\"]?(?:{alias_pattern})['\"]?(?![\w$])", re.IGNORECASE)
