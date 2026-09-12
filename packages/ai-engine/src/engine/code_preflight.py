"""Lightweight generated-code preflight checks before heavy QA/runtime loops."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

from ..api.models import GameRuntimeContract
from .section_patch import extract_script_content, replace_script_content

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = r"[A-Za-z_$][A-Za-z0-9_$]*"
_CALL_RE = re.compile(rf"(?<![\w$.])(?P<name>{_IDENTIFIER_RE})\s*\(")
_VALUE_REFS = (
    re.compile(rf"(?<![\w$.])(?P<name>{_IDENTIFIER_RE})\s*(?:[+\-*/%]=|===|==|!==|!=|<=|>=|<|>)"),
    re.compile(rf"(?<![\w$.])(?P<name>{_IDENTIFIER_RE})\s*(?:\+\+|--)"),
    re.compile(rf"[(,]\s*(?P<name>{_IDENTIFIER_RE})\s*(?=[,)])"),
)
_DECL_RE = re.compile(
    rf"\b(?:const|let|var|function|class)\s+(?P<name>{_IDENTIFIER_RE})\b"
)
_VAR_DECL_BLOCK_RE = re.compile(r"\b(?:const|let|var)\s+(?P<body>[^;]+)", re.MULTILINE)
_FOR_DECL_RE = re.compile(
    rf"\bfor\s*\(\s*(?:const|let|var)\s+(?P<lhs>[^;)]*?)(?:\s+of\s+|\s+in\s+|;)",
    re.MULTILINE,
)
_PARAM_BLOCK_RE = re.compile(
    r"(?:function(?:\s+[A-Za-z_$][A-Za-z0-9_$]*)?\s*\((?P<fn_params>[^)]*)\)|"
    r"\((?P<arrow_params>[^)]*)\)\s*=>|"
    r"(?<![\w$.])(?P<single_param>[A-Za-z_$][A-Za-z0-9_$]*)\s*=>)"
)
_CATCH_PARAM_RE = re.compile(rf"\bcatch\s*\(\s*(?P<name>{_IDENTIFIER_RE})\s*\)")
_CANVAS_DIMENSION_RE = re.compile(
    r"\bcanvas\s*\.\s*(width|height)\s*=\s*(?!0(?:\D|$))",
    re.IGNORECASE,
)
_TOUCH_ZERO_RE = re.compile(r"\b(?:touches|changedTouches)\s*\[\s*0\s*\]")
_TOUCH_LENGTH_RE = re.compile(r"\b(?:touches|changedTouches)\s*\.\s*length\b")
_TOUCH_FALLBACK_RE = re.compile(
    rf"(?<![\w$.])(?P<event>{_IDENTIFIER_RE})\.(?P<list>touches|changedTouches)"
    rf"\s*\?\s*(?P=event)\.(?P=list)\s*\[\s*0\s*\]\s*:\s*(?P=event)\b(?=\s*(?:[;,)\]}}]|$))"
)
_CTX_SET_TRANSFORM_RE = re.compile(r"\bctx\s*\.\s*setTransform\s*\(")
_CANVAS_GET_CONTEXT_RE = re.compile(r"\bcanvas\s*\.\s*getContext\s*\(\s*['\"]2d['\"]")
_CANVAS_PATH_CHAIN_RE = re.compile(
    r"\bctx\s*\.\s*roundRect\s*\([^;\n]*\)\s*\.\s*(fill|stroke)\s*\(",
    re.IGNORECASE,
)
_COLOR_ALPHA_SUFFIX_RE = re.compile(r"\+\s*['\"][0-9a-fA-F]{2}['\"]")
_DYNAMIC_COLOR_ALPHA_CONCAT_RE = re.compile(
    r"(?P<expr>(?:[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*|\[[^\]]+\]|\([^()]*\))*|\([^()]+\)))\s*\+\s*['\"](?P<alpha>[0-9a-fA-F]{2})['\"]"
)
_PRIMARY_READY_INPUT_LISTENER_RE = re.compile(
    r"\b(?:canvas|document|window)\s*\.\s*addEventListener\(\s*['\"]"
    r"(?P<event>pointerdown|touchstart|mousedown|click)['\"]\s*,\s*(?P<handler>"
    + _IDENTIFIER_RE
    + r")\b",
    re.IGNORECASE,
)
_PLAYING_ONLY_GUARD_RE = re.compile(
    r"if\s*\(\s*(?:gameState|state|currentState|status)\s*!==?\s*['\"]playing['\"]\s*\)\s*return\s*;",
    re.IGNORECASE,
)
_READY_TRANSITION_RE = re.compile(
    r"\b(?:startGame|restartGame)\s*\(|\b(?:gameState|state|currentState|status)\s*=\s*['\"]playing['\"]",
    re.IGNORECASE,
)
_NULL_INIT_DECL_RE = re.compile(
    rf"\b(?:const|let|var)\s+(?P<name>{_IDENTIFIER_RE})\s*=\s*null\b"
)
_LANE_HELPER_CALL_RE = re.compile(r"\.\s*(?P<name>laneX|laneCenterX|laneY)\s*\(")
_GRID_CELL_PROPERTY_READ_RE = re.compile(
    r"\bgrid\s*\[\s*(?P<row>[^\]]+?)\s*\]\s*\[\s*(?P<col>[^\]]+?)\s*\]\s*\.(?P<prop>[A-Za-z_$][A-Za-z0-9_$]*)"
)
_FUNCTION_BINDING_RE = re.compile(
    rf"\b(?P<kind>const|let|var)\s+(?P<name>{_IDENTIFIER_RE})\s*=\s*"
    rf"(?P<prefix>async\s+)?(?P<body>function\b|\([^)]*\)\s*=>|(?P<single>{_IDENTIFIER_RE})\s*=>)",
    re.MULTILINE,
)
_BARE_FUNCTION_ASSIGN_RE = re.compile(
    rf"(?<![\w$.])(?P<name>{_IDENTIFIER_RE})\s*=\s*"
    rf"(?P<prefix>async\s+)?(?P<body>function\b|\([^)]*\)\s*=>|(?P<single>{_IDENTIFIER_RE})\s*=>)",
    re.MULTILINE,
)
_BARE_ASSIGNMENT_RE = re.compile(
    rf"(?<![\w$.])(?P<name>{_IDENTIFIER_RE})\s*(?:[+\-*/%]=|=(?!=))"
)
_ARRAY_DESTRUCTURE_ASSIGN_RE = re.compile(
    r"(?<![\w$])\[(?P<body>[^\[\]]+)\]\s*=(?!=)"
)
_OBJECT_DESTRUCTURE_ASSIGN_RE = re.compile(
    r"\(\s*\{(?P<body>[^{}]+)\}\s*\)\s*=(?!=)"
)
_DECL_KEYWORD_PREFIX_RE = re.compile(r"\b(?:const|let|var|function|class)\s+$")
_SCRIPT_BLOCK_RE = re.compile(r"(<script\b[^>]*>)(?P<body>[\s\S]*?)(</script>)", re.IGNORECASE)
_BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_STRING_RE = re.compile(r"(['\"`])(?:\\.|(?!\1)[\s\S])*?\1")

_RESERVED_IDENTIFIERS = {
    "Array",
    "alert",
    "Boolean",
    "Date",
    "Error",
    "Infinity",
    "JSON",
    "Map",
    "Math",
    "NaN",
    "Number",
    "Object",
    "Promise",
    "RegExp",
    "Set",
    "String",
    "Symbol",
    "TypeError",
    "URL",
    "URLSearchParams",
    "addEventListener",
    "cancelAnimationFrame",
    "cancelIdleCallback",
    "clearInterval",
    "clearTimeout",
    "console",
    "ctx",
    "document",
    "event",
    "false",
    "globalThis",
    "history",
    "if",
    "Infinity",
    "localStorage",
    "location",
    "navigator",
    "new",
    "null",
    "isNaN",
    "isFinite",
    "encodeURI",
    "encodeURIComponent",
    "decodeURI",
    "decodeURIComponent",
    "parseFloat",
    "parseInt",
    "performance",
    "prompt",
    "requestAnimationFrame",
    "requestIdleCallback",
    "self",
    "setInterval",
    "setTimeout",
    "super",
    "this",
    "true",
    "undefined",
    "window",
}


@dataclass(frozen=True)
class CodePreflightIssue:
    code: str
    message: str


class CodePreflightValidator:
    """Catch high-frequency runtime hazards before contract/runtime QA."""

    _SAFE_GRID_HELPER = (
        "function __safeGridCell(gridRef, row, col) {\n"
        "  const rowBucket = gridRef && gridRef[row];\n"
        "  return rowBucket ? rowBucket[col] : null;\n"
        "}\n"
    )
    _SAFE_ALPHA_HELPER = (
        "function __withAlpha(color, alpha) {\n"
        "  const normalized = String(color || '').trim();\n"
        "  const safeAlpha = Math.max(0, Math.min(1, Number(alpha)));\n"
        "  if (!normalized) {\n"
        "    return normalized;\n"
        "  }\n"
        "  const shortHex = normalized.match(/^#([0-9a-fA-F]{3})$/);\n"
        "  const fullHex = normalized.match(/^#([0-9a-fA-F]{6})$/);\n"
        "  if (shortHex || fullHex) {\n"
        "    const hexBody = shortHex\n"
        "      ? shortHex[1].split('').map((part) => part + part).join('')\n"
        "      : fullHex[1];\n"
        "    const alphaHex = Math.round(safeAlpha * 255).toString(16).padStart(2, '0');\n"
        "    return `#${hexBody}${alphaHex}`;\n"
        "  }\n"
        "  const rgb = normalized.match(/^rgba?\\(([^)]+)\\)$/i);\n"
        "  if (rgb) {\n"
        "    const parts = rgb[1].split(',').map((part) => part.trim());\n"
        "    if (parts.length >= 3) {\n"
        "      return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${safeAlpha})`;\n"
        "    }\n"
        "  }\n"
        "  const hsl = normalized.match(/^hsla?\\(([^)]+)\\)$/i);\n"
        "  if (hsl) {\n"
        "    const parts = hsl[1].split(',').map((part) => part.trim());\n"
        "    if (parts.length >= 3) {\n"
        "      return `hsla(${parts[0]}, ${parts[1]}, ${parts[2]}, ${safeAlpha})`;\n"
        "    }\n"
        "  }\n"
        "  return normalized;\n"
        "}\n"
    )

    def validate(
        self,
        html_code: str,
        *,
        runtime_contract: Optional[GameRuntimeContract] = None,
    ) -> List[CodePreflightIssue]:
        script = extract_script_content(html_code or "") or ""
        if not script.strip():
            return []

        issues: List[CodePreflightIssue] = []
        issues.extend(self._check_canvas_dimensions(script, runtime_contract))
        issues.extend(self._check_canvas_context_order(script))
        issues.extend(self._check_canvas_path_chains(script))
        issues.extend(self._check_unsafe_color_alpha_concat(script))
        issues.extend(self._check_touch_access(script))
        issues.extend(self._check_ready_state_input_gate(script))
        issues.extend(self._check_nullable_runtime_objects(script))
        issues.extend(self._check_lane_helper_calls(script, runtime_contract))
        issues.extend(self._check_nested_grid_reads(script, runtime_contract))
        issues.extend(self._check_tdz_helpers(script))
        issues.extend(self._check_undefined_symbols(script))
        return self._dedupe(issues)

    def auto_repair(
        self,
        html_code: str,
        *,
        runtime_contract: Optional[GameRuntimeContract] = None,
        issues: Optional[Iterable[CodePreflightIssue]] = None,
    ) -> str:
        if not html_code or "<script" not in (html_code or "").lower():
            return html_code
        seen_codes = {issue.code for issue in (issues or [])}
        repaired = self._repair_nested_grid_reads(html_code, runtime_contract)
        if not seen_codes or "unsafe_touch_access" in seen_codes:
            repaired = self._repair_touch_fallback(repaired)
        if not seen_codes or "unsafe_color_alpha_concat" in seen_codes:
            repaired = self._repair_unsafe_color_alpha_concat(repaired)
        if not seen_codes or any(
            code.startswith("nullable_runtime_object:")
            and code.split(":", 1)[-1] in {"ctx", "context"}
            for code in seen_codes
        ):
            repaired = self._repair_null_canvas_context(repaired)
        symbol_issue_names = {
            code.split(":", 1)[-1]
            for code in seen_codes
            if code.startswith(("tdz_symbol:", "undefined_symbol:"))
        }
        if not seen_codes or symbol_issue_names:
            repaired = self._repair_declare_before_use(
                repaired,
                names=symbol_issue_names or None,
            )
        return repaired or html_code

    @staticmethod
    def _repair_touch_fallback(html_code: str) -> str:
        """Repair only the exact event-list ternary, preserving pointer behavior.

        A missing/empty touches list falls through to changedTouches (touchend),
        then the original pointer event. No synthetic coordinates or handlers.
        """
        def script_block(block):
            if re.search(r'\btype\s*=\s*[\'\"](?!module[\'\"]|(?:text|application)/javascript[\'\"])', block[1], re.I):
                return block[0]
            script = block.group('body')
            try:
                import esprima
                tokens = esprima.tokenize(script, {'range':True,'comment':True})
                excluded = [token.range for token in tokens
                    if token.type in {'String','Template','RegularExpression','LineComment','BlockComment'}]
            except Exception:
                # If lexical boundaries are unknown, do not risk editing data.
                return block[0]
            def is_code(match):
                return not any(start < match.end() and end > match.start() for start, end in excluded)
            replacements = [m for m in _TOUCH_FALLBACK_RE.finditer(script) if is_code(m)]
            reads = [m for m in _TOUCH_ZERO_RE.finditer(script) if is_code(m)]
            # The legacy validator recognizes a list-length guard globally.
            # Do not introduce one while leaving an unfamiliar access behind;
            # that would accidentally hide an unresolved preflight hazard.
            if any(not any(r.start() <= m.start() and m.end() <= r.end() for r in replacements) for m in reads):
                return block[0]
            def replace(match):
                if not is_code(match):
                    return match[0]
                event, kind = match['event'], match['list']
                other = 'changedTouches' if kind == 'touches' else 'touches'
                return (f'({event}.{kind} && {event}.{kind}.length ? {event}.{kind}[0] : '
                        f'({event}.{other} && {event}.{other}.length ? {event}.{other}[0] : {event}))')
            return block[1] + _TOUCH_FALLBACK_RE.sub(replace, script) + block[3]
        return _SCRIPT_BLOCK_RE.sub(script_block, html_code)

    @staticmethod
    def _repair_null_canvas_context(html_code: str) -> str:
        """Bind a real 2D context before the first rAF when ctx starts as null.

        Does not invent a fake context object. If no canvas exists, validation
        still fails. Scripts without an animation loop are left unchanged.
        """
        def script_block(block):
            script = block.group("body")
            if "requestAnimationFrame" not in script or "__bootCanvas" in script:
                return block[0]
            names = [
                match.group("name")
                for match in re.finditer(
                    r"\b(?:let|var)\s+(?P<name>ctx|context)\s*=\s*null\b",
                    script,
                )
            ]
            if not names:
                return block[0]
            loop = re.search(r"requestAnimationFrame\s*\(", script)
            if not loop:
                return block[0]
            boot = "".join(
                (
                    f"if (!{name}) {{"
                    " const __bootCanvas = (typeof canvas!=='undefined' && canvas)"
                    " ? canvas : document.querySelector('canvas');"
                    f" if (__bootCanvas) {name} = __bootCanvas.getContext('2d');"
                    "}"
                )
                for name in dict.fromkeys(names)
            )
            return block[1] + script[:loop.start()] + boot + script[loop.start():] + block[3]
        return _SCRIPT_BLOCK_RE.sub(script_block, html_code)

    def render_guidance(self, issues: Iterable[CodePreflightIssue]) -> str:
        visible = []
        seen = set()
        seen_codes = set()
        undefined_symbols: set[str] = set()
        tdz_symbols: set[str] = set()
        nullable_objects: set[str] = set()
        raw_messages: list[str] = []
        for issue in issues:
            key = (issue.code, issue.message)
            if key in seen:
                continue
            seen.add(key)
            seen_codes.add(issue.code)
            raw_messages.append(issue.message)
            if issue.code.startswith("undefined_symbol:"):
                undefined_symbols.add(issue.code.split(":", 1)[1])
            if issue.code.startswith("tdz_symbol:"):
                tdz_symbols.add(issue.code.split(":", 1)[1])
            if issue.code.startswith("nullable_runtime_object:"):
                nullable_objects.add(issue.code.split(":", 1)[1])
            visible.append(f"- {issue.message}")
        if "unsafe_nested_grid_read" in seen_codes:
            visible.append(
                "- For puzzle grids, replace direct reads like `grid[row][col].anim` with `const rowBucket = grid[row]; "
                "const cell = rowBucket && rowBucket[col];` and only read `cell.anim` after the null check passes."
            )
            visible.append(
                "- Prefer a dedicated helper such as "
                "`function getCell(row, col) { const rowBucket = grid[row]; return rowBucket ? rowBucket[col] : null; }` "
                "and then read `const cell = getCell(row, col); if (!cell) return;` before touching `cell.type`, "
                "`cell.fruit`, `cell.anim`, or neighbor properties."
            )
            visible.append(
                "- The regenerated code must remove every raw `grid[row][col].*` expression from the final HTML; "
                "after this retry, every grid property read should go through `getCell(...)` or an equivalent guarded "
                "`rowBucket/cell` pattern."
            )
        if "canvas_dimensions" in seen_codes:
            visible.append(
                "- In init/resize, explicitly assign canvas dimensions before the first render, for example "
                "`canvas.width = Math.round(viewportWidth); canvas.height = Math.round(viewportHeight);`."
            )
        if "unsafe_touch_access" in seen_codes:
            visible.append(
                "- Use a guarded touch extraction pattern such as "
                "`const touch = (e.touches && e.touches.length ? e.touches[0] : "
                "(e.changedTouches && e.changedTouches.length ? e.changedTouches[0] : null)); if (!touch) return;`."
            )
        if "unsafe_canvas_path_chain" in seen_codes:
            visible.append(
                "- Do not chain canvas path builders like `ctx.roundRect(...).fill()`. Use "
                "`ctx.beginPath(); ctx.roundRect(...); ctx.fill();` or `ctx.stroke();` as separate statements."
            )
        if "unsafe_color_alpha_concat" in seen_codes:
            visible.append(
                "- Do not build translucent gradient or fill colors with suffix concatenation such as `light.color + '80'`. "
                "Use explicit `rgba(...)` / `hsla(...)` values, or precompute full `#RRGGBBAA` literals before assignment."
            )
            visible.append(
                "- If a palette starts from `hsl(...)` or `rgb(...)`, keep the alpha inside the same color function, for example "
                "`hsla(0, 80%, 60%, 0.5)` instead of `hsl(0, 80%, 60%) + '80'`."
            )
        if "ready_state_input_gate" in seen_codes:
            visible.append(
                "- Do not gate the primary canvas/document pointer or touch handler behind `gameState !== 'playing'`; "
                "the first tap or pointerdown on the play surface should call `startGame()` or switch into `playing` immediately."
            )
            visible.append(
                "- Keep any intro overlay or start button optional for UX only. Runtime QA must still see an immediate visible "
                "state change when it dispatches the first input on the canvas or primary input target."
            )
        if nullable_objects:
            joined = ", ".join(f"`{name}`" for name in sorted(nullable_objects))
            visible.append(
                f"- Initialize nullable runtime objects like {joined} to safe defaults before requestAnimationFrame starts, "
                "or guard every property read and write until the object is created."
            )
            if {"ctx", "context"} & nullable_objects:
                visible.append(
                    "- Acquire a real 2D context before the loop starts, for example "
                    "`ctx = (typeof canvas !== 'undefined' && canvas ? canvas : document.querySelector('canvas')).getContext('2d'); "
                    "if (!ctx) return;`. Do not leave `let ctx = null` while `requestAnimationFrame` can run."
                )
            if any(name.lower().startswith("touch") for name in nullable_objects):
                visible.append(
                    "- For touch state, prefer a declared safe shape such as "
                    "`let touchState = { active: false, x: 0, y: 0, startX: 0, startY: 0 };` "
                    "instead of starting from null."
                )
            if any(name.lower().startswith("drag") for name in nullable_objects):
                visible.append(
                    "- For drag state, prefer a declared safe shape such as "
                    "`let dragTarget = { active: false, row: -1, col: -1, pointerId: null };` "
                    "instead of starting from null."
                )
        if {"viewWidth", "viewHeight", "viewW", "viewH"} & undefined_symbols:
            visible.append(
                "- If render or layout code uses viewWidth/viewHeight aliases, declare them from the live canvas dimensions "
                "inside init/resize first, for example `const viewWidth = canvas.width; const viewHeight = canvas.height;`."
            )
        if {"scaleX", "scaleY", "uiScale"} & undefined_symbols:
            visible.append(
                "- If layout logic reads `scaleX`, `scaleY`, or `uiScale`, declare those values inside resize/init from the "
                "reference canvas size before any HUD or draw code uses them."
            )
        if "generateBackgroundLayers" in undefined_symbols:
            visible.append(
                "- Declare `generateBackgroundLayers()` before the first call, or inline the background/parallax layer array "
                "creation during top-level init instead of calling an undefined helper."
            )
        if "render" in undefined_symbols or "update" in undefined_symbols:
            visible.append(
                "- Declare `render()` and `update()` before the first direct call or loop bootstrap that invokes them; "
                "do not call them from `loop()` until both functions are defined in scope."
            )
        hoist_names = tdz_symbols | (undefined_symbols & {"resize", "loop", "update", "render", "init"})
        if tdz_symbols or hoist_names:
            helpers = ", ".join(f"`{name}`" for name in sorted(tdz_symbols or hoist_names))
            visible.append(
                f"- Declare {helpers} as function declarations "
                "(not `const name = () =>` / `let name = function`). Function declarations hoist; "
                "`const`/`let` arrows throw TDZ when `init()` or a listener/rAF path runs "
                "before the binding is initialized."
            )
            visible.append(
                "- Register `addEventListener` and `requestAnimationFrame` callbacks only after "
                "those function declarations exist in the same script scope."
            )
        short_aliases = {
            name
            for name in undefined_symbols
            if name not in tdz_symbols and len(name) <= 3 and name not in _RESERVED_IDENTIFIERS
        }
        if short_aliases & {"nr", "nc", "dr", "dc", "gc", "sc"} or {"nr", "nc"} & undefined_symbols:
            visible.append(
                "- Declare short grid/loop aliases before use. For neighbor walks write "
                "`let nr, nc;` then `nr = r + dr; nc = c + dc;`, or "
                "`const [nr, nc] = [r + dr, c + dc];`. Do not read `nr` / `nc` as implicit globals."
            )
        if "line" in undefined_symbols:
            visible.append(
                "- Do not reference a bare `line` object unless it is declared in the same loop or scope, for example "
                "`const line = lines[index];` before reading `line.*`."
            )
        if "dot" in undefined_symbols:
            visible.append(
                "- Do not reference a bare `dot` alias outside the loop or callback that declares it. "
                "If particle or background dots are rendered, declare `const dot = dots[index];` in the same block "
                "before reading `dot.x`, `dot.y`, `dot.radius`, or `dot.alpha`."
            )
        if "star" in undefined_symbols:
            visible.append(
                "- Do not reference a bare `star` alias outside the loop or callback that declares it. "
                "If background stars or particles are rendered, declare `const star = stars[index];` in the same block "
                "before reading `star.x`, `star.y`, or `star.alpha`."
            )
        if "type" in undefined_symbols:
            visible.append(
                "- Do not reference a bare `type` variable. Read it from a declared entity or cell first, for example "
                "`const type = cell.type;` or use `cell.type` directly in the condition."
            )
        if "ctx" in undefined_symbols or "canvas_context_order" in seen_codes:
            visible.append(
                "- Acquire the 2D context immediately after creating the canvas, for example "
                "`const ctx = canvas.getContext('2d'); if (!ctx) return;`, and only then call `ctx.setTransform(...)`, "
                "`ctx.clearRect(...)`, or any other drawing API."
            )
        if any("unexpected token ." in message.lower() for message in raw_messages):
            visible.append(
                "- Fix any stray leading `.` tokens or broken property chains; do not start a new line with `.foo(...)`, "
                "and keep every chained call as a complete JavaScript statement."
            )
        if not visible:
            return ""
        return "\n".join(
            [
                "PRE-FLIGHT CORRECTIONS (MANDATORY):",
                *visible,
                "- Regenerate the full game so these issues are fixed in the first output.",
                "- Do not leave helper names, dimension values, or touch variables undeclared.",
            ]
        )

    @staticmethod
    def _dedupe(issues: Iterable[CodePreflightIssue]) -> List[CodePreflightIssue]:
        deduped: List[CodePreflightIssue] = []
        seen = set()
        for issue in issues:
            key = (issue.code, issue.message)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)
        return deduped

    def _repair_nested_grid_reads(
        self,
        html_code: str,
        runtime_contract: Optional[GameRuntimeContract],
    ) -> str:
        profile = str(getattr(runtime_contract, "runtime_profile", "") or "").strip().lower()
        if "puzzle_grid" not in profile:
            return html_code

        script = extract_script_content(html_code or "")
        if script is None:
            return html_code
        replaced_any = False

        def _replace(match: re.Match[str]) -> str:
            nonlocal replaced_any
            suffix = script[match.end(): match.end() + 4]
            if re.match(r"\s*=", suffix):
                return match.group(0)
            row_expr = (match.group("row") or "").strip()
            col_expr = (match.group("col") or "").strip()
            prop_name = (match.group("prop") or "").strip()
            if not row_expr or not col_expr or not prop_name:
                return match.group(0)
            replaced_any = True
            return f"(__safeGridCell(grid, {row_expr}, {col_expr})?.{prop_name})"

        repaired_script = _GRID_CELL_PROPERTY_READ_RE.sub(_replace, script)
        if not replaced_any:
            return html_code
        if "__safeGridCell(" not in repaired_script:
            repaired_script = self._SAFE_GRID_HELPER + repaired_script.lstrip()
        return replace_script_content(html_code, repaired_script)

    def _repair_unsafe_color_alpha_concat(self, html_code: str) -> str:
        script = extract_script_content(html_code or "")
        if script is None:
            return html_code
        if not _COLOR_ALPHA_SUFFIX_RE.search(script):
            return html_code

        replaced_any = False
        repaired_lines: list[str] = []
        for line in script.splitlines(keepends=True):
            stripped = line.strip()
            if (
                not stripped
                or not _COLOR_ALPHA_SUFFIX_RE.search(line)
                or ("addColorStop" not in line and "fillStyle" not in line and "strokeStyle" not in line)
            ):
                repaired_lines.append(line)
                continue
            if re.search(
                r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})['\"]\s*\+\s*['\"][0-9a-fA-F]{2}['\"]",
                line,
            ):
                repaired_lines.append(line)
                continue

            def _replace(match: re.Match[str]) -> str:
                nonlocal replaced_any
                alpha = int(match.group("alpha"), 16) / 255.0
                alpha_text = f"{alpha:.3f}".rstrip("0").rstrip(".")
                expr = (match.group("expr") or "").strip()
                if not expr:
                    return match.group(0)
                replaced_any = True
                return f"__withAlpha({expr}, {alpha_text})"

            repaired_lines.append(_DYNAMIC_COLOR_ALPHA_CONCAT_RE.sub(_replace, line))

        if not replaced_any:
            return html_code

        repaired_script = "".join(repaired_lines)
        if "function __withAlpha(" not in repaired_script:
            repaired_script = self._SAFE_ALPHA_HELPER + repaired_script.lstrip()
        return replace_script_content(html_code, repaired_script)

    @staticmethod
    def _check_canvas_dimensions(
        script: str,
        runtime_contract: Optional[GameRuntimeContract],
    ) -> List[CodePreflightIssue]:
        if not re.search(r"\bcanvas\b", script):
            return []
        found = {match.group(1).lower() for match in _CANVAS_DIMENSION_RE.finditer(script)}
        if {"width", "height"}.issubset(found):
            return []
        orientation = (
            getattr(getattr(runtime_contract, "canvas", None), "orientation", None)
            or getattr(getattr(runtime_contract, "mobile_layout", None), "orientation", None)
            or "mobile"
        )
        return [
            CodePreflightIssue(
                code="canvas_dimensions",
                message=(
                    "Set explicit non-zero canvas.width and canvas.height during init/resize "
                    f"before the first render for the {orientation} layout."
                ),
            )
        ]

    @staticmethod
    def _check_touch_access(script: str) -> List[CodePreflightIssue]:
        if not _TOUCH_ZERO_RE.search(script):
            return []
        if _TOUCH_LENGTH_RE.search(script):
            return []
        if CodePreflightValidator._touch_reads_have_element_guards(script):
            return []
        return [
            CodePreflightIssue(
                code="unsafe_touch_access",
                message=(
                    "Guard touches[0]/changedTouches[0] with a length or element-existence check and fall back to the event itself "
                    "before reading clientX/clientY."
                ),
            )
        ]

    @staticmethod
    def _touch_reads_have_element_guards(script: str) -> bool:
        """Accept equivalent existence guards, without guessing boolean precedence.

        Recognize e.touches && e.touches[0] ? e.touches[0].clientX : fallback
        from the AST. Every touch read must be covered; an unrelated guarded
        read must not hide an unsafe one. Unsupported syntax stays conservative.
        """
        try:
            import esprima
            tree = esprima.parseScript(script, {'range': True}).toDict()
        except Exception:
            return False

        def touch_list(node):
            return (isinstance(node, dict) and node.get('type') == 'MemberExpression'
                    and not node.get('computed') and node.get('property', {}).get('name') in {'touches', 'changedTouches'})

        def zero_read(node):
            return (isinstance(node, dict) and node.get('type') == 'MemberExpression'
                    and node.get('computed') and node.get('property', {}).get('type') == 'Literal'
                    and node['property'].get('value') == 0 and touch_list(node.get('object')))

        def owner(node):
            if touch_list(node) and node.get('object', {}).get('type') == 'Identifier':
                return (node['object']['name'], node['property']['name'])
            return None

        reads, guarded = set(), set()
        def visit(node):
            if isinstance(node, list):
                for item in node: visit(item)
            elif isinstance(node, dict):
                if zero_read(node): reads.add(tuple(node['range']))
                if node.get('type') == 'ConditionalExpression':
                    test, value = node['test'], node['consequent']
                    if (test.get('type') == 'LogicalExpression' and test.get('operator') == '&&'
                            and owner(test.get('left')) and zero_read(test.get('right'))
                            and value.get('type') == 'MemberExpression' and zero_read(value.get('object'))
                            and not value.get('computed') and value.get('property', {}).get('name') in {'clientX', 'clientY'}
                            and owner(test['left']) == owner(test['right']['object']) == owner(value['object']['object'])):
                        guarded.update((tuple(test['right']['range']), tuple(value['object']['range'])))
                for value in node.values():
                    if isinstance(value, (dict, list)): visit(value)
        visit(tree)
        return reads <= guarded

    @staticmethod
    def _check_ready_state_input_gate(script: str) -> List[CodePreflightIssue]:
        for listener_match in _PRIMARY_READY_INPUT_LISTENER_RE.finditer(script):
            handler_name = (listener_match.group("handler") or "").strip()
            if not handler_name or handler_name in {"startGame", "restartGame"}:
                continue
            handler_body = CodePreflightValidator._extract_named_handler_body(script, handler_name)
            if not handler_body:
                continue
            if not _PLAYING_ONLY_GUARD_RE.search(handler_body):
                continue
            if _READY_TRANSITION_RE.search(handler_body):
                continue
            return [
                CodePreflightIssue(
                    code="ready_state_input_gate",
                    message=(
                        f"Primary input handler `{handler_name}` returns unless the game is already in `playing`; "
                        "let the first canvas/document interaction start play or produce an immediate visible state change."
                    ),
                )
            ]
        return []

    @staticmethod
    def _check_canvas_context_order(script: str) -> List[CodePreflightIssue]:
        set_transform_match = _CTX_SET_TRANSFORM_RE.search(script)
        if not set_transform_match:
            return []
        get_context_match = _CANVAS_GET_CONTEXT_RE.search(script)
        if get_context_match and get_context_match.start() < set_transform_match.start():
            return []
        return [
            CodePreflightIssue(
                code="canvas_context_order",
                message=(
                    "Initialize `ctx = canvas.getContext('2d')` before any `ctx.setTransform(...)` call, "
                    "or guard the resize path when the 2D context is still null."
                ),
            )
        ]

    @staticmethod
    def _check_canvas_path_chains(script: str) -> List[CodePreflightIssue]:
        match = _CANVAS_PATH_CHAIN_RE.search(script)
        if not match:
            return []
        method = match.group(1) or "fill"
        return [
            CodePreflightIssue(
                code="unsafe_canvas_path_chain",
                message=(
                    f"Do not chain `ctx.roundRect(...).{method}()`; call `ctx.roundRect(...)` and `ctx.{method}()` "
                    "as separate statements because the path builder does not return a fillable object."
                ),
            )
        ]

    @staticmethod
    def _check_unsafe_color_alpha_concat(script: str) -> List[CodePreflightIssue]:
        for raw_line in script.splitlines():
            line = raw_line.strip()
            if not line or not _COLOR_ALPHA_SUFFIX_RE.search(line):
                continue
            if "addColorStop" not in line and "fillStyle" not in line and "strokeStyle" not in line:
                continue
            if re.search(
                r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})['\"]\s*\+\s*['\"][0-9a-fA-F]{2}['\"]",
                line,
            ):
                continue
            return [
                CodePreflightIssue(
                    code="unsafe_color_alpha_concat",
                    message=(
                        "Do not create translucent canvas colors by concatenating alpha suffixes such as `+ '80'` "
                        "inside gradient or fill assignments; build the color with explicit rgba/hsla or a full hex literal."
                    ),
                )
            ]
        return []

    @staticmethod
    def _check_nullable_runtime_objects(script: str) -> List[CodePreflightIssue]:
        issues: List[CodePreflightIssue] = []
        animation_loop_present = "requestAnimationFrame" in script
        for match in _NULL_INIT_DECL_RE.finditer(script):
            name = match.group("name")
            if not name:
                continue
            deref_re = re.compile(rf"\b{re.escape(name)}\s*\.")
            if not deref_re.search(script):
                continue
            if not animation_loop_present:
                continue
            if CodePreflightValidator._has_nullable_guard_pattern(script, name):
                continue
            if CodePreflightValidator._has_top_level_non_null_assignment_before_loop(
                script,
                name,
                search_start=match.end(),
            ):
                continue
            if name in {"ctx", "context"}:
                message = (
                    f"Do not leave `{name}` initialized as null while the main loop can run; "
                    "create a safe default canvas context before the loop starts."
                )
            else:
                message = (
                    f"Do not leave `{name}` initialized as null while the main loop can run; "
                    f"create a safe default object before the first animation frame or guard every `{name}.*` access."
                )
            issues.append(
                CodePreflightIssue(
                    code=f"nullable_runtime_object:{name}",
                    message=message,
                )
            )
        return issues

    @staticmethod
    def _has_nullable_guard_pattern(script: str, name: str) -> bool:
        escaped = re.escape(name)
        guard_patterns = (
            rf"\bif\s*\(\s*!{escaped}\s*\)\s*(?:return|continue|break)\b",
            rf"\bif\s*\(\s*[^)]*!\s*{escaped}\b[^)]*\)\s*(?:return|continue|break)\b",
            rf"\bif\s*\(\s*{escaped}\s*\)",
            rf"\bif\s*\(\s*[^)]*\b{escaped}\b[^)]*\)",
            rf"\bif\s*\(\s*{escaped}\s*!=\s*null\s*\)",
            rf"\bif\s*\(\s*{escaped}\s*!==\s*null\s*\)",
            rf"\b{escaped}\s*&&\s*{escaped}\s*\.",
            rf"\b{escaped}\s*\?\s*{escaped}\s*\.",
            rf"\b{escaped}\s*\?\.",
        )
        return any(re.search(pattern, script) for pattern in guard_patterns)

    @staticmethod
    def _has_top_level_non_null_assignment_before_loop(
        script: str,
        name: str,
        *,
        search_start: int = 0,
    ) -> bool:
        loop_start = script.find("requestAnimationFrame", search_start)
        if loop_start == -1 or loop_start <= search_start:
            return False
        segment = script[search_start:loop_start]
        assignment_re = re.compile(
            rf"\b{re.escape(name)}\s*=\s*(?!null\b|undefined\b)",
            re.IGNORECASE,
        )
        depth_paren = 0
        depth_brace = 0
        depth_bracket = 0
        quote: Optional[str] = None
        escaped = False

        for index, ch in enumerate(segment):
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue
            if ch in {"'", '"', "`"}:
                quote = ch
                continue
            if ch == "(":
                depth_paren += 1
                continue
            if ch == ")" and depth_paren > 0:
                depth_paren -= 1
                continue
            if ch == "{":
                depth_brace += 1
                continue
            if ch == "}" and depth_brace > 0:
                depth_brace -= 1
                continue
            if ch == "[":
                depth_bracket += 1
                continue
            if ch == "]" and depth_bracket > 0:
                depth_bracket -= 1
                continue
            if depth_paren == 0 and depth_brace == 0 and depth_bracket == 0:
                if assignment_re.match(segment, index):
                    return True
        return False

    @staticmethod
    def _check_lane_helper_calls(
        script: str,
        runtime_contract: Optional[GameRuntimeContract],
    ) -> List[CodePreflightIssue]:
        profile = str(getattr(runtime_contract, "runtime_profile", "") or "").strip().lower()
        if "lane" not in profile:
            return []

        issues: List[CodePreflightIssue] = []
        for match in _LANE_HELPER_CALL_RE.finditer(script):
            name = match.group("name")
            if not name:
                continue
            definition_patterns = (
                rf"\bfunction\s+{re.escape(name)}\s*\(",
                rf"\b{re.escape(name)}\s*[:=]\s*function\b",
                rf"\b{re.escape(name)}\s*[:=]\s*\([^)]*\)\s*=>",
                rf"\b{re.escape(name)}\s*\([^)]*\)\s*\{{",
            )
            if any(re.search(pattern, script) for pattern in definition_patterns):
                continue
            issues.append(
                CodePreflightIssue(
                    code=f"undefined_lane_helper_call:{name}",
                    message=(
                        f"Do not call `{name}()` unless it is explicitly declared; compute lane x/y positions "
                        "from a declared helper or array lookup before the main loop runs."
                    ),
                )
            )
        return issues

    @staticmethod
    def _check_nested_grid_reads(
        script: str,
        runtime_contract: Optional[GameRuntimeContract],
    ) -> List[CodePreflightIssue]:
        profile = str(getattr(runtime_contract, "runtime_profile", "") or "").strip().lower()
        if "puzzle_grid" not in profile:
            return []

        issues: List[CodePreflightIssue] = []
        for match in _GRID_CELL_PROPERTY_READ_RE.finditer(script):
            row_expr = re.sub(r"\s+", "", match.group("row") or "")
            col_expr = re.sub(r"\s+", "", match.group("col") or "")
            prop_name = match.group("prop") or "value"
            line_start = script.rfind("\n", 0, match.start()) + 1
            line_end = script.find("\n", match.end())
            if line_end == -1:
                line_end = len(script)
            line = script[line_start:line_end]
            compact_line = re.sub(r"\s+", "", line)
            if CodePreflightValidator._has_safe_grid_access_context(
                script=script,
                match_start=match.start(),
                compact_line=compact_line,
                row_expr=row_expr,
                col_expr=col_expr,
            ):
                continue
            issues.append(
                CodePreflightIssue(
                    code="unsafe_nested_grid_read",
                    message=(
                        f"Guard nested grid reads before accessing `grid[row][col].{prop_name}`; "
                        "check that both the row bucket and cell exist, or read through a safe helper first."
                    ),
                )
            )
        return issues

    def _check_undefined_symbols(self, script: str) -> List[CodePreflightIssue]:
        scan_script = self._sanitize_for_symbol_scan(script)
        declared = self._collect_declared_symbols(scan_script)
        issues: List[CodePreflightIssue] = []

        def _append_issue(name: str, *, suffix: str) -> None:
            issues.append(
                CodePreflightIssue(
                    code=f"undefined_symbol:{name}",
                    message=f"Declare or inline `{name}` before use; it is referenced as {suffix}.",
                )
            )

        for match in _CALL_RE.finditer(scan_script):
            name = match.group("name")
            prefix = scan_script[max(0, match.start() - 6):match.start()]
            if prefix.rstrip().endswith("new"):
                continue
            if self._should_ignore_symbol(name, declared):
                continue
            _append_issue(name, suffix="a function call")

        for pattern in _VALUE_REFS:
            for match in pattern.finditer(scan_script):
                name = match.group("name")
                if self._should_ignore_symbol(name, declared):
                    continue
                _append_issue(name, suffix="a live expression")

        return issues

    def _check_tdz_helpers(self, script: str) -> List[CodePreflightIssue]:
        """Flag const/let function bindings that can throw TDZ via hoisted callers."""
        scan_script = self._sanitize_for_symbol_scan(script)
        issues: List[CodePreflightIssue] = []
        seen: set[str] = set()
        for match in _FUNCTION_BINDING_RE.finditer(scan_script):
            name = match.group("name")
            kind = match.group("kind")
            if not name or name in seen or name in _RESERVED_IDENTIFIERS:
                continue
            if kind == "var":
                continue
            seen.add(name)
            if self._first_live_symbol_use(scan_script, name) is None:
                continue
            issues.append(
                CodePreflightIssue(
                    code=f"tdz_symbol:{name}",
                    message=(
                        f"Declare or inline `{name}` before use; `{kind} {name} = ...` is in the "
                        "temporal dead zone when a hoisted init/listener/rAF path runs first."
                    ),
                )
            )
        return issues

    @staticmethod
    def _find_function_binding(script: str, name: str) -> tuple[str, int] | None:
        for match in _FUNCTION_BINDING_RE.finditer(script):
            if match.group("name") == name:
                return match.group("kind"), match.start()
        return None

    def _first_live_symbol_use(self, script: str, name: str) -> int | None:
        positions: list[int] = []
        pattern = re.compile(rf"(?<![\w$.]){re.escape(name)}\b")
        for match in pattern.finditer(script):
            prefix = script[max(0, match.start() - 24):match.start()]
            if _DECL_KEYWORD_PREFIX_RE.search(prefix):
                continue
            if re.match(r"\s*=(?!=)", script[match.end():]):
                continue
            positions.append(match.start())
        return min(positions) if positions else None

    def _repair_declare_before_use(
        self,
        html_code: str,
        names: set[str] | None = None,
    ) -> str:
        """Hoist function bindings and declare assigned-but-undeclared aliases."""
        def script_block(block):
            script = block.group("body")
            repaired = self._hoist_function_bindings(script, names)
            repaired = self._declare_assigned_symbols(repaired, names)
            if repaired == script:
                return block[0]
            return block[1] + repaired + block[3]
        return _SCRIPT_BLOCK_RE.sub(script_block, html_code)

    def _repair_tdz_runtime_helpers(self, html_code: str) -> str:
        return self._repair_declare_before_use(html_code)

    @classmethod
    def _iter_function_bindings(cls, script: str, names: set[str] | None):
        matches = list(_FUNCTION_BINDING_RE.finditer(script))
        covered = {match.start() for match in matches}
        for match in _BARE_FUNCTION_ASSIGN_RE.finditer(script):
            if match.start() in covered:
                continue
            prefix = script[max(0, match.start() - 10):match.start()]
            if _DECL_KEYWORD_PREFIX_RE.search(prefix):
                continue
            matches.append(match)
        matches.sort(key=lambda item: item.start())
        for match in matches:
            name = match.group("name")
            if not name or name in _RESERVED_IDENTIFIERS:
                continue
            if names is not None and name not in names:
                continue
            yield match

    def _hoist_function_bindings(self, script: str, names: set[str] | None) -> str:
        matches = []
        for match in self._iter_function_bindings(script, names):
            name = match.group("name")
            if names is None and self._first_live_symbol_use(script, name) is None:
                continue
            matches.append(match)
        if not matches:
            return script
        rebuilt: list[str] = []
        cursor = 0
        changed = False
        for match in matches:
            if match.start() < cursor:
                continue
            rewritten = self._rewrite_function_binding(script, match)
            if rewritten is None:
                continue
            text, end = rewritten
            rebuilt.append(script[cursor:match.start()])
            rebuilt.append(text)
            cursor = end
            changed = True
        if not changed:
            return script
        rebuilt.append(script[cursor:])
        return "".join(rebuilt)

    def _declare_assigned_symbols(self, script: str, names: set[str] | None) -> str:
        scan_script = self._sanitize_for_symbol_scan(script)
        declared = self._collect_declared_symbols(scan_script)
        assigned = self._collect_assigned_symbols(scan_script)
        targets = {
            name
            for name in assigned
            if name not in declared
            and name not in _RESERVED_IDENTIFIERS
            and (names is None or name in names)
            and self._first_live_symbol_use(scan_script, name) is not None
        }
        if not targets:
            return script
        return "let " + ", ".join(sorted(targets)) + ";\n" + script.lstrip()

    @staticmethod
    def _collect_assigned_symbols(script: str) -> set[str]:
        names: set[str] = set()

        def _skip_declared(match: re.Match[str]) -> bool:
            prefix = script[max(0, match.start() - 10):match.start()]
            return bool(_DECL_KEYWORD_PREFIX_RE.search(prefix))

        for match in _BARE_ASSIGNMENT_RE.finditer(script):
            if _skip_declared(match):
                continue
            name = match.group("name")
            if name:
                names.add(name)
        for match in _ARRAY_DESTRUCTURE_ASSIGN_RE.finditer(script):
            if _skip_declared(match):
                continue
            names.update(CodePreflightValidator._extract_binding_identifiers(match.group("body") or ""))
        for match in _OBJECT_DESTRUCTURE_ASSIGN_RE.finditer(script):
            names.update(CodePreflightValidator._extract_binding_identifiers(match.group("body") or ""))
        return names

    @classmethod
    def _rewrite_function_binding(
        cls,
        script: str,
        match: re.Match[str],
    ) -> tuple[str, int] | None:
        name = match.group("name")
        prefix = match.group("prefix") or ""
        body_token = match.group("body") or ""
        rest_start = match.end()
        if body_token.startswith("function"):
            # const resize = function (...) { ... }  or function resize (...) { ... }
            after = script[rest_start:]
            header = re.match(
                rf"\s*(?:{re.escape(name)}\s*)?\((?P<params>[^)]*)\)\s*\{{",
                after,
            )
            if not header:
                return None
            body_open = rest_start + header.end()
            body, body_end = cls._consume_balanced_block(script, body_open)
            if body is None:
                return None
            params = header.group("params")
            async_prefix = "async " if prefix else ""
            return f"{async_prefix}function {name}({params}) {{{body}}}", body_end
        # Arrow: const loop = (t) => { ... } or t => expr
        after = script[match.start("body"):]
        arrow = re.match(
            rf"(?:\((?P<params>[^)]*)\)|(?P<single>{_IDENTIFIER_RE}))\s*=>\s*",
            after,
        )
        if not arrow:
            return None
        params = arrow.group("params")
        if params is None:
            params = arrow.group("single") or ""
        expr_start = match.start("body") + arrow.end()
        if expr_start < len(script) and script[expr_start] == "{":
            body, body_end = cls._consume_balanced_block(script, expr_start + 1)
            if body is None:
                return None
            # consume optional trailing semicolon
            end = body_end
            if end < len(script) and script[end] == ";":
                end += 1
            async_prefix = "async " if prefix else ""
            return f"{async_prefix}function {name}({params}) {{{body}}}", end
        statement, end = cls._consume_arrow_expression(script, expr_start)
        if statement is None:
            return None
        async_prefix = "async " if prefix else ""
        return f"{async_prefix}function {name}({params}) {{ return {statement}; }}", end

    @staticmethod
    def _consume_balanced_block(script: str, body_start: int) -> tuple[str | None, int]:
        depth = 1
        index = body_start
        quote: str | None = None
        escaped = False
        while index < len(script):
            ch = script[index]
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                index += 1
                continue
            if ch in {"'", '"', "`"}:
                quote = ch
                index += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return script[body_start:index], index + 1
            index += 1
        return None, body_start

    @staticmethod
    def _consume_arrow_expression(script: str, start: int) -> tuple[str | None, int]:
        index = start
        depth_paren = depth_brace = depth_bracket = 0
        quote: str | None = None
        escaped = False
        while index < len(script):
            ch = script[index]
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                index += 1
                continue
            if ch in {"'", '"', "`"}:
                quote = ch
                index += 1
                continue
            if ch == "(":
                depth_paren += 1
            elif ch == ")" and depth_paren:
                depth_paren -= 1
            elif ch == "{":
                depth_brace += 1
            elif ch == "}" and depth_brace:
                depth_brace -= 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]" and depth_bracket:
                depth_bracket -= 1
            elif ch == ";" and depth_paren == depth_brace == depth_bracket == 0:
                return script[start:index].strip(), index + 1
            elif ch == "\n" and depth_paren == depth_brace == depth_bracket == 0:
                return script[start:index].strip(), index
            index += 1
        tail = script[start:].strip()
        return (tail, len(script)) if tail else (None, start)

    @staticmethod
    def _sanitize_for_symbol_scan(script: str) -> str:
        sanitized = _BLOCK_COMMENT_RE.sub(" ", script)
        sanitized = _LINE_COMMENT_RE.sub(" ", sanitized)
        sanitized = _STRING_RE.sub("''", sanitized)
        return sanitized

    @staticmethod
    def _should_ignore_symbol(name: str, declared: set[str]) -> bool:
        if not name:
            return True
        if name in declared or name in _RESERVED_IDENTIFIERS:
            return True
        lowered = name.lower()
        if lowered in {"function", "return", "if", "for", "while", "switch", "catch"}:
            return True
        return False

    @staticmethod
    def _collect_declared_symbols(script: str) -> set[str]:
        declared = {match.group("name") for match in _DECL_RE.finditer(script)}
        for match in _VAR_DECL_BLOCK_RE.finditer(script):
            for declarator in CodePreflightValidator._split_declarators(match.group("body") or ""):
                lhs = declarator.split("=", 1)[0].strip()
                if not lhs:
                    continue
                declared.update(CodePreflightValidator._extract_binding_identifiers(lhs))
        for match in _FOR_DECL_RE.finditer(script):
            lhs = (match.group("lhs") or "").strip()
            if not lhs:
                continue
            declared.update(CodePreflightValidator._extract_binding_identifiers(lhs))
        for match in _PARAM_BLOCK_RE.finditer(script):
            params = (
                match.group("fn_params")
                or match.group("arrow_params")
                or match.group("single_param")
                or ""
            )
            for raw_part in params.split(","):
                part = raw_part.strip()
                if not part:
                    continue
                declared.update(CodePreflightValidator._extract_binding_identifiers(part))
        for match in _CATCH_PARAM_RE.finditer(script):
            name = (match.group("name") or "").strip()
            if re.fullmatch(_IDENTIFIER_RE, name):
                declared.add(name)
        return declared

    @staticmethod
    def _extract_binding_identifiers(pattern: str) -> set[str]:
        identifiers: set[str] = set()
        text = pattern or ""
        for match in re.finditer(_IDENTIFIER_RE, text):
            name = match.group(0)
            if name in _RESERVED_IDENTIFIERS:
                continue
            suffix = text[match.end():].lstrip()
            prefix = text[:match.start()].rstrip()
            if suffix.startswith(":") and (not prefix or prefix[-1] in "{,"):
                continue
            identifiers.add(name)
        return identifiers

    @staticmethod
    def _has_safe_grid_access_context(
        *,
        script: str,
        match_start: int,
        compact_line: str,
        row_expr: str,
        col_expr: str,
    ) -> bool:
        safe_patterns = (
            f"grid[{row_expr}]&&grid[{row_expr}][{col_expr}]",
            f"grid[{row_expr}]?." + f"[{col_expr}]",
            f"!grid[{row_expr}]||!grid[{row_expr}][{col_expr}]",
            f"!(grid[{row_expr}]&&grid[{row_expr}][{col_expr}])",
            f"!grid[{row_expr}]?." + f"[{col_expr}]",
        )
        if any(pattern in compact_line for pattern in safe_patterns):
            return True
        context_start = max(0, match_start - 240)
        context = re.sub(r"\s+", "", script[context_start:match_start])
        return any(pattern in context for pattern in safe_patterns)

    @staticmethod
    def _split_declarators(body: str) -> List[str]:
        parts: List[str] = []
        current: List[str] = []
        depth_paren = 0
        depth_brace = 0
        depth_bracket = 0
        quote: Optional[str] = None
        escaped = False

        for ch in body:
            current.append(ch)
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue
            if ch in {"'", '"', "`"}:
                quote = ch
                continue
            if ch == "(":
                depth_paren += 1
                continue
            if ch == ")" and depth_paren > 0:
                depth_paren -= 1
                continue
            if ch == "{":
                depth_brace += 1
                continue
            if ch == "}" and depth_brace > 0:
                depth_brace -= 1
                continue
            if ch == "[":
                depth_bracket += 1
                continue
            if ch == "]" and depth_bracket > 0:
                depth_bracket -= 1
                continue
            if ch == "," and depth_paren == 0 and depth_brace == 0 and depth_bracket == 0:
                parts.append("".join(current[:-1]).strip())
                current = []

        tail = "".join(current).strip()
        if tail:
            parts.append(tail)
        return parts

    @staticmethod
    def _extract_named_handler_body(script: str, handler_name: str) -> str:
        escaped = re.escape(handler_name)
        patterns = (
            rf"\bfunction\s+{escaped}\s*\([^)]*\)\s*\{{",
            rf"\b(?:const|let|var)\s+{escaped}\s*=\s*function\s*\([^)]*\)\s*\{{",
            rf"\b(?:const|let|var)\s+{escaped}\s*=\s*\([^)]*\)\s*=>\s*\{{",
            rf"\b{escaped}\s*:\s*function\s*\([^)]*\)\s*\{{",
        )
        for pattern in patterns:
            match = re.search(pattern, script)
            if not match:
                continue
            body_start = match.end()
            depth = 1
            index = body_start
            while index < len(script):
                ch = script[index]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return script[body_start:index]
                index += 1
        return ""
