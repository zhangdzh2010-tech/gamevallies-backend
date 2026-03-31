# 动态创作会话 V2 执行方案

> 基于：[DYNAMIC_CREATION_DIALOGUE_DESIGN.md](/d:/Project/gamevallies/gamevallies-backend/docs/integration/DYNAMIC_CREATION_DIALOGUE_DESIGN.md)  
> 日期：2026-03-30  
> 状态：待排期  
> 目标：把“后端驱动补问”升级成“质量导向创作会话”

配套排期文档：

- [DYNAMIC_CREATION_DIALOGUE_RND_SCHEDULE_2026-03-30.md](/d:/Project/gamevallies/gamevallies-backend/docs/integration/DYNAMIC_CREATION_DIALOGUE_RND_SCHEDULE_2026-03-30.md)

---

## 1. 执行目标

本执行方案聚焦 3 个结果：

1. 提升用户创意被系统正确理解的概率
2. 提升进入生成链路的 `GameSpec` 质量
3. 建立 `showcase` 档的精品生成前置链路

本方案不只解决“前端补问逻辑迁到后端”，还要解决“为什么生成结果经常普通、相似、理解偏题”。

---

## 2. 当前实现差距

结合当前仓库实现，主要差距如下：

### 2.1 创作会话未真正落地

- 设计文档中的 `game_creation_sessions`、`creation-sessions` API、`analyze-turn/spec-from-slots` 主链尚未真正接到线上创建入口
- 当前创建仍以 `description/prompt` 直推为主：[create-game.dto.ts](/d:/Project/gamevallies/gamevallies-backend/packages/game-service/src/game/dto/create-game.dto.ts)
- `game-service` 仍直接组织 pipeline create/iterate，而非围绕创作会话组织：[game.service.ts](/d:/Project/gamevallies/gamevallies-backend/packages/game-service/src/game/game.service.ts)

### 2.2 意图采集仍偏浅层

- `DialogueEngine` 当前仍以启发式归类、默认槽位和预设实体/主题为主：[dialogue_engine.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/dialogue_engine.py)
- 目前没有每槽位 `confidence/evidence/ambiguity` 输出
- 目前没有“文字方案草案”这一层

### 2.3 `GameSpec` 对质量表达力不足

- 当前 `GameSpec` 更像“能生成的技术合同”，缺少：
  - `session_length`
  - `progression_shape`
  - `reward_loop`
  - `signature_moment`
  - `tone`
  - `target_audience`
  - `complexity_budget`
- 见：[models.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py)

### 2.4 Designer 仍主要是数值与布局层

- `GameDesigner` 当前重点仍是：
  - `numerics`
  - `ui_layout`
  - `input_map`
  - `state_machine`
- 缺少：
  - 关卡结构
  - 教学节奏
  - 奖励循环
  - 高光时刻
  - 失败恢复设计
- 见：[game_designer.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/game_designer.py)

### 2.5 `showcase` 仍主要靠 prompt 增强

- 已有 `generation_tier`、扩 runtime profile、`visual_pack`
- 但仍主要是单候选生成
- 缺少多候选和选优
- 见：
  - [code_generator.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/code_generator.py)
  - [pipeline_v2_runner.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/pipeline_v2_runner.py)
  - [visual_pack_catalog.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/visual_pack_catalog.py)

### 2.6 QA 仍偏“修到能跑”

- 当前 QA 的 repair family 仍主要服务于运行与契约修复：[qa_pipeline.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/qa_pipeline.py)
- `showcase` 尚无独立的“保结构修 bug”策略

---

## 3. 总体实施阶段

建议拆成 5 个阶段推进。

### 阶段 A：创作会话 MVP

目标：

- 真正落地 `creation session`
- 由后端驱动补问
- 让 `spec` 可以直通 pipeline

产出：

- `game_creation_sessions`
- `creation-sessions` API
- `ai-engine analyze-turn/spec-from-slots`
- 前端创建页切换到会话模式

### 阶段 B：质量导向会话

目标：

- 在“缺失槽位”之外，引入“低置信高影响”追问
- 增加“文字方案草案”

产出：

- `confidenceBySlot`
- `evidenceBySlot`
- `ambiguityFlags`
- `planDraft`

