# PlayForge 部署标准运维手册 (SOP)

## 目录
1. [开发环境搭建](#一开发环境搭建)
2. [Docker全栈部署](#二docker全栈部署)
3. [Kubernetes生产部署](#三kubernetes生产部署)
4. [CI/CD流水线说明](#四cicd流水线说明)
5. [发布流程标准](#五发布流程标准)
6. [回滚策略](#六回滚策略)
7. [数据库迁移SOP](#七数据库迁移sop)
8. [监控与告警](#八监控与告警)
9. [常见问题排查](#九常见问题排查)

---

## 一、开发环境搭建

### 系统要求

| 组件 | 最低版本 | 推荐版本 | 备注 |
|------|---------|---------|------|
| Node.js | 18.0.0 | 20.10.0+ | 用于后端服务 |
| Python | 3.10.0 | 3.12.0+ | 用于AI引擎 |
| Docker | 20.10.0 | 24.0.0+ | 容器化部署 |
| Docker Compose | 2.0.0 | 2.20.0+ | 编排工具 |
| kubectl (K8s部署) | 1.26.0 | 1.28.0+ | Kubernetes客户端 |
| Helm (K8s部署) | 3.12.0 | 3.13.0+ | Kubernetes包管理 |
| Git | 2.35.0 | 2.40.0+ | 版本控制 |

### 步骤1: 克隆仓库

```bash
# 克隆仓库
git clone https://github.com/willgame/playforge-backend.git
cd playforge-backend

# 检查分支（开发使用develop分支）
git branch -a
git checkout develop
```

### 步骤2: 安装依赖

```bash
# 在项目根目录安装Node.js依赖
npm install

# 为AI引擎安装Python依赖
cd packages/ai-engine
pip install -r requirements.txt
cd ../..

# 为每个微服务验证依赖
npm --prefix packages/user-service install
npm --prefix packages/game-service install
npm --prefix packages/social-service install
npm --prefix packages/feed-service install
```

### 步骤3: 启动Docker数据库

```bash
# 启动PostgreSQL、MongoDB、Redis容器
docker compose up -d postgres mongo redis

# 验证容器状态
docker ps | grep -E "(postgres|mongo|redis)"

# 预期输出：三个容器应该处于Up状态，且不含Unhealthy
```

**端口映射说明：**
- PostgreSQL: `localhost:5432`
- MongoDB: `localhost:27017`
- Redis: `localhost:6379`

### 步骤4: 数据库迁移

```bash
# 生成Prisma客户端
npx prisma generate

# 执行待迁移的数据库Schema
npx prisma migrate dev --name initial

# 如果是从scratch，该命令会创建所有表结构
# 输入migration名称（如："init"）

# 验证数据库连接
npx prisma db execute --stdin < /dev/null
```

**常见问题：**
- 如果提示数据库已存在，使用 `npx prisma migrate reset` 清空并重新创建
- Windows用户需要确保PostgreSQL密码为 `postgres`（参考docker-compose.yml）

### 步骤5: 种子数据

```bash
# 创建初始测试数据（用户、游戏、社交关系等）
npx ts-node prisma/seed.ts

# 检查种子数据是否成功创建
npx prisma studio

# 此命令会打开Web界面 (http://localhost:5555) 查看数据库内容
```

**种子数据包含：**
- 5个测试用户（包含不同权限级别）
- 10个示例游戏（包括6个JSX demo游戏）
- 社交互动数据（关注、点赞）
- 评论和通知样本
- 创作者收益数据

### 步骤6: 启动各服务

**方式A: 分别启动（适合调试）**

```bash
# 终端1: 启动user-service
npm --prefix packages/user-service run dev

# 终端2: 启动game-service
npm --prefix packages/game-service run dev

# 终端3: 启动social-service
npm --prefix packages/social-service run dev

# 终端4: 启动feed-service
npm --prefix packages/feed-service run dev

# 终端5: 启动ai-engine (Python)
cd packages/ai-engine
python -m uvicorn main:app --reload --port 3005
```

**方式B: 并行启动（推荐开发）**

```bash
# 在项目根目录安装concurrently（如尚未安装）
npm install --save-dev concurrently

# 使用concurrently启动所有服务
npm run dev:all

# 查看package.json中的dev:all脚本配置
```

**服务启动顺序和依赖：**
1. PostgreSQL、MongoDB、Redis（必须先启动）
2. user-service（基础服务，其他服务可能依赖）
3. game-service、social-service、feed-service（可并行）
4. ai-engine（可选，游戏AI功能需要）

### 步骤7: 验证服务健康

```bash
# 验证各服务健康端点
curl http://localhost:3000/health      # user-service
curl http://localhost:3001/health      # game-service
curl http://localhost:3002/health      # social-service
curl http://localhost:3003/health      # feed-service
curl http://localhost:3005/health      # ai-engine

# 预期返回：
# {"status":"ok","timestamp":"2024-01-15T10:30:00Z","version":"1.0.0"}

# 验证数据库连接
curl http://localhost:3000/api/users   # 应返回用户列表

# 验证WebSocket连接（使用websocat或wscat）
npm install -g wscat
wscat -c ws://localhost:3000/ws

# 输入任意文本，应收到连接确认响应
```

**调试提示：**
- 使用 `npm run logs` 查看所有服务的实时日志
- 使用 `npm run logs -- --tail=100` 查看最后100行日志
- 对单个服务调试，使用 `DEBUG=* npm --prefix packages/user-service run dev`

---

## 二、Docker全栈部署

### 步骤1: 构建所有Docker镜像

```bash
# 方式A: 使用docker-compose自动构建
docker compose build

# 方式B: 单独构建特定服务
docker build -f packages/user-service/Dockerfile -t playforge-user-service:latest packages/user-service
docker build -f packages/game-service/Dockerfile -t playforge-game-service:latest packages/game-service
docker build -f packages/social-service/Dockerfile -t playforge-social-service:latest packages/social-service
docker build -f packages/feed-service/Dockerfile -t playforge-feed-service:latest packages/feed-service
docker build -f packages/ai-engine/Dockerfile -t playforge-ai-engine:latest packages/ai-engine

# 方式C: 使用buildx构建多平台镜像（用于发布）
docker buildx build --platform linux/amd64,linux/arm64 -t playforge-user-service:latest --push packages/user-service
```

**镜像标签规范：**
- 开发版: `playforge-user-service:latest`
- 版本发布: `playforge-user-service:v1.2.3`
- 分支版本: `playforge-user-service:main-abc1234`（commit hash）

### 步骤2: 启动容器

```bash
# 启动所有容器（包括数据库）
docker compose up -d

# 查看启动日志
docker compose logs -f

# 按服务查看日志
docker compose logs -f user-service
docker compose logs -f game-service

# 预期看到所有容器Running状态
docker compose ps
```

### 步骤3: 验证所有容器运行状态

```bash
# 列出所有容器及状态
docker compose ps

# 预期输出示例：
# NAME                COMMAND                STATUS              PORTS
# postgres            docker-entrypoint.sh...  Up 2 minutes        5432/tcp
# mongo               docker-entrypoint.sh...  Up 2 minutes        27017/tcp
# redis               docker-entrypoint.sh...  Up 2 minutes        6379/tcp
# user-service       npm run start           Up 1 minute          3000/tcp
# game-service       npm run start           Up 1 minute          3001/tcp
# ...

# 检查容器资源占用
docker compose stats

# 验证网络连接（Docker内部）
docker compose exec user-service curl http://postgres:5432 2>/dev/null && echo "DB连接OK" || echo "DB连接失败"
```

### 步骤4: 初始化数据库和种子数据

```bash
# 进入user-service容器
docker compose exec user-service bash

# 在容器内执行迁移
npx prisma migrate deploy

# 创建种子数据
npx ts-node prisma/seed.ts

# 验证数据
npx prisma studio

# 退出容器
exit
```

**如果需要从头初始化：**
```bash
# 删除所有容器和卷（危险操作）
docker compose down -v

# 重新启动
docker compose up -d

# 重新初始化
docker compose exec user-service npx prisma migrate deploy
docker compose exec user-service npx ts-node prisma/seed.ts
```

### 步骤5: 验证API端点

```bash
# 等待服务完全启动（约10秒）
sleep 10

# 验证各服务
curl -s http://localhost:3000/health | jq .
curl -s http://localhost:3001/health | jq .
curl -s http://localhost:3002/health | jq .
curl -s http://localhost:3003/health | jq .
curl -s http://localhost:3005/health | jq .

# 测试API端点
curl -s http://localhost:3000/api/users | jq . | head -20
curl -s http://localhost:3001/api/games | jq . | head -20

# 测试游戏搜索
curl -s "http://localhost:3001/api/games?search=星际" | jq .

# 获取用户信息
curl -s "http://localhost:3000/api/users?page=1&limit=10" | jq .
```

**性能测试（可选）：**
```bash
# 安装ab（Apache Bench）
sudo apt-get install apache2-utils

# 对health端点进行负载测试
ab -n 1000 -c 10 http://localhost:3000/health

# 预期响应时间 < 100ms
```

---

## 三、Kubernetes生产部署

### 前提条件检查

```bash
# 检查kubectl是否安装并可用
kubectl version --client

# 检查Kubernetes集群连接
kubectl cluster-info
kubectl get nodes

# 检查Helm是否安装
helm version

# 切换到正确的kubeconfig上下文（如果有多个集群）
kubectl config current-context
kubectl config use-context production-cluster

# 验证有足够权限
kubectl auth can-i create namespaces --as=system:serviceaccount:default:default
```

### 步骤1: 创建Namespace

```bash
# 应用namespace配置
kubectl apply -f infrastructure/k8s/namespace.yaml

# 验证namespace创建
kubectl get namespace playforge

# 设置默认namespace（可选，方便后续命令）
kubectl config set-context --current --namespace=playforge
```

### 步骤2: 部署数据层（仅限开发/预发布环境）

**注意：生产环境应使用云厂商托管数据库（如AWS RDS、Azure Database）**

```bash
# 部署Redis
kubectl apply -f infrastructure/k8s/redis.yaml
kubectl get deployment -n playforge redis
kubectl get svc -n playforge redis

# 验证Redis可连接
kubectl run -it --image=redis:7 redis-test -n playforge -- redis-cli -h redis ping
# 预期输出: PONG

# 部署PostgreSQL StatefulSet
kubectl apply -f infrastructure/k8s/postgres.yaml
kubectl get statefulset -n playforge postgres
kubectl get pvc -n playforge postgres-data

# 初始化PostgreSQL（等待Pod就绪）
kubectl wait --for=condition=ready pod -l app=postgres -n playforge --timeout=300s
kubectl exec -it postgres-0 -n playforge -- psql -U postgres -c "CREATE DATABASE playforge;"

# 部署MongoDB（可选）
kubectl apply -f infrastructure/k8s/mongo.yaml
kubectl get deployment -n playforge mongo
```

### 步骤3: 创建ConfigMap和Secret

```bash
# 应用ConfigMap（非敏感配置）
kubectl apply -f infrastructure/k8s/configmap.yaml

# 验证ConfigMap
kubectl get configmap -n playforge
kubectl describe cm playforge-config -n playforge

# 创建Secret（敏感数据，需要先编码）
# 重要：先修改secret.yaml中的base64值为实际值

# 生成base64编码值
echo -n "your-database-url" | base64
echo -n "your-jwt-secret" | base64
echo -n "your-llm-api-key" | base64

# 编辑secret文件并替换占位符
# 然后应用
kubectl apply -f infrastructure/k8s/secret.yaml

# 验证Secret
kubectl get secret -n playforge
kubectl describe secret playforge-secret -n playforge

# 警告：不要将实际Secret值提交到Git
# 使用kubectl直接创建Secret，或使用Sealed Secrets/External Secrets
```

### 步骤4: 部署业务服务（5个Deployment）

```bash
# 部署user-service
kubectl apply -f infrastructure/k8s/user-service.yaml
kubectl get deployment -n playforge user-service
kubectl get pods -n playforge -l app=user-service

# 等待Pod就绪
kubectl wait --for=condition=ready pod -l app=user-service -n playforge --timeout=300s

# 查看Pod日志
kubectl logs -n playforge -l app=user-service --tail=50

# 类似地部署其他服务
kubectl apply -f infrastructure/k8s/game-service.yaml
kubectl apply -f infrastructure/k8s/social-service.yaml
kubectl apply -f infrastructure/k8s/feed-service.yaml
kubectl apply -f infrastructure/k8s/ai-engine.yaml

# 验证所有服务
kubectl get deployments -n playforge
kubectl get pods -n playforge
kubectl get services -n playforge

# 预期输出：所有Pod状态为Running
```

**服务就绪检查：**
```bash
# 检查specific pod的日志
kubectl logs -n playforge <pod-name>

# 进入容器进行调试
kubectl exec -it <pod-name> -n playforge -- /bin/bash

# 查看pod事件（诊断启动问题）
kubectl describe pod <pod-name> -n playforge

# 检查资源是否充足
kubectl top nodes
kubectl top pods -n playforge
```

### 步骤5: 配置Ingress

```bash
# 前提：集群已安装Ingress Controller（nginx-ingress或类似）
# 检查Ingress Controller状态
kubectl get pods -n ingress-nginx

# 应用Ingress配置
kubectl apply -f infrastructure/k8s/ingress.yaml

# 验证Ingress
kubectl get ingress -n playforge
kubectl describe ingress playforge-ingress -n playforge

# 获取Ingress外部IP地址
kubectl get ingress -n playforge -o wide

# 配置DNS解析（或更新/etc/hosts）
# playforge-api.example.com -> <INGRESS_EXTERNAL_IP>

# 验证HTTPS（如已配置TLS）
curl -k https://playforge-api.example.com/health

# 验证WebSocket路由
# 访问 https://playforge-api.example.com/ws
```

**如果使用自签名证书：**
```bash
# 创建自签名证书
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# 创建TLS Secret
kubectl create secret tls playforge-tls -n playforge --cert=cert.pem --key=key.pem

# 在ingress.yaml中引用该Secret
```

### 步骤6: 验证部署

```bash
# 综合状态检查脚本
#!/bin/bash
echo "=== Namespace ==="
kubectl get namespace playforge

echo "=== ConfigMap & Secret ==="
kubectl get cm,secret -n playforge

echo "=== Deployments ==="
kubectl get deployments -n playforge -o wide

echo "=== Pods ==="
kubectl get pods -n playforge -o wide

echo "=== Services ==="
kubectl get svc -n playforge

echo "=== Ingress ==="
kubectl get ingress -n playforge

echo "=== PersistentVolumes ==="
kubectl get pvc -n playforge

# 测试服务连接
echo "=== Health Check ==="
kubectl port-forward svc/user-service 3000:3000 -n playforge &
sleep 2
curl -s http://localhost:3000/health | jq .
kill %1
```

### 步骤7: 配置HPA自动扩缩

```bash
# 前提：集群已安装Metrics Server
kubectl get deployment metrics-server -n kube-system

# 应用HPA配置（已包含在deployment yaml中）
# HPA会自动扩缩Pod数量，基于CPU使用率

# 查看HPA状态
kubectl get hpa -n playforge

# 模拟负载测试HPA
kubectl run -it --image=loadimpact/k6 k6-test -n playforge -- run /scripts/load-test.js

# 实时查看Pod数量变化
watch kubectl get pods -n playforge

# 查看HPA详细信息
kubectl describe hpa user-service -n playforge

# 手动扩缩（如需测试）
kubectl scale deployment user-service --replicas=5 -n playforge

# 恢复到HPA管理
kubectl set env deployment user-service MANUAL_SCALE=false -n playforge
```

---

## 四、CI/CD流水线说明

### GitHub Actions工作流概览

工作流文件位置：`.github/workflows/ci.yml`

**触发条件：**
- 推送到 `main` 分支（自动构建+部署到生产）
- 推送到 `develop` 分支（自动构建+部署到测试）
- Pull Request到 `main` 分支（仅运行测试）

### 工作流阶段

#### 1. Lint 阶段（代码质量检查）
```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - 检出代码
      - 安装Node.js
      - 安装依赖
      - 运行ESLint检查
      - 运行Prettier格式检查（可选）
```

**执行命令：**
```bash
npm run lint
npm run lint:fix  # 自动修复（可选）
```

**失败原则：** 严格错误导致流水线中断，警告允许继续

#### 2. 测试后端服务阶段
```yaml
jobs:
  test-backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: playforge_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432
      
      redis:
        image: redis:7-alpine
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 6379:6379
    
    steps:
      - 检出代码
      - 安装Node.js
      - 安装依赖
      - 生成Prisma Client
      - 运行数据库迁移
      - 运行Jest单元测试
      - 运行集成测试
      - 上传覆盖率报告
```

**测试覆盖率要求：**
- 行覆盖率 >= 70%
- 分支覆盖率 >= 60%
- 函数覆盖率 >= 75%

#### 3. 测试AI引擎阶段
```yaml
jobs:
  test-ai-engine:
    runs-on: ubuntu-latest
    steps:
      - 检出代码
      - 安装Python 3.12
      - 安装依赖（pip install -r requirements.txt）
      - 运行pytest单元测试
      - 运行集成测试（需要Mock LLM API）
```

#### 4. 测试前端阶段（如有）
```yaml
jobs:
  test-frontend:
    runs-on: ubuntu-latest
    steps:
      - 检出代码
      - 安装Node.js
      - 安装依赖
      - 运行Jest/Vitest测试
      - 构建产物（npm run build）
      - 检查Bundle体积
```

#### 5. 构建并推送镜像阶段
```yaml
jobs:
  build-and-push:
    needs: [lint, test-backend, test-ai-engine, test-frontend]
    if: github.ref == 'refs/heads/main' || github.ref == 'refs/heads/develop'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    
    steps:
      - 检出代码
      - 设置Docker Buildx（支持多平台）
      - 登录Docker Registry
      - 为user-service构建并推送镜像
      - 为game-service构建并推送镜像
      - 为social-service构建并推送镜像
      - 为feed-service构建并推送镜像
      - 为ai-engine构建并推送镜像
```

**Docker镜像标签规范：**
- main分支: `playforge-user-service:latest`, `playforge-user-service:v1.2.3`
- develop分支: `playforge-user-service:develop`, `playforge-user-service:dev-<short-sha>`

#### 6. 部署到测试环境阶段
```yaml
jobs:
  deploy-staging:
    needs: build-and-push
    if: github.ref == 'refs/heads/develop'
    runs-on: ubuntu-latest
    steps:
      - 检出代码
      - 设置kubectl和kubeconfig
      - 更新K8s镜像标签
      - 部署到staging namespace
      - 等待Pod就绪（timeout: 5分钟）
      - 运行Smoke测试
      - 发送Slack通知
```

#### 7. 部署到生产环境阶段
```yaml
jobs:
  deploy-production:
    needs: deploy-staging
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: production  # 需要人工审批
    steps:
      - 检出代码
      - 设置kubectl和kubeconfig
      - 执行金丝雀发布（10% -> 50% -> 100%）
      - 运行端到端测试
      - 如失败自动回滚
      - 发送通知（邮件/Slack）
```

### 环境变量配置

**GitHub Secrets配置（在Repository Settings中）：**

```
# Docker Registry认证
DOCKER_USERNAME=<your-docker-username>
DOCKER_PASSWORD=<your-docker-password>

# Kubernetes集群配置
KUBE_CONFIG_STAGING=<base64-encoded-kubeconfig-staging>
KUBE_CONFIG_PRODUCTION=<base64-encoded-kubeconfig-production>

# 敏感环境变量
DATABASE_URL=postgresql://user:pass@host/db
MONGO_URL=mongodb://user:pass@host/db
REDIS_URL=redis://:password@host:6379
JWT_SECRET=<random-secret-key>
LLM_API_KEY=<openai-api-key>

# 通知配置
SLACK_WEBHOOK=https://hooks.slack.com/services/...
OPSGENIE_API_KEY=<your-opsgenie-key>
```

**GitHub Variables配置（公开信息）：**

```
REGISTRY=ghcr.io
IMAGE_NAMESPACE=willgame
KUBE_NAMESPACE=playforge
KUBE_CLUSTER_STAGING=staging-cluster
KUBE_CLUSTER_PRODUCTION=production-cluster
```

### 工作流触发示例

```bash
# 触发main分支工作流（自动部署生产）
git commit -m "fix: critical bug in user service" --allow-empty
git push origin main

# 触发develop分支工作流（自动部署测试）
git push origin develop

# 提交PR到main分支（仅运行测试，不部署）
git checkout -b feature/new-game-type
git push origin feature/new-game-type
# 在GitHub上创建PR到main分支
```

### 工作流监控和调试

```bash
# 查看最新的工作流运行
gh run list --repo willgame/playforge-backend

# 查看特定工作流的详细日志
gh run view <run-id> --repo willgame/playforge-backend

# 实时查看工作流日志
gh run watch <run-id> --repo willgame/playforge-backend

# 重新运行失败的工作流
gh run rerun <run-id> --repo willgame/playforge-backend
```

---

## 五、发布流程标准

### 发布前检查清单（Pre-Release Checklist）

在执行发布之前，必须完成以下10项检查：

```
□ 1. 代码审查
    - 所有PR已获得至少2名维护者的approved
    - 无pending的代码审查意见
    - 没有WIP(Work In Progress)代码

□ 2. 单元测试覆盖率
    - 整体覆盖率 >= 70%
    - 新增代码覆盖率 >= 85%
    - 无skipped或focused测试

□ 3. 集成测试通过
    - 所有服务间通信测试通过
    - 数据库迁移测试通过
    - 第三方API集成测试通过

□ 4. 性能测试通过
    - API响应时间 <= 200ms (P95)
    - WebSocket连接延迟 <= 100ms
    - 数据库查询耗时 <= 500ms

□ 5. 安全检查
    - 运行npm audit，无高危漏洞
    - 运行SAST工具（如Semgrep）
    - 检查敏感信息是否泄露（npm-check-updates）
    - SQL注入/XSS防护验证

□ 6. 数据库迁移脚本
    - 迁移脚本已在测试环境验证
    - 包含rollback脚本
    - 迁移时间预估 < 5分钟
    - 无锁表操作（对于大表）

□ 7. 配置和环境变量
    - .env.example已更新
    - 所有新的environment variables已在secret中配置
    - 配置值在staging环境中验证
    - 无hardcoded密钥或token

□ 8. 文档更新
    - README已更新（新功能说明）
    - API文档已更新（OpenAPI/Swagger）
    - 部署文档已更新（本SOP）
    - 变更日志(CHANGELOG)已更新

□ 9. 依赖版本检查
    - Node.js依赖已更新到最新安全版本
    - Python依赖已更新
    - Docker基础镜像已更新
    - 无过期或不维护的库

□ 10. 灾难恢复计划
    - 回滚脚本已准备
    - 数据库备份已验证
    - On-call人员已通知
    - 紧急联系方式已确认
```

### 金丝雀发布步骤（Canary Deployment）

金丝雀发布是一种分阶段的发布策略，逐步向用户推出新版本，以最小化风险。

**第1阶段：10%流量（15分钟监控）**

```bash
# 1. 更新user-service Deployment，副本分配
# 新版本: 1个pod  旧版本: 9个pod

kubectl set image deployment/user-service \
  user-service=playforge-user-service:v1.2.3 \
  -n playforge

# 修改副本数（使用blue-green或weighted routing）
# 通过修改Deployment或使用Istio/Flagger进行流量分配

# 2. 监控关键指标（15分钟）
# 错误率、延迟、CPU/内存使用
watch kubectl top pods -n playforge
kubectl logs -n playforge -l app=user-service --tail=50 -f

# 3. 收集metrics
# 错误率对比: 旧版本 vs 新版本
# 新版本错误率应 <= 旧版本的110%

# 4. 验证关键业务指标
# - 用户登录成功率 >= 99.5%
# - API P95延迟 <= 200ms
# - 无数据库死锁或超时

curl -s http://playforge-api.example.com/metrics | grep user_login_count
```

**第2阶段：50%流量（30分钟监控）**

```bash
# 1. 确认阶段1指标通过，继续灰度

# 2. 增加新版本副本数到5个，保持旧版本5个
kubectl scale deployment user-service --replicas=10 -n playforge
# （分配5个新pod，5个旧pod，通过load balancer进行流量分配）

# 3. 持续监控30分钟
# 重点关注：
# - 错误率趋势
# - 性能退化信号
# - 用户投诉反馈

# 4. 如发现问题立即停止灰度，回滚到第1阶段
kubectl rollout undo deployment/user-service -n playforge

# 5. 分析问题后重新灰度
```

**第3阶段：100%流量（持续监控）**

```bash
# 1. 确认阶段2指标通过

# 2. 全量部署新版本
kubectl set image deployment/user-service \
  user-service=playforge-user-service:v1.2.3 \
  -n playforge

kubectl scale deployment user-service --replicas=10 -n playforge
# （全部副本更新为新版本）

# 3. 验证所有Pod运行正常
kubectl rollout status deployment/user-service -n playforge --timeout=5m

# 4. 持续监控1小时
# 关注所有关键指标

# 5. 确认新版本稳定后，下线旧版本Pod
# （由于已全部更新，此步骤自动完成）

# 6. 更新生产标签
docker tag playforge-user-service:v1.2.3 playforge-user-service:production
docker push playforge-user-service:production
```

### 发布后验证步骤

```bash
# 1. 健康检查
echo "=== Health Check ==="
curl -s https://playforge-api.example.com/health | jq .
curl -s https://playforge-api.example.com/api/users?limit=1 | jq .

# 2. 烟雾测试（Smoke Testing）
echo "=== Smoke Test ==="
# 用户登录流程
curl -X POST https://playforge-api.example.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password"}'

# 游戏列表
curl -s https://playforge-api.example.com/api/games | jq '.data | length'

# 社交功能（关注）
curl -X POST https://playforge-api.example.com/api/social/follow \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"userId":"<target-user-id>"}'

# 3. 性能基准测试
echo "=== Performance Benchmark ==="
# 与发布前的基准对比
# 新版本P95延迟应 <= 110% of baseline

# 4. 错误日志检查
echo "=== Error Log Check ==="
kubectl logs -n playforge -l app=user-service --tail=500 | grep -i error | wc -l
# 如错误率 > 0.1%，立即回滚

# 5. 数据完整性检查
echo "=== Data Integrity Check ==="
# 如有迁移，验证数据正确性
npx prisma studio  # 或使用SQL查询

# 6. 业务监控确认
echo "=== Business Metrics Check ==="
# - DAU (Daily Active Users) 无异常下降
# - 新游戏上传数 无异常下降
# - 用户充值/消费 无异常
# - 用户投诉 无增加

# 7. 关闭发布警报
# 通知QA和Product团队发布成功
echo "Release v1.2.3 completed successfully" | mail -s "Release Notification" team@playforge.com
```

---

## 六、回滚策略

### 自动回滚触发条件

系统会在以下条件下自动触发回滚：

```
1. 错误率异常
   - 5分钟内错误率 > 1%（正常 < 0.1%）
   - HTTP 500错误率 > 0.5%
   - 连续5个健康检查失败

2. 性能恶化
   - P95响应时间 > 基准线 * 150%
   - P99响应时间 > 基准线 * 200%
   - 请求超时率 > 2%

3. 资源耗尽
   - CPU使用率 > 90% 持续2分钟
   - 内存使用率 > 95%
   - Pod OOMKilled

4. 依赖服务故障
   - 数据库连接失败率 > 5%
   - Redis缓存击穿
   - 第三方API超时

5. 数据一致性问题
   - 数据库迁移失败
   - 数据不一致警告
```

### 自动回滚实现（Kubernetes）

```yaml
# 在CI/CD流水线中配置自动回滚
apiVersion: autoscaling.alibabacloud.com/v1beta1
kind: RolloutController
metadata:
  name: user-service-rollout
spec:
  target: user-service
  strategy: canary
  steps:
  - weight: 10
    duration: 15m
  - weight: 50
    duration: 30m
  - weight: 100
    duration: 1h
  metrics:
  - name: http_error_rate
    threshold: 1%
    action: rollback
  - name: p95_latency
    threshold: 150%
    action: rollback
  - name: cpu_usage
    threshold: 90%
    action: rollback
```

### 手动回滚步骤

**快速回滚（Immediate Rollback）：**

```bash
# 1. 检查当前状态
kubectl get deployment user-service -n playforge
kubectl get rs -n playforge  # 查看ReplicaSet历史

# 2. 执行回滚
kubectl rollout undo deployment/user-service -n playforge

# 3. 验证回滚
kubectl rollout status deployment/user-service -n playforge --timeout=5m
kubectl get pods -n playforge -l app=user-service

# 4. 确认服务恢复
curl -s http://playforge-api.example.com/health | jq .

# 5. 查看回滚前的版本
kubectl rollout history deployment/user-service -n playforge

# 6. 如需回滚到特定版本
kubectl rollout undo deployment/user-service \
  --to-revision=<revision-number> \
  -n playforge
```

**逐步回滚（Staged Rollback）：**

```bash
# 1. 识别问题来源
kubectl logs -n playforge -l app=user-service --tail=200 | grep -i error

# 2. 减少新版本副本数（如金丝雀发布中）
kubectl set image deployment/user-service \
  user-service=playforge-user-service:v1.2.2 \
  -n playforge

# 3. 逐步增加旧版本副本数
kubectl scale deployment user-service --replicas=5 -n playforge

# 4. 监控转换过程
watch kubectl get pods -n playforge -l app=user-service

# 5. 完全回滚
kubectl rollout undo deployment/user-service -n playforge
```

### 数据库回滚策略

**情况1：迁移失败的回滚**

```bash
# 1. 检查迁移历史
npx prisma migrate status

# 2. 解析失败的迁移
# 查看migrations/xxx_*.sql文件中的错误

# 3. 执行回滚（如支持）
npx prisma migrate resolve --rolled-back <migration-name>

# 4. 修复迁移脚本
# 编辑migrations/xxx_*/migration.sql，修正SQL

# 5. 重新应用迁移
npx prisma migrate deploy
```

**情况2：应用层bug导致数据损坏**

```bash
# 1. 停止应用
kubectl scale deployment user-service --replicas=0 -n playforge

# 2. 从备份恢复数据库
# 如使用AWS RDS
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier playforge-db-restored \
  --db-snapshot-identifier playforge-snapshot-2024-01-15-10-00

# 3. 验证恢复的数据
# 运行数据一致性检查脚本

# 4. 如恢复成功，重启应用
kubectl scale deployment user-service --replicas=3 -n playforge

# 5. 验证应用正常
kubectl wait --for=condition=ready pod -l app=user-service -n playforge --timeout=300s
```

**数据库备份策略：**

```bash
# 配置自动备份（每6小时一次）
# PostgreSQL: pg_basebackup or pg_dump
pg_dump -U postgres playforge > playforge_backup_$(date +%Y%m%d_%H%M%S).sql

# MongoDB: mongodump
mongodump --db playforge --out /backups/playforge_$(date +%Y%m%d_%H%M%S)

# 验证备份
psql -U postgres playforge < playforge_backup.sql  # 测试恢复

# 备份保留策略：
# - 每日备份保留7天
# - 每周备份保留4周
# - 每月备份保留1年
```

---

## 七、数据库迁移SOP

### 向后兼容原则

所有数据库迁移必须遵守向后兼容原则，确保新旧版本应用可以在迁移期间共存：

```
基本原则：
1. 不能删除旧列（先废弃，后删除）
2. 不能修改列的数据类型（创建新列，迁移数据，删除旧列）
3. 新增列必须有默认值（不能NOT NULL）
4. 新增表要避免外键依赖（先创建表，后加外键）
5. 索引修改不影响功能（增加/删除索引无碍）
```

### 迁移脚本编写规范

**迁移文件命名：** `YYYYMMDD_HHMMSS_<descriptive-name>.sql`

**示例1：新增列（安全）**

```sql
-- migrations/20240115_100000_add_email_verified_to_users.sql

-- UP: 添加email_verified列
ALTER TABLE users ADD COLUMN email_verified BOOLEAN DEFAULT FALSE NOT NULL;
CREATE INDEX idx_users_email_verified ON users(email_verified);

-- DOWN: 回滚
DROP INDEX IF EXISTS idx_users_email_verified;
ALTER TABLE users DROP COLUMN IF EXISTS email_verified;
```

**示例2：修改列类型（需要中间步骤）**

```sql
-- migrations/20240115_100100_change_user_age_to_string.sql

-- UP: 年龄从int改为string
-- 步骤1: 创建新列
ALTER TABLE users ADD COLUMN age_new VARCHAR(50);

-- 步骤2: 迁移数据
UPDATE users SET age_new = CAST(age AS VARCHAR) WHERE age IS NOT NULL;

-- 步骤3: 删除旧列，重命名新列
ALTER TABLE users DROP COLUMN age;
ALTER TABLE users RENAME COLUMN age_new TO age;

-- DOWN: 回滚
ALTER TABLE users ADD COLUMN age INT;
UPDATE users SET age = CAST(age_string AS INT) WHERE age_string IS NOT NULL;
ALTER TABLE users DROP COLUMN age_string;
```

**示例3：删除列（需要废弃期）**

```sql
-- 迁移1 (v1.1.0): 标记列为废弃（代码层面停止写入）
-- 迁移2 (v1.2.0): 实际删除列
ALTER TABLE users DROP COLUMN deprecated_field;
```

**示例4：添加NOT NULL约束（需要数据清理）**

```sql
-- migrations/20240115_100200_add_not_null_constraint.sql

-- UP:
-- 步骤1: 找出并处理NULL值
UPDATE users SET status = 'active' WHERE status IS NULL;

-- 步骤2: 添加约束
ALTER TABLE users 
  ALTER COLUMN status SET NOT NULL;

-- DOWN:
ALTER TABLE users 
  ALTER COLUMN status DROP NOT NULL;
```

### Prisma迁移工作流

```bash
# 1. 修改schema.prisma
# 如在User model中添加新字段
# model User {
#   id        Int     @id @default(autoincrement())
#   email     String  @unique
#   name      String?
#   newField  String  @default("")  // 新增字段，带默认值
#   createdAt DateTime @default(now())
#   updatedAt DateTime @updatedAt
# }

# 2. 创建migration
npx prisma migrate dev --name add_new_field

# 系统会：
# - 检查schema变更
# - 生成migration文件 (prisma/migrations/xxx_add_new_field/migration.sql)
# - 自动应用到本地数据库
# - 生成Prisma Client更新

# 3. 审查生成的SQL
cat prisma/migrations/xxx_add_new_field/migration.sql

# 4. 在测试环境验证
# 在staging集群运行迁移
docker compose exec user-service npx prisma migrate deploy

# 5. 测试应用兼容性
# 验证新旧代码都能运行

# 6. 提交到Git
git add prisma/migrations/
git commit -m "chore: add new_field to users table"

# 7. 在生产环境应用
npx prisma migrate deploy --environment production

# 生产环境应用迁移步骤：
# - 应用新代码版本（支持新旧数据）
# - 执行数据库迁移
# - 监控错误率和性能
# - 验证数据正确性
```

### 在线DDL操作流程（大表）

对于超过1GB的大表，DDL操作可能锁表，影响可用性：

```bash
# 使用pt-online-schema-change工具（MySQL）或类似工具（PostgreSQL）

# 1. 安装工具
sudo apt-get install percona-toolkit

# 2. 执行在线修改
# 示例: 添加索引
pt-online-schema-change \
  --alter "ADD INDEX idx_game_created_at (created_at)" \
  D=playforge,t=games \
  --execute

# 3. 命令说明
# --alter: 修改语句
# D=database, t=table: 目标表
# --execute: 实际执行（不加此参数只模拟）

# PostgreSQL等数据库可使用：
# CONCURRENTLY: CREATE INDEX CONCURRENTLY idx_name ON table(column)

# 4. 监控进度
# pt-online-schema-change会：
# - 创建影子表
# - 复制旧表数据
# - 应用新DDL
# - 交换表名
# - 删除旧表

# 5. 验证
# - 数据行数一致
# - 索引大小正常
# - 查询性能无退化
```

### 迁移检查清单

```bash
# 在执行迁移前运行此检查清单

□ 1. 备份数据库
    pg_dump playforge > backup_$(date +%s).sql

□ 2. 测试迁移脚本
    # 在测试数据库运行迁移
    psql playforge < migration_file.sql

□ 3. 验证向后兼容性
    # 确保旧代码（如前一个版本）仍可运行

□ 4. 估计执行时间
    # 大表迁移可能耗时较长，评估影响

□ 5. 准备回滚脚本
    # migration文件应包含DOWN段

□ 6. 通知相关团队
    # 告知可能的短暂不可用

□ 7. 计划迁移窗口
    # 选择低峰期（如凌晨2-4点）

□ 8. 监控准备
    # 准备好监控面板，迁移期间实时关注

□ 9. 通信准备
    # 准备好客户通知、Slack消息等
```

---

## 八、监控与告警

### Prometheus指标清单

**应用层指标（Application Metrics）：**

```prometheus
# HTTP请求指标
playforge_http_request_duration_seconds   # 请求耗时分布
playforge_http_requests_total             # 请求总数（按method/path/status分）
playforge_http_errors_total               # 错误请求数

# 业务指标
playforge_user_login_total                # 登录次数
playforge_game_created_total              # 创建游戏数
playforge_game_played_total               # 游戏播放次数
playforge_user_earnings_usd               # 用户收益

# 数据库指标
playforge_db_query_duration_seconds       # 查询耗时
playforge_db_connections_active           # 活跃连接数
playforge_db_connection_pool_size         # 连接池大小
playforge_db_errors_total                 # 数据库错误数

# 缓存指标
playforge_redis_commands_total            # Redis命令次数
playforge_redis_command_duration_seconds  # Redis命令耗时
playforge_cache_hit_ratio                 # 缓存命中率

# WebSocket指标
playforge_websocket_connections_active    # 活跃WebSocket连接数
playforge_websocket_messages_total        # WebSocket消息数

# 外部服务调用
playforge_llm_api_calls_total             # LLM API调用数
playforge_llm_api_duration_seconds        # LLM API耗时
playforge_llm_api_errors_total            # LLM API错误数
```

**系统层指标（Infrastructure Metrics）：**

```prometheus
# Node指标（由node-exporter收集）
node_cpu_seconds_total                    # CPU使用时间
node_memory_MemFree_bytes                 # 空闲内存
node_network_receive_bytes_total          # 网络接收字节数
node_disk_io_time_seconds_total           # 磁盘IO时间

# Kubernetes指标
kube_pod_container_resource_limits        # Pod资源限制
kube_pod_container_resource_requests      # Pod资源请求
kube_deployment_replicas                  # 副本数
container_cpu_usage_seconds_total         # 容器CPU使用
container_memory_usage_bytes               # 容器内存使用
```

### Grafana Dashboard配置

**Dashboard 1: 服务概览（Service Overview）**

```json
{
  "dashboard": {
    "title": "PlayForge Service Overview",
    "panels": [
      {
        "title": "HTTP Request Rate",
        "targets": [
          {
            "expr": "rate(playforge_http_requests_total[5m])"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Error Rate",
        "targets": [
          {
            "expr": "rate(playforge_http_errors_total[5m]) / rate(playforge_http_requests_total[5m])"
          }
        ]
      },
      {
        "title": "P95 Latency",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rate(playforge_http_request_duration_seconds_bucket[5m]))"
          }
        ]
      },
      {
        "title": "Active Connections",
        "targets": [
          {
            "expr": "playforge_db_connections_active"
          }
        ]
      }
    ]
  }
}
```

**Dashboard 2: 资源利用（Resource Utilization）**

```json
{
  "panels": [
    {
      "title": "CPU Usage by Pod",
      "targets": [
        {
          "expr": "sum(rate(container_cpu_usage_seconds_total{pod=~\"user-service.*\"}[1m])) by (pod)"
        }
      ]
    },
    {
      "title": "Memory Usage by Pod",
      "targets": [
        {
          "expr": "sum(container_memory_usage_bytes{pod=~\"user-service.*\"}) by (pod) / 1024 / 1024"
        }
      ]
    },
    {
      "title": "Network I/O",
      "targets": [
        {
          "expr": "rate(container_network_receive_bytes_total[1m])"
        }
      ]
    }
  ]
}
```

**Dashboard 3: 业务指标（Business Metrics）**

```json
{
  "panels": [
    {
      "title": "Daily Active Users",
      "targets": [
        {
          "expr": "increase(playforge_user_login_total[24h])"
        }
      ]
    },
    {
      "title": "Games Created (Daily)",
      "targets": [
        {
          "expr": "increase(playforge_game_created_total[24h])"
        }
      ]
    },
    {
      "title": "Total Revenue",
      "targets": [
        {
          "expr": "sum(playforge_user_earnings_usd)"
        }
      ]
    }
  ]
}
```

### 告警规则（Alert Rules）

告警分为4个级别，告警规则定义在 `infrastructure/prometheus/alert-rules.yml`

**P0 告警（Critical - 立即响应）：**

```yaml
groups:
- name: playforge-p0-alerts
  interval: 30s
  rules:
  - alert: ServiceDown
    expr: up{job="playforge"} == 0
    for: 1m
    annotations:
      severity: P0
      description: "Service {{ $labels.instance }} is down"

  - alert: HighErrorRate
    expr: rate(playforge_http_errors_total[5m]) / rate(playforge_http_requests_total[5m]) > 0.05
    for: 2m
    annotations:
      severity: P0
      description: "Error rate > 5% in {{ $labels.service }}"

  - alert: DatabaseConnectionFailed
    expr: playforge_db_connections_active == 0
    for: 1m
    annotations:
      severity: P0
      description: "No active database connections"
```

**P1 告警（High - 30分钟内响应）：**

```yaml
- alert: HighLatency
  expr: histogram_quantile(0.95, rate(playforge_http_request_duration_seconds_bucket[5m])) > 1
  for: 5m
  annotations:
    severity: P1
    description: "P95 latency > 1s"

- alert: HighMemoryUsage
  expr: sum(container_memory_usage_bytes) by (pod) / sum(kube_pod_container_resource_limits{resource="memory"}) by (pod) > 0.9
  for: 5m
  annotations:
    severity: P1
    description: "Memory usage > 90%"
```

**P2 告警（Medium - 4小时内响应）：**

```yaml
- alert: CacheHitRatioLow
  expr: playforge_cache_hit_ratio < 0.7
  for: 15m
  annotations:
    severity: P2
    description: "Cache hit ratio < 70%"

- alert: SlowDatabaseQuery
  expr: histogram_quantile(0.95, rate(playforge_db_query_duration_seconds_bucket[5m])) > 2
  for: 10m
  annotations:
    severity: P2
    description: "Database queries slow"
```

**P3 告警（Low - 24小时内响应）：**

```yaml
- alert: LowDiskSpace
  expr: (node_filesystem_avail_bytes / node_filesystem_size_bytes) < 0.15
  for: 30m
  annotations:
    severity: P3
    description: "Disk space < 15%"

- alert: UnusualErrorPattern
  expr: rate(playforge_http_errors_total{status=~"4xx"}[1h]) > 0.01
  for: 30m
  annotations:
    severity: P3
    description: "Unusual 4xx error pattern"
```

### 告警通知渠道

```yaml
# alertmanager配置示例
global:
  resolve_timeout: 5m

route:
  # 默认接收器
  receiver: 'default'
  group_by: ['alertname', 'cluster']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 4h

  routes:
  # P0告警: 立即通知所有渠道
  - match:
      severity: P0
    receiver: 'p0-oncall'
    repeat_interval: 5m

  # P1告警: 通知Slack和邮件
  - match:
      severity: P1
    receiver: 'p1-team'
    repeat_interval: 1h

receivers:
- name: 'default'
  slack_configs:
  - api_url: https://hooks.slack.com/services/YOUR/WEBHOOK
    channel: '#playforge-alerts'

- name: 'p0-oncall'
  slack_configs:
  - api_url: https://hooks.slack.com/services/YOUR/WEBHOOK
    channel: '#playforge-p0'
  opsgenie_configs:
  - api_key: 'YOUR-OPSGENIE-KEY'
    priority: 'P1'
  pagerduty_configs:
  - service_key: 'YOUR-PAGERDUTY-KEY'
  email_configs:
  - to: 'oncall@playforge.com'
    smarthost: 'smtp.gmail.com:587'

- name: 'p1-team'
  slack_configs:
  - api_url: https://hooks.slack.com/services/YOUR/WEBHOOK
    channel: '#playforge-p1'
  email_configs:
  - to: 'team@playforge.com'
```

### On-Call值班机制

```
每周值班表（On-Call Rotation）：

周一-周五 (工作日) 09:00-18:00: 主值班 (值班时长8小时)
周一-周五 (工作日) 18:00-09:00: 备值班 (值班时长13小时)
周末全天: 周末值班 (值班时长24小时)

值班职责：
1. 监控告警，P0/P1告警5分钟内响应
2. 协助解决线上问题
3. 执行紧急发布和回滚
4. 记录incident日志
5. 每个incident后，团队进行复盘(Postmortem)

值班工具：
- PagerDuty: 告警路由和事件管理
- 值班表: Google Calendar 或 OncallCalendar
- Incident追踪: Jira 或 Linear

值班交接：
- 前一位值班人员在Slack告知：
  "@new-oncall 你的值班时间从XX开始"
- 新值班人员确认已准备好
- 交接约5分钟，包括当前issue状态
```

---

## 九、常见问题排查

### 问题1: 服务无法启动

**症状：** Pod一直处于CrashLoopBackOff或Pending状态

```bash
# 诊断步骤
kubectl describe pod <pod-name> -n playforge
# 查看Events部分，寻找失败原因

kubectl logs <pod-name> -n playforge
# 查看应用日志

# 常见原因及解决方案

# 原因1: 镜像无法拉取
# 症状: "ImagePullBackOff"
# 解决:
kubectl get secret regcred -n playforge  # 检查镜像库凭证
docker pull <image-name>  # 验证镜像是否存在

# 原因2: 资源不足
# 症状: "Pending", "Insufficient memory"
# 解决:
kubectl top nodes  # 检查集群资源
kubectl describe node <node-name>  # 查看节点资源
# 调整Deployment中的resources.requests或scale up节点

# 原因3: 健康检查失败
# 症状: 健康检查失败，容器反复重启
# 解决:
kubectl logs <pod-name> --previous  # 查看上一次的日志
# 检查Dockerfile中的HEALTHCHECK或K8s中的livenessProbe

# 原因4: 环境变量缺失
# 症状: "ReferenceError: DATABASE_URL is not defined"
# 解决:
kubectl get cm,secret -n playforge  # 检查ConfigMap和Secret
kubectl set env deployment user-service -n playforge --list  # 列出已设置的环境变量
# 添加缺失的环境变量

# 原因5: 端口冲突
# 症状: "Address already in use"
# 解决:
kubectl exec <pod-name> -n playforge -- netstat -tlnp  # 检查监听端口
# 修改Dockerfile或应用配置中的端口
```

### 问题2: 数据库连接失败

**症状：** "Error: connect ECONNREFUSED postgres:5432"

```bash
# 诊断步骤

# 1. 检查PostgreSQL Pod状态
kubectl get pods -n playforge -l app=postgres
kubectl logs postgres-0 -n playforge

# 2. 检查Service是否存在
kubectl get svc postgres -n playforge
kubectl describe svc postgres -n playforge

# 3. 从应用Pod测试连接
kubectl exec <app-pod> -n playforge -- nc -zv postgres 5432
# 或
kubectl exec <app-pod> -n playforge -- psql -h postgres -U postgres -c "SELECT 1"

# 常见原因及解决方案

# 原因1: PostgreSQL Pod未就绪
# 症状: pod status 不是 "Running"
# 解决:
kubectl describe pod postgres-0 -n playforge
# 检查events，等待初始化完成，可能需要几分钟

# 原因2: Service DNS解析失败
# 症状: "getaddrinfo ENOTFOUND postgres"
# 解决:
# Pod内测试DNS:
kubectl exec <app-pod> -n playforge -- nslookup postgres.playforge.svc.cluster.local
# 如失败，检查集群DNS配置，可能是CoreDNS故障

# 原因3: 网络策略阻止
# 症状: 连接超时，telnet不通
# 解决:
kubectl get networkpolicies -n playforge
# 如有NetworkPolicy，检查是否允许应用到postgres的连接
# 临时禁用测试:
kubectl delete networkpolicies --all -n playforge

# 原因4: 密码错误
# 症状: "FATAL: password authentication failed"
# 解决:
kubectl get secret playforge-secret -n playforge -o yaml
# 检查POSTGRES_PASSWORD是否正确，base64解码验证:
echo "encoded-password" | base64 -d

# 原因5: 数据库不存在
# 症状: "FATAL: database 'playforge' does not exist"
# 解决:
# 进入postgres pod创建数据库
kubectl exec postgres-0 -n playforge -- psql -U postgres -c "CREATE DATABASE playforge;"
```

### 问题3: AI引擎超时

**症状：** "LLM API request timeout after 30s"

```bash
# 诊断步骤

# 1. 检查AI引擎Pod状态
kubectl get pods -n playforge -l app=ai-engine
kubectl logs -n playforge -l app=ai-engine --tail=100

# 2. 检查AI引擎健康状态
kubectl exec <ai-engine-pod> -n playforge -- curl -s http://localhost:3005/health | jq .

# 3. 检查GPU资源（如配置了GPU）
kubectl describe node <gpu-node>
# 查看nvidia.com/gpu的Allocatable和Allocated

# 常见原因及解决方案

# 原因1: LLM API速度慢或故障
# 症状: 调用OpenAI/Claude API缓慢
# 解决:
# 检查API调用日志
kubectl logs <ai-engine-pod> -n playforge | grep -i "openai\|api"
# 检查API配额和rate limit
# 增加超时时间（在ai-engine/config.py中）
timeout = 60  # 从30改为60

# 原因2: AI引擎Pod内存不足
# 症状: "MemoryError", "OOMKilled"
# 解决:
kubectl top pods <ai-engine-pod> -n playforge
# 增加内存request和limit:
kubectl set resources deployment ai-engine \
  --limits=memory=2Gi \
  --requests=memory=1Gi \
  -n playforge

# 原因3: 网络连接问题
# 症状: "Connection timeout to openai.com"
# 解决:
# 检查网络访问
kubectl exec <ai-engine-pod> -n playforge -- ping 8.8.8.8
# 检查http proxy配置（如有）
kubectl exec <ai-engine-pod> -n playforge -- env | grep PROXY

# 原因4: LLM API Key过期或无效
# 症状: "Invalid API key" or "Unauthorized"
# 解决:
# 检查Secret中的LLM_API_KEY
kubectl get secret playforge-secret -n playforge -o jsonpath='{.data.LLM_API_KEY}' | base64 -d
# 更新Secret:
kubectl patch secret playforge-secret -n playforge \
  -p '{"data":{"LLM_API_KEY":"'$(echo -n "new-key" | base64 -w0)'"}}'
# 重启AI引擎pod以读取新的环境变量
kubectl rollout restart deployment ai-engine -n playforge
```

### 问题4: WebSocket连接断开

**症状：** "WebSocket connection closed unexpectedly"

```bash
# 诊断步骤

# 1. 检查WebSocket服务状态
curl -s http://localhost:3000/health | jq .

# 2. 测试WebSocket连接
wscat -c ws://localhost:3000/ws
# 发送消息后查看是否能接收回复

# 3. 检查应用日志
kubectl logs -n playforge -l app=user-service | grep -i websocket

# 常见原因及解决方案

# 原因1: Ingress不支持WebSocket
# 症状: 通过ingress连接时WebSocket断开，但直接pod端口正常
# 解决:
# 确保Ingress配置包含WebSocket升级注解
# 在ingress.yaml中:
metadata:
  annotations:
    nginx.ingress.kubernetes.io/websocket-services: "user-service"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"

# 原因2: 长连接超时
# 症状: 连接空闲超过特定时间后自动断开
# 解决:
# 增加nginx proxy timeout:
kubectl set env deployment user-service \
  WEBSOCKET_TIMEOUT=3600 \
  -n playforge
# 或在代码中定期ping客户端保持连接

# 原因3: 代理/LB连接超时
# 症状: 通过公网连接时容易断开
# 解决:
# 增加TCP keepalive
# 在应用代码中设置WebSocket ping/pong
setInterval(() => {
  ws.ping();
}, 30000);

# 原因4: 内存泄漏导致进程崩溃
# 症状: WebSocket连接数增加后逐渐崩溃
# 解决:
# 监控进程内存
kubectl top pods <user-service-pod> -n playforge
# 检查代码中的内存泄漏
# 配置自动重启（如内存超过限制）
kubectl set resources deployment user-service \
  --limits=memory=512Mi \
  -n playforge
```

### 问题5: Redis缓存异常

**症状：** "Redis connection error" 或 "Cache miss rate > 50%"

```bash
# 诊断步骤

# 1. 检查Redis Pod状态
kubectl get pods -n playforge -l app=redis
kubectl logs redis-0 -n playforge

# 2. 测试Redis连接
kubectl exec <app-pod> -n playforge -- redis-cli -h redis ping
# 预期输出: PONG

# 3. 检查Redis内存使用
kubectl exec redis-0 -n playforge -- redis-cli INFO memory

# 常见原因及解决方案

# 原因1: Redis服务未启动
# 症状: "Connection refused on redis:6379"
# 解决:
kubectl apply -f infrastructure/k8s/redis.yaml
kubectl wait --for=condition=ready pod -l app=redis -n playforge

# 原因2: Redis内存满
# 症状: "OOM command not allowed when used memory > maxmemory"
# 解决:
# 增加内存限制
kubectl edit statefulset redis -n playforge
# 修改resources.limits.memory
# 或清理过期key:
kubectl exec redis-0 -n playforge -- redis-cli FLUSHDB  # 谨慎！

# 原因3: Redis持久化失败
# 症状: "Failed to persist data", RDB文件损坏
# 解决:
# 检查磁盘空间
kubectl exec redis-0 -n playforge -- df -h
# 删除损坏的RDB文件:
kubectl exec redis-0 -n playforge -- rm /data/dump.rdb
# 重启Redis:
kubectl delete pod redis-0 -n playforge

# 原因4: 缓存Key过期策略不当
# 症状: 重要缓存被意外删除，导致缓存穿透
# 解决:
# 检查Redis缓存策略:
kubectl exec redis-0 -n playforge -- redis-cli CONFIG GET maxmemory-policy
# 应为: allkeys-lru 或 volatile-lru
# 更新策略:
kubectl exec redis-0 -n playforge -- redis-cli CONFIG SET maxmemory-policy "allkeys-lru"

# 原因5: 应用端缓存配置错误
# 症状: 代码中缓存参数设置不当
# 解决:
# 检查.env文件中的REDIS_URL和缓存TTL设置
# 确保缓存Key不会重复或冲突
# 使用Redis命令查看key:
kubectl exec redis-0 -n playforge -- redis-cli KEYS "*"
kubectl exec redis-0 -n playforge -- redis-cli TTL <key-name>
```

### 其他常见问题快速查询

**Q: 如何查看完整的应用日志？**
```bash
# 查看某个pod的实时日志
kubectl logs -f <pod-name> -n playforge

# 查看特定时间范围的日志
kubectl logs <pod-name> -n playforge --since=1h

# 查看已终止pod的日志
kubectl logs <pod-name> --previous -n playforge

# 查看所有同一应用的pod日志
kubectl logs -f -l app=user-service -n playforge --all-containers=true
```

**Q: 如何进入Pod内部调试？**
```bash
# 进入Pod容器
kubectl exec -it <pod-name> -n playforge -- /bin/bash

# 运行单条命令
kubectl exec <pod-name> -n playforge -- env

# 如果容器很小（Alpine）
kubectl exec -it <pod-name> -n playforge -- sh
```

**Q: 如何临时修改环境变量进行测试？**
```bash
# 设置环境变量
kubectl set env deployment user-service DEBUG=true -n playforge

# 列出当前环境变量
kubectl set env deployment user-service -n playforge --list

# 删除环境变量
kubectl set env deployment user-service DEBUG- -n playforge
```

**Q: 如何强制删除卡住的资源？**
```bash
# 强制删除Pod（最后手段）
kubectl delete pod <pod-name> -n playforge --grace-period=0 --force

# 强制删除Deployment
kubectl delete deployment <name> -n playforge --grace-period=0 --force
```

---

## 附录：快速参考命令

### 常用kubectl命令
```bash
# 资源查看
kubectl get nodes
kubectl get namespaces
kubectl get deployments -n playforge
kubectl get pods -n playforge -o wide
kubectl get services -n playforge
kubectl get ingress -n playforge

# 资源详情
kubectl describe node <node-name>
kubectl describe deployment user-service -n playforge
kubectl describe pod <pod-name> -n playforge

# 日志和调试
kubectl logs <pod-name> -n playforge
kubectl exec -it <pod-name> -n playforge -- bash
kubectl port-forward <pod-name> 3000:3000 -n playforge

# 修改和更新
kubectl edit deployment user-service -n playforge
kubectl patch deployment user-service -p '{"spec":{"replicas":5}}' -n playforge
kubectl rollout restart deployment user-service -n playforge
kubectl rollout undo deployment user-service -n playforge

# 应用配置
kubectl apply -f <file.yaml> -n playforge
kubectl delete -f <file.yaml> -n playforge
```

### 常用Docker命令
```bash
# 镜像操作
docker build -t playforge-user-service:latest .
docker tag playforge-user-service:latest registry.example.com/playforge-user-service:latest
docker push registry.example.com/playforge-user-service:latest
docker pull registry.example.com/playforge-user-service:latest

# 容器操作
docker compose up -d
docker compose down
docker compose logs -f <service>
docker exec -it <container> bash
docker inspect <container>

# 清理
docker system prune -a  # 删除未使用的镜像和容器
```

---

## 文档维护

本文档最后更新时间：2024-01-15
主要维护者：DevOps Team
更新频率：季度审查，发现问题立即更新

如有问题或改进建议，请联系 devops@playforge.com

