# 阿里云函数计算 FC 部署与维护

## 运行方案

使用 FC 3.0 自定义容器与 ACR，不使用 ECS、服务器 Runner、Docker Compose 或 ALB。GitHub 托管 Runner 构建镜像并使用官方 Python SDK 4.8.2 发布。地域、函数前缀与资源参数在部署前明确配置。

| 函数 | 运行方式 | 入口 |
| --- | --- | --- |
| frontend（前端仓库） | 按需实例 | 静态 Web 页面 |
| user-service / social-service / feed-service | 按需实例 | 内部服务调用 |
| game-service | 1 个预留实例，持续 CPU，禁止按需实例 | 业务 API、BullMQ 消费者、进度 |
| ai-engine | 1 个预留实例，持续 CPU，禁止按需实例 | 内部 AI 请求和后台生成 |
| gateway | 按需实例 | 应用域名，Web + API + Socket.IO |
| content | 按需实例 | 独立作品内容域名，仅 /games/ |

后台执行仍使用现有 BullMQ 和 AI 异步任务管理器，本轮没有改写为 FC 原生异步任务。持续 CPU 预留模式用于避免闲置冻结，两个服务有持续资源成本。`functions.json` 是初始资源配置，不代表已完成并发压测。禁止在未改造后台任务和跨实例取消前将这两个服务缩到零或增加按需副本。FC 平台重启仍可能中断在途计算；Redis 快照/数据库任务记录、现有超时对账与重试负责暴露和处理失败，不承诺计算无损续跑。

逻辑执行区域目前沿用业务代码的 `cn_shanghai`；真实 FC 地域由 `FC_REGION` 决定，二者不要混淆。部署前检查后台 Provider 的 AI 地址和区域记录是否指向新 FC AI 引擎，数据库中已保存的旧目标不会被部署器自动批量改写。

## 配置清单

两个仓库的 Repository Variables：

| 名称 | 值 |
| --- | --- |
| ALIYUN_FC_DEPLOY_ENABLED | 准备完成后设 true，旧 ALIYUN_DEPLOY_ENABLED 无效 |
| FC_ACCOUNT_ID / FC_REGION | 阿里云主账号 ID / FC 地域 |
| FC_PREFIX | 专用于本项目环境的函数前缀，例如 gamevallies-prod |
| FC_EXECUTION_ROLE | 函数执行 RAM 角色 ARN，允许必要的 ACR 拉取、VPC、NAS、SLS 操作 |
| ACR_REGISTRY / ACR_NAMESPACE | 同账号同地域 ACR 地址与命名空间 |
| ACR_INSTANCE_ID | 企业版 ACR 按实际情况填写 |
| FC_FRONTEND_URL | 后端仓库必填，前端发布输出的 HTTPS 函数 origin |

Repository Secrets：`ACR_USERNAME`、`ACR_PASSWORD`（镜像推送）；`ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET`（最小权限部署身份）；使用临时凭据时增加 `ALIBABA_CLOUD_SECURITY_TOKEN`。本实现直接支持 AK/STS 环境变量，不宣称已经接入 GitHub OIDC 信任交换。禁止配置阿里云主账号密钥。

`FC_RUNTIME_JSON` 保存按 `deploy/fc/runtime.example.json` 填写的完整运行 JSON，不能只复制示例占位值。`common` 是服务共享环境变量，`services` 按服务覆盖；模型密钥仅放在 `services.ai-engine`。配置文件不得提交到 Git，也不写入前端构建变量。域名、支付/短信/登录密钥按实际启用功能补齐。创建 GitHub `production` Environment，部署分支限制为 main。

后台需要已有 MySQL/RDS、Redis、VPC/vSwitch/安全组以及 NAS。当前 APK 上传功能仍使用本地路径，为保持既有功能，必须将 `APP_RELEASE_UPLOAD_DIR` 放在配置的 NAS 挂载目录下；旧文件单独复制并校验。Web 产品暂不运营移动服务不等于可以丢弃已有文件。作品主体与生成产物沿用数据库/现有对象存储策略，临时浏览器工作目录不作为持久存储。OSS/CDN 可后续迁移，不能直接删除历史对象或凭据。

部署身份需要对本项目前缀函数执行 Get/Create/UpdateFunction、Get/CreateTrigger、PublishFunctionVersion、Put/GetProvisionConfig、PutConcurrencyConfig，以及传递执行角色所需权限。按官方 RAM 文档限定资源与 PassRole；函数角色不授予发布权限。SDK 发布过程不回显完整请求/环境变量。运行凭据存储在受权限控制的函数配置中，本次不自动创建 KMS、RDS、NAS 或付费资源。

## 内部调用与域名

