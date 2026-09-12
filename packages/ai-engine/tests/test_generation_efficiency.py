import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from src.api.models import GameSpec, RunPipelineV2Request
from src.engine.desktop_runtime_shell import (
    accumulate_sim_time,
    shell_time_advance_contract_errors,
)
from src.engine.interactive_creation import normalize_interactive_request, run_interactive
from src.engine.interactive_diversity import (
    DiversityLedger,
    fingerprints_near_duplicate,
    plan_interactive_diversity,
)
from src.engine.interactive_families import RECIPES, get_recipe, recipes_for_family
from src.engine.interactive_router import route_interactive_template
from src.engine.interactive_short_path import (
    assemble_short_path_document,
    default_slots,
    extract_fill_payload,
)
from src.engine.template_registry import TemplateRegistry


class RouterFamilies(unittest.TestCase):
    def test_hit_common_science_recipes(self):
        cases = [
            ("作品类型：科学演示。制作小角度理想单摆演示，可调摆长L和重力g，周期T=2π√(L/g)。", "HIT", "time_integrator_1d", "pendulum", "physics"),
            ("作品类型：科学演示。制作欧姆定律交互演示，电压V和电阻R可调，电流按I=V/R计算。", "HIT", "param_formula_panel", "ohm_law", "physics"),
            ("作品类型：科学演示。制作自由落体实验，可调高度和重力g，显示v=gt。", "HIT", "time_integrator_1d", "free_fall", "physics"),
            ("作品类型：科学演示。制作理想气体状态方程，可调n、T、V，按PV=nRT显示压强。", "HIT", "param_formula_panel", "gas_law", "chem"),
            ("作品类型：科学演示。制作双波源干涉与波纹演示，可调振幅和波长。", "HIT", "field_or_wave_2d", "wave_interference", "physics"),
            ("作品类型：科学演示。制作半透膜渗透实验，两侧浓度可调，水流按浓度差流动。", "HIT", "compartment_flow", "osmosis", "bio"),
            ("作品类型：科学演示。制作酶活性随温度变化的示意曲线，可调温度和活化能。", "HIT", "param_formula_panel", "enzyme_temp", "bio"),
            ("做一个捕食者与猎物的种群变化模型。", "HIT", "compartment_flow", "population", "bio"),
        ]
        for brief, route, family, recipe, subject in cases:
            decision = route_interactive_template("science", brief)
            self.assertEqual(decision.route, route, brief)
            self.assertEqual(decision.family_id, family, brief)
            self.assertEqual(decision.recipe_id, recipe, brief)
            self.assertEqual(decision.subject, subject, brief)
            self.assertTrue(decision.uses_short_path)

    def test_miss_is_full_generate_not_failure(self):
        decision = route_interactive_template(
            "science",
            "作品类型：科学演示。制作平面镜反射光学演示，可调入射角，显示反射定律。",
        )
        self.assertEqual(decision.route, "MISS")
        self.assertFalse(decision.uses_short_path)
        self.assertIn(decision.reason, {"no_family_match", "ambiguous_family"})

    def test_tool_converter_does_not_force_enzyme_family(self):
        decision = route_interactive_template(
            "tool",
            "作品类型：工具。制作摄氏和华氏双向温度转换器。",
        )
        self.assertEqual(decision.route, "MISS")
        self.assertEqual(decision.reason, "tool_requires_strong_recipe")

    def test_soft_family_without_strong_recipe(self):
        decision = route_interactive_template("science", "做一个波动场的示意，可调参数。")
        self.assertIn(decision.route, {"SOFT", "MISS"})
        if decision.route == "SOFT":
            self.assertEqual(decision.family_id, "field_or_wave_2d")

    def test_never_force_wrong_family_when_ambiguous(self):
        decision = route_interactive_template(
            "science",
            "同时演示单摆周期和双波源干涉，并把渗透隔室画在旁边。",
        )
        self.assertEqual(decision.route, "MISS")
        self.assertEqual(decision.reason, "ambiguous_family")

    def test_subject_coverage_spans_physics_chem_bio(self):
        subjects = {recipe.subject for recipe in RECIPES.values()}
        self.assertEqual(subjects, {"physics", "chem", "bio"})
        self.assertTrue(recipes_for_family("param_formula_panel"))
        self.assertTrue(recipes_for_family("time_integrator_1d"))
        self.assertTrue(recipes_for_family("field_or_wave_2d"))
        self.assertTrue(recipes_for_family("compartment_flow"))


