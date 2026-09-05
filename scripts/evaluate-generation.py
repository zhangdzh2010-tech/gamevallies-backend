#!/usr/bin/env python3
"""Summarize one JSONL row per task; no model calls or production access.

Fields: task_id (unique), status, failure_stage, request_ok, runtime_ok,
playable, intent_ok, elapsed_s, cost, gameplay_fingerprint. Optional booleans
must be true/false; absent means unassessed. Cost must use one currency per file.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path


def summarize(rows: list[dict]) -> dict:
    ids = [r.get("task_id") for r in rows]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Each row must have a unique non-empty task_id; aggregate attempts first")
    result = {"tasks": len(rows), "status": dict(Counter(r.get("status", "unknown") for r in rows))}
    for key in ("request_ok", "runtime_ok", "playable", "intent_ok"):
        values = [r.get(key) for r in rows]
        if any(v is not None and type(v) is not bool for v in values):
            raise ValueError(f"{key} must be a boolean or null")
        passed = sum(v is True for v in values)
        assessed = sum(v is not None for v in values)
        result[key] = {"passed": passed, "assessed": assessed, "unknown": len(rows) - assessed,
                       "confirmed_rate_all_tasks": passed / len(rows) if rows else None,
                       "rate_assessed": passed / assessed if assessed else None}
    result["failure_stages"] = dict(Counter(
        r.get("failure_stage") or "unknown" for r in rows
        if r.get("status") in {"failed", "timed_out", "timeout", "error"}
    ))
    for key in ("elapsed_s", "cost"):
        values = [r[key] for r in rows if r.get(key) is not None]
        if any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError(f"{key} must be finite and non-negative")
        values.sort()
        result[key] = {"known": len(values), "unknown": len(rows) - len(values), "sum": sum(values),
                       "p50": values[max(0, math.ceil(len(values) * .5) - 1)] if values else None,
                       "p95": values[max(0, math.ceil(len(values) * .95) - 1)] if values else None}
    playable = result["playable"]["passed"]
    result["cost_per_confirmed_playable"] = (
        result["cost"]["sum"] / playable
        if playable and result["cost"]["unknown"] == 0 else None
    )
    fingerprints = [r["gameplay_fingerprint"] for r in rows if r.get("gameplay_fingerprint")]
    result["exact_gameplay_duplicates"] = {
        "assessed": len(fingerprints), "unknown": len(rows) - len(fingerprints),
        "duplicates": len(fingerprints) - len(set(fingerprints)),
        "note": "Exact normalized design duplicates only; requires human gameplay review for novelty.",
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        rows = [json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
        if any(not isinstance(r, dict) for r in rows):
            raise ValueError("Every row must be a JSON object")
        output = json.dumps(summarize(rows), ensure_ascii=False, indent=2)
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))
    if args.output:
        args.output.write_text(output + "\n")
    else:
        print(output)
