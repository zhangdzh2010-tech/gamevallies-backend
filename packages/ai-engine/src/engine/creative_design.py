"""Bounded creative planning inside the existing intent-parse request.

No extra LLM call, history store or subjective novelty score is introduced.
The returned plan is evidence for design/review, never a replacement for the brief.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def creative_design_contract(variation_seed: str | None) -> str:
    seed = hashlib.sha256((variation_seed or "").encode()).hexdigest()[:12]
    return f"""
CREATIVE DESIGN EXTENSION (additional keys are permitted in the same JSON object):
Preserve the user's explicit mechanics, controls, difficulty, audience and exclusions.
For an open brief, consider three feasible concepts differing in player decisions or
rules, not just theme/colors. For a precise brief or faithful recreation, use one
faithful concept; never add a twist that contradicts the request.
Select one concept that fits the brief and can be implemented as one complete small
game. Use at most one optional signature twist. Do not add networking or external assets.
Return creative_design: {{"candidates":[{{"core_loop":"short player action and feedback",
"input":"control","goal":"goal","signature_rule":"distinctive rule"}}],
"selected_index":0,"selection_reason":"brief fit and feasibility"}}.
Use 1-3 candidates, <=120 characters per value. The selected candidate MUST agree
with top-level core_mechanic, input_method, win_condition and special_rules.
Also return lose_condition and scoring as short rule descriptions. Do not impose
survival, three lives or collection scoring on puzzles or untimed games. If lives
are explicitly requested, return lives (integer 1-99); otherwise omit it.
Variation anchor: {seed}. Use this only to vary unspecified design choices.
Return JSON only, with the original slot fields and this extension.
""".strip()


def normalize_creative_design(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    candidates = value.get("candidates")
    index = value.get("selected_index")
    if (not isinstance(candidates, list) or not 1 <= len(candidates) <= 3
            or type(index) is not int or not 0 <= index < len(candidates)):
        return None
    cleaned = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return None
        item = {}
        for key in ("core_loop", "input", "goal", "signature_rule"):
            text = candidate.get(key)
            if not isinstance(text, str) or not text.strip() or len(text) > 240:
                return None
            item[key] = re.sub(r"\s+", " ", text.strip())
        cleaned.append(item)
    reason = value.get("selection_reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        return None
    return {"version": "1", "candidates": cleaned, "selected_index": index,
            "selection_reason": reason.strip()}


def gameplay_fingerprint(spec: Any) -> str:
    """Exact normalized mechanism fingerprint; not a semantic novelty judgement."""
    fields = {
        "mechanics": [m.model_dump() for m in spec.core_mechanics],
        "rules": spec.rules.model_dump(),
        "special_rules": sorted(spec.special_rules or []),
        "loop": spec.intent_summary,
    }
    design = normalize_creative_design(getattr(spec, "creative_design", None))
    if design:
        fields["selected"] = design["candidates"][design["selected_index"]]
    normalized = re.sub(r"\s+", " ", json.dumps(fields, sort_keys=True, ensure_ascii=False).lower())
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]


def core_playability_contract(spec: Any) -> str:
    design = normalize_creative_design(getattr(spec, "creative_design", None))
    selected = design["candidates"][design["selected_index"]] if design else None
    # Rules and brief already appear in the structured design. Avoid repeating
    # them: repeated prompt requirements consume output/context budget.
    evidence = {"selected_concept": selected} if selected else {}
    return "\n".join([
        "CORE PLAYABILITY CONTRACT:",
        "The original user request wins over generated design suggestions or template defaults.",
        "Implement start -> player input -> visible state/feedback -> objective or ongoing loop -> restart first.",
        "Each advertised mechanic must execute; no placeholder controls or decorative-only gameplay.",
        "The game runs in an opaque-origin sandbox: guard localStorage/sessionStorage with try/catch and use in-memory fallback.",
        "Embed assets or draw them procedurally; no remote scripts, fetch, WebSocket, nested frames or worker dependencies.",
        "Keep one complete core loop and the requested signature rule. Add visual polish only after these work.",
        "During repair preserve controls, objectives and signature mechanics; fix the reported defect locally.",
        "Do not substitute survival/collection for a puzzle, strategy or untimed objective.",
        json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
    ])
