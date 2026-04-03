"""Tests for runtime QA timeout and fallback behavior."""
import asyncio
import base64
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.runtime_qa import (
    _inject_probe_script,
    _phase_timeout_s,
    _resolve_chromium_executable,
    _resolve_fallback_cover_variant,
    capture_cover_artifact,
    run_runtime_qa,
)


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
        if "dominantBuckets" in script:
            return {
                "source": "canvas",
                "primary": "#204060",
                "secondary": "#60a5fa",
                "background": "#0f172a",
            }
        if "getImageData" in script and "return false" in script:
            return True
        return {"fps": 60, "errors": [], "gameOverReceived": False}

    async def screenshot(self, **_kwargs):
        return b"slow-cover"


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
        if "dominantBuckets" in script:
            if not hasattr(self, "_palette_calls"):
                self._palette_calls = 0
            self._palette_calls += 1
            palettes = {
                1: {
                    "source": "canvas",
                    "primary": "#1d4ed8",
                    "secondary": "#7c3aed",
                    "background": "#0f172a",
                },
                2: {
                    "source": "canvas",
                    "primary": "#0ea5e9",
                    "secondary": "#22c55e",
                    "background": "#082f49",
                },
                3: {
                    "source": "canvas",
                    "primary": "#38bdf8",
                    "secondary": "#c084fc",
                    "background": "#0f172a",
                },
            }
            return palettes.get(self._palette_calls, palettes[3])
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

    async def screenshot(self, **_kwargs):
        if not hasattr(self, "_screenshot_calls"):
            self._screenshot_calls = 0
        self._screenshot_calls += 1
        frames = {
            1: b"ready-frame",
            2: b"action-frame",
            3: b"settled-frame",
        }
        return frames.get(self._screenshot_calls, b"settled-frame")


class _InteractiveContext:
    def __init__(self):
        self.page = None

    async def new_page(self):
        self.page = _InteractivePage()
        return self.page


class _DeferredInteractionPage:
    def __init__(self):
        self.html = None
        self._fingerprint_calls = 0

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
            return {"hash": 100, "bodyText": "", "title": "Deferred", "count": 1}
        if "getImageData" in script and "return false" in script:
            return True
        if "hash = (hash * 33" in script:
            self._fingerprint_calls += 1
            return 100 if self._fingerprint_calls == 1 else 200
        if "window.__qaInteractionScheduled = true" in script:
            return True
        if "registeredInputHandlers" in script:
            return {
                "fps": 52,
                "errors": [],
                "gameOverReceived": False,
                "registeredInputHandlers": ["pointerdown"],
                "directInputHandlers": ["click"],
                "triggeredInputHandlers": ["pointerdown"],
                "interactionPerformed": True,
                "interactionScheduled": True,
                "interactionPending": False,
            }
        return {
            "source": "canvas",
            "primary": "#1d4ed8",
            "secondary": "#7c3aed",
            "background": "#0f172a",
        }


class _DeferredInteractionContext:
    def __init__(self):
        self.page = None

    async def new_page(self):
        self.page = _DeferredInteractionPage()
        return self.page


class _QualityHeuristicPage:
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
            if not hasattr(self, "_dom_calls"):
                self._dom_calls = 0
            self._dom_calls += 1
            dom_states = {
                1: {
                    "hash": 111,
                    "bodyText": "Tap to Start",
                    "title": "Runner",
                    "count": 3,
                },
                2: {
                    "hash": 222,
                    "bodyText": "Score 12 Combo x2",
                    "title": "Runner",
                    "count": 4,
                },
                3: {
                    "hash": 333,
                    "bodyText": "Game Over Try Again",
                    "title": "Runner",
                    "count": 4,
                },
            }
            return dom_states.get(self._dom_calls, dom_states[3])
        if "dominantBuckets" in script:
            return {
                "source": "canvas",
                "primary": "#22c55e",
                "secondary": "#38bdf8",
                "background": "#052e16",
            }
        if "getImageData" in script and "return false" in script:
            return True
        if "hash = (hash * 33" in script:
            if not hasattr(self, "_fingerprint_calls"):
                self._fingerprint_calls = 0
            self._fingerprint_calls += 1
            return {1: 111, 2: 222, 3: 333}.get(self._fingerprint_calls, 333)
        if "window.__qaInteractionPerformed = true" in script:
            return True
        return {
            "fps": 60,
            "errors": [],
            "gameOverReceived": False,
            "registeredInputHandlers": ["pointerdown", "pointerup"],
            "directInputHandlers": ["click"],
            "triggeredInputHandlers": ["pointerdown"],
            "interactionPerformed": True,
        }

    async def screenshot(self, **_kwargs):
        if not hasattr(self, "_screenshot_calls"):
            self._screenshot_calls = 0
        self._screenshot_calls += 1
        frames = {
            1: b"menu-frame",
            2: b"gameplay-frame",
            3: b"game-over-frame",
        }
        return frames.get(self._screenshot_calls, b"game-over-frame")


