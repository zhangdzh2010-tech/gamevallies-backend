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

import logging
import re
import time
import hashlib
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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
from ..services.llm_client import LLMClient
from ..services.llm_gateway import get_request_context
from ..services.task_memory import task_memory
from .mobile_layout import MOBILE_LAYOUT_BRIDGE_MARKER, has_short_edge_scaling
from .prompt_store import require_prompt
from .restart_entry import has_restart_entry
from .scoring_loop import has_visible_scoring_loop
from .section_patch import (
    PATCH_SECTION_BODY,
    PATCH_SECTION_HUD,
    PATCH_SECTION_SCRIPT,
    PATCH_SECTION_STYLE,
    PATCH_SCRIPT_ANCHOR_CONFIG,
    PATCH_SCRIPT_ANCHOR_GAME_LOOP,
    PATCH_SCRIPT_ANCHOR_INPUT,
    PATCH_SCRIPT_ANCHOR_LEVEL_DATA,
    SectionPatch,
    apply_section_patches,
    build_patch_protocol,
    build_section_context,
    ensure_structured_section_markers,
    find_incomplete_structured_markers,
    has_structured_section_markers,
    parse_patch_response,
    validate_patch_candidate,
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

REPAIR_SLOT_BY_FAMILY: Dict[str, str] = {
    "forbidden_api": "repair_forbidden_api",
    "input_contract": "repair_input_contract",
    "score_feedback": "repair_generic",
    "syntax_structural": "repair_syntax_structural",
    "terminal_state": "repair_terminal_state",
    "mobile_layout": "repair_mobile_layout",
    "runtime_startup": "repair_runtime_startup",
    "generic": "repair_generic",
}

REPAIR_FAMILY_PRIORITY: Tuple[str, ...] = (
    "syntax_structural",
    "forbidden_api",
    "input_contract",
    "score_feedback",
    "terminal_state",
    "mobile_layout",
    "runtime_startup",
    "generic",
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
    def _estimate_fix_max_tokens(code: str, *, prefer_fast: bool) -> int:
        approx_tokens = max(1024, len((code or "").encode("utf-8")) // (4 if prefer_fast else 3))
        buffer = 1024 if prefer_fast else 3072
        ceiling = 6144 if prefer_fast else 10240
        floor = 2048 if prefer_fast else 4096
        return min(ceiling, max(floor, approx_tokens + buffer))

    @staticmethod
    def _estimate_syntax_repair_max_tokens(code: str, *, truncation_risk: bool) -> int:
        approx_tokens = max(2048, len((code or "").encode("utf-8")) // 3)
        buffer = 5120 if truncation_risk else 3584
        ceiling = max(
            settings.LLM_LONG_GENERATION_MAX_TOKENS,
            settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX if truncation_risk else settings.LLM_GENERATION_TOKEN_BUDGET_STANDARD,
        )
        floor = 7168 if truncation_risk else 6144
        return min(ceiling, max(floor, approx_tokens + buffer))

    # ── Repair family timeout lookup (Phase 4 optimization) ────────────
    _REPAIR_FAMILY_TIMEOUT_S: dict[str, int] = {
        "forbidden_api": 45,
        "input_contract": 45,
        "score_feedback": 45,
        "terminal_state": 60,
        "mobile_layout": 60,
        "runtime_startup": 60,
        "syntax_structural": 120,
        "generic": 120,
    }

    @classmethod
    def _estimate_repair_timeout_s(
        cls,
        *,
        max_tokens: int,
        prefer_fast: bool,
        repair_family: str,
    ) -> int:
        if getattr(settings, "LLM_ADAPTIVE_REPAIR_TIMEOUTS_ENABLED", False):
            # Use family-based adaptive timeouts
            base = cls._REPAIR_FAMILY_TIMEOUT_S.get(repair_family, 120)
            # Scale up for very large token budgets
            if max_tokens >= 12288:
                base = max(base, settings.LLM_LONG_GENERATION_TIMEOUT_S)
            elif max_tokens >= 8192:
                base = max(base, 180)
            elif max_tokens >= 6144:
                base = max(base, min(base + 60, 180))
            return base

        # Legacy fixed timeout path
        timeout_key = "timeout.ai_engine.qa_fast_repair_s" if prefer_fast else "timeout.ai_engine.qa_repair_s"
        default_timeout = 120 if prefer_fast else 180
        timeout_s = get_timeout_int(timeout_key, default_timeout, min_value=30)
        if prefer_fast:
            if max_tokens >= 4096:
                timeout_s = max(timeout_s, 150)
            if max_tokens >= 6144:
                timeout_s = max(timeout_s, 180)
            return timeout_s

        if repair_family == "syntax_structural":
            timeout_s = max(timeout_s, 240)
        if max_tokens >= 8192:
            timeout_s = max(timeout_s, 240)
        if max_tokens >= 12288:
            timeout_s = max(timeout_s, settings.LLM_LONG_GENERATION_TIMEOUT_S)
        return timeout_s

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
    def _append_instruction_prompt(instructions: List[str], prompt_key: str) -> None:
        prompt_text = require_prompt(prompt_key).strip()
        if not prompt_text:
            return
        for line in prompt_text.splitlines():
            normalized = line.strip()
            if not normalized:
                continue
            instructions.append(normalized if normalized.startswith("-") else f"- {normalized}")

    @staticmethod
    def _build_targeted_fix_instructions(
        errors: List[QACheckError],
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> str:
        instructions: List[str] = []
        mobile_orientation = QAPipeline._resolve_mobile_layout_orientation(runtime_contract)
        mobile_reference = "landscape" if mobile_orientation == "landscape_first" else "portrait"
        mobile_requirement = "landscape-first" if mobile_orientation == "landscape_first" else "portrait-first"

        for error in errors:
            message = error.message.lower()
            if "no user input handlers" in message or "no registered user input handlers" in message:
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_input_handlers",
                )
            elif "visible state change after user interaction" in message:
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_visible_feedback",
                )
            elif "game-over state never set to true" in message or "terminal or completion state is never set" in message:
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_terminal_state",
                )
            elif "localstorage" in message or "sessionstorage" in message:
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_storage",
                )
            elif "blank screen" in message or "canvas never rendered" in message:
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_blank_screen",
                )
            elif "runtime js error" in message:
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_runtime_js_error",
                )
            elif any(
                token in message
                for token in (
                    "terminal state",
                    "game-over state",
                    "restart entry point",
                    "restart/reset function",
                    "requires state 'game_over'",
                )
            ):
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_terminal_state",
                )
            elif any(
                token in message
                for token in (
                    "viewport",
                    "portrait-first",
                    "portrait first",
                    "short-edge",
                    "short edge",
                    "landscape",
                    "width only",
                    "mobile",
                    "ui scale",
                )
            ):
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_mobile_layout",
                )
                instructions.extend([
                    "- Read both viewport width and viewport height inside a resize or orientation-change handler.",
                    f"- Keep a {mobile_reference} reference canvas and compute scaleX and scaleY against that reference size.",
                    f"- Derive uiScale from Math.min(scaleX, scaleY) or an equivalent short-edge fit, then center the {mobile_requirement} playfield.",
                    "- Do not size HUD text from screen width alone on wide mobile screens.",
                ])
            elif any(
                token in message
                for token in (
                    "visible scoring loop",
                    "visible score",
                    "score display",
                    "score hud",
                    "scoreboard",
                )
            ):
                instructions.extend([
                    "- Keep a visible score HUD, timer, or points display on screen during active play.",
                    "- Bind the HUD to real score-like state when available, and make it update inside the gameplay loop or an animation frame callback.",
                    "- Reset the visible score/timer display when the player restarts or returns to the ready state.",
                ])
            elif any(
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
                )
            ):
                QAPipeline._append_instruction_prompt(
                    instructions,
                    "prompt.qa_instruction_forbidden_api",
                )

        if not instructions:
            QAPipeline._append_instruction_prompt(
                instructions,
                "prompt.qa_instruction_generic",
            )

        unique_instructions: List[str] = []
        for instruction in instructions:
            if instruction not in unique_instructions:
                unique_instructions.append(instruction)
        return "\n".join(unique_instructions)

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
    def _resolve_mobile_layout_orientation(
        runtime_contract: Optional[GameRuntimeContract],
    ) -> str:
        orientation = str(
            getattr(getattr(runtime_contract, "mobile_layout", None), "orientation", None)
            or getattr(getattr(runtime_contract, "canvas", None), "orientation", None)
            or "portrait_first"
        ).strip()
        return "landscape_first" if orientation == "landscape_first" else "portrait_first"

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

    def _select_repair_scope(
        self,
        errors: List[QACheckError],
        *,
        force_full: bool = False,
    ) -> Tuple[str, List[QACheckError]]:
        if not errors:
            return "generic", []
        if force_full:
            return "generic", errors

        grouped: Dict[str, List[QACheckError]] = {}
        for error in errors:
            family = self._classify_error_family(error)
            grouped.setdefault(family, []).append(error)

        for family in REPAIR_FAMILY_PRIORITY:
            if family in grouped:
                return family, grouped[family]
        return "generic", errors

    def _resolve_repair_prompt(
        self,
        *,
        repair_family: str,
        prefer_fast: bool,
        prompt_bundle_snapshot: Optional[Dict[str, Any]],
    ) -> Tuple[str, str]:
        slot = REPAIR_SLOT_BY_FAMILY.get(repair_family)
        if slot:
            bundle_prompt = self._resolved_bundle_prompt(prompt_bundle_snapshot, slot)
            if bundle_prompt:
                return f"bundle.{slot}", bundle_prompt

        prompt_key = "prompt.qa_fix_fast" if prefer_fast else "prompt.qa_fix"
        return prompt_key, require_prompt(prompt_key)

    def _should_use_fast_fix(self, errors: List[QACheckError]) -> bool:
        if not errors or len(errors) > 2:
            return False

        known_issue_count = 0
        for error in errors:
            message = error.message.lower()
            if any(
                token in message
                for token in (
                    "no user input handlers",
                    "no registered user input handlers",
                    "game-over state never set to true",
                    "terminal or completion state is never set",
                    "localstorage",
                    "sessionstorage",
                    "blank screen",
                    "canvas never rendered",
                    "runtime js error",
                    "visible state change after user interaction",
                    "visible scoring loop",
                )
            ):
                known_issue_count += 1

        return known_issue_count == len(errors)

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

            current_signature = self._normalize_error_signature(result.errors)
            can_use_fast_fix = self._should_use_fast_fix(result.errors)
            if len(result.errors) == 1 and current_signature == previous_error_signature:
                repeated_single_issue_rounds += 1
            else:
                repeated_single_issue_rounds = 0

            if can_use_fast_fix and repeated_single_issue_rounds >= 2:
                logger.warning(
                    "QA circuit breaker triggered after repeated single-issue failures: %s",
                    result.errors[0].message if result.errors else "unknown",
                )
                break

            if attempt >= 1 and self._errors_look_like_truncation(result.errors):
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

            error_count_history.append(len(result.errors))
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
                    retry_cb(attempt + 1, max_retries, result.errors)
                except Exception:
                    pass
            logger.info(f"QA attempt {attempt} failed ({len(result.errors)} errors), triggering LLM auto-fix")
            repaired_code = await self.repair_code(
                code,
                result.errors,
                game_spec,
                runtime_contract=runtime_contract,
                prompt_bundle_snapshot=prompt_bundle_snapshot,
                fix_round=attempt + 1,
                max_fix_rounds=max_retries,
                force_full=repeated_single_issue_rounds >= 1,
            )
            repair_attempts += 1
            repaired_hash = self._code_hash(repaired_code)
            if repaired_hash == previous_code_hash:
                logger.warning("QA repair produced an unchanged code hash on round %s; stopping early", repair_attempts)
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
        force_full: bool = False,
    ) -> str:
        repaired = self._apply_deterministic_repairs(code)
        errors = self.normalize_issues(errors, default_blocking=True)
        if not self._client.is_enabled() or not errors:
            return repaired

        preserve_structured_markers = has_structured_section_markers(code)
        if preserve_structured_markers:
            repaired = ensure_structured_section_markers(repaired)

        force_full = force_full or self._should_force_full_repair(errors)
        repair_family, scoped_errors = self._select_repair_scope(errors, force_full=force_full)
        repaired = self._apply_family_deterministic_repairs(
            repaired,
            scoped_errors,
            repair_family=repair_family,
            runtime_contract=runtime_contract,
        )
        if self._can_short_circuit_repair(
            repaired,
            scoped_errors,
            repair_family=repair_family,
            runtime_contract=runtime_contract,
        ):
            return repaired
        prefer_fast = self._should_use_fast_fix(scoped_errors) and not force_full and repair_family not in {"syntax_structural", "generic"}
        effective_max_tokens = max_tokens if max_tokens is not None else self._estimate_fix_max_tokens(
            repaired,
            prefer_fast=prefer_fast,
        )
        if repair_family == "syntax_structural":
            effective_max_tokens = max(
                effective_max_tokens,
                self._estimate_syntax_repair_max_tokens(
                    repaired,
                    truncation_risk=self._errors_look_like_truncation(scoped_errors),
                ),
            )

        llm_fixed = await self._fix_with_llm(
            repaired,
            scoped_errors,
            game_spec,
            runtime_contract,
            max_tokens=effective_max_tokens,
            fix_round=fix_round,
            max_fix_rounds=max_fix_rounds,
            prefer_fast=prefer_fast,
            prompt_bundle_snapshot=prompt_bundle_snapshot,
            repair_family=repair_family,
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

        repaired = self._strip_storage_apis(repaired)

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

    @staticmethod
    def _build_compact_spec_summary(game_spec: GameSpec) -> str:
        mechanics = ", ".join(
            mechanic.type or ""
            for mechanic in (game_spec.core_mechanics or [])
            if (mechanic.type or "").strip()
        ) or game_spec.game_type
        entities = ", ".join(
            f"{entity.role}:{entity.name}"
            for entity in (game_spec.entities or [])[:6]
            if (entity.role or "").strip() and (entity.name or "").strip()
        ) or "player, obstacle, collectible"
        special_rules = "; ".join(
            rule.strip()
            for rule in (game_spec.special_rules or [])
            if (rule or "").strip()
        ) or "none"
        return (
            f"Game type: {game_spec.game_type}\n"
            f"Intent summary: {game_spec.intent_summary or game_spec.source_description or 'minimal mobile game'}\n"
            f"Core mechanics: {mechanics}\n"
            f"Theme: {game_spec.visual_style.theme}\n"
            f"Art style: {game_spec.visual_style.art_style}\n"
            f"Entities: {entities}\n"
            f"Special rules: {special_rules}\n"
            f"Original request: {game_spec.source_description or game_spec.intent_summary or '(empty)'}"
        )

    @staticmethod
    def _repair_family_supports_section_patch(repair_family: str) -> bool:
        return repair_family != "syntax_structural"

    @staticmethod
    def _repair_errors_touch_style(errors: List[QACheckError]) -> bool:
        tokens = ("style", "css", "font", "color", "colour", "ui text", "hud")
        for error in errors:
            message = (error.message or "").lower()
            if any(token in message for token in tokens):
                return True
        return False

    @classmethod
    def _select_patch_sections_for_repair(
        cls,
        repair_family: str,
        errors: List[QACheckError],
    ) -> tuple[str, ...]:
        sections: List[str] = [PATCH_SECTION_SCRIPT]
        if repair_family in {"mobile_layout", "runtime_startup", "generic"}:
            sections.insert(0, PATCH_SECTION_BODY)
        if repair_family == "mobile_layout" or cls._repair_errors_touch_style(errors):
            sections.insert(0, PATCH_SECTION_STYLE)
        deduped: List[str] = []
        for section in sections:
            if section not in deduped:
                deduped.append(section)
        return tuple(deduped)

    @staticmethod
    def _select_preferred_patch_targets_for_repair(
        repair_family: str,
        patch_sections: Sequence[str],
    ) -> tuple[str, ...]:
        candidates: List[str] = []
        if repair_family == "forbidden_api":
            candidates.extend([PATCH_SCRIPT_ANCHOR_CONFIG, PATCH_SCRIPT_ANCHOR_GAME_LOOP])
        elif repair_family == "input_contract":
            candidates.extend([PATCH_SCRIPT_ANCHOR_INPUT, PATCH_SCRIPT_ANCHOR_GAME_LOOP])
        elif repair_family == "score_feedback":
            candidates.extend([PATCH_SECTION_HUD, PATCH_SCRIPT_ANCHOR_GAME_LOOP, PATCH_SCRIPT_ANCHOR_LEVEL_DATA])
        elif repair_family == "terminal_state":
            candidates.extend([PATCH_SCRIPT_ANCHOR_GAME_LOOP, PATCH_SCRIPT_ANCHOR_INPUT])
        elif repair_family == "mobile_layout":
            candidates.extend([PATCH_SECTION_STYLE, PATCH_SECTION_HUD])
        elif repair_family == "runtime_startup":
            candidates.extend([PATCH_SCRIPT_ANCHOR_CONFIG, PATCH_SCRIPT_ANCHOR_GAME_LOOP, PATCH_SECTION_HUD])
        else:
            candidates.extend([PATCH_SCRIPT_ANCHOR_GAME_LOOP, PATCH_SCRIPT_ANCHOR_CONFIG, PATCH_SECTION_HUD])

        allowed_targets: List[str] = []
        allows_script = PATCH_SECTION_SCRIPT in patch_sections
        allows_body = PATCH_SECTION_BODY in patch_sections
        allows_style = PATCH_SECTION_STYLE in patch_sections
        for target in candidates:
            if target in {PATCH_SCRIPT_ANCHOR_CONFIG, PATCH_SCRIPT_ANCHOR_GAME_LOOP, PATCH_SCRIPT_ANCHOR_INPUT, PATCH_SCRIPT_ANCHOR_LEVEL_DATA} and not allows_script:
                continue
            if target == PATCH_SECTION_HUD and not allows_body:
                continue
            if target == PATCH_SECTION_STYLE and not allows_style:
                continue
            if target not in allowed_targets:
                allowed_targets.append(target)
        return tuple(allowed_targets)

    async def _rebuild_from_spec_for_syntax_recovery(
        self,
        *,
        code: str,
        errors: List[QACheckError],
        game_spec: Optional[GameSpec],
        runtime_contract: Optional[GameRuntimeContract],
    ) -> Optional[str]:
        if not game_spec:
            return None

        from .code_generator import CodeGenerator

        error_list = "\n".join(f"  - [{e.type}] {e.message}" for e in errors)
        implementation_budget = CodeGenerator._build_implementation_budget_block(
            game_spec,
            game_spec.source_description or game_spec.intent_summary,
            fallback_game_type="puzzle",
        )
        prompt_parts = [
            self._build_ui_language_instruction(game_spec),
            (
                "MINIMAL SPEC REBUILD (NON-NEGOTIABLE):\n"
                "- The previous candidate failed syntax validation repeatedly and must NOT be edited in place.\n"
                "- Rebuild the game from scratch using the spec and runtime contract below.\n"
                "- Return the smallest complete mobile game that satisfies the mechanic and runtime contract.\n"
                "- Use one canvas, one primary state object, one requestAnimationFrame loop, and at most one overlay screen.\n"
                "- Keep JavaScript compact, balanced, and syntactically complete.\n"
                "- Prefer 3-5 short prompts/levels max for classroom or knowledge-check requests.\n"
                "- Avoid long lesson-plan text, worksheets, scene managers, or multi-screen flows.\n"
                "- Return ONLY one complete HTML document that ends with </html>."
            ),
            implementation_budget,
            self._build_runtime_contract_block(runtime_contract),
            self._build_compact_spec_summary(game_spec),
            "Observed syntax/structural failures:",
            error_list,
        ]
        prompt = "\n\n".join(part for part in prompt_parts if part)
        try:
            max_tokens = max(
                6144,
                self._estimate_syntax_repair_max_tokens(
                    code,
                    truncation_risk=True,
                ),
            )
            request_timeout_s = self._estimate_repair_timeout_s(
                max_tokens=max_tokens,
                prefer_fast=False,
                repair_family="syntax_structural",
            )
            return await self._complete_repair_prompt_with_retry(
                prompt=prompt,
                code=code,
                step_key="qa_fix.syntax_rebuild",
                request_timeout_s=request_timeout_s,
                max_tokens=max_tokens,
                prefer_fast=False,
                repair_family="syntax_structural",
            )
        except Exception as exc:
            logger.error("Spec-driven syntax rebuild failed: %s", exc)
            return None

    async def _rewrite_with_simplified_budget(
        self,
        code: str,
        errors: List[QACheckError],
        game_spec: Optional[GameSpec],
        runtime_contract: Optional[GameRuntimeContract],
    ) -> str:
        error_list = "\n".join(f"  - [{e.type}] {e.message}" for e in errors)
        prompt_parts = [
            self._build_ui_language_instruction(game_spec),
            (
                "SIMPLIFIED STRUCTURAL REWRITE (NON-NEGOTIABLE):\n"
                "- The previous candidate still has JavaScript or structural syntax errors.\n"
                "- Rewrite the game into the smallest complete implementation that satisfies the listed issues and runtime contract.\n"
                "- Use one canvas, one primary state object, one requestAnimationFrame loop, and at most one overlay screen.\n"
                "- Remove optional subsystems, worksheets, lesson-plan text, scene managers, or multi-page flows before touching the core loop.\n"
                "- Keep the original core mechanic and visible UI language, but simplify supporting systems aggressively.\n"
                "- Output must parse as plain browser JavaScript with balanced blocks and complete statements.\n"
                "- Return ONLY one complete HTML document."
            ),
            self._build_runtime_contract_block(runtime_contract),
            f"Game type: {game_spec.game_type if game_spec else 'unknown'}",
            "Issues:",
            error_list,
            "Current code:",
            code,
        ]
        prompt = "\n\n".join(part for part in prompt_parts if part)
        try:
            max_tokens = max(
                6144,
                self._estimate_syntax_repair_max_tokens(
                    code,
                    truncation_risk=self._errors_look_like_truncation(errors),
                ),
            )
            request_timeout_s = self._estimate_repair_timeout_s(
                max_tokens=max_tokens,
                prefer_fast=False,
                repair_family="syntax_structural",
            )
            return await self._complete_repair_prompt_with_retry(
                prompt=prompt,
                code=code,
                step_key="qa_fix.syntax_structural",
                request_timeout_s=request_timeout_s,
                max_tokens=max_tokens,
                prefer_fast=False,
                repair_family="syntax_structural",
            )
        except Exception as exc:
            logger.error("Simplified syntax rewrite failed: %s", exc)
            return code

    async def _complete_repair_prompt_raw_with_retry(
        self,
        *,
        prompt: str,
        code: str,
        step_key: str,
        request_timeout_s: int,
        max_tokens: int,
        prefer_fast: bool,
        repair_family: str,
    ) -> str:
        allow_provider_fallback = bool(settings.LLM_PROVIDER_FAILOVER_ENABLED)
        retry_ceiling = 8192 if prefer_fast else max(
            settings.LLM_LONG_GENERATION_MAX_TOKENS,
            settings.LLM_GENERATION_TOKEN_BUDGET_COMPLEX,
        )
        retry_floor = max_tokens + (1024 if prefer_fast else 3072)
        if repair_family == "syntax_structural":
            retry_floor = max(
                retry_floor,
                self._estimate_syntax_repair_max_tokens(code, truncation_risk=True),
            )
        else:
            retry_floor = max(
                retry_floor,
                self._estimate_fix_max_tokens(code, prefer_fast=prefer_fast) + (1024 if prefer_fast else 2048),
            )
        timeout_retry_attempts = 1 if not prefer_fast else 0
        timeout_retry_increment_s = 30 if prefer_fast else 60
        timeout_retry_max_s = request_timeout_s + (60 if prefer_fast else 120)

        text = await self._client.complete_with_truncation_retry(
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            step_key=step_key,
            stage="qa_checking",
            prefer_fast=prefer_fast,
            request_timeout_s=request_timeout_s,
            overall_timeout_s=request_timeout_s,
            allow_provider_fallback=allow_provider_fallback,
            response_size_hint="medium" if prefer_fast else "xlarge",
            context_scope="task",
            compression_policy="qa_fix",
            truncation_retry_attempts=1,
            truncation_retry_increment=1024 if prefer_fast else 3072,
            truncation_retry_max_tokens=retry_ceiling,
            truncation_retry_min_tokens=retry_floor,
            timeout_retry_attempts=timeout_retry_attempts,
                timeout_retry_increment_s=timeout_retry_increment_s,
                timeout_retry_max_s=timeout_retry_max_s,
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
        prefer_fast: bool,
        repair_family: str,
    ) -> str:
        from .code_generator import _extract_html

        text = await self._complete_repair_prompt_raw_with_retry(
            prompt=prompt,
            code=code,
            step_key=step_key,
            request_timeout_s=request_timeout_s,
            max_tokens=max_tokens,
            prefer_fast=prefer_fast,
            repair_family=repair_family,
        )
        return _extract_html(text)

    async def _fix_with_llm(
        self,
        code: str,
        errors: List[QACheckError],
        game_spec: Optional[GameSpec],
        runtime_contract: Optional[GameRuntimeContract],
        max_tokens: int,
        fix_round: int = 1,
        max_fix_rounds: int = 1,
        prefer_fast: bool = False,
        prompt_bundle_snapshot: Optional[Dict[str, Any]] = None,
        repair_family: str = "generic",
    ) -> str:
        await task_memory.remember_qa_findings(
            self._current_task_id(),
            errors,
            repair_family=repair_family,
            fix_round=fix_round,
        )
        error_list = "\n".join(f"  - [{e.type}] {e.message}" for e in errors)
        game_type = game_spec.game_type if game_spec else "unknown"
        targeted_instructions = self._build_targeted_fix_instructions(errors, runtime_contract)
        runtime_contract_block = self._build_runtime_contract_block(runtime_contract)
        patch_sections = (
            self._select_patch_sections_for_repair(repair_family, errors)
            if self._repair_family_supports_section_patch(repair_family)
            else ()
        )
        preferred_targets = (
            self._select_preferred_patch_targets_for_repair(repair_family, patch_sections)
            if patch_sections
            else ()
        )
        code_context = (
            build_section_context(code, patch_sections, preferred_targets=preferred_targets)
            if patch_sections
            else code
        )

        prompt_key, prompt_template = self._resolve_repair_prompt(
            repair_family=repair_family,
            prefer_fast=prefer_fast,
            prompt_bundle_snapshot=prompt_bundle_snapshot,
        )
        prompt_values = {
            "error_list": error_list,
            "game_type": game_type,
            "code": code_context,
            "runtime_contract_block": runtime_contract_block,
            "fix_round": fix_round,
            "max_fix_rounds": max_fix_rounds,
            "targeted_instructions": targeted_instructions,
            "repair_family": repair_family,
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
        if patch_sections:
            prompt = "\n\n".join([
                build_patch_protocol(
                    patch_sections,
                    task_label=f"qa repair ({repair_family})",
                    preferred_targets=preferred_targets,
                ),
                prompt,
            ])
        try:
            request_timeout_s = self._estimate_repair_timeout_s(
                max_tokens=max_tokens,
                prefer_fast=prefer_fast,
                repair_family=repair_family,
            )
            step_key = f"qa_fix.{repair_family}" if repair_family and repair_family != "generic" else "qa_fix"
            if patch_sections:
                raw_response = await self._complete_repair_prompt_raw_with_retry(
                    prompt=prompt,
                    code=code,
                    step_key=step_key,
                    request_timeout_s=request_timeout_s,
                    max_tokens=max_tokens,
                    prefer_fast=prefer_fast,
                    repair_family=repair_family,
                )
                patches, full_html = parse_patch_response(raw_response, allowed_sections=patch_sections)
                if patches:
                    repaired = apply_section_patches(code, patches)
                elif full_html:
                    repaired = full_html
                else:
                    repaired = code
                validation_errors = validate_patch_candidate(
                    code,
                    repaired,
                    allowed_sections=patch_sections,
                )
                if validation_errors:
                    logger.warning(
                        "QA patch candidate rejected; keeping previous stable code: %s",
                        ", ".join(validation_errors),
                    )
                    repaired = code
            else:
                repaired = await self._complete_repair_prompt_with_retry(
                    prompt=prompt,
                    code=code,
                    step_key=step_key,
                    request_timeout_s=request_timeout_s,
                    max_tokens=max_tokens,
                    prefer_fast=prefer_fast,
                    repair_family=repair_family,
                )
            await task_memory.remember_code(
                self._current_task_id(),
                repaired,
                label=f"qa_fix_{repair_family}",
            )
            await task_memory.append_decision(
                self._current_task_id(),
                f"Applied QA fix family={repair_family} round={fix_round}/{max_fix_rounds}",
            )
            return repaired
        except Exception as e:
            logger.error(f"LLM auto-fix failed: {e}")
            return code

    def _apply_family_deterministic_repairs(
        self,
        code: str,
        errors: List[QACheckError],
        *,
        repair_family: str,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> str:
        repaired = code
        if repair_family == "input_contract" and self._needs_input_bridge(errors):
            repaired = self._inject_input_bridge(repaired)
        if repair_family == "score_feedback" and self._needs_score_feedback(errors):
            repaired = self._inject_score_feedback_bridge(repaired)
        if repair_family == "runtime_startup" and self._needs_touch_coordinate_guard(errors):
            repaired = self._inject_touch_coordinate_guard(repaired)
        if repair_family == "forbidden_api":
            repaired = self._sanitize_forbidden_api_usage(repaired, errors)
            repaired = self._strip_storage_apis(repaired)
        if repair_family == "terminal_state":
            repaired = self._inject_terminal_state_fallback(repaired)
        if repair_family == "mobile_layout":
            repaired = self._ensure_mobile_viewport_meta(repaired)
            repaired = self._inject_mobile_layout_bridge(repaired, runtime_contract=runtime_contract)
        return repaired

    @classmethod
    def _should_force_full_repair(cls, errors: List[QACheckError]) -> bool:
        if len(errors) <= 1:
            return False
        families = {cls._classify_error_family(error) for error in errors}
        if "syntax_structural" in families:
            return False
        return len(families) >= 3

    def _can_short_circuit_repair(
        self,
        code: str,
        errors: List[QACheckError],
        *,
        repair_family: str,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> bool:
        if repair_family != "input_contract" or not errors:
            if repair_family == "score_feedback" and errors:
                return self._has_score_bridge_marker(code) or has_visible_scoring_loop(code)
            if repair_family == "mobile_layout" and errors:
                orientation = self._resolve_mobile_layout_orientation(runtime_contract)
                return self._has_mobile_viewport_meta(code) and (
                    has_short_edge_scaling(code, orientation=orientation)
                    and not self._has_width_only_font_scaling(code)
                )
            if repair_family == "runtime_startup" and errors and self._needs_touch_coordinate_guard(errors):
                return not self._still_has_unsafe_touch_coordinate_access(code)
            if repair_family == "forbidden_api" and errors:
                return not self._still_contains_forbidden_api(code, errors)
            if repair_family == "terminal_state" and errors:
                return has_terminal_state_transition(code) and has_required_state_presence(code, "game_over")
            return False

        if not self._needs_input_bridge(errors):
            return False

        input_handlers = self._extract_input_handlers(code)
        has_observable_handlers = bool(
            input_handlers["touch"] or input_handlers["pointer"] or input_handlers["mouse"]
        )
        if not has_observable_handlers:
            return False

        if self._needs_visible_feedback_bridge(errors):
            return self._has_input_bridge_marker(code)
        return True

    @staticmethod
    def _needs_input_bridge(errors: List[QACheckError]) -> bool:
        for error in errors:
            message = (error.message or "").lower()
            if any(
                token in message
                for token in (
                    "no user input handlers",
                    "no registered user input handlers",
                    "primary touch or pointer gameplay handlers",
                    "game is not interactive",
                    "visible state change after user interaction",
                )
            ):
                return True
        return False

    @staticmethod
    def _needs_visible_feedback_bridge(errors: List[QACheckError]) -> bool:
        return any(
            "visible state change after user interaction" in (error.message or "").lower()
            for error in errors
        )

    @staticmethod
    def _needs_score_feedback(errors: List[QACheckError]) -> bool:
        return any(
            any(
                token in (error.message or "").lower()
                for token in (
                    "visible scoring loop",
                    "visible score",
                    "score display",
                    "score hud",
                    "scoreboard",
                )
            )
            for error in errors
        )

    @staticmethod
    def _needs_touch_coordinate_guard(errors: List[QACheckError]) -> bool:
        return any(
            any(
                token in (error.message or "").lower()
                for token in (
                    "reading 'clientx'",
                    "reading 'clienty'",
                    "touches[0]",
                )
            )
            for error in errors
        )

    @staticmethod
    def _has_input_bridge_marker(code: str) -> bool:
        return "__playforgeInputBridgeInstalled" in (code or "")

    @staticmethod
    def _has_score_bridge_marker(code: str) -> bool:
        return "__playforgeScoreBridgeInstalled" in (code or "")

    @staticmethod
    def _has_mobile_layout_bridge_marker(code: str) -> bool:
        return MOBILE_LAYOUT_BRIDGE_MARKER in (code or "")

    @staticmethod
    def _has_mobile_viewport_meta(code: str) -> bool:
        return re.search(
            r"<meta\b[^>]*name\s*=\s*['\"]viewport['\"]",
            code or "",
            re.IGNORECASE,
        ) is not None

    @classmethod
    def _ensure_mobile_viewport_meta(cls, code: str) -> str:
        if cls._has_mobile_viewport_meta(code):
            return code

        meta_tag = (
            '<meta name="viewport" content="width=device-width, initial-scale=1.0, '
            'maximum-scale=1.0, user-scalable=no">'
        )
        if re.search(r"<head\b[^>]*>", code, re.IGNORECASE):
            return re.sub(
                r"(<head\b[^>]*>)",
                rf"\1\n    {meta_tag}",
                code,
                count=1,
                flags=re.IGNORECASE,
            )
        return code

    @staticmethod
    def _inject_input_bridge(code: str) -> str:
        marker = "__playforgeInputBridgeInstalled"
        if marker in (code or ""):
            return code

        bridge = r"""
<script>
(() => {
  if (window.__playforgeInputBridgeInstalled) return;
  window.__playforgeInputBridgeInstalled = true;
  window.__playforgeInteractionFeedbackVersion = 0;
  window.__playforgeLastInputKind = '';
  window.__playforgeBridgeHandling = false;
  const BRIDGE_EVENT_FLAG = '__playforgeBridgeSynthetic';
  const BRIDGE_HANDLED_FLAG = '__playforgeBridgeHandled';
  const target = document.getElementById('gameCanvas') || document.querySelector('canvas') || document.body || document.documentElement;
  if (!target) return;
  const canvas = document.getElementById('gameCanvas') || document.querySelector('canvas');
  const knownStartNames = [
    'startGame', 'restartGame', 'resetGame', 'newGame', 'initGame',
    'beginGame', 'playGame', 'resumeGame', 'bootGame', 'launchGame',
    'start', 'restart', 'reset', 'begin', 'play'
  ];
  const startHints = /(start|begin|play|launch|ready|go|点击开始|开始游戏|开始|play again|restart|再来一局|重新开始)/i;

  const markEvent = (event, key) => {
    if (!event) return;
    try {
      Object.defineProperty(event, key, {
        value: true,
        configurable: true,
      });
      return;
    } catch (err) {
      /* ignore non-configurable event marker errors */
    }
    try {
      event[key] = true;
    } catch (err) {
      /* ignore direct event marker errors */
    }
  };

  const hasEventFlag = (event, key) => {
    try {
      return !!(event && event[key]);
    } catch (err) {
      return false;
    }
  };

  const dispatchSyntheticEvent = (node, event) => {
    if (!node || !event) return false;
    markEvent(event, BRIDGE_EVENT_FLAG);
    try {
      return !!node.dispatchEvent(event);
    } catch (err) {
      return false;
    }
  };

  const setKnownStateFlags = () => {
    const candidates = ['gameState', 'state', 'currentState', 'status', 'mode'];
    for (const key of candidates) {
      try {
        if (typeof window[key] === 'string' && /^(boot|ready|menu|start|idle)$/i.test(window[key])) {
          window[key] = 'playing';
        }
      } catch (err) {
        /* ignore state bridge errors */
      }
    }
    for (const key of ['gameStarted', 'started', 'isRunning']) {
      try {
        if (typeof window[key] === 'boolean') {
          window[key] = true;
        }
      } catch (err) {
        /* ignore state bridge errors */
      }
    }
    for (const key of ['gameOver', 'isGameOver']) {
      try {
        if (typeof window[key] === 'boolean') {
          window[key] = false;
        }
      } catch (err) {
        /* ignore state bridge errors */
      }
    }
  };

  const invokeKnownEntryPoint = () => {
    for (const name of knownStartNames) {
      try {
        const fn = window[name];
        if (typeof fn === 'function') {
          fn();
          return true;
        }
      } catch (err) {
        /* ignore entry-point bridge errors */
      }
    }
    try {
      const dynamicNames = Object.keys(window)
        .filter((name) => /(?:start|restart|reset|begin|play|launch|init|boot)/i.test(name))
        .slice(0, 12);
      for (const name of dynamicNames) {
        const fn = window[name];
        if (typeof fn === 'function') {
          fn();
          return true;
        }
      }
    } catch (err) {
      /* ignore dynamic entry-point scan errors */
    }
    return false;
  };

  const invokeVisibleDomStartControls = () => {
    try {
      const candidates = Array.from(document.querySelectorAll(
        'button, [role="button"], [id], [class], [data-action], .ui-overlay, .button'
      )).filter((node) => {
        if (!node || typeof node.getBoundingClientRect !== 'function') return false;
        const text = ((node.innerText || node.textContent || '') + ' ' + (node.id || '') + ' ' + (node.className || ''))
          .replace(/\s+/g, ' ')
          .trim();
        if (!startHints.test(text)) return false;
        const style = window.getComputedStyle ? window.getComputedStyle(node) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0')) {
          return false;
        }
        const rect = node.getBoundingClientRect();
        return Math.round(rect.width || 0) > 0 && Math.round(rect.height || 0) > 0;
      }).slice(0, 6);

      for (const node of candidates) {
        try {
          dispatchSyntheticEvent(
            node,
            new PointerEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'touch' })
          );
        } catch (err) {
          /* ignore pointerdown synthesis errors */
        }
        try {
          dispatchSyntheticEvent(
            node,
            new MouseEvent('mousedown', { bubbles: true, cancelable: true, button: 0, buttons: 1 })
          );
        } catch (err) {
          /* ignore mousedown synthesis errors */
        }
        try {
          dispatchSyntheticEvent(
            node,
            new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })
          );
        } catch (err) {
          /* ignore click synthesis errors */
        }
      }
      return candidates.length > 0;
    } catch (err) {
      /* ignore DOM start-control scan errors */
    }
    return false;
  };

  const paintVisibleFeedback = (inputKind) => {
    try {
      window.__playforgeInteractionFeedbackVersion += 1;
      window.__playforgeLastInputKind = inputKind || 'tap';
      if (canvas && typeof canvas.getContext === 'function') {
        const ctx = canvas.getContext('2d');
        if (ctx) {
          const stamp = window.__playforgeInteractionFeedbackVersion;
          const drawStamp = () => {
            try {
              const w = canvas.width || 320;
              const h = canvas.height || 480;
              ctx.save();
              ctx.globalAlpha = 0.98;
              ctx.fillStyle = 'rgba(12,18,28,0.88)';
              ctx.fillRect(10, 10, Math.min(180, Math.max(140, w * 0.42)), 56);
              ctx.fillStyle = '#ffffff';
              ctx.font = 'bold 16px sans-serif';
              ctx.fillText('Tap ' + stamp, 22, 38);
              ctx.strokeStyle = 'rgba(34,197,94,0.95)';
              ctx.lineWidth = 3;
              ctx.strokeRect(Math.max(12, w - 68), Math.max(12, h - 68), 48, 48);
              ctx.restore();
            } catch (err) {
              /* ignore draw stamp failures */
            }
          };
          drawStamp();
          window.requestAnimationFrame(drawStamp);
          window.setTimeout(drawStamp, 120);
          window.setTimeout(drawStamp, 280);
          window.setTimeout(drawStamp, 520);
        }
      }
      if (target && target.style) {
        target.style.outline = '3px solid rgba(255,255,255,0.9)';
        target.style.outlineOffset = '2px';
      }
      const existingBadge = document.getElementById('__playforgeInputBridgeBadge');
      if (existingBadge) {
        existingBadge.textContent = 'Tap ' + window.__playforgeInteractionFeedbackVersion;
        return;
      }
      const badge = document.createElement('div');
      badge.id = '__playforgeInputBridgeBadge';
      badge.textContent = 'Tap ' + window.__playforgeInteractionFeedbackVersion;
      badge.style.position = 'fixed';
      badge.style.left = '12px';
      badge.style.top = '12px';
      badge.style.zIndex = '99999';
      badge.style.padding = '6px 10px';
      badge.style.background = 'rgba(12,18,28,0.88)';
      badge.style.color = '#ffffff';
      badge.style.font = 'bold 14px sans-serif';
      badge.style.borderRadius = '10px';
      document.body && document.body.appendChild(badge);
    } catch (err) {
      /* ignore feedback paint failures */
    }
  };

  const dispatchBridgeEvents = () => {
    try {
      window.dispatchEvent(new CustomEvent('playforge:start-requested'));
      document.dispatchEvent(new CustomEvent('playforge:start-requested'));
      target.dispatchEvent(new CustomEvent('playforge:start-requested', { bubbles: true }));
    } catch (err) {
      /* ignore bridge custom events */
    }
  };

  const bridgeHandler = (event) => {
    const type = event && event.type ? String(event.type) : 'interaction';
    if (hasEventFlag(event, BRIDGE_EVENT_FLAG) || hasEventFlag(event, BRIDGE_HANDLED_FLAG)) {
      return;
    }
    markEvent(event, BRIDGE_HANDLED_FLAG);
    if (window.__playforgeBridgeHandling) {
      return;
    }
    window.__playforgeBridgeHandling = true;
    window.__playforgeLastInputAt = Date.now();
    try {
      paintVisibleFeedback(type);
      setKnownStateFlags();
      invokeKnownEntryPoint();
      invokeVisibleDomStartControls();
      dispatchBridgeEvents();
    } finally {
      window.__playforgeBridgeHandling = false;
    }
  };

  const attach = (node) => {
    if (!node) return;
    const options = { passive: true, capture: true };
    node.addEventListener('pointerdown', bridgeHandler, options);
    node.addEventListener('touchstart', bridgeHandler, options);
    node.addEventListener('click', bridgeHandler, options);
    node.addEventListener('keydown', bridgeHandler, options);
  };

  const bindInputHandlers = () => {
    attach(target);
    attach(document);
    attach(window);
  };

  bindInputHandlers();
})();
</script>
""".strip()

        if re.search(r"</body>", code, re.IGNORECASE):
            return re.sub(
                r"</body>",
                lambda _: bridge + "\n</body>",
                code,
                count=1,
                flags=re.IGNORECASE,
            )
        if re.search(r"</html>", code, re.IGNORECASE):
            return re.sub(
                r"</html>",
                lambda _: bridge + "\n</html>",
                code,
                count=1,
                flags=re.IGNORECASE,
            )
        return code + "\n" + bridge

    @classmethod
    def _inject_mobile_layout_bridge(
        cls,
        code: str,
        *,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> str:
        orientation = cls._resolve_mobile_layout_orientation(runtime_contract)
        if cls._has_mobile_layout_bridge_marker(code) or has_short_edge_scaling(code, orientation=orientation):
            return code
        if not re.search(r"<canvas\b", code, re.IGNORECASE):
            return code

        bridge = r"""
<script>
(() => {
  if (window.__playforgeMobileLayoutBridgeInstalled) return;
  window.__playforgeMobileLayoutBridgeInstalled = true;
  const canvas = document.getElementById('gameCanvas') || document.querySelector('canvas');
  if (!canvas) return;
  const designWidth = Math.max(1, Number(canvas.width) || Number(canvas.getAttribute('width')) || 360);
  const designHeight = Math.max(1, Number(canvas.height) || Number(canvas.getAttribute('height')) || 640);
  const applyResponsiveLayout = () => {
    const docEl = document.documentElement || document.body;
    const viewportWidth = Math.max((docEl && docEl.clientWidth) || 0, window.innerWidth || 0);
    const viewportHeight = Math.max((docEl && docEl.clientHeight) || 0, window.innerHeight || 0);
    const scaleX = viewportWidth / designWidth;
    const scaleY = viewportHeight / designHeight;
    const uiScale = Math.min(scaleX, scaleY);
    const shortEdge = Math.min(viewportWidth, viewportHeight);
    const renderWidth = Math.max(1, Math.round(designWidth * uiScale));
    const renderHeight = Math.max(1, Math.round(designHeight * uiScale));
    if (document.body) {
      document.body.style.margin = '0';
      document.body.style.minHeight = '100vh';
      document.body.style.overflow = 'hidden';
      document.body.style.position = 'relative';
      document.body.style.display = 'block';
    }
    canvas.style.position = 'absolute';
    canvas.style.width = renderWidth + 'px';
    canvas.style.height = renderHeight + 'px';
    canvas.style.left = Math.max(0, Math.round((viewportWidth - renderWidth) / 2)) + 'px';
    canvas.style.top = Math.max(0, Math.round((viewportHeight - renderHeight) / 2)) + 'px';
    canvas.style.maxWidth = 'none';
    canvas.style.maxHeight = 'none';
    window.__playforgeUiScale = uiScale;
    window.__playforgeShortEdge = shortEdge;
    window.__playforgePortraitUiScale = uiScale;
    window.__playforgePortraitShortEdge = shortEdge;
  };
  applyResponsiveLayout();
  window.addEventListener('resize', applyResponsiveLayout, { passive: true });
  window.addEventListener('orientationchange', applyResponsiveLayout, { passive: true });
})();
</script>
"""

        if re.search(r"</html>", code, re.IGNORECASE):
            return re.sub(
                r"</html>",
                lambda _: bridge + "\n</html>",
                code,
                count=1,
                flags=re.IGNORECASE,
            )
        return code + "\n" + bridge

    @staticmethod
    def _inject_score_feedback_bridge(code: str) -> str:
        marker = "__playforgeScoreBridgeInstalled"
        if marker in (code or ""):
            return code

        bridge = r"""
<script>
(() => {
  if (window.__playforgeScoreBridgeInstalled) return;
  window.__playforgeScoreBridgeInstalled = true;
  window.__playforgeScoreBridgeStartedAt = Date.now();
  const scoreKeys = ['score', 'points', 'point', 'combo', 'multiplier', 'coins', 'coin', 'time', 'timer', 'moves', 'steps'];
  const rootKeys = ['game', 'state', 'player', 'world', 'session', 'hud', 'ui', 'stats', 'runtime'];

  const ensureHud = () => {
    let hud = document.getElementById('playforgeScoreHud');
    if (hud) return hud;
    hud = document.createElement('div');
    hud.id = 'playforgeScoreHud';
    hud.style.position = 'fixed';
    hud.style.left = '12px';
    hud.style.top = '12px';
    hud.style.zIndex = '99998';
    hud.style.padding = '8px 12px';
    hud.style.borderRadius = '12px';
    hud.style.background = 'rgba(15, 23, 42, 0.88)';
    hud.style.color = '#f8fafc';
    hud.style.font = '700 14px/1.2 sans-serif';
    hud.style.letterSpacing = '0.02em';
    hud.style.boxShadow = '0 8px 24px rgba(15, 23, 42, 0.22)';
    hud.style.pointerEvents = 'none';
    (document.body || document.documentElement).appendChild(hud);
    return hud;
  };

  const normalizeValue = (value) => {
    if (typeof value === 'number' && Number.isFinite(value)) return Math.round(value);
    if (typeof value === 'string' && value.trim()) return value.trim();
    return null;
  };

  const probeObject = (root, labelPrefix = '') => {
    if (!root || typeof root !== 'object') return null;
    for (const key of scoreKeys) {
      try {
        const value = normalizeValue(root[key]);
        if (value !== null) {
          return { label: labelPrefix || key, value };
        }
      } catch (err) {
        /* ignore score probe errors */
      }
    }
    return null;
  };

  const readScoreSample = () => {
    for (const key of scoreKeys) {
      try {
        const value = normalizeValue(window[key]);
        if (value !== null) {
          return { label: key, value };
        }
      } catch (err) {
        /* ignore direct score probe errors */
      }
    }

    for (const rootKey of rootKeys) {
      try {
        const sample = probeObject(window[rootKey], rootKey);
        if (sample) return sample;
      } catch (err) {
        /* ignore nested score probe errors */
      }
    }

    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - window.__playforgeScoreBridgeStartedAt) / 1000));
    return { label: 'time', value: elapsedSeconds };
  };

  const formatLabel = (label) => {
    const normalized = String(label || 'score').toLowerCase();
    if (normalized === 'time' || normalized === 'timer') return 'Time';
    if (normalized === 'coin' || normalized === 'coins') return 'Coins';
    if (normalized === 'moves' || normalized === 'steps') return 'Moves';
    if (normalized === 'combo') return 'Combo';
    return 'Score';
  };

  const tick = () => {
    try {
      const hud = ensureHud();
      const sample = readScoreSample();
      hud.textContent = formatLabel(sample.label) + ': ' + sample.value;
    } catch (err) {
      /* ignore score bridge render errors */
    }
    window.requestAnimationFrame(tick);
  };

  tick();
})();
</script>
""".strip()

        if re.search(r"</body>", code, re.IGNORECASE):
            return re.sub(
                r"</body>",
                lambda _: bridge + "\n</body>",
                code,
                count=1,
                flags=re.IGNORECASE,
            )
        if re.search(r"</html>", code, re.IGNORECASE):
            return re.sub(
                r"</html>",
                lambda _: bridge + "\n</html>",
                code,
                count=1,
                flags=re.IGNORECASE,
            )
        return code + "\n" + bridge

    @staticmethod
    def _inject_touch_coordinate_guard(code: str) -> str:
        marker = "__playforgeResolveTouchPointInstalled"
        if marker in (code or ""):
            return code

        repaired = re.sub(
            r"\b([A-Za-z_$][\w$]*)\.touches\s*\?\s*\1\.touches\s*\[\s*0\s*\]\s*:\s*\1\b",
            r"window.__playforgeResolveTouchPoint(\1)",
            code,
        )
        repaired = re.sub(
            r"\b([A-Za-z_$][\w$]*)\.touches\s*\[\s*0\s*\]",
            r"window.__playforgeResolveTouchPoint(\1)",
            repaired,
        )

        helper = r"""
<script>
(() => {
  if (window.__playforgeResolveTouchPointInstalled) return;
  window.__playforgeResolveTouchPointInstalled = true;
  window.__playforgeResolveTouchPoint = function(evt) {
    return (evt && evt.touches && evt.touches[0])
      || (evt && evt.changedTouches && evt.changedTouches[0])
      || evt
      || { clientX: 0, clientY: 0 };
  };
})();
</script>
""".strip()

        if re.search(r"</body>", repaired, re.IGNORECASE):
            return re.sub(
                r"</body>",
                lambda _: helper + "\n</body>",
                repaired,
                count=1,
                flags=re.IGNORECASE,
            )
        if re.search(r"</html>", repaired, re.IGNORECASE):
            return re.sub(
                r"</html>",
                lambda _: helper + "\n</html>",
                repaired,
                count=1,
                flags=re.IGNORECASE,
            )
        return repaired + "\n" + helper

    @staticmethod
    def _still_has_unsafe_touch_coordinate_access(code: str) -> bool:
        if "__playforgeResolveTouchPointInstalled" in (code or ""):
            return False
        return bool(
            re.search(r"\b[A-Za-z_$][\w$]*\.touches\s*\[\s*0\s*\]", code)
            or re.search(r"\b([A-Za-z_$][\w$]*)\.touches\s*\?\s*\1\.touches\s*\[\s*0\s*\]\s*:\s*\1\b", code)
        )

    @staticmethod
    def _strip_storage_apis(code: str) -> str:
        """Deterministically remove localStorage/sessionStorage usage."""
        repaired = code
        # Replace getItem calls with empty string
        repaired = re.sub(r'localStorage\.getItem\([^)]*\)', '""', repaired)
        repaired = re.sub(r'sessionStorage\.getItem\([^)]*\)', '""', repaired)
        # Remove setItem / removeItem / clear calls entirely
        repaired = re.sub(r'localStorage\.(?:setItem|removeItem|clear)\([^)]*\)\s*;?', '', repaired)
        repaired = re.sub(r'sessionStorage\.(?:setItem|removeItem|clear)\([^)]*\)\s*;?', '', repaired)
        # Remove remaining bare references used as conditions
        repaired = re.sub(r'localStorage\b', '({})', repaired)
        repaired = re.sub(r'sessionStorage\b', '({})', repaired)
        return repaired

    @staticmethod
    def _inject_terminal_state_fallback(code: str) -> str:
        """If gameOver is declared but never set to true, inject a timeout fallback."""
        if "__playforgeTerminalFallback" in code:
            return code
        # Check: gameOver declared as false but never assigned true
        has_decl = bool(re.search(r'\bgameOver\s*=\s*false\b', code, re.IGNORECASE))
        has_set_true = bool(re.search(r'\bgameOver\s*=\s*true\b', code, re.IGNORECASE))
        if not has_decl or has_set_true:
            return code
        # Inject a 60-second timeout that sets gameOver = true
        fallback = (
            '\n<script>'
            '/* __playforgeTerminalFallback */'
            'setTimeout(function(){'
            'if(typeof gameOver!=="undefined"&&!gameOver){gameOver=true;}'
            '},60000);'
            '</script>\n'
        )
        # Insert before </body>
        if re.search(r'</body>', code, re.IGNORECASE):
            return re.sub(r'(</body>)', fallback + r'\1', code, count=1, flags=re.IGNORECASE)
        return code + fallback

    @staticmethod
    def _sanitize_forbidden_api_usage(code: str, errors: List[QACheckError]) -> str:
        repaired = code
        forbidden_names = QAPipeline._extract_forbidden_api_names(errors)

        if "Function" in forbidden_names:
            repaired = re.sub(r"\bnew\s+Function\s*\(", "(", repaired)
            repaired = re.sub(r"\b(?:window|globalThis|self|this)\.Function\s*\(", "(", repaired)
            repaired = re.sub(r"\bFunction\s*\(", "(", repaired)

        if "eval" in forbidden_names:
            repaired = re.sub(r"\b(?:window|globalThis|self|this)\.eval\s*\(", "(", repaired)
            repaired = re.sub(r"\beval\s*\(", "(", repaired)

        return repaired

    @staticmethod
    def _still_contains_forbidden_api(code: str, errors: List[QACheckError]) -> bool:
        for api_name in QAPipeline._extract_forbidden_api_names(errors):
            pattern = None
            if api_name == "Function":
                pattern = r"\bFunction\s*\("
            elif api_name == "eval":
                pattern = r"\beval\s*\("
            elif api_name:
                pattern = re.escape(api_name)

            if pattern and re.search(pattern, code):
                return True
        return False

    @staticmethod
    def _extract_forbidden_api_names(errors: List[QACheckError]) -> set[str]:
        names: set[str] = set()
        for error in errors:
            message = error.message or ""
            contract_match = re.search(r"forbids API usage:\s*([A-Za-z.]+)", message)
            detected_match = re.search(r"Forbidden API detected:\s*([A-Za-z.]+)", message)
            candidate = contract_match.group(1) if contract_match else (detected_match.group(1) if detected_match else None)
            if candidate:
                names.add(candidate.strip().rstrip("()"))
        return names
