# Gamevallies Backend 部署 SOP

## 概述

基于 GitHub Actions + Kubernetes 的全自动 CI/CD 流水线，共 **8 个阶段**。

---

## 一、分支与触发规则

| 分支/事件 | 触发阶段 | 目标环境 |
|---|---|---|
| PR → `main` / `develop` | Stage 1–5（测试+安全扫描） | 无部署 |
| push `develop` | Stage 1–7 | Staging 自动部署 |
| push `main` | Stage 1–8 | Production 金丝雀部署 |
| push tag `v*` | Stage 1–6 | 仅构建镜像 |

---

## 二、流水线各阶段说明

```
[lint] ──┬──→ [test-backend]  ──┐
         ├──→ [test-ai-engine] ──┼──→ [build-and-push] ──→ [deploy-staging] ──→ [deploy-production]
         ├──→ [test-frontend]  ──┘
         └──→ [security-scan] ──┘
```

| Stage | 名称 | 说明 |
|---|---|---|
| 1 | lint | ESLint + Prettier + TypeScript 类型检查 |
| 2 | test-backend | 单元测试 + 集成测试（含 PG/Mongo/Redis 服务） |
| 3 | test-ai-engine | Python pytest + 覆盖率上报 |
| 4 | test-frontend | 前端测试（有前端代码时触发） |
| 5 | security-scan | Trivy 漏洞扫描 + npm audit |
| 6 | build-and-push | 构建 5 个服务镜像推送到 `ghcr.io` |
| 7 | deploy-staging | 更新 K8s Staging 集群镜像，smoke test |
| 8 | deploy-production | 金丝雀部署（10% → 50% → 100%），失败自动回滚 |

---

## 三、首次部署前置准备

### 3.1 配置 GitHub Secrets

进入仓库 → Settings → Secrets and variables → Actions，添加以下 Secret：

| Secret 名称 | 说明 |
|---|---|
| `KUBE_CONFIG_STAGING` | Staging K8s kubeconfig（base64 编码） |
| `KUBE_CONFIG_PRODUCTION` | Production K8s kubeconfig（base64 编码） |
| `SLACK_WEBHOOK` | Slack 通知 Webhook URL |

生成 kubeconfig base64：
```bash
cat ~/.kube/config | base64 | pbcopy
```

### 3.2 初始化 K8s 命名空间和基础资源

```bash
# 创建 namespace
kubectl apply -f infrastructure/k8s/namespace.yaml

# 创建 Secret（先填写真实密码）
kubectl apply -f infrastructure/k8s/secret.yaml

# 创建 ConfigMap
kubectl apply -f infrastructure/k8s/configmap.yaml

# 部署数据库
kubectl apply -f infrastructure/k8s/postgres.yaml
kubectl apply -f infrastructure/k8s/mongo.yaml
kubectl apply -f infrastructure/k8s/redis.yaml

# 执行数据库迁移（首次）
kubectl run prisma-migrate --image=ghcr.io/willgame/gamevallies-user-service:latest \
  -n gamevallies --restart=Never \
  --env="DATABASE_URL=<your-db-url>" \
  -- npx prisma migrate deploy
```

### 3.3 GitHub Container Registry 权限

确保仓库 Settings → Actions → General → Workflow permissions 设置为 **Read and write permissions**（用于 push 镜像）。

---

## 四、日常发布流程

### 4.1 发布到 Staging

```bash
git checkout develop
git merge feature/your-feature
git push origin develop
# CI/CD 自动触发，约 10–15 分钟完成
```

### 4.2 发布到 Production

```bash
git checkout main
git merge develop
git push origin main
# 金丝雀部署自动开始
# 10% 流量 → 观察 5 分钟 → 50% 流量 → 观察 5 分钟 → 100%
```

### 4.3 发布带版本号的 Release

```bash
git tag v1.2.0
git push origin v1.2.0
# 自动构建并推送镜像 tag: v1.2.0 和 1.2
```

---

## 五、镜像命名规则

镜像仓库地址：`ghcr.io/willgame/<service-name>`

| 服务 | 镜像地址 |
|---|---|
| user-service | `ghcr.io/willgame/gamevallies-user-service` |
| game-service | `ghcr.io/willgame/gamevallies-game-service` |
| social-service | `ghcr.io/willgame/gamevallies-social-service` |
| feed-service | `ghcr.io/willgame/gamevallies-feed-service` |
| ai-engine | `ghcr.io/willgame/gamevallies-ai-engine` |

Tag 规则：

| Tag | 说明 |
|---|---|
| `latest` | main 分支最新 |
| `develop` | develop 分支最新 |
| `sha-xxxxxxx` | 每次提交的 commit SHA |
| `v1.2.0` / `1.2` | 版本 tag |

---

## 六、回滚操作

### 自动回滚

Production 部署失败时，流水线自动执行：

```bash
kubectl rollout undo deployment/<service-name> -n gamevallies
```

### 手动回滚

```bash
# 查看历史版本
kubectl rollout history deployment/user-service -n gamevallies

# 回滚到上一版本
kubectl rollout undo deployment/user-service -n gamevallies

# 回滚到指定版本
kubectl rollout undo deployment/user-service --to-revision=3 -n gamevallies

# 验证回滚状态
kubectl rollout status deployment/user-service -n gamevallies
```

---

## 七、部署状态监控

```bash
# 查看所有服务状态
kubectl get deployments -n gamevallies -o wide

# 查看 Pod 状态
kubectl get pods -n gamevallies

# 查看服务日志
kubectl logs -f deployment/user-service -n gamevallies

# 查看近期事件
kubectl get events -n gamevallies --sort-by='.lastTimestamp'
```

---

## 八、注意事项

1. **数据库迁移**：有 schema 变更时，需在部署前手动执行 `prisma migrate deploy`，或在 CI 中增加迁移 Job
2. **Secret 更新**：修改密码等敏感配置后，需重启相关 Pod 使配置生效：
   ```bash
   kubectl rollout restart deployment/<name> -n gamevallies
   ```
3. **Production 审批**：如需人工审批，在 GitHub → Environments → production 配置 **Required reviewers**
