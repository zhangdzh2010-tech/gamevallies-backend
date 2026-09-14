import json
import os
import re
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))

from src.api.models import GameSpec, RunPipelineV2Request
from src.engine.desktop_runtime_shell import (
    CONVERTER_CONTROL_SEARCH,
    accumulate_sim_time,
    converter_shell_contract_errors,
    shell_time_advance_contract_errors,
)
from src.engine.interactive_creation import (
    ensure_full_path_chrome,
    normalize_interactive_request,
    run_interactive,
    validate_interactive_html,
)
from src.engine.interactive_diversity import (
    DiversityLedger,
    fingerprints_near_duplicate,
    plan_interactive_diversity,
)
from src.engine.interactive_families import (
    CONVERTER_FAMILY_ID,
    ENZYME_DEFAULT_EA_KJ,
    ENZYME_TEMP_MAX_C,
    ENZYME_TEMP_MIN_C,
    OSMOSIS_START_VIN,
    OSMOSIS_START_VOUT,
    RECIPES,
    convert_bidirectional,
    enzyme_activity_peak_celsius,
    enzyme_activity_rate,
    family_plugin_js,
    get_recipe,
    osmosis_volume_step,
    pendulum_bob_center,
    photosynthesis_light_rays,
    photosynthesis_oxygen_rate,
    plane_mirror_rays,
    recipes_for_family,
    wave_superposition,
)
from src.engine.interactive_short_path import (
    assemble_short_path_document,
    default_slots,
    extract_fill_payload,
    fill_prompt,
    looks_like_full_html,
)
from src.engine.interactive_router import route_interactive_template
from src.engine.template_registry import TemplateRegistry


