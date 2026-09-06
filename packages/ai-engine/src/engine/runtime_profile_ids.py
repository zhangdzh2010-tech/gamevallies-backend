"""Canonical runtime profile identifiers and legacy aliases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Tuple

def _contract_path(name: str) -> Path:
    candidates = tuple(
        parent / "contracts" / "generation" / name
        for parent in Path(__file__).resolve().parents
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    checked = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"Unable to locate generation contract {name!r}. Checked: {checked}")


_CONTRACT = json.loads(_contract_path("runtime-profiles.json").read_text(encoding="utf-8"))

DEFAULT_RUNTIME_PROFILE_ID = str(_CONTRACT["defaultRuntimeProfileId"])

LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS = {
    str(key): str(value)
    for key, value in dict(_CONTRACT["legacyToCanonical"]).items()
}

CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS = {
    str(key): tuple(str(item) for item in value)
    for key, value in dict(_CONTRACT["canonicalToLegacy"]).items()
}


def normalize_runtime_profile_id(profile_id: str | None) -> str:
    normalized = str(profile_id or "").strip()
    if not normalized:
        return DEFAULT_RUNTIME_PROFILE_ID
    return LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS.get(normalized, normalized)


def runtime_profile_lookup_candidates(profile_id: str | None) -> Tuple[str, ...]:
    canonical = normalize_runtime_profile_id(profile_id)
    legacy = CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS.get(canonical, ())
    seen: list[str] = []
    for candidate in (canonical, *legacy):
        if candidate and candidate not in seen:
            seen.append(candidate)
    return tuple(seen)


def prompt_key_candidates_for_profile(profile_id: str | None) -> Tuple[str, ...]:
    return tuple(
        f"bundle.runtime.profile.{candidate}"
        for candidate in runtime_profile_lookup_candidates(profile_id)
    )


def canonical_runtime_profiles() -> Iterable[str]:
    return CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS.keys()
