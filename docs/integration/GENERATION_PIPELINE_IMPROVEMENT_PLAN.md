# Game Generation Pipeline Improvement Plan

最后更新：2026-03-20

本文档用于收敛“游戏生成 8 个阶段”的优化任务，重点覆盖：

- 每个阶段的重试机制
- 用户可见的生成中提示
- 失败日志记录与排查

## 1. 目标口径

- Stage 06 QA：总共 4 轮
  - 1 次初检
  - 最多 3 次自动修复重试
- 其他生成阶段：总共 3 轮
  - 1 次首次执行
  - 最多 2 次重试
- 生成过程中必须向用户明确展示当前阶段与重试次数
- 最终失败时，必须能定位到失败阶段、失败原因、重试次数

## 2. 当前实现现状

- P0 第一轮代码改造后，`POST /api/v1/ai/pipeline/run` 已恢复真实 Stage 02 与 Stage 04 主链
- Stage 05、Stage 06、Stage 08 已接入第一轮重试和结构化失败日志
- ai-engine 的细粒度进度事件已桥接到默认 `game-service /ws` 用户通道
- 生成失败终态已补充 `gen:error` 事件，`notification` 仅保留兼容用途
- 失败详细原因已持久化到数据库字段：`failedStage`、`failedReason`、`retryCount`、`lastErrorAt`
- 后台管理页已可查看失败详情，并新增失败阶段、重试次数、质量分等聚合统计
- 迭代链路的发布落库已复用 Stage 08 的重试逻辑，后台失败统计也会纳入带失败字段的迭代记录

## 2.1 业务旅程复查发现

- 已修复：`game-service` 不再把 ai-engine 返回的 `5xx/504` 当成可重试错误，避免整单重复启动生成任务
- 已修复：阶段级重试提示已从 ai-engine 桥接到默认 `game-service /ws` 用户通道
- 已修复：`game-service` 的 Jest 用例已替换为真实 `GameService` / `GameWebSocketGateway` / `InternalGenerationController` 实现测试
- 已修复：`packages/game-service/README.md` 的生成返回结构与 WebSocket 事件说明已更新到当前真实契约

## 3. 分阶段任务清单

### 3.1 Stage 01 - Dialogue Engine

- [ ] 为槽位提取增加统一重试包装，总共 3 轮
- [ ] 为回复生成增加统一重试包装，总共 3 轮
- [ ] 用户提示：`理解需求失败，正在重试（1/2）`
- [ ] 日志记录：`stage=dialogue`、`step=slot_extract/reply`、`attempt`、`duration_ms`、`error`

### 3.2 Stage 02 - Intent Parser

- [ ] 主链恢复调用真实 intent parser，不再直接使用 `_mock_parse`
- [ ] 解析失败时总共 3 轮
- [ ] 仅在全部失败后才 fallback 到 `_mock_parse`
- [ ] 用户提示：`意图解析失败，正在重试（1/2）`
- [ ] 日志记录：是否进入 fallback、fallback 原因、最终 `game_type`

### 3.3 Stage 03 - Game Designer

- [ ] `design(spec)` 接入统一重试包装，总共 3 轮
- [ ] 全部失败后才使用默认 `GDD()`
- [ ] 用户提示：`游戏数值设计失败，正在重试（1/2）`
- [ ] 日志记录：`stage=design`、`attempt`、`error`、`fallback=default_gdd`

### 3.4 Stage 04 - Template Matcher

- [ ] 主链恢复真实模板匹配流程
- [ ] 模板匹配总共 3 轮
- [ ] 全部失败后再降级到 `llm` path
- [ ] 用户提示：`模板匹配失败，正在重试（1/2）`
- [ ] 日志记录：`template_id`、`confidence`、`path`、`attempt`

### 3.5 Stage 05 - Code Generator

- [ ] 模板生成 / hybrid / full LLM 统一接入重试，总共 3 轮
- [ ] 仅对可重试错误执行重试
- [ ] 最终才允许降级到 skeleton 或 mock
- [ ] 用户提示：`代码生成失败，正在重试（1/2）`
- [ ] 日志记录：`strategy`、`model`、`attempt`、`fallback_to`

