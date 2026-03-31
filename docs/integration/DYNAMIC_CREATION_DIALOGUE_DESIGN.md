# 动态创作追问方案设计

> 版本：v1.0  
> 日期：2026-03-23  
> 状态：方案待确认，未实现

---

## 1. 背景

当前“创建游戏”页在前端本地使用固定规则补问：

- 缺少“画面风格”就问风格
- 缺少“难度”就问难度
- 缺少“计分方式”就问计分

这套逻辑存在 4 个问题：

1. 问题和选项写死在前端，不能随模型和产品策略动态调整
2. 前端用正则判断缺什么，和后端真实意图解析结果可能不一致
3. 问答结果只是重新拼回 prompt，不是结构化槽位输入
4. 现有 `ai-engine` 对话能力是进程内内存态，不能直接承接线上业务

目标是改成：

- 由后端根据缺失槽位动态出题
- 前端只渲染后端下发的问题对象
- 问答过程结构化落库
- 最终生成时优先基于结构化槽位 / `GameSpec`

---

## 2. 目标与非目标

### 2.1 目标

1. 创建页补问从“前端硬编码”切换为“后端驱动”
2. 补问依据统一为后端槽位缺失状态
3. 一次只问一个问题，支持跳过
4. 支持会话恢复、并发保护、幂等提交
5. 生成链路可直接消费结构化 `GameSpec`
6. 不影响现有“直接输入 prompt 并生成”的主流程稳定性

### 2.2 非目标

1. 本期不做自由多轮开放式 Agent 对话
2. 本期不做语音输入
3. 本期不做多端实时协作编辑
4. 本期不要求可选槽位全部采齐后才允许生成

---

## 3. 现状

### 3.1 当前前端逻辑

当前前端在本地决定是否进入补问模式，并构造固定问题：

