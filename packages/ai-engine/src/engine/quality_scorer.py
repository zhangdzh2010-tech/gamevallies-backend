"""Quality Scorer – computes a 0-10 qualityScore from pipeline results.

Score components:
  - Static QA (L1-L6): penalties for errors and warnings
  - Generation strategy: template > hybrid > llm (reliability)
  - Code size heuristic: very small = minimal, very large = feature-rich
  - QA retries: each retry means first attempt was bad
  - Runtime QA (optional): bonus for clean runtime
  - LLM review (optional): fun_score and gameplay richness
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class QAStaticResult:
    passed: bool
    error_count: int
    warning_count: int
    retries: int
    strategy: str           # "template" | "hybrid" | "llm" | "mock"
    code_size_bytes: int


@dataclass
class RuntimeQAResult:
    ran: bool = False               # Playwright test actually executed
    canvas_renders: bool = False    # canvas has non-blank pixels
    js_errors: List[str] = field(default_factory=list)
    fps: float = 0.0
    load_time_ms: int = 0
    game_over_triggered: bool = False


@dataclass
class LLMReviewResult:
    ran: bool = False
    is_complete_game: bool = True
    has_real_gameplay: bool = True
    difficulty_balanced: bool = True
    fun_score: float = 5.0      # 1-10
    issues: List[str] = field(default_factory=list)


@dataclass
class QualityScoreBreakdown:
    base_score: float
    qa_penalty: float
    strategy_bonus: float
    size_bonus: float
    retry_penalty: float
    runtime_bonus: float
    review_bonus: float
    final_score: float
    details: dict


class QualityScorer:
    """Compute a 0-10 quality score from multi-layer QA results."""

    # Weight knobs
    ERROR_PENALTY = 1.5        # per static QA error
    WARNING_PENALTY = 0.3      # per static QA warning
    RETRY_PENALTY = 0.5        # per QA auto-fix retry
    STRATEGY_BONUS = {
        "template": 0.5,
        "hybrid": 0.2,
        "llm": 0.0,
        "mock": -1.0,
    }

    def compute(
        self,
        static: QAStaticResult,
        runtime: Optional[RuntimeQAResult] = None,
        review: Optional[LLMReviewResult] = None,
    ) -> QualityScoreBreakdown:
        base = 7.0

        # ── Static QA penalties ──────────────────────────────────────
        qa_penalty = (
            static.error_count * self.ERROR_PENALTY
            + static.warning_count * self.WARNING_PENALTY
        )

        # ── Strategy bonus ───────────────────────────────────────────
        strategy_bonus = self.STRATEGY_BONUS.get(static.strategy, 0.0)

        # ── Code size heuristic ──────────────────────────────────────
        size_kb = static.code_size_bytes / 1024
        if size_kb < 2:
            size_bonus = -1.0    # suspiciously tiny
        elif size_kb < 5:
            size_bonus = 0.0
        elif size_kb < 15:
            size_bonus = 0.5     # reasonable size
        elif size_kb < 50:
            size_bonus = 1.0     # feature-rich
        else:
            size_bonus = 0.5     # large but ok

        # ── QA retry penalty ─────────────────────────────────────────
        retry_penalty = static.retries * self.RETRY_PENALTY

        # ── Runtime QA bonus ─────────────────────────────────────────
        runtime_bonus = 0.0
        if runtime and runtime.ran:
            if runtime.canvas_renders:
                runtime_bonus += 0.8
            if not runtime.js_errors:
                runtime_bonus += 0.5
            if runtime.fps >= 50:
                runtime_bonus += 0.5
            elif runtime.fps >= 30:
                runtime_bonus += 0.2
            if runtime.js_errors:
                runtime_bonus -= len(runtime.js_errors) * 0.3
            if not runtime.canvas_renders:
                runtime_bonus -= 2.0   # blank canvas is critical

        # ── LLM review bonus ─────────────────────────────────────────
        review_bonus = 0.0
        if review and review.ran:
            if not review.is_complete_game:
                review_bonus -= 2.0
            if not review.has_real_gameplay:
                review_bonus -= 1.5
            if not review.difficulty_balanced:
                review_bonus -= 0.5
            # fun_score: 5 = neutral, each point above/below = ±0.2
            review_bonus += (review.fun_score - 5.0) * 0.2

        final = base - qa_penalty + strategy_bonus + size_bonus - retry_penalty + runtime_bonus + review_bonus
        final = round(max(0.0, min(10.0, final)), 2)

        return QualityScoreBreakdown(
            base_score=base,
            qa_penalty=round(qa_penalty, 2),
            strategy_bonus=strategy_bonus,
            size_bonus=size_bonus,
            retry_penalty=round(retry_penalty, 2),
            runtime_bonus=round(runtime_bonus, 2),
            review_bonus=round(review_bonus, 2),
            final_score=final,
            details={
                "errors": static.error_count,
                "warnings": static.warning_count,
                "strategy": static.strategy,
                "size_kb": round(size_kb, 1),
                "qa_retries": static.retries,
                "runtime_ran": runtime.ran if runtime else False,
                "review_ran": review.ran if review else False,
            },
        )

    def compute_from_behavior(
        self,
        current_ai_score: float,
        play_count: int,
        like_count: int,
        avg_play_time_s: float,
        expected_play_time_s: float = 60.0,
    ) -> float:
        """P2.1 – dynamically update score using post-publish behavior data."""
        if play_count < 10:
            # Not enough data yet, keep AI score
            return current_ai_score

        # Like rate: likes / plays, normalised to 0-10
        like_rate = min(like_count / play_count, 1.0)
        like_score = like_rate * 10.0

        # Retention: avg play time vs expected
        retention_ratio = min(avg_play_time_s / max(expected_play_time_s, 1), 2.0)
        retention_score = min(retention_ratio * 5.0, 10.0)

        # Weighted blend: 40% AI + 40% retention + 20% likes
        blended = 0.40 * current_ai_score + 0.40 * retention_score + 0.20 * like_score
        return round(max(0.0, min(10.0, blended)), 2)
