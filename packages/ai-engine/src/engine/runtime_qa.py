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

from .runtime_isolation import network_policy_meta, restrict_context_network

import asyncio
import base64
import contextlib
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, TypeVar

from ..config.settings import settings
from ..config.timeout_store import get_float as get_timeout_float, get_int as get_timeout_int
from .quality_scorer import RuntimeQAResult
from .visual_pack_catalog import get_visual_pack, select_visual_pack

logger = logging.getLogger(__name__)
T = TypeVar("T")
_RUNTIME_QA_SEMAPHORES: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]]" = weakref.WeakKeyDictionary()
_RUNTIME_QA_SEMAPHORE_LOCK = threading.Lock()


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
    window.__qaInteractionScheduled = false;
    window.__qaInteractionPending = false;

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
        const x = Math.max(0, Math.floor((canvas.width - w) / 2));
        const y = Math.max(0, Math.floor((canvas.height - h) / 2));
        const data = ctx.getImageData(x, y, w, h).data;
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
        const x = Math.max(0, Math.floor((canvas.width - w) / 2));
        const y = Math.max(0, Math.floor((canvas.height - h) / 2));
        const data = ctx.getImageData(x, y, w, h).data;
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
                    const sampleX = Math.max(0, Math.floor(((canvas.width || canvas.clientWidth || 0) - sampleW) / 2));
                    const sampleY = Math.max(0, Math.floor(((canvas.height || canvas.clientHeight || 0) - sampleH) / 2));
                    const data = ctx.getImageData(sampleX, sampleY, sampleW, sampleH).data;
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
    const midX = rect.left + Math.max(8, (rect.width || 320) / 2);
    const midY = rect.top + Math.max(8, (rect.height || 480) / 2);
    const cell = Math.max(28, Math.min(72, Math.round(Math.min(rect.width || 320, rect.height || 480) / 8)));
    const tapPoints = [
        [midX, midY],
        [midX + cell, midY],
        [midX, midY + cell],
        [midX - cell, midY],
        [midX, midY - cell],
        [midX + cell, midY + cell]
    ];
    const clientX = tapPoints[0][0];
    const clientY = tapPoints[0][1];
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

    const pointInit = function(x, y, extra) {
        return Object.assign({
            clientX: x,
            clientY: y,
            offsetX: x - (rect.left || 0),
            offsetY: y - (rect.top || 0),
            button: 0
        }, extra || {});
    };
    const tapAt = function(x, y) {
        dispatch(target, 'pointerdown', 'PointerEvent', pointInit(x, y, { pointerId: 1, pointerType: 'touch', buttons: 1 }));
        dispatch(target, 'pointerup', 'PointerEvent', pointInit(x, y, { pointerId: 1, pointerType: 'touch', buttons: 0 }));
        dispatch(target, 'mousedown', 'MouseEvent', pointInit(x, y, { buttons: 1 }));
        dispatch(target, 'mouseup', 'MouseEvent', pointInit(x, y, { buttons: 0 }));
        dispatch(target, 'click', 'MouseEvent', pointInit(x, y));
    };
    const clickVisibleControls = function() {
        const nodes = Array.from(document.querySelectorAll('button, [role="button"], [data-action], input[type="button"]'));
        const matched = nodes.filter(function(node) {
            const hint = ((node.getAttribute('aria-label') || '') + ' ' + (node.id || '') + ' ' + (node.textContent || '')).toLowerCase();
            return /start|play|hint|shuffle|reset|restart|new|开始|提示|重来|重置/.test(hint);
        }).slice(0, 3);
        matched.forEach(function(node) {
            try { node.click(); } catch (e) { /* ignore control click failures */ }
        });
    };
    const interactionSteps = [
        function() { tapAt(tapPoints[0][0], tapPoints[0][1]); },
        function() { tapAt(tapPoints[1][0], tapPoints[1][1]); },
        function() {
            dispatch(target, 'pointerdown', 'PointerEvent', pointInit(clientX, clientY, { pointerId: 1, pointerType: 'touch', buttons: 1 }));
        },
        function() {
            dispatch(target, 'pointermove', 'PointerEvent', pointInit(clientX + cell, clientY - cell, { pointerId: 1, pointerType: 'touch', buttons: 1 }));
        },
        function() {
            dispatch(target, 'pointerup', 'PointerEvent', pointInit(clientX + cell, clientY - cell, { pointerId: 1, pointerType: 'touch', buttons: 0 }));
        },
        function() { dispatchTouch('touchstart', clientX, clientY); },
        function() { dispatchTouch('touchmove', clientX + cell, clientY - cell); },
        function() { dispatchTouch('touchend', clientX + cell, clientY - cell); },
        function() { tapAt(tapPoints[2][0], tapPoints[2][1]); },
        function() { clickVisibleControls(); },
        function() { dispatch(document, 'keydown', 'KeyboardEvent', { key: 'ArrowUp', code: 'ArrowUp' }); },
        function() { dispatch(document, 'keyup', 'KeyboardEvent', { key: 'ArrowUp', code: 'ArrowUp' }); },
        function() { dispatch(document, 'keydown', 'KeyboardEvent', { key: 'ArrowLeft', code: 'ArrowLeft' }); },
        function() { dispatch(document, 'keyup', 'KeyboardEvent', { key: 'ArrowLeft', code: 'ArrowLeft' }); },
        function() { dispatch(document, 'keydown', 'KeyboardEvent', { key: ' ', code: 'Space' }); },
        function() { dispatch(document, 'keyup', 'KeyboardEvent', { key: ' ', code: 'Space' }); }
    ];

    window.__qaInteractionScheduled = true;
    window.__qaInteractionPending = true;

    const runStep = function(index) {
        if (index >= interactionSteps.length) {
            window.__qaInteractionPerformed = true;
            window.__qaInteractionPending = false;
            return;
        }
        try {
            interactionSteps[index]();
        } catch (e) {
            window.__qaErrors.push('Interaction step failed at ' + index + ': ' + e.message);
        }
        setTimeout(function() { runStep(index + 1); }, index < 3 ? 0 : 16);
    };

    setTimeout(function() { runStep(0); }, 0);
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
    interactionScheduled: window.__qaInteractionScheduled || false,
    interactionPending: window.__qaInteractionPending || false,
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


def _runtime_qa_max_concurrency() -> int:
    # The provisioned FC worker has 1 GB RAM; serialize Chromium by default.
    default_limit = 1 if os.getenv("FC_DEPLOYMENT") == "true" else 4
    return max(get_timeout_int("timeout.ai_engine.runtime_qa.max_concurrency", default_limit, min_value=1), 1)


