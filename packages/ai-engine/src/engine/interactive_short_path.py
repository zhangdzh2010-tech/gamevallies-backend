"""HIT/SOFT short path: fill L3 presentation slots, then assemble the shell.

The LLM is asked for JSON/fragment copy, not a full HTML document. Fill
output is never used as the playable page — even when the model returns a
complete HTML document — so gas_law/population cannot ship a canvas-less or
script-less artifact. JSON slots are merged when present; otherwise recipe
defaults are assembled.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from .contract_digest import contract_digest
from .desktop_runtime_shell import render_shell, shell_time_advance_contract_errors
from .interactive_diversity import DiversityPlan, visual_css_from_pack
from .interactive_families import Recipe, family_plugin_js, render_param_controls
from .interactive_router import RouteDecision

FILL_STEP_KEY = "code_generate.template_fill"

SLOT_KEYS = (
    "title",
    "summary",
    "formula",
    "assumptions",
    "limits",
    "caption",
    "scene_note",
)


def default_slots(recipe: Recipe, plan: DiversityPlan, brief: str) -> Dict[str, str]:
    headline = (brief or "").split("\n")[0].strip()
    return {
        "title": recipe.title,
        "summary": headline or recipe.title,
        "formula": recipe.formula,
        "assumptions": recipe.assumptions,
        "limits": recipe.limits,
        "caption": plan.anchor_copy,
        "scene_note": plan.anchor,
    }


def extract_fill_payload(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    blob = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", blob, re.S)
    if fenced:
        parsed = _parse_slot_object(fenced.group(1))
        if parsed:
            return parsed
    for match in re.finditer(r"\{[^{}]{0,4000}\}", blob):
        parsed = _parse_slot_object(match.group(0))
        if parsed and any(key in parsed for key in SLOT_KEYS):
            return parsed
    start, end = blob.find("{"), blob.rfind("}")
    if start < 0 or end <= start:
        return None
    parsed = _parse_slot_object(blob[start : end + 1])
    if parsed and any(key in parsed for key in SLOT_KEYS):
        return parsed
    return None


def _parse_slot_object(blob: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def looks_like_full_html(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"<html\b", text, re.I) and re.search(r"</html\s*>", text, re.I))


def merge_slots(base: Dict[str, str], payload: Optional[Dict[str, Any]]) -> Dict[str, str]:
    merged = dict(base)
    if not payload:
        return merged
    creative = payload.get("creative") if isinstance(payload.get("creative"), dict) else {}
    for key in SLOT_KEYS:
        value = payload.get(key) or creative.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    return merged


def assemble_short_path_document(
    *,
    recipe: Recipe,
    slots: Dict[str, str],
    plan: DiversityPlan,
) -> str:
    html = render_shell(
        title=slots.get("title") or recipe.title,
        summary=slots.get("summary") or recipe.title,
        formula=slots.get("formula") or recipe.formula,
        assumptions=slots.get("assumptions") or recipe.assumptions,
        limits=slots.get("limits") or recipe.limits,
        param_controls_html=render_param_controls(recipe.params),
        family_script=family_plugin_js(recipe.family_id, recipe),
        visual_css=visual_css_from_pack(plan.visual_pack, plan.anchor),
        family_id=recipe.family_id,
        recipe_id=recipe.id,
        subject=recipe.subject,
        visual_pack_id=plan.visual_pack_id,
        variation_seed=plan.variation_seed,
        readout_html='<output id="work-readout" data-work-readout></output>',
    )
    errors = shell_time_advance_contract_errors(html)
    if errors:
        raise ValueError("assembled shell failed time-advance contract: " + "; ".join(errors))
    return html


def fill_prompt(
    *,
    kind: str,
    brief: str,
    recipe: Recipe,
    plan: DiversityPlan,
    route: RouteDecision,
) -> str:
    digest = contract_digest(kind=kind, recipe=recipe)
    directions = "\n".join(plan.direction_lines)
    return (
        f"{digest}\n\n"
        f"用户创意：\n{brief}\n\n"
        f"路由={route.route} 家族={recipe.family_id} 配方={recipe.id} "
        f"锚点={plan.anchor} visual_pack={plan.visual_pack_id} seed={plan.variation_seed}\n"
        f"{directions}\n\n"
        "只输出 JSON，不要 HTML、不要 Markdown。填写展示文案槽位，不要改运动方程或控件 id：\n"
        '{"title":"...","summary":"...","formula":"...","assumptions":"...","limits":"...",'
        '"caption":"...","scene_note":"..."}\n'
        "公式必须与配方科学一致；不要换成错误家族的模型。"
    )


def fill_system_prompt(kind: str) -> str:
    extra = "科学正确性优先于文案创意。" if kind == "science" else "保持工具语义，不要做成游戏。"
    return (
        "你是桌面交互作品的展示文案填写器。根据 ContractDigest 与配方填写 JSON 槽位。"
        "不要输出完整 HTML。不要发明与配方冲突的公式。"
        + extra
    )
