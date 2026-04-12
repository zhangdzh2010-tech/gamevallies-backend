"""Run concurrent live GameVallies load tests with a configurable user count.

Examples:
  python scripts/run_live_generation_concurrency.py --users 10
  python scripts/run_live_generation_concurrency.py --users 20 --flow session_generate
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import parse, request
from uuid import uuid4

from run_live_creation_session_full_flow_e2e import (
    DEFAULT_CASES as DEFAULT_SESSION_CASES,
    answer_for_slot,
    get_session,
)
from run_live_full_flow_e2e import build_bearer_headers, poll_task_terminal, run_subscription_flow
from run_live_generation_e2e import (
    DEFAULT_CASES as DEFAULT_GENERATE_CASES,
    DEFAULT_ENV_PATH,
    ensure_user,
    fetch_task_diagnostics,
    http_json,
    load_env,
    login,
    now_iso,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_USER_PATTERN = re.compile(
    r"(?:^|[_-])(e2e|load|codex|lat|test|qa|perf|bench|smoke)(?:[_-]|$)",
    re.IGNORECASE,
)
TEST_USER_SEARCH_TERMS = [
    "load_",
    "e2e_",
    "codex",
    "lat_",
    "smoke",
    "perf",
    "test",
]


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * ratio)))
    return ordered[index]


def summarize_numbers(values: list[float], *, digits: int = 3) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "min": round(min(values), digits),
        "avg": round(sum(values) / len(values), digits),
        "p50": round(percentile(values, 0.50) or 0.0, digits),
        "p95": round(percentile(values, 0.95) or 0.0, digits),
        "max": round(max(values), digits),
    }


def choose_cases(flow: str) -> list[dict[str, Any]]:
    if flow == "session_generate":
        return list(DEFAULT_SESSION_CASES)
    return list(DEFAULT_GENERATE_CASES)


def list_admin_users(
    base_url: str,
    admin_token: str,
    *,
    page: int = 1,
    limit: int = 100,
    search: str = "",
) -> list[dict[str, Any]]:
    response = http_json(
        "GET",
        f"{base_url}/api/v1/admin/users?page={page}&limit={limit}&search={search}",
        headers={"x-admin-token": admin_token},
        timeout=60,
    )
    return (((response or {}).get("data") or {}).get("items")) or []


def admin_reset_password(base_url: str, admin_token: str, user_id: str, password: str) -> None:
    http_json(
        "POST",
        f"{base_url}/api/v1/admin/user-password/{user_id}",
        payload={"password": password},
        headers={"x-admin-token": admin_token},
        timeout=60,
    )


def looks_like_test_user(user: dict[str, Any]) -> bool:
    haystacks = [
        str(user.get("username") or ""),
        str(user.get("displayName") or ""),
        str(user.get("email") or ""),
    ]
    return any(TEST_USER_PATTERN.search(value) for value in haystacks if value)


def collect_existing_test_users(
    base_url: str,
    admin_token: str,
    *,
    desired_count: int,
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}

    for search in TEST_USER_SEARCH_TERMS:
        try:
            for item in list_admin_users(base_url, admin_token, page=1, limit=100, search=search):
                if not looks_like_test_user(item):
                    continue
                found[item["id"]] = item
            if len(found) >= desired_count:
                break
        except Exception:
            continue

    if len(found) < desired_count:
        for page in range(1, 6):
            try:
                items = list_admin_users(base_url, admin_token, page=page, limit=100)
            except Exception:
                break
            if not items:
                break
            for item in items:
                if looks_like_test_user(item):
                    found[item["id"]] = item
            if len(found) >= desired_count:
                break

    users = list(found.values())
    users.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
    return users[:desired_count]


def get_subscription_status(base_url: str, bearer_headers: dict[str, str]) -> dict[str, Any]:
    response = http_json(
        "GET",
        f"{base_url}/api/v1/subscription/status",
        headers=bearer_headers,
        timeout=60,
    )
    return (response or {}).get("data") or {}


def get_user_quota_status(base_url: str, bearer_headers: dict[str, str]) -> dict[str, Any]:
    response = http_json(
        "GET",
        f"{base_url}/api/v1/users/quota",
        headers=bearer_headers,
        timeout=60,
    )
    return (response or {}).get("data") or {}


def subscription_quota_remaining(status: dict[str, Any]) -> int:
    quota_this_period = int(status.get("quotaThisPeriod") or 0)
    used_this_period = int(status.get("usedThisPeriod") or 0)
    return max(quota_this_period - used_this_period, 0)


def ensure_user_allowance(
    base_url: str,
    bearer_headers: dict[str, str],
    *,
    min_remaining_quota: int,
) -> dict[str, Any]:
    before = get_subscription_status(base_url, bearer_headers)
    quota_before = get_user_quota_status(base_url, bearer_headers)
    free_remaining = int(quota_before.get("freeQuota") or 0)
    subscription_snapshot = quota_before.get("subscription") or {}

    if free_remaining >= min_remaining_quota:
        return {
            "ok": True,
            "mode": "existing_free_quota",
            "before": before,
            "quotaBefore": quota_before,
            "after": before,
            "quotaAfter": quota_before,
            "remainingQuota": free_remaining,
        }

    if bool(before.get("active")) and subscription_quota_remaining(before) >= min_remaining_quota:
        return {
            "ok": True,
            "mode": "existing_subscription",
            "before": before,
            "quotaBefore": quota_before,
            "after": before,
            "quotaAfter": quota_before,
            "remainingQuota": subscription_quota_remaining(before),
        }

    if bool(subscription_snapshot.get("active")) and subscription_quota_remaining(subscription_snapshot) >= min_remaining_quota:
        return {
            "ok": True,
            "mode": "existing_subscription",
            "before": before,
            "quotaBefore": quota_before,
            "after": before,
            "quotaAfter": quota_before,
            "remainingQuota": subscription_quota_remaining(subscription_snapshot),
        }

    top_up = run_subscription_flow(base_url, bearer_headers)
    after = get_subscription_status(base_url, bearer_headers)
    quota_after = get_user_quota_status(base_url, bearer_headers)
    remaining_after = max(
        int(quota_after.get("freeQuota") or 0),
        subscription_quota_remaining((quota_after.get("subscription") or {})),
        subscription_quota_remaining(after),
    )
    return {
        "ok": remaining_after >= min_remaining_quota,
        "mode": "mock_pay_subscription",
        "before": before,
        "quotaBefore": quota_before,
        "topUp": top_up,
        "after": after,
        "quotaAfter": quota_after,
        "remainingQuota": remaining_after,
    }


def wait_for_session_interactive(
    base_url: str,
    session_id: str,
    bearer_headers: dict[str, str],
    *,
    wait_s: int,
    poll_interval_s: float,
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


def wait_for_session_interactive_sse(
    base_url: str,
    session_id: str,
    bearer_headers: dict[str, str],
    *,
    wait_s: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + wait_s
    history: list[dict[str, Any]] = []
    latest_snapshot: dict[str, Any] = {}
    event_name = "message"
    data_lines: list[str] = []
    last_session_refresh_monotonic = 0.0
    auth_mode = "bearer_header"
    stream_url = f"{base_url}/api/v1/games/creation-sessions/{session_id}/events"
    auth_header = str(bearer_headers.get("Authorization") or "")

    if auth_header.startswith("Bearer "):
        auth_mode = "query_token"
        stream_url = (
            f"{stream_url}?{parse.urlencode({'token': auth_header[len('Bearer '):].strip()})}"
        )

    req = request.Request(stream_url, method="GET")
    if auth_mode == "bearer_header":
        for key, value in bearer_headers.items():
            req.add_header(key, value)
    req.add_header("Accept", "text/event-stream")
    req.add_header("Cache-Control", "no-cache")

    def current_question_slot(snapshot: dict[str, Any]) -> str | None:
        current_question = snapshot.get("currentQuestion")
        return current_question.get("slotKey") if isinstance(current_question, dict) else None

    def build_result(*, elapsed_s: float | None, timed_out: bool) -> dict[str, Any]:
        return {
            "snapshot": latest_snapshot,
            "events": history,
            "authMode": auth_mode,
            "timedOutLocally": timed_out,
            "interactiveElapsedS": elapsed_s,
        }

    def refresh_snapshot_from_api(elapsed_s: float, *, force: bool = False) -> dict[str, Any] | None:
        nonlocal latest_snapshot, last_session_refresh_monotonic
        now = time.perf_counter()
        if not force and (now - last_session_refresh_monotonic) < 0.25:
            return None
        last_session_refresh_monotonic = now
        latest_snapshot = get_session(base_url, session_id, bearer_headers)
        history.append(
            {
                "elapsedS": elapsed_s,
                "event": "session.refresh",
                "status": latest_snapshot.get("status"),
                "revision": latest_snapshot.get("revision"),
                "readyToGenerate": bool(latest_snapshot.get("readyToGenerate")),
                "slotFillPct": latest_snapshot.get("slotFillPct"),
                "error": latest_snapshot.get("initError"),
                "currentQuestion": current_question_slot(latest_snapshot),
            }
        )
        if latest_snapshot.get("readyToGenerate") or current_question_slot(latest_snapshot):
            return build_result(elapsed_s=elapsed_s, timed_out=False)
        if str(latest_snapshot.get("status") or "").lower() in {"abandoned", "completed"}:
            return build_result(elapsed_s=None, timed_out=False)
        return None

    def flush_event() -> dict[str, Any] | None:
        nonlocal event_name, data_lines, latest_snapshot
        if not data_lines:
            event_name = "message"
            return None
        payload_text = "\n".join(data_lines)
        data_lines = []
        parsed: dict[str, Any]
        try:
            parsed = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError:
            parsed = {"raw": payload_text}

        elapsed_s = round(time.perf_counter() - started, 3)
        session_payload = parsed.get("session") if isinstance(parsed, dict) else None
        latest_snapshot = session_payload if isinstance(session_payload, dict) else latest_snapshot
        current_question = latest_snapshot.get("currentQuestion") if isinstance(latest_snapshot, dict) else None
        history.append(
            {
                "elapsedS": elapsed_s,
                "event": event_name,
                "status": latest_snapshot.get("status") if isinstance(latest_snapshot, dict) else None,
                "revision": latest_snapshot.get("revision") if isinstance(latest_snapshot, dict) else None,
                "readyToGenerate": bool(latest_snapshot.get("readyToGenerate")) if isinstance(latest_snapshot, dict) else False,
                "slotFillPct": latest_snapshot.get("slotFillPct") if isinstance(latest_snapshot, dict) else None,
                "error": parsed.get("error") if isinstance(parsed, dict) else None,
                "currentQuestion": current_question.get("slotKey") if isinstance(current_question, dict) else None,
            }
        )

        result: dict[str, Any] | None = None
        if event_name in {"bootstrap", "snapshot"} and isinstance(latest_snapshot, dict):
            if latest_snapshot.get("readyToGenerate") or current_question:
                result = build_result(elapsed_s=elapsed_s, timed_out=False)
            elif str(latest_snapshot.get("status") or "").lower() in {"abandoned", "completed"}:
                result = refresh_snapshot_from_api(elapsed_s, force=True) or build_result(
                    elapsed_s=None,
                    timed_out=False,
                )
        elif event_name in {"delta", "done"}:
            result = refresh_snapshot_from_api(elapsed_s)
        elif event_name == "error":
            latest_snapshot = {
                "status": "abandoned",
                "initError": (
                    parsed.get("message")
                    if isinstance(parsed, dict) and parsed.get("message")
                    else parsed.get("error")
                    if isinstance(parsed, dict)
                    else "session_error"
                ),
            }
            result = build_result(elapsed_s=None, timed_out=False)

        event_name = "message"
        return result

    with request.urlopen(req, timeout=max(wait_s + 5, 10)) as resp:
        while time.perf_counter() < deadline:
            raw_line = resp.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                flushed = flush_event()
                if flushed is not None:
                    return flushed
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip() or "message"
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())

    flushed = flush_event()
    if flushed is not None:
        return flushed
    refreshed = refresh_snapshot_from_api(round(time.perf_counter() - started, 3), force=True)
    if refreshed is not None:
        return refreshed
    return {
        "snapshot": latest_snapshot,
        "events": history,
        "authMode": auth_mode,
        "timedOutLocally": True,
        "interactiveElapsedS": None,
    }


def answer_session_questions(
    base_url: str,
    session_id: str,
    bearer_headers: dict[str, str],
    case: dict[str, Any],
    *,
    max_turns: int,
) -> dict[str, Any]:
    latest_snapshot = get_session(base_url, session_id, bearer_headers)
    turns: list[dict[str, Any]] = []

    for turn_index in range(max_turns):
        current_question = latest_snapshot.get("currentQuestion") or {}
        if latest_snapshot.get("readyToGenerate") and not current_question:
            break
        if not current_question:
            break

        slot_key = current_question.get("slotKey")
        prompt = current_question.get("prompt")
        revision = latest_snapshot.get("revision")
        answer = answer_for_slot(case, slot_key, prompt)

        if answer:
            response = http_json(
                "POST",
                f"{base_url}/api/v1/games/creation-sessions/{session_id}/messages",
                payload={"content": answer, "revision": revision},
                headers=bearer_headers,
                timeout=60,
            )
            latest_snapshot = (response or {}).get("data") or {}
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
            response = http_json(
                "POST",
                f"{base_url}/api/v1/games/creation-sessions/{session_id}/skip",
                payload={"revision": revision},
                headers=bearer_headers,
                timeout=60,
            )
            latest_snapshot = (response or {}).get("data") or {}
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

        turns.append(
            {
                "turn": turn_index + 1,
                "action": "blocked",
                "slotKey": slot_key,
                "prompt": prompt,
                "revision": revision,
            }
        )
        break

    latest_snapshot = get_session(base_url, session_id, bearer_headers)
    return {
        "snapshot": latest_snapshot,
        "turns": turns,
    }


def maybe_sleep_stagger(index: int, stagger_ms: int) -> None:
    if stagger_ms <= 0:
        return
    time.sleep(((index - 1) * stagger_ms) / 1000.0)


def run_generate_worker(
    *,
    index: int,
    total_users: int,
    start_barrier: threading.Barrier,
    base_url: str,
    admin_token: str,
    user_context: dict[str, Any],
    case: dict[str, Any],
    wait_s: int,
    timeout_s: int,
    stagger_ms: int,
    diagnostics_mode: str,
) -> dict[str, Any]:
    try:
        start_barrier.wait(timeout=60)
        maybe_sleep_stagger(index, stagger_ms)

        started_at = now_iso()
        submission_started = time.perf_counter()
        create_response = http_json(
            "POST",
            f"{base_url}/api/v1/games/generate",
            payload={
                "title": case["title"],
                "description": case["description"],
                "regionHint": "cn_shanghai",
                "timeoutS": timeout_s,
            },
            headers=user_context["headers"],
            timeout=60,
        )
        submission_elapsed_ms = round((time.perf_counter() - submission_started) * 1000, 1)

        create_data = (create_response or {}).get("data") or {}
        game_id = create_data.get("gameId")
        task_id = create_data.get("taskId")
        if not game_id:
            raise RuntimeError(f"Generate did not return gameId: {create_response}")

        task_id, terminal = poll_task_terminal(
            base_url,
            game_id,
            task_id,
            user_context["headers"],
            wait_s=wait_s,
        )
        status_data = terminal.get("status") or {}
        final_status = str(status_data.get("status") or "").lower() or "unknown"

        result: dict[str, Any] = {
            "worker": index,
            "totalUsers": total_users,
            "flow": "generate",
            "username": user_context["username"],
            "userSource": user_context.get("source"),
            "allowanceSetup": user_context.get("allowance"),
            "caseName": case["name"],
            "startedAt": started_at,
            "submissionLatencyMs": submission_elapsed_ms,
            "gameId": game_id,
            "taskId": task_id,
            "finalStatus": final_status,
            "failedStage": status_data.get("failedStage"),
            "failureFamily": status_data.get("failureFamily"),
            "errorMessage": status_data.get("errorMessage"),
            "pollHistory": terminal.get("pollHistory") or [],
            "timedOutLocally": bool(terminal.get("timedOutLocally")),
            "endToEndElapsedS": round(time.perf_counter() - submission_started, 3),
            "completedAt": now_iso(),
        }

        if diagnostics_mode == "all" or (diagnostics_mode == "failures" and final_status != "succeeded"):
            result["diagnostics"] = fetch_task_diagnostics(
                base_url,
                game_id,
                task_id,
                admin_token,
                user_context["headers"],
            )
        return result
    except Exception as exc:
        return {
            "worker": index,
            "totalUsers": total_users,
            "flow": "generate",
            "username": user_context["username"],
            "userSource": user_context.get("source"),
            "caseName": case["name"],
            "startedAt": now_iso(),
            "finalStatus": "script_error",
            "errorMessage": str(exc),
            "completedAt": now_iso(),
        }


def run_session_generate_worker(
    *,
    index: int,
    total_users: int,
    start_barrier: threading.Barrier,
    base_url: str,
    admin_token: str,
    user_context: dict[str, Any],
    case: dict[str, Any],
    wait_s: int,
    timeout_s: int,
    session_wait_s: int,
    session_turns: int,
    session_poll_interval_s: float,
    session_observer: str,
    target_interactive_s: float,
    stagger_ms: int,
    diagnostics_mode: str,
) -> dict[str, Any]:
    try:
        start_barrier.wait(timeout=60)
        maybe_sleep_stagger(index, stagger_ms)

        started_at = now_iso()
        session_started = time.perf_counter()
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
            headers=user_context["headers"],
            timeout=60,
        )
        create_snapshot = (create_response or {}).get("data") or {}
        session_id = create_snapshot.get("id")
        if not session_id:
            raise RuntimeError(f"Creation session did not return id: {create_response}")

        session_post_elapsed_ms = round((time.perf_counter() - session_started) * 1000, 1)
        if session_observer == "sse":
            interactive = wait_for_session_interactive_sse(
                base_url,
                session_id,
                user_context["headers"],
                wait_s=session_wait_s,
            )
        else:
            interactive = wait_for_session_interactive(
                base_url,
                session_id,
                user_context["headers"],
                wait_s=session_wait_s,
                poll_interval_s=session_poll_interval_s,
            )
        interactive_snapshot = interactive.get("snapshot") or {}
        interactive_elapsed_s = interactive.get("interactiveElapsedS")

        result: dict[str, Any] = {
            "worker": index,
            "totalUsers": total_users,
            "flow": "session_generate",
            "username": user_context["username"],
            "userSource": user_context.get("source"),
            "allowanceSetup": user_context.get("allowance"),
            "caseName": case["name"],
            "startedAt": started_at,
            "sessionId": session_id,
            "sessionObserver": session_observer,
            "sessionPostLatencyMs": session_post_elapsed_ms,
            "sessionStatus": interactive_snapshot.get("status") or create_snapshot.get("status"),
            "interactiveElapsedS": interactive_elapsed_s,
            "interactiveTargetS": target_interactive_s,
            "interactiveTargetMet": bool(
                interactive_elapsed_s is not None and interactive_elapsed_s <= target_interactive_s
            ),
            "sessionTimedOutLocally": bool(interactive.get("timedOutLocally")),
            "sessionPolls": interactive.get("polls") or [],
            "sessionEvents": interactive.get("events") or [],
            "sessionInitError": interactive_snapshot.get("initError"),
        }

        if interactive.get("timedOutLocally") or str(interactive_snapshot.get("status") or "").lower() == "abandoned":
            result["finalStatus"] = "session_failed"
            result["errorMessage"] = interactive_snapshot.get("initError") or "Session did not become interactive"
            result["completedAt"] = now_iso()
            return result

        answered = answer_session_questions(
            base_url,
            session_id,
            user_context["headers"],
            case,
            max_turns=session_turns,
        )
        latest_snapshot = answered.get("snapshot") or {}
        result["sessionTurns"] = answered.get("turns") or []
        result["readyToGenerate"] = bool(latest_snapshot.get("readyToGenerate"))
        result["slotFillPct"] = latest_snapshot.get("slotFillPct")
        result["sessionRevision"] = latest_snapshot.get("revision")

        if not latest_snapshot.get("readyToGenerate"):
            result["finalStatus"] = "session_incomplete"
            result["errorMessage"] = "Session did not reach readyToGenerate within allotted turns"
            result["completedAt"] = now_iso()
            return result

        generate_started = time.perf_counter()
        generate_response = http_json(
            "POST",
            f"{base_url}/api/v1/games/creation-sessions/{session_id}/generate",
            payload={
                "revision": latest_snapshot.get("revision"),
                "timeoutS": timeout_s,
            },
            headers=user_context["headers"],
            timeout=60,
        )
        generate_elapsed_ms = round((time.perf_counter() - generate_started) * 1000, 1)
        generate_data = (generate_response or {}).get("data") or {}
        game_id = generate_data.get("gameId")
        task_id = generate_data.get("taskId")
        if not game_id:
            raise RuntimeError(f"Generate-from-session did not return gameId: {generate_response}")

        task_id, terminal = poll_task_terminal(
            base_url,
            game_id,
            task_id,
            user_context["headers"],
            wait_s=wait_s,
        )
        status_data = terminal.get("status") or {}
        final_status = str(status_data.get("status") or "").lower() or "unknown"

        result.update(
            {
                "generateLatencyMs": generate_elapsed_ms,
                "gameId": game_id,
                "taskId": task_id,
                "finalStatus": final_status,
                "failedStage": status_data.get("failedStage"),
                "failureFamily": status_data.get("failureFamily"),
                "errorMessage": status_data.get("errorMessage"),
                "pollHistory": terminal.get("pollHistory") or [],
                "timedOutLocally": bool(terminal.get("timedOutLocally")),
                "endToEndElapsedS": round(time.perf_counter() - session_started, 3),
                "completedAt": now_iso(),
            }
        )

        if diagnostics_mode == "all" or (diagnostics_mode == "failures" and final_status != "succeeded"):
            result["diagnostics"] = fetch_task_diagnostics(
                base_url,
                game_id,
                task_id,
                admin_token,
                user_context["headers"],
            )
        return result
    except Exception as exc:
        return {
            "worker": index,
            "totalUsers": total_users,
            "flow": "session_generate",
            "username": user_context["username"],
            "userSource": user_context.get("source"),
            "caseName": case["name"],
            "startedAt": now_iso(),
            "finalStatus": "script_error",
            "errorMessage": str(exc),
            "completedAt": now_iso(),
        }


def prepare_user_context(
    *,
    base_url: str,
    admin_token: str,
    username: str,
    password: str,
    ensure_allowance: bool,
    min_remaining_quota: int,
) -> dict[str, Any]:
    ensure_user(base_url, admin_token, username, password)
    token = login(base_url, username, password)
    headers = build_bearer_headers(token)
    allowance = None
    if ensure_allowance:
        allowance = ensure_user_allowance(
            base_url,
            headers,
            min_remaining_quota=min_remaining_quota,
        )
        if not allowance.get("ok"):
            raise RuntimeError(f"Failed to provision allowance for created user {username}")
    return {
        "username": username,
        "password": password,
        "token": token,
        "headers": headers,
        "allowance": allowance,
        "source": "created",
    }


def prepare_existing_user_context(
    *,
    base_url: str,
    admin_token: str,
    user: dict[str, Any],
    password: str,
    ensure_allowance: bool,
    min_remaining_quota: int,
) -> dict[str, Any]:
    admin_reset_password(base_url, admin_token, str(user["id"]), password)
    token = login(base_url, str(user["username"]), password)
    headers = build_bearer_headers(token)
    allowance = None
    if ensure_allowance:
        allowance = ensure_user_allowance(
            base_url,
            headers,
            min_remaining_quota=min_remaining_quota,
        )
        if not allowance.get("ok"):
            raise RuntimeError(f"Failed to provision allowance for existing user {user['username']}")
    return {
        "userId": user["id"],
        "username": user["username"],
        "password": password,
        "token": token,
        "headers": headers,
        "allowance": allowance,
        "source": "existing",
    }


def build_summary(results: list[dict[str, Any]], *, flow: str) -> dict[str, Any]:
    final_status_counts = Counter(str(item.get("finalStatus") or "unknown") for item in results)
    submission_latencies = [
        float(item["submissionLatencyMs"])
        for item in results
        if item.get("submissionLatencyMs") is not None
    ]
    session_post_latencies = [
        float(item["sessionPostLatencyMs"])
        for item in results
        if item.get("sessionPostLatencyMs") is not None
    ]
    interactive_latencies = [
        float(item["interactiveElapsedS"])
        for item in results
        if item.get("interactiveElapsedS") is not None
    ]
    end_to_end_latencies = [
        float(item["endToEndElapsedS"])
        for item in results
        if item.get("endToEndElapsedS") is not None
    ]

    summary: dict[str, Any] = {
        "flow": flow,
        "totalUsers": len(results),
        "finalStatusCounts": dict(final_status_counts),
        "successCount": final_status_counts.get("succeeded", 0),
        "failureCount": len(results) - final_status_counts.get("succeeded", 0),
        "submissionLatencyMs": summarize_numbers(submission_latencies, digits=1),
        "sessionPostLatencyMs": summarize_numbers(session_post_latencies, digits=1),
        "interactiveElapsedS": summarize_numbers(interactive_latencies),
        "endToEndElapsedS": summarize_numbers(end_to_end_latencies),
        "userSourceCounts": dict(
            Counter(str(item.get("userSource") or "unknown") for item in results)
        ),
        "sessionObserverCounts": dict(
            Counter(str(item.get("sessionObserver") or "unknown") for item in results if item.get("sessionObserver"))
        ),
    }

    if flow == "session_generate":
        summary["interactiveTargetMetCount"] = sum(1 for item in results if item.get("interactiveTargetMet"))
        summary["sessionInitFailureCount"] = sum(
            1 for item in results if str(item.get("finalStatus") or "").lower() == "session_failed"
        )

    failure_families = Counter(
        str(item.get("failureFamily") or "")
        for item in results
        if item.get("failureFamily")
    )
    if failure_families:
        summary["failureFamilies"] = dict(failure_families)
    return summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--users", type=int, required=True, help="Concurrent user count.")
    parser.add_argument("--flow", choices=["generate", "session_generate"], default="session_generate")
    parser.add_argument("--user-source", choices=["auto", "existing", "create"], default="auto")
    parser.add_argument("--timeout-s", type=int, default=1200)
    parser.add_argument("--wait-s", type=int, default=1500)
    parser.add_argument("--session-wait-s", type=int, default=20)
    parser.add_argument("--session-turns", type=int, default=3)
    parser.add_argument("--session-poll-interval-s", type=float, default=1.0)
    parser.add_argument("--session-observer", choices=["poll", "sse"], default="poll")
    parser.add_argument("--target-interactive-s", type=float, default=5.0)
    parser.add_argument("--skip-allowance-check", action="store_true")
    parser.add_argument("--min-remaining-quota", type=int, default=1)
    parser.add_argument("--stagger-ms", type=int, default=0)
    parser.add_argument("--prepare-workers", type=int, default=8)
    parser.add_argument("--max-workers", type=int, default=0)
    parser.add_argument("--diagnostics", choices=["none", "failures", "all"], default="failures")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    if args.users < 1:
        print("--users must be >= 1", file=sys.stderr)
        return 1

    env = load_env(Path(args.env_file))
    base_url = (
        args.base_url
        or os.environ.get("PUBLIC_API_BASE_URL")
        or env.get("PUBLIC_API_BASE_URL")
        or ""
    ).rstrip("/")
    admin_token = (
        args.admin_token
        or os.environ.get("ADMIN_TOKEN")
        or env.get("ADMIN_TOKEN")
        or ""
    )
    if not base_url or not admin_token:
        print("PUBLIC_API_BASE_URL and ADMIN_TOKEN are required", file=sys.stderr)
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    username_seed = datetime.now().strftime("%y%m%d%H%M%S")
    run_nonce = uuid4().hex[:4]
    output_path = Path(args.output) if args.output else REPO_ROOT / f"tmp_generation_concurrency_{timestamp}.json"
    cases = choose_cases(args.flow)
    worker_count = args.max_workers or args.users
    ensure_allowance = not args.skip_allowance_check

    preparation_started = time.perf_counter()
    user_contexts: list[dict[str, Any]] = []
    print(f"Preparing {args.users} user contexts for flow={args.flow} against {base_url} ...", flush=True)
    selected_existing_users: list[dict[str, Any]] = []
    if args.user_source in {"auto", "existing"}:
        selected_existing_users = collect_existing_test_users(
            base_url,
            admin_token,
            desired_count=args.users,
        )
        print(f"Found {len(selected_existing_users)} reusable test users", flush=True)
        if args.user_source == "existing" and len(selected_existing_users) < args.users:
            print(
                f"Not enough existing test users: need {args.users}, found {len(selected_existing_users)}",
                file=sys.stderr,
            )
            return 1

    with ThreadPoolExecutor(max_workers=max(1, min(args.prepare_workers, args.users))) as executor:
        future_map = {}
        for index in range(1, args.users + 1):
            password = f"CodexLoad!{timestamp}_{run_nonce}_{index:03d}"
            if index <= len(selected_existing_users):
                future = executor.submit(
                    prepare_existing_user_context,
                    base_url=base_url,
                    admin_token=admin_token,
                    user=selected_existing_users[index - 1],
                    password=password,
                    ensure_allowance=ensure_allowance,
                    min_remaining_quota=args.min_remaining_quota,
                )
            else:
                username = f"load_{username_seed}_{run_nonce}_{index:03d}"
                future = executor.submit(
                    prepare_user_context,
                    base_url=base_url,
                    admin_token=admin_token,
                    username=username,
                    password=password,
                    ensure_allowance=ensure_allowance,
                    min_remaining_quota=args.min_remaining_quota,
                )
            future_map[future] = index

        ordered_contexts: dict[int, dict[str, Any]] = {}
        for future in as_completed(future_map):
            index = future_map[future]
            ordered_contexts[index] = future.result()
            source = ordered_contexts[index].get("source") or "unknown"
            print(f"Prepared user {index}/{args.users} ({source})", flush=True)

    for index in range(1, args.users + 1):
        user_contexts.append(ordered_contexts[index])

    prep_elapsed_s = round(time.perf_counter() - preparation_started, 3)
    start_barrier = threading.Barrier(args.users)
    results: list[dict[str, Any]] = []

    print(
        f"Starting concurrency run with {args.users} users, flow={args.flow}, staggerMs={args.stagger_ms} ...",
        flush=True,
    )
    run_started_at = now_iso()
    with ThreadPoolExecutor(max_workers=max(1, worker_count)) as executor:
        future_map = {}
        for index, user_context in enumerate(user_contexts, start=1):
            case = cases[(index - 1) % len(cases)]
            if args.flow == "session_generate":
                future = executor.submit(
                    run_session_generate_worker,
                    index=index,
                    total_users=args.users,
                    start_barrier=start_barrier,
                    base_url=base_url,
                    admin_token=admin_token,
                    user_context=user_context,
                    case=case,
                    wait_s=args.wait_s,
                    timeout_s=args.timeout_s,
                    session_wait_s=args.session_wait_s,
                    session_turns=args.session_turns,
                    session_poll_interval_s=args.session_poll_interval_s,
                    session_observer=args.session_observer,
                    target_interactive_s=args.target_interactive_s,
                    stagger_ms=args.stagger_ms,
                    diagnostics_mode=args.diagnostics,
                )
            else:
                future = executor.submit(
                    run_generate_worker,
                    index=index,
                    total_users=args.users,
                    start_barrier=start_barrier,
                    base_url=base_url,
                    admin_token=admin_token,
                    user_context=user_context,
                    case=case,
                    wait_s=args.wait_s,
                    timeout_s=args.timeout_s,
                    stagger_ms=args.stagger_ms,
                    diagnostics_mode=args.diagnostics,
                )
            future_map[future] = index

        ordered_results: dict[int, dict[str, Any]] = {}
        for future in as_completed(future_map):
            index = future_map[future]
            result = future.result()
            ordered_results[index] = result
            print(
                f"[{index}/{args.users}] {result['username']} -> {result.get('finalStatus')}",
                flush=True,
            )

            partial_results = [ordered_results[i] for i in sorted(ordered_results)]
            partial_summary = {
                "generatedAt": now_iso(),
                "baseUrl": base_url,
                "flow": args.flow,
                "users": args.users,
                "preparedInS": prep_elapsed_s,
                "runStartedAt": run_started_at,
                "resultsCompleted": len(partial_results),
                "summary": build_summary(partial_results, flow=args.flow),
                "results": partial_results,
            }
            output_path.write_text(json.dumps(partial_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    results = [ordered_results[index] for index in sorted(ordered_results)]
    final_summary = {
        "generatedAt": now_iso(),
        "baseUrl": base_url,
        "flow": args.flow,
        "users": args.users,
        "maxWorkers": worker_count,
        "prepareWorkers": max(1, min(args.prepare_workers, args.users)),
        "userSource": args.user_source,
        "preparedInS": prep_elapsed_s,
        "runStartedAt": run_started_at,
        "staggerMs": args.stagger_ms,
        "timeoutS": args.timeout_s,
        "waitS": args.wait_s,
        "sessionWaitS": args.session_wait_s,
        "sessionTurns": args.session_turns,
        "targetInteractiveS": args.target_interactive_s,
        "ensureAllowance": ensure_allowance,
        "minRemainingQuota": args.min_remaining_quota,
        "diagnosticsMode": args.diagnostics,
        "summary": build_summary(results, flow=args.flow),
        "results": results,
    }
    output_path.write_text(json.dumps(final_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "summary": final_summary["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
