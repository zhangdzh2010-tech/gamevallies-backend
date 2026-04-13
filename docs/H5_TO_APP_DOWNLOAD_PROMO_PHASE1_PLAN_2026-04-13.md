# H5 To App Download Promo Phase 1 Plan

Date: 2026-04-13

Status: Draft

Supersedes for Phase 1 rollout:

- `docs/H5_APP_DOWNLOAD_PROMO_PLAN_2026-04-03.md`

## 1. Background

The current cold-start strategy is still H5-first.

We want to guide qualified H5 users to install the native app after they have already received value, instead of forcing an app download before they can try the product.

This document updates the earlier shell-based idea with the current code reality:

- Phase 1 must not depend on backend `game-shell.html`
- the current live frontend can already host the promo directly
- backend capabilities must also be included in Phase 1, not postponed

## 2. Current State

### 2.1 Frontend reality

The current frontend has two real entry points that can host the promo without using the backend shell:

- create success state:
  - `gamevallies-frontend/src/pages/create/index.jsx`
- H5 game play host:
  - `gamevallies-frontend/src/components/common/GamePlayer.jsx`
  - `gamevallies-frontend/src/stores/gamePlayer.js`
  - `gamevallies-frontend/src/pages/game/detail/index.jsx`

Important notes:

- `GamePlayer.jsx` already renders the H5 game inside the frontend overlay iframe
- `gamePlayer.openGame()` is already the unified open-play entry for H5
- the frontend also contains a `web-shell` route and can build a shell URL, but the current env does not enable the backend shell as the default live path

### 2.2 Backend reality

The backend already provides:

- a generic system config model:
  - `prisma/schema.prisma` -> `SystemConfig`
- admin config CRUD:
  - `packages/game-service/src/admin/admin.controller.ts`
  - `packages/game-service/src/admin/admin.service.ts`
- an existing admin panel page:
  - `packages/game-service/src/admin/admin-panel.html`

The backend does not yet provide a dedicated app release management module, download promo event model, or app upload pipeline.

## 3. Product Goal

Phase 1 should achieve four things:

- improve H5-to-App conversion without blocking H5 trial
- support fast operational control through the admin page
- support Android package upload and release metadata management in backend
- keep rollout low-risk, switchable, and measurable

## 4. Non-Goals

Phase 1 does not include:

- forcing app download before H5 trial
- requiring backend shell adoption before launch
- injecting popup logic into generated game HTML
- enterprise iOS package distribution workflow

For iOS public distribution, Phase 1 manages App Store link and version metadata rather than uploading `.ipa` files through the admin panel.

## 5. Phase 1 Scope

### 5.1 Frontend scope

- show a download promo after create success
- show a download promo after repeated H5 play or enough H5 play time
- local frequency control on device
- environment-based link routing
- event reporting to backend

### 5.2 Backend scope

- public promo bootstrap API
- promo event reporting API
- admin promo config management
- admin app release management
- Android package upload support
- iOS release metadata management

### 5.3 Admin scope

- app promo strategy configuration
- app copy and scene configuration
- Android release upload and publish
- iOS App Store link and release metadata management
- current active release selection

## 6. Phase 1 Product Decision

Phase 1 will not use backend `game-shell.html` as a dependency.

Instead, the promo will be hosted directly by the current frontend containers:

- create success promo on `src/pages/create/index.jsx`
- play-nudge promo on `src/components/common/GamePlayer.jsx`

This keeps the first rollout aligned with the real live path and avoids blocking on shell migration.

## 7. User Journey Design

## 7.1 Scene A: Create Complete

Trigger:

- user successfully finishes game creation

Entry point:

- `gamevallies-frontend/src/pages/create/index.jsx`

Behavior:

- wait 2 to 3 seconds after success UI becomes stable
- show a bottom sheet
- emphasize creator value in app:
  - continue editing
  - continue publishing
  - continue managing
  - continue replaying

Frequency:

- max 1 impression per device every 7 days
- if clicked download, suppress 30 days
- if dismissed twice, suppress 14 days

