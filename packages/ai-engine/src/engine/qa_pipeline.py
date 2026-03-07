"""Stage 06: QA Pipeline – 6-checkpoint validation with auto-fix loop.

Checkpoints:
  L1  Syntax        – HTML structure parseable
  L2  Security      – no forbidden APIs (eval, fetch, localStorage, …)
  L3  Startup       – canvas present, game loop present, no obvious crash
  L4  Playability   – touch events, game-over state, score variable
  L5  Performance   – file size within limits, no obvious blocking loops
  L6  Content Safety – basic keyword filter (production: WeChat msgSecCheck)

Auto-fix loop: on failure, build targeted fix-prompt → call Claude → retry (max 3×).
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Tuple

from ..api.models import GameSpec, QACheckError, QACheckResponse, QAResult
from ..config.settings import settings
from ..services.llm_client import LLMClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# L2 – Forbidden API patterns
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS: List[Tuple[str, str]] = [
    (r"\beval\s*\(", "eval()"),
    (r"\bFunction\s*\(", "Function()"),
    (r"\bimport\s+", "import statement"),
    (r"\brequire\s*\(", "require()"),
    (r"\bfetch\s*\(", "fetch()"),
    (r"\bXMLHttpRequest\b", "XMLHttpRequest"),
    (r"\bWebSocket\b", "WebSocket"),
    (r"\blocalStorage\b", "localStorage"),
    (r"\bsessionStorage\b", "sessionStorage"),
    (r"\bdocument\.cookie\b", "document.cookie"),
    (r"\bdocument\.write\b", "document.write"),
]

# ---------------------------------------------------------------------------
# L6 – Basic content safety keywords
# ---------------------------------------------------------------------------

UNSAFE_KEYWORDS = [
    "violence", "gore", "blood", "porn", "sexy", "nude", "kill",
    "hack", "exploit", "phishing", "malware",
]

# ---------------------------------------------------------------------------
# Auto-fix prompt
# ---------------------------------------------------------------------------

FIX_PROMPT = """You are fixing a HTML5 game. The code has the following issues that MUST be fixed:

{error_list}

Game type: {game_type}

Fix ONLY the listed issues. Do not change the game logic or visual design.
Return ONLY the complete fixed HTML file with no extra text.

