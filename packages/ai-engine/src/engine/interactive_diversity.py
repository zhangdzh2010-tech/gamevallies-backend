"""Diversity gate for interactive short-path presentations.

Near-duplicate fingerprints swap creative anchors / visual packs, then
raise the diversity tier, then fall back to full generate. Templates stay
skeleton-only; this only varies L3 slots and visual accents.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .visual_pack_catalog import select_visual_pack, visual_pack_direction_lines

CREATIVE_ANCHORS = (
    "lab_bench",
    "classroom_board",
    "field_notebook",
    "night_observatory",
    "workshop_table",
    "greenhouse",
)

ANCHOR_COPY = {
    "lab_bench": "实验台侧光，仪器读数优先",
    "classroom_board": "黑板示意，粉笔标注公式",
    "field_notebook": "田野速写本边注",
    "night_observatory": "深色星图底板",
    "workshop_table": "木作台与量具",
    "greenhouse": "温室叶片与水珠",
}


def _stable_index(seed: str, modulus: int) -> int:
    if modulus <= 0:
        return 0
    digest = hashlib.sha256((seed or "").encode()).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def content_stem(
    *,
    family_id: str,
    recipe_id: str,
    title: str,
    formula: str,
) -> str:
    material = "|".join(
        [
            (family_id or "").lower(),
            (recipe_id or "").lower(),
            (title or "").strip().lower(),
            (formula or "").strip().lower(),
        ]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def presentation_fingerprint(
    *,
    family_id: str,
    recipe_id: str,
    title: str,
    formula: str,
    visual_pack_id: str,
    anchor: str,
    summary: str = "",
) -> str:
    stem = content_stem(family_id=family_id, recipe_id=recipe_id, title=title, formula=formula)
    accent = hashlib.sha256(f"{visual_pack_id}|{anchor}|{(summary or '')[:80]}".encode()).hexdigest()[:8]
    return f"{stem}{accent}"


def fingerprints_near_duplicate(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    # Same family/recipe/title/formula stem is a near-duplicate reskin.
    return left[:12] == right[:12]


@dataclass
class DiversityPlan:
    variation_seed: str
    visual_pack: Dict[str, Any]
    visual_pack_id: str
    anchor: str
    anchor_copy: str
    tier: str = "standard"
    action: str = "initial"
    fingerprint: str = ""
    fallback_to_full: bool = False
    direction_lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variation_seed": self.variation_seed,
            "visual_pack_id": self.visual_pack_id,
            "anchor": self.anchor,
            "tier": self.tier,
            "action": self.action,
            "fingerprint": self.fingerprint,
            "fallback_to_full": self.fallback_to_full,
        }


class DiversityLedger:
    """In-process recent fingerprints so consecutive same-family fills diverge."""

    def __init__(self, limit: int = 64) -> None:
        self._limit = limit
        self._seen: List[str] = []
        self._lock = threading.Lock()

    def recent(self) -> List[str]:
        with self._lock:
            return list(self._seen)

    def remember(self, fingerprint: str) -> None:
        if not fingerprint:
            return
        with self._lock:
            self._seen.append(fingerprint)
            if len(self._seen) > self._limit:
                self._seen = self._seen[-self._limit:]

    def exact_seen(self, fingerprint: str) -> bool:
        return any(prior == fingerprint for prior in self.recent())

    def stem_seen(self, fingerprint: str) -> bool:
        return any(fingerprints_near_duplicate(fingerprint, prior) for prior in self.recent())

    def collides(self, fingerprint: str) -> bool:
        return self.stem_seen(fingerprint)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


_LEDGER = DiversityLedger()


def plan_interactive_diversity(
    *,
    family_id: str,
    recipe_id: str,
    title: str,
    formula: str,
    variation_seed: str,
    generation_tier: str = "standard",
    summary: str = "",
    ledger: Optional[DiversityLedger] = None,
) -> DiversityPlan:
    store = ledger or _LEDGER
    seed = variation_seed or f"{family_id}:{recipe_id}"
    pack = select_visual_pack(
        game_type="educational",
        theme=f"science {family_id} {recipe_id} classroom",
        generation_tier=generation_tier,
        variation_seed=seed,
    )
    anchor = CREATIVE_ANCHORS[_stable_index(seed + ":anchor", len(CREATIVE_ANCHORS))]
    plan = DiversityPlan(
        variation_seed=seed,
        visual_pack=pack,
        visual_pack_id=str(pack.get("id") or "clean_edu"),
        anchor=anchor,
        anchor_copy=ANCHOR_COPY[anchor],
        tier=generation_tier,
        direction_lines=visual_pack_direction_lines(pack),
    )
    plan.fingerprint = presentation_fingerprint(
        family_id=family_id,
        recipe_id=recipe_id,
        title=title,
        formula=formula,
        visual_pack_id=plan.visual_pack_id,
        anchor=plan.anchor,
        summary=summary,
    )
    if not store.stem_seen(plan.fingerprint):
        store.remember(plan.fingerprint)
        return plan

    # 1) Swap creative anchor on a near-duplicate stem.
    swapped = CREATIVE_ANCHORS[(CREATIVE_ANCHORS.index(anchor) + 1) % len(CREATIVE_ANCHORS)]
    plan.anchor = swapped
    plan.anchor_copy = ANCHOR_COPY[swapped]
    plan.action = "swap_anchor"
    plan.fingerprint = presentation_fingerprint(
        family_id=family_id,
        recipe_id=recipe_id,
        title=title,
        formula=formula,
        visual_pack_id=plan.visual_pack_id,
        anchor=plan.anchor,
        summary=summary,
    )
    if not store.exact_seen(plan.fingerprint):
        store.remember(plan.fingerprint)
        return plan

    # 2) Raise visual tier / pick another pack.
    raised = "showcase" if generation_tier != "showcase" else "standard"
    alt_pack = select_visual_pack(
        game_type="educational",
        theme=f"science {family_id} {recipe_id} classroom observatory",
        generation_tier=raised,
        variation_seed=seed + ":raised",
    )
    plan.visual_pack = alt_pack
    plan.visual_pack_id = str(alt_pack.get("id") or plan.visual_pack_id)
    plan.tier = raised
    plan.action = "raise_tier"
    plan.direction_lines = visual_pack_direction_lines(alt_pack)
    plan.fingerprint = presentation_fingerprint(
        family_id=family_id,
        recipe_id=recipe_id,
        title=title,
        formula=formula,
        visual_pack_id=plan.visual_pack_id,
        anchor=plan.anchor,
        summary=summary,
    )
    if not store.exact_seen(plan.fingerprint):
        store.remember(plan.fingerprint)
        return plan

    # 3) Exact presentation still repeats — caller falls back to full generate.
    plan.fallback_to_full = True
    plan.action = "fallback_full_generate"
    return plan


def visual_css_from_pack(pack: Dict[str, Any], anchor: str) -> str:
    palette = list(pack.get("palette") or ["#0f172a", "#0ea5e9", "#f8fafc"])
    bg = palette[0] if palette else "#0f172a"
    accent = palette[1] if len(palette) > 1 else "#0ea5e9"
    ink = palette[-1] if palette else "#f8fafc"
    font = pack.get("fontFamily") or "'Helvetica Neue', Arial, sans-serif"
    return (
        "html,body{box-sizing:border-box;max-width:100%;overflow-x:hidden}"
        f"body{{margin:0;padding:8px;font:16px/1.4 {font};background:{ink};color:{bg}}}"
        "h1{margin:4px 0;font-size:1.25rem}"
        "p{margin:4px 0}"
        f"canvas{{display:block;max-width:100%;max-height:min(38vh,240px);width:100%;"
        f"height:auto;background:{bg};border-radius:8px}}"
        "form[data-work-controls]{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:8px}"
        f"button{{background:{accent};color:{ink};border:0;border-radius:8px;padding:6px 10px}}"
        "input,label,output{margin:4px 6px;padding:6px 8px}"
        f"details{{margin:4px 0;border-left:3px solid {accent};padding-left:8px}}"
        f"body::after{{content:'{anchor}';position:absolute;left:-9999px}}"
    )
