# LLM 网关、多 Region 部署与异步任务管理设计

> 版本：v1.1  
> 日期：2026-03-22  
> 状态：方案细化版，待实现

---

## 一、目标与范围

本文档定义以下 4 项能力的详细设计，并细化到数据库字段、接口体与后台管理口径：

1. 在 `ai-engine` 服务内建设 LLM Gateway 子服务
2. 游戏生成错误日志下钻到最末端 LLM 调用错误
3. 游戏生成支持持久化异步任务管理，并向前端暴露标准接口
4. 云厂商 Region 目录、`ai-engine` Region 部署目标与 Provider Region 绑定

本文档只描述设计，不包含代码实现。

---

## 二、现状与问题

当前仓库的相关实现边界如下：

- `ai-engine` 仍是单一环境变量模型配置：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_FAST_MODEL`
- `ai-engine` 异步任务仅为进程内存态，不适合多实例、重启恢复、跨 Region
- `game-service` 后台已有管理面板与 `system_configs`，但还没有 LLM 网关池、步骤路由、任务与 LLM 调用日志页面
- 失败日志当前能落到任务级摘要，但不能稳定回溯到“具体哪个 LLM、哪个 Region、哪个 endpoint、返回了什么错误”
- Provider 的 `Region` 当前仍是手填字符串，没有和云厂商实际可用 Region、`ai-engine` 实际部署目标绑定
- `scripts/deploy.py` 当前仍是单 Region 口径，不能根据后台配置把 `ai-engine` 真正部署到指定云 Region

因此本次设计的核心原则是：

- 配置中心化：LLM Provider、步骤路由、全局超时都落 MySQL
- 运行时热生效：新任务立即使用新配置，运行中任务使用创建时快照
- 多 Region：国内走上海，海外走柔佛，支持同 Region 优先、跨 Region 兜底
- 可观测：任务日志和 LLM 调用日志可在后台直接查看
- 前端友好：生成全链路统一走异步任务接口
- 云 Region 可发现：Provider 表单中的 Region 必须来自云厂商 Region 目录，不允许手填
- 部署目标显式化：Provider 只能绑定到“已经定义好的 `ai-engine` Region 部署目标”
- 部署与运行打通：`ai-engine` 的具体云 Region 部署结果要能回写为运行时可用的 Region Target 与内网地址

---

## 三、术语与枚举

### 3.1 CloudVendor 枚举

一期实现固定：

| 值 | 说明 |
|------|------|
| `volcengine` | 火山引擎 |

### 3.2 CloudRegionCode 枚举

说明：

- `CloudRegionCode` 是云厂商原始 Region 编码，用于 SDK、VCR、VeFaaS、APIG 调用
- 一期以火山引擎 Region 编码为准

| 值 | 说明 |
|------|------|
| `cn-shanghai` | 火山引擎上海 Region |

### 3.3 ExecutionRegion 枚举

说明：

- `ExecutionRegion` 是系统内部运行时 Region 语义
- `llm_gateway_providers.region`、`generation_tasks.region`、步骤路由中的 `region` 都使用该枚举
- `ExecutionRegion` 与 `CloudRegionCode` 通过 `ai_engine_region_targets` 做映射，不直接混用

| 值 | 说明 |
|------|------|
| `cn_shanghai` | 国内 `ai-engine` 执行 Region，当前映射火山上海 |

说明：

- 一期不再支持 `global`
- 每个 Provider、任务、步骤绑定都必须落到一个具体 `execution_region`
- 若未来需要“全局默认 Provider”，应通过复制配置到多个具体 Region 实现，而不是引入运行时含糊的 `global`

### 3.4 ProviderType 枚举

一期实现支持：

| 值 | 说明 |
|------|------|
| `openai_compatible` | OpenAI 兼容协议，如 MiniMax |
| `anthropic` | Claude 原生协议 |

为后续保留但一期不落地：

| 值 | 说明 |
|------|------|
| `azure_openai` | Azure OpenAI |
| `custom_http` | 自定义 HTTP 网关 |

### 3.5 StepKey 枚举

只对真正调用 LLM 的步骤建路由，不为纯规则步骤单独建模型配置。

| StepKey | 所属阶段 | 说明 |
|------|------|------|
| `dialogue.slot_extract` | Stage 01 | 对话槽位提取 |
| `dialogue.reply` | Stage 01 | 对话回复生成 |
| `intent_parse` | Stage 02 | 文本转 `GameSpec` |
| `code_generate.hybrid` | Stage 05 | 模板混合 LLM 生成首版代码 |
| `code_generate.full` | Stage 05 | 全量依赖 LLM 生成首版代码 |
| `qa_fix` | Stage 06 | QA 自动修复 |
| `code_review` | Stage 06 | LLM 代码审查 |
| `iterate.classify` | Stage 07 | 反馈分类 |
| `iterate.param_adjust` | Stage 07 | 迭代参数调整 |
| `iterate.element_change` | Stage 07 | 迭代元素修改 |
| `iterate.mechanic_change` | Stage 07 | 迭代机制改写 |

说明：

- `StepKey` 必须与系统内置步骤目录 `llm_step_catalog` 完全一致
- 该目录由系统启动时 seed，后台不允许手工录入自由文本 `stepKey`
- 后台步骤绑定页的下拉和列表必须直接读取 `llm_step_catalog`

### 3.6 TaskStatus 枚举

| 值 | 说明 |
|------|------|
| `queued` | 已创建，未开始执行 |
| `running` | 执行中 |
| `succeeded` | 成功完成 |
| `failed` | 失败结束 |
| `canceled` | 用户或后台取消 |
| `timed_out` | 总任务超时 |

### 3.7 TaskType 枚举

| 值 | 说明 |
|------|------|
| `pipeline_run` | 首次生成 |
| `pipeline_iterate` | 迭代修改 |

---

## 四、总体架构

### 4.1 服务分工

- `game-service`
  - 对外业务入口
  - 负责后台管理页面
  - 负责创建游戏记录与生成任务入口
  - 负责云账号、云 Region 目录和 `ai-engine` Region 部署目标的配置与展示
  - 负责异步任务主编排、任务状态落库、失败补偿与业务结果收口
  - 负责给前端返回任务状态、事件流和错误摘要
  - 负责 WebSocket 推送
- `ai-engine`
  - 负责 LLM Gateway 运行时
  - 负责按步骤路由选择具体 LLM Provider
  - 负责执行生成流水线
  - 负责回传进度事件和最末端 LLM 调用日志
  - 负责返回最终生成结果或最终失败结果
- `scripts/deploy.py`
  - 作为多 Region 部署执行器
  - 负责读取 `ai_engine_region_targets`
  - 负责把 `ai-engine` 发布到目标云 Region
  - 负责回写部署状态、内网 `ai_engine_url`、revision、image tag

补充说明：

- 当前现有代码里，用户侧实时进度已经是 `ai-engine -> game-service internal progress -> GameWebSocketGateway` 这条链路
- 当前现有代码里，bundle 落库、游戏状态更新、失败补偿、额度退回都在 `game-service`
- 因此一期方案保持这个中心不变，避免把任务编排和业务状态拆成两套主源

任务归属口径：

- `generation_tasks`、`generation_task_events`、`llm_call_logs` 主写服务为 `game-service`
- `game-service` 负责创建任务、调用 `ai-engine`、接收内部回调、落库任务与日志、收口业务结果
- `ai-engine` 不直接写业务数据库中的任务表，只通过内部接口把进度、路由快照和 LLM 调用日志回传给 `game-service`
- `ai-engine` 读取 LLM 网关配置时，对数据库保持“读多写少”模式，和当前 `prompt_store` 的接入方式保持一致

### 4.2 多 Region 部署

一期部署形态固定为 2 个 `ExecutionRegion`：

| ExecutionRegion | CloudVendor | CloudRegionCode | Function Name | 说明 |
|------|------|------|------|------|
| `cn_shanghai` | `volcengine` | `cn-shanghai` | `gv-ai-engine-cn` | 国内主实例 |

`game-service` 请求 `ai-engine` 时的 Region 选择顺序：

1. 请求体 `regionHint`
2. 请求头 `x-gv-region`
3. 用户所在站点环境
4. 服务自身 `SERVICE_REGION`

同一个任务一旦创建，`generation_tasks.region` 固定，不在运行中切 Region；如果步骤路由允许跨 Region 兜底，只允许在同一任务内部对单次 LLM 调用做 fallback，并且必须写日志。

### 4.2.1 与当前部署脚本对齐的前置改造

当前仓库的部署脚本和 `.env.deploy` 仍是单 Region 口径，因此一期实施前需要先补齐以下部署执行前置项：

| 变量 | 说明 |
|------|------|
| `DATABASE_URL` | 部署脚本读取与回写 `ai_engine_region_targets` |
| `CLOUD_PROVIDER_ACTIVE_VENDOR` | 当前激活云厂商，一期固定 `volcengine` |
| `VOLCENGINE_ACCESS_KEY` | 火山引擎 AK |
| `VOLCENGINE_SECRET_KEY` | 火山引擎 SK |
| `VOLCENGINE_DEFAULT_REGISTRY_NAMESPACE` | 默认镜像命名空间 |
| `VOLCENGINE_DEFAULT_VPC_ID` | 默认 VPC |
| `VOLCENGINE_DEFAULT_SUBNET_ID` | 默认子网 |
| `VOLCENGINE_DEFAULT_SECURITY_GROUP_ID` | 默认安全组 |

说明：

- `scripts/deploy.py` 不再以单个 `VOLCENGINE_REGION` 作为 `ai-engine` 发布真源
- `ai-engine` 的多 Region 目标改由 `ai_engine_region_targets` 驱动
- `AI_ENGINE_URL_CN_SHANGHAI` 不再作为主输入手填，而是部署成功后回写到 `ai_engine_region_targets.ai_engine_url`

### 4.2.2 Provider 的 Region 语义

`llm_gateway_providers.region` 的语义重新定义为：

- 不是大模型厂商自己的机房地域
- 不是云厂商原始 Region 代码
- 而是 Provider 归属的系统内部 `ExecutionRegion`

Provider 创建时前端不再直接提交 `region`，而是提交 `regionTargetId`：

1. 后台从 `ai_engine_region_targets` 读取部署目标
2. 自动得到：
   - `execution_region`
   - `cloud_vendor`
   - `cloud_region_code`
   - `ai_engine_url`
3. 保存 Provider 时把这些值派生写入 Provider 记录

因此 Provider 的 Region 下拉来源必须是：

- 云厂商 Region 目录中可用于 `ai-engine` 的 Region
- 且已经在本系统中配置成 `ai_engine_region_targets`
- 且目标状态至少为 `deployed`

### 4.2.3 Region 选择与真实部署的联动流程

完整链路如下：

1. 管理后台通过云厂商 adapter 同步 `cloud_region_catalog`
2. 管理员在后台把某个 `CloudRegionCode` 配置为一个 `ai_engine_region_target`
3. 管理员为该 target 指定：
   - `execution_region`
   - `function_name`
   - `registry`
   - `registry_namespace`
   - `deploy_enabled`
4. 执行 `scripts/deploy.py ai-engine --all-enabled-region-targets`
5. 部署脚本逐个读取 `ai_engine_region_targets`
6. 对每个 target：
   - 使用 target 对应的 `cloud_region_code` 初始化火山 SDK
   - 使用 target 对应的镜像仓库与函数名发布 `ai-engine`
   - 注入 `SERVICE_REGION=<execution_region>`
7. 发布成功后，部署脚本回写：
   - `deploy_status`
   - `last_revision`
   - `last_image_tag`
   - `ai_engine_url`
   - `last_deployed_at`
8. 只有 `deploy_status=deployed` 的 target 才允许在 Provider 创建表单中被选择

结论：

- 选择 Provider 的 Region 不会隐式触发部署
- Provider 只能绑定到“已经存在并已部署”的 `ai-engine` Region Target
- 真正的云 Region 部署动作由 `scripts/deploy.py` 完成

说明：

- `scripts/deploy.py` 当前只有一个 `VOLCENGINE_REGION` 和一个 `AI_ENGINE_URL`，实施前需要扩展成双目标发布
- 这部分属于部署能力前置项，不在业务 API 范围内，但必须在实施计划中提前完成

### 4.3 热生效原则

- Provider 配置和步骤路由保存后，立即写入 DB
- 后台保存成功后递增 `system_configs.llm.gateway.config_version`
- 后台再调用 `ai-engine` 两个 Region 的内部刷新接口
- `ai-engine` 本地缓存同时保留 10 秒 TTL 拉取兜底

热生效边界：

- 新建任务：立即使用新配置
- 单个生成请求：`ai-engine` 在请求开始时一次性解析当前路由并固定在内存中，不在该请求中途重新拉配置
- `game-service` 在接收到首个进度回调时，把本次请求实际命中的 `gatewayConfigVersion + routeSnapshot` 落到 `generation_tasks`
- 因此同一个任务在执行过程中不会因为后台保存新配置而切换模型，但后续新任务会立即使用新配置

---

## 五、数据库设计

### 5.1 复用 `system_configs` 的全局配置项

本次不新增“全局配置表”，而是复用现有 `system_configs`。

新增约定 Key 如下：

| `config_key` | `config_value` 类型 | 默认值 | 说明 |
|------|------|------|------|
| `cloud.active_vendor` | string | `volcengine` | 当前激活云厂商 |
| `cloud.region_catalog_refresh_ttl_s` | string(int) | `3600` | 云 Region 目录缓存秒数 |
| `llm.gateway.config_version` | string(int) | `1` | 每次 Provider 或步骤路由变更时递增 |
| `llm.gateway.cache_ttl_s` | string(int) | `10` | `ai-engine` 读取 DB 配置的缓存秒数 |
| `llm.gateway.default_request_timeout_s` | string(int) | `600` | 单次 LLM 调用默认超时 |
| `llm.gateway.default_connect_timeout_s` | string(int) | `15` | 单次 LLM 连接超时 |
| `generation.task.default_timeout_s` | string(int) | `600` | 生成任务总超时默认值 |
| `generation.task.max_timeout_s` | string(int) | `3600` | 前端可传的任务超时上限 |
| `generation.task.log_retention_days` | string(int) | `30` | 任务事件与 LLM 日志保留天数 |

---

### 5.1.1 表：`cloud_provider_accounts`

用途：存储当前系统可用的云厂商账号与部署默认参数。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 主键 UUID |
| `vendor` | `ENUM('volcengine')` | 是 | `IDX(vendor, enabled)` | 云厂商 |
| `account_key` | `VARCHAR(64)` | 是 | `UNIQUE` | 稳定账号键 |
| `display_name` | `VARCHAR(128)` | 是 |  | 后台展示名称 |
| `enabled` | `BOOLEAN` | 是 | `IDX(vendor, enabled)` | 是否启用 |
| `credential_mode` | `ENUM('env_secret','secret_manager')` | 是 |  | 凭据来源 |
| `credential_ref_json` | `JSON` | 否 |  | 凭据引用，不存明文 AK/SK |
| `default_registry` | `VARCHAR(255)` | 否 |  | 默认镜像仓库地址 |
| `default_registry_namespace` | `VARCHAR(128)` | 否 |  | 默认镜像命名空间 |
| `default_vpc_id` | `VARCHAR(64)` | 否 |  | 默认 VPC |
| `default_subnet_id` | `VARCHAR(64)` | 否 |  | 默认子网 |
| `default_security_group_id` | `VARCHAR(64)` | 否 |  | 默认安全组 |
| `region_sync_mode` | `ENUM('vendor_api','seed_and_verify')` | 是 |  | Region 同步模式 |
| `last_region_sync_status` | `ENUM('idle','success','failed')` | 是 |  | 最近一次同步状态 |
| `last_region_sync_error` | `VARCHAR(1024)` | 否 |  | 最近一次同步错误 |
| `last_region_sync_at` | `DATETIME(3)` | 否 |  | 最近一次同步时间 |
| `created_at` | `DATETIME(3)` | 是 |  | 创建时间 |
| `updated_at` | `DATETIME(3)` | 是 |  | 更新时间 |

约束：

- 一期不在数据库中保存明文 AK/SK
- 真正的云凭据从服务端环境变量或 Secret Manager 获取

### 5.1.2 表：`cloud_region_catalog`

用途：存储从云厂商同步得到的 Region 目录。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 主键 UUID |
| `account_id` | `VARCHAR(36)` | 是 | `IDX(account_id, region_code)` | 对应云账号 |
| `vendor` | `ENUM('volcengine')` | 是 | `IDX(vendor, region_code)` | 云厂商 |
| `region_code` | `VARCHAR(64)` | 是 | `UNIQUE(account_id, region_code)` | 云厂商原始 Region 编码，如 `cn-shanghai` |
| `region_name` | `VARCHAR(128)` | 是 |  | 展示名称，如“上海” |
| `region_group` | `VARCHAR(32)` | 否 |  | 分组，如 `cn_mainland` / `overseas` |
| `vcr_supported` | `BOOLEAN` | 是 |  | 是否支持镜像仓库 |
| `vefaas_supported` | `BOOLEAN` | 是 |  | 是否支持 VeFaaS |
| `apig_supported` | `BOOLEAN` | 是 |  | 是否支持 APIG |
| `deploy_supported` | `BOOLEAN` | 是 | `IDX(vendor, deploy_supported)` | 是否可用于 `ai-engine` 部署 |
| `enabled` | `BOOLEAN` | 是 | `IDX(account_id, enabled)` | 是否启用 |
| `raw_meta_json` | `JSON` | 否 |  | 云厂商原始元数据摘录 |
| `synced_at` | `DATETIME(3)` | 是 |  | 本条记录最近同步时间 |
| `created_at` | `DATETIME(3)` | 是 |  | 创建时间 |
| `updated_at` | `DATETIME(3)` | 是 |  | 更新时间 |

说明：

- 若云厂商没有统一“列出所有 Region”的公开 API，一期允许采用 `seed_and_verify`
- `seed_and_verify` 表示先用内置 Region 种子，再结合云厂商能力校验补全 `deploy_supported`

### 5.1.3 表：`ai_engine_region_targets`

用途：存储本系统实际要部署 `ai-engine` 的 Region 目标，是 Provider Region 下拉的直接数据来源。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 主键 UUID |
| `account_id` | `VARCHAR(36)` | 是 | `IDX(account_id)` | 对应云账号 |
| `region_catalog_id` | `VARCHAR(36)` | 是 | `IDX(region_catalog_id)` | 对应云 Region 目录项 |
| `vendor` | `ENUM('volcengine')` | 是 | `IDX(vendor, execution_region)` | 云厂商 |
| `cloud_region_code` | `VARCHAR(64)` | 是 | `IDX(vendor, cloud_region_code)` | 云厂商原始 Region 编码 |
| `execution_region` | `ENUM('cn_shanghai')` | 是 | `UNIQUE(execution_region)` | 系统内部执行 Region |
| `display_name` | `VARCHAR(128)` | 是 |  | 如“AI Engine 上海主实例” |
| `function_name` | `VARCHAR(128)` | 是 | `UNIQUE` | VeFaaS 函数名 |
| `registry` | `VARCHAR(255)` | 是 |  | 该 Region 使用的镜像仓库 |
| `registry_namespace` | `VARCHAR(128)` | 是 |  | 镜像命名空间 |
| `image_repository` | `VARCHAR(128)` | 是 |  | 镜像仓库内 repo 名 |
| `service_region_env` | `VARCHAR(32)` | 是 |  | 注入容器的 `SERVICE_REGION` |
| `ai_engine_url` | `VARCHAR(255)` | 否 |  | 部署完成后的内网 APIG 地址 |
| `deploy_enabled` | `BOOLEAN` | 是 | `IDX(deploy_enabled, deploy_status)` | 是否纳入部署 |
| `deploy_status` | `ENUM('pending','ready','deployed','failed','disabled')` | 是 | `IDX(deploy_status, updated_at)` | 当前部署状态 |
| `last_revision` | `VARCHAR(64)` | 否 |  | 最近 revision |
| `last_image_tag` | `VARCHAR(128)` | 否 |  | 最近镜像 tag |
| `last_release_status` | `VARCHAR(64)` | 否 |  | 最近 release 状态 |
| `last_deploy_error` | `VARCHAR(1024)` | 否 |  | 最近部署错误 |
| `last_deployed_at` | `DATETIME(3)` | 否 |  | 最近成功部署时间 |
| `created_at` | `DATETIME(3)` | 是 |  | 创建时间 |
| `updated_at` | `DATETIME(3)` | 是 |  | 更新时间 |

约束：

- 一期每个 `execution_region` 只允许存在一个启用的 target
- 只有 `deploy_status='deployed'` 的 target 才允许在 Provider 创建表单中被选择

### 5.2 表：`llm_gateway_providers`

用途：存储 LLM 网关池中的可选 Provider。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | Provider 主键 UUID |
| `provider_key` | `VARCHAR(64)` | 是 | `UNIQUE` | 稳定配置键，如 `minimaxi_sh_primary` |
| `display_name` | `VARCHAR(128)` | 是 |  | 后台展示名称 |
| `provider_type` | `ENUM` | 是 | `IDX(provider_type, enabled)` | 取值见 `ProviderType` |
| `region_target_id` | `VARCHAR(36)` | 是 | `IDX(region_target_id)` | 绑定的 `ai_engine_region_targets.id` |
| `cloud_vendor` | `ENUM('volcengine')` | 是 | `IDX(cloud_vendor, region)` | 云厂商 |
| `cloud_region_code` | `VARCHAR(64)` | 是 | `IDX(cloud_vendor, cloud_region_code)` | 云厂商原始 Region 编码 |
| `region` | `ENUM` | 是 | `IDX(region, enabled)` | 逻辑含义为 `execution_region`，不是云厂商原始 Region |
| `base_url` | `VARCHAR(255)` | 是 |  | Provider 基础地址 |
| `api_path` | `VARCHAR(128)` | 是 |  | 默认 `/chat/completions`，Anthropic 可为空或自定义 |
| `auth_mode` | `ENUM('bearer','x_api_key','custom_header','anthropic')` | 是 |  | 鉴权方式 |
| `auth_header_name` | `VARCHAR(64)` | 否 |  | `custom_header` 时使用 |
| `api_key_ciphertext` | `LONGTEXT` | 是 |  | 加密后的密钥，不明文落库 |
| `api_key_masked` | `VARCHAR(32)` | 是 |  | 后台展示的脱敏值，如 `sk-****abcd` |
| `model_default` | `VARCHAR(128)` | 是 |  | 主模型名 |
| `model_fast` | `VARCHAR(128)` | 否 |  | 快速模型名 |
| `request_timeout_s` | `INT` | 是 |  | 单次 LLM 调用默认超时，默认 `600` |
| `connect_timeout_s` | `INT` | 是 |  | 连接超时，默认 `15` |
| `max_retries` | `INT` | 是 |  | Provider 级额外重试次数 |
| `priority` | `INT` | 是 | `IDX(region, priority)` | 池内排序，数值越小优先级越高 |
| `enabled` | `BOOLEAN` | 是 | `IDX(region, enabled)` | 是否启用 |
| `health_status` | `ENUM('unknown','healthy','degraded','unhealthy')` | 是 |  | 最近一次测试后的健康状态 |
| `last_test_success` | `BOOLEAN` | 否 |  | 最近一次连接测试结果 |
| `last_test_latency_ms` | `INT` | 否 |  | 最近一次连接测试耗时 |
| `last_test_error` | `VARCHAR(512)` | 否 |  | 最近一次连接测试错误摘要 |
| `last_test_at` | `DATETIME(3)` | 否 |  | 最近一次连接测试时间 |
| `extra_headers_json` | `JSON` | 否 |  | 额外请求头 |
| `meta_json` | `JSON` | 否 |  | 非核心元数据 |
| `created_at` | `DATETIME(3)` | 是 |  | 创建时间 |
| `updated_at` | `DATETIME(3)` | 是 |  | 更新时间 |

约束：

- `provider_key` 全局唯一
- `enabled=false` 的 Provider 不参与步骤路由选择
- `api_key_ciphertext` 必须使用服务端配置密钥加密
- `region_target_id` 必须指向 `deploy_status='deployed'` 的 target
- Provider 创建和更新时，`cloud_vendor`、`cloud_region_code`、`region` 均由 `region_target_id` 自动派生，前端不得手填

---

### 5.3 表：`llm_step_routes`

用途：存储“某个步骤在某个 Region 下如何选模型”的主路由配置。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 路由主键 UUID |
| `step_key` | `VARCHAR(64)` | 是 | `UNIQUE(step_key, region)` | 取值见 `StepKey` |
| `region` | `ENUM` | 是 | `UNIQUE(step_key, region)` | 取值见 `Region` |
| `enabled` | `BOOLEAN` | 是 | `IDX(region, enabled)` | 该步骤路由是否启用 |
| `request_timeout_s` | `INT` | 是 |  | 单次 LLM 调用超时覆盖值，默认 `600` |
| `max_tokens` | `INT` | 是 |  | 单次调用最大输出 token |
| `temperature` | `DECIMAL(4,2)` | 否 |  | 模型温度 |
| `top_p` | `DECIMAL(4,2)` | 否 |  | 可选采样参数 |
| `allow_cross_region_fallback` | `BOOLEAN` | 是 |  | 是否允许跨 Region 兜底 |
| `cache_ttl_s` | `INT` | 是 |  | 运行时本地缓存 TTL |
| `active_version` | `INT` | 是 |  | 当前生效版本号，每次保存加 1 |
| `remark` | `VARCHAR(255)` | 否 |  | 备注 |
| `created_at` | `DATETIME(3)` | 是 |  | 创建时间 |
| `updated_at` | `DATETIME(3)` | 是 |  | 更新时间 |

说明：

- `request_timeout_s` 是“单次 LLM 调用超时”，不是整条生成任务超时
- 一期默认所有步骤该值均为 `600`
- 一期后台不单独暴露 `request_timeout_s / max_tokens / temperature / top_p / allow_cross_region_fallback` 的编辑表单
- 一期后台只允许维护“固定步骤在某个 `execution_region` 下绑定哪个主 Provider”
- 以上高级字段先由系统默认值或后端保底逻辑维护，待二期再开放

---

### 5.4 表：`llm_step_route_items`

用途：存储某个步骤路由下按顺序可用的 Provider 候选项，支持主模型与 fallback。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 候选项主键 UUID |
| `route_id` | `VARCHAR(36)` | 是 | `IDX(route_id, order_no)` | 对应 `llm_step_routes.id` |
| `order_no` | `INT` | 是 | `UNIQUE(route_id, order_no)` | 顺序号，`1` 为主模型 |
| `provider_id` | `VARCHAR(36)` | 是 | `IDX(provider_id)` | 对应 `llm_gateway_providers.id` |
| `model_name` | `VARCHAR(128)` | 否 |  | 覆盖主模型名，不填则使用 Provider 默认 |
| `fast_model_name` | `VARCHAR(128)` | 否 |  | 覆盖快速模型名 |
| `enabled` | `BOOLEAN` | 是 |  | 是否启用 |
| `created_at` | `DATETIME(3)` | 是 |  | 创建时间 |
| `updated_at` | `DATETIME(3)` | 是 |  | 更新时间 |

说明：

- 路由选择逻辑按 `order_no ASC` 执行
- 某个候选项失败后，若错误可重试，则依次尝试下一项
- 一次 LLM 调用切换过 fallback 后，必须记录 `is_fallback=true`
- 一期后台仅维护 `order_no=1` 的主 Provider 绑定
- `order_no>1` 的 fallback 候选项保留为二期扩展能力，不在一期后台暴露

---

### 5.5 表：`llm_provider_test_logs`

用途：保存后台“在线连接测试”的结果，便于排查配置正确性。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 主键 UUID |
| `provider_id` | `VARCHAR(36)` | 是 | `IDX(provider_id, created_at)` | 对应 Provider |
| `region` | `ENUM` | 是 |  | 本次测试执行 Region |
| `test_type` | `ENUM('connectivity','chat_completion')` | 是 |  | 连通性或真实补全测试 |
| `step_key` | `VARCHAR(64)` | 否 |  | 绑定某个步骤时可选填写 |
| `requested_model` | `VARCHAR(128)` | 否 |  | 本次测试使用的模型 |
| `request_timeout_s` | `INT` | 是 |  | 本次测试单次调用超时 |
| `success` | `BOOLEAN` | 是 |  | 是否成功 |
| `latency_ms` | `INT` | 否 |  | 耗时 |
| `http_status` | `INT` | 否 |  | 上游 HTTP 状态码 |
| `upstream_request_id` | `VARCHAR(128)` | 否 |  | 上游 request id |
| `error_code` | `VARCHAR(64)` | 否 |  | 内部归一化错误码 |
| `error_message` | `VARCHAR(512)` | 否 |  | 错误摘要 |
| `response_excerpt` | `VARCHAR(1024)` | 否 |  | 响应摘录，禁止明文回显敏感信息 |
| `created_at` | `DATETIME(3)` | 是 |  | 测试时间 |

---

### 5.6 表：`generation_tasks`

用途：持久化生成任务，用于前端轮询、后台查询、断线恢复、多实例接续。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 任务 ID |
| `task_type` | `ENUM('pipeline_run','pipeline_iterate')` | 是 | `IDX(user_id, created_at)` | 任务类型 |
| `runner_service` | `ENUM('game_service')` | 是 |  | 一期固定为 `game_service` |
| `region` | `ENUM` | 是 | `IDX(region, status)` | 本任务归属 Region |
| `game_id` | `VARCHAR(36)` | 是 | `IDX(game_id, created_at)` | 对应游戏 ID |
| `user_id` | `VARCHAR(36)` | 是 | `IDX(user_id, created_at)` | 对应用户 ID |
| `status` | `ENUM('queued','running','succeeded','failed','canceled','timed_out')` | 是 | `IDX(status, updated_at)` | 任务状态 |
| `cancel_requested` | `BOOLEAN` | 是 | `IDX(status, cancel_requested)` | 是否收到取消请求 |
| `current_stage` | `VARCHAR(64)` | 否 |  | 当前流水线阶段 |
| `current_step_key` | `VARCHAR(64)` | 否 |  | 当前 LLM 步骤 |
| `progress_pct` | `INT` | 是 |  | `0-100` |
| `task_timeout_s` | `INT` | 是 |  | 整个任务总超时，默认 `600` |
| `attempt_count` | `INT` | 是 |  | 任务总执行次数 |
| `max_attempts` | `INT` | 是 |  | 任务最大执行次数 |
| `gateway_config_version` | `INT` | 否 |  | 本次请求命中的网关配置版本 |
| `route_snapshot_json` | `JSON` | 否 |  | 由 `ai-engine` 在首个进度回调中回传、`game-service` 落库的实际路由快照 |
| `request_payload_json` | `JSON` | 否 |  | 脱敏后的请求体摘要 |
| `upstream_task_id` | `VARCHAR(64)` | 否 |  | 预留给后续启用 `ai-engine` 原生 async runner 时使用 |
| `result_version` | `INT` | 否 |  | 成功后 bundle 版本 |
| `result_bundle_id` | `VARCHAR(36)` | 否 |  | 成功后 bundle ID |
| `result_preview_url` | `VARCHAR(255)` | 否 |  | 成功后预览地址 |
| `result_payload_json` | `JSON` | 否 |  | 成功后的任务结果摘要 |
| `terminal_error_code` | `VARCHAR(64)` | 否 |  | 终态错误码 |
| `terminal_error_message` | `VARCHAR(1024)` | 否 |  | 终态错误摘要 |
| `terminal_error_json` | `JSON` | 否 |  | 终态完整错误对象 |
| `started_at` | `DATETIME(3)` | 否 |  | 开始时间 |
| `completed_at` | `DATETIME(3)` | 否 |  | 结束时间 |
| `heartbeat_at` | `DATETIME(3)` | 否 |  | Worker 心跳 |
| `last_error_at` | `DATETIME(3)` | 否 |  | 最后错误时间 |
| `created_at` | `DATETIME(3)` | 是 |  | 创建时间 |
| `updated_at` | `DATETIME(3)` | 是 |  | 更新时间 |

说明：

- 该表由 `game-service` 主写，`ai-engine` 不直接写入
- `route_snapshot_json` 是保证可追溯性的关键字段，但由 `ai-engine` 回调、`game-service` 落库
- `cancel_requested=true` 表示收到取消请求；若上游调用已无法中断，则按“软取消”处理，不再提交结果
- 前端任务查询与后台任务查询都基于此表

---

### 5.7 表：`generation_task_events`

用途：存储任务时间线，既给前端轮询事件，也给后台“生成记录详情”展示。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 事件 ID |
| `task_id` | `VARCHAR(36)` | 是 | `IDX(task_id, seq_no)` | 所属任务 |
| `seq_no` | `INT` | 是 | `UNIQUE(task_id, seq_no)` | 单任务内递增序号 |
| `game_id` | `VARCHAR(36)` | 是 | `IDX(game_id, created_at)` | 游戏 ID |
| `user_id` | `VARCHAR(36)` | 是 | `IDX(user_id, created_at)` | 用户 ID |
| `stage` | `VARCHAR(64)` | 是 |  | 流水线阶段 |
| `step_key` | `VARCHAR(64)` | 否 |  | 对应 LLM 步骤 |
| `status` | `ENUM('queued','running','retrying','succeeded','failed','canceled','timed_out','info')` | 是 |  | 事件状态 |
| `message` | `VARCHAR(255)` | 是 |  | 前端可展示的文本 |
| `detail_json` | `JSON` | 否 |  | 附加细节 |
| `created_at` | `DATETIME(3)` | 是 | `IDX(task_id, created_at)` | 事件时间 |

---

### 5.8 表：`llm_call_logs`

用途：保存最末端 LLM 调用日志，满足“知道到底哪个模型、哪个 Region、返回了什么错误”的要求。

| 字段 | 类型 | 必填 | 索引/约束 | 说明 |
|------|------|------|------|------|
| `id` | `VARCHAR(36)` | 是 | PK | 日志 ID |
| `task_id` | `VARCHAR(36)` | 是 | `IDX(task_id, created_at)` | 对应任务 |
| `game_id` | `VARCHAR(36)` | 是 | `IDX(game_id, created_at)` | 游戏 ID |
| `user_id` | `VARCHAR(36)` | 是 | `IDX(user_id, created_at)` | 用户 ID |
| `stage` | `VARCHAR(64)` | 是 |  | 流水线阶段 |
| `step_key` | `VARCHAR(64)` | 是 | `IDX(step_key, created_at)` | LLM 步骤 |
| `provider_id` | `VARCHAR(36)` | 是 | `IDX(provider_id, created_at)` | 命中的 Provider |
| `provider_type` | `VARCHAR(32)` | 是 |  | Provider 类型 |
| `region` | `ENUM` | 是 |  | 实际执行 Region |
| `model_name` | `VARCHAR(128)` | 是 |  | 实际调用模型 |
| `request_timeout_s` | `INT` | 是 |  | 单次调用超时 |
| `max_tokens` | `INT` | 否 |  | 输出 token 上限 |
| `temperature` | `DECIMAL(4,2)` | 否 |  | 实际温度 |
| `attempt_no` | `INT` | 是 |  | 本步骤第几次尝试 |
| `is_fallback` | `BOOLEAN` | 是 |  | 是否走 fallback Provider |
| `success` | `BOOLEAN` | 是 | `IDX(success, created_at)` | 是否成功 |
| `latency_ms` | `INT` | 否 |  | 耗时 |
| `http_status` | `INT` | 否 |  | 上游 HTTP 状态码 |
| `upstream_request_id` | `VARCHAR(128)` | 否 |  | 上游 request id |
| `finish_reason` | `VARCHAR(64)` | 否 |  | 成功时的结束原因 |
| `input_tokens` | `INT` | 否 |  | 输入 token |
| `output_tokens` | `INT` | 否 |  | 输出 token |
| `error_code` | `VARCHAR(64)` | 否 |  | 归一化错误码 |
| `error_message` | `VARCHAR(1024)` | 否 |  | 错误摘要 |
| `error_body_excerpt` | `LONGTEXT` | 否 |  | 上游错误体截断摘录 |
| `created_at` | `DATETIME(3)` | 是 |  | 记录时间 |

日志要求：

- 必须脱敏，不落完整 API Key、完整 Prompt、完整成功响应
- `error_body_excerpt` 建议截断至 2048 字符
- 若上游返回 request id，必须落 `upstream_request_id`
- 该表由 `game-service` 通过内部回调落库，`ai-engine` 只负责生成并上报日志内容

---

## 六、后台管理设计

### 6.1 页面结构

在现有 admin panel 中新增 4 个页面：

| 页面 | 作用 |
|------|------|
| `云账号与 Region 目标` | 同步云 Region、配置 `ai-engine` Region Target、查看部署状态 |
| `LLM 网关配置` | 页面内包含 `Provider 表单`、`Provider 列表`、`固定步骤绑定列表` 3 个区块 |
| `生成任务` | 查看异步任务状态、取消、重试、详情 |
| `LLM 调用日志` | 查看最末端模型调用结果与错误 |

### 6.2 后台支持的操作

### LLM 网关配置

- `Provider 表单`
  - 新增 Provider
  - 编辑 Provider
  - 启用/停用 Provider
  - 在线连接测试
  - Provider 的 Region 不再手填，而是从“已部署的 `ai-engine` Region Target”下拉选择
- `Provider 列表`
  - 展示 Provider 基本信息、运行 Region、模型、状态、最近测试结果
- `固定步骤绑定列表`
  - 按 `executionRegion` 过滤后展示系统内置步骤目录
  - 每个步骤仅允许选择 1 个主 Provider
  - `stepKey` 不可编辑，必须来自 `llm_step_catalog`
  - Provider 下拉数据来自已启用的 `Provider 列表`
  - 选择 Provider 后，`region / modelDefault / modelFast` 自动带出，不再手工填写
  - 一期不暴露单独的“步骤路由表单”、`fallback`、`model override`

### 云账号与 Region 目标

- 查看当前激活云账号
- 手动同步云厂商 Region 目录
- 查看 Region 是否支持 VCR / VeFaaS / APIG
- 创建和编辑 `ai-engine` Region Target
- 查看每个 target 的 `deployStatus`、`functionName`、`aiEngineUrl`、`lastRevision`
- 标记 target 是否纳入部署

### 生成任务

- 按用户、游戏、状态、Region、时间筛选
- 查看任务详情、时间线、终态错误
- 管理员取消任务
- 管理员手动重试失败任务

### LLM 调用日志

- 按 `taskId`、`gameId`、`stepKey`、`providerId`、`success` 筛选
- 查看 `HTTP Status`、`upstreamRequestId`、`errorBodyExcerpt`

---

## 七、Admin API 设计

后台接口延续当前管理面板风格：

- 请求头：`x-admin-token: <token>`
- 响应格式：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

### 7.0 云厂商与 Region 目标 API

### `GET /admin/cloud/accounts`

Response `data.items[*]`：

```json
{
  "id": "acct_1",
  "vendor": "volcengine",
  "accountKey": "volc-prod",
  "displayName": "火山生产账号",
  "enabled": true,
  "credentialMode": "env_secret",
  "defaultRegistry": "gamevallies-repo-cn-shanghai.cr.volces.com",
  "defaultRegistryNamespace": "gamevallies",
  "defaultVpcId": "vpc-xxx",
  "defaultSubnetId": "subnet-xxx",
  "defaultSecurityGroupId": "sg-xxx",
  "regionSyncMode": "seed_and_verify",
  "lastRegionSyncStatus": "success",
  "lastRegionSyncError": null,
  "lastRegionSyncAt": "2026-03-22T15:30:00.000Z"
}
```

### `POST /admin/cloud/regions/sync`

Request:

```json
{
  "accountId": "acct_1"
}
```

Response:

```json
{
  "syncedCount": 2,
  "vendor": "volcengine",
  "accountId": "acct_1",
  "syncedAt": "2026-03-22T15:32:00.000Z"
}
```

### `GET /admin/cloud/regions`

Query:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `accountId` | string | 否 | 云账号 ID |
| `deploySupported` | boolean | 否 | 仅返回可部署 Region |

Response `data.items[*]`：

```json
{
  "id": "region_1",
  "accountId": "acct_1",
  "vendor": "volcengine",
  "regionCode": "cn-shanghai",
  "regionName": "上海",
  "regionGroup": "cn_mainland",
  "vcrSupported": true,
  "vefaasSupported": true,
  "apigSupported": true,
  "deploySupported": true,
  "enabled": true,
  "syncedAt": "2026-03-22T15:32:00.000Z"
}
```

### `GET /admin/cloud/ai-engine-region-targets`

Query:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `executionRegion` | string | 否 | `cn_shanghai` |
| `deployStatus` | string | 否 | `pending` / `ready` / `deployed` / `failed` |
| `providerSelectableOnly` | boolean | 否 | 若为 `true`，仅返回可用于 Provider 选择的目标 |

Response `data.items[*]`：

```json
{
  "id": "target_1",
  "accountId": "acct_1",
  "regionCatalogId": "region_1",
  "vendor": "volcengine",
  "cloudRegionCode": "cn-shanghai",
  "executionRegion": "cn_shanghai",
  "displayName": "AI Engine 上海主实例",
  "functionName": "gv-ai-engine-cn",
  "registry": "gamevallies-repo-cn-shanghai.cr.volces.com",
  "registryNamespace": "gamevallies",
  "imageRepository": "gv-ai-engine-cn",
  "serviceRegionEnv": "cn_shanghai",
  "aiEngineUrl": "https://xxx.apigateway-cn-shanghai-inner.volceapi.com",
  "deployEnabled": true,
  "deployStatus": "deployed",
  "lastRevision": "36",
  "lastImageTag": "release-20260322-1456-task-heartbeat",
  "lastReleaseStatus": "done",
  "lastDeployError": null,
  "lastDeployedAt": "2026-03-22T15:40:00.000Z"
}
```

### `POST /admin/cloud/ai-engine-region-targets`

Request:

```json
{
  "accountId": "acct_1",
  "regionCatalogId": "region_1",
  "executionRegion": "cn_shanghai",
  "displayName": "AI Engine 上海主实例",
  "functionName": "gv-ai-engine-cn",
  "registry": "gamevallies-repo-cn-shanghai.cr.volces.com",
  "registryNamespace": "gamevallies",
  "imageRepository": "gv-ai-engine-cn",
  "serviceRegionEnv": "cn_shanghai",
  "deployEnabled": true
}
```

保存规则：

- `regionCatalogId` 必须来自 `cloud_region_catalog`
- `executionRegion` 一期只允许 `cn_shanghai`
- 创建或更新 target 不等于触发部署
- 真正部署仍由 `scripts/deploy.py` 执行

### `PUT /admin/cloud/ai-engine-region-targets/:id`

用途：

- 编辑已有 target 的 `displayName / functionName / registry / imageRepository / deployEnabled`
- 修正错误配置，而不需要删除重建

规则：

- 不允许修改已落库 target 的 `executionRegion`
- `regionCatalogId` 仅允许在同一云账号、同一 `cloudRegionCode` 下更正
- 若 target 已被 Provider 引用，则禁止把 `deployEnabled` 改成 `false`，除非先迁走相关 Provider

### 7.1 LLM Provider 列表

### `GET /admin/llm-providers`

Query:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `region` | string | 否 | `cn_shanghai` |
| `providerType` | string | 否 | Provider 类型 |
| `enabled` | boolean | 否 | 是否启用 |
| `page` | number | 否 | 默认 `1` |
| `limit` | number | 否 | 默认 `20` |

Response `data`：

```json
{
  "items": [
    {
      "id": "8a9f...",
      "providerKey": "minimaxi_sh_primary",
      "displayName": "MiniMax 上海主模型",
      "providerType": "openai_compatible",
      "regionTargetId": "target_1",
      "cloudVendor": "volcengine",
      "cloudRegionCode": "cn-shanghai",
      "region": "cn_shanghai",
      "regionDisplayName": "AI Engine 上海主实例",
      "baseUrl": "https://api.minimaxi.com/v1",
      "apiPath": "/chat/completions",
      "authMode": "bearer",
      "apiKeyMasked": "sk-****1a2b",
      "modelDefault": "MiniMax-M2.5",
      "modelFast": "MiniMax-M2.5",
      "requestTimeoutS": 600,
      "connectTimeoutS": 15,
      "maxRetries": 1,
      "priority": 10,
      "enabled": true,
      "healthStatus": "healthy",
      "lastTestSuccess": true,
      "lastTestLatencyMs": 1840,
      "lastTestError": null,
      "lastTestAt": "2026-03-21T20:10:22.000Z",
      "extraHeaders": {},
      "createdAt": "2026-03-21T19:00:00.000Z",
      "updatedAt": "2026-03-21T20:10:22.000Z"
    }
  ],
  "page": 1,
  "limit": 20,
  "total": 1
}
```

### 7.2 新增 LLM Provider

### `POST /admin/llm-providers`

Request:

```json
{
  "providerKey": "minimaxi_sh_primary",
  "displayName": "MiniMax 上海主模型",
  "providerType": "openai_compatible",
  "regionTargetId": "target_1",
  "baseUrl": "https://api.minimaxi.com/v1",
  "apiPath": "/chat/completions",
  "authMode": "bearer",
  "authHeaderName": null,
  "apiKey": "sk-xxxx",
  "modelDefault": "MiniMax-M2.5",
  "modelFast": "MiniMax-M2.5",
  "requestTimeoutS": 600,
  "connectTimeoutS": 15,
  "maxRetries": 1,
  "priority": 10,
  "enabled": true,
  "extraHeaders": {}
}
```

Request 字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `providerKey` | string | 是 | 稳定唯一键 |
| `displayName` | string | 是 | 后台展示名称 |
| `providerType` | string | 是 | Provider 类型 |
| `regionTargetId` | string | 是 | 绑定的 `ai-engine` Region Target |
| `baseUrl` | string | 是 | 基础地址 |
| `apiPath` | string | 否 | 默认路径 |
| `authMode` | string | 是 | 鉴权模式 |
| `authHeaderName` | string \| null | 否 | `custom_header` 时必填 |
| `apiKey` | string | 是 | 明文仅在请求体中出现，入库前加密 |
| `modelDefault` | string | 是 | 主模型 |
| `modelFast` | string \| null | 否 | 快速模型 |
| `requestTimeoutS` | number | 否 | 单次调用超时，默认 `600` |
| `connectTimeoutS` | number | 否 | 连接超时，默认 `15` |
| `maxRetries` | number | 否 | Provider 级重试次数 |
| `priority` | number | 否 | 优先级 |
| `enabled` | boolean | 否 | 是否启用 |
| `extraHeaders` | object | 否 | 额外请求头 |

保存时后端自动派生：

- `cloudVendor`
- `cloudRegionCode`
- `region`（即 `executionRegion`）
- `regionDisplayName`

校验规则：

- `regionTargetId` 必须存在
- 对应 target 必须 `deployStatus=deployed`
- Provider 表单中的 Region 下拉数据源来自 `GET /admin/cloud/ai-engine-region-targets?providerSelectableOnly=true`

### 7.3 编辑 LLM Provider

### `PUT /admin/llm-providers/:id`

规则：

- `apiKey` 不回显
- 不传 `apiKey` 表示不修改密钥
- 传 `apiKey` 表示覆盖更新

### 7.4 在线连接测试

### `POST /admin/llm-providers/:id/test`

Request:

```json
{
  "stepKey": "code_generate.hybrid",
  "model": "MiniMax-M2.5",
  "requestTimeoutS": 30
}
```

Response `data`：

```json
{
  "success": true,
  "providerId": "8a9f...",
  "providerKey": "minimaxi_sh_primary",
  "region": "cn_shanghai",
  "resolvedEndpoint": "https://api.minimaxi.com/v1/chat/completions",
  "model": "MiniMax-M2.5",
  "requestTimeoutS": 30,
  "latencyMs": 1840,
  "httpStatus": 200,
  "upstreamRequestId": "req_abc123",
  "errorCode": null,
  "errorMessage": null,
  "responseExcerpt": "pong"
}
```

### 7.5 获取步骤路由

### `GET /admin/llm-step-routes`

Query:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `executionRegion` | string | 是 | `cn_shanghai` |

Response `data.items[*]`：

```json
{
  "id": "route_123",
  "stepKey": "code_generate.hybrid",
  "stepOrder": 40,
  "stageLabel": "Stage 05",
  "displayName": "代码生成（Hybrid）",
  "description": "使用模板混合 LLM 生成首版代码",
  "executionRegion": "cn_shanghai",
  "enabled": true,
  "providerId": "8a9f...",
  "providerKey": "minimaxi_sh_primary",
  "providerDisplayName": "MiniMax 上海主模型",
  "providerRegionTargetId": "target_1",
  "providerRegionDisplayName": "AI Engine 上海主实例",
  "modelDefault": "MiniMax-M2.5",
  "modelFast": "MiniMax-M2.5",
  "updatedAt": "2026-03-22T16:10:00.000Z"
}
```

说明：

- 该接口返回“固定步骤目录 + 当前绑定的主 Provider”视图模型
- 后台页面直接按此接口渲染列表，不再拼装独立的步骤路由表单
- 一期每个步骤只允许绑定 1 个主 Provider

### 7.6 保存步骤路由

### `PUT /admin/llm-step-routes/:stepKey`

Request:

```json
{
  "executionRegion": "cn_shanghai",
  "providerId": "8a9f...",
  "enabled": true
}
```

规则：

- `stepKey` 必须存在于 `llm_step_catalog`
- `providerId` 必须存在于 `llm_gateway_providers`
- `executionRegion` 必须与所选 Provider 的 `region` 一致；若不一致则拒绝保存
- `region / cloudRegionCode / regionTargetId / modelDefault / modelFast` 均由 Provider 自动派生，不允许手工覆盖
- 后端按 `stepKey + executionRegion` 做 upsert
- 一期仅维护主 Provider，对应 `llm_step_route_items.order_no=1`
- 保存成功后：
  - 路由 `activeVersion + 1`
  - `system_configs.llm.gateway.config_version + 1`
  - 自动触发运行时刷新

### 7.7 手动刷新运行时配置

### `POST /admin/llm-runtime/refresh`

Request:

```json
{
  "regions": ["cn_shanghai"]
}
```

Response:

```json
{
  "results": [
    {
      "region": "cn_shanghai",
      "success": true,
      "refreshedVersion": 12
    },
  ]
}
```

### 7.8 后台查看生成任务

### `GET /admin/generation-tasks`

Query:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | string | 否 | 任务状态 |
| `region` | string | 否 | 任务 Region |
| `userId` | string | 否 | 用户 ID |
| `gameId` | string | 否 | 游戏 ID |
| `page` | number | 否 | 页码 |
| `limit` | number | 否 | 每页数量 |

### `GET /admin/generation-tasks/:id`

返回任务详情，包括 `terminalError`、`routeSnapshot`、`resultPayload`。

### `GET /admin/generation-tasks/:id/events`

返回任务时间线：

```json
{
  "items": [
    {
      "seqNo": 1,
      "stage": "intent_parsing",
      "stepKey": "intent_parse",
      "status": "running",
      "message": "解析游戏意图",
      "detail": {
        "attempt": 1
      },
      "createdAt": "2026-03-21T20:30:01.000Z"
    }
  ]
}
```

### `POST /admin/generation-tasks/:id/cancel`

用途：管理员取消运行中任务。

取消语义：

- 一期为“best effort”
- 若未来启用 `upstream_task_id` 且 `ai-engine` 支持取消，则同步转发上游取消
- 若当前仍是 `game-service` 背景同步调用，则先写 `cancel_requested=true`
- 已经完成持久化的任务不允许回滚成取消态

Response：

```json
{
  "code": 0,
  "data": {
    "taskId": "task_123",
    "status": "canceled"
  }
}
```

### `POST /admin/generation-tasks/:id/retry`

用途：管理员重新触发失败任务。重试时：

- 新建一条新的 `generation_tasks` 记录
- 原任务保持原始失败记录不覆盖
- 新任务重新抓取最新配置并生成新的 `route_snapshot_json`

Response：

```json
{
  "code": 0,
  "data": {
    "previousTaskId": "task_123",
    "newTaskId": "task_456",
    "status": "queued"
  }
}
```

### 7.9 后台查看 LLM 调用日志

### `GET /admin/llm-call-logs`

Query:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `taskId` | string | 否 | 任务 ID |
| `gameId` | string | 否 | 游戏 ID |
| `stepKey` | string | 否 | 步骤键 |
| `providerId` | string | 否 | Provider ID |
| `success` | boolean | 否 | 是否成功 |
| `page` | number | 否 | 页码 |
| `limit` | number | 否 | 每页数量 |

---

## 八、前端业务 API 设计

前端继续走业务入口 `game-service`，不直接操作 `ai-engine` 的底层路由配置。

### 8.1 生成任务对象

统一对象 `GenerationTask`：

```json
{
  "taskId": "task_123",
  "taskType": "pipeline_run",
  "status": "running",
  "region": "cn_shanghai",
  "gameId": "game_123",
  "version": 1,
  "progressPct": 60,
  "cancelRequested": false,
  "currentStage": "code_generating",
  "currentStepKey": "code_generate.hybrid",
  "taskTimeoutS": 600,
  "wsChannel": "game:game_123",
  "pollUrl": "/api/v1/games/tasks/task_123",
  "eventsUrl": "/api/v1/games/tasks/task_123/events",
  "cancelUrl": "/api/v1/games/tasks/task_123/cancel",
  "startedAt": "2026-03-21T20:30:00.000Z",
  "completedAt": null,
  "terminalError": null
}
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 任务 ID |
| `taskType` | string | `pipeline_run` / `pipeline_iterate` |
| `status` | string | 任务状态 |
| `region` | string | 实际执行 Region |
| `gameId` | string | 游戏 ID |
| `version` | number | 目标版本 |
| `progressPct` | number | `0-100` |
| `cancelRequested` | boolean | 是否已收到取消请求 |
| `currentStage` | string \| null | 当前流水线阶段 |
| `currentStepKey` | string \| null | 当前 LLM 步骤 |
| `taskTimeoutS` | number | 任务总超时 |
| `wsChannel` | string | WebSocket 频道 |
| `pollUrl` | string | 轮询地址 |
| `eventsUrl` | string | 事件流地址 |
| `cancelUrl` | string \| null | 取消地址 |
| `terminalError` | object \| null | 失败后返回终态错误 |

