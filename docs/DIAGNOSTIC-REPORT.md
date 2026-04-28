# GameVallies 后端深度诊断报告

> 生成时间：2026-04-03
> 审查范围：ai-engine、game-service、nginx、docker-compose 全链路

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [问题一：首轮交互慢（5s+）](#2-问题一首轮交互慢5s)
3. [问题二：游戏生成慢 & Token 消耗大](#3-问题二游戏生成慢--token-消耗大)
4. [问题三：流程过重、枷锁过严、游戏太简单](#4-问题三流程过重枷锁过严游戏太简单)
5. [问题四：并发能力不足（~20 上限）](#5-问题四并发能力不足20-上限)
6. [附录 A：全链路 LLM 调用清单](#6-附录-a全链路-llm-调用清单)
7. [附录 B：Prompt 模板与输出格式详解](#7-附录-bprompt-模板与输出格式详解)
8. [附录 C：QA 检查点与修复机制](#8-附录-cqa-检查点与修复机制)
9. [附录 D：关键配置参数速查](#9-附录-d关键配置参数速查)
10. [优先级排序与行动建议](#10-优先级排序与行动建议)

---

## 1. 系统架构总览

```
┌──────────────┐
│   Frontend   │
└──────┬───────┘
       │  HTTP + WebSocket (Socket.io)
       │
┌──────▼───────┐     ┌─────────────────┐
│    Nginx     │────►│  user-service   │ :3001 (NestJS)
│  Rate Limit  │     │  auth / billing  │
│  + Proxy     │     └─────────────────┘
└──────┬───────┘
       │
┌──────▼──────────────┐     ┌───────────────────────┐
│   game-service      │────►│      AI Engine         │
│   :3002 (NestJS)    │     │   :8000 (FastAPI)      │
│   session / gen     │◄────│   8-stage pipeline     │
│   WebSocket relay   │     └───────────────────────┘
└─────────────────────┘              │
       │                             │ LLM API Calls
┌──────▼──────┐              ┌───────▼───────┐
│   MySQL     │              │ MiniMax / Claude│
│   MongoDB   │              │ DeepSeek       │
│   Redis     │              └───────────────┘
└─────────────┘
```

### 8 阶段生成管线

| Stage | 名称 | 类型 | LLM 调用 |
|-------|------|------|----------|
| 01 | Dialogue Engine | 多轮对话槽位填充 | 2-3 次/轮 |
| 02 | Intent Parser | 描述 → GameSpec | 1-2 次 |
| 03 | Game Designer | GameSpec → GDD | 0 次（规则引擎）或 1 次（可选 LLM） |
| 04 | Template Matcher | GDD → 模板匹配 | 0 次 |
| 05 | Code Generator | GDD → HTML5 游戏 | 1 次 |
| 06 | QA Pipeline | 6 级检查 + 自动修复 | 0-3 次修复 |
| 06b | Runtime QA | Playwright 运行时验证 | 0-2 次修复 |
| 07 | Iteration Engine | 用户反馈 → 增量修改 | 1-2 次 |

---

## 2. 问题一：首轮交互慢（5s+）

### 2.1 请求链路时序

```
T+0ms     用户 POST /games/creation-sessions {prompt: "做个打飞机游戏"}
T+50ms    game-service 写 DB，返回 {status: 'initializing'}  ← 前端拿到响应
T+50ms    game-service 异步调用 AI Engine POST /api/v1/ai/dialogue/analyze-turn
T+100ms   AI Engine → DialogueEngine.analyze_turn()
            │
            ├── LLM Call #1: slot_extraction（串行等待）
            │   ├── System: prompt.slot_extraction_system + prompt.slot_output_contract
            │   ├── User: 对话历史 [{role:"user", content:"做个打飞机游戏"}]
            │   ├── max_tokens: 640
            │   ├── timeout: 4s (FAST_DIALOGUE_SLOT_REQUEST_TIMEOUT_S)
            │   └── 耗时: 1.5-4s
            │
            ├── JSON 解析 + 可选 Repair Call #2（如果 JSON 格式错误）
            │   ├── System: prompt.slot_json_repair_system + prompt.slot_output_contract
            │   ├── User: prompt.slot_json_repair_user_template.format(source_text=..., raw_parser_output=...)
            │   ├── max_tokens: 640
            │   └── 耗时: 1-3s（条件触发）
            │
            └── LLM Call #3: dialogue_reply（必须等 slot 提取完）
                ├── System: prompt.dialogue_system.format(slot_summary=..., missing_slots=...)
                ├── User: 对话历史
                ├── max_tokens: 2048
                ├── prefer_fast: true
                └── 耗时: 1.5-4s

T+3~11s   AI Engine 返回分析结果
T+3~11s   game-service CAS 更新 session → WebSocket push session:updated
T+3~11s   前端收到第一个问题  ← 用户可见延迟
```

### 2.2 根因分析

| 根因 | 代码位置 | 影响 |
|------|---------|------|
| **3 次串行 LLM 调用** | `dialogue_engine.py` _llm_process() | 最小延迟 = 3 次 × 1.5s = 4.5s |
| Slot 提取必须先于 Reply 生成 | 业务逻辑依赖：reply 需要 slot 上下文 | 无法并行化（当前设计） |
| 每次新建 httpx.AsyncClient | `llm_client.py` complete() | 额外 TCP 握手 200-500ms |
| 无首轮快速通道 | 首轮也走完整 LLM 流程 | 首轮体验最差 |
| 30s 超时兜底太长 | `creation-session.service.ts` L227 | 异常时用户等 30s |

### 2.3 Slot 提取 LLM 调用详情

**System Prompt 结构**（两部分拼接）：

```
[prompt.slot_extraction_system 内容]
  — 指导 LLM 从对话中提取游戏设计槽位

[prompt.slot_output_contract 内容]
  — 强制输出 JSON 格式，schema 如下：
```

**期望输出格式**：
```json
{
  "game_type": "casual | puzzle | educational | funny",
  "core_mechanic": "string: 核心玩法描述",
  "theme": "string: 主题/世界观",
  "input_method": "touch | swipe | drag | tilt",
  "win_condition": "string: 胜利条件",
  "difficulty": "easy | medium | hard | progressive",
  "visual_style": "string: 视觉风格（可选）",
  "audio_style": "string: 音频风格（可选）",
  "special_rules": "string: 特殊规则（可选）",
  "reference_game": "string: 参考游戏（可选）"
}
```

### 2.4 Dialogue Reply LLM 调用详情

**System Prompt**（动态构建）：

```
[prompt.dialogue_system 模板内容]
  — 包含 {slot_summary} 和 {missing_slots} 占位符

实际注入：
  slot_summary:
    Game Type: casual
    Core Mechanic: (unknown)
    Theme: 太空
    Input Method: (unknown)
    Win Condition: (unknown)
    Difficulty: (unknown)

  missing_slots: Core Mechanic, Input Method, Win Condition, Difficulty
```

**期望输出格式**：纯文本自然语言回复（中文或英文，取决于 locale）

**Fallback 机制**：如果 LLM 调用失败，使用预置问题模板：
```python
# 中文预置问题
"game_type":     "这个游戏更偏休闲、益智、教育还是搞笑？"
"core_mechanic": "玩家在这个游戏里最核心、最常做的操作是什么？"
"theme":         "你希望游戏呈现什么主题、世界观或情境？"
"input_method":  "玩家主要通过点击、滑动还是拖拽来操作？"
"win_condition":  "这一局里玩家怎样算赢，或者达成了什么目标？"
"difficulty":    "整体难度你更想要轻松、标准还是逐步变难？"
```

### 2.5 解决方案

| 方案 | 预期收益 | 改动量 | 说明 |
|------|---------|--------|------|
| **首轮快速通道** | 延迟 < 1s | 中 | 首轮跳过 LLM，用预置欢迎语 + 规则引擎推断首个 slot 值，异步补充 LLM 分析 |
| **并行化 slot + reply** | -2~3s | 中 | 首轮 reply 不依赖 slot 结果（可用通用欢迎语），后续轮次可用上一轮 slot 缓存 |
| **流式 SSE 返回** | 感知延迟 -80% | 中 | 用户看到首 token 仅需 ~500ms |
| **httpx 全局连接池** | -200~500ms | 低 | 复用 TCP 连接 |
| **超时兜底缩短** | 异常体验改善 | 极低 | 30s → 10s |

---

## 3. 问题二：游戏生成慢 & Token 消耗大

### 3.1 生成全流程 LLM 调用拓扑

```
用户点击"生成"
  │
  ├── spec-from-slots（如走 session 路径）
  │   └── LLM #1: intent_parse — 640 tokens — 1-3s
  │
  ├── Stage 03: Game Designer（规则引擎，无 LLM）
  │   └── 可选 LLM #2: llm_design.enrich — 4096 tokens — 3-8s
  │       （ENABLE_LLM_DESIGN_PASS=false，当前关闭）
  │
  ├── Stage 05: Code Generation
  │   └── LLM #3: code_generate.full — 8K~16K tokens — 15-60s ★ 最耗时
  │
  ├── Stage 06: QA Pipeline（6 级静态检查）
  │   ├── 通过 → 无额外 LLM
  │   ├── 确定性修复（注入 bridge）→ 无 LLM
  │   └── LLM 修复 → 最多 3 轮：
  │       ├── LLM #4: qa_fix.{family} — 4K~12K tokens — 10-40s
  │       ├── LLM #5: qa_fix.{family} — 4K~12K tokens — 10-40s
  │       └── LLM #6: qa_fix.{family} — 4K~12K tokens — 10-40s
  │
  └── Stage 06b: Runtime QA（Playwright 浏览器验证）
      ├── 启动 Chromium — 3-8s
      ├── 运行检查 — 5-10s
      └── 修复（如需要）→ 最多 2 轮：
          ├── LLM #7: qa_fix — 4K~12K tokens — 10-40s
          └── LLM #8: qa_fix — 4K~12K tokens — 10-40s
```

### 3.2 Token 消耗明细

#### 代码生成（Stage 05）— 主要开销

**System Prompt 构成**（`_build_system_prompt()`）：

| 层 | 来源 | 估算 Token |
|----|------|-----------|
| Locked Contract | bundle.runtime.locked_contract | 200-400 |
| Product Policy | bundle.product.policy | 200-400 |
| Code Gen System | prompt.code_gen_system | 500-1000 |

**User Prompt 构成**（`_llm_generate()` 12 层拼接）：

| 层 | 来源 | 估算 Token | 内容概要 |
|----|------|-----------|---------|
| 1. Logic Generate Policy | bundle.product.logic_generate | 200-400 | 生成策略指令 |
| 2. Generation Tier Block | 动态构建 | 150-250 | safe/standard/showcase 行为指导 |
| 3. Visual Pack Direction | visual_pack_catalog | 200-400 | HUD/按钮/背景/动效/粒子风格 |
| 4. Profile Few-Shot | bundle.runtime.profile.{type} | 300-800 | 运行时档案 + 示例 |
| 5. Structured Design Doc | prompt.game_design_template | 400-800 | 70+ 变量替换的游戏设计文档 |
| 6. Design Program | 动态构建 | 300-600 | 关卡结构/奖励循环/教程节拍 |
| 7. Critical Intent | 动态构建 | 200-400 | 核心机制/主题/胜利条件/参考游戏 |
| 8. UI Language | 动态构建 | 100-150 | 语言要求（zh-CN/en-US） |
| 9. Runtime Contract | prompt.runtime_contract_summary | 200-350 | 状态机/输入/禁止 API/方向 |
| 10. Implementation Budget | 动态构建 | 300-500 | 实体上限/子系统限制/Tier 约束 |
| 11. Mobile Layout | prompt.mobile_layout_guardrails | 250-400 | 方向/缩放/字号 |
| 12. Platform Standard | prompt.platform_standard | 100-150 | Canvas/输入/网络要求 |
| 13. Enriched Design (可选) | LLM 设计传递结果 | 0-600 | 关卡/行为/难度曲线 |
| 14. Reference Skeleton (可选) | 模板缓存 | 0-800 | HTML 结构参考 |

**总 Prompt Token 估算**：

| 场景 | System Tokens | User Tokens | Output Tokens | 总计 |
|------|-------------|-------------|---------------|------|
| Simple (safe) | ~1,000 | ~3,500 | ~3,000 | ~7,500 |
| Standard | ~1,200 | ~5,000 | ~6,000 | ~12,200 |
| Complex (showcase) | ~1,200 | ~7,000 | ~10,000 | ~18,200 |
| + QA 修复 ×1 | +3,000 | +5,000 | +5,000 | +13,000 |
| + QA 修复 ×3 | +9,000 | +15,000 | +15,000 | +39,000 |

**关键发现：Prompt 占 Input Token 的 40-60%**，大量是重复的约束性文本。

#### 代码生成 Prompt 内主要变量替换（`prompt.game_design_template`）

**70+ 模板变量**（从 `_build_game_design_prompt_values()` 构建）：
```python
{game_type}              # "casual"
{core_mechanic}          # "Use one readable arcade loop..."
{ui_language}            # "zh-CN"
{theme}                  # "space"
{art_style}              # "pixel"
{visual_pack}            # "neon_glow"
{palette}                # "#0a0a2e, #6366f1, #22c55e, #f43f5e, #ffffff"
{canvas_w} / {canvas_h}  # 390 / 693
{player_speed}           # 6
{obstacle_speed}         # 3
{spawn_interval}         # 1200 (ms)
{speed_formula}          # "base + base * 0.02 * elapsed_s"
{entities_desc}          # "player: circle #6366f1 40x40; obstacle: ..."
{entities_yaml}          # YAML 格式实体列表
{input_map}              # "tap → shoot; swipe_left → move_left; ..."
{state_flow}             # "boot -> ready -> playing -> game_over -> ready"
{win_condition}          # "达到目标分数"
{lives}                  # 3
{special_rules_list}     # "无"
{design_goals}           # "快速反馈, 渐进难度, 清晰目标"
{level_structure_json}   # 紧凑 JSON
{tutorial_beats_json}    # 紧凑 JSON
...
```

**期望输出格式**：完整 HTML5 文档
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0,...">
  <title>游戏标题</title>
  <style>
    /* 内联 CSS */
  </style>
</head>
<body>
  <canvas id="gameCanvas"></canvas>
  <!-- HUD 元素 -->
  <script>
    // 完整游戏逻辑：状态机、输入处理、游戏循环、碰撞检测、计分、游戏结束
  </script>
</body>
</html>
```

### 3.3 QA 修复 LLM 调用详情

**Prompt 构建流程**（`qa_pipeline._fix_with_llm()`）：

```
prompt = prompt_template.format_map({
    "error_list":             # 格式化的错误列表
    "game_type":              # 游戏类型
    "code":                   # 当前代码（完整或分段）
    "runtime_contract_block": # 运行时约束
    "fix_round":              # 当前修复轮次 (1/2/3)
    "max_fix_rounds":         # 最大轮次
    "targeted_instructions":  # 错误类型专属指令
    "repair_family":          # 修复族类
})
```

**修复族类与 Prompt Key 对照**：

| repair_family | Bundle Key | Fallback Key | Timeout |
|---------------|-----------|--------------|---------|
| syntax_structural | bundle.repair.syntax_structural | prompt.qa_fix | 120s |
| forbidden_api | bundle.repair.forbidden_api | prompt.qa_fix_fast | 45s |
| input_contract | bundle.repair.input_contract | prompt.qa_fix_fast | 45s |
| score_feedback | bundle.repair.generic | prompt.qa_fix_fast | 45s |
| terminal_state | bundle.repair.terminal_state | prompt.qa_fix | 60s |
| mobile_layout | bundle.repair.mobile_layout | prompt.qa_fix | 60s |
| runtime_startup | bundle.repair.runtime_startup | prompt.qa_fix | 60s |
| generic | bundle.repair.generic | prompt.qa_fix | 120s |

**Targeted Instructions（按错误类型动态拼接）**：

| 错误类型 | Prompt Key | 说明 |
|---------|-----------|------|
| 无输入处理器 | prompt.qa_instruction_input_handlers | 注入 touch/pointer/keyboard 监听 |
| 无可见反馈 | prompt.qa_instruction_visible_feedback | 确保画面变化 |
| 无终止状态 | prompt.qa_instruction_terminal_state | 确保 gameOver 可达 |
| 存储 API | prompt.qa_instruction_storage | 移除 localStorage 等 |
| 黑屏 | prompt.qa_instruction_blank_screen | 确保 canvas 渲染 |
| JS 运行时错误 | prompt.qa_instruction_runtime_js_error | 修复语法/逻辑错误 |
| 移动端布局 | prompt.qa_instruction_mobile_layout | 修复视口/缩放 |
| 禁止 API | prompt.qa_instruction_forbidden_api | 移除 eval/fetch 等 |
| 通用 | prompt.qa_instruction_generic | 通用修复指导 |

**QA 修复期望输出格式**：

支持两种模式：

**模式 A：Section Patch（优先）**
```json
{
  "patches": [
    {
      "section": "SCRIPT",
      "operation": "replace_section",
      "content": "// 完整的 JavaScript 代码..."
    },
    {
      "section": "STYLE",
      "operation": "replace_section",
      "content": "/* 完整的 CSS */"
    }
  ]
}
```

**模式 B：完整 HTML 文档**
```html
<!DOCTYPE html>
<html>... 完整修复后的游戏 ...</html>
```

### 3.4 Token Budget 配置

```python
# settings.py
LLM_GENERATION_TOKEN_BUDGET_SIMPLE   = 8192    # safe tier
LLM_GENERATION_TOKEN_BUDGET_STANDARD = 12288   # standard tier (默认)
LLM_GENERATION_TOKEN_BUDGET_COMPLEX  = 16384   # showcase tier
LLM_LONG_GENERATION_MAX_TOKENS       = 12288   # 长生成上限
LLM_DESIGN_PASS_MAX_TOKENS           = 4096    # 设计传递
```

### 3.5 解决方案

| 方案 | Token 节省 | 时间节省 | 改动量 |
|------|-----------|---------|--------|
| **Prompt 合并去重** — 12 层有大量重复约束（"single canvas" 出现 3+ 处），合并为 3-4 层 | -30~40% | -20% | 中 |
| **关闭 Runtime QA** — `RUNTIME_QA_REQUIRED=false` | 0 | -8~15s | 改配置 |
| **降低 QA 级别** — safe tier 仅做 L1+L2，跳过 L3-L6 | -20~60% 修复 | -30~60s | 中 |
| **提升 Token Budget** — standard 12K→20K 减少截断重试 | 减少重试 | -15~30s | 改配置 |
| **缓存静态 Prompt** — game_type+tier 相同时复用构建好的 prompt 框架 | -10~15% | -100ms | 低 |
| **选用更快模型** — 代码生成用 DeepSeek，修复用 Haiku | 0 | -40~60% | 低 |

---

## 4. 问题三：流程过重、枷锁过严、游戏太简单

### 4.1 代码层面硬性限制

#### L2 安全检查 — 14 条禁止规则

| 禁止项 | 正则/检测方式 | 创意影响 |
|--------|-------------|---------|
| `eval()` | 关键词匹配 | 低 — 游戏不需要 |
| `Function()` | 关键词匹配 | 低 |
| `import` / `require` | 关键词匹配 | 中 — 无法用模块 |
| **`fetch()`** | 关键词匹配 | **高 — 无联网/排行榜/多人** |
| **`XMLHttpRequest`** | 关键词匹配 | **高** |
| **`WebSocket`** | 关键词匹配 | **高 — 无实时对战** |
| **`localStorage`** | 关键词匹配 | **中 — 无存档/进度保存** |
| **`sessionStorage`** | 关键词匹配 | 中 |
| `document.cookie` | 关键词匹配 | 低 |
| `document.write` | 关键词匹配 | 低 |
| **`<script src="...">`** | HTML 解析 | **高 — 无法用 Phaser/Three.js** |
| **`<img/audio/video src>`** | HTML 解析 | **中 — 素材必须内联** |
| `<link href="...">` | HTML 解析 | 低 |
| `url()` 外部 CSS 资源 | CSS 解析 | 中 — 无 WebFont |

#### L3 启动检查 — 强制 Canvas

| 检查项 | 要求 | 创意影响 |
|--------|------|---------|
| Canvas 元素 | 必须存在 | 中 — 不能做纯 DOM 游戏 |
| getContext() | 必须调用 | 低 |
| Canvas 尺寸 | 必须设置 | 低 |
| 绘图命令 | 必须有 fillRect 等 | 中 — 不能做纯文字游戏 |
| requestAnimationFrame | 必须存在 | 低 |

#### L4 可玩性检查

| 检查项 | 要求 | 创意影响 |
|--------|------|---------|
| **gameOver 状态** | **必须设置 gameOver=true** | **高 — 不能做无尽/沙盒游戏** |
| **输入处理器** | **必须注册 touch/pointer/keyboard** | **中 — 不能做自动运行/观赏类** |
| 重启函数 | 应有（warning 级） | 低 |
| Score 递增 | 必须实际递增 | 中 — 不是所有游戏都需要分数 |

#### L5 性能限制

| 限制 | 值 | 创意影响 |
|------|---|---------|
| 文件大小硬限 | 500 KB | 高 — 限制游戏复杂度上限 |
| 文件大小软限 | 300 KB（微信） | 中 |
| 阻塞循环 | 禁止 while(true) 无 break | 低 |

### 4.2 Prompt 层面软性限制

**核心约束语句**（来自 prompt.code_gen_system）：

| 约束 | 原文 | 影响 |
|------|------|------|
| **最小实现原则** | "Choose the smallest implementation that fully satisfies the request" | **致命 — 默认推向极简** |
| 实体上限 | "1 player entity, at most 2-3 active entity families" | 高 — 游戏内容贫乏 |
| 单循环约束 | "One canvas + one requestAnimationFrame loop" | 中 — 禁止多场景 |
| 压缩多机制 | "Compress multiple mechanics into one shared loop" | 高 — 复杂游戏被强制简化 |
| 禁止高级系统 | "Avoid scene managers, dialogue trees, inventories" | 高 — RPG/冒险类不可做 |
| UI 极简 | "Keep one main HUD, at most one overlay screen" | 中 |
| Canvas 2D only | "No WebGL" | 中 — 无 3D 效果 |
| 教育类关卡限 | "3-5 levels/prompts max" | 中 |

**各 Tier 的创意空间**：

| Tier | 允许子系统 | 允许实体族 | Token Budget | 创意空间 |
|------|-----------|-----------|-------------|---------|
| safe | 0 | 2-3 | 8K | 极低 — "最小可玩原型" |
| standard | 1 | 2-3 | 12K | 低 — 一个辅助系统 |
| showcase | 2-3 | 未放宽 | 16K | 中低 — 仍受实体上限 |

### 4.3 GDD 完全程序化（无创意空间）

`game_designer.py` 的 GDD 生成是 100% 规则引擎：

```python
# 数值完全由公式决定
player_speed = BASE_SPEEDS[game_type] * difficulty_mult
obstacle_speed = player_speed * 0.5
spawn_interval = BASE_SPAWN_INTERVALS[game_type]

# 状态机是固定模板
state_machine = STATE_MACHINES[game_type]
# casual: init → playing → paused → game_over
# puzzle: boot → ready → playing → level_complete

# 输入映射是固定表
input_map = INPUT_MAPS[game_type]

# 教程节拍是模板
tutorial_beats = TUTORIAL_TEMPLATES[game_type]  # 固定 3 节拍
```

**结果**：所有同类型游戏的结构和数值高度雷同。

### 4.4 Prompt 中约束叠加的量化

生成一个游戏时，LLM 收到的约束性文本（非游戏内容描述）占比：

| 内容类型 | 估算 Token | 占比 |
|---------|-----------|------|
| 约束/限制/禁止性指令 | 2,500-4,000 | 35-50% |
| 格式/结构要求 | 800-1,200 | 10-15% |
| 游戏设计参数 | 1,500-3,000 | 25-35% |
| 创意引导/鼓励性文本 | 200-400 | 3-5% |

**约束性文本是创意引导文本的 10-20 倍。**

### 4.5 Implementation Budget Block 详情

**代码位置**：`code_generator.py` `_build_implementation_budget_block()`

```
IMPLEMENTATION BUDGET (NON-NEGOTIABLE):
- All screens share one canvas and one primary requestAnimationFrame loop.
- Whole experience lives in one HTML file and one state model.
- Reuse the same controls and state machine across the whole experience
  instead of creating disconnected subsystems.

[SAFE tier 额外约束]
- Budget: one main HUD plus at most one overlay screen (ready and game-over).
- Avoid: scene managers, dialogue trees, worksheet generators, inventories,
  or parallel mini-games unless absolutely required.
- Prefer the smallest complete mechanic that satisfies the request.

[STANDARD tier 额外约束]
- Allow one supporting subsystem (combos, pickups, rescue goals, etc.).
- Stronger HUD, pacing, and progression are OK.
- Balance stability with delight.

[SHOWCASE tier 额外约束]
- Allow up to 2-3 linked subsystems (all sharing the same loop).
- Favor one signature mechanic plus 1-2 supporting systems.
- Spend budget on juice, pacing, and presentation.
```

### 4.6 解决方案

| 方案 | 收益 | 改动量 | 说明 |
|------|------|--------|------|
| **去掉 "smallest implementation"** | 游戏丰富度大幅提升 | 低 | 改为 "most engaging implementation within the token budget" |
| **提升 Token Budget** | 允许更复杂游戏 | 极低 | standard: 12K→24K, showcase: 16K→32K |
| **放宽实体上限** | 更丰富内容 | 低 | "2-3 families" → "5-8 families" |
| **允许 localStorage** | 支持存档 | 低 | 非安全风险，仅隔离策略；从 L2 禁止列表移除 |
| **L4 改为 Warning** | 支持更多游戏类型 | 中 | gameOver、score 非必须；允许沙盒/无尽模式 |
| **开启 LLM Design Pass** | 设计多样性 | 极低 | `ENABLE_LLM_DESIGN_PASS=true` |
| **减少约束比例** | 释放创意空间 | 中 | 目标：约束占比从 50% 降到 25% |
| **允许 WebGL** | 支持 3D 效果 | 低 | 移除 "Canvas 2D only" |

---

## 5. 问题四：并发能力不足（~20 上限）

### 5.1 瓶颈定位

```
用户请求 → Nginx → game-service → MySQL → AI Engine → LLM Provider
                ↑         ↑         ↑          ↑           ↑
              2r/s      2 conn     ——      无限制      外部限制
            瓶颈 #2    瓶颈 #1              瓶颈 #3     瓶颈 #4
```

### 5.2 瓶颈 #1：MySQL 连接池 = 2（最致命）

**代码位置**：`docker-compose.yml`（4 处）

```yaml
# 4 个服务全部是：
DATABASE_URL: mysql://gamevallies:...@mysql:3306/gamevallies?connection_limit=2
```

**影响计算**：
- 4 个服务 × 2 连接 = **全局仅 8 个 DB 连接**
- 每个请求通常需要 1-2 个连接（查 session + 更新状态）
- 事务中的连接锁定时间 ~10-50ms
- **理论极限：8 ÷ 0.5 = ~16 个并发请求**
- 超过后请求排队 → 超时 → 失败

### 5.3 瓶颈 #2：Nginx AI 限速 = 2r/s

**代码位置**：`nginx/conf.d/default.conf`

```nginx
limit_req_zone $binary_remote_addr zone=ai_limit:10m rate=2r/s;

location /api/v1/ai/ {
    limit_req zone=ai_limit burst=5 nodelay;
    proxy_pass http://ai_engine;
}
```

**影响**：
- 每 IP 每秒仅 2 个请求，burst 允许瞬间突破到 7 个
- 如果所有用户走同一网关/NAT IP → **全局仅 2r/s**
- 生成请求平均 30-120s，这意味着新请求大量被拒

### 5.4 瓶颈 #3：AI Engine 无并发控制

**代码位置**：`packages/ai-engine/src/services/llm_client.py`

```python
# 每次请求新建 httpx.AsyncClient — 无连接池复用
async with httpx.AsyncClient(
    timeout=httpx.Timeout(route.request_timeout_s, connect=route.connect_timeout_s),
) as client:
    response = await client.post(...)
```

**问题**：
- **没有 `asyncio.Semaphore`** — 并发 LLM 调用数无上限
- 每次新建 TCP 连接（无连接池）
- 无请求队列/排队机制
- 20+ 并发 → 同时 40+ 个 LLM API 请求 → Provider 限流 → 级联失败

### 5.5 瓶颈 #4：LLM Provider 外部限制

```python
# settings.py — 使用的模型
LLM_MODEL = "MiniMax-M2.5"
CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_FAST_MODEL = "claude-haiku-4-5-20251001"
```

各 Provider 的 Rate Limit 是外部约束，20+ 并发容易触发。

### 5.6 瓶颈 #5：单进程 Node.js

game-service 是单进程 NestJS，所有请求共享一个事件循环。CPU 密集操作阻塞整个服务。

### 5.7 解决方案

| 方案 | 预期效果 | 改动量 | 说明 |
|------|---------|--------|------|
| **`connection_limit=2` → `20`** | 并发 ×5-10 | 极低 | 改 docker-compose.yml 4 行 |
| **Nginx `ai_limit` 2r/s → 30r/s** | AI 接口解锁 | 极低 | 改 1 行配置 |
| **AI Engine 加 Semaphore** | 稳定并发 | 低 | `asyncio.Semaphore(10)` 限制 LLM 调用 |
| **httpx 全局连接池** | 减少 TCP 开销 | 低 | 单例 AsyncClient |
| **game-service 多实例** | 水平扩展 | 中 | PM2 cluster 或 K8s |
| **任务队列** | 分布式扩展 | 中 | BullMQ 替代内存 AsyncTaskManager |

---

## 6. 附录 A：全链路 LLM 调用清单

### 对话阶段（Stage 01）

| # | 方法 | step_key | System Prompt | User Message | max_tokens | 模型 | 超时 | 输出格式 |
|---|------|----------|---------------|-------------|------------|------|------|---------|
| 1 | `_complete_slot_request()` | `intent_parse` | prompt.intent_parse_system + prompt.slot_output_contract | `"Game title: {title}\nUser request: {desc}"` | 640 | 默认 | 无限制 | JSON: slot schema |
| 2 | `_complete_slot_request()` | `dialogue.slot_extract` | prompt.slot_extraction_system + prompt.slot_output_contract | 对话历史 `[{role, content}, ...]` | 640 | 默认 | 4s request / 5s overall | JSON: slot schema |
| 3 | `_extract_slot_payload_with_repair()` | 同上 | prompt.slot_json_repair_system + prompt.slot_output_contract | `prompt.slot_json_repair_user_template.format(source_text=..., raw_parser_output=...)` | 640 | 默认 | 同上 | JSON: slot schema |
| 4 | `complete_with_truncation_retry()` | `dialogue.reply` | prompt.dialogue_system.format(slot_summary=..., missing_slots=...) | 对话历史 | 2048 | prefer_fast | 含重试 | 纯文本 |

### 设计阶段（Stage 03，可选）

| # | 方法 | step_key | System Prompt | User Message | max_tokens | 模型 | 超时 | 输出格式 |
|---|------|----------|---------------|-------------|------------|------|------|---------|
| 5 | `complete_with_truncation_retry()` | `llm_design.enrich` | _DESIGN_SYSTEM_PROMPT（硬编码） | _DESIGN_USER_TEMPLATE.format(game_type, core_mechanic, theme, ...) | 4096 | prefer_fast | LLM_DESIGN_PASS_TIMEOUT_S (60s) | JSON: EnrichedGDD |

### 代码生成阶段（Stage 05）

| # | 方法 | step_key | System Prompt | User Message | max_tokens | 模型 | 超时 | 输出格式 |
|---|------|----------|---------------|-------------|------------|------|------|---------|
| 6 | `complete_with_truncation_retry()` | `code_generate.full` | locked_contract + product_policy + prompt.code_gen_system | 12 层拼接 prompt（见 §3.2） | 8K-16K | 默认 | LLM_LONG_GENERATION_TIMEOUT_S (300s) | 完整 HTML5 文档 |

### QA 修复阶段（Stage 06）

| # | 方法 | step_key | System Prompt | User Message | max_tokens | 模型 | 超时 | 输出格式 |
|---|------|----------|---------------|-------------|------------|------|------|---------|
| 7-9 | `_complete_repair_prompt_with_retry()` | `qa_fix.{family}` | 无 system（全在 user） | prompt.qa_fix / qa_fix_fast + targeted_instructions + code | 4K-12K | 按 family | 45-120s | HTML 或 Section Patch JSON |

### 代码审查（Stage 06b）

| # | 方法 | step_key | System Prompt | User Message | max_tokens | 模型 | 超时 | 输出格式 |
|---|------|----------|---------------|-------------|------------|------|------|---------|
| 10 | `complete_with_truncation_retry()` | `code_review` | prompt.code_review_system | prompt.code_review_template.format(code_preview=first4K+last4K) | 1024 | prefer_fast | 含重试 | JSON: {is_complete_game, has_real_gameplay, fun_score, issues[]} |

### 迭代阶段（Stage 07）

| # | 方法 | step_key | System Prompt | User Message | max_tokens | 模型 | 超时 | 输出格式 |
|---|------|----------|---------------|-------------|------------|------|------|---------|
| 11 | `complete_with_truncation_retry()` | `iterate.classify` | 无 | prompt.iterate_classify.format(feedback=...) | 512 | prefer_fast | 含重试 | 文本: "param_adjust" / "element_change" / "mechanic_change" |
| 12 | `complete_with_truncation_retry()` | `iterate.apply` | prompt.code_gen_system（变体） | prompt.{类型}.format(feedback, code, history?) | 4K-16K | 默认 | 含重试 | Section Patch JSON 或完整 HTML |

---

## 7. 附录 B：Prompt 模板与输出格式详解

### B.1 Slot 输出契约（JSON Schema）

所有 slot 提取调用共用此输出格式：

```json
{
  "game_type": "casual | puzzle | educational | funny | null",
  "core_mechanic": "string | null",
  "theme": "string | null",
  "input_method": "touch | swipe | drag | tilt | keyboard | null",
  "win_condition": "string | null",
  "difficulty": "easy | medium | hard | progressive | null",
  "visual_style": "string | null",
  "audio_style": "string | null",
  "special_rules": "string | null",
  "reference_game": "string | null"
}
```

### B.2 LLM Design Enrichment 输出格式

```json
{
  "level_design": [
    {
      "level": 1,
      "enemy_count": 3,
      "speed_mult": 0.7,
      "spawn_pattern": "sequential",
      "duration_s": 15
    }
  ],
  "enemy_behaviors": [
    {
      "name": "zigzag",
      "description": "Move in a zigzag pattern across the screen",
      "applies_to": ["obstacle"]
    }
  ],
  "difficulty_curve_params": {
    "ramp_formula": "base + base * 0.03 * elapsed_s",
    "plateau_at_s": 30,
    "spike_at_s": 50,
    "max_speed_mult": 2.5
  },
  "visual_effects": [
    "particle_trail_on_player",
    "screen_shake_on_hit",
    "flash_on_collect"
  ],
  "gameplay_phases": [
    {"name": "warmup", "duration_s": 10, "description": "Slow enemies, wide spacing"},
    {"name": "normal", "duration_s": 25, "description": "Standard difficulty"},
    {"name": "climax", "duration_s": 15, "description": "Fast enemies, tight spacing"},
    {"name": "finale", "duration_s": 10, "description": "Boss or survival challenge"}
  ]
}
```

### B.3 Section Patch 协议（QA 修复 & 迭代共用）

**Patch Protocol 注入内容**：
```
PATCH-FIRST [TASK_LABEL] OUTPUT CONTRACT (NON-NEGOTIABLE):
- Return JSON only.
- Use the shape: {"patches":[{"section":"SCRIPT","operation":"replace_section","content":"..."}]}
- Allowed sections: STYLE, BODY, SCRIPT
- Omit unchanged sections.
- STYLE content: raw CSS (no <style> tags)
- BODY content: raw HTML (no <body> tags)
- SCRIPT content: raw JavaScript (no <script> tags)
- Safe SCRIPT anchors for replace_block: CONFIG, INPUT, GAME_LOOP, LEVEL_DATA
- Safe BODY anchor for replace_block: HUD
- Prefer replace_section when rewriting a whole section
- Use replace_block only when one safe anchor is enough
- Do NOT return a full HTML document unless patching is impossible
```

**Section Context 注入格式**：
```
CURRENT PATCHABLE SECTIONS:
=== SECTION:STYLE START ===
[当前 CSS 代码]
=== SECTION:STYLE END ===
=== SECTION:BODY START ===
[当前 HTML 代码]
=== SECTION:BODY END ===
=== SECTION:SCRIPT START ===
[当前 JavaScript 代码]
=== SECTION:SCRIPT END ===

CURRENT SAFE PATCH ANCHORS:
=== ANCHOR:CONFIG START ===
[配置块]
=== ANCHOR:CONFIG END ===
=== ANCHOR:INPUT START ===
[输入处理块]
=== ANCHOR:INPUT END ===
...
```

### B.4 Code Review 输出格式

```json
{
  "is_complete_game": true,
  "has_real_gameplay": true,
  "difficulty_balanced": true,
  "fun_score": 7.5,
  "issues": [
    "Score display overlaps with game area on small screens",
    "No visual feedback when collecting items"
  ]
}
```

### B.5 Iterate Classify 输出格式

纯文本，必须包含以下之一：
- `param_adjust` — 数值参数微调（速度、大小、颜色等）
- `element_change` — 元素变更（添加/移除/替换 UI 元素）
- `mechanic_change` — 机制变更（改变核心玩法/交互逻辑）

### B.6 数据库存储的 Prompt Key 完整清单

| 类别 | Key | 用途 |
|------|-----|------|
| **对话** | prompt.intent_parse_system | 意图解析系统提示 |
| | prompt.slot_extraction_system | 对话槽位提取系统提示 |
| | prompt.dialogue_system | 对话回复系统提示（含 {slot_summary}, {missing_slots}） |
| | prompt.slot_output_contract | 槽位 JSON 输出格式约束 |
| | prompt.slot_json_repair_system | 槽位 JSON 修复系统提示 |
| | prompt.slot_json_repair_user_template | 修复用户模板（含 {source_text}, {raw_parser_output}） |
| **生成** | prompt.code_gen_system | 代码生成主系统提示 |
| | prompt.code_gen_system_safe | safe tier 覆盖 |
| | prompt.code_gen_system_standard | standard tier 覆盖 |
| | prompt.code_gen_system_showcase | showcase tier 覆盖 |
| | prompt.game_design_template | 游戏设计文档模板（70+ 变量） |
| | prompt.platform_standard | 平台标准（Canvas/输入/网络） |
| | prompt.mobile_layout_guardrails | 移动端布局防护栏 |
| | prompt.runtime_contract_summary | 运行时契约摘要 |
| | prompt.generate_request_context_template | 生成请求上下文 |
| | prompt.generate_alignment_reminder | 生成对齐提醒 |
| **迭代** | prompt.iterate_classify | 迭代分类（三类之一） |
| | prompt.param_adjust | 参数调整 prompt（含 {feedback}, {code}） |
| | prompt.element_change | 元素变更 prompt（含 {feedback}, {code}） |
| | prompt.mechanic_change | 机制变更 prompt（含 {feedback}, {history}, {code}） |
| | prompt.iteration_mobile_layout_guardrails | 迭代专用移动端防护栏 |
| **QA** | prompt.qa_fix | 标准修复模板 |
| | prompt.qa_fix_fast | 快速修复模板 |
| | prompt.qa_instruction_input_handlers | 输入处理器修复指令 |
| | prompt.qa_instruction_visible_feedback | 可见反馈修复指令 |
| | prompt.qa_instruction_terminal_state | 终止状态修复指令 |
| | prompt.qa_instruction_storage | 存储 API 修复指令 |
| | prompt.qa_instruction_blank_screen | 黑屏修复指令 |
| | prompt.qa_instruction_runtime_js_error | 运行时 JS 错误修复指令 |
| | prompt.qa_instruction_mobile_layout | 移动端布局修复指令 |
| | prompt.qa_instruction_forbidden_api | 禁止 API 修复指令 |
| | prompt.qa_instruction_generic | 通用修复指令 |
| **审查** | prompt.code_review_system | 代码审查系统提示 |
| | prompt.code_review_template | 审查模板（含 {code_preview}） |
| **Bundle** | bundle.runtime.locked_contract | 运行时锁定契约 |
| | bundle.product.policy | 产品策略 |
| | bundle.product.logic_generate | 生成逻辑策略 |
| | bundle.product.intent_parse | 意图解析策略 |
| | bundle.repair.syntax_structural | 语法结构修复 |
| | bundle.repair.input_contract | 输入契约修复 |
| | bundle.repair.terminal_state | 终止状态修复 |
| | bundle.repair.mobile_layout | 移动端布局修复 |
| | bundle.repair.forbidden_api | 禁止 API 修复 |
| | bundle.repair.runtime_startup | 运行时启动修复 |
| | bundle.repair.generic | 通用修复 |
| **Profile** | bundle.runtime.profile.casual_arcade | 休闲街机档案 |
| | bundle.runtime.profile.casual_lane | 休闲跑道档案 |
| | bundle.runtime.profile.puzzle_grid | 益智网格档案 |
| | bundle.runtime.profile.casual_action | 休闲动作档案 |
| | bundle.runtime.profile.tap_challenge | 点击挑战档案 |

---

## 8. 附录 C：QA 检查点与修复机制

### C.1 六级检查详情

| Level | 名称 | 严重度 | 检查内容 | 自动修复 |
|-------|------|--------|---------|---------|
| L1 | 语法结构 | ERROR | DOCTYPE/html/head/body 标签 + charset + viewport + 标签平衡 + 冲突标记 + JS 语法 | 确定性注入 + LLM 重写 |
| L2 | 安全 | ERROR | 14 条禁止 API（eval/fetch/WebSocket/localStorage 等） | 确定性移除 + LLM 替换 |
| L3 | 启动渲染 | ERROR | Canvas 存在 + getContext + 尺寸 + 绘图命令 + requestAnimationFrame | LLM 修复 |
| L4 | 可玩性 | ERROR/WARN | gameOver 状态 + 输入处理器 + score 递增 + 重启函数 + 移动端视口 | 确定性注入 Bridge + LLM |
| L5 | 性能 | ERROR | 文件 < 500KB + 无阻塞循环 + 最小 2KB | 无自动修复（硬限制） |
| L6 | 内容安全 | FATAL | 暴力/色情/赌博/政治关键词（中英文） | 无法修复，直接拒绝 |

### C.2 确定性修复（无 LLM 调用）

| 修复 | 注入标记 | 内容 |
|------|---------|------|
| Input Bridge | `__playforgeInputBridgeInstalled` | ~250 行 JS：wrap addEventListener、dispatch 合成事件、paint overlay |
| Score Bridge | `__playforgeScoreBridgeInstalled` | ~100 行 JS：probe score 变量、创建 HUD 元素、持续更新 |
| Mobile Layout Bridge | `__playforgeMobileLayoutBridgeInstalled` | ~40 行 JS：响应式 canvas 缩放 |
| Touch Coordinate Guard | `__playforgeResolveTouchPointInstalled` | 触摸事件坐标解析辅助函数 |
| Terminal State Fallback | `__playforgeTerminalFallback` | 60s 超时强制 gameOver=true |
| Storage API Sanitization | 无 | 正则替换：localStorage.getItem → ""，.setItem → 删除 |
| Forbidden API Strip | 无 | 正则替换：eval() → ()，Function() → ()，new Function → ( |
| HTML 结构注入 | 无 | 补 DOCTYPE、head、body、viewport、charset |

### C.3 修复重试策略

```
初始生成 → QA 检查
  │
  ├── 通过 → 完成
  │
  └── 失败 → 分类修复族 → 确定性修复
        │
        ├── 通过 → 完成
        │
        └── 仍失败 → LLM 修复 #1
              │
              ├── 通过 → 完成
              │
              └── 仍失败 → 熔断检查
                    │
                    ├── 同错误连续 2 次 → 退出
                    ├── 代码 hash 未变 → 退出
                    ├── 结构退化（canvas 丢失/代码缩短 >45%）→ 退出
                    │
                    └── LLM 修复 #2
                          │
                          └── LLM 修复 #3（max_retries=3）
                                │
                                └── 全部失败 → 返回最佳候选或报错
```

---

## 9. 附录 D：关键配置参数速查

### D.1 AI Engine — settings.py

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `QA_MAX_RETRIES` | 3 | QA 修复最大轮次 |
| `PIPELINE_TIMEOUT_S` | 1200 (20min) | 管线整体超时 |
| `LLM_LONG_GENERATION_TIMEOUT_S` | 300 (5min) | 长生成单次超时 |
| `LLM_GENERATION_TOKEN_BUDGET_SIMPLE` | 8192 | safe tier token 上限 |
| `LLM_GENERATION_TOKEN_BUDGET_STANDARD` | 12288 | standard tier token 上限 |
| `LLM_GENERATION_TOKEN_BUDGET_COMPLEX` | 16384 | showcase tier token 上限 |
| `LLM_LONG_GENERATION_MAX_TOKENS` | 12288 | 长生成 token 上限 |
| `ENABLE_LLM_DESIGN_PASS` | false | LLM 设计传递开关 |
| `LLM_DESIGN_PASS_MAX_TOKENS` | 4096 | 设计传递 token 上限 |
| `RUNTIME_QA_REQUIRED` | true | 运行时 QA 是否强制 |
| `RUNTIME_QA_TIMEOUT_S` | 8.0 | Runtime QA 单次超时 |
| `RUNTIME_QA_REMEDIATION_MAX_RETRIES` | 2 | Runtime QA 修复重试 |
| `SLOT_MIN_FILL_PCT` | 0.6 | 槽位填充率阈值 |
| `DIALOGUE_SLOT_REQUEST_TIMEOUT_S` | 4 | 对话 slot 提取超时 |
| `DIALOGUE_SLOT_OVERALL_TIMEOUT_S` | 5 | 对话 slot 整体超时 |
| `LLM_ADAPTIVE_TOKEN_BUDGET_ENABLED` | true | 自适应 token 预算 |
| `QA_REPAIR_TIMEOUT_S` | 180 | QA 修复超时 |
| `QA_FAST_REPAIR_TIMEOUT_S` | 120 | 快速修复超时 |
| `LLM_PROVIDER_FAILOVER_ENABLED` | true | Provider 故障切换 |

### D.2 Docker / Nginx

| 参数 | 位置 | 默认值 | 说明 |
|------|------|--------|------|
| `connection_limit` | docker-compose.yml ×4 | **2** | MySQL 连接池（致命瓶颈） |
| `ai_limit rate` | nginx/conf.d/default.conf | **2r/s** | AI 接口限速（严重瓶颈） |
| `api_limit rate` | nginx/conf.d/default.conf | 30r/s | 通用 API 限速 |
| `auth_limit rate` | nginx/conf.d/default.conf | 5r/s | 认证限速 |
| `worker_connections` | nginx/nginx.conf | 1024 | Nginx 工作连接数 |
| `worker_processes` | nginx/nginx.conf | auto | Nginx 工作进程 |

### D.3 LLM 模型配置

| 参数 | 默认值 | 用途 |
|------|--------|------|
| `LLM_MODEL` | MiniMax-M2.5 | 主要生成模型 |
| `LLM_FAST_MODEL` | MiniMax-M2.5 | 快速任务模型 |
| `CLAUDE_MODEL` | claude-sonnet-4-5 | Claude 主模型 |
| `CLAUDE_FAST_MODEL` | claude-haiku-4-5-20251001 | Claude 快速模型 |
| `LLM_BASE_URL` | https://api.minimaxi.com/v1 | MiniMax API |

---

## 10. 优先级排序与行动建议

### P0 — 立即生效，改配置即可

| # | 改动 | 文件 | 效果 |
|---|------|------|------|
| 1 | `connection_limit=2` → `20` | docker-compose.yml (4 处) | 并发 ×5-10 |
| 2 | `ai_limit rate=2r/s` → `rate=30r/s` | nginx/conf.d/default.conf | AI 接口解锁 |
| 3 | `RUNTIME_QA_REQUIRED=false` | ai-engine .env | 生成快 8-15s |

### P1 — 中等改动，高回报

| # | 改动 | 效果 |
|---|------|------|
| 4 | 首轮对话快速通道（预置欢迎语 + 异步 LLM 补充） | 首轮 < 1s |
| 5 | Prompt 合并去重（12 层 → 3-4 层） | Token -30%，速度 +20% |
| 6 | 去掉 "smallest implementation"，改为 "most engaging" | 游戏质量大幅提升 |
| 7 | Token budget 提升 — standard: 12K→24K，showcase: 16K→32K | 允许更复杂游戏 |
| 8 | 放宽实体上限 — "2-3" → "5-8" | 更丰富游戏内容 |
| 9 | L4 gameOver/score 从 ERROR 改为 WARNING | 允许沙盒/无尽模式 |
| 10 | AI Engine 加 `asyncio.Semaphore(10)` + httpx 连接池 | 稳定并发 |

### P2 — 需要设计评审

| # | 改动 | 效果 |
|---|------|------|
| 11 | `ENABLE_LLM_DESIGN_PASS=true` | 设计多样性（增加 1 次 LLM） |
| 12 | 允许 localStorage（从 L2 禁止列表移除） | 支持存档 |
| 13 | 允许内联 WebGL（移除 "Canvas 2D only"） | 支持 3D |
| 14 | 流式 SSE 返回对话 | 感知延迟 -80% |
| 15 | BullMQ 任务队列替代内存 AsyncTaskManager | 分布式扩展 |
| 16 | game-service 多实例 (PM2 cluster) | 水平扩展 |

---

*文档结束*