## 7.2 Scene B: Repeated H5 Play

Trigger:

- local play sessions >= 3
- or local total play duration >= 180 seconds

Entry point:

- `gamevallies-frontend/src/stores/gamePlayer.js`
- `gamevallies-frontend/src/components/common/GamePlayer.jsx`

Behavior:

- do not show within the first 30 seconds of the current play session
- show a lighter play-context sheet instead of a full blocking dialog
- if dismissed during the current game session, do not show again in the same session

Frequency:

- max 1 impression every 72 hours per device
- max 3 impressions every 30 days

## 8. Frontend Technical Design

## 8.1 New frontend modules

Recommended new files in `gamevallies-frontend`:

- `src/components/common/AppDownloadPromo.jsx`
- `src/components/common/AppDownloadPromo.scss`
- `src/stores/appDownloadPromoStore.js`
- `src/utils/appDownloadPromo.js`
- `src/services/growth.js`

## 8.2 Shared state and storage

Reuse the existing Taro storage pattern in:

- `gamevallies-frontend/src/utils/storage.js`

Recommended storage keys:

- `gamevallies_app_download_promo_device_id`
- `gamevallies_app_download_promo_state`
- `gamevallies_app_download_promo_play_stats`

Suggested state shape:

```json
{
  "deviceId": "uuid",
  "scenes": {
    "create_complete": {
      "lastShownAt": 0,
      "dismissCount": 0,
      "lastClickAt": 0,
      "impressions": []
    },
    "play_nudge": {
      "lastShownAt": 0,
      "dismissCount": 0,
      "lastClickAt": 0,
      "impressions": [],
      "sessionCount": 0,
      "totalPlaySeconds": 0
    }
  }
}
```

## 8.3 Runtime gating

Promo must only run in H5 browser.

Do not show promo in:

- WeChat Mini Program runtime
- native Capacitor app runtime

Frontend should add a shared runtime helper:

- `isNativeAppRuntime()`

Expected gating:

- H5 browser: enabled
- WeApp: disabled
- Native app: disabled

## 8.4 Download target routing

Frontend should resolve download target by environment:

- iOS Safari:
  - prefer universal link
  - fallback to App Store link
- Android browser:
  - prefer universal link or download landing page
  - fallback to Android package URL
- WeChat browser:
  - show "open in browser" guidance layer
- desktop:
  - show QR code plus platform links

## 8.5 Create success integration

Integrate in:

- `gamevallies-frontend/src/pages/create/index.jsx`

Implementation notes:

- watch `currentGame` terminal success state
- wait 2500ms before evaluating popup
- cancel popup if user already proceeds into play flow immediately
- mount promo near existing `GlobalGamePlayer` and `PaywallPopup`

## 8.6 Play-nudge integration

Integrate in:

- `gamevallies-frontend/src/stores/gamePlayer.js`
- `gamevallies-frontend/src/components/common/GamePlayer.jsx`

Implementation notes:

- increment play session count in `openGame()`
- start duration timer after iframe `onLoad`
- count only while page is visible and player is still open
- show promo in the outer overlay, not inside the iframe content

## 9. Backend Technical Design

## 9.1 Summary

Phase 1 backend should include four parts:

- promo config bootstrap
- promo event collection
- app release management
- admin panel management UI

## 9.2 Public APIs

### GET `/api/v1/growth/app-promo/bootstrap`

Purpose:

- provide the frontend with everything required to render the promo without hardcoding operational values in the app build

