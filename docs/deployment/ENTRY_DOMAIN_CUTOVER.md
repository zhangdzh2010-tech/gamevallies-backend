# Gamevallies Entry Domain Cutover Guide

Last updated: 2026-03-24

This guide documents how to switch the public entry domain from
`https://www.gamevallies.com` to `https://gamevallies.com`.

Scope:

- public base URL and service URL configuration
- edge / DNS / certificate / platform updates
- post-deploy validation and rollback

Non-goal:

- This guide does not require application code changes for the domain string
  itself. The current services already derive public URLs from runtime
  configuration. The main task is to update those configuration values
  consistently and redeploy the affected services.

## 1. Current state and target state

Current canonical public URL:

- `https://www.gamevallies.com`

Target canonical public URL:

- `https://gamevallies.com`

After cutover:

- backend payloads must return `https://gamevallies.com/...` in `gameUrl`,
  `previewUrl`, cover URLs, and share URLs
- `www.gamevallies.com` may remain enabled as an alias or redirect, but it
  must stop being the canonical URL emitted by backend responses

## 2. Configuration values that must change

Update these production environment values in `.env.deploy`:

```bash
PUBLIC_API_BASE_URL=https://gamevallies.com
USER_SERVICE_URL=https://gamevallies.com
GAME_SERVICE_URL=https://gamevallies.com
FEED_SERVICE_URL=https://gamevallies.com
```

If `WECHAT_PAY_NOTIFY_URL` is explicitly configured, update it as well:

```bash
WECHAT_PAY_NOTIFY_URL=https://gamevallies.com/api/v1/subscription/wechat/notify
```

If CORS is restricted to explicit origins instead of `*`, also update the
origin allowlist to include `https://gamevallies.com`.

## 3. Why these values matter

The current services build public URLs from runtime configuration:

- `scripts/deploy.py` forwards `PUBLIC_API_BASE_URL`, `USER_SERVICE_URL`,
  `GAME_SERVICE_URL`, and `FEED_SERVICE_URL`, and derives `APP_URL` for
  deployed services.
- `packages/game-service/src/common/game-presenter.ts` builds `gameUrl` and
  `previewUrl` from `PUBLIC_API_BASE_URL` or `APP_URL`.
- `packages/feed-service/src/common/feed-presenter.ts` builds feed item
  `gameUrl`, `previewUrl`, and `coverUrl`.
- `packages/feed-service/src/feed/feed.service.ts`,
  `packages/feed-service/src/search/search.service.ts`, and
  `packages/feed-service/src/challenge/challenge.service.ts` construct public
  preview links from `PUBLIC_API_BASE_URL` or `GAME_SERVICE_URL`.
- `packages/feed-service/src/share/share.service.ts` builds public share URLs
  from `PUBLIC_API_BASE_URL` or `APP_URL`.
- `packages/user-service/src/billing/billing.service.ts` falls back to
  `PUBLIC_API_BASE_URL` when constructing the WeChat Pay notify callback URL.

Important note:

- `APP_URL` for `game-service` and `feed-service` is derived by
  `scripts/deploy.py` from `PUBLIC_API_BASE_URL` during normal deployment. Do
  not treat it as an independent production source of truth.

## 4. Files that must stay in sync

When the cutover decision is approved and executed, keep these files aligned:

- `.env.deploy`
- `.env.example`
- `.env.deploy.example`
- `docs/deployment/DEPLOY_RUNBOOK.md`
- `docs/deployment/ENV_SYNC_GUIDE.md`

Before the cutover window is approved, documentation may still describe the
current production domain as `https://www.gamevallies.com`. Do not mix
"planned target" values into the live production runbook unless the rollout
window is actually being executed.

## 5. External platform changes

Changing the backend env values is necessary but not sufficient. Also update:

- DNS for `gamevallies.com`
  The apex domain must resolve to the same production edge entry as the
  current public site.
- TLS / certificate coverage
  The certificate presented at the edge must include `gamevallies.com`.
