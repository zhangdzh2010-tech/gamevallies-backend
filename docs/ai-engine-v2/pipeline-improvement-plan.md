# GameVallies 生成 Pipeline 改进方案

> 版本: v2.0
> 日期: 2026-03-27
> 状态: Phase 1 & 2 & 3 全部已实施

---

## 1 背景与问题

Pipeline 当前存在三大问题：

| 问题 | 现状 | 目标 |
|------|------|------|
| 失败率高 | ~30% (截断 33%, 缺终止态 25%, 缺输入 25%, 解析 17%) | <10% |
| 生成慢 | 最坏 6-7 分钟 (p50 ~3.5min) | p50 <2min, p95 <3min |
| 深度浅 | 玩法单一、无关卡编排、难度曲线固定 | 多阶段玩法、丰富关卡 |

**根因**: 单次 LLM 调用同时承担设计+编码+美术+交互，6144 token 上限对有深度的 HTML5 游戏严重不足。

---

## 2 架构概览

```
用户描述
  │
  ▼
[Stage 01-02] DialogueEngine → GameSpec (10 slot)
  │
  ▼
[Stage 03] GameDesigner → GDD (规则, 数值, 碰撞, 状态机)
  │
  ▼
[Stage 04] Runtime Profile 匹配 (5 种 profile)
  │
  ▼
[Stage 05] CodeGenerator → 单文件 HTML5 (LLM 生成)
  │
  ▼
[Stage 06] QA Pipeline (6 层静态检查 + 自动修复 × 3 轮)
  │
  ▼
[Stage 06b] Runtime QA (Playwright 无头浏览器)
  │
  ▼
[Stage 06c] Code Review (LLM 语义审查)
  │
  ▼
[Stage 07] Quality Scorer → 0-10 分
```

### 关键文件

| 模块 | 文件路径 |
|------|----------|
| Pipeline 编排 | `packages/ai-engine/src/engine/pipeline_v2_runner.py` |
| 代码生成 | `packages/ai-engine/src/engine/code_generator.py` |
| QA 检查 | `packages/ai-engine/src/engine/qa_pipeline.py` |
| Runtime QA | `packages/ai-engine/src/engine/runtime_qa.py` |
| 游戏设计 | `packages/ai-engine/src/engine/game_designer.py` |
| 质量评分 | `packages/ai-engine/src/engine/quality_scorer.py` |
| 代码审查 | `packages/ai-engine/src/engine/code_reviewer.py` |
| 配置 | `packages/ai-engine/src/config/settings.py` |
| 数据模型 | `packages/ai-engine/src/api/models.py` |
| Prompt 目录 | `packages/game-service/src/game/catalogs/prompt-catalog.json` |
| Profile 目录 | `packages/game-service/src/game/catalogs/runtime-profile-catalog.json` |

---

## 3 Phase 1: 可靠性 (已实施)

目标: 失败率从 ~30% 降至 <10%

### 3.1 提升 Token 预算 (P1.1)

**问题**: 6144 token 上限导致 ~33% 的生成截断。

**方案**: 按游戏复杂度分级选择 token 预算。

| 复杂度 | 条件 | Token 上限 |
|--------|------|------------|
| simple | dodge/runner 且 ≤2 entities 且无 special_rules | 8,192 |
| standard | 默认 | 12,288 |
| complex | rpg/tower_defense 或 ≥5 entities 或 ≥3 special_rules | 16,384 |

**变更文件**:
- `settings.py`: `LLM_LONG_GENERATION_MAX_TOKENS` 6144→12288, 新增 `LLM_GENERATION_TOKEN_BUDGET_SIMPLE/STANDARD/COMPLEX`
- `code_generator.py`: 新增 `_select_token_budget(spec, budget_override)`, 替换 `_llm_generate` 和 `_llm_iterate` 中的硬编码值
- 生成 timeout 同步调高: 240s → 300s

### 3.2 预生成验证门 (P1.2)

**问题**: spec 缺失 player entity、win_condition 等字段时, LLM 生成的代码必然失败。

**方案**: 在 contract_compose 和 logic_generate 之间插入轻量校验, 自动补全缺失字段。

**检查项**:
- spec 至少有一个 player entity
- core_mechanics 非空
- game_type 属于已知类型
- win_condition 非空
- runtime contract 含 input modes

**变更文件**:
- 新建 `pre_generation_validator.py`
- `pipeline_v2_runner.py`: `_run_create_impl()` 中插入 `pre_gen_validator.validate()` + `auto_fix()` 调用

