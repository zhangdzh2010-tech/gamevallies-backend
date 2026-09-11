#!/usr/bin/env python3
"""Analyze yield batch JSONL/summary and emit architecture-oriented failure report."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def lower_bound(successes: int, total: int, alpha: float = 0.05) -> float:
    if not successes or not total:
        return 0.0

    def tail(p: float) -> float:
        if p <= 0:
            return 0.0
        if p >= 1:
            return 1.0
        return sum(
            math.exp(
                math.lgamma(total + 1)
                - math.lgamma(k + 1)
                - math.lgamma(total - k + 1)
                + k * math.log(p)
                + (total - k) * math.log1p(-p)
            )
            for k in range(successes, total + 1)
        )

    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if tail(mid) < alpha:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def analyze(summary_path: Path) -> dict:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    results = summary.get("results") or []
    terminal = [r for r in results if str(r.get("finalStatus") or "").lower() != "unknown"]
    INFRA_YIELD_FAMILIES = frozenset({"infra_maintenance", "provider_transport"})

    def _is_infra(row: dict) -> bool:
        status = str(row.get("finalStatus") or "").lower()
        family = str(row.get("failureFamily") or "").lower()
        return status in INFRA_YIELD_FAMILIES or family in INFRA_YIELD_FAMILIES

    product = [r for r in terminal if not _is_infra(r)]
    succeeded = sum(1 for r in product if str(r.get("finalStatus")).lower() == "succeeded")
    failed = [r for r in product if str(r.get("finalStatus")).lower() != "succeeded"]
    maintenance = [
        r
        for r in terminal
        if str(r.get("finalStatus") or "").lower() == "infra_maintenance"
        or str(r.get("failureFamily") or "").lower() == "infra_maintenance"
    ]
    provider_transport = [
        r for r in terminal if str(r.get("failureFamily") or "").lower() == "provider_transport"
    ]

    families = Counter(str(r.get("failureFamily") or "unknown") for r in failed)
    stages = Counter(str(r.get("failedStage") or "unknown") for r in failed)
    cases = Counter(str(r.get("name") or "unknown") for r in failed)

    elapsed = [float(r["elapsedS"]) for r in terminal if r.get("elapsedS") is not None]
    elapsed.sort()

    diagnostics_samples = []
    for row in failed[:5]:
        diag = row.get("diagnostics") or {}
        events = (((diag.get("events") or {}).get("data") or {}).get("items")) or []
        diagnostics_samples.append(
            {
                "runIndex": row.get("runIndex"),
                "case": row.get("name"),
                "failureFamily": row.get("failureFamily"),
                "failedStage": row.get("failedStage"),
                "errorMessage": (row.get("errorMessage") or "")[:300],
                "eventCount": len(events),
                "lastEvents": [
                    {
                        "stage": e.get("stage") or e.get("progressStage"),
                        "message": (e.get("message") or e.get("progressMessage") or "")[:160],
                    }
                    for e in events[-5:]
                ],
            }
        )

    completed = len(terminal)

    def _seed_worthy(row: dict) -> bool | None:
        if row.get("seedWorthy") is not None:
            return bool(row.get("seedWorthy"))
        breakdown = row.get("qualityBreakdown") or {}
        if not isinstance(breakdown, dict):
            breakdown = {}
        if "seed_worthy" in breakdown:
            return bool(breakdown.get("seed_worthy"))
        diagnostics = row.get("diagnostics") or {}
        nested = diagnostics.get("qualityBreakdown") or {}
        if isinstance(nested, dict) and "seed_worthy" in nested:
            return bool(nested.get("seed_worthy"))
        return None

    labeled = [row for row in product if _seed_worthy(row) is not None]
    seed_worthy = sum(1 for row in labeled if _seed_worthy(row))
    seed_rate_n = len(labeled)
    seed_bound = round(lower_bound(seed_worthy, seed_rate_n), 4) if seed_rate_n else None
    return {
        "source": str(summary_path),
        "completed": completed,
        "infraMaintenance": len(maintenance),
        "providerTransport": len(provider_transport),
        "productCompleted": len(product),
        "targetCount": summary.get("targetCount"),
        "succeeded": succeeded,
        "failed": len(product) - succeeded,
        "observedPipelineSuccessRate": round(succeeded / len(product), 4) if product else None,
        "observedSuccessRate": round(succeeded / len(product), 4) if product else None,
        "oneSided95LowerBound": round(lower_bound(succeeded, len(product)), 4) if product else None,
        "seedWorthyLabeled": seed_rate_n,
        "seedWorthy": seed_worthy,
        "observedSeedWorthyRate": round(seed_worthy / seed_rate_n, 4) if seed_rate_n else None,
        "oneSided95LowerBoundSeedWorthy": seed_bound,
        "target99UsesSeedWorthyBar": True,
        "target99Supported": (
            seed_rate_n >= 50 and seed_bound is not None and seed_bound >= 0.99
            if seed_rate_n
            else False
        ),
        "note": (
            "99% target is seed_worthy (structured review + quality/creativity + "
            "contract/runtime), not pipeline_success or HTTP 200. Degraded review "
            "completions may count as pipeline_success but are not catalog seeds."
        ),
        "statusBreakdown": dict(Counter(str(r.get("finalStatus") or "unknown") for r in terminal)),
        "infraMaintenanceCount": len(maintenance),
        "failureFamilyBreakdown": dict(families),
        "failedStageBreakdown": dict(stages),
        "failedCaseBreakdown": dict(cases),
        "latency": {
            "p50": elapsed[len(elapsed) // 2] if elapsed else None,
            "p95": elapsed[max(0, math.ceil(len(elapsed) * 0.95) - 1)] if elapsed else None,
            "max": max(elapsed) if elapsed else None,
            "avg": round(sum(elapsed) / len(elapsed), 1) if elapsed else None,
        },
        "diagnosticsSamples": diagnostics_samples,
        "architectureRecommendations": build_recommendations(
            families, stages,
            infra_maintenance=len(maintenance),
            provider_transport=len(provider_transport),
        ),
    }


def build_recommendations(
    families: Counter,
    stages: Counter,
    infra_maintenance: int = 0,
    provider_transport: int = 0,
) -> list[str]:
    recs: list[str] = []
    if families.get("interactive_validation", 0) + families.get("artifact_quality", 0) > 0:
        recs.append(
            "桌面交互验收（interactive_validation/artifact_quality）失败占比较高："
            "区分 smoke 覆盖与语义断言，对 standard 档增加 motion 观察窗口与控件发现容错，"
            "保留 failed candidate 供局部修复而非整页重生成。"
        )
    if families.get("review_infrastructure", 0) + families.get("review_evidence", 0) + families.get("review_actionability", 0) > 0:
        recs.append(
            "审核基础设施/证据校验失败：standard/safe 档在 review 不可用时降级为静态+运行 QA；"
            "showcase 保持 fail-closed；recover_review 已有一次纠错，可扩展 timeout_retry。"
        )
    if families.get("pipeline", 0) + families.get("code_generation", 0) > 0:
        recs.append(
            "代码生成失败：检查 truncation 与 preflight 修复；"
            "transport 错误不得当作创意/质量失败，也不得触发 quality 全量重生成。"
        )
    if provider_transport or families.get("provider_transport", 0) > 0:
        recs.append(
            "provider_transport（504/502/429 等网关超时）是基础设施，不是创意失败："
            "应对同一质量门槛做有界退避重试并切换备用线路，排除在产品/seed 成功率之外。"
        )
    if families.get("quality_gate", 0) + families.get("quality_repair_exhausted", 0) > 0:
        recs.append(
            "质量门槛/修复耗尽：优先走 quality_gate patch 局部修复，"
            "扩大 QUALITY_GATE_PATCH 预算后再消耗 full regeneration attempt。"
        )
    if families.get("contract_qa", 0) > 0:
        recs.append(
            "contract_qa 失败：检查 runtime contract 与 touch/pointer 要求是否对桌面/工具类作品过严。"
        )
    if infra_maintenance or families.get("infra_maintenance", 0) > 0:
        recs.append(
            "infra_maintenance（HTTP 503 GENERATION_MAINTENANCE）应排除在产品成功率之外，"
            "不计入 seed_worthy 或 pipeline_success 口径。"
        )
    if stages.get("runtime_simulation_qa", 0) >= stages.get("code_review", 0) and stages.get("runtime_simulation_qa", 0) > 0:
        recs.append(
            "失败集中在 runtime_simulation_qa：优先优化 Playwright 探针与 qa_fix 循环，"
            "而非提高 LLM 全量生成次数。"
        )
    if not recs:
        recs.append("当前批次暂无失败样本，继续扩大样本量以验证 99% 目标。")
    return recs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, help="Path to summary.json from yield batch")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.summary)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
