"""Stage 06: QA Pipeline – 6-checkpoint validation with auto-fix loop.

Checkpoints:
  L1  Syntax        – HTML structure parseable, required tags present
  L2  Security      – no forbidden APIs (eval, fetch, remote network APIs, …)
  L3  Startup       – canvas present + sized, game loop present, no obvious crash
  L4  Playability   – state-machine validated: gameOver SET to true, restart fn,
                      score incremented, input handlers present
  L5  Performance   – file size limits, blocking-loop detection (while/for infinite)
  L6  Content Safety – word-boundary safe regex + Chinese keyword filter

Auto-fix loop: on failure, build targeted fix-prompt → call LLM → retry (max 3×).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import esprima
except ImportError:  # pragma: no cover - optional dependency during local editing
    esprima = None

from ..api.models import (
    GameRuntimeContract,
    GameSpec,
    QACheckError,
    QACheckResponse,
    QAIssueList,
    QAIssueLocation,
    QAResult,
)
from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int
from ..services.llm_client import LLMClient, LLMResponseTruncatedError
from ..services.llm_gateway import get_request_context
from ..services.task_memory import task_memory
from .prompt_store import require_prompt
from .restart_entry import has_restart_entry
from .section_patch import (
    ensure_structured_section_markers,
    extract_script_content,
    find_incomplete_structured_markers,
    has_structured_section_markers,
    replace_script_content,
)
from .terminal_state import has_required_state_presence, has_terminal_state_transition

logger = logging.getLogger(__name__)

QARetryCallback = Optional[Callable[[int, int, List[QACheckError]], None]]


class _SafePromptFormatDict(dict):
    """Preserve unknown placeholders instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"

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
    (r"\bdocument\.cookie\b", "document.cookie"),
    (r"\bdocument\.write\b", "document.write"),
    (r"<script\b[^>]*\bsrc\s*=\s*['\"](?:https?:)?//", "external script src"),
    (r"<(?:img|audio|video|source|iframe)\b[^>]*\bsrc\s*=\s*['\"](?:https?:)?//", "external media src"),
    (r"<link\b[^>]*\bhref\s*=\s*['\"](?:https?:)?//", "external stylesheet"),
    (r"url\(\s*['\"]?(?:https?:)?//", "external CSS asset"),
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

_INPUT_EVENT_FAMILIES: Dict[str, Tuple[str, ...]] = {
    "touch": ("touchstart", "touchmove", "touchend", "touchcancel"),
    "pointer": ("pointerdown", "pointermove", "pointerup", "pointercancel"),
    "mouse": ("click", "mousedown", "mousemove", "mouseup"),
    "keyboard": ("keydown", "keyup", "keypress"),
    "sensor": ("deviceorientation", "devicemotion"),
}

SYNTAX_REPAIR_FAMILY = "syntax_structural"
_SCRIPT_SYNTAX_LINE_RE = re.compile(
    r"JavaScript syntax error in <script>:\s*Line\s*(\d+)\s*:",
    re.IGNORECASE,
)


