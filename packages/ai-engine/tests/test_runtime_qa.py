"""Tests for runtime QA timeout and fallback behavior."""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.runtime_qa import run_runtime_qa


class _SlowPage:
    def on(self, *_args, **_kwargs):
        return None

    async def add_init_script(self, *_args, **_kwargs):
        return None

    async def set_content(self, *_args, **_kwargs):
        await asyncio.sleep(0.2)

    async def evaluate(self, script):
        if "canvas" in script:
            return True
        return {"fps": 60, "errors": [], "gameOverReceived": False}


class _SlowContext:
    async def new_page(self):
        return _SlowPage()


class _ClosableBrowser:
    def __init__(self):
        self.closed = False

    async def new_context(self, **_kwargs):
        return _SlowContext()

    async def close(self):
        self.closed = True


class _ChromiumWrapper:
    def __init__(self, browser):
        self._browser = browser

    async def launch(self, **_kwargs):
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
