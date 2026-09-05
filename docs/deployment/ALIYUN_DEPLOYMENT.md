# 阿里云部署与维护

本仓库采用 ACR + 单台 Linux x86_64 ECS + Docker Compose，数据库使用已有 MySQL/RDS，异步任务使用已有 Redis/Tair。发布入口为 `.github/workflows/deploy.yml`。前端使用另一个仓库的独立发布流程，两者共用 ECS 上的 `gamevallies` 网络。

提交工作流不等于上线：必须先完成下列资源、Runner、Secrets 和运行配置，再设置 `ALIYUN_DEPLOY_ENABLED=true`。未配置时部署任务跳过；CI 照常执行。首次启用后在 main 手动运行一次，之后 main 的提交自动部署。

## 1. 准备资源

- ACR 命名空间内建立 `user-service`、`game-service`、`social-service`、`feed-service`、`ai-engine`、`gateway`、`frontend` 七个镜像仓库。GitHub 托管构建机需要访问 ACR 推送地址，ECS 需要访问同一地址拉取。
- ECS 安装 Docker Engine、Compose v2（支持 `up --wait --wait-timeout`）、Git、Bash、Python 3、flock。为 AI 浏览器检查预留共享内存；具体内存和 CPU 以生成并发压测确定。
- 在 ECS 为两个私有仓库各注册一个专用 GitHub Actions Runner，分别添加 `aliyun-ecs-backend`、`aliyun-ecs-front` 标签，系统标签应为 Linux / X64。使用 GitHub 仓库 Settings → Actions → Runners 提供的安装命令，以服务方式运行。仅用于受信任 main 发布，禁止将 PR 工作分配到这些 Runner。
- Runner 用户需要使用 Docker，且分别拥有 `/opt/gamevallies/backend`、`/opt/gamevallies/front`。Docker 权限等同于主机管理权限，Runner 应是专用服务账号。
- RDS、Redis、ECS 放在可互通的 VPC 中，配置相应私网访问许可。不要暴露数据库和 Redis 公网端口。
- 用 ALB 终止 HTTPS：应用域名指向 ECS 8080，独立游戏内容域名指向 ECS 8082。两组健康检查路径都是 `/health`。8082 仅提供游戏内容，不能用作应用 API。ECS 这两个端口只允许来自 ALB 的流量；ALB 的空闲超时需覆盖 SSE/WebSocket 心跳间隔。

## 2. GitHub 配置

两个仓库均设置下列 **repository Variables/Secrets**（镜像构建 job 不使用 production Environment，因此不能只把构建凭据放在 Environment Secrets 中）：

| 类型 | 名称 | 内容 |
| --- | --- | --- |
| Variable | `ALIYUN_DEPLOY_ENABLED` | 准备完毕后设为 `true` |
| Variable | `ACR_REGISTRY` | ACR 登录域名，不带 `https://` 或路径 |
| Variable | `ACR_NAMESPACE` | 上述镜像所在命名空间 |
| Secret | `ACR_USERNAME` / `ACR_PASSWORD` | 仅授予所需仓库推送权限的登录凭据 |
| Secret | `ACR_PULL_USERNAME` / `ACR_PULL_PASSWORD` | ECS 专用、仅授予所需仓库拉取权限的凭据 |
| Variable（后端） | `GATEWAY_BIND` | ECS 私网 IP；未设时只监听 `127.0.0.1` |

创建名为 `production` 的 Environment，将部署分支限制为 main。工作流本身也拒绝非 main 的手动部署。不需要把 ECS SSH 私钥交给工作流。

前端另需设置 `PUBLIC_ORIGIN`（应用 HTTPS origin）和 `GAME_CONTENT_ORIGIN`（游戏内容 HTTPS origin）；可选 `GAME_SHELL_ORIGIN` 默认应用 origin。均不带末尾 `/`。微信公众号登录的公开构建变量见前端部署文档。

## 3. ECS 运行配置

将 `deploy/aliyun/runtime.env.example` 复制为 `/opt/gamevallies/backend/runtime.env`，填写真实值并设为 `chmod 600`。文件由 Runner 账号读取；不得提交到 Git。数据库 URL 内的特殊字符需要 URL 编码。

必填项包括数据库、Redis、JWT 两个独立密钥、管理员令牌、生产域名、模型访问配置。短信、支付、微信等账户参数按已有业务接入填写；工作流不会从已删除的部署脚本推导这些值。若支付使用文件型私钥，需要另行挂载只读密钥文件并配置路径。

