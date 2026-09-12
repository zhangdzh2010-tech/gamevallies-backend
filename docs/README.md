# Gamevallies Backend Docs

`docs/` contains active backend design, integration, deployment, and testing documents.

## Architecture

- [`architecture/generation-efficiency-memo-20260912.md`](./architecture/generation-efficiency-memo-20260912.md)
  User-approved science/tool short-path design: DesktopRuntimeShell, four
  interaction families, HIT/SOFT/MISS router, telemetry, and staging-only
  template mining. Quality bars unchanged.

## Top-Level Plans

- [`H5_TO_APP_DOWNLOAD_PROMO_PHASE1_PLAN_2026-04-13.md`](./H5_TO_APP_DOWNLOAD_PROMO_PHASE1_PLAN_2026-04-13.md)
  Phase 1 plan for the H5-to-App download promo. This version does not depend on backend shell adoption and includes frontend integration, backend APIs, admin configuration, and Android release upload management.
- [`H5_APP_DOWNLOAD_PROMO_PLAN_2026-04-03.md`](./H5_APP_DOWNLOAD_PROMO_PLAN_2026-04-03.md)
  Earlier shell-based draft. For Phase 1 rollout, use the 2026-04-13 document as the source of truth.
- [`backend_api_schema.md`](./backend_api_schema.md)
  Backend API schema reference.

## Directory Structure

### `deployment/`

  Production deployment runbook.
  Environment variable sync and maintenance guide.

### `integration/`

- [`integration/api-schema.md`](./integration/api-schema.md)
  Backend API, data model, and WebSocket contract documentation.
- [`integration/FRONTEND_ADAPTATION_P0_P2.md`](./integration/FRONTEND_ADAPTATION_P0_P2.md)
  Frontend adaptation plan for backend capabilities.
- [`integration/FRONTEND_DYNAMIC_CREATION_DIALOGUE_ADAPTATION_PLAN_2026-03-31.md`](./integration/FRONTEND_DYNAMIC_CREATION_DIALOGUE_ADAPTATION_PLAN_2026-03-31.md)
  Current frontend implementation guide for the creation-session confirm-and-generate flow.
- [`integration/ARCHITECTURE_DEBT_AUDIT_2026-04-16.md`](./integration/ARCHITECTURE_DEBT_AUDIT_2026-04-16.md)
  Audit of large-file and mixed-responsibility hotspots, plus the staged refactor plan.
- [`integration/GENERATION_PIPELINE_IMPROVEMENT_PLAN.md`](./integration/GENERATION_PIPELINE_IMPROVEMENT_PLAN.md)
  Generation pipeline improvement plan.
  Async task and LLM gateway design.
- [`integration/DYNAMIC_CREATION_DIALOGUE_DESIGN.md`](./integration/DYNAMIC_CREATION_DIALOGUE_DESIGN.md)
  Dynamic creation dialogue design.
- [`integration/GAME_GENERATION_LOGIC_BREAKDOWN.md`](./integration/GAME_GENERATION_LOGIC_BREAKDOWN.md)
  Current generation logic breakdown.
- [`integration/GAME_GENERATION_RUNTIME_REARCHITECTURE.md`](./integration/GAME_GENERATION_RUNTIME_REARCHITECTURE.md)
  Runtime rearchitecture design.

### `testing/`

- [`testing/TEST_SUITE.md`](./testing/TEST_SUITE.md)
  Test suite and execution notes.

### `reference/`

  Historical reference configuration sample.

## Maintenance Notes

- Prefer updating an existing document over creating near-duplicate plans.
- When a newer plan supersedes an older draft, mark that clearly in the new document.
- Keep deployment and integration docs current with the real running system rather than aspirational architecture.


- [阿里云部署与维护](deployment/ALIYUN_DEPLOYMENT.md)