### 3.3 QA 截断检测 + 自动再生 (P1.3)

**问题**: 截断代码进入修复循环后, 修复器修语法不修设计, 3 轮修复 (150-210s) 全浪费。

**方案**:
1. `qa_pipeline.py` 新增 `_errors_look_like_truncation()` — 检测 "unexpected end of input"、"unclosed tag" 等信号
2. 修复循环中: 若 attempt ≥ 1 且仍检测到截断 → 设 `needs_regeneration=True`, 立即退出
3. `pipeline_v2_runner.py`: 检测到 `needs_regeneration` → 用 `complex` 预算 (16384 token) 重新生成

**变更文件**:
- `models.py`: `QAResult` 新增 `needs_regeneration: bool = False`
- `qa_pipeline.py`: 新增截断检测方法 + 修复循环早退逻辑
- `pipeline_v2_runner.py`: `_run_create_impl()` 处理再生, `_run_contract_qa_loop()` 也加入截断检测

### 3.4 Runtime QA 强制化 (P1.4)

**问题**: `RUNTIME_QA_REQUIRED=False` 导致生产环境可跳过浏览器测试, 发布不可玩的游戏。

**方案**:
- `RUNTIME_QA_REQUIRED` 改为 `True`
- iterate 流也传 `allow_runtime_qa_unavailable=False`
- 仅在 Playwright 基础设施不可用时 (infra 问题) 降级为 warning

**变更文件**:
- `settings.py`: `RUNTIME_QA_REQUIRED: bool = True`
- `pipeline_v2_runner.py`: iterate 流的 `allow_runtime_qa_unavailable` 改为 `False`

### 3.5 Code Review 接入 V2 Pipeline (P1.5)

**问题**: `CodeReviewer` 存在但 V2 pipeline 未调用, quality_scorer 始终收到 `LLMReviewResult(ran=False)`。

**方案**: 在 runtime QA 通过后调用 `code_reviewer.review()`, 将实际结果传入 `quality_scorer.compute()`。

**变更文件**:
- `pipeline_v2_runner.py`: `__init__` 新增 `self.code_reviewer`, `_run_create_impl` 中调用 `review()`

---

## 4 Phase 2: 速度优化 (已实施)

目标: 生成时间从 p50 ~3.5min 降至 p50 <2min, p95 <3min

### 4.1 修复循环递减效益检测

**问题**: 修复循环可能连续 3 轮都没减少 error count, 白白浪费 150-210s。

**方案**: 新增 `_is_diminishing_returns(current_errors, history)`:
- 记录每轮 error count
- 若连续 2 轮 error count 没减少 → 提前退出

**预期收益**: 节省 1-2 轮无效修复 (50-140s)

**变更文件**:
- `qa_pipeline.py`: `run_with_auto_fix()` 中新增 error history 跟踪和递减检测

**实现细节**:

```python
# 在 run_with_auto_fix 中维护 error_count_history
error_count_history: list[int] = []

# 每轮 check 后:
error_count_history.append(len(result.errors))

# 检查递减效益:
if len(error_count_history) >= 3:
    last_three = error_count_history[-3:]
    if last_three[-1] >= last_three[-2] >= last_three[-3]:
        logger.info("Fix loop showing diminishing returns; exiting early")
        break
```

### 4.2 首次通过快速路径

**问题**: 即使代码首次就通过所有 QA, 仍然走完整 contract 验证流程的 overhead。

**方案**: 在 `_run_contract_and_runtime_flow()` 开头加快速检查:
- 运行 `qa_pipeline.check()` + `_validate_contract_bundle()`
- 若全部通过 → 跳过修复循环, 直接进入 Runtime QA

**预期收益**: 对首次通过的游戏节省 ~10-30s 的循环 overhead

**变更文件**:
- `pipeline_v2_runner.py`: `_run_contract_and_runtime_flow()` 增加 fast-path 逻辑

### 4.3 扩展确定性修复

**问题**: 部分 QA 失败可以用正则替换修复 (<1ms), 但目前走 LLM 修复 (50-70s)。

**方案**: 扩展 `_apply_deterministic_repairs()` 和 `_apply_family_deterministic_repairs()`:

| 问题 | 确定性修复 | 当前方式 |
|------|-----------|---------|
| gameOver 声明但未设为 true | 注入 timeout 触发 fallback | LLM 修复 |
| localStorage/sessionStorage 使用 | 正则剥离 | LLM 修复 |
| 缺 viewport meta | 注入 | 已有 |
| 缺 charset | 注入 | 已有 |

