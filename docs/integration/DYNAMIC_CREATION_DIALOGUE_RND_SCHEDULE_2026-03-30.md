# 动态创作会话 V2 研发任务排期（P0-P2）

> 基于：[DYNAMIC_CREATION_DIALOGUE_EXECUTION_PLAN_2026-03-30.md](/d:/Project/gamevallies/gamevallies-backend/docs/integration/DYNAMIC_CREATION_DIALOGUE_EXECUTION_PLAN_2026-03-30.md)  
> 日期：2026-03-30  
> 状态：待排期  
> 用途：用于研发分工、sprint 排期、联调顺序安排

---

## 1. 排期目标

本排期的目标是：

1. 先把“后端驱动创作会话”作为主链路跑通
2. 再把“质量导向理解”接进会话
3. 最后拉升 `showcase` 精品生成上限

排期原则：

- `P0`
  - 不完成则新方案无法上线
- `P1`
  - 不完成则质量收益不完整或风险较大
- `P2`
  - 体验增强和长期优化，可延后

---

## 2. 排期假设

以下排期按一个相对现实的小团队假设给出：

- `game-service`：1 人
- `ai-engine`：1 人
- `frontend`：1 人
- 联调 / 测试 / 灰度：0.5 到 1 人共享

默认节奏：

- 每个 sprint 1 周
- 共 5 个 sprint

如果实际人力更少，可把 Sprint 2 和 Sprint 3 合并；如果人力更强，可把部分 `P1` 并行推进。

---

## 3. 总体节奏

### Sprint 1

目标：

- 落地创作会话数据模型和后端主 API

优先级：

- `P0`

### Sprint 2

目标：

- 跑通前端创建页接入创作会话
- 跑通 `spec` 直通 pipeline

优先级：

- `P0`

### Sprint 3

目标：

- 接入方案草案和低置信高影响追问

优先级：

- `P0` + `P1`

### Sprint 4

目标：

- 扩 richer spec 和 designer

优先级：

- `P1`

### Sprint 5

目标：

- showcase 多候选、选优、QA 分层、灰度

优先级：

- `P1` + `P2`

---

## 4. P0 研发排期

P0 是必须完成的最小上线集合。

### 4.1 `game-service`

#### GS-P0-01：新增 `game_creation_sessions` 表和 migration

关联任务：

- `DCD-V2-01`

产出：

- Prisma schema
- migration
- 基础索引

依赖：

- 无

验收：

- migration 可执行
- 对线上现有数据无 destructive 影响

#### GS-P0-02：定义 creation session DTO / types / constants

关联任务：

- `DCD-V2-02`

产出：

- `creation-session.dto.ts`
- `creation-session.types.ts`
- `creation-session.constants.ts`

依赖：

- `GS-P0-01`

#### GS-P0-03：实现 creation session service

关联任务：

- `DCD-V2-05`

产出：

- `CreationSessionService`
- owner 校验
- revision 校验
- 幂等 / active session 控制

依赖：

- `GS-P0-01`
- `GS-P0-02`

#### GS-P0-04：实现 creation session API

接口：

- `POST /creation-sessions`
- `GET /creation-sessions/active`
- `GET /creation-sessions/:id`
- `POST /creation-sessions/:id/messages`
- `POST /creation-sessions/:id/skip`
- `POST /creation-sessions/:id/generate`
- `POST /creation-sessions/:id/abandon`

依赖：

- `GS-P0-03`
- `AE-P0-01`
- `AE-P0-02`

#### GS-P0-05：生成完成后回写 session 状态

关联任务：

- `DCD-V2-05`
- `DCD-V2-07`

目标：

- `collecting -> generating -> completed/failed`

依赖：

- `GS-P0-03`
- 现有 generation task 回调链路

### 4.2 `ai-engine`

#### AE-P0-01：实现无状态 `analyze-turn`

关联任务：

- `DCD-V2-03`

产出：

- 请求/响应模型
- 纯无状态分析

