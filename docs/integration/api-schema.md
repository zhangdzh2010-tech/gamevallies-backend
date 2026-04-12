# API Schema

当前文档描述 `gamevallies-backend` 已实现的前端契约，以及从整体业务逻辑出发建议继续补齐的 API。

更新时间：`2026-04-01`

---

## 服务地址

| 服务 | 开发环境 | 说明 |
|------|---------|------|
| 用户/认证 | `http://localhost:3001` | 前端直连 |
| 游戏 | `http://localhost:3002` | 前端直连 |
| 社交 | `http://localhost:3003` | 前端直连 |
| Feed | `http://localhost:3004` | 前端直连 |
| AI 引擎 | `http://localhost:8000` | 主要供 `game-service` 内部调用 |
| WebSocket | `http://localhost:3002/ws` | `Socket.IO namespace`，不是原生 `ws://` |
| 游戏内容 | `http://localhost:3002` | HTML 由 `game-service` 直接托管 |

---

## 通用规范

### 请求头

除注册、登录、验证码相关接口外，其余接口默认携带：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

### 响应体格式

#### 格式 A

认证、游戏、社交、Feed 服务统一使用：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

#### 格式 B

无前缀的用户兼容路由使用：

```json
{
  "success": true,
  "data": {}
}
```

### 分页对象

```json
{
  "items": [],
  "hasMore": true,
  "page": 1,
  "limit": 10,
  "total": 100
}
```

---

## 数据模型

### User

```json
{
  "id": "string",
  "username": "string",
  "email": "string",
  "phone": "string",
  "avatar": "string",
  "avatarUrl": "string",
  "bio": "string",
  "displayName": "string",
  "followerCount": 0,
  "followingCount": 0,
  "gameCount": 0,
  "totalPlays": 0,
  "createdAt": "ISO8601"
}
```

### Game

```json
{
  "id": "string",
  "title": "string",
  "description": "string",
  "status": "generating | ready | published | banned",
  "gameUrl": "string",
  "previewUrl": "string",
  "coverUrl": "string",
  "tags": ["string"],
  "type": "string",
  "plays": 0,
  "likes": 0,
  "forks": 0,
  "authorId": "string",
  "author": {
    "id": "string",
    "username": "string",
    "avatar": "string"
  },
  "createdAt": "ISO8601",
  "publishedAt": "ISO8601"
}
```

### Comment

```json
{
  "id": "string",
  "gameId": "string",
  "content": "string",
  "authorId": "string",
  "author": {
    "id": "string",
    "username": "string",
    "avatar": "string"
  },
  "likes": 0,
  "parentId": "string | null",
  "replyCount": 0,
  "createdAt": "ISO8601"
}
```

### Notification

```json
{
  "id": "string",
  "type": "like | comment | follow | fork | system",
  "title": "string",
  "body": "string",
  "isRead": false,
  "data": {
    "targetId": "string",
    "userId": "string",
    "gameId": "string"
  },
  "createdAt": "ISO8601"
}
```

### CreationSessionSnapshot

```json
{
  "id": "string",
  "streamPath": "/api/v1/games/creation-sessions/<sessionId>/events",
  "status": "initializing | collecting | ready | generating | completed | failed | abandoned",
  "entryMode": "create | fork | iterate",
  "initialPrompt": "string",
  "titleDraft": "string | null",
  "revision": 1,
  "slotState": {
    "game_type": "funny",
    "core_mechanic": "tap to hide",
    "theme": "office",
    "input_method": "tap",
    "win_condition": "string | null",
    "difficulty": "string | null"
  },
  "missingRequired": ["win_condition", "difficulty"],
  "skippedSlots": [],
  "currentQuestion": {
    "slotKey": "win_condition",
    "label": "Win Condition",
    "prompt": "玩家怎么才算赢？",
    "skippable": true
  },
  "conversation": [
    { "role": "user", "content": "string", "kind": "prompt | answer", "createdAt": "ISO8601" },
    { "role": "assistant", "content": "string", "kind": "question | system", "createdAt": "ISO8601" }
  ],
  "slotFillPct": 0.67,
  "readyToGenerate": false,
  "generatedGameId": "string | null",
  "generationTaskId": "string | null",
  "sourceGameId": "string | null",
  "orientation": "portrait | landscape | null",
  "generationTier": "safe | standard | showcase",
  "questionBudget": 4,
  "planDraft": {
    "title": "string",
    "summary": "string",
    "concept": "string",
    "interaction": "string",
    "objective": "string",
    "pacing": "string",
    "visualDirection": "string",
    "signatureMoment": "string"
  },
  "confidenceSummary": {
    "overallConfidence": 0.72,
    "strongestSlots": ["game_type", "core_mechanic"],
    "weakestSlots": ["difficulty", "win_condition"],
    "ambiguityFlags": ["win_condition:missing"],
    "missingCriticalSlots": ["win_condition"]
  },
  "questionStrategy": {
    "mode": "missing_required | low_confidence | ambiguity_resolution",
    "slotKey": "win_condition",
    "reason": "string",
    "impact": 0.95,
    "confidence": 0,
    "ambiguityWeight": 0.25
  },
  "intentBuild": {
    "brief": "string | null"
  },
  "metadata": {
    "initError": "string | null",
    "abandonedAt": "ISO8601 | null"
  }
}
```

