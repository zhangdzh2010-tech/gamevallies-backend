import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from analyze_yield_batch import analyze
from run_generation_yield_batch import (
    extract_quality_outcome,
    is_generation_maintenance_error,
    ledger_row,
)


def test_generation_maintenance_is_classified_as_infra_not_script_error():
    assert is_generation_maintenance_error(
        RuntimeError(
            "HTTP 503 https://www.zlspace.ai/api/v1/games/generate: "
            "{'code': 'GENERATION_MAINTENANCE', 'message': '服务更新中，请稍后重试；当前创作未扣费。'}"
        )
    )
    assert not is_generation_maintenance_error(RuntimeError("HTTP 500 upstream timeout"))


def test_extract_quality_outcome_from_generation_status_and_admin_diagnostics():
    status = {
        "data": {
            "status": "succeeded",
            "resultSummary": {
                "qualityScore": 7.2,
                "qualityBreakdown": {
                    "pipeline_success": True,
                    "seed_worthy": True,
                    "seed_worthy_reason": "structured_review_passed",
                    "review_fun_score": 7.4,
                    "review_visual_polish_score": 7.1,
                    "review_character_quality_score": 6.6,
                    "final_score": 7.2,
                },
                "seedWorthy": True,
                "seedWorthyReason": "structured_review_passed",
            },
        }
    }
    outcome = extract_quality_outcome(status)
    assert outcome["seedWorthy"] is True
    assert outcome["seedWorthyReason"] == "structured_review_passed"
    assert outcome["pipelineSuccess"] is True
    assert outcome["reviewFunScore"] == 7.4
    assert outcome["finalScore"] == 7.2

    degraded = extract_quality_outcome({
        "diagnostics": {
            "task": {
                "data": {
                    "resultSummary": {
                        "seedWorthy": False,
                        "seedWorthyReason": "review_actionability_degraded",
                        "qualityBreakdown": {"pipeline_success": True, "seed_worthy": False},
                    }
                }
            }
        }
    })
    assert degraded["seedWorthy"] is False
    assert degraded["seedWorthyReason"] == "review_actionability_degraded"
    assert degraded["pipelineSuccess"] is True


def test_ledger_row_persists_seed_worthy_and_quality_breakdown():
    row = ledger_row(
        run_id="run-1",
        run_index=3,
        case={"name": "simple_dodge_cn", "title": "Dodge"},
        result={
            "finalStatus": "succeeded",
            "gameId": "game-1",
            "taskId": "task-1",
            "elapsedS": 12,
            "authorPlayOk": True,
            "seedWorthy": True,
            "seedWorthyReason": "structured_review_passed",
            "pipelineSuccess": True,
            "qualityBreakdown": {"seed_worthy": True, "review_fun_score": 7.5},
        },
        base_url="https://www.zlspace.ai",
    )
    assert row["seedWorthy"] is True
    assert row["seedWorthyReason"] == "structured_review_passed"
    assert row["qualityBreakdown"]["review_fun_score"] == 7.5


def test_analyze_excludes_infra_maintenance_from_product_and_seed_rates(tmp_path: Path):
    summary = {
        "targetCount": 4,
        "results": [
            {
                "finalStatus": "succeeded",
                "name": "simple_dodge_cn",
                "elapsedS": 10,
                "seedWorthy": True,
                "qualityBreakdown": {"seed_worthy": True},
            },
            {
                "finalStatus": "succeeded",
                "name": "complex_runner_cn",
                "elapsedS": 12,
                "seedWorthy": False,
                "seedWorthyReason": "review_actionability_degraded",
                "qualityBreakdown": {"pipeline_success": True, "seed_worthy": False},
            },
            {
                "finalStatus": "infra_maintenance",
                "failureFamily": "infra_maintenance",
                "name": "grid_puzzle_en",
                "elapsedS": 1,
                "errorMessage": "HTTP 503 GENERATION_MAINTENANCE",
            },
            {
                "finalStatus": "failed",
                "failureFamily": "code_generation",
                "failedStage": "logic_generate",
                "name": "lane_runner_en",
                "elapsedS": 20,
                "seedWorthy": False,
            },
        ],
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    report = analyze(path)
    assert report["infraMaintenance"] == 1
    assert report["productCompleted"] == 3
    assert report["succeeded"] == 2
    assert report["failed"] == 1
    assert report["observedPipelineSuccessRate"] == 0.6667
    assert report["seedWorthyLabeled"] == 3
    assert report["seedWorthy"] == 1
    assert report["observedSeedWorthyRate"] == 0.3333
    assert "infra_maintenance" in " ".join(report["architectureRecommendations"])