### 阶段 C：Richer Spec 与 Designer

目标：

- 把创意理解转成更强的生成合同
- 提升 `spec -> design` 这一步的表达力

产出：

- richer `GameSpec`
- richer `Designer` 输出

### 阶段 D：Showcase 精品链路

目标：

- 建立区别于默认生成的高质量链路

产出：

- 多候选生成
- 候选评分与选优
- showcase QA 分层

### 阶段 E：评估、灰度与持续优化

目标：

- 形成质量闭环，而不是只做一次性实现

产出：

- 指标体系
- 灰度开关
- 失败样本回流
- prompt / profile / designer 数据闭环

---

## 4. 任务拆解

以下任务按 `DCD-V2-xx` 编号。

### 4.1 阶段 A：创作会话 MVP

#### DCD-V2-01 `P0`：落地 `game_creation_sessions`

目标：

- 新增 `game_creation_sessions` 数据表
- 承载创作会话全生命周期

涉及：

- [schema.prisma](/d:/Project/gamevallies/gamevallies-backend/prisma/schema.prisma)

关键字段：

- `id`
- `userId`
- `status`
- `initialPrompt`
- `titleDraft`
- `revision`
- `slotState`
- `missingRequired`
- `skippedSlots`
- `currentQuestion`
- `conversation`
- `generatedGameId`
- `generationTaskId`
- `entryMode`
- `sourceGameId`
- `questionBudget`

验收：

- migration 可执行
- 对现有线上数据无 destructive 影响
- 单用户活跃会话查询性能可接受

#### DCD-V2-02 `P0`：定义创作会话 DTO / Types / Constants

目标：

- 统一后端契约，避免 controller/service/frontend 各自拼结构

涉及：

- `packages/game-service/src/game/dto/creation-session.dto.ts`
- `packages/game-service/src/game/types/creation-session.types.ts`
- `packages/game-service/src/game/creation-session.constants.ts`

验收：

- 所有 creation session API 复用统一类型

#### DCD-V2-03 `P0`：实现 `ai-engine` 无状态 `analyze-turn`

目标：

- 把现有 `DialogueEngine` 拆成可在线业务使用的无状态分析接口

涉及：

- [dialogue_engine.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/dialogue_engine.py)
- [generate.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/endpoints/generate.py)
- [models.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py)

输出：

- `reply`
- `slots`
- `slotsUpdated`
- `missingRequired`
- `slotFillPct`

验收：

- 相同输入下结果稳定
- 不依赖进程内 `_sessions`

#### DCD-V2-04 `P0`：实现 `spec-from-slots`

目标：

- 从结构化槽位生成 `GameSpec`

涉及：

- [dialogue_engine.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/dialogue_engine.py)
- [generate.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/endpoints/generate.py)

验收：

- 有 `spec` 时可跳过二次弱意图解析

#### DCD-V2-05 `P0`：`game-service` 落地 creation session API

目标：

- 落地以下接口：
  - `POST /api/v1/games/creation-sessions`
  - `GET /api/v1/games/creation-sessions/active`
  - `GET /api/v1/games/creation-sessions/:id`
  - `POST /api/v1/games/creation-sessions/:id/messages`
  - `POST /api/v1/games/creation-sessions/:id/skip`
  - `POST /api/v1/games/creation-sessions/:id/generate`
  - `POST /api/v1/games/creation-sessions/:id/abandon`

涉及：

- `packages/game-service/src/game/creation-session.service.ts`
- `packages/game-service/src/game/game.controller.ts`

验收：

- revision 冲突可控
- 幂等提交可控
- 任务状态能回写 session

#### DCD-V2-06 `P0`：前端创建页切换到会话模式

目标：

- 删掉前端本地 `generateClarifications` 逻辑
- 改为完全吃后端 `SessionSnapshot`

涉及：

- `../gamevallies-frontend/src/pages/create/index.jsx`

验收：

- 页面刷新可恢复
- 不再本地拼 prompt

#### DCD-V2-07 `P0`：`spec` 直通 pipeline

目标：

- 当 session 已完成高质量槽位收集后，生成直接走 `spec`

涉及：

