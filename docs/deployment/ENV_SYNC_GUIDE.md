# Gamevallies 环境变量同步指南

最后更新：2026-03-20

本文档只保留当前仍在使用的环境变量字段和同步规则，不再记录历史值、控制台抄录值或废弃部署方案。

## 1. 基本规则

- 本地开发使用 `.env`
- 生产发布使用 `.env.deploy`
- 字段模板以 `.env.example` 与 `.env.deploy.example` 为准
- 新增或删除字段时，必须同时更新模板文件和本说明
- 生产发布统一走 `scripts/deploy.py`

## 2. 本地 `.env`

本地开发至少需要以下字段：

```bash
DATABASE_URL=
JWT_SECRET=
JWT_REFRESH_SECRET=
NODE_ENV=development
USER_SERVICE_URL=
GAME_SERVICE_URL=
FEED_SERVICE_URL=
AI_ENGINE_URL=
```

如需运行 AI 引擎，再补充：

```bash
LLM_MODE=
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
LLM_FAST_MODEL=
```

## 3. 生产 `.env.deploy`

### 3.1 部署基础字段

```bash
VOLCENGINE_ACCESS_KEY=
VOLCENGINE_SECRET_KEY=
VOLCENGINE_REGION=
VOLCENGINE_API_HOST=
VOLCENGINE_VPC_ID=
VOLCENGINE_SUBNET_ID=
VOLCENGINE_SECURITY_GROUP_ID=
VOLCENGINE_REGISTRY=
VOLCENGINE_REGISTRY_NAMESPACE=
VOLCENGINE_REGISTRY_USERNAME=
VOLCENGINE_REGISTRY_PASSWORD=
VOLCENGINE_TOS_BUCKET=
IMAGE_TAG=
```

### 3.2 运行时公共字段

```bash
NODE_ENV=production
CORS_ORIGIN=
ADMIN_TOKEN=
DATABASE_URL=
REDIS_URL=
JWT_SECRET=
JWT_REFRESH_SECRET=
PUBLIC_API_BASE_URL=
USER_SERVICE_URL=
GAME_SERVICE_URL=
FEED_SERVICE_URL=
AI_ENGINE_URL=
GAME_SERVICE_UPSTREAM_URL=
FEED_SERVICE_UPSTREAM_URL=
```

### 3.3 `user-service` 专属字段

```bash
PORT=3001
WECHAT_MINIAPP_APP_ID=
WECHAT_MINIAPP_APP_SECRET=
ALIYUN_ACCESS_KEY_ID=
ALIYUN_ACCESS_KEY_SECRET=
ALIYUN_SMS_REGION_ID=
ALIYUN_SMS_SIGN_NAME=
ALIYUN_SMS_TPL_REGISTER=
ALIYUN_SMS_TPL_LOGIN=
VERIFY_CODE_SEND_INTERVAL_SECONDS=
```

### 3.4 `game-service` / `feed-service` 端口

```bash
# game-service
PORT=3002

# feed-service
PORT=3004
```

### 3.5 `ai-engine` 专属字段

```bash
PORT=8000
LLM_MODE=
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
LLM_FAST_MODEL=
```

## 4. 地址使用约定

- 对外给用户或浏览器访问的地址，统一使用公网地址
- 服务间调用优先使用内网 upstream 地址
- 前端构建时只能注入公网可访问地址
- 不再单独维护“控制台当前值”类文档

## 5. 变量来源速查

| 变量类别 | 典型字段 | 来源 |
| --- | --- | --- |
| 火山云账号与网络 | `VOLCENGINE_ACCESS_KEY`、`VOLCENGINE_SECRET_KEY`、`VOLCENGINE_VPC_ID`、`VOLCENGINE_SUBNET_ID`、`VOLCENGINE_SECURITY_GROUP_ID` | 火山云 IAM / VPC 控制台 |
| 镜像仓库 | `VOLCENGINE_REGISTRY`、`VOLCENGINE_REGISTRY_NAMESPACE`、`VOLCENGINE_REGISTRY_USERNAME`、`VOLCENGINE_REGISTRY_PASSWORD` | 火山云 VCR 控制台 |
| 对外域名与内部 upstream | `PUBLIC_API_BASE_URL`、`USER_SERVICE_URL`、`GAME_SERVICE_URL`、`FEED_SERVICE_URL`、`AI_ENGINE_URL`、`GAME_SERVICE_UPSTREAM_URL`、`FEED_SERVICE_UPSTREAM_URL` | 当前生产 `.env.deploy` + APIG 控制台 |
| 数据库与缓存 | `DATABASE_URL`、`REDIS_URL` | 生产数据库 / Redis 控制台或团队维护的生产 `.env.deploy` |
| 认证与通用运行时 | `JWT_SECRET`、`JWT_REFRESH_SECRET`、`ADMIN_TOKEN`、`CORS_ORIGIN` | 当前生产 `.env.deploy` |
| 微信小程序 | `WECHAT_MINIAPP_APP_ID`、`WECHAT_MINIAPP_APP_SECRET` | 微信小程序后台 |
| 阿里云短信 | `ALIYUN_ACCESS_KEY_ID`、`ALIYUN_ACCESS_KEY_SECRET`、`ALIYUN_SMS_REGION_ID`、`ALIYUN_SMS_SIGN_NAME`、`ALIYUN_SMS_TPL_REGISTER`、`ALIYUN_SMS_TPL_LOGIN` | 阿里云 RAM / 短信服务控制台 |
| AI 引擎模型 | `LLM_MODE`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_FAST_MODEL` | 当前 LLM 供应商控制台 + 生产 `.env.deploy` |
| 发布时动态设置 | `IMAGE_TAG` | 发布人本次部署时手动设置，必须使用唯一 tag |

## 6. 发布前检查

发布前至少确认：

- `.env.deploy` 中字段名与 `.env.deploy.example` 一致
- 新增变量已经在 `scripts/deploy.py` 中接入
- 当前要发布的服务专属变量没有缺失
- 不依赖控制台手工补环境变量

## 7. 有效文档

当前只承认以下两份部署相关文档：

- `docs/deployment/DEPLOY_RUNBOOK.md`
- `docs/deployment/ENV_SYNC_GUIDE.md`

其余旧的部署说明、总览、Kubernetes 方案、历史整理文档均已删除，不再作为当前发布依据。
