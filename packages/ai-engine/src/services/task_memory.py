"""Task-scoped shared memory for multi-step LLM pipelines."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import json
import logging
import math
import re
import time
from typing import Any, Optional

from redis.asyncio import Redis

from ..api.models import GameRuntimeContract, GameSpec, QACheckError, SourceBundleContext
from ..config.settings import settings
from ..config.timeout_store import get_int as get_timeout_int

logger = logging.getLogger(__name__)


@dataclass
class TaskMemoryRecord:
    task_id: str
    task_meta: dict[str, Any] = field(default_factory=dict)
    spec_summary: str = ""
    runtime_profile_summary: str = ""
    contract_summary: str = ""
    source_context_summary: str = ""
    latest_code_summary: str = ""
    latest_qa_findings: str = ""
    repair_history_summary: str = ""
    decision_log: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _head_tail(text: str, *, head: int = 700, tail: int = 380) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= head + tail + 48:
        return normalized
    omitted = len(normalized) - head - tail
    return f"{normalized[:head].rstrip()}\n...[omitted {omitted} chars]...\n{normalized[-tail:].lstrip()}"


class TaskMemoryService:
    def __init__(self) -> None:
        self._memory: dict[str, TaskMemoryRecord] = {}
        self._lock = asyncio.Lock()
        self._redis: Optional[Redis] = None
        self._redis_checked = False

    def _ttl_s(self) -> int:
        return get_timeout_int(
            "timeout.ai_engine.task_memory_ttl_s",
            2 * 60 * 60,
            min_value=300,
        )

    @staticmethod
    def _redis_key(task_id: str) -> str:
        return f"ai-engine:task-memory:{task_id}"

    async def _get_redis(self) -> Optional[Redis]:
        if self._redis_checked:
            return self._redis

        self._redis_checked = True
        try:
            self._redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
            await self._redis.ping()
        except Exception as exc:  # pragma: no cover - best effort only
            logger.debug("task_memory: redis unavailable, using in-memory store: %s", exc)
            self._redis = None
        return self._redis

    @staticmethod
    def _serialize(record: TaskMemoryRecord) -> str:
        payload = asdict(record)
        payload["updated_at"] = time.time()
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _deserialize(payload: str) -> Optional[TaskMemoryRecord]:
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or not data.get("task_id"):
            return None
        return TaskMemoryRecord(
            task_id=str(data.get("task_id")),
            task_meta=dict(data.get("task_meta") or {}),
            spec_summary=str(data.get("spec_summary") or ""),
            runtime_profile_summary=str(data.get("runtime_profile_summary") or ""),
            contract_summary=str(data.get("contract_summary") or ""),
            source_context_summary=str(data.get("source_context_summary") or ""),
            latest_code_summary=str(data.get("latest_code_summary") or ""),
            latest_qa_findings=str(data.get("latest_qa_findings") or ""),
            repair_history_summary=str(data.get("repair_history_summary") or ""),
            decision_log=[str(item) for item in (data.get("decision_log") or []) if str(item or "").strip()],
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )

    async def _persist(self, record: TaskMemoryRecord) -> TaskMemoryRecord:
        record.updated_at = time.time()
        self._memory[record.task_id] = record
        redis = await self._get_redis()
        if redis is not None:
            try:
                await redis.setex(self._redis_key(record.task_id), self._ttl_s(), self._serialize(record))
            except Exception as exc:  # pragma: no cover - best effort only
                logger.debug("task_memory: redis persist failed for %s: %s", record.task_id, exc)
        return record

    async def get(self, task_id: Optional[str]) -> Optional[TaskMemoryRecord]:
        if not task_id:
            return None

        async with self._lock:
            existing = self._memory.get(task_id)
            if existing is not None:
                return existing

        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            payload = await redis.get(self._redis_key(task_id))
        except Exception as exc:  # pragma: no cover - best effort only
            logger.debug("task_memory: redis get failed for %s: %s", task_id, exc)
            return None

        record = self._deserialize(payload or "")
        if record is None:
            return None

        async with self._lock:
            self._memory[task_id] = record
        return record

    async def begin_task(
        self,
        task_id: Optional[str],
        *,
        task_meta: Optional[dict[str, Any]] = None,
        source_context_summary: Optional[str] = None,
    ) -> None:
        if not task_id:
            return

        record = TaskMemoryRecord(
            task_id=task_id,
            task_meta=dict(task_meta or {}),
            source_context_summary=str(source_context_summary or ""),
        )
        async with self._lock:
            await self._persist(record)

    async def clear_task(self, task_id: Optional[str]) -> None:
        if not task_id:
            return

        async with self._lock:
            self._memory.pop(task_id, None)

        redis = await self._get_redis()
        if redis is not None:
            try:
                await redis.delete(self._redis_key(task_id))
            except Exception as exc:  # pragma: no cover - best effort only
                logger.debug("task_memory: redis delete failed for %s: %s", task_id, exc)

    async def update_meta(self, task_id: Optional[str], **fields: Any) -> None:
        if not task_id:
            return

        async with self._lock:
            record = self._memory.get(task_id) or TaskMemoryRecord(task_id=task_id)
            for key, value in fields.items():
                if value is None:
                    continue
                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        continue
                record.task_meta[key] = value
            await self._persist(record)

    async def append_decision(self, task_id: Optional[str], message: Optional[str]) -> None:
        normalized = _clean_text(message)
        if not task_id or not normalized:
            return

        async with self._lock:
            record = self._memory.get(task_id) or TaskMemoryRecord(task_id=task_id)
            if not record.decision_log or record.decision_log[-1] != normalized:
                record.decision_log.append(normalized)
                if len(record.decision_log) > 8:
                    record.decision_log = record.decision_log[-8:]
            await self._persist(record)

    async def remember_spec(self, task_id: Optional[str], spec: Optional[GameSpec]) -> None:
        if not task_id or spec is None:
            return

        mechanics = ", ".join(
            _clean_text(getattr(mechanic, "type", ""))
            for mechanic in (spec.core_mechanics or [])[:3]
            if _clean_text(getattr(mechanic, "type", ""))
        ) or "n/a"
        rules = spec.rules
        summary = "\n".join([
            f"- game_type: {_clean_text(spec.game_type) or 'unknown'}",
            f"- intent: {_clean_text(spec.intent_summary or spec.source_description)[:220] or 'n/a'}",
            f"- theme: {_clean_text(spec.visual_style.theme) or 'n/a'}",
            f"- ui_language: {_clean_text(spec.ui_language) or 'en-US'}",
            f"- mechanics: {mechanics}",
            f"- rules: win={_clean_text(rules.win_condition)}, lose={_clean_text(rules.lose_condition)}, scoring={_clean_text(rules.scoring)}",
        ])

        async with self._lock:
            record = self._memory.get(task_id) or TaskMemoryRecord(task_id=task_id)
            record.spec_summary = summary.strip()
            await self._persist(record)

    async def remember_runtime_contract(
        self,
        task_id: Optional[str],
        *,
        runtime_profile: Optional[str],
        contract: Optional[GameRuntimeContract],
    ) -> None:
        if not task_id:
            return

        orientation = _clean_text(
            getattr(getattr(contract, "mobile_layout", None), "orientation", None)
            or getattr(getattr(contract, "canvas", None), "orientation", None)
        ) or "portrait_first"
        required_states = ", ".join(getattr(getattr(contract, "state", None), "required_states", []) or []) or "n/a"
        gestures = ", ".join(getattr(getattr(contract, "input", None), "gestures", []) or []) or "n/a"
        goal = _clean_text(getattr(getattr(contract, "gameplay", None), "primary_goal", None)) or "n/a"

        runtime_profile_summary = "\n".join([
            f"- runtime_profile: {_clean_text(runtime_profile) or 'n/a'}",
            f"- orientation: {orientation}",
            f"- primary_goal: {goal}",
        ])
        contract_summary = "\n".join([
            f"- required_states: {required_states}",
            f"- gestures: {gestures}",
            f"- restart_required: {bool(getattr(getattr(contract, 'gameplay', None), 'requires_restart_entry', False))}",
        ])

        async with self._lock:
            record = self._memory.get(task_id) or TaskMemoryRecord(task_id=task_id)
            record.runtime_profile_summary = runtime_profile_summary.strip()
            record.contract_summary = contract_summary.strip()
            await self._persist(record)

    async def remember_source_context(
        self,
        task_id: Optional[str],
        *,
        source_bundle_context: Optional[SourceBundleContext] = None,
        source_spec: Optional[GameSpec] = None,
        current_code: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> None:
        if not task_id:
            return

        parts: list[str] = []
        if source_bundle_context is not None:
            parts.extend(
                part for part in [
                    f"- source_title: {_clean_text(source_bundle_context.title) or 'n/a'}",
                    f"- latest_game_type: {_clean_text(source_bundle_context.latest_game_type) or 'n/a'}",
                    f"- latest_bundle_version: {source_bundle_context.latest_bundle_version or 'n/a'}",
                ] if part
            )
        if source_spec is not None:
            parts.append(f"- source_spec: {_clean_text(source_spec.intent_summary or source_spec.source_description)[:220] or 'n/a'}")
        if feedback:
            parts.append(f"- requested_change: {_clean_text(feedback)[:220]}")
        if current_code:
            parts.append(f"- current_code: {self._summarize_code(current_code)}")

        async with self._lock:
            record = self._memory.get(task_id) or TaskMemoryRecord(task_id=task_id)
            record.source_context_summary = "\n".join(parts).strip()
            await self._persist(record)

    async def remember_code(self, task_id: Optional[str], code: Optional[str], *, label: str = "latest_code") -> None:
        if not task_id or not code:
            return

        async with self._lock:
            record = self._memory.get(task_id) or TaskMemoryRecord(task_id=task_id)
            record.latest_code_summary = f"- {label}: {self._summarize_code(code)}"
            await self._persist(record)

    async def remember_qa_findings(
        self,
        task_id: Optional[str],
        errors: list[QACheckError],
        *,
        repair_family: Optional[str] = None,
        fix_round: Optional[int] = None,
    ) -> None:
        if not task_id:
            return

        findings = [
            f"- [{_clean_text(error.type) or 'unknown'}] {_clean_text(error.message)[:200]}"
            for error in (errors or [])[:6]
        ]
        header = []
        if repair_family:
            header.append(f"family={repair_family}")
        if fix_round is not None:
            header.append(f"round={fix_round}")
        prefix = f"({', '.join(header)}) " if header else ""

        async with self._lock:
            record = self._memory.get(task_id) or TaskMemoryRecord(task_id=task_id)
            record.latest_qa_findings = prefix + ("\n".join(findings).strip() or "- none")
            if findings:
                history_line = f"{prefix}{'; '.join(_clean_text(item) for item in findings[:3])}"
                record.repair_history_summary = "\n".join(
                    part for part in [record.repair_history_summary.strip(), history_line.strip()] if part
                ).strip()
                record.repair_history_summary = "\n".join(record.repair_history_summary.splitlines()[-6:])
            await self._persist(record)

    @staticmethod
    def _summarize_code(code: str) -> str:
        stripped = (code or "").strip()
        line_count = len(stripped.splitlines())
        function_names = re.findall(r"function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
        handler_names = re.findall(r"addEventListener\s*\(\s*['\"]([^'\"]+)['\"]", stripped)
        details = [
            f"chars={len(stripped)}",
            f"lines={line_count}",
        ]
        if function_names:
            details.append(f"functions={', '.join(function_names[:5])}")
        if handler_names:
            details.append(f"handlers={', '.join(handler_names[:4])}")
        excerpt = _head_tail(stripped, head=420, tail=240)
        return "; ".join(details) + f"; excerpt={excerpt}"

    def build_prompt_block(
        self,
        task_id: Optional[str],
        *,
        step_key: str,
        compression_policy: str,
        compact: bool = False,
    ) -> str:
        if not task_id:
            return ""

        record = self._memory.get(task_id)
        if record is None:
            return ""

        ordered_sections: list[tuple[str, str]] = []
        if record.task_meta:
            meta_lines = [
                f"- {key}: {_clean_text(value)}"
                for key, value in record.task_meta.items()
                if _clean_text(value)
            ]
            if meta_lines:
                ordered_sections.append(("TASK META", "\n".join(meta_lines)))

        def include(label: str, content: str) -> None:
            normalized = (content or "").strip()
            if normalized:
                ordered_sections.append((label, normalized))

        policy = (compression_policy or "generic").strip().lower()
        if policy == "code_generation":
            include("SPEC SUMMARY", record.spec_summary)
            include("RUNTIME PROFILE", record.runtime_profile_summary)
            include("CONTRACT SUMMARY", record.contract_summary)
            include("SOURCE CONTEXT", record.source_context_summary)
        elif policy == "iteration_rewrite":
            include("SPEC SUMMARY", record.spec_summary)
            include("SOURCE CONTEXT", record.source_context_summary)
            include("RUNTIME PROFILE", record.runtime_profile_summary)
            include("CONTRACT SUMMARY", record.contract_summary)
            include("LATEST CODE SUMMARY", record.latest_code_summary)
        elif policy == "qa_fix":
            include("SPEC SUMMARY", record.spec_summary)
            include("RUNTIME PROFILE", record.runtime_profile_summary)
            include("CONTRACT SUMMARY", record.contract_summary)
            include("LATEST CODE SUMMARY", record.latest_code_summary)
            include("LATEST QA FINDINGS", record.latest_qa_findings)
            include("REPAIR HISTORY", record.repair_history_summary)
        else:
            include("SPEC SUMMARY", record.spec_summary)
            include("SOURCE CONTEXT", record.source_context_summary)
            include("RUNTIME PROFILE", record.runtime_profile_summary)

        if record.decision_log:
            decision_limit = 3 if compact else 6
            include("DECISION LOG", "\n".join(f"- {item}" for item in record.decision_log[-decision_limit:]))

        if not ordered_sections:
            return ""

        rendered_parts = ["TASK MEMORY (shared task context)"]
        per_section_limit = 320 if compact else 700
        total_limit = 1800 if compact else 3600
        for label, content in ordered_sections:
            normalized = content.strip()
            if len(normalized) > per_section_limit:
                normalized = _head_tail(normalized, head=math.floor(per_section_limit * 0.7), tail=math.floor(per_section_limit * 0.2))
            rendered_parts.append(f"{label}\n{normalized}")

        rendered = "\n\n".join(rendered_parts).strip()
        if len(rendered) > total_limit:
            rendered = _head_tail(rendered, head=math.floor(total_limit * 0.7), tail=math.floor(total_limit * 0.2))
        return rendered


task_memory = TaskMemoryService()
