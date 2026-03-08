# Gamevallies Backend

AI 驱动的 HTML5 小游戏创作平台后端，基于 NestJS 微服务 + Python FastAPI + MySQL + Prisma 构建。

---

## 目录

- [技术架构](#技术架构)
- [快速启动](#快速启动)
- [数据库配置](#数据库配置)
- [AI 引擎配置](#ai-引擎配置)
- [测试数据](#测试数据)
- [测试](#测试)
- [服务端口](#服务端口)
- [环境变量](#环境变量)

---

## 技术架构

```
gamevallies-backend/
├── packages/
│   ├── user-service/     # NestJS 用户认证服务 (port 3001)
│   ├── game-service/     # NestJS 游戏管理服务 (port 3002)
│   ├── social-service/   # NestJS 社交互动服务 (port 3003)
│   ├── feed-service/     # NestJS 信息流/搜索服务 (port 3004)
│   ├── ai-engine/        # Python FastAPI AI 生成引擎 (port 8000)
│   └── shared/           # 共享类型和工具
├── prisma/
│   ├── schema.prisma     # MySQL 数据库 Schema
│   └── seed.ts           # 开发种子数据（5 用户 + 6 游戏 + 社交互动）
├── scripts/
│   └── init-games.ts     # 种子游戏业务初始化（12 款 HTML5 游戏，幂等）
└── test/
    └── integration/
        └── real-data.spec.ts  # 真实数据库集成测试
```

**核心技术栈**

| 层级 | 技术 |
|------|------|
| 语言 | TypeScript (Node.js 20) / Python 3.9 |
| NestJS 框架 | v10，微服务架构 |
| AI 引擎 | FastAPI + httpx，调用 DeepSeek API |
| 数据库 | MySQL 8.0 |
| ORM | Prisma 5.x |
| 认证 | JWT (access token 24h + refresh token 7d) |
| 测试 | Jest + ts-jest |

---

## 快速启动

### 前置条件

- Node.js 20+
- MySQL 8.0（本地已安装）
- Python 3.9+（运行 AI 引擎）

### 1. 安装依赖

```bash
npm install
```

### 2. 配置数据库

```bash
# 推送 Schema 到 MySQL（自动建表）
npx prisma db push

# 填充种子数据（5 个用户 + 6 个完整 HTML5 游戏）
npx ts-node --project tsconfig.base.json prisma/seed.ts
```

### 3. 启动各服务

```bash
# 用户服务
npm run dev:user        # http://localhost:3001

# 游戏服务
npm run dev:game        # http://localhost:3002

# 社交服务
npm run dev:social      # http://localhost:3003

# Feed 服务
npm run dev:feed        # http://localhost:3004

# AI 引擎
cd packages/ai-engine
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

或一键启动所有 NestJS 服务：

```bash
npm run dev:all
```

---

## 数据库配置

### 连接信息

| 参数 | 值 |
|------|----|
| Host | localhost |
| Port | 3306 |
| Database | gamevallies |
| User | root |
| Password | piicko2026 |

### 根目录 `.env`

```env
DATABASE_URL="mysql://root:piicko2026@localhost:3306/gamevallies"
JWT_SECRET=gamevallies-dev-secret-key-2026
JWT_REFRESH_SECRET=gamevallies-dev-refresh-secret-2026
JWT_EXPIRES_IN=24h
JWT_REFRESH_EXPIRES_IN=7d
NODE_ENV=development

# LLM / AI Engine
LLM_MODE=real
LLM_API_KEY=sk-65a0f82bea6b4018bf46f0f6b7c4a57a
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_FAST_MODEL=deepseek-chat
AI_ENGINE_URL=http://localhost:8000
```

### 数据库 Schema 核心模型

```
User          → 用户账号（role: user/creator/moderator/admin）
RefreshToken  → JWT 刷新令牌（支持撤销）
Game          → 游戏元数据（status: draft/published/banned 等）
GameBundle    → 游戏 HTML/CSS/JS 代码存储（LongText）
GameTemplate  → AI 生成模板库
SocialInteraction → 点赞/关注/分享等互动（唯一约束）
UserFollow    → 关注关系
Comment       → 游戏评论（支持多级回复）
Notification  → 消息通知（like/follow/comment/fork/system/earning）
CreatorEarning → 创作者收益记录
```

---

## AI 引擎配置

### DeepSeek API

AI 引擎使用 DeepSeek（OpenAI-compatible 接口）生成 HTML5 游戏代码。

**配置文件** `packages/ai-engine/.env`：

```env
ENVIRONMENT=development
PORT=8000

DATABASE_URL=mysql://root:piicko2026@localhost:3306/gamevallies

# 启用真实 AI 生成（改为 mock 可跳过 API 调用）
LLM_MODE=real

# DeepSeek API（OpenAI-compatible）
LLM_API_KEY=sk-65a0f82bea6b4018bf46f0f6b7c4a57a
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_FAST_MODEL=deepseek-chat

# Claude（可选备用）
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-4-6
CLAUDE_FAST_MODEL=claude-haiku-4-5-20251001

# Pipeline 调优
TEMPLATE_CONFIDENCE_THRESHOLD=0.8
HYBRID_CONFIDENCE_THRESHOLD=0.5
QA_MAX_RETRIES=3
PIPELINE_TIMEOUT_S=60
MAX_ITERATIONS=20
```

### 三路生成策略

```
用户输入描述
    │
    ▼
Intent Parser (置信度分析)
    │
    ├─ confidence ≥ 0.8 ──→ 模板直接填参  (Path A: ~3s，不消耗 Token)
    │
    ├─ 0.5 ≤ confidence < 0.8 ──→ 模板骨架 + DeepSeek 定制 (Path B)
    │
    └─ confidence < 0.5 ──→ DeepSeek 全量生成完整 HTML5 游戏 (Path C)
```

### 验证 API 连通性

```bash
curl -X POST https://api.deepseek.com/chat/completions \
  -H "Authorization: Bearer sk-65a0f82bea6b4018bf46f0f6b7c4a57a" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"OK"}],"max_tokens":5}'
```

---

## 数据初始化

### 脚本说明

| 脚本 | 命令 | 用途 | 幂等 |
|------|------|------|------|
| `prisma/seed.ts` | `npm run db:seed` | 开发测试数据（用户+游戏+社交互动） | 是（upsert） |
| `scripts/init-games.ts` | `npm run db:init-games` | 种子游戏业务初始化（12 款游戏） | 是（upsert） |

### 完整初始化流程

```bash
# 1. 推送 Schema
npx prisma db push

# 2. 填充开发用户和基础游戏数据
npm run db:seed

# 3. 写入 12 款种子游戏（业务初始化，可单独运行）
npm run db:init-games
```

---

## 测试数据

运行 `npm run db:seed` 后，数据库将包含：

### 测试用户（密码均为 `password123`）

| 用户名 | 邮箱 | 角色 | 简介 |
|--------|------|------|------|
| alice | alice@gamevallies.com | creator | 独立游戏开发者 |
| bobgamer | bob@gamevallies.com | user | 休闲玩家 |
| caroldev | carol@gamevallies.com | creator | AI 游戏设计师 |
| davidy | david@gamevallies.com | user | 动作游戏爱好者 |
| emmacraft | emma@gamevallies.com | creator | 策略游戏达人 |

### 6 个精品 HTML5 小游戏

每款游戏均包含完整可运行的 HTML5 代码（约 3-5 KB），存储在 `game_bundles` 表的 `html_code` 字段（`LongText`）。

| 游戏 | slug | 类型 | 游玩次数 | 点赞 | 质量分 |
|------|------|------|---------|------|-------|
| 🐦 飞翔小鸟 | `flappy-bird-clone` | casual | 44,600 | 3,201 | 9.7 |
| 🔨 打地鼠！ | `whack-a-mole` | casual | 31,200 | 2,104 | 9.5 |
| 🐍 贪吃蛇进化版 | `snake-evolution` | arcade | 25,680 | 1,842 | 9.2 |
| 🔢 2048 极限挑战 | `2048-challenge` | puzzle | 22,800 | 1,567 | 9.0 |
| 🧱 打砖块大师 | `breakout-master` | arcade | 18,420 | 1,356 | 8.8 |
| 🃏 记忆翻牌王 | `memory-card-game` | puzzle | 16,900 | 1,234 | 8.6 |

**游戏技术特性：**
- 纯原生 HTML5 Canvas，零外部依赖
- 支持键盘 + 触屏双端操作
- requestAnimationFrame 60fps 游戏循环
- 完整游戏逻辑（得分、生命、关卡、排行）

### 其他种子数据

| 数据类型 | 数量 |
|---------|------|
| 关注关系 | 6 条 |
| 游戏点赞 | 11 条 |
| 游戏评论 | 14 条 |
| 系统通知 | 8 条 |
| 收益记录 | 7 条 |

---

### 种子游戏（`npm run db:init-games`）

运行 `scripts/init-games.ts` 后，额外写入 12 款精选游戏，归属于系统账号 `seed_creator`：

**桌面端游戏（键盘 + 鼠标）**

| 游戏 | slug | 类型 | 游玩次数 | 质量分 |
|------|------|------|---------|-------|
| 贪吃蛇 | `seed-snake-classic` | casual | 3,241 | 8.8 |
| 打砖块 | `seed-breakout-classic` | casual | 5,102 | 9.2 |
| 记忆翻牌 | `seed-memory-card-classic` | puzzle | 2,187 | 8.5 |
| 打地鼠 | `seed-whack-mole-classic` | casual | 4,876 | 9.0 |
| 2048 | `seed-2048-classic` | puzzle | 8,934 | 9.5 |
| 太空射击 | `seed-space-shooter` | action | 6,521 | 9.3 |

**移动端游戏（触控优先）**

| 游戏 | slug | 类型 | 游玩次数 | 质量分 |
|------|------|------|---------|-------|
| 叠叠高塔 | `seed-stack-tower` | casual | 5,230 | 9.1 |
| 水果忍者 | `seed-fruit-ninja` | action | 8,910 | 9.4 |
| 泡泡消消 | `seed-bubble-pop` | casual | 4,650 | 8.8 |
| 消消星 | `seed-star-blast` | puzzle | 3,870 | 8.9 |
| 节奏达人 | `seed-rhythm-tap` | casual | 6,340 | 9.2 |
| 接水果 | `seed-catch-fruits` | casual | 4,120 | 8.7 |

**实现特点：**
- 完整可运行的 HTML5 单文件游戏（约 3~8 KB）
- 存入 `game_bundles.html_code`（`LongText`），无 MongoDB 依赖
- `GameBundle.spec` 中标记 `platform: desktop/mobile`
- 以 `slug` 为唯一键做 `upsert`，重复执行只更新统计数据
- 系统用户 `seed_creator` 自动创建（若不存在）

---

## 测试

### 单元测试（Mock 环境）

```bash
npm test --workspaces
```

| 服务 | 测试套件 | 测试数 | 状态 |
|------|---------|--------|------|
| user-service | 4 | 42 | ✅ |
| game-service | 3 | 32 | ✅ |
| social-service | 4 | 31 | ✅ |
| feed-service | 2 | 18 | ✅ |
| **合计** | **13** | **123** | **全部通过** |

覆盖范围：
- 用户注册/登录/刷新 Token/撤销 Token
- 游戏 CRUD、发布、Fork、游玩计数
- 点赞切换（toggle）、关注/取关
- 通知创建、标记已读
- Feed 热门排序、搜索过滤
- WebSocket 连接鉴权

### 真实数据库集成测试

```bash
DATABASE_URL=mysql://root:piicko2026@localhost:3306/gamevallies \
  npx jest --config jest.integration.config.js --verbose
```

**31 项测试，全部通过：**

| 测试组 | 验证内容 |
|--------|---------|
| 种子数据完整性 | 用户/游戏/代码包/评论/通知数量 |
| 用户账号 | bcrypt 密码验证、角色、OR 查询（email/username） |
| 游戏数据 | HTML 代码包完整性、关联查询、JSON 标签、排序 |
| 社交互动 | 关注关系、粉丝查询、唯一约束冲突检测 |
| 评论系统 | 按时间排序、关联用户数据 |
| 通知系统 | 类型多样性、标记已读（自清理） |
| 收益系统 | 收益记录查询、汇总计算 |
| Feed 聚合 | 热门排序、类型筛选、标题搜索、原子递增 |

---

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| user-service | 3001 | 认证、用户资料 |
| game-service | 3002 | 游戏管理、Fork、WebSocket |
| social-service | 3003 | 点赞、关注、评论、通知 |
| feed-service | 3004 | 热门推荐、搜索、标签 |
| ai-engine | 8000 | HTML5 游戏 AI 生成 |
| ai-engine docs | 8000/docs | FastAPI Swagger UI |

---

## 环境变量

### 各服务 `.env` 示例

**user-service** (`packages/user-service/.env`)
```env
NODE_ENV=development
PORT=3001
DATABASE_URL=mysql://root:piicko2026@localhost:3306/gamevallies
JWT_SECRET=gamevallies-dev-secret-key-2026
JWT_REFRESH_SECRET=gamevallies-dev-refresh-secret-2026
JWT_EXPIRES_IN=24h
JWT_REFRESH_EXPIRES_IN=7d
CORS_ORIGIN=*
```

**game-service** (`packages/game-service/.env`)
```env
NODE_ENV=development
PORT=3002
DATABASE_URL=mysql://root:piicko2026@localhost:3306/gamevallies
JWT_SECRET=gamevallies-dev-secret-key-2026
JWT_REFRESH_SECRET=gamevallies-dev-refresh-secret-2026
```

**social-service** (`packages/social-service/.env`)
```env
NODE_ENV=development
PORT=3003
DATABASE_URL=mysql://root:piicko2026@localhost:3306/gamevallies
JWT_SECRET=gamevallies-dev-secret-key-2026
```

**feed-service** (`packages/feed-service/.env`)
```env
NODE_ENV=development
PORT=3004
DATABASE_URL=mysql://root:piicko2026@localhost:3306/gamevallies
JWT_SECRET=gamevallies-dev-secret-key-2026
```

**ai-engine** (`packages/ai-engine/.env`)
```env
ENVIRONMENT=development
PORT=8000
DATABASE_URL=mysql://root:piicko2026@localhost:3306/gamevallies
LLM_MODE=real
LLM_API_KEY=sk-65a0f82bea6b4018bf46f0f6b7c4a57a
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_FAST_MODEL=deepseek-chat
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-sonnet-4-6
CLAUDE_FAST_MODEL=claude-haiku-4-5-20251001
TEMPLATE_CONFIDENCE_THRESHOLD=0.8
HYBRID_CONFIDENCE_THRESHOLD=0.5
QA_MAX_RETRIES=3
PIPELINE_TIMEOUT_S=60
MAX_ITERATIONS=20
SLOT_MIN_FILL_PCT=0.6
CORS_ORIGINS=["*"]
```

---

## 重新生成种子数据

### 仅重置开发数据（保留种子游戏）

```bash
# 清空开发测试数据（保留 seed_creator 和种子游戏）
mysql -u root -ppiicko2026 -e "
  SET FOREIGN_KEY_CHECKS=0;
  TRUNCATE TABLE gamevallies.creator_earnings;
  TRUNCATE TABLE gamevallies.notifications;
  TRUNCATE TABLE gamevallies.comments;
  TRUNCATE TABLE gamevallies.social_interactions;
  TRUNCATE TABLE gamevallies.user_follows;
  SET FOREIGN_KEY_CHECKS=1;
"

# 重新填充开发用户和游戏
npm run db:seed
```

### 完全重置（清空所有数据）

```bash
mysql -u root -ppiicko2026 -e "
  SET FOREIGN_KEY_CHECKS=0;
  TRUNCATE TABLE gamevallies.creator_earnings;
  TRUNCATE TABLE gamevallies.notifications;
  TRUNCATE TABLE gamevallies.comments;
  TRUNCATE TABLE gamevallies.social_interactions;
  TRUNCATE TABLE gamevallies.user_follows;
  TRUNCATE TABLE gamevallies.game_bundles;
  TRUNCATE TABLE gamevallies.games;
  TRUNCATE TABLE gamevallies.refresh_tokens;
  TRUNCATE TABLE gamevallies.users;
  SET FOREIGN_KEY_CHECKS=1;
"

# 重新填充所有数据
npm run db:seed && npm run db:init-games
```
