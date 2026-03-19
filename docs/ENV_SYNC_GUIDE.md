# Gamevallies Environment Variable Guide

> Last updated: 2026-03-19
>
> This document intentionally contains no real secrets.
> Use placeholders here, and put actual values only in local `.env` files or your deployment secret manager.

---

## 1. Files

| File | Repo | Purpose | Who uses it |
|---|---|---|---|
| `front/.env` | `gamevallies-front` | Frontend production build-time variables | Deployers |
| `front/.env.development` | `gamevallies-front` | Frontend local development variables | Developers |
| `front/.env.deploy` | `gamevallies-front` | Frontend deployment credentials | Deployers |
| `backend/.env` | `gamevallies-backend` | Backend local development variables | Developers |
| `backend/.env.deploy` | `gamevallies-backend` | Backend deployment variables | Deployers |
| `backend/.env.example` | `gamevallies-backend` | Safe example template | Everyone |

---

## 2. Frontend

### 2.1 Production build example

Recommended unified-domain setup:

```bash
TARO_APP_API_BASE=https://<unified-api-domain>
TARO_APP_AUTH_SERVICE_URL=https://<unified-api-domain>
TARO_APP_GAME_SERVICE_URL=https://<unified-api-domain>
TARO_APP_SOCIAL_SERVICE_URL=https://<unified-api-domain>
TARO_APP_FEED_SERVICE_URL=https://<unified-api-domain>
TARO_APP_AI_SERVICE_URL=https://<unified-api-domain>
TARO_APP_GAME_CONTENT_URL=https://<unified-api-domain>
TARO_APP_WS_URL=
SENTRY_DSN=
SEGMENT_WRITE_KEY=
```

Legacy multi-domain setup:

```bash
TARO_APP_AUTH_SERVICE_URL=https://<auth-gateway-domain>
TARO_APP_GAME_SERVICE_URL=https://<game-gateway-domain>
TARO_APP_SOCIAL_SERVICE_URL=https://<social-gateway-domain>
TARO_APP_FEED_SERVICE_URL=https://<feed-gateway-domain>
TARO_APP_AI_SERVICE_URL=https://<ai-gateway-domain>
TARO_APP_WS_URL=
TARO_APP_GAME_CONTENT_URL=https://<game-content-domain>
SENTRY_DSN=
SEGMENT_WRITE_KEY=
```

### 2.2 Local development example

```bash
TARO_APP_AUTH_SERVICE_URL=http://<your-ip>:3001
TARO_APP_GAME_SERVICE_URL=http://<your-ip>:3002
TARO_APP_SOCIAL_SERVICE_URL=http://<your-ip>:3003
TARO_APP_FEED_SERVICE_URL=http://<your-ip>:3004
TARO_APP_AI_SERVICE_URL=http://<your-ip>:8001
TARO_APP_WS_URL=ws://<your-ip>:3001
TARO_APP_GAME_CONTENT_URL=http://<your-ip>:3002
SENTRY_DSN=
SEGMENT_WRITE_KEY=
```

### 2.3 Frontend deploy credentials example

```bash
VOLCENGINE_ACCESS_KEY=<volcengine_access_key>
VOLCENGINE_SECRET_KEY=<volcengine_secret_key>
VOLCENGINE_REGION=cn-shanghai
VOLCENGINE_REGISTRY=<registry_host>
VOLCENGINE_REGISTRY_NAMESPACE=<registry_namespace>
VOLCENGINE_REGISTRY_USERNAME=<registry_username>
VOLCENGINE_REGISTRY_PASSWORD=<registry_password>
IMAGE_TAG=latest
```

---

## 3. Backend

### 3.1 Local development example

```bash
DATABASE_URL="mysql://<user>:<password>@localhost:3306/gamevallies"
REDIS_URL=redis://:<password>@localhost:6379

JWT_SECRET=<jwt_secret>
JWT_REFRESH_SECRET=<jwt_refresh_secret>
JWT_EXPIRES_IN=24h
JWT_REFRESH_EXPIRES_IN=7d
NODE_ENV=development

USER_SERVICE_URL=http://localhost:3001
GAME_SERVICE_URL=http://localhost:3002
FEED_SERVICE_URL=http://localhost:3004
AI_ENGINE_URL=http://localhost:8000
GAME_SERVICE_UPSTREAM_URL=http://localhost:3002
FEED_SERVICE_UPSTREAM_URL=http://localhost:3004
PUBLIC_API_BASE_URL=http://localhost:3001
APP_URL=http://localhost:3002

LLM_MODE=real
LLM_API_KEY=<llm_api_key>
LLM_BASE_URL=https://<llm-base-url>
LLM_MODEL=<llm_model>
LLM_FAST_MODEL=<llm_fast_model>
```

### 3.2 Deployment example

