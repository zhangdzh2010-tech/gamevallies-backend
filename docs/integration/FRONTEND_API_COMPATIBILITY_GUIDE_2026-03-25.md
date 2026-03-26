# Frontend API Compatibility Guide (2026-03-25)

## 1. 结论

本次后端调整后，前端公开用户接口没有破坏性变更。

前端必须兼容的重点只有两类：

- 链接域名统一切换为 `https://gamevallies.com`
- 作者态和后台态返回的 `previewUrl` / `gameUrl` 可能带 `previewToken`，前端必须原样使用，不能丢失 query string

除这两点外，`generate`、`iterate`、`publish` 的请求体都没有新增必填字段。

---

## 2. 前端必须修改

### 2.1 链接必须整串使用

适用字段：

- `previewUrl`
- `gameUrl`
- `coverUrl`

前端要求：

- 不要自己用 `gameId` 拼 `/games/:id/preview`
- 不要自己把 `/preview` 替换成 `/index.html`
- 不要丢掉 `?previewToken=...`
- 最稳妥的做法是直接使用后端返回的完整链接

原因：

- 作者态 / admin 态的预览链接可能带 `previewToken`
- 未发布或私有游戏如果丢失 token，会重新变成 `404`

### 2.2 域名要兼容 apex

当前 canonical 域名是：

- `https://gamevallies.com`

前端要求：

- 去掉对 `www.gamevallies.com` 的强依赖
- 如果前端有域名白名单、iframe allowlist、分享域名判断、CSP allowlist，也要加入 `gamevallies.com`

---

## 3. 用户侧接口

### 3.1 创建游戏

接口：

- `POST /api/v1/games/generate`

请求体无破坏性变化：

```json
{
  "description": "做一个竖屏跑酷小游戏",
  "prompt": "做一个竖屏跑酷小游戏",
  "title": "小猪快跑",
  "regionHint": "cn_shanghai",
  "timeoutS": 600
}
```

说明：

- `description` 和 `prompt` 仍是二选一
- `timeoutS`、`regionHint` 仍是可选

响应 shape 保持兼容，但返回里会带任务摘要：

```json
{
  "taskId": "task_xxx",
  "taskType": "pipeline_run",
  "status": "queued",
  "pollUrl": "/api/v1/games/tasks/task_xxx",
  "eventsUrl": "/api/v1/games/tasks/task_xxx/events",
  "artifactsUrl": "/api/v1/games/tasks/task_xxx/artifacts",
  "cancelUrl": "/api/v1/games/tasks/task_xxx/cancel",
  "gameId": "game_xxx",
  "version": 1,
  "pipelineVersion": "v2",
  "runtimeProfile": "lane_runner",
  "previewUrl": "https://gamevallies.com/games/game_xxx/preview?previewToken=...",
  "gameUrl": "https://gamevallies.com/games/game_xxx/index.html?previewToken=...",
  "title": "小猪快跑",
  "description": "做一个竖屏跑酷小游戏",
  "statusText": "generating",
  "canPlay": true,
  "requireSubscription": false,
  "generationTask": {}
}
```

前端动作：

- 创建后直接用返回的 `pollUrl`
- 预览按钮直接使用返回的 `previewUrl`

### 3.2 迭代游戏

接口：

- `POST /api/v1/games/:id/iterate`

请求体无破坏性变化：

```json
{
  "feedback": "继续增加关卡，设置5个关卡",
  "regionHint": "cn_shanghai",
  "timeoutS": 600
}
```

说明：

- 前端不用传 `source_spec`
- 前端不用传 `source_bundle_context`
- 这些历史资料由 `game-service` 在服务端自动补齐

响应 shape 与创建类似：

```json
{
  "iterationId": "game_xxx:v3",
  "taskId": "task_iter_xxx",
  "taskType": "pipeline_iterate",
  "status": "queued",
  "pollUrl": "/api/v1/games/tasks/task_iter_xxx",
  "eventsUrl": "/api/v1/games/tasks/task_iter_xxx/events",
  "artifactsUrl": "/api/v1/games/tasks/task_iter_xxx/artifacts",
  "cancelUrl": "/api/v1/games/tasks/task_iter_xxx/cancel",
  "gameId": "game_xxx",
  "version": 3,
  "pipelineVersion": "v2",
  "runtimeProfile": "lane_runner",
  "previewUrl": "https://gamevallies.com/games/game_xxx/preview?previewToken=...",
  "gameUrl": "https://gamevallies.com/games/game_xxx/index.html?previewToken=...",
  "statusText": "iterating",
  "generationTask": {}
}
```

前端动作：

- 继续沿用现有轮询逻辑
- 不需要新增请求字段

### 3.3 获取生成状态

接口：

- `GET /api/v1/games/:id/generation-status`

建议前端使用这些字段：

```json
{
  "taskId": "task_xxx",
  "taskType": "pipeline_run",
  "status": "queued|running|succeeded|failed|timed_out|canceled",
  "stage": "queued|spec_build|code_generating|qa_checking|completed|failed",
  "gameId": "game_xxx",
  "version": 1,
  "previewUrl": "https://gamevallies.com/games/game_xxx/preview?previewToken=...",
  "gameUrl": "https://gamevallies.com/games/game_xxx/index.html?previewToken=...",
  "gameStatus": "generating|draft|published|failed",
  "canPlay": true,
  "requireSubscription": false,
  "failedStage": null,
  "failedReason": null,
  "retryCount": 0
}
```

