# GameVallies Environment Sync Guide

Last updated: 2026-03-20

This document is the single source of truth for backend runtime and deployment
variables. The current production setup uses one public domain:

`https://www.gamevallies.com`

All public URLs returned to the mini program and H5 should use this domain.
Service-to-service proxying inside `gv-user-service` uses private upstream URLs.

## 1. File roles

| File | Purpose |
| --- | --- |
| `backend/.env` | Local runtime reference file for backend services |
| `backend/.env.example` | Runtime key template with the latest key set |
| `backend/.env.deploy` | Actual deployment variables for local manual deploy |
| `backend/.env.deploy.example` | Deploy template with the latest key set |

## 2. Runtime variables

The backend currently expects the following runtime variables:

```bash
NODE_ENV=production
PORT=3001
CORS_ORIGIN=*
ADMIN_TOKEN=admin123

DATABASE_URL=mysql://gamevallies:gamevallies@2026@mysql5f64263dff43.rds.ivolces.com:3306/gamevallies
REDIS_URL=redis://:gamevallies2026@redis-shzlsq69qwdo5877a.redis.ivolces.com:6379

JWT_SECRET=02e9621b10d223a2aa1bd18b25bb1023802238dcf3de0f55ce3059c4e34290d0
JWT_REFRESH_SECRET=56154800a4084f1b89ba9459923bdd6bc2ae57f11bb4a0b5c7b6c58a20982f0a

PUBLIC_API_BASE_URL=https://www.gamevallies.com
USER_SERVICE_URL=https://www.gamevallies.com
GAME_SERVICE_URL=https://www.gamevallies.com
FEED_SERVICE_URL=https://www.gamevallies.com
AI_ENGINE_URL=https://sd6na7o7g00oknv60o970.apigateway-cn-shanghai-inner.volceapi.com
GAME_SERVICE_UPSTREAM_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai-inner.volceapi.com
FEED_SERVICE_UPSTREAM_URL=https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai-inner.volceapi.com

ALIYUN_ACCESS_KEY_ID=LTAI5tFkYReK6cMtwcioNfGw
ALIYUN_ACCESS_KEY_SECRET=9SGZeB5N2SmEpzBVvQKUc1yvDA2Iwx
ALIYUN_SMS_REGION_ID=cn-hangzhou
ALIYUN_SMS_SIGN_NAME=智了科技
ALIYUN_SMS_TPL_REGISTER=SMS_503430059
ALIYUN_SMS_TPL_LOGIN=SMS_503470064
VERIFY_CODE_SEND_INTERVAL_SECONDS=60

WECHAT_MINIAPP_APP_ID=wx77918f137dc7f5a5
WECHAT_MINIAPP_APP_SECRET=895a0f5d6c9360fd744c6246ee6313a6
```

## 3. Deployment-only variables

Manual deployment also requires Volcengine and VCR variables:

```bash
VOLCENGINE_ACCESS_KEY=...
VOLCENGINE_SECRET_KEY=...
VOLCENGINE_REGION=cn-shanghai
VOLCENGINE_API_HOST=open.volcengineapi.com
VOLCENGINE_VPC_ID=vpc-7uh247krgohs72200skd7y04
VOLCENGINE_SUBNET_ID=subnet-33guvcwoe43y86k70bqnvis8n
VOLCENGINE_SECURITY_GROUP_ID=sg-7uh24dhusuf472200rliec4v

VOLCENGINE_REGISTRY=gamevallies-repo-cn-shanghai.cr.volces.com
VOLCENGINE_REGISTRY_NAMESPACE=gamevallies
VOLCENGINE_REGISTRY_USERNAME="6448手机用户#UeaqaB@2112970785"
VOLCENGINE_REGISTRY_PASSWORD="Gamevallies@2026"
VOLCENGINE_TOS_BUCKET=gamevallies-deploy
IMAGE_TAG=latest
```

If you deploy `ai-engine`, keep these variables in `.env.deploy` as well:

```bash
LLM_MODE=real
LLM_API_KEY=...
LLM_BASE_URL=https://api.minimaxi.com
LLM_MODEL=MiniMax-M2.5
LLM_FAST_MODEL=MiniMax-M2.5
```

## 4. Public vs private URL rules

Use these rules consistently:

- `PUBLIC_API_BASE_URL` must be `https://www.gamevallies.com`
- `USER_SERVICE_URL`, `GAME_SERVICE_URL`, `FEED_SERVICE_URL` should also be `https://www.gamevallies.com`
- `AI_ENGINE_URL`, `GAME_SERVICE_UPSTREAM_URL`, `FEED_SERVICE_UPSTREAM_URL` must stay on the private `*-inner.volceapi.com` network

Why:

- Frontend and mini program only know the single public domain
- `gv-user-service` handles unified proxy routes such as `/api/v1/games`,
  `/api/v1/feed`, `/api/v1/social`, `/api/v1/comments`, `/api/v1/ai`,
  `/games/*`
- Internal forwarding should not bounce back through the public domain

## 5. SMS variable names that must be used

The current code reads these exact names:

- `ALIYUN_ACCESS_KEY_ID`
- `ALIYUN_ACCESS_KEY_SECRET`
- `ALIYUN_SMS_REGION_ID`
- `ALIYUN_SMS_SIGN_NAME`
- `ALIYUN_SMS_TPL_REGISTER`
- `ALIYUN_SMS_TPL_LOGIN`
- `VERIFY_CODE_SEND_INTERVAL_SECONDS`

Do not use these old names anymore:

- `ALIYUN_REGION_ID`
- `ALIYUN_ENDPOINT`
- `ALIYUN_SMS_TEMPLATE_CODE`
- `ALIYUN_SMS_TEMPLATE_PARAM_CODE`
- `ALIYUN_SMS_TEMPLATE_PARAM_MIN`

## 6. Production function env checklist

### 6.1 gv-user-service

Must contain at least:

- `NODE_ENV=production`
- `PORT=3001`
- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `JWT_REFRESH_SECRET`
- `CORS_ORIGIN=*`
- `ADMIN_TOKEN=admin123`
- `PUBLIC_API_BASE_URL=https://www.gamevallies.com`
- `USER_SERVICE_URL=https://www.gamevallies.com`
- `GAME_SERVICE_URL=https://www.gamevallies.com`
- `FEED_SERVICE_URL=https://www.gamevallies.com`
- `AI_ENGINE_URL=https://sd6na7o7g00oknv60o970.apigateway-cn-shanghai-inner.volceapi.com`
- `GAME_SERVICE_UPSTREAM_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai-inner.volceapi.com`
- `FEED_SERVICE_UPSTREAM_URL=https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai-inner.volceapi.com`
- `ALIYUN_ACCESS_KEY_ID`
- `ALIYUN_ACCESS_KEY_SECRET`
- `ALIYUN_SMS_REGION_ID=cn-hangzhou`
- `ALIYUN_SMS_SIGN_NAME=智了科技`
- `ALIYUN_SMS_TPL_REGISTER=SMS_503430059`
- `ALIYUN_SMS_TPL_LOGIN=SMS_503470064`
- `VERIFY_CODE_SEND_INTERVAL_SECONDS=60`
- `WECHAT_MINIAPP_APP_ID`
- `WECHAT_MINIAPP_APP_SECRET`

### 6.2 gv-game-service

Must contain at least:

- `NODE_ENV=production`
- `PORT=3002`
- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `JWT_REFRESH_SECRET`
- `CORS_ORIGIN=*`
- `ADMIN_TOKEN=admin123`
- `PUBLIC_API_BASE_URL=https://www.gamevallies.com`
- `APP_URL=https://www.gamevallies.com`
- `AI_ENGINE_URL=https://sd6na7o7g00oknv60o970.apigateway-cn-shanghai-inner.volceapi.com`

### 6.3 gv-feed-service

Must contain at least:

- `NODE_ENV=production`
- `PORT=3004`
- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`
- `JWT_REFRESH_SECRET`
- `CORS_ORIGIN=*`
- `ADMIN_TOKEN=admin123`
- `PUBLIC_API_BASE_URL=https://www.gamevallies.com`
- `APP_URL=https://www.gamevallies.com`
- `GAME_SERVICE_URL=https://www.gamevallies.com`

### 6.4 gv-ai-engine

Must contain at least:

- `PORT=8000`
- `DATABASE_URL`
- `REDIS_URL`
- `LLM_MODE`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `LLM_FAST_MODEL`
- `CORS_ORIGINS=["*"]`

## 7. Verification checks after deploy

After publishing, verify these URLs:

```bash
curl https://www.gamevallies.com/33zqDBay4T.txt
curl https://www.gamevallies.com/api/v1/health
curl https://www.gamevallies.com/api/v1/games/explore/published?limit=1
curl https://www.gamevallies.com/api/v1/feed/latest?limit=1
```

Expected:

- `33zqDBay4T.txt` returns `5142b16983df09708831078604fbcfeb`
- auth routes are no longer `404`
- `gameUrl`, `previewUrl`, and share URLs are all based on `https://www.gamevallies.com`

## 8. Common mistakes

- Using old Aliyun SMS variable names
- Leaving one service on an old API gateway domain
- Setting `GAME_SERVICE_UPSTREAM_URL` or `FEED_SERVICE_UPSTREAM_URL` to the public domain instead of the private inner domain
- Forgetting the double quotes around `VOLCENGINE_REGISTRY_USERNAME` because it contains `#`
- Updating code but not refreshing `.env.deploy`
