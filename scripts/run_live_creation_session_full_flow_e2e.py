"""Run live full-flow E2E checks through creation sessions against production."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from run_live_full_flow_e2e import (
    DEFAULT_ENV_PATH,
    build_bearer_headers,
    fetch_author_play,
    fetch_share_data,
    fetch_task_diagnostics,
    http_json,
    login,
    now_iso,
    poll_task_terminal,
    publish_and_check_public,
)
from run_live_generation_e2e import ensure_user


REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CASES: list[dict[str, Any]] = [
    {
        "name": "office_slacker_cn",
        "title": "E2E Dialogue Office Slacker",
        "prompt": "做一个上班摸鱼的游戏，五个关卡，要抓住现代职场的梗，整体要好笑。",
        "orientation": "portrait",
        "generationTier": "standard",
        "game_type": "funny",
        "core_mechanic": "点击和短拖拽切换摸鱼动作，同时躲避老板巡查。",
        "theme": "办公室职场喜剧",
        "input_method": "tap",
        "win_condition": "在限时内积累摸鱼值并不被抓包。",
        "difficulty": "medium",
        "session_length": "short",
        "progression_shape": "staged",
        "reward_loop": "通关后解锁新的摸鱼手段和夸张演出。",
        "signature_moment": "老板突然靠近时，玩家要一键切回工作界面。",
        "target_audience": "上班族和大学生",
        "tone": "playful",
        "reference_style": "短视频梗图式夸张喜剧",
        "complexity_budget": "standard",
        "comedy_device": "反差和职场梗",
        "feedback": "把后两关做得更离谱一点，增加午休、开会和汇报三个突发事件，并把被抓包后的演出做得更夸张。",
    },
    {
        "name": "circuit_classroom_cn",
        "title": "E2E Dialogue Circuit Classroom",
        "prompt": "做一个电路教学小游戏，让学生拖拽元件点亮灯泡，想要有闯关感。",
        "orientation": "portrait",
        "generationTier": "showcase",
        "game_type": "educational",
        "core_mechanic": "拖拽电池、导线和开关拼出正确电路。",
        "theme": "实验教室和电子工作台",
        "input_method": "drag",
        "win_condition": "在限定步数内点亮目标灯泡并完成教学目标。",
        "difficulty": "medium",
        "session_length": "medium",
        "progression_shape": "staged",
        "reward_loop": "每关学会一种新电路知识并解锁新的器件。",
        "signature_moment": "拼对电路时，电流会沿着导线流动并触发讲解。",
        "target_audience": "中学生",
        "tone": "curious",
        "reference_style": "明亮的教育可视化界面",
        "complexity_budget": "showcase",
        "teaching_mode": "guided",
        "feedback": "增加一个并联关卡和一个开关控制关卡，并让通关讲解更简洁但更直观。",
    },
    {
        "name": "parkour_delivery_en",
        "title": "E2E Dialogue Parkour Delivery",
        "prompt": "Make a landscape delivery game where the hero dashes across rooftops, avoids drones, and drops parcels on target balconies.",
        "orientation": "landscape",
        "generationTier": "showcase",
        "game_type": "casual",
        "core_mechanic": "Swipe to dash, jump gaps, and time parcel drops.",
        "theme": "sunset city rooftops",
        "input_method": "swipe",
        "win_condition": "Deliver enough parcels before time runs out without crashing.",
        "difficulty": "medium",
        "session_length": "medium",
        "progression_shape": "wave",
        "reward_loop": "Successful deliveries build combo streaks and unlock faster routes.",
        "signature_moment": "A slow-motion near-miss jump between rooftops while a parcel lands perfectly.",
        "target_audience": "teens",
        "tone": "energetic",
        "reference_style": "stylized animated city action",
        "complexity_budget": "showcase",
        "feedback": "Add a dramatic storm round, make parcel drop feedback clearer, and add a combo banner when two perfect deliveries happen back to back.",
    },
    {
        "name": "fruit_merge_relax_en",
        "title": "E2E Dialogue Fruit Merge Relax",
        "prompt": "Create a cozy fruit merge puzzle for mobile that feels polished, colorful, and easy to understand for first-time players.",
        "orientation": "portrait",
        "generationTier": "standard",
        "game_type": "puzzle",
        "core_mechanic": "Tap and drag fruit pieces to merge matching items into larger combos.",
        "theme": "soft fruit market",
        "input_method": "drag",
        "win_condition": "Reach target scores within a limited number of moves.",
        "difficulty": "easy",
        "session_length": "short",
        "progression_shape": "staged",
        "reward_loop": "Bigger merges create chain bonuses and unlock prettier fruit tiers.",
        "signature_moment": "A cascading merge chain fills the basket with sparkle effects.",
        "target_audience": "casual players",
        "tone": "cozy",
        "reference_style": "soft 3D toy-like casual puzzle",
        "complexity_budget": "standard",
        "feedback": "Add a clearer tutorial hint for the first move, make the combo celebration bigger, and add a limited booster that appears in stage two.",
    },
    {
        "name": "history_quiz_show_cn",
        "title": "E2E Dialogue History Quiz Show",
        "prompt": "做一个历史知识闯关小游戏，像节目答题秀一样有节奏感，但不要太严肃。",
        "orientation": "landscape",
        "generationTier": "showcase",
        "game_type": "educational",
        "core_mechanic": "点击选择正确答案，配合限时和连击奖励。",
        "theme": "历史答题舞台秀",
        "input_method": "tap",
        "win_condition": "连续答对并累计足够分数通过多轮题目。",
        "difficulty": "medium",
        "session_length": "medium",
        "progression_shape": "wave",
        "reward_loop": "答对会叠加连击、解锁新朝代题库和演出效果。",
        "signature_moment": "连续三题全对时舞台灯光和主持人台词一起爆发。",
        "target_audience": "初高中学生",
        "tone": "playful",
        "reference_style": "综艺节目答题舞台",
        "complexity_budget": "showcase",
        "teaching_mode": "quiz_show",
        "feedback": "把每题后的讲解再压缩一点，但增加一个终局总结面板，并让连击效果更有节目感。",
    },
]


SLOT_KEY_ALIASES = {
    "game_type": "game_type",
    "core_mechanic": "core_mechanic",
    "theme": "theme",
    "input_method": "input_method",
    "win_condition": "win_condition",
    "difficulty": "difficulty",
    "session_length": "session_length",
    "progression_shape": "progression_shape",
    "reward_loop": "reward_loop",
    "signature_moment": "signature_moment",
    "target_audience": "target_audience",
    "tone": "tone",
    "reference_style": "reference_style",
    "complexity_budget": "complexity_budget",
    "teaching_mode": "teaching_mode",
    "comedy_device": "comedy_device",
}


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


def get_session(
    base_url: str,
    session_id: str,
    bearer_headers: dict[str, str],
) -> dict[str, Any]:
    response = http_json(
        "GET",
        f"{base_url}/api/v1/games/creation-sessions/{session_id}",
        headers=bearer_headers,
        timeout=60,
    )
    return (response or {}).get("data") or {}


def get_active_session(base_url: str, bearer_headers: dict[str, str]) -> dict[str, Any]:
    response = http_json(
        "GET",
        f"{base_url}/api/v1/games/creation-sessions/active",
        headers=bearer_headers,
        timeout=60,
    )
    return (response or {}).get("data") or {}


def wait_for_session_interactive(
    base_url: str,
    session_id: str,
    bearer_headers: dict[str, str],
    *,
    wait_s: int = 60,
    poll_interval_s: float = 1.0,
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + wait_s
    history: list[dict[str, Any]] = []
    latest_snapshot: dict[str, Any] = {}

    while time.perf_counter() < deadline:
        latest_snapshot = get_session(base_url, session_id, bearer_headers)
        current_question = latest_snapshot.get("currentQuestion")
        elapsed_s = round(time.perf_counter() - started, 3)
        history.append(
            {
                "elapsedS": elapsed_s,
                "status": latest_snapshot.get("status"),
                "revision": latest_snapshot.get("revision"),
                "readyToGenerate": bool(latest_snapshot.get("readyToGenerate")),
                "slotFillPct": latest_snapshot.get("slotFillPct"),
                "initError": latest_snapshot.get("initError"),
                "currentQuestion": current_question.get("slotKey") if isinstance(current_question, dict) else None,
            }
        )

        if latest_snapshot.get("readyToGenerate") or current_question:
            return {
                "snapshot": latest_snapshot,
                "polls": history,
                "timedOutLocally": False,
                "interactiveElapsedS": elapsed_s,
            }

        if str(latest_snapshot.get("status") or "").lower() in {"abandoned", "completed"}:
            return {
                "snapshot": latest_snapshot,
                "polls": history,
                "timedOutLocally": False,
                "interactiveElapsedS": None,
            }

        time.sleep(poll_interval_s)

    return {
        "snapshot": latest_snapshot,
        "polls": history,
        "timedOutLocally": True,
        "interactiveElapsedS": None,
    }


def answer_for_slot(case: dict[str, Any], slot_key: str | None, prompt: str | None = None) -> str | None:
    if not slot_key:
        return None
    normalized_key = SLOT_KEY_ALIASES.get(slot_key, slot_key)
    if normalized_key in case:
        value = case.get(normalized_key)
        if value:
            return str(value)

    lowered_prompt = str(prompt or "").lower()
    if "操作" in lowered_prompt or "input" in lowered_prompt:
        return str(case.get("input_method") or "tap")
    if "主题" in lowered_prompt or "theme" in lowered_prompt:
        return str(case.get("theme") or "明亮街机风")
    if "胜利" in lowered_prompt or "目标" in lowered_prompt or "win" in lowered_prompt:
        return str(case.get("win_condition") or "完成目标并成功通关")
    if "难度" in lowered_prompt or "difficulty" in lowered_prompt:
        return str(case.get("difficulty") or "medium")
    return None


def collect_creation_session(
    *,
    base_url: str,
    bearer_headers: dict[str, str],
    case: dict[str, Any],
    max_turns: int = 3,
) -> dict[str, Any]:
    create_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/creation-sessions",
        payload={
            "prompt": case["prompt"],
            "title": case["title"],
            "regionHint": "cn_shanghai",
            "orientation": case.get("orientation") or "portrait",
            "generationTier": case.get("generationTier") or "standard",
            "entryMode": "create",
        },
        headers=bearer_headers,
        timeout=60,
    )
    snapshot = (create_response or {}).get("data") or {}
    session_id = snapshot.get("id")
    if not session_id:
        raise RuntimeError(f"Creation session did not return id: {create_response}")

    active_snapshot = get_active_session(base_url, bearer_headers)
    interactive_wait = wait_for_session_interactive(
        base_url,
        session_id,
        bearer_headers,
    )
    turns: list[dict[str, Any]] = []
    latest_snapshot = interactive_wait.get("snapshot") or snapshot

    if interactive_wait.get("timedOutLocally"):
        raise RuntimeError(
            f"Creation session did not become interactive within timeout: {interactive_wait}"
        )

    for turn_index in range(max_turns):
        current_question = latest_snapshot.get("currentQuestion") or {}
        if latest_snapshot.get("readyToGenerate") and not current_question:
            break
        slot_key = current_question.get("slotKey")
        prompt = current_question.get("prompt")
        if slot_key == "expanded_prompt":
            revision = latest_snapshot.get("revision")
            edited_prompt = case.get("edited_prompt")
            if edited_prompt:
                message_response = http_json(
                    "POST",
                    f"{base_url}/api/v1/games/creation-sessions/{session_id}/messages",
                    payload={"content": str(edited_prompt), "revision": revision},
                    headers=bearer_headers,
                    timeout=60,
                )
                latest_snapshot = (message_response or {}).get("data") or {}
                turns.append(
                    {
                        "turn": turn_index + 1,
                        "action": "confirm_edit",
                        "slotKey": slot_key,
                        "prompt": prompt,
                        "answer": str(edited_prompt),
                        "revision": revision,
                    }
                )
                continue

            skip_response = http_json(
                "POST",
                f"{base_url}/api/v1/games/creation-sessions/{session_id}/skip",
                payload={"revision": revision},
                headers=bearer_headers,
                timeout=60,
            )
            latest_snapshot = (skip_response or {}).get("data") or {}
            turns.append(
                {
                    "turn": turn_index + 1,
                    "action": "confirm_generated_prompt",
                    "slotKey": slot_key,
                    "prompt": prompt,
                    "revision": revision,
                }
            )
            continue

        answer = answer_for_slot(case, slot_key, prompt)
        revision = latest_snapshot.get("revision")
        if answer:
            message_response = http_json(
                "POST",
                f"{base_url}/api/v1/games/creation-sessions/{session_id}/messages",
                payload={"content": answer, "revision": revision},
                headers=bearer_headers,
                timeout=60,
            )
            latest_snapshot = (message_response or {}).get("data") or {}
            turns.append(
                {
                    "turn": turn_index + 1,
                    "action": "answer",
                    "slotKey": slot_key,
                    "prompt": prompt,
                    "answer": answer,
                    "revision": revision,
                }
            )
            continue
        if current_question.get("skippable"):
            skip_response = http_json(
                "POST",
                f"{base_url}/api/v1/games/creation-sessions/{session_id}/skip",
                payload={"revision": revision},
                headers=bearer_headers,
                timeout=60,
            )
            latest_snapshot = (skip_response or {}).get("data") or {}
            turns.append(
                {
                    "turn": turn_index + 1,
                    "action": "skip",
                    "slotKey": slot_key,
                    "prompt": prompt,
                    "revision": revision,
                }
            )
            continue
        break

    latest_snapshot = get_session(base_url, session_id, bearer_headers)
    return {
        "sessionId": session_id,
        "createResponse": create_response,
        "activeSnapshot": active_snapshot,
        "interactiveWait": interactive_wait,
        "turns": turns,
        "snapshot": latest_snapshot,
    }


def run_full_flow_case(
    *,
    base_url: str,
    admin_token: str,
    case: dict[str, Any],
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

    session_result = collect_creation_session(
        base_url=base_url,
        bearer_headers=author_headers,
        case=case,
    )
    session_snapshot = session_result["snapshot"] or {}
    session_id = session_result["sessionId"]
    result["create"] = {
        "creationSession": session_result,
        "sessionId": session_id,
        "readyToGenerate": bool(session_snapshot.get("readyToGenerate")),
        "slotFillPct": session_snapshot.get("slotFillPct"),
        "planDraft": session_snapshot.get("planDraft"),
        "questionStrategy": session_snapshot.get("questionStrategy"),
    }

    generate_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/creation-sessions/{session_id}/generate",
        payload={
            "revision": session_snapshot.get("revision"),
            "timeoutS": timeout_s,
        },
        headers=author_headers,
        timeout=60,
    )
    generate_data = (generate_response or {}).get("data") or {}
    game_id = generate_data.get("gameId")
    task_id = generate_data.get("taskId")
    if not game_id:
        raise RuntimeError(f"Generate-from-session did not return gameId: {generate_response}")

    result["create"].update(
        {
            "generateResponse": generate_response,
            "gameId": game_id,
            "creationSessionAfterGenerate": generate_data.get("creationSession"),
        }
    )

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
    result["create"]["share"] = fetch_share_data(base_url, game_id)
    try:
        from run_live_full_flow_e2e import run_subscription_flow

        result["subscription"] = run_subscription_flow(
            base_url,
            author_headers,
            game_id_to_unlock=game_id,
        )
    except Exception as exc:
        result["subscription"] = {
            "ok": False,
            "optionalFailure": str(exc),
        }

    iterate_response = http_json(
        "POST",
        f"{base_url}/api/v1/games/{game_id}/iterate",
        payload={
            "feedback": case["feedback"],
            "regionHint": "cn_shanghai",
            "timeoutS": timeout_s,
            "generationTier": case.get("generationTier") or "standard",
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
    result["fork"]["share"] = fetch_share_data(base_url, fork_game_id)
    forks_response = http_json("GET", f"{base_url}/api/v1/games/{game_id}/forks?page=1&limit=50", timeout=60)
    tree_response = http_json("GET", f"{base_url}/api/v1/games/{game_id}/fork-tree", timeout=60)
    lineage_response = http_json("GET", f"{base_url}/api/v1/games/{fork_game_id}/fork-lineage", timeout=60)
    forks_items = ((((forks_response or {}).get("data") or {}).get("items")) or [])
    tree_children = ((((tree_response or {}).get("data") or {}).get("children")) or [])
    lineage_items = ((((lineage_response or {}).get("data") or {}).get("lineage")) or [])
    result["fork"]["relationships"] = {
        "containsForkId": any(item.get("id") == fork_game_id for item in forks_items),
        "containsForkIdInTree": any(item.get("id") == fork_game_id for item in tree_children),
        "containsSourceId": any(item.get("id") == game_id for item in lineage_items),
        "forks": forks_response,
        "tree": tree_response,
        "lineage": lineage_response,
    }

    result["ok"] = (
        result["create"]["finalStatus"] == "succeeded"
        and result["create"]["authorPlay"]["ok"]
        and result["create"]["publicAfterPublish"]["previewOk"]
        and result["create"]["share"]["ok"]
        and result["iterate"]["finalStatus"] == "succeeded"
        and result["iterate"]["authorPlay"]["ok"]
        and result["iterate"]["publicAfterPublish"]["previewOk"]
        and result["fork"]["authorPlay"]["ok"]
        and result["fork"]["publicAfterPublish"]["previewOk"]
        and result["fork"]["share"]["ok"]
        and result["fork"]["relationships"]["containsForkId"]
        and result["fork"]["relationships"]["containsForkIdInTree"]
        and result["fork"]["relationships"]["containsSourceId"]
    )
    result["completedAt"] = now_iso()
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--timeout-s", type=int, default=1500)
    parser.add_argument("--wait-s", type=int, default=1800)
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
    output_path = Path(args.output) if args.output else REPO_ROOT / f"tmp_creation_session_e2e_batch_{timestamp}.json"
    results: list[dict[str, Any]] = []

    for index, case in enumerate(DEFAULT_CASES, start=1):
        author_username = f"dca_{short_stamp}_{index}"
        forker_username = f"dcf_{short_stamp}_{index}"
        password = f"CodexDcd!{timestamp}_{index}"
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
