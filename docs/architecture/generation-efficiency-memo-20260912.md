# Generation efficiency memo (2026-09-12)

User-approved overnight slice for science/tool interactive generation. This is
the design that shipped: a working short path for five interaction families,
a HIT/SOFT/MISS router, queryable telemetry, and an observed→candidate→staging
registry. Quality bars are unchanged.

## Non-negotiable constraints

- Do **not** lower `seed_worthy`, contract QA, runtime QA, or creativity/visual bars.
- Templates are **skeleton-only** (engine shell / state machine / safe APIs / sim
  time advance). Never whole-page reskins.
- **Diversity gate**: creative slots + `visual_pack` / anchors / `variation_seed`.
  Near-duplicate fingerprint → swap anchor → raise tier → fall back to full generate.
- Science coverage is **subject × interaction-family**, not exhaustive experiment lists.
- Router states: **HIT / SOFT / MISS**. MISS = full `logic_generate` (not a failure).
  Never force-fit the wrong family; scientific incorrectness is worse than slow.
- Self-evolution: first slice of Template Registry + candidate mining from
  `seed_worthy` successes (`observed → candidate → staging`). Auto-promote to
  production stays gated (`INTERACTIVE_TEMPLATE_AUTO_PROMOTE=false`).

## Architecture

```
brief + artifact_kind
    → route_interactive_template()
         HIT / SOFT → DesktopRuntimeShell + L1 family plugin + L3 JSON fill
         MISS       → existing full HTML logic_generate
    → same validate_interactive_html + structured review + seed_worthy labels
```

Arcade/game generation is unchanged. Game family skeletons are stubbed as
`arcade_loop` on the registry only.

### L0 — `DesktopRuntimeShell`

Module: `packages/ai-engine/src/engine/desktop_runtime_shell.py`

Shared by science and tool short path:

- canvas + `ResizeObserver`
- `requestAnimationFrame` loop
- pointer/keyboard (space start/pause, `r` reset)
- visible **开始 / 暂停 / 重置**
- leftover accumulator (`acc += dt`, never floor a sub-step frame to zero)
- `WorkRuntime` state machine and `WorkFamily.step/draw` plugin slot

Python `accumulate_sim_time()` mirrors the in-page contract for unit tests.

### L1 families and L2 recipes

Module: `packages/ai-engine/src/engine/interactive_families.py`

| Family | Interaction | Subjects | First recipes |
| --- | --- | --- | --- |
| `param_formula_panel` | live formula from labeled params | physics / chem / bio | Ohm, gas law, enzyme-temp, photosynthesis rate, Newton F=ma |
| `time_integrator_1d` | fixed-step 1D integration | physics | pendulum, free fall |
| `field_or_wave_2d` | 2D field / superposition | physics | wave interference |
| `compartment_flow` | coupled compartments | bio / chem | osmosis, population |
| `geometric_ray_2d` | labeled angle drives incident/reflected rays | physics | plane-mirror optics |

Recipes carry distinctive `required_any` keywords so a temperature converter
cannot be force-fit into enzyme-temp.

### Router

Module: `packages/ai-engine/src/engine/interactive_router.py`

- HIT: strong unique recipe match → short path, small JSON fill
- SOFT: family or partial recipe → short path, slightly larger fill
- MISS: no family, ambiguous families, tools without a strong recipe, iterate
  edits, or diversity fallback → full `code_generate.full`

Recorded as `template_route` on `quality_breakdown` and `task_memory`.

### Short path

Module: `packages/ai-engine/src/engine/interactive_short_path.py`

- Asks the LLM for L3 JSON slots (`title`, `summary`, `formula`, assumptions,
  limits, caption). Not full HTML.
- Uses `ContractDigest` instead of re-sending the full interactive system prompt.
- Assembles shell + family plugin + visual pack CSS.
- Fill output is **never** used as the playable document, even when the model
  returns a complete HTML page. JSON slots are merged when present; otherwise
  recipe defaults are assembled. This keeps `gas_law` / `population` on the
  canvas + executable-script shell instead of a broken fill page.
- Invalid JSON falls back to recipe default slots (model remains the skeleton).
- After a short-path candidate exists, repair stays **patch-first**.

### Diversity

Module: `packages/ai-engine/src/engine/interactive_diversity.py`

Fingerprint stem = family + recipe + title + formula. Accents = visual pack +
creative anchor. Near-duplicate stem → swap anchor → raise visual tier →
`fallback_to_full` (MISS / full generate).

### Telemetry

Module: `packages/ai-engine/src/engine/interactive_telemetry.py`

Per stage: `prompt_tokens`, `completion_tokens`, `stage_ms`, `queue_wait_ms`
(runtime-QA semaphore wait). Also `template_route`, `family_id`, `recipe_id`,
`subject`. Persisted on:

- `quality_breakdown` (flows to generation-status / `resultSummary`)
- `task_memory.task_meta`
- yield ledger fields

Token counts use provider usage when present; otherwise `len(text)//4`.

### Template registry (first slice)

Module: `packages/ai-engine/src/engine/template_registry.py`

`seed_worthy` successes are mined to **staging**. Production promote requires
`INTERACTIVE_TEMPLATE_AUTO_PROMOTE=true` (off by default). No whole-page HTML
is stored — only family/recipe/slot metadata.

## Efficiency knobs