说明：

- `status` 生命周期：`initializing` → `collecting` → `ready` → `generating` → `completed`。任何阶段都可转为 `abandoned`。
- `initializing`：POST 创建后立即返回此状态（<200ms），AI 分析在后台异步运行（2–5s）。前端应显示加载骨架屏。
- `collecting`：AI 分析完成，正在向用户收集信息。`currentQuestion` 非空。
- `ready`：已收集足够信息，可触发生成。一旦进入 `ready` 不会回退到 `collecting`。
- `revision`：乐观并发控制（CAS），每次更新递增。前端发送请求时应携带当前 `revision`，不匹配返回 `409`。
- `currentQuestion`：仅在 `collecting` 状态下有值，`initializing`/`ready`/`generating`/`completed` 下为 `null`。

---

## 认证服务 (`3001`)

### POST `/api/v1/auth/register`

```json
{
  "username": "string",
  "email": "string",
  "phone": "string",
  "password": "string (min 8)",
  "verificationCode": "string (optional)"
}
```

```json
{
  "code": 0,
  "data": {
    "user": { "...User": "..." }
  }
}
```

### POST `/api/v1/auth/login`

```json
{
  "account": "string",
  "password": "string"
}
```

```json
{
  "code": 0,
  "data": {
    "token": "string",
    "refreshToken": "string",
    "user": { "...User": "..." }
  }
}
```

### POST `/api/v1/auth/refresh`

```json
{
  "refreshToken": "string"
}
```

```json
{
  "code": 0,
  "data": {
    "token": "string",
    "refreshToken": "string"
  }
}
```

### POST `/api/v1/auth/logout`

```json
{
  "code": 0,
  "data": null
}
```

说明：服务会从请求头 `x-refresh-token` 中读取旧 refresh token 并吊销。

### POST `/api/v1/auth/send-code`

```json
{
  "target": "string",
  "type": "register | reset_password | verify"
}
```

```json
{
  "code": 0,
  "data": null
}
```

说明：当前开发环境会生成 6 位验证码并写入服务日志，10 分钟过期。

### POST `/api/v1/auth/verify-code`

```json
{
  "target": "string",
  "code": "string"
}
```

```json
{
  "code": 0,
  "data": {
    "valid": true
  }
}
```

### POST `/api/v1/auth/reset-password`

```json
{
  "target": "string",
  "code": "string",
  "newPassword": "string"
}
```

```json
{
  "code": 0,
  "data": null
}
```

### POST `/api/v1/auth/change-password`

```json
{
  "currentPassword": "string",
  "newPassword": "string"
}
```

```json
{
  "code": 0,
  "data": null
}
```

### GET `/api/v1/auth/profile`

```json
{
  "code": 0,
  "data": { "...User": "..." }
}
```

---

## 用户服务 (`3001`)

### GET `/api/v1/users/me`

格式 A，返回当前用户。

### GET `/api/v1/users/:userId`

格式 A，返回用户基础信息。

### PATCH `/api/v1/users/profile`

```json
{
  "username": "string (optional)",
  "displayName": "string (optional)",
  "bio": "string (optional)",
  "avatar": "string url (optional)",
  "avatarUrl": "string url (optional)"
}
```

格式 A，返回更新后的用户。

### POST `/api/v1/users/avatar`

```json
{
  "filePath": "string"
}
```

```json
{
  "code": 0,
  "data": {
    "url": "string"
  }
}
```