- `generateClarifications(prompt)`：[`../gamevallies-frontend/src/pages/create/index.jsx`](d:/Project/gamevallies/gamevallies-frontend/src/pages/create/index.jsx#L30)
- 固定开场白：[`../gamevallies-frontend/src/pages/create/index.jsx`](d:/Project/gamevallies/gamevallies-frontend/src/pages/create/index.jsx#L377)
- 用户答完后拼接回 prompt：[`../gamevallies-frontend/src/pages/create/index.jsx`](d:/Project/gamevallies/gamevallies-frontend/src/pages/create/index.jsx#L428)

### 3.2 当前后端能力

`ai-engine` 已有对话能力，但不适合直接作为线上会话主源：

- 对话会话是进程内 `_sessions`：[`packages/ai-engine/src/engine/dialogue_engine.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/dialogue_engine.py#L30)
- 对话入口：[`packages/ai-engine/src/api/endpoints/generate.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/endpoints/generate.py#L220)
- 当前响应只返回 `reply/state/slot_fill_pct`，缺少 UI 所需结构化问题对象：[`packages/ai-engine/src/api/models.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py#L62)

### 3.3 当前 Pipeline 入参限制

当前 `pipeline/run` 只接受 `description`，不能直接消费结构化 `GameSpec`：

- `RunPipelineRequest`：[`packages/ai-engine/src/api/models.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py#L303)

---

## 4. 总体设计

### 4.1 设计原则

1. `game-service` 作为对外 API 和会话主数据源
2. `ai-engine` 负责无状态理解和生成，不持久化业务会话
3. 问题是否出现由后端槽位缺失决定
4. 问题文本和选项由后端输出结构化对象
5. 前端不再自己判断缺什么，也不再自己拼 prompt
6. 生成时优先走结构化 `spec`，减少重复意图解析

### 4.2 组件职责

#### 前端

- 发起创作会话
- 展示对话消息流
- 展示后端下发的当前问题
- 提交用户回答 / 跳过
- 发起“开始创作”

#### game-service

- 对外暴露创作会话 API
- 持久化会话状态
- 协调 `ai-engine` 做槽位提取
- 基于缺失槽位生成 `currentQuestion`
- 做并发控制和 revision 校验
- 最终创建 `game`、`generationTask`

#### ai-engine

- 基于消息历史和当前槽位分析本轮输入
- 返回更新后的槽位
- 生成自然语言回复
- 将最终槽位转换为 `GameSpec`

---

## 5. 槽位模型

本期继续复用现有 `SlotState` 语义：

- `game_type`
- `core_mechanic`
- `theme`
- `input_method`
- `win_condition`
- `difficulty`
- `visual_style`
- `audio_style`
- `special_rules`
- `reference_game`

来源：[`packages/ai-engine/src/api/models.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py#L20)

### 5.1 必填槽位

本期仅这 6 个槽位阻塞生成：

- `game_type`
- `core_mechanic`
- `theme`
- `input_method`
- `win_condition`
- `difficulty`

### 5.2 可选槽位

- `visual_style`
- `audio_style`
- `special_rules`
- `reference_game`

策略：

- 可选槽位缺失不阻塞生成
- 可在“继续完善细节”里扩展，不影响一期主链路

### 5.3 问题优先级

默认顺序：

1. `game_type`
2. `core_mechanic`
3. `theme`
4. `input_method`
5. `win_condition`
6. `difficulty`

说明：

- 优先问最影响代码生成结构的槽位
- 一次只问 1 个槽位
- `currentQuestion.slotKey` 永远对应当前真正缺失的必填槽位

---

## 6. 数据模型设计

建议新增表：`game_creation_sessions`

### 6.1 表结构

```prisma
model GameCreationSession {
  id                String   @id @default(uuid()) @db.VarChar(36)
  userId            String   @map("user_id") @db.VarChar(36)
  status            String   @default("collecting") @db.VarChar(32)
  titleDraft        String?  @map("title_draft") @db.VarChar(128)
  initialPrompt     String   @map("initial_prompt") @db.Text
  regionHint        String?  @map("region_hint") @db.VarChar(32)
  locale            String?  @db.VarChar(16)
  revision          Int      @default(1)
  aiState           String   @default("greeting") @map("ai_state") @db.VarChar(32)
  slotState         Json     @map("slot_state")
  missingRequired   Json     @map("missing_required")
  skippedSlots      Json     @map("skipped_slots")
  currentQuestion   Json?    @map("current_question")
  conversation      Json     @default("[]")
  generatedGameId   String?  @map("generated_game_id") @db.VarChar(36)
  generationTaskId  String?  @map("generation_task_id") @db.VarChar(36)
  expiresAt         DateTime? @map("expires_at") @db.DateTime(3)
  createdAt         DateTime @default(now()) @map("created_at") @db.DateTime(3)
  updatedAt         DateTime @updatedAt @map("updated_at") @db.DateTime(3)

  user              User     @relation(fields: [userId], references: [id], onDelete: Cascade)

  @@index([userId, status, updatedAt])
  @@index([generatedGameId])
  @@map("game_creation_sessions")
}
```

### 6.2 字段语义

- `status`
  - `collecting`
  - `ready`
  - `generating`
  - `completed`
  - `abandoned`
  - `expired`
  - `failed`

- `aiState`
  - 兼容现有 `DialogueState`
  - `greeting | describing | clarifying | confirmed`

- `slotState`
  - 当前完整槽位快照

- `missingRequired`
  - 当前缺失必填槽位数组

- `skippedSlots`
  - 用户显式跳过过的槽位数组

- `currentQuestion`
  - 当前等待回答的问题对象

- `conversation`
  - 消息数组，作为后端重建上下文的唯一来源

- `revision`
  - 乐观锁版本号

---

## 7. API 设计

对外 API 全部挂在 `game-service`。

### 7.1 通用响应结构

遵循现有 `ok(...)` 包装风格：

```json
{
  "code": 0,
  "message": "success",
  "data": { ... }
}
```

### 7.2 公共对象定义

#### ConversationMessage

```json
{
  "id": "m_uuid",
  "role": "user | assistant | system",
  "content": "string",
  "createdAt": "2026-03-23T14:00:00.000Z"
}
```

#### SlotState

```json
{
  "game_type": "string|null",
  "core_mechanic": "string|null",
  "theme": "string|null",
  "input_method": "string|null",
  "win_condition": "string|null",
  "difficulty": "string|null",
  "visual_style": "string|null",
  "audio_style": "string|null",
  "special_rules": ["string"] | null,
  "reference_game": "string|null"
}
```

#### QuestionOption

```json
{
  "label": "string",
  "value": "string",
  "description": "string|null"
}
```

#### CurrentQuestion

```json
{
  "questionId": "q_uuid",
  "slotKey": "game_type",
  "text": "你更希望它偏向哪类玩法？",
  "answerMode": "single_choice | single_choice_or_text | free_text",
  "options": [
    { "label": "解谜闯关", "value": "puzzle", "description": null },
    { "label": "物理实验", "value": "sandbox", "description": null }
  ],
  "required": true,
  "allowSkip": true,
  "placeholder": "也可以直接输入你自己的描述"
}
```

#### SessionSnapshot

```json
{
  "session": {
    "id": "uuid",
    "status": "collecting",
    "titleDraft": "光的折射",
    "initialPrompt": "用光的折射原理设计一个小游戏，要具有教育作用",
    "regionHint": "cn_shanghai",
    "locale": "zh-CN",
    "revision": 1,
    "aiState": "clarifying",
    "createdAt": "2026-03-23T14:00:00.000Z",
    "updatedAt": "2026-03-23T14:00:02.000Z",
    "expiresAt": "2026-03-24T14:00:00.000Z",
    "generatedGameId": null,
    "generationTaskId": null
  },
  "conversation": [],
  "slotState": {},
  "missingRequired": [],
  "slotFillPct": 0.0,
  "readyToGenerate": false,
  "currentQuestion": null,
  "generateAction": {
    "allowed": false,
    "forceAllowed": true,
    "reason": "missing_required_slots"
  }
}
```

### 7.3 创建创作会话

`POST /api/v1/games/creation-sessions`

#### 请求

```json
{
  "prompt": "用光的折射原理设计一个小游戏，要具有教育作用",
  "title": "光的折射",
  "regionHint": "cn_shanghai",
  "locale": "zh-CN"
}
```

#### 字段约束

- `prompt`
  - 必填
  - `string`
  - 长度建议 `5..5000`
- `title`
  - 选填
  - `string`
  - 长度建议 `0..64`
- `regionHint`
  - 选填
  - `cn_shanghai | ap_southeast_johor`
- `locale`
  - 选填
  - 如 `zh-CN`

#### 响应

返回 `SessionSnapshot`

#### 行为

1. 创建会话
2. 把首条用户输入写入 `conversation`
3. 调 `ai-engine` 做首轮槽位分析
4. 生成 `currentQuestion`
5. 返回会话快照

### 7.4 提交一条回答

`POST /api/v1/games/creation-sessions/:sessionId/messages`

#### 请求

```json
{
  "content": "解谜闯关",
  "questionId": "q_uuid",
  "expectedRevision": 1,
  "source": "option"
}
```

#### 字段约束

- `content`
  - 必填
  - `string`
  - 长度建议 `1..1000`
- `questionId`
  - 选填但推荐必传
  - 用于校验本次回答是否针对当前问题
- `expectedRevision`
  - 必填
  - `int`
- `source`
  - 选填
  - `option | text`

#### 响应

返回 `SessionSnapshot`

#### 错误

- `404`：session 不存在
- `409`：revision 冲突
- `422`：回答为空 / questionId 不匹配

### 7.5 跳过当前问题

`POST /api/v1/games/creation-sessions/:sessionId/skip`

#### 请求

```json
{
  "questionId": "q_uuid",
  "expectedRevision": 2
}
```

#### 响应

返回 `SessionSnapshot`

#### 行为

1. 把当前槽位加入 `skippedSlots`
2. 生成下一个问题
3. 若没有剩余必填槽位则 `readyToGenerate=true`

### 7.6 获取会话详情

`GET /api/v1/games/creation-sessions/:sessionId`

#### 响应

返回 `SessionSnapshot`

#### 用途

- 页面刷新恢复
- 小程序返回前台恢复
- 多端查看当前状态

### 7.7 发起生成

`POST /api/v1/games/creation-sessions/:sessionId/generate`

#### 请求

```json
{
  "expectedRevision": 4,
  "force": false
}
```

#### 字段约束

- `expectedRevision`
  - 必填
- `force`
  - 选填
  - `true` 表示忽略剩余问题直接生成

#### 成功响应

```json
{
  "gameId": "uuid",
  "status": "generating",
  "generationTask": {
    "taskId": "uuid",
    "taskType": "pipeline_run",
    "status": "queued",
    "timeoutS": 1200,
    "pollUrl": "/api/v1/games/tasks/uuid",
    "eventsUrl": "/api/v1/games/tasks/uuid/events",
    "cancelUrl": "/api/v1/games/tasks/uuid/cancel",
    "gameId": "uuid"
  },
  "session": {
    "id": "uuid",
    "status": "generating",
    "revision": 5,
    "generatedGameId": "uuid",
    "generationTaskId": "uuid"
  }
}
```

#### 冲突响应

```json
{
  "code": 409,
  "message": "missing_required_slots",
  "data": {
    "missingRequired": ["difficulty", "win_condition"],
    "currentQuestion": {
      "questionId": "q_uuid",
      "slotKey": "difficulty",
      "text": "希望整体难度偏简单、中等还是挑战型？",
      "answerMode": "single_choice_or_text",
      "options": [
        { "label": "简单（休闲）", "value": "easy", "description": null },
        { "label": "中等", "value": "medium", "description": null },
        { "label": "困难（挑战）", "value": "hard", "description": null },
        { "label": "渐进提升", "value": "progressive", "description": null }
      ],
      "required": true,
      "allowSkip": true,
      "placeholder": null
    },
    "readyToGenerate": false,
    "forceAllowed": true
  }
}
```

---

## 8. ai-engine 内部 API 设计

这部分不直接暴露给前端，由 `game-service` 调用。

### 8.1 分析一轮输入

`POST /api/v1/ai/dialogue/analyze-turn`

#### 请求

```json
{
  "user_id": "uuid",
  "session_id": "uuid",
  "history": [
    { "role": "user", "content": "用光的折射原理设计一个小游戏，要具有教育作用" },
    { "role": "assistant", "content": "我先帮你补齐几个关键信息。" },
    { "role": "user", "content": "解谜闯关" }
  ],
  "current_slots": {
    "game_type": null,
    "core_mechanic": null,
    "theme": "光的折射",
    "input_method": null,
    "win_condition": null,
    "difficulty": null,
    "visual_style": null,
    "audio_style": null,
    "special_rules": null,
    "reference_game": null
  },
  "locale": "zh-CN"
}
```

#### 响应

```json
{
  "reply": "明白了，我们先按解谜闯关方向来设计。",
  "state": "clarifying",
  "slots": {
    "game_type": "puzzle",
    "core_mechanic": "利用光线折射解谜",
    "theme": "光的折射",
    "input_method": null,
    "win_condition": null,
    "difficulty": null,
    "visual_style": null,
    "audio_style": null,
    "special_rules": null,
    "reference_game": null
  },
  "slotsUpdated": ["game_type", "core_mechanic"],
  "missingRequired": ["input_method", "win_condition", "difficulty"],
  "slotFillPct": 0.5,
  "readyToGenerate": false
}
```

#### 说明

- 无状态
- `history` 由 `game-service` 提供
- `slots` 为合并后的完整结果
- `reply` 只作为对话展示，不参与 UI 题目判断

### 8.2 从槽位生成 GameSpec

`POST /api/v1/ai/dialogue/spec-from-slots`

#### 请求

```json
{
  "sourceDescription": "用光的折射原理设计一个小游戏，要具有教育作用",
  "slots": {
    "game_type": "puzzle",
    "core_mechanic": "利用光线折射解谜",
    "theme": "光的折射",
    "input_method": "tap",
    "win_condition": "让光线命中目标并完成关卡",
    "difficulty": "progressive",
    "visual_style": "geometric",
    "audio_style": null,
    "special_rules": ["每关引入新的折射现象"],
    "reference_game": null
  }
}
```

#### 响应

```json
{
  "spec": {
    "version": "1.0",
    "game_type": "puzzle",
    "source_description": "用光的折射原理设计一个小游戏，要具有教育作用",
    "intent_summary": "利用光线折射解谜",
    "...": "..."
  }
}
```

---

## 9. Pipeline 改造

### 9.1 目标

对话完成后不再必须重新做一次 `intent_parse`。

### 9.2 请求模型扩展

当前 `RunPipelineRequest` 只有 `description`，建议扩展为：

```json
{
  "game_id": "uuid",
  "user_id": "uuid",
  "description": "string|null",
  "spec": { "...GameSpec..." },
  "conversation": [
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ],
  "platform": "wechat_webview",
  "timeout_s": 1200,
  "task_id": "uuid"
}
```

### 9.3 行为规则

1. `description` 与 `spec` 至少传一个
2. 若传入 `spec`
   - 跳过 Stage 02 `intent_parse`
   - 直接进入 Stage 03 `design`
3. 若只传 `description`
   - 保持现有链路不变
4. `conversation` 作为上下文写入 bundle metadata 和任务日志

### 9.4 兼容性

- 旧调用方不受影响
- 新建对话式创作会话可直接走结构化链路

---

## 10. 问题生成策略

### 10.1 不让模型直接决定 UI 题目

推荐由 `game-service` 根据缺失槽位生成 `currentQuestion`，原因：

1. 题目结构稳定
2. 选项可控
3. 多语言可管理
4. 测试容易
5. 不会出现模型乱问与当前缺失槽位不一致

### 10.2 题目模板示例

#### `game_type`

```json
{
  "slotKey": "game_type",
  "text": "你更希望它偏向哪类玩法？",
  "answerMode": "single_choice_or_text",
  "options": [
    { "label": "解谜闯关", "value": "puzzle" },
    { "label": "跑酷躲避", "value": "runner" },
    { "label": "射击挑战", "value": "shooter" },
    { "label": "实验沙盒", "value": "sandbox" }
  ]
}
```

#### `input_method`

```json
{
  "slotKey": "input_method",
  "text": "玩家主要通过什么方式操作？",
  "answerMode": "single_choice_or_text",
  "options": [
    { "label": "点击", "value": "tap" },
    { "label": "滑动", "value": "swipe" },
    { "label": "拖拽", "value": "drag" },
    { "label": "虚拟摇杆", "value": "joystick" }
  ]
}
```

#### `difficulty`

```json
{
  "slotKey": "difficulty",
  "text": "希望整体难度偏简单、中等还是挑战型？",
  "answerMode": "single_choice_or_text",
  "options": [
    { "label": "简单（休闲）", "value": "easy" },
    { "label": "中等", "value": "medium" },
    { "label": "困难（挑战）", "value": "hard" },
    { "label": "渐进提升", "value": "progressive" }
  ]
}
```

### 10.3 题目生成输入

后端根据这些字段生成题目：

- `slotKey`
- `locale`
- `slotState`
- `missingRequired`
- `skippedSlots`
- `initialPrompt`

### 10.4 题目数量

- 一次只下发 1 个 `currentQuestion`
- 前端不再拿到 `pendingQuestions`
- “下一题”由后端每轮重新计算

---

## 11. 前端状态机

### 11.1 页面状态

- `idle`
- `creating_session`
- `collecting`
- `submitting_answer`
- `ready_to_generate`
- `generating`
- `generation_failed`
- `generation_succeeded`

### 11.2 页面行为

#### 进入页面

- 读取是否存在未完成会话
- 若有则恢复 `GET /creation-sessions/:id`

#### 用户输入首个 prompt

- 调 `POST /creation-sessions`
- 服务端返回 `SessionSnapshot`
- 渲染 `conversation + currentQuestion`

#### 用户回答

- 调 `POST /messages`
- 用 `expectedRevision` 做并发保护

#### 用户跳过

- 调 `POST /skip`

#### 用户开始创作

- 调 `POST /generate`
- 若返回 `409 missing_required_slots`
  - 继续展示后端返回的当前题

---

## 12. 兼容迁移策略

建议分两期做。

### 12.1 一期

目标：

- 创作会话先落地
- 前端改成后端驱动补问
- 最终生成仍允许先走兼容方式

范围：

1. 新增 `game_creation_sessions`
2. 新增 `game-service` 创作会话 API
3. 前端创建页接入新 API
4. `ai-engine` 增加无状态 `analyze-turn`
5. 生成时先允许：
   - 若槽位齐全则生成 `spec`
   - 若未齐全但 `force=true`，仍可回退用合成描述生成

### 12.2 二期

目标：

- 完全改成结构化生成

范围：

1. `pipeline/run` 支持 `spec`
2. 创作会话生成只走 `spec`
3. 删除前端旧补问逻辑
4. 下线 `ai-engine` 内存 `_sessions` 线上依赖

---

## 13. 测试方案

### 13.1 后端单元测试

`game-service`

- 创建会话成功
- revision 冲突返回 `409`
- 跳过问题后进入下一题
- 所有必填槽位采齐后 `readyToGenerate=true`
- `force=false` 且缺必填槽位时 `generate` 返回 `409`
- `force=true` 可以生成

`ai-engine`

- `analyze-turn` 能从历史消息更新槽位
- `spec-from-slots` 能输出有效 `GameSpec`
- 模型回复异常时 repair 逻辑仍可恢复 JSON

### 13.2 前端测试

- 创建会话后正确展示 `currentQuestion`
- 选项回答 / 自由输入都能提交
- revision 过期时正确刷新为最新快照
- 跳过后继续显示下一题
- 直接开始创作能正确进入任务进度页

### 13.3 端到端测试

1. 只输入一句简短创意
2. 回答 2 到 3 个问题
3. 成功生成游戏
4. 中途关闭页面后恢复会话
5. `force=true` 跳过剩余题后仍能生成

---

## 14. 风险与控制

### 14.1 风险

1. 会话状态多一层，前后端同步复杂度上升
2. `ai-engine` 和 `game-service` 会出现字段契约不一致风险
3. 旧页面若未完全移除本地补问，可能出现双重提问
4. 结构化 `spec` 与老 `description` 生成效果短期可能不一致

### 14.2 控制

1. 会话 API 全部返回 `SessionSnapshot`，前端不自己推导状态
2. 所有写接口都强制 `expectedRevision`
3. 先保留兼容生成路径
4. 上线前用 feature flag 控制新流程

---

## 15. 推荐实施顺序

1. 新增 `game_creation_sessions` Prisma 模型和迁移
2. 在 `ai-engine` 增加 `analyze-turn` / `spec-from-slots`
3. 在 `game-service` 增加会话 service/controller
4. 前端创建页切到新会话 API
5. `pipeline/run` 扩展支持 `spec`
6. 删除前端本地固定补问逻辑
7. 补齐端到端回归

---

## 16. 本方案结论

本方案的核心决策是：

1. 创作追问改为后端驱动
2. `game-service` 负责持久化会话与问题编排
3. `ai-engine` 只做无状态理解与结构化输出
4. 前端不再硬编码问题和拼 prompt
5. 最终生成逐步过渡到直接消费 `GameSpec`

如果方案确认，下一步实施文档应继续细化为：

- Prisma 迁移草案
- `game-service` 接口定义与 DTO
- `ai-engine` 新模型与接口变更清单
- 前端页面状态迁移图
- feature flag 与灰度上线步骤

---

## 17. 业务旅程补充规则

本节用于补齐从业务旅程视角必须明确但在主方案中尚未展开的规则。

### 17.1 额度与付费规则

#### 规则

1. 无额度用户允许进入创建页
2. 无额度用户允许走完整个补问会话
3. 无额度用户允许发起生成
4. 付费提示不在创建会话阶段拦截
5. 付费提示发生在“生成成功后，用户尝试玩/查看游戏”阶段

#### 与现网行为对齐

对齐当前 `game-service.create()` 语义：

- 有免费额度或订阅额度
  - 生成时直接授予可玩权限
  - 生成成功后可直接试玩
- 无额度
  - 仍允许生成
  - 生成的游戏落库后 `canPlay=false`
  - `requireSubscription=true`
  - 用户在进入试玩/查看游戏时看到付费引导

#### SessionSnapshot 补充字段

建议在所有创作会话响应中增加：

```json
{
  "entitlement": {
    "quotaRemaining": 0,
    "canPlayAfterGenerate": false,
    "requireSubscriptionAfterGenerate": true,
    "accessMode": "locked"
  }
}
```

`accessMode` 枚举建议：

- `free_quota`
- `subscription_quota`
- `locked`

#### generate 响应补充字段

成功响应增加：

```json
{
  "postGenerationAccess": {
    "canPlay": false,
    "requireSubscription": true,
    "paywallOnFirstPlay": true
  }
}
```

#### 失败退款规则

若本次生成实际消耗了免费额度或订阅额度，则仍沿用现有失败退款规则：

- 任务失败：退款
- 任务取消：退款
- 任务超时：退款

创作会话本身不扣额度，额度只在真正发起生成时处理。

### 17.2 活跃会话唯一性规则

#### 规则

同一用户**不允许**同时存在多个 `collecting/ready` 会话。

推荐进一步收紧为：

- 同一用户在任一时刻最多只有一个“未结束创作会话”
- 未结束状态包括：
  - `collecting`
  - `ready`
  - `generating`

#### 服务端处理策略

`POST /api/v1/games/creation-sessions` 需要先查当前用户是否已有活跃会话：

- 若没有活跃会话：创建新会话
- 若有活跃会话且本次请求与该会话同源：
  - 直接返回现有会话
  - `reusedExisting=true`
- 若有活跃会话但本次是另一条新创作意图：
  - 返回 `409 active_session_exists`
  - 同时返回现有会话快照

#### 响应示例

```json
{
  "code": 409,
  "message": "active_session_exists",
  "data": {
    "reusedExisting": false,
    "activeSession": { "...SessionSnapshot..." }
  }
}
```

#### 新增接口

`GET /api/v1/games/creation-sessions/active`

用途：

- 创建页启动时恢复当前未完成会话
- 小程序冷启动恢复
- 多端切换恢复

响应：

```json
{
  "session": { "...SessionSnapshot..." }
}
```

若不存在则：

```json
{
  "session": null
}
```

### 17.3 会话终止与替换

由于不允许多个活跃会话，必须提供显式结束旧会话的能力。

#### 新增接口

`POST /api/v1/games/creation-sessions/:sessionId/abandon`

请求：

```json
{
  "expectedRevision": 7
}
```

行为：

- 将会话标记为 `abandoned`
- 若会话还未发起生成，不影响游戏数据
- 若会话已进入 `generating`，则不允许 abandon，返回 `409`

#### 用途

- 用户明确放弃当前创作
- 用户准备开启另一条全新创作
- 用户从一个 fork 目标切到另一个 fork 目标

### 17.4 Fork 业务旅程

#### 目标

fork 进入创作区时，不是完全绕过 creation session，而是允许先问 1 到 2 个问题。

#### 设计决策

fork 流程新增 `entryMode="fork"`：

```json
{
  "entryMode": "fork",
  "sourceGameId": "uuid"
}
```

#### 行为

1. 后端检查源游戏是否允许 fork
2. 用源游戏最新 bundle/spec/metadata 预填 `slotState`
3. 问题预算最多 `2` 题
4. 这 1 到 2 题以“你想改什么”为主，不要求重新补齐全部必填槽位
5. 用户跳过后可以直接发起 fork 生成

#### 推荐提问优先级

fork 模式下优先问“差异问题”而不是“基础定义问题”：

1. 你最想改的是玩法、主题还是难度？
2. 是否希望换成不同视觉风格？

#### 生成方式

fork 会话最终发起的不是 `pipeline_run`，而是：

- 新建 fork 游戏草稿
- 以源代码为基底启动 `pipeline_iterate`

因此 `generate` 成功响应中的 `generationTask.taskType` 可能为：

- `pipeline_run`
- `pipeline_iterate`

### 17.5 现有创建页入口分流

当前创建页入口不仅有 fresh，还有 `task / resume / fork`。

本方案建议分流如下：

#### `fresh`

- 创建新 `creation session`
- 走完整补问旅程

#### `fork`

- 创建 `entryMode=fork` 的 `creation session`
- 预填源游戏信息
- 最多追问 1 到 2 个差异化问题

#### `task`

- 不创建新 `creation session`
- 直接恢复现有生成任务页

#### `resume`

- 不创建新 `creation session`
- 直接恢复已有游戏草稿/编辑态

说明：

- 只有 `fresh` 和 `fork` 需要进入“补问式创作会话”
- `task` 和 `resume` 继续复用现有恢复逻辑

### 17.6 幂等与并发规则

仅靠 `expectedRevision` 还不够，移动端必须补“客户端请求幂等键”。

#### 创建会话

`POST /api/v1/games/creation-sessions`

新增字段：

```json
{
  "clientRequestId": "uuid"
}
```

规则：

- 同一用户、同一 `clientRequestId`、同一路径的重复请求，返回同一结果

#### 提交消息

`POST /api/v1/games/creation-sessions/:sessionId/messages`

新增字段：

```json
{
  "clientMessageId": "uuid"
}
```

规则：

- 同一 session 下重复的 `clientMessageId` 只落库一次
- 若客户端超时重发，直接返回该次处理后的最新快照

#### 发起生成

`POST /api/v1/games/creation-sessions/:sessionId/generate`

新增字段：

```json
{
  "expectedRevision": 4,
  "force": true,
  "idempotencyKey": "uuid"
}
```

规则：

- 同一 session 下重复的 `idempotencyKey` 只能创建一个游戏 / 一个任务
- 若生成任务已创建，则重复请求直接返回同一 `gameId/taskId`

### 17.7 force 规则

#### 规则

`force=true` 对**所有用户开放**。

#### 语义

- 用户可以无视剩余问题直接开始生成
- 不因为缺少必填槽位而强制拦截
- 后端应在日志和会话快照中记录“这是一次 force 生成”

#### SessionSnapshot 补充

```json
{
  "generateAction": {
    "allowed": true,
    "forceAllowed": true,
    "reason": "missing_required_slots",
    "forceWillBypassQuestions": true
  }
}
```

### 17.8 会话与任务状态同步

当前文档中最大的业务缺口之一，是生成任务结束后谁来更新 session。

#### 规则

`game_creation_sessions.status` 必须和生成任务状态联动：

- 任务创建成功后：`collecting/ready -> generating`
- 任务成功后：`generating -> completed`
- 任务失败后：`generating -> failed`
- 任务取消后：`generating -> abandoned`

#### 同步来源

推荐使用双保险：

1. 主动回写
   - `game-service` 在任务成功/失败/取消的既有收口点中，回写 session
2. 被动 reconcile
   - `GET /creation-sessions/:id`
   - `GET /creation-sessions/active`
   在读路径里顺带 reconcile 一次

#### completed 快照要求

当 session 进入 `completed`，快照中应补充：

```json
{
  "result": {
    "gameId": "uuid",
    "taskId": "uuid",
    "status": "ready",
    "previewUrl": "https://www.gamevallies.com/games/uuid/preview",
    "canPlay": false,
    "requireSubscription": true
  }
}
```

#### failed 快照要求

当 session 进入 `failed`，快照中应补充：

```json
{
  "error": {
    "failedStage": "intent_parsing",
    "message": "Intent parsing failed after 3 attempts ..."
  },
  "retryAction": {
    "canRetry": true,
    "retryMode": "reuse_session"
  }
}
```

### 17.9 重试规则

#### 会话失败后

- 不强制新建会话
- 允许在原 session 上重试生成

#### 规则

`POST /creation-sessions/:sessionId/generate` 在以下状态下允许重试：

- `ready`
- `failed`

不允许在以下状态下重试：

- `collecting` 且 `force=false` 且仍缺题
- `generating`
- `completed`
- `abandoned`
- `expired`

### 17.10 前端必须补的恢复动作

从业务旅程看，前端除 `GET /creation-sessions/:id` 外，必须实现：

1. 创建页进入时先调用 `GET /creation-sessions/active`
2. 若返回 `generating`
   - 直接跳任务/生成进度页
3. 若返回 `collecting/ready/failed`
   - 直接恢复问答会话
4. 若返回 `completed`
   - 可直接展示“去查看作品”

---

## 18. 数据模型增量补充

在主方案的 `GameCreationSession` 基础上，建议再增加以下字段：

```prisma
entryMode          String   @default("fresh") @map("entry_mode") @db.VarChar(32)
sourceGameId       String?  @map("source_game_id") @db.VarChar(36)
sourceBundleVersion Int?    @map("source_bundle_version")
questionBudget     Int      @default(6) @map("question_budget")
questionsAsked     Int      @default(0) @map("questions_asked")
lastClientMessageId String? @map("last_client_message_id") @db.VarChar(64)
lastGenerateKey    String?  @map("last_generate_key") @db.VarChar(64)
```

字段说明：

- `entryMode`
  - `fresh | fork`
- `sourceGameId`
  - fork 来源游戏
- `sourceBundleVersion`
  - fork 时基于哪个 bundle 版本
- `questionBudget`
  - fresh 默认 `6`
  - fork 默认 `2`
- `questionsAsked`
  - 已经问过多少题
- `lastClientMessageId`
  - 最近一次消息幂等键
- `lastGenerateKey`
  - 最近一次 generate 幂等键

---

## 19. API 契约增量补充

### 19.1 创建会话请求补充

```json
{
  "prompt": "string",
  "title": "string|null",
  "regionHint": "cn_shanghai",
  "locale": "zh-CN",
  "entryMode": "fresh | fork",
  "sourceGameId": "uuid|null",
  "clientRequestId": "uuid"
}
```

约束：

- `entryMode=fork` 时 `sourceGameId` 必填
- `entryMode=fresh` 时 `sourceGameId` 必须为空

### 19.2 Message 请求补充

```json
{
  "content": "string",
  "questionId": "q_uuid",
  "expectedRevision": 2,
  "source": "option | text",
  "clientMessageId": "uuid"
}
```

### 19.3 Generate 请求补充

```json
{
  "expectedRevision": 4,
  "force": true,
  "idempotencyKey": "uuid"
}
```

### 19.4 新增 Active Session 接口

`GET /api/v1/games/creation-sessions/active`

响应：

```json
{
  "session": null | { "...SessionSnapshot..." }
}
```

### 19.5 新增 Abandon 接口

`POST /api/v1/games/creation-sessions/:sessionId/abandon`

请求：

```json
{
  "expectedRevision": 5
}
```

响应：

```json
{
  "session": {
    "id": "uuid",
    "status": "abandoned",
    "revision": 6
  }
}
```

---

## 20. 测试用例补充

除主方案中的测试外，再增加以下业务旅程用例：

1. 无额度用户完整回答问题并生成成功，但试玩时被付费引导拦下
2. 无额度用户 `force=true` 直接生成，生成成功但不可玩
3. 用户已有 `collecting` 会话时再次点击“开始创作”，返回 `active_session_exists`
4. 用户放弃旧会话后可以开启新会话
5. fork 模式只问最多 2 个问题
6. 同一 `clientMessageId` 重发不会重复写消息
7. 同一 `idempotencyKey` 重发不会重复建游戏
8. 会话在任务成功后自动变成 `completed`
9. 会话在任务失败后自动变成 `failed`
10. 创建页冷启动时 `GET /creation-sessions/active` 能恢复正确状态

---

## 21. 实施优先级调整

结合业务旅程，建议实际实现顺序调整为：

1. `game_creation_sessions` 数据模型 + 活跃会话唯一性规则
2. `GET /creation-sessions/active` + `abandon`
3. `ai-engine analyze-turn/spec-from-slots`
4. `game-service` 会话创建、回答、跳过、生成
5. 任务完成后 session 状态回写
6. 前端创建页切换到新会话 API
7. fork 模式的 1 到 2 题差异化补问
8. `pipeline/run` 直吃 `spec`

---

## 22. 实施任务拆解总览

本节把方案拆成可以直接进入研发排期的任务包。

### 22.1 任务分层

按依赖关系分为 6 层：

1. 数据与契约层
2. `ai-engine` 无状态对话层
3. `game-service` 创作会话层
4. 前端创建页接入层
5. 生成链路与任务回写层
6. 测试、灰度与上线层

### 22.2 优先级定义

- `P0`
  - 不完成则新方案无法跑通
- `P1`
  - 不完成则核心旅程不完整或风险较大
- `P2`
  - 体验增强或运营配套，能延后

---

## 23. 详细任务清单

### 23.1 数据与契约层

#### T01 `P0`：新增 `GameCreationSession` Prisma 模型

目标：

- 新增 `game_creation_sessions` 表
- 包含主方案与业务旅程补充中的全部核心字段

涉及文件：

- [`schema.prisma`](d:/Project/gamevallies/gamevallies-backend/prisma/schema.prisma)

建议字段范围：

- 基础字段
  - `id`
  - `userId`
  - `status`
  - `titleDraft`
  - `initialPrompt`
  - `regionHint`
  - `locale`
  - `revision`
  - `aiState`
- 会话字段
  - `slotState`
  - `missingRequired`
  - `skippedSlots`
  - `currentQuestion`
  - `conversation`
- 生成结果字段
  - `generatedGameId`
  - `generationTaskId`
  - `expiresAt`
- 业务补充字段
  - `entryMode`
  - `sourceGameId`
  - `sourceBundleVersion`
  - `questionBudget`
  - `questionsAsked`
  - `lastClientMessageId`
  - `lastGenerateKey`

验收标准：

- Prisma schema 可生成 client
- migration 可执行
- 对已有线上数据无 destructive 影响

#### T02 `P0`：定义会话状态与 DTO 契约

目标：

- 在 `game-service` 定义统一的 DTO、返回对象和内部类型
- 避免 controller/service/frontend 各自拼字段

建议新增文件：

- `packages/game-service/src/game/dto/creation-session.dto.ts`
- `packages/game-service/src/game/types/creation-session.types.ts`

核心类型：

- `CreateCreationSessionDto`
- `CreateCreationSessionMessageDto`
- `SkipCreationSessionQuestionDto`
- `GenerateFromCreationSessionDto`
- `AbandonCreationSessionDto`
- `CreationSessionSnapshot`
- `CreationQuestion`
- `CreationEntitlement`

验收标准：

- 所有创作会话接口复用同一套类型
- TS 编译通过

#### T03 `P0`：定义共享枚举与常量

目标：

- 集中管理状态、入口模式、问题预算、幂等错误码

建议新增文件：

- `packages/game-service/src/game/creation-session.constants.ts`

内容建议：

- `CreationSessionStatus`
- `CreationEntryMode`
- `CreationQuestionBudget`
- `CreationApiErrorCode`

验收标准：

- controller/service 不再散落写 magic string

### 23.2 ai-engine 无状态对话层

#### T04 `P0`：新增 `analyze-turn` 请求/响应模型

目标：

- 在 `ai-engine` 新增无状态对话分析模型

涉及文件：

- [`packages/ai-engine/src/api/models.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py)

建议新增模型：

- `AnalyzeDialogueTurnRequest`
- `AnalyzeDialogueTurnResponse`
- `SpecFromSlotsRequest`
- `SpecFromSlotsResponse`

验收标准：

- Pydantic 模型覆盖必需字段
- 支持 `history/current_slots/locale`

#### T05 `P0`：将 `DialogueEngine` 拆成无状态分析方法

目标：

- 复用现有槽位提取和 repair 能力
- 去掉对 `_sessions` 的依赖

涉及文件：

- [`dialogue_engine.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/dialogue_engine.py)

建议新增方法：

- `analyze_turn(history, current_slots, locale)`
- `build_spec_from_slots(source_description, slots)`

说明：

- 现有 `process_message()` 保留兼容
- 新接口不再调用 `get_or_create_session()`

验收标准：

- 相同输入下，多次调用结果一致
- 不依赖内存 session 即可完成槽位更新

#### T06 `P0`：新增内部 API `analyze-turn`

目标：

- 提供 `game-service` 可调用的无状态接口

涉及文件：

- [`generate.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/endpoints/generate.py)

新增接口：

- `POST /api/v1/ai/dialogue/analyze-turn`
- `POST /api/v1/ai/dialogue/spec-from-slots`

验收标准：

- admin token 不要求
- 普通服务间调用可用
- 结构符合文档定义

#### T07 `P1`：对 fork 场景增强槽位分析提示词

目标：

- 支持 `entryMode=fork` 下“差异化补问”

实现建议：

- 给 `analyze-turn` 增加可选上下文字段
  - `entry_mode`
  - `source_game_summary`
- 对 fork 模式优先抽取“差异意图”

验收标准：

- fork 模式下不会退回去追问一整套基础槽位

### 23.3 game-service 创作会话层

#### T08 `P0`：新增 `CreationSessionService`

目标：

- 把会话逻辑独立成 service

建议新增文件：

- `packages/game-service/src/game/creation-session.service.ts`

主要职责：

- active session 检查
- session create/load/update
- revision 校验
- 幂等键处理
- question 生成
- task 状态回写

验收标准：

- controller 只做参数转发
- 主要业务逻辑集中在 service

#### T09 `P0`：实现 `POST /creation-sessions`

目标：

- 创建新会话
- 或返回当前活跃会话冲突

涉及文件：

- `packages/game-service/src/game/game.controller.ts`
- `packages/game-service/src/game/creation-session.service.ts`

核心逻辑：

1. 校验 `entryMode`
2. 查活跃会话
3. fresh/fork 分支处理
4. 写入首条用户消息
5. 调 `ai-engine analyze-turn`
6. 生成 `currentQuestion`
7. 返回快照

验收标准：

- 单用户多次点击不会创建多个活跃会话
- fork 模式能正确校验 `sourceGameId`

#### T10 `P0`：实现 `GET /creation-sessions/active`

目标：

- 支持页面冷启动恢复当前会话

涉及文件：

- `packages/game-service/src/game/game.controller.ts`
- `packages/game-service/src/game/creation-session.service.ts`

验收标准：

- 用户无活跃会话时返回 `session: null`
- 有活跃会话时返回完整快照

#### T11 `P0`：实现 `GET /creation-sessions/:id`

目标：

- 加载指定会话
- 顺带 reconcile 任务状态

验收标准：

- 仅会话 owner 可访问
- 若会话为 `generating`，读取时会校正 session/task 状态

#### T12 `P0`：实现 `POST /messages`

目标：

- 提交一轮用户回答

核心逻辑：

1. 校验 owner
2. 校验 `expectedRevision`
3. 校验 `questionId`
4. 校验 `clientMessageId` 幂等
5. 追加 user message
6. 调 `ai-engine analyze-turn`
7. 更新 `slotState/missingRequired/currentQuestion/revision`

验收标准：

- 重复 `clientMessageId` 不会重复写消息
- revision 冲突返回最新快照

#### T13 `P0`：实现 `POST /skip`

目标：

- 跳过当前问题并进入下一题

核心逻辑：

- 当前 `slotKey` 写入 `skippedSlots`
- 递增 `questionsAsked`
- 若 budget 未满则继续下一题
- 若已满足生成条件则置 `ready`

验收标准：

- 跳过后不会重复问同一题

#### T14 `P0`：实现 `POST /abandon`

目标：

- 用户显式结束当前创作会话

核心逻辑：

- 仅 `collecting/ready/failed` 可 abandon
- `generating` 状态禁止 abandon

验收标准：

- abandon 后 active session 查询为空
- 可以重新新建会话

#### T15 `P0`：实现 `POST /generate`

目标：

- 从创作会话发起真正生成

核心逻辑：

1. 校验 owner
2. 校验 `expectedRevision`
3. 校验 `idempotencyKey`
4. 校验会话状态是否允许生成
5. 处理 `force`
6. 生成 `spec` 或兼容 `description`
7. 调现有创建/生成流程
8. 更新 session 为 `generating`

验收标准：

- 同一 `idempotencyKey` 不会重复建游戏
- `force=true` 可对所有用户开放
- 无额度用户也能走通生成

#### T16 `P1`：实现 question builder

目标：

- 统一生成 `currentQuestion`

建议新增文件：

- `packages/game-service/src/game/creation-question-builder.ts`

能力：

- fresh 问题模板
- fork 问题模板
- 单题预算控制
- locale 占位

验收标准：

- 题目完全由后端生成
- 前端不再保留本地固定问题逻辑

#### T17 `P1`：实现 entitlement snapshot 组装

目标：

- 在会话阶段就告知“生成后是否能直接玩”

涉及文件：

- `packages/game-service/src/game/creation-session.service.ts`
- 可复用 `GameService` 中现有 quota/subscription 读取逻辑

验收标准：

- snapshot 中始终带 `entitlement`
- 与实际生成后的 `canPlay/requireSubscription` 语义一致

### 23.4 任务与状态回写层

#### T18 `P1`：任务完成时回写 session 状态

目标：

- session 和 generation task 生命周期一致

涉及文件：

- `packages/game-service/src/game/game.service.ts`
- `packages/game-service/src/game/creation-session.service.ts`

需要接入的既有收口点：

- pipeline succeed
- pipeline fail
- pipeline cancel
- pipeline timeout
- iterate succeed/fail（fork 场景）

验收标准：

- session 不会长期停在 `generating`
- completed/failed 快照带结果或错误摘要

#### T19 `P1`：读路径 reconcile

目标：

- 即使主动回写漏了，读接口也能自愈

接入点：

- `GET /creation-sessions/:id`
- `GET /creation-sessions/active`

验收标准：

- 老任务、跨进程、异常中断后仍可恢复正确状态

#### T20 `P1`：fork 模式转到 iterate 任务

目标：

- fork 会话最终不是全新 run，而是基于源游戏执行 iterate

实现建议：

- `entryMode=fork` 时
  - 先调用现有 fork 草稿创建逻辑
  - 再调用 iterate 任务

验收标准：

- fork 会话生成出的任务类型为 `pipeline_iterate`
- 最多只问 1 到 2 个问题

### 23.5 Pipeline 与生成链路层

#### T21 `P1`：扩展 `RunPipelineRequest` 支持 `spec`

目标：

- 让会话完成后可直接跳过重复 intent parse

涉及文件：

- [`packages/ai-engine/src/api/models.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/models.py)
- [`packages/ai-engine/src/api/endpoints/generate.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/api/endpoints/generate.py)
- [`packages/ai-engine/src/engine/pipeline_orchestrator.py`](d:/Project/gamevallies/gamevallies-backend/packages/ai-engine/src/engine/pipeline_orchestrator.py)

验收标准：

- 传 `spec` 时不再跑 Stage 02
- 老的 `description` 调用保持不变

#### T22 `P1`：生成时落 conversation/spec 元数据

目标：

- 让后续迭代和后台排障能看到创作会话上下文

落库位置：

- `GameBundle.spec`
- `GameBundle.aiConversation`
- `GenerationTask.metadata/resultSummary`

验收标准：

- 生成后的 bundle 可追溯到创作会话

### 23.6 前端接入层

#### T23 `P0`：删除前端本地补问逻辑

目标：

- 去掉 `generateClarifications()` 和本地增强 prompt 拼接

涉及文件：

- [`../gamevallies-frontend/src/pages/create/index.jsx`](d:/Project/gamevallies/gamevallies-frontend/src/pages/create/index.jsx)

删除/替换内容：

- `generateClarifications`
- 固定开场白触发逻辑
- `buildEnhancedPrompt`
- 本地 `pendingQuestions`

验收标准：

- 创建页的问答 UI 完全由后端快照驱动

#### T24 `P0`：创建页接入会话 API

目标：

- 创建页按后端快照渲染

前端状态：

- `idle`
- `creating_session`
- `collecting`
- `submitting_answer`
- `ready_to_generate`
- `generating`
- `failed`
- `completed`

验收标准：

- 页面刷新后可恢复 active session
- 多次点击不会重复提交

#### T25 `P1`：fork 入口接入 creation session

目标：

- `fork` 按新会话模式进入创建页

实现建议：

- 现有 `mode=fork` 不再直接 fork 完跳编辑页
- 改为带 `entryMode=fork/sourceGameId` 启动会话

验收标准：

- fork 进入后最多看到 1 到 2 个问题

#### T26 `P1`：完成页与失败页收口

目标：

- 会话完成后给用户明确下一步

完成态：

- 去查看作品
- 去试玩
- 去发布 / 继续编辑

失败态：

- 重试生成
- 返回继续完善问题

验收标准：

- 用户不会卡在“完成了但不知道去哪”

### 23.7 测试与上线层

#### T27 `P0`：后端单元测试

覆盖：

- active session 唯一性
- 幂等键
- revision 冲突
- skip/force/fork
- session 状态回写

建议文件：

- `packages/game-service/test/creation-session.service.spec.ts`
- `packages/ai-engine/tests/test_dialogue_analyze_turn.py`

#### T28 `P1`：端到端回归

覆盖旅程：

1. fresh + 正常回答后生成
2. fresh + force 生成
3. 无额度用户生成成功但试玩需付费
4. fork + 1 到 2 题后生成
5. active session 恢复
6. abandon 后重新新建

#### T29 `P1`：灰度与 feature flag

目标：

- 避免直接替换旧创建页逻辑

建议：

- 后端开关：`CREATION_SESSION_ENABLED`
- 前端开关：远程配置或构建变量

验收标准：

- 新旧流程可切换
- 可定向灰度用户

---

## 24. 任务依赖关系

### 24.1 必须先完成

先后顺序建议：

1. `T01 -> T02 -> T03`
2. `T04 -> T05 -> T06`
3. `T08 -> T09 -> T10 -> T11 -> T12 -> T13 -> T14 -> T15`
4. `T18 -> T19`
5. `T23 -> T24`
6. `T27 -> T28 -> T29`

### 24.2 可并行

这些任务可并行推进：

- `T16` 与 `T17`
- `T18` 与 `T21`
- `T22` 与 `T26`
- `T25` 可在主 fresh 链路稳定后单独推进

### 24.3 推荐拆批次

#### 批次 A：打通 fresh 最短链路

- `T01`
- `T02`
- `T04`
- `T05`
- `T06`
- `T08`
- `T09`
- `T10`
- `T11`
- `T12`
- `T13`
- `T15`

#### 批次 B：补齐恢复、幂等、状态回写

- `T14`
- `T17`
- `T18`
- `T19`
- `T27`

#### 批次 C：前端切换与灰度

- `T23`
- `T24`
- `T26`
- `T28`
- `T29`

#### 批次 D：fork 与 spec 直通优化

- `T07`
- `T20`
- `T21`
- `T22`
- `T25`

---

## 25. 各角色交付物

### 25.1 后端 `game-service`

最终需要交付：

- Prisma migration
- DTO / types / constants
- `CreationSessionService`
- 会话相关 controller API
- session/task 状态回写
- 单元测试

### 25.2 后端 `ai-engine`

最终需要交付：

- 无状态 analyze-turn 模型
- `spec-from-slots` 模型
- 新接口
- 对应测试
- 与旧对话接口兼容

### 25.3 前端

最终需要交付：

- 创建页新状态机
- 会话恢复
- 问答 UI 改为后端驱动
- 完成/失败收口页
- fork 模式接入

### 25.4 测试与运维

最终需要交付：

- fresh / force / no-quota / fork / restore E2E 用例
- feature flag 灰度方案
- 监控与日志排查脚本

---

## 26. 建议的第一批实现范围

如果要先做一版最小可用实现，推荐只做：

- `T01`
- `T02`
- `T04`
- `T05`
- `T06`
- `T08`
- `T09`
- `T10`
- `T11`
- `T12`
- `T13`
- `T15`
- `T17`
- `T18`
- `T23`
- `T24`
- `T27`

这批完成后，可以先上线一个能力：

- fresh 创作由后端动态提问
- 单用户单活跃会话
- 支持恢复
- 支持 force
- 无额度用户也能生成
- 生成完成后会话能自动收口

而以下内容可以放到第二批：

- `spec` 直通 Pipeline
- fork 1 到 2 题差异化会话
- abandon / 更完整完成页
- 更精细的灰度与监控增强

---

## 27. 质量导向 V2 优化补充

配套执行文档：

- [DYNAMIC_CREATION_DIALOGUE_EXECUTION_PLAN_2026-03-30.md](/d:/Project/gamevallies/gamevallies-backend/docs/integration/DYNAMIC_CREATION_DIALOGUE_EXECUTION_PLAN_2026-03-30.md)

本节是在原方案基础上的质量升级版补充，目标不是推翻一期的“后端驱动补问”，而是把创作会话从“收集能生成的信息”升级成“收集能生成高质量游戏的信息”。

### 27.1 为什么要做 V2

当前生成质量不稳定，根因不只在代码生成阶段，而在于：

1. 用户输入在进入 pipeline 前被过早压缩成较薄的意图
2. 补问只解决“缺什么”，没有解决“理解是否足够准”
3. `GameSpec` 对“精品感”相关信息表达能力不足
4. `showcase` 目前主要还是 prompt 加强版，而不是独立的高质量链路
5. QA 仍然更偏向“修到能跑”，容易把复杂结果修回保守版本

### 27.2 V2 总目标

V2 目标是把链路升级成：

`用户创意输入 -> 方案摘要草案 -> 高影响低置信追问 -> richer spec -> 设计程序 -> 分层生成 -> 候选选优 -> tier-aware QA`

其中最关键的变化是：

1. 系统先给出“文字方案草案”，而不是直接开生成
2. 追问从“缺失槽位驱动”升级成“高影响、低置信驱动”
3. `showcase` 档不再只是更强 prompt，而是拥有更完整 spec 和候选选优能力

### 27.3 用户交互升级

#### 27.3.1 新的用户交互流程

建议把创建流程升级为：

1. 用户输入一句创意
2. 后端创建创作会话
3. 后端先返回一版“游戏方案草案”
4. 用户可直接确认，或继续回答当前关键问题
5. 系统每轮只追问 1 个高价值问题
6. 信息足够后进入生成
7. 生成完成后进入试玩 / 继续优化 / 发布

#### 27.3.2 方案草案的作用

方案草案不是 prompt 拼接结果，而是面向用户的自然语言摘要。其目的：

1. 让用户先确认 AI 是否理解正确
2. 在生成前发现“方向理解偏了”的问题
3. 提前暴露玩法、节奏、视觉、亮点是否足够清晰
4. 为后续追问建立上下文

建议草案至少包含：

- 游戏定位
- 核心玩法循环
- 操作方式
- 胜负条件
- 节奏 / 关卡结构
- 视觉方向
- 一个记忆点 / 卖点

### 27.4 槽位模型升级

原方案中的 6 个必填槽位足以支持“能生成”，但不足以支持“生成得好”。建议在一期槽位之上增加质量相关槽位。

#### 27.4.1 保留的一期核心槽位

- `game_type`
- `core_mechanic`
- `theme`
- `input_method`
- `win_condition`
- `difficulty`

#### 27.4.2 建议新增的质量槽位

- `session_length`
  - 单局时长，短局 / 中局 / 多阶段
- `progression_shape`
  - 递进方式，线性 / 波次 / 关卡 / 组合成长
- `reward_loop`
  - 玩家持续玩的奖励反馈来源
- `signature_moment`
  - 最希望被记住的一个高光时刻
- `target_audience`
  - 面向谁，儿童 / 泛休闲 / 职场梗 / 课堂
- `tone`
  - 情绪和表达风格，轻松 / 紧张 / 荒诞 / 温和
- `reference_style`
  - 参考的视觉气质，不一定是具体游戏
- `complexity_budget`
  - safe / standard / showcase 对应的复杂度期望
- `teaching_mode`
  - 教育类专用，题答型 / 操作实验型 / 引导探索型
- `comedy_device`
  - 搞笑类专用，反转 / 误会 / 夸张 / 节奏梗

#### 27.4.3 槽位分类建议

建议将槽位拆为 3 层：

1. 结构骨架层
   - 直接决定生成结构
   - 如 `game_type/core_mechanic/input_method/win_condition`
2. 品质增强层
   - 决定节奏、奖励、体验层次
   - 如 `progression_shape/reward_loop/session_length/signature_moment`
3. 风格表达层
   - 决定视觉、情绪、主题表达
   - 如 `theme/tone/reference_style/visual_style/comedy_device`

### 27.5 从“缺失槽位”升级为“低置信高影响槽位”

原方案中 `currentQuestion` 的出现逻辑主要基于“缺失必填槽位”。V2 建议改为：

`question_priority = slot_impact * (1 - confidence) * ambiguity_weight`

也就是说，后端不只关心“缺不缺”，还要关心：

1. 这个槽位是否影响生成结构
2. 当前理解是否低置信
3. 当前用户输入是否存在明显歧义

#### 27.5.1 `ai-engine analyze-turn` 建议新增返回

在现有 `slots/missingRequired/slotFillPct` 基础上增加：

```json
{
  "confidenceBySlot": {
    "game_type": 0.92,
    "core_mechanic": 0.44,
    "theme": 0.88,
    "input_method": 0.31
  },
  "evidenceBySlot": {
    "game_type": ["用户提到“益智”", "提到“解谜”"],
    "core_mechanic": ["仅提到“做一个关于光的游戏”，未明确互动方式"]
  },
  "ambiguityFlags": [
    {
      "slotKey": "core_mechanic",
      "reason": "主题明确，但互动方式不明确"
    }
  ],
  "nextBestQuestionReason": "core_mechanic has high impact and low confidence"
}
```

#### 27.5.2 问题选择规则建议

优先选择满足以下条件的槽位：

1. 结构影响高
2. 置信度低
3. 已经有足够上下文，用户能回答
4. 不与上一题高度重复

### 27.6 新增“方案草案”对象

建议在 `SessionSnapshot` 中新增：

```json
{
  "planDraft": {
    "title": "光的折射",
    "summary": "这是一个用光线反射与折射来完成目标路径的教育解谜游戏。",
    "concept": "玩家通过调整镜面与介质，让光线穿过关卡中的关键节点。",
    "interaction": "主要通过点击选择和拖拽摆放来操作。",
    "objective": "引导光线命中目标并完成多关递进挑战。",
    "pacing": "每关引入一个新的光学概念，关卡时长短，反馈明确。",
    "visualDirection": "几何科教风，带轻微未来感。",
    "signatureMoment": "当多次折射后的光束成功贯穿全部目标时，出现强反馈演出。"
  }
}
```

#### 27.6.1 方案草案的来源

建议由 `ai-engine` 提供一个新的无状态能力：

- `POST /api/v1/ai/dialogue/draft-plan-from-input`

输入：

- `initial_prompt`
- `current_slots`
- `locale`
- `entry_mode`

输出：

- `planDraft`
- `draftConfidence`
- `recommendedQuestions`

### 27.7 `GameSpec` 升级方向

当前 `GameSpec` 更像“足够生成的技术合同”，V2 建议把它升级成“可表达精品体验的创作合同”。

建议新增字段：

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
  - 用于承载“希望玩家感受到什么”

### 27.8 Designer 层升级

当前 `GameDesigner` 偏向：

- 数值
- HUD 基本布局
- 输入映射

V2 建议让 Designer 额外输出以下结构：

- `level_structure`
- `phase_plan`
- `reward_plan`
- `tutorial_beats`
- `signature_interactions`
- `feedback_moments`
- `failure_recovery_plan`

这样 `CodeGenerator` 就不再需要独自凭 prompt“脑补”完整体验结构。

### 27.9 `showcase` 升级为独立精品链路

#### 27.9.1 当前问题

当前 `showcase` 已经有：

- 更高 token budget
- 更强 prompt
- visual pack direction

但本质仍然是单候选单次生成。

#### 27.9.2 建议改法

对 `showcase` 档采用：

1. richer spec
2. richer designer output
3. 2 到 3 个候选方案生成
4. 借助 QA + 结构评分 + 视觉评分做选优

建议评分来源包括：

- 运行时 QA 通过率
- 玩法可见性
- 反馈强度
- 视觉层次
- 与方案草案一致性

### 27.10 QA 分层

当前 QA 更偏“修到能跑”。V2 需要按 tier 分层：

#### `safe`

- 保持当前保守策略
- 优先稳定性和通过率

#### `standard`

- 允许适中复杂度
- repair 时尽量保留 HUD / feedback / phase

#### `showcase`

- repair 不应轻易删子系统
- 优先局部修 bug，而不是把复杂结构整体修平
- 增加“保持高光时刻和 signature interaction”的提示

### 27.11 V2 API 建议

在原有 API 之外建议新增：

1. `POST /api/v1/ai/dialogue/draft-plan-from-input`
   - 生成文字方案草案
2. `POST /api/v1/ai/dialogue/analyze-turn`
   - 增加 `confidenceBySlot/evidenceBySlot/ambiguityFlags`
3. `POST /api/v1/ai/dialogue/spec-from-slots`
   - 支持 richer spec 字段

在 `game-service` 的 `SessionSnapshot` 中增加：

- `planDraft`
- `confidenceSummary`
- `questionStrategy`

### 27.12 前端体验建议

创建页状态机建议升级为：

- `idle`
- `creating_session`
- `drafting_plan`
- `collecting`
- `ready_to_generate`
- `generating`
- `completed`
- `failed`

新增 UI 区块：

1. 方案草案卡片
   - 支持“确认方向”
   - 支持“我想改一下”
2. 当前关键问题卡片
3. 生成准备度条
4. `showcase` 提示
   - 告知会更慢，但更精致

### 27.13 实施优先级建议

#### V2-A：最小质量升级

1. `analyze-turn` 增加置信度输出
2. 增加 `planDraft`
3. 创建页先展示“方案草案 + 当前问题”
4. `spec` 直通 pipeline

这是最值得优先做的一批，因为它直接改善输入质量。

#### V2-B：精品 spec 升级

1. 扩 `GameSpec`
2. 扩 Designer 输出
3. 新增 `quality slots`

#### V2-C：showcase 精品链路

1. 多候选生成
2. 候选选优
3. QA 分层

### 27.14 推荐结论

推荐将本方案正式升级为：

**“后端驱动的质量导向创作会话方案”**

它不再只是解决“前端补问逻辑不统一”，而是承担 3 个更高价值目标：

1. 提升用户创意被正确理解的概率
2. 提升进入 pipeline 的 `spec` 质量
3. 为 `showcase` 档建立真正可扩展的精品生成前置链路

一句话总结：

> 一期解决“后端驱动补问”，V2 解决“高质量创意理解与精品生成输入”。
