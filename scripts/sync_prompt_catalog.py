"""Sync the checked-in prompt catalog into game-service system_configs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib import error, parse, request


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "packages" / "game-service" / "src" / "game" / "catalogs" / "prompt-catalog.json"
ENV_PATH = REPO_ROOT / ".env.deploy"


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


def put_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, method="PUT")
    for key, value in headers.items():
        req.add_header(key, value)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Prompt sync failed with HTTP {resp.status}: {resp.read().decode('utf-8', errors='ignore')}")


def main() -> int:
    env = load_env(ENV_PATH)
    base_url = os.environ.get("PUBLIC_API_BASE_URL") or env.get("PUBLIC_API_BASE_URL")
    admin_token = os.environ.get("ADMIN_TOKEN") or env.get("ADMIN_TOKEN")
    if not base_url or not admin_token:
        print("PUBLIC_API_BASE_URL and ADMIN_TOKEN are required", file=sys.stderr)
        return 1

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    headers = {
        "x-admin-token": admin_token,
    }

    created_or_updated = 0
    for item in catalog:
        key = item["key"]
        encoded_key = parse.quote(key, safe="")
        put_json(
            f"{base_url.rstrip('/')}/api/v1/admin/configs/{encoded_key}",
            {
                "value": item["value"],
                "description": item.get("description") or "",
                "category": "prompt",
            },
            headers,
        )
        created_or_updated += 1
        print(f"synced {key}")

    print(f"prompt catalog sync complete: {created_or_updated} items")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        print(f"HTTPError {exc.code}: {body}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # pragma: no cover - operator script
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