说明：当前实现会把传入的 `filePath` 直接写入 `avatarUrl` 字段。生产环境应替换为真实媒体上传服务，见文末“业务补齐 API 建议”。

### GET `/users/search`

Query: `q=string&page=1&limit=20`

格式 B，返回分页用户列表。

### GET `/users/:userId/followers`

格式 B，返回分页粉丝列表。

### GET `/users/:userId/following`

格式 B，返回分页关注列表。

### POST `/users/:userId/follow`

格式 B：

```json
{
  "success": true,
  "data": null
}
```

### DELETE `/users/:userId/follow`

格式 B，返回 `data: null`。

### GET `/users/:userId/stats`

格式 B：

```json
{
  "success": true,
  "data": {
    "gamesCreated": 0,
    "totalPlays": 0,
    "totalLikes": 0,
    "followers": 0,
    "following": 0
  }
}
```

---

## 游戏服务 (`3002`)

### POST `/api/v1/games/expand-prompt`

扩写用户的简短描述为更完善的游戏描述。无需认证。

请求体：

```json
{
  "description": "string (必填，或使用 prompt 别名)",
  "regionHint": "string (可选，AI 引擎区域)"
}
```

响应：

```json
{
  "code": 0,
  "data": {
    "expandedPrompt": "string"
  }
}
```

说明：实际响应结构取决于 AI 引擎返回内容，透传 `response.data`。

### POST `/api/v1/games/generate`

直接从 prompt 生成游戏（跳过 creation session 会话流程）。

请求体：

```json
{
  "description": "string (min 10，与 prompt 二选一)",
  "prompt": "string (min 10，与 description 二选一)",
  "title": "string (可选，max 50)",
  "orientation": "portrait | landscape (可选)",
  "generationTier": "safe | standard | showcase (可选)",
  "timeoutS": "number (可选，30–3600)",
  "regionHint": "string (可选)"
}
```

响应（HTTP `201`）：

```json
{
  "code": 0,
  "data": {
    "gameId": "string",
    "taskId": "string"
  }
}
```

### GET `/api/v1/games/game-types`

返回平台支持的游戏类型列表。无需认证。

响应：

```json
{
  "code": 0,
  "data": ["casual", "puzzle", "education", "funny"]
}
```

### GET `/api/v1/games/:gameId`

格式 A，返回 `Game`。无需认证。

### GET `/api/v1/games/my`

格式 A，返回分页游戏列表。

Query: `page=1&limit=10`

### GET `/api/v1/games/my/games`

兼容旧路由，返回同 `/api/v1/games/my`。

### GET `/api/v1/games/explore/published`

格式 A，返回分页已发布游戏列表。无需认证。

Query: `page=1&limit=10&search=string`

### GET `/api/v1/games/:gameId/generation-status`

轮询游戏生成进度。作为 WebSocket `gen:progress` / `gen:complete` 的 HTTP 兜底。

响应：

```json
{
  "code": 0,
  "data": {
    "status": "queued | generating | ready | failed",
    "progress": 60,
    "stage": "string",
    "message": "string",
    "gameUrl": "string | null",
    "previewUrl": "string | null"
  }
}
```

### GET `/api/v1/games/:gameId/play`

获取游戏可运行 HTML 源码。会自增 playCount。

响应：

```json
{
  "code": 0,
  "data": {
    "htmlCode": "string",
    "gameId": "string"
  }
}
```

### POST `/api/v1/games/:gameId/unlock`

解锁游戏源码访问。

响应：

```json
{
  "code": 0,
  "data": {
    "unlocked": true,
    "htmlCode": "string"
  }
}
```

### POST `/api/v1/games/:gameId/iterate`

对已有游戏提交迭代反馈，触发新一轮生成。

请求体：

```json
{
  "feedback": "string (必填，min 5)",
  "regionHint": "string (可选)",
  "generationTier": "safe | standard | showcase (可选)",
  "timeoutS": "number (可选，30–3600)"
}
```

响应：

```json
{
  "code": 0,
  "data": {
    "iterationId": "string (格式 gameId:vN)",
    "gameId": "string",
    "version": 2,
    "taskId": "string"
  }
}
```

### POST `/api/v1/games/:gameId/publish`

```json
{
  "title": "string (可选，min 3)",
  "description": "string (可选，min 10)",
  "tags": ["string"],
  "gameType": "string (可选)",
  "visibility": "public | private (可选)"
}
```

