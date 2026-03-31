# 前端动态创作会话适配开发文档

> 版本：v1.0  
> 日期：2026-03-31  
> 状态：待开发  
> 关联文档：
> - [DYNAMIC_CREATION_DIALOGUE_DESIGN.md](/d:/Project/gamevallies/gamevallies-backend/docs/integration/DYNAMIC_CREATION_DIALOGUE_DESIGN.md)
> - [DYNAMIC_CREATION_DIALOGUE_EXECUTION_PLAN_2026-03-30.md](/d:/Project/gamevallies/gamevallies-backend/docs/integration/DYNAMIC_CREATION_DIALOGUE_EXECUTION_PLAN_2026-03-30.md)
> - [DYNAMIC_CREATION_DIALOGUE_RND_SCHEDULE_2026-03-30.md](/d:/Project/gamevallies/gamevallies-backend/docs/integration/DYNAMIC_CREATION_DIALOGUE_RND_SCHEDULE_2026-03-30.md)

---

## 1. 文档目标

这份文档用于指导 `gamevallies-frontend` 对“动态创作会话”方案做完整适配，目标不是只让创建页可用，而是让前端在以下场景都统一切到后端驱动的创作会话模型：

- 新建游戏 `create`
- 复刻后继续创作 `fork`
- 已有作品优化 `iterate`
- 创作中断恢复 `resume`

文档重点说明：

- 当前前端已经适配了什么
- 还缺哪些关键适配
- 页面与组件应该如何改
- 接口契约如何使用
- 分阶段开发任务和验收标准

---

## 2. 当前状态评估

## 2.1 已完成的前端适配

当前前端创建页已经接入了创作会话主链，主要落点如下：

- 创建页：
  - [index.jsx](/d:/Project/gamevallies/gamevallies-frontend/src/pages/create/index.jsx)
- 前端服务层：
  - [game.js](/d:/Project/gamevallies/gamevallies-frontend/src/services/game.js)

目前已经具备的能力：

1. 用户输入首条创意后，前端会调用 `POST /api/v1/games/creation-sessions`
2. 前端可以显示后端返回的：
   - `planDraft`
   - `confidenceSummary`
   - `questionStrategy`
   - `currentQuestion`
3. 前端已经支持：
   - 回答当前问题
   - 跳过当前问题
   - 从会话直接发起生成
   - 恢复 active session
4. 创建页已经不再使用原来那套本地硬编码补问逻辑

简而言之：

- `create` 主入口：已部分接入
- `fork / iterate`：尚未接入
- “高质量交互体验”：只做了基础版

## 2.2 当前不足

虽然创建页已经可用，但距离文档方案还有明显差距。

主要问题如下：

1. `generationTier` 还没有在创建页给用户可见选择
2. `fork` 和 `iterate` 仍然走旧链路，没有进入创作会话
3. 问题呈现仍然是“纯文本问题 + 纯文本回答”，还没有结构化问答 UI
4. 缺少“方案确认 / 理解纠偏”的显式交互
5. 创作会话状态异常处理不完整
6. 还没有形成完整的前端状态机和恢复逻辑
7. 还没有把创作会话的数据延伸到详情页、作者工作台等后续页面

---

## 3. 前端适配目标

前端最终要达到的状态是：

1. 所有“开始创作”的入口，都先进入创作会话，而不是直接生成
2. 用户先看到“系统整理出的方案草案”，再选择继续补充或直接生成
3. 系统每次只追问一个高价值问题
4. 用户可以：
   - 回答
   - 跳过
   - 纠偏
   - 直接生成
5. `fork` 和 `iterate` 进入的是“差异化创作会话”，不是全量重新问一遍
6. 会话可以恢复、放弃、重新开始
7. 前端能够清晰区分：
   - 会话阶段
   - 生成阶段
   - 完成阶段
   - 异常阶段

---

## 4. 当前前端与目标方案的差距

## 4.1 创建页差距

当前创建页在 [index.jsx](/d:/Project/gamevallies/gamevallies-frontend/src/pages/create/index.jsx) 已经具备基本能力，但还缺：

1. `generationTier` 选择器
2. “确认方案”按钮和“修改理解”按钮
3. 更清晰的槽位进度视图
4. 结构化答案控件
5. 更完善的会话恢复与过期提示

