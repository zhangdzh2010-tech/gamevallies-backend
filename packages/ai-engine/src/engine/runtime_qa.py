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
import contextlib
import logging
import os
import re
import time
from pathlib import Path
from typing import List

from ..config.settings import settings
from ..config.timeout_store import get_float as get_timeout_float, get_int as get_timeout_int
from .quality_scorer import RuntimeQAResult

logger = logging.getLogger(__name__)


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
        )

    js_errors: List[str] = []
    start = time.time()
    browser = None

    async def _execute() -> RuntimeQAResult:
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
            context = await browser.new_context(
                viewport={"width": 420, "height": 700},
            )
            page = await context.new_page()

            page.on("console", lambda msg: (
                js_errors.append(msg.text) if msg.type == "error" else None
            ))
            page.on("pageerror", lambda exc: js_errors.append(str(exc)))

            await page.add_init_script(_INSTRUMENTATION_JS)
            await page.set_content(_inject_probe_script(html_code), wait_until="load")
            with contextlib.suppress(Exception):
                await page.wait_for_load_state(
                    "load",
                    timeout=max(
                        get_timeout_int("timeout.ai_engine.runtime_qa.load_wait_min_ms", 250, min_value=1),
                        int(
                            effective_timeout_s * get_timeout_float(
                                "timeout.ai_engine.runtime_qa.load_wait_factor_ms_per_s",
                                250.0,
                                min_value=1.0,
                            )
                        ),
                    ),
                )

            initial_wait_s = min(
                get_timeout_float("timeout.ai_engine.runtime_qa.initial_wait_max_s", 2.0, min_value=0.01),
                max(
                    effective_timeout_s * get_timeout_float("timeout.ai_engine.runtime_qa.initial_wait_ratio", 0.25, min_value=0.0),
                    get_timeout_float("timeout.ai_engine.runtime_qa.initial_wait_min_s", 0.35, min_value=0.0),
                ),
            )
            await asyncio.sleep(initial_wait_s)

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
            await asyncio.sleep(post_interaction_wait_s)
            canvas_fingerprint_after = await page.evaluate(_CANVAS_FINGERPRINT_JS)
            dom_fingerprint_after = await page.evaluate(_DOM_FINGERPRINT_JS)
            collected = await page.evaluate(_COLLECT_JS)

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
            load_time_ms = int((time.time() - start) * 1000)

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
            )

    try:
        return await asyncio.wait_for(_execute(), timeout=effective_timeout_s)
    except asyncio.TimeoutError:
        logger.warning("Runtime QA timed out")
        return RuntimeQAResult(
            ran=False,
            unavailable_reason=f"runtime_qa_timeout:{effective_timeout_s:.2f}s",
        )
    except Exception as exc:
        logger.warning(f"Runtime QA failed: {exc}")
        return RuntimeQAResult(
            ran=False,
            unavailable_reason=f"runtime_qa_exception:{exc}",
        )
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
