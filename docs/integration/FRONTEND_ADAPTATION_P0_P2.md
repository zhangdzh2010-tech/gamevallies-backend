# 前端适配方案：P0–P2 质量保障功能

> 对应后端实现：P0.1 QA Pipeline 修复 / P0.2 qualityScore 写入 / P1.1 Playwright 运行时检测 /
> P1.2 LLM 语义审查 / P2.1 行为数据动态刷新分数 / P2.2 创作者声誉系统

---

## 概览

| 功能 | 变更类型 | 前端工作量 |
|------|---------|-----------|
| P0.2 qualityScore 写入 DB | 字段新增，无破坏性 | 低：展示分数 |
| P1.1 Playwright 运行时 QA | 后端透明，流程不变 | 无（质量检测阶段耗时增加） |
| P1.2 LLM 语义审查 | 后端透明，流程不变 | 无（同上） |
| P2.1 行为数据动态刷新分数 | 分数值会随时间变化 | 中：动态展示 / 轮询 |
| P2.2 创作者声誉系统 | 新增接口 | 中：新增声誉展示页面/组件 |

---

## 一、游戏对象（Game Object）变更

所有返回游戏列表或详情的接口响应体中，`qualityScore` 字段由 P0.2 开始有实际值（之前默认为 `0`）。

**涉及接口：**
- `GET /api/v1/games/:id`
- `GET /api/v1/games/my`
- `GET /api/v1/games/my/games`
- `GET /api/v1/games/explore/published`

### 游戏对象完整类型定义

```typescript
interface Game {
  id: string;
  title: string;
  description: string;
  status: 'generating' | 'ready' | 'published' | 'failed';
  gameUrl: string;          // 可玩地址（index.html）
  previewUrl: string;       // 预览地址（preview）
  coverUrl: string;         // 封面图（thumbnailUrl 或 gameUrl）
  tags: string[];
  type: string;             // 游戏类型，如 "casual"
  plays: number;            // 累计游玩次数
  likes: number;            // 累计点赞数
  forks: number;            // 分叉数
  commentCount: number;
  qualityScore: number;     // NEW: 0.00 ~ 10.00，保留2位小数
  authorId: string | null;
  author: {
    id: string;
    username: string;
    avatar: string;
  } | null;
  createdAt: string;        // ISO 8601
  updatedAt: string;
  publishedAt: string | null;
}
```

### qualityScore 字段说明

| 属性 | 值 |
|------|---|
| 类型 | `number`（float） |
| 范围 | `0.00 ~ 10.00` |
| 精度 | 保留2位小数（`7.43`） |
| 初始值 | 生成完成后由 AI 自动打分写入 |
| 更新时机 | 每 10 次游玩 或 每次点赞后异步刷新 |

### 展示建议

```typescript
// 星级展示（0-5星 = qualityScore / 2）
function QualityBadge({ score }: { score: number }) {
  const stars = Math.round(score / 2);
  const color = score >= 8 ? '#4CAF50' : score >= 6 ? '#FF9800' : '#F44336';
  return (
    <span style={{ color }}>
      {'★'.repeat(stars)}{'☆'.repeat(5 - stars)} {score.toFixed(1)}
    </span>
  );
}
```

---

## 二、游戏生成接口（无破坏性变更）

### `POST /api/v1/games/generate`

**请求体：**
```json
{
  "description": "做一个躲避炸弹的游戏"
}
```
> `prompt` 字段等同于 `description`，两者均可使用。

**响应体（无变化）：**
```json
{
  "success": true,
  "data": {
    "gameId": "550e8400-e29b-41d4-a716-446655440000",
    "wsChannel": "game:550e8400-e29b-41d4-a716-446655440000",
    "status": "generating"
  }
}
```

---

## 三、WebSocket 生成进度事件

### 连接方式

```javascript
import { io } from 'socket.io-client';

const socket = io('wss://your-api.com/ws', {
  query: { token: 'Bearer_JWT_Token' }
});
```

> WebSocket 命名空间为 `/ws`，连接时必须携带 JWT token（query 参数）。

### 事件：`gen:progress` — 生成进度

```typescript
// 监听
socket.on('gen:progress', (data: GenProgressEvent) => {
  updateProgress(data.gameId, data.percentage, data.data.message);
});

// 事件结构
interface GenProgressEvent {
  type: 'gen:progress';
  gameId: string;
  stage: string;           // 统一 5 阶段标识（英文）
  percentage: number;      // 0-100
  data: {
    progress: number;      // 同 percentage
    message: string;       // 中文阶段描述（统一 5 阶段）
  };
  timestamp: number;       // Unix 毫秒时间戳
}
```

**各阶段进度对照表：**

