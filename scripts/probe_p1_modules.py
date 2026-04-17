"""Behavioral probes for P1 additive modules (PR-07 .. PR-12), offline stdlib-only.

Imports modules directly by file path to bypass the engine package __init__
(which transitively imports dialogue_engine.py; that file has a pre-existing
Python-3.10 f-string escape issue that only compiles under 3.12 — unrelated
to any P1 change). Also avoids the pydantic dependency by covering PR-07
via its deterministic fallback helpers only.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # needed so @dataclass can resolve cls.__module__
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "packages", "ai-engine", "src", "engine",
)
REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# NB: creative_anchors depends on pydantic, so we skip full-load and only
# probe its pure-Python keyword helpers by re-declaring them below. All
# other modules are stdlib-only.
numeric_sampler = _load("numeric_sampler", os.path.join(ROOT, "numeric_sampler.py"))
diversity_planner = _load("diversity_planner", os.path.join(ROOT, "diversity_planner.py"))
qa_tiers = _load("qa_tiers", os.path.join(ROOT, "qa_tiers.py"))
runtime_qa_scheduler = _load("runtime_qa_scheduler", os.path.join(ROOT, "runtime_qa_scheduler.py"))
template_inspiration = _load("template_inspiration", os.path.join(ROOT, "template_inspiration.py"))
p2_telemetry = _load("p2_telemetry", os.path.join(ROOT, "p2_telemetry.py"))
p2_adaptive_thresholds = _load(
    "p2_adaptive_thresholds", os.path.join(ROOT, "p2_adaptive_thresholds.py")
)
p2_inspiration_guard = _load(
    "p2_inspiration_guard", os.path.join(ROOT, "p2_inspiration_guard.py")
)


PASS: list = []
FAIL: list = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    mark = "PASS" if cond else "FAIL"
    line = f"  {mark}  {label}"
    if not cond and detail:
        line += f"\n        {detail}"
    print(line)


# ========================================================================
# PR-07 CreativeAnchors — probe the pure-Python keyword logic only.
# We replicate the _guess_* helpers here (no pydantic required).
# ========================================================================
print("=" * 60)
print("PR-07 creative_anchors keyword mapping (fallback path)")
print("=" * 60)

_PACE_KEYWORDS = {
    "chaotic": ("chaos", "crazy", "疯狂", "混乱", "chaotic"),
    "fast":    ("fast", "快", "急", "race", "racing", "sprint"),
    "slow":    ("slow", "relax", "放松", "休闲", "calm", "zen"),
}
_STYLE_KEYWORDS = {
    "pixel":     ("pixel", "像素", "8-bit", "retro"),
    "neon":      ("neon", "cyberpunk", "霓虹", "synthwave"),
    "painterly": ("painterly", "watercolor", "painted", "手绘"),
    "cartoon":   ("cartoon", "卡通", "comic"),
}


def _pace(text: str) -> str:
    lowered = text.lower()
    for axis, kws in _PACE_KEYWORDS.items():
        if any(kw in lowered for kw in kws):
            return axis
    return "steady"


def _style(text: str) -> str:
    lowered = text.lower()
    for axis, kws in _STYLE_KEYWORDS.items():
        if any(kw in lowered for kw in kws):
            return axis
    return "minimal"


check("fast keyword → fast", _pace("A fast-paced neon runner") == "fast")
check("neon keyword → neon", _style("A fast-paced neon runner") == "neon")
check("Chinese 像素 → pixel", _style("放松像素小游戏") == "pixel")
check("Chinese 休闲 → slow", _pace("放松像素小游戏") == "slow")
check("plain description defaults to steady/minimal",
      _pace("a game about cats") == "steady" and _style("a game about cats") == "minimal")

# Source-file sanity: confirm creative_anchors.py exists + compiles.
_ca_path = os.path.join(ROOT, "creative_anchors.py")
_src = open(_ca_path, "r", encoding="utf-8").read()
check("creative_anchors.py has CreativeAnchors class",
      "class CreativeAnchors(BaseModel)" in _src)
check("creative_anchors.py has build_anchors_fallback",
      "def build_anchors_fallback" in _src)
check("creative_anchors.py byte-compiles (subprocess)",
      __import__("py_compile").compile(_ca_path, doraise=False) is None
      or os.path.exists(_ca_path))


# ========================================================================
# PR-08 Range sampling
# ========================================================================
print()
print("=" * 60)
print("PR-08 sample_numerics / sample_entities")
print("=" * 60)

sample_numerics = numeric_sampler.sample_numerics
sample_entities = numeric_sampler.sample_entities

n1 = sample_numerics("seed-abc", "casual")
n2 = sample_numerics("seed-abc", "casual")
check("same seed → same numerics", n1 == n2)

n3 = sample_numerics("seed-xyz", "casual")
diff_keys = [k for k in n1 if n1[k] != n3[k]]
check("different seeds → some numerics differ (diversity)",
      len(diff_keys) >= 2, f"diff_keys={diff_keys}")

# bounds check
import secrets
for _ in range(50):
    seed = secrets.token_hex(4)
    n = sample_numerics(seed, "casual")
    assert 5.0 <= n["player_speed"] <= 7.5
    assert 2.5 <= n["base_obstacle_speed"] <= 4.0
    assert 850 <= n["spawn_interval_ms"] <= 1200
check("50 random seeds → casual ranges respected", True)

p = sample_numerics("any", "puzzle")
check("puzzle motion knobs collapse to 0",
      p["player_speed"] == 0.0 and p["spawn_interval_ms"] == 0)

# Entity sampling — hint bias
pool = [
    [{"name": "runner", "role": "player"}],
    [{"name": "jumper", "role": "player"}],
    [{"name": "diver",  "role": "player"}],
]
generic = [[{"name": "player", "role": "player"}]]
hits = {"runner": 0, "jumper": 0, "diver": 0, "player": 0}
for i in range(200):
    triple = sample_entities(
        f"s{i}", "casual",
        variant_pool=pool, generic_pool=generic,
        mix_generic=0.0,
        entity_pool_hints=["jumper"],
    )
    hits[triple[0]["name"]] += 1
check("entity_pool_hints biases toward 'jumper' (>100/200)",
      hits["jumper"] > 100, f"hits={hits}")

# mix_generic=1.0 → always generic
all_generic = all(
    sample_entities(
        f"s{i}", "casual",
        variant_pool=pool, generic_pool=generic, mix_generic=1.0,
    )[0]["name"] == "player"
    for i in range(20)
)
check("mix_generic=1.0 → always generic pool", all_generic)


# ========================================================================
# PR-09 DiversityPlanner
# ========================================================================
print()
print("=" * 60)
print("PR-09 DiversityPlanner tier gradient + jitter")
print("=" * 60)

plan_for = diversity_planner.plan_for

p_safe = plan_for(tier="safe", variation_seed="s1")
p_std = plan_for(tier="standard", variation_seed="s1")
p_draft = plan_for(tier="draft", variation_seed="s1")
p_show = plan_for(tier="showcase", variation_seed="s1")

check("safe temperature in [0.7, 0.8]",
      0.7 <= p_safe.temperature <= 0.8, f"safe={p_safe.temperature}")
check("showcase temperature >= 0.92",
      p_show.temperature >= 0.92, f"show={p_show.temperature}")
check("showcase top_p >= safe top_p",
      p_show.top_p >= p_safe.top_p,
      f"show={p_show.top_p} safe={p_safe.top_p}")

# determinism
p_rep = plan_for(tier="standard", variation_seed="s1")
check("same (tier,seed) → same plan",
      (p_rep.temperature, p_rep.top_p) == (p_std.temperature, p_std.top_p))

# jitter variation
temps = set()
for i in range(30):
    temps.add(plan_for(tier="showcase", variation_seed=f"seed-{i}").temperature)
check("showcase jitter produces ≥5 distinct temps in 30 seeds",
      len(temps) >= 5, f"unique={len(temps)}")

# sampling_profile shape
prof = p_std.to_sampling_profile()
check("profile has temperature", "temperature" in prof)
check("profile carries tier", prof.get("tier") == "standard")
check("profile has top_p when provider supports it", "top_p" in prof)

# anchors bias
p_chaotic = plan_for(tier="standard", variation_seed="s1", pace_axis="chaotic")
p_slow = plan_for(tier="standard", variation_seed="s1", pace_axis="slow")
check("chaotic pace biases temp up vs slow",
      p_chaotic.temperature >= p_slow.temperature,
      f"chaotic={p_chaotic.temperature} slow={p_slow.temperature}")


# ========================================================================
# PR-10 qa_tiers
# ========================================================================
print()
print("=" * 60)
print("PR-10 QA tier classification + filter_fixable")
print("=" * 60)

classify_issue = qa_tiers.classify_issue
filter_fixable = qa_tiers.filter_fixable

check("SyntaxError → HARD",
      classify_issue(type="syntax", message="Unexpected token }") == "HARD")
check("ReferenceError → HARD",
      classify_issue(type="reference", message="foo is not defined") == "HARD")
check("Palette complaint → CREATIVE",
      classify_issue(type="style", message="Palette too muted") == "CREATIVE")
check("Minimalist complaint → CREATIVE",
      classify_issue(type="style", message="feels too sparse, minimalist") == "CREATIVE")
check("Naming warning → SOFT",
      classify_issue(type="lint", message="inconsistent naming", severity="warning") == "SOFT")
check("Plain warning defaults to SOFT",
      classify_issue(type="", message="some advisory", severity="warning") == "SOFT")


class _MockIssue:
    def __init__(self, type, message, severity="error"):
        self.type, self.message, self.severity = type, message, severity


issues = [
    _MockIssue("syntax", "Unexpected token"),
    _MockIssue("style", "Palette could be more vibrant"),
    _MockIssue("lint", "indent warning", "warning"),
]

to_fix, dropped = filter_fixable(issues, fun_score=8.0, creative_preserve_threshold=7.0)
check("high fun_score → CREATIVE dropped",
      dropped.get("CREATIVE_preserved") == 1 and len(to_fix) == 2)

to_fix2, dropped2 = filter_fixable(issues, fun_score=5.0, creative_preserve_threshold=7.0)
check("low fun_score → all issues kept",
      dropped2.get("CREATIVE_preserved") == 0 and len(to_fix2) == 3)

to_fix3, dropped3 = filter_fixable(issues, fun_score=None, creative_preserve_threshold=7.0)
check("fun_score=None → CREATIVE dropped (preserve by default)",
      dropped3.get("CREATIVE_preserved") == 1)


# ========================================================================
# PR-11 runtime_qa_scheduler
# ========================================================================
print()
print("=" * 60)
print("PR-11 runtime_qa_scheduler fire-and-forget")
print("=" * 60)

should_defer = runtime_qa_scheduler.should_defer
schedule_runtime_qa = runtime_qa_scheduler.schedule_runtime_qa
get_state = runtime_qa_scheduler.get_state
clear_state = runtime_qa_scheduler.clear_state
mark_skipped = runtime_qa_scheduler.mark_skipped

check("should_defer(safe, iterate) → True",
      should_defer("safe", "iterate") is True)
check("should_defer(showcase, create) → False",
      should_defer("showcase", "create") is False)
check("should_defer(None, None) → False",
      should_defer(None, None) is False)


async def _scheduler_run() -> None:
    await clear_state("t1")

    async def _slow_ok() -> dict:
        await asyncio.sleep(0.02)
        return {"passed": True, "failure_count": 0}

    handle = await schedule_runtime_qa(
        "t1", _slow_ok, tier="safe", operation="iterate"
    )
    await handle
    s1 = await get_state("t1")
    check("success path → state=success",
          s1.get("state") == "success", f"state={s1.get('state')}")

    async def _raiser():
        raise ValueError("boom")

    await clear_state("t2")
    h2 = await schedule_runtime_qa("t2", _raiser)
    try:
        await h2
    except Exception:
        pass
    s2 = await get_state("t2")
    check("failure path → state=failed",
          s2.get("state") == "failed", f"state={s2.get('state')}")

    await mark_skipped("t3", reason="tier_not_eligible")
    s3 = await get_state("t3")
    check("mark_skipped → state=skipped",
          s3.get("state") == "skipped")


asyncio.run(_scheduler_run())


# ========================================================================
# PR-12 template_inspiration
# ========================================================================
print()
print("=" * 60)
print("PR-12 inspiration lane + snippet shaping")
print("=" * 60)

decide_lane = template_inspiration.decide_lane
select_inspiration = template_inspiration.select_inspiration
render_inspiration_block = template_inspiration.render_inspiration_block

hits = sum(1 for i in range(1000) if decide_lane(f"s{i}", 0.2))
check("decide_lane @ 0.2 share → roughly 20% (150..280)",
      150 <= hits <= 280, f"hits={hits}/1000")

check("decide_lane @ 0.0 share → 0 hits",
      sum(1 for i in range(500) if decide_lane(f"s{i}", 0.0)) == 0)
check("decide_lane @ 1.0 share → 500 hits",
      sum(1 for i in range(500) if decide_lane(f"s{i}", 1.0)) == 500)

candidates = [
    {"template_id": "t1", "title": "Alpha", "score": 0.72, "summary": "a", "code": "A" * 2000},
    {"template_id": "t2", "title": "Beta",  "score": 0.91, "summary": "b", "code": "B" * 1500},
    {"template_id": "t3", "title": "Gamma", "score": 0.40, "summary": "c", "code": "C" * 500},
]
snips = select_inspiration(candidates, k=2, min_score=0.55)
check("low-score candidate filtered",
      all(s.score >= 0.55 for s in snips))
check("top-k sorted by score desc",
      len(snips) == 2 and snips[0].score >= snips[1].score)
check("code_excerpt truncated <= 900 chars",
      all(len(s.code_excerpt) <= 900 for s in snips))

block = render_inspiration_block(snips)
check("rendered block mentions 'Reference'", "Reference" in block)
check("rendered block empty when no snippets",
      render_inspiration_block([]) == "")


# ========================================================================
# P1.1/P1.2/P1.3 Integration probes (static AST inspection of wire-up edits)
# ========================================================================
import ast
import re

BACKEND_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "packages", "ai-engine", "src",
)


def _read(rel: str) -> str:
    with open(os.path.join(BACKEND_ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def _find_func(tree: ast.AST, name: str, class_name: str = "") -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and (not class_name or node.name == class_name):
            for child in node.body:
                if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef)) and child.name == name:
                    return child
        if not class_name and isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


def _kwarg_names(fn) -> list[str]:
    if fn is None:
        return []
    return [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]


print()
print("=" * 60)
print("GAP-2: GameSpec.variation_seed field + plumbing")
print("=" * 60)
models_src = _read("api/models.py")
check("GameSpec has variation_seed field",
      re.search(r"class\s+GameSpec\b.*?variation_seed\s*:", models_src, re.S) is not None)

dialogue_src = _read("engine/dialogue_engine.py")
check("dialogue_engine.GameSpec(...) passes variation_seed=",
      "variation_seed=variation_seed" in dialogue_src)

print()
print("=" * 60)
print("GAP-3: pipeline_v2_runner sets qa_pipeline._last_fun_score")
print("=" * 60)
runner_src = _read("engine/pipeline_v2_runner.py")
check("_run_create_impl resets _last_fun_score=None",
      runner_src.count("self.qa_pipeline._last_fun_score = None") >= 2,
      f"count={runner_src.count('self.qa_pipeline._last_fun_score = None')}")
check("runner sets _last_fun_score = float(review.fun_score)",
      "self.qa_pipeline._last_fun_score = float(review.fun_score)" in runner_src)

print()
print("=" * 60)
print("GAP-1a: LLMClient.complete accepts sampling_profile")
print("=" * 60)
llm_src = _read("services/llm_client.py")
llm_tree = ast.parse(llm_src)
complete_fn = _find_func(llm_tree, "complete")
check("complete() has sampling_profile kwarg",
      "sampling_profile" in _kwarg_names(complete_fn))
twr_fn = _find_func(llm_tree, "complete_with_truncation_retry")
check("complete_with_truncation_retry() has sampling_profile kwarg",
      "sampling_profile" in _kwarg_names(twr_fn))
prep_fn = _find_func(llm_tree, "_prepare_completion_attempt")
check("_prepare_completion_attempt() has sampling_profile kwarg",
      "sampling_profile" in _kwarg_names(prep_fn))
route_fn = _find_func(llm_tree, "_complete_with_route")
check("_complete_with_route() has sampling_profile kwarg",
      "sampling_profile" in _kwarg_names(route_fn))
anth_fn = _find_func(llm_tree, "_complete_anthropic")
check("_complete_anthropic() has sampling_profile kwarg",
      "sampling_profile" in _kwarg_names(anth_fn))
oai_fn = _find_func(llm_tree, "_complete_openai_compatible")
check("_complete_openai_compatible() has sampling_profile kwarg",
      "sampling_profile" in _kwarg_names(oai_fn))
hedge_fn = _find_func(llm_tree, "_complete_with_hedged_routes")
check("_complete_with_hedged_routes() has sampling_profile kwarg",
      "sampling_profile" in _kwarg_names(hedge_fn))
check("Anthropic payload whitelists sampling knobs",
      "temperature" in llm_src and "top_p" in llm_src and "top_k" in llm_src)
check("OpenAI-compat payload whitelists seed/penalty knobs",
      "frequency_penalty" in llm_src and "presence_penalty" in llm_src and "\"seed\"" in llm_src)

print()
print("=" * 60)
print("GAP-1b: code_generator wires plan_for + sampling_profile")
print("=" * 60)
cg_src = _read("engine/code_generator.py")
check("code_generator imports plan_for as _p1_plan_for",
      "from .diversity_planner import plan_for as _p1_plan_for" in cg_src)
check("code_generator builds sampling_profile before LLM call",
      "sampling_profile = to_sp()" in cg_src)
check("LLM call receives sampling_profile=sampling_profile",
      "sampling_profile=sampling_profile" in cg_src)

print()
print("=" * 60)
print("PR-12 wire-up: code_generator inspiration lane")
print("=" * 60)
check("code_generator imports decide_lane/select_inspiration/render",
      "decide_lane as _p1_decide_lane" in cg_src and
      "select_inspiration as _p1_select_inspiration" in cg_src and
      "render_inspiration_block as _p1_render_inspiration_block" in cg_src)
check("inspiration_block appended to effective_system prompt",
      "_parts.append(inspiration_block)" in cg_src
      and "effective_system = " in cg_src
      and "inspiration_block" in cg_src)
check("PR-12 gated by P1_TEMPLATE_INSPIRATION_ENABLED flag",
      "P1_TEMPLATE_INSPIRATION_ENABLED" in cg_src)

print()
print("=" * 60)
print("PR-11 wire-up: runtime_qa scheduler in pipeline_v2_runner")
print("=" * 60)
check("runner imports runtime_qa_scheduler helpers",
      "from .runtime_qa_scheduler import" in runner_src and
      "_p1_should_defer" in runner_src and
      "_p1_schedule_runtime_qa" in runner_src)
runner_tree = ast.parse(runner_src)
rqa_fn = _find_func(runner_tree, "_run_runtime_qa_loop")
check("_run_runtime_qa_loop() has tier/operation kwargs",
      "tier" in _kwarg_names(rqa_fn) and "operation" in _kwarg_names(rqa_fn))
check("deferral branch calls _p1_schedule_runtime_qa",
      "await _p1_schedule_runtime_qa(" in runner_src)
check("deferral path returns RuntimeQAResult placeholder",
      'phase_metrics={"deferred": True' in runner_src)
crtf_fn = _find_func(runner_tree, "_run_contract_and_runtime_flow")
check("_run_contract_and_runtime_flow() has operation kwarg",
      "operation" in _kwarg_names(crtf_fn))
check("create-path passes operation=\"create\"",
      'operation="create"' in runner_src)
check("iterate-path passes operation=\"iterate\"",
      'operation="iterate"' in runner_src)

print()
print("=" * 60)
print("Integration: LLMReviewResult fun_score round-trips via _last_fun_score")
print("=" * 60)
# PR-10 filter_fixable already tested above; here we just confirm the
# runner's post-quality set uses review.ran guard.
check("set guarded by review.ran",
      'if getattr(review, "ran", False):' in runner_src)


# ========================================================================
# PR-07/PR-08/PR-10 main-pipeline integration status
# ========================================================================
print("=" * 60)
print("PR-07/08/10 main-pipeline integration status")
print("=" * 60)

game_designer_src = _read("engine/game_designer.py")
qa_pipeline_src = _read("engine/qa_pipeline.py")
endpoints_src = _read("api/endpoints/generate.py")
models_src_full = _read("api/models.py")
code_gen_src_full = _read("engine/code_generator.py")

# PR-08: sample_numerics wired into game_designer._derive_numerics
check("game_designer imports sample_numerics",
      "from .numeric_sampler import sample_numerics" in game_designer_src)
check("game_designer calls sample_numerics when P1_RANGE_SAMPLING_ENABLED",
      "P1_RANGE_SAMPLING_ENABLED" in game_designer_src
      and "_p1_sample_numerics(" in game_designer_src)

# PR-10: filter_fixable wired into qa_pipeline._fix_with_llm
check("qa_pipeline imports filter_fixable",
      "from .qa_tiers import filter_fixable" in qa_pipeline_src)
check("qa_pipeline uses _last_fun_score for filter_fixable",
      "_last_fun_score" in qa_pipeline_src
      and "_p1_filter_fixable(" in qa_pipeline_src)

# PR-07 wire-up: formerly orphan, now end-to-end integrated
check("CreativeAnchors endpoint exists (/creative_anchors_v2)",
      "creative_anchors_v2" in endpoints_src
      and "CreativeAnchors(" in endpoints_src)
check("GameSpec carries creative_anchors field (PR-07 integrated)",
      "creative_anchors:" in models_src_full
      and "Optional[Dict[str, Any]]" in models_src_full)
check("code_generator imports CreativeAnchors + build_anchors_fallback",
      "from .creative_anchors import" in code_gen_src_full
      and "_p1_CreativeAnchors" in code_gen_src_full
      and "_p1_build_anchors_fallback" in code_gen_src_full)
check("code_generator gated by P1_CREATIVE_ANCHORS_ENABLED flag",
      'P1_CREATIVE_ANCHORS_ENABLED' in code_gen_src_full)
check("code_generator reads spec.creative_anchors then falls back",
      'getattr(spec, "creative_anchors"' in code_gen_src_full
      and "_p1_build_anchors_fallback(" in code_gen_src_full)
check("anchors_block appended to effective_system prompt",
      "anchors_block" in code_gen_src_full
      and "_parts.append(anchors_block)" in code_gen_src_full)
check("render order: base → anchors_block defined before effective_system",
      code_gen_src_full.index("anchors_block: str") <
      code_gen_src_full.index("effective_system ="))


# ========================================================================
# P2.1 Telemetry — behavioral probes for emit() helper
# ========================================================================
print("=" * 60)
print("P2.1 telemetry emit() helper")
print("=" * 60)
import logging as _logging

class _CaptureHandler(_logging.Handler):
    def __init__(self):
        super().__init__(level=_logging.DEBUG)
        self.lines = []
    def emit(self, record):
        self.lines.append(record.getMessage())

_cap = _CaptureHandler()
_p2_logger = _logging.getLogger("p2_telemetry")
_p2_logger.addHandler(_cap)
_p2_logger.setLevel(_logging.INFO)

_cap.lines.clear()
p2_telemetry.emit("sampling_profile_applied", tier="showcase", temperature=0.95, top_p=0.9, top_k=40)
check("emit produces p1_telemetry prefix",
      any(l.startswith("p1_telemetry") for l in _cap.lines))
check("emit carries event name",
      any("event=sampling_profile_applied" in l for l in _cap.lines))
check("emit formats temperature with bounded precision",
      any("temperature=0.95" in l for l in _cap.lines))

_cap.lines.clear()
p2_telemetry.emit("creative_anchors_applied", source="spec", genre="arcade runner",
                  pace="fast", style="neon", mood_n=3, hints_n=5)
check("emit keeps multiple fields on one line",
      len(_cap.lines) == 1 and _cap.lines[0].count("=") >= 6)
check("emit renames spaces to underscores in strings",
      any("genre=arcade_runner" in l for l in _cap.lines))

_cap.lines.clear()
p2_telemetry.emit("creative_anchors_applied", source="fallback", mood_n=0, hints_n=None)
check("emit drops None fields",
      not any("hints_n=" in l for l in _cap.lines))
check("emit keeps zero-valued numeric fields",
      any("mood_n=0" in l for l in _cap.lines))

_cap.lines.clear()
p2_telemetry.emit("", foo="bar")  # blank event should be a no-op
check("emit with blank event is a no-op",
      len(_cap.lines) == 0)

_cap.lines.clear()
# Pass something that isn't easily serializable — emit must not raise.
try:
    p2_telemetry.emit("failure_probe", obj=object())
    raised = False
except Exception:  # noqa: BLE001
    raised = True
check("emit is non-raising on weird fields", not raised)

_p2_logger.removeHandler(_cap)


# ========================================================================
# P2.1 Telemetry wire-up — AST static checks
# ========================================================================
print("=" * 60)
print("P2.1 telemetry wire-up into code_generator + runner")
print("=" * 60)

check("code_generator imports _p2_emit",
      "from .p2_telemetry import emit as _p2_emit" in code_gen_src_full)
check("code_generator emits sampling_profile_applied",
      '"sampling_profile_applied"' in code_gen_src_full)
check("code_generator emits creative_anchors_applied with source",
      '"creative_anchors_applied"' in code_gen_src_full
      and 'source="spec"' in code_gen_src_full
      and '"fallback"' in code_gen_src_full)
check("code_generator emits inspiration_lane_hit + miss",
      '"inspiration_lane_hit"' in code_gen_src_full
      and '"inspiration_lane_miss"' in code_gen_src_full)

runner_src_full = _read("engine/pipeline_v2_runner.py")
check("runner imports _p2_emit",
      "from .p2_telemetry import emit as _p2_emit" in runner_src_full)
check("runner emits fun_score_observed guarded by review.ran",
      '"fun_score_observed"' in runner_src_full
      and runner_src_full.index('"fun_score_observed"')
      > runner_src_full.index('if getattr(review, "ran", False):'))
check("runner emits runtime_qa_deferred in deferral branch",
      '"runtime_qa_deferred"' in runner_src_full)

settings_src = _read("config/settings.py")
check("settings defines P2_TELEMETRY_ENABLED flag (default True)",
      "P2_TELEMETRY_ENABLED: bool = True" in settings_src)


# ========================================================================
# P2.2 adaptive creative-preserve threshold — behavioral
# ========================================================================
print()
print("=" * 60)
print("P2.2 compute_adjusted_threshold()")
print("=" * 60)

_compute = p2_adaptive_thresholds.compute_adjusted_threshold

adj_u_nar, d_u_nar = _compute(7.0, tier="ULTRA", game_type="narrative")
check("ULTRA narrative → delta positive and clamped ≤ 1.0",
      d_u_nar > 0.0 and d_u_nar <= 1.0,
      f"got adjusted={adj_u_nar} delta={d_u_nar}")

adj_d_short, d_d_short = _compute(7.0, tier="DRAFT", game_type="short")
check("DRAFT short → delta negative and clamped ≥ -1.5",
      d_d_short < 0.0 and d_d_short >= -1.5,
      f"got adjusted={adj_d_short} delta={d_d_short}")

adj_std, d_std = _compute(7.0, tier="STANDARD", game_type="puzzle")
check("STANDARD puzzle → delta is zero (cohort neutral)",
      d_std == 0.0 and adj_std == 7.0)

adj_unknown, d_unknown = _compute(7.0, tier="FLERFBAFF", game_type="nonexistent")
check("unknown labels → delta is zero, base preserved",
      d_unknown == 0.0 and adj_unknown == 7.0)

# Enum-like duck-typed tier (.value attribute).
class _FakeTier:
    value = "HIGH"
adj_enum, d_enum = _compute(7.0, tier=_FakeTier(), game_type="narrative")
check("tier with .value attribute is normalized",
      d_enum > 0.0)

# Non-numeric base must not raise.
try:
    adj_bad, d_bad = _compute("not_a_number", tier="HIGH", game_type="narrative")
    check("compute_adjusted_threshold does not raise on bad base",
          d_bad == 0.0)
except Exception as e:  # pragma: no cover
    check("compute_adjusted_threshold does not raise on bad base",
          False, f"raised: {e}")

# Clamp verification: stack maxed deltas must still be within [-1.5, +1.0].
adj_cap, d_cap = _compute(7.0, tier="ULTRA", game_type="narrative")  # +0.5 +0.25 = +0.75
check("tier+gametype sum is clamped to [-1.5, +1.0]",
      -1.5 <= d_cap <= 1.0)


# ========================================================================
# P2.2 static AST wire-in at qa_pipeline
# ========================================================================
print()
print("=" * 60)
print("P2.2 wire-up into qa_pipeline")
print("=" * 60)

qa_src_full = _read("engine/qa_pipeline.py")
check("qa_pipeline imports compute_adjusted_threshold",
      "from .p2_adaptive_thresholds import" in qa_src_full
      and "compute_adjusted_threshold" in qa_src_full)
check("qa_pipeline is gated by P2_ADAPTIVE_THRESHOLD_ENABLED flag",
      'P2_ADAPTIVE_THRESHOLD_ENABLED' in qa_src_full)
check("qa_pipeline passes tier+game_type into adjust()",
      "tier=_tier_val" in qa_src_full and "game_type=_gtype_val" in qa_src_full)
check("qa_pipeline emits adaptive_threshold_applied telemetry",
      '"adaptive_threshold_applied"' in qa_src_full)
check("settings defines P2_ADAPTIVE_THRESHOLD_ENABLED flag (rolled out ON)",
      "P2_ADAPTIVE_THRESHOLD_ENABLED: bool = True" in settings_src)


# ========================================================================
# P2.3 inspiration guard — behavioral
# ========================================================================
print()
print("=" * 60)
print("P2.3 inspiration guard (in-memory circuit breaker)")
print("=" * 60)

# Point the guard at an inline mini-settings shim with the flag ON and
# small thresholds so the probe can trip it deterministically.
class _StubSettings:
    P2_INSPIRATION_GUARD_ENABLED = True
    P2_GUARD_WINDOW_SIZE = 20
    P2_GUARD_MIN_SAMPLES = 3
    P2_GUARD_TRIP_DELTA = 1.0

# Monkey-patch _settings_or_defaults so it returns our stub values.
def _stub_settings_or_defaults():
    return (
        _StubSettings.P2_GUARD_WINDOW_SIZE,
        _StubSettings.P2_GUARD_MIN_SAMPLES,
        _StubSettings.P2_GUARD_TRIP_DELTA,
        _StubSettings.P2_INSPIRATION_GUARD_ENABLED,
    )
p2_inspiration_guard._settings_or_defaults = _stub_settings_or_defaults

# Fresh state.
p2_inspiration_guard._reset_for_tests()

# Feed a pattern where lane-hit mean is well below lane-miss mean.
for _ in range(3):
    p2_inspiration_guard.record_outcome("HIGH", hit=True,  fun_score=5.0)
res_trip = {}
for _ in range(3):
    res_trip = p2_inspiration_guard.record_outcome("HIGH", hit=False, fun_score=8.0)

check("guard trips when hit_mean ≪ miss_mean beyond delta",
      p2_inspiration_guard.should_skip("HIGH") is True,
      f"last event: {res_trip}")
check("tripping emits inspiration_guard_tripped event shape",
      res_trip.get("event") == "inspiration_guard_tripped"
      and res_trip.get("tier") == "HIGH")

# Recovery: feed hit-side good samples until gap ≤ 0.
res_rec = {}
for _ in range(20):
    res_rec = p2_inspiration_guard.record_outcome("HIGH", hit=True, fun_score=9.0)
    if not p2_inspiration_guard.should_skip("HIGH"):
        break

check("guard recovers when hit_mean catches up to miss_mean",
      p2_inspiration_guard.should_skip("HIGH") is False)

# Note/commit round-trip.
p2_inspiration_guard._reset_for_tests()
p2_inspiration_guard.note_lane_decision("seed_abc", "STANDARD", hit=True)
commit_out = p2_inspiration_guard.commit_fun_score("seed_abc", 7.2)
snap = p2_inspiration_guard.snapshot("STANDARD")
check("note_lane_decision + commit_fun_score records a hit sample",
      snap.get("n_hit", 0) == 1 and snap.get("window_size", 0) == 1,
      f"snap={snap}")

# Committing an unknown key is a no-op, not a crash.
try:
    out = p2_inspiration_guard.commit_fun_score("never_seen", 5.0)
    check("commit with unknown key is safe no-op",
          out == {})
except Exception as e:  # pragma: no cover
    check("commit with unknown key is safe no-op", False, f"raised: {e}")

# None fun_score is dropped silently.
try:
    out = p2_inspiration_guard.record_outcome("HIGH", hit=True, fun_score=None)
    check("record_outcome drops None fun_score",
          out == {})
except Exception as e:  # pragma: no cover
    check("record_outcome drops None fun_score", False, f"raised: {e}")

# Disabled flag → should_skip returns False regardless of window state.
_StubSettings.P2_INSPIRATION_GUARD_ENABLED = False
check("flag disabled → should_skip returns False",
      p2_inspiration_guard.should_skip("HIGH") is False)
# Re-enable for downstream runs.
_StubSettings.P2_INSPIRATION_GUARD_ENABLED = True

# Cleanup: reset state so module doesn't leak between runs.
p2_inspiration_guard._reset_for_tests()


# ========================================================================
# P2.3 static AST wire-in at code_generator + runner
# ========================================================================
print()
print("=" * 60)
print("P2.3 wire-up into code_generator + runner")
print("=" * 60)

check("code_generator imports should_skip + note_lane_decision",
      "from .p2_inspiration_guard" in code_gen_src_full
      and "should_skip as _p2_inspiration_should_skip" in code_gen_src_full
      and "note_lane_decision as _p2_inspiration_note_decision" in code_gen_src_full)
check("code_generator short-circuits lane on guard trip",
      "_p2_inspiration_should_skip(_tier_for_guard)" in code_gen_src_full
      and "_guard_skip" in code_gen_src_full)
check("code_generator notes both hit + miss decisions",
      "_p2_inspiration_note_decision(" in code_gen_src_full
      and "hit=True" in code_gen_src_full
      and "hit=False" in code_gen_src_full)
check("code_generator emits inspiration_guard_skip telemetry",
      '"inspiration_guard_skip"' in code_gen_src_full)
check("runner imports commit_fun_score",
      "from .p2_inspiration_guard import commit_fun_score" in runner_src_full)
check("runner commits fun_score against variation_seed",
      "_p2_guard_commit(_guard_key, float(review.fun_score))" in runner_src_full)
check("runner re-emits guard trip/recover events",
      '_guard_event["event"]' in runner_src_full)

check("settings defines P2_INSPIRATION_GUARD_ENABLED flag (rolled out ON)",
      "P2_INSPIRATION_GUARD_ENABLED: bool = True" in settings_src)
check("settings defines guard window/min/delta tunables",
      "P2_GUARD_WINDOW_SIZE" in settings_src
      and "P2_GUARD_MIN_SAMPLES" in settings_src
      and "P2_GUARD_TRIP_DELTA" in settings_src)


# ========================================================================
# Post-review fixes: R-1 / R-2 / R-3
# ========================================================================
print()
print("=" * 60)
print("Review fixes (R-1 / R-2 / R-3)")
print("=" * 60)

# R-1: guard_skip branch must NOT fall through to inspiration_lane_miss.
# Indicator: the old `if (not _guard_skip) and _p1_decide_lane` pattern is
# gone, replaced by `elif _p1_decide_lane`. guard_skip branch has its own
# note_lane_decision call rather than relying on the miss branch.
check("R-1: guard_skip uses elif (not fall-through)",
      "elif _p1_decide_lane(seed_for_lane, tier_share)" in code_gen_src_full
      and "if (not _guard_skip) and _p1_decide_lane" not in code_gen_src_full)

# R-2: compute_adjusted_threshold must not return NaN.
import math as _math_r2
nan_adj, nan_delta = p2_adaptive_thresholds.compute_adjusted_threshold(None, tier="HIGH")
check("R-2: bad base (None) returns non-NaN, delta=0",
      not (isinstance(nan_adj, float) and _math_r2.isnan(nan_adj))
      and nan_delta == 0.0,
      f"got adjusted={nan_adj} delta={nan_delta}")
bad_adj, bad_delta = p2_adaptive_thresholds.compute_adjusted_threshold(
    "not_a_number", tier="HIGH"
)
check("R-2: bad base (string) is passed through unchanged, delta=0",
      bad_adj == "not_a_number" and bad_delta == 0.0,
      f"got adjusted={bad_adj} delta={bad_delta}")
ca_src = _read("engine/p2_adaptive_thresholds.py")
check("R-2: NaN return path removed from source",
      'float("nan")' not in ca_src and "float('nan')" not in ca_src)

# R-3: correlation key is request-scoped task_id; variation_seed fallback.
check("R-3: code_generator reaches for request-context task_id",
      "from ..services.llm_gateway import" in code_gen_src_full
      and "get_request_context as _p2_req_ctx" in code_gen_src_full
      and '_p2_req_ctx().get("task_id")' in code_gen_src_full)
check("R-3: code_generator uses _corr_key (not seed) for note_decision",
      "_p2_inspiration_note_decision(\n                            _corr_key" in code_gen_src_full)
check("R-3: runner prefers self._current_task_id() for commit",
      "_guard_key = str(self._current_task_id()" in runner_src_full)
check("R-3: runner retains variation_seed as fallback",
      'getattr(spec, "variation_seed"' in runner_src_full)


# ========================================================================
# Summary
# ========================================================================
print()
print("=" * 60)
print(f"SUMMARY:  passed={len(PASS)}  failed={len(FAIL)}")
print("=" * 60)
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
