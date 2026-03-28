"""Stage 06b: Runtime QA with lightweight interaction smoke tests.

Loads the generated HTML in a headless Chromium page, instruments frame timing
and input registration, then validates:
  - JS errors (console errors / uncaught exceptions)
  - canvas render check (any non-black pixel)
  - measured FPS
  - game-over postMessage detection
  - registered input handlers
  - whether a synthetic tap / swipe / key press reaches the game

The check is best-effort: if Playwright is unavailable or times out, it
returns ``RuntimeQAResult(ran=False)`` and the pipeline decides whether to
fail closed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, TypeVar

from ..config.settings import settings
from ..config.timeout_store import get_float as get_timeout_float, get_int as get_timeout_int
from .quality_scorer import RuntimeQAResult

logger = logging.getLogger(__name__)
T = TypeVar("T")


_INSTRUMENTATION_JS = """
(function() {
    const QA_INPUT_EVENTS = [
        'touchstart', 'touchmove', 'touchend', 'touchcancel',
        'pointerdown', 'pointermove', 'pointerup', 'pointercancel',
        'click', 'mousedown', 'mousemove', 'mouseup',
        'keydown', 'keyup', 'keypress',
        'deviceorientation', 'devicemotion'
    ];
    const pushUnique = function(bucket, value) {
        if (!value) return;
        if (bucket.indexOf(value) === -1) bucket.push(value);
    };

    window.__qaErrors = [];
    window.__fpsFrames = 0;
    window.__fpsStart = performance.now();
    window.__fps = 0;
    window.__gameOverReceived = false;
    window.__qaRegisteredInputs = [];
    window.__qaTriggeredInputs = [];
    window.__qaInteractionPerformed = false;

    const originalAddEventListener = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(type, handler, options) {
        const normalized = String(type || '').toLowerCase();
        if (QA_INPUT_EVENTS.indexOf(normalized) !== -1) {
            pushUnique(window.__qaRegisteredInputs, normalized);
        }
        if (typeof handler !== 'function') {
            return originalAddEventListener.call(this, type, handler, options);
        }
        const wrappedHandler = function() {
            if (QA_INPUT_EVENTS.indexOf(normalized) !== -1) {
                pushUnique(window.__qaTriggeredInputs, normalized);
            }
            return handler.apply(this, arguments);
        };
        return originalAddEventListener.call(this, type, wrappedHandler, options);
    };

    window.__qaCollectDirectInputs = function() {
        const targets = [
            window,
            document,
            document.body,
            document.getElementById('gameCanvas'),
            document.querySelector('canvas')
        ].filter(Boolean);
        const events = [];
        QA_INPUT_EVENTS.forEach(function(eventName) {
            const prop = 'on' + eventName;
            for (const target of targets) {
                try {
                    if (typeof target[prop] === 'function') {
                        pushUnique(events, eventName);
                    }
                } catch (err) {
                    /* ignore property access errors */
                }
            }
            if (document.querySelector('[' + prop + ']')) {
                pushUnique(events, eventName);
            }
        });
        return events;
    };

    window.addEventListener('message', function(e) {
        if (e.data && e.data.type === 'game_over') {
            window.__gameOverReceived = true;
        }
    });

    const originalRAF = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = function(cb) {
        return originalRAF(function(ts) {
            window.__fpsFrames++;
            const elapsed = (performance.now() - window.__fpsStart) / 1000;
            if (elapsed > 0) window.__fps = window.__fpsFrames / elapsed;
            return cb(ts);
        });
    };
})();
"""

_CANVAS_CHECK_JS = """
(function() {
    const canvas = document.getElementById('gameCanvas') ||
                   document.querySelector('canvas');
    if (!canvas) return false;
    try {
        const ctx = canvas.getContext('2d');
        if (!ctx) return false;
        const w = Math.min(canvas.width, 200);
        const h = Math.min(canvas.height, 200);
        if (w === 0 || h === 0) return false;
        const data = ctx.getImageData(0, 0, w, h).data;
        for (let i = 0; i < data.length; i++) {
            if (data[i] !== 0) return true;
        }
        return false;
    } catch (e) {
        return false;
    }
})()
"""

_CANVAS_FINGERPRINT_JS = """
(function() {
    const canvas = document.getElementById('gameCanvas') ||
                   document.querySelector('canvas');
    if (!canvas) return null;
    try {
        const ctx = canvas.getContext('2d');
        if (!ctx) return null;
        const w = Math.min(canvas.width, 160);
        const h = Math.min(canvas.height, 160);
        if (w === 0 || h === 0) return null;
        const data = ctx.getImageData(0, 0, w, h).data;
        let hash = 0;
        for (let i = 0; i < data.length; i += 16) {
            hash = (hash * 33 + data[i] + data[i + 1] + data[i + 2] + data[i + 3]) % 2147483647;
        }
        return hash;
    } catch (e) {
        return null;
    }
})()
"""

_DOM_FINGERPRINT_JS = """
(function() {
    const root = document.body || document.documentElement;
    if (!root) return null;
    try {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
        let hash = 0;
        let count = 0;
        while (walker.nextNode() && count < 120) {
            const el = walker.currentNode;
            const tag = (el.tagName || '').toLowerCase();
            if (!tag || ['script', 'style', 'meta', 'link', 'noscript'].indexOf(tag) !== -1) {
                continue;
            }
            const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            if (style && (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0')) {
                continue;
            }
            const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : { left: 0, top: 0, width: 0, height: 0 };
            if (
                tag !== 'body' &&
                tag !== 'html' &&
                Math.round(rect.width || 0) === 0 &&
                Math.round(rect.height || 0) === 0
            ) {
                continue;
            }
            const text = ((el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim()).slice(0, 64);
            const token = [
                tag,
                el.id || '',
                typeof el.className === 'string' ? el.className.slice(0, 48) : '',
                Math.round(rect.left || 0),
                Math.round(rect.top || 0),
                Math.round(rect.width || 0),
                Math.round(rect.height || 0),
                text,
            ].join('|');
            for (let i = 0; i < token.length; i++) {
                hash = (hash * 33 + token.charCodeAt(i)) % 2147483647;
            }
            count++;
        }
        return {
            hash,
            count,
            bodyText: ((root.innerText || root.textContent || '').replace(/\\s+/g, ' ').trim()).slice(0, 160),
            title: document.title || '',
        };
    } catch (e) {
        return null;
    }
})()
"""

_FRAME_PALETTE_JS = """
(function() {
    const canvas = document.getElementById('gameCanvas') ||
                   document.querySelector('canvas');
    const toHex = function(r, g, b) {
        const clamp = function(value) {
            return Math.max(0, Math.min(255, Math.round(value || 0)));
        };
        return '#' + [clamp(r), clamp(g), clamp(b)].map(function(value) {
            return value.toString(16).padStart(2, '0');
        }).join('');
    };
    const parseCssColor = function(value) {
        if (!value || typeof value !== 'string') {
            return null;
        }
        const rgba = value.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i);
        if (!rgba) {
            return null;
        }
        return {
            r: Number(rgba[1]),
            g: Number(rgba[2]),
            b: Number(rgba[3]),
            hex: toHex(Number(rgba[1]), Number(rgba[2]), Number(rgba[3])),
        };
    };
    const colorDistance = function(a, b) {
        if (!a || !b) {
            return 0;
        }
        return Math.sqrt(
            Math.pow((a.r || 0) - (b.r || 0), 2) +
            Math.pow((a.g || 0) - (b.g || 0), 2) +
            Math.pow((a.b || 0) - (b.b || 0), 2)
        );
    };
    const luma = function(color) {
        if (!color) {
            return 0;
        }
        return (0.2126 * (color.r || 0)) + (0.7152 * (color.g || 0)) + (0.0722 * (color.b || 0));
    };

    if (canvas) {
        try {
            const ctx = canvas.getContext('2d');
            if (ctx) {
                const sampleW = Math.max(1, Math.min(canvas.width || canvas.clientWidth || 0, 96));
                const sampleH = Math.max(1, Math.min(canvas.height || canvas.clientHeight || 0, 96));
                if (sampleW > 0 && sampleH > 0) {
                    const data = ctx.getImageData(0, 0, sampleW, sampleH).data;
                    const buckets = new Map();
                    for (let i = 0; i < data.length; i += 4) {
                        const alpha = data[i + 3];
                        if (alpha < 24) {
                            continue;
                        }
                        const r = data[i];
                        const g = data[i + 1];
                        const b = data[i + 2];
                        const key = [
                            Math.floor(r / 24),
                            Math.floor(g / 24),
                            Math.floor(b / 24),
                        ].join('-');
                        const existing = buckets.get(key) || {
                            r: 0,
                            g: 0,
                            b: 0,
                            count: 0,
                            score: 0,
                        };
                        existing.r += r;
                        existing.g += g;
                        existing.b += b;
                        existing.count += 1;
                        const max = Math.max(r, g, b);
                        const min = Math.min(r, g, b);
                        const saturation = max - min;
                        existing.score += 1 + (saturation / 180) + ((255 - luma({ r, g, b })) / 255) * 0.3;
                        buckets.set(key, existing);
                    }

                    const dominantBuckets = Array.from(buckets.values()).map(function(entry) {
                        const color = {
                            r: entry.count ? entry.r / entry.count : 0,
                            g: entry.count ? entry.g / entry.count : 0,
                            b: entry.count ? entry.b / entry.count : 0,
                        };
                        return {
                            r: color.r,
                            g: color.g,
                            b: color.b,
                            count: entry.count,
                            score: entry.score,
                            luma: luma(color),
                            hex: toHex(color.r, color.g, color.b),
                        };
                    }).sort(function(a, b) {
                        return b.score - a.score;
                    });

                    if (dominantBuckets.length) {
                        const primary = dominantBuckets[0];
                        const secondary = dominantBuckets.find(function(candidate) {
                            return colorDistance(primary, candidate) >= 70;
                        }) || dominantBuckets[Math.min(1, dominantBuckets.length - 1)] || primary;
                        const background = dominantBuckets
                            .slice()
                            .sort(function(a, b) { return a.luma - b.luma; })
                            .find(function(candidate) { return candidate.count >= 2; }) || primary;

                        return {
                            source: 'canvas',
                            primary: primary.hex,
                            secondary: secondary.hex,
                            background: background.hex,
                        };
                    }
                }
            }
        } catch (e) {
            /* ignore canvas palette extraction failures */
        }
    }

    try {
        const root = document.body || document.documentElement;
        const style = root && window.getComputedStyle ? window.getComputedStyle(root) : null;
        const parsed = parseCssColor(style ? style.backgroundColor : '');
        if (parsed) {
            return {
                source: 'body',
                primary: parsed.hex,
                secondary: parsed.hex,
                background: parsed.hex,
            };
        }
    } catch (e) {
        /* ignore DOM palette extraction failures */
    }

    return null;
})()
"""

_INTERACTION_JS = """
(async function() {
    const target = document.getElementById('gameCanvas') ||
                   document.querySelector('canvas') ||
                   document.body ||
                   document.documentElement;
    if (!target) return false;

    const rect = target.getBoundingClientRect ? target.getBoundingClientRect() : { left: 0, top: 0, width: 320, height: 480 };
    const clientX = rect.left + Math.max(8, (rect.width || 320) / 2);
    const clientY = rect.top + Math.max(8, (rect.height || 480) / 2);
    const makeTouchLikePoint = function(x, y) {
        return {
            identifier: 1,
            target,
            clientX: x,
            clientY: y,
            pageX: x,
            pageY: y,
            screenX: x,
            screenY: y,
            radiusX: 1,
            radiusY: 1,
            rotationAngle: 0,
            force: 1
        };
    };

    const dispatch = function(dispatchTarget, eventName, ctorName, init) {
        try {
            const Ctor = window[ctorName];
            let event;
            if (typeof Ctor === 'function') {
                event = new Ctor(eventName, Object.assign({ bubbles: true, cancelable: true }, init || {}));
            } else {
                event = new Event(eventName, { bubbles: true, cancelable: true });
            }
            dispatchTarget.dispatchEvent(event);
            return true;
        } catch (e) {
            window.__qaErrors.push('Interaction dispatch failed for ' + eventName + ': ' + e.message);
            return false;
        }
    };
    const dispatchTouch = function(eventName, x, y) {
        try {
            const touchLike = makeTouchLikePoint(x, y);
            const changedTouches = [touchLike];
            const activeTouches = eventName === 'touchend' ? [] : changedTouches;
            let event;
            if (typeof window.TouchEvent === 'function') {
                let touchObjects = changedTouches;
                if (typeof window.Touch === 'function') {
                    touchObjects = [
                        new window.Touch({
                            identifier: touchLike.identifier,
                            target: touchLike.target,
                            clientX: touchLike.clientX,
                            clientY: touchLike.clientY,
                            pageX: touchLike.pageX,
                            pageY: touchLike.pageY,
                            screenX: touchLike.screenX,
                            screenY: touchLike.screenY,
                            radiusX: touchLike.radiusX,
                            radiusY: touchLike.radiusY,
                            rotationAngle: touchLike.rotationAngle,
                            force: touchLike.force
                        })
                    ];
                }
                event = new window.TouchEvent(eventName, {
                    bubbles: true,
                    cancelable: true,
                    touches: eventName === 'touchend' ? [] : touchObjects,
                    targetTouches: eventName === 'touchend' ? [] : touchObjects,
                    changedTouches: touchObjects
                });
            } else {
                event = new Event(eventName, { bubbles: true, cancelable: true });
                Object.defineProperty(event, 'touches', { value: activeTouches });
                Object.defineProperty(event, 'targetTouches', { value: activeTouches });
                Object.defineProperty(event, 'changedTouches', { value: changedTouches });
            }
            target.dispatchEvent(event);
            return true;
        } catch (e) {
            window.__qaErrors.push('Interaction dispatch failed for ' + eventName + ': ' + e.message);
            return false;
        }
    };

    dispatch(target, 'pointerdown', 'PointerEvent', { pointerId: 1, pointerType: 'touch', clientX, clientY, button: 0, buttons: 1 });
    dispatch(target, 'pointermove', 'PointerEvent', { pointerId: 1, pointerType: 'touch', clientX: clientX + 12, clientY: clientY - 24, button: 0, buttons: 1 });
    dispatch(target, 'pointerup', 'PointerEvent', { pointerId: 1, pointerType: 'touch', clientX: clientX + 12, clientY: clientY - 24, button: 0, buttons: 0 });

    dispatchTouch('touchstart', clientX, clientY);
    dispatchTouch('touchmove', clientX + 12, clientY - 24);
    dispatchTouch('touchend', clientX + 12, clientY - 24);

    dispatch(target, 'mousedown', 'MouseEvent', { clientX, clientY, button: 0, buttons: 1 });
    dispatch(target, 'mousemove', 'MouseEvent', { clientX: clientX + 8, clientY: clientY - 8, button: 0, buttons: 1 });
    dispatch(target, 'mouseup', 'MouseEvent', { clientX: clientX + 8, clientY: clientY - 8, button: 0, buttons: 0 });
    dispatch(target, 'click', 'MouseEvent', { clientX, clientY, button: 0 });

    dispatch(document, 'keydown', 'KeyboardEvent', { key: 'ArrowUp', code: 'ArrowUp' });
    dispatch(document, 'keyup', 'KeyboardEvent', { key: 'ArrowUp', code: 'ArrowUp' });

    window.__qaInteractionPerformed = true;
    await new Promise(function(resolve) { setTimeout(resolve, 250); });
    return true;
})()
"""

_COLLECT_JS = """
({
    fps: window.__fps || 0,
    frames: window.__fpsFrames || 0,
    errors: window.__qaErrors || [],
    gameOverReceived: window.__gameOverReceived || false,
    registeredInputHandlers: window.__qaRegisteredInputs || [],
    directInputHandlers: window.__qaCollectDirectInputs ? window.__qaCollectDirectInputs() : [],
    triggeredInputHandlers: window.__qaTriggeredInputs || [],
    interactionPerformed: window.__qaInteractionPerformed || false,
})
"""


def _inject_probe_script(html_code: str) -> str:
    """Inline the runtime QA probe before any page-owned script executes."""
    if "__qaRegisteredInputs" in (html_code or ""):
        return html_code

    probe_tag = f"<script>\n{_INSTRUMENTATION_JS.strip()}\n</script>"
    if re.search(r"<head[^>]*>", html_code or "", re.IGNORECASE):
        return re.sub(
            r"(<head[^>]*>)",
            rf"\1\n{probe_tag}\n",
            html_code,
            count=1,
            flags=re.IGNORECASE,
        )
    if re.search(r"<body[^>]*>", html_code or "", re.IGNORECASE):
        return re.sub(
            r"(<body[^>]*>)",
            rf"\1\n{probe_tag}\n",
            html_code,
            count=1,
            flags=re.IGNORECASE,
        )
    return probe_tag + "\n" + html_code


def _resolve_chromium_executable() -> str | None:
    """Locate a bundled Chromium binary in serverless/container runtimes."""
    override = (os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or "").strip()
    if override and Path(override).exists():
        return override

    search_roots: list[Path] = []
    env_root = (os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env_root:
        search_roots.append(Path(env_root))
    search_roots.extend([
        Path("/ms-playwright"),
        Path.home() / ".cache" / "ms-playwright",
        Path("/root/.cache/ms-playwright"),
    ])

    seen: set[str] = set()
    for root in search_roots:
        root_key = str(root)
        if not root_key or root_key in seen:
            continue
        seen.add(root_key)
        try:
            for candidate in sorted(root.glob("chromium-*/chrome-linux/chrome"), reverse=True):
                if candidate.exists():
                    return str(candidate)
        except Exception:
            continue
    return None


def _dom_fingerprint_changed(before: object, after: object) -> bool:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False

    before_hash = before.get("hash")
    after_hash = after.get("hash")
    before_text = str(before.get("bodyText") or "")
    after_text = str(after.get("bodyText") or "")
    before_title = str(before.get("title") or "")
    after_title = str(after.get("title") or "")
    return bool(
        before_hash != after_hash
        or before_text != after_text
        or before_title != after_title
    )


class RuntimeQAPhaseTimeoutError(asyncio.TimeoutError):
    def __init__(self, phase: str, timeout_s: float) -> None:
        super().__init__(f"{phase} timed out after {timeout_s:.2f}s")
        self.phase = phase
        self.timeout_s = timeout_s


def _phase_timeout_s(key: str, default: float, *, effective_timeout_s: float) -> float:
    configured = max(get_timeout_float(key, default, min_value=0.05), 0.05)
    return max(min(configured, effective_timeout_s), 0.05)


def _overall_timeout_s(*, effective_timeout_s: float) -> float:
    phase_budget_s = sum((
        _phase_timeout_s(
            "timeout.ai_engine.runtime_qa.phase_launch_s",
            8.0,
            effective_timeout_s=effective_timeout_s,
        ),
        _phase_timeout_s(
            "timeout.ai_engine.runtime_qa.phase_content_load_s",
            15.0,
            effective_timeout_s=effective_timeout_s,
        ),
        _phase_timeout_s(
            "timeout.ai_engine.runtime_qa.phase_interaction_s",
            6.0,
            effective_timeout_s=effective_timeout_s,
        ),
        _phase_timeout_s(
            "timeout.ai_engine.runtime_qa.phase_collect_s",
            5.0,
            effective_timeout_s=effective_timeout_s,
        ),
    ))
    headroom_s = max(
        get_timeout_float(
            "timeout.ai_engine.runtime_qa.phase_total_headroom_s",
            3.0,
            min_value=0.05,
        ),
        0.05,
    )
    return max(min(effective_timeout_s, phase_budget_s + headroom_s), 0.05)


def _cover_viewport(orientation: str | None) -> dict[str, int]:
    if orientation == "landscape_first":
        return {"width": 640, "height": 360}
    return {"width": 360, "height": 640}


def _normalize_cover_text(value: str | None, *, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if re.fullmatch(r"Game\s+[0-9a-f]{8,}", text, flags=re.IGNORECASE):
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def _normalize_hex_color(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", text):
        return None
    return text


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    color = _normalize_hex_color(value) or "#000000"
    return (
        int(color[1:3], 16),
        int(color[3:5], 16),
        int(color[5:7], 16),
    )


def _rgb_to_hex(red: float, green: float, blue: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, int(round(red)))),
        max(0, min(255, int(round(green)))),
        max(0, min(255, int(round(blue)))),
    )


def _mix_hex(first: str, second: str, ratio: float) -> str:
    weight = max(0.0, min(1.0, ratio))
    r1, g1, b1 = _hex_to_rgb(first)
    r2, g2, b2 = _hex_to_rgb(second)
    return _rgb_to_hex(
        r1 + ((r2 - r1) * weight),
        g1 + ((g2 - g1) * weight),
        b1 + ((b2 - b1) * weight),
    )


def _color_distance(first: str, second: str) -> float:
    r1, g1, b1 = _hex_to_rgb(first)
    r2, g2, b2 = _hex_to_rgb(second)
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def _theme_cover_palette(theme: str | None) -> tuple[str, str]:
    token = re.sub(r"[^a-z0-9]+", " ", str(theme or "").lower()).strip()
    palette_map = (
        (("space", "neon"), ("#60a5fa", "#a855f7")),
        (("ocean", "water"), ("#22d3ee", "#0ea5e9")),
        (("forest", "nature"), ("#84cc16", "#22c55e")),
        (("city", "urban"), ("#fb7185", "#38bdf8")),
        (("food",), ("#fb7185", "#f59e0b")),
        (("toy",), ("#f59e0b", "#22c55e")),
        (("fantasy", "magic"), ("#c084fc", "#f472b6")),
    )
    for needles, palette in palette_map:
        if any(needle in token for needle in needles):
            return palette
    return ("#f59e0b", "#f43f5e")


def _resolve_cover_palette(
    *,
    theme: str | None,
    extracted_palette: dict[str, Any] | None,
) -> dict[str, str]:
    fallback_primary, fallback_secondary = _theme_cover_palette(theme)
    source = str((extracted_palette or {}).get("source") or "theme_fallback").strip() or "theme_fallback"
    primary = _normalize_hex_color((extracted_palette or {}).get("primary")) or fallback_primary
    secondary = _normalize_hex_color((extracted_palette or {}).get("secondary")) or fallback_secondary
    background = _normalize_hex_color((extracted_palette or {}).get("background"))

    if _color_distance(primary, secondary) < 36:
        secondary = fallback_secondary if _color_distance(primary, fallback_secondary) >= 36 else _mix_hex(primary, "#ffffff", 0.32)

    if not background:
        background = _mix_hex(primary, "#040816", 0.78)

    if _color_distance(background, primary) < 28:
        background = _mix_hex(background, "#020617", 0.5)

    return {
        "source": source if source in {"canvas", "body"} else "theme_fallback",
        "primary": primary,
        "secondary": secondary,
        "background": background,
    }


def _resolve_cover_copy(
    *,
    requested_title: str | None,
    dom_title: str | None,
    game_type: str | None,
    theme: str | None,
    runtime_profile: str | None,
    updated: bool,
) -> dict[str, str]:
    title = _normalize_cover_text(requested_title or dom_title, max_len=42)
    theme_token = _normalize_cover_text(str(theme or "").replace("_", " ").replace("-", " "), max_len=14)
    game_token = _normalize_cover_text(
        str(game_type or runtime_profile or "").replace("_", " ").replace("-", " "),
        max_len=18,
    )

    if not title:
        if theme_token and game_token:
            title = f"{theme_token.title()} {game_token.title()}"
        elif game_token:
            title = f"{game_token.title()} Arcade"
        else:
            title = "Instant Arcade"

    badge_parts: list[str] = []
    if theme_token and theme_token.lower() != "arcade":
        badge_parts.append(theme_token.upper())
    if game_token:
        badge_parts.append(game_token.upper())
    badge = " ".join(badge_parts[:2]) or ("UPDATED BUILD" if updated else "PLAYABLE BUILD")

    subtitle = "Updated playable build" if updated else "Playable build"
    title = _normalize_cover_text(title, max_len=42) or "Instant Arcade"
    subtitle = _normalize_cover_text(subtitle, max_len=32) or "Playable build"

    return {
        "title": title,
        "badge": badge,
        "subtitle": subtitle,
    }


async def _remove_cover_overlay(page: Any) -> None:
    with contextlib.suppress(Exception):
        await page.evaluate(
            """
            (function() {
                const overlay = document.getElementById('__codexCoverOverlay');
                if (overlay && overlay.parentNode) {
                    overlay.parentNode.removeChild(overlay);
                }
                return true;
            })()
            """
        )


async def _apply_cover_overlay(
    page: Any,
    *,
    orientation: str | None,
    title: str,
    badge: str,
    subtitle: str,
    palette: dict[str, str],
) -> bool:
    accent_start = palette.get("primary") or "#f59e0b"
    accent_end = palette.get("secondary") or "#f43f5e"
    background = palette.get("background") or "#020617"
    panel_background = _mix_hex(background, "#0f172a", 0.35)
    is_landscape = orientation == "landscape_first"
    content_width = "54%" if is_landscape else "78%"
    title_font = "clamp(22px, 4.8vw, 40px)" if is_landscape else "clamp(28px, 7.2vw, 54px)"
    body_padding = "18px 20px 18px 20px" if is_landscape else "22px 22px 24px 22px"
    badge_alignment = "flex-end" if is_landscape else "flex-start"
    script = f"""
    (function() {{
        try {{
            const existing = document.getElementById('__codexCoverOverlay');
            if (existing && existing.parentNode) {{
                existing.parentNode.removeChild(existing);
            }}

            const host = document.body || document.documentElement;
            if (!host) {{
                return false;
            }}

            const overlay = document.createElement('div');
            overlay.id = '__codexCoverOverlay';
            overlay.setAttribute('aria-hidden', 'true');
            overlay.style.cssText = [
                'position:fixed',
                'inset:0',
                'z-index:2147483647',
                'pointer-events:none',
                'overflow:hidden',
                'box-sizing:border-box',
                'font-family:\"Trebuchet MS\",\"Avenir Next\",\"Segoe UI\",sans-serif'
            ].join(';');

            const wash = document.createElement('div');
            wash.style.cssText = [
                'position:absolute',
                'inset:0',
                'background:radial-gradient(circle at 18% 14%, {accent_start}66 0%, transparent 30%), radial-gradient(circle at 82% 20%, {accent_end}55 0%, transparent 28%), linear-gradient(180deg, rgba(6,10,20,0.04) 0%, {background}40 46%, {background}e6 100%)'
            ].join(';');

            const gloss = document.createElement('div');
            gloss.style.cssText = [
                'position:absolute',
                'left:-12%',
                'top:-6%',
                'width:56%',
                'height:34%',
                'transform:rotate(-10deg)',
                'background:linear-gradient(180deg, rgba(255,255,255,0.22) 0%, rgba(255,255,255,0.02) 100%)',
                'filter:blur(0px)',
                'opacity:0.9'
            ].join(';');

            const frame = document.createElement('div');
            frame.style.cssText = [
                'position:absolute',
                'inset:12px',
                'border:1px solid rgba(255,255,255,0.18)',
                'border-radius:28px',
                'box-shadow:inset 0 0 0 1px rgba(255,255,255,0.05)'
            ].join(';');

            const content = document.createElement('div');
            content.style.cssText = [
                'position:relative',
                'display:flex',
                'flex-direction:column',
                'justify-content:space-between',
                'height:100%',
                'padding:{body_padding}',
                'box-sizing:border-box'
            ].join(';');

            const metaRow = document.createElement('div');
            metaRow.style.cssText = [
                'display:flex',
                'justify-content:{badge_alignment}',
                'align-items:flex-start'
            ].join(';');

            const badgeEl = document.createElement('div');
            badgeEl.textContent = {json.dumps(badge)};
            badgeEl.style.cssText = [
                'display:inline-flex',
                'align-items:center',
                'gap:8px',
                'padding:10px 14px',
                'border-radius:999px',
                'border:1px solid rgba(255,255,255,0.18)',
                'background:{panel_background}cc',
                'backdrop-filter:blur(6px)',
                'color:#f8fafc',
                'font-size:11px',
                'font-weight:800',
                'letter-spacing:0.24em',
                'text-transform:uppercase'
            ].join(';');
            metaRow.appendChild(badgeEl);

            const footer = document.createElement('div');
            footer.style.cssText = [
                'display:flex',
                'flex-direction:column',
                'gap:12px',
                'max-width:{content_width}',
                'align-self:flex-start'
            ].join(';');

            const accent = document.createElement('div');
            accent.style.cssText = [
                'width:88px',
                'height:6px',
                'border-radius:999px',
                'background:linear-gradient(90deg, {accent_start} 0%, {accent_end} 100%)',
                'box-shadow:0 0 20px {accent_end}55'
            ].join(';');

            const titleEl = document.createElement('div');
            titleEl.textContent = {json.dumps(title)};
            titleEl.style.cssText = [
                'color:#ffffff',
                'font-size:{title_font}',
                'font-weight:900',
                'line-height:0.94',
                'letter-spacing:-0.04em',
                'text-shadow:0 10px 30px rgba(15,23,42,0.55)'
            ].join(';');

            const subtitleEl = document.createElement('div');
            subtitleEl.textContent = {json.dumps(subtitle)};
            subtitleEl.style.cssText = [
                'color:rgba(241,245,249,0.88)',
                'font-size:14px',
                'font-weight:600',
                'letter-spacing:0.04em',
                'text-transform:uppercase'
            ].join(';');

            footer.appendChild(accent);
            footer.appendChild(titleEl);
            footer.appendChild(subtitleEl);

            overlay.appendChild(wash);
            overlay.appendChild(gloss);
            overlay.appendChild(frame);
            content.appendChild(metaRow);
            content.appendChild(footer);
            overlay.appendChild(content);
            host.appendChild(overlay);
            return true;
        }} catch (_error) {{
            return false;
        }}
    }})()
    """
    try:
        return bool(await page.evaluate(script))
    except Exception:
        return False


def _cover_candidate_text(candidate: dict[str, Any]) -> str:
    dom_fingerprint = candidate.get("dom_fingerprint")
    if not isinstance(dom_fingerprint, dict):
        return ""

    parts = [
        str(dom_fingerprint.get("title") or ""),
        str(dom_fingerprint.get("bodyText") or ""),
    ]
    return re.sub(r"\s+", " ", " ".join(parts)).strip().lower()


def _cover_candidate_stage_bonus(
    label: str,
    *,
    canvas_changed_after_input: bool,
    dom_changed_after_input: bool,
) -> float:
    interaction_visible = canvas_changed_after_input or dom_changed_after_input
    if interaction_visible:
        if label == "settled_frame":
            return 0.8
        if label == "action_frame":
            return 0.45
        return 0.0

    if label == "ready_frame":
        return 0.2
    if label == "action_frame":
        return 0.1
    return 0.0


def _score_cover_candidate(
    candidate: dict[str, Any],
    *,
    canvas_changed_after_input: bool,
    dom_changed_after_input: bool,
) -> tuple[float, list[str]]:
    label = str(candidate.get("label") or "")
    text = _cover_candidate_text(candidate)
    palette_source = str(candidate.get("cover_palette_source") or "")
    dom_fingerprint = candidate.get("dom_fingerprint")
    dom_count = int(dom_fingerprint.get("count") or 0) if isinstance(dom_fingerprint, dict) else 0
    score = 0.0
    reasons: list[str] = []

    if candidate.get("canvas_renders"):
        score += 3.5
        reasons.append("canvas_renders")
    else:
        score -= 3.5
        reasons.append("no_canvas_render")

    if palette_source == "canvas":
        score += 0.45
        reasons.append("palette_from_canvas")
    elif palette_source == "body":
        score += 0.1
        reasons.append("palette_from_body")
    else:
        score -= 0.15
        reasons.append("palette_fallback")

    if dom_count > 0:
        score += min(dom_count, 10) * 0.04
        reasons.append("visible_dom")

    negative_groups = (
        ("game_over_text", 4.8, ("game over", "try again", "you lose", "defeat", "failed", "mission failed")),
        ("menu_text", 2.8, ("tap to start", "click to start", "press start", "start game", "how to play", "tutorial")),
        ("pause_text", 2.2, ("paused", "pause menu", "resume game")),
        ("loading_text", 3.4, ("loading", "please wait", "generating", "booting")),
    )
    for reason, penalty, phrases in negative_groups:
        if any(phrase in text for phrase in phrases):
            score -= penalty
            reasons.append(reason)

    gameplay_markers = (
        "score", "combo", "coins", "collect", "distance", "wave", "level", "survive",
        "timer", "enemy", "boss", "hp", "health", "lap", "stars",
    )
    if any(marker in text for marker in gameplay_markers):
        score += 1.25
        reasons.append("gameplay_text")

    stage_bonus = _cover_candidate_stage_bonus(
        label,
        canvas_changed_after_input=canvas_changed_after_input,
        dom_changed_after_input=dom_changed_after_input,
    )
    score += stage_bonus
    reasons.append(f"stage_bonus:{stage_bonus:.2f}")

    return score, reasons


def _select_cover_candidate(
    candidates: list[dict[str, Any]],
    *,
    canvas_changed_after_input: bool,
    dom_changed_after_input: bool,
) -> dict[str, Any] | None:
    if not candidates:
        return None

    best_candidate: dict[str, Any] | None = None
    best_score = float("-inf")
    best_stage_bonus = float("-inf")
    best_index = len(candidates)

    for index, candidate in enumerate(candidates):
        score, reasons = _score_cover_candidate(
            candidate,
            canvas_changed_after_input=canvas_changed_after_input,
            dom_changed_after_input=dom_changed_after_input,
        )
        stage_bonus = _cover_candidate_stage_bonus(
            str(candidate.get("label") or ""),
            canvas_changed_after_input=canvas_changed_after_input,
            dom_changed_after_input=dom_changed_after_input,
        )
        candidate["quality_score"] = round(score, 4)
        candidate["quality_reasons"] = reasons
        candidate["stage_bonus"] = round(stage_bonus, 4)

        if (
            best_candidate is None
            or score > best_score + 1e-9
            or (
                abs(score - best_score) <= 1e-9
                and (
                    stage_bonus > best_stage_bonus + 1e-9
                    or (
                        abs(stage_bonus - best_stage_bonus) <= 1e-9
                        and index < best_index
                    )
                )
            )
        ):
            best_candidate = candidate
            best_score = score
            best_stage_bonus = stage_bonus
            best_index = index

    return best_candidate or candidates[0]


async def capture_cover_artifact(
    html_code: str,
    *,
    orientation: str | None = None,
    timeout_s: float | None = None,
    title: str | None = None,
    game_type: str | None = None,
    theme: str | None = None,
    runtime_profile: str | None = None,
    updated: bool = False,
) -> dict[str, Any] | None:
    effective_timeout_s = max(float(timeout_s if timeout_s is not None else get_timeout_float(
        "timeout.ai_engine.cover_capture_s",
        4.0,
        min_value=0.1,
    )), 0.1)
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        logger.debug("Playwright not installed, skipping cover capture: %s", exc)
        return None

    browser = None
    viewport = _cover_viewport(orientation)

    async def _execute() -> dict[str, Any] | None:
        nonlocal browser

        async with async_playwright() as p:
            launch_kwargs = dict(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-zygote",
                ],
            )
            chromium_executable = _resolve_chromium_executable()
            if chromium_executable:
                launch_kwargs["executable_path"] = chromium_executable

            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(viewport=viewport)
            page = await context.new_page()

            page.on("pageerror", lambda exc: logger.debug("Cover capture page error: %s", exc))
            page.on("console", lambda msg: (
                logger.debug("Cover capture console error: %s", msg.text)
                if msg.type == "error"
                else None
            ))

            await page.add_init_script(_INSTRUMENTATION_JS)
            await page.set_content(_inject_probe_script(html_code), wait_until="load")
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("load", timeout=500)
            await asyncio.sleep(0.35)

            candidates: list[dict[str, Any]] = []

            async def _capture(label: str) -> None:
                canvas_fingerprint = await page.evaluate(_CANVAS_FINGERPRINT_JS)
                dom_fingerprint = await page.evaluate(_DOM_FINGERPRINT_JS)
                canvas_renders = bool(await page.evaluate(_CANVAS_CHECK_JS))
                extracted_palette = await page.evaluate(_FRAME_PALETTE_JS)
                dom_title = (
                    str(dom_fingerprint.get("title") or "")
                    if isinstance(dom_fingerprint, dict)
                    else ""
                )
                cover_copy = _resolve_cover_copy(
                    requested_title=title,
                    dom_title=dom_title,
                    game_type=game_type,
                    theme=theme,
                    runtime_profile=runtime_profile,
                    updated=updated,
                )
                cover_palette = _resolve_cover_palette(
                    theme=theme,
                    extracted_palette=extracted_palette if isinstance(extracted_palette, dict) else None,
                )
                overlay_applied = await _apply_cover_overlay(
                    page,
                    orientation=orientation,
                    title=cover_copy["title"],
                    badge=cover_copy["badge"],
                    subtitle=cover_copy["subtitle"],
                    palette=cover_palette,
                )
                if overlay_applied:
                    await asyncio.sleep(0.03)
                try:
                    image_bytes = await page.screenshot(type="jpeg", quality=58, scale="css")
                finally:
                    await _remove_cover_overlay(page)
                candidates.append({
                    "label": label,
                    "canvas_renders": canvas_renders,
                    "canvas_fingerprint": canvas_fingerprint,
                    "dom_fingerprint": dom_fingerprint,
                    "payload": base64.b64encode(image_bytes).decode("ascii"),
                    "size_bytes": len(image_bytes),
                    "cover_overlay_applied": overlay_applied,
                    "cover_title": cover_copy["title"],
                    "cover_badge": cover_copy["badge"],
                    "cover_subtitle": cover_copy["subtitle"],
                    "cover_palette_source": cover_palette["source"],
                    "cover_palette": {
                        "primary": cover_palette["primary"],
                        "secondary": cover_palette["secondary"],
                        "background": cover_palette["background"],
                    },
                })

            await _capture("ready_frame")
            initial_canvas = candidates[0].get("canvas_fingerprint")
            initial_dom = candidates[0].get("dom_fingerprint")

            await page.evaluate(_INTERACTION_JS)
            await asyncio.sleep(0.2)
            await _capture("action_frame")

            await asyncio.sleep(0.35)
            await _capture("settled_frame")

            final_canvas = candidates[-1].get("canvas_fingerprint")
            final_dom = candidates[-1].get("dom_fingerprint")
            canvas_changed_after_input = (
                initial_canvas is not None
                and final_canvas is not None
                and initial_canvas != final_canvas
            )
            dom_changed_after_input = _dom_fingerprint_changed(initial_dom, final_dom)
            selected = _select_cover_candidate(
                candidates,
                canvas_changed_after_input=canvas_changed_after_input,
                dom_changed_after_input=dom_changed_after_input,
            )
            if not selected:
                return None

            return {
                "payload": selected["payload"],
                "content_type": "image/jpeg",
                "metadata": {
                    "encoding": "base64",
                    "selectedFrame": selected["label"],
                    "orientation": "landscape" if viewport["width"] > viewport["height"] else "portrait",
                    "width": viewport["width"],
                    "height": viewport["height"],
                    "coverStyle": "posterized_overlay" if selected.get("cover_overlay_applied") else "screenshot",
                    "overlayApplied": bool(selected.get("cover_overlay_applied")),
                    "title": str(selected.get("cover_title") or ""),
                    "badge": str(selected.get("cover_badge") or ""),
                    "subtitle": str(selected.get("cover_subtitle") or ""),
                    "theme": str(theme or ""),
                    "qualityScore": float(selected.get("quality_score") or 0.0),
                    "qualityReasons": list(selected.get("quality_reasons") or []),
                    "paletteSource": str(selected.get("cover_palette_source") or "theme_fallback"),
                    "palette": dict(selected.get("cover_palette") or {}),
                    "canvasChangedAfterInput": canvas_changed_after_input,
                    "domChangedAfterInput": dom_changed_after_input,
                    "candidates": [
                        {
                            "label": candidate["label"],
                            "canvasRenders": candidate["canvas_renders"],
                            "sizeBytes": candidate["size_bytes"],
                            "overlayApplied": bool(candidate.get("cover_overlay_applied")),
                            "qualityScore": float(candidate.get("quality_score") or 0.0),
                            "qualityReasons": list(candidate.get("quality_reasons") or []),
                            "paletteSource": str(candidate.get("cover_palette_source") or "theme_fallback"),
                        }
                        for candidate in candidates
                    ],
                },
            }

    try:
        return await asyncio.wait_for(_execute(), timeout=min(effective_timeout_s, 6.0))
    except Exception as exc:
        logger.debug("Cover capture skipped: %s", exc)
        return None
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()


async def run_runtime_qa(html_code: str, timeout_s: float | None = None) -> RuntimeQAResult:
    """Run headless browser QA and a lightweight interaction smoke test."""
    effective_timeout_s = max(float(timeout_s if timeout_s is not None else get_timeout_float(
        "timeout.ai_engine.runtime_qa.base_s",
        8.0,
        min_value=0.1,
    )), 0.1)
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        logger.warning("Playwright not installed, skipping runtime QA")
        return RuntimeQAResult(
            ran=False,
            unavailable_reason=f"playwright_import_error: {exc}",
            unavailable_kind="infra_unavailable",
            unavailable_phase="bootstrap",
        )

    js_errors: List[str] = []
    start = time.perf_counter()
    browser = None
    overall_timeout_s = _overall_timeout_s(effective_timeout_s=effective_timeout_s)
    phase_metrics: Dict[str, Any] = {
        "requested_timeout_s": effective_timeout_s,
        "overall_timeout_s": overall_timeout_s,
    }

    async def _execute() -> RuntimeQAResult:
        nonlocal browser

        async def _run_phase(
            phase: str,
            operation: Callable[[], Awaitable[T]],
            *,
            timeout_key: str,
            default_timeout_s: float,
        ) -> T:
            phase_start = time.perf_counter()
            timeout_budget_s = _phase_timeout_s(
                timeout_key,
                default_timeout_s,
                effective_timeout_s=effective_timeout_s,
            )
            phase_metrics[f"{phase}_timeout_s"] = timeout_budget_s
            try:
                result = await asyncio.wait_for(operation(), timeout=timeout_budget_s)
            except asyncio.TimeoutError as exc:
                phase_metrics[f"{phase}_elapsed_ms"] = int((time.perf_counter() - phase_start) * 1000)
                raise RuntimeQAPhaseTimeoutError(phase, timeout_budget_s) from exc
            phase_metrics[f"{phase}_elapsed_ms"] = int((time.perf_counter() - phase_start) * 1000)
            return result

        async with async_playwright() as p:
            launch_kwargs = dict(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--no-zygote",
                ],
            )
            chromium_executable = _resolve_chromium_executable()
            if chromium_executable:
                launch_kwargs["executable_path"] = chromium_executable

            async def _launch_phase():
                nonlocal browser
                browser = await p.chromium.launch(**launch_kwargs)
                context = await browser.new_context(
                    viewport={"width": 420, "height": 700},
                )
                return await context.new_page()

            page = await _run_phase(
                "launch",
                _launch_phase,
                timeout_key="timeout.ai_engine.runtime_qa.phase_launch_s",
                default_timeout_s=8.0,
            )

            page.on("console", lambda msg: (
                js_errors.append(msg.text) if msg.type == "error" else None
            ))
            page.on("pageerror", lambda exc: js_errors.append(str(exc)))

            async def _content_load_phase() -> None:
                await page.add_init_script(_INSTRUMENTATION_JS)
                await page.set_content(_inject_probe_script(html_code), wait_until="load")
                load_wait_timeout_ms = max(
                    get_timeout_int("timeout.ai_engine.runtime_qa.load_wait_min_ms", 250, min_value=1),
                    int(
                        effective_timeout_s * get_timeout_float(
                            "timeout.ai_engine.runtime_qa.load_wait_factor_ms_per_s",
                            250.0,
                            min_value=1.0,
                        )
                    ),
                )
                phase_metrics["load_wait_timeout_ms"] = load_wait_timeout_ms
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state(
                        "load",
                        timeout=load_wait_timeout_ms,
                    )

                initial_wait_s = min(
                    get_timeout_float("timeout.ai_engine.runtime_qa.initial_wait_max_s", 2.0, min_value=0.01),
                    max(
                        effective_timeout_s * get_timeout_float("timeout.ai_engine.runtime_qa.initial_wait_ratio", 0.25, min_value=0.0),
                        get_timeout_float("timeout.ai_engine.runtime_qa.initial_wait_min_s", 0.35, min_value=0.0),
                    ),
                )
                phase_metrics["initial_wait_s"] = initial_wait_s
                await asyncio.sleep(initial_wait_s)

            await _run_phase(
                "content_load",
                _content_load_phase,
                timeout_key="timeout.ai_engine.runtime_qa.phase_content_load_s",
                default_timeout_s=15.0,
            )

            async def _interaction_phase() -> tuple[Any, Any, Any]:
                canvas_fingerprint_before = await page.evaluate(_CANVAS_FINGERPRINT_JS)
                dom_fingerprint_before = await page.evaluate(_DOM_FINGERPRINT_JS)
                canvas_renders = await page.evaluate(_CANVAS_CHECK_JS)
                await page.evaluate(_INTERACTION_JS)
                post_interaction_wait_s = min(
                    get_timeout_float("timeout.ai_engine.runtime_qa.post_wait_max_s", 0.8, min_value=0.01),
                    max(
                        effective_timeout_s * get_timeout_float("timeout.ai_engine.runtime_qa.post_wait_ratio", 0.15, min_value=0.0),
                        get_timeout_float("timeout.ai_engine.runtime_qa.post_wait_min_s", 0.15, min_value=0.0),
                    ),
                )
                phase_metrics["post_interaction_wait_s"] = post_interaction_wait_s
                await asyncio.sleep(post_interaction_wait_s)
                return canvas_fingerprint_before, dom_fingerprint_before, canvas_renders

            canvas_fingerprint_before, dom_fingerprint_before, canvas_renders = await _run_phase(
                "interaction",
                _interaction_phase,
                timeout_key="timeout.ai_engine.runtime_qa.phase_interaction_s",
                default_timeout_s=6.0,
            )

            async def _collect_phase() -> tuple[Any, Any, Any]:
                canvas_fingerprint_after = await page.evaluate(_CANVAS_FINGERPRINT_JS)
                dom_fingerprint_after = await page.evaluate(_DOM_FINGERPRINT_JS)
                collected = await page.evaluate(_COLLECT_JS)
                return canvas_fingerprint_after, dom_fingerprint_after, collected

            canvas_fingerprint_after, dom_fingerprint_after, collected = await _run_phase(
                "collect",
                _collect_phase,
                timeout_key="timeout.ai_engine.runtime_qa.phase_collect_s",
                default_timeout_s=5.0,
            )

            fps = float(collected.get("fps", 0))
            game_over_triggered = bool(collected.get("gameOverReceived", False))
            page_errors = list(collected.get("errors", []))
            registered_input_handlers = list(collected.get("registeredInputHandlers", []))
            direct_input_handlers = list(collected.get("directInputHandlers", []))
            triggered_input_handlers = list(collected.get("triggeredInputHandlers", []))
            interaction_performed = bool(collected.get("interactionPerformed", False))
            js_errors.extend(page_errors)

            canvas_changed_after_input = (
                canvas_fingerprint_before is not None
                and canvas_fingerprint_after is not None
                and canvas_fingerprint_before != canvas_fingerprint_after
            )
            dom_changed_after_input = _dom_fingerprint_changed(
                dom_fingerprint_before,
                dom_fingerprint_after,
            )
            load_time_ms = int((time.perf_counter() - start) * 1000)
            phase_metrics["total_elapsed_ms"] = load_time_ms

            logger.info(
                "Runtime QA: canvas_renders=%s, fps=%.1f, js_errors=%s, input_signals=%s, load_ms=%s",
                canvas_renders,
                fps,
                len(js_errors),
                len(set(registered_input_handlers + direct_input_handlers)),
                load_time_ms,
            )
            return RuntimeQAResult(
                ran=True,
                canvas_renders=bool(canvas_renders),
                js_errors=js_errors[:10],
                fps=fps,
                load_time_ms=load_time_ms,
                game_over_triggered=game_over_triggered,
                registered_input_handlers=registered_input_handlers,
                direct_input_handlers=direct_input_handlers,
                triggered_input_handlers=triggered_input_handlers,
                interaction_performed=interaction_performed,
                canvas_changed_after_input=canvas_changed_after_input,
                dom_changed_after_input=dom_changed_after_input,
                phase_metrics=dict(phase_metrics),
            )

    try:
        return await asyncio.wait_for(_execute(), timeout=overall_timeout_s)
    except RuntimeQAPhaseTimeoutError as exc:
        logger.warning("Runtime QA timed out during phase %s", exc.phase)
        phase_metrics["total_elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        return RuntimeQAResult(
            ran=False,
            unavailable_reason=f"runtime_qa_timeout:{exc.phase}:{exc.timeout_s:.2f}s",
            unavailable_kind="timeout",
            unavailable_phase=exc.phase,
            phase_metrics=dict(phase_metrics),
        )
    except asyncio.TimeoutError:
        logger.warning("Runtime QA timed out")
        phase_metrics["total_elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        return RuntimeQAResult(
            ran=False,
            unavailable_reason=f"runtime_qa_timeout:overall:{overall_timeout_s:.2f}s",
            unavailable_kind="timeout",
            unavailable_phase="overall",
            phase_metrics=dict(phase_metrics),
        )
    except Exception as exc:
        logger.warning(f"Runtime QA failed: {exc}")
        phase_metrics["total_elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        return RuntimeQAResult(
            ran=False,
            unavailable_reason=f"runtime_qa_exception:{exc}",
            unavailable_kind="infra_unavailable",
            unavailable_phase="execution",
            phase_metrics=dict(phase_metrics),
        )
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