class _QualityHeuristicContext:
    def __init__(self):
        self.page = None

    async def new_page(self):
        self.page = _QualityHeuristicPage()
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
    assert result.unavailable_kind == "timeout"
    assert browser.closed is True


def test_runtime_qa_phase_timeout_records_phase_details(monkeypatch):
    browser = _ClosableBrowser()
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    original_get_float = sys.modules["src.engine.runtime_qa"].get_timeout_float

    def fake_get_float(key, default, min_value=0.0, max_value=None):
        if key == "timeout.ai_engine.runtime_qa.phase_content_load_s":
            return 0.05
        return original_get_float(key, default, min_value=min_value, max_value=max_value)

    monkeypatch.setattr("src.engine.runtime_qa.get_timeout_float", fake_get_float)

    result = asyncio.run(run_runtime_qa("<html></html>", timeout_s=1.0))

    assert result.ran is False
    assert result.unavailable_kind == "timeout"
    assert result.unavailable_phase == "content_load"
    assert result.unavailable_reason == "runtime_qa_timeout:content_load:0.05s"
    assert result.phase_metrics["content_load_timeout_s"] == 0.05


def test_runtime_qa_queues_browser_launches_when_capacity_is_exhausted(monkeypatch):
    class _FreshPlaywrightFactory:
        def __call__(self):
            browser = _ClosableBrowser()
            browser.context_factory = _SlowContext
            return _PlaywrightCtx(browser)

    fake_module = types.SimpleNamespace(
        async_playwright=_FreshPlaywrightFactory(),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    original_get_int = sys.modules["src.engine.runtime_qa"].get_timeout_int

    def fake_get_int(key, default, min_value=0, max_value=None):
        if key == "timeout.ai_engine.runtime_qa.max_concurrency":
            return 1
        return original_get_int(key, default, min_value=min_value, max_value=max_value)

    monkeypatch.setattr("src.engine.runtime_qa.get_timeout_int", fake_get_int)

    async def _run_pair():
        return await asyncio.gather(
            run_runtime_qa("<html></html>", timeout_s=2.0),
            run_runtime_qa("<html></html>", timeout_s=2.0),
        )

    first, second = asyncio.run(_run_pair())

    assert first.ran is True
    assert second.ran is True
    assert first.phase_metrics["max_concurrency"] == 1
    assert second.phase_metrics["max_concurrency"] == 1
    assert max(first.phase_metrics["queue_wait_ms"], second.phase_metrics["queue_wait_ms"]) >= 500


def test_phase_timeout_scales_with_effective_timeout():
    scaled = _phase_timeout_s(
        "timeout.ai_engine.runtime_qa.phase_interaction_s",
        6.0,
        effective_timeout_s=30.0,
        ratio_default=0.28,
    )
    assert scaled == 8.4


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


def test_runtime_qa_interaction_phase_returns_quickly_when_dispatch_is_deferred(monkeypatch):
    browser = _ClosableBrowser()
    browser.context_factory = _DeferredInteractionContext
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    result = asyncio.run(run_runtime_qa("<html></html>", timeout_s=1.0))

    assert result.ran is True
    assert result.interaction_performed is True
    assert result.registered_input_handlers == ["pointerdown"]


def test_capture_cover_artifact_prefers_settled_frame_after_interaction(monkeypatch):
    browser = _ClosableBrowser()
    browser.context_factory = _InteractiveContext
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    result = asyncio.run(capture_cover_artifact(
        "<html></html>",
        orientation="landscape_first",
        timeout_s=1.5,
        title="Nebula Rush",
        game_type="casual",
        theme="space",
        runtime_profile="casual_lane",
    ))

    assert result is not None
    assert result["content_type"] == "image/jpeg"
    assert result["metadata"]["selectedFrame"] == "settled_frame"
    assert result["metadata"]["orientation"] == "landscape"
    assert result["metadata"]["coverStyle"] == "runtime_frame_capture"
    assert result["metadata"]["coverVariant"] == "direct_runtime_frame_v2"
    assert result["metadata"]["overlayApplied"] is False
    assert result["metadata"]["title"] == "Nebula Rush"
    assert result["metadata"]["titleVisible"] is False
    assert result["metadata"]["badge"] == "SPACE LANE RUSH"
    assert result["metadata"]["theme"] == "space"
    assert result["metadata"]["paletteSource"] == "canvas"
    assert result["metadata"]["palette"]["primary"] == "#38bdf8"
    assert result["metadata"]["palette"]["secondary"] == "#c084fc"
    assert result["metadata"]["canvasChangedAfterInput"] is True


def test_capture_cover_artifact_avoids_menu_and_game_over_frames(monkeypatch):
    browser = _ClosableBrowser()
    browser.context_factory = _QualityHeuristicContext
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    result = asyncio.run(capture_cover_artifact(
        "<html></html>",
        orientation="portrait_first",
        timeout_s=1.5,
        title="Combo Runner",
        game_type="casual",
        theme="arcade",
        runtime_profile="casual_lane",
    ))

    assert result is not None
    assert result["metadata"]["selectedFrame"] == "action_frame"
    assert result["metadata"]["qualityScore"] > 0
    assert "gameplay_text" in result["metadata"]["qualityReasons"]

    candidate_map = {candidate["label"]: candidate for candidate in result["metadata"]["candidates"]}
    assert candidate_map["ready_frame"]["qualityScore"] < candidate_map["action_frame"]["qualityScore"]
    assert candidate_map["settled_frame"]["qualityScore"] < candidate_map["action_frame"]["qualityScore"]
    assert "menu_text" in candidate_map["ready_frame"]["qualityReasons"]
    assert "game_over_text" in candidate_map["settled_frame"]["qualityReasons"]


def test_capture_cover_artifact_falls_back_to_svg_when_playwright_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "playwright.async_api", raising=False)

    result = asyncio.run(capture_cover_artifact(
        "<html></html>",
        orientation="portrait_first",
        timeout_s=1.0,
        title="上班摸鱼",
        game_type="funny",
        theme="office",
        runtime_profile="casual_arcade",
    ))

    assert result is not None
    assert result["content_type"] == "image/svg+xml"
    assert result["metadata"]["selectedFrame"] == "fallback_poster"
    assert result["metadata"]["fallbackGenerated"] is True
    assert result["metadata"]["fallbackReason"] == "playwright_unavailable"
    assert result["metadata"]["coverStyle"] == "fallback_art_poster"
    assert result["metadata"]["coverVariant"].startswith("fallback_")


