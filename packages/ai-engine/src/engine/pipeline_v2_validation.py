"""Validation extracted from pipeline_v2_runner.py."""

from __future__ import annotations
import logging
import re
from typing import Any, Optional
from ..api.models import GameRuntimeContract, GameSpec, QACheckError, QAResult
from .mobile_layout import has_short_edge_scaling
from .qa_pipeline import QAPipeline, SYNTAX_REPAIR_FAMILY
from .restart_entry import has_restart_entry
from .terminal_state import has_required_state_presence
from .storage_api_detection import contains_storage_api_usage
from .pipeline_v2_support import ProgressCallback, INPUT_EVENT_PATTERNS, FORBIDDEN_API_PATTERNS

logger = logging.getLogger(__name__)

class PipelineV2ValidationMixin:
    """Validation behavior; state remains owned by V2PipelineRunner."""

    async def _run_contract_qa_loop(
        self,
        *,
        code: str,
        spec: GameSpec,
        runtime_contract: GameRuntimeContract,
        prompt_bundle_snapshot: Optional[dict[str, Any]],
        progress_cb: ProgressCallback,
        game_id: str,
        user_id: str,
        max_retries: Optional[int] = None,
    ) -> QAResult:
        current_code = self.qa_pipeline._apply_deterministic_repairs(code)
        errors = self._validate_contract_bundle(current_code, runtime_contract)
        repair_attempts = 0
        if errors:
            syntax_errors = [
                error
                for error in errors
                if self.qa_pipeline._classify_error_family(error) == SYNTAX_REPAIR_FAMILY
            ]
            if syntax_errors:
                repaired_code = await self.qa_pipeline.repair_code(
                    current_code,
                    syntax_errors,
                    game_spec=spec,
                    runtime_contract=runtime_contract,
                    prompt_bundle_snapshot=prompt_bundle_snapshot,
                    fix_round=1,
                    max_fix_rounds=1,
                )
                repaired_code = self.qa_pipeline._apply_deterministic_repairs(repaired_code)
                repaired_errors = self._validate_contract_bundle(repaired_code, runtime_contract)
                if len(repaired_errors) < len(errors):
                    current_code = repaired_code
                    errors = repaired_errors
                    repair_attempts = 1
                elif repaired_code != current_code:
                    current_code = repaired_code
                    errors = repaired_errors
                    repair_attempts = 1

        if not errors:
            return QAResult(
                success=True,
                code=current_code,
                retries=repair_attempts,
                issue_list=self.qa_pipeline.build_issue_list([], []),
            )

        logger.info(
            "Contract QA found %s issue(s); contract-stage remediation is disabled, signaling full regeneration",
            len(errors),
        )
        return QAResult(
            success=False,
            code=current_code,
            retries=repair_attempts,
            last_errors=errors,
            needs_regeneration=True,
            issue_list=self.qa_pipeline.build_issue_list(errors, []),
        )


    def _validate_contract_bundle(
        self,
        code: str,
        runtime_contract: GameRuntimeContract,
    ) -> list[QACheckError]:
        result = self.qa_pipeline.check(code)
        errors = list(result.errors)
        errors.extend(self._validate_runtime_contract(code, runtime_contract))
        deduped: list[QACheckError] = []
        seen: set[tuple[str, str]] = set()
        for error in errors:
            signature = (error.type, error.message)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(error)
        return deduped


    def _validate_runtime_contract(
        self,
        code: str,
        runtime_contract: GameRuntimeContract,
    ) -> list[QACheckError]:
        errors: list[QACheckError] = []
        lower = (code or "").lower()
        has_canvas_2d_context = re.search(
            r"getcontext\s*\(\s*['\"]2d['\"](?:\s*,[^\)]*)?\)",
            code,
            re.IGNORECASE,
        ) is not None
        has_webgl_context = re.search(
            r"getcontext\s*\(\s*['\"](?:webgl|webgl2)['\"](?:\s*,[^\)]*)?\)",
            code,
            re.IGNORECASE,
        ) is not None

        if runtime_contract.canvas.requires_canvas_2d:
            if not has_canvas_2d_context:
                errors.append(QACheckError(
                    type="contract_canvas",
                    message="Runtime contract requires an explicit Canvas 2D context",
                    severity="error",
                ))
        elif not (has_canvas_2d_context or (getattr(runtime_contract.canvas, "allow_webgl", False) and has_webgl_context)):
            errors.append(QACheckError(
                type="contract_canvas",
                message="Runtime contract requires an explicit canvas rendering context (Canvas 2D or WebGL)",
                severity="error",
            ))

        if "viewport" not in lower:
            errors.append(QACheckError(
                type="contract_mobile",
                message="Runtime contract requires a mobile viewport meta tag",
                severity="error",
            ))

        orientation = self._resolve_contract_orientation(runtime_contract)
        if not has_short_edge_scaling(code, orientation=orientation):
            requirement = "landscape-first" if orientation == "landscape_first" else "portrait-first"
            errors.append(QACheckError(
                type="contract_mobile",
                message=f"Runtime contract requires {requirement} short-edge UI scaling",
                severity="error",
            ))

        if any(mode in ("touch", "pointer") for mode in runtime_contract.input.required_modes):
            has_primary_input = any(
                any(pattern in lower for pattern in INPUT_EVENT_PATTERNS.get(mode, ()))
                for mode in runtime_contract.input.required_modes
                if mode in ("touch", "pointer")
            )
            if not has_primary_input:
                errors.append(QACheckError(
                    type="contract_input",
                    message="Runtime contract requires primary touch or pointer gameplay handlers",
                    severity="error",
                ))

        for required_state in runtime_contract.state.required_states:
            normalized_required_state = str(required_state or "").strip().lower()
            if normalized_required_state in {"game_over", "level_complete"}:
                continue
            if not has_required_state_presence(code, required_state, runtime_contract):
                errors.append(QACheckError(
                    type="contract_state",
                    message=f"Runtime contract requires state '{required_state}'",
                    severity="error",
                ))

        if runtime_contract.gameplay.requires_restart_entry and not has_restart_entry(code):
            errors.append(QACheckError(
                type="contract_gameplay",
                message="Runtime contract requires a restart entry point",
                severity="error",
            ))

        for api_name in runtime_contract.safety.forbidden_apis:
            if self._contains_forbidden_api(code, api_name):
                errors.append(QACheckError(
                    type="contract_safety",
                    message=f"Runtime contract forbids API usage: {api_name}",
                    severity="error",
                ))

        return errors


    @staticmethod
    def _contains_forbidden_api(code: str, api_name: str) -> bool:
        normalized = (api_name or "").strip()
        if not normalized:
            return False

        if normalized in {"localStorage", "sessionStorage"}:
            return contains_storage_api_usage(code, normalized)

        pattern = FORBIDDEN_API_PATTERNS.get(normalized)
        if pattern:
            if normalized == "Function":
                return re.search(pattern, code) is not None
            return re.search(pattern, code, re.IGNORECASE) is not None

        escaped = re.escape(normalized)
        return re.search(rf"(?<![\w$]){escaped}(?![\w$])", code, re.IGNORECASE) is not None


    def _runtime_qa_errors(self, runtime_qa: Any, code: str) -> list[QACheckError]:
        errors: list[QACheckError] = [
            QACheckError(type="runtime_qa", message=f"Runtime JS error: {message}", severity="error")
            for message in runtime_qa.js_errors[:3]
        ]
        if not runtime_qa.canvas_renders:
            errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected that the canvas never rendered",
                severity="error",
            ))
        input_handlers = (
            set(getattr(runtime_qa, "registered_input_handlers", []) or [])
            | set(getattr(runtime_qa, "direct_input_handlers", []) or [])
            | set(getattr(runtime_qa, "triggered_input_handlers", []) or [])
        )
        static_input_map = self.qa_pipeline._extract_input_handlers(code)
        has_static_inputs = any(
            static_input_map.get(family)
            for family in ("touch", "pointer", "mouse", "keyboard", "sensor")
        )
        if not input_handlers and not has_static_inputs:
            errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected no registered user input handlers",
                severity="error",
            ))
        visible_change_detected = bool(
            getattr(runtime_qa, "canvas_changed_after_input", False)
            or getattr(runtime_qa, "dom_changed_after_input", False)
        )
        if not visible_change_detected:
            errors.append(QACheckError(
                type="runtime_qa",
                message="Runtime QA detected no visible state change after user interaction",
                severity="error",
            ))
        return errors


    def _runtime_qa_unavailable_errors(self, runtime_qa: Any, code: str) -> list[QACheckError]:
        unavailable_kind = str(getattr(runtime_qa, "unavailable_kind", "") or "").strip().lower()
        unavailable_phase = str(getattr(runtime_qa, "unavailable_phase", "") or "").strip().lower()
        if unavailable_kind != "timeout":
            return []

        errors: list[QACheckError] = []
        unavailable_reason = getattr(runtime_qa, "unavailable_reason", None) or "runtime QA timed out"
        static_input_map = self.qa_pipeline._extract_input_handlers(code)
        has_static_inputs = any(
            static_input_map.get(family)
            for family in ("touch", "pointer", "mouse", "keyboard", "sensor")
        )
        input_handlers = (
            set(getattr(runtime_qa, "registered_input_handlers", []) or [])
            | set(getattr(runtime_qa, "direct_input_handlers", []) or [])
            | set(getattr(runtime_qa, "triggered_input_handlers", []) or [])
        )
        visible_change_detected = bool(
            getattr(runtime_qa, "canvas_changed_after_input", False)
            or getattr(runtime_qa, "dom_changed_after_input", False)
        )
        interaction_performed = bool(getattr(runtime_qa, "interaction_performed", False))

        if unavailable_phase in {"interaction", "collect"}:
            if not input_handlers and not has_static_inputs:
                errors.append(QACheckError(
                    type="runtime_qa",
                    message="Runtime QA detected no registered user input handlers",
                    severity="error",
                ))
            if input_handlers or interaction_performed:
                message = (
                    "Runtime QA dispatched synthetic input but the game did not finish the first interaction quickly; "
                    "keep first-input handlers lightweight and make them produce an immediate visible state change"
                )
                if visible_change_detected:
                    message = (
                        "Runtime QA timed out while collecting post-interaction signals after input dispatch; "
                        "keep the first interaction lightweight and avoid long synchronous work on input handlers"
                    )
            else:
                message = (
                    "Runtime QA timed out during synthetic interaction; register gameplay handlers during boot "
                    "and make the first input cause an immediate visible state change"
                )
            errors.append(QACheckError(
                type="runtime_qa",
                message=message,
                severity="error",
            ))
        else:
            return []

        for message in list(getattr(runtime_qa, "js_errors", []) or [])[:3]:
            errors.insert(0, QACheckError(
                type="runtime_qa",
                message=f"Runtime JS error: {message}",
                severity="error",
            ))

        logger.warning(
            "Treating runtime QA timeout as repairable runtime failure: phase=%s reason=%s",
            unavailable_phase,
            unavailable_reason,
        )
        return errors


    @staticmethod
    def _build_text_artifact(
        *,
        artifact_type: str,
        payload: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "artifact_type": artifact_type,
            "content_type": "text/html",
            "payload": payload,
            "metadata": metadata or {},
        }


    @staticmethod
    def _build_json_artifact(
        *,
        artifact_type: str,
        payload: dict[str, Any],
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "artifact_type": artifact_type,
            "content_type": "application/json",
            "payload": payload,
            "metadata": metadata or {},
        }


    @staticmethod
    def _serialize_errors(errors: list[QACheckError]) -> list[dict[str, Any]]:
        return [
            {
                "type": enriched.type,
                "message": enriched.message,
                "severity": enriched.severity,
                "family": enriched.family or "generic",
                "blocking": bool(enriched.blocking if enriched.blocking is not None else True),
                "repairHint": enriched.repair_hint or "",
                **(
                    {"location": enriched.location.model_dump(exclude_none=True)}
                    if enriched.location is not None
                    else {}
                ),
            }
            for enriched in (QAPipeline.enrich_issue(error) for error in errors)
        ]


    @staticmethod
    def _build_runtime_qa_warning(runtime_qa: Any) -> dict[str, Any]:
        unavailable_reason = getattr(runtime_qa, "unavailable_reason", None) or "runtime QA unavailable"
        return {
            "type": "runtime_qa_unavailable",
            "severity": "warning",
            "family": "runtime_startup",
            "blocking": False,
            "repairHint": "Retry runtime QA later or inspect the runtime environment if Playwright/browser infrastructure is unavailable.",
            "message": f"Runtime QA unavailable: {unavailable_reason}",
            "kind": getattr(runtime_qa, "unavailable_kind", None),
            "phase": getattr(runtime_qa, "unavailable_phase", None),
            "softFailed": True,
        }


    def _serialize_runtime_qa(
        self,
        runtime_qa: Any,
        errors: list[QACheckError],
    ) -> dict[str, Any]:
        return {
            "ran": bool(getattr(runtime_qa, "ran", False)),
            "canvasRenders": bool(getattr(runtime_qa, "canvas_renders", False)),
            "canvasChangedAfterInput": bool(getattr(runtime_qa, "canvas_changed_after_input", False)),
            "domChangedAfterInput": bool(getattr(runtime_qa, "dom_changed_after_input", False)),
            "interactionPerformed": bool(getattr(runtime_qa, "interaction_performed", False)),
            "registeredInputHandlers": list(getattr(runtime_qa, "registered_input_handlers", []) or []),
            "directInputHandlers": list(getattr(runtime_qa, "direct_input_handlers", []) or []),
            "triggeredInputHandlers": list(getattr(runtime_qa, "triggered_input_handlers", []) or []),
            "jsErrors": list(getattr(runtime_qa, "js_errors", []) or []),
            "fps": float(getattr(runtime_qa, "fps", 0.0) or 0.0),
            "loadTimeMs": int(getattr(runtime_qa, "load_time_ms", 0) or 0),
            "unavailableReason": getattr(runtime_qa, "unavailable_reason", None),
            "unavailableKind": getattr(runtime_qa, "unavailable_kind", None),
            "unavailablePhase": getattr(runtime_qa, "unavailable_phase", None),
            "phaseMetrics": dict(getattr(runtime_qa, "phase_metrics", {}) or {}),
            "errors": self._serialize_errors(errors),
        }