函数 HTTP 触发器提供真实 HTTPS URL；内部 API 使用 `FC_INTERNAL_TOKEN` 校验传输层共享凭据，业务 JWT/管理员鉴权仍保留。令牌为 32 随机字节的 64 位十六进制字符串。Node 服务通过 FC 专用 preload、AI 通过 ASGI 边界验证请求；不携带凭据的直连请求返回 403。公开健康路径不含敏感配置。服务间发送凭据只允许部署器注入的精确 origin，并禁止内部凭据随重定向流出。FC 运行凭据禁止通过请求头注入。

公共 gateway 不开放 /__fc/、/api/v1/internal/、/api/v1/ai/；用户创作走现有创建会话 API。内部 AI WebSocket 不对公网开放。进度使用现有会话 SSE / 游戏 Socket.IO 与持久化轮询。FC、域名入口的流式响应、连接时长和断线恢复必须在目标地域验收；构建/健康检查通过不证明这些功能全部通过。

在 FC 自定义域名控制台绑定：应用域名所有路径 → gateway 的 LATEST；独立作品内容域名所有路径 → content 的 LATEST。配置 HTTPS 证书和 DNS。发布器不擅自修改生产域名、证书或 DNS。保持不可信作品 origin 与账户 origin 分离。

## 首次发布

1. 每个环境使用独立数据库/Redis 库；切换前停止旧环境针对同一队列的消费者并排空在途任务，避免旧实例与 FC 同时处理。准备数据库并单独验证 Prisma 初始化/迁移；已有数据库禁止当空库执行 db push。配置 ACR 的 frontend、user-service、game-service、social-service、feed-service、ai-engine、gateway 镜像仓库。
2. 先部署前端函数（其 API 构建变量使用最终应用域名），从 `fc-release-<SHA>` 工件获取 frontend URL，填入后端 `FC_FRONTEND_URL`。
3. 启用新开关并手动运行后端发布工作流。发布器先创建禁止按需实例的函数、获取触发器地址，再注入真实依赖地址并启动资源。已有函数的运行配置不会在地址发现阶段被临时占位覆盖。
4. 发布器等待 FC 的 Active / Successful 状态，校验后台预留资源与 HTTP 健康，再记录版本和 URL。任务管理器启用 FC 时要求 Redis 配置且启动 ping 成功。
5. 检查并切换后台 Provider 的实际执行目标，验证登录、创作、暂停/恢复、预览、发布、流式连接与已启用支付/短信功能，然后绑定/切换正式域名。首次云端业务验证尚需用户账号与真实资源。

本地只验证配置（无云端变更）：

```bash
python -m pip install -r scripts/fc/requirements.txt
python scripts/fc/deploy.py validate --runtime .fc-runtime.json
```

执行时需要上表的环境变量，包括完整 40 位 `IMAGE_TAG`。`validate` 使用 SDK 模型校验但不能代替 FC 侧地域/配额/权限与网络检查。

## 更新与回滚

后端发布先通过受内部凭据保护的 /__fc/drain 设置 Redis 维护标记，阻止新任务入队，等待队列中的生成/迭代任务完成（最多约 40 分钟，连续三次空队列）。等待失败不更新函数；完成或失败后尝试解除维护。维护标记 TTL 为两小时，工作流被强制终止时需核验任务和发布状态再主动恢复，不立即再次部署。

每个已有函数更新前发布检查点版本，记录在无密钥的 `fc-release.json`。更新是 LATEST 原地更新，可能短暂中断连接，并非零停机蓝绿部署。失败时尝试恢复已更新函数的原版本配置。新建函数没有旧版本：停止预留容量、禁止按需实例，保留资源诊断，不自动删除。自动恢复失败会明确报错。

下载对应发布工件后手动回滚：

```bash
python scripts/fc/deploy.py rollback --runtime .fc-runtime.json --release fc-release.json
```

回滚会检查账号、地域、前缀与在途任务，恢复记录中的上一函数版本。函数版本包含当时环境变量，因此密钥轮换后必须先审查回滚目标；回滚不回退数据库、NAS 文件或外部 Provider 配置。不自动清理旧版本、镜像或持久数据。

## 验证依据

- [FC 自定义容器](https://www.alibabacloud.com/help/en/functioncompute/create-a-custom-container-function-in-a-container-runtime)
- [FC 预留实例 API](https://help.aliyun.com/en/functioncompute/fc/developer-reference/api-fc-2023-03-30-putprovisionconfig)
- [函数状态与更新完成条件](https://help.aliyun.com/zh/functioncompute/states-of-custom-container-functions)
- [FC 3.0 函数字段](https://help.aliyun.com/zh/functioncompute/api-fc-2023-03-30-struct-function)
