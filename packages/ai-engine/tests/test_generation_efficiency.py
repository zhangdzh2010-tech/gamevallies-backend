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
    accumulate_sim_time,
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
    RECIPES,
    family_plugin_js,
    get_recipe,
    plane_mirror_rays,
    recipes_for_family,
    wave_superposition,
)
from src.engine.interactive_router import route_interactive_template
from src.engine.interactive_short_path import (
    assemble_short_path_document,
    default_slots,
    extract_fill_payload,
    looks_like_full_html,
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
            ("作品类型：科学演示。制作平面镜反射光学演示，可调入射角，入射光指向镜面交点、反射光离开交点，显示反射定律。", "HIT", "geometric_ray_2d", "mirror_optics", "physics"),
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
            "作品类型：科学演示。制作三棱镜色散与透镜成像对比演示，可调折射率。",
        )
        self.assertEqual(decision.route, "MISS")
        self.assertFalse(decision.uses_short_path)
        self.assertIn(decision.reason, {"no_family_match", "ambiguous_family"})
        self.assertIsNone(decision.family_id)

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
        self.assertTrue(recipes_for_family("geometric_ray_2d"))


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
        for recipe_id in ("gas_law", "population"):
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


if __name__ == "__main__":
    unittest.main()
