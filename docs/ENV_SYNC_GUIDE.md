# Gamevallies 环境变量同步指南

> 最后更新：2026-03-17
>
> `.env`、`.env.development`、`.env.deploy` 均在 `.gitignore` 中，不会被提交。
> 新成员加入或配置丢失时，按本文档创建对应文件。

---

## 一、文件总览

| 文件 | 仓库 | 用途 | 谁需要 |
|---|---|---|---|
| `front/.env` | gamevallies-front | **生产构建**时注入的后端 API 地址 | 部署人员 |
| `front/.env.development` | gamevallies-front | **本地开发**时覆盖 `.env` | 所有开发者 |
| `front/.env.deploy` | gamevallies-front | 前端部署到火山引擎的 VCR 凭证 | 部署人员 |
| `backend/.env` | gamevallies-backend | **本地开发**时后端各服务使用 | 所有开发者 |
| `backend/.env.deploy` | gamevallies-backend | 后端部署到火山引擎的全量配置 | 部署人员 |

---

## 二、前端 gamevallies-front

### 2.1 `.env`（生产构建 — 公网地址）

```bash
# ⚠️ 必须使用公网地址！浏览器直接访问，不能用 inner 内网地址！
TARO_APP_AUTH_SERVICE_URL=https://sd6n8k2up8bgiaakgor40.apigateway-cn-shanghai.volceapi.com
TARO_APP_GAME_SERVICE_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com
TARO_APP_SOCIAL_SERVICE_URL=https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai.volceapi.com
TARO_APP_FEED_SERVICE_URL=https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai.volceapi.com
TARO_APP_AI_SERVICE_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com
TARO_APP_WS_URL=
TARO_APP_GAME_CONTENT_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com
SENTRY_DSN=
SEGMENT_WRITE_KEY=
```

### 2.2 `.env.development`（本地开发 — 每人不同）

```bash
# 将 IP 改为你自己的本机 IP（ifconfig | grep "inet " | grep -v 127.0.0.1）
TARO_APP_AUTH_SERVICE_URL=http://<你的IP>:3001
TARO_APP_GAME_SERVICE_URL=http://<你的IP>:3002
TARO_APP_SOCIAL_SERVICE_URL=http://<你的IP>:3003
TARO_APP_FEED_SERVICE_URL=http://<你的IP>:3004
TARO_APP_AI_SERVICE_URL=http://<你的IP>:8001
TARO_APP_WS_URL=ws://<你的IP>:3001
TARO_APP_GAME_CONTENT_URL=http://<你的IP>:3002
SENTRY_DSN=
SEGMENT_WRITE_KEY=
```

### 2.3 `.env.deploy`（前端部署凭证）

```bash
VOLCENGINE_ACCESS_KEY=AKLTZjIyNTY4NDhiMDkwNDg0YzhiYTUzYjNlNmI1ZGVmNjA
VOLCENGINE_SECRET_KEY=T1RBMU1HWmpZakEzTkRRNU5EUmxORGxoWkdRNE9URmlNV1psT1RneU0yTQ==
VOLCENGINE_REGION=cn-shanghai
VOLCENGINE_REGISTRY=gamevallies-repo-cn-shanghai.cr.volces.com
VOLCENGINE_REGISTRY_NAMESPACE=gamevallies
VOLCENGINE_REGISTRY_USERNAME="6448手机用户#UeaqaB@2112970785"
VOLCENGINE_REGISTRY_PASSWORD=Gamevallies@2026
IMAGE_TAG=latest
```

---

## 三、后端 gamevallies-backend

### 3.1 `.env`（本地开发 — 所有人统一）

```bash
DATABASE_URL="mysql://root:piicko2026@localhost:3306/gamevallies"
JWT_SECRET=gamevallies-dev-secret-key-2026
JWT_REFRESH_SECRET=gamevallies-dev-refresh-secret-2026
JWT_EXPIRES_IN=24h
JWT_REFRESH_EXPIRES_IN=7d
NODE_ENV=development

# 服务间调用（本地）
USER_SERVICE_URL=http://localhost:3001
GAME_SERVICE_URL=http://localhost:3002
FEED_SERVICE_URL=http://localhost:3004
AI_ENGINE_URL=http://localhost:8000
APP_URL=http://localhost:3002

# LLM（本地开发用 DeepSeek）
LLM_MODE=real
LLM_API_KEY=sk-65a0f82bea6b4018bf46f0f6b7c4a57a
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_FAST_MODEL=deepseek-chat
```

### 3.2 `.env.deploy`（生产部署 — 全量配置）