- [models.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py)
- [generate.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/endpoints/generate.py)
- [pipeline_v2_runner.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/pipeline_v2_runner.py)

验收：

- 传 `spec` 时不再重复做弱 intent parse

---

### 4.2 阶段 B：质量导向会话

#### DCD-V2-08 `P0`：给 `analyze-turn` 增加置信度输出

目标：

- 每个槽位输出置信度和证据

新增字段：

- `confidenceBySlot`
- `evidenceBySlot`
- `ambiguityFlags`
- `nextBestQuestionReason`

验收：

- 后端能够根据低置信高影响规则选题

#### DCD-V2-09 `P0`：新增 `draft-plan-from-input`

目标：

- 根据初始创意和当前槽位，生成给用户看的“方案草案”

接口建议：

- `POST /api/v1/ai/dialogue/draft-plan-from-input`

输出对象：

- `title`
- `summary`
- `concept`
- `interaction`
- `objective`
- `pacing`
- `visualDirection`
- `signatureMoment`

验收：

- 初始输入后就能拿到草案
- 草案能被用户确认或修正

#### DCD-V2-10 `P0`：`SessionSnapshot` 支持 `planDraft`

目标：

- 会话快照直接向前端返回方案草案

新增字段：

- `planDraft`
- `confidenceSummary`
- `questionStrategy`

验收：

- 前端创建页不需要再自行拼“系统理解摘要”

#### DCD-V2-11 `P1`：问题选择器升级

目标：

- 从“按固定顺序缺失补问”升级为“高影响低置信优先”

实现：

- `slot_impact`
- `confidence`
- `ambiguity_weight`
- `repeat_penalty`

验收：

- 追问数量减少
- 问题更聚焦

#### DCD-V2-12 `P1`：fork / iterate 差异化追问

目标：

- `fork` 和 `iterate` 不重问整套基础问题

策略：

- `fork` 最多 1 到 2 题
- `iterate` 只围绕改动目标追问

---

### 4.3 阶段 C：Richer Spec 与 Designer

#### DCD-V2-13 `P0`：扩展 `GameSpec`

目标：

- 新增质量相关字段

建议字段：

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

涉及：

- [models.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py)

#### DCD-V2-14 `P1`：升级 `GameDesigner`

目标：

- 从数值器升级为更完整的设计程序

新增输出：

- `level_structure`
- `phase_plan`
- `reward_plan`
- `tutorial_beats`
- `signature_interactions`
- `feedback_moments`
- `failure_recovery_plan`

涉及：

- [game_designer.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/game_designer.py)

验收：

- `CodeGenerator` 不再完全依赖 prompt 自发脑补结构

#### DCD-V2-15 `P1`：`spec -> design` 丰富化透传

目标：

- 让 richer spec 真正进入 codegen prompt 和 runtime 约束

涉及：

- [pipeline_v2_runner.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/pipeline_v2_runner.py)
- [code_generator.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/code_generator.py)

---

### 4.4 阶段 D：Showcase 精品链路

#### DCD-V2-16 `P1`：`showcase` 多候选生成

目标：

- `showcase` 生成 2 到 3 个候选，而不是 1 个

策略：

- 候选共享同一 richer spec
- 变化点体现在：
  - runtime profile
  - pacing plan
  - visual pack bias
  - candidate prompt variation

#### DCD-V2-17 `P1`：候选评分与选优

目标：

- 借助 QA 和质量评分挑出最优候选

评分来源：

- 运行时 QA
- 结构完整度
- 反馈强度
- 与 `planDraft` 一致性
- 视觉层次

涉及：

- `quality_scorer`
- `pipeline_v2_runner`

#### DCD-V2-18 `P1`：`showcase` repair 分层

目标：

- repair 时尽量保留复杂结构

策略：

- `safe`: 当前策略
- `standard`: 适中保结构
- `showcase`: 禁止轻易删子系统、删高光节点、删反馈层

涉及：

- [qa_pipeline.py](/d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/qa_pipeline.py)

---

### 4.5 阶段 E：评估、灰度与持续优化

#### DCD-V2-19 `P0`：会话与生成指标

目标：

- 建立质量闭环指标

建议指标：

- 首次草案确认率
- 平均追问轮次
- 追问后直接生成率
- 生成后首次试玩满意代理指标
- 迭代返工率
- showcase 成功率

