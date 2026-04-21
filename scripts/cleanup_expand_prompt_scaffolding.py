#!/usr/bin/env python3
"""Clean up legacy expand-prompt scaffolding that leaked into production data.

Targets three sources:

1. ``game_creation_sessions.metadata.expandedPrompt`` (JSON column).
2. ``game_creation_sessions.metadata.originalIdea`` (JSON column).
3. ``games.description`` (text column).

Runs the same sanitizer rules used by the live services so the stored values
match what ai-engine / game-service / frontend now emit.

Usage::

    python scripts/cleanup_expand_prompt_scaffolding.py --dry-run
    python scripts/cleanup_expand_prompt_scaffolding.py --apply

Credentials are read from the environment: ``PROD_DB_HOST``, ``PROD_DB_USER``,
``PROD_DB_PASSWORD``, ``PROD_DB_NAME``, ``PROD_DB_PORT`` (defaults to 3306).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Load the ai-engine sanitizer directly by file path to avoid triggering the
# ``engine`` package __init__ which pulls in the whole pipeline + FastAPI.
import importlib.util  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_SANITIZER_PATH = (
    ROOT / "packages" / "ai-engine" / "src" / "engine" / "expand_prompt_sanitizer.py"
)
_spec = importlib.util.spec_from_file_location(
    "expand_prompt_sanitizer", _SANITIZER_PATH
)
if _spec is None or _spec.loader is None:
    raise SystemExit(f"Unable to load sanitizer from {_SANITIZER_PATH}")
_sanitizer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sanitizer)  # type: ignore[union-attr]
strip_user_facing_scaffolding = _sanitizer.strip_user_facing_scaffolding
has_meaningful_brief = _sanitizer.has_meaningful_brief

import pymysql  # noqa: E402


# Heuristic patterns used to pre-filter rows that might contain scaffolding.
# The canonical cleanup still routes every candidate through
# ``strip_user_facing_scaffolding`` to make sure the result stays in lockstep
# with the live service output.
SCAFFOLDING_HINTS = re.compile(
    r"(请把这条想法整理成|请将这条想法整理成|原始想法[:：]|用户想法[:：]|"
    r"Game\s*Type\s*[:：]|Core\s*Mechanic\s*[:：]|Input\s*Method\s*[:：]|"
    r"Win\s*Condition\s*[:：]|Difficulty\s*Ramp\s*[:：]|Visual\s*Direction\s*[:：]|"
    r"Special\s*Rules[^:\n]*[:：]|Scoring\s*/?\s*Rewards\s*[:：]|"
    r"游戏类型[:：]|核心玩法[:：]|操作方式[:：]|胜利条件[:：]|视觉方向[:：]|难度节奏[:：])",
    re.IGNORECASE,
)


_ORIGINAL_IDEA_EXTRACT = re.compile(
    r"^\s*(?:原始想法|用户想法|用户输入|Original\s+Idea|User(?:'s)?\s+Idea|User\s+Input)"
    r"\s*[:：]\s*(?P<idea>[^\r\n]*)",
    re.IGNORECASE | re.MULTILINE,
)


def sanitize(text: str | None) -> tuple[str, bool]:
    """Return (sanitized, changed) for ``text``.

    Strategy:

    1. Run the canonical sanitizer (what live services apply).
    2. If the sanitizer empties the field but the original text had an
       ``原始想法:`` / ``Original Idea:`` prefix, recover the idea from that
       prefix so we never destroy the user's real input during migration.

    ``changed`` is ``True`` when the stored value would be rewritten.
    """
    if text is None:
        return "", False
    original = str(text)
    cleaned = strip_user_facing_scaffolding(original)

    if not cleaned.strip():
        match = _ORIGINAL_IDEA_EXTRACT.search(original)
        if match:
            idea = match.group("idea").strip()
            if idea:
                # Pass the recovered idea through the sanitizer one more time
                # so we don't smuggle a label back in.
                cleaned = strip_user_facing_scaffolding(idea) or idea

    return cleaned, cleaned != original


def get_connection() -> pymysql.connections.Connection:
    host = os.environ.get("PROD_DB_HOST", "mysql-5f64263dff43-public.rds.volces.com")
    port = int(os.environ.get("PROD_DB_PORT", "3306"))
    user = os.environ.get("PROD_DB_USER")
    password = os.environ.get("PROD_DB_PASSWORD")
    database = os.environ.get("PROD_DB_NAME", "gamevallies")
    if not user or not password:
        raise SystemExit(
            "Missing PROD_DB_USER / PROD_DB_PASSWORD in environment. Refusing to connect."
        )
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def scan_and_fix_sessions(conn, *, apply_changes: bool) -> dict:
    stats = {
        "rows_scanned": 0,
        "expanded_prompt_cleaned": 0,
        "expanded_prompt_cleared_to_empty": 0,
        "original_idea_cleaned": 0,
    }
    sql = """
        SELECT id, metadata
        FROM game_creation_sessions
        WHERE metadata IS NOT NULL
          AND (
            JSON_EXTRACT(metadata, '$.expandedPrompt') IS NOT NULL
            OR JSON_EXTRACT(metadata, '$.originalIdea') IS NOT NULL
          )
    """
    updates: list[tuple[str, str]] = []
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    stats["rows_scanned"] = len(rows)

    for row in rows:
        session_id = row["id"]
        raw_metadata = row["metadata"]
        if isinstance(raw_metadata, (bytes, bytearray)):
            raw_metadata = raw_metadata.decode("utf-8", errors="replace")
        try:
            meta = json.loads(raw_metadata) if isinstance(raw_metadata, str) else raw_metadata
        except json.JSONDecodeError:
            print(f"[WARN] session {session_id}: metadata is not JSON, skipping")
            continue
        if not isinstance(meta, dict):
            continue

        changed = False

        ep = meta.get("expandedPrompt")
        if isinstance(ep, str):
            if SCAFFOLDING_HINTS.search(ep) or not has_meaningful_brief(ep, min_chars=1):
                cleaned, did_change = sanitize(ep)
                if did_change:
                    changed = True
                    meta["expandedPrompt"] = cleaned
                    stats["expanded_prompt_cleaned"] += 1
                    if not cleaned.strip():
                        stats["expanded_prompt_cleared_to_empty"] += 1
                    print(
                        f"[sessions] {session_id}: expandedPrompt "
                        f"{len(ep)} -> {len(cleaned)} chars"
                    )

        oi = meta.get("originalIdea")
        if isinstance(oi, str):
            cleaned, did_change = sanitize(oi)
            if did_change:
                changed = True
                meta["originalIdea"] = cleaned
                stats["original_idea_cleaned"] += 1
                print(
                    f"[sessions] {session_id}: originalIdea "
                    f"{len(oi)} -> {len(cleaned)} chars"
                )

        if changed:
            updates.append(
                (session_id, json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
            )

    if apply_changes and updates:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE game_creation_sessions SET metadata = %s WHERE id = %s",
                [(metadata_json, sid) for sid, metadata_json in updates],
            )
        conn.commit()
        print(f"[sessions] applied {len(updates)} row updates")
    elif updates:
        print(f"[sessions] dry-run: would update {len(updates)} rows")
    return stats


def scan_and_fix_games(conn, *, apply_changes: bool) -> dict:
    stats = {"rows_scanned": 0, "descriptions_cleaned": 0}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, description
            FROM games
            WHERE description IS NOT NULL
              AND (
                description LIKE '%请把这条想法整理成%'
                OR description LIKE '%原始想法%'
                OR description LIKE '%用户想法%'
                OR description LIKE '%Game Type:%'
                OR description LIKE '%Core Mechanic:%'
                OR description LIKE '%Input Method:%'
                OR description LIKE '%Win Condition:%'
                OR description LIKE '%Difficulty Ramp:%'
                OR description LIKE '%Visual Direction:%'
                OR description LIKE '%Special Rules%'
              )
            """
        )
        rows = cur.fetchall()
    stats["rows_scanned"] = len(rows)

    updates: list[tuple[str, str]] = []
    for row in rows:
        cleaned, did_change = sanitize(row["description"])
        if did_change:
            updates.append((row["id"], cleaned))
            stats["descriptions_cleaned"] += 1
            print(
                f"[games] {row['id']}: description "
                f"{len(row['description'])} -> {len(cleaned)} chars"
            )

    if apply_changes and updates:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE games SET description = %s WHERE id = %s",
                [(cleaned, gid) for gid, cleaned in updates],
            )
        conn.commit()
        print(f"[games] applied {len(updates)} row updates")
    elif updates:
        print(f"[games] dry-run: would update {len(updates)} rows")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="scan only, do not modify")
    mode.add_argument("--apply", action="store_true", help="apply in-place UPDATEs")
    args = parser.parse_args()

    conn = get_connection()
    try:
        session_stats = scan_and_fix_sessions(conn, apply_changes=args.apply)
        game_stats = scan_and_fix_games(conn, apply_changes=args.apply)
    finally:
        conn.close()

    print("\n==== summary ====")
    print(f"sessions scanned:              {session_stats['rows_scanned']}")
    print(f"expandedPrompt cleaned:        {session_stats['expanded_prompt_cleaned']}")
    print(f"  cleared to empty:            {session_stats['expanded_prompt_cleared_to_empty']}")
    print(f"originalIdea cleaned:          {session_stats['original_idea_cleaned']}")
    print(f"games scanned:                 {game_stats['rows_scanned']}")
    print(f"game.description cleaned:      {game_stats['descriptions_cleaned']}")
    print(f"mode:                          {'APPLIED' if args.apply else 'dry-run'}")


if __name__ == "__main__":
    main()
