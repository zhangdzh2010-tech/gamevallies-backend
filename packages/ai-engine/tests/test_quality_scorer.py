"""Unit tests for QualityScorer."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from src.engine.quality_scorer import (
    QualityScorer, QAStaticResult, RuntimeQAResult, LLMReviewResult,
)

scorer = QualityScorer()


def make_static(**kwargs) -> QAStaticResult:
    defaults = dict(passed=True, error_count=0, warning_count=0,
                    retries=0, strategy="llm", code_size_bytes=10_000)
    defaults.update(kwargs)
    return QAStaticResult(**defaults)


class TestQualityScorer:
    def test_perfect_game_scores_high(self):
        static = make_static(strategy="llm", code_size_bytes=15_000)
        runtime = RuntimeQAResult(ran=True, canvas_renders=True, js_errors=[], fps=60)
        review = LLMReviewResult(ran=True, is_complete_game=True, has_real_gameplay=True,
                                 difficulty_balanced=True, fun_score=8.0,
                                 visual_polish_score=8.0, character_quality_score=7.5)
        bd = scorer.compute(static, runtime, review)
        assert bd.final_score >= 8.0

    def test_broken_game_scores_low(self):
        static = make_static(error_count=5, warning_count=3, retries=3, strategy="llm",
                             code_size_bytes=500)
        runtime = RuntimeQAResult(ran=True, canvas_renders=False, js_errors=["Error: x"], fps=0)
        review = LLMReviewResult(ran=True, is_complete_game=False, has_real_gameplay=False,
                                 fun_score=2.0, visual_polish_score=2.0, character_quality_score=2.0)
        bd = scorer.compute(static, runtime, review)
        assert bd.final_score <= 2.0

    def test_score_clamped_0_to_10(self):
        # Force extreme values
        static = make_static(error_count=100, strategy="llm", code_size_bytes=100)
        bd = scorer.compute(static)
        assert 0.0 <= bd.final_score <= 10.0

    def test_no_runtime_still_scores(self):
        static = make_static(strategy="llm", code_size_bytes=8_000)
        bd = scorer.compute(static)
        assert bd.final_score > 0

    def test_llm_strategy_bonus_is_neutral(self):
        breakdown = scorer.compute(make_static(strategy="llm"))
        assert breakdown.strategy_bonus == 0.0

    def test_retries_penalty(self):
        s_no_retry = make_static(retries=0)
        s_retry = make_static(retries=3)
        assert scorer.compute(s_no_retry).final_score > scorer.compute(s_retry).final_score

    def test_runtime_canvas_bonus(self):
        static = make_static()
        rt_good = RuntimeQAResult(ran=True, canvas_renders=True, js_errors=[], fps=60)
        rt_bad = RuntimeQAResult(ran=True, canvas_renders=False, js_errors=["err"], fps=0)
        assert scorer.compute(static, rt_good).final_score > scorer.compute(static, rt_bad).final_score

    def test_breakdown_details_present(self):
        static = make_static(strategy="llm", code_size_bytes=12_000)
        bd = scorer.compute(static)
        assert "errors" in bd.details
        assert "strategy" in bd.details
        assert "size_kb" in bd.details

    def test_visual_and_character_scores_affect_quality(self):
        static = make_static(strategy="llm", code_size_bytes=12_000)
        runtime = RuntimeQAResult(ran=True, canvas_renders=True, js_errors=[], fps=60)
        low_review = LLMReviewResult(
            ran=True,
            is_complete_game=True,
            has_real_gameplay=True,
            difficulty_balanced=True,
            fun_score=7.0,
            visual_polish_score=4.0,
            character_quality_score=4.0,
        )
        high_review = LLMReviewResult(
            ran=True,
            is_complete_game=True,
            has_real_gameplay=True,
            difficulty_balanced=True,
            fun_score=7.0,
            visual_polish_score=8.0,
            character_quality_score=8.0,
        )
        assert scorer.compute(static, runtime, high_review).final_score > scorer.compute(static, runtime, low_review).final_score

    def test_prototype_like_visuals_do_not_score_as_premium(self):
        static = make_static(strategy="llm", code_size_bytes=28_000)
        runtime = RuntimeQAResult(ran=True, canvas_renders=True, js_errors=[], fps=55)
        review = LLMReviewResult(
            ran=True,
            is_complete_game=True,
            has_real_gameplay=True,
            difficulty_balanced=True,
            fun_score=7.0,
            visual_polish_score=5.0,
            character_quality_score=4.5,
        )

        breakdown = scorer.compute(static, runtime, review)

        assert breakdown.final_score < 7.0

    def test_behavior_score_not_updated_below_10_plays(self):
        score = scorer.compute_from_behavior(
            current_ai_score=7.0, play_count=5, like_count=3,
            avg_play_time_s=45.0
        )
        assert score == 7.0  # unchanged

    def test_behavior_score_updates_above_10_plays(self):
        # High engagement: many likes, long play time
        score = scorer.compute_from_behavior(
            current_ai_score=7.0, play_count=100, like_count=80,
            avg_play_time_s=90.0
        )
        assert score > 7.0  # should increase

    def test_behavior_score_decreases_for_bad_games(self):
        # Low retention: few likes, short play time
        score = scorer.compute_from_behavior(
            current_ai_score=7.0, play_count=100, like_count=2,
            avg_play_time_s=5.0
        )
        assert score < 7.0


class TestCodeReviewerParsing:
    """Test CodeReviewer._parse_review without hitting the LLM."""
    def setup_method(self):
        from src.engine.code_reviewer import CodeReviewer
        self.reviewer = CodeReviewer()

    def test_valid_json_parsed(self):
        raw = '{"is_complete_game": true, "has_real_gameplay": true, "difficulty_balanced": false, "fun_score": 7, "visual_polish_score": 8, "character_quality_score": 6, "issues": ["too easy"]}'
        result = self.reviewer._parse_review(raw)
        assert result.ran is True
        assert result.is_complete_game is True
        assert result.difficulty_balanced is False
        assert result.fun_score == 7.0
        assert result.visual_polish_score == 8.0
        assert result.character_quality_score == 6.0
        assert result.issues == ["too easy"]

    def test_json_with_markdown_fences_parsed(self):
        raw = "```json\n{\"is_complete_game\": true, \"has_real_gameplay\": false, \"difficulty_balanced\": true, \"fun_score\": 3, \"visual_polish_score\": 7, \"character_quality_score\": 7, \"issues\": []}\n```"
        result = self.reviewer._parse_review(raw)
        assert result.ran is True
        assert result.has_real_gameplay is False

    def test_invalid_json_returns_not_ran(self):
        result = self.reviewer._parse_review("this is not json at all")
        assert result.ran is False

    def test_out_of_range_fun_score_rejected(self):
        raw = '{"is_complete_game": true, "has_real_gameplay": true, "difficulty_balanced": true, "fun_score": 15, "visual_polish_score": 7, "character_quality_score": 7, "issues": []}'
        result = self.reviewer._parse_review(raw)
        assert result.ran is False

    def test_negative_fun_score_rejected(self):
        raw = '{"is_complete_game": true, "has_real_gameplay": true, "difficulty_balanced": true, "fun_score": -5, "visual_polish_score": 7, "character_quality_score": 7, "issues": []}'
        result = self.reviewer._parse_review(raw)
        assert result.ran is False

    def test_invalid_visual_and_character_scores_are_rejected(self):
        raw = '{"is_complete_game": true, "has_real_gameplay": true, "difficulty_balanced": true, "fun_score": 8, "visual_polish_score": 12, "character_quality_score": 0, "issues": []}'
        result = self.reviewer._parse_review(raw)
        assert result.ran is False