#### DCD-V2-20 `P1`：失败样本回流

目标：

- 对失败生成、低质量生成建立可回放样本池

建议存储：

- `initial_prompt`
- `planDraft`
- `slots`
- `spec`
- `designer output`
- `candidate score`
- `final failure reason`

#### DCD-V2-21 `P1`：feature flag 与灰度

目标：

- 新旧创建流程、质量会话、showcase 多候选可独立灰度

建议开关：

- `CREATION_SESSION_ENABLED`
- `CREATION_PLAN_DRAFT_ENABLED`
- `CREATION_CONFIDENCE_ENABLED`
- `SHOWCASE_MULTI_CANDIDATE_ENABLED`
- `SHOWCASE_QA_TIERED_REPAIR_ENABLED`

---

## 5. 推荐排期批次

### 批次 1：把创作会话跑通

- `DCD-V2-01`
- `DCD-V2-02`
- `DCD-V2-03`
- `DCD-V2-04`
- `DCD-V2-05`
- `DCD-V2-06`
- `DCD-V2-07`

结果：

- 后端驱动补问真正上线
- `spec` 可直通

### 批次 2：先解决“理解偏题”

- `DCD-V2-08`
- `DCD-V2-09`
- `DCD-V2-10`
- `DCD-V2-11`
- `DCD-V2-12`

结果：

- 有文字方案草案
- 问题不再只是缺槽位，而是更有质量导向

### 批次 3：提升生成输入质量

- `DCD-V2-13`
- `DCD-V2-14`
- `DCD-V2-15`

结果：

- richer spec
- richer designer

### 批次 4：做 showcase 精品链路

- `DCD-V2-16`
- `DCD-V2-17`
- `DCD-V2-18`

结果：

- `showcase` 从“更强 prompt”升级成“精品生成链路”

### 批次 5：闭环与灰度

- `DCD-V2-19`
- `DCD-V2-20`
- `DCD-V2-21`

---

## 6. 依赖关系

关键依赖如下：

1. `DCD-V2-01/02` 是所有 creation session 任务前提
2. `DCD-V2-03/04` 是 `DCD-V2-05` 前提
3. `DCD-V2-05` 是 `DCD-V2-06` 前提
4. `DCD-V2-08/09` 建议在批次 1 稳定后推进
5. `DCD-V2-13/14/15` 依赖 `spec` 直通先落地
6. `DCD-V2-16/17/18` 依赖 richer spec / designer

---

## 7. 验收标准

### MVP 验收

1. fresh create 全链路由后端驱动补问
2. 页面刷新后可恢复 active session
3. 生成支持 `spec` 直通
4. 前端不再本地拼 prompt

### 质量验收

1. 用户首次输入后能看到方案草案
2. 追问数量降低，但关键信息质量提升
3. `showcase` 结果在结构和视觉上明显区别于 `safe`
4. 复杂结果在 QA 后不再被频繁修回普通 demo

### 指标验收

1. 方案草案确认率达到目标阈值
2. 生成后首次迭代率下降
3. showcase 失败率可控

---

## 8. 风险与缓解

### 风险 1：会话链路复杂度提高

缓解：

- 先做 MVP
- 用 feature flag 灰度
- 创建流程保留老入口兜底

### 风险 2：追问过多影响转化

缓解：

- 每轮只问 1 题
- 保留 `skip`
- 保留“信息足够时直接生成”

### 风险 3：showcase 成本和耗时增加

缓解：

- 仅 `showcase` 档开启多候选
- 限制为 2 到 3 候选
- 对普通流量默认仍走 `standard`

### 风险 4：QA 复杂度上升

缓解：

- tier-aware repair 仅在 `showcase` 启用
- 保留 fail-fast 和降级策略

---

## 9. 推荐实施结论

推荐研发节奏：

1. 先完成批次 1 和批次 2
2. 让系统先具备“理解清楚再生成”的能力
3. 再推进 richer spec / designer
4. 最后引入 showcase 多候选与选优

一句话建议：

> 先把“创意理解质量”做起来，再去拔高“代码生成质量”，否则后段再强，也只是在放大前段理解误差。
