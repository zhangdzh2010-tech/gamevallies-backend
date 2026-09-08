"""Resolve explicit platform requirements once, before profile defaults."""
from __future__ import annotations
import re
from ..api.models import GameSpec


def requires_desktop(spec: GameSpec | None) -> bool:
    brief = str(getattr(spec, "source_description", "") or "")
    return bool(re.search(r"桌面|电脑|键盘|鼠标|方向键|\b(?:desktop|pc|keyboard)\b|\bmouse\s+(?:move|movement|control|click)", brief, re.I))


def normalize_requested_platform(spec: GameSpec) -> GameSpec:
    if not requires_desktop(spec):
        return spec
    resolved = spec.model_copy(deep=True)
    resolved.platform_constraints.platform = "desktop_browser"
    input_overrides, _ = requested_contract_overrides(spec)
    resolved.platform_constraints.input_mode = "pointer_keyboard" if "keyboard" in input_overrides["required_modes"] else "pointer"
    return resolved


def requested_contract_overrides(spec: GameSpec) -> tuple[dict, list[str]]:
    if not requires_desktop(spec):
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