## 4.2 复刻页差距

当前复刻页仍是旧流程：

- [fork/index.jsx](/d:/Project/gamevallies/gamevallies-frontend/src/pages/game/fork/index.jsx)

当前逻辑是：

1. 用户进入复刻页
2. 点击复刻
3. 直接 `forkGame()`
4. 跳转到 iterate 页面

这与新方案不一致。  
新方案要求：

1. 先进入一个 `entryMode=fork` 的创作会话
2. 会话根据源游戏和用户目标生成“差异化问题”
3. 用户补充“想改哪里”
4. 再发起生成或 fork 后迭代

## 4.3 迭代页差距

当前迭代页仍是旧流程：

- [iterate/index.jsx](/d:/Project/gamevallies/gamevallies-frontend/src/pages/game/iterate/index.jsx)

当前逻辑是：

1. 用户输入一段 feedback
2. 直接调 `/api/v1/games/:id/iterate`

这与新方案不一致。  
新方案要求：

1. 先进入 `entryMode=iterate` 的创作会话
2. 后端分析“本次想改什么”
3. 只追问最关键的 1 到 2 个问题
4. 再由会话发起迭代生成

---

## 5. 后端接口契约与前端使用方式

## 5.1 已有接口

前端已接入的接口在 [game.js](/d:/Project/gamevallies/gamevallies-frontend/src/services/game.js)：

1. `createCreationSession(prompt, title, options)`
2. `getActiveCreationSession()`
3. `getCreationSession(sessionId)`
4. `appendCreationSessionMessage(sessionId, content, revision)`
5. `skipCreationSessionQuestion(sessionId, revision)`
6. `generateFromCreationSession(sessionId, options)`
7. `abandonCreationSession(sessionId)`

## 5.2 核心对象

前端需要围绕 `CreationSessionSnapshot` 开发，当前定义在后端：

- [creation-session.types.ts](/d:/Project/gamevallies/gamevallies-backend/packages/game-service/src/game/types/creation-session.types.ts)

关键字段含义如下：

- `status`
  - `collecting`
  - `ready`
  - `generating`
  - `completed`
  - `abandoned`
  - `expired`
  - `failed`
- `entryMode`
  - `create`
  - `fork`
  - `iterate`
- `slotState`
  - 当前槽位快照
- `missingRequired`
  - 当前缺失的关键槽位
- `currentQuestion`
  - 当前问题
- `planDraft`
  - 用户可见的方案草案
- `confidenceSummary`
  - 整体理解置信信息
- `questionStrategy`
  - 本轮为什么问这个问题
- `orientation`
  - 横竖屏
- `generationTier`
  - `safe | standard | showcase`

## 5.3 前端调用原则

前端必须遵循：

1. 不再自行判断“缺什么槽位”
2. 不再自行拼接 prompt
3. 只渲染后端返回的：
   - 当前问题
   - 方案草案
   - 置信信息
   - 会话状态

---

## 6. 前端用户交互逻辑

## 6.1 create

用户路径：

1. 进入创建页
2. 输入一句创意
3. 选择方向：
   - `portrait`
   - `landscape`
4. 选择生成档位：
   - `safe`
   - `standard`
   - `showcase`
5. 点击“开始创作会话”
6. 前端请求创建 session
7. 展示后端返回的：
   - 方案草案
   - 当前问题
   - 追问理由
   - 已明确/待确认信息
8. 用户可选：
   - 提交回答
   - 跳过此题
   - 直接生成
   - 重新开始
9. 生成后切换到进度态
10. 完成后切到作品完成态

## 6.2 fork

目标交互：

1. 用户从详情页点击“复刻”
2. 不直接调用 `forkGame()`
3. 先创建 `entryMode=fork` 的创作会话
4. 后端返回：
   - 基于原作品的方案草案
   - 本次最值得追问的问题
5. 用户补充“想改哪里”
6. 生成完成后得到 fork 后的新游戏
7. 再进入后续试玩或继续优化

## 6.3 iterate

目标交互：

1. 用户从详情页或作品页点击“继续优化”
2. 不直接进老式 textarea 提交
3. 先创建 `entryMode=iterate` 的创作会话
4. 用户针对本次改动方向补充说明
5. 系统只追问 1 到 2 题
6. 再发起会话生成