格式 A，返回 `Game`。

### PATCH `/api/v1/games/:gameId/settings`

更新游戏设置（标题、描述等）。

请求体：自由 JSON 对象，字段直接更新到游戏记录。

响应：格式 A，返回更新后的游戏信息。

### DELETE `/api/v1/games/:gameId`

HTTP `204`。

### POST `/api/v1/games/:gameId/fork`

```json
{
  "code": 0,
  "data": {
    "gameId": "string"
  }
}
```

### GET `/api/v1/games/:gameId/forks`

格式 A，返回分页 fork 列表。

### GET `/api/v1/games/:gameId/fork-tree`

```json
{
  "code": 0,
  "data": {
    "game": { "...Game": "..." },
    "parent": { "...Game": "..." },
    "children": [{ "...Game": "..." }]
  }
}
```

### GET `/api/v1/games/:gameId/fork-lineage`

返回当前作品的祖先链。

### GET `/api/v1/games/:gameId/share-data`

返回分享卡片所需元数据。无需认证。

### GET `/api/v1/games/creator/:creatorId/reputation`

获取创作者信誉分。无需认证。

响应：

```json
{
  "code": 0,
  "data": {
    "creatorId": "string",
    "score": 85,
    "level": "string",
    "gamesPublished": 10,
    "totalPlays": 5000,
    "avgRating": 4.2
  }
}
```

---

## 任务管理 (`3002`)

通用的异步任务查询接口，用于追踪生成、迭代等长时任务。

### GET `/api/v1/games/tasks/:taskId`

获取任务概要信息。

响应：

```json
{
  "code": 0,
  "data": {
    "id": "string",
    "type": "generate | iterate",
    "status": "pending | running | completed | failed | cancelled",
    "gameId": "string | null",
    "progress": 0,
    "createdAt": "ISO8601",
    "updatedAt": "ISO8601"
  }
}
```

### GET `/api/v1/games/tasks/:taskId/events`

获取任务事件流（进度日志）。

Query: `limit=number (可选)`

响应：

```json
{
  "code": 0,
  "data": [
    {
      "type": "progress | stage | error | complete",
      "message": "string",
      "data": {},
      "timestamp": "ISO8601"
    }
  ]
}
```

### GET `/api/v1/games/tasks/:taskId/artifacts`

获取任务产出物（生成的代码、截图等）。

Query: `limit=number (可选)`

响应：

```json
{
  "code": 0,
  "data": [
    {
      "type": "html | screenshot | metadata",
      "url": "string",
      "createdAt": "ISO8601"
    }
  ]
}
```

### POST `/api/v1/games/tasks/:taskId/cancel`

取消进行中的任务。

响应：

```json
{
  "code": 0,
  "data": {
    "cancelled": true
  }
}
```

---

## 创作会话 (`3002`)

Creation Session 是渐进式游戏创作流程，AI 逐步收集信息后再生成。

### POST `/api/v1/games/creation-sessions`

创建新的创作会话。采用乐观创建模式：立即返回 `initializing` 状态（<200ms），AI 分析在后台异步完成（2–5s）。

请求体：

```json
{
  "prompt": "string (必填，min 5)",
  "title": "string (可选，max 50)",
  "orientation": "portrait | landscape (可选)",
  "generationTier": "safe | standard | showcase (可选)",
  "entryMode": "create | fork | iterate (可选，默认 create)",
  "sourceGameId": "string (可选，fork/iterate 时必填，max 36)",
  "regionHint": "string (可选)"
}
```

响应（HTTP `201`）：

```json
{
  "code": 0,
  "data": "CreationSessionSnapshot (status=initializing)"
}
```

说明：

- 创建时自动 abandon 同一用户的旧 active session。
- 返回的 snapshot 中 `currentQuestion=null`，需等待状态变为 `collecting` 后才会有问题。
- 返回的 snapshot 中包含 `streamPath`，前端可据此拼接 SSE 订阅地址。
- 前端应在 `initializing` 期间展示加载动画，优先订阅 `GET /creation-sessions/:id/events` 的 SSE 流等待状态变化；未接入 SSE 时可继续用 WebSocket `session:updated` 或轮询 `GET /creation-sessions/:id` 兜底。

### GET `/api/v1/games/creation-sessions/active`

获取当前用户的活跃创作会话（`initializing` / `collecting` / `ready`）。

