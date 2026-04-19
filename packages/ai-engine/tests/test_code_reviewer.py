import os
import sys
import asyncio
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.engine.code_reviewer import CodeReviewer, _build_code_preview


def test_build_code_preview_keeps_short_code_unchanged():
    html = "<html><body>Hello</body></html>"
    assert _build_code_preview(html, limit=100) == html


def test_build_code_preview_keeps_head_and_tail_for_long_code():
    html = "<html>" + ("A" * 5000) + ("B" * 5000) + "</html>"
    preview = _build_code_preview(html, limit=100)

    assert preview.startswith("<html>")
    assert preview.endswith("</html>")
    assert "middle omitted for review" in preview


def test_review_uses_safe_prompt_format_for_literal_json_examples():
    reviewer = CodeReviewer()

    def fake_require_prompt(key: str) -> str:
        if key == "prompt.code_review_system":
            return "Return JSON only."
        if key == "prompt.code_review_template":
            return (
                "Return JSON with these fields exactly:\n"
                "{\n"
                '  "is_complete_game": false,\n'
                '  "has_real_gameplay": false,\n'
                '  "difficulty_balanced": false,\n'
                '  "fun_score": 5,\n'
                '  "visual_polish_score": 5,\n'
                '  "character_quality_score": 5,\n'
                '  "issues": []\n'
                "}\n"
                "Preview:\n{code_preview}"
            )
        raise AssertionError(key)

    with patch("src.engine.code_reviewer.require_prompt", side_effect=fake_require_prompt), patch.object(
        reviewer._client,
        "is_enabled",
        return_value=True,
    ), patch.object(
        reviewer._client,
        "complete_with_truncation_retry",
        new=AsyncMock(return_value='{"is_complete_game": true, "has_real_gameplay": true, "difficulty_balanced": true, "fun_score": 8, "visual_polish_score": 7, "character_quality_score": 6, "issues": []}'),
    ):
        result = asyncio.run(reviewer.review("<html><body>ok</body></html>"))

    assert result.ran is True
    assert result.is_complete_game is True
    assert result.has_real_gameplay is True
    assert result.visual_polish_score == 7.0
    assert result.character_quality_score == 6.0