class ShellTimeAdvance(unittest.TestCase):
    def test_substep_frames_are_accumulated_not_discarded(self):
        sim, leftover, steps = accumulate_sim_time([0.008, 0.008, 0.008])
        self.assertEqual(steps, 1)
        self.assertGreater(sim, 0)
        self.assertLess(leftover, 1.0 / 60.0)

    def test_start_advances_and_pause_does_not(self):
        running = accumulate_sim_time([0.02, 0.02], running=True)
        paused = accumulate_sim_time([0.02, 0.02], running=False)
        self.assertGreater(running[2], 0)
        self.assertEqual(paused[2], 0)
        self.assertEqual(paused[0], 0.0)

    def test_assembled_shell_keeps_accumulator_contract(self):
        recipe = get_recipe("pendulum")
        plan = plan_interactive_diversity(
            family_id=recipe.family_id,
            recipe_id=recipe.id,
            title=recipe.title,
            formula=recipe.formula,
            variation_seed="shell-contract",
            ledger=DiversityLedger(),
        )
        html = assemble_short_path_document(
            recipe=recipe,
            slots=default_slots(recipe, plan, "单摆"),
            plan=plan,
        )
        self.assertEqual(shell_time_advance_contract_errors(html), [])
        self.assertIn("data-work-shell=\"desktop-runtime-v1\"", html)
        self.assertIn("acc +=", html)
        self.assertIn("btn-start", html)
        self.assertIn("WorkRuntime", html)


class DiversityFallback(unittest.TestCase):
    def test_near_duplicate_swaps_then_raises_then_falls_back(self):
        ledger = DiversityLedger()
        kwargs = dict(
            family_id="time_integrator_1d",
            recipe_id="pendulum",
            title="小角度理想单摆",
            formula="θ'' = -(g/L) sin θ",
            variation_seed="same-seed",
            ledger=ledger,
        )
        first = plan_interactive_diversity(**kwargs)
        self.assertEqual(first.action, "initial")
        self.assertFalse(first.fallback_to_full)

        second = plan_interactive_diversity(**kwargs)
        self.assertEqual(second.action, "swap_anchor")
        self.assertNotEqual(first.anchor, second.anchor)
        self.assertTrue(fingerprints_near_duplicate(first.fingerprint, second.fingerprint))
        self.assertFalse(second.fallback_to_full)

        third = plan_interactive_diversity(**kwargs)
        self.assertEqual(third.action, "raise_tier")
        self.assertFalse(third.fallback_to_full)

        fourth = plan_interactive_diversity(**kwargs)
        self.assertTrue(fourth.fallback_to_full)
        self.assertEqual(fourth.action, "fallback_full_generate")


