import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.code_reviewer import _build_code_preview


def test_build_code_preview_keeps_short_code_unchanged():
    html = "<html><body>Hello</body></html>"
    assert _build_code_preview(html, limit=100) == html


def test_build_code_preview_keeps_head_and_tail_for_long_code():
    html = "<html>" + ("A" * 5000) + ("B" * 5000) + "</html>"
    preview = _build_code_preview(html, limit=100)

    assert preview.startswith("<html>")
    assert preview.endswith("</html>")
    assert "middle omitted for review" in preview