**变更文件**:
- `qa_pipeline.py`: 扩展 `_apply_deterministic_repairs()` 和 `_apply_family_deterministic_repairs()`

**实现细节**:

```python
def _inject_terminal_state_fallback(self, code: str) -> str:
    """If gameOver is declared but never set true, inject timeout fallback."""
    if re.search(r'gameOver\s*=\s*false', code) and not re.search(r'gameOver\s*=\s*true', code):
        # 在 game loop 中注入 60s 超时 fallback
        fallback = "if(!gameOver && Date.now()-_startTime>60000){gameOver=true;}"
        code = code.replace('requestAnimationFrame',
            f'{fallback}\nrequestAnimationFrame', 1)
    return code

def _strip_storage_apis(self, code: str) -> str:
    """Remove localStorage/sessionStorage usage."""
    code = re.sub(r'localStorage\.\w+\([^)]*\)', '""', code)
    code = re.sub(r'sessionStorage\.\w+\([^)]*\)', '""', code)
    return code
```

### 4.4 成功骨架缓存

**问题**: LLM 每次从零生成完整游戏结构, 首次失败率高, 需要 2-3 次尝试。

**方案**: 按 `game_type:runtime_profile` 缓存通过 QA 的代码骨架, 生成时作为结构参考注入 prompt。

**预期收益**: 首次通过率提升, 从平均 2-3 次尝试降至 1-2 次

**变更文件**:
- 新建 `code_template_cache.py`
- `code_generator.py`: `_llm_generate()` 中查询缓存, 有则前置到 prompt
- `pipeline_v2_runner.py`: 生成成功后存入缓存

**实现细节**:

```python
class CodeTemplateCache:
    """缓存通过 QA 的代码骨架, 按 game_type:profile 索引."""

    def __init__(self):
        self._cache: dict[str, str] = {}  # key → skeleton

    def get_skeleton(self, game_type: str, runtime_profile: str) -> Optional[str]:
        return self._cache.get(f"{game_type}:{runtime_profile}")

    def store(self, game_type: str, runtime_profile: str, code: str):
        skeleton = self._extract_skeleton(code)  # 去掉游戏特定内容, 保留结构
        self._cache[f"{game_type}:{runtime_profile}"] = skeleton

    def _extract_skeleton(self, code: str) -> str:
        """保留 HTML 骨架、canvas 初始化、game loop、state machine,
        去掉具体实体定义、颜色、文案等."""
        # 截取关键结构块: DOCTYPE → canvas setup → game loop → state transitions
        ...
```

**骨架注入 prompt**:

```python
skeleton = self._template_cache.get_skeleton(spec.game_type, runtime_profile)
if skeleton:
    full_prompt = (
        "REFERENCE SKELETON (follow this HTML structure, replace game-specific content):\n"
        f"```html\n{skeleton[:3000]}\n```\n\n"
        + full_prompt
    )
```

---

## 5 Phase 3: 深度提升 (已实施)

目标: 提升游戏质量、多样性和可玩性

### 5.1 两阶段生成: 设计与编码分离

**问题**: 单次 LLM 调用同时设计和编码, token 预算不足以产出有深度的游戏。

**方案**: 拆分为两个 LLM pass:
- Pass 1 (设计): 用快速模型 (Haiku) 生成 EnrichedGDD — 关卡编排、敌人行为、难度曲线、视觉特效
- Pass 2 (编码): CodeGenerator 只实现已设计好的游戏

**新增数据模型**:

```python
class EnrichedGDD(GDD):
    """扩展 GDD, 包含 LLM 生成的设计细节."""
    level_design: list[dict] = []        # 每关配置: 敌人数、速度、spawn 模式
    enemy_behaviors: list[dict] = []     # 行为模式: zigzag/homing/patrol
    difficulty_curve_params: dict = {}   # 曲线公式、plateau 点、spike 触发
    visual_effects: list[str] = []       # 粒子轨迹、屏幕震动、闪光等
    gameplay_phases: list[dict] = []     # 玩法阶段: 热身→正常→高潮→boss
```

**变更文件**:
- 新建 `llm_game_designer.py`
- `models.py`: 新增 `EnrichedGDD`
- `pipeline_v2_runner.py`: 在 GDD 构建后插入 LLM design pass
- `prompt-catalog.json`: 新增 `prompt.llm_design_system`
- Feature flag: `ENABLE_LLM_DESIGN_PASS` (默认 False, 灰度开启)

### 5.2 丰富规则 GDD

