"""First slice of the Template Registry + candidate mining.

Lifecycle: observed (seed_worthy success) → candidate → staging.
Promotion to production is manual unless INTERACTIVE_TEMPLATE_AUTO_PROMOTE
is enabled. Auto-promote of whole-page HTML is never performed — only
skeleton metadata (family/recipe/slots) is stored.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

STATUSES = ("observed", "candidate", "staging", "production")


@dataclass
class RegistryEntry:
    family_id: str
    recipe_id: str
    subject: str
    template_route: str
    fingerprint: str
    slots: Dict[str, Any] = field(default_factory=dict)
    status: str = "observed"
    seed_worthy: bool = True
    observed_at: float = field(default_factory=time.time)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TemplateRegistry:
    def __init__(self) -> None:
        self._entries: List[RegistryEntry] = []
        self._lock = threading.Lock()

    def observe(
        self,
        *,
        family_id: str,
        recipe_id: str,
        subject: str,
        template_route: str,
        fingerprint: str,
        slots: Optional[Dict[str, Any]] = None,
        seed_worthy: bool = True,
    ) -> Optional[RegistryEntry]:
        if not seed_worthy or not family_id or not recipe_id:
            return None
        entry = RegistryEntry(
            family_id=family_id,
            recipe_id=recipe_id,
            subject=subject or "",
            template_route=template_route,
            fingerprint=fingerprint or "",
            slots=dict(slots or {}),
            status="observed",
        )
        # Immediate mine: seed_worthy successes become candidates.
        entry.status = "candidate"
        entry.notes = "mined_from_seed_worthy"
        with self._lock:
            self._entries.append(entry)
            # Auto-advance candidates to staging; production stays gated.
            entry.status = "staging"
            entry.notes = "auto_staging_awaiting_review"
        return entry

    def promote_to_production(self, fingerprint: str, *, allow_auto: bool = False) -> bool:
        if not allow_auto:
            return False
        with self._lock:
            for entry in self._entries:
                if entry.fingerprint == fingerprint and entry.status == "staging":
                    entry.status = "production"
                    entry.notes = "auto_promoted"
                    return True
        return False

    def staging(self) -> List[RegistryEntry]:
        with self._lock:
            return [entry for entry in self._entries if entry.status == "staging"]

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [entry.to_dict() for entry in self._entries]


registry = TemplateRegistry()