响应：

```json
{
  "code": 0,
  "data": "CreationSessionSnapshot | null"
}
```

### GET `/api/v1/games/creation-sessions/:sessionId`

按 ID 获取指定创作会话。

响应：

```json
{
  "code": 0,
  "data": "CreationSessionSnapshot"
}
```

错误：`404` 会话不存在或不属于当前用户。

### GET `/api/v1/games/creation-sessions/:sessionId/events`

订阅指定创作会话的 SSE 事件流，用于更快感知初始化完成、追问变更和异常终态。

请求头：

```http
Accept: text/event-stream
Cache-Control: no-cache
Authorization: Bearer <JWT>
```

浏览器 `EventSource` 场景：

- 由于浏览器原生 `EventSource` 不能发送 `Authorization` 请求头，可改为在 URL 上追加 `?token=<JWT>`。
- `Authorization` 头和 `token` query 二选一即可；`token` query 仅在 `GET + text/event-stream` 的 SSE 请求中生效。

响应：

- `200 OK`
- `Content-Type: text/event-stream`

事件类型：

- `bootstrap`：建立连接后立即返回一次当前快照
- `delta`：assistant 当前回复的增量片段
- `done`：assistant 当前回复结束
- `snapshot`：session 快照发生变化后的最新状态
- `error`：初始化失败、超时或其它可公开错误
- `heartbeat`：仍然存在，但以 SSE comment 形式发送，前端不需要作为普通事件处理

`bootstrap` / `snapshot` 数据示例：

```json
{
  "type": "bootstrap",
  "sessionId": "string",
  "session": "CreationSessionSnapshot",
  "timestamp": 0
}
```

`delta` 数据示例：

```json
{
  "type": "delta",
  "sessionId": "string",
  "messageId": "string",
  "delta": "string",
  "accumulated": "string",
  "kind": "question | summary",
  "timestamp": 0
}
```

`done` 数据示例：

```json
{
  "type": "done",
  "sessionId": "string",
  "messageId": "string",
  "message": "string",
  "kind": "question | summary",
  "timestamp": 0
}
```

`error` 数据示例：

```json
{
  "type": "error",
  "sessionId": "string",
  "code": "init_failed | init_timeout | session_error",
  "message": "string",
  "retryable": true,
  "details": {
    "reason": "init_failed | init_timeout",
    "stage": "string"
  },
  "timestamp": 0
}
```

### POST `/api/v1/games/creation-sessions/:sessionId/messages`

向会话追加用户回答，触发下一轮 AI 分析。

请求体：

```json
{
  "content": "string (必填，min 1)",
  "revision": "number (可选，乐观锁)"
}
```

响应：

```json
{
  "code": 0,
  "data": "CreationSessionSnapshot"
}
```

错误：

- `409` revision 不匹配（并发冲突）
- `409` session 处于 `initializing` 状态（尚未完成初始化，不可修改）

### POST `/api/v1/games/creation-sessions/:sessionId/skip`

跳过当前问题。

请求体：

```json
{
  "revision": "number (可选，乐观锁)"
}
```

响应：

```json
{
  "code": 0,
  "data": "CreationSessionSnapshot"
}
```

错误：同 `/messages`。

### POST `/api/v1/games/creation-sessions/:sessionId/generate`

从当前会话触发游戏生成。仅在 `status=ready` 时可用。

请求体：

```json
{
  "revision": "number (可选，乐观锁)",
  "timeoutS": "number (可选，30–3600)"
}
```

响应（HTTP `201`）：

```json
{
  "code": 0,
  "data": "CreationSessionSnapshot (status=generating)"
}
```

错误：`409` 状态不为 `ready`。

### POST `/api/v1/games/creation-sessions/:sessionId/abandon`

放弃会话。可从任何 active 状态（包括 `initializing`）调用。

响应：

```json
{
  "code": 0,
  "data": "CreationSessionSnapshot (status=abandoned)"
}
```

---

## 社交服务 (`3003`)

### POST `/api/v1/social/like`

```json
{
  "targetType": "game | comment",
  "targetId": "string"
}
```

```json
{
  "code": 0,
  "data": {
    "liked": true,
    "likes": 1
  }
}
```

### GET `/api/v1/social/like-status/:type/:id`

```json
{
  "code": 0,
  "data": {
    "liked": false
  }
}
```

### POST `/api/v1/social/follow`