def test_capture_cover_artifact_falls_back_to_svg_when_runtime_capture_errors(monkeypatch):
    class _BrokenPage:
        def on(self, *_args, **_kwargs):
            return None

        async def add_init_script(self, *_args, **_kwargs):
            return None

        async def set_content(self, *_args, **_kwargs):
            return None

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def evaluate(self, _script):
            return {}

        async def screenshot(self, **_kwargs):
            raise RuntimeError("screenshot failed")

    class _BrokenContext:
        async def new_page(self):
            return _BrokenPage()

    browser = _ClosableBrowser()

    async def _broken_context(**_kwargs):
        return _BrokenContext()

    browser.new_context = _broken_context
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    result = asyncio.run(capture_cover_artifact(
        "<html></html>",
        orientation="landscape_first",
        timeout_s=1.0,
        title="Toy Rescue",
        game_type="casual",
        theme="toy",
        runtime_profile="casual_action",
    ))

    assert result is not None
    assert result["content_type"] == "image/svg+xml"
    assert result["metadata"]["fallbackGenerated"] is True
    assert result["metadata"]["fallbackReason"] == "runtimeerror"
    assert result["metadata"]["orientation"] == "landscape"
    assert result["metadata"]["coverStyle"] == "fallback_art_poster"


def test_resolve_fallback_cover_variant_is_stable_and_varied():
    first = _resolve_fallback_cover_variant(
        title="Orbital Office",
        theme="neon_city",
        runtime_profile="casual_arcade",
        game_type="funny",
        orientation="portrait_first",
    )
    second = _resolve_fallback_cover_variant(
        title="Orbital Office",
        theme="neon_city",
        runtime_profile="casual_arcade",
        game_type="funny",
        orientation="portrait_first",
    )
    different = _resolve_fallback_cover_variant(
        title="Circuit School",
        theme="forest",
        runtime_profile="puzzle_grid",
        game_type="educational",
        orientation="landscape_first",
    )

    assert first == second
    assert first["name"] in {"orbital", "spotlight", "stacked", "diagonal"}
    assert different["name"] in {"orbital", "spotlight", "stacked", "diagonal"}
    assert first["seed"] != different["seed"]