依赖：

- 无

#### AE-P0-02：实现 `spec-from-slots`

关联任务：

- `DCD-V2-04`

依赖：

- `AE-P0-01`

#### AE-P0-03：支持 `spec` 直通 pipeline

关联任务：

- `DCD-V2-07`

涉及：

- `RunPipelineRequest`
- `pipeline_v2_runner`
- `generate endpoint`

依赖：

- `AE-P0-02`

### 4.3 `frontend`

#### FE-P0-01：移除前端本地补问逻辑

关联任务：

- `DCD-V2-06`

目标：

- 删除 `generateClarifications`
- 删除本地拼 prompt 逻辑

依赖：

- `GS-P0-04`

#### FE-P0-02：创建页接入 creation session 状态机

关联任务：

- `DCD-V2-06`

状态：

- `idle`
- `creating_session`
- `collecting`
- `ready_to_generate`
- `generating`
- `completed`
- `failed`

依赖：

- `GS-P0-04`

#### FE-P0-03：会话恢复

关联任务：

- `DCD-V2-06`

目标：

- 页面刷新后可恢复 active session

依赖：

- `GS-P0-04`

### 4.4 `测试 / 联调`

#### QA-P0-01：fresh create 主链路 E2E

覆盖：

- 新建 session
- 问题回答
- 跳过
- generate
- completed/failed 回写

#### QA-P0-02：幂等 / revision 冲突测试

覆盖：

- 重复提交消息
- expectedRevision 冲突
- active session 唯一性

### 4.5 P0 建议排期

#### Sprint 1

- `GS-P0-01`
- `GS-P0-02`
- `AE-P0-01`
- `AE-P0-02`

#### Sprint 2

- `GS-P0-03`
- `GS-P0-04`
- `GS-P0-05`
- `AE-P0-03`
- `FE-P0-01`
- `FE-P0-02`
- `FE-P0-03`
- `QA-P0-01`
- `QA-P0-02`

---

## 5. P1 研发排期

P1 用来真正提升“理解质量”和“生成上限”。

### 5.1 `ai-engine`

#### AE-P1-01：`analyze-turn` 增加置信度输出

关联任务：

- `DCD-V2-08`

新增：

- `confidenceBySlot`
- `evidenceBySlot`
- `ambiguityFlags`
- `nextBestQuestionReason`

#### AE-P1-02：实现 `draft-plan-from-input`

关联任务：

- `DCD-V2-09`

产出：

- `planDraft`
- `draftConfidence`
- `recommendedQuestions`

#### AE-P1-03：扩展 `GameSpec`

关联任务：

- `DCD-V2-13`

新增字段：

- `session_length`
- `progression_shape`
- `reward_loop`
- `signature_moment`
- `target_audience`
- `tone`
- `reference_style`
- `complexity_budget`
- `teaching_mode`
- `comedy_device`
- `design_goals`

#### AE-P1-04：升级 `GameDesigner`

关联任务：

- `DCD-V2-14`

新增输出：

- `level_structure`
- `phase_plan`
- `reward_plan`
- `tutorial_beats`
- `signature_interactions`
- `feedback_moments`
- `failure_recovery_plan`

#### AE-P1-05：`showcase` 多候选生成

关联任务：

- `DCD-V2-16`

#### AE-P1-06：候选评分与选优

关联任务：

- `DCD-V2-17`

### 5.2 `game-service`

#### GS-P1-01：`SessionSnapshot` 增加 `planDraft`

关联任务：

- `DCD-V2-10`

#### GS-P1-02：问题选择器升级

关联任务：

- `DCD-V2-11`

目标：

- 从“固定缺失补问”升级成“高影响低置信优先”

#### GS-P1-03：支持 fork / iterate 差异化追问

关联任务：

- `DCD-V2-12`

### 5.3 `frontend`

#### FE-P1-01：新增方案草案卡片

关联任务：

- `DCD-V2-10`

能力：

- 展示计划草案
- 用户确认方向
- 用户触发“我想改一下”

