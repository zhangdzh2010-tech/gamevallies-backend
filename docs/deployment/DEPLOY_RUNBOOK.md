# GameVallies Manual Deploy Runbook

Last updated: 2026-03-20

This runbook describes the current manual deployment flow. Do not use GitHub
Actions for this procedure.

The production public domain is:

`https://gamevallies.com`

Historical cutover notes for the `www` to apex migration remain in
`docs/deployment/ENTRY_DOMAIN_CUTOVER.md`.

## 1. Preconditions

Before you deploy, make sure these files are up to date:

- `.env`
- `.env.example`
- `.env.deploy`
- `.env.deploy.example`
- `docs/deployment/ENV_SYNC_GUIDE.md`
- `docs/deployment/ENTRY_DOMAIN_CUTOVER.md` when the rollout window includes
  an entry-domain change

The authoritative values for the current production rollout are:

- `PUBLIC_API_BASE_URL=https://gamevallies.com`
- `USER_SERVICE_URL=https://gamevallies.com`
- `GAME_SERVICE_URL=https://gamevallies.com`
- `FEED_SERVICE_URL=https://gamevallies.com`
- `AI_ENGINE_URL_CN_SHANGHAI=https://sd6vrn9api80atrf10evg.apigateway-cn-shanghai-inner.volceapi.com`
- `GAME_SERVICE_UPSTREAM_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai-inner.volceapi.com`
- `FEED_SERVICE_UPSTREAM_URL=https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai-inner.volceapi.com`

Legacy note:

- `AI_ENGINE_URL` is now a compatibility fallback only.
- The current China production path is `gv-ai-engine-cn`.
- `gv-ai-engine` should be treated as a rollback-only legacy instance until final retirement.

## 2. Required variables

Deployment uses:

- environment file: `.env.deploy`
- template files: `.env.deploy.example` and `.env.example`
- deploy script: `python scripts/deploy.py <service-name>`

How to obtain values:

- Copy the latest team-approved production values into `.env.deploy`
- Use `.env.deploy.example` only as the field checklist, not as the source of real values
- If a field belongs to cloud infrastructure or a third-party platform, fetch it from that platform's console and sync it back into `.env.deploy`

### 2.1 Deployment infrastructure

Source:

- `VOLCENGINE_ACCESS_KEY`, `VOLCENGINE_SECRET_KEY`: Volcengine IAM / API credentials
- `VOLCENGINE_REGION`: fixed team deployment setting, copied from the current production `.env.deploy`
- `VOLCENGINE_API_HOST`, `VOLCENGINE_TOS_BUCKET`: kept in `.env.deploy` / templates for infra bookkeeping; the current `scripts/deploy.py` does not read them directly
- `VOLCENGINE_VPC_ID`, `VOLCENGINE_SUBNET_ID`, `VOLCENGINE_SECURITY_GROUP_ID`: Volcengine VPC / subnet / security group console
- `VOLCENGINE_REGISTRY`, `VOLCENGINE_REGISTRY_NAMESPACE`, `VOLCENGINE_REGISTRY_USERNAME`, `VOLCENGINE_REGISTRY_PASSWORD`: Volcengine VCR console
- `IMAGE_TAG`: set per release by the deployer; use a unique value such as timestamp or git commit hash

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
VOLCENGINE_REGISTRY_USERNAME="6448手机用户#UeaqaB@21129707855"
VOLCENGINE_REGISTRY_PASSWORD="Gamevallies@2026"
VOLCENGINE_TOS_BUCKET=gamevallies-deploy
IMAGE_TAG=<unique-tag>