def test_capture_cover_artifact_marks_runtime_frames_as_titleless(monkeypatch):
    browser = _ClosableBrowser()
    browser.context_factory = _InteractiveContext
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    result = asyncio.run(capture_cover_artifact(
        "<html></html>",
        orientation="landscape_first",
        timeout_s=1.5,
        title="Nebula Rush",
        game_type="casual",
        theme="space",
        runtime_profile="casual_lane",
        visual_pack="neon_glass",
        render_style_intensity="high",
    ))

    assert result is not None
    assert result["metadata"]["titleVisible"] is False
    assert result["metadata"]["visualPack"] == "neon_glass"
    assert result["metadata"]["renderStyleIntensity"] == "high"


def test_fallback_cover_artifact_does_not_render_requested_title(monkeypatch):
    monkeypatch.delitem(sys.modules, "playwright.async_api", raising=False)

    result = asyncio.run(capture_cover_artifact(
        "<html></html>",
        orientation="portrait_first",
        timeout_s=1.0,
        title="Office Poster",
        game_type="funny",
        theme="office",
        runtime_profile="casual_arcade",
        visual_pack="comic_bounce",
        render_style_intensity="high",
    ))

    assert result is not None
    assert result["content_type"] == "image/svg+xml"
    assert result["metadata"]["titleVisible"] is False
    assert result["metadata"]["visualPack"] == "comic_bounce"
    assert result["metadata"]["coverVariant"].startswith("fallback_comic_bounce_")
    svg = base64.b64decode(result["payload"]).decode("utf-8")
    assert "Office Poster" not in svg


def test_runtime_qa_launch_exception_is_reported_as_infra_unavailable(monkeypatch):
    class _BrokenPlaywrightCtx:
        async def __aenter__(self):
            raise RuntimeError("browser crashed")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _BrokenPlaywrightCtx(),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    result = asyncio.run(run_runtime_qa("<html></html>", timeout_s=1.0))

    assert result.ran is False
    assert result.unavailable_kind == "infra_unavailable"
    assert result.unavailable_phase == "execution"


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


def test_runtime_qa_caps_outer_timeout_to_phase_budget_plus_headroom(monkeypatch):
    browser = _ClosableBrowser()
    browser.context_factory = _InteractiveContext
    fake_module = types.SimpleNamespace(
        async_playwright=lambda: _PlaywrightCtx(browser),
    )
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_module)

    original_get_float = sys.modules["src.engine.runtime_qa"].get_timeout_float
    real_wait_for = asyncio.wait_for
    seen_timeouts = []

    def fake_get_float(key, default, min_value=0.0, max_value=None):
        overrides = {
            "timeout.ai_engine.runtime_qa.phase_launch_s": 2.0,
            "timeout.ai_engine.runtime_qa.phase_content_load_s": 3.0,
            "timeout.ai_engine.runtime_qa.phase_interaction_s": 4.0,
            "timeout.ai_engine.runtime_qa.phase_collect_s": 5.0,
            "timeout.ai_engine.runtime_qa.phase_total_headroom_s": 1.0,
        }
        if key in overrides:
            return overrides[key]
        return original_get_float(key, default, min_value=min_value, max_value=max_value)

    async def recording_wait_for(awaitable, timeout):
        seen_timeouts.append(timeout)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr("src.engine.runtime_qa.get_timeout_float", fake_get_float)
    monkeypatch.setattr("src.engine.runtime_qa.asyncio.wait_for", recording_wait_for)

    result = asyncio.run(run_runtime_qa("<html></html>", timeout_s=600.0))

    assert result.ran is True
    assert seen_timeouts[0] == 600.0
    assert result.phase_metrics["overall_timeout_s"] == 600.0
    assert result.phase_metrics["requested_timeout_s"] == 600.0
