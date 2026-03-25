"""Run repeated live end-to-end generation tests against production APIs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / ".env.deploy"


DEFAULT_CASES: list[dict[str, str]] = [
    {
        "name": "simple_dodge_cn",
        "title": "E2E Simple Dodge CN",
        "description": "做一个太空躲避手机小游戏。玩家点击开始后，通过左右滑动躲开陨石并收集星星，坚持 30 秒获胜。必须有分数、开始提示、失败结算和重新开始按钮。",
    },
    {
        "name": "grid_puzzle_en",
        "title": "E2E Grid Puzzle EN",
        "description": "Create a small portrait mobile grid puzzle. The player taps tiles to connect matching runes and clear the board in under 20 moves. Include a score, remaining moves, a win state, a lose state, and restart.",
    },
    {
        "name": "lane_runner_en",
        "title": "E2E Lane Runner EN",
        "description": "Create a portrait endless lane runner for mobile web. The player swipes left or right to dodge traffic cones, collect batteries, and survive long enough to trigger a clear victory screen at 45 seconds. Include a visible combo meter and restart.",
    },
    {
        "name": "topdown_action_cn",
        "title": "E2E Topdown Action CN",
        "description": "做一个竖屏俯视角动作射击小游戏。点击开始后，玩家拖动摇杆区域移动飞船，点击屏幕发射子弹，击败三波敌人后胜利。需要血量、分数、关卡提示、失败结算和重新开始。",
    },
    {
        "name": "complex_runner_cn",
        "title": "E2E Complex Runner CN",
        "description": "做一个竖屏跑酷战斗小游戏。点击开始后玩家通过左右滑动切换跑道、上滑跳跃、下滑滑铲，途中需要躲避障碍、收集能量、击败一个小 Boss，最终在 60 秒内通关。必须包含清晰的新手提示、分数、血量、阶段进度、胜利页、失败页和重新开始。",
    },
]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def http_json(
    method: str,
    url: str,
    *,
    payload: Any | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> Any:
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json; charset=utf-8")
    req = request.Request(url, data=data, method=method.upper())
    for key, value in req_headers.items():
        req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else None
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail: Any = body
        try:
            detail = json.loads(body) if body else {}
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc


def http_status(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[int, str]:
    req = request.Request(url, method="GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def ensure_user(base_url: str, admin_token: str, username: str, password: str) -> None:
    payload = {
        "username": username,
        "displayName": "Codex E2E Runner",
        "email": f"{username}@example.com",
        "password": password,
    }
    try:
        http_json(
            "POST",
            f"{base_url}/api/v1/admin/users",
            payload=payload,
            headers={"x-admin-token": admin_token},
            timeout=30,
        )
    except RuntimeError as exc:
        message = str(exc)
        if "already exists" not in message and "HTTP 409" not in message:
            raise


def login(base_url: str, username: str, password: str) -> str:
    response = http_json(
        "POST",
        f"{base_url}/api/v1/auth/login",
        payload={"account": username, "password": password},
        timeout=30,
    )
    token = (((response or {}).get("data") or {}).get("token") or "").strip()
    if not token:
        raise RuntimeError(f"Login succeeded without token: {response}")
    return token


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_task_id(
    base_url: str,
    game_id: str,
    bearer_headers: dict[str, str],
    *,
    poll_timeout_s: int = 120,
    poll_interval_s: int = 5,
) -> str | None:
    deadline = time.time() + poll_timeout_s
    latest_task_id: str | None = None
    while time.time() < deadline:
        status = http_json(
            "GET",
            f"{base_url}/api/v1/games/{game_id}/generation-status",
            headers=bearer_headers,
            timeout=30,
        )
        data = (status or {}).get("data") or {}
        latest_task_id = data.get("taskId") or latest_task_id
        if latest_task_id:
            return latest_task_id
        time.sleep(poll_interval_s)
    return latest_task_id


def wait_for_terminal_status(
    base_url: str,
    game_id: str,
    bearer_headers: dict[str, str],
    *,
    max_wait_s: int,
    poll_interval_s: int = 15,
) -> dict[str, Any]:
    deadline = time.time() + max_wait_s
    last_data: dict[str, Any] = {}
    history: list[dict[str, Any]] = []
    while time.time() < deadline:
        response = http_json(
            "GET",
            f"{base_url}/api/v1/games/{game_id}/generation-status",
            headers=bearer_headers,
            timeout=30,
        )
        data = (response or {}).get("data") or {}
        last_data = data
        history.append(
            {
                "polledAt": now_iso(),
                "status": data.get("status"),
                "taskId": data.get("taskId"),
                "stage": data.get("progressStage") or data.get("stage"),
                "percentage": data.get("progressPct") or data.get("percentage"),
                "message": data.get("progressMessage") or data.get("message"),
                "failedStage": data.get("failedStage"),
                "failureFamily": data.get("failureFamily"),
                "errorMessage": data.get("errorMessage"),
            }
        )
        if str(data.get("status") or "").lower() in {"succeeded", "failed", "canceled", "timed_out"}:
            return {"status": data, "history": history}
        time.sleep(poll_interval_s)
    return {"status": last_data, "history": history, "timeout": True}


def fetch_task_diagnostics(
    base_url: str,
    game_id: str,
    task_id: str | None,
    admin_token: str,
    bearer_headers: dict[str, str],
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    if task_id:
        try:
            diagnostics["task"] = http_json(
                "GET",
                f"{base_url}/api/v1/games/tasks/{task_id}",
                headers=bearer_headers,
                timeout=30,
            )
        except Exception as exc:  # pragma: no cover - operator path
            diagnostics["taskError"] = str(exc)
        try:
            diagnostics["events"] = http_json(
                "GET",
                f"{base_url}/api/v1/admin/tasks/{task_id}/events?limit=200",
                headers={"x-admin-token": admin_token},
                timeout=30,
            )
        except Exception as exc:  # pragma: no cover - operator path
            diagnostics["eventsError"] = str(exc)
        try:
            diagnostics["artifacts"] = http_json(
                "GET",
                f"{base_url}/api/v1/admin/tasks/{task_id}/artifacts?limit=200",
                headers={"x-admin-token": admin_token},
                timeout=30,
            )
        except Exception as exc:  # pragma: no cover - operator path
            diagnostics["artifactsError"] = str(exc)
    try:
        diagnostics["gameStatus"] = http_json(
            "GET",
            f"{base_url}/api/v1/games/{game_id}/generation-status",
            headers=bearer_headers,
            timeout=30,
        )
    except Exception as exc:  # pragma: no cover - operator path
        diagnostics["gameStatusError"] = str(exc)
    return diagnostics


def run_case(
    *,
    base_url: str,
    admin_token: str,
    bearer_headers: dict[str, str],
    case: dict[str, str],
    timeout_s: int,
    wait_s: int,
) -> dict[str, Any]:
    started_at = now_iso()
    create_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/generate",
        payload={
            "title": case["title"],
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

    if not task_id:
        task_id = resolve_task_id(base_url, game_id, bearer_headers)

    terminal = wait_for_terminal_status(base_url, game_id, bearer_headers, max_wait_s=wait_s)
    status_data = terminal.get("status") or {}
    final_status = str(status_data.get("status") or "").lower()
    task_id = status_data.get("taskId") or task_id

    result: dict[str, Any] = {
        "name": case["name"],
        "title": case["title"],
        "description": case["description"],
        "startedAt": started_at,
        "gameId": game_id,
        "taskId": task_id,
        "createResponse": create_response,
        "pollHistory": terminal.get("history") or [],
        "finalStatus": final_status or "unknown",
        "failedStage": status_data.get("failedStage"),
        "failureFamily": status_data.get("failureFamily"),
        "errorMessage": status_data.get("errorMessage"),
    }

    if terminal.get("timeout"):
        result["timedOutLocally"] = True

    if final_status == "succeeded":
        play_response = http_json(
            "GET",
            f"{base_url}/api/v1/games/{game_id}/play",
            headers=bearer_headers,
            timeout=60,
        )
        html_code = (((play_response or {}).get("data") or {}).get("htmlCode") or "")
        result["authorPlayOk"] = len(html_code) > 500
        result["authorPlayHtmlLength"] = len(html_code)

        publish_response = http_json(
            "POST",
            f"{base_url}/api/v1/games/{game_id}/publish",
            payload={},
            headers=bearer_headers,
            timeout=60,
        )
        result["publishOk"] = ((publish_response or {}).get("code") == 0)

        preview_status, preview_body = http_status(
            f"{base_url}/games/{game_id}/preview",
            timeout=60,
        )
        result["publicPreviewOk"] = preview_status == 200 and len(preview_body) > 100
        result["publicPreviewStatus"] = preview_status
        result["publicPreviewBodySnippet"] = preview_body[:300]
    else:
        result["authorPlayOk"] = False
        result["publishOk"] = False
        result["publicPreviewOk"] = False

    result["diagnostics"] = fetch_task_diagnostics(
        base_url,
        game_id,
        task_id,
        admin_token,
        bearer_headers,
    )
    result["completedAt"] = now_iso()
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--timeout-s", type=int, default=1200)
    parser.add_argument("--wait-s", type=int, default=1500)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    env = load_env(Path(args.env_file))
    base_url = (args.base_url or os.environ.get("PUBLIC_API_BASE_URL") or env.get("PUBLIC_API_BASE_URL") or "").rstrip("/")
    admin_token = args.admin_token or os.environ.get("ADMIN_TOKEN") or env.get("ADMIN_TOKEN") or ""
    if not base_url or not admin_token:
        print("PUBLIC_API_BASE_URL and ADMIN_TOKEN are required", file=sys.stderr)
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output) if args.output else REPO_ROOT / f"tmp_e2e_live_{timestamp}.json"
    total_repeats = max(1, args.repeats)
    runs: list[dict[str, Any]] = []
    flat_results: list[dict[str, Any]] = []

    for repeat_index in range(1, total_repeats + 1):
        run_username = args.username or f"e2e_v2_{timestamp}_{repeat_index}"
        run_password = args.password or f"CodexE2E!{timestamp}_{repeat_index}"
        ensure_user(base_url, admin_token, run_username, run_password)
        token = login(base_url, run_username, run_password)
        bearer_headers = {"Authorization": f"Bearer {token}"}

        run_results: list[dict[str, Any]] = []
        for index, case in enumerate(DEFAULT_CASES, start=1):
            print(
                f"[run {repeat_index}/{total_repeats}][{index}/{len(DEFAULT_CASES)}] running {case['name']} ...",
                flush=True,
            )
            try:
                result = run_case(
                    base_url=base_url,
                    admin_token=admin_token,
                    bearer_headers=bearer_headers,
                    case=case,
                    timeout_s=args.timeout_s,
                    wait_s=args.wait_s,
                )
            except Exception as exc:  # pragma: no cover - operator path
                result = {
                    "name": case["name"],
                    "title": case["title"],
                    "description": case["description"],
                    "startedAt": now_iso(),
                    "finalStatus": "script_error",
                    "errorMessage": str(exc),
                    "completedAt": now_iso(),
                }
            result["repeat"] = repeat_index
            run_results.append(result)
            flat_results.append(result)
            output_path.write_text(
                json.dumps(
                    {
                        "generatedAt": now_iso(),
                        "baseUrl": base_url,
                        "repeats": total_repeats,
                        "runsCompleted": len(runs),
                        "resultsCompleted": len(flat_results),
                        "runs": runs + [{
                            "repeat": repeat_index,
                            "username": run_username,
                            "results": run_results,
                        }],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        runs.append({
            "repeat": repeat_index,
            "username": run_username,
            "results": run_results,
            "fullFlowSuccess": sum(
                1
                for item in run_results
                if item.get("finalStatus") == "succeeded"
                and item.get("authorPlayOk")
                and item.get("publishOk")
                and item.get("publicPreviewOk")
            ),
        })

    full_flow_success = sum(
        1
        for item in flat_results
        if item.get("finalStatus") == "succeeded"
        and item.get("authorPlayOk")
        and item.get("publishOk")
        and item.get("publicPreviewOk")
    )
    summary = {
        "generatedAt": now_iso(),
        "baseUrl": base_url,
        "repeats": total_repeats,
        "casesPerRun": len(DEFAULT_CASES),
        "total": len(flat_results),
        "completed": len(flat_results),
        "full_flow_success": full_flow_success,
        "runs": runs,
        "results": flat_results,
    }
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "full_flow_success": full_flow_success}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