### 8.2 终态错误对象

```json
{
  "source": "llm_gateway",
  "stage": "code_generating",
  "stepKey": "code_generate.hybrid",
  "providerId": "8a9f...",
  "providerType": "openai_compatible",
  "region": "cn_shanghai",
  "modelName": "MiniMax-M2.5",
  "httpStatus": 404,
  "errorCode": "upstream_http_error",
  "message": "Client error '404 Not Found' for url 'https://api.minimaxi.com/chat/completions'",
  "upstreamRequestId": "req_abc123",
  "retryCount": 1,
  "retryable": false,
  "occurredAt": "2026-03-21T20:35:20.000Z"
}
```

### 8.3 创建生成任务

### `POST /api/v1/games/generate`

Request:

```json
{
  "description": "做一个点击躲避障碍物的小游戏",
  "title": "障碍躲避",
  "taskTimeoutS": 600,
  "regionHint": "cn_shanghai"
}
```

Request 字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | 与 `prompt` 二选一 | 生成描述 |
| `prompt` | string | 与 `description` 二选一 | 兼容字段 |
| `title` | string | 否 | 初始标题 |
| `taskTimeoutS` | number | 否 | 任务总超时，不传默认 `600` |
| `regionHint` | string | 否 | `cn_shanghai` |

Response:

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "gameId": "game_123",
    "title": "障碍躲避",
    "description": "做一个点击躲避障碍物的小游戏",
    "status": "generating",
    "canPlay": true,
    "quotaRemaining": 4,
    "requireSubscription": false,
    "generationTask": {
      "taskId": "task_123",
      "taskType": "pipeline_run",
      "status": "queued",
      "region": "cn_shanghai",
      "gameId": "game_123",
      "version": 1,
      "progressPct": 0,
      "currentStage": "started",
      "currentStepKey": null,
      "taskTimeoutS": 600,
      "wsChannel": "game:game_123",
      "pollUrl": "/api/v1/games/tasks/task_123",
      "eventsUrl": "/api/v1/games/tasks/task_123/events",
      "cancelUrl": "/api/v1/games/tasks/task_123/cancel",
      "startedAt": null,
      "completedAt": null,
      "terminalError": null
    }
  }
}
```

### 8.4 创建迭代任务

### `POST /api/v1/games/:id/iterate`

Request:

```json
{
  "feedback": "把主角移动速度调快一点，并增加得分动画",
  "taskTimeoutS": 600
}
```

Response：

```json
{
  "code": 0,
  "data": {
    "gameId": "game_123",
    "version": 2,
    "status": "iterating",
    "generationTask": {
      "taskId": "task_456",
      "taskType": "pipeline_iterate",
      "status": "queued",
      "region": "cn_shanghai",
      "gameId": "game_123",
      "version": 2,
      "progressPct": 0,
      "currentStage": "started",
      "currentStepKey": null,
      "taskTimeoutS": 600,
      "wsChannel": "game:game_123",
      "pollUrl": "/api/v1/games/tasks/task_456",
      "eventsUrl": "/api/v1/games/tasks/task_456/events",
      "cancelUrl": "/api/v1/games/tasks/task_456/cancel",
      "terminalError": null
    }
  }
}
```

### 8.5 查询任务详情

### `GET /api/v1/games/tasks/:taskId`

Response:

```json
{
  "code": 0,
  "data": {
    "taskId": "task_123",
    "taskType": "pipeline_run",
    "status": "failed",
    "region": "cn_shanghai",
    "gameId": "game_123",
    "version": 1,
    "progressPct": 78,
    "currentStage": "qa_checking",
    "currentStepKey": "qa_fix",
    "taskTimeoutS": 600,
    "wsChannel": "game:game_123",
    "pollUrl": "/api/v1/games/tasks/task_123",
    "eventsUrl": "/api/v1/games/tasks/task_123/events",
    "cancelUrl": null,
    "startedAt": "2026-03-21T20:30:00.000Z",
    "completedAt": "2026-03-21T20:35:20.000Z",
    "terminalError": {
      "source": "llm_gateway",
      "stage": "qa_checking",
      "stepKey": "qa_fix",
      "providerId": "8a9f...",
      "providerType": "openai_compatible",
      "region": "cn_shanghai",
      "modelName": "MiniMax-M2.5",
      "httpStatus": 429,
      "errorCode": "rate_limit",
      "message": "Upstream provider rate limited",
      "upstreamRequestId": "req_abc123",
      "retryCount": 3,
      "retryable": true,
      "occurredAt": "2026-03-21T20:35:20.000Z"
    }
  }
}
```

### 8.6 查询任务事件流

### `GET /api/v1/games/tasks/:taskId/events`

Query:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `cursor` | number | 否 | 从某个 `seqNo` 之后继续拉取 |
| `limit` | number | 否 | 默认 `50` |

Response:

```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "seqNo": 1,
        "stage": "started",
        "stepKey": null,
        "status": "queued",
        "message": "任务已创建",
        "detail": {
          "taskType": "pipeline_run"
        },
        "createdAt": "2026-03-21T20:30:00.000Z"
      },
      {
        "seqNo": 2,
        "stage": "code_generating",
        "stepKey": "code_generate.hybrid",
        "status": "running",
        "message": "生成游戏代码",
        "detail": {
          "attempt": 1,
          "providerId": "8a9f..."
        },
        "createdAt": "2026-03-21T20:30:12.000Z"
      }
    ],
    "nextCursor": 2,
    "hasMore": false
  }
}
```

### 8.7 取消任务

### `POST /api/v1/games/tasks/:taskId/cancel`

Response：

```json
{
  "code": 0,
  "data": {
    "taskId": "task_123",
    "status": "canceled"
  }
}
```

### 8.8 兼容接口

### `GET /api/v1/games/:id/generation-status`

此接口继续保留，内部读取 `generation_tasks` 当前最新任务并返回兼容结构，供未升级前端继续使用。

---

## 九、WebSocket 事件约定

沿用当前 `game-service` 的实时推送方式，频道仍为 `user:{userId}`，事件名保持兼容：

| 事件 | 说明 |
|------|------|
| `gen:progress` | 任务进行中 |
| `gen:complete` | 任务完成 |
| `gen:error` | 任务失败 |

事件体中新增：

| 字段 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 当前任务 ID |
| `taskType` | string | 任务类型 |
| `region` | string | 执行 Region |
| `stepKey` | string \| null | 当前 LLM 步骤 |
| `terminalError` | object \| null | 失败时返回 |

---

## 十、服务间内部接口

以下接口不暴露给前端，只供 `game-service` 与 `ai-engine` 之间调用。

说明：

- 一期保留当前主执行模式：`game-service` 背景 worker 调 `ai-engine` 同步流水线接口
- `ai-engine` 自带的内存态 `/pipeline/run/async` 与 `/tasks/*` 仅作为调试能力保留，不作为生产主链路的任务真源

### 10.1 `game-service -> ai-engine` 首次生成

### `POST /api/v1/ai/pipeline/run`

Request：

```json
{
  "task_id": "task_123",
  "game_id": "game_123",
  "user_id": "user_123",
  "description": "做一个点击躲避障碍物的小游戏",
  "platform": "wechat_webview",
  "region": "cn_shanghai",
  "timeout_s": 600
}
```

Response：

```json
{
  "game_id": "game_123",
  "html_code": "<!DOCTYPE html>...</html>",
  "strategy": "hybrid",
  "qa_passed": true,
  "qa_retries": 1,
  "generation_time_ms": 184000,
  "code_size_bytes": 28641,
  "quality_score": 9.2
}
```

说明：

- 最终成功结果仍通过该同步响应返回给 `game-service`
- `game-service` 收到成功响应后负责保存 bundle、更新 game 状态、补齐 previewUrl、推送 `gen:complete`
- 若响应异常，则 `game-service` 负责写任务失败终态和业务失败状态

### 10.2 `game-service -> ai-engine` 迭代生成

### `POST /api/v1/ai/pipeline/iterate`

Request：

```json
{
  "task_id": "task_456",
  "game_id": "game_123",
  "user_id": "user_123",
  "feedback": "把主角移动速度调快一点",
  "current_code": "<!DOCTYPE html>...</html>",
  "conversation": [],
  "region": "cn_shanghai",
  "timeout_s": 600
}
```

### 10.3 `ai-engine -> game-service` 进度回调

### `POST /api/v1/internal/generation/progress`

说明：

- 这是现有内部接口的扩展版，兼容当前实现
- 由 `ai-engine` 在每个阶段回调，`game-service` 收到后：
  - 更新 `generation_tasks`
  - 写入 `generation_task_events`
  - 推送 WebSocket

Request：

```json
{
  "taskId": "task_123",
  "gameId": "game_123",
  "userId": "user_123",
  "stage": "code_generating",
  "stepKey": "code_generate.hybrid",
  "status": "running",
  "percentage": 60,
  "message": "生成游戏代码",
  "region": "cn_shanghai",
  "gatewayConfigVersion": 12,
  "routeSnapshot": {
    "stepKey": "code_generate.hybrid",
    "region": "cn_shanghai",
    "items": [
      {
        "orderNo": 1,
        "providerId": "provider_1",
        "providerKey": "minimaxi_sh_primary",
        "modelName": "MiniMax-M2.5"
      }
    ]
  },
  "details": {
    "attempt": 1,
    "maxAttempts": 3
  }
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `taskId` | string | 是 | 任务 ID |
| `gameId` | string | 是 | 游戏 ID |
| `userId` | string | 是 | 用户 ID |
| `stage` | string | 是 | 流水线阶段 |
| `stepKey` | string \| null | 否 | 当前 LLM 步骤 |
| `status` | string | 是 | `queued` / `running` / `retrying` / `info` |
| `percentage` | number | 是 | 进度百分比 |
| `message` | string | 是 | 进度文本 |
| `region` | string | 是 | 实际执行 Region |
| `gatewayConfigVersion` | number | 否 | 命中的配置版本 |
| `routeSnapshot` | object | 否 | 首次进入某个 LLM 步骤时回传 |
| `details` | object | 否 | 额外细节 |

### 10.4 `ai-engine -> game-service` LLM 调用日志回调

### `POST /api/v1/internal/generation/llm-call-log`

用途：

- `ai-engine` 每次实际调用 LLM 后回调一次
- `game-service` 收到后写入 `llm_call_logs`

Request：

```json
{
  "taskId": "task_123",
  "gameId": "game_123",
  "userId": "user_123",
  "stage": "qa_checking",
  "stepKey": "qa_fix",
  "providerId": "provider_1",
  "providerType": "openai_compatible",
  "region": "cn_shanghai",
  "modelName": "MiniMax-M2.5",
  "requestTimeoutS": 600,
  "maxTokens": 4096,
  "temperature": 0.2,
  "attemptNo": 1,
  "isFallback": false,
  "success": false,
  "latencyMs": 1812,
  "httpStatus": 429,
  "upstreamRequestId": "req_abc123",
  "finishReason": null,
  "inputTokens": 1840,
  "outputTokens": null,
  "errorCode": "rate_limit",
  "errorMessage": "Upstream provider rate limited",
  "errorBodyExcerpt": "{\"error\":{\"message\":\"rate limit\"}}"
}
```

### 10.5 运行时配置刷新

### `POST /internal/llm-gateway/refresh`

Request：

```json
{
  "expectedVersion": 12
}
```

Response：

```json
{
  "success": true,
  "currentVersion": 12,
  "providerCount": 6,
  "routeCount": 8
}
```

### 10.6 部署执行器与 Region Target

说明：

- 一期真实部署动作仍由 `scripts/deploy.py` 负责，不由后台保存 Provider 时隐式触发
- 部署脚本应以 `ai_engine_region_targets` 为 `ai-engine` 多 Region 部署真源

部署脚本建议支持的模式：

1. `python scripts/deploy.py ai-engine --all-enabled-region-targets`
2. `python scripts/deploy.py ai-engine --execution-region cn_shanghai`
3. `python scripts/deploy.py all --all-enabled-region-targets`

部署脚本执行步骤：

1. 读取 `ai_engine_region_targets`，筛选 `deploy_enabled=true`
2. 根据 target 的 `cloud_region_code` 初始化云厂商 SDK
3. 根据 target 的 `registry` / `registry_namespace` / `image_repository` 推送镜像
4. 根据 target 的 `function_name` 更新或创建函数
5. 注入 `SERVICE_REGION=<execution_region>`
6. 发布 revision
7. 解析并回写：
   - `deploy_status`
   - `last_revision`
   - `last_image_tag`
   - `last_release_status`
   - `ai_engine_url`
   - `last_deployed_at`
8. 若失败则回写 `last_deploy_error`

---

## 十一、运行时流程

### 11.0 `game-service` 自动切换 `ai-engine` endpoint 规则

`game-service` 当前代码仍是单 `AI_ENGINE_URL` 直连模式；实施本方案后，需要改为“按任务 Region 动态解析上游 endpoint”，而不是在 Provider 绑定时直接改环境变量。

运行规则如下：

1. 前端发起生成请求后，`game-service` 先确定本次任务的 `execution_region`
2. `game-service` 根据 `execution_region` 查询 `ai_engine_region_targets`
3. 仅选择满足以下条件的 target：
   - `execution_region` 匹配
   - `deploy_enabled=true`
   - `deploy_status='deployed'`
   - `ai_engine_url` 非空
4. 取该 target 的 `ai_engine_url` 作为本次任务调用 `ai-engine` 的真实 endpoint
5. 然后再把请求发给该 Region 的 `ai-engine`
6. `ai-engine` 收到请求后，再根据步骤绑定决定本 Region 内实际使用哪个 Provider

关键结论：

- `Provider 绑定` 影响的是 `ai-engine` 内部“调用哪个大模型”
- `Region Target` 影响的是 `game-service` “把请求发到哪个 `ai-engine` endpoint”
- 因此 `game-service` 可以自动切换到真实 endpoint，但切换依据不是 Provider 本身，而是 `task.region -> ai_engine_region_target.ai_engine_url`

运行时缓存建议：

- `game-service` 对 `ai_engine_region_targets` 做 10 秒内存缓存
- 后台保存 target 或部署成功回写后，新任务最多 10 秒内切到新 endpoint
- 正在运行的任务不在中途切 endpoint

回退策略：

- 本地/开发环境：若查不到已部署 target，可回退到环境变量 `AI_ENGINE_URL`
- 生产环境：若查不到已部署 target，必须快速失败并返回 `missing_ai_engine_region_target`

### 11.1 新建任务流程

```text
前端 -> POST /api/v1/games/generate
  -> game-service 创建 game 记录
  -> game-service 创建 generation_tasks 记录
  -> game-service 根据 task.region 查找已部署的 ai_engine_region_target
  -> game-service 读取 target.ai_engine_url 作为本次上游地址
  -> game-service 后台 worker 调 ai-engine 同步 /pipeline/run
  -> ai-engine 在请求开始时解析 LLM 路由
  -> ai-engine 通过 /internal/generation/progress 回传 routeSnapshot 与进度
  -> game-service 更新 generation_tasks / generation_task_events 并推送 WebSocket
  -> ai-engine 执行流水线
  -> ai-engine 每次调用 LLM 通过 /internal/generation/llm-call-log 回传日志
  -> 成功后 ai-engine 把最终 HTML 通过同步响应返回给 game-service
  -> game-service 保存 bundle、更新 game 状态、更新 generation_tasks 成功终态
  -> 若失败则 game-service 写 failed 终态、failedStage/failedReason，并执行退款补偿
```

### 11.2 路由选择顺序

单次 LLM 调用选择顺序：

1. `game-service` 先按 `task.region` 解析本次要访问的 `ai_engine_url`
2. `ai-engine` 读取任务创建时快照中的 `llm_step_routes`
3. 找到当前 `stepKey + task.region`，其中 `task.region` 是 `execution_region`
4. 一期只取该路由绑定的主 Provider，即 `llm_step_route_items.order_no=1`
5. 若未找到主 Provider 绑定，则快速失败并写 `missing_step_provider_binding`
6. 二期若开放 fallback，再按 `order_no ASC` 尝试后续 Provider

### 11.3 任务超时与单次调用超时

两类超时必须区分：

- `requestTimeoutS`
  - 作用对象：单次 LLM 调用
  - 来源优先级：步骤路由 > Provider > `system_configs.llm.gateway.default_request_timeout_s`
  - 默认值：`600`
- `taskTimeoutS`
  - 作用对象：整条生成任务
  - 来源优先级：前端请求 > `system_configs.generation.task.default_timeout_s`
  - 默认值：`600`

---

## 十二、错误日志设计要求

### 12.1 必须记录的最末端错误字段

当 LLM 调用失败时，以下字段必须最终能在后台详情中看到：

| 字段 | 说明 |
|------|------|
| `stepKey` | 哪个步骤在调用模型 |
| `providerId` | 命中哪个 Provider |
| `providerType` | Provider 类型 |
| `region` | 实际发生在哪个 Region |
| `modelName` | 实际使用模型 |
| `httpStatus` | 上游 HTTP 状态码 |
| `errorCode` | 归一化错误码 |
| `errorMessage` | 错误摘要 |
| `errorBodyExcerpt` | 上游错误体摘录 |
| `upstreamRequestId` | 上游 request id |
| `attemptNo` | 第几次尝试 |
| `isFallback` | 是否已经切到 fallback |

### 12.2 归一化错误码建议

| 错误码 | 说明 |
|------|------|
| `invalid_provider_config` | Provider 配置不完整 |
| `upstream_http_error` | 上游返回非 2xx |
| `rate_limit` | 上游限流 |
| `network_error` | 网络连接异常 |
| `request_timeout` | 单次调用超时 |
| `response_parse_error` | 响应解析失败 |
| `missing_ai_engine_region_target` | 未找到已部署的 `ai-engine` Region Target |
| `missing_step_provider_binding` | 当前步骤未绑定可用 Provider |
| `task_timeout` | 整体任务超时 |

---

## 十三、实施顺序

建议按以下顺序开发：

1. `cloud_provider_accounts`、`cloud_region_catalog`、`ai_engine_region_targets`
2. 云 Region 同步接口与后台 `云账号 / Region 目标` 页面
3. `scripts/deploy.py` 改为读取 `ai_engine_region_targets` 做多 Region 部署
4. `llm_gateway_providers` 增加 `regionTargetId` / `cloudVendor` / `cloudRegionCode`
5. `ai-engine` 内部 `LLMGateway` 运行时与热加载
6. 后台 `LLM 网关配置` 页面
7. `generation_tasks` 与 `generation_task_events`
8. `llm_call_logs` 与后台日志页
9. 前端异步任务接口与兼容接口适配
10. 上海 / 柔佛双 Region 真实部署与联调

---

## 十四、一期实施边界

为控制风险，一期实施范围明确如下：

- 云厂商先只支持 `volcengine`
- Provider 表单中的 Region 下拉只允许选择 `deploy_status=deployed` 的 `ai_engine_region_target`
- 云凭据先通过服务端环境变量或 Secret Manager 注入，不做后台明文管理
- `scripts/deploy.py` 先支持多 Region CLI 部署，不在一期做后台“一键发布”
- 支持的 Provider 类型先落地 `openai_compatible` 与 `anthropic`
- 后台先支持手动在线测试，不做定时巡检
- 一期不支持 `global` 执行 Region
- 一期步骤绑定页只支持“固定步骤 -> 单主 Provider”模式，不开放 fallback 编辑
- 热生效先保证“新建任务立即生效”，运行中任务继续使用任务快照
- 前端先走轮询 + 现有 WebSocket，暂不引入新消息中间件

不在一期实施范围：

- 自动根据 Provider 创建动作即时触发云部署
- 自动流量分流 / AB Test
- 多租户隔离
- Prompt 全量审计存档
- 历史成功完整响应体长期保存