def _runtime_qa_semaphore(max_concurrency: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    with _RUNTIME_QA_SEMAPHORE_LOCK:
        cached = _RUNTIME_QA_SEMAPHORES.get(loop)
        if cached is not None:
            cached_limit, semaphore = cached
            if cached_limit == max_concurrency:
                return semaphore
        semaphore = asyncio.Semaphore(max_concurrency)
        _RUNTIME_QA_SEMAPHORES[loop] = (max_concurrency, semaphore)
        return semaphore


def _phase_timeout_s(
    key: str,
    default: float,
    *,
    effective_timeout_s: float,
    ratio_default: float = 0.0,
) -> float:
    configured = max(get_timeout_float(key, default, min_value=0.05), 0.05)
    ratio_key = f"{key}_ratio_of_total"
    ratio = get_timeout_float(ratio_key, ratio_default, min_value=0.0)
    scaled = effective_timeout_s * ratio if ratio > 0 else configured
    return max(min(max(configured, scaled), effective_timeout_s), 0.05)


def _overall_timeout_s(*, effective_timeout_s: float) -> float:
    phase_budget_s = sum((
        _phase_timeout_s(
            "timeout.ai_engine.runtime_qa.phase_launch_s",
            8.0,
            effective_timeout_s=effective_timeout_s,
            ratio_default=0.22,
        ),
        _phase_timeout_s(
            "timeout.ai_engine.runtime_qa.phase_content_load_s",
            15.0,
            effective_timeout_s=effective_timeout_s,
            ratio_default=0.42,
        ),
        _phase_timeout_s(
            "timeout.ai_engine.runtime_qa.phase_interaction_s",
            6.0,
            effective_timeout_s=effective_timeout_s,
            ratio_default=0.28,
        ),
        _phase_timeout_s(
            "timeout.ai_engine.runtime_qa.phase_collect_s",
            5.0,
            effective_timeout_s=effective_timeout_s,
            ratio_default=0.18,
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
    if str(orientation or "").strip().lower() in {"landscape", "landscape_first"}:
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


def _cover_generation_tier_from_intensity(render_style_intensity: str | None) -> str:
    token = str(render_style_intensity or "").strip().lower()
    if token in {"high", "max", "showcase", "bold"}:
        return "showcase"
    if token in {"restrained", "soft", "low", "minimal"}:
        return "safe"
    return "standard"


def _resolve_cover_visual_pack(
    *,
    visual_pack: str | None,
    game_type: str | None,
    theme: str | None,
    render_style_intensity: str | None,
    variation_seed: str | None,
) -> dict[str, Any] | None:
    explicit_pack = get_visual_pack(visual_pack)
    if explicit_pack:
        return explicit_pack
    normalized_game_type = str(game_type or "").strip().lower() or "casual"
    return select_visual_pack(
        game_type=normalized_game_type,
        theme=str(theme or "").strip().lower(),
        generation_tier=_cover_generation_tier_from_intensity(render_style_intensity),
        variation_seed=variation_seed,
    )


def _visual_pack_cover_palette(pack: dict[str, Any] | None) -> tuple[str, str, str] | None:
    if not pack:
        return None
    raw_palette = pack.get("palette") or []
    palette = [color for color in (_normalize_hex_color(item) for item in raw_palette) if color]
    if not palette:
        return None
    primary = palette[1] if len(palette) > 1 else palette[0]
    secondary = palette[2] if len(palette) > 2 else (palette[-1] if len(palette) > 1 else _mix_hex(primary, "#ffffff", 0.26))
    background_seed = palette[0]
    background = _mix_hex(background_seed, "#020617", 0.52)
    if _color_distance(background, primary) < 28:
        background = _mix_hex(background, "#020617", 0.34)
    return primary, secondary, background


def _resolve_cover_palette(
    *,
    theme: str | None,
    extracted_palette: dict[str, Any] | None,
    visual_pack: str | None = None,
    render_style_intensity: str | None = None,
    game_type: str | None = None,
) -> dict[str, str]:
    pack = _resolve_cover_visual_pack(
        visual_pack=visual_pack,
        game_type=game_type,
        theme=theme,
        render_style_intensity=render_style_intensity,
        variation_seed="|".join(
            [
                str(visual_pack or "").strip().lower(),
                str(theme or "").strip().lower(),
                str(game_type or "").strip().lower(),
            ]
        ),
    )
    pack_palette = _visual_pack_cover_palette(pack)
    fallback_primary, fallback_secondary = _theme_cover_palette(theme)
    fallback_background = _mix_hex(fallback_primary, "#040816", 0.78)
    fallback_source = "theme_fallback"
    if pack_palette:
        fallback_primary, fallback_secondary, fallback_background = pack_palette
        fallback_source = "visual_pack_fallback"

    source = str((extracted_palette or {}).get("source") or fallback_source).strip() or fallback_source
    primary = _normalize_hex_color((extracted_palette or {}).get("primary")) or fallback_primary
    secondary = _normalize_hex_color((extracted_palette or {}).get("secondary")) or fallback_secondary
    background = _normalize_hex_color((extracted_palette or {}).get("background"))

    if _color_distance(primary, secondary) < 36:
        secondary = fallback_secondary if _color_distance(primary, fallback_secondary) >= 36 else _mix_hex(primary, "#ffffff", 0.32)

    if not background:
        background = fallback_background

    if _color_distance(background, primary) < 28:
        background = _mix_hex(background, "#020617", 0.5)

    return {
        "source": source if source in {"canvas", "body"} else fallback_source,
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
    runtime_token_map = {
        "casual_arcade": "arcade spark",
        "casual_lane": "lane rush",
        "puzzle_grid": "grid puzzle",
        "casual_action": "action scene",
        "tap_challenge": "tap challenge",
    }
    normalized_runtime_profile = str(runtime_profile or "").strip().lower()
    runtime_token = _normalize_cover_text(
        runtime_token_map.get(normalized_runtime_profile, normalized_runtime_profile.replace("_", " ").replace("-", " ")),
        max_len=18,
    )
    normalized_game_type = str(game_type or "").strip().lower()
    game_token = ""
    if normalized_game_type and normalized_game_type not in {"casual", "puzzle", "educational", "funny"}:
        game_token = _normalize_cover_text(normalized_game_type.replace("_", " ").replace("-", " "), max_len=18)
    descriptor_token = runtime_token or game_token

    if not title:
        if theme_token and descriptor_token:
            title = f"{theme_token.title()} {descriptor_token.title()}"
        elif descriptor_token:
            title = descriptor_token.title()
        elif theme_token:
            title = theme_token.title()
        else:
            title = "Playable Build"

    badge_parts: list[str] = []
    if theme_token and theme_token.lower() != "arcade":
        badge_parts.append(theme_token.upper())
    if descriptor_token:
        badge_parts.append(descriptor_token.upper())
    badge = " ".join(badge_parts[:2]) or ("UPDATED BUILD" if updated else "NEW BUILD")

    if updated:
        subtitle = "Fresh gameplay snapshot"
    elif theme_token and descriptor_token:
        subtitle = f"{theme_token.title()} {descriptor_token.title()}"
    elif descriptor_token:
        subtitle = f"{descriptor_token.title()} scene"
    elif theme_token:
        subtitle = f"{theme_token.title()} world"
    else:
        subtitle = "Live gameplay"

    title = _normalize_cover_text(title, max_len=42) or "Playable Build"
    subtitle = _normalize_cover_text(subtitle, max_len=32) or "Playable build"

    return {
        "title": title,
        "badge": badge,
        "subtitle": subtitle,
    }

def _resolve_fallback_cover_variant(
    *,
    title: str,
    theme: str | None,
    runtime_profile: str | None,
    game_type: str | None,
    orientation: str | None,
    visual_pack: str | None = None,
) -> dict[str, Any]:
    seed = "|".join(
        [
            str(title or "").strip().lower(),
            str(theme or "").strip().lower(),
            str(runtime_profile or "").strip().lower(),
            str(game_type or "").strip().lower(),
            str(orientation or "").strip().lower(),
            str(visual_pack or "").strip().lower(),
        ]
    )
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    variant_names = ("orbital", "spotlight", "stacked", "diagonal")
    index = int(digest[:8], 16) % len(variant_names)
    return {
        "name": variant_names[index],
        "index": index,
        "seed": digest,
    }


def _build_visual_pack_cover_scene(
    *,
    pack: dict[str, Any] | None,
    width: int,
    height: int,
    primary: str,
    secondary: str,
    accent_soft: str,
    accent_warm: str,
    background: str,
    is_landscape: bool,
    variant_name: str,
) -> str:
    pack_id = str((pack or {}).get("id") or "").strip().lower()
    if not pack_id:
        pack_id = "neon_glass"

    if pack_id == "toy_3d":
        return f"""
  <circle cx="{int(width * 0.24)}" cy="{int(height * 0.24)}" r="{int(min(width, height) * 0.10)}" fill="{_mix_hex(primary, '#ffffff', 0.20)}" fill-opacity="0.44"/>
  <circle cx="{int(width * 0.78)}" cy="{int(height * 0.22)}" r="{int(min(width, height) * 0.08)}" fill="{accent_warm}" fill-opacity="0.34"/>
  <rect x="{int(width * 0.14)}" y="{int(height * 0.62)}" width="{int(width * 0.28)}" height="{int(height * 0.10)}" rx="999" fill="{_mix_hex(primary, '#ffffff', 0.18)}" fill-opacity="0.72"/>
  <rect x="{int(width * 0.58)}" y="{int(height * 0.66)}" width="{int(width * 0.20)}" height="{int(height * 0.08)}" rx="999" fill="{_mix_hex(secondary, '#ffffff', 0.16)}" fill-opacity="0.64"/>
"""

    if pack_id == "pixel_arcade":
        pixels: list[str] = []
        pixel_size = 18 if is_landscape else 16
        columns = 8 if is_landscape else 6
        rows = 5 if is_landscape else 8
        for row in range(rows):
            for col in range(columns):
                color = primary if (row + col) % 2 == 0 else secondary
                opacity = "0.18" if (row + col) % 3 else "0.30"
                pixels.append(
                    f'<rect x="{36 + col * (pixel_size + 10)}" y="{52 + row * (pixel_size + 10)}" width="{pixel_size}" height="{pixel_size}" rx="4" fill="{color}" fill-opacity="{opacity}"/>'
                )
        return "\n".join(pixels)

    if pack_id == "comic_bounce":
        return f"""
  <circle cx="{int(width * 0.18)}" cy="{int(height * 0.74)}" r="{int(min(width, height) * 0.06)}" fill="{accent_warm}" fill-opacity="0.24"/>
  <circle cx="{int(width * 0.24)}" cy="{int(height * 0.80)}" r="{int(min(width, height) * 0.03)}" fill="{primary}" fill-opacity="0.42"/>
  <path d="M {int(width * 0.74)} {int(height * 0.18)} L {int(width * 0.78)} {int(height * 0.08)} L {int(width * 0.82)} {int(height * 0.18)} L {int(width * 0.92)} {int(height * 0.22)} L {int(width * 0.82)} {int(height * 0.26)} L {int(width * 0.80)} {int(height * 0.38)} L {int(width * 0.74)} {int(height * 0.28)} L {int(width * 0.64)} {int(height * 0.24)} Z" fill="{secondary}" fill-opacity="0.54"/>
  <path d="M {int(width * 0.12)} {int(height * 0.22)} L {int(width * 0.20)} {int(height * 0.16)} L {int(width * 0.26)} {int(height * 0.24)} L {int(width * 0.18)} {int(height * 0.30)} Z" fill="{accent_soft}" fill-opacity="0.42"/>
"""

    if pack_id == "clean_edu":
        return f"""
  <rect x="{int(width * 0.12)}" y="{int(height * 0.20)}" width="{int(width * 0.26)}" height="{int(height * 0.12)}" rx="24" fill="{_mix_hex(primary, '#ffffff', 0.14)}" fill-opacity="0.40"/>
  <rect x="{int(width * 0.60)}" y="{int(height * 0.18)}" width="{int(width * 0.16)}" height="{int(height * 0.10)}" rx="20" fill="{_mix_hex(secondary, '#ffffff', 0.10)}" fill-opacity="0.34"/>
  <rect x="{int(width * 0.16)}" y="{int(height * 0.72)}" width="{int(width * 0.34)}" height="10" rx="999" fill="{accent_soft}" fill-opacity="0.46"/>
  <rect x="{int(width * 0.16)}" y="{int(height * 0.76)}" width="{int(width * 0.24)}" height="6" rx="999" fill="{accent_warm}" fill-opacity="0.36"/>
"""

    if pack_id == "retro_terminal":
        grid_lines: list[str] = []
        x_step = 42 if is_landscape else 30
        y_step = 38 if is_landscape else 34
        for x in range(24, width, x_step):
            grid_lines.append(f'<path d="M {x} 0 L {x} {height}" stroke="{primary}" stroke-opacity="0.10" stroke-width="1"/>')
        for y in range(24, height, y_step):
            grid_lines.append(f'<path d="M 0 {y} L {width} {y}" stroke="{secondary}" stroke-opacity="0.08" stroke-width="1"/>')
        grid_lines.append(
            f'<rect x="{int(width * 0.12)}" y="{int(height * 0.18)}" width="{int(width * 0.24)}" height="{int(height * 0.16)}" rx="18" fill="{_mix_hex(background, primary, 0.12)}" fill-opacity="0.42" stroke="{primary}" stroke-opacity="0.24"/>'
        )
        return "\n".join(grid_lines)

    if pack_id == "soft_fantasy":
        return f"""
  <path d="M {int(width * 0.14)} {int(height * 0.64)} C {int(width * 0.24)} {int(height * 0.48)}, {int(width * 0.34)} {int(height * 0.48)}, {int(width * 0.42)} {int(height * 0.62)}" stroke="{accent_soft}" stroke-width="16" stroke-linecap="round" opacity="0.42"/>
  <path d="M {int(width * 0.62)} {int(height * 0.28)} C {int(width * 0.70)} {int(height * 0.14)}, {int(width * 0.82)} {int(height * 0.18)}, {int(width * 0.88)} {int(height * 0.30)}" stroke="{secondary}" stroke-width="12" stroke-linecap="round" opacity="0.44"/>
  <circle cx="{int(width * 0.80)}" cy="{int(height * 0.66)}" r="{int(min(width, height) * 0.03)}" fill="#ffffff" fill-opacity="0.74"/>
  <circle cx="{int(width * 0.74)}" cy="{int(height * 0.60)}" r="{int(min(width, height) * 0.015)}" fill="{accent_warm}" fill-opacity="0.78"/>
"""

    if pack_id == "sports_broadcast":
        return f"""
  <path d="M {int(width * 0.08)} {int(height * 0.14)} L {int(width * 0.36)} {int(height * 0.14)} L {int(width * 0.30)} {int(height * 0.24)} L {int(width * 0.02)} {int(height * 0.24)} Z" fill="{accent_warm}" fill-opacity="0.46"/>
  <path d="M {int(width * 0.62)} {int(height * 0.76)} L {int(width * 0.94)} {int(height * 0.56)}" stroke="{secondary}" stroke-width="18" stroke-linecap="round" opacity="0.34"/>
  <path d="M {int(width * 0.66)} {int(height * 0.84)} L {int(width * 0.98)} {int(height * 0.64)}" stroke="{primary}" stroke-width="8" stroke-linecap="round" opacity="0.42"/>
"""

    if variant_name == "diagonal":
        return f"""
  <path d="M {int(width * 0.18)} {int(height * 0.78)} L {int(width * 0.86)} {int(height * 0.18)}" stroke="{accent_soft}" stroke-width="18" stroke-linecap="round" opacity="0.20"/>
  <path d="M {int(width * 0.12)} {int(height * 0.84)} L {int(width * 0.80)} {int(height * 0.24)}" stroke="{secondary}" stroke-width="8" stroke-linecap="round" opacity="0.22"/>
"""

    return f"""
  <circle cx="{int(width * 0.14)}" cy="{int(height * 0.20)}" r="{int(min(width, height) * 0.09)}" fill="{primary}" fill-opacity="0.18"/>
  <circle cx="{int(width * 0.84)}" cy="{int(height * 0.22)}" r="{int(min(width, height) * 0.07)}" fill="{secondary}" fill-opacity="0.18"/>
  <path d="M {int(width * 0.10)} {int(height * 0.72)} C {int(width * 0.32)} {int(height * 0.60)}, {int(width * 0.54)} {int(height * 0.74)}, {int(width * 0.78)} {int(height * 0.62)}" stroke="{accent_soft}" stroke-width="14" stroke-linecap="round" opacity="0.24"/>
"""


def _build_fallback_cover_artifact(
    *,
    viewport: dict[str, int],
    orientation: str | None,
    title: str,
    badge: str,
    subtitle: str,
    theme: str | None,
    updated: bool,
    runtime_profile: str | None,
    game_type: str | None,
    reason: str,
    visual_pack: str | None = None,
    render_style_intensity: str | None = None,
) -> dict[str, Any]:
    width = int(viewport.get("width") or 720)
    height = int(viewport.get("height") or 1280)
    cover_pack = _resolve_cover_visual_pack(
        visual_pack=visual_pack,
        game_type=game_type,
        theme=theme,
        render_style_intensity=render_style_intensity,
        variation_seed="|".join(
            [
                str(title or "").strip().lower(),
                str(theme or "").strip().lower(),
                str(runtime_profile or "").strip().lower(),
                str(game_type or "").strip().lower(),
            ]
        ),
    )
    pack_id = str((cover_pack or {}).get("id") or visual_pack or "").strip().lower()
    pack_name = str((cover_pack or {}).get("displayName") or pack_id.replace("_", " ").title() or "Signature Pack")
    font_family = html.escape(
        str(
            (cover_pack or {}).get("fontFamily")
            or "HarmonyOS Sans SC, PingFang SC, Microsoft YaHei, Segoe UI, sans-serif"
        ),
        quote=True,
    )
    palette = _resolve_cover_palette(
        theme=theme,
        extracted_palette=None,
        visual_pack=pack_id,
        render_style_intensity=render_style_intensity,
        game_type=game_type,
    )
    primary = palette["primary"]
    secondary = palette["secondary"]
    background = palette["background"]
    accent_soft = _mix_hex(primary, "#ffffff", 0.22)
    accent_warm = _mix_hex(secondary, "#f59e0b", 0.18)
    panel_bg = _mix_hex(background, "#101827", 0.32)
    shell_bg = _mix_hex(background, "#020617", 0.24)
    is_landscape = width > height
    fallback_variant = _resolve_fallback_cover_variant(
        title=title,
        theme=theme,
        runtime_profile=runtime_profile,
        game_type=game_type,
        orientation=orientation,
        visual_pack=pack_id,
    )
    variant_name = str(fallback_variant["name"])
    subtitle_font = 21 if is_landscape else 24
    badge_font = 17
    shell_x = 36
    shell_y = 42 if is_landscape else 54
    shell_w = width - shell_x * 2
    shell_h = height - shell_y * 2
    shell_radius = 34
    panel_x = shell_x + 26
    panel_y = shell_y + shell_h - (124 if is_landscape else 142)
    panel_w = int(shell_w * (0.44 if is_landscape else 0.74))
    panel_h = 94 if is_landscape else 108
    orb_cx = int(width * 0.78)
    orb_cy = int(height * (0.36 if is_landscape else 0.30))
    orb_r = int(min(width, height) * (0.16 if is_landscape else 0.18))
    info_x = int(width * 0.58)
    info_y = int(height * 0.14)
    info_w = int(shell_w * 0.28)
    info_h = 104
    hero_svg = ""
    accent_svg = ""

    if variant_name == "spotlight":
        panel_x = shell_x + int(shell_w * (0.48 if is_landscape else 0.10))
        panel_y = shell_y + int(shell_h * (0.68 if is_landscape else 0.68))
        panel_w = int(shell_w * (0.38 if is_landscape else 0.76))
        panel_h = 90 if is_landscape else 104
        orb_cx = int(width * 0.22)
        orb_cy = int(height * 0.22)
        orb_r = int(min(width, height) * (0.18 if is_landscape else 0.16))
        info_x = int(width * 0.54)
        info_y = int(height * 0.14)
        info_w = int(shell_w * 0.26)
        info_h = 98
        hero_svg = f"""
  <ellipse cx="{orb_cx}" cy="{orb_cy}" rx="{int(orb_r * 1.36)}" ry="{int(orb_r * 1.04)}" fill="{secondary}" fill-opacity="0.92"/>
  <ellipse cx="{orb_cx}" cy="{orb_cy}" rx="{int(orb_r * 0.86)}" ry="{int(orb_r * 0.68)}" fill="{primary}" fill-opacity="0.74"/>
  <circle cx="{orb_cx + int(orb_r * 0.18)}" cy="{orb_cy - int(orb_r * 0.10)}" r="{int(orb_r * 0.28)}" fill="#ffffff" fill-opacity="0.88"/>
"""
        accent_svg = f"""
  <rect x="{shell_x + 34}" y="{shell_y + 42}" width="{int(shell_w * 0.22)}" height="14" rx="999" fill="{accent_soft}" fill-opacity="0.82"/>
  <rect x="{shell_x + 34}" y="{shell_y + 66}" width="{int(shell_w * 0.15)}" height="10" rx="999" fill="{accent_warm}" fill-opacity="0.72"/>
"""
    elif variant_name == "stacked":
        panel_x = shell_x + 26
        panel_y = shell_y + shell_h - (138 if is_landscape else 154)
        panel_w = int(shell_w * (0.42 if is_landscape else 0.72))
        panel_h = 108 if is_landscape else 122
        orb_cx = int(width * 0.76)
        orb_cy = int(height * 0.72)
        orb_r = int(min(width, height) * (0.14 if is_landscape else 0.16))
        info_x = int(width * 0.56)
        info_y = int(height * 0.16)
        info_w = int(shell_w * 0.28)
        info_h = 102
        hero_svg = f"""
  <rect x="{int(width * 0.56)}" y="{int(height * 0.44)}" width="{int(shell_w * 0.24)}" height="{int(shell_h * 0.20)}" rx="28" fill="{_mix_hex(primary, '#ffffff', 0.18)}" fill-opacity="0.92"/>
  <rect x="{int(width * 0.60)}" y="{int(height * 0.48)}" width="{int(shell_w * 0.22)}" height="{int(shell_h * 0.18)}" rx="24" fill="{_mix_hex(primary, secondary, 0.28)}" fill-opacity="0.88"/>
  <rect x="{int(width * 0.64)}" y="{int(height * 0.52)}" width="{int(shell_w * 0.20)}" height="{int(shell_h * 0.16)}" rx="22" fill="{_mix_hex(secondary, '#111827', 0.18)}" fill-opacity="0.92"/>
"""
        accent_svg = f"""
  <circle cx="{orb_cx}" cy="{orb_cy}" r="{orb_r}" fill="{secondary}" fill-opacity="0.84"/>
  <circle cx="{orb_cx}" cy="{orb_cy}" r="{int(orb_r * 0.52)}" fill="#ffffff" fill-opacity="0.84"/>
"""
    elif variant_name == "diagonal":
        shell_radius = 40
        panel_x = shell_x + int(shell_w * 0.08)
        panel_y = shell_y + shell_h - (120 if is_landscape else 144)
        panel_w = int(shell_w * (0.46 if is_landscape else 0.76))
        panel_h = 92 if is_landscape else 108
        orb_cx = int(width * 0.82)
        orb_cy = int(height * 0.28)
        orb_r = int(min(width, height) * (0.15 if is_landscape else 0.17))
        info_x = int(width * 0.58)
        info_y = int(height * 0.18)
        info_w = int(shell_w * 0.26)
        info_h = 104
        hero_svg = f"""
  <path d="M {int(width * 0.58)} {int(height * 0.76)} L {int(width * 0.84)} {int(height * 0.42)}" stroke="{accent_soft}" stroke-width="20" stroke-linecap="round" opacity="0.74"/>
  <path d="M {int(width * 0.62)} {int(height * 0.82)} L {int(width * 0.88)} {int(height * 0.48)}" stroke="{secondary}" stroke-width="8" stroke-linecap="round" opacity="0.56"/>
  <circle cx="{orb_cx}" cy="{orb_cy}" r="{orb_r}" fill="{primary}" fill-opacity="0.88"/>
  <circle cx="{orb_cx}" cy="{orb_cy}" r="{int(orb_r * 0.48)}" fill="#ffffff" fill-opacity="0.86"/>
"""
        accent_svg = f"""
  <rect x="{shell_x + 32}" y="{int(height * 0.82)}" width="{int(shell_w * 0.22)}" height="12" rx="999" fill="{accent_warm}" fill-opacity="0.70"/>
"""
    else:
        hero_svg = f"""
  <circle cx="{orb_cx}" cy="{orb_cy}" r="{orb_r}" fill="{secondary}" fill-opacity="0.92"/>
  <circle cx="{orb_cx}" cy="{orb_cy}" r="{int(orb_r * 0.64)}" fill="{primary}" fill-opacity="0.68"/>
  <circle cx="{orb_cx}" cy="{orb_cy}" r="{int(orb_r * 0.34)}" fill="#ffffff" fill-opacity="0.92"/>
  <circle cx="{orb_cx + int(orb_r * 0.48)}" cy="{orb_cy - int(orb_r * 0.42)}" r="{int(orb_r * 0.14)}" fill="{accent_warm}" fill-opacity="0.95"/>
  <circle cx="{orb_cx - int(orb_r * 0.68)}" cy="{orb_cy + int(orb_r * 0.54)}" r="{int(orb_r * 0.10)}" fill="{accent_soft}" fill-opacity="0.88"/>
"""
        accent_svg = f"""
  <rect x="{info_x}" y="{info_y}" width="{info_w}" height="{info_h}" rx="28" fill="{_mix_hex(primary, '#0f172a', 0.48)}" fill-opacity="0.94" stroke="rgba(255,255,255,0.12)"/>
"""
    badge_text = html.escape((badge or ("UPDATED BUILD" if updated else "NEW BUILD")).upper())
    subtitle_text = html.escape(subtitle or "Playable build")
    tone_text = "UPDATED" if updated else "HOT"
    profile_text = html.escape(str(runtime_profile or game_type or "playable").replace("_", " ").title())
    theme_text = html.escape((theme or "arcade").replace("_", " ").replace("-", " ").title())
    pack_text = html.escape(pack_name)
    pack_scene_svg = _build_visual_pack_cover_scene(
        pack=cover_pack,
        width=width,
        height=height,
        primary=primary,
        secondary=secondary,
        accent_soft=accent_soft,
        accent_warm=accent_warm,
        background=background,
        is_landscape=is_landscape,
        variant_name=variant_name,
    )
    svg = f"""
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="{width}" y2="{height}">
      <stop offset="0%" stop-color="{background}"/>
      <stop offset="48%" stop-color="{_mix_hex(background, primary, 0.18)}"/>
      <stop offset="100%" stop-color="{_mix_hex(background, secondary, 0.2)}"/>
    </linearGradient>
    <linearGradient id="shell" x1="{shell_x}" y1="{shell_y}" x2="{shell_x + shell_w}" y2="{shell_y + shell_h}">
      <stop offset="0%" stop-color="{shell_bg}" stop-opacity="0.94"/>
      <stop offset="55%" stop-color="{panel_bg}" stop-opacity="0.92"/>
      <stop offset="100%" stop-color="{_mix_hex(panel_bg, '#020617', 0.34)}" stop-opacity="0.98"/>
    </linearGradient>
    <linearGradient id="panel" x1="{panel_x}" y1="{panel_y}" x2="{panel_x + panel_w}" y2="{panel_y + panel_h}">
      <stop offset="0%" stop-color="{_mix_hex(primary, '#ffffff', 0.16)}" stop-opacity="0.94"/>
      <stop offset="50%" stop-color="{_mix_hex(primary, secondary, 0.35)}" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="{_mix_hex(secondary, '#111827', 0.28)}" stop-opacity="0.94"/>
    </linearGradient>
    <linearGradient id="chip" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#fff0c2"/>
      <stop offset="100%" stop-color="#ffd28a"/>
    </linearGradient>
    <filter id="blurGlow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="24"/>
    </filter>
    <filter id="softShadow" x="-20%" y="-20%" width="150%" height="150%">
      <feDropShadow dx="0" dy="20" stdDeviation="24" flood-color="#020617" flood-opacity="0.34"/>
    </filter>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bg)"/>
  <circle cx="{int(width * 0.18)}" cy="{int(height * 0.16)}" r="{int(min(width, height) * 0.16)}" fill="{primary}" fill-opacity="0.30" filter="url(#blurGlow)"/>
  <circle cx="{int(width * 0.88)}" cy="{int(height * 0.18)}" r="{int(min(width, height) * 0.12)}" fill="{secondary}" fill-opacity="0.34" filter="url(#blurGlow)"/>
  <circle cx="{int(width * 0.72)}" cy="{int(height * 0.82)}" r="{int(min(width, height) * 0.10)}" fill="{accent_soft}" fill-opacity="0.24" filter="url(#blurGlow)"/>
  {pack_scene_svg}
  <rect x="{shell_x}" y="{shell_y}" width="{shell_w}" height="{shell_h}" rx="{shell_radius}" fill="url(#shell)" stroke="rgba(255,255,255,0.16)" filter="url(#softShadow)"/>
  <rect x="{panel_x}" y="{panel_y}" width="{panel_w}" height="{panel_h}" rx="26" fill="{_mix_hex(panel_bg, '#020617', 0.10)}" fill-opacity="0.82" stroke="rgba(255,255,255,0.16)"/>
  <rect x="{panel_x + 22}" y="{panel_y + 22}" width="{max(156, min(250, len(badge_text) * 15 + 34))}" height="38" rx="19" fill="{_mix_hex(primary, '#ffffff', 0.12)}" stroke="rgba(255,255,255,0.18)"/>
  <text x="{panel_x + 42}" y="{panel_y + 47}" fill="#F8FAFC" font-size="{badge_font}" font-weight="800" letter-spacing="2.4" font-family="{font_family}">{badge_text}</text>
  <text x="{panel_x + 22}" y="{panel_y + 82}" fill="#F8FAFC" fill-opacity="0.96" font-size="{subtitle_font}" font-weight="800" letter-spacing="1.4" font-family="{font_family}">{subtitle_text}</text>
  <text x="{panel_x + 22}" y="{panel_y + panel_h - 18}" fill="#CBD5E1" fill-opacity="0.88" font-size="18" font-weight="700" letter-spacing="1.2" font-family="{font_family}">{theme_text}</text>
  {hero_svg}
  {accent_svg}
  <rect x="{info_x}" y="{info_y}" width="{info_w}" height="{info_h}" rx="28" fill="{_mix_hex(primary, '#0f172a', 0.48)}" fill-opacity="0.94" stroke="rgba(255,255,255,0.12)"/>
  <text x="{info_x + 22}" y="{info_y + 34}" fill="#F8FAFC" font-size="18" font-weight="800" letter-spacing="2.4" font-family="{font_family}">{html.escape(tone_text)}</text>
  <text x="{info_x + 22}" y="{info_y + 66}" fill="#E2E8F0" fill-opacity="0.94" font-size="20" font-weight="800" font-family="{font_family}">{pack_text}</text>
  <text x="{info_x + 22}" y="{info_y + 94}" fill="#CBD5E1" fill-opacity="0.86" font-size="16" font-weight="700" font-family="{font_family}">{profile_text}</text>
</svg>
""".strip()
    payload = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return {
        "payload": payload,
        "content_type": "image/svg+xml",
        "metadata": {
            "encoding": "base64",
            "selectedFrame": "fallback_poster",
            "orientation": "landscape" if is_landscape else "portrait",
            "width": width,
            "height": height,
            "coverStyle": "fallback_art_poster",
            "coverVariant": f"fallback_{pack_id or 'default'}_{variant_name}_v3",
            "overlayApplied": False,
            "fallbackGenerated": True,
            "fallbackReason": reason,
            "title": title,
            "badge": badge,
            "subtitle": subtitle,
            "titleVisible": False,
            "theme": str(theme or ""),
            "qualityScore": 4.2,
            "qualityReasons": ["fallback_poster", reason],
            "paletteSource": palette["source"],
            "palette": {
                "primary": primary,
                "secondary": secondary,
                "background": background,
            },
            "canvasChangedAfterInput": False,
            "domChangedAfterInput": False,
            "runtimeProfile": str(runtime_profile or ""),
            "gameType": str(game_type or ""),
            "visualPack": pack_id,
            "renderStyleIntensity": str(render_style_intensity or ""),
            "fallbackSeed": str(fallback_variant["seed"]),
        },
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
    theme: str | None,
    updated: bool,
    palette: dict[str, str],
) -> bool:
    accent_start = palette.get("primary") or "#f59e0b"
    accent_end = palette.get("secondary") or "#f43f5e"
    background = palette.get("background") or "#020617"
    panel_background = _mix_hex(background, "#0f172a", 0.28)
    accent_soft = _mix_hex(accent_start, "#ffffff", 0.18)
    accent_warm = _mix_hex(accent_end, "#f59e0b", 0.18)
    is_landscape = str(orientation or "").strip().lower() in {"landscape", "landscape_first"}
    content_width = "60%" if is_landscape else "100%"
    title_font = "clamp(26px, 5vw, 46px)" if is_landscape else "clamp(30px, 7.6vw, 56px)"
    subtitle_font = "14px" if is_landscape else "15px"
    body_padding = "18px" if is_landscape else "20px"
    shell_inset = "14px 16px 14px 16px" if is_landscape else "18px 18px 20px 18px"
    shell_radius = "30px" if is_landscape else "34px"
    panel_radius = "24px" if is_landscape else "28px"
    right_cluster_size = "34%" if is_landscape else "44%"
    hero_scale = "1.0" if is_landscape else "1.08"
    tone_chip = "UPDATED" if updated else "HOT"
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
                'font-family:\"HarmonyOS Sans SC\",\"PingFang SC\",\"Microsoft YaHei\",\"Avenir Next\",\"Trebuchet MS\",\"Segoe UI\",sans-serif'
            ].join(';');

            const wash = document.createElement('div');
            wash.style.cssText = [
                'position:absolute',
                'inset:0',
                'background:radial-gradient(circle at 14% 16%, {accent_start}88 0%, transparent 32%), radial-gradient(circle at 82% 18%, {accent_end}7a 0%, transparent 28%), radial-gradient(circle at 68% 82%, {accent_soft}55 0%, transparent 24%), linear-gradient(180deg, rgba(2,6,23,0.05) 0%, {background}48 42%, {background}ee 100%)'
            ].join(';');

            const ambient = document.createElement('div');
            ambient.style.cssText = [
                'position:absolute',
                'inset:0',
                'background:linear-gradient(125deg, rgba(255,255,255,0.14) 0%, rgba(255,255,255,0.01) 28%, rgba(255,255,255,0.06) 52%, transparent 72%)',
                'mix-blend-mode:screen',
                'opacity:0.85'
            ].join(';');

            const grid = document.createElement('div');
            grid.style.cssText = [
                'position:absolute',
                'inset:0',
                'background-image:linear-gradient(rgba(255,255,255,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.06) 1px, transparent 1px)',
                'background-size:34px 34px',
                'mask-image:linear-gradient(180deg, rgba(0,0,0,0.42) 0%, rgba(0,0,0,0.12) 36%, rgba(0,0,0,0.68) 100%)',
                'opacity:0.18'
            ].join(';');

            const shell = document.createElement('div');
            shell.style.cssText = [
                'position:absolute',
                'inset:{shell_inset}',
                'border-radius:{shell_radius}',
                'background:linear-gradient(145deg, rgba(8,15,34,0.80) 0%, rgba(14,20,44,0.56) 46%, rgba(4,8,24,0.78) 100%)',
                'border:1px solid rgba(255,255,255,0.18)',
                'box-shadow:0 24px 60px rgba(2,6,23,0.48), inset 0 0 0 1px rgba(255,255,255,0.05)',
                'backdrop-filter:blur(5px)'
            ].join(';');

            const shellGlow = document.createElement('div');
            shellGlow.style.cssText = [
                'position:absolute',
                'inset:-1px',
                'border-radius:{shell_radius}',
                'background:linear-gradient(135deg, {accent_start}88 0%, transparent 26%, transparent 68%, {accent_end}7a 100%)',
                'opacity:0.95'
            ].join(';');

            const shellInner = document.createElement('div');
            shellInner.style.cssText = [
                'position:absolute',
                'inset:1px',
                'border-radius:calc({shell_radius} - 1px)',
                'overflow:hidden'
            ].join(';');

            const beam = document.createElement('div');
            beam.style.cssText = [
                'position:absolute',
                'right:-18%',
                'top:-8%',
                'width:54%',
                'height:62%',
                'background:radial-gradient(circle at center, {accent_end}55 0%, {accent_end}14 38%, transparent 70%)',
                'filter:blur(6px)',
                'opacity:0.95'
            ].join(';');

            const content = document.createElement('div');
            content.style.cssText = [
                'position:relative',
                'display:flex',
                'flex-direction:{'row' if is_landscape else 'column'}',
                'justify-content:space-between',
                'align-items:{'stretch' if is_landscape else 'flex-start'}',
                'height:100%',
                'padding:{body_padding}',
                'box-sizing:border-box',
                'gap:16px'
            ].join(';');

            const leftColumn = document.createElement('div');
            leftColumn.style.cssText = [
                'display:flex',
                'flex-direction:column',
                'justify-content:space-between',
                'gap:16px',
                'width:{content_width}',
                'min-height:100%'
            ].join(';');

            const topRow = document.createElement('div');
            topRow.style.cssText = [
                'display:flex',
                'justify-content:{'space-between' if is_landscape else 'flex-start'}',
                'align-items:flex-start',
                'gap:10px',
                'width:100%'
            ].join(';');

            const badgeEl = document.createElement('div');
            badgeEl.textContent = {json.dumps(badge)};
            badgeEl.style.cssText = [
                'display:inline-flex',
                'align-items:center',
                'gap:8px',
                'padding:10px 14px',
                'border-radius:999px',
                'border:1px solid rgba(255,255,255,0.20)',
                'background:linear-gradient(180deg, rgba(255,255,255,0.18) 0%, {panel_background}e6 100%)',
                'box-shadow:inset 0 1px 0 rgba(255,255,255,0.22), 0 10px 30px rgba(15,23,42,0.18)',
                'backdrop-filter:blur(10px)',
                'color:#f8fafc',
                'font-size:11px',
                'font-weight:800',
                'letter-spacing:0.24em',
                'text-transform:uppercase'
            ].join(';');

            const toneChip = document.createElement('div');
            toneChip.textContent = {json.dumps(tone_chip)};
            toneChip.style.cssText = [
                'display:inline-flex',
                'align-items:center',
                'padding:10px 13px',
                'border-radius:999px',
                'background:linear-gradient(135deg, rgba(255,236,179,0.96) 0%, rgba(254,215,170,0.96) 100%)',
                'color:#1f2937',
                'font-size:11px',
                'font-weight:900',
                'letter-spacing:0.16em',
                'box-shadow:0 12px 26px rgba(249,115,22,0.22)'
            ].join(';');

            topRow.appendChild(badgeEl);
            if ({'true' if is_landscape else 'false'}) {{
                topRow.appendChild(toneChip);
            }}

            const titlePanel = document.createElement('div');
            titlePanel.style.cssText = [
                'display:flex',
                'flex-direction:column',
                'gap:14px',
                'padding:18px 18px 18px 18px',
                'border-radius:{panel_radius}',
                'background:linear-gradient(160deg, rgba(8,15,34,0.26) 0%, {panel_background}d8 36%, rgba(9,14,34,0.72) 100%)',
                'border:1px solid rgba(255,255,255,0.16)',
                'box-shadow:inset 0 1px 0 rgba(255,255,255,0.14), 0 16px 38px rgba(2,6,23,0.32)',
                'backdrop-filter:blur(12px)'
            ].join(';');

            const accentBar = document.createElement('div');
            accentBar.style.cssText = [
                'width:92px',
                'height:7px',
                'border-radius:999px',
                'background:linear-gradient(90deg, {accent_start} 0%, {accent_warm} 55%, {accent_end} 100%)',
                'box-shadow:0 0 26px {accent_end}66'
            ].join(';');

            const titleEl = document.createElement('div');
            titleEl.textContent = {json.dumps(title)};
            titleEl.style.cssText = [
                'color:#ffffff',
                'font-size:{title_font}',
                'font-weight:900',
                'line-height:0.96',
                'letter-spacing:-0.05em',
                'text-shadow:0 12px 32px rgba(2,6,23,0.46)'
            ].join(';');

            const subtitleEl = document.createElement('div');
            subtitleEl.textContent = {json.dumps(subtitle)};
            subtitleEl.style.cssText = [
                'color:rgba(241,245,249,0.90)',
                'font-size:{subtitle_font}',
                'font-weight:700',
                'letter-spacing:0.12em',
                'text-transform:uppercase'
            ].join(';');

            titlePanel.appendChild(accentBar);
            titlePanel.appendChild(titleEl);
            titlePanel.appendChild(subtitleEl);

            const toneChipPortraitWrap = document.createElement('div');
            toneChipPortraitWrap.style.cssText = [
                'display:{'flex' if not is_landscape else 'none'}',
                'justify-content:flex-start'
            ].join(';');
            toneChipPortraitWrap.appendChild(toneChip);

            leftColumn.appendChild(topRow);
            leftColumn.appendChild(titlePanel);
            leftColumn.appendChild(toneChipPortraitWrap);

            const rightCluster = document.createElement('div');
            rightCluster.style.cssText = [
                'position:relative',
                'flex:1',
                'min-width:{right_cluster_size}',
                'display:flex',
                'align-items:flex-end',
                'justify-content:flex-end'
            ].join(';');

            const heroGlow = document.createElement('div');
            heroGlow.style.cssText = [
                'position:absolute',
                'right:6%',
                'bottom:10%',
                'width:{'54%' if is_landscape else '68%'}',
                'aspect-ratio:1 / 1',
                'border-radius:50%',
                'background:radial-gradient(circle at 35% 32%, rgba(255,255,255,0.92) 0%, {accent_start}d8 18%, {accent_end}cc 52%, rgba(15,23,42,0.08) 78%, transparent 86%)',
                'box-shadow:0 0 40px {accent_end}44'
            ].join(';');

            const heroRing = document.createElement('div');
            heroRing.style.cssText = [
                'position:absolute',
                'right:12%',
                'bottom:16%',
                'width:{'44%' if is_landscape else '58%'}',
                'aspect-ratio:1 / 1',
                'border-radius:50%',
                'border:1px solid rgba(255,255,255,0.26)',
                'box-shadow:inset 0 0 0 1px rgba(255,255,255,0.08)',
                'transform:rotate(-12deg)'
            ].join(';');

            const heroCore = document.createElement('div');
            heroCore.style.cssText = [
                'position:absolute',
                'right:18%',
                'bottom:18%',
                'width:{'30%' if is_landscape else '42%'}',
                'aspect-ratio:1 / 1',
                'border-radius:30px',
                'transform:rotate(-10deg) scale({hero_scale})',
                'background:linear-gradient(160deg, rgba(255,255,255,0.94) 0%, rgba(255,255,255,0.44) 22%, {accent_start}cc 56%, {accent_end}dd 100%)',
                'box-shadow:0 28px 48px rgba(15,23,42,0.38), inset 0 2px 8px rgba(255,255,255,0.35)'
            ].join(';');

            const heroSpec = document.createElement('div');
            heroSpec.style.cssText = [
                'position:absolute',
                'inset:14% 18% auto auto',
                'width:46%',
                'height:18%',
                'border-radius:999px',
                'background:linear-gradient(90deg, rgba(255,255,255,0.0) 0%, rgba(255,255,255,0.68) 100%)',
                'filter:blur(1px)'
            ].join(';');
            heroCore.appendChild(heroSpec);

            const floatingDotA = document.createElement('div');
            floatingDotA.style.cssText = [
                'position:absolute',
                'right:36%',
                'top:16%',
                'width:16px',
                'height:16px',
                'border-radius:50%',
                'background:{accent_soft}',
                'box-shadow:0 0 18px {accent_soft}'
            ].join(';');

            const floatingDotB = document.createElement('div');
            floatingDotB.style.cssText = [
                'position:absolute',
                'right:14%',
                'top:28%',
                'width:10px',
                'height:10px',
                'border-radius:50%',
                'background:{accent_warm}',
                'box-shadow:0 0 14px {accent_warm}'
            ].join(';');

            rightCluster.appendChild(heroGlow);
            rightCluster.appendChild(heroRing);
            rightCluster.appendChild(heroCore);
            rightCluster.appendChild(floatingDotA);
            rightCluster.appendChild(floatingDotB);

            content.appendChild(leftColumn);
            content.appendChild(rightCluster);

            shellInner.appendChild(beam);
            shellInner.appendChild(content);
            shell.appendChild(shellGlow);
            shell.appendChild(shellInner);

            overlay.appendChild(wash);
            overlay.appendChild(ambient);
            overlay.appendChild(grid);
            overlay.appendChild(shell);
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
    visual_pack: str | None = None,
    render_style_intensity: str | None = None,
    updated: bool = False,
) -> dict[str, Any] | None:
    effective_timeout_s = max(float(timeout_s if timeout_s is not None else get_timeout_float(
        "timeout.ai_engine.cover_capture_s",
        4.0,
        min_value=0.1,
    )), 0.1)
    viewport = _cover_viewport(orientation)
    resolved_cover_pack = _resolve_cover_visual_pack(
        visual_pack=visual_pack,
        game_type=game_type,
        theme=theme,
        render_style_intensity=render_style_intensity,
        variation_seed="|".join(
            [
                str(title or "").strip().lower(),
                str(theme or "").strip().lower(),
                str(runtime_profile or "").strip().lower(),
                str(game_type or "").strip().lower(),
                str(orientation or "").strip().lower(),
            ]
        ),
    )
    resolved_visual_pack = str((resolved_cover_pack or {}).get("id") or visual_pack or "")
    resolved_render_style_intensity = str(render_style_intensity or "")
    cover_copy = _resolve_cover_copy(
        requested_title=title,
        dom_title=None,
        game_type=game_type,
        theme=theme,
        runtime_profile=runtime_profile,
        updated=updated,
    )
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        logger.debug("Playwright not installed, using fallback cover capture: %s", exc)
        return _build_fallback_cover_artifact(
            viewport=viewport,
            orientation=orientation,
            title=cover_copy["title"],
            badge=cover_copy["badge"],
            subtitle=cover_copy["subtitle"],
            theme=theme,
            updated=updated,
            runtime_profile=runtime_profile,
            game_type=game_type,
            reason="playwright_unavailable",
            visual_pack=resolved_visual_pack,
            render_style_intensity=resolved_render_style_intensity,
        )

    browser = None

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
            context = await browser.new_context(viewport=viewport, service_workers="block")
            await restrict_context_network(context)
            page = await context.new_page()

            page.on("pageerror", lambda exc: logger.debug("Cover capture page error: %s", exc))
            page.on("console", lambda msg: (
                logger.debug("Cover capture console error: %s", msg.text)
                if msg.type == "error"
                else None
            ))

            await page.add_init_script(_INSTRUMENTATION_JS)
            await page.set_content(network_policy_meta() + _inject_probe_script(html_code), wait_until="load")
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
                    visual_pack=resolved_visual_pack,
                    render_style_intensity=resolved_render_style_intensity,
                    game_type=game_type,
                )
                image_bytes = await page.screenshot(type="jpeg", quality=72, scale="css")
                candidates.append({
                    "label": label,
                    "canvas_renders": canvas_renders,
                    "canvas_fingerprint": canvas_fingerprint,
                    "dom_fingerprint": dom_fingerprint,
                    "payload": base64.b64encode(image_bytes).decode("ascii"),
                    "size_bytes": len(image_bytes),
                    "cover_overlay_applied": False,
                    "cover_title": cover_copy["title"],
                    "cover_badge": cover_copy["badge"],
                    "cover_subtitle": cover_copy["subtitle"],
                    "cover_palette_source": cover_palette["source"],
                    "cover_visual_pack": resolved_visual_pack,
                    "cover_render_style_intensity": resolved_render_style_intensity,
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
                return _build_fallback_cover_artifact(
                    viewport=viewport,
                    orientation=orientation,
                    title=cover_copy["title"],
                    badge=cover_copy["badge"],
                    subtitle=cover_copy["subtitle"],
                    theme=theme,
                    updated=updated,
                    runtime_profile=runtime_profile,
                    game_type=game_type,
                    reason="candidate_selection_empty",
                    visual_pack=resolved_visual_pack,
                    render_style_intensity=resolved_render_style_intensity,
                )

            return {
                "payload": selected["payload"],
                "content_type": "image/jpeg",
                "metadata": {
                    "encoding": "base64",
                    "selectedFrame": selected["label"],
                    "orientation": "landscape" if viewport["width"] > viewport["height"] else "portrait",
                    "width": viewport["width"],
                    "height": viewport["height"],
                    "coverStyle": "runtime_frame_capture",
                    "coverVariant": "direct_runtime_frame_v2",
                    "overlayApplied": bool(selected.get("cover_overlay_applied")),
                    "title": str(selected.get("cover_title") or ""),
                    "badge": str(selected.get("cover_badge") or ""),
                    "subtitle": str(selected.get("cover_subtitle") or ""),
                    "titleVisible": False,
                    "theme": str(theme or ""),
                    "qualityScore": float(selected.get("quality_score") or 0.0),
                    "qualityReasons": list(selected.get("quality_reasons") or []),
                    "paletteSource": str(selected.get("cover_palette_source") or "theme_fallback"),
                    "palette": dict(selected.get("cover_palette") or {}),
                    "visualPack": str(selected.get("cover_visual_pack") or resolved_visual_pack or ""),
                    "renderStyleIntensity": str(
                        selected.get("cover_render_style_intensity") or resolved_render_style_intensity or ""
                    ),
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
        result = await asyncio.wait_for(_execute(), timeout=min(max(effective_timeout_s, 6.0), 12.0))
        if result:
            return result
        return _build_fallback_cover_artifact(
            viewport=viewport,
            orientation=orientation,
            title=cover_copy["title"],
            badge=cover_copy["badge"],
            subtitle=cover_copy["subtitle"],
            theme=theme,
            updated=updated,
            runtime_profile=runtime_profile,
            game_type=game_type,
            reason="empty_runtime_capture",
            visual_pack=resolved_visual_pack,
            render_style_intensity=resolved_render_style_intensity,
        )
    except Exception as exc:
        logger.debug("Cover capture fell back to poster: %s", exc)
        return _build_fallback_cover_artifact(
            viewport=viewport,
            orientation=orientation,
            title=cover_copy["title"],
            badge=cover_copy["badge"],
            subtitle=cover_copy["subtitle"],
            theme=theme,
            updated=updated,
            runtime_profile=runtime_profile,
            game_type=game_type,
            reason=type(exc).__name__.lower() or "runtime_capture_error",
            visual_pack=resolved_visual_pack,
            render_style_intensity=resolved_render_style_intensity,
        )
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
    semaphore: asyncio.Semaphore | None = None
    overall_timeout_s = _overall_timeout_s(effective_timeout_s=effective_timeout_s)
    phase_metrics: Dict[str, Any] = {
        "requested_timeout_s": effective_timeout_s,
        "overall_timeout_s": overall_timeout_s,
    }
    max_concurrency = _runtime_qa_max_concurrency()
    phase_metrics["max_concurrency"] = max_concurrency
    queue_started = time.perf_counter()
    semaphore = _runtime_qa_semaphore(max_concurrency)
    await semaphore.acquire()
    phase_metrics["queue_wait_ms"] = int((time.perf_counter() - queue_started) * 1000)

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
                    service_workers="block",
                )
                await restrict_context_network(context)
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
                await page.set_content(network_policy_meta() + _inject_probe_script(html_code), wait_until="load")
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

            canvas_fingerprint_before = None
            dom_fingerprint_before = None
            canvas_renders = False

            async def _best_effort_collect() -> tuple[Any, Any, dict[str, Any]]:
                short_timeout_s = max(
                    0.25,
                    min(
                        1.5,
                        get_timeout_float(
                            "timeout.ai_engine.runtime_qa.best_effort_collect_s",
                            0.75,
                            min_value=0.05,
                        ),
                    ),
                )
                canvas_after = None
                dom_after = None
                collected_payload: dict[str, Any] = {}
                with contextlib.suppress(Exception):
                    canvas_after = await asyncio.wait_for(
                        page.evaluate(_CANVAS_FINGERPRINT_JS),
                        timeout=short_timeout_s,
                    )
                with contextlib.suppress(Exception):
                    dom_after = await asyncio.wait_for(
                        page.evaluate(_DOM_FINGERPRINT_JS),
                        timeout=short_timeout_s,
                    )
                with contextlib.suppress(Exception):
                    collected_candidate = await asyncio.wait_for(
                        page.evaluate(_COLLECT_JS),
                        timeout=short_timeout_s,
                    )
                    if isinstance(collected_candidate, dict):
                        collected_payload = collected_candidate
                return canvas_after, dom_after, collected_payload

            async def _interaction_phase() -> tuple[Any, Any, Any]:
                nonlocal canvas_fingerprint_before, dom_fingerprint_before, canvas_renders
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

            try:
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
            except RuntimeQAPhaseTimeoutError as exc:
                if exc.phase not in {"interaction", "collect"}:
                    raise
                canvas_fingerprint_after, dom_fingerprint_after, collected = await _best_effort_collect()
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
                phase_metrics["total_elapsed_ms"] = int((time.perf_counter() - start) * 1000)
                return RuntimeQAResult(
                    ran=False,
                    canvas_renders=bool(canvas_renders),
                    js_errors=js_errors[:10],
                    registered_input_handlers=registered_input_handlers,
                    direct_input_handlers=direct_input_handlers,
                    triggered_input_handlers=triggered_input_handlers,
                    interaction_performed=interaction_performed,
                    canvas_changed_after_input=canvas_changed_after_input,
                    dom_changed_after_input=dom_changed_after_input,
                    unavailable_reason=f"runtime_qa_timeout:{exc.phase}:{exc.timeout_s:.2f}s",
                    unavailable_kind="timeout",
                    unavailable_phase=exc.phase,
                    phase_metrics=dict(phase_metrics),
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
        if semaphore is not None:
            semaphore.release()