```bash
# ── 火山引擎认证 ──────────────────────────────────────────
VOLCENGINE_ACCESS_KEY=AKLTZjIyNTY4NDhiMDkwNDg0YzhiYTUzYjNlNmI1ZGVmNjA
VOLCENGINE_SECRET_KEY=T1RBMU1HWmpZakEzTkRRNU5EUmxORGxoWkdRNE9URmlNV1psT1RneU0yTQ==
VOLCENGINE_REGION=cn-shanghai
VOLCENGINE_API_HOST=open.volcengineapi.com

# ── VPC / 子网 / 安全组 ──────────────────────────────────
VOLCENGINE_VPC_ID=vpc-7uh247krgohs72200skd7y04
VOLCENGINE_SUBNET_ID=subnet-33guvcwoe43y86k70bqnvis8n
VOLCENGINE_SECURITY_GROUP_ID=sg-7uh24dhusuf472200rliec4v

# ── 容器镜像仓库 (VCR) ────────────────────────────────────
VOLCENGINE_REGISTRY=gamevallies-repo-cn-shanghai.cr.volces.com
VOLCENGINE_REGISTRY_NAMESPACE=gamevallies
IMAGE_TAG=latest
VOLCENGINE_REGISTRY_USERNAME="6448手机用户#UeaqaB@2112970785"
VOLCENGINE_REGISTRY_PASSWORD="Gamevallies@2026"
VOLCENGINE_TOS_BUCKET=gamevallies-deploy

# ── 服务公网 API 网关地址（面向用户/浏览器访问）─────────────
FRONTEND_URL=https://www.gamevallies.com
USER_SERVICE_URL=https://sd6n8k2up8bgiaakgor40.apigateway-cn-shanghai.volceapi.com
GAME_SERVICE_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com
FEED_SERVICE_URL=https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai.volceapi.com

# ── 服务内网 API 网关地址（服务间调用）─────────────────────
AI_ENGINE_URL=https://sd6na7o7g00oknv60o970.apigateway-cn-shanghai-inner.volceapi.com

# ── 数据库（VPC 内网地址）─────────────────────────────────
DATABASE_URL=mysql://gamevallies:gamevallies@2026@mysql5f64263dff43.rds.ivolces.com:3306/gamevallies
REDIS_URL=redis://:gamevallies2026@redis-shzlsq69qwdo5877a.redis.ivolces.com:6379

# ── JWT 密钥（不可更换！）─────────────────────────────────
JWT_SECRET=02e9621b10d223a2aa1bd18b25bb1023802238dcf3de0f55ce3059c4e34290d0
JWT_REFRESH_SECRET=56154800a4084f1b89ba9459923bdd6bc2ae57f11bb4a0b5c7b6c58a20982f0a

# ── 跨域 ──────────────────────────────────────────────────
CORS_ORIGIN=*

# ── AI 引擎 / MiniMax ────────────────────────────────────
LLM_API_KEY=sk-cp-AyyibOC2T1XsLOKNFog1M1bN9eJcrTONnGZuySyKjxJWKdQ5G33yTyGPjicyBTd4VvLiqShREf5BqLebG1CQkG2NZFFQ75NOzP4eaQzWFVrGriknQqazFKY
LLM_BASE_URL=https://api.minimaxi.com
LLM_MODEL=MiniMax-M2.5
LLM_FAST_MODEL=MiniMax-M2.5
LLM_MODE=real

# ── 阿里云号码认证 / 短信服务 ────────────────────────────
ALIYUN_ACCESS_KEY_ID=LTAI5tFkYReK6cMtwcioNfGw
ALIYUN_ACCESS_KEY_SECRET=9SGZeB5N2SmEpzBVvQKUc1yvDA2Iwx
ALIYUN_REGION_ID=cn-hangzhou
ALIYUN_ENDPOINT=dypnsapi.aliyuncs.com
ALIYUN_SMS_SIGN_NAME=速通互联验证码
ALIYUN_SMS_TEMPLATE_CODE=100001
```

---

## 四、火山引擎各函数服务的环境变量（线上实际值）

以下是各函数服务在火山引擎控制台上配置的环境变量，作为参考和校验用。

### 4.1 gv-user-service (ID: o2lc6jtq, Port: 3001)

| 变量 | 值 |
|---|---|
| NODE_ENV | production |
| PORT | 3001 |
| DATABASE_URL | mysql://gamevallies:gamevallies@2026@mysql5f64263dff43.rds.ivolces.com:3306/gamevallies |
| REDIS_URL | redis://:gamevallies2026@redis-shzlsq69qwdo5877a.redis.ivolces.com:6379 |
| JWT_SECRET | 02e9621b...290d0 |
| JWT_REFRESH_SECRET | 56154800...82f0a |
| CORS_ORIGIN | * |
| ADMIN_TOKEN | admin123 |
| ALIYUN_ACCESS_KEY_ID | LTAI5tFkYReK6cMtwcioNfGw |
| ALIYUN_ACCESS_KEY_SECRET | 9SGZeB5N2SmEpzBVvQKUc1yvDA2Iwx |
| ALIYUN_REGION_ID | cn-hangzhou |
| ALIYUN_ENDPOINT | dypnsapi.aliyuncs.com |
| ALIYUN_SMS_SIGN_NAME | 速通互联验证码 |
| ALIYUN_SMS_TEMPLATE_CODE | 100001 |

