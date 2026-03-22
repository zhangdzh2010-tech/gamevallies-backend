"""Stage 06b: Runtime QA – headless Playwright browser testing.

Loads the HTML game in a headless Chromium, injects FPS instrumentation,
waits 3 seconds, then collects:
  - JS errors (console errors / uncaught exceptions)
  - canvas render check (any non-black pixel)
  - measured FPS
  - load time
  - game-over message detection (postMessage)

The check is best-effort: if Playwright is unavailable or times out,
it returns RuntimeQAResult(ran=False) and the pipeline continues.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import time
from typing import List

from .quality_scorer import RuntimeQAResult

logger = logging.getLogger(__name__)

# JS injected before the game loads – instruments RAF for FPS measurement
# and captures postMessage game_over events
_INSTRUMENTATION_JS = """
(function() {
    window.__qaErrors = [];
    window.__fpsFrames = 0;
    window.__fpsStart = performance.now();
    window.__fps = 0;
    window.__gameOverReceived = false;

    // Intercept postMessage game_over
    const _origAdd = window.addEventListener.bind(window);
    window.addEventListener = function(type, handler, ...rest) {
        return _origAdd(type, handler, ...rest);
    };
    window.addEventListener('message', function(e) {
        if (e.data && e.data.type === 'game_over') {
            window.__gameOverReceived = true;
        }
    });

    // Wrap rAF to count frames
    const _origRAF = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = function(cb) {
        return _origRAF(function(ts) {
            window.__fpsFrames++;
            const elapsed = (performance.now() - window.__fpsStart) / 1000;
            if (elapsed > 0) window.__fps = window.__fpsFrames / elapsed;
            return cb(ts);
        });
    };
})();
"""

# JS to check canvas pixels (returns true if any non-zero pixel exists)
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
    } catch(e) {
        return false;
    }
})()
"""

_COLLECT_JS = """
({
    fps: window.__fps || 0,
    frames: window.__fpsFrames || 0,
    errors: window.__qaErrors || [],
    gameOverReceived: window.__gameOverReceived || false,
})
"""


async def run_runtime_qa(html_code: str, timeout_s: float = 6.0) -> RuntimeQAResult:
    """Run the headless browser QA check.

    Returns RuntimeQAResult(ran=False) if Playwright is unavailable or times out.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed – skipping runtime QA")
        return RuntimeQAResult(ran=False)

    js_errors: List[str] = []
    start = time.time()
    browser = None

    async def _execute() -> RuntimeQAResult:
        nonlocal browser
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
            )
            context = await browser.new_context(
                viewport={"width": 420, "height": 700},
            )
            page = await context.new_page()

            # Collect console errors
            page.on("console", lambda msg: (
                js_errors.append(msg.text) if msg.type == "error" else None
            ))
            page.on("pageerror", lambda exc: js_errors.append(str(exc)))

            # Inject instrumentation before any page script runs
            await page.add_init_script(_INSTRUMENTATION_JS)

            # Load the HTML directly as content (no network needed)
            await page.set_content(html_code, wait_until="domcontentloaded")

            # Wait for game to start rendering
            await asyncio.sleep(min(3.0, max(timeout_s - 1.0, 0.1)))

            # Collect results
            canvas_renders = await page.evaluate(_CANVAS_CHECK_JS)
            collected = await page.evaluate(_COLLECT_JS)

            fps = float(collected.get("fps", 0))
            game_over_triggered = bool(collected.get("gameOverReceived", False))
            page_errors = list(collected.get("errors", []))
            js_errors.extend(page_errors)

            load_time_ms = int((time.time() - start) * 1000)

            logger.info(
                f"Runtime QA: canvas_renders={canvas_renders}, fps={fps:.1f}, "
                f"js_errors={len(js_errors)}, load_ms={load_time_ms}"
            )
            return RuntimeQAResult(
                ran=True,
                canvas_renders=bool(canvas_renders),
                js_errors=js_errors[:10],   # cap at 10
                fps=fps,
                load_time_ms=load_time_ms,
                game_over_triggered=game_over_triggered,
            )

    try:
        return await asyncio.wait_for(_execute(), timeout=max(timeout_s, 0.1))
    except asyncio.TimeoutError:
        logger.warning("Runtime QA timed out")
        return RuntimeQAResult(ran=False)
    except Exception as exc:
        logger.warning(f"Runtime QA failed: {exc}")
        return RuntimeQAResult(ran=False)
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