```
### VOLCENGINE_REGISTRY_JOHOR
VOLCENGINE_REGISTRY=gv-respo-johor-ap-southeast-1.cr.volces.com
VOLCENGINE_REGISTRY_NAMESPACE=gamevallies
VOLCENGINE_REGISTRY_USERNAME="6448手机用户#UeaqaB@21129707855"
VOLCENGINE_REGISTRY_PASSWORD="Gamevallies@2026"


Source:

- `NODE_ENV`: fixed as `production`
- `CORS_ORIGIN`, `ADMIN_TOKEN`: current production `.env.deploy`
- `DATABASE_URL`: production MySQL / RDS instance
- `REDIS_URL`: production Redis instance
- `JWT_SECRET`, `JWT_REFRESH_SECRET`: current production auth secrets from `.env.deploy`
- `PUBLIC_API_BASE_URL`, `USER_SERVICE_URL`, `GAME_SERVICE_URL`, `FEED_SERVICE_URL`: current public domain routing values
- `AI_ENGINE_URL_CN_SHANGHAI`, `AI_ENGINE_URL_AP_SOUTHEAST_JOHOR`, `GAME_SERVICE_UPSTREAM_URL`, `FEED_SERVICE_UPSTREAM_URL`: internal APIG upstream addresses from the current production `.env.deploy` and Volcengine APIG console
- `AI_ENGINE_URL`: compatibility fallback only; do not use as the primary China routing value for new deployments

```bash
NODE_ENV=production
CORS_ORIGIN=*
ADMIN_TOKEN=admin123
DATABASE_URL=mysql://gamevallies:gamevallies@2026@mysql5f64263dff43.rds.ivolces.com:3306/gamevallies
REDIS_URL=redis://:gamevallies2026@redis-shzlsq69qwdo5877a.redis.ivolces.com:6379
JWT_SECRET=02e9621b10d223a2aa1bd18b25bb1023802238dcf3de0f55ce3059c4e34290d0
JWT_REFRESH_SECRET=56154800a4084f1b89ba9459923bdd6bc2ae57f11bb4a0b5c7b6c58a20982f0a
PUBLIC_API_BASE_URL=https://gamevallies.com
USER_SERVICE_URL=https://gamevallies.com
GAME_SERVICE_URL=https://gamevallies.com
FEED_SERVICE_URL=https://gamevallies.com
AI_ENGINE_URL=https://sd6vrn9api80atrf10evg.apigateway-cn-shanghai-inner.volceapi.com
AI_ENGINE_URL_CN_SHANGHAI=https://sd6vrn9api80atrf10evg.apigateway-cn-shanghai-inner.volceapi.com
AI_ENGINE_URL_AP_SOUTHEAST_JOHOR=
AI_ENGINE_DEFAULT_REGION=cn_shanghai
GAME_SERVICE_UPSTREAM_URL=https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai-inner.volceapi.com
FEED_SERVICE_UPSTREAM_URL=https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai-inner.volceapi.com
```

### 2.3 user-service only

Source:

- `PORT`: fixed by service deployment convention
- `WECHAT_MINIAPP_APP_ID`, `WECHAT_MINIAPP_APP_SECRET`: WeChat Mini Program admin console
- `ALIYUN_ACCESS_KEY_ID`, `ALIYUN_ACCESS_KEY_SECRET`: Aliyun RAM / AccessKey console
- `ALIYUN_SMS_REGION_ID`, `ALIYUN_SMS_SIGN_NAME`, `ALIYUN_SMS_TPL_REGISTER`, `ALIYUN_SMS_TPL_LOGIN`: Aliyun SMS console
- `VERIFY_CODE_SEND_INTERVAL_SECONDS`: current production `.env.deploy`
- `VEFAAS_USER_SERVICE_MIN_INSTANCE`, `VEFAAS_USER_SERVICE_MAX_INSTANCE`, `VEFAAS_USER_SERVICE_RESERVED_FROZEN_INSTANCE`: optional VeFaaS function resource limits consumed by `scripts/deploy.py`

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
VEFAAS_USER_SERVICE_MIN_INSTANCE=0
VEFAAS_USER_SERVICE_MAX_INSTANCE=20
# VEFAAS_USER_SERVICE_RESERVED_FROZEN_INSTANCE=1
```

