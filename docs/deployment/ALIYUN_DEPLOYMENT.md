# GameVallies：阿里云 FC + OSS 代码包发布

## 发布方式

GitHub Actions 在 Debian 12 / linux/amd64 构建 ZIP，上传现有私有 OSS Bucket，再用 FC 3.0 官方 SDK 4.8.2 更新 `custom.debian12` 函数。Docker 仅作为构建与隔离测试工具，不登录、不推送、不部署 ACR 镜像。无需 ECS、ACR，也不需要手工维护 `FC_RUNTIME_JSON`。

前端为桌面 Web 创意平台。内部 `build:h5` 是现有 Taro Web 构建命令，不代表新增移动 H5 产品。

代码目录：`deploy/fc/`、`scripts/fc/`。ZIP 根目录含可执行 `bootstrap`；Node/Python/Nginx 与所需本地库随包分发。AI 包含 Chromium；CI 在干净 Debian 12 中解压并真实启动浏览器，以发现构建镜像掩盖的依赖缺失。深圳包体限制 500 MB，超限停止发布，不自动切换 ACR。若后续增大，应将依赖拆为 FC 层。

OSS 对象路径：`gamevallies/prod/releases/<commit SHA>/<SHA256>/<function>.zip`。禁止覆盖已有对象；重试核对元数据摘要与大小。不要让生命周期规则清理仍用于回滚的发布包。业务上传路径：`gamevallies/prod/app-releases/...`。Bucket 保持私有，下载使用短期签名 URL。

## GitHub 配置位置

两个仓库分别打开 Settings → Environments → **Aliyun** → Environment secrets / variables。大小写和工作流一致。不需要 `services → game-service` 页面，那是旧 JSON 配置层级。Secrets 的值无法从 GitHub API 回读；截图只能核验名称，实际有效性需工作流和部署验证。

### 两库共用的 Variables

| 名称 | 值或用途 |
| --- | --- |
| `FC_ACCOUNT_ID` | `1318350152273303` |
| `FC_REGION` | `cn-shenzhen`（默认） |
| `FC_PREFIX` | `gamevallies-prod`（默认） |
| `FC_EXECUTION_ROLE` | 已授权的 FC 执行角色完整 ARN，必填 |
| `ALIYUN_OSS_BUCKET` | `clawworks-server-staging-1318350152273303`；注意末尾是 `BUCKET`，不是 `BUCKE` |
| `ALIYUN_OSS_REGION` | `cn-shenzhen`（默认） |
| `ALIYUN_OSS_ENDPOINT` | `https://oss-cn-shenzhen.aliyuncs.com`（默认） |
| `ALIYUN_OSS_PREFIX` | `gamevallies/prod/`（默认） |
| `OBJECT_STORAGE_PROVIDER` | `aliyun-oss`（默认） |
| `PUBLIC_ORIGIN` | `https://zlspace.clawworks.cn`（默认） |
| `CONTENT_ORIGIN` | 独立作品 HTTPS origin，必填；例如确认 DNS/证书后使用 `https://zlspace-content.clawworks.cn` |
| `ALIYUN_FC_DEPLOY_ENABLED` | 首次验收前保持 `false` 或不设置；`true` 允许 main push 自动发布 |

### 两库共用的 Secrets

| 名称 | 用途 |
| --- | --- |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` | 部署身份 AK |
| `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 部署身份 SK |
| `ALIBABA_CLOUD_SECURITY_TOKEN` | 可选；临时凭据时必填 |

### 仅后端的 Variables

| 名称 | 用途 |
| --- | --- |
| `FC_FRONTEND_URL` | 前端 FC 发布后输出的真实 HTTPS 触发器 origin；不能填应用域名，否则网关可能循环代理 |
| `FC_VPC_ID` | 已有 VPC ID |
| `FC_VSWITCH_IDS` | 同 VPC 的交换机 ID，多个用逗号分隔 |
| `FC_SECURITY_GROUP_ID` | 允许连接共享 MySQL/Redis 的安全组 ID |
| `FC_LOG_PROJECT` / `FC_LOG_STORE` | 可选；已有 SLS 项目和 Logstore，成对填写 |
| `LLM_BASE_URL` / `LLM_MODEL` | 实际模型 API 地址及模型名 |

### 仅后端的 Secrets

