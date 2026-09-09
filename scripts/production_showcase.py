"""Bounded production showcase operations; credentials stay in caller memory.

No account creation, quota changes, automatic retries or automatic publication.
Each write is explicit. Keep all attempts, including failures, in the journal.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from run_live_generation_e2e import http_json


def redact(value):
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.fullmatch(r"(?:.*[_-])?(?:access_?token|refresh_?token|preview_?token|token|password|secret|authorization|api_?key)", key, re.I)
                      else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"([?&](?:previewToken|token|signature)=)[^&\s\"<>]+", r"\1[REDACTED]", value, flags=re.I)
        return re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED_JWT]", value)
    return value


class Showcase:
    def __init__(self, base, token, output):
        if urlsplit(base).scheme != "https":
            raise ValueError("Production requests require HTTPS")
        self.base = base.rstrip("/")
        self.headers = {"Authorization": "Bearer " + token}
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.manifest = json.loads((Path(__file__).parents[1] / "tests/acceptance/showcase-20260909.json").read_text())
        self.journal = self.output / "events.jsonl"

    def record(self, event, **data):
        row = redact(dict(timestamp=datetime.now(timezone.utc).isoformat(), event=event, **data))
        with self.journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def get(self, path):
        response = http_json("GET", self.base + "/api/v1/" + path, headers=self.headers, timeout=45)
        if response.get("code") != 0:
            raise RuntimeError(str(redact(response)))
        return response.get("data")

    def post(self, path, payload):
        response = http_json("POST", self.base + "/api/v1/" + path, payload=payload, headers=self.headers, timeout=60)
        if response.get("code") != 0:
            raise RuntimeError(str(redact(response)))
        return response.get("data")

    def submit(self, index):
        case = self.manifest["cases"][index]
        if self.journal.exists():
            rows = [json.loads(line) for line in self.journal.read_text().splitlines() if line]
            if any(row.get("event") == "submit_started" and row.get("case_id") == case["id"] for row in rows):
                raise ValueError("Already submitted or ambiguous: reconcile journal before retrying")
        quota = self.get("users/quota")
        subscription = quota.get("subscription") or {}
        remaining = int(quota.get("freeQuota") or 0)
        if subscription.get("active"):
            remaining += max(0, int(subscription.get("quotaThisPeriod") or 0) - int(subscription.get("usedThisPeriod") or 0))
        if remaining < 1:
            raise ValueError("No generation allowance; no top-up will be attempted")
        prompt = case["prompt"] + "\n" + self.manifest["shared_brief"]
        self.record("submit_started", case_id=case["id"], kind=case["kind"], title=case["title"], prompt=prompt, quota_remaining=remaining)
        started = time.monotonic()
        try:
            result = self.post("games/generate", {"title": case["title"], "description": prompt,
                "orientation": "landscape", "generationTier": "showcase", "timeoutS": 1800})
        except Exception as exc:
            self.record("submit_error", case_id=case["id"], error=str(exc), acceptance="unknown")
            raise
        return self.record("submitted", case_id=case["id"], kind=case["kind"], response=result,
                           request_elapsed_s=round(time.monotonic()-started, 3))

    def status(self, game_id):
        return self.record("status", work_id=game_id, data=self.get(f"games/{game_id}/generation-status"))

    def cache_playable(self, game_id):
        """Fetch author-accessible HTML for offline browser QA, never credentials."""
        import uuid
        uuid.UUID(game_id)
        result = self.get(f"games/{game_id}/play")
        code = result.get("htmlCode")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("No playable HTML returned")
        path = self.output / (game_id + ".html")
        path.write_text(code, encoding="utf-8")
        self.record("playable_cached", work_id=game_id, path=str(path), bytes=len(code.encode()))
        return str(path)

    def evidence(self, game_id):
        status = self.get(f"games/{game_id}/generation-status")
        evidence = {"status": status}
        task_id = status.get("taskId")
        if task_id:
            for name, path in (("task", f"games/tasks/{task_id}"),
                               ("events", f"games/tasks/{task_id}/events?limit=200"),
                               ("artifacts", f"games/tasks/{task_id}/artifacts?limit=200")):
                try:
                    evidence[name] = self.get(path)
                except Exception as exc:
                    evidence[name] = {"retrieval_error": str(exc)}
        # Keep the original generation evidence when a later iteration replaces
        # the work's latest-task pointer.
        (self.output / f"{game_id}-{task_id or 'no-task'}.json").write_text(json.dumps(redact(evidence), ensure_ascii=False, indent=2))
        self.record("evidence_saved", work_id=game_id, task_id=task_id)
        return redact(evidence)

    def publish(self, index, game_id, checks):
        case = self.manifest["cases"][index]
        expected = set(case["checks"] + self.manifest["shared_checks"])
        if set(checks) != expected or not all(value is True for value in checks.values()):
            raise ValueError("Every declared interactive and visual acceptance check must pass")
        status = self.get(f"games/{game_id}/generation-status")
        if status.get("status") != "succeeded":
            raise ValueError("Generation has not succeeded")
        rows = [json.loads(line) for line in self.journal.read_text().splitlines() if line]
        if not any(row.get("event") == "submitted" and row.get("case_id") == case["id"]
                   and (row.get("response") or {}).get("gameId") == game_id for row in rows):
            raise ValueError("Only this batch's own matching work may be published")
        self.record("acceptance_passed", case_id=case["id"], work_id=game_id, checks=checks)
        result = self.post(f"games/{game_id}/publish", {"title": case["title"],
            "description": case["description"], "visibility": "public", "tags": case["tags"]})
        return self.record("published", case_id=case["id"], work_id=game_id, response=result)