### 2.4 game-service and feed-service ports

Source:

- `PORT`: fixed by service deployment convention in `scripts/deploy.py`

```bash
# game-service
PORT=3002

# feed-service
PORT=3004
```

### 2.5 ai-engine only

Source:

- `PORT`: fixed by service deployment convention
- `LLM_MODE`: current production `.env.deploy`
- `LLM_API_KEY`: current LLM provider key
- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_FAST_MODEL`: active LLM provider configuration from `.env.deploy`
- `PIPELINE_TIMEOUT_S`: current ai-engine pipeline timeout from `.env.deploy`

```bash
PORT=8000
LLM_MODE=real
LLM_API_KEY=...
LLM_BASE_URL=https://api.minimaxi.com/v1
LLM_MODEL=MiniMax-M2.5
LLM_FAST_MODEL=MiniMax-M2.5
PIPELINE_TIMEOUT_S=600
```

## 3. Deploy steps

Run all commands from the repository root.

### 3.1 Load environment

This step loads deployment values from `.env.deploy` into the current shell.

Current note:

- `scripts/deploy.py` now self-configures UTF-8 stdio on Windows and switches the
  console code page to `65001`.
- Do not prepend manual `PYTHONIOENCODING=utf-8` or `chcp 65001` when you are
  already using the current `scripts/deploy.py`; keep those only as fallback for
  older revisions of the script.

PowerShell:

```powershell
Get-Content -Encoding utf8 .env.deploy | ForEach-Object {
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

PowerShell:

```powershell
$env:VOLCENGINE_REGISTRY_PASSWORD | docker login $env:VOLCENGINE_REGISTRY -u $env:VOLCENGINE_REGISTRY_USERNAME --password-stdin
```

Bash:

```bash
echo "$VOLCENGINE_REGISTRY_PASSWORD" | docker login "$VOLCENGINE_REGISTRY" -u "$VOLCENGINE_REGISTRY_USERNAME" --password-stdin
```

Important:

- The username contains `#`
- Keep quotes around `VOLCENGINE_REGISTRY_USERNAME` in `.env.deploy`
- Use dedicated VCR credentials here; do not substitute Volcengine AK/SK for
  VeFaaS image pull credentials
- Do not reuse `latest`; use a unique version tag
- `scripts/deploy.py` now auto-increments image tags to `vN`
  when `IMAGE_TAG` is omitted or set to `latest`
- Docker `push` success does not prove VeFaaS can pull a fresh tag
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

`scripts/deploy.py` is the only supported production deployment entrypoint. Do
not deploy by mixing manual console edits with ad hoc local commands unless the
runbook explicitly tells you to.

If you deploy multiple services in one command, pass them explicitly:

```bash
python scripts/deploy.py game-service feed-service ai-engine-cn
```

The script now supports multiple service targets in one invocation and dedupes
expanded targets such as `ai-engine`.

## 4. Post-deploy validation

### 4.1 Unified entry validation

```bash
curl https://gamevallies.com/api/v1/health
curl https://gamevallies.com/api/v1/games/explore/published?limit=1
curl https://gamevallies.com/api/v1/feed/latest?limit=1
curl https://gamevallies.com/games/<game-id>/preview
```

Check that:

- `POST /api/v1/auth/wechat/miniapp-login` no longer returns `404`
- `gameUrl`, `previewUrl`, and share URLs use `https://gamevallies.com`
- `/games/<game-id>/preview` returns `200 text/html`

### 4.2 Domain verification file

```bash
curl https://gamevallies.com/33zqDBay4T.txt
```

Expected body:

```text
5142b16983df09708831078604fbcfeb
```

If this still returns `404`:

- confirm the latest `gv-user-service` revision is released
- confirm the route in `packages/user-service/src/bootstrap.ts` is included in the deployed image
- confirm the public domain is actually routed to the latest `gv-user-service`

### 4.3 V2 generation smoke test

When manually smoke-testing the v2 generation flow after deployment:

- use a real create or iterate request against `https://gamevallies.com`
- use `timeoutS=1200` for manual smoke tests
- do not use `timeoutS=600` as the primary validation budget for v2

Reason:

- real production LLM latency plus QA remediation can exceed `600s`
- a `600s` failure can be a false negative caused by budget exhaustion rather
  than a broken deployment
- if the task still fails at `1200s`, treat the remaining error as a real
  pipeline or prompt issue, not just a timeout budget issue

Additional interpretation notes:

- if VeFaaS `release` succeeds but the final step `POST /api/v1/admin/cloud/ai-engine-region-targets/sync-deploy`
  returns `500`, treat the function release as successful and the deploy-state
  backfill as a separate non-blocking follow-up
- if user-facing task status appears stuck on an early stage, check admin task
  events before assuming the pipeline is frozen; current progress events can
  arrive out of order, so `logic_generate` may already have started even if the
  summary still shows `runtime_profile_select`

## 5. Failure checklist

### 5.1 VeFaaS image sync failed

Check:

- `VOLCENGINE_REGISTRY_USERNAME`
- `VOLCENGINE_REGISTRY_PASSWORD`
- whether the username still includes the `#` suffix
- whether the new image was actually pushed with the intended unique tag
- whether `scripts/deploy.py` passed VCR credentials, not AK/SK, as
  `source_access_config`

Operational notes from 2026-03-24:

- VeFaaS fresh sync can fail even when `docker push` already succeeded locally
- an old cached tag may still sync successfully while a new tag fails; that
  points to a VeFaaS fresh-pull credential/path issue, not necessarily an image
  build issue
- if the error contains `invalid username/password` or `user ... not exist`,
  treat it as a registry pull-credential problem first
- verify the exact same VCR username/password in `.env.deploy`, local Docker
  login, and CI secrets
- if old cached tags succeed but fresh tags fail with the same credentials,
  escalate to the cloud-side VeFaaS/VCR pull path instead of blaming the image
  contents immediately
- the current deployment AK/SK may not have `cr:GetAuthorizationToken`; if so,
  the temporary-token workaround is unavailable until IAM grants that action

### 5.2 Windows deploy output is garbled

Check:

- you are using the latest `scripts/deploy.py`
- the script starts before any wrapper resets `PYTHONIOENCODING`
- you are not re-running an older cached copy of the deploy script from another
  working directory

Operational note:

- the current deploy script already forces UTF-8 stdio and Windows console code
  page `65001`
- if output is still garbled, verify that the script revision on disk includes
  the UTF-8 bootstrap rather than reapplying shell-level workarounds by habit

### 5.3 Mini program web-view still blocked

Check:

- WeChat `web-view` business domain includes `https://gamevallies.com`
- frontend build uses `TARO_APP_GAME_CONTENT_URL=https://gamevallies.com`
- backend `PUBLIC_API_BASE_URL=https://gamevallies.com`
- if the domain was just configured or updated in WeChat, allow time for the business-domain change to propagate before changing backend routes
- if the warning disappears later without any code change, treat the WeChat business-domain propagation delay as the primary cause, not the UUID path format

### 5.4 SMS verification code not sent

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

### 5.5 VeFaaS reached max replica limit

If deployment or runtime returns:

```text
error_code: reached_max_replica_limit
error_message: Function has reached its max replica limit.
```

Check:

- the function resource in VeFaaS; `max_instance` is the hard ceiling
- whether the current instance count is already close to or equal to that ceiling
- whether `.env.deploy` sets `VEFAAS_USER_SERVICE_MAX_INSTANCE` high enough for `gv-user-service`

Recommended fix:

- increase `VEFAAS_USER_SERVICE_MAX_INSTANCE` in `.env.deploy`
- optionally set `VEFAAS_USER_SERVICE_RESERVED_FROZEN_INSTANCE` for burst traffic or cold-start control
- redeploy with `python scripts/deploy.py user-service`