```json
{
  "targetId": "string"
}
```

格式 A，返回 `data: null`。

### DELETE `/api/v1/social/follow/:userId`

格式 A，返回 `data: null`。

### GET `/api/v1/social/followers/:userId`

格式 A，返回分页用户列表。

### GET `/api/v1/social/following/:userId`

格式 A，返回分页用户列表。

### GET `/api/v1/social/follow-status/:userId`

```json
{
  "code": 0,
  "data": {
    "following": true
  }
}
```

### POST `/api/v1/comments`

```json
{
  "gameId": "string",
  "content": "string",
  "parentId": "string | null"
}
```

格式 A，返回 `Comment`。

### GET `/api/v1/comments/games/:gameId`

格式 A，返回分页评论列表。

### GET `/api/v1/comments/:commentId/replies`

格式 A，返回分页回复列表。

### DELETE `/api/v1/comments/:commentId`

格式 A，返回 `data: null`。

### POST `/api/v1/comments/:commentId/like`

格式 A，返回：

```json
{
  "code": 0,
  "data": {
    "liked": true,
    "likes": 1
  }
}
```

### GET `/api/v1/notifications`

格式 A，返回分页通知列表。

### POST `/api/v1/notifications/mark-read`

```json
{
  "ids": ["string"]
}
```

格式 A，返回 `data: null`。

### POST `/api/v1/notifications/mark-all-read`

格式 A，返回 `data: null`。

### GET `/api/v1/notifications/unread-count`

```json
{
  "code": 0,
  "data": {
    "count": 0
  }
}
```

### POST `/api/v1/social/share`

记录分享行为，返回 `data: null`。

---

## Feed 服务 (`3004`)

所有 Feed 列表接口统一返回格式 A + 分页对象。

### GET `/api/v1/feed/trending`

热门游戏。

### GET `/api/v1/feed/latest`

最新游戏。

### GET `/api/v1/feed/following`

关注动态。

### GET `/api/v1/feed/featured`

精选游戏。

### GET `/api/v1/feed/search`

Query:

```text
q=string&page=1&limit=10&gameType=string&tags=tag1,tag2&sortBy=relevance|popularity
```

### GET `/api/v1/feed/category`

Query:

```text
category=string&page=1&limit=10
```

### GET `/api/v1/feed/by-type/:type`

兼容旧路由。

### GET `/api/v1/tags/trending`

```json
{
  "code": 0,
  "data": {
    "items": [
      { "tag": "space", "count": 10 }
    ],
    "hasMore": false,
    "page": 1,
    "limit": 10,
    "total": 1
  }
}
```

### GET `/api/v1/tags/all`

返回全部标签。

### GET `/api/v1/challenges/current`

```json
{
  "code": 0,
  "data": {
    "id": "string",
    "title": "string",
    "description": "string",
    "endsAt": "ISO8601",
    "startDate": "ISO8601",
    "participantCount": 0,
    "rules": ["string"]
  }
}
```

### GET `/api/v1/challenges/:id/games`

返回挑战作品列表。

### GET `/api/v1/creators/trending`

```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": "string",
        "username": "string",
        "avatar": "string",
        "bio": "string",
        "gamesCount": 0,
        "totalPlays": 0,
        "followerCount": 0
      }
    ],
    "hasMore": false,
    "page": 1,
    "limit": 10,
    "total": 1
  }
}
```

---

## WebSocket

### 连接方式

当前实现使用 `Socket.IO`，不是原生 WebSocket：

```js
io('http://localhost:3002/ws', {
  query: {
    token: accessToken
  }
})
```

### 服务端事件

#### `gen:progress`

```json
{
  "type": "gen:progress",
  "gameId": "string",
  "data": {
    "progress": 60,
    "message": "生成游戏代码"
  },
  "stage": "生成游戏代码",
  "percentage": 60,
  "timestamp": 0
}
```

#### `gen:complete`

```json
{
  "type": "gen:complete",
  "gameId": "string",
  "data": {
    "success": true,
    "game": {
      "id": "string",
      "gameUrl": "string",
      "previewUrl": "string",
      "status": "ready"
    },
    "error": null
  },
  "timestamp": 0
}
```

#### `gen:error`

