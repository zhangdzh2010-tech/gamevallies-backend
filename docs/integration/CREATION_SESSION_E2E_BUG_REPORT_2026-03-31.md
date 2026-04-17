# Creation Session 线上端到端缺陷报告

更新时间：2026-03-31  
测试环境：生产环境 `https://gamevallies.com`  
测试账号：用户 `will zhang`（手机号 `18909246448`，仅用于本次联调）  
测试方式：真实短信登录 + 真实线上 API 调用 + 真实会话创建/追问/生成/放弃

## 一、结论摘要

当前 creation session 主链路不是完全不可用，但存在明显不稳定性，已经足以影响前端多轮对话体验。

本轮真实测试确认：

- `create / iterate / fork` 三类会话都可以成功建立。
- `iterate` 和 `create` 都可以真实触发生成任务。
- 但 `create` 存在偶发性创建失败。
- active session 残留频繁，会影响下一轮创作。
- `status / readyToGenerate / currentQuestion` 的语义存在冲突。
- 相似输入下，会话状态在 `ready` 和 `collecting` 之间不稳定漂移。

## 二、核心问题列表

### 1. `create` 会话创建存在偶发性失败

严重级别：High

现象：

- 同一账号、同一环境、相近时间下，`POST /api/v1/games/creation-sessions` 并不稳定。
- 第一轮 `create` 请求直接返回 `400`，未拿到 `sessionId`。
- 随后重跑相同链路，又成功返回 `201`。

影响：

- 前端首句提交后可能直接失败。
- 因为没有 `sessionId`，后续补问和生成会级联变成 `404`。

本轮记录：

- 首次失败样本：`create-1`
  - `POST /api/v1/games/creation-sessions`
  - 返回：`400`
- 成功重跑样本：
  - `sessionId`: `ef378f5e-e7aa-4fdc-9d17-b606f3525714`
  - 返回：`201`

建议排查：

- 后端创建 session 的校验逻辑是否存在竞态或上下文污染。
- 是否与账号当前 active session、quota 状态或历史生成状态有关。
- 补充稳定的错误码和错误体，避免前端只能看到泛化失败。

### 2. active session 残留严重，影响下一轮创作

严重级别：High

现象：

- 同一账号连续测试时，多次在开始新一轮前查到旧的 active session。
- 必须先手动调用 `POST /creation-sessions/:id/abandon`，才能开始下一轮。

影响：

- 多轮对话容易串。
- 不同入口之间会互相占用 active slot。
- 前端恢复逻辑会被旧会话拖偏。

本轮清理到的残留会话：

- `b53cc33c-7e35-4e01-a931-7d5d393d0931`，`entryMode=iterate`
- `cb6e64be-ab2f-48d7-b11a-42463530f1c0`，`entryMode=create`
- `9557d42d-d25a-4f8b-8d49-1fa6d89b5ff6`，`entryMode=create`
- `eba7176b-b08f-499c-b362-7f2ac21259d3` 在真实生成后仍需要人工清理

建议排查：

- 会话何时自动从 active 转为非 active。
- 生成开始后，旧 session 是否仍应长期占用 active slot。
- abandon、completed、generating、expired 之间的状态收口规则是否一致。

### 3. `status=ready` 与 `currentQuestion` 同时存在，语义冲突

严重级别：High

现象：

- `create` 成功样本中，会话已经：
  - `status=ready`
  - `readyToGenerate=true`
- 但同时又继续返回 `currentQuestion`

影响：

- 前端无法定义唯一主操作。
- 用户会困惑：现在应该继续回答，还是直接生成。
- 页面很难统一交互逻辑。

真实样本：

- `sessionId`: `ef378f5e-e7aa-4fdc-9d17-b606f3525714`
- `revision=2`
- `status=ready`
- `readyToGenerate=true`
- `currentQuestion.slotKey=input_method`

建议排查：

- 明确 `ready` 的定义：
  - 是“可以立即生成，但仍可继续打磨”
  - 还是“信息已经完整，不再继续追问”
- 如果允许两者并存，建议增加更明确的状态字段，而不是只靠 `status + readyToGenerate + currentQuestion` 组合推断。

### 4. 相近输入下，状态在 `ready` 和 `collecting` 之间不稳定漂移

严重级别：Medium

现象：

- 同一账号、同一作品、同一 `entryMode=iterate`，不同优化提示下：
  - 一轮直接 `ready`
  - 下一轮又是 `collecting`