```bash
VOLCENGINE_ACCESS_KEY=<volcengine_access_key>
VOLCENGINE_SECRET_KEY=<volcengine_secret_key>
VOLCENGINE_REGION=cn-shanghai
VOLCENGINE_API_HOST=open.volcengineapi.com

VOLCENGINE_VPC_ID=<vpc_id>
VOLCENGINE_SUBNET_ID=<subnet_id>
VOLCENGINE_SECURITY_GROUP_ID=<security_group_id>

VOLCENGINE_REGISTRY=<registry_host>
VOLCENGINE_REGISTRY_NAMESPACE=<registry_namespace>
VOLCENGINE_REGISTRY_USERNAME=<registry_username>
VOLCENGINE_REGISTRY_PASSWORD=<registry_password>
VOLCENGINE_TOS_BUCKET=<deploy_bucket>
IMAGE_TAG=latest

FRONTEND_URL=https://<frontend-domain>
USER_SERVICE_URL=https://<user-service-domain>
GAME_SERVICE_URL=https://<game-service-domain>
FEED_SERVICE_URL=https://<feed-service-domain>
AI_ENGINE_URL=https://<ai-engine-inner-domain>
GAME_SERVICE_UPSTREAM_URL=https://<game-service-domain>
FEED_SERVICE_UPSTREAM_URL=https://<feed-service-domain>
PUBLIC_API_BASE_URL=https://<unified-api-domain>

DATABASE_URL=mysql://<user>:<password>@<mysql-host>:3306/gamevallies
REDIS_URL=redis://:<password>@<redis-host>:6379

JWT_SECRET=<jwt_secret>
JWT_REFRESH_SECRET=<jwt_refresh_secret>
CORS_ORIGIN=*

LLM_MODE=real
LLM_API_KEY=<llm_api_key>
LLM_BASE_URL=https://<llm-base-url>
LLM_MODEL=<llm_model>
LLM_FAST_MODEL=<llm_fast_model>

ALIYUN_ACCESS_KEY_ID=<aliyun_access_key_id>
ALIYUN_ACCESS_KEY_SECRET=<aliyun_access_key_secret>
ALIYUN_SMS_REGION_ID=cn-hangzhou
ALIYUN_SMS_SIGN_NAME=智了科技
ALIYUN_SMS_TPL_REGISTER=SMS_503430059
ALIYUN_SMS_TPL_LOGIN=SMS_503470064

WECHAT_MINIAPP_APP_ID=<wechat_miniapp_app_id>
WECHAT_MINIAPP_APP_SECRET=<wechat_miniapp_app_secret>
```

---

## 4. Service Checklist

### 4.1 User service

Required:

```bash
NODE_ENV=production
PORT=3001
DATABASE_URL=mysql://<user>:<password>@<mysql-host>:3306/gamevallies
REDIS_URL=redis://:<password>@<redis-host>:6379
JWT_SECRET=<jwt_secret>
JWT_REFRESH_SECRET=<jwt_refresh_secret>
CORS_ORIGIN=*
ALIYUN_ACCESS_KEY_ID=<aliyun_access_key_id>
ALIYUN_ACCESS_KEY_SECRET=<aliyun_access_key_secret>
ALIYUN_SMS_REGION_ID=cn-hangzhou
ALIYUN_SMS_SIGN_NAME=智了科技
ALIYUN_SMS_TPL_REGISTER=SMS_503430059
ALIYUN_SMS_TPL_LOGIN=SMS_503470064
WECHAT_MINIAPP_APP_ID=<wechat_miniapp_app_id>
WECHAT_MINIAPP_APP_SECRET=<wechat_miniapp_app_secret>
GAME_SERVICE_UPSTREAM_URL=https://<game-service-domain>
FEED_SERVICE_UPSTREAM_URL=https://<feed-service-domain>
```

### 4.2 Game service

Required:

```bash
NODE_ENV=production
PORT=3002
DATABASE_URL=mysql://<user>:<password>@<mysql-host>:3306/gamevallies
REDIS_URL=redis://:<password>@<redis-host>:6379
JWT_SECRET=<jwt_secret>
APP_URL=https://<game-service-domain>
PUBLIC_API_BASE_URL=https://<unified-api-domain>
GAME_SERVICE_URL=https://<game-service-domain>
AI_ENGINE_URL=https://<ai-engine-inner-domain>
```

### 4.3 Feed service

Required:

```bash
NODE_ENV=production
PORT=3004
DATABASE_URL=mysql://<user>:<password>@<mysql-host>:3306/gamevallies
REDIS_URL=redis://:<password>@<redis-host>:6379
JWT_SECRET=<jwt_secret>
APP_URL=https://<frontend-domain>
GAME_SERVICE_URL=https://<game-service-domain>
PUBLIC_API_BASE_URL=https://<unified-api-domain>
```

### 4.4 AI engine

Required:

```bash
PORT=8000
DATABASE_URL=mysql://<user>:<password>@<mysql-host>:3306/gamevallies
REDIS_URL=redis://:<password>@<redis-host>:6379
LLM_MODE=real
LLM_API_KEY=<llm_api_key>
LLM_BASE_URL=https://<llm-base-url>
LLM_MODEL=<llm_model>
LLM_FAST_MODEL=<llm_fast_model>
```

---

## 5. Rules

### 5.1 Public vs internal URLs

```text
Public:  xxx.apigateway-cn-shanghai.volceapi.com
Internal: xxx.apigateway-cn-shanghai-inner.volceapi.com
```

Use `PUBLIC_API_BASE_URL` for the browser-facing unified API domain.
Use public service URLs as upstreams unless you have matching internal gateway domains.
Use internal URLs only for service-to-service traffic inside the private network.

### 5.2 Frontend production build

```bash
mv .env.development .env.development.bak
npm run build:h5
mv .env.development.bak .env.development
```

### 5.3 Secret handling

- Never commit real keys, passwords, tokens, or production connection strings.
- Store local values in `.env`, `.env.development`, or `.env.deploy`, which should stay ignored by git.
- Store deployed values in your platform secret manager.
- If a real secret is ever committed, rotate it instead of only deleting it from docs.