---

## 7. 前端状态机设计

建议前端统一抽象为如下状态：

1. `idle`
   - 尚未创建会话
2. `collecting`
   - 正在追问
3. `ready_to_generate`
   - 信息足够，可以生成
4. `generating`
   - 已发起生成任务
5. `completed`
   - 生成成功
6. `failed`
   - 会话或生成失败
7. `expired`
   - 会话已过期
8. `abandoned`
   - 会话已放弃

页面不要再用大量局部布尔值隐式拼状态，而建议围绕：

- `creationSession?.status`
- `currentTask?.status`
- `currentGame?.status`

形成显式状态切换。

---

## 8. 页面级开发任务

## 8.1 创建页

文件：

- [index.jsx](/d:/Project/gamevallies/gamevallies-frontend/src/pages/create/index.jsx)
- [index.scss](/d:/Project/gamevallies/gamevallies-frontend/src/pages/create/index.scss)

需要新增或调整：

1. `generationTier` 选择器
2. 方案草案区增加两个主操作：
   - `理解正确，继续`
   - `我想改一下理解`
3. 显示更完整的槽位状态：
   - 已明确
   - 待确认
   - 已跳过
4. 问题区域支持多种回答方式
5. 错误态 UI
6. 过期/放弃恢复 UI

## 8.2 复刻页

文件：

- [fork/index.jsx](/d:/Project/gamevallies/gamevallies-frontend/src/pages/game/fork/index.jsx)
- [fork/index.scss](/d:/Project/gamevallies/gamevallies-frontend/src/pages/game/fork/index.scss)

要改成：

1. 进入时创建 `entryMode=fork` 会话
2. 将 `sourceGameId` 透传给后端
3. 渲染与创建页相同的会话区域，但文案更聚焦“想改哪里”
4. 会话完成后调用 `generateFromCreationSession`
5. 生成成功后进入 fork 后作品

## 8.3 迭代页

文件：

- [iterate/index.jsx](/d:/Project/gamevallies/gamevallies-frontend/src/pages/game/iterate/index.jsx)
- [iterate/index.scss](/d:/Project/gamevallies/gamevallies-frontend/src/pages/game/iterate/index.scss)

要改成：

1. 进入时创建 `entryMode=iterate` 会话
2. 将 `sourceGameId=currentGame.id` 透传给后端
3. 用会话替代旧的“纯 feedback 提交”
4. 仅保留旧 textarea 作为兜底，不作为主链

---

## 9. 组件设计建议

建议新增一个通用创作会话组件层，避免 `create/fork/iterate` 各自复制一套 UI。

推荐拆分：

1. `CreationSessionShell`
   - 负责页面布局和状态切换

2. `CreationPlanDraftCard`
   - 展示 `planDraft`

3. `CreationConfidenceCard`
   - 展示 `confidenceSummary`
   - 展示 `questionStrategy`

4. `CreationConversationList`
   - 展示最近对话

5. `CreationQuestionCard`
   - 展示当前问题

6. `CreationAnswerComposer`
   - 回答输入
   - 未来支持结构化选项

7. `CreationSessionActions`
   - 回答
   - 跳过
   - 直接生成
   - 重新开始

建议位置：

- `src/components/creation/`

这样可让：

- 创建页
- 复刻页
- 迭代页

共享大部分 UI 与逻辑。

---

## 10. 交互增强建议

## 10.1 generation tier UI

建议文案：

- `安全生成`
  - 更稳，生成更快，适合快速出稿
- `标准生成`
  - 平衡稳定性和丰富度
- `精品生成`
  - 更有层次和风格，耗时更长

前端提交字段：

- `generationTier: 'safe' | 'standard' | 'showcase'`

## 10.2 方案草案操作

当前只是展示草案，还不够。

建议增加：

1. `理解正确，继续追问`
2. `直接开始创作`
3. `我想调整理解`

“调整理解”的实现第一版可以很简单：

- 聚焦到回答框
- 用 hint 提示用户直接写“你理解偏了，我想要……”

## 10.3 问题输入方式

当前都是 textarea。

后续建议前端支持如下 answer types：

- `text`
- `single_select`
- `multi_select`
- `chips`
- `text_with_suggestions`

当前后端尚未把 `answerType/options` 正式暴露出来，但前端组件要为此留出扩展位。