class ShortPathAndRegistry(unittest.IsolatedAsyncioTestCase):
    def test_extract_fill_payload_and_defaults(self):
        payload = extract_fill_payload('```json\n{"title":"欧姆课堂","formula":"I=V/R"}\n```')
        self.assertEqual(payload["title"], "欧姆课堂")
        recipe = get_recipe("ohm_law")
        plan = plan_interactive_diversity(
            family_id=recipe.family_id,
            recipe_id=recipe.id,
            title=recipe.title,
            formula=recipe.formula,
            variation_seed="ohm",
            ledger=DiversityLedger(),
        )
        slots = default_slots(recipe, plan, "欧姆定律演示")
        html = assemble_short_path_document(recipe=recipe, slots=slots, plan=plan)
        self.assertIn("I = V / R", html)
        self.assertIn("param-V", html)

    def test_registry_mines_seed_worthy_to_staging_not_production(self):
        store = TemplateRegistry()
        entry = store.observe(
            family_id="time_integrator_1d",
            recipe_id="pendulum",
            subject="physics",
            template_route="HIT",
            fingerprint="abc",
            slots={"title": "单摆"},
            seed_worthy=True,
        )
        self.assertEqual(entry.status, "staging")
        self.assertFalse(store.promote_to_production("abc", allow_auto=False))
        self.assertEqual(store.staging()[0].status, "staging")
        self.assertTrue(store.promote_to_production("abc", allow_auto=True))
        self.assertEqual(store.snapshot()[0]["status"], "production")

    async def test_miss_brief_still_uses_full_logic_generate(self):
        request = normalize_interactive_request(RunPipelineV2Request(
            game_id="game", user_id="user", timeout_s=1800,
            raw_user_input="作品类型：科学演示。制作平面镜反射光学演示，可调入射角。",
            source_spec=GameSpec(game_type="interactive_experience", source_description="镜面"),
        ))
        html = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><h1>镜</h1><button>开始</button><script>1</script></body></html>'
        review = json.dumps({
            "artifact_kind": "science", "complete": True, "critical_issues": [],
            "scores": {k: 8 for k in ["scientific_correctness", "parameter_fidelity", "explanation_integrity", "visual_clarity"]},
            "evidence": {k: "ok" for k in ["scientific_correctness", "parameter_fidelity", "explanation_integrity", "visual_clarity"]},
            "issues": [],
        })
        with patch(
            "src.engine.interactive_creation.LLMClient.complete_with_truncation_retry",
            new=AsyncMock(side_effect=[html, review]),
        ) as llm, patch(
            "src.engine.interactive_creation.validate_interactive_html",
            new=AsyncMock(return_value={"ran": True, "passed": True, "issues": []}),
        ):
            result = await run_interactive(request)
        self.assertEqual(llm.call_args_list[0].kwargs["step_key"], "code_generate.full")
        self.assertEqual(result.quality_breakdown["template_route"], "MISS")
        self.assertTrue(result.quality_breakdown["seed_worthy"])


class YieldLedgerEfficiency(unittest.TestCase):
    def test_ledger_records_route_tokens_and_keeps_infra_exclusion(self):
        from run_generation_yield_batch import (
            ALL_CASES,
            EXTRA_CASES,
            extract_quality_outcome,
            is_infra_yield_row,
            ledger_row,
        )

        names = {case["name"] for case in EXTRA_CASES}
        self.assertIn("gas_law_science_cn", names)
        self.assertIn("osmosis_science_cn", names)
        self.assertIn("enzyme_temp_science_cn", names)
        self.assertTrue(any(case.get("expected_subject") == "chem" for case in EXTRA_CASES))
        self.assertTrue(any(case.get("expected_subject") == "bio" for case in EXTRA_CASES))
        families = {case.get("expected_family") for case in EXTRA_CASES if case.get("expected_family")}
        self.assertIn("param_formula_panel", families)
        self.assertIn("compartment_flow", families)

        outcome = extract_quality_outcome({
            "qualityBreakdown": {
                "artifact_kind": "science",
                "review_ran": True,
                "passed": True,
                "template_route": "HIT",
                "family_id": "time_integrator_1d",
                "recipe_id": "pendulum",
                "prompt_tokens": 120,
                "completion_tokens": 40,
            }
        })
        self.assertEqual(outcome["template_route"], "HIT")
        self.assertEqual(outcome["family_id"], "time_integrator_1d")
        self.assertEqual(outcome["prompt_tokens"], 120)
        row = ledger_row(
            run_id="r",
            run_index=1,
            case={"name": "pendulum_science_cn", "title": "Pendulum", "artifact_kind": "science"},
            result={
                "finalStatus": "succeeded",
                "elapsedS": 11.2,
                "qualityBreakdown": outcome["qualityBreakdown"],
            },
            base_url="https://example.test",
        )
        self.assertEqual(row["kind"], "science")
        self.assertEqual(row["template_route"], "HIT")
        self.assertEqual(row["elapsed_s"], 11.2)
        self.assertTrue(is_infra_yield_row({"finalStatus": "failed", "failureFamily": "infra_maintenance"}))
        self.assertGreater(len(ALL_CASES), 8)


if __name__ == "__main__":
    unittest.main()