**问题**: `GameDesigner` 仅覆盖 dodge/runner/platformer/puzzle/rhythm, 缺少 tower_defense/idle/rpg。

**方案**: 扩展 `GAME_TYPE_NUMERICS` 补全缺失类型。

**变更文件**:
- `game_designer.py`: 扩展 `GAME_TYPE_NUMERICS` 字典

### 5.3 Runtime Profile 细分

**问题**: 5 种 profile 过于粗粒度, 如 `topdown_action` 同时匹配 dodge 和 shooter。

**方案**: 拆分子 profile:
- `topdown_action` → `topdown_dodge` + `topdown_shooter`
- 每个子 profile 有针对性的 fewShotPrompt 和 contract

**变更文件**:
- `runtime-profile-catalog.json`: 新增子 profile
- `pipeline_v2_runner.py`: 扩展 `PROFILE_BY_GAME_TYPE` 映射

### 5.4 玩法感知质量评分

**问题**: quality_scorer 侧重技术指标 (QA error、代码大小), 无法衡量玩法质量。

**方案**: 新增 gameplay depth bonus:

| 维度 | 检测方式 | 加分 |
|------|---------|------|
| 关卡系统 | 代码中含 level/wave/stage | +0.5 |
| 多实体类型 | spec.entities 角色数 ≥ 3 | +0.3 |
| 视觉反馈 | 代码中含 particle/shake/flash/glow | +0.3 |
| 音效提示 | 代码中含 AudioContext/playSound | +0.2 |

上限 +2.0

**变更文件**:
- `quality_scorer.py`: 新增 `_compute_gameplay_depth_bonus()`

---

## 6 实施优先级

```
Phase 1 (已完成)                 Phase 2 (已完成)                Phase 3 (已完成)
─────────────────                ──────────────                  ──────────────
✅ P1.1 Token 预算提升           ✅ P2.1 递减效益检测             ✅ P3.1 两阶段生成
✅ P1.2 预生成验证门             ✅ P2.2 首次通过快速路径         ✅ P3.2 丰富规则 GDD
✅ P1.3 截断检测+再生            ✅ P2.3 扩展确定性修复           ✅ P3.3 Profile 细分
✅ P1.4 Runtime QA 强制化        ✅ P2.4 成功骨架缓存             ✅ P3.4 玩法感知评分
✅ P1.5 Code Review 接入 V2
```

**建议 Phase 2 实施顺序**: P2.1 → P2.3 → P2.2 → P2.4

---

## 7 验证策略

### Phase 1 验证
1. 对 10 个已知失败 spec 跑全流程, 验证通过率提升
2. E2E 回归: `scripts/run_live_full_flow_e2e.py` 的 5 个标准用例
3. 检查 quality_score 分布变化 (Code Review 接入后)

### Phase 2 验证
1. Benchmark: 20 个 spec 的端到端耗时, 对比修改前后
2. 目标: p50 < 2min, p95 < 3min
3. 验证早退逻辑: mock error 序列, 确认正确触发

### Phase 3 验证
1. A/B 测试: 单阶段 vs 两阶段生成, 对比 quality_score 分布
2. 用户评测: 10 人评分 1-5, 对比新旧流程
3. EnrichedGDD 填充率: 所有游戏类型的设计字段完整性

---

## 8 回滚方案

| Phase | 回滚方式 |
|-------|---------|
| Phase 1 | 恢复 settings 配置值 (token=6144, RUNTIME_QA=False); 删除 `pre_generation_validator.py` 的调用; 移除 code_reviewer 调用 |
| Phase 2 | 移除递减检测和 fast-path 逻辑; 清空骨架缓存 |
| Phase 3 | `ENABLE_LLM_DESIGN_PASS=False`; 子 profile 不影响现有 profile; 质量评分新维度可独立关闭 |

---

## 9 风险评估

| 风险 | 影响 | 缓解 |
|------|------|------|
| Token 预算翻倍增加 LLM 成本 | 中 | 按复杂度分级, simple 游戏仍用 8K |
| Runtime QA 强制化导致 Playwright 不可用时全部失败 | 高 | 保留 infra 不可用时的降级路径 |
| 骨架缓存 prompt 过长挤占有效 token | 低 | 骨架截取前 3000 字符 |
| 两阶段生成增加总延迟 | 中 | Design pass 用 Haiku 快速模型, 预计 <15s |
| 截断再生导致成本翻倍 | 低 | 仅在首轮修复后仍截断时触发, 概率 <10% |