前端建议：

- 优先信任后端返回的 `status` 和 `stage`
- 不要再根据 `game.status` 自己推导生成状态

### 3.4 获取任务详情

接口：

- `GET /api/v1/games/tasks/:taskId`

稳定可用字段：

- `taskId`
- `taskType`
- `region`
- `status`
- `timeoutS`
- `pollUrl`
- `eventsUrl`
- `artifactsUrl`
- `cancelUrl`
- `gameId`
- `version`
- `pipelineVersion`
- `runtimeProfile`
- `contractVersion`
- `failureFamily`
- `primaryArtifactId`
- `progressStage`
- `progressPct`
- `progressMessage`
- `failedStage`
- `errorMessage`
- `retryCount`
- `previewUrl`
- `gameUrl`
- `startedAt`
- `completedAt`
- `createdAt`
- `updatedAt`

前端建议：

- 任务详情页直接消费这些字段
- 不要自己二次推导 `pollUrl/eventsUrl/artifactsUrl`

### 3.5 作者游戏列表

接口：

- `GET /api/v1/games/my/games`
- `GET /api/v1/games/my`

兼容说明：

- 响应 shape 没变
- 但作者自己的 `previewUrl` 现在可能带 `previewToken`

前端动作：

- 卡片上的“预览”按钮直接用返回的 `previewUrl`

### 3.6 公共游戏列表

接口：

- `GET /api/v1/games/explore/published`
- `GET /api/v1/games/:id`

兼容说明：

- 字段 shape 没变
- 返回链接统一以 `https://gamevallies.com` 为 canonical
- `unlisted` 游戏不会出现在公共详情和公开列表中

---

## 4. Admin 侧接口

### 4.1 任务详情

接口：

- `GET /api/v1/admin/tasks/:id`

新增 / 推荐前端使用字段：

- `inputPrompt`
- `sourceBundle`
- `previewUrl`
- `gameUrl`

示例：

```json
{
  "id": "task_xxx",
  "gameId": "game_xxx",
  "status": "succeeded",
  "inputPrompt": "做一个竖屏躲避障碍小游戏",
  "sourceBundle": {
    "version": 2,
    "metadata": {}
  },
  "previewUrl": "https://gamevallies.com/games/game_xxx/preview?previewToken=...",
  "gameUrl": "https://gamevallies.com/games/game_xxx/index.html?previewToken=..."
}
```

前端动作：

- “源码/详情”页可直接展示 `inputPrompt`
- “来源版本”可直接展示 `sourceBundle`
- 预览按钮直接使用返回链接

### 4.2 任务列表

接口：

- `GET /api/v1/admin/tasks`

兼容说明：

- 列表项会附带可直接访问的 `previewUrl` 和 `gameUrl`
- 前端不需要再自己拼 admin 预览地址

### 4.3 生成记录列表

接口：

- `GET /api/v1/admin/genlog`

语义变化：

- `status`
- `failedStage`
- `failedReason`

以上字段现在优先来自“最新 generation task”，不是旧的 `games.failedStage/failedReason`

前端动作：

- 如果页面里存在本地状态推导逻辑，建议删除
- 直接展示后端返回的结果

### 4.4 超时配置保存

接口：

- `PUT /api/v1/admin/configs/:key`

当 `category=timeout` 时，响应会附带：

```json
{
  "configKey": "timeout.xxx",
  "configValue": "60",
  "refreshResult": {
    "refreshed": 3,
    "failed": 1,
    "partialFailure": true
  }
}
```

前端动作：

- 保存配置后不要只按 HTTP 200 认为“全部成功”
- 需要展示 `partialFailure`
- 如果 `failed > 0`，应提示“数据库已保存，但部分 AI engine 节点未刷新成功”

### 4.5 手动刷新超时配置

接口：

- `POST /api/v1/admin/configs/refresh-timeouts`

响应：

```json
{
  "refreshed": 3,
  "failed": 1,
  "partialFailure": true,
  "gameService": { "status": "ok" },
  "aiEngine": [
    { "baseUrl": "https://...", "status": "ok" },
    { "baseUrl": "https://...", "status": "error", "errorMessage": "timeout" }
  ]
}
```

前端动作：

- 展示部分成功状态
- 支持展示失败节点和错误信息

---

## 5. 不需要前端改的内部协议

以下字段变化只发生在 `game-service -> ai-engine` 内部调用：

- `IterateV2Request.source_spec`
- `IterateV2Request.source_bundle_context`
- `IterateResponse.game_spec`

前端不需要传，也不需要解析。

---

## 6. 前端适配清单

- 全局搜索是否写死了 `www.gamevallies.com`
- 全局搜索是否有手动拼 `/games/${id}/preview`
- 全局搜索是否把 `previewUrl` 自己替换成 `/index.html`
- 检查路由跳转、分享卡片、iframe、H5 打开页是否保留 query string
- 作者态列表、任务详情、生成状态页都改成直接信任后端返回的链接
- admin 保存 timeout 配置后，补上 `partialFailure` 提示
- admin 任务详情页接入 `inputPrompt` 和 `sourceBundle`

---

## 7. 推荐联调顺序

1. 先验证作者态“我的游戏”列表里预览按钮是否还能打开未发布游戏
2. 再验证创建和迭代后的轮询页是否直接使用返回的 `pollUrl`
3. 再验证 admin 任务详情页是否展示 `inputPrompt/sourceBundle`
4. 最后验证 timeout 配置页是否能正确处理“部分成功”

