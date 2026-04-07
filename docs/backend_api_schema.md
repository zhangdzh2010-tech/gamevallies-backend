# Backend Admin API Schema

## Scope

This document summarizes the management-backend APIs exposed by `game-service` for the admin panel.

Source of truth:

- `packages/game-service/src/admin/admin.controller.ts`
- `packages/game-service/src/admin/admin-panel.html`

## Base URL

- Admin HTML page: `/admin`
- Admin API base: `/api/v1/admin`

The current admin panel hardcodes:

```js
const API_BASE = window.location.origin + '/api/v1/admin';
```

## Authentication

All admin APIs require the request header below unless explicitly noted otherwise:

```http
x-admin-token: <ADMIN_TOKEN>
```

Validation happens in `checkAdminToken(...)` inside `packages/game-service/src/admin/admin.controller.ts`.

## Common Response Shape

Most admin APIs return the standard wrapper:

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

Implemented by `packages/game-service/src/common/api-response.ts`.

## Current Admin Panel APIs

These endpoints are already called by `packages/game-service/src/admin/admin-panel.html`.

### Dashboard

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/stats` | Dashboard statistics | `from`, `to` |

### Game Management

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/games` | Paginated game list | `page`, `limit`, `search`, `status` |
| GET | `/api/v1/admin/games/:id` | Game detail for admin modal | path `id` |
| POST | `/api/v1/admin/games` | Create game manually | title, description, authorId, status, visibility, etc. |
| PUT | `/api/v1/admin/games/:id` | Update game metadata | title, description, status, visibility, allowComments, allowFork, etc. |
| PUT | `/api/v1/admin/games/:id/cover` | Update or replace game cover | `imageDataUrl`, `imageUrl`, `fileName` |
| DELETE | `/api/v1/admin/games/:id` | Delete one game | path `id` |
| POST | `/api/v1/admin/games/batch-status` | Batch publish / unpublish | `ids`, `status` |
| POST | `/api/v1/admin/games/batch-delete` | Batch delete selected games | `ids` |
| POST | `/api/v1/admin/game-status/:id` | Toggle one game's status quickly | `status` |
| POST | `/api/v1/admin/games/refresh-types` | Recompute `gameType` for existing games | optional `dryRun` |

### User Management

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/users` | Paginated user list | `page`, `limit`, `search`, `role` |
| GET | `/api/v1/admin/users/:id` | User detail | path `id` |
| POST | `/api/v1/admin/users` | Create user | username, displayName, email, password, role, etc. |
| PUT | `/api/v1/admin/users/:id` | Update user | editable user fields |
| DELETE | `/api/v1/admin/users/:id` | Delete user | path `id` |
| POST | `/api/v1/admin/user-password/:id` | Reset user password | `password` |

### Generation Logs and Tasks

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/genlog` | Generation log list | `page`, `limit`, `status`, `search` |
| GET | `/api/v1/admin/tasks` | Generation task list | `page`, `limit`, `status`, `search` |
| GET | `/api/v1/admin/tasks/:id` | Generation task detail | path `id` |
| POST | `/api/v1/admin/tasks/:id/terminate` | Force terminate task | optional `reason` |

### Subscription Plans

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/subscription/plans` | List subscription plans | `from`, `to` |
| POST | `/api/v1/admin/subscription/plans` | Create subscription plan | plan payload |
| PUT | `/api/v1/admin/subscription/plans/:id` | Update subscription plan | plan payload |
| DELETE | `/api/v1/admin/subscription/plans/:id` | Delete subscription plan | path `id` |

### Config and Prompt Management

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/configs` | List system configs | `category` |
| PUT | `/api/v1/admin/configs/:key` | Update one config value | `{ "value": ... }` |
| POST | `/api/v1/admin/configs/init-prompts` | Initialize prompt configs | none |
| POST | `/api/v1/admin/configs/init-timeouts` | Initialize timeout configs | none |
| POST | `/api/v1/admin/configs/refresh-timeouts` | Refresh timeout configs from catalog and upstream | none |

