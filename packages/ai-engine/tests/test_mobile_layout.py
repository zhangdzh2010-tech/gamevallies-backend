"""Regression tests for mobile layout contract detection."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.mobile_layout import has_landscape_short_edge_scaling, has_portrait_short_edge_scaling


def test_short_edge_scaling_accepts_sw_sh_aliases_for_portrait_layout():
    code = """
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const REF_W = 360;
    const REF_H = 640;
    const sw = viewportWidth / REF_W;
    const sh = viewportHeight / REF_H;
    const scale = Math.min(sw, sh);
    canvas.width = REF_W * scale;
    canvas.height = REF_H * scale;
    """

    assert has_portrait_short_edge_scaling(code) is True


def test_short_edge_scaling_accepts_sw_sh_aliases_for_landscape_layout():
    code = """
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const REF_W = 640;
    const REF_H = 360;
    const sw = viewportWidth / REF_W;
    const sh = viewportHeight / REF_H;
    const uiScale = Math.min(sw, sh);
    canvas.width = REF_W * uiScale;
    canvas.height = REF_H * uiScale;
    """

    assert has_landscape_short_edge_scaling(code) is True
