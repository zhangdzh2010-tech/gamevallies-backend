"""Per-stage generation telemetry for interactive short/full paths.

Persisted on quality_breakdown and task_memory so yield scripts can query
prompt_tokens, completion_tokens, stage_ms, queue_wait_ms, template_route,
and family/recipe ids without scraping logs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .p2_telemetry import emit as _p2_emit


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class StageRecord:
    name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    stage_ms: float = 0.0
    queue_wait_ms: float = 0.0
    step_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "prompt_tokens": int(self.prompt_tokens),
            "completion_tokens": int(self.completion_tokens),
            "stage_ms": round(self.stage_ms, 1),
            "queue_wait_ms": round(self.queue_wait_ms, 1),
            "step_key": self.step_key,
        }


class GenerationTelemetry:
    def __init__(self) -> None:
        self.stages: List[StageRecord] = []
        self.template_route: str = "MISS"
        self.family_id: Optional[str] = None
        self.recipe_id: Optional[str] = None
        self.subject: Optional[str] = None
        self.route_reason: str = ""
        self.short_path: bool = False
        self.diversity_action: str = ""
        self._open: Dict[str, float] = {}

    def set_route(self, decision: Any) -> None:
        self.template_route = getattr(decision, "route", None) or "MISS"
        self.family_id = getattr(decision, "family_id", None)
        self.recipe_id = getattr(decision, "recipe_id", None)
        self.subject = getattr(decision, "subject", None)
        self.route_reason = getattr(decision, "reason", "") or ""
        self.short_path = bool(getattr(decision, "uses_short_path", False))
        _p2_emit(
            "interactive_template_route",
            route=self.template_route,
            family=self.family_id,
            recipe=self.recipe_id,
            subject=self.subject,
            reason=self.route_reason,
        )

    def start(self, name: str) -> None:
        self._open[name] = time.perf_counter()

    def record(
        self,
        name: str,
        *,
        prompt: str = "",
        completion: str = "",
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        queue_wait_ms: float = 0.0,
        step_key: str = "",
        stage_ms: Optional[float] = None,
    ) -> StageRecord:
        started = self._open.pop(name, None)
        elapsed_ms = stage_ms if stage_ms is not None else (
            (time.perf_counter() - started) * 1000 if started is not None else 0.0
        )
        record = StageRecord(
            name=name,
            prompt_tokens=int(prompt_tokens if prompt_tokens is not None else estimate_tokens(prompt)),
            completion_tokens=int(
                completion_tokens if completion_tokens is not None else estimate_tokens(completion)
            ),
            stage_ms=elapsed_ms,
            queue_wait_ms=queue_wait_ms,
            step_key=step_key,
        )
        self.stages.append(record)
        _p2_emit(
            "interactive_stage",
            stage=name,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            stage_ms=record.stage_ms,
            queue_wait_ms=record.queue_wait_ms,
            template_route=self.template_route,
            family=self.family_id,
            recipe=self.recipe_id,
        )
        return record

    def totals(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": sum(stage.prompt_tokens for stage in self.stages),
            "completion_tokens": sum(stage.completion_tokens for stage in self.stages),
            "stage_ms": round(sum(stage.stage_ms for stage in self.stages), 1),
            "queue_wait_ms": round(sum(stage.queue_wait_ms for stage in self.stages), 1),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_route": self.template_route,
            "family_id": self.family_id,
            "recipe_id": self.recipe_id,
            "subject": self.subject,
            "route_reason": self.route_reason,
            "short_path": self.short_path,
            "diversity_action": self.diversity_action,
            "stages": [stage.to_dict() for stage in self.stages],
            **self.totals(),
        }

    def yield_fields(self) -> Dict[str, Any]:
        totals = self.totals()
        return {
            "template_route": self.template_route,
            "family_id": self.family_id,
            "recipe_id": self.recipe_id,
            "subject": self.subject,
            "prompt_tokens": totals["prompt_tokens"],
            "completion_tokens": totals["completion_tokens"],
            "stage_ms": totals["stage_ms"],
            "queue_wait_ms": totals["queue_wait_ms"],
        }