class RouterFamilies(unittest.TestCase):
    def test_hit_common_science_recipes(self):
        cases = [
            ("作品类型：科学演示。制作小角度理想单摆演示，可调摆长L和重力g，周期T=2π√(L/g)。", "HIT", "time_integrator_1d", "pendulum", "physics"),
            ("作品类型：科学演示。制作欧姆定律交互演示，电压V和电阻R可调，电流按I=V/R计算。", "HIT", "param_formula_panel", "ohm_law", "physics"),
            ("作品类型：科学演示。制作自由落体实验，可调高度和重力g，显示v=gt。", "HIT", "time_integrator_1d", "free_fall", "physics"),
            ("作品类型：科学演示。制作理想气体状态方程，可调n、T、V，按PV=nRT显示压强。", "HIT", "param_formula_panel", "gas_law", "chem"),
            ("作品类型：科学演示。制作双波源干涉与波纹演示，可调振幅和波长。", "HIT", "field_or_wave_2d", "wave_interference", "physics"),
            ("作品类型：科学演示。制作平面镜反射光学演示，可调入射角，入射光指向镜面交点、反射光离开交点，显示反射定律。", "HIT", "geometric_ray_2d", "mirror_optics", "physics"),
            ("作品类型：科学演示。制作半透膜渗透实验，两侧浓度可调，水流按浓度差流动。", "HIT", "compartment_flow", "osmosis", "bio"),
            ("作品类型：科学演示。制作酶活性随温度变化的示意曲线，可调温度和活化能。", "HIT", "param_formula_panel", "enzyme_temp", "bio"),
            ("作品类型：科学演示。做一个光合作用实验，可调光照强度和二氧化碳浓度，观察氧气产生速率，展示简化公式和假设。不要游戏闯关。", "HIT", "param_formula_panel", "photosynthesis_rate", "bio"),
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
            "作品类型：科学演示。制作三棱镜色散与透镜成像对比演示，可调折射率。",
        )
        self.assertEqual(decision.route, "MISS")
        self.assertFalse(decision.uses_short_path)
        self.assertIn(decision.reason, {"no_family_match", "ambiguous_family"})
        self.assertIsNone(decision.family_id)

    def test_tool_converter_hits_converter_family_not_enzyme(self):
        cases = [
            (
                "作品类型：工具。制作摄氏和华氏双向温度转换器，输入值和单位后点击转换，正确处理负数、小数及无效输入。",
                "temp_c_f",
            ),
            (
                "作品类型：工具。制作米与英尺双向单位换算工具，输入数值后转换，处理小数和无效输入，不要游戏玩法。",
                "length_m_ft",
            ),
        ]
        for brief, recipe_id in cases:
            decision = route_interactive_template("tool", brief)
            self.assertEqual(decision.route, "HIT", brief)
            self.assertEqual(decision.family_id, CONVERTER_FAMILY_ID, brief)
            self.assertEqual(decision.recipe_id, recipe_id, brief)
            self.assertTrue(decision.uses_short_path, brief)
            self.assertNotEqual(decision.recipe_id, "enzyme_temp", brief)

    def test_tool_counter_still_requires_strong_recipe(self):
        decision = route_interactive_template(
            "tool",
            "作品类型：工具。制作一个可加减的计数器，有重置。",
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
        self.assertTrue(recipes_for_family("geometric_ray_2d"))
        self.assertTrue(recipes_for_family("bidirectional_converter"))


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
        inlined = html.replace(' data-work-family-script="true"', '', 1).replace('id="work-sim-time"', 'id="clock"', 1)
        self.assertTrue(shell_time_advance_contract_errors(inlined))
        self.assertIn('data-work-family-script="true"', html)
        self.assertIn('data-work-runtime-script="true"', html)
        self.assertIn('id="work-sim-time"', html)
        family_at = html.find('data-work-family-script="true"')
        runtime_at = html.find('data-work-runtime-script="true"')
        self.assertLess(family_at, runtime_at)
        self.assertGreater(family_at, html.find('id="work-canvas"'))


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
            game_id="game", user_id="user", timeout_s=1800, artifact_kind="science",
            raw_user_input="作品类型：科学演示。制作三棱镜色散演示，可调入射角和折射率。",
            source_spec=GameSpec(
                game_type="interactive_experience",
                artifact_kind="science",
                source_description="作品类型：科学演示。制作三棱镜色散演示，可调入射角和折射率。",
            ),
        ))
        html = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><h1>镜</h1><button>开始</button><script>1</script></body></html>'
        review = json.dumps({
            "artifact_kind": "science", "complete": True, "critical_issues": [],
            "scores": {k: 8 for k in ["scientific_correctness", "parameter_fidelity", "explanation_integrity", "visual_clarity"]},
            "evidence": {k: "Fixture assertion for routing test" for k in ["scientific_correctness", "parameter_fidelity", "explanation_integrity", "visual_clarity"]},
            "issues": [],
        })
        async def llm_side_effect(**kwargs):
            if kwargs.get("step_key") == "code_review":
                return review
            return html
        with patch(
            "src.engine.interactive_creation.LLMClient.complete_with_truncation_retry",
            new=AsyncMock(side_effect=llm_side_effect),
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
        self.assertIn("geometric_ray_2d", families)
        self.assertIn("field_or_wave_2d", families)
        self.assertIn("bidirectional_converter", families)
        names = {case["name"] for case in EXTRA_CASES}
        self.assertIn("unit_converter_cn", names)
        self.assertIn("temp_converter_cn", names)

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


def _assemble_recipe(recipe_id: str, brief: str = "") -> str:
    recipe = get_recipe(recipe_id)
    plan = plan_interactive_diversity(
        family_id=recipe.family_id,
        recipe_id=recipe.id,
        title=recipe.title,
        formula=recipe.formula,
        variation_seed=f"assemble-{recipe_id}",
        ledger=DiversityLedger(),
    )
    return assemble_short_path_document(
        recipe=recipe,
        slots=default_slots(recipe, plan, brief or recipe.title),
        plan=plan,
    )


def _has_executable_script(html: str) -> bool:
    return bool(re.search(r"<script\b|\son(?:click|input|change|submit|keydown|keyup)\s*=", html, re.I))


class ShortPathShellRegressions(unittest.IsolatedAsyncioTestCase):
    def test_fill_html_is_not_used_as_slots_and_shell_still_assembles(self):
        broken = (
            "<!DOCTYPE html><html><head></head><body><h1>broken gas</h1>"
            "<script>document.getElementById('missing').getContext('2d');</script></body></html>"
        )
        self.assertTrue(looks_like_full_html(broken))
        self.assertIsNone(extract_fill_payload(broken))
        html = _assemble_recipe("gas_law", "理想气体状态方程")
        self.assertIn('id="work-canvas"', html)
        self.assertTrue(_has_executable_script(html))
        self.assertLess(html.lower().find('id="work-canvas"'), html.find("getContext"))
        self.assertNotIn("getElementById('missing')", html)
        self.assertEqual(shell_time_advance_contract_errors(html), [])

    def test_extract_slots_from_html_wrapped_json(self):
        wrapped = (
            "<!DOCTYPE html><html><body><p>ignore</p>"
            '{"title":"课堂气体","summary":"PV=nRT 示意"}'
            "</body></html>"
        )
        payload = extract_fill_payload(wrapped)
        self.assertEqual(payload["title"], "课堂气体")

    def test_full_path_chrome_injects_title_without_inventing_controls(self):
        bare = "<!DOCTYPE html><html><head></head><body><p></p><script>1</script></body></html>"
        repaired = ensure_full_path_chrome(
            bare, "作品类型：工具。制作摄氏和华氏双向温度转换器。"
        )
        self.assertIn("<title>", repaired)
        self.assertIn("<h1", repaired)
        self.assertIn("温度转换", repaired)
        already = '<!DOCTYPE html><html><head></head><body><h1>已有标题</h1></body></html>'
        self.assertEqual(ensure_full_path_chrome(already, "摄氏华氏转换"), already)
        self.assertNotIn("<input", repaired)

    def test_assembled_gas_and_population_have_executable_script_and_canvas(self):
        for recipe_id in ("gas_law", "population", "photosynthesis_rate"):
            html = _assemble_recipe(recipe_id)
            self.assertTrue(re.search(r"</html\s*>\s*$", html, re.I), recipe_id)
            self.assertTrue(_has_executable_script(html), recipe_id)
            self.assertIn('id="work-canvas"', html)
            self.assertIn("getContext", html)
            self.assertLess(
                html.lower().find('id="work-canvas"'),
                html.find("getContext"),
                recipe_id,
            )
            self.assertEqual(shell_time_advance_contract_errors(html), [], recipe_id)

    async def test_assembled_gas_law_params_change_readout_and_canvas_context_works(self):
        html = _assemble_recipe(
            "gas_law",
            "作品类型：科学演示。制作理想气体状态方程，可调n、T、V，按PV=nRT显示压强。",
        )
        report = await validate_interactive_html(html)
        self.assertFalse(report.get("js_errors"), report)
        self.assertTrue(report["passed"], report["issues"])
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            probe = await page.evaluate(
                """() => {
                  const canvas = document.getElementById('work-canvas');
                  const ctx = canvas && canvas.getContext('2d');
                  const readout = document.getElementById('work-readout');
                  const before = readout ? readout.textContent : '';
                  const vol = document.getElementById('param-V');
                  vol.value = vol.max;
                  vol.dispatchEvent(new Event('input', {bubbles:true}));
                  const after = readout ? readout.textContent : '';
                  return {
                    hasCanvas: !!canvas,
                    hasCtx: !!(ctx && ctx.fillRect),
                    before: before,
                    after: after,
                    changed: before !== after
                  };
                }"""
            )
            await browser.close()
        self.assertTrue(probe["hasCanvas"])
        self.assertTrue(probe["hasCtx"])
        self.assertTrue(probe["changed"], probe)

    async def test_assembled_population_start_advances_sim_and_script_executes(self):
        html = _assemble_recipe("population", "做一个捕食者与猎物的种群变化模型。")
        self.assertTrue(_has_executable_script(html))
        report = await validate_interactive_html(html)
        self.assertFalse(report.get("js_errors"), report)
        self.assertTrue(report["passed"], report["issues"])
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            before = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime.simTime(),
                  readout: document.getElementById('work-readout').textContent,
                  ctx: !!document.getElementById('work-canvas').getContext('2d')
                })"""
            )
            await page.click("#btn-start")
            await page.wait_for_timeout(180)
            after = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime.simTime(),
                  readout: document.getElementById('work-readout').textContent
                })"""
            )
            await browser.close()
        self.assertTrue(before["ctx"])
        self.assertGreater(after["t"], before["t"])
        self.assertNotEqual(after["readout"], before["readout"])

    async def test_short_path_ignores_broken_fill_html_for_gas_law(self):
        request = normalize_interactive_request(RunPipelineV2Request(
            game_id="game", user_id="user", timeout_s=1800, artifact_kind="science",
            raw_user_input="作品类型：科学演示。制作理想气体状态方程，可调n、T、V，按PV=nRT显示压强。",
            source_spec=GameSpec(
                game_type="interactive_experience",
                artifact_kind="science",
                source_description="作品类型：科学演示。制作理想气体状态方程，可调n、T、V，按PV=nRT显示压强。",
            ),
        ))
        broken = (
            "<!DOCTYPE html><html><head></head><body><h1>broken</h1>"
            "<script>document.getElementById('missing').getContext('2d');</script></body></html>"
        )
        review = json.dumps({
            "artifact_kind": "science", "complete": True, "critical_issues": [],
            "scores": {k: 8 for k in ["scientific_correctness", "parameter_fidelity", "explanation_integrity", "visual_clarity"]},
            "evidence": {k: "Fixture assertion for routing test" for k in ["scientific_correctness", "parameter_fidelity", "explanation_integrity", "visual_clarity"]},
            "issues": [],
        })

        async def llm_side_effect(**kwargs):
            if kwargs.get("step_key") == "code_review":
                return review
            return broken

        with patch(
            "src.engine.interactive_creation.LLMClient.complete_with_truncation_retry",
            new=AsyncMock(side_effect=llm_side_effect),
        ) as llm, patch(
            "src.engine.interactive_creation.validate_interactive_html",
            new=AsyncMock(return_value={"ran": True, "passed": True, "issues": []}),
        ):
            result = await run_interactive(request)
        self.assertEqual(llm.call_args_list[0].kwargs["step_key"], "code_generate.template_fill")
        self.assertIn('id="work-canvas"', result.html_code)
        self.assertIn('data-recipe="gas_law"', result.html_code)
        self.assertTrue(_has_executable_script(result.html_code))
        self.assertNotIn("getElementById('missing')", result.html_code)
        self.assertEqual(result.quality_breakdown["template_route"], "HIT")
        self.assertEqual(result.runtime_qa_report["generationAttempts"]["fullGenerationCalls"], 0)


