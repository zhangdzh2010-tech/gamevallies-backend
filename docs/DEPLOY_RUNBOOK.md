# GameVallies 全自动部署运维手册

> 本文档供 Claude Code 直接读取执行，包含所有凭证和操作步骤。
> 最后更新: 2026-03-14

---

## 1. 基础设施概览

### 1.1 服务清单

| 服务 | 函数名 | 函数 ID | 端口 | 类型 | 公网访问 |
|------|--------|---------|------|------|----------|
| user-service | gv-user-service | o2lc6jtq | 3001 | NestJS | 否 |
| game-service | gv-game-service | 5gtqkf4z | 3002 | NestJS | 否 |
| feed-service | gv-feed-service | 4w6qkqv7 | 3004 | NestJS | 否 |
| ai-engine | gv-ai-engine | v9b2a2dx | 8000 | Python/FastAPI | 是 (DeepSeek API) |
| frontend | gv-frontend | tsrtwmbw | — | 静态服务 | 是 |

> **social-service 已合并入 feed-service**（`packages/feed-service/src/app.module.ts` 注释：`// Social 模块 (从 social-service 合入)`）。
> 点赞、关注、评论、通知、分享均由 gv-feed-service 承载，`packages/social-service/` 目录保留为历史备份，不部署。

### 1.2 API 网关地址

| 服务 | 公网地址 | 用途 |
|------|----------|------|
| gv-user-service | `https://sd6n8k2up8bgiaakgor40.apigateway-cn-shanghai.volceapi.com` | 用户注册/登录/认证 |
| gv-game-service | `https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com` | 游戏 CRUD / AI 生成 |
| gv-feed-service | `https://sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai.volceapi.com` | 信息流 |
| gv-ai-engine | `https://sd6na7o7g00oknv60o970.apigateway-cn-shanghai-inner.volceapi.com` | AI 引擎 (仅内网) |

> gv-social-service 已下线，原地址 `https://sd6n8k7ng00oknv60n2dg.apigateway-cn-shanghai.volceapi.com` 不再使用，社交接口统一走 gv-feed-service。

### 1.3 网络拓扑

```
用户请求 → API Gateway (公网) → VeFaaS 函数 (VPC 内网)
                                    ├── RDS MySQL (VPC 内网)
                                    ├── Redis (VPC 内网)
                                    └── gv-ai-engine (VPC 内网) → DeepSeek API (公网)
```

---

## 2. 凭证信息

### 2.1 火山引擎

```
VOLCENGINE_ACCESS_KEY   = AKLTZjIyNTY4NDhiMDkwNDg0YzhiYTUzYjNlNmI1ZGVmNjA
VOLCENGINE_SECRET_KEY   = T1RBMU1HWmpZakEzTkRRNU5EUmxORGxoWkdRNE9URmlNV1psT1RneU0yTQ==
VOLCENGINE_REGION       = cn-shanghai
```

### 2.2 VCR 镜像仓库

```
Registry   = gamevallies-repo-cn-shanghai.cr.volces.com
Namespace  = gamevallies
Username   = 6448手机用户#UeaqaB@2112970785
Password   = Gamevallies@2026
```

**注意**: Username 包含 `#` 号，shell source 时必须加双引号，docker login 时必须用单引号包裹。

### 2.3 VPC 网络

```
VPC_ID            = vpc-7uh247krgohs72200skd7y04
SUBNET_ID         = subnet-33guvcwoe43y86k70bqnvis8n
SECURITY_GROUP_ID = sg-7uh24dhusuf472200rliec4v
```

### 2.4 数据库

```
RDS MySQL (内网) = mysql://gamevallies:gamevallies@2026@mysql5f64263dff43.rds.ivolces.com:3306/gamevallies
Redis (内网)     = redis://:gamevallies2026@redis-shzlsq69qwdo5877a.redis.ivolces.com:6379
```

> RDS 仅 VPC 内可达，本地无法直连。如需执行 DDL，通过火山引擎 RDS 控制台的 SQL 窗口。

### 2.5 JWT 密钥

```
JWT_SECRET         = 02e9621b10d223a2aa1bd18b25bb1023802238dcf3de0f55ce3059c4e34290d0
JWT_REFRESH_SECRET = 56154800a4084f1b89ba9459923bdd6bc2ae57f11bb4a0b5c7b6c58a20982f0a
```

### 2.6 DeepSeek LLM

```
LLM_API_KEY  = sk-65a0f82bea6b4018bf46f0f6b7c4a57a
LLM_BASE_URL = https://api.deepseek.com
LLM_MODEL    = deepseek-chat
```

---