---

## 11. 数据与状态持久化建议

前端需要保证以下场景可恢复：

1. 用户离开创建页再回来
2. H5 刷新后恢复 active session
3. 会话已进入 `generating`
4. 任务完成后自动跳转完成态

当前创建页已有初步恢复逻辑，但建议进一步统一成：

- 优先恢复 active creation session
- 若 session 已在 `generating`，恢复 generation task
- 若任务已完成，恢复完成态

不要让会话恢复逻辑散落在多个 `useEffect` 中互相打架。

---

## 12. 错误处理要求

前端需要专门处理下列错误类型：

1. `revision conflict`
   - 文案：当前创作已在其他地方更新，请刷新后继续

2. `session expired`
   - 文案：创作会话已过期，请重新开始

3. `session abandoned`
   - 文案：当前创作会话已结束，请重新开启

4. `generate failed`
   - 文案：生成阶段遇到问题，可稍后重试

5. `restore failed`
   - 文案：恢复创作失败，请手动重新开始

不要全部压成一个“创建游戏阶段遇到问题”。

---

## 13. 前端埋点建议

建议至少记录：

1. `creation_session_started`
2. `creation_plan_draft_shown`
3. `creation_question_answered`
4. `creation_question_skipped`
5. `creation_generate_clicked`
6. `creation_generate_direct_clicked`
7. `creation_session_abandoned`
8. `creation_session_resumed`
9. `creation_generate_succeeded`
10. `creation_generate_failed`

额外建议带字段：

- `entryMode`
- `generationTier`
- `orientation`
- `questionCount`
- `skipCount`
- `slotFillPct`

---

## 14. 测试要求

## 14.1 单元测试

重点覆盖：

1. `normalizeCreationSession`
2. `planDraft/confidenceSummary/questionStrategy` 映射
3. `generationTier` 提交
4. `fork/iterate` 创建会话逻辑

## 14.2 页面流程测试

建议新增或扩展：

- [create.journey.test.jsx](/d:/Project/gamevallies/gamevallies-frontend/src/pages/create/__tests__/create.journey.test.jsx)

补充以下场景：

1. 创建会话成功 -> 看到 plan draft
2. 回答问题 -> revision 递增
3. 跳过问题 -> session 更新
4. 直接生成 -> 调用 generateFromCreationSession
5. 生成 tier 选择生效

并新增：

- `fork.creation-session.test.jsx`
- `iterate.creation-session.test.jsx`

## 14.3 真机联调

需要覆盖：

1. H5 创建会话
2. 微信内创建会话
3. 生成中断恢复
4. 作品完成后继续优化
5. 详情页发起复刻 -> 会话 -> 生成

---

## 15. 前端任务拆分

## P0

1. 创建页增加 `generationTier` 选择器
2. 创建页增加方案确认动作
3. 复刻页切换到 creation session
4. 迭代页切换到 creation session
5. 会话异常态和恢复态补齐

## P1

1. 抽通用 `creation session` 组件层
2. 增强槽位状态展示
3. 增加结构化回答扩展位
4. 完善埋点

## P2

1. 详情页/作者页展示方案草案摘要
2. 历史会话列表
3. 会话模板化推荐

---

## 16. 验收标准

前端适配完成后，应满足：

1. `create/fork/iterate` 三条入口都能进入创作会话
2. 用户能看到方案草案，而不是直接进入生成
3. 用户能回答、跳过、直接生成
4. `generationTier` 能提交到后端
5. 会话可恢复
6. 任务完成后能自然流转到作品完成态
7. 无需前端自己判断缺失槽位

---

## 17. 推荐开发顺序

建议顺序：

1. 创建页补全 `generationTier + 方案确认`
2. 抽 `creation session` 共用组件
3. 复刻页接入 session
4. 迭代页接入 session
5. 错误态/恢复态完善
6. 测试补齐

这样可以先保证主链稳定，再逐步扩到 `fork/iterate`。

---

## 18. 一句话结论

当前前端并不是没接动态创作会话，而是**只接了创建页的基础版**。  
下一步前端真正要做的是：把这套会话模型扩展成统一的创作入口层，让 `create / fork / iterate` 都按“方案草案 -> 动态追问 -> 会话生成”的方式工作。