影响：

- 前端体验不稳定。
- 用户会感知为“有时可以直接生成，有时又必须补问”，缺乏一致性。

真实样本：

- 目标作品：`4f0e467a-d8b0-4420-8ac9-33604189017c`
- `iterate-1`
  - `sessionId`: `eba7176b-b08f-499c-b362-7f2ac21259d3`
  - 初始状态：`ready`
- `iterate-2`
  - `sessionId`: `93741e24-29d2-4968-9567-4cc763c8de72`
  - 初始状态：`collecting`

建议排查：

- 后端 question strategy / plan draft / slot fill 判定是否过于敏感。
- 是否存在某些 slot 命中导致会话分支跳变。
- 建议定义更稳定的阈值或明确的状态转换规则。

### 5. 生成任务已启动，但会话仍长期处于 active

严重级别：Medium

现象：

- `iterate` 真实生成后，任务已经建立并进入 `running`。
- 但下一轮开始前，该 session 仍需要人工 `abandon`，否则会占用 active。

影响：

- 用户完成一轮“开始生成”后，可能无法无缝开始下一轮。
- 前端若按 active session 恢复，会被上一轮生成中会话拖住。

真实样本：

- `sessionId`: `eba7176b-b08f-499c-b362-7f2ac21259d3`
- `taskId`: `da489ee8-adf7-4bcd-a2e2-a6633a9ea3a5`
- `gameId`: `08e4078d-57c3-437e-bda7-02ad62ceb674`
- 轮询 3 次后任务仍为 `running`
- 下一轮前仍需 `abandon`

建议排查：

- 生成开始后，session 是否应转为只读且不再占用 active。
- 是否需要把 active session 与 generating task 解耦。

## 三、已跑通的正向样本

说明：以下不是缺陷，而是确认目前链路“并非完全不可用”。

### 1. `create` 成功样本

- `sessionId`: `ef378f5e-e7aa-4fdc-9d17-b606f3525714`
- `revision`: `1 -> 2`
- 真实生成任务：`faf53d53-cda5-4b6d-8706-19f3f2a2b55e`
- 新作品：`4586ef55-998a-44ee-b159-f1b84c1ff048`

### 2. `iterate` 成功样本

- `sessionId`: `eba7176b-b08f-499c-b362-7f2ac21259d3`
- `revision`: `1 -> 2 -> 3`
- 真实生成任务：`da489ee8-adf7-4bcd-a2e2-a6633a9ea3a5`
- 新作品：`08e4078d-57c3-437e-bda7-02ad62ceb674`

### 3. `fork` 成功建立与恢复样本

- `sessionId`: `a5142480-da62-4c0d-9b08-c407a8038559`
- `entryMode`: `fork`
- `sourceGameId`: `37a543b4-ed14-4591-9af3-ff43864486e4`
- active 查询能正确返回同一条 fork 会话

## 四、补充观察

### 1. `create` 生成返回中包含额度相关信息

真实返回中出现：

- `quotaRemaining: 0`
- `requireSubscription: true`

这说明账号额度状态可能也会影响部分链路体验，建议后端确认：

- quota 不足时，是否仍允许创建 session
- quota 不足时，生成前后的错误返回是否稳定
- 是否会影响 `create` 的偶发 `400`

### 2. `gameCount=0` 与 `GET /games/my` 实际作品数量不一致

`GET /api/v1/users/me` 中返回：

- `gameCount: 0`

但 `GET /api/v1/games/my?page=1&limit=20` 实际返回了多条作品。  
这不是 creation session 主问题，但说明账号摘要信息可能不准确。

## 五、建议后端优先处理顺序

建议按下面顺序排查：

1. 修复 `POST /creation-sessions` 的偶发 `400`
2. 明确 active session 生命周期，避免残留
3. 统一 `status / readyToGenerate / currentQuestion` 语义
4. 稳定 `ready` 与 `collecting` 的判断规则
5. 明确生成中 session 是否继续占用 active slot

## 六、前后端联调建议

为减少反复联调，建议后端补充一份更明确的契约说明：

- session 状态机完整定义
- active session 的唯一性与生命周期
- generating 状态下的 session 行为
- `readyToGenerate` 与 `currentQuestion` 的关系
- create / iterate / fork 三种 entryMode 的预期状态流转