```json
{
  "type": "gen:error",
  "gameId": "string",
  "data": {
    "success": false,
    "error": "Generated code failed QA",
    "details": {
      "stage": "qa_checking",
      "retryCount": 3
    }
  },
  "stage": "qa_checking",
  "details": {
    "stage": "qa_checking",
    "retryCount": 3
  },
  "status": "error",
  "timestamp": 0
}
```

#### `session:updated`

创作会话状态变更推送。当后台 AI 分析完成、session 从 `initializing` 转为 `collecting`/`ready` 时推送。

```json
{
  "type": "session:updated",
  "sessionId": "string",
  "session": "CreationSessionSnapshot",
  "timestamp": 0
}
```

说明：前端收到后应直接替换本地 session 缓存。新接入建议优先使用 `GET /creation-sessions/:id/events` 的 SSE；WebSocket `session:updated` 和轮询 `GET /creation-sessions/:id` 保留为兼容兜底方案。

#### `session:error`

创作会话初始化失败推送。当 AI 分析异常或初始化超时（30s）时推送。

```json
{
  "type": "session:error",
  "sessionId": "string",
  "error": "string",
  "details": {
    "reason": "init_failed | init_timeout",
    "stage": "string"
  },
  "timestamp": 0
}
```

说明：收到此事件后 session 已变为 `abandoned`，前端应提示用户重新创建。

#### `game:update`

游戏状态变更通用事件。

#### `notification`

通知事件，`data` 结构见 `Notification`。生成失败时仍会发 `type="error"` 的通知作为兼容补充，但终态失败事件以 `gen:error` 为准。

### 客户端事件

#### `ping`

服务端回：

```json
{
  "type": "pong",
  "timestamp": 0
}
```

---

## 游戏内容文件

### 正式加载地址

```http
GET /games/:gameId/index.html
```

直接返回 `text/html`，不走 JSON API。

### 兼容地址

```http
GET /games/:gameId/preview
```

---

## 兼容路由

以下接口为了兼容旧前端或旧测试脚本仍保留：

1. `GET /api/v1/games/my/games`
2. `GET /api/v1/feed/by-type/:type`
3. `GET /games/:gameId/preview`
4. `POST /api/v1/notifications/read`
5. `POST /api/v1/notifications/read-all`

---

## 业务补齐 API 建议

下面这些接口不是当前前端强依赖，但从整体业务逻辑看，后端下一阶段应该补齐。

### 1. 媒体上传与托管

当前 `POST /api/v1/users/avatar` 只是把 `filePath` 字符串写入数据库，不具备真实文件上传能力。建议新增：

#### POST `/api/v1/media/presign`

```json
{
  "scope": "avatar | game_cover | share_image",
  "filename": "string",
  "contentType": "image/png"
}
```

返回预签名上传信息。

#### POST `/api/v1/media/complete`

```json
{
  "scope": "avatar",
  "objectKey": "string"
}
```

返回 CDN URL。

### 2. 游玩结果与行为埋点

目前 HTML 被加载时只会自增一次 `playCount`，无法统计游玩时长、得分和完成率。建议新增：

#### POST `/api/v1/games/:gameId/play-session/start`

#### POST `/api/v1/games/:gameId/play-session/complete`

```json
{
  "durationMs": 12345,
  "score": 100,
  "completed": true
}
```

### 3. 举报与审核

业务上已经有 `report` 行为枚举，但没有对外 API。建议新增：

#### POST `/api/v1/social/report`

```json
{
  "targetType": "game | comment | user",
  "targetId": "string",
  "reason": "spam | abuse | copyright | other",
  "detail": "string"
}
```

### 4. 创作者收益中心

数据库已有 `creator_earnings`，但没有对外接口。建议新增：

#### GET `/api/v1/creator/earnings/summary`

#### GET `/api/v1/creator/earnings/records`

#### POST `/api/v1/creator/earnings/withdraw`

### 5. 通知偏好设置

建议新增：

#### GET `/api/v1/notifications/settings`

#### PATCH `/api/v1/notifications/settings`

```json
{
  "like": true,
  "comment": true,
  "follow": true,
  "system": true
}
```

### 6. 游戏分析面板

创作者查看作品效果需要独立分析接口，建议新增：

#### GET `/api/v1/games/:gameId/analytics`

```json
{
  "code": 0,
  "data": {
    "plays": 0,
    "uniqueUsers": 0,
    "avgPlayTime": 0,
    "completionRate": 0,
    "likes": 0,
    "shares": 0,
    "forks": 0
  }
}
```
