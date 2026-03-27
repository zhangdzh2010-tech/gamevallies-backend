from __future__ import annotations

import re


class SafePromptFormatDict(dict):
    """Preserve unknown placeholders instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def safe_format_prompt(template: str, **values: object) -> str:
    source = str(template or "")
    placeholders: dict[str, str] = {}
    for index, key in enumerate(values.keys()):
        sentinel = f"__PLAYFORGE_PROMPT_FIELD_{index}__"
        source = source.replace("{" + key + "}", sentinel)
        placeholders[sentinel] = "{" + key + "}"

    source = source.replace("{", "{{").replace("}", "}}")
    for sentinel, placeholder in placeholders.items():
        source = source.replace(sentinel, placeholder)

    source = re.sub(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})", r"{\1}", source)
    return source.format_map(SafePromptFormatDict(values))
