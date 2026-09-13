from __future__ import annotations

from unittest import TestCase

from src.api.models import GameSpec, RunPipelineV2Request
from src.engine.desktop_runtime_shell import shell_time_advance_contract_errors
from src.engine.interactive_creation import interactive_system_prompt, normalize_interactive_request
from src.engine.interactive_diversity import (
    DiversityLedger,
    plan_interactive_diversity,
    visual_css_from_pack,
)
from src.engine.interactive_families import family_plugin_js, get_recipe
from src.engine.interactive_short_path import assemble_short_path_document, default_slots, fill_prompt
from src.engine.interactive_router import route_interactive_template
from src.engine.visual_pack_catalog import get_visual_pack


def _request(text: str) -> RunPipelineV2Request:
    return RunPipelineV2Request(game_id="g", user_id="u", raw_user_input=text)


def _assemble_ohm() -> str:
    recipe = get_recipe("ohm_law")
    plan = plan_interactive_diversity(
        family_id=recipe.family_id,
        recipe_id=recipe.id,
        title=recipe.title,
        formula=recipe.formula,
        variation_seed="flat-edu-ohm",
        ledger=DiversityLedger(),
    )
    return assemble_short_path_document(
        recipe=recipe,
        slots=default_slots(recipe, plan, "欧姆定律演示"),
        plan=plan,
    )


class FlatEduVisualPack(TestCase):
    def test_science_diversity_pins_clean_edu_even_on_raise_tier(self) -> None:
        ledger = DiversityLedger()
        kwargs = dict(
            family_id="param_formula_panel",
            recipe_id="ohm_law",
            title="欧姆定律",
            formula="I = V / R",
            variation_seed="flat-edu-diversity",
            ledger=ledger,
        )
        first = plan_interactive_diversity(**kwargs)
        second = plan_interactive_diversity(**kwargs)
        third = plan_interactive_diversity(**kwargs)
        self.assertEqual(first.visual_pack_id, "clean_edu")
        self.assertEqual(second.visual_pack_id, "clean_edu")
        self.assertEqual(third.visual_pack_id, "clean_edu")
        self.assertEqual(third.action, "raise_tier")

    def test_visual_css_is_flat_edu_not_frosted_neon(self) -> None:
        css = visual_css_from_pack(get_visual_pack("clean_edu") or {}, "lab_bench")
        self.assertIn("--work-canvas-bg:#f1f5f9", css)
        self.assertIn("--work-accent:#0f766e", css)
        self.assertIn("max-width:100%", css)
        self.assertIn("max-height:min(38vh,240px)", css)
        self.assertIn("min-height:clamp(100px,22vh,140px)", css)
        self.assertIn("font-size:clamp(14px,2.8vw,16px)", css)
        self.assertIn("border-radius:6px", css)
        self.assertNotIn("backdrop-filter", css)
        self.assertNotIn("box-shadow", css)
        self.assertNotIn("border-radius:999", css)
        self.assertNotIn("#0f172a", css.split("--work-ink")[0])

    def test_assembled_ohm_shell_keeps_contracts_and_flat_chrome(self) -> None:
        html = _assemble_ohm()
        self.assertEqual(shell_time_advance_contract_errors(html), [])
        self.assertIn('data-work-shell="desktop-runtime-v1"', html)
        self.assertIn('id="btn-start"', html)
        self.assertIn('id="work-canvas"', html)
        self.assertIn("acc +=", html)
        self.assertIn("--work-canvas-bg:#f1f5f9", html)
        self.assertIn("workTokens", html)
        self.assertNotIn("backdrop-filter", html)
        self.assertNotIn("ctx.fillStyle = '#0f172a'", html)
        self.assertNotIn("ctx.fillStyle = '#facc15'", html)

    def test_family_plugin_reads_tokens_instead_of_charcoal_fill(self) -> None:
        plugin = family_plugin_js("param_formula_panel", get_recipe("ohm_law"))
        self.assertIn("function workTokens()", plugin)
        self.assertIn("ctx.fillStyle = t.bg", plugin)
        self.assertNotIn("ctx.fillStyle = '#0f172a'", plugin)

    def test_miss_and_fill_prompts_forbid_neon_frost(self) -> None:
        science = interactive_system_prompt("science")
        tool = interactive_system_prompt("tool")
        self.assertIn("平面教材风", science)
        self.assertIn("backdrop-filter", science)
        self.assertIn("毛玻璃", science)
        self.assertIn("平面教材风", tool)
        recipe = get_recipe("ohm_law")
        plan = plan_interactive_diversity(
            family_id=recipe.family_id,
            recipe_id=recipe.id,
            title=recipe.title,
            formula=recipe.formula,
            variation_seed="flat-edu-fill",
            ledger=DiversityLedger(),
        )
        prompt = fill_prompt(
            kind="science",
            brief="欧姆定律演示",
            recipe=recipe,
            plan=plan,
            route=route_interactive_template("science", "作品类型：科学演示。制作欧姆定律交互演示，电压V和电阻R可调。"),
        )
        self.assertIn("不要描写霓虹", prompt)
        self.assertIn("rounded_rect", prompt)
        self.assertIn("no glass or frost", prompt.lower())

    def test_normalize_interactive_request_sets_clean_edu(self) -> None:
        normalized = normalize_interactive_request(
            _request("作品类型：科学演示。制作欧姆定律交互演示，电压V和电阻R可调。")
        )
        self.assertEqual(normalized.source_spec.visual_style.visual_pack, "clean_edu")
        self.assertEqual(normalized.source_spec.visual_style.art_style, "flat_edu")
        self.assertEqual(normalized.source_spec.visual_style.effects, [])
        self.assertIsInstance(normalized.source_spec, GameSpec)
