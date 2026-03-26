"""Run live full-flow E2E checks against production create -> iterate -> fork journeys."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from run_live_generation_e2e import (
    DEFAULT_ENV_PATH,
    ensure_user,
    fetch_task_diagnostics,
    http_json,
    http_status,
    load_env,
    login,
    now_iso,
    resolve_task_id,
    wait_for_terminal_status,
)


REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CASES: list[dict[str, str]] = [
    {
        "name": "simple_dodge_full_flow_cn",
        "title": "E2E Full Flow Simple Dodge CN",
        "description": "做一个竖屏手机躲避小游戏。玩家左右滑动躲开障碍并收集星星，坚持 30 秒胜利。必须有开始界面、分数、生命、胜利界面、失败界面和重新开始按钮。",
        "feedback": "增加第二阶段，障碍速度更快一点，并把分数显示做得更醒目。",
    },
    {
        "name": "tap_collector_full_flow_en",
        "title": "E2E Full Flow Tap Collector EN",
        "description": "Create a portrait one-button mobile game. The player taps to move a basket left and right, catches falling stars, avoids falling bombs, and reaches 20 points to win. Include score, lives, a start screen, a win screen, a lose screen, and restart.",
        "feedback": "Add a second round with slightly faster falling speed, make the lives display larger, and show a short hint before round two starts.",
    },
    {
        "name": "grid_puzzle_full_flow_en",
        "title": "E2E Full Flow Grid Puzzle EN",
        "description": "Create a small portrait mobile grid puzzle. The player taps tiles to connect matching runes and clear the board in under 20 moves. Include a score, remaining moves, a win state, a lose state, and restart.",
        "feedback": "Make the moves counter larger, add a short hint banner for first-time players, and slow down the tile clear animation a little.",
    },
    {
        "name": "lane_runner_full_flow_en",
        "title": "E2E Full Flow Lane Runner EN",
        "description": "Create a portrait endless lane runner for mobile web. The player swipes left or right to dodge traffic cones, collect batteries, and survive long enough to trigger a clear victory screen at 45 seconds. Include a visible combo meter and restart.",
        "feedback": "Add a milestone banner at 20 seconds, make lane changes feel slightly snappier, and add a clearer restart prompt on game over.",
    },
    {
        "name": "circuit_drag_full_flow_cn",
        "title": "E2E Full Flow Circuit Drag CN",
        "description": "玩家通过拖拽电池、导线、开关、灯泡等元件，组成正确电路并点亮灯泡。游戏包含闭合电路、串联、并联、短路、开关控制等基础电路知识关卡。操作方式为点击和拖拽，加入关卡、提示、重置、得分和成功动画，整体简单易上手，适合学生学习基础物理电路知识。",
        "feedback": "把提示按钮做得更明显，增加第二关并加入开关控制灯泡的目标，还要在通关后展示简单讲解。",
    },
]


def build_bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def poll_task_terminal(
    base_url: str,
    game_id: str,
    task_id: str | None,
    bearer_headers: dict[str, str],
    *,
    wait_s: int,
) -> tuple[str | None, dict[str, Any]]:
    if not task_id:
        task_id = resolve_task_id(base_url, game_id, bearer_headers)
    terminal = wait_for_terminal_status(
        base_url,
        game_id,
        bearer_headers,
        max_wait_s=wait_s,
    )
    status_data = terminal.get("status") or {}
    task_id = status_data.get("taskId") or task_id
    return task_id, {
        "taskId": task_id,
        "status": status_data,
        "pollHistory": terminal.get("history") or [],
        "timedOutLocally": bool(terminal.get("timeout")),
    }


def fetch_author_play(base_url: str, game_id: str, bearer_headers: dict[str, str]) -> dict[str, Any]:
    play_response = http_json(
        "GET",
        f"{base_url}/api/v1/games/{game_id}/play",
        headers=bearer_headers,
        timeout=60,
    )
    html_code = (((play_response or {}).get("data") or {}).get("htmlCode") or "")
    return {
        "ok": len(html_code) > 500,
        "htmlLength": len(html_code),
    }


def publish_and_check_public(base_url: str, game_id: str, bearer_headers: dict[str, str]) -> dict[str, Any]:
    publish_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/{game_id}/publish",
        payload={},
        headers=bearer_headers,
        timeout=60,
    )
    preview_status, preview_body = http_status(f"{base_url}/games/{game_id}/preview", timeout=60)
    index_status, index_body = http_status(f"{base_url}/games/{game_id}/index.html", timeout=60)
    return {
        "publish": publish_response,
        "publicAfterPublish": {
            "previewStatus": preview_status,
            "previewOk": preview_status == 200 and len(preview_body) > 100,
            "indexStatus": index_status,
            "indexOk": index_status == 200 and len(index_body) > 100,
            "previewBodySnippet": preview_body[:240],
            "indexBodySnippet": index_body[:240],
        },
    }


def run_full_flow_case(
    *,
    base_url: str,
    admin_token: str,
    case: dict[str, str],
    author_credentials: dict[str, str],
    forker_credentials: dict[str, str],
    timeout_s: int,
    wait_s: int,
) -> dict[str, Any]:
    started_at = now_iso()
    author_token = login(base_url, author_credentials["username"], author_credentials["password"])
    author_headers = build_bearer_headers(author_token)
    forker_token = login(base_url, forker_credentials["username"], forker_credentials["password"])
    forker_headers = build_bearer_headers(forker_token)
    result: dict[str, Any] = {
        "startedAt": started_at,
        "case": case,
        "baseUrl": base_url,
        "author": {"username": author_credentials["username"]},
        "forker": {"username": forker_credentials["username"]},
        "ok": False,
    }

    create_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/generate",
        payload={
            "title": case["title"],
            "description": case["description"],
            "regionHint": "cn_shanghai",
            "timeoutS": timeout_s,
        },
        headers=author_headers,
        timeout=60,
    )
    create_data = (create_response or {}).get("data") or {}
    game_id = create_data.get("gameId")
    task_id = create_data.get("taskId")
    if not game_id:
        raise RuntimeError(f"Generate did not return gameId: {create_response}")

    result["create"] = {
        "createResponse": create_response,
        "gameId": game_id,
    }

    create_task_id, create_terminal = poll_task_terminal(
        base_url,
        game_id,
        task_id,
        author_headers,
        wait_s=wait_s,
    )
    create_status = create_terminal["status"]
    result["create"].update(create_terminal)
    result["create"]["finalStatus"] = str(create_status.get("status") or "").lower()
    result["create"]["failedStage"] = create_status.get("failedStage")
    result["create"]["failureFamily"] = create_status.get("failureFamily")
    result["create"]["errorMessage"] = create_status.get("errorMessage")
    result["create"]["diagnostics"] = fetch_task_diagnostics(
        base_url,
        game_id,
        create_task_id,
        admin_token,
        author_headers,
    )
    if result["create"]["finalStatus"] != "succeeded":
        result["completedAt"] = now_iso()
        return result

    result["create"]["authorPlay"] = fetch_author_play(base_url, game_id, author_headers)
    result["create"].update(publish_and_check_public(base_url, game_id, author_headers))

    iterate_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/{game_id}/iterate",
        payload={
            "feedback": case["feedback"],
            "regionHint": "cn_shanghai",
            "timeoutS": timeout_s,
        },
        headers=author_headers,
        timeout=60,
    )
    iterate_data = (iterate_response or {}).get("data") or {}
    iterate_task_id = iterate_data.get("taskId")
    result["iterate"] = {
        "iterateResponse": iterate_response,
        "gameId": game_id,
    }
    iterate_task_id, iterate_terminal = poll_task_terminal(
        base_url,
        game_id,
        iterate_task_id,
        author_headers,
        wait_s=wait_s,
    )
    iterate_status = iterate_terminal["status"]
    result["iterate"].update(iterate_terminal)
    result["iterate"]["finalStatus"] = str(iterate_status.get("status") or "").lower()
    result["iterate"]["failedStage"] = iterate_status.get("failedStage")
    result["iterate"]["failureFamily"] = iterate_status.get("failureFamily")
    result["iterate"]["errorMessage"] = iterate_status.get("errorMessage")
    result["iterate"]["diagnostics"] = fetch_task_diagnostics(
        base_url,
        game_id,
        iterate_task_id,
        admin_token,
        author_headers,
    )
    if result["iterate"]["finalStatus"] != "succeeded":
        result["completedAt"] = now_iso()
        return result

    result["iterate"]["authorPlay"] = fetch_author_play(base_url, game_id, author_headers)
    result["iterate"].update(publish_and_check_public(base_url, game_id, author_headers))

    fork_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/{game_id}/fork",
        payload={},
        headers=forker_headers,
        timeout=60,
    )
    fork_data = (fork_response or {}).get("data") or {}
    fork_game_id = fork_data.get("gameId")
    if not fork_game_id:
        raise RuntimeError(f"Fork did not return gameId: {fork_response}")

    result["fork"] = {
        "forkResponse": fork_response,
        "gameId": fork_game_id,
        "authorPlay": fetch_author_play(base_url, fork_game_id, forker_headers),
    }
    result["fork"].update(publish_and_check_public(base_url, fork_game_id, forker_headers))
    forks_response = http_json("GET", f"{base_url}/api/v1/games/{game_id}/forks?page=1&limit=50", timeout=60)
    tree_response = http_json("GET", f"{base_url}/api/v1/games/{game_id}/fork-tree", timeout=60)
    lineage_response = http_json("GET", f"{base_url}/api/v1/games/{fork_game_id}/fork-lineage", timeout=60)
    forks_items = ((((forks_response or {}).get("data") or {}).get("items")) or [])
    tree_children = ((((tree_response or {}).get("data") or {}).get("children")) or [])
    lineage_items = ((((lineage_response or {}).get("data") or {}).get("lineage")) or [])
    result["fork"]["relationships"] = {
        "containsForkId": any(item.get("id") == fork_game_id for item in forks_items),
        "containsSourceId": any(item.get("id") == game_id for item in lineage_items),
        "forks": forks_response,
        "tree": tree_response,
        "lineage": lineage_response,
    }

    result["ok"] = (
        result["create"]["finalStatus"] == "succeeded"
        and result["create"]["authorPlay"]["ok"]
        and result["create"]["publicAfterPublish"]["previewOk"]
        and result["iterate"]["finalStatus"] == "succeeded"
        and result["iterate"]["authorPlay"]["ok"]
        and result["iterate"]["publicAfterPublish"]["previewOk"]
        and result["fork"]["authorPlay"]["ok"]
        and result["fork"]["publicAfterPublish"]["previewOk"]
        and result["fork"]["relationships"]["containsForkId"]
        and result["fork"]["relationships"]["containsSourceId"]
    )
    result["completedAt"] = now_iso()
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--timeout-s", type=int, default=1200)
    parser.add_argument("--wait-s", type=int, default=1500)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    env = load_env(Path(args.env_file))
    base_url = (args.base_url or env.get("PUBLIC_API_BASE_URL") or "").rstrip("/")
    admin_token = args.admin_token or env.get("ADMIN_TOKEN") or ""
    if not base_url or not admin_token:
        print("PUBLIC_API_BASE_URL and ADMIN_TOKEN are required", file=sys.stderr)
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_stamp = datetime.now().strftime("%m%d%H%M%S")
    output_path = Path(args.output) if args.output else REPO_ROOT / f"tmp_e2e_full_flow_batch_{timestamp}.json"
    results: list[dict[str, Any]] = []

    for index, case in enumerate(DEFAULT_CASES, start=1):
        author_username = f"ffa_{short_stamp}_{index}"
        forker_username = f"fff_{short_stamp}_{index}"
        password = f"CodexFull!{timestamp}_{index}"
        ensure_user(base_url, admin_token, author_username, password)
        ensure_user(base_url, admin_token, forker_username, password)
        print(f"[{index}/{len(DEFAULT_CASES)}] running {case['name']} ...", flush=True)
        try:
            result = run_full_flow_case(
                base_url=base_url,
                admin_token=admin_token,
                case={
                    **case,
                    "title": f"{case['title']} {timestamp}_{index}",
                },
                author_credentials={"username": author_username, "password": password},
                forker_credentials={"username": forker_username, "password": password},
                timeout_s=args.timeout_s,
                wait_s=args.wait_s,
            )
        except Exception as exc:
            result = {
                "startedAt": now_iso(),
                "case": case,
                "baseUrl": base_url,
                "ok": False,
                "finalStatus": "script_error",
                "errorMessage": str(exc),
                "completedAt": now_iso(),
            }
        results.append(result)
        output_path.write_text(
            json.dumps(
                {
                    "generatedAt": now_iso(),
                    "baseUrl": base_url,
                    "cases": len(DEFAULT_CASES),
                    "completed": len(results),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    summary = {
        "generatedAt": now_iso(),
        "baseUrl": base_url,
        "cases": len(DEFAULT_CASES),
        "completed": len(results),
        "okCount": sum(1 for item in results if item.get("ok")),
        "results": results,
    }
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "okCount": summary["okCount"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
