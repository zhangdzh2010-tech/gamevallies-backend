# Runtime QA P0 Refactor Plan

Last updated: 2026-03-26

## Summary

The current `runtime_simulation_qa` step is too heavy for the synchronous create and iterate critical path.
It currently combines browser launch, page load, first render wait, synthetic interaction, and final data collection
into one blocking Playwright step guarded only by a single coarse timeout.

This creates three systemic problems:

- The pipeline cannot distinguish `game failed runtime QA` from `QA infrastructure timed out`.
- One stuck browser phase can consume the entire Runtime QA budget and fail the whole generation.
- Published-game iterations are blocked by Runtime QA infrastructure instability even when contract QA already passed.

This document defines the `P0` refactor that can be implemented without redesigning the whole generation architecture.

## Production Symptoms

Observed on task `4595425c-a5a8-43ee-8a9c-d793fc334f88`:

- `logic_generate` slow call: about `175s`
- `contract_qa` plus `3` targeted remediations
- `qa_fix.mobile_layout` slow call: about `108s`
- `runtime_simulation_qa` entered at `2026-03-25 16:45:54Z`
- failed at `2026-03-25 16:55:54Z`
- final error: `Runtime QA unavailable: runtime_qa_timeout:600.00s`

This shows that Runtime QA is currently the last heavy blocking step after an already expensive chain.

## Root Cause

Current Runtime QA implementation in [runtime_qa.py](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/runtime_qa.py) does all of the following inside one outer timeout:

- launch Chromium
- create browser context and page
- inject instrumentation
- `set_content(...)`
- `wait_for_load_state("load")`
- sleep for initial render wait
- collect DOM and canvas fingerprints
- inject synthetic interaction
- sleep for post-interaction wait
- collect final runtime signals

If any internal sub-phase stalls, the system only reports a generic `runtime_qa_timeout`.

## P0 Goals

P0 is intentionally limited to reducing critical-path risk without redesigning all QA.

### P0.1 Separate infra-unavailable from runtime validation failure

Introduce explicit Runtime QA unavailable classification:

- `timeout`
- `infra_unavailable`
- `exception`

This prevents all Runtime QA failures from collapsing into the same operational bucket.

### P0.2 Add phase-level timeouts and metrics

Split Runtime QA into coarse internal phases:

- `launch`
- `content_load`
- `interaction`
- `collect`

Each phase gets its own timeout budget and duration metrics.

### P0.3 Soft-fail published iterations when Runtime QA infra is unavailable

For `iterate` on a currently `published` game:

- if `contract_qa` passes
- and Runtime QA is unavailable because of timeout or infra unavailability

then the iteration should still return a successful candidate result with warning metadata instead of failing the whole task.

This preserves the candidate bundle while keeping the live published version unchanged.

### P0.4 Surface Runtime QA warnings in response and task summary

Persist:

- `qa_warnings`
- `runtime_qa_report`
- phase metrics
- unavailable kind and phase

so admin and diagnostics can distinguish:

- code correctness problem
- Runtime QA infrastructure problem
- browser phase bottleneck

## P0 Non-Goals

P0 does not yet do the following:

- split smoke QA and soak QA into separate worker pools
- move heavy runtime validation fully async
- publish gating by risk tier
- change the contract QA architecture

Those belong to P1 and P2.

## Planned Implementation

### 1. Runtime QA result enrichment

Extend `RuntimeQAResult` with:

- `unavailable_kind`
- `unavailable_phase`
- `phase_metrics`

### 2. Runtime QA phase budgets

New timeout config keys:

- `timeout.ai_engine.runtime_qa.phase_launch_s`
- `timeout.ai_engine.runtime_qa.phase_content_load_s`
- `timeout.ai_engine.runtime_qa.phase_interaction_s`
- `timeout.ai_engine.runtime_qa.phase_collect_s`

These are caps for internal phase watchdogs.

### 3. Response model enrichment

Add to both v2 create and iterate responses:

- `qa_warnings`
- `runtime_qa_report`

### 4. Iterate soft-fail guard

Only for `iterate` when `existing_game.status == "published"`:

- if Runtime QA unavailable kind is `timeout` or `infra_unavailable`
- do not raise pipeline failure
- emit warning progress details
- return successful candidate bundle with warning payload

### 5. Game-service persistence

Persist warning metadata into:

- bundle metadata
- generation task `resultSummary`

so admin can inspect warnings without digging only through artifacts.

## P1 Direction

After P0 lands, the next architectural step should be:

- keep `contract_qa` in the blocking main path
- shrink `runtime_simulation_qa` into a lightweight smoke check
- move heavy runtime soak validation to async sidecar execution

## P2 Direction

Longer term:

- risk-tiered QA by runtime profile
- delta-aware iterate QA
- dedicated browser worker pool
- publish policy that differentiates smoke-pass from soak-pass