#### FE-P1-02：创建页问题体验升级

关联任务：

- `DCD-V2-11`

能力：

- 展示当前关键问题
- 更明确显示“为什么问这个”
- 显示生成准备度

#### FE-P1-03：fork / iterate 会话化接入

关联任务：

- `DCD-V2-12`

### 5.4 `测试 / 联调`

#### QA-P1-01：方案草案正确性抽检

抽检维度：

- 理解是否偏题
- 与最终 spec 一致性

#### QA-P1-02：追问有效性回归

目标：

- 追问数不变多
- 但生成方向更稳

#### QA-P1-03：showcase 候选选优回归

目标：

- 候选评分选择稳定
- 不引入大规模失败

### 5.5 P1 建议排期

#### Sprint 3

- `AE-P1-01`
- `AE-P1-02`
- `GS-P1-01`
- `GS-P1-02`
- `FE-P1-01`
- `FE-P1-02`
- `QA-P1-01`
- `QA-P1-02`

#### Sprint 4

- `AE-P1-03`
- `AE-P1-04`
- `GS-P1-03`
- `FE-P1-03`

#### Sprint 5

- `AE-P1-05`
- `AE-P1-06`
- `QA-P1-03`

---

## 6. P2 研发排期

P2 是体验增强和长期闭环。

### 6.1 `ai-engine`

#### AE-P2-01：showcase QA 分层

关联任务：

- `DCD-V2-18`

目标：

- `showcase` repair 时保结构修 bug

#### AE-P2-02：审美 / 视觉评分器增强

目标：

- 候选选优不只看 QA，通过视觉层次和反馈节奏做辅助评分

### 6.2 `game-service`

#### GS-P2-01：指标与样本回流

关联任务：

- `DCD-V2-19`
- `DCD-V2-20`

目标：

- 建立创作会话质量数据闭环

#### GS-P2-02：feature flag 与灰度

关联任务：

- `DCD-V2-21`

### 6.3 `frontend`

#### FE-P2-01：showcase 提示与引导

目标：

- 明确告知：
  - 更慢
  - 更精致
  - 更适合完整创意

#### FE-P2-02：方案草案修改体验增强

目标：

- 用户可以针对某个草案字段做定向修正

### 6.4 `测试 / 联调`

#### QA-P2-01：灰度对照测试

目标：

- 对比旧创建链路和新会话链路
- 对比 `standard` 和 `showcase`

#### QA-P2-02：质量指标周报

目标：

- 形成固定复盘机制

### 6.5 P2 建议排期

#### Sprint 6+

- `AE-P2-01`
- `AE-P2-02`
- `GS-P2-01`
- `GS-P2-02`
- `FE-P2-01`
- `FE-P2-02`
- `QA-P2-01`
- `QA-P2-02`

---

## 7. 按泳道分工建议

### `game-service`

优先负责：

- 会话持久化
- 会话 API
- 问题选择器
- 任务回写
- 指标与灰度

### `ai-engine`

优先负责：

- 无状态分析
- 方案草案
- richer spec
- richer designer
- showcase 多候选与选优
- tier-aware QA

### `frontend`

优先负责：

- 创建页状态机
- 方案草案卡片
- 当前问题卡片
- 会话恢复
- fork / iterate 会话化入口

### `测试 / 联调`

优先负责：

- E2E 主链
- revision / 幂等
- 方案草案抽检
- showcase 成功率和质量回归

---

## 8. 最终建议

推荐研发顺序：

1. 先完成全部 `P0`
2. 再优先推进 `P1` 中的：
   - `AE-P1-01`
   - `AE-P1-02`
   - `GS-P1-01`
   - `GS-P1-02`
   - `FE-P1-01`
3. 最后推进 richer spec / showcase 多候选

一句话结论：

> `P0` 解决“创作会话真正上线”，`P1` 解决“理解质量和生成上限”，`P2` 解决“长期精品化和数据闭环”。
