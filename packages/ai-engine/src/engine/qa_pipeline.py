"""Stage 06: QA Pipeline – 6-checkpoint validation with auto-fix loop.

Checkpoints:
  L1  Syntax        – HTML structure parseable, required tags present
  L2  Security      – no forbidden APIs (eval, fetch, localStorage, …)
  L3  Startup       – canvas present + sized, game loop present, no obvious crash
  L4  Playability   – state-machine validated: gameOver SET to true, restart fn,
                      score incremented, input handlers present
  L5  Performance   – file size limits, blocking-loop detection (while/for infinite)
  L6  Content Safety – word-boundary safe regex + Chinese keyword filter

Auto-fix loop: on failure, build targeted fix-prompt → call LLM → retry (max 3×).
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Tuple

try:
    import esprima
except ImportError:  # pragma: no cover - optional dependency during local editing
    esprima = None

from ..api.models import GameSpec, QACheckError, QACheckResponse, QAResult
from ..config.settings import settings
from ..services.llm_client import LLMClient
from .prompt_store import get_prompt

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
# L6 – Content safety: word-boundary English + Chinese keywords
# ---------------------------------------------------------------------------

# Use \b word boundaries to avoid false positives (kill → skill)
UNSAFE_EN_PATTERNS: List[Tuple[str, str]] = [
    (r"\bviolence\b", "violence"),
    (r"\bgore\b", "gore"),
    (r"\bbloodpool\b|\bbloodsplatter\b", "blood imagery"),
    (r"\bporn\b|\bpornograph", "explicit content"),
    (r"\bnude\b|\bnudity\b", "nudity"),
    (r"\bhack\b.*\bbank\b|\bphish", "phishing/hacking"),
    (r"\bmalware\b|\bexploit\b", "malware/exploit"),
]

# Chinese unsafe keywords (no word boundary needed for CJK)
UNSAFE_ZH_KEYWORDS: List[Tuple[str, str]] = [
    ("色情", "adult content"),
    ("赌博", "gambling"),
    ("暴力血腥", "gore"),
    ("政治敏感", "political content"),
]

# ---------------------------------------------------------------------------
# Auto-fix prompt
# ---------------------------------------------------------------------------

FIX_PROMPT = """You are fixing a HTML5 game. The code has the following issues that MUST be fixed:

{error_list}

Game type: {game_type}