### 4.2 gv-game-service (ID: 5gtqkf4z, Port: 3002)

| 变量 | 值 |
|---|---|
| NODE_ENV | production |
| PORT | 3002 |
| DATABASE_URL | (同上) |
| REDIS_URL | (同上) |
| JWT_SECRET | (同上) |
| JWT_REFRESH_SECRET | (同上) |
| CORS_ORIGIN | * |
| ADMIN_TOKEN | admin123 |
| APP_URL | https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com |
| GAME_SERVICE_URL | https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com |
| AI_ENGINE_URL | https://sd6na7o7g00oknv60o970.apigateway-cn-shanghai-**inner**.volceapi.com |

### 4.3 gv-feed-service (ID: 4w6qkqv7, Port: 3004)

| 变量 | 值 |
|---|---|
| NODE_ENV | production |
| PORT | 3004 |
| DATABASE_URL | (同上) |
| REDIS_URL | (同上) |
| JWT_SECRET | (同上) |
| JWT_REFRESH_SECRET | (同上) |
| CORS_ORIGIN | * |
| ADMIN_TOKEN | admin123 |
| APP_URL | https://www.gamevallies.com |
| GAME_SERVICE_URL | https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com |

### 4.4 gv-ai-engine (ID: v9b2a2dx, Port: 8000)

| 变量 | 值 |
|---|---|
| PORT | 8000 |
| LLM_API_KEY | sk-cp-Ayyib...KY |
| LLM_BASE_URL | https://api.minimaxi.com |
| LLM_MODEL | MiniMax-M2.5 |
| CORS_ORIGINS | ["*"] |

### 4.5 gv-frontend (ID: tsrtwmbw, Port: 8080)

| 变量 | 值 |
|---|---|
| PORT | 8080 |
| DEPLOY_TS | (自动生成) |

> 前端是纯静态 nginx，不读运行时环境变量。API 地址在构建时通过 `.env` 注入到 JS 中。

---

## 五、关键规则

### 5.1 公网 vs 内网地址

```
公网: xxx.apigateway-cn-shanghai.volceapi.com       ← 浏览器/用户访问
内网: xxx.apigateway-cn-shanghai-inner.volceapi.com  ← VPC 内服务间调用
```

| 场景 | 用哪种 |
|---|---|
| 前端 `.env` 中的 `TARO_APP_*` | **必须公网**（浏览器直接请求） |
| game-service 的 `APP_URL` / `GAME_SERVICE_URL` | **公网**（用于拼接给用户看的 gameUrl） |
| feed-service 的 `GAME_SERVICE_URL` | **公网**（同上，拼接 gameUrl） |
| game-service 的 `AI_ENGINE_URL` | **内网**（服务间调用） |

### 5.2 前端生产构建注意事项

```bash
# ⚠️ 必须先移除 .env.development，否则 Taro 会用开发地址覆盖生产地址！
mv .env.development .env.development.bak
npm run build:h5
mv .env.development.bak .env.development

# 然后执行部署
source .env.deploy && python3 scripts/deploy.py
```

### 5.3 后端服务部署命令

```bash
cd gamevallies-backend
set -a && source .env.deploy && set +a

# 构建指定服务（替换 SERVICE 和 PORT）
docker build --no-cache --platform linux/amd64 \
  --build-arg SERVICE=user-service --build-arg PORT=3001 \
  -t $VOLCENGINE_REGISTRY/$VOLCENGINE_REGISTRY_NAMESPACE/gv-user-service:latest .

# 推送
docker push $VOLCENGINE_REGISTRY/$VOLCENGINE_REGISTRY_NAMESPACE/gv-user-service:latest

# 通过 deploy.py 或控制台发布
```

### 5.4 服务端口映射

| 服务 | 本地端口 | 生产端口 |
|---|---|---|
| user-service | 3001 | 3001 |
| game-service | 3002 | 3002 |
| social-service | 3003 | (合入 feed-service) |
| feed-service | 3004 | 3004 |
| ai-engine | 8000 | 8000 |
| frontend (nginx) | 10089 | 8080 |

---

## 六、常见问题

| 问题 | 原因 | 解决 |
|---|---|---|
| 前端页面白屏 | 构建时注入了 localhost / inner 地址 | 检查 `.env` 是否用公网地址，构建前移除 `.env.development` |
| API 返回 gameUrl 是 localhost | game-service 缺 `APP_URL` 环境变量 | 在火山引擎控制台添加 |
| 短信验证码不发送 | user-service 缺阿里云 SMS 配置 | 添加 `ALIYUN_*` 环境变量 |
| Admin 面板 404 | 路径应为 `/admin`，API 为 `/api/v1/admin/*` | 检查路径 |
| 数据库 column not found | Prisma schema 和数据库不同步 | 执行 `prisma db push` 或手动 ALTER TABLE |
