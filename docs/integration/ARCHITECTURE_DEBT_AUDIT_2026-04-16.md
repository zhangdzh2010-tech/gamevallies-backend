# Architecture Debt Audit 2026-04-16

This document records the current large-file and mixed-responsibility hotspots in the backend repository and proposes a staged refactor plan.

## Scope

This audit focused on two classes of structural debt:

- Unusually large source files that are hard to review, test, and evolve safely.
- Files that mix unrelated concerns or resource types, such as HTML with large inline CSS and JavaScript, or services that aggregate multiple domains.

This audit intentionally excludes:

- Generated lockfiles such as `package-lock.json`
- Historical design docs under `docs/`
- Sample or generated game HTML under `scripts/games/`, which are expected to be self-contained artifacts

## Largest Source Hotspots

Measured from tracked source files on 2026-04-16:

| File | Lines | Approx Size | Primary Concern |
| --- | ---: | ---: | --- |
| `packages/game-service/src/admin/admin-panel.html` | 11,748 | 446.6 KB | Monolithic admin UI with inline CSS and JS |
| `packages/game-service/src/game/game.service.ts` | 5,909 | 213.0 KB | Game command, orchestration, preview, quota, and async task coordination |
| `packages/game-service/src/admin/admin.service.ts` | 5,828 | 182.7 KB | Admin operations across games, users, billing, AI ops, configs, and analytics |
| `packages/ai-engine/src/services/llm_client.py` | 2,368 | 106.1 KB | Provider protocol handling, retries, routing, prompt shaping, streaming |
| `packages/ai-engine/src/engine/code_generator.py` | 2,352 | 111.6 KB | Large generation pipeline implementation |
| `packages/ai-engine/src/engine/pipeline_v2_runner.py` | 2,318 | 111.0 KB | Large pipeline orchestration implementation |
| `packages/ai-engine/src/engine/runtime_qa.py` | 2,231 | 103.8 KB | QA orchestration and checks |
| `packages/ai-engine/src/engine/dialogue_engine.py` | 2,107 | 88.1 KB | Intent parsing and dialogue-related logic |
| `packages/ai-engine/src/api/endpoints/generate.py` | 2,046 | 84.2 KB | Mixed API router for pipeline, admin refresh, legacy, health, and artifacts |
| `packages/game-service/src/game/creation-session.service.ts` | 1,153 | 37.2 KB | Session orchestration that is still manageable but growing quickly |

## Confirmed Structural Issues

### 1. `admin-panel.html` is a monolithic page app embedded inside a single HTML file

Evidence:

- Inline stylesheet starts at `packages/game-service/src/admin/admin-panel.html:7`
- Inline script starts at `packages/game-service/src/admin/admin-panel.html:5235`
- The file drives multiple pages and domains from one script:
  - dashboard loading at `packages/game-service/src/admin/admin-panel.html:5816`
  - game management at `packages/game-service/src/admin/admin-panel.html:5949`
  - user management at `packages/game-service/src/admin/admin-panel.html:6813`
  - generation logs at `packages/game-service/src/admin/admin-panel.html:7117`
  - prompt/config management at `packages/game-service/src/admin/admin-panel.html:8126`
- The controller reads and serves the raw file directly via `fs.readFileSync` at `packages/game-service/src/admin/admin.controller.ts:29`

Why this matters:

- Any admin UI change touches one giant file, which creates a merge hotspot.
- CSS, markup, and JavaScript cannot evolve independently.
- There is no meaningful asset-level caching.
- UI behavior is difficult to test in isolation because tab logic and API calls live in the same global script.

### 2. `admin.service.ts` has become a cross-domain admin monolith

Representative responsibilities inside one service:

