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
        "name": "memory_cards_cn",
        "title": "Yield Memory Cards",
        "description": "作品类型：游戏。制作精致的4对卡片记忆配对游戏，含开始、翻牌、配对、步数、胜利提示和重新开始。",
    },
    {
        "name": "reaction_timer_cn",
        "title": "Yield Reaction Timer",
        "description": "作品类型：游戏。制作五轮反应力挑战，等待随机信号再点击，抢跑判罚，显示每轮毫秒和最终成绩，可重玩。",
    },
    {
        "name": "snake_classic_cn",
        "title": "Yield Snake Classic",
        "description": "作品类型：游戏。制作键盘控制贪吃蛇，含开始、暂停、食物、得分、碰撞结束和重开，清晰说明操作。",
    },
    {
        "name": "counter_tool_cn",
        "title": "Yield Counter Tool",
        "description": "作品类型：工具。制作交互计数器，初值0，增加、减少、重置按钮，允许负数，显示当前值。不要游戏玩法。",
    },
    {
        "name": "temp_converter_cn",
        "title": "Yield Temp Converter",
        "description": "作品类型：工具。制作摄氏和华氏双向温度转换器，输入值和单位后点击转换，正确处理负数、小数及无效输入。",
    },
    {
        "name": "ohm_law_science_cn",
        "title": "Yield Ohm Law Demo",
        "description": "作品类型：科学演示。制作欧姆定律交互演示，电压V默认12伏、电阻R默认6欧，两者可调整，电流按I=V/R实时计算。",
    },
    {
        "name": "pendulum_science_cn",
        "title": "Yield Pendulum Demo",
        "description": "作品类型：科学演示。制作小角度理想单摆演示，可调摆长L和重力g，周期T=2π√(L/g)，有开始暂停重置。",
    },
    {
        "name": "bubble_shooter_cn",
        "title": "Yield Bubble Shooter",
        "description": "做一个竖屏泡泡射击小游戏。点击开始后，玩家瞄准并发射彩色泡泡，三个同色相连消除，清空顶部泡泡获胜。需要分数、剩余步数和重新开始。",
    },
    {
        "name": "whack_mole_cn",
        "title": "Yield Whack Mole",
        "description": "做一个打地鼠小游戏。30秒内点击冒出的地鼠得分，错过不扣分，显示倒计时和最终成绩，有开始和重新开始按钮。",
    },
    {
        "name": "color_match_cn",
        "title": "Yield Color Match",
        "description": "做一个颜色记忆小游戏。展示颜色序列后，玩家按顺序点击色块复现，每轮增加一个颜色，错误则结束并显示最高关卡。",
    },
]

ALL_CASES = DEFAULT_CASES + EXTRA_CASES


def pick_case(run_index: int) -> dict[str, str]:
    return ALL_CASES[(run_index - 1) % len(ALL_CASES)]


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
    return {
        "run_id": run_id,
        "run_index": run_index,
        "environment": "production",
        "kind": "game",
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
    create_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/generate",
        payload={
            "title": f"{case['title']} #{run_index}",
            "description": case["description"],
            "regionHint": "cn_shanghai",
            "timeoutS": timeout_s,
        },
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
        "pollHistory": terminal.get("history") or [],
        "timedOutLocally": bool(terminal.get("timeout")),
        "elapsedS": elapsed_s,
        "completedAt": now_iso(),
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
    summary = {
        "generatedAt": now_iso(),
        "baseUrl": base_url,
        "targetCount": count,
        "completed": completed,
        "succeeded": succeeded,
        "failed": completed - succeeded,
        "successRate": round(succeeded / completed, 4) if completed else None,
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
            result = {
                "runIndex": run_index,
                "name": case["name"],
                "title": case["title"],
                "startedAt": now_iso(),
                "finalStatus": "script_error",
                "errorMessage": str(exc),
                "completedAt": now_iso(),
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