| percentage | message（前端展示） | 说明 |
|-----------|-------------------|------|
| `15` | 理解游戏需求 | 接收请求并整理成可执行需求 |
| `35` | 构建游戏设计 | 生成设计结构、参数和运行时约束 |
| `60` | 生成游戏代码 | AI 生成 HTML5 游戏代码 |
| `85` | 质量校验与修复 | **P1.1+P1.2**：Playwright 运行时 + LLM 审查与修复 |
| `95` | 发布生成结果 | 保存 bundle、更新状态并准备交付 |
| `100` | 发布生成结果 | 终态完成，qualityScore 已写入 |

> **重要：** 对外动画现在固定为 5 个 display stage。内部 raw stage 如 `intent_parsing`、`template_matching`、`contract_qa` 只保留在 `details.rawStage` 中用于排障，前端不要再直接拿 raw stage 驱动动画。

### 事件：`gen:complete` — 生成完成

```typescript
// 监听
socket.on('gen:complete', (data: GenCompleteEvent) => {
  if (data.data.success) {
    navigateToGame(data.gameId);
  }
});

// 事件结构
interface GenCompleteEvent {
  type: 'gen:complete';
  gameId: string;
  previewUrl: string;
  data: {
    success: true;
    game: {
      id: string;
      gameUrl: string;      // 可玩地址
      previewUrl: string;
      status: 'ready';
    };
    error: null;
  };
  timestamp: number;
  status: 'success';
}
```

### 事件：`gen:error` — 生成失败终态

```typescript
socket.on('gen:error', (data: GenErrorEvent) => {
  showError(data.data.error);
  markGenerationFailed(data.gameId, data.stage);
});

interface GenErrorEvent {
  type: 'gen:error';
  gameId: string;
  stage: string;          // 失败阶段英文 code
  status: 'error';
  details: {
    stage?: string;
    retryCount?: number;
    fallback?: string;
  };
  data: {
    success: false;
    error: string;
    details: Record<string, unknown>;
  };
  timestamp: number;
}
```

### 事件：`notification` — 生成失败通知（兼容）

```typescript
socket.on('notification', (data: NotificationEvent) => {
  if (data.type === 'error') {
    showError(data.message);
  }
});

interface NotificationEvent {
  type: 'error' | 'info';
  message: string;
  gameId?: string;
  timestamp: number;
  id: string;
}
```

> 建议前端把 `gen:error` 作为生成失败终态的主监听事件，`notification` 仅保留给旧逻辑和通用 toast。

---

## 四、新增接口：创作者声誉（P2.2）

### `GET /api/v1/games/creator/:creatorId/reputation`

**请求：**
```
GET /api/v1/games/creator/{creatorId}/reputation
Authorization: 无需（公开接口）
```

**响应体：**
```json
{
  "success": true,
  "data": {
    "creatorId": "user-uuid-string",
    "totalGames": 12,
    "publishedGames": 8,
    "avgQualityScore": 7.43,
    "totalPlays": 3420,
    "totalLikes": 891,
    "failedGenerations": 2,
    "reputationScore": 72,
    "tier": "trusted"
  }
}
```

**响应字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `creatorId` | `string` | 创作者用户 ID |
| `totalGames` | `number` | 总创作次数（含失败、草稿） |
| `publishedGames` | `number` | 已发布游戏数 |
| `avgQualityScore` | `number` | 已发布游戏平均 AI 质量分（0-10） |
| `totalPlays` | `number` | 所有已发布游戏的累计游玩数 |
| `totalLikes` | `number` | 所有已发布游戏的累计点赞数 |
| `failedGenerations` | `number` | 生成失败次数 |
| `reputationScore` | `number` | 综合声誉分（0-100 整数） |
| `tier` | `string` | 等级：`new` \| `trusted` \| `verified` \| `flagged` |

**TypeScript 类型定义：**
```typescript
interface CreatorReputation {
  creatorId: string;
  totalGames: number;
  publishedGames: number;
  avgQualityScore: number;
  totalPlays: number;
  totalLikes: number;
  failedGenerations: number;
  reputationScore: number;    // 0-100 整数
  tier: 'new' | 'trusted' | 'verified' | 'flagged';
}
```

### Tier 等级规则

| Tier | 条件 | 展示建议 |
|------|------|---------|
| `verified` | reputationScore >= 80 且 publishedGames >= 5 | 绿色「认证创作者」 |
| `trusted` | reputationScore >= 50 且 publishedGames >= 3 | 蓝色「信任创作者」 |
| `new` | publishedGames < 3 | 灰色「新创作者」 |
| `flagged` | 失败率 > 30% 或 reputationScore < 20 | 橙色「待审核」 |

### 声誉分构成（前端提示文案参考）

| 权重 | 维度 | 计算方式 |
|------|------|---------|
| 40% | 作品质量 | 平均 qualityScore / 10 × 40 |
| 30% | 创作成功率 | 已发布 / 总创作次数 × 30 |
| 20% | 互动率 | min(点赞 / 游玩, 1) × 20 |
| 10% | 作品数量 | min(publishedGames, 10) |