容器内部服务地址、端口和 `cn_shanghai` 执行区域由 Compose 指定。AI 模型 Provider 的业务配置仍由后台管理；发布前检查数据库内启用的 Provider/区域目标是否指向本次配置的 AI 引擎。可在 runtime.env 设置 `ACR_REGISTRY`、`ACR_NAMESPACE`、`ALIYUN_VPC_ID`、`ALIYUN_VSWITCH_ID`、`ALIYUN_SECURITY_GROUP_ID`，供后台展示部署元数据。

此发布流程不会执行数据库初始化或结构迁移。首次部署空数据库时，先在备份/测试环境验证仓库 Prisma schema 的初始化方案；生产迁移单独审阅执行。现有应用启动时仍会进行其原有的表结构与配置补齐，并将默认云账号/区域元数据更新为阿里云，历史数据库列名保留兼容。不要将已有数据库当作空库执行 `db push`。

## 4. 发布与回滚

1. CI 构建和回归通过后，按完整 Git commit SHA 标记镜像并推送 ACR。
2. ECS Runner 拉取该次发布的全部镜像；拉取失败时不重启现有服务。
3. 运行 `docker compose up -d --wait --wait-timeout 240`，各容器通过 HTTP 健康检查后才切换 `current` 发布指针。
4. 健康检查失败时使用上个发布目录的镜像清单恢复。首次安装没有旧版本，会保留失败容器供诊断并将 job 标为失败。回滚失败也会明确报错。

运行健康检查仅证明进程及 HTTP 路由可用，不代表短信、支付、模型或数据库业务已全部通过。首次发布后验证登录、支付回调、生成任务、SSE、WebSocket、作品播放、APK 下载，然后再切换生产 DNS。

单 ECS 原地更新会有短暂中断；生成中的任务可能受进程重启影响，选择低流量窗口并观察队列。需要无中断发布时，再引入多实例和 ALB 流量切换。

手动回滚：登录 ECS，先以只读 ACR 账号执行 `docker login`，选择已验证发布目录，并用其完整清单执行（`RELEASE` 必须替换成实际目录）：

```bash
RELEASE=/opt/gamevallies/backend/releases/REPLACE_WITH_EXISTING_RELEASE
# 避免 shell 环境覆盖旧发布清单。
unset IMAGE_PREFIX IMAGE_TAG RUNTIME_ENV_FILE GATEWAY_BIND
docker compose -p gamevallies-backend --env-file "$RELEASE/images.env" -f "$RELEASE/compose.yml" up -d --wait --wait-timeout 240
ln -sfn "$RELEASE" /opt/gamevallies/backend/current.next
mv -Tf /opt/gamevallies/backend/current.next /opt/gamevallies/backend/current
```

仅在上述 `up` 返回成功后更新指针；若失败，停止并排查，不要继续执行后续命令。回滚只恢复镜像和 Compose，**不回退运行密钥、数据库内容或模型配置**。

## 5. 日常维护与资源迁移边界

- 当前清单：`/opt/gamevallies/backend/current/{compose.yml,images.env}`。在同一 compose 命令前缀下执行 `ps` 或 `logs --tail 200 SERVICE` 排查；避免将包含令牌的应用日志公开发布。
- APK 的本地存储挂载为持久化命名卷 `gamevallies-backend_app-releases`。已有本地文件需要单独复制/恢复；工作流不删除卷、不执行 `down -v`、不自动清理旧镜像。
- 游戏包、APK 的现有对象存储适配仍保留，以免迁移部署导致历史下载失效。本次没有复制历史对象、替换 CDN 地址或修改数据库中的存储键。若要求所有存储也迁入 OSS，应先盘点对象与引用，再做复制、校验和切换；不要直接删除原存储凭据。新模板默认关闭游戏 CDN，作品走应用内容路由。
- 业务运维脚本默认使用仓库本地 `.env.production`；需要运行时仅准备所需配置，并通过已有 `--env-file` 参数指定路径（若脚本支持）。服务器运行配置统一在 `runtime.env`；不应把生产密钥同步回仓库。
- 已移除原云平台的函数部署、镜像登录/同步脚本、Lambda 打包入口、部署技能和相应旧运维/设计文档。LLM 接口、对象存储适配及兼容数据库列属于业务代码，不作为部署脚本删除。

## 官方参考

- [ACR 推送与拉取镜像](https://www.alibabacloud.com/help/en/acr/getting-started/use-a-container-registry-enterprise-edition-instance-to-push-and-pull-images)
- [ACR 访问凭据](https://www.alibabacloud.com/help/en/acr/user-guide/configure-access-credentials)
- [ECS 安装和使用 Docker](https://help.aliyun.com/en/ecs/user-guide/install-and-use-docker)
- [Compose up 健康等待参数](https://docs.docker.com/reference/cli/docker/compose/up/)
