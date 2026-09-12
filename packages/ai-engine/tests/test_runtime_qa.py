"""Regression tests for runtime QA sampling helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine import runtime_qa


def test_canvas_check_samples_center_region_instead_of_top_left_only():
    assert "const x = Math.max(0, Math.floor((canvas.width - w) / 2));" in runtime_qa._CANVAS_CHECK_JS
    assert "const y = Math.max(0, Math.floor((canvas.height - h) / 2));" in runtime_qa._CANVAS_CHECK_JS
    assert "ctx.getImageData(x, y, w, h)" in runtime_qa._CANVAS_CHECK_JS


def test_canvas_fingerprint_samples_center_region_instead_of_top_left_only():
    assert "const x = Math.max(0, Math.floor((canvas.width - w) / 2));" in runtime_qa._CANVAS_FINGERPRINT_JS
    assert "const y = Math.max(0, Math.floor((canvas.height - h) / 2));" in runtime_qa._CANVAS_FINGERPRINT_JS
    assert "ctx.getImageData(x, y, w, h)" in runtime_qa._CANVAS_FINGERPRINT_JS


def test_interaction_probe_covers_adjacent_grid_taps_and_visible_controls():
    script = runtime_qa._INTERACTION_JS
    assert "tapPoints" in script
    assert "tapAt" in script
    assert "clickVisibleControls" in script
    assert "ArrowLeft" in script
    assert "Space" in script
    assert "offsetX" in script
    assert "/start|play|hint|shuffle|reset|restart|new|开始|提示|重来|重置/" in script