Recommended response:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "enabled": true,
    "wechatMode": "guide_to_browser",
    "scenes": {
      "createComplete": {
        "enabled": true,
        "cooldownHours": 168,
        "maxImpressions30d": 1
      },
      "playNudge": {
        "enabled": true,
        "minSessions": 3,
        "minSeconds": 180,
        "cooldownHours": 72,
        "maxImpressions30d": 3
      }
    },
    "copy": {
      "createComplete": {
        "title": "下载 App，继续管理你的作品",
        "body": "继续编辑、优化、发布和管理你的游戏。",
        "primaryCta": "下载 App",
        "secondaryCta": "继续使用 H5"
      },
      "playNudge": {
        "title": "在 App 中体验更完整",
        "body": "更流畅地试玩、收藏和长期管理你的游戏体验。",
        "primaryCta": "下载 App",
        "secondaryCta": "继续试玩"
      },
      "wechatGuide": {
        "title": "请在浏览器中打开",
        "body": "当前环境不支持直接下载，请先在浏览器中打开页面。"
      }
    },
    "releases": {
      "ios": {
        "versionName": "1.0.3",
        "downloadUrl": "https://apps.apple.com/app/...",
        "qrCodeUrl": "https://cdn.example.com/ios-qr.png"
      },
      "android": {
        "versionName": "1.0.3",
        "downloadUrl": "https://cdn.example.com/gamevallies-1.0.3.apk",
        "qrCodeUrl": "https://cdn.example.com/android-qr.png"
      }
    },
    "links": {
      "universalUrl": "https://app.gamevallies.com/download"
    }
  }
}
```

### POST `/api/v1/growth/app-promo/events`

Purpose:

- record promo exposure and click funnel

Recommended request body:

```json
{
  "scene": "play_nudge",
  "eventType": "click_download",
  "deviceId": "stable-anonymous-id",
  "gameId": "uuid-or-null",
  "platform": "android",
  "channel": "frontend_h5_player",
  "extra": {
    "playSessions": 4,
    "playSeconds": 235,
    "inWechat": false
  }
}
```

Allowed `eventType` values:

- `impression`
- `dismiss`
- `click_download`
- `click_continue_h5`

## 9.3 Admin APIs

Phase 1 should add dedicated admin APIs instead of overloading raw config CRUD for every structured operation.

Recommended admin routes:

- `GET /api/v1/admin/app-promo/config`
- `PUT /api/v1/admin/app-promo/config`
- `GET /api/v1/admin/app-releases`
- `POST /api/v1/admin/app-releases`
- `PUT /api/v1/admin/app-releases/:id`
- `POST /api/v1/admin/app-releases/:id/publish`
- `POST /api/v1/admin/app-releases/upload`
- `GET /api/v1/admin/app-promo/events`

Notes:

- promo config may still be persisted on top of `SystemConfig`
- app release management should use a dedicated table
- upload should be admin-only

## 9.4 Data model design

### Reuse existing `SystemConfig`

Use `SystemConfig` for low-frequency operational config:

- `growth.app_promo.enabled`
- `growth.app_promo.wechat_mode`
- `growth.app_promo.scene_create_complete_enabled`
- `growth.app_promo.scene_play_nudge_enabled`
- `growth.app_promo.play_nudge_min_sessions`
- `growth.app_promo.play_nudge_min_seconds`
- `growth.app_promo.copy_json`
- `growth.app_promo.universal_url`

### New table: `AppRelease`

Recommended Prisma model:

- `id`
- `platform`
- `channel`
- `versionName`
- `buildNumber`
- `releaseNotes`
- `downloadUrl`
- `qrCodeUrl`
- `fileName` nullable
- `fileSize` nullable
- `mimeType` nullable
- `storageKey` nullable
- `checksumSha256` nullable
- `sourceType`
- `status`
- `isActive`
- `publishedAt` nullable
- `createdBy`
- `createdAt`
- `updatedAt`

Recommended enums:

- `AppReleasePlatform`
  - `ios`
  - `android`
- `AppReleaseSourceType`
  - `upload`
  - `external_url`
  - `app_store`
- `AppReleaseStatus`
  - `draft`
  - `published`
  - `archived`

Why a dedicated table is needed:

- release metadata is structured and versioned
- active release switching should not rely on many flat config keys
- audit history matters

### New table: `AppPromoEvent`

Recommended fields:

- `id`
- `scene`
- `eventType`
- `userId` nullable
- `gameId` nullable
- `deviceId`
- `platform`
- `channel`
- `userAgent`
- `ipHash` nullable
- `extraJson`
- `createdAt`

## 9.5 Upload design

Phase 1 should support Android package upload through the admin page.

Recommended rule by platform:

- Android:
  - support package upload and external URL fallback
- iOS:
  - manage App Store link and release metadata
  - do not require `.ipa` upload for public release

Recommended upload flow for Android:

1. admin opens release management page
2. admin creates or edits an Android release draft
3. admin uploads APK
4. backend validates file type and size
5. backend stores file into object storage
6. backend writes release metadata and public download URL
7. admin publishes the release

Recommended storage env additions:

- `APP_RELEASE_STORAGE_PROVIDER`
- `APP_RELEASE_STORAGE_BUCKET`
- `APP_RELEASE_STORAGE_REGION`
- `APP_RELEASE_STORAGE_PUBLIC_BASE_URL`
- `APP_RELEASE_STORAGE_ACCESS_KEY_ID`
- `APP_RELEASE_STORAGE_ACCESS_KEY_SECRET`

If signed direct upload is too costly for the first engineering week, a backend multipart upload endpoint is acceptable for Phase 1.

## 9.6 Validation rules

Android upload validation:

- only `.apk`
- configurable max file size
- capture file size and checksum
- publish only after upload succeeds

iOS release validation:

- require App Store URL
- require version name
- allow release notes and QR code URL

## 10. Admin Panel Design

The existing admin panel should add a new growth section rather than a separate standalone system.

Primary location:

- `packages/game-service/src/admin/admin-panel.html`

Recommended new admin modules:

## 10.1 App Promo Config

Fields:

- global enable switch
- create-complete enable switch
- play-nudge enable switch
- create-complete cooldown
- play-nudge minimum sessions
- play-nudge minimum seconds
- play-nudge cooldown
- max impressions in 30 days
- WeChat mode
- universal URL
- per-scene copy

## 10.2 App Release Management

Features:

- list current iOS and Android releases
- create new draft release
- upload Android APK
- fill iOS App Store link
- edit release notes
- publish one release as active
- archive old release

List columns:

- platform
- version
- build
- source type
- status
- file size
- active flag
- published time
- operator

## 10.3 Promo Event Overview

Minimum Phase 1 capability:

- list recent promo events
- filter by scene
- filter by event type
- filter by platform
- show daily counts

A full chart dashboard is optional, but event browsing and export are recommended in Phase 1.

## 11. Recommended Phase 1 Copy

### Create Complete

Title:

- 下载 App，继续管理你的作品

Body:

- 继续编辑、优化、发布和管理你的游戏。

Primary CTA:

- 下载 App

Secondary CTA:

- 继续使用 H5

### Play Nudge

Title:

- 在 App 中体验更完整

Body:

- 更流畅地试玩、收藏和长期管理你的游戏体验。

Primary CTA:

- 下载 App

Secondary CTA:

- 继续试玩

## 12. Metrics

Track at least:

- impression count
- dismiss count
- click_download count
- continue_h5 count
- CTR by scene
- CTR by platform
- create-complete vs play-nudge performance

## 13. Rollout Plan

### Step 1

- backend tables and admin config endpoints
- admin panel config section
- bootstrap API

### Step 2

- Android release management and upload flow
- iOS release metadata management

### Step 3

- frontend `create_complete` popup

### Step 4

- frontend `play_nudge` popup
- promo event reporting

### Step 5

- operational tuning based on first-week data

## 14. Risks and Mitigations

Risk:

- showing promo too early hurts H5 retention

Mitigation:

- keep thresholds configurable from admin

Risk:

- Android upload pipeline adds backend operational complexity

Mitigation:

- keep iOS to metadata management only
- allow Android external URL fallback

Risk:

- direct game URL traffic bypasses frontend host and cannot show promo

Mitigation:

- accept this in Phase 1
- revisit shell or landing wrapper in Phase 2

## 15. Final Phase 1 Decision

Phase 1 will launch with:

- frontend page-level promo integration without backend shell dependency
- backend promo bootstrap and event APIs
- admin promo strategy configuration
- admin Android package upload and release management
- admin iOS App Store metadata management

This gives the business an end-to-end usable first release without waiting for shell migration.