- Games at `packages/game-service/src/admin/admin.service.ts:1488`
- Users at `packages/game-service/src/admin/admin.service.ts:2691`
- Subscription plans at `packages/game-service/src/admin/admin.service.ts:2926`
- Generation logs and tasks at `packages/game-service/src/admin/admin.service.ts:3549`
- Cloud accounts and regions at `packages/game-service/src/admin/admin.service.ts:3934`
- LLM providers and routes at `packages/game-service/src/admin/admin.service.ts:4243`
- System configs at `packages/game-service/src/admin/admin.service.ts:5432`
- Prompt bundles at `packages/game-service/src/admin/admin.service.ts:5788`
- Runtime profiles at `packages/game-service/src/admin/admin.service.ts:5847`

Why this matters:

- Changes in one admin domain increase risk in unrelated domains.
- Unit tests become broad and expensive because too many branches share the same class.
- Ownership boundaries are unclear: product admin, AI operations, billing, and content moderation all flow through one file.

### 3. `game.service.ts` mixes command orchestration, preview delivery, quotas, and async upstream task recovery

Representative responsibilities:

- Source spec compilation at `packages/game-service/src/game/game.service.ts:1346`
- Upstream async task submission at `packages/game-service/src/game/game.service.ts:2478`
- Upstream failover polling at `packages/game-service/src/game/game.service.ts:2602`
- Create flow at `packages/game-service/src/game/game.service.ts:3634`
- Publish flow at `packages/game-service/src/game/game.service.ts:5966`
- Iterate flow at `packages/game-service/src/game/game.service.ts:6067`

Other logic in the same file also covers preview URL creation, JWT preview access, timeout caches, entitlement checks, active-task sweeping, and task reconciliation.

Why this matters:

- The service is acting as both domain facade and infrastructure coordinator.
- The create and iterate flows are harder to reason about because they are surrounded by unrelated support concerns.
- Regression risk is high when touching task recovery, quota logic, or publishing because they share one file.

### 4. `generate.py` is a mixed router for multiple API surfaces

Representative concerns:

- Admin token validation at `packages/ai-engine/src/api/endpoints/generate.py:94`
- Progress and artifact relaying to `game-service` at `packages/ai-engine/src/api/endpoints/generate.py:102`
- Legacy-to-v2 request upgrades at `packages/ai-engine/src/api/endpoints/generate.py:399`
- Prompt cache refresh at `packages/ai-engine/src/api/endpoints/generate.py:1529`
- V2 pipeline endpoints at `packages/ai-engine/src/api/endpoints/generate.py:1870`
- Legacy generation endpoint at `packages/ai-engine/src/api/endpoints/generate.py:2028`
- Health endpoint at `packages/ai-engine/src/api/endpoints/generate.py:2148`

Why this matters:

- The file mixes runtime APIs, admin APIs, internal relays, health, and legacy compatibility.
- Router-level ownership is unclear, which makes future deletion of legacy paths harder.
- Endpoint tests must import a large module surface with many side concerns.

### 5. `llm_client.py` mixes provider adapters, retry policy, prompt shaping, concurrency, and streaming

Representative concerns:

- OpenAI-compatible URL shaping at `packages/ai-engine/src/services/llm_client.py:162`
- Usage extraction at `packages/ai-engine/src/services/llm_client.py:306`
- Retry backoff policy at `packages/ai-engine/src/services/llm_client.py:418`
- Prompt admission and compression at `packages/ai-engine/src/services/llm_client.py:898`
- Standard completion orchestration at `packages/ai-engine/src/services/llm_client.py:1415`
- Streaming orchestration at `packages/ai-engine/src/services/llm_client.py:1815`
- Anthropic client bootstrapping at `packages/ai-engine/src/services/llm_client.py:2328`
- OpenAI-compatible request execution at `packages/ai-engine/src/services/llm_client.py:2381`

Why this matters:

- Provider-specific behavior is coupled to general routing and compression policy.
- Introducing a new provider or retry rule becomes a high-risk edit.
- The class is too large to be the single seam for all LLM IO policy.

### 6. `game-shell.html` also mixes markup, CSS, and JavaScript, but is a lower-priority case

