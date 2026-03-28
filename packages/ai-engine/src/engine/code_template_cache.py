"""Code Template Cache – caches QA-passing code skeletons by game_type:profile.

Provides structural references for the LLM to reduce first-attempt failure
rate. Skeletons retain HTML structure, canvas setup, game loop, and state
machine but strip game-specific content (colors, text, entity details).
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional

from ..api.models import GameSpec

logger = logging.getLogger(__name__)

# Maximum skeleton length injected into prompt (chars)
_MAX_SKELETON_CHARS = 3000


class CodeTemplateCache:
    """In-memory cache of successful game code skeletons."""

    def __init__(self) -> None:
        self._cache: Dict[str, str] = {}

    def get_skeleton(self, spec: GameSpec, runtime_profile: str) -> Optional[str]:
        """Return cached skeleton for the given gameplay fingerprint, or None."""
        key = self._key(spec, runtime_profile)
        skeleton = self._cache.get(key)
        if skeleton:
            logger.debug("Skeleton cache hit for %s", key)
        return skeleton

    def store(self, spec: GameSpec, runtime_profile: str, code: str) -> None:
        """Extract and cache a skeleton from QA-passing code."""
        key = self._key(spec, runtime_profile)
        skeleton = self._extract_skeleton(code)
        if skeleton and len(skeleton) > 200:
            self._cache[key] = skeleton[:_MAX_SKELETON_CHARS]
            logger.info("Cached skeleton for %s (%d chars)", key, len(skeleton))

    @staticmethod
    def _key(spec: GameSpec, runtime_profile: str) -> str:
        theme = (spec.visual_style.theme or "arcade").lower()
        input_mode = (
            spec.platform_constraints.input_mode
            or (spec.core_mechanics[0].input if spec.core_mechanics else "")
            or "touch"
        ).lower()
        goal_bucket = CodeTemplateCache._goal_bucket(spec.rules.win_condition)
        return ":".join([
            (spec.game_type or "unknown").lower(),
            (runtime_profile or "default").lower(),
            theme,
            input_mode,
            goal_bucket,
        ])

    @staticmethod
    def _goal_bucket(win_condition: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", (win_condition or "").lower()).strip()
        keyword_buckets = (
            ("survive", "survive"),
            ("score", "score"),
            ("collect", "collect"),
            ("rescue", "rescue"),
            ("deliver", "deliver"),
            ("clear", "clear"),
            ("solve", "solve"),
            ("defend", "defend"),
            ("defeat", "defeat"),
            ("reach", "reach"),
        )
        for keyword, bucket in keyword_buckets:
            if keyword in normalized:
                return bucket
        return normalized[:24] or "generic"

    @staticmethod
    def _extract_skeleton(code: str) -> str:
        """Strip game-specific content, keep structural skeleton.

        Preserves:
        - DOCTYPE, html/head/body/canvas structure
        - Canvas context setup
        - Game loop (requestAnimationFrame)
        - State machine transitions
        - Input handler registration pattern
        - Score/lives variable declarations

        Strips:
        - Inline color values → placeholder
        - String literals (UI text) → placeholder
        - Numeric constants in draw calls → placeholder
        - Entity-specific variable names kept but content simplified
        """
        skeleton = code

        # Replace color hex values with placeholder
        skeleton = re.sub(r"#[0-9a-fA-F]{3,8}\b", "#PALETTE", skeleton)

        # Replace rgb/rgba values
        skeleton = re.sub(
            r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\)",
            "rgb(PALETTE)",
            skeleton,
        )

        # Replace quoted strings longer than 3 chars (UI text) but keep short ones
        skeleton = re.sub(r'"[^"]{4,}"', '"__TEXT__"', skeleton)
        skeleton = re.sub(r"'[^']{4,}'", "'__TEXT__'", skeleton)

        # Collapse consecutive blank lines
        skeleton = re.sub(r"\n{3,}", "\n\n", skeleton)

        return skeleton.strip()
