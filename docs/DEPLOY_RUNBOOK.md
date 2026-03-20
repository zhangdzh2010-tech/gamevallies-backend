# GameVallies Manual Deploy Runbook

Last updated: 2026-03-20

This runbook describes the current manual deployment flow. Do not use GitHub
Actions for this procedure.

The production public domain is:

`https://www.gamevallies.com`

## 1. Preconditions

Before you deploy, make sure these files are up to date:

- `backend/.env`
- `backend/.env.example`
- `backend/.env.deploy`
- `backend/.env.deploy.example`
- `backend/docs/ENV_SYNC_GUIDE.md`

The authoritative values for the current production rollout are:

- `PUBLIC_API_BASE_URL=https://www.gamevallies.com`
- `USER_SERVICE_URL=https://www.gamevallies.com`
- `GAME_SERVICE_URL=https://www.gamevallies.com`
- `FEED_SERVICE_URL=https://www.gamevallies.com`
- `AI_ENGINE_URL=https://sd6na7o7g00oknv60o970.apigateway-cn-shanghai-inner.volceapi.com`
- `GAME_SERVICE_UPSTREAM_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai-inner.volceapi.com`
- `FEED_SERVICE_UPSTREAM_URL=https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai-inner.volceapi.com`

## 2. Required variables

### 2.1 Deployment infrastructure

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

### 2.2 Runtime env shared by backend services

```bash
NODE_ENV=production
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
```

### 2.3 user-service only

```bash
PORT=3001
WECHAT_MINIAPP_APP_ID=wx77918f137dc7f5a5
WECHAT_MINIAPP_APP_SECRET=895a0f5d6c9360fd744c6246ee6313a6
ALIYUN_ACCESS_KEY_ID=LTAI5tFkYReK6cMtwcioNfGw
ALIYUN_ACCESS_KEY_SECRET=9SGZeB5N2SmEpzBVvQKUc1yvDA2Iwx
ALIYUN_SMS_REGION_ID=cn-hangzhou
ALIYUN_SMS_SIGN_NAME=智了科技
ALIYUN_SMS_TPL_REGISTER=SMS_503430059
ALIYUN_SMS_TPL_LOGIN=SMS_503470064
VERIFY_CODE_SEND_INTERVAL_SECONDS=60
```

### 2.4 game-service and feed-service ports

```bash
# game-service
PORT=3002

# feed-service
PORT=3004
```

### 2.5 ai-engine only

```bash
PORT=8000
LLM_MODE=real
LLM_API_KEY=...
LLM_BASE_URL=https://api.minimaxi.com
LLM_MODEL=MiniMax-M2.5
LLM_FAST_MODEL=MiniMax-M2.5
```

## 3. Deploy steps

Run all commands from `d:\workspace\gamevallies-backend`.

### 3.1 Load environment

PowerShell:

```powershell
Get-Content .env.deploy | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
  $name, $value = $_ -split '=', 2
  [System.Environment]::SetEnvironmentVariable($name, $value.Trim('\"'), 'Process')
}
```

Bash:

```bash
set -a
source .env.deploy
set +a
```

### 3.2 Login to VCR

```bash
echo 'Gamevallies@2026' | docker login gamevallies-repo-cn-shanghai.cr.volces.com -u '6448手机用户#UeaqaB@2112970785' --password-stdin
```

Important:

- The username contains `#`
- Keep quotes around `VOLCENGINE_REGISTRY_USERNAME` in `.env.deploy`
- If image sync later fails inside VeFaaS, re-check the exact same username and password first

### 3.3 Deploy one service

```bash
python scripts/deploy.py user-service
python scripts/deploy.py game-service
python scripts/deploy.py feed-service
python scripts/deploy.py ai-engine
```

Or deploy all supported services:

```bash
python scripts/deploy.py
```

`deploy.py` performs:

1. Docker build
2. Docker push to VCR
3. VeFaaS update or create
4. Wait for image sync
5. Release a new revision

## 4. Post-deploy validation

### 4.1 Unified entry validation

```bash
curl https://www.gamevallies.com/api/v1/health
curl https://www.gamevallies.com/api/v1/games/explore/published?limit=1
curl https://www.gamevallies.com/api/v1/feed/latest?limit=1
curl https://www.gamevallies.com/games/<game-id>/preview
```

Check that:

- `POST /api/v1/auth/wechat/miniapp-login` no longer returns `404`
- `gameUrl`, `previewUrl`, and share URLs use `https://www.gamevallies.com`
- `/games/<game-id>/preview` returns `200 text/html`

### 4.2 Domain verification file

```bash
curl https://www.gamevallies.com/33zqDBay4T.txt
```

Expected body:

```text
5142b16983df09708831078604fbcfeb
```

If this still returns `404`:

- confirm the latest `gv-user-service` revision is released
- confirm the route in `packages/user-service/src/bootstrap.ts` is included in the deployed image
- confirm the public domain is actually routed to the latest `gv-user-service`

## 5. Failure checklist

### 5.1 VeFaaS image sync failed

Check:

- `VOLCENGINE_REGISTRY_USERNAME`
- `VOLCENGINE_REGISTRY_PASSWORD`
- whether the username still includes the `#` suffix
- whether the new image was actually pushed to `:latest`

### 5.2 Mini program web-view still blocked

Check:

- WeChat `web-view` business domain includes `https://www.gamevallies.com`
- frontend build uses `TARO_APP_GAME_CONTENT_URL=https://www.gamevallies.com`
- backend `PUBLIC_API_BASE_URL=https://www.gamevallies.com`

### 5.3 SMS verification code not sent

Check:

- `ALIYUN_ACCESS_KEY_ID`
- `ALIYUN_ACCESS_KEY_SECRET`
- `ALIYUN_SMS_REGION_ID`
- `ALIYUN_SMS_SIGN_NAME`
- `ALIYUN_SMS_TPL_REGISTER`
- `ALIYUN_SMS_TPL_LOGIN`
- `VERIFY_CODE_SEND_INTERVAL_SECONDS`

Do not use the old names:

- `ALIYUN_REGION_ID`
- `ALIYUN_ENDPOINT`
- `ALIYUN_SMS_TEMPLATE_CODE`