### 3.6 Stage 06 - QA Pipeline

- [ ] 保持总共 4 轮：1 次初检 + 最多 3 次自动修复
- [ ] 区分“确定性修复”和“LLM 修复”
- [ ] 用户提示：`质量检查未通过，正在自动修复（1/3）`
- [ ] 日志记录：每轮错误摘要、修复方式、最终 `qa_retries`
- [ ] 最终失败时返回明确失败摘要，而不是笼统“生成失败”

### 3.7 Stage 07 - Iteration Engine

- [ ] 迭代生成总共 3 轮
- [ ] 迭代后的 QA 复用 Stage 06 规则，总共 4 轮
- [ ] 用户提示：
  - `代码修改失败，正在重试（1/2）`
  - `修改后的质量检查未通过，正在修复（1/3）`
- [ ] 日志记录：`iteration_type`、`attempt`、`error`、是否回退旧代码

### 3.8 Stage 08 - Publish Engine

- [ ] bundle 保存、game 状态更新、完成通知总共 3 轮
- [ ] 增加半成功保护，避免 bundle 与 game 状态不一致
- [ ] 用户提示：`发布失败，正在重试（1/2）`
- [ ] 日志记录：`publish_step`、`attempt`、`error`

## 4. 横向改造任务

### 4.1 统一重试能力

- [ ] 新增统一 retry helper
- [ ] 明确可重试错误范围：
  - HTTP 5xx
  - 限流
  - 临时网络异常
  - 短暂超时
- [ ] 明确不可重试错误范围：
  - 参数错误
  - 明确的内容校验错误
  - 不可恢复的业务错误

### 4.2 用户提示与 WebSocket

- [ ] WebSocket 进度事件增加 `attempt`、`maxAttempts`
- [ ] 阶段提示统一格式：`{阶段名}失败，正在重试（1/2）`
- [ ] 最终失败提示统一格式：`{阶段名}失败，已重试 2 次`
- [ ] 保留当前阶段英文 code，同时补齐面向用户的中文文案

### 4.3 失败日志与持久化

- [ ] 统一日志字段：`game_id`、`user_id`、`stage`、`attempt`、`error_type`、`error_message`
- [ ] 成功结果继续保留 `qa_retries`、`generation_time_ms`
- [ ] 新增最终失败持久化字段：
  - `failedStage`
  - `failedReason`
  - `retryCount`
  - `lastErrorAt`
- [ ] 后台管理页增加失败详情查看能力

## 5. 建议优先级

### P0

- [x] 恢复真实 Stage 02 和 Stage 04
- [x] 为 Stage 05、Stage 06、Stage 08 接入第一轮重试
- [x] 给用户补齐阶段重试提示
  - 说明：ai-engine 的阶段事件已桥接到默认 `game-service /ws` 通道
- [x] 给最终失败补齐结构化日志
- [x] 收敛 AI 服务外层重试策略，避免整单重复生成
- [x] 修正与真实行为不一致的生成链路文档和测试

### P1

- [x] Stage 03、Stage 07 接入统一重试
- [x] 失败信息持久化到数据库
- [x] 后台展示失败阶段和失败原因

### P2

- [x] 优化各阶段提示文案
- [x] 增加更多重试统计与质量分析报表
- [ ] 基于历史失败数据继续调优阶段策略

## 6. 验收标准

- [ ] 用户在生成过程中能看到明确阶段名
- [ ] 用户在重试时能看到 `重试中（x/y）`
- [ ] Stage 06 QA 总共 4 轮，其余阶段总共 3 轮
- [ ] 每次失败都能在日志中定位到阶段、重试次数和错误原因
- [ ] 最终失败后，后台可查到失败阶段和失败摘要
- [ ] 主链真实执行的阶段与文档定义保持一致
- [x] 默认用户链路不会因为上游 `5xx/504` 再次整单启动重复生成
