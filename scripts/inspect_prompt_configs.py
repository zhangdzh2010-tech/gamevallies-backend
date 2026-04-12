"""Inspect live prompt configuration through the production admin API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / ".env.deploy"


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


def http_json(url: str, *, headers: dict[str, str], timeout: int = 30) -> Any:
    req = request.Request(url, method="GET")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else None
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {body}") from exc


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--base-url", default="")
    parser.add_argument("--admin-token", default="")
    args = parser.parse_args(argv)

    env = load_env(Path(args.env_file))
    base_url = (args.base_url or os.environ.get("PUBLIC_API_BASE_URL") or env.get("PUBLIC_API_BASE_URL") or "").rstrip("/")
    admin_token = args.admin_token or os.environ.get("ADMIN_TOKEN") or env.get("ADMIN_TOKEN") or ""
    if not base_url or not admin_token:
        print("PUBLIC_API_BASE_URL and ADMIN_TOKEN are required", file=sys.stderr)
        return 1

    headers = {"x-admin-token": admin_token}
    configs = http_json(f"{base_url}/api/v1/admin/configs?category=prompt", headers=headers)
    prompt_bundles = http_json(f"{base_url}/api/v1/admin/prompt-bundles", headers=headers)
    runtime_profiles = http_json(f"{base_url}/api/v1/admin/runtime-profiles", headers=headers)

    config_rows = (configs or {}).get("data") or []
    bundle_rows = (prompt_bundles or {}).get("data") or []
    profile_rows = (runtime_profiles or {}).get("data") or []

    prompt_map = {
      row.get("configKey"): row.get("configValue")
      for row in config_rows
      if isinstance(row, dict) and row.get("configKey")
    }

    summary = {
        "baseUrl": base_url,
        "promptCount": len(config_rows),
        "promptBundleCount": len(bundle_rows),
        "runtimeProfileCount": len(profile_rows),
        "activePromptBundles": [
            {
                "id": row.get("id"),
                "version": row.get("version"),
                "status": row.get("status"),
                "productPolicy": row.get("productPolicy"),
                "repairPlaybook": row.get("repairPlaybook"),
                "profileOverrides": row.get("profileOverrides"),
                "metadata": row.get("metadata"),
            }
            for row in bundle_rows
            if isinstance(row, dict) and str(row.get("status") or "").lower() == "active"
        ],
        "runtimeProfiles": [
            {
                "id": row.get("id"),
                "displayName": row.get("displayName"),
                "enabled": row.get("enabled"),
                "skeletonVersion": row.get("skeletonVersion"),
                "fewShotPrompt": row.get("fewShotPrompt"),
                "contractSchema": row.get("contractSchema"),
                "metadata": row.get("metadata"),
            }
            for row in profile_rows
            if isinstance(row, dict)
        ],
        "promptSamples": {
            key: prompt_map.get(key)
            for key in (
                "prompt.code_gen_system",
                "bundle.product.policy",
                "bundle.product.logic_generate",
                "bundle.repair.syntax_structural",
            )
            if key in prompt_map
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