class ResidualFamilyContracts(unittest.IsolatedAsyncioTestCase):
    def test_wave_superposition_uses_opposite_phase_only_on_second_source(self):
        in_phase = wave_superposition(12, 0.4, amplitude1=0.6, amplitude2=0.6, wavelength=80, phase=0)
        shifted = wave_superposition(12, 0.4, amplitude1=0.6, amplitude2=0.6, wavelength=80, phase=1.2)
        opposite = wave_superposition(12, 0.4, amplitude1=0.6, amplitude2=0.6, wavelength=80, phase=3.141592653589793)
        self.assertAlmostEqual(in_phase, 2 * 0.6 * __import__("math").sin(2 * __import__("math").pi * 12 / 80 - 0.4))
        self.assertNotAlmostEqual(in_phase, shifted)
        self.assertAlmostEqual(opposite, 0.0, places=6)
        plugin = family_plugin_js("field_or_wave_2d", get_recipe("wave_interference"))
        self.assertIn("param-lambda", plugin)
        self.assertIn("param-phi", plugin)
        self.assertIn("k*x - time + phi", plugin)
        self.assertNotIn("2*Math.PI*x/lam - phi) + A2*Math.sin(2*Math.PI*x/lam - phi)", plugin)

    def test_photosynthesis_rays_converge_on_the_leaf(self):
        import math
        scene = photosynthesis_light_rays(720, 220)
        sx, sy = scene["sun"]
        lx, ly = scene["leaf"]
        self.assertGreater(sx, lx)
        self.assertLess(sy, ly)
        self.assertEqual(len(scene["rays"]), 5)
        mid = scene["rays"][2]
        mid_end = mid["end"]
        self.assertLess(
            math.hypot(mid_end[0] - lx, mid_end[1] - ly),
            math.hypot(mid["start"][0] - lx, mid["start"][1] - ly),
        )
        self.assertAlmostEqual(scene["aim"], math.atan2(ly - sy, lx - sx), places=9)
        for ray in scene["rays"]:
            self.assertLess(
                math.hypot(ray["end"][0] - lx, ray["end"][1] - ly),
                math.hypot(sx - lx, sy - ly) * 0.45,
            )
        plugin = family_plugin_js("param_formula_panel", get_recipe("photosynthesis_rate"))
        self.assertIn("sunX + Math.cos(ang)*reach", plugin)
        self.assertIn("fillText('光', lightCx, lightCy)", plugin)

    def test_pendulum_bob_center_is_the_string_end(self):
        import math
        pivot = (360.0, 16.0)
        length = 120.0
        theta = 0.35
        x, y = pendulum_bob_center(theta, pivot=pivot, length_px=length)
        self.assertAlmostEqual(x, pivot[0] + length * math.sin(theta))
        self.assertAlmostEqual(y, pivot[1] + length * math.cos(theta))
        rest = pendulum_bob_center(0.0, pivot=pivot, length_px=length)
        self.assertAlmostEqual(rest[0], pivot[0])
        self.assertAlmostEqual(rest[1], pivot[1] + length)
        plugin = family_plugin_js("time_integrator_1d", get_recipe("pendulum"))
        self.assertIn("ctx.lineTo(x,yb)", plugin)
        self.assertIn("ctx.arc(x,yb,10,0,Math.PI*2)", plugin)
        self.assertIn("bob center = string end", plugin)

    def test_plane_mirror_rays_keep_incident_toward_and_reflected_away(self):
        import math
        rays = plane_mirror_rays(math.radians(30), hit=(200.0, 100.0), length=80.0)
        ix, iy = rays["incident_from"]
        hx, hy = rays["hit"]
        rx, ry = rays["reflected_to"]
        nx, ny = rays["normal_to"]
        self.assertLess(ix, hx)
        self.assertLess(rx, hx)
        self.assertLess(iy, hy)
        self.assertGreater(ry, hy)
        self.assertAlmostEqual(ny, hy)
        self.assertLess(nx, hx)

        def angle_between(a, b):
            na = math.hypot(*a)
            nb = math.hypot(*b)
            return math.acos(max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (na * nb))))

        back_incident = (ix - hx, iy - hy)
        out_reflected = (rx - hx, ry - hy)
        normal_dir = (nx - hx, ny - hy)
        self.assertAlmostEqual(angle_between(back_incident, normal_dir), math.radians(30), places=6)
        self.assertAlmostEqual(angle_between(out_reflected, normal_dir), math.radians(30), places=6)
        self.assertGreater(hx - ix, 0)
        self.assertLess(rx - hx, 0)

    def test_assembled_wave_and_optics_keep_required_controls(self):
        wave = _assemble_recipe("wave_interference", "双波源干涉")
        optics = _assemble_recipe("mirror_optics", "平面镜反射")
        fall = _assemble_recipe("free_fall", "自由落体")
        for html in (wave, optics, fall):
            self.assertTrue(re.search(r"</html\s*>\s*$", html, re.I))
            self.assertEqual(shell_time_advance_contract_errors(html), [])
        self.assertIn('id="param-lambda"', wave)
        self.assertIn('id="param-phi"', wave)
        self.assertIn('id="param-theta"', optics)
        self.assertIn("angleRad", optics)
        self.assertIn("var angleRad = 30 * Math.PI / 180", optics)
        self.assertIn("法线", optics)
        self.assertIn("地面", fall)

    async def test_assembled_wave_phase_and_lambda_change_the_waveform(self):
        html = _assemble_recipe("wave_interference", "作品类型：科学演示。制作双波源干涉与波纹演示，可调振幅和波长。")
        report = await validate_interactive_html(html)
        self.assertFalse(report.get("js_errors"), report)
        self.assertTrue(report["passed"], report["issues"])
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            probe = await page.evaluate(
                """() => {
                  const canvas = document.getElementById('work-canvas');
                  const ctx = canvas.getContext('2d');
                  function snap(){
                    WorkFamily.draw(ctx, canvas);
                    return canvas.toDataURL();
                  }
                  const before = snap();
                  const phi = document.getElementById('param-phi');
                  const lam = document.getElementById('param-lambda');
                  phi.value = phi.max;
                  phi.dispatchEvent(new Event('input', {bubbles:true}));
                  const afterPhi = snap();
                  lam.value = lam.min;
                  lam.dispatchEvent(new Event('input', {bubbles:true}));
                  const afterLam = snap();
                  return {
                    hasPhi: !!phi,
                    hasLambda: !!lam,
                    phiChanged: before !== afterPhi,
                    lambdaChanged: afterPhi !== afterLam,
                    readout: document.getElementById('work-readout').textContent
                  };
                }"""
            )
            await browser.close()
        self.assertTrue(probe["hasPhi"])
        self.assertTrue(probe["hasLambda"])
        self.assertTrue(probe["phiChanged"], probe)
        self.assertTrue(probe["lambdaChanged"], probe)
        self.assertIn("φ=", probe["readout"])

    async def test_assembled_mirror_optics_geometry_and_live_interaction(self):
        html = _assemble_recipe(
            "mirror_optics",
            "作品类型：科学演示。制作平面镜反射光学演示，可调入射角，显示反射定律。",
        )
        report = await validate_interactive_html(html)
        self.assertFalse(report.get("js_errors"), report)
        self.assertTrue(report["passed"], report["issues"])
        self.assertFalse(report.get("angleEvidence"), report.get("angleEvidence"))
        clipped = [
            item for item in report.get("canvasTextEvidence") or []
            if item.get("type") == "canvas_text_clipped" and "法线" in str(item.get("text") or "")
        ]
        self.assertFalse(clipped, clipped)
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            before = await page.evaluate("() => window.WorkRuntime.simTime()")
            await page.click("#btn-start")
            await page.wait_for_timeout(200)
            probe = await page.evaluate(
                """() => {
                  const theta = document.getElementById('param-theta');
                  const before = document.getElementById('work-readout').textContent;
                  theta.value = '55';
                  theta.dispatchEvent(new Event('input', {bubbles:true}));
                  return {
                    t: window.WorkRuntime.simTime(),
                    before: before,
                    after: document.getElementById('work-readout').textContent
                  };
                }"""
            )
            await browser.close()
        self.assertGreater(probe["t"], before)
        self.assertNotEqual(probe["before"], probe["after"])
        self.assertIn("55", probe["after"])

    async def test_assembled_free_fall_keeps_visual_labels_and_motion(self):
        html = _assemble_recipe("free_fall", "作品类型：科学演示。制作自由落体实验，可调高度和重力g，显示v=gt。")
        report = await validate_interactive_html(html)
        self.assertFalse(report.get("js_errors"), report)
        self.assertTrue(report["passed"], report["issues"])
        self.assertIn("地面", html)
        self.assertIn("y=", html)
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            await page.click("#btn-start")
            await page.wait_for_timeout(200)
            readout = await page.evaluate("() => document.getElementById('work-readout').textContent")
            await browser.close()
        self.assertIn("v=", readout)

    def test_enzyme_rate_is_non_monotonic_with_interior_max(self):
        peak_t, peak_rate = enzyme_activity_peak_celsius()
        low = enzyme_activity_rate(ENZYME_TEMP_MIN_C, ENZYME_DEFAULT_EA_KJ)
        mid = enzyme_activity_rate(37, ENZYME_DEFAULT_EA_KJ)
        high = enzyme_activity_rate(ENZYME_TEMP_MAX_C, ENZYME_DEFAULT_EA_KJ)
        self.assertGreaterEqual(peak_t, 37.0)
        self.assertLessEqual(peak_t, 50.0)
        self.assertGreater(peak_rate, low)
        self.assertGreater(peak_rate, high)
        self.assertGreater(mid, low)
        self.assertGreater(mid, high)
        self.assertLess(high, 0.25 * peak_rate)
        rising = enzyme_activity_rate(20) < enzyme_activity_rate(35)
        falling = enzyme_activity_rate(60) > enzyme_activity_rate(80)
        self.assertTrue(rising)
        self.assertTrue(falling)
        plugin = family_plugin_js("param_formula_panel", get_recipe("enzyme_temp"))
        self.assertIn("enzymeRate", plugin)
        self.assertIn("1 / Tk - 1 / Tref", plugin)
        self.assertIn("(Tc - 48) / 4", plugin)
        self.assertNotIn("(0.3+t)*s.rate", plugin)
        self.assertNotIn("4000 * arr * denature", plugin)
        prompt = fill_prompt(
            kind="science",
            brief="酶活性随温度变化",
            recipe=get_recipe("enzyme_temp"),
            plan=plan_interactive_diversity(
                family_id="param_formula_panel",
                recipe_id="enzyme_temp",
                title="酶",
                formula=get_recipe("enzyme_temp").formula,
                variation_seed="enzyme-fill",
                ledger=DiversityLedger(),
            ),
            route=route_interactive_template("science", "作品类型：科学演示。制作酶活性随温度变化的示意曲线，可调温度和活化能。"),
        )
        self.assertIn("先升后降", prompt)

    def test_osmosis_start_advances_volumes_and_draw_is_null_safe(self):
        self.assertNotEqual(OSMOSIS_START_VIN, OSMOSIS_START_VOUT)
        vin, vout, flux = osmosis_volume_step(
            OSMOSIS_START_VIN, OSMOSIS_START_VOUT, 0.8, 0.2, 0.15, 0.25
        )
        self.assertGreater(flux, 0)
        self.assertGreater(vin, OSMOSIS_START_VIN)
        self.assertLess(vout, OSMOSIS_START_VOUT)
        plugin = family_plugin_js("compartment_flow", get_recipe("osmosis"))
        self.assertIn("typeof ctx.clearRect !== 'function'", plugin)
        self.assertIn("ensureOsmosisOffset", plugin)
        self.assertIn("半透膜", plugin)
        self.assertIn("内侧", plugin)
        self.assertIn("外侧", plugin)
        self.assertIn("vin = 0.32", plugin)
        self.assertIn("vout = 0.58", plugin)
        self.assertNotIn("vin = 0.45; vout = 0.45", plugin)

    async def test_assembled_enzyme_curve_and_start_motion(self):
        html = _assemble_recipe(
            "enzyme_temp",
            "作品类型：科学演示。制作酶活性随温度变化的示意曲线，可调温度和活化能。",
        )
        self.assertIn("enzymeRate", html)
        self.assertIn("T/°C", html)
        report = await validate_interactive_html(html)
        self.assertFalse(report.get("js_errors"), report)
        self.assertTrue(report["passed"], report["issues"])
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            curve = await page.evaluate(
                """() => {
                  const samples = [0, 20, 37, 44, 60, 80].map(T => {
                    const slider = document.getElementById('param-T');
                    slider.value = String(T);
                    slider.dispatchEvent(new Event('input', {bubbles:true}));
                    return {T: T, rate: WorkFamily.applyParams().rate};
                  });
                  return samples;
                }"""
            )
            before = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime.simTime(),
                  snap: document.getElementById('work-canvas').toDataURL(),
                  readout: document.getElementById('work-readout').textContent
                })"""
            )
            await page.click("#btn-start")
            await page.wait_for_timeout(220)
            after = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime.simTime(),
                  snap: document.getElementById('work-canvas').toDataURL(),
                  readout: document.getElementById('work-readout').textContent,
                  nullSafe: (function(){
                    try { WorkFamily.draw(null, null); WorkFamily.draw(undefined, document.getElementById('work-canvas')); return true; }
                    catch (err) { return String(err); }
                  })()
                })"""
            )
            await browser.close()
        rates = {row["T"]: row["rate"] for row in curve}
        self.assertGreater(rates[37], rates[0])
        self.assertGreater(rates[44], rates[80])
        self.assertGreater(rates[37], rates[80])
        self.assertGreater(after["t"], before["t"])
        self.assertTrue(after["snap"] != before["snap"] or after["readout"] != before["readout"], after)
        self.assertIs(after["nullSafe"], True)

    async def test_assembled_osmosis_start_advances_volumes_and_null_safe_draw(self):
        html = _assemble_recipe(
            "osmosis",
            "作品类型：科学演示。制作半透膜渗透实验，两侧浓度可调，水流按浓度差流动。",
        )
        self.assertIn("半透膜", html)
        self.assertIn("内侧", html)
        report = await validate_interactive_html(html)
        self.assertFalse(report.get("js_errors"), report)
        self.assertTrue(report["passed"], report["issues"])
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            before = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime.simTime(),
                  readout: document.getElementById('work-readout').textContent,
                  snap: document.getElementById('work-canvas').toDataURL()
                })"""
            )
            await page.click("#btn-start")
            await page.wait_for_timeout(220)
            after = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime.simTime(),
                  readout: document.getElementById('work-readout').textContent,
                  snap: document.getElementById('work-canvas').toDataURL(),
                  nullSafe: (function(){
                    try { WorkFamily.draw(null, null); WorkFamily.draw({}, document.getElementById('work-canvas')); return true; }
                    catch (err) { return String(err); }
                  })()
                })"""
            )
            await browser.close()
        self.assertGreater(after["t"], before["t"])
        self.assertNotEqual(after["readout"], before["readout"], after)
        self.assertIn("Vin=", after["readout"])
        self.assertIn("Vout=", after["readout"])
        self.assertTrue(after["snap"] != before["snap"] or after["readout"] != before["readout"])
        self.assertIs(after["nullSafe"], True)

    def test_photosynthesis_rate_saturates_and_plugin_tracks_time(self):
        dark = photosynthesis_oxygen_rate(0, 400)
        dim = photosynthesis_oxygen_rate(10, 400)
        mid = photosynthesis_oxygen_rate(40, 400)
        bright = photosynthesis_oxygen_rate(100, 400)
        low_co2 = photosynthesis_oxygen_rate(40, 100)
        high_co2 = photosynthesis_oxygen_rate(40, 800)
        self.assertEqual(dark, 0.0)
        self.assertGreater(mid, dim)
        self.assertGreater(bright, mid)
        self.assertGreater(high_co2, low_co2)
        self.assertLess(bright, 1.0)
        plugin = family_plugin_js("param_formula_panel", get_recipe("photosynthesis_rate"))
        self.assertIn("photosynthesis_rate", plugin)
        self.assertIn("ΣO₂", plugin)
        self.assertIn("t=' + phase.toFixed(2)", plugin)
        self.assertIn("oxygen += compute().rate * dt", plugin)
        self.assertIn("ellipse", plugin)
        self.assertIn("var leafX = w*0.42, leafY = h*0.56", plugin)
        self.assertIn("var sunX = w*0.86, sunY = h*0.16", plugin)
        self.assertIn("Math.atan2(dy, dx)", plugin)
        self.assertIn("ctx.fillText('光', lightCx, lightCy)", plugin)
        self.assertIn("textBaseline = 'middle'", plugin)
        self.assertNotIn("ctx.moveTo(14, 12 + ray*4)", plugin)
        prompt = fill_prompt(
            kind="science",
            brief="光合作用实验",
            recipe=get_recipe("photosynthesis_rate"),
            plan=plan_interactive_diversity(
                family_id="param_formula_panel",
                recipe_id="photosynthesis_rate",
                title="光合",
                formula=get_recipe("photosynthesis_rate").formula,
                variation_seed="photo-fill",
                ledger=DiversityLedger(),
            ),
            route=route_interactive_template(
                "science",
                "作品类型：科学演示。做一个光合作用实验，可调光照强度和二氧化碳浓度，观察氧气产生速率。",
            ),
        )
        self.assertIn("饱和型", prompt)

    async def test_assembled_photosynthesis_start_advances_sim_readout_and_canvas(self):
        brief = (
            "作品类型：科学演示。做一个光合作用实验，可调光照强度和二氧化碳浓度，"
            "观察氧气产生速率，展示简化公式和假设。不要游戏闯关。"
        )
        html = _assemble_recipe("photosynthesis_rate", brief)
        self.assertEqual(shell_time_advance_contract_errors(html), [])
        self.assertIn('data-recipe="photosynthesis_rate"', html)
        self.assertIn('id="param-I"', html)
        self.assertIn('id="param-C"', html)
        report = await validate_interactive_html(html)
        self.assertFalse(report.get("js_errors"), report)
        self.assertTrue(report["passed"], report["issues"])
        motion = [item for item in report.get("motionChecks") or [] if item.get("advances")]
        self.assertTrue(motion, report.get("motionChecks"))
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html)
            before = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime.simTime(),
                  clock: document.getElementById('work-sim-time').textContent,
                  readout: document.getElementById('work-readout').textContent,
                  snap: document.getElementById('work-canvas').toDataURL(),
                  rate: WorkFamily.applyParams().rate
                })"""
            )
            await page.click("#btn-start")
            await page.wait_for_timeout(220)
            after = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime.simTime(),
                  clock: document.getElementById('work-sim-time').textContent,
                  readout: document.getElementById('work-readout').textContent,
                  snap: document.getElementById('work-canvas').toDataURL(),
                  running: window.WorkRuntime.isRunning()
                })"""
            )
            await browser.close()
        self.assertGreater(before["rate"], 0)
        self.assertGreater(after["t"], before["t"])
        self.assertNotEqual(after["clock"], before["clock"])
        self.assertNotEqual(after["readout"], before["readout"])
        self.assertIn("t=", after["readout"])
        self.assertIn("ΣO₂", after["readout"])
        self.assertTrue(after["snap"] != before["snap"] or after["readout"] != before["readout"])
        self.assertTrue(after["running"])

    async def test_broken_family_if_token_does_not_stop_runtime_sim_time(self):
        html = _assemble_recipe(
            "photosynthesis_rate",
            "作品类型：科学演示。做一个光合作用实验，可调光照和二氧化碳浓度。",
        )
        broken = html.replace(
            "phase += dt;",
            "phase += dt if (recipe === 'photosynthesis_rate') oxygen += 0.01;",
            1,
        )
        self.assertIn("dt if (", broken)
        self.assertIn('data-work-family-script="true"', broken)
        self.assertIn('data-work-runtime-script="true"', broken)
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            page_errors = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            await page.set_content(broken)
            before = await page.evaluate("() => window.WorkRuntime && window.WorkRuntime.simTime()")
            await page.click("#btn-start")
            await page.wait_for_timeout(200)
            after = await page.evaluate(
                """() => ({
                  t: window.WorkRuntime && window.WorkRuntime.simTime(),
                  clock: document.getElementById('work-sim-time').textContent,
                  running: window.WorkRuntime && window.WorkRuntime.isRunning()
                })"""
            )
            await browser.close()
        self.assertTrue(any("Unexpected token" in item and "if" in item for item in page_errors), page_errors)
        self.assertIsNotNone(before)
        self.assertGreater(after["t"], before)
        self.assertNotEqual(after["clock"], "0.000")
        self.assertTrue(after["running"])


