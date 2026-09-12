"""Batch generation yield test for success-rate improvement campaigns.

Runs N independent create tasks against production, records JSONL ledger rows,
fetches admin diagnostics on failures, and emits rolling + final summaries.

Example:
  python scripts/run_generation_yield_batch.py \\
    --base-url https://www.zlspace.ai \\
    --username 18909246448 \\
    --password '***' \\
    --admin-token '***' \\
    --count 50 \\
    --concurrency 2 \\
    --output tmp/yield-batch-20260911
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_live_generation_e2e import (
    DEFAULT_CASES,
    fetch_task_diagnostics,
    http_json,
    load_env,
    login,
    now_iso,
    wait_for_terminal_status,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Extra prompts beyond DEFAULT_CASES for variety in large batches.
EXTRA_CASES: list[dict[str, str]] = [
    {
        "name": "population_science_cn",
        "title": "Yield Population Model",
        "description": "作品类型：科学演示。制作捕食者与猎物种群模型，可调增长率和捕食率，按Lotka-Volterra更新曲线，有开始暂停重置。桌面横屏，不要闯关。",
        "orientation": "landscape",
        "artifact_kind": "science",
    },
    {
        "name": "mirror_optics_science_cn",
        "title": "Yield Mirror Optics",
        "description": "作品类型：科学演示。制作平面镜反射光学演示，可调入射角，入射光指向镜面交点、反射光离开交点，显示反射定律。不要游戏玩法。",
        "orientation": "landscape",
        "artifact_kind": "science",
        "expected_family": "geometric_ray_2d",
        "expected_subject": "physics",
    },
    {
        "name": "wave_interference_science_cn",
        "title": "Yield Wave Interference",
        "description": "作品类型：科学演示。制作双波源干涉与波纹演示，可调振幅和波长，实时显示合成波形与公式。桌面横屏。",
        "orientation": "landscape",
        "artifact_kind": "science",
        "expected_family": "field_or_wave_2d",
        "expected_subject": "physics",
    },
    {
        "name": "free_fall_science_cn",
        "title": "Yield Free Fall",
        "description": "作品类型：科学演示。制作自由落体实验，可调高度和重力g，显示v=gt与位移公式，有开始暂停重置。不要积分或关卡。",
        "orientation": "landscape",
        "artifact_kind": "science",
        "expected_family": "time_integrator_1d",
        "expected_subject": "physics",
    },
    {
        "name": "temp_converter_cn",
        "title": "Yield Temp Converter",
        "description": "作品类型：工具。制作摄氏和华氏双向温度转换器，输入值和单位后点击转换，正确处理负数、小数及无效输入。",
        "orientation": "landscape",
        "artifact_kind": "tool",
    },
    {
        "name": "unit_converter_cn",
        "title": "Yield Unit Converter",
        "description": "作品类型：工具。制作米与英尺双向单位换算工具，输入数值后转换，处理小数和无效输入，不要游戏玩法。",
        "orientation": "landscape",
        "artifact_kind": "tool",
    },
    {
        "name": "snake_classic_cn",
        "title": "Yield Snake Classic",
        "description": "作品类型：游戏。制作键盘控制桌面贪吃蛇，含开始、暂停、食物、得分、碰撞结束和重开，清晰说明操作。",
        "orientation": "landscape",
        "artifact_kind": "game",
    },
    {
        "name": "memory_cards_cn",
        "title": "Yield Memory Cards",
        "description": "作品类型：游戏。制作精致的4对卡片记忆配对手机竖屏小游戏，含开始、翻牌、配对、步数、胜利提示和重新开始。",
        "orientation": "portrait",
        "artifact_kind": "game",
    },
    {
        "name": "grid_puzzle_en",
        "title": "Yield Grid Puzzle EN",
        "description": "Create a small portrait mobile grid puzzle. The player taps tiles to connect matching runes and clear the board in under 20 moves. Include a score, remaining moves, a win state, a lose state, and restart.",
        "orientation": "portrait",
        "artifact_kind": "game",
    },
    {
        "name": "gas_law_science_cn",
        "title": "Yield Gas Law",
        "description": "作品类型：科学演示。制作理想气体状态方程演示，可调物质的量n、温度T和体积V，按PV=nRT显示压强，有开始暂停重置。桌面横屏，不要闯关。",
        "orientation": "landscape",
        "artifact_kind": "science",
        "expected_family": "param_formula_panel",
        "expected_subject": "chem",
    },
    {
        "name": "osmosis_science_cn",
        "title": "Yield Osmosis",
        "description": "作品类型：科学演示。制作半透膜渗透实验，两侧浓度可调，水流按浓度差流动，有开始暂停重置。不要游戏玩法。",
        "orientation": "landscape",
        "artifact_kind": "science",
        "expected_family": "compartment_flow",
        "expected_subject": "bio",
    },
    {
        "name": "enzyme_temp_science_cn",
        "title": "Yield Enzyme Temp",
        "description": "作品类型：科学演示。制作酶活性随温度变化的示意曲线，可调温度和活化能，说明热变性假设，有开始暂停重置。",
        "orientation": "landscape",
        "artifact_kind": "science",
        "expected_family": "param_formula_panel",
        "expected_subject": "bio",
    },
]

ALL_CASES = DEFAULT_CASES + EXTRA_CASES


def pick_case(run_index: int) -> dict[str, str]:
    return ALL_CASES[(run_index - 1) % len(ALL_CASES)]


INFRA_YIELD_FAMILIES = frozenset({"infra_maintenance", "provider_transport"})


def is_generation_maintenance_error(exc: BaseException) -> bool:
    message = str(exc or "")
    if "GENERATION_MAINTENANCE" in message:
        return True
    return "HTTP 503" in message and "维护" in message


def is_provider_gateway_timeout_error(exc: BaseException) -> bool:
    message = str(exc or "")
    return "504" in message and "Gateway Timeout" in message


def is_infra_yield_row(row: dict[str, Any]) -> bool:
    status = str(row.get("finalStatus") or "").lower()
    family = str(row.get("failureFamily") or "").lower()
    return status in INFRA_YIELD_FAMILIES or family in INFRA_YIELD_FAMILIES


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _unwrap_data(payload: Any) -> dict[str, Any]:
    mapping = _as_mapping(payload)
    nested = mapping.get("data")
    if isinstance(nested, dict):
        return nested
    return mapping


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def extract_quality_outcome(*payloads: Any) -> dict[str, Any]:
    """Pull seed-worthiness labels out of generation-status or admin diagnostics."""
    quality_breakdown: dict[str, Any] | None = None
    seed_worthy = None
    seed_worthy_reason = None
    pipeline_success = None
    review_fun = None
    review_visual = None
    review_character = None
    final_score = None

    def _ingest(raw: Any) -> None:
        nonlocal quality_breakdown, seed_worthy, seed_worthy_reason
        nonlocal pipeline_success, review_fun, review_visual, review_character, final_score
        mapping = _unwrap_data(raw)
        if not mapping:
            return
        summary = mapping.get("resultSummary")
        if isinstance(summary, dict):
            _ingest(summary)
        breakdown = mapping.get("qualityBreakdown") or mapping.get("quality_breakdown")
        if isinstance(breakdown, dict):
            quality_breakdown = breakdown
            _ingest(breakdown)
        if seed_worthy is None:
            value = _first_present(mapping, "seedWorthy", "seed_worthy")
            if value is not None:
                seed_worthy = bool(value)
        if not seed_worthy_reason:
            value = _first_present(mapping, "seedWorthyReason", "seed_worthy_reason")
            if value is not None:
                seed_worthy_reason = str(value)
        if pipeline_success is None:
            value = _first_present(mapping, "pipelineSuccess", "pipeline_success")
            if value is not None:
                pipeline_success = bool(value)
        if review_fun is None:
            review_fun = _first_present(mapping, "review_fun_score", "fun_score")
        if review_visual is None:
            review_visual = _first_present(mapping, "review_visual_polish_score", "visual_polish_score")
        if review_character is None:
            review_character = _first_present(
                mapping, "review_character_quality_score", "character_quality_score"
            )
        if final_score is None:
            final_score = _first_present(mapping, "final_score", "qualityScore", "quality_score")
        if seed_worthy is None:
            artifact_kind = mapping.get("artifact_kind") or mapping.get("artifactKind")
            review_ran = _first_present(mapping, "review_ran", "reviewRan")
            passed = mapping.get("passed")
            if artifact_kind in {"tool", "science"} and review_ran is not None:
                seed_worthy = bool(review_ran) and bool(passed)
                if not seed_worthy_reason:
                    seed_worthy_reason = (
                        "structured_review_passed"
                        if seed_worthy
                        else (
                            "structured_review_missing"
                            if not review_ran
                            else "quality_gate_unresolved"
                        )
                    )
                if pipeline_success is None:
                    pipeline_success = bool(passed)

    for payload in payloads:
        if payload is None:
            continue
        if isinstance(payload, dict) and "diagnostics" in payload and len(payload) <= 8:
            _ingest(payload)
            _ingest(payload.get("diagnostics"))
            diagnostics = _as_mapping(payload.get("diagnostics"))
            _ingest(diagnostics.get("task"))
            _ingest(diagnostics.get("gameStatus"))
            _ingest(diagnostics.get("qualityBreakdown"))
            continue
        _ingest(payload)

    template_route = None
    family_id = None
    recipe_id = None
    prompt_tokens = None
    completion_tokens = None
    if isinstance(quality_breakdown, dict):
        template_route = quality_breakdown.get("template_route") or quality_breakdown.get("templateRoute")
        family_id = quality_breakdown.get("family_id") or quality_breakdown.get("familyId")
        recipe_id = quality_breakdown.get("recipe_id") or quality_breakdown.get("recipeId")
        prompt_tokens = quality_breakdown.get("prompt_tokens") or quality_breakdown.get("promptTokens")
        completion_tokens = quality_breakdown.get("completion_tokens") or quality_breakdown.get("completionTokens")
        efficiency = quality_breakdown.get("generation_efficiency") or {}
        if isinstance(efficiency, dict):
            template_route = template_route or efficiency.get("template_route")
            family_id = family_id or efficiency.get("family_id")
            recipe_id = recipe_id or efficiency.get("recipe_id")
            prompt_tokens = prompt_tokens if prompt_tokens is not None else efficiency.get("prompt_tokens")
            completion_tokens = completion_tokens if completion_tokens is not None else efficiency.get("completion_tokens")

    return {
        "seedWorthy": seed_worthy,
        "seedWorthyReason": seed_worthy_reason,
        "pipelineSuccess": pipeline_success,
        "qualityBreakdown": quality_breakdown,
        "reviewFunScore": review_fun,
        "reviewVisualPolishScore": review_visual,
        "reviewCharacterQualityScore": review_character,
        "finalScore": final_score,
        "template_route": template_route,
        "family_id": family_id,
        "recipe_id": recipe_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def resolve_ledger_kind(case: dict[str, str], result: dict[str, Any], outcome: dict[str, Any]) -> str:
    breakdown = result.get("qualityBreakdown") or outcome.get("qualityBreakdown") or {}
    if not isinstance(breakdown, dict):
        breakdown = {}
    for candidate in (
        case.get("artifact_kind"),
        breakdown.get("artifact_kind"),
        breakdown.get("artifactKind"),
        result.get("artifact_kind"),
        result.get("artifactKind"),
    ):
        kind = str(candidate or "").strip().lower()
        if kind in {"game", "tool", "science"}:
            return kind
    return "game"


def ledger_row(
    *,
    run_id: str,
    run_index: int,
    case: dict[str, str],
    result: dict[str, Any],
    base_url: str,
) -> dict[str, Any]:
    final_status = str(result.get("finalStatus") or "unknown").lower()
    succeeded = final_status == "succeeded"
    outcome = extract_quality_outcome(result, result.get("statusData"), result.get("diagnostics"))
    return {
        "run_id": run_id,
        "run_index": run_index,
        "environment": "production",
        "kind": resolve_ledger_kind(case, result, outcome),
        "version": "yield-batch",
        "case_name": case["name"],
        "title": case["title"],
        "status": "passed" if succeeded else ("pending" if final_status == "unknown" else "failed"),
        "attempts": 1,
        "evidence_url": f"{base_url}/api/v1/games/{result.get('gameId')}/generation-status" if result.get("gameId") else "",
        "game_id": result.get("gameId"),
        "task_id": result.get("taskId"),
        "final_status": final_status,
        "failed_stage": result.get("failedStage"),
        "failure_family": result.get("failureFamily"),
        "error_message": result.get("errorMessage"),
        "elapsed_s": result.get("elapsedS"),
        "author_play_ok": result.get("authorPlayOk"),
        "started_at": result.get("startedAt"),
        "completed_at": result.get("completedAt"),
        "seedWorthy": result.get("seedWorthy") if result.get("seedWorthy") is not None else outcome["seedWorthy"],
        "seedWorthyReason": result.get("seedWorthyReason") or outcome["seedWorthyReason"],
        "pipelineSuccess": result.get("pipelineSuccess") if result.get("pipelineSuccess") is not None else outcome["pipelineSuccess"],
        "qualityBreakdown": result.get("qualityBreakdown") or outcome["qualityBreakdown"],
        "reviewFunScore": outcome["reviewFunScore"],
        "reviewVisualPolishScore": outcome["reviewVisualPolishScore"],
        "reviewCharacterQualityScore": outcome["reviewCharacterQualityScore"],
        "finalScore": outcome["finalScore"],
        "template_route": result.get("template_route") or outcome.get("template_route"),
        "family_id": result.get("family_id") or outcome.get("family_id"),
        "recipe_id": result.get("recipe_id") or outcome.get("recipe_id"),
        "prompt_tokens": result.get("prompt_tokens") if result.get("prompt_tokens") is not None else outcome.get("prompt_tokens"),
        "completion_tokens": result.get("completion_tokens") if result.get("completion_tokens") is not None else outcome.get("completion_tokens"),
    }


def run_single(
    *,
    run_index: int,
    base_url: str,
    admin_token: str,
    bearer_headers: dict[str, str],
    case: dict[str, str],
    timeout_s: int,
    wait_s: int,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    started_mono = time.monotonic()
    started_at = now_iso()
    create_payload: dict[str, Any] = {
        "title": f"{case['title']} #{run_index}",
        "description": case["description"],
        "regionHint": "cn_shanghai",
        "timeoutS": timeout_s,
    }
    if case.get("orientation") in {"portrait", "landscape"}:
        create_payload["orientation"] = case["orientation"]
    create_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/generate",
        payload=create_payload,
        headers=bearer_headers,
        timeout=60,
    )
    create_data = (create_response or {}).get("data") or {}
    game_id = create_data.get("gameId")
    task_id = create_data.get("taskId")
    if not game_id:
        raise RuntimeError(f"Generate did not return gameId: {create_response}")

    terminal = wait_for_terminal_status(
        base_url,
        game_id,
        bearer_headers,
        max_wait_s=wait_s,
        poll_interval_s=15,
    )
    status_data = terminal.get("status") or {}
    final_status = str(status_data.get("status") or "").lower() or "unknown"
    task_id = status_data.get("taskId") or task_id
    elapsed_s = round(time.monotonic() - started_mono, 1)
    quality_outcome = extract_quality_outcome(status_data)

    result: dict[str, Any] = {
        "runId": run_id,
        "runIndex": run_index,
        "name": case["name"],
        "title": case["title"],
        "startedAt": started_at,
        "gameId": game_id,
        "taskId": task_id,
        "finalStatus": final_status,
        "failedStage": status_data.get("failedStage"),
        "failureFamily": status_data.get("failureFamily"),
        "errorMessage": status_data.get("errorMessage"),
        "statusData": status_data,
        "pollHistory": terminal.get("history") or [],
        "timedOutLocally": bool(terminal.get("timeout")),
        "elapsedS": elapsed_s,
        "completedAt": now_iso(),
        "seedWorthy": quality_outcome["seedWorthy"],
        "seedWorthyReason": quality_outcome["seedWorthyReason"],
        "pipelineSuccess": quality_outcome["pipelineSuccess"],
        "qualityBreakdown": quality_outcome["qualityBreakdown"],
        "template_route": quality_outcome.get("template_route"),
        "family_id": quality_outcome.get("family_id"),
        "recipe_id": quality_outcome.get("recipe_id"),
        "prompt_tokens": quality_outcome.get("prompt_tokens"),
        "completion_tokens": quality_outcome.get("completion_tokens"),
    }

    if final_status == "succeeded":
        try:
            play_response = http_json(
                "GET",
                f"{base_url}/api/v1/games/{game_id}/play",
                headers=bearer_headers,
                timeout=60,
            )
            html_code = (((play_response or {}).get("data") or {}).get("htmlCode") or "")
            result["authorPlayOk"] = len(html_code) > 500
            result["authorPlayHtmlLength"] = len(html_code)
        except Exception as exc:
            result["authorPlayOk"] = False
            result["authorPlayError"] = str(exc)
    else:
        result["authorPlayOk"] = False
        result["diagnostics"] = fetch_task_diagnostics(
            base_url,
            game_id,
            task_id,
            admin_token,
            bearer_headers,
        )
        diagnostic_outcome = extract_quality_outcome(result, result["diagnostics"])
        if result.get("seedWorthy") is None:
            result["seedWorthy"] = diagnostic_outcome["seedWorthy"]
        if not result.get("seedWorthyReason"):
            result["seedWorthyReason"] = diagnostic_outcome["seedWorthyReason"]
        if result.get("pipelineSuccess") is None:
            result["pipelineSuccess"] = diagnostic_outcome["pipelineSuccess"]
        if not result.get("qualityBreakdown"):
            result["qualityBreakdown"] = diagnostic_outcome["qualityBreakdown"]

    return result


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_summary(
    output_dir: Path,
    *,
    base_url: str,
    count: int,
    results: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    statuses = Counter(str(r.get("finalStatus") or "unknown") for r in results)
    families = Counter(
        str(r.get("failureFamily") or "unknown")
        for r in results
        if str(r.get("finalStatus") or "").lower() != "succeeded"
    )
    stages = Counter(
        str(r.get("failedStage") or "unknown")
        for r in results
        if str(r.get("finalStatus") or "").lower() != "succeeded"
    )
    succeeded = statuses.get("succeeded", 0)
    completed = len(results)
    product = [r for r in results if not is_infra_yield_row(r)]
    product_succeeded = sum(1 for r in product if str(r.get("finalStatus") or "").lower() == "succeeded")
    seed_labeled = [r for r in product if r.get("seedWorthy") is not None]
    seed_worthy = sum(1 for r in seed_labeled if r.get("seedWorthy"))
    summary = {
        "generatedAt": now_iso(),
        "baseUrl": base_url,
        "targetCount": count,
        "completed": completed,
        "succeeded": succeeded,
        "failed": completed - succeeded,
        "successRate": round(succeeded / completed, 4) if completed else None,
        "infraMaintenance": statuses.get("infra_maintenance", 0),
        "productCompleted": len(product),
        "productSucceeded": product_succeeded,
        "productSuccessRate": round(product_succeeded / len(product), 4) if product else None,
        "seedWorthyLabeled": len(seed_labeled),
        "seedWorthy": seed_worthy,
        "seedWorthyRate": round(seed_worthy / len(seed_labeled), 4) if seed_labeled else None,
        "statusBreakdown": dict(statuses),
        "failureFamilyBreakdown": dict(families),
        "failedStageBreakdown": dict(stages),
        "avgElapsedS": round(
            sum(float(r.get("elapsedS") or 0) for r in results) / completed,
            1,
        )
        if completed
        else None,
        "results": results,
        "ledger": ledger_rows,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env.production"))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-password", default="")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=1, choices=[1, 2, 3])
    parser.add_argument("--timeout-s", type=int, default=1200)
    parser.add_argument("--wait-s", type=int, default=1500)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-index", type=int, default=1)
    args = parser.parse_args(argv)

    env = load_env(Path(args.env_file))
    base_url = (
        args.base_url
        or env.get("PUBLIC_API_BASE_URL")
        or "https://www.zlspace.ai"
    ).rstrip("/")
    admin_token = args.admin_token or env.get("ADMIN_TOKEN") or ""
    if not admin_token:
        admin_password = args.admin_password or env.get("ADMIN_PASSWORD") or ""
        if not admin_password:
            print("ADMIN_TOKEN or --admin-password is required", file=sys.stderr)
            return 1
        admin_login = http_json(
            "POST",
            f"{base_url}/api/v1/admin/login",
            payload={"username": args.admin_username, "password": admin_password},
            timeout=30,
        )
        admin_token = ((admin_login or {}).get("data") or admin_login or {}).get("token") or ""
    if not admin_token:
        print("ADMIN_TOKEN or admin login required", file=sys.stderr)
        return 1

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "ledger.jsonl"
    if ledger_path.exists() and args.start_index == 1:
        print(f"Refusing to overwrite existing ledger: {ledger_path}", file=sys.stderr)
        return 1

    token = login(base_url, args.username, args.password)
    bearer_headers = {"Authorization": f"Bearer {token}"}

    quota = http_json("GET", f"{base_url}/api/v1/users/quota", headers=bearer_headers, timeout=30)
    quota_data = (quota or {}).get("data") or {}
    sub = quota_data.get("subscription") or {}
    remaining = int(quota_data.get("freeQuota") or 0) + (
        max(0, int(sub.get("quotaThisPeriod") or 0) - int(sub.get("usedThisPeriod") or 0))
        if sub.get("active")
        else 0
    )
    print(
        json.dumps(
            {
                "event": "batch_start",
                "baseUrl": base_url,
                "count": args.count,
                "concurrency": args.concurrency,
                "remainingQuota": remaining,
                "output": str(output_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if remaining < args.count:
        print(
            json.dumps(
                {
                    "event": "quota_warning",
                    "remaining": remaining,
                    "requested": args.count,
                    "message": "Proceeding anyway; failures may occur when quota exhausted",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    results: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    end_index = args.start_index + args.count - 1

    def worker(run_index: int) -> dict[str, Any]:
        case = pick_case(run_index)
        print(
            json.dumps(
                {"event": "run_start", "runIndex": run_index, "case": case["name"]},
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            result = run_single(
                run_index=run_index,
                base_url=base_url,
                admin_token=admin_token,
                bearer_headers=bearer_headers,
                case=case,
                timeout_s=args.timeout_s,
                wait_s=args.wait_s,
            )
        except Exception as exc:
            maintenance = is_generation_maintenance_error(exc)
            transport = is_provider_gateway_timeout_error(exc)
            family = (
                "infra_maintenance" if maintenance
                else "provider_transport" if transport
                else "script_error"
            )
            result = {
                "runIndex": run_index,
                "name": case["name"],
                "title": case["title"],
                "startedAt": now_iso(),
                "finalStatus": "infra_maintenance" if maintenance else "script_error",
                "failureFamily": family,
                "errorMessage": str(exc),
                "completedAt": now_iso(),
                "seedWorthy": False,
                "seedWorthyReason": family,
            }
        row = ledger_row(
            run_id=result.get("runId") or str(uuid.uuid4()),
            run_index=run_index,
            case=case,
            result=result,
            base_url=base_url,
        )
        return {"result": result, "ledger": row}

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(worker, run_index): run_index
            for run_index in range(args.start_index, end_index + 1)
        }
        for future in as_completed(futures):
            run_index = futures[future]
            payload = future.result()
            result = payload["result"]
            row = payload["ledger"]
            results.append(result)
            ledger_rows.append(row)
            append_jsonl(ledger_path, row)
            summary = write_summary(
                output_dir,
                base_url=base_url,
                count=args.count,
                results=sorted(results, key=lambda item: item.get("runIndex") or 0),
                ledger_rows=sorted(ledger_rows, key=lambda item: item.get("run_index") or 0),
            )
            print(
                json.dumps(
                    {
                        "event": "run_complete",
                        "runIndex": run_index,
                        "finalStatus": result.get("finalStatus"),
                        "failureFamily": result.get("failureFamily"),
                        "failedStage": result.get("failedStage"),
                        "elapsedS": result.get("elapsedS"),
                        "completed": len(results),
                        "succeeded": summary["succeeded"],
                        "successRate": summary["successRate"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    print(
        json.dumps(
            {
                "event": "batch_complete",
                "output": str(output_dir),
                "ledger": str(ledger_path),
                "summary": str(output_dir / "summary.json"),
                **{
                    k: summary[k]
                    for k in ("completed", "succeeded", "failed", "successRate")
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if summary.get("successRate") == 1.0 else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
