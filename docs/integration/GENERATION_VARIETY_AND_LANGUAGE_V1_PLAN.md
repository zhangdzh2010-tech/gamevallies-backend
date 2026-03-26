# 生成多样性与语言一致性整改方案

最后更新：2026-03-25

## 背景

当前生成链路存在两个明显问题：

- 模糊描述容易收敛成少数几种街机骨架，尤其是类似 `space dodge` 的结果。
- 中文用户生成的游戏，界面文案经常仍然是英文。

这不是单一 prompt 问题，而是由以下链路叠加导致：

- 意图解析失败或不充分时，启发式兜底过度依赖 `dodge / runner / shooter`。
- `GameSpec` 的默认实体、默认目标、默认视觉预设过于模板化。
- 运行时 profile 数量较少，很多请求最终复用相同骨架。
- 对话层会跟随用户语言，但代码生成、修复和 UI 默认文案没有把语言要求贯穿下去。

## 目标

### V1

优先解决最明显的体验问题：

- 让玩家可见文本跟随用户语言，中文请求默认生成中文 UI。
- 弱化模糊描述时的单一默认模板，让生成结果在主题和表层玩法上更分散。
- 不扩大 runtime profile 范围，尽量用低风险方式提升差异度。

### V2

在 V1 稳定后继续推进：

- 扩 runtime profile 类型和选择策略。
- 将“模糊描述 -> 候选 profile 打分 -> 分流”的机制替代当前硬映射。
- 在后台暴露命中的 profile、语言和兜底原因，便于观察效果。

## V1 范围

### 1. 语言贯通

- 在 `GameSpec` 中新增 `ui_language`。
- 解析阶段从用户描述中推断 `ui_language`，当前先稳定支持：
  - `zh-CN`
  - `en-US`
- 代码生成 prompt、迭代 prompt、QA 修复 prompt 增加统一语言约束：
  - 所有玩家可见文本必须使用 `ui_language`
  - 变量名、函数名、JSON key 可继续使用英文
- `GameDesigner` 生成本地化 UI label 示例，供 codegen prompt 使用。

### 2. 模糊描述去模板化

- 为 `dodge / runner / shooter / puzzle / platformer / rhythm` 提供多套默认实体原型，而不是固定一套。
- 通过稳定的 hash 选择实体和视觉变体，避免每次随机导致结果不可复现。
- 增加更多主题识别词，减少“无主题时总落到 space 风格”的概率。
- 当用户没有明确主题时，默认使用非 space 的中性视觉预设池。

### 3. Prompt 对齐

- 更新 `prompt.code_gen_system`
- 更新 `prompt.game_design_template`
- 更新 `prompt.intent_detail_template`
- 更新 `prompt.qa_fix`
- 更新 `prompt.qa_fix_fast`
- 更新迭代相关 prompt，明确“修改时也必须保持当前 UI 语言”

## 非目标

本轮不做以下事情：

- 不新增 runtime profile。
- 不修改 profile 选择算法为多候选打分。
- 不新增数据库字段存储 `ui_language`。
- 不调整公开接口返回结构。

## 验收标准

- 中文输入生成的游戏，其 `Start / Restart / Score / Lives / Game Over` 等玩家可见文案默认为中文。
- QA 修复后不会把中文 UI 修回英文 UI。
- 相同类型的模糊输入，在默认实体、主题和视觉上不再总是同一套模板。
- 新增测试覆盖：
  - `ui_language` 推断
  - `GameSpec` 默认文案本地化
  - codegen prompt 包含语言约束
  - QA repair prompt 包含语言约束
  - 默认实体/视觉变体不再固定单一模板

## 实施顺序

1. 新增整改文档并冻结 V1 边界
2. 在 `ai-engine` 中新增 `ui_language` 数据通路
3. 在 `dialogue_engine` 中本地化默认文案与多样化实体/视觉预设
4. 在 `game_designer` / `code_generator` / `qa_pipeline` 中贯通语言约束
5. 更新 prompt catalog 与默认 bundle 文案
6. 补充自动化测试并验证