class QAPipeline:
    """6-checkpoint QA pipeline with auto-fix loop."""

    def __init__(self) -> None:
        self._client = LLMClient()

    @staticmethod
    def _current_task_id() -> Optional[str]:
        return get_request_context().get("task_id")

    # ------------------------------------------------------------------
    # Public: single check
    # ------------------------------------------------------------------

    def check(
        self,
        html_code: str,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> QACheckResponse:
        errors: List[QACheckError] = []
        warnings: List[QACheckError] = []
        summary: dict = {}

        l1_errors = self._check_l1_syntax(html_code)
        summary["L1_syntax"] = len(l1_errors) == 0
        errors.extend(l1_errors)

        marker_errors, marker_warnings = self._check_l1_structure_markers(html_code)
        summary["L1_structure_markers"] = len(marker_errors) == 0
        errors.extend(marker_errors)
        warnings.extend(marker_warnings)

        l2_errors = self._check_l2_security(html_code)
        summary["L2_security"] = len(l2_errors) == 0
        errors.extend(l2_errors)

        l3_errors, l3_warnings = self._check_l3_startup(html_code)
        summary["L3_startup"] = len(l3_errors) == 0
        errors.extend(l3_errors)
        warnings.extend(l3_warnings)

        l4_errors, l4_warnings = self._check_l4_playability(
            html_code,
            runtime_contract=runtime_contract,
        )
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

        normalized_errors = self.normalize_issues(errors, default_blocking=True)
        normalized_warnings = self.normalize_issues(warnings, default_blocking=False)

        return QACheckResponse(
            passed=len(normalized_errors) == 0,
            errors=normalized_errors,
            warnings=normalized_warnings,
            validation_summary=summary,
            issue_list=self.build_issue_list(normalized_errors, normalized_warnings),
        )

    @classmethod
    def normalize_issues(
        cls,
        issues: List[QACheckError],
        *,
        default_blocking: Optional[bool] = None,
    ) -> List[QACheckError]:
        return [
            cls.enrich_issue(issue, default_blocking=default_blocking)
            for issue in (issues or [])
        ]

    @classmethod
    def build_issue_list(
        cls,
        errors: List[QACheckError],
        warnings: Optional[List[QACheckError]] = None,
    ) -> QAIssueList:
        normalized_errors = cls.normalize_issues(errors, default_blocking=True)
        normalized_warnings = cls.normalize_issues(warnings or [], default_blocking=False)
        issues = [*normalized_errors, *normalized_warnings]
        families = sorted({
            str(issue.family or "generic")
            for issue in issues
            if str(issue.family or "").strip()
        })
        return QAIssueList(
            issues=issues,
            blocking_count=sum(1 for issue in issues if issue.blocking is not False),
            warning_count=sum(1 for issue in issues if str(issue.severity or "").lower() == "warning"),
            families=families,
        )

    @classmethod
    def enrich_issue(
        cls,
        error: QACheckError,
        *,
        default_blocking: Optional[bool] = None,
    ) -> QACheckError:
        family = str(error.family or "").strip() or cls._classify_error_family(error)
        severity = str(error.severity or "error").strip() or "error"
        blocking = (
            error.blocking
            if error.blocking is not None
            else (
                default_blocking
                if default_blocking is not None
                else severity.lower() != "warning"
            )
        )
        location = error.location
        if location is None and error.line is not None:
            location = QAIssueLocation(line=error.line)
        elif location is not None and location.line is None and error.line is not None:
            location = location.model_copy(update={"line": error.line})
        repair_hint = str(error.repair_hint or "").strip() or cls._repair_hint_for_family(
            family,
            error,
        )
        normalized_line = error.line if error.line is not None else (location.line if location else None)
        return error.model_copy(update={
            "severity": severity,
            "family": family,
            "blocking": blocking,
            "repair_hint": repair_hint,
            "location": location,
            "line": normalized_line,
        })

    def _has_canvas_draw_commands(self, code: str) -> bool:
        visible_draw_methods = (
            "fillRect",
            "strokeRect",
            "drawImage",
            "fillText",
            "strokeText",
            "putImageData",
            "fill",
            "stroke",
        )
        webgl_draw_methods = (
            "clear",
            "clearColor",
            "drawArrays",
            "drawElements",
            "bufferData",
            "bufferSubData",
            "texImage2D",
            "texSubImage2D",
            "viewport",
            "useProgram",
        )
        method_pattern = r"(?:%s)" % "|".join(visible_draw_methods + webgl_draw_methods)

        if re.search(
            rf"getContext\s*\(\s*['\"](?:2d|webgl|webgl2)['\"]\s*\)\s*\.\s*{method_pattern}\s*\(",
            code,
            re.IGNORECASE,
        ):
            return True

        context_vars = {
            match.group(1)
            for match in re.finditer(
                r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[^;]*getContext\s*\(\s*['\"](?:2d|webgl|webgl2)['\"]\s*\)",
                code,
                re.IGNORECASE,
            )
        }
        context_vars.update(
            match.group(1)
            for match in re.finditer(
                r"\b((?:this\.)?[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*=\s*[^;]*getContext\s*\(\s*['\"](?:2d|webgl|webgl2)['\"]\s*\)",
                code,
                re.IGNORECASE,
            )
        )
        for var_name in context_vars:
            if re.search(
                rf"\b{re.escape(var_name)}\s*\.\s*{method_pattern}\s*\(",
                code,
                re.IGNORECASE,
            ):
                return True

        if re.search(
            rf"(?:\b(?:this\.)?[A-Za-z_$][\w$]*\.ctx\b|\b(?:ctx|context|canvasCtx|canvasContext|renderCtx|drawCtx)\b)\s*\.\s*{method_pattern}\s*\(",
            code,
            re.IGNORECASE,
        ):
            return True

        return False

    def _extract_canvas_handles(self, code: str) -> set[str]:
        handles = {"canvas"}
        for match in re.finditer(
            r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*document\.(?:getElementById|querySelector)\s*\(\s*['\"][^'\"]*(?:gameCanvas|canvas)[^'\"]*['\"]\s*\)",
            code,
            re.IGNORECASE,
        ):
            handles.add(match.group(1))
        return handles

    def _has_canvas_dimensions_set(self, code: str) -> bool:
        if re.search(
            r"<canvas\b[^>]*\bwidth\s*=\s*['\"]?\d+['\"]?[^>]*\bheight\s*=\s*['\"]?\d+['\"]?",
            code,
            re.IGNORECASE,
        ):
            return True
        for handle in self._extract_canvas_handles(code):
            if re.search(rf"\b{re.escape(handle)}\.(width|height)\s*=", code):
                return True
        return False

    @staticmethod
    def _has_width_only_font_scaling(code: str) -> bool:
        if not re.search(r"\bscaleX\b", code) or not re.search(r"\bscaleY\b", code):
            return False
        if not re.search(r"window\.innerWidth|window\.innerHeight|addEventListener\s*\(\s*['\"]resize['\"]", code, re.IGNORECASE):
            return False

        font_assignments = re.findall(r"\b(?:ctx|context|canvasCtx|canvasContext|renderCtx|drawCtx)\.font\s*=\s*([^;]+);", code)
        for assignment in font_assignments:
            normalized = assignment.replace(" ", "")
            if "scaleX" not in normalized:
                continue
            if any(token in normalized for token in ("scaleY", "uiScale", "fontScale", "layoutScale", "Math.min")):
                continue
            return True
        return False

    @staticmethod
    def _code_hash(code: str) -> str:
        return hashlib.sha1((code or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_error_signature(errors: List[QACheckError]) -> Tuple[str, ...]:
        return tuple(
            sorted(
                "{}:{}".format(
                    error.type,
                    re.sub(r"\s+", " ", error.message.strip().lower()),
                )
                for error in errors
            )
        )

    @staticmethod
    def _estimate_syntax_repair_max_tokens(code: str, *, truncation_risk: bool) -> int:
        approx_tokens = max(2048, len((code or "").encode("utf-8")) // 3)
        buffer = 3072 if truncation_risk else 2048
        ceiling = min(
            settings.LLM_LONG_GENERATION_MAX_TOKENS,
            12288 if truncation_risk else 8192,
        )
        floor = 6144 if truncation_risk else 4096
        return min(ceiling, max(floor, approx_tokens + buffer))

    @classmethod
    def _estimate_repair_timeout_s(
        cls,
        *,
        max_tokens: int,
    ) -> int:
        timeout_s = get_timeout_int("timeout.ai_engine.qa_repair_s", 45, min_value=30)
        timeout_s = max(timeout_s, 45)
        if max_tokens >= 8192:
            timeout_s = max(timeout_s, 50)
        if max_tokens >= 12288:
            timeout_s = max(timeout_s, 60)
        return timeout_s

    @staticmethod
    def _syntax_repair_hedge_delay_s(request_timeout_s: int) -> int:
        return max(8, min(15, request_timeout_s // 3))

    @staticmethod
    def _estimate_script_repair_max_tokens(script: str) -> int:
        approx_tokens = max(1024, len((script or "").encode("utf-8")) // 3)
        return min(6144, max(2048, approx_tokens + 1024))

    @classmethod
    def _estimate_script_repair_timeout_s(
        cls,
        *,
        max_tokens: int,
    ) -> int:
        return get_timeout_int("timeout.ai_engine.qa_fast_repair_s", 120, min_value=1)

    @staticmethod
    def _script_syntax_error_line_numbers(errors: List[QACheckError]) -> List[int]:
        line_numbers: list[int] = []
        for error in errors or []:
            match = _SCRIPT_SYNTAX_LINE_RE.search(error.message or "")
            if not match:
                continue
            try:
                line_numbers.append(int(match.group(1)))
            except Exception:
                continue
        return sorted({line_no for line_no in line_numbers if line_no > 0})

    @staticmethod
    def _extract_script_repair_window(
        script: str,
        *,
        line_numbers: List[int],
        context_lines: int = 18,
    ) -> tuple[int, int, str]:
        script_lines = (script or "").splitlines()
        if not script_lines:
            return 1, 1, script or ""
        if not line_numbers:
            return 1, len(script_lines), script or ""
        start_line = max(1, min(line_numbers) - context_lines)
        end_line = min(len(script_lines), max(line_numbers) + context_lines)
        snippet = "\n".join(script_lines[start_line - 1:end_line])
        return start_line, end_line, snippet

    @staticmethod
    def _replace_script_repair_window(
        script: str,
        *,
        start_line: int,
        end_line: int,
        replacement: str,
    ) -> str:
        original_lines = (script or "").splitlines()
        replacement_lines = (replacement or "").splitlines()
        merged = original_lines[: max(0, start_line - 1)] + replacement_lines + original_lines[end_line:]
        return "\n".join(merged)

    @staticmethod
    def _script_has_valid_syntax(script: str) -> bool:
        cleaned = (script or "").strip()
        if not cleaned:
            return False
        if esprima is None:
            return True
        try:
            esprima.parseScript(cleaned, tolerant=False)
            return True
        except Exception:
            return False

    def _extract_input_handlers(self, code: str) -> Dict[str, List[str]]:
        detected: Dict[str, set[str]] = {
            family: set() for family in _INPUT_EVENT_FAMILIES
        }

        for family, events in _INPUT_EVENT_FAMILIES.items():
            for event_name in events:
                escaped = re.escape(event_name)
                patterns = (
                    rf"addEventListener\s*\(\s*['\"]{escaped}['\"]",
                    rf"\bon{escaped}\s*=",
                )
                if any(re.search(pattern, code, re.IGNORECASE) for pattern in patterns):
                    detected[family].add(event_name)

        if re.search(r"\b(?:key|code)\s*===?\s*['\"]Arrow(?:Up|Down|Left|Right)['\"]", code, re.IGNORECASE):
            detected["keyboard"].add("arrow-key-branch")

        return {
            family: sorted(values)
            for family, values in detected.items()
        }

    @staticmethod
    def _build_runtime_contract_block(runtime_contract: Optional[GameRuntimeContract]) -> str:
        if not runtime_contract:
            return ""

        required_states = ", ".join(runtime_contract.state.required_states) or "boot, ready, playing, game_over"
        input_modes = ", ".join(runtime_contract.input.required_modes) or "touch, pointer"
        forbidden_apis = ", ".join(runtime_contract.safety.forbidden_apis) or "eval, Function, import, require"
        terminal_state_aliases = ", ".join(runtime_contract.gameplay.terminal_state_aliases or []) or "game_over"
        block = require_prompt("prompt.qa_runtime_contract_block").format(
            contract_version=runtime_contract.version,
            runtime_profile=runtime_contract.runtime_profile,
            required_states=required_states,
            input_modes=input_modes,
            forbidden_apis=forbidden_apis,
            terminal_state_aliases=terminal_state_aliases,
        )
        if "terminal/completion state aliases" not in block.lower():
            block += f"\n- Accepted terminal/completion state aliases: {terminal_state_aliases}"
        if "mobile orientation" not in block.lower():
            block += f"\n- Mobile orientation: {runtime_contract.mobile_layout.orientation}"
        if "ui scale mode" not in block.lower():
            block += f"\n- UI scale mode: {runtime_contract.mobile_layout.ui_scale_mode}"
        return "\n" + block

    @staticmethod
    def _resolved_bundle_prompt(
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
        slot: str,
    ) -> str:
        resolved_prompts = ((prompt_bundle_snapshot or {}).get("layers") or {}).get("resolved_prompts")
        if not isinstance(resolved_prompts, dict):
            return ""

        entry = resolved_prompts.get(slot)
        if isinstance(entry, dict):
            return str(entry.get("content") or "").strip()
        if isinstance(entry, str):
            return entry.strip()
        return ""

    @staticmethod
    def _classify_error_family(error: QACheckError) -> str:
        error_type = (error.type or "").lower()
        message = (error.message or "").lower()

        if error_type.startswith("l1_") or "syntax" in error_type:
            return "syntax_structural"

        if any(
            token in message
            for token in (
                "forbidden api",
                "forbids api usage",
                "eval",
                "function()",
                "import statement",
                "require()",
                "fetch()",
                "xmlhttprequest",
                "websocket",
                "localstorage",
                "sessionstorage",
                "document.cookie",
                "document.write",
            )
        ):
            return "forbidden_api"

        if any(
            token in message
            for token in (
                "no user input handlers",
                "touch event handlers",
                "primary touch or pointer gameplay handlers",
                "registered user input handlers",
                "visible state change after user interaction",
                "game is not interactive",
            )
        ):
            return "input_contract"

        if any(
            token in message
            for token in (
                "visible scoring loop",
                "visible score",
                "score display",
                "score hud",
                "scoreboard",
            )
        ) or (error_type in {"contract_gameplay"} and "scor" in message):
            return "score_feedback"

        if any(
            token in message
            for token in (
                "game-over state",
                "terminal state",
                "restart entry point",
                "restart/reset function",
                "requires state 'game_over'",
                "requires state 'playing'",
                "requires state 'ready'",
                "requires state 'boot'",
            )
        ) or error_type in {"contract_state"}:
            return "terminal_state"

        if any(
            token in message
            for token in (
                "viewport",
                "portrait-first",
                "portrait first",
                "short-edge",
                "short edge",
                "ui text scales from screen width only",
                "wide or landscape mobile screens",
                "mobile",
                "landscape",
                "ui scale",
            )
        ) or error_type in {"contract_mobile"}:
            return "mobile_layout"

        if any(
            token in message
            for token in (
                "blank screen",
                "canvas never rendered",
                "canvas 2d context",
                "canvas not initialised",
                "canvas width/height never set",
                "runtime js error",
                "no canvas drawing commands",
                "no <canvas> element",
            )
        ) or error_type in {"l3_startup", "runtime_qa", "contract_canvas"}:
            return "runtime_startup"

        return "generic"

    @staticmethod
    def _repair_hint_for_family(family: str, error: QACheckError) -> str:
        message = str(error.message or "").strip()
        if family == "syntax_structural":
            return "Restore valid HTML/JS structure without deleting the main gameplay loop."
        if family == "forbidden_api":
            return "Replace forbidden browser APIs with safe inline logic and remove blocked storage/network usage."
        if family == "input_contract":
            return "Register primary touch/pointer handlers and make the first user action trigger an immediate visible state change."
        if family == "score_feedback":
            return "Expose scoring or progress feedback in the live HUD so players can see reward updates during play."
        if family == "terminal_state":
            return "Ensure the game can enter a terminal state and restart through an explicit reset entry point."
        if family == "mobile_layout":
            return "Apply portrait-safe viewport and short-edge scaling so HUD and canvas remain readable on mobile."
        if family == "runtime_startup":
            return "Keep boot lightweight, render the first frame quickly, and avoid synchronous work that blocks startup or first input."
        if message:
            return f"Apply the smallest targeted fix that resolves: {message}"
        return "Apply the smallest targeted fix while preserving the current gameplay loop."

    @classmethod
    def _syntax_repair_errors(cls, errors: List[QACheckError]) -> List[QACheckError]:
        return [
            error
            for error in (errors or [])
            if cls._classify_error_family(error) == SYNTAX_REPAIR_FAMILY
        ]

    @classmethod
    def _is_markup_structure_syntax_error(cls, error: QACheckError) -> bool:
        if cls._classify_error_family(error) != SYNTAX_REPAIR_FAMILY:
            return False
        message = str(error.message or "").lower()
        return any(
            token in message
            for token in (
                "missing required html tag",
                "missing <!doctype html>",
                "missing charset meta tag",
                "unbalanced <html>",
                "unbalanced <head>",
                "unbalanced <body>",
                "unbalanced <script>",
                "html appears truncated",
                "conflict markers detected",
                "unexpected end of input",
                "unexpected eof",
                "unterminated string",
                "missing closing",
                "unclosed",
                "syntax error: unexpected end",
            )
        )

    @classmethod
    def _should_attempt_syntax_only_repair(cls, errors: List[QACheckError]) -> bool:
        normalized = cls.normalize_issues(errors or [], default_blocking=True)
        if not normalized:
            return False
        syntax_errors = cls._syntax_repair_errors(normalized)
        if not syntax_errors or len(syntax_errors) != len(normalized):
            return False
        if cls._errors_look_like_truncation(syntax_errors):
            return True
        return all(cls._is_markup_structure_syntax_error(error) for error in syntax_errors)

    def _resolve_syntax_repair_prompt(
        self,
        *,
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
    ) -> Tuple[str, str]:
        bundle_prompt = self._resolved_bundle_prompt(
            prompt_bundle_snapshot,
            "repair_syntax_structural",
        )
        if bundle_prompt:
            return "bundle.repair.syntax_structural", bundle_prompt
        return "bundle.repair.syntax_structural", require_prompt("bundle.repair.syntax_structural")

    # ------------------------------------------------------------------
    # Public: auto-fix loop
    # ------------------------------------------------------------------

    @staticmethod
    def _errors_look_like_truncation(errors: List[QACheckError]) -> bool:
        """Detect if errors suggest the LLM output was truncated mid-code."""
        truncation_signals = (
            "unexpected end of input",
            "unexpected eof",
            "unterminated string",
            "unbalanced",
            "missing closing",
            "unclosed",
            "syntax error",
            "</html> missing",
            "</script> missing",
            "</body> missing",
        )
        for error in errors:
            msg = error.message.lower()
            if any(signal in msg for signal in truncation_signals):
                return True
            error_type = (getattr(error, "type", None) or getattr(error, "error_type", "") or "").lower()
            if error_type == "l1_syntax" and "tag" in msg and "missing" in msg:
                return True
        return False

    async def run_with_auto_fix(
        self,
        code: str,
        game_spec: Optional[GameSpec] = None,
        runtime_contract: Optional[GameRuntimeContract] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        max_retries: int = None,
        retry_cb: QARetryCallback = None,
    ) -> QAResult:
        max_retries = max_retries if max_retries is not None else settings.QA_MAX_RETRIES
        code = self._apply_deterministic_repairs(code)
        repair_attempts = 0
        previous_error_signature: Optional[Tuple[str, ...]] = None
        repeated_single_issue_rounds = 0
        previous_code_hash = self._code_hash(code)
        error_count_history: List[int] = []

        for attempt in range(max_retries + 1):
            result = self.check(code, runtime_contract=runtime_contract)
            if result.passed:
                logger.info(f"QA passed on attempt {attempt}")
                return QAResult(
                    success=True,
                    code=code,
                    retries=repair_attempts,
                    issue_list=result.issue_list,
                )

            if attempt == max_retries:
                break

            if not self._client.is_enabled():
                logger.warning("QA failed but LLM auto-fix is unavailable")
                break

            repairable_errors = self._syntax_repair_errors(result.errors)
            if not repairable_errors or not self._should_attempt_syntax_only_repair(result.errors):
                logger.info(
                    "QA failed without truncation-like syntax issues; syntax-only repair is disabled for this error set"
                )
                break

            current_signature = self._normalize_error_signature(repairable_errors)
            if len(repairable_errors) == 1 and current_signature == previous_error_signature:
                repeated_single_issue_rounds += 1
            else:
                repeated_single_issue_rounds = 0

            if repeated_single_issue_rounds >= 2:
                logger.warning(
                    "Syntax-only QA circuit breaker triggered after repeated single-issue failures: %s",
                    repairable_errors[0].message if repairable_errors else "unknown",
                )
                break

            if attempt >= 1 and self._errors_look_like_truncation(repairable_errors):
                logger.warning(
                    "Truncation persists after %d repair attempt(s); signaling regeneration needed",
                    repair_attempts,
                )
                final = self.check(code, runtime_contract=runtime_contract)
                return QAResult(
                    success=False,
                    code=code,
                    retries=repair_attempts,
                    last_errors=final.errors,
                    needs_regeneration=True,
                    issue_list=final.issue_list,
                )

            error_count_history.append(len(repairable_errors))
            if len(error_count_history) >= 3:
                last3 = error_count_history[-3:]
                if last3[-1] >= last3[-2] >= last3[-3]:
                    logger.warning(
                        "Fix loop showing diminishing returns (error counts: %s); exiting early",
                        last3,
                    )
                    break

            if retry_cb:
                try:
                    retry_cb(attempt + 1, max_retries, repairable_errors)
                except Exception:
                    pass
            logger.info(f"QA attempt {attempt} failed ({len(repairable_errors)} syntax errors), triggering syntax-only auto-fix")
            try:
                repaired_code = await self.repair_code(
                    code,
                    repairable_errors,
                    game_spec,
                    runtime_contract=runtime_contract,
                    prompt_bundle_snapshot=prompt_bundle_snapshot,
                    fix_round=attempt + 1,
                    max_fix_rounds=max_retries,
                )
            except LLMResponseTruncatedError as exc:
                logger.warning(
                    "Syntax repair hit output truncation on round %s; signaling full regeneration",
                    repair_attempts + 1,
                )
                final = self.check(code, runtime_contract=runtime_contract)
                return QAResult(
                    success=False,
                    code=code,
                    retries=repair_attempts,
                    last_errors=final.errors,
                    needs_regeneration=True,
                    issue_list=final.issue_list,
                )
            repair_attempts += 1
            repaired_hash = self._code_hash(repaired_code)
            if repaired_hash == previous_code_hash:
                logger.warning("QA repair produced an unchanged code hash on round %s; stopping early", repair_attempts)
                final = self.check(code, runtime_contract=runtime_contract)
                if not final.passed:
                    return QAResult(
                        success=False,
                        code=code,
                        retries=repair_attempts,
                        last_errors=final.errors,
                        needs_regeneration=True,
                        issue_list=final.issue_list,
                    )
                code = repaired_code
                break

            code = repaired_code
            previous_code_hash = repaired_hash
            previous_error_signature = current_signature

        final = self.check(code, runtime_contract=runtime_contract)
        return QAResult(
            success=final.passed,
            code=code,
            retries=repair_attempts,
            last_errors=final.errors,
            issue_list=final.issue_list,
        )

    async def repair_code(
        self,
        code: str,
        errors: List[QACheckError],
        game_spec: Optional[GameSpec] = None,
        runtime_contract: Optional[GameRuntimeContract] = None,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
        fix_round: int = 1,
        max_fix_rounds: int = 1,
    ) -> str:
        repaired = self._apply_deterministic_repairs(code)
        errors = self.normalize_issues(errors, default_blocking=True)
        syntax_errors = self._syntax_repair_errors(errors)
        if (
            not self._client.is_enabled()
            or not syntax_errors
            or not self._should_attempt_syntax_only_repair(errors)
        ):
            return repaired

        preserve_structured_markers = has_structured_section_markers(code)
        if preserve_structured_markers:
            repaired = ensure_structured_section_markers(repaired)

        effective_max_tokens = max_tokens if max_tokens is not None else self._estimate_syntax_repair_max_tokens(
            repaired,
            truncation_risk=self._errors_look_like_truncation(syntax_errors),
        )

        llm_fixed = await self._fix_with_llm(
            repaired,
            syntax_errors,
            game_spec,
            runtime_contract,
            max_tokens=effective_max_tokens,
            fix_round=fix_round,
            max_fix_rounds=max_fix_rounds,
            prompt_bundle_snapshot=prompt_bundle_snapshot,
        )
        llm_repaired = self._apply_deterministic_repairs(llm_fixed)
        if preserve_structured_markers:
            llm_repaired = ensure_structured_section_markers(llm_repaired)
        if self._introduces_structural_regression(repaired, llm_repaired):
            logger.warning(
                "QA repair candidate rejected because it introduced structural regression; keeping previous stable candidate"
            )
            return repaired
        return llm_repaired

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

        if re.search(r"(?m)^\s*static\s+[A-Za-z_$][\w$]*\s*=", trimmed):
            errors.append(QACheckError(
                type="L1_syntax",
                message="JavaScript local static declarations are not valid in plain browser JS; use outer-scope let/const state instead",
                severity="error",
            ))

        script_matches = list(re.finditer(r"<script\b[^>]*>([\s\S]*?)</script>", trimmed, re.IGNORECASE))
        if script_matches and esprima is None:
            logger.warning("esprima is unavailable; skipping JavaScript syntax parsing in L1 QA")

        if esprima is not None:
            for match in script_matches:
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

    def _check_l1_structure_markers(self, code: str) -> Tuple[List[QACheckError], List[QACheckError]]:
        errors: List[QACheckError] = []
        warnings: List[QACheckError] = []
        if not has_structured_section_markers(code):
            return errors, warnings

        incomplete_markers = find_incomplete_structured_markers(code)
        if incomplete_markers:
            errors.append(QACheckError(
                type="L1_structure_markers",
                message="Structured section marker set is incomplete: missing matching boundary for "
                + ", ".join(sorted(dict.fromkeys(incomplete_markers))),
                severity="error",
            ))
        return errors, warnings

    def _introduces_structural_regression(self, previous_code: str, candidate_code: str) -> bool:
        previous = (previous_code or "").strip()
        candidate = (candidate_code or "").strip()
        if not candidate:
            return True

        previous_l1 = self._check_l1_syntax(previous)
        candidate_l1 = self._check_l1_syntax(candidate)
        previous_messages = {error.message for error in previous_l1}
        candidate_messages = {error.message for error in candidate_l1}

        truncation_markers = (
            "HTML appears truncated",
            "Unbalanced <html>",
            "Unbalanced <head>",
            "Unbalanced <body>",
            "Unbalanced <script>",
            "Missing required HTML tag",
        )
        candidate_has_new_truncation = any(
            any(marker in message for marker in truncation_markers)
            for message in candidate_messages - previous_messages
        )
        if candidate_has_new_truncation:
            return True

        previous_has_canvas = bool(re.search(r"<canvas\b", previous, re.IGNORECASE))
        candidate_has_canvas = bool(re.search(r"<canvas\b", candidate, re.IGNORECASE))
        if previous_has_canvas and not candidate_has_canvas:
            return True

        if previous:
            if len(previous) >= 512:
                minimum_candidate_length = max(512, int(len(previous) * 0.55))
            else:
                minimum_candidate_length = max(96, int(len(previous) * 0.55))
            if len(candidate) < minimum_candidate_length:
                return True

        return len(candidate_l1) > len(previous_l1)

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
        if not self._has_canvas_dimensions_set(code):
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

        if not self._has_canvas_draw_commands(code):
            errors.append(QACheckError(
                type="L3_startup",
                message="No canvas drawing commands detected – game may render a blank screen",
                severity="error",
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

    def _check_l4_playability(
        self,
        code: str,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> Tuple[List[QACheckError], List[QACheckError]]:
        errors, warnings = [], []

        # ── game-over state: must be ASSIGNED true, not just declared ──
        terminal_state_required = (
            runtime_contract.gameplay.requires_terminal_state
            if runtime_contract
            else True
        )
        gameover_set = (
            not terminal_state_required
            or has_terminal_state_transition(code, runtime_contract)
            or bool(re.search(
                r"(gameOver|game[._]over|isOver|game_over|isGameOver|gameEnded|hasEnded|isEnded|playerDead|isDead|dead)\s*=\s*true",
                code, re.IGNORECASE,
            ))
        )
        # Also accept patterns like: state = 'gameover', state = states.OVER
        gameover_state_change = bool(re.search(
            r"(state|gameState|currentState|status|gameStatus)\s*=\s*['\"]?(gameover|game_over|over|ended|lost|lose|failed|dead)['\"]?",
            code, re.IGNORECASE,
        ))
        gameover_state_transition = bool(re.search(
            r"(setState|changeState|transitionTo|enterState)\s*\(\s*['\"]?(gameover|game_over|over|ended|lost|lose|failed|dead)['\"]?",
            code, re.IGNORECASE,
        ))
        gameover_state_enum_change = bool(re.search(
            r"(state|gameState|currentState|status|gameStatus)\s*=\s*(?:[A-Za-z_$][\w$]*\.)*(GAME[_-]?OVER|OVER|ENDED|LOST|LOSE|FAILED|DEAD)\b",
            code, re.IGNORECASE,
        ))
        if not gameover_set and not gameover_state_change and not gameover_state_transition and not gameover_state_enum_change:
            errors.append(QACheckError(
                type="L4_playability",
                message="Required terminal or completion state is never set – game cannot end or complete",
                severity="error",
            ))

        # ── restart / reset logic ──
        downgraded_errors: List[QACheckError] = []
        for error in errors:
            if (
                error.type == "L4_playability"
                and "Required terminal or completion state is never set" in error.message
            ):
                warnings.append(QACheckError(
                    type=error.type,
                    message=error.message,
                    severity="warning",
                ))
                continue
            downgraded_errors.append(error)
        errors = downgraded_errors

        has_restart = has_restart_entry(code)
        if (runtime_contract.gameplay.requires_restart_entry if runtime_contract else True) and not has_restart:
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
        if (runtime_contract.gameplay.requires_scoring if runtime_contract else True) and not has_score_increment:
            warnings.append(QACheckError(
                type="L4_playability",
                message="Score variable exists but is never incremented",
                severity="warning",
            ))

        # ── user input handling ──
        input_handlers = self._extract_input_handlers(code)
        has_touch = bool(input_handlers["touch"])
        has_pointer = bool(input_handlers["pointer"])
        has_keyboard = bool(input_handlers["keyboard"])
        has_mouse = bool(input_handlers["mouse"])
        has_sensor = bool(input_handlers["sensor"])
        has_any_input = any(input_handlers.values())
        if not has_any_input:
            errors.append(QACheckError(
                type="L4_playability",
                message="No user input handlers – game is not interactive",
                severity="error",
            ))
        if any(mode in {"touch", "pointer"} for mode in (runtime_contract.input.required_modes if runtime_contract else ["touch", "pointer"])) and not has_touch and not has_pointer and not has_sensor:
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

        if self._has_width_only_font_scaling(code):
            errors.append(QACheckError(
                type="L4_playability",
                message="UI text scales from screen width only – likely oversized on wide or landscape mobile screens",
                severity="error",
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

    @staticmethod
    def _describe_ui_language(game_spec: Optional[GameSpec]) -> str:
        normalized = (game_spec.ui_language if game_spec else "en-US") or "en-US"
        if normalized == "zh-CN":
            return "zh-CN (Simplified Chinese)"
        return f"{normalized} (English)" if normalized == "en-US" else normalized

    @classmethod
    def _build_ui_language_instruction(cls, game_spec: Optional[GameSpec]) -> str:
        if not game_spec:
            return ""
        return (
            "UI LANGUAGE (NON-NEGOTIABLE):\n"
            f"- Visible UI language: {cls._describe_ui_language(game_spec)}\n"
            "- Keep all player-visible text in this language, including HUD labels, buttons, overlays, tutorials, and win/lose copy.\n"
            "- Keep code identifiers and internal keys in English.\n"
            "- Do not rewrite visible UI copy into English unless the visible UI language itself is English."
        )

    async def _complete_repair_prompt_raw_with_retry(
        self,
        *,
        prompt: str,
        code: str,
        step_key: str,
        request_timeout_s: int,
        max_tokens: int,
    ) -> str:
        allow_provider_fallback = True
        retry_ceiling = max(
            settings.LLM_LONG_GENERATION_MAX_TOKENS,
            12288,
        )
        retry_floor = max(
            min(max_tokens + 2048, retry_ceiling),
            self._estimate_syntax_repair_max_tokens(code, truncation_risk=True),
        )
        timeout_retry_attempts = 0
        timeout_retry_increment_s = 30
        timeout_retry_max_s = request_timeout_s
        hedge_after_s = self._syntax_repair_hedge_delay_s(request_timeout_s)

        text = await self._client.complete_with_truncation_retry(
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            step_key=step_key,
            stage="qa_checking",
            prefer_fast=True,
            request_timeout_s=request_timeout_s,
            overall_timeout_s=request_timeout_s,
            allow_provider_fallback=allow_provider_fallback,
            hedge_provider_fallback_after_s=hedge_after_s,
            response_size_hint="full_document",
            context_scope="request",
            compression_policy="qa_fix",
            truncation_retry_attempts=2,
            truncation_retry_increment=2048,
            truncation_retry_max_tokens=retry_ceiling,
            truncation_retry_min_tokens=retry_floor,
            timeout_retry_attempts=timeout_retry_attempts,
            timeout_retry_increment_s=timeout_retry_increment_s,
            timeout_retry_max_s=timeout_retry_max_s,
            provider_retry_attempts=0,
            provider_retry_on_timeout_errors=False,
        )
        return text

    async def _complete_repair_prompt_with_retry(
        self,
        *,
        prompt: str,
        code: str,
        step_key: str,
        request_timeout_s: int,
        max_tokens: int,
    ) -> str:
        from .code_generator import _extract_html

        text = await self._complete_repair_prompt_raw_with_retry(
            prompt=prompt,
            code=code,
            step_key=step_key,
            request_timeout_s=request_timeout_s,
            max_tokens=max_tokens,
        )
        return _extract_html(text)

    @staticmethod
    def _extract_script_repair_text(text: str) -> str:
        cleaned = (text or "").strip()
        cleaned = re.sub(r"^\s*```(?:javascript|js|html)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
        if "<script" in cleaned.lower():
            extracted = extract_script_content(f"<html><body>{cleaned}</body></html>")
            if extracted:
                return extracted
        if "<html" in cleaned.lower() or "<body" in cleaned.lower():
            raise ValueError("Script repair returned full HTML instead of raw JavaScript")
        return cleaned

    async def _complete_script_repair_prompt_with_retry(
        self,
        *,
        prompt: str,
        step_key: str,
        request_timeout_s: int,
        max_tokens: int,
    ) -> str:
        retry_ceiling = max(4096, max_tokens)
        text = await self._client.complete_with_truncation_retry(
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            step_key=step_key,
            stage="qa_checking",
            prefer_fast=True,
            request_timeout_s=request_timeout_s,
            overall_timeout_s=request_timeout_s,
            allow_provider_fallback=True,
            hedge_provider_fallback_after_s=self._syntax_repair_hedge_delay_s(request_timeout_s),
            response_size_hint="medium",
            context_scope="request",
            compression_policy="qa_fix",
            truncation_retry_attempts=1,
            truncation_retry_increment=1024,
            truncation_retry_max_tokens=retry_ceiling,
            truncation_retry_min_tokens=max_tokens,
            timeout_retry_attempts=0,
            provider_retry_attempts=0,
            provider_retry_on_timeout_errors=False,
        )
        return self._extract_script_repair_text(text)

    async def _fix_with_llm(
        self,
        code: str,
        errors: List[QACheckError],
        game_spec: Optional[GameSpec],
        runtime_contract: Optional[GameRuntimeContract],
        max_tokens: int,
        fix_round: int = 1,
        max_fix_rounds: int = 1,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
    ) -> str:
        syntax_errors = self._syntax_repair_errors(errors)
        # PR-10: when QA tiering is enabled, drop CREATIVE-tier issues so the
        # fixer does not chase aesthetic complaints and rewrite working code.
        # HARD and SOFT issues pass through unchanged.
        try:
            from ..config.settings import settings as _p1_settings
            if getattr(_p1_settings, "P1_QA_TIERING_ENABLED", False):
                from .qa_tiers import filter_fixable as _p1_filter_fixable
                fun_score = getattr(self, "_last_fun_score", None)
                base_threshold = float(
                    getattr(_p1_settings, "P1_QA_CREATIVE_PRESERVE_THRESHOLD", 7.0)
                )
                threshold = base_threshold
                # P2.2 adaptive threshold: nudge the preserve line per
                # (tier, game_type) when the flag is on. Always non-raising;
                # falls back to the static base threshold on any error.
                try:
                    if getattr(_p1_settings, "P2_ADAPTIVE_THRESHOLD_ENABLED", False):
                        from .p2_adaptive_thresholds import (
                            compute_adjusted_threshold as _p2_adjust,
                        )
                        _tier_val = getattr(game_spec, "generation_tier", None) if game_spec else None
                        _gtype_val = getattr(game_spec, "game_type", None) if game_spec else None
                        adjusted, delta = _p2_adjust(
                            base_threshold,
                            tier=_tier_val,
                            game_type=_gtype_val,
                        )
                        threshold = float(adjusted)
                        # P2.1 telemetry: emit base/adjusted/delta so the
                        # rollout can correlate fix-drop rates with threshold
                        # changes per cohort.
                        try:
                            from .p2_telemetry import emit as _p2_emit_local
                            _p2_emit_local(
                                "adaptive_threshold_applied",
                                tier=getattr(_tier_val, "value", _tier_val),
                                game_type=_gtype_val,
                                base=base_threshold,
                                adjusted=threshold,
                                delta=delta,
                                fun_score=fun_score,
                            )
                        except Exception:  # pragma: no cover
                            pass
                except Exception:  # pragma: no cover — fall back to base
                    threshold = base_threshold
                syntax_errors, dropped = _p1_filter_fixable(
                    syntax_errors,
                    fun_score=fun_score,
                    creative_preserve_threshold=threshold,
                )
                if dropped.get("CREATIVE_preserved"):
                    logger.info(
                        "qa_pipeline.pr10_creative_preserved fix_round=%s dropped=%s",
                        fix_round, dropped["CREATIVE_preserved"],
                    )
        except Exception:  # pragma: no cover — never block on filter issues
            pass

        if not syntax_errors:
            return code
        await task_memory.remember_qa_findings(
            self._current_task_id(),
            syntax_errors,
            repair_family=SYNTAX_REPAIR_FAMILY,
            fix_round=fix_round,
        )
        # PR-04: cap error list passed to LLM to avoid token bloat on failure-heavy rounds.
        # Keep first N by original order (already severity-sorted by upstream); append
        # summary line so the model knows there are more un-surfaced errors.
        _QA_FIX_MAX_ERRORS_PER_ROUND = 8
        if len(syntax_errors) > _QA_FIX_MAX_ERRORS_PER_ROUND:
            shown = syntax_errors[:_QA_FIX_MAX_ERRORS_PER_ROUND]
            remaining = len(syntax_errors) - _QA_FIX_MAX_ERRORS_PER_ROUND
            error_list = "\n".join(f"  - [{e.type}] {e.message}" for e in shown)
            error_list += f"\n  - (+{remaining} more errors omitted; fix the above first, remainder will be surfaced next round)"
        else:
            error_list = "\n".join(f"  - [{e.type}] {e.message}" for e in syntax_errors)

        # PR-04: on rounds >= 2 the model already has the runtime contract in its
        # prompt-cache from round 1 — send a short reference instead of the full block
        # to save 300-600 tokens per round.
        if fix_round >= 2 and runtime_contract is not None:
            runtime_contract_block = (
                f"\n- Runtime contract: unchanged from fix round 1 "
                f"(version={runtime_contract.version}, profile={runtime_contract.runtime_profile}). "
                "Continue to honor its canvas/state/input/safety requirements."
            )
        else:
            runtime_contract_block = self._build_runtime_contract_block(runtime_contract)
        prompt_key, prompt_template = self._resolve_syntax_repair_prompt(
            prompt_bundle_snapshot=prompt_bundle_snapshot,
        )
        prompt_values = {
            "error_list": error_list,
            "game_type": game_spec.game_type if game_spec else "unknown",
            "code": code,
            "runtime_contract_block": runtime_contract_block,
            "fix_round": fix_round,
            "max_fix_rounds": max_fix_rounds,
        }
        try:
            prompt = prompt_template.format_map(_SafePromptFormatDict(prompt_values))
        except Exception as exc:
            raise RuntimeError(
                f"QA fix prompt template is invalid for {prompt_key}: {exc}"
            ) from exc
        ui_language_instruction = self._build_ui_language_instruction(game_spec)
        if ui_language_instruction:
            prompt = "\n\n".join([ui_language_instruction, prompt])
        script_only_errors = [
            error
            for error in syntax_errors
            if "JavaScript syntax error in <script>:" in (error.message or "")
        ]
        script_content = extract_script_content(code or "")
        if script_content and len(script_only_errors) == len(syntax_errors):
            script_prompt_values = dict(prompt_values)
            script_prompt_values["code"] = script_content
            try:
                script_base_prompt = prompt_template.format_map(_SafePromptFormatDict(script_prompt_values))
            except Exception as exc:
                raise RuntimeError(
                    f"QA fix prompt template is invalid for {prompt_key}: {exc}"
                ) from exc
            script_prompt = "\n\n".join(
                part
                for part in (
                    ui_language_instruction or "",
                    "SCRIPT SYNTAX REPAIR (RETURN JAVASCRIPT ONLY):\n"
                    "- Fix only the JavaScript syntax inside the main inline <script> block.\n"
                    "- Return raw JavaScript only, with no <script> tags, HTML, markdown fences, or commentary.\n"
                    "- Preserve gameplay logic, identifiers, and visible UI strings unless a syntax fix requires a tiny edit.\n"
                    "- Do not rewrite unrelated HTML/CSS sections.\n"
                    f"- Current fix round: {fix_round}/{max_fix_rounds}.",
                    script_base_prompt,
                )
                if str(part).strip()
            )
            try:
                script_max_tokens = min(max_tokens, self._estimate_script_repair_max_tokens(script_content))
                script_timeout_s = self._estimate_script_repair_timeout_s(max_tokens=script_max_tokens)
                line_numbers = self._script_syntax_error_line_numbers(script_only_errors)
                if line_numbers:
                    start_line, end_line, script_window = self._extract_script_repair_window(
                        script_content,
                        line_numbers=line_numbers,
                    )
                    window_prompt = "\n\n".join(
                        part
                        for part in (
                            ui_language_instruction or "",
                            "SCRIPT WINDOW SYNTAX REPAIR (RETURN JAVASCRIPT ONLY):\n"
                            "- Fix only the syntax inside the provided original script line window.\n"
                            f"- The original script line range is {start_line}-{end_line}.\n"
                            "- Return only the corrected replacement for those lines as raw JavaScript.\n"
                            "- Do not return HTML, <script> tags, markdown fences, commentary, or the untouched lines outside this window.\n"
                            "- Preserve gameplay logic and identifiers unless a tiny syntax edit is required.",
                            script_base_prompt,
                        )
                        if str(part).strip()
                    )
                    try:
                        window_max_tokens = min(
                            script_max_tokens,
                            max(
                                1024,
                                min(3072, len(script_window.encode("utf-8")) // 3 + 768),
                            ),
                        )
                        window_timeout_s = script_timeout_s
                        repaired_window = await self._complete_script_repair_prompt_with_retry(
                            prompt=window_prompt,
                            step_key="qa_fix.syntax_structural",
                            request_timeout_s=window_timeout_s,
                            max_tokens=window_max_tokens,
                        )
                        if repaired_window.strip():
                            candidate_script = self._replace_script_repair_window(
                                script_content,
                                start_line=start_line,
                                end_line=end_line,
                                replacement=repaired_window,
                            )
                            if self._script_has_valid_syntax(candidate_script):
                                return replace_script_content(code, candidate_script)
                            logger.warning(
                                "Windowed script syntax repair returned invalid JavaScript; falling back to whole-script repair"
                            )
                    except LLMResponseTruncatedError:
                        raise
                    except Exception as window_exc:
                        logger.warning(
                            "Windowed script syntax repair failed, falling back to whole-script repair: %s",
                            window_exc,
                        )
                repaired_script = await self._complete_script_repair_prompt_with_retry(
                    prompt=script_prompt,
                    step_key="qa_fix.syntax_structural",
                    request_timeout_s=script_timeout_s,
                    max_tokens=script_max_tokens,
                )
                if repaired_script.strip():
                    return replace_script_content(code, repaired_script)
            except LLMResponseTruncatedError:
                raise
            except Exception as script_exc:
                logger.warning(
                    "Whole-script syntax repair failed; skipping full-document fallback for script-only syntax errors: %s",
                    script_exc,
                )
                return code
        try:
            request_timeout_s = self._estimate_repair_timeout_s(max_tokens=max_tokens)
            repaired = await self._complete_repair_prompt_with_retry(
                prompt=prompt,
                code=code,
                step_key="qa_fix.syntax_structural",
                request_timeout_s=request_timeout_s,
                max_tokens=max_tokens,
            )
            await task_memory.remember_code(
                self._current_task_id(),
                repaired,
                label="qa_fix_syntax_structural",
            )
            await task_memory.append_decision(
                self._current_task_id(),
                f"Applied QA fix family={SYNTAX_REPAIR_FAMILY} round={fix_round}/{max_fix_rounds}",
            )
            return repaired
        except LLMResponseTruncatedError:
            raise
        except Exception as e:
            logger.error(f"LLM auto-fix failed: {e}")
            return code
