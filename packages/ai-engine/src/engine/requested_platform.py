"""Resolve explicit platform requirements once, before profile defaults."""
from __future__ import annotations
import re
from ..api.models import GameSpec

DESKTOP_BRIEF_RE = re.compile(
    r"桌面|电脑|键盘|鼠标|方向键|横屏|横版|宽屏|16\s*[:：]\s*9|"
    r"\b(?:desktop|pc|keyboard|landscape)\b|"
    r"\bmouse\s+(?:move|movement|control|click)|"
    r"creative\s+studio|web\s*pc",
    re.I,
)
MOBILE_PORTRAIT_BRIEF_RE = re.compile(
    r"手机|竖屏|纵向|9\s*[:：]\s*16|"
    r"mobile\s+(?:portrait|h5)|portrait\s+mobile|\bportrait\b",
    re.I,
)
LANDSCAPE_ORIENTATIONS = frozenset({"landscape", "landscape_first", "desktop"})
DESKTOP_PLATFORMS = frozenset({"desktop_browser", "desktop_web"})


def _brief_text(spec: GameSpec | None) -> str:
    return str(getattr(spec, "source_description", "") or "")


def _normalize_orientation(*candidates: object) -> str:
    for candidate in candidates:
        if isinstance(candidate, dict):
            for key in ("orientation", "requested_orientation", "requestedOrientation"):
                value = candidate.get(key)
                if value:
                    return str(value).strip().lower()
            continue
        value = str(candidate or "").strip().lower()
        if value:
            return value
    return ""


def requires_mobile_portrait(
    spec: GameSpec | None,
    *,
    orientation: str | None = None,
    metadata: dict | None = None,
) -> bool:
    """True only when the user explicitly asked for a phone/portrait game."""
    brief = _brief_text(spec)
    resolved = _normalize_orientation(orientation, metadata)
    explicit_mobile = bool(MOBILE_PORTRAIT_BRIEF_RE.search(brief))
    explicit_desktop = bool(DESKTOP_BRIEF_RE.search(brief))
    landscape = resolved in LANDSCAPE_ORIENTATIONS
    return explicit_mobile and not explicit_desktop and not landscape


def requires_desktop(
    spec: GameSpec | None,
    *,
    orientation: str | None = None,
    metadata: dict | None = None,
) -> bool:
    """Desktop when the brief is science/tool or landscape/desktop, unless mobile-portrait is explicit."""
    if spec is None:
        resolved = _normalize_orientation(orientation, metadata)
        return resolved in LANDSCAPE_ORIENTATIONS
    if requires_mobile_portrait(spec, orientation=orientation, metadata=metadata):
        return False

    brief = _brief_text(spec)
    resolved = _normalize_orientation(orientation, metadata)
    kind = getattr(spec, "artifact_kind", None)
    if kind not in {"game", "tool", "science"}:
        from .artifact_quality import infer_artifact_kind
        kind = infer_artifact_kind(brief)
    if kind in {"science", "tool"}:
        return True
    if resolved in LANDSCAPE_ORIENTATIONS or DESKTOP_BRIEF_RE.search(brief):
        return True
    platform = str(getattr(getattr(spec, "platform_constraints", None), "platform", "") or "")
    return platform in DESKTOP_PLATFORMS


def normalize_requested_platform(
    spec: GameSpec,
    *,
    orientation: str | None = None,
    metadata: dict | None = None,
) -> GameSpec:
    if not requires_desktop(spec, orientation=orientation, metadata=metadata):
        return spec
    resolved = spec.model_copy(deep=True)
    resolved.platform_constraints.platform = "desktop_browser"
    input_overrides, _ = requested_contract_overrides(
        spec, orientation=orientation, metadata=metadata,
    )
    resolved.platform_constraints.input_mode = (
        "pointer_keyboard" if "keyboard" in input_overrides.get("required_modes", []) else "pointer"
    )
    return resolved


def requested_contract_overrides(
    spec: GameSpec,
    *,
    orientation: str | None = None,
    metadata: dict | None = None,
) -> tuple[dict, list[str]]:
    if not requires_desktop(spec, orientation=orientation, metadata=metadata):
        return {}, []
    brief = spec.source_description or ""
    modes = ["pointer"]
    if re.search(r"键盘|方向键|\bkeyboard\b|arrow\s+keys", brief, re.I):
        modes.append("keyboard")
    gestures = ["click"]
    if re.search(r"鼠标移动|mouse\s+(?:move|movement)|pointer\s+move", brief, re.I):
        gestures.append("move")
    states = ["paused"] if re.search(r"暂停|失焦|\bpause|\bblur\b", brief, re.I) else []
    return {"required_modes": modes, "allow_mouse_fallback": True, "gestures": gestures}, states
