"""Helpers for orientation-aware short-edge mobile layout validation."""

from __future__ import annotations

import re

MOBILE_LAYOUT_BRIDGE_MARKER = "__playforgeMobileLayoutBridgeInstalled"

_VIEWPORT_WIDTH_RE = re.compile(
    r"(?:window\.)?innerWidth|document\.documentElement\.clientWidth|visualViewport\.width|viewportWidth|containerWidth|viewWidth|\bvw\b",
    re.IGNORECASE,
)
_VIEWPORT_HEIGHT_RE = re.compile(
    r"(?:window\.)?innerHeight|document\.documentElement\.clientHeight|visualViewport\.height|viewportHeight|containerHeight|viewHeight|\bvh\b",
    re.IGNORECASE,
)
_WIDTH_HINT_RE = re.compile(
    r"scaleX|widthScale|uiScaleX|layoutScaleX|viewportWidth|containerWidth|innerWidth|clientWidth|\bvw\b|\bsx\b|\bsw\b|\bscalew\b",
    re.IGNORECASE,
)
_HEIGHT_HINT_RE = re.compile(
    r"scaleY|heightScale|uiScaleY|layoutScaleY|viewportHeight|containerHeight|innerHeight|clientHeight|\bvh\b|\bsy\b|\bsh\b|\bscaleh\b",
    re.IGNORECASE,
)
_ASPECT_COMPARE_RE = re.compile(
    r"\bif\s*\([^)]*(?:ratio|aspect(?:Ratio)?|viewportRatio|screenRatio)[^)]*[<>][^)]*(?:ratio|aspect(?:Ratio)?|viewportRatio|screenRatio)",
    re.IGNORECASE,
)
_RESIZE_HANDLER_RE = re.compile(
    r"addEventListener\s*\(\s*['\"](?:resize|orientationchange)['\"]|\bfunction\s+resize\w*\s*\(|\bconst\s+resize\w*\s*=\s*\(",
    re.IGNORECASE,
)


def has_short_edge_scaling(code: str, *, orientation: str = "portrait_first") -> bool:
    source = code or ""
    lower = source.lower()
    normalized_orientation = _normalize_orientation(orientation)

    if MOBILE_LAYOUT_BRIDGE_MARKER.lower() in lower:
        return True
    if not _has_viewport_dimension_signals(source):
        return False
    if _has_math_min_short_edge_scaling(source):
        return True
    if _has_explicit_short_edge_tokens(lower):
        return True
    return _has_orientation_aspect_ratio_fit(source, orientation=normalized_orientation)


def has_portrait_short_edge_scaling(code: str) -> bool:
    return has_short_edge_scaling(code, orientation="portrait_first")


def has_landscape_short_edge_scaling(code: str) -> bool:
    return has_short_edge_scaling(code, orientation="landscape_first")


def _has_viewport_dimension_signals(code: str) -> bool:
    return bool(_VIEWPORT_WIDTH_RE.search(code) and _VIEWPORT_HEIGHT_RE.search(code))


def _has_math_min_short_edge_scaling(code: str) -> bool:
    for match in re.finditer(
        r"Math\.min\s*\(\s*([^,\n]+?)\s*,\s*([^\)\n]+?)\s*\)",
        code,
        re.IGNORECASE,
    ):
        first = match.group(1)
        second = match.group(2)
        if _expr_has_width_hint(first) and _expr_has_height_hint(second):
            return True
        if _expr_has_height_hint(first) and _expr_has_width_hint(second):
            return True
    return False


def _has_explicit_short_edge_tokens(lower: str) -> bool:
    if "shortedge" in lower or "short_edge" in lower:
        return True
    return "uiscale" in lower and (
        ("scalex" in lower and "scaley" in lower)
        or ("sx" in lower and "sy" in lower)
    )


def _has_orientation_aspect_ratio_fit(code: str, *, orientation: str) -> bool:
    if not _RESIZE_HANDLER_RE.search(code):
        return False
    if not _ASPECT_COMPARE_RE.search(code):
        return False
    if not re.search(r"\b(?:canvas|[A-Za-z_$][\w$]*)\.(?:width|height)\s*=", code):
        return False
    return _has_reference_dimensions(code, orientation=orientation)


def _has_reference_dimensions(code: str, *, orientation: str) -> bool:
    assignments = [
        (name.lower(), int(value))
        for name, value in re.findall(r"\b([A-Za-z_$][\w$]*)\s*=\s*(\d{2,4})\b", code)
    ]
    width_values = [value for name, value in assignments if _looks_width_name(name)]
    height_values = [value for name, value in assignments if _looks_height_name(name)]
    if orientation == "landscape_first":
        return any(width > height for width in width_values for height in height_values)
    return any(width < height for width in width_values for height in height_values)


def _normalize_orientation(orientation: str) -> str:
    return "landscape_first" if orientation == "landscape_first" else "portrait_first"


def _looks_width_name(name: str) -> bool:
    normalized = name.lower()
    return normalized in {"w", "ref_w", "canvas_w", "game_w"} or "width" in normalized


def _looks_height_name(name: str) -> bool:
    normalized = name.lower()
    return normalized in {"h", "ref_h", "canvas_h", "game_h"} or "height" in normalized


def _expr_has_width_hint(expr: str) -> bool:
    return bool(_WIDTH_HINT_RE.search(expr))


def _expr_has_height_hint(expr: str) -> bool:
    return bool(_HEIGHT_HINT_RE.search(expr))