## 3. 一键部署流程

### 3.1 部署全部服务 (5 个)

```bash
# 1. 登录 VCR
echo 'Gamevallies@2026' | docker login gamevallies-repo-cn-shanghai.cr.volces.com \
  -u '6448手机用户#UeaqaB@2112970785' --password-stdin

# 2. 加载环境变量 & 部署
set -a && source .env.deploy && set +a && python3 scripts/deploy.py
```

### 3.2 部署单个服务

```bash
echo 'Gamevallies@2026' | docker login gamevallies-repo-cn-shanghai.cr.volces.com \
  -u '6448手机用户#UeaqaB@2112970785' --password-stdin

set -a && source .env.deploy && set +a && python3 scripts/deploy.py <service-name>
```

可选的 `<service-name>`:
- `user-service`
- `game-service`
- `feed-service`
- `ai-engine`
- `frontend`

### 3.3 部署流程内部步骤 (deploy.py 自动执行)

```
1. docker build --platform linux/amd64
   - NestJS 服务: 根目录 Dockerfile, --build-arg SERVICE=xxx PORT=xxx
   - AI 引擎:     packages/ai-engine/Dockerfile, 无 build-arg
2. docker push → VCR 镜像仓库
3. VeFaaS API: 查找函数 (存在则 UpdateFunction, 不存在则 CreateFunction)
4. 等待镜像缓存就绪 (轮询 GetImageSyncStatus, 最多 5 分钟)
5. Release 发布新版本
```

---

## 4. 运维操作

### 4.1 查看函数日志

```python
# 通过 VeFaaS SDK 获取实例日志
import volcenginesdkvefaas, volcenginesdkcore

cfg = volcenginesdkcore.Configuration()
cfg.ak = "AKLTZjIyNTY4NDhiMDkwNDg0YzhiYTUzYjNlNmI1ZGVmNjA"
cfg.sk = "T1RBMU1HWmpZakEzTkRRNU5EUmxORGxoWkdRNE9URmlNV1psT1RneU0yTQ=="
cfg.region = "cn-shanghai"
api = volcenginesdkvefaas.VEFAASApi(volcenginesdkcore.ApiClient(cfg))

# 列出运行实例
instances = api.list_function_instances(
    volcenginesdkvefaas.ListFunctionInstancesRequest(function_id="<function_id>")
)
for inst in (instances.items or []):
    print(inst.id, inst.status)

# 获取实例日志
logs = api.get_function_instance_logs(
    volcenginesdkvefaas.GetFunctionInstanceLogsRequest(
        function_id="<function_id>", instance_id="<instance_id>"
    )
)
for line in (logs.logs or []):
    print(line.content)
```

### 4.2 数据库 DDL 操作

RDS 内网地址无法从本地访问，DDL 操作需要通过:
- **火山引擎控制台** → 云数据库 RDS → 实例详情 → SQL 窗口
- 或先在本地生成 SQL (`npx prisma migrate diff ...`)，再到控制台执行

### 4.3 Prisma Schema 变更流程

```bash
# 1. 本地修改 prisma/schema.prisma
# 2. 生成迁移 SQL
npx prisma migrate diff --from-schema-datamodel prisma/schema.prisma.bak \
  --to-schema-datamodel prisma/schema.prisma --script > migration.sql
# 3. 到 RDS 控制台 SQL 窗口执行 migration.sql
# 4. 重新部署受影响的服务
```

---

## 5. 健康检查 & 接口测试

### 5.1 快速健康检查

```bash
# 各服务健康状态
for host in \
  sd6n8k2up8bgiaakgor40.apigateway-cn-shanghai.volceapi.com \
  sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com \
  sd6n8kcmp8bgiaakgorig.apigateway-cn-shanghai.volceapi.com \
  sd6n8k7ng00oknv60n2dg.apigateway-cn-shanghai.volceapi.com; do
  echo -n "$host → "
  curl -s -o /dev/null -w "%{http_code}" "https://$host/api/v1/health" --max-time 10
  echo
done
```

### 5.2 AI 游戏生成端到端测试

```bash
# 1. 登录
TOKEN=$(curl -s -X POST \
  https://sd6n8k2up8bgiaakgor40.apigateway-cn-shanghai.volceapi.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"account":"aitest01","password":"Test123456"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")

# 2. 提交游戏生成
GAME_ID=$(curl -s -X POST \
  https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com/api/v1/games/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"description":"一个简单的躲避陨石的太空游戏"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['gameId'])")
echo "gameId: $GAME_ID"

# 3. 轮询结果 (通常 10-15 秒)
sleep 15
curl -s "https://sd6n8j9fmqc3q4mg90pr0.apigateway-cn-shanghai.volceapi.com/api/v1/games/$GAME_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

### 5.3 测试账号

```
username: aitest01
email:    aitest01@test.com
password: Test123456
```

---

## 6. 已知问题 & 注意事项

### 6.1 VCR 用户名 `#` 号问题

