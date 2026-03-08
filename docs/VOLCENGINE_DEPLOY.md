# 火山引擎函数服务部署手册

> 将 Gamevallies 后端部署到火山引擎函数服务（Serverless），数据库使用火山引擎云数据库 MySQL 和 Redis。

---

## 目录

1. [架构概览](#1-架构概览)
2. [前置准备](#2-前置准备)
3. [Step 1 — 创建 VPC 和安全组](#step-1--创建-vpc-和安全组)
4. [Step 2 — 开通云数据库 MySQL](#step-2--开通云数据库-mysql)
5. [Step 3 — 开通云数据库 Redis](#step-3--开通云数据库-redis)
6. [Step 4 — 初始化数据库](#step-4--初始化数据库)
7. [Step 5 — 构建函数代码包](#step-5--构建函数代码包)
8. [Step 6 — 部署 4 个 NestJS 函数](#step-6--部署-4-个-nestjs-函数)
9. [Step 7 — 部署 AI Engine（容器实例）](#step-7--部署-ai-engine容器实例)
10. [Step 8 — 配置 API 网关路由](#step-8--配置-api-网关路由)
11. [Step 9 — 验证部署](#step-9--验证部署)
12. [自动化部署（CI/CD）](#自动化部署cicd)
13. [环境变量速查表](#环境变量速查表)
14. [常见问题](#常见问题)

---

## 1. 架构概览

```
Internet
    │
    ▼
火山引擎 API 网关 (APIG)
    │
    ├── /api/v1/users/*     → 函数服务: gv-user-service
    ├── /api/v1/games/*     → 函数服务: gv-game-service
    ├── /api/v1/social/*    → 函数服务: gv-social-service
    ├── /api/v1/feed/*      → 函数服务: gv-feed-service
    └── /api/v1/ai/*        → 容器实例 (VCI): gv-ai-engine
              │
              ▼  (同 VPC 内网访问)
    ┌─────────────────────────────┐
    │     私有 VPC                │
    │  ┌──────────┐  ┌─────────┐ │
    │  │  MySQL   │  │  Redis  │ │
    │  │ RDS 8.0  │  │ 6.x/7.x│ │
    │  └──────────┘  └─────────┘ │
    └─────────────────────────────┘
```

**函数服务配置一览**

| 函数名 | 运行时 | Handler | 端口映射 |
|--------|--------|---------|---------|
| gv-user-service | Node.js 20 | `dist/lambda.main` | 原 3001 |
| gv-game-service | Node.js 20 | `dist/lambda.main` | 原 3002 |
| gv-social-service | Node.js 20 | `dist/lambda.main` | 原 3003 |
| gv-feed-service | Node.js 20 | `dist/lambda.main` | 原 3004 |
| gv-ai-engine | 容器镜像 | uvicorn | 原 8000 |

---

## 2. 前置准备

### 本地工具

```bash
# 验证 Node.js 版本（需要 20+）
node -v

# 安装 火山引擎 CLI（vestack）
# macOS:
brew install volcengine/tap/ve-stack

# 或直接下载：https://www.volcengine.com/docs/6737/136091
# 验证安装
ve --version
```

### 配置火山引擎 CLI 凭证

```bash
ve configure
# 依次填入：
#   Access Key ID:     （火山引擎控制台 → 访问控制 → 密钥管理）
#   Secret Access Key: （同上）
#   Region:            cn-beijing        ← 推荐华北（北京）
```

---

## Step 1 — 创建 VPC 和安全组

> 函数服务和数据库必须在同一 VPC 才能内网互通。

### 1.1 创建 VPC

1. 进入 **火山引擎控制台** → **私有网络 VPC** → **创建 VPC**
2. 填写：
   - 名称：`gamevallies-vpc`
   - 网段：`192.168.0.0/16`
   - 地域：`华北（北京）`
3. 创建子网：
   - 名称：`gamevallies-subnet`
   - 可用区：选默认
   - 网段：`192.168.1.0/24`

### 1.2 创建安全组

1. **私有网络** → **安全组** → **创建安全组**
2. 名称：`gamevallies-sg`
3. 入站规则添加：

| 协议 | 端口 | 来源 | 说明 |
|------|------|------|------|
| TCP | 3306 | 192.168.0.0/16 | MySQL（仅 VPC 内） |
| TCP | 6379 | 192.168.0.0/16 | Redis（仅 VPC 内） |
| TCP | 8000 | 192.168.0.0/16 | AI Engine |
| TCP | 443 | 0.0.0.0/0 | HTTPS |

> ⚠️ **MySQL 和 Redis 端口不要开放外网入站规则**，保证安全。

---

## Step 2 — 开通云数据库 MySQL

### 2.1 创建 RDS MySQL 实例

1. 控制台 → **云数据库 RDS** → **创建实例**
2. 配置选项：

| 配置项 | 推荐值 |
|--------|--------|
| 数据库类型 | MySQL 8.0 |
| 实例规格 | 通用型 2 核 4G（测试）/ 4 核 8G（生产） |
| 存储 | SSD 云盘 50 GB 起 |
| 网络 | 选择上一步创建的 `gamevallies-vpc` + `gamevallies-subnet` |
| 实例名称 | `gamevallies-mysql` |

3. 设置账号密码（**记录好，后续配置使用**）：
   - 用户名：`gamevallies`
   - 密码：自定义强密码

4. 创建完成后，点击实例 → **账号管理** → **创建账号**
5. 点击 **数据库管理** → **创建数据库**：
   - 数据库名：`gamevallies`
   - 字符集：`utf8mb4`

### 2.2 获取连接信息

实例详情页 → **连接信息**，记录：
- **内网地址**（形如 `rm-xxx.mysql.volces.com`）：mysql5f64263dff43.rds.ivolces.com
- **端口**：3306

> 函数服务使用内网地址，延迟低、免流量费。

---

## Step 3 — 开通云数据库 Redis

### 3.1 创建 Redis 实例

1. 控制台 → **云数据库 Redis** → **创建实例**
2. 配置：

| 配置项 | 推荐值 |
|--------|--------|
| 版本 | Redis 7.0 |
| 架构 | 主从版（测试）/ 集群版（生产） |
| 规格 | 1G 内存起 |
| 网络 | 同 `gamevallies-vpc` |
| 实例名称 | `gamevallies-redis` |
| 密码 | 自定义强密码 |

### 3.2 获取连接信息

实例详情页记录：
- **内网地址**（形如 `redis-xxx.redis.volces.com`）；redis-shzlsq69qwdo5877a.redis.ivolces.com
- **端口**：6379

---

## Step 4 — 初始化数据库

> 在本地使用云端 MySQL 连接信息，通过 Prisma 建表并写入种子数据。

### 4.1 开启 MySQL 外网访问（临时）

> 仅初始化时使用，完成后关闭。

1. RDS 实例 → **网络** → **开启外网地址**
2. 安全组临时添加入站：TCP 3306，来源 `你的本地 IP/32`

### 4.2 本地执行初始化

```bash
# 设置云端 DATABASE_URL
export DATABASE_URL="mysql://gamevallies:你的密码@mysql-5f64263dff43-public.rds.volces.com:3306/gamevallies"
npx prisma db push

export DATABASE_URL="mysql://gamevallies:gamevallies@2026@mysql-5f64263dff43-public.rds.volces.com:3306/gamevallies"



# 推送 Schema（建表）
npx prisma db push

# 写入开发种子数据（可选）
npm run db:seed

# 写入 12 款种子游戏（必须）
npm run db:init-games
```

### 4.3 关闭外网访问

初始化完成后，回到 RDS 控制台关闭外网地址，仅保留内网访问。

---

## Step 5 — 构建函数代码包

> 在本地构建并打包，生成 4 个 zip 文件上传到函数服务。

```bash
# 安装依赖（如未安装）
npm install

# 构建所有服务（约 5-10 分钟）
npm run lambda:build

# 或单独构建某个服务
npm run lambda:build:user
npm run lambda:build:game
npm run lambda:build:social
npm run lambda:build:feed
```

构建产物位于 `.lambda-dist/` 目录：

```
.lambda-dist/
├── user-service.zip    ← 上传给 gv-user-service 函数
├── game-service.zip    ← 上传给 gv-game-service 函数
├── social-service.zip  ← 上传给 gv-social-service 函数
└── feed-service.zip    ← 上传给 gv-feed-service 函数
```

> 单个 zip 包大小约 50~80 MB（含 node_modules 生产依赖）。

---

## Step 6 — 部署 4 个 NestJS 函数

> 每个服务重复以下步骤（共 4 次）。以 `user-service` 为例。

### 6.1 创建函数

1. 控制台 → **函数服务** → **函数列表** → **创建函数**
2. 基础配置：

| 字段 | 值 |
|------|-----|
| 函数名称 | `gv-user-service` |
| 运行时 | `Native Node.js 20.x` |
| 部署方式 | 本地上传代码（zip 包） |
| Webserver 模式 | **是** |
| 启动命令 | `node dist/main.js` |
| 监听端口 | `3001` |
| 描述 | 用户认证服务 |

3. 上传 `user-service.zip`

4. 高级配置：

| 字段 | 值 |
|------|-----|
| 内存规格 | 512 MB（可按需调整） |
| 超时时间 | 30 秒 |
| 并发数 | 按需（测试阶段 10 即可） |
| 网络 | **选择 VPC** → `gamevallies-vpc` + `gamevallies-subnet` |

> ⚠️ **必须配置 VPC**，否则函数无法访问内网 MySQL/Redis。

### 6.2 配置环境变量

函数详情 → **环境变量** → 添加以下变量（**每个服务都需要配置**）：

> 4 个服务的环境变量基本相同，统一配置如下：

| 变量名 | 值 | 备注 |
|--------|-----|------|
| `NODE_ENV` | `production` | |
| `PORT` | `3001` / `3002` / `3003` / `3004` | 各服务对应端口 |
| `DATABASE_URL` | `mysql://gamevallies:密码@mysql5f64263dff43.rds.ivolces.com:3306/gamevallies` | 内网地址 |
| `REDIS_URL` | `redis://:密码@redis-shzlsq69qwdo5877a.redis.ivolces.com:6379` | 内网地址 |
| `JWT_SECRET` | 32位以上随机字符串 | 4个服务保持一致 |
| `JWT_REFRESH_SECRET` | 32位以上随机字符串（与上面不同） | |
| `CORS_ORIGIN` | `*`（或前端域名） | |

**game-service 额外添加：**

| 变量名 | 值 |
|--------|-----|
| `AI_ENGINE_URL` | `http://gv-ai-engine内网IP:8000`（Step 7 完成后填写） |

**各服务端口对应：**

| 服务 | PORT |
|------|------|
| gv-user-service | `3001` |
| gv-game-service | `3002` |
| gv-social-service | `3003` |
| gv-feed-service | `3004` |

### 6.3 添加 HTTP 触发器

函数详情 → **触发器** → **创建触发器** → **HTTP 触发器**：

| 字段 | 值 |
|------|-----|
| 鉴权方式 | 无（由服务内部 JWT 鉴权） |
| 请求方法 | ANY |
| 路径 | `/*` |

创建后会生成一个访问 URL，格式如：
```
https://xxx.cn-beijing.volces.com/
```

**记录 4 个服务的触发器 URL**，Step 8 配置网关时使用。

### 6.4 测试函数

点击 **测试** → 发送以下测试事件（HTTP 触发器格式）：

```json
{
  "httpMethod": "GET",
  "path": "/api/v1/users/health",
  "headers": {},
  "queryStringParameters": {},
  "body": ""
}
```

返回 200 即部署成功。

### 6.5 其余 3 个服务

重复 6.1~6.4，函数名分别为：
- `gv-game-service`，zip：`game-service.zip`
- `gv-social-service`，zip：`social-service.zip`
- `gv-feed-service`，zip：`feed-service.zip`

---

## Step 7 — 部署 AI Engine（容器实例）

> Python FastAPI 使用**弹性容器实例（VCI）**部署，与函数服务在同一 VPC 内网互通。

### 7.1 构建 Docker 镜像

```bash
cd packages/ai-engine

# 构建镜像
docker build -t gamevallies-ai-engine:latest .

# 登录火山引擎容器镜像服务（CR）
docker login cr.volces.com -u 你的AccessKey

# 打标签
docker tag gamevallies-ai-engine:latest \
  cr.volces.com/你的命名空间/gamevallies-ai-engine:latest

# 推送
docker push cr.volces.com/你的命名空间/gamevallies-ai-engine:latest
```

### 7.2 创建弹性容器实例

1. 控制台 → **弹性容器服务 VCI** → **创建容器实例**
2. 配置：

| 字段 | 值 |
|------|-----|
| 实例名称 | `gv-ai-engine` |
| 镜像 | `cr.volces.com/你的命名空间/gamevallies-ai-engine:latest` |
| CPU | 2 核 |
| 内存 | 4 GB |
| 网络 | `gamevallies-vpc` + `gamevallies-subnet` |
| 端口 | 8000 |

3. 环境变量：

| 变量名 | 值 |
|--------|-----|
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | `mysql://gamevallies:密码@内网地址:3306/gamevallies` |
| `LLM_MODE` | `real` |
| `LLM_API_KEY` | DeepSeek API Key |
| `LLM_BASE_URL` | `https://api.deepseek.com` |
| `LLM_MODEL` | `deepseek-chat` |
| `REDIS_HOST` | Redis 内网地址 |
| `REDIS_PORT` | `6379` |
| `REDIS_PASSWORD` | Redis 密码 |

4. 记录 VCI 实例内网 IP，填入 `game-service` 的 `AI_ENGINE_URL` 环境变量。

---

## Step 8 — 配置 API 网关路由

> 统一入口，将不同路径路由到对应函数。

1. 控制台 → **API 网关** → **创建 API 分组**
   - 名称：`gamevallies-api`
   - 地域：华北（北京）

2. 创建 API，每条规则对应一个后端函数：

| API 路径 | 后端服务 | 说明 |
|---------|---------|------|
| `/api/v1/users/*` | gv-user-service 触发器 URL | 用户服务 |
| `/api/v1/auth/*` | gv-user-service 触发器 URL | 认证接口 |
| `/api/v1/games/*` | gv-game-service 触发器 URL | 游戏服务 |
| `/api/v1/social/*` | gv-social-service 触发器 URL | 社交服务 |
| `/api/v1/comments/*` | gv-social-service 触发器 URL | 评论接口 |
| `/api/v1/notifications/*` | gv-social-service 触发器 URL | 通知接口 |
| `/api/v1/feed/*` | gv-feed-service 触发器 URL | Feed 服务 |
| `/api/v1/search/*` | gv-feed-service 触发器 URL | 搜索接口 |
| `/api/v1/ai/*` | gv-ai-engine VCI 内网:8000 | AI 生成 |

3. 发布 API 分组，获取**公网访问域名**（形如 `xxx.cn-beijing.apigateway.volces.com`）。

---

## Step 9 — 验证部署

用以下命令逐一验证各服务正常运行（将 `API_BASE` 替换为网关域名）：

```bash
API_BASE="https://xxx.cn-beijing.apigateway.volces.com"

# 1. 用户服务 - 注册
curl -X POST $API_BASE/api/v1/users/register \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","email":"test@test.com","password":"Test@123456"}'

# 2. 用户服务 - 登录
curl -X POST $API_BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@test.com","password":"Test@123456"}'

# 3. 游戏服务 - 获取游戏列表（需先拿到 TOKEN）
TOKEN="登录返回的 access_token"
curl $API_BASE/api/v1/games \
  -H "Authorization: Bearer $TOKEN"

# 4. Feed 服务 - 热门游戏
curl $API_BASE/api/v1/feed/trending

# 5. AI Engine - 健康检查
curl $API_BASE/api/v1/ai/health
```

---

## 环境变量速查表

> 生产环境建议使用火山引擎**密钥管理服务（KMS）**存储敏感信息。

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `DATABASE_URL` | MySQL 内网连接串 | `mysql://user:pass@host:3306/gamevallies` |
| `REDIS_HOST` | Redis 内网地址 | `redis-xxx.redis.volces.com` |
| `REDIS_PORT` | Redis 端口 | `6379` |
| `REDIS_PASSWORD` | Redis 密码 | 自定义 |
| `JWT_SECRET` | JWT 签名密钥 | 32位以上随机字符串 |
| `JWT_REFRESH_SECRET` | Refresh Token 密钥 | 另一个随机字符串 |
| `JWT_EXPIRES_IN` | Access Token 有效期 | `24h` |
| `JWT_REFRESH_EXPIRES_IN` | Refresh Token 有效期 | `7d` |
| `AI_ENGINE_URL` | AI 引擎内网地址 | `http://192.168.1.x:8000` |
| `LLM_API_KEY` | DeepSeek API Key | `sk-xxx` |
| `LLM_BASE_URL` | LLM 接口地址 | `https://api.deepseek.com` |
| `NODE_ENV` | 运行环境 | `production` |
| `CORS_ORIGIN` | 跨域白名单 | 前端域名或 `*` |

---

## 常见问题

### Q1: 函数冷启动时间长？

函数服务存在冷启动问题（首次调用约 3~5 秒）。解决方案：
- 在函数配置中开启**预留并发**（Provisioned Concurrency）
- 或为关键服务设置**定时触发器**每 5 分钟 ping 一次保活

### Q2: Prisma 连接报错 `Can't reach database server`？

1. 确认函数已配置 VPC，且与 MySQL 同一子网
2. 检查安全组入站规则是否允许 3306 端口
3. 确认 `DATABASE_URL` 使用的是**内网地址**，而非外网地址

### Q3: `@prisma/client` 找不到二进制文件？

在 `prisma/schema.prisma` 的 `binaryTargets` 中已包含 `linux-musl-openssl-3.0.x`（火山引擎函数服务运行环境）。确保构建时执行了 `npx prisma generate`，且 `.prisma/client` 目录被打入 zip 包。

### Q4: 上传 zip 包超出限制（100MB）？

火山引擎函数服务单包限制 500MB。如仍超限：
1. 排除开发依赖：脚本已使用 `npm install --production`
2. 排除测试文件：脚本已使用 `--ignore-scripts`
3. 改用**对象存储（TOS）上传**：
   - 将 zip 上传到 TOS bucket
   - 函数代码来源选择"TOS 对象"

### Q5: WebSocket 连接（game-service）在函数服务不支持？

函数服务的 HTTP 触发器**不支持 WebSocket 长连接**。解决方案：
- 将 game-service 同时部署一份到 **ECS（云服务器）** 或 **VCI（容器实例）** 单独处理 WebSocket 连接
- 或改用火山引擎**消息队列（MQ）** 替代 WebSocket 实时推送

### Q6: 如何更新已部署的函数？

```bash
# 重新构建
npm run lambda:build:user

# 在控制台手动上传新 zip
# 或使用 CLI 更新：
ve faas update-function \
  --function-name gv-user-service \
  --zip-file .lambda-dist/user-service.zip \
  --region cn-beijing
```

---

## 自动化部署（CI/CD）

> 配置完成后，每次 `git push main` 自动构建并部署所有服务，无需手动上传 zip。

### 方式一：GitHub Actions 自动部署（推荐）

已配置 `.github/workflows/deploy.yml`，触发条件：推送到 `main` 分支。

**配置步骤：**

1. 打开 GitHub 仓库 → **Settings** → **Secrets and variables** → **Actions**
2. 添加以下 Secrets：

| Secret 名称 | 值 |
|------------|-----|
| `VOLCENGINE_ACCESS_KEY` | 火山引擎控制台 → 访问控制 → 密钥管理 |
| `VOLCENGINE_SECRET_KEY` | 同上 |
| `DATABASE_URL` | `mysql://gamevallies:密码@mysql5f64263dff43.rds.ivolces.com:3306/gamevallies` |
| `REDIS_URL` | `redis://:密码@redis-shzlsq69qwdo5877a.redis.ivolces.com:6379` |
| `JWT_SECRET` | 生成的 JWT 密钥 |
| `JWT_REFRESH_SECRET` | 生成的 JWT 刷新密钥 |
| `CORS_ORIGIN` | `*` 或实际前端域名 |

3. 推送代码触发部署，或在 GitHub Actions 页面手动点击 **Run workflow**。

### 方式二：本地一键部署

```bash
# 1. 复制并编辑配置文件
cp .env.deploy.example .env.deploy
# 编辑 .env.deploy 填入实际值

# 2. 加载环境变量
source .env.deploy

# 3. 构建并部署所有服务（自动完成）
npm run lambda:build && python scripts/deploy.py

# 或只部署单个服务
python scripts/deploy.py user-service
```

> **提示：** 首次部署需要在控制台手动创建函数（Step 6.1），后续更新代码只需运行上述脚本即可。

---

## 部署检查清单

- [ ] VPC 和安全组已创建
- [ ] MySQL 实例已创建，数据库 `gamevallies` 已建立
- [ ] Redis 实例已创建
- [ ] 本地已执行 `prisma db push` 建表
- [ ] 本地已执行 `npm run db:init-games` 写入种子游戏
- [ ] 4 个 NestJS 服务 zip 包已构建
- [ ] 4 个函数已创建并上传代码
- [ ] 4 个函数均已配置 VPC
- [ ] 4 个函数均已配置环境变量
- [ ] 4 个函数均已添加 HTTP 触发器
- [ ] AI Engine 容器镜像已推送
- [ ] VCI 容器实例已创建
- [ ] game-service 的 `AI_ENGINE_URL` 已填写
- [ ] API 网关路由规则已配置
- [ ] 所有接口验证通过
