# Game-path telemetry and HIT/SOFT assessment

Arcade/game generations previously left `quality_breakdown.template_route` and
`family_id` unset. Science/tool already persist those fields via
`interactive_telemetry.py`. The yield ledger therefore recorded the 12 arcade
rows in a post-#88 50-run batch as MISSING (3× `simple_dodge_cn`,
`snake_classic_cn`, `memory_cards_cn`, `grid_puzzle_en`).

This note is telemetry + assessment only. It does **not** add game HIT shells
and does **not** lower the diversity gate or `seed_worthy` / review bars.

## What now ships

On the pipeline_v2 / casual_arcade full-generate path (create and iterate):

| Field | Value |
| --- | --- |
| `template_route` | `MISS` (existing enum: full `logic_generate`, not a failure) |
| `route_reason` | `game_full_generate` (distinguishes from science-router MISS) |
| `family_id` | canonical `runtime_profile` (stable string) |
| `recipe_id` | profile mechanic / `primary_goal` (`orbit_control`, `grid_completion`, …) |
| `mechanic_id` | advertised `core_mechanics[0].type` when present, else `recipe_id` |

Persisted on `quality_breakdown` and `task_memory` (`template_route`,
`template_family`, `template_recipe`). Yield extraction also reads camelCase,
`generation_efficiency`, and `task_meta`.

Science/tool HIT/SOFT/MISS behavior is unchanged. The merge helper will not
overwrite an existing HIT/SOFT + family pair.

## Diversity gate (must stay)

Game originality still depends on all of:

- `visual_pack` (theme/palette, not a reskin of a shared HTML page)
- `variation_seed`
- `creative_anchors`
- `gameplay_fingerprint` (mechanics + rules + selected creative concept)

A cookie-cutter skeleton that only swaps colors/copy is rejected. Any future
SOFT/HIT shell must keep these slots forced and must fall back to full generate
on near-duplicate fingerprints — same rule as science.

## Per-family HIT / SOFT recommendation

Default stance: **telemetry + stay FULL** unless a skeleton is proven
presentation-only (SOFT) or has a deterministic core with diversity slots like
science recipes (HIT).

| Yield family | Typical `family_id` | Recommendation | Why |
| --- | --- | --- | --- |
| Dodge (`simple_dodge_cn`) | `casual_arcade_orbit` / action-survival / arcade-rescue | **stay FULL** | Live spawn / move / collide / score **is** the originality surface. Runtime contract only names `orbit_control`; there is no playable skeleton. A HIT loop would cookie-cutter dodgers. |
| Snake (`snake_classic_cn`) | `casual_arcade*` | **stay FULL** | Grid + grow is conceptually deterministic, but no DesktopRuntimeShell plugin or forced diversity slots exist. Skins-only HIT would collapse originality. Revisit HIT only after a proven core with fingerprint-gated food/grid/feel slots. |
| Memory (`memory_cards_cn`) | `puzzle_grid*` | **stay FULL** (closest future SOFT) | Flip/match state is deterministic, but card count, match rules, and win/lose are gameplay, not chrome. SOFT only if a stub is presentation-only (art/theme/copy) with forced `visual_pack` / anchors / seed. Not proven today. |
| Grid (`grid_puzzle_en`) | `puzzle_grid_route` / match / merge | **stay FULL** | “Grid” is many puzzles. One HIT shell would flatten connect / match / merge. Needs per-recipe cores like science — none exist. |

No SOFT stub is shipped in this change. If one is added later it must keep
diversity forced and must not lower `seed_worthy` or review quality gates.

## How to verify

```bash
cd packages/ai-engine
python -m pytest -q tests/test_game_path_telemetry.py tests/test_generation_efficiency.py tests/test_quality_gate_patch_repair.py tests/test_pipeline_v2_runner.py tests/test_interactive_creation.py tests/test_yield_batch.py
```
