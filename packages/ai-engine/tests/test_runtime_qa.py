"""Tests for runtime QA timeout and fallback behavior."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.runtime_qa import _inject_probe_script, _resolve_chromium_executable, run_runtime_qa


class _SlowPage:
    def __init__(self):
        self.html = None

    def on(self, *_args, **_kwargs):
        return None

    async def add_init_script(self, *_args, **_kwargs):
        return None

    async def set_content(self, html, *_args, **_kwargs):
        self.html = html
        await asyncio.sleep(0.2)

    async def wait_for_load_state(self, *_args, **_kwargs):
        await asyncio.sleep(0.2)

    async def evaluate(self, script):
        if "createTreeWalker" in script:
            return {"hash": 1, "bodyText": "", "title": "", "count": 1}
        if "canvas" in script:
            return True
        return {"fps": 60, "errors": [], "gameOverReceived": False}


class _SlowContext:
    def __init__(self):
        self.page = None

    async def new_page(self):
        self.page = _SlowPage()
        return self.page


class _InteractivePage:
    def __init__(self):
        self.html = None

    def on(self, *_args, **_kwargs):
        return None

    async def add_init_script(self, *_args, **_kwargs):
        return None

    async def set_content(self, html, *_args, **_kwargs):
        self.html = html
        return None

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def evaluate(self, script):
        if "createTreeWalker" in script:
            if not hasattr(self, "_dom_fingerprint_calls"):
                self._dom_fingerprint_calls = 0
            self._dom_fingerprint_calls += 1
            return {
                "hash": 301 if self._dom_fingerprint_calls == 1 else 404,
                "bodyText": "" if self._dom_fingerprint_calls == 1 else "Tap 1",
                "title": "Test",
                "count": 2,
            }
        if "getImageData" in script and "return false" in script:
            return True
        if "hash = (hash * 33" in script:
            if not hasattr(self, "_fingerprint_calls"):
                self._fingerprint_calls = 0
            self._fingerprint_calls += 1
            return 101 if self._fingerprint_calls == 1 else 202
        if "window.__qaInteractionPerformed = true" in script:
            return True
        return {
            "fps": 58,
            "errors": [],
            "gameOverReceived": False,
            "registeredInputHandlers": ["pointerdown", "pointerup"],
            "directInputHandlers": ["click"],
            "triggeredInputHandlers": ["pointerdown"],
            "interactionPerformed": True,
        }


class _InteractiveContext:
    def __init__(self):
        self.page = None

    async def new_page(self):
        self.page = _InteractivePage()
        return self.page


class _ClosableBrowser:
    def __init__(self):
        self.closed = False
        self.context_factory = _SlowContext

    async def new_context(self, **_kwargs):
        return self.context_factory()

    async def close(self):
        self.closed = True


class _ChromiumWrapper:
    def __init__(self, browser):
        self._browser = browser
        self.launch_kwargs = None

    async def launch(self, **_kwargs):
        self.launch_kwargs = _kwargs
        return self._browser


class _PlaywrightCtx:
    def __init__(self, browser):
        self.chromium = _ChromiumWrapper(browser)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_runtime_qa_honors_timeout_and_closes_browser(monkeypatch):
    browser = _ClosableBrowser()
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    result = asyncio.run(run_runtime_qa("<html></html>", timeout_s=0.05))

    assert result.ran is False
    assert browser.closed is True


def test_runtime_qa_collects_input_handler_signals(monkeypatch):
    browser = _ClosableBrowser()
    browser.context_factory = _InteractiveContext
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    result = asyncio.run(run_runtime_qa("<html></html>", timeout_s=1.0))

    assert result.ran is True
    assert result.canvas_renders is True
    assert result.registered_input_handlers == ["pointerdown", "pointerup"]
    assert result.direct_input_handlers == ["click"]
    assert result.triggered_input_handlers == ["pointerdown"]
    assert result.interaction_performed is True
    assert result.canvas_changed_after_input is True
    assert result.dom_changed_after_input is True


def test_inject_probe_script_places_runtime_probe_before_page_scripts():
    html = """<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body><script>console.log('game-script');</script></body>
</html>"""

    instrumented = _inject_probe_script(html)

    assert "__qaRegisteredInputs" in instrumented
    assert instrumented.index("__qaRegisteredInputs") < instrumented.index("console.log('game-script');")


def test_resolve_chromium_executable_prefers_bundled_browser_path(monkeypatch, tmp_path):
    browser_root = tmp_path / "ms-playwright"
    executable = browser_root / "chromium-1112" / "chrome-linux" / "chrome"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browser_root))
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", raising=False)

    assert _resolve_chromium_executable() == str(executable)


def test_runtime_qa_passes_resolved_executable_to_launch(monkeypatch):
    browser = _ClosableBrowser()
    wrapper = _ChromiumWrapper(browser)
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    fake_module.async_playwright = lambda: _PlaywrightCtx(browser)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)
    monkeypatch.setattr("src.engine.runtime_qa._resolve_chromium_executable", lambda: "/ms-playwright/chromium-1112/chrome-linux/chrome")

    playwright_ctx = _PlaywrightCtx(browser)
    playwright_ctx.chromium = wrapper
    monkeypatch.setitem(sys.modules, "playwright.async_api", types.SimpleNamespace(async_playwright=lambda: playwright_ctx))

    result = asyncio.run(run_runtime_qa("<html></html>", timeout_s=1.0))

    assert result.ran is True
    assert wrapper.launch_kwargs["executable_path"] == "/ms-playwright/chromium-1112/chrome-linux/chrome"
