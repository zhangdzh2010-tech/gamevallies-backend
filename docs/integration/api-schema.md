# API Schema

当前文档描述 `gamevallies-backend` 已实现的前端契约，以及从整体业务逻辑出发建议继续补齐的 API。

更新时间：`2026-03-07`

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

### POST `/api/v1/games/generate`

```json
{
  "prompt": "string"
}
```

或：

```json
{
  "description": "string"
}
```

```json
{
  "code": 0,
  "data": {
    "gameId": "string"
  }
}
```

### GET `/api/v1/games/:gameId`

格式 A，返回 `Game`。

### GET `/api/v1/games/my`

格式 A，返回分页游戏列表。

### GET `/api/v1/games/my/games`

兼容旧路由，返回同 `/api/v1/games/my`。

### GET `/api/v1/games/explore/published`

格式 A，返回分页已发布游戏列表。

### POST `/api/v1/games/:gameId/iterate`

```json
{
  "feedback": "string"
}
```

```json
{
  "code": 0,
  "data": {
    "iterationId": "string",
    "gameId": "string",
    "version": 2
  }
}
```

### POST `/api/v1/games/:gameId/fork`

```json
{
  "code": 0,
  "data": {
    "gameId": "string"
  }
}
```

### POST `/api/v1/games/:gameId/publish`

```json
{
  "title": "string (optional)",
  "description": "string (optional)",
  "tags": ["string"],
  "gameType": "string (optional)"
}
```

格式 A，返回 `Game`。

### DELETE `/api/v1/games/:gameId`

HTTP `204`。

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

返回分享卡片所需元数据。

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

### 2. 游戏生成状态查询

当前生成完全依赖 WebSocket。建议新增轮询兜底：

#### GET `/api/v1/games/:gameId/generation-status`

```json
{
  "code": 0,
  "data": {
    "status": "queued | generating | ready | failed",
    "progress": 60,
    "message": "string"
  }
}
```

### 3. 游玩结果与行为埋点

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

### 4. 举报与审核

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

### 5. 创作者收益中心

数据库已有 `creator_earnings`，但没有对外接口。建议新增：

#### GET `/api/v1/creator/earnings/summary`

#### GET `/api/v1/creator/earnings/records`

#### POST `/api/v1/creator/earnings/withdraw`

### 6. 通知偏好设置

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

### 7. 游戏分析面板

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
