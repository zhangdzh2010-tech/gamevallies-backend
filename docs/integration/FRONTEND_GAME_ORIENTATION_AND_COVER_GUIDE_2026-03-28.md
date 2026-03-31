# Frontend Game Orientation And Cover Guide (2026-03-28)

## 1. 结论

本次后端改动后，前端必须关注两件事：

- 创建游戏时增加 `orientation` 字段，并在请求体里传给后端
- 游戏封面统一使用后端返回的 `coverUrl`，不要前端自行拼接

如果前端已经直接消费后端返回的 `coverUrl`、`previewUrl`、`gameUrl`，那封面链路基本不用额外改造。

---

## 2. 创建游戏接口适配

### 2.1 接口

- `POST /api/v1/games/generate`

### 2.2 新增字段

- `orientation: 'portrait' | 'landscape'`

前端建议文案映射：

- `竖屏 (9:16)` -> `portrait`
- `横屏 (16:9)` -> `landscape`

### 2.3 请求体示例

```json
{
  "title": "Runner",
  "description": "做一个双人竞速小游戏",
  "orientation": "landscape",
  "timeoutS": 900
}
```

### 2.4 前端建议

- 表单里显式提供方向选择，不要让用户无感知
- 默认值建议前端直接给 `portrait`
- 不要传 `9:16`、`16:9`、`vertical`、`horizontal` 这类自定义值

---

## 3. 封面读取约定

### 3.1 必须遵守

- 游戏列表卡片封面：直接使用 `coverUrl`
- 游戏详情页封面：直接使用 `coverUrl`
- 作者草稿/预览页封面：直接使用 `coverUrl`
- 游戏运行入口：继续使用 `gameUrl`
- 游戏预览入口：继续使用 `previewUrl`

### 3.2 不要这样做

- 不要自己拼 `/games/:id/cover`
- 不要用 `gameUrl` 或 `previewUrl` 推导封面图地址
- 不要把 `coverUrl` 上的 query string 去掉
- 不要自己判断“这是不是 live 封面 / candidate 封面”

原因：

- `coverUrl` 可能带 `taskId`
- `coverUrl` 可能带 `v`
- 作者预览场景下 `coverUrl` 可能带 `previewToken`
- 这些参数现在参与 live/candidate 封面隔离和权限控制

### 3.3 推荐渲染方式

```tsx
<img src={game.coverUrl} alt={game.title} />
```

---

## 4. 作者工作台说明

### 4.1 默认方案

如果前端只是展示“当前这个游戏”的封面，直接用后端返回的 `coverUrl` 即可。

后端已经保证：

- 公众看到的是 live 封面
- 作者预览可以拿到带权限的封面地址
- 已发布游戏的未发布 iterate candidate 不会再污染公众封面

### 4.2 候选版本预览

如果前端有“本次生成候选版本”的独立面板，可以额外展示 candidate 封面：

- live 封面：使用游戏对象里的 `coverUrl`
- candidate 封面：使用 `/games/:id/cover?taskId=<taskId>&previewToken=<previewToken>`

这属于增强能力，不是本次必须改动。

---

## 5. Iterate 接口说明

- `POST /api/v1/games/:id/iterate`

本次默认行为下，前端不需要在 iterate 时重复传 `orientation`。

后端会自动继承：

- 当前游戏的已保存方向
- 对应 runtime contract 的方向配置

如果后续产品需要“迭代时允许改横竖屏”，那会是单独的新需求，不属于这次联调范围。

---

## 6. 页面联调清单

### 6.1 创建页

- 增加方向单选或分段选择器
- 提交时写入 `orientation`
- 校验最终请求值只会是 `portrait` 或 `landscape`

### 6.2 列表页

- 卡片封面统一读 `coverUrl`
- 不再手工拼封面地址

### 6.3 详情页

- 详情主图统一读 `coverUrl`
- 游戏启动按钮继续用 `gameUrl`

### 6.4 作者草稿页

- 草稿封面统一读 `coverUrl`
- 不要手动去掉 `previewToken`

### 6.5 任务/生成状态页

- 如果页面上展示封面，也只读 `coverUrl`
- 如果展示候选版本封面，再额外使用 `taskId + previewToken` 方案

---

## 7. 建议验收项

- 创建竖屏游戏后，预览比例和布局符合预期
- 创建横屏游戏后，预览比例和布局符合预期
- 已发布游戏发起一次 iterate 后，公众列表封面保持不变
- 作者仍能在自己的预览链路里看到 candidate 封面
- 历史外部 CDN 封面链接仍然能正常显示

---

## 8. 前端类型建议

```ts
type GameOrientation = 'portrait' | 'landscape';

interface CreateGameRequest {
  title?: string;
  description?: string;
  prompt?: string;
  orientation?: GameOrientation;
  timeoutS?: number;
  regionHint?: string;
}

interface GameCard {
  id: string;
  title: string;
  description: string;
  status: string;
  coverUrl: string;
  previewUrl: string;
  gameUrl: string;
}
```

---

## 9. 后端约束摘要

- `orientation` 目前只接受 `portrait` / `landscape`
- `coverUrl` 是后端最终权威字段
- `coverUrl` 可能是站内地址，也可能是外部绝对地址
- 前端必须原样使用后端返回的 `coverUrl`

