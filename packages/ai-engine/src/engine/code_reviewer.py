"""Stage 06c: LLM Code Reviewer – semantic game quality assessment.

Calls the LLM to review the generated HTML5 game code and return a
structured JSON assessment:
  - is_complete_game: bool
  - has_real_gameplay: bool
  - difficulty_balanced: bool
  - fun_score: 1-10
  - visual_polish_score: 1-10
  - character_quality_score: 1-10
  - issues: list[str]

Returns LLMReviewResult(ran=False) if LLM is unavailable or review fails.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from ..services.llm_client import LLMClient
from .prompt_format import safe_format_prompt
from .prompt_store import require_prompt
from .quality_scorer import LLMReviewResult

logger = logging.getLogger(__name__)

def _build_code_preview(html_code: str, limit: int | None = None) -> str:
    """Review the complete artifact; slicing hides gameplay and character drawing.

    Kept the optional argument for older callers; no source is silently omitted.
    Provider context admission must fail explicitly if a document cannot fit.
    """
    return html_code


class CodeReviewer:
    """LLM-based semantic quality reviewer for HTML5 games."""

    def __init__(self) -> None:
        self._client = LLMClient()

    async def review(self, html_code: str, *, user_requirements: str = "") -> LLMReviewResult:
        """Run LLM review. Returns LLMReviewResult(ran=False) on any failure."""
        if not self._client.is_enabled():
            logger.debug("LLM not enabled – skipping code review")
            return LLMReviewResult(ran=False)

        code_preview = _build_code_preview(html_code)
        prompt = safe_format_prompt(require_prompt("prompt.code_review_template"), code_preview=code_preview)
        system = require_prompt("prompt.code_review_system")
        if user_requirements:
            system += (
                "\nREQUIREMENT-SCOPED REVIEW: Evaluate the target platform and requested scope in ORIGINAL USER REQUIREMENTS. "
                "Explicit desktop mouse/keyboard requirements take precedence over generic mobile defaults. "
                "Do not require touch gestures or haptics for desktop games. Do not treat unrequested features "
                "(such as radial timers instead of readable numeric timers) as missing requirements. "
                "For non-character games, grade the intentional design and readability of the actual props rather than anatomy. "
                "Still assess real gameplay, responsive controls, coherent composition and required feedback rigorously. "
                "For each low score, include a concrete code-supported defect and a feasible correction in issues; "
                "distinguish missing required behavior from optional aesthetic suggestions."
            )
            prompt = "ORIGINAL USER REQUIREMENTS:\n" + user_requirements + "\n\n" + prompt

        try:
            raw = await self._client.complete_with_truncation_retry(
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                step_key="code_review",
                stage="qa_checking",
                prefer_fast=True,
                response_size_hint="small",
                context_scope="request",
                compression_policy="code_review",
                truncation_retry_attempts=1,
                truncation_retry_increment=512,
                truncation_retry_max_tokens=2048,
                timeout_retry_attempts=1,
                timeout_retry_increment_s=30,
                timeout_retry_max_s=120,
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

        def _clamp_score(field: str, default: float = 5.0) -> float:
            try:
                value = float(data.get(field, default))
            except (TypeError, ValueError):
                value = default
            return max(1.0, min(10.0, value))

        fun_score = _clamp_score("fun_score")
        visual_polish_score = _clamp_score("visual_polish_score")
        character_quality_score = _clamp_score("character_quality_score")

        issues = data.get("issues", [])
        if not isinstance(issues, list):
            issues = [str(issues)] if issues else []

        result = LLMReviewResult(
            ran=True,
            is_complete_game=bool(data.get("is_complete_game", False)),
            has_real_gameplay=bool(data.get("has_real_gameplay", False)),
            difficulty_balanced=bool(data.get("difficulty_balanced", False)),
            fun_score=fun_score,
            visual_polish_score=visual_polish_score,
            character_quality_score=character_quality_score,
            issues=issues[:10],
        )
        logger.info(
            f"Code review: complete={result.is_complete_game}, "
            f"gameplay={result.has_real_gameplay}, fun={result.fun_score:.1f}, "
            f"visual={result.visual_polish_score:.1f}, "
            f"character={result.character_quality_score:.1f}, "
            f"issues={len(result.issues)}"
        )
        return result