Fix ONLY the listed issues. Do not change the game logic or visual design.
Before returning the final answer, do a strict code review and a precompile check:
- verify the HTML document is complete and not truncated
- verify required tags and closing tags are present
- verify every <script> block is syntactically valid JavaScript
- verify the final output ends with </html>
If you notice any additional syntax or structure issue while fixing the listed problems, fix it too.
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

        l1_errors = self._check_l1_syntax(html_code)
        summary["L1_syntax"] = len(l1_errors) == 0
        errors.extend(l1_errors)

        l2_errors = self._check_l2_security(html_code)
        summary["L2_security"] = len(l2_errors) == 0
        errors.extend(l2_errors)

        l3_errors, l3_warnings = self._check_l3_startup(html_code)
        summary["L3_startup"] = len(l3_errors) == 0
        errors.extend(l3_errors)
        warnings.extend(l3_warnings)

        l4_errors, l4_warnings = self._check_l4_playability(html_code)
        summary["L4_playability"] = len(l4_errors) == 0
        errors.extend(l4_errors)
        warnings.extend(l4_warnings)

        l5_errors, l5_warnings = self._check_l5_performance(html_code)
        summary["L5_performance"] = len(l5_errors) == 0
        errors.extend(l5_errors)
        warnings.extend(l5_warnings)

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
        max_retries = max_retries if max_retries is not None else settings.QA_MAX_RETRIES
        code = self._apply_deterministic_repairs(code)

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

            logger.info(f"QA attempt {attempt} failed ({len(result.errors)} errors), triggering LLM auto-fix")
            code = await self.repair_code(code, result.errors, game_spec)

        final = self.check(code)
        return QAResult(
            success=final.passed,
            code=code,
            retries=max_retries,
            last_errors=final.errors,
        )

    async def repair_code(
        self,
        code: str,
        errors: List[QACheckError],
        game_spec: Optional[GameSpec] = None,
        max_tokens: int = 8192,
    ) -> str:
        repaired = self._apply_deterministic_repairs(code)
        if settings.LLM_MODE == "mock" or not self._client.is_enabled() or not errors:
            return repaired

        llm_fixed = await self._fix_with_llm(repaired, errors, game_spec, max_tokens=max_tokens)
        return self._apply_deterministic_repairs(llm_fixed)

    # ------------------------------------------------------------------
    # L1: Syntax – structural HTML completeness
    # ------------------------------------------------------------------

    def _check_l1_syntax(self, code: str) -> List[QACheckError]:
        errors = []
        trimmed = (code or "").strip()
        lower = trimmed.lower()

        if not trimmed:
            return [QACheckError(
                type="L1_syntax",
                message="Generated output is empty",
                severity="error",
            )]

        if re.search(r"^<{7}|^={7}$|^>{7}", trimmed, re.MULTILINE):
            errors.append(QACheckError(
                type="L1_syntax",
                message="Conflict markers detected in generated output",
                severity="error",
            ))

        for tag in ("<html", "<head", "<body", "</body>", "</html>"):
            if tag not in lower:
                errors.append(QACheckError(
                    type="L1_syntax",
                    message=f"Missing required HTML tag: {tag}",
                    severity="error",
                ))
        # Must start with DOCTYPE
        if not re.search(r"<!doctype\s+html", lower):
            errors.append(QACheckError(
                type="L1_syntax",
                message="Missing <!DOCTYPE html> declaration",
                severity="error",
            ))
        # charset meta
        if "charset" not in lower:
            errors.append(QACheckError(
                type="L1_syntax",
                message="Missing charset meta tag",
                severity="error",
            ))

        for tag_name in ("html", "head", "body", "script"):
            open_count = len(re.findall(rf"<{tag_name}\b", lower))
            close_count = len(re.findall(rf"</{tag_name}>", lower))
            if open_count != close_count:
                errors.append(QACheckError(
                    type="L1_syntax",
                    message=f"Unbalanced <{tag_name}> tags: {open_count} open vs {close_count} close",
                    severity="error",
                ))

        if not re.search(r"</html>\s*$", lower):
            errors.append(QACheckError(
                type="L1_syntax",
                message="HTML appears truncated or missing the final </html> closing tag",
                severity="error",
            ))

        if esprima is not None:
            for match in re.finditer(r"<script\b[^>]*>([\s\S]*?)</script>", trimmed, re.IGNORECASE):
                script_content = match.group(1).strip()
                if not script_content:
                    continue
                try:
                    esprima.parseScript(script_content, tolerant=False)
                except Exception as exc:
                    errors.append(QACheckError(
                        type="L1_syntax",
                        message=f"JavaScript syntax error in <script>: {exc}",
                        severity="error",
                    ))
        return errors

    def _apply_deterministic_repairs(self, code: str) -> str:
        repaired = (code or "").strip()
        if not repaired:
            return repaired

        repaired = re.sub(r"^\s*```(?:html)?\s*", "", repaired, flags=re.IGNORECASE)
        repaired = re.sub(r"\s*```\s*$", "", repaired)
        repaired = repaired.replace("\r\n", "\n")

        html_start = re.search(r"<!doctype\s+html|<html\b", repaired, re.IGNORECASE)
        if html_start:
            repaired = repaired[html_start.start():]

        html_end = list(re.finditer(r"</html>", repaired, re.IGNORECASE))
        if html_end:
            repaired = repaired[:html_end[-1].end()]

        lower = repaired.lower()

        if "<!doctype html" not in lower and "<html" in lower:
            repaired = "<!DOCTYPE html>\n" + repaired
            lower = repaired.lower()

        if "<html" not in lower:
            body_content = repaired
            repaired = (
                "<!DOCTYPE html>\n"
                "<html lang=\"zh-CN\">\n"
                "<head>\n"
                "<meta charset=\"UTF-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
                "<title>Generated Game</title>\n"
                "</head>\n"
                "<body>\n"
                f"{body_content}\n"
                "</body>\n"
                "</html>"
            )
            lower = repaired.lower()

        if "<head" not in lower:
            repaired = re.sub(
                r"(<html\b[^>]*>)",
                (
                    "\\1\n<head>\n"
                    "<meta charset=\"UTF-8\">\n"
                    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
                    "<title>Generated Game</title>\n"
                    "</head>"
                ),
                repaired,
                count=1,
                flags=re.IGNORECASE,
            )
            lower = repaired.lower()

        if "charset" not in lower:
            repaired = re.sub(
                r"(<head\b[^>]*>)",
                "\\1\n<meta charset=\"UTF-8\">",
                repaired,
                count=1,
                flags=re.IGNORECASE,
            )
            lower = repaired.lower()

        if "viewport" not in lower:
            repaired = re.sub(
                r"(<head\b[^>]*>)",
                "\\1\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">",
                repaired,
                count=1,
                flags=re.IGNORECASE,
            )
            lower = repaired.lower()

        if "<body" not in lower:
            if "</head>" in lower:
                repaired = re.sub(r"</head>", "</head>\n<body>", repaired, count=1, flags=re.IGNORECASE)
            else:
                repaired += "\n<body>"
            lower = repaired.lower()

        open_script_count = len(re.findall(r"<script\b", lower))
        close_script_count = len(re.findall(r"</script>", lower))
        if open_script_count > close_script_count:
            repaired += "\n" + ("</script>\n" * (open_script_count - close_script_count))
            lower = repaired.lower()

        if "</body>" not in lower:
            if "</html>" in lower:
                repaired = re.sub(r"</html>", "</body>\n</html>", repaired, count=1, flags=re.IGNORECASE)
            else:
                repaired += "\n</body>"
            lower = repaired.lower()

        if "</html>" not in lower:
            repaired += "\n</html>"

        return repaired.strip()

    # ------------------------------------------------------------------
    # L2: Security – forbidden API usage
    # ------------------------------------------------------------------

    def _check_l2_security(self, code: str) -> List[QACheckError]:
        errors = []
        for pattern, label in FORBIDDEN_PATTERNS:
            # Case-sensitive: Function() is dangerous eval-equivalent (capital F);
            # lowercase function declarations must not be flagged.
            if re.search(pattern, code):
                errors.append(QACheckError(
                    type="L2_security",
                    message=f"Forbidden API detected: {label}",
                    severity="error",
                ))
        return errors

    # ------------------------------------------------------------------
    # L3: Startup – canvas initialisation and game loop
    # ------------------------------------------------------------------

    def _check_l3_startup(self, code: str) -> Tuple[List[QACheckError], List[QACheckError]]:
        errors, warnings = [], []

        if not re.search(r"<canvas", code, re.IGNORECASE):
            errors.append(QACheckError(
                type="L3_startup",
                message="No <canvas> element – game cannot render",
                severity="error",
            ))

        if "getContext" not in code:
            errors.append(QACheckError(
                type="L3_startup",
                message="No canvas.getContext() – canvas not initialised",
                severity="error",
            ))

        # Canvas must have width/height set (not just declared)
        if not re.search(r"canvas\.(width|height)\s*=", code):
            errors.append(QACheckError(
                type="L3_startup",
                message="Canvas width/height never set – game renders at 0×0",
                severity="error",
            ))

        if "requestAnimationFrame" not in code and "setInterval" not in code:
            warnings.append(QACheckError(
                type="L3_startup",
                message="No game loop (requestAnimationFrame / setInterval) detected",
                severity="warning",
            ))

        # Detect obvious JS syntax errors: unmatched braces
        open_braces = code.count("{")
        close_braces = code.count("}")
        if abs(open_braces - close_braces) > 5:
            errors.append(QACheckError(
                type="L3_startup",
                message=f"Likely JS syntax error: {open_braces} open braces vs {close_braces} close braces",
                severity="error",
            ))

        return errors, warnings

    # ------------------------------------------------------------------
    # L4: Playability – state-machine validation (P0 improved version)
    # ------------------------------------------------------------------

    def _check_l4_playability(self, code: str) -> Tuple[List[QACheckError], List[QACheckError]]:
        errors, warnings = [], []

        # ── game-over state: must be ASSIGNED true, not just declared ──
        gameover_set = bool(re.search(
            r"(gameOver|game[._]over|isOver|game_over)\s*=\s*true",
            code, re.IGNORECASE,
        ))
        # Also accept patterns like: state = 'gameover', state = states.OVER
        gameover_state_change = bool(re.search(
            r"(state|gameState)\s*=\s*['\"]?(gameover|game_over|over|ended|lost)['\"]?",
            code, re.IGNORECASE,
        ))
        if not gameover_set and not gameover_state_change:
            errors.append(QACheckError(
                type="L4_playability",
                message="Game-over state never set to true – game cannot end",
                severity="error",
            ))

        # ── restart / reset logic ──
        has_restart = bool(re.search(
            r"function\s+(restart|reset|init|newGame|startGame)\s*\(",
            code, re.IGNORECASE,
        ))
        # Accept arrow fn / method form too
        has_restart = has_restart or bool(re.search(
            r"(restart|reset|newGame)\s*[=:]\s*(function|\(|\(\))",
            code, re.IGNORECASE,
        ))
        if not has_restart:
            warnings.append(QACheckError(
                type="L4_playability",
                message="No restart/reset function detected – player cannot retry",
                severity="warning",
            ))

        # ── score system: must be incremented, not just declared ──
        has_score_increment = bool(re.search(
            r"score\s*[\+\-]=|score\s*\+\+|\bscore\b\s*=\s*\bscore\b\s*\+",
            code, re.IGNORECASE,
        ))
        if not has_score_increment:
            warnings.append(QACheckError(
                type="L4_playability",
                message="Score variable exists but is never incremented",
                severity="warning",
            ))

        # ── touch / keyboard input ──
        has_touch = bool(re.search(r"touchstart|touchmove|touchend", code))
        has_keyboard = bool(re.search(r"keydown|keyup|keypress|ArrowUp|ArrowDown", code))
        has_mouse = bool(re.search(r"click|mousedown|mousemove", code))
        if not has_touch and not has_keyboard and not has_mouse:
            errors.append(QACheckError(
                type="L4_playability",
                message="No user input handlers – game is not interactive",
                severity="error",
            ))
        if not has_touch:
            warnings.append(QACheckError(
                type="L4_playability",
                message="No touch event handlers – game may not work on mobile",
                severity="warning",
            ))

        # ── viewport meta for mobile ──
        if "viewport" not in code.lower():
            warnings.append(QACheckError(
                type="L4_playability",
                message="Missing viewport meta tag for mobile",
                severity="warning",
            ))

        return errors, warnings

    # ------------------------------------------------------------------
    # L5: Performance – size + blocking loop detection (P0 improved)
    # ------------------------------------------------------------------

    def _check_l5_performance(self, code: str) -> Tuple[List[QACheckError], List[QACheckError]]:
        errors, warnings = [], []
        size_kb = len(code.encode("utf-8")) / 1024

        if size_kb > 500:
            errors.append(QACheckError(
                type="L5_performance",
                message=f"File size {size_kb:.1f} KB exceeds 500 KB hard limit",
                severity="error",
            ))
        elif size_kb > 300:
            warnings.append(QACheckError(
                type="L5_performance",
                message=f"File size {size_kb:.1f} KB exceeds 300 KB WeChat limit",
                severity="warning",
            ))
        elif size_kb < 2:
            warnings.append(QACheckError(
                type="L5_performance",
                message=f"File size only {size_kb:.1f} KB – game may be too minimal",
                severity="warning",
            ))

        # Detect while(true) / for(;;) without break inside script tags
        script_match = re.search(r"<script[^>]*>(.*?)</script>", code, re.DOTALL | re.IGNORECASE)
        if script_match:
            script = script_match.group(1)
            has_infinite_while = bool(re.search(r"while\s*\(\s*true\s*\)", script))
            has_infinite_for = bool(re.search(r"for\s*\(\s*;;\s*\)", script))
            has_break = "break" in script
            if (has_infinite_while or has_infinite_for) and not has_break:
                errors.append(QACheckError(
                    type="L5_performance",
                    message="Potential infinite loop detected (while(true)/for(;;) with no break) – will freeze browser",
                    severity="error",
                ))

            # Detect synchronous sleep-like patterns (busy wait)
            if re.search(r"while\s*\([^)]*(?:Date\.now|performance\.now)\s*\(\)", script):
                errors.append(QACheckError(
                    type="L5_performance",
                    message="Busy-wait loop detected – blocks main thread",
                    severity="error",
                ))

        return errors, warnings

    # ------------------------------------------------------------------
    # L6: Content Safety – word-boundary English + Chinese (P0 improved)
    # ------------------------------------------------------------------

    def _check_l6_content_safety(self, code: str) -> List[QACheckError]:
        errors = []

        # English: use pre-compiled word-boundary patterns
        for pattern, label in UNSAFE_EN_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                errors.append(QACheckError(
                    type="L6_content",
                    message=f"Unsafe content detected: {label}",
                    severity="error",
                ))

        # Chinese: substring match (CJK has no word boundaries)
        for keyword, label in UNSAFE_ZH_KEYWORDS:
            if keyword in code:
                errors.append(QACheckError(
                    type="L6_content",
                    message=f"Unsafe content detected (ZH): {label}",
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
        max_tokens: int,
    ) -> str:
        error_list = "\n".join(f"  - [{e.type}] {e.message}" for e in errors)
        game_type = game_spec.game_type if game_spec else "unknown"

        prompt_template = get_prompt("prompt.qa_fix", FIX_PROMPT)
        prompt = prompt_template.format(
            error_list=error_list,
            game_type=game_type,
            code=code,
        )
        try:
            from .code_generator import _extract_html
            text = await self._client.complete(
                model=self._client.model_for(),
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return _extract_html(text)
        except Exception as e:
            logger.error(f"LLM auto-fix failed: {e}")
            return code
