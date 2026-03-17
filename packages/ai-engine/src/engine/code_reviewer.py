"""Stage 06c: LLM Code Reviewer – semantic game quality assessment.

Calls the LLM to review the generated HTML5 game code and return a
structured JSON assessment:
  - is_complete_game: bool
  - has_real_gameplay: bool
  - difficulty_balanced: bool
  - fun_score: 1-10
  - issues: list[str]

Returns LLMReviewResult(ran=False) if LLM is unavailable or review fails.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from ..services.llm_client import LLMClient
from .quality_scorer import LLMReviewResult

logger = logging.getLogger(__name__)

REVIEW_SYSTEM = """You are an expert HTML5 mobile game QA reviewer.
Review the provided game code and assess its quality objectively.
Return ONLY valid JSON. No markdown fences, no extra text."""

REVIEW_PROMPT = """Review this HTML5 game code and return a JSON object with exactly these fields:

{{
  "is_complete_game": <bool>,        // true if it's a fully playable game, false if it's a stub/demo
  "has_real_gameplay": <bool>,       // true if it has genuine mechanics (collision, scoring, progression)
  "difficulty_balanced": <bool>,     // true if difficulty is neither impossibly hard nor trivially easy
  "fun_score": <number 1-10>,        // estimated fun factor: 1=boring, 5=average, 10=very engaging
  "issues": [<string>, ...]          // list specific problems (empty list if none)
}}

Game code to review (first 8000 chars):
{code_preview}"""


class CodeReviewer:
    """LLM-based semantic quality reviewer for HTML5 games."""

    def __init__(self) -> None:
        self._client = LLMClient()

    async def review(self, html_code: str) -> LLMReviewResult:
        """Run LLM review. Returns LLMReviewResult(ran=False) on any failure."""
        if not self._client.is_enabled():
            logger.debug("LLM not enabled – skipping code review")
            return LLMReviewResult(ran=False)

        # Truncate to avoid excessive token usage (~8000 chars ≈ 2000 tokens)
        code_preview = html_code[:8000]
        if len(html_code) > 8000:
            code_preview += "\n\n... [truncated for review]"

        prompt = REVIEW_PROMPT.format(code_preview=code_preview)

        try:
            raw = await self._client.complete(
                model=self._client.model_for(fast=True),   # use fast model for review
                max_tokens=4096,
                system=REVIEW_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._parse_review(raw)
        except Exception as exc:
            logger.warning(f"Code review LLM call failed: {exc}")
            return LLMReviewResult(ran=False)

    def _parse_review(self, raw: str) -> LLMReviewResult:
        """Parse LLM JSON output, with fallback for malformed responses."""
        # Strip markdown fences if present
        cleaned = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()

        # Use raw_decode from first { to safely handle surrounding text
        start = cleaned.find("{")
        data = None
        if start != -1:
            try:
                data, _ = json.JSONDecoder().raw_decode(cleaned, start)
            except json.JSONDecodeError:
                # Last resort: first { to last }
                end = cleaned.rfind("}")
                if end > start:
                    try:
                        data = json.loads(cleaned[start:end + 1])
                    except json.JSONDecodeError as exc:
                        logger.warning(f"JSON parse error in review: {exc} | raw: {raw[:200]}")

        if data is None:
            logger.warning(f"No JSON found in review response: {raw[:200]}")
            return LLMReviewResult(ran=False)

        fun_score = float(data.get("fun_score", 5.0))
        fun_score = max(1.0, min(10.0, fun_score))

        issues = data.get("issues", [])
        if not isinstance(issues, list):
            issues = [str(issues)] if issues else []

        result = LLMReviewResult(
            ran=True,
            is_complete_game=bool(data.get("is_complete_game", False)),
            has_real_gameplay=bool(data.get("has_real_gameplay", False)),
            difficulty_balanced=bool(data.get("difficulty_balanced", False)),
            fun_score=fun_score,
            issues=issues[:10],
        )
        logger.info(
            f"Code review: complete={result.is_complete_game}, "
            f"gameplay={result.has_real_gameplay}, fun={result.fun_score:.1f}, "
            f"issues={len(result.issues)}"
        )
        return result
