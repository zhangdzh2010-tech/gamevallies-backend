"""Resolve v2 prompt bundles into concrete prompt layers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from ..api.models import PromptBundleSnapshot
from .prompt_store import (
    PromptConfigError,
    get_default_runtime_profile,
    get_prompt,
    get_prompt_bundle,
    get_runtime_profile,
)
from .runtime_profile_ids import (
    DEFAULT_RUNTIME_PROFILE_ID,
    normalize_runtime_profile_id,
    prompt_key_candidates_for_profile,
)

BUNDLE_SLOT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "locked_contract": ("bundle.runtime.locked_contract",),
    "product_policy": ("bundle.product.policy",),
    "intent_parse": ("bundle.product.intent_parse", "prompt.intent_parse_system"),
    "logic_generate": ("bundle.product.logic_generate",),
    "repair_syntax_structural": ("bundle.repair.syntax_structural",),
}


def _first_required_text(keys: Iterable[str]) -> tuple[str, str]:
    for key in keys:
        value = get_prompt(key)
        if value:
            return key, value
    joined_keys = ", ".join(keys)
    raise PromptConfigError(f"Missing required prompt config(s): {joined_keys}")


def _profile_prompt(runtime_profile: str) -> tuple[str, str]:
    for bundle_key in prompt_key_candidates_for_profile(runtime_profile):
        value = get_prompt(bundle_key)
        if value:
            return bundle_key, value
    raise PromptConfigError(
        f"Missing required prompt config(s): {', '.join(prompt_key_candidates_for_profile(runtime_profile))}"
    )


def _default_runtime_profile_id() -> str:
    profile = get_default_runtime_profile()
    if isinstance(profile, dict) and profile.get("id"):
        return normalize_runtime_profile_id(str(profile["id"]))
    raise PromptConfigError("No enabled runtime profile is configured")


def _resolved_entry(
    key: Optional[str],
    content: Optional[str],
    *,
    source_keys: Optional[list[str]] = None,
) -> dict[str, Any]:
    entry = {
        "key": key,
        "content": content or "",
    }
    if source_keys:
        entry["source_keys"] = [item for item in source_keys if item]
    return entry


def _append_sections(*sections: Optional[str]) -> str:
    deduped: list[str] = []
    seen: set[str] = set()
    for section in sections:
        normalized = (section or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return "\n\n".join(deduped)


def _format_override_payload(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                rendered = json.dumps(item, ensure_ascii=False, sort_keys=True)
            else:
                rendered = str(item)
            lines.append(f"- {key}: {rendered}")
        return "\n".join(lines)
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def resolve_prompt_bundle_snapshot(
    snapshot: PromptBundleSnapshot,
    *,
    runtime_profile: Optional[str] = None,
) -> PromptBundleSnapshot:
    base_layers = dict(snapshot.layers or {})
    selected_profile = normalize_runtime_profile_id(str(
        runtime_profile
        or base_layers.get("profile_few_shot")
        or _default_runtime_profile_id()
    ).strip() or DEFAULT_RUNTIME_PROFILE_ID)

    bundle_record = get_prompt_bundle(snapshot.bundle_id, snapshot.bundle_version)
    runtime_profile_record = get_runtime_profile(selected_profile)
    resolved_prompts: dict[str, Any] = {}

    for slot in ("locked_contract", "product_policy", "intent_parse", "logic_generate"):
        key, value = _first_required_text(BUNDLE_SLOT_CANDIDATES[slot])
        source_keys = [key]
        bundle_overlay = ""
        if slot == "locked_contract":
            bundle_overlay = _format_override_payload(
                (bundle_record or {}).get("locked_contract_override")
            )
            if bundle_overlay:
                source_keys.append(
                    f"prompt_bundle:{snapshot.bundle_id}@{snapshot.bundle_version}.locked_contract_override"
                )
        elif slot == "product_policy":
            bundle_overlay = _format_override_payload((bundle_record or {}).get("product_policy"))
            if bundle_overlay:
                source_keys.append(
                    f"prompt_bundle:{snapshot.bundle_id}@{snapshot.bundle_version}.product_policy"
                )
        else:
            slot_override = _format_override_payload(
                ((bundle_record or {}).get("profile_overrides") or {}).get(slot)
            )
            if slot_override:
                bundle_overlay = "BUNDLE SLOT OVERRIDES\n" + slot_override
                source_keys.append(
                    f"prompt_bundle:{snapshot.bundle_id}@{snapshot.bundle_version}.profile_overrides.{slot}"
                )
        resolved_prompts[slot] = _resolved_entry(
            key,
            _append_sections(value, bundle_overlay),
            source_keys=source_keys,
        )

    key, value = _profile_prompt(selected_profile)
    profile_overlay = _format_override_payload((runtime_profile_record or {}).get("few_shot_prompt"))
    profile_sources = [key]
    if profile_overlay:
        profile_sources.append(f"runtime_profile_catalog:{selected_profile}.few_shot_prompt")
    resolved_prompts["profile_few_shot"] = _resolved_entry(
        key,
        _append_sections(value, profile_overlay),
        source_keys=profile_sources,
    )

    for slot in ("repair_syntax_structural",):
        key, value = _first_required_text(BUNDLE_SLOT_CANDIDATES[slot])
        repair_playbook = _format_override_payload((bundle_record or {}).get("repair_playbook"))
        source_keys = [key]
        repair_overlay = ""
        if repair_playbook:
            repair_overlay = "BUNDLE REPAIR PLAYBOOK\n" + repair_playbook
            source_keys.append(
                f"prompt_bundle:{snapshot.bundle_id}@{snapshot.bundle_version}.repair_playbook"
            )
        resolved_prompts[slot] = _resolved_entry(
            key,
            _append_sections(value, repair_overlay),
            source_keys=source_keys,
        )

    next_layers = {
        **base_layers,
        "profile_few_shot": selected_profile,
        "resolved_prompts": resolved_prompts,
    }

    return snapshot.model_copy(
        update={
            "resolved_at": snapshot.resolved_at or datetime.now(timezone.utc).isoformat(),
            "layers": next_layers,
        }
    )