| Knob | Default | Role |
| --- | --- | --- |
| `INTERACTIVE_SHORT_PATH_ENABLED` | `true` | Master switch |
| `INTERACTIVE_SHORT_PATH_HIT_MAX_TOKENS` | `2048` | HIT fill budget |
| `INTERACTIVE_SHORT_PATH_SOFT_MAX_TOKENS` | `3072` | SOFT fill budget |
| `INTERACTIVE_PATCH_FIRST_ON_SHORT_PATH` | `true` | Documented; short-path candidates already enter the existing patch loop |
| `INTERACTIVE_TEMPLATE_AUTO_PROMOTE` | `false` | Gate production promote |
| `INTERACTIVE_DIVERSITY_FALLBACK_ENABLED` | `true` | Allow MISS fallback on duplicate stem |
| `timeout.ai_engine.llm.max_concurrency` | `12` (was 10) | Overlapping fill + review |
| `timeout.ai_engine.runtime_qa.max_concurrency` | `6` (was 4) | Non-FC default; FC still serializes when `FC_DEPLOYMENT=true` and the store is unset |

## Yield

`scripts/run_generation_yield_batch.py` extra cases now include gas law (chem),
osmosis (bio), and enzyme-temp (bio), plus existing pendulum / Ohm / wave /
free-fall / population. Ledger records `kind`, `template_route`, `family_id`,
`recipe_id`, `elapsed_s`, `prompt_tokens`, `completion_tokens`.
`infra_maintenance` / `provider_transport` exclusion is unchanged.

## How to verify each family + MISS fallback

1. HIT short path (no live LLM required in unit tests):
   - pendulum → `time_integrator_1d`
   - Ohm / gas law / enzyme-temp → `param_formula_panel`
   - wave interference → `field_or_wave_2d`
   - plane-mirror optics → `geometric_ray_2d`
   - osmosis / population → `compartment_flow`
2. Assemble a shell with `assemble_short_path_document` and assert
   `shell_time_advance_contract_errors(html) == []`.
3. MISS: a mixed pendulum+wave+osmosis brief or a family-less optics
   variant (prism/lens without plane-mirror keywords) must call
   `code_generate.full` and still pass existing QA/review.
4. Live (optional): run yield cases and confirm HIT rows have lower
   `prompt_tokens` / `elapsed_s` than MISS rows, with `seedWorthy` still true.

Tool MISS (temperature converter) can still fail `interactive_validation` when
the full-generate model omits operable controls. Full path now injects a
readable title/h1 from the brief when those tags are missing; it does **not**
invent converter inputs. Treat missing controls as a known full-path flake.

```bash
cd packages/ai-engine
python -m pytest tests/test_generation_efficiency.py tests/test_interactive_creation.py tests/test_yield_batch.py tests/test_requested_platform.py tests/test_artifact_quality.py -q
```

## New / changed behavior (coverage matrix)

Use this list for a later exhaustive matrix.

| ID | Behavior | Path |
| --- | --- | --- |
| R1 | Science brief with distinctive recipe keywords → HIT | router |
| R2 | Partial family match → SOFT + default recipe | router |
| R3 | No family / mixed families → MISS | router |
| R4 | Tool without strong recipe → MISS | router |
| R5 | Iterate always MISS (`iterate_preserves_source`) | router |
| R6 | Ambiguous multi-family brief → MISS, never force-fit | router |
| S1 | HIT/SOFT first LLM step is `code_generate.template_fill` | short path |
| S2 | Fill JSON assembled onto DesktopRuntimeShell | short path |
| S3 | Full HTML fill response accepted as document | short path |
| S4 | Unparsed fill uses recipe default slots | short path |
| S5 | Short-path system uses ContractDigest, not full SYSTEM_PROMPT | short path |
| S6 | After short-path candidate, repair is patch-first | interactive_creation |
| S7 | Patch protocol exhaustion still full-regenerates on create | interactive_creation (unchanged rule) |
| T1 | Shell leftover accumulator advances sim time after start | L0 |
| T2 | Pause does not advance sim time | L0 |
| T3 | Start/pause/reset are distinct labeled controls | L0 |
| F1 | `param_formula_panel` Ohm / gas / enzyme / photosynthesis / F=ma | L1/L2 |
| F2 | `time_integrator_1d` pendulum / free fall | L1/L2 |
| F3 | `field_or_wave_2d` wave interference | L1/L2 |
| F4 | `compartment_flow` osmosis / population | L1/L2 |
| F5 | `geometric_ray_2d` plane-mirror optics | L1/L2 |
| D1 | Repeat stem swaps creative anchor | diversity |
| D2 | Repeat after swap raises visual tier | diversity |
| D3 | Repeat after raise → full generate | diversity |
| Q1 | Runtime QA / contract probes / structured review unchanged | QA |
| Q2 | `seed_worthy` still requires review_ran + pipeline success | labels |
| M1 | `template_route` + family/recipe + tokens on quality_breakdown | telemetry |
| M2 | Same fields on task_memory | telemetry |
| M3 | Runtime QA records `queue_wait_ms` | telemetry |
| Y1 | Yield ledger records kind, route, elapsed, tokens | yield |
| Y2 | infra_maintenance / provider_transport still excluded | yield |
| E1 | seed_worthy → registry staging | registry |
| E2 | Production promote gated unless auto-promote flag | registry |
| K1 | LLM concurrency default 12 | knobs |
| K2 | Runtime QA concurrency default 6 (non-FC) | knobs |
| G1 | Game arcade path not rewritten; `arcade_loop` stub only | out of scope |

## Out of scope (still deferred)

- Full auto-promote of templates to production without review
- Rewriting the arcade/game generation path
- Exhaustive experiment catalogs beyond subject × family recipes