class ConverterDesktopResidual(unittest.IsolatedAsyncioTestCase):
    UNIT_BRIEF = "作品类型：工具。制作米与英尺双向单位换算工具，输入数值后转换，处理小数和无效输入，不要游戏玩法。"
    TEMP_BRIEF = "作品类型：工具。制作摄氏和华氏双向温度转换器，输入值和单位后点击转换，正确处理负数、小数及无效输入。"

    def test_conversion_oracle_covers_decimals_negatives_and_identity(self):
        self.assertAlmostEqual(convert_bidirectional("length_m_ft", 1, "m", "ft"), 3.280839895)
        self.assertAlmostEqual(convert_bidirectional("length_m_ft", 3.280839895, "ft", "m"), 1)
        self.assertAlmostEqual(convert_bidirectional("length_m_ft", 2.5, "m", "m"), 2.5)
        self.assertAlmostEqual(convert_bidirectional("temp_c_f", 0, "C", "F"), 32)
        self.assertAlmostEqual(convert_bidirectional("temp_c_f", -40, "C", "F"), -40)
        self.assertAlmostEqual(convert_bidirectional("temp_c_f", 98.6, "F", "C"), 37.0, places=4)

    def test_assembled_unit_converter_keeps_search_targets_and_no_science_chrome(self):
        html = _assemble_recipe("length_m_ft", self.UNIT_BRIEF)
        self.assertEqual(converter_shell_contract_errors(html), [])
        self.assertIn(CONVERTER_CONTROL_SEARCH, html)
        self.assertIn('id="convertBtn"', html)
        self.assertIn('id="resetBtn"', html)
        self.assertIn('id="valueInput"', html)
        self.assertNotIn('id="btn-start"', html)
        self.assertNotIn('id="work-canvas"', html)
        self.assertIn("convertBtn", fill_prompt(
            kind="tool",
            brief=self.UNIT_BRIEF,
            recipe=get_recipe("length_m_ft"),
            plan=plan_interactive_diversity(
                family_id=CONVERTER_FAMILY_ID,
                recipe_id="length_m_ft",
                title="米与英尺换算",
                formula=get_recipe("length_m_ft").formula,
                variation_seed="unit-fill",
                ledger=DiversityLedger(),
            ),
            route=route_interactive_template("tool", self.UNIT_BRIEF),
        ))

    async def test_assembled_converters_pass_desktop_qa_and_stay_operable(self):
        from playwright.async_api import async_playwright

        cases = (
            ("length_m_ft", self.UNIT_BRIEF, "2", "6.5617"),
            ("temp_c_f", self.TEMP_BRIEF, "-40", "-40"),
        )
        for recipe_id, brief, typed, expected_fragment in cases:
            html = _assemble_recipe(recipe_id, brief)
            self.assertEqual(converter_shell_contract_errors(html), [], recipe_id)
            report = await validate_interactive_html(html, brief=brief)
            self.assertFalse(report.get("js_errors"), report)
            self.assertTrue(report["passed"], (recipe_id, report["issues"]))
            self.assertTrue(report.get("contentChanged"), recipe_id)
            self.assertFalse(report.get("layoutIssues"), (recipe_id, report.get("layoutIssues")))
            layout_text = " ".join(report.get("layoutIssues") or [])
            self.assertNotIn("convertBtn", layout_text)
            self.assertNotIn("resetBtn", layout_text)

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                page = await browser.new_page(viewport={"width": 1000, "height": 600})
                await page.set_content(html)
                await page.evaluate(
                    "() => document.documentElement.style.setProperty('font-size',"
                    "parseFloat(getComputedStyle(document.documentElement).fontSize)*1.125+'px','important')"
                )
                probe = await page.evaluate(
                    """() => {
                      const ids = ['convertBtn','resetBtn','valueInput','fromUnit','toUnit','convertResult'];
                      const viewport = {w: innerWidth, h: innerHeight};
                      const nodes = {};
                      for (const id of ids) {
                        const el = document.getElementById(id);
                        if (!el) { nodes[id] = null; continue; }
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        nodes[id] = {
                          visible: r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none',
                          inView: r.top>=-2 && r.left>=-2 && r.bottom<=viewport.h+2 && r.right<=viewport.w+2,
                        };
                      }
                      return {nodes, overflow: document.documentElement.scrollWidth > innerWidth+2};
                    }"""
                )
                before = await page.locator("#convertResult").inner_text()
                await page.fill("#valueInput", typed)
                await page.click("#convertBtn")
                after = await page.locator("#convertResult").inner_text()
                await page.click("#resetBtn")
                restored = await page.locator("#convertResult").inner_text()
                restored_value = await page.input_value("#valueInput")
                await browser.close()
            self.assertFalse(probe["overflow"], (recipe_id, probe))
            for control_id, info in probe["nodes"].items():
                self.assertIsNotNone(info, (recipe_id, control_id))
                self.assertTrue(info["visible"], (recipe_id, control_id, info))
                self.assertTrue(info["inView"], (recipe_id, control_id, info))
            self.assertNotEqual(after, before, recipe_id)
            self.assertIn(expected_fragment, after, (recipe_id, after))
            self.assertEqual(after != restored or restored_value != typed, True, recipe_id)
            self.assertNotEqual(restored_value, typed, recipe_id)

    async def test_tall_converter_fixture_keeps_buttons_after_compact(self):
        from src.engine.interactive_creation import apply_preview_layout_compact

        tall = '''<!doctype html><html><head><meta charset="utf-8">
        <style>body{margin:24px;font:16px/1.4 sans-serif}
        canvas{width:960px;height:520px;display:block}</style></head>
        <body><h1>米英尺换算</h1>
        <p>输入数值后转换。</p>
        <canvas id="art" width="960" height="520"></canvas>
        <label>数值 <input id="valueInput" type="number" value="1"></label>
        <select id="fromUnit"><option value="m" selected>米</option><option value="ft">英尺</option></select>
        <select id="toUnit"><option value="m">米</option><option value="ft" selected>英尺</option></select>
        <button type="button" id="convertBtn"
          onclick="convertResult.textContent=String((Number(valueInput.value)||0)*3.28084)">转换</button>
        <button type="button" id="resetBtn"
          onclick="valueInput.value='1';convertResult.textContent='3.2808'">重置</button>
        <output id="convertResult">3.2808</output>
        <script>void 0</script>
        </body></html>'''
        original = await validate_interactive_html(tall)
        self.assertTrue(
            any("字号容差" in issue or "核心图形" in issue or "convertBtn" in issue or "resetBtn" in issue
                for issue in original["issues"]),
            original["issues"],
        )
        compact = apply_preview_layout_compact(tall)
        self.assertIn('id="convertBtn"', compact)
        self.assertIn('id="resetBtn"', compact)
        self.assertIn("#convertBtn,#resetBtn", compact)
        repaired = await validate_interactive_html(compact)
        self.assertTrue(repaired["passed"], repaired["issues"])
        self.assertFalse(repaired.get("layoutIssues"), repaired.get("layoutIssues"))


if __name__ == "__main__":
    unittest.main()