### LLM Provider / Route Management

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/llm/providers` | List LLM providers | none |
| POST | `/api/v1/admin/llm/providers` | Create provider | provider payload |
| PUT | `/api/v1/admin/llm/providers/:id` | Update provider | provider payload |
| DELETE | `/api/v1/admin/llm/providers/:id` | Delete provider | path `id` |
| POST | `/api/v1/admin/llm/providers/:id/test` | Provider connectivity test | none |
| POST | `/api/v1/admin/llm/providers/catalog/preview` | Preview provider model catalog | preview payload |
| POST | `/api/v1/admin/llm/providers/:id/test-chat` | Run chat test against one provider | test-chat payload |
| GET | `/api/v1/admin/llm/providers/:id/test-records` | Recent provider test records | `limit` |
| GET | `/api/v1/admin/llm/routes` | List LLM routes | `executionRegion` |
| POST | `/api/v1/admin/llm/routes` | Create route | route payload |
| PUT | `/api/v1/admin/llm/routes/:id` | Update route | route payload |
| POST | `/api/v1/admin/llm/refresh` | Refresh gateway runtime config | none |
| GET | `/api/v1/admin/llm/steps` | List available step keys / step catalog | none |
| GET | `/api/v1/admin/cloud/ai-engine-region-targets` | List selectable ai-engine region targets | `providerSelectableOnly` |

### Admin Token

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| POST | `/api/v1/admin/change-token` | Change admin token | `newToken` |

## Admin APIs Exposed by Backend but Not Yet Wired in Current Panel

These endpoints exist in `admin.controller.ts`, but there is no direct call site in the current `admin-panel.html`.

### Game Maintenance

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| POST | `/api/v1/admin/games/repair-legacy-preview-links` | Repair legacy preview links in historical game data | `limit`, `dryRun`, `gameIds` |
| POST | `/api/v1/admin/games/backfill-covers` | Regenerate / backfill historical game covers | `limit`, `dryRun`, `gameIds`, `overwriteExisting` |

### Task Inspection

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/tasks/:id/events` | Task event stream for one generation task | `limit` |
| GET | `/api/v1/admin/tasks/:id/artifacts` | Task artifact list for one generation task | `limit` |

### Cloud / Deployment Metadata

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/cloud/accounts` | List cloud provider accounts | none |
| GET | `/api/v1/admin/cloud/regions` | List cloud regions | none |
| POST | `/api/v1/admin/cloud/ai-engine-region-targets` | Create ai-engine region target | region target payload |
| PUT | `/api/v1/admin/cloud/ai-engine-region-targets/:id` | Update ai-engine region target | region target payload |
| POST | `/api/v1/admin/cloud/ai-engine-region-targets/sync-deploy` | Sync deploy state from runtime / cloud side | sync payload |

### LLM Deep Management

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/llm/routes/:id` | Get one LLM route detail | path `id` |
| DELETE | `/api/v1/admin/llm/routes/:id` | Delete one LLM route | path `id` |

### Config / Metadata Catalog

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/configs/:key` | Get one config item | path `key` |
| GET | `/api/v1/admin/prompt-bundles` | List prompt bundles | `status` |
| GET | `/api/v1/admin/runtime-profiles` | List runtime profiles | `enabledOnly` |

### Maintenance

| Method | Path | Purpose | Main Query / Body |
| --- | --- | --- | --- |
| POST | `/api/v1/admin/migrate` | Run admin-triggered migration task | none |

## Notes

- `GET /admin` serves the HTML panel directly and is intentionally outside the `/api/v1` prefix.
- Current panel stores the token in `localStorage` under `gv_admin_token`.
- The panel currently talks only to `game-service`; some admin operations are relayed by `game-service` to `ai-engine`.
- A few endpoints above accept broad JSON payloads and rely on `AdminService` validation logic rather than strict DTOs, so request-body shape should be treated as service-defined rather than fully schema-locked.