Evidence:

- Inline stylesheet at `packages/game-service/src/game/game-shell.html:8`
- Inline script at `packages/game-service/src/game/game-shell.html:62`

Assessment:

- This file is much smaller and behaves more like a dedicated shell template.
- It is still worth extracting once the admin panel is cleaned up, but it is not the first bottleneck.

## Recommended Refactor Priority

### Priority 0: Break the admin UI monolith

Target:

- Replace the single `admin-panel.html` blob with:
  - `packages/game-service/src/admin/ui/index.html`
  - `packages/game-service/src/admin/ui/admin-panel.css`
  - `packages/game-service/src/admin/ui/admin-panel.js`
  - `packages/game-service/src/admin/ui/modules/` for per-tab modules such as `dashboard.ts`, `games.ts`, `users.ts`, `logs.ts`, `prompts.ts`

Expected outcome:

- Faster iteration on admin UI
- Lower merge conflict frequency
- Cleaner review boundaries
- Easier future migration to a real frontend build if needed

### Priority 1: Split `AdminService` by domain

Suggested split:

- `AdminGamesService`
- `AdminUsersService`
- `AdminBillingService`
- `AdminGenerationOpsService`
- `AdminLlmOpsService`
- `AdminConfigService`
- `AdminRuntimeProfileService`

Rule:

- Keep `AdminController` thin and route each endpoint group to its domain service.

### Priority 1: Split `GameService` into domain facade plus coordinators

Suggested split:

- `GameCommandService` for create, iterate, publish
- `GamePreviewService` for preview tokens and URLs
- `GameAccessService` for entitlement and quota checks
- `GameUpstreamTaskService` for async task submission, polling, cancellation, and failover

Rule:

- `GameService` can remain a facade temporarily, but it should delegate instead of owning all implementation details.

### Priority 2: Split AI engine API routers by concern

Suggested split under `packages/ai-engine/src/api/endpoints/`:

- `pipeline.py`
- `pipeline_v2.py`
- `admin.py`
- `health.py`
- `cover.py`
- `legacy.py`

Rule:

- Keep shared helpers in a separate `endpoint_support/` module instead of embedding them in a router file.

### Priority 2: Split `LLMClient` into policy and provider layers

Suggested split:

- `llm_client/core.py` for public orchestration
- `llm_client/retry_policy.py`
- `llm_client/prompt_admission.py`
- `llm_client/providers/openai_compatible.py`
- `llm_client/providers/anthropic.py`
- `llm_client/usage.py`

Rule:

- Provider adapters should not own generic retry policy or prompt compression behavior.

## Suggested Delivery Sequence

### Stage 1: Low-risk structure extraction

- Extract admin CSS and JS from `admin-panel.html`
- Move admin UI logic into per-tab modules without changing UI behavior
- Add simple smoke tests for admin static asset serving

### Stage 2: Backend service decomposition

- Split `AdminService` first because the domain seams are already obvious
- Split `GameService` second, starting with upstream task coordination and preview access
- Keep controller contracts stable during the split

### Stage 3: AI engine modularization

- Break `generate.py` into routers
- Extract `LLMClient` provider adapters and retry policy
- Keep endpoint paths and payloads stable until after module extraction

### Stage 4: Prevent regression

- Add a file-size guardrail to CI
- Add an architectural lint rule or review checklist that flags:
  - source files above 1,500 lines
  - HTML files with large inline CSS or JS
  - service classes spanning multiple bounded contexts

## Immediate Next Steps

Recommended first refactor batch:

1. Extract `admin-panel.html` assets and split the JavaScript by tab.
2. Create `AdminConfigService` and move prompt, timeout, prompt bundle, and runtime profile logic out of `AdminService`.
3. Create `GameUpstreamTaskService` and move task submission, polling, and failover logic out of `GameService`.

This sequence delivers the best risk reduction without changing product behavior first.