- API gateway / CDN / edge custom-domain binding
  The apex domain must route to the same backend entry as the current public
  domain.
- WeChat `web-view` business domain
  Replace `https://www.gamevallies.com` with `https://gamevallies.com`.
- Frontend build configuration
  Update `TARO_APP_GAME_CONTENT_URL` to `https://gamevallies.com`.
- Payment platform callback configuration
  If the callback URL is maintained in a third-party console, update it to the
  apex notify URL.

Recommended follow-up:

- keep `www.gamevallies.com` alive during the transition
- configure `www` to redirect permanently to `https://gamevallies.com`
  after validation succeeds

## 6. Rollout procedure

### 6.1 Preflight

Before changing anything:

- record the current `.env.deploy` values for the four public URL variables
- confirm the apex domain already terminates TLS correctly
- confirm the apex domain is routed to the expected production entry
- confirm the deployment operator has access to the relevant WeChat / edge /
  DNS consoles

### 6.2 Update production env

Edit `.env.deploy` and set:

```bash
PUBLIC_API_BASE_URL=https://gamevallies.com
USER_SERVICE_URL=https://gamevallies.com
GAME_SERVICE_URL=https://gamevallies.com
FEED_SERVICE_URL=https://gamevallies.com
```

If explicitly configured:

```bash
WECHAT_PAY_NOTIFY_URL=https://gamevallies.com/api/v1/subscription/wechat/notify
```

### 6.3 Deploy affected services

Redeploy at least:

```bash
python scripts/deploy.py user-service
python scripts/deploy.py game-service
python scripts/deploy.py feed-service
```

Deploy `ai-engine` only if its CORS configuration must be updated and is not
already permissive enough for the new origin.

### 6.4 Update external platforms

During the same window:

- bind the apex domain at the edge if it is not already bound
- update WeChat `web-view` business domain
- update frontend `TARO_APP_GAME_CONTENT_URL`
- update payment callback configuration if it is managed outside the backend

## 7. Post-cutover validation

Run these checks against `https://gamevallies.com`:

```bash
curl https://gamevallies.com/api/v1/health
curl https://gamevallies.com/api/v1/games/explore/published?limit=1
curl https://gamevallies.com/api/v1/feed/latest?limit=1
curl https://gamevallies.com/33zqDBay4T.txt
```

Validation requirements:

- `/api/v1/health` returns `200`
- public feed / game list endpoints return `200`
- response payloads use `https://gamevallies.com` in:
  `gameUrl`, `previewUrl`, `coverUrl`, and share `url`
- a known-good published game preview returns `200 text/html` from
  `/games/<game-id>/preview`
- `POST /api/v1/auth/wechat/miniapp-login` no longer returns `404`
- if `WECHAT_PAY_NOTIFY_URL` is explicitly configured, its runtime value uses
  the apex domain

Recommended smoke checks after deployment:

- unauthenticated read endpoints on `/api/v1/feed/*`, `/api/v1/games/*`,
  `/api/v1/tags/*`, and `/api/v1/creators/*`
- authenticated / write routes should at least return the expected `401` or
  validation error when called without valid credentials, not `404`

## 8. Rollback

If the cutover must be reverted:

1. restore the previous `www` values in `.env.deploy`
2. redeploy `user-service`, `game-service`, and `feed-service`
3. revert any edge / DNS / WeChat / frontend / payment-console changes that
   were part of the cutover
4. rerun the validation suite against `https://www.gamevallies.com`

Rollback values:

```bash
PUBLIC_API_BASE_URL=https://www.gamevallies.com
USER_SERVICE_URL=https://www.gamevallies.com
GAME_SERVICE_URL=https://www.gamevallies.com
FEED_SERVICE_URL=https://www.gamevallies.com
```

## 9. Known observation before cutover

As of 2026-03-24:

- `https://gamevallies.com/api/v1/health` is already reachable
- the current backend payloads still emit `https://www.gamevallies.com/...`
  because the public URL environment values still point to `www`

Treat that as confirmation that the runtime already supports apex entry, while
the canonical URL configuration has not yet been switched.