### 展示组件示例

```typescript
const TIER_CONFIG = {
  new:      { label: '新创作者', color: '#9E9E9E', badge: 'STARTER' },
  trusted:  { label: '信任创作者', color: '#2196F3', badge: 'TRUSTED' },
  verified: { label: '认证创作者', color: '#4CAF50', badge: 'VERIFIED' },
  flagged:  { label: '待审核',   color: '#FF5722', badge: 'REVIEW' },
} as const;

function TierBadge({ tier }: { tier: keyof typeof TIER_CONFIG }) {
  const config = TIER_CONFIG[tier];
  return (
    <span style={{ color: config.color, border: `1px solid ${config.color}` }}>
      {config.badge} {config.label}
    </span>
  );
}

function ReputationCard({ rep }: { rep: CreatorReputation }) {
  return (
    <div>
      <TierBadge tier={rep.tier} />
      <div>声誉分：{rep.reputationScore}/100</div>
      <div>平均质量：{rep.avgQualityScore.toFixed(1)}/10</div>
      <div>已发布：{rep.publishedGames} 个游戏</div>
      <div>累计游玩：{rep.totalPlays.toLocaleString()} 次</div>
    </div>
  );
}
```

---

## 五、qualityScore 动态刷新策略（P2.1）

qualityScore 在以下操作后会**异步刷新**（后端 setImmediate 非阻塞），不会立刻反映在当次请求响应中：

| 触发事件 | 更新条件 | 延迟 |
|---------|---------|------|
| 用户游玩游戏 | playCount 达到 10 的倍数时 | < 100ms（异步） |
| 用户点赞 | 每次点赞都触发 | < 100ms（异步） |

**注意：** 后端未主动 push qualityScore 变更事件，前端需主动获取最新值。

### 方案 A：轮询（推荐）

```typescript
// 游戏详情页，每 30 秒轮询一次
function useQualityScore(gameId: string) {
  const [score, setScore] = useState<number>(0);

  useEffect(() => {
    const fetchScore = async () => {
      const res = await fetch(`/api/v1/games/${gameId}`);
      const data = await res.json();
      setScore(data.data.qualityScore);
    };

    fetchScore(); // 立即执行一次
    const timer = setInterval(fetchScore, 30_000);
    return () => clearInterval(timer);
  }, [gameId]);

  return score;
}
```

### 方案 B：WebSocket 推送（需后端配合，推荐高频场景）

若需要实时更新（如游戏大厅排行榜），可请求后端在 `refreshQualityScore` 完成后额外推送事件：

```typescript
// 前端监听（需后端新增 emit 逻辑）
socket.on('game:qualityUpdate', (data: {
  gameId: string;
  qualityScore: number;
  timestamp: number;
}) => {
  updateGameScore(data.gameId, data.qualityScore);
});
```

---

## 六、需要新增的页面/组件清单

| 优先级 | 组件/页面 | 说明 |
|-------|---------|------|
| P0 必须 | `QualityBadge` | 游戏卡片/详情页展示 qualityScore 星级 |
| P0 必须 | 生成进度页「质量检测」loading | 80% 阶段增加「深度检测」提示动画 |
| P1 建议 | `TierBadge` | 创作者名称旁展示 tier 徽章 |
| P1 建议 | `ReputationCard` | 创作者主页声誉详情卡片 |
| P1 建议 | 创作者主页接口调用 | `GET /api/v1/games/creator/:id/reputation` |
| P2 可选 | qualityScore 轮询刷新 | 游戏详情页 30s 轮询 |
| P2 可选 | WebSocket `game:qualityUpdate` | 排行榜实时更新（需后端配合） |

---

## 七、接口速查表

| 接口 | 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|------|
| 生成游戏 | POST | `/api/v1/games/generate` | JWT 必须 | 返回 gameId，通过 WS 监听进度 |
| 游戏详情 | GET | `/api/v1/games/:id` | 无需 | 含 qualityScore |
| 我的游戏 | GET | `/api/v1/games/my` | JWT 必须 | 列表含 qualityScore |
| 已发布列表 | GET | `/api/v1/games/explore/published` | 无需 | 列表含 qualityScore |
| 发布游戏 | POST | `/api/v1/games/:id/publish` | JWT 必须 | 无变化 |
| 迭代游戏 | POST | `/api/v1/games/:id/iterate` | JWT 必须 | 无变化 |
| 游玩游戏 | GET | `/api/v1/games/:id/play` | 无需 | 触发 playCount++ → 可能刷新 qualityScore |
| 分享数据 | GET | `/api/v1/games/:id/share-data` | 无需 | 含 qualityScore |
| **创作者声誉** | **GET** | **`/api/v1/games/creator/:creatorId/reputation`** | **无需** | **P2.2 新增** |