| 名称 | 用途 |
| --- | --- |
| `DATABASE_URL` | GameVallies 独立数据库/账号连接串，密码中的特殊字符需 URL 编码 |
| `REDIS_URL` | 专用逻辑库连接串，避免与 ClawWorks 队列键冲突；核验现有 Redis 版本与 BullMQ 兼容性 |
| `JWT_SECRET` / `JWT_REFRESH_SECRET` | 独立高强度随机签名密钥 |
| `ADMIN_TOKEN` | 后台访问令牌 |
| `FC_INTERNAL_TOKEN` | 64 位十六进制随机内部通信密钥 |
| `LLM_API_KEY` | 模型密钥，只注入 AI 引擎 |
| `ALIYUN_OSS_ACCESS_KEY_ID` / `ALIYUN_OSS_ACCESS_KEY_SECRET` | 业务 OSS 最小权限身份，只注入 game-service |

启用短信、微信/支付宝等功能前，需要另外按业务配置适配其独立字段；本发布配置没有默认启用这些集成。不要把所有 GitHub Secrets 整体转发给函数或前端。

## 权限与复用边界

部署身份需要对目标前缀函数的读取、创建、更新、发布版本、HTTP 触发器、并发和预留实例管理权限，以及对上述 OSS releases 前缀的 PutObject/GetObject 权限；重复发布校验使用 HeadObject（对应 GetObject）。配置角色需要相应 PassRole 权限。执行角色的信任主体为 FC，按已配置 VPC/日志和代码读取需求授权。应用 OSS 身份仅能读写业务前缀，不能访问 ClawWorks 私有对象或删除发布包。

不创建或改动共享 MySQL/Redis 资源。部署脚本不执行数据库迁移：首次建表、已有数据迁移及 Redis 兼容性需在独立 GameVallies 库上单独验证，不能对 ClawWorks 执行 schema push。

首次部署无需 NAS；已有挂载不会被自动移除。历史 local/TOS 下载记录仍兼容，历史文件必须迁移并核验后再停用旧存储。新 OSS 配置不完整或写入失败直接报错，不回退 FC 临时磁盘。作品 HTML 的现有数据库持久化和独立内容函数继续使用，未将私有 OSS URL 误当公共作品 URL。

## 发布顺序

1. 配齐 Aliyun 环境。先运行 Actions → Deploy OSS packages to Alibaba FC，保留 `apply=false`：检查参数、构建和校验，不写云资源。
2. 前端主干运行 `apply=true`，记录 `fc-release.json` 中 frontend 的实际 HTTPS URL，并填入后端 `FC_FRONTEND_URL`。
3. 后端同样先校验，再以 `apply=true` 发布。脚本发现真实内部函数 URL，注入内部鉴权，检查 HTTP 健康并记录版本。
4. game-service 与 ai-engine 各保留 1 个始终分配 CPU 的预留实例，禁止按需副本；这两项有持续费用，当前 BullMQ/后台 AI 架构不能直接缩到零。
5. 以真实流程验收登录、一次创作、进度、取消/恢复、预览、发布、下载和实例重启后数据。独立内容 origin、SSE/WebSocket 和业务回调需实际验证；健康检查通过不等于业务验收通过。
6. 将应用域名绑定 gateway、作品域名绑定 content，完成 HTTPS 与 DNS；此代码改造不会自动修改域名或切流。验收后再选择开启自动发布开关。

当前 FC HTTP 路由中内部管理/AI RPC 均经过令牌或网关拒绝规则。作品与登录页面必须保持独立 origin。

## 回滚

发布前记录函数版本，先阻止新任务并等待已提交任务排空，再更新函数。失败自动恢复已修改函数的前一版；已有代码包函数通过版本描述中的 OSS Bucket/Object 恢复代码，不能只恢复 GetFunction 返回的配置。首次从自定义容器迁移仍支持恢复原镜像；不要提前删除旧镜像。

手动回滚：使用失败发布对应的 `fc-release.json`、同账号/地域/前缀及独立参数重新生成私有配置，再执行 `python scripts/fc/deploy.py rollback --runtime "$config" --release fc-release.json`。保留原提交及 ZIP；无上一版本的首次安装不能回滚成不存在的函数。未知来源、未记录 OSS 引用的现有代码函数会在修改前停止，需先导出并保留其代码。

临时运行配置权限 0600，退出时清除，不上传 Actions Artifact。发布记录只含版本、函数名与 URL；不要将函数环境变量导出到公开日志。

## 官方参考

- [创建 Web 函数与 OSS 代码上传](https://www.alibabacloud.com/help/en/functioncompute/creating-a-web-function)
- [FC 配额与代码包限制](https://www.alibabacloud.com/help/en/functioncompute/fc/product-overview/limits-of-usage)
- [自定义运行时](https://www.alibabacloud.com/help/en/functioncompute/custom-runtime/)
