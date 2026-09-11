# 作品生成成功率提升专项（2026-09-11）

## 目标

- 批量测试 50–100 次真实生产生成，记录日志与失败分层
- 从架构层分析根因并实施优化
- 成功率目标：99%（需 95% 单侧二项下界 ≥ 0.99，至少 50 次独立样本）

## 测试账号与入口

| 角色 | 账号 | 用途 |
| --- | --- | --- |
| 创作用户 | 18909246448 | 批量 `/api/v1/games/generate` |
| 管理员 | admin / admin123 | 任务事件、产物、统计 |

生产 API：`https://www.zlspace.ai`

## 批量测试脚本

```bash
cd gamevallies-backend

# 50 次，并发 2
python3 scripts/run_generation_yield_batch.py \
  --username 18909246448 \
  --password '***' \
  --count 50 \
  --concurrency 2 \
  --output tmp/yield-batch-20260911

# 分析结果
python3 scripts/analyze_yield_batch.py tmp/yield-batch-20260911/summary.json \
  --output tmp/yield-batch-20260911/analysis.json
```

输出：

- `ledger.jsonl`：逐条验收账本
- `summary.json`：滚动汇总与诊断
- `analysis.json`：失败分层 + 架构建议

## 生产基线（专项启动前）

近 7 日 `pipeline_run` 成功率约 **29%–43%**（内部统计），距 99% 差距极大。

最近 50 条失败任务分层（admin/tasks?status=failed）：

| failureFamily | 数量 | 典型阶段 |
| --- | ---: | --- |
| interactive_validation | 13 | runtime_simulation_qa |
| review_evidence | 5 | code_review |
| code_generation | 5 | logic_generate |
| repair_protocol / quality_repair_exhausted | 8 | code_review |
| review_infrastructure | 3 | code_review |
| contract_qa | 3 | contract_qa |
| pipeline (含 provider 403) | 3 | pipeline_run / logic_generate |
| quality_gate | 3 | code_review |

## 架构根因（顶层）

1. **验收层过严且与生成层耦合**：桌面交互探针、结构化审核证据与质量门槛共享同一 fail-closed 出口，基础设施抖动（review 不可用、504/403）直接计为作品失败。
2. **修复预算是整页重生成**：大量失败本可通过局部 patch / qa_fix 解决，却消耗 full regeneration attempt，放大上游不稳定的影响。
3. **观测与归因分散**：game-service 任务态、ai-engine 阶段、LLM 调用日志、Playwright 报告分处多表/产物，批量测试需统一 ledger 才能做版本间对照。

## 本轮代码优化（CI 修复后待部署）

`1006933` 的 yield fallback 未过 CI。本轮补齐测试与合同信号，使该架构可部署：

- 非 showcase `review_infrastructure` 继续降级到静态/runtime QA，create 集成测试与该行为对齐。
- `repair_contract` / `repair_protocol` 回退整页重生成时，`generation_guidance` 同时保留质量门槛与合同失败信号（如 `keyboard handler removed`、`search_not_unique`）。
- 窗口化 `qa_fix.syntax_structural` 截断后先回退整段 script 修复，再升整页重生成。
- 脚本行号越界（例如 HTML 行号 82 落在 1 行 script 上）会钳制到真实源码窗口，避免空窗口拼接把本可局部修复的语法错误做成无效候选。

### 1. qa_fix 截断 / 无效修复 → 整页重生成

- `qa_pipeline.run_with_auto_fix`：`LLMResponseTruncatedError` 或 repair 后代码 hash 未变且仍失败时，设置 `needs_regeneration=true`，不再在 contract_qa 空转。
- 语法修复 truncation retry 从 1 次增至 2 次。

### 2. quality gate patch 失败 → full regen

- `pipeline_v2_runner`：`repair_protocol` / `repair_contract` 不再终端失败，回退到整页重生成并保留原候选诊断。
- standard 档 full regen 预算从 2 次扩至 3 次（standard → simple → safe）。

### 3. standard/safe 审核基础设施降级

`pipeline_v2_runner._resolve_create_review`：当 `review_infrastructure` 且非 showcase 时，降级为 `review.ran=false`，仅依赖静态 QA + runtime QA 通过质量门槛，不再 fail-closed。

showcase 仍要求结构化审核。

### 4. 批量测试与 analysis 工具

- `scripts/run_generation_yield_batch.py`
- `scripts/analyze_yield_batch.py`

## 试点结果

| 批次 | 样本 | 成功 | 成功率 | 备注 |
| --- | ---: | ---: | ---: | --- |
| pilot-20260911 | 2 | 2 | 100% | 平均 320s/次 |
| yield-batch-20260911 | 进行中 | — | — | 目标 50 |

## 后续优化队列（按影响排序）

1. provider 403/504 备用路由与健康检查（pipeline / code_generation）
2. interactive_validation 探针：motion 观察窗口、控件发现容错（工具/科学/desktop）
3. review_evidence 一次纠错后仍失败 → standard 档降级策略（需人工验收对齐）
4. quality_gate patch 预算扩至 3 轮后再 full regen
5. 固定版本 ledger + binomial CI 持续验收，禁止混版本计成功率