`.env.deploy` 中 `VOLCENGINE_REGISTRY_USERNAME` 必须加双引号:
```
VOLCENGINE_REGISTRY_USERNAME="6448手机用户#UeaqaB@2112970785"
```
否则 shell `source` 会将 `#` 后内容当作注释截断，导致 VeFaaS 镜像拉取鉴权失败。

### 6.2 VeFaaS native/v1 启动脚本

VeFaaS 的 `native/v1` 运行时会强制执行 `/opt/application/run.sh`，忽略 Dockerfile 的 CMD。
- NestJS 服务: 根 Dockerfile 已包含此脚本
- AI 引擎: `packages/ai-engine/Dockerfile` 已包含此脚本
- 同时在 VeFaaS `command` 字段也设置了启动命令作为双保险

### 6.3 CORS_ORIGINS 类型

AI 引擎的 `CORS_ORIGINS` 在 Pydantic Settings 中定义为 `list[str]`，env var 必须传 JSON 格式:
```
CORS_ORIGINS=["*"]     # 正确
CORS_ORIGINS=*         # 错误, Pydantic 解析失败
```
`deploy.py` 中已硬编码为 `'["*"]'`。

### 6.4 API Gateway `!` 字符问题

API 网关会损坏 JSON body 中的 `!` 字符（触发 "Bad escaped character" 错误）。
测试数据中避免使用 `!`，生产环境需在网关侧开启 raw body 透传。

### 6.5 SocialInteraction 多态 FK

`SocialInteraction.targetId` 是多态字段 (可以是 gameId 或 commentId)，
已移除对 Game 表的 FK 约束。DDL 已在 RDS 执行:
```sql
ALTER TABLE social_interactions DROP FOREIGN KEY social_interactions_target_id_fkey;
```

### 6.6 镜像缓存就绪等待

VeFaaS 更新/创建函数后需要等待镜像缓存就绪才能 Release。
`deploy.py` 已内置轮询逻辑 (每 10 秒检查一次, 最多 5 分钟)。
如果镜像同步状态为 `Failed`，检查 VCR 凭证是否正确。

---

## 7. 服务架构图

```
                     ┌──────────────────────────────────────────────────┐
                     │                API Gateway (公网)                  │
                     │  user / game / feed / frontend  │   ai-engine    │
                     │           (公网 URL)             │   (仅内网)      │
                     └──────┬──────┬──────┬─────────────┴──────┬────────┘
                            │      │      │                    │
                     ┌──────▼──────▼──────▼────────────────────▼────────┐
                     │              VeFaaS 函数 (VPC 内网)                │
                     │  ┌────────┐┌────────┐┌──────────────────────┐    │
                     │  │  user  ││  game  ││       feed           │    │
                     │  │ :3001  ││ :3002  ││ :3004                │    │
                     │  └────────┘└───┬────┘│ (含 social 模块:      │    │
                     │                │     │  like/follow/comment │    │
                     │                │     │  notification/share)  │    │
                     │                │     └──────────────────────┘    │
                     │                │ AI_ENGINE_URL (内网)              │
                     │           ┌────▼──────┐                          │
                     │           │ ai-engine │── DeepSeek API (公网)     │
                     │           │  :8000    │   api.deepseek.com       │
                     │           └───────────┘                          │
                     │                 │              │                  │
                     │        ┌────────▼──┐   ┌───────▼──┐              │
                     │        │ RDS MySQL │   │  Redis   │              │
                     │        │  (内网)    │   │  (内网)   │              │
                     │        └───────────┘   └──────────┘              │
                     └──────────────────────────────────────────────────┘
```

---

## 8. 关键文件索引

| 文件 | 用途 |
|------|------|
| `scripts/deploy.py` | 一键部署脚本 (build → push → update/create → release) |
| `.env.deploy` | 部署凭证和配置 (含密码, gitignore) |
| `.env.deploy.example` | 凭证模板 (不含真实密码) |
| `Dockerfile` | NestJS 4 服务的统一 Dockerfile |
| `packages/ai-engine/Dockerfile` | AI 引擎独立 Dockerfile |
| `prisma/schema.prisma` | 数据库模型定义 |
| `.github/workflows/deploy.yml` | GitHub Actions CI/CD |
| `docs/config-file.json` | API 网关域名映射 |