Current code:
{code}"""


class QAPipeline:
    """6-checkpoint QA pipeline with auto-fix loop."""

    def __init__(self) -> None:
        self._client = LLMClient()

    # ------------------------------------------------------------------
    # Public: single check
    # ------------------------------------------------------------------

    def check(self, html_code: str) -> QACheckResponse:
        errors: List[QACheckError] = []
        warnings: List[QACheckError] = []
        summary: dict = {}

        # L1 – Syntax
        l1_errors = self._check_l1_syntax(html_code)
        summary["L1_syntax"] = len(l1_errors) == 0
        errors.extend(l1_errors)

        # L2 – Security
        l2_errors = self._check_l2_security(html_code)
        summary["L2_security"] = len(l2_errors) == 0
        errors.extend(l2_errors)

        # L3 – Startup
        l3_errors, l3_warnings = self._check_l3_startup(html_code)
        summary["L3_startup"] = len(l3_errors) == 0
        errors.extend(l3_errors)
        warnings.extend(l3_warnings)

        # L4 – Playability
        l4_errors, l4_warnings = self._check_l4_playability(html_code)
        summary["L4_playability"] = len(l4_errors) == 0
        errors.extend(l4_errors)
        warnings.extend(l4_warnings)

        # L5 – Performance / size
        l5_errors, l5_warnings = self._check_l5_performance(html_code)
        summary["L5_performance"] = len(l5_errors) == 0
        errors.extend(l5_errors)
        warnings.extend(l5_warnings)

        # L6 – Content safety
        l6_errors = self._check_l6_content_safety(html_code)
        summary["L6_content"] = len(l6_errors) == 0
        errors.extend(l6_errors)

        return QACheckResponse(
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            validation_summary=summary,
        )

    # ------------------------------------------------------------------
    # Public: auto-fix loop
    # ------------------------------------------------------------------

    async def run_with_auto_fix(
        self,
        code: str,
        game_spec: Optional[GameSpec] = None,
        max_retries: int = None,
    ) -> QAResult:
        max_retries = max_retries or settings.QA_MAX_RETRIES

        for attempt in range(max_retries + 1):
            result = self.check(code)
            if result.passed:
                logger.info(f"QA passed on attempt {attempt}")
                return QAResult(success=True, code=code, retries=attempt)

            if attempt == max_retries:
                break

            if settings.LLM_MODE == "mock" or not self._client.is_enabled():
                logger.warning("QA failed but LLM auto-fix disabled (mock mode)")
                break

            logger.info(f"QA failed attempt {attempt}, triggering auto-fix. Errors: {len(result.errors)}")
            code = await self._fix_with_llm(code, result.errors, game_spec)

        # Final check after last fix attempt
        final = self.check(code)
        return QAResult(
            success=final.passed,
            code=code,
            retries=max_retries,
            last_errors=final.errors,
        )

    # ------------------------------------------------------------------
    # Individual checkpoint implementations
    # ------------------------------------------------------------------

    def _check_l1_syntax(self, code: str) -> List[QACheckError]:
        errors = []
        lower = code.lower()
        for tag in ("<html", "<body", "</body>", "</html>"):
            if tag not in lower:
                errors.append(QACheckError(
                    type="L1_syntax",
                    message=f"Missing required HTML tag: {tag}",
                    severity="error",
                ))
        return errors

    def _check_l2_security(self, code: str) -> List[QACheckError]:
        errors = []
        for pattern, label in FORBIDDEN_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                errors.append(QACheckError(
                    type="L2_security",
                    message=f"Forbidden API detected: {label}",
                    severity="error",
                ))
        return errors

    def _check_l3_startup(self, code: str) -> Tuple[List[QACheckError], List[QACheckError]]:
        errors, warnings = [], []
        if "<canvas" not in code.lower():
            errors.append(QACheckError(
                type="L3_startup",
                message="No <canvas> element found – game cannot render",
                severity="error",
            ))
        if "requestAnimationFrame" not in code and "setInterval" not in code:
            warnings.append(QACheckError(
                type="L3_startup",
                message="No game loop (requestAnimationFrame / setInterval) detected",
                severity="warning",
            ))
        if "getContext" not in code:
            errors.append(QACheckError(
                type="L3_startup",
                message="No canvas.getContext() call – canvas not initialised",
                severity="error",
            ))
        return errors, warnings

    def _check_l4_playability(self, code: str) -> Tuple[List[QACheckError], List[QACheckError]]:
        errors, warnings = [], []
        has_touch = any(ev in code for ev in ("touchstart", "touchmove", "touchend"))
        if not has_touch:
            warnings.append(QACheckError(
                type="L4_playability",
                message="No touch event handlers – game may not work on mobile",
                severity="warning",
            ))
        if "game.over" not in code and "gameOver" not in code and "game_over" not in code:
            errors.append(QACheckError(
                type="L4_playability",
                message="No game-over state detected – game cannot end",
                severity="error",
            ))
        if "score" not in code.lower():
            warnings.append(QACheckError(
                type="L4_playability",
                message="No score variable detected",
                severity="warning",
            ))
        if "viewport" not in code.lower():
            warnings.append(QACheckError(
                type="L4_playability",
                message="Missing viewport meta tag for mobile",
                severity="warning",
            ))
        return errors, warnings

    def _check_l5_performance(self, code: str) -> Tuple[List[QACheckError], List[QACheckError]]:
        errors, warnings = [], []
        size_kb = len(code.encode("utf-8")) / 1024

        if size_kb > 500:
            errors.append(QACheckError(
                type="L5_performance",
                message=f"File size {size_kb:.1f} KB exceeds 500 KB limit",
                severity="error",
            ))
        elif size_kb > 300:
            warnings.append(QACheckError(
                type="L5_performance",
                message=f"File size {size_kb:.1f} KB exceeds 300 KB WeChat limit",
                severity="warning",
            ))

        # Detect potential infinite loops (while(true) without break)
        if re.search(r"while\s*\(\s*true\s*\)", code) and "break" not in code:
            errors.append(QACheckError(
                type="L5_performance",
                message="Potential infinite loop detected (while(true) with no break)",
                severity="error",
            ))
        return errors, warnings

    def _check_l6_content_safety(self, code: str) -> List[QACheckError]:
        errors = []
        lower = code.lower()
        for kw in UNSAFE_KEYWORDS:
            if kw in lower:
                errors.append(QACheckError(
                    type="L6_content",
                    message=f"Potentially unsafe content keyword: '{kw}'",
                    severity="error",
                ))
        return errors

    # ------------------------------------------------------------------
    # LLM auto-fix
    # ------------------------------------------------------------------

    async def _fix_with_llm(
        self,
        code: str,
        errors: List[QACheckError],
        game_spec: Optional[GameSpec],
    ) -> str:
        error_list = "\n".join(f"  - [{e.type}] {e.message}" for e in errors)
        game_type = game_spec.game_type if game_spec else "unknown"

        prompt = FIX_PROMPT.format(
            error_list=error_list,
            game_type=game_type,
            code=code,
        )
        try:
            from .code_generator import _extract_html
            text = await self._client.complete(
                model=self._client.model_for(),
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            return _extract_html(text)
        except Exception as e:
            logger.error(f"LLM auto-fix failed: {e}")
            return code
