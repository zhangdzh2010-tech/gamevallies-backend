# GameVallies Generation E2E Runbook

## 目的

这份文档用于统一 GameVallies 主生成链路的线上端到端验证方式，覆盖：

- 创作会话生成
- 发布
- 迭代
- 再发布
- 复刻
- 复刻发布
- 分享数据读取

主推荐入口是创作会话脚本：

- [run_live_creation_session_full_flow_e2e.py](/d:/Project/gamevallies/gamevallies-backend/scripts/run_live_creation_session_full_flow_e2e.py)

## 适用场景

适用于以下场景：

- 后端 `game-service` / `ai-engine` 发布前冒烟
- 前端创建、迭代、复刻链路改动后的线上验证
- 生成质量或稳定性回归检查
- 线上失败修复后的真实流量回放

如果只是验证单次生成，不需要覆盖迭代和复刻，可改用：

- [run_live_generation_e2e.py](/d:/Project/gamevallies/gamevallies-backend/scripts/run_live_generation_e2e.py)

如果要验证旧的非创作会话主链，可改用：

- [run_live_full_flow_e2e.py](/d:/Project/gamevallies/gamevallies-backend/scripts/run_live_full_flow_e2e.py)

## 前置条件

在仓库根目录执行：

- `d:\Project\gamevallies\gamevallies-backend`

并确保 [.env.deploy](/d:/Project/gamevallies/gamevallies-backend/.env.deploy) 至少包含：

- `PUBLIC_API_BASE_URL`
- `ADMIN_TOKEN`

## 推荐命令

```powershell
python scripts/run_live_creation_session_full_flow_e2e.py --env-file .env.deploy --timeout-s 1500 --wait-s 1800
```

如果需要显式输出文件名：

```powershell
python scripts/run_live_creation_session_full_flow_e2e.py --env-file .env.deploy --timeout-s 1500 --wait-s 1800 --output tmp_creation_session_e2e_batch_manual.json
```

## 成功判定

一次主流程 E2E 成功，至少要满足：

- `okCount == cases`
- 每个 case 的 `ok = true`
- 每个 case 的 `create.finalStatus = "succeeded"`
- 每个 case 的 `iterate.finalStatus = "succeeded"`
- 每个 case 都产出：
  - 源游戏 `create.gameId`
  - 复刻游戏 `fork.gameId`

结果文件默认落在仓库根目录：

- `tmp_creation_session_e2e_batch_YYYYMMDD_HHMMSS.json`

## 失败时优先查看的字段

不要只看控制台最后一行，要先看结果 JSON。

优先看：

- `create.failedStage`
- `create.failureFamily`
- `create.errorMessage`
- `iterate.failedStage`
- `iterate.failureFamily`
- `iterate.errorMessage`
- `create.diagnostics`
- `iterate.diagnostics`

向团队同步时，至少带上：

- case 名称
- `gameId`
- `taskId`
- `failedStage`
- `failureFamily`
- `errorMessage`

## 本机 Codex Skill

这套主流程已经整理成一个本机 Codex skill：

- `gamevallies-generation-e2e`

本机路径：

- `C:\Users\zhang\.codex\skills\gamevallies-generation-e2e`

适合在 Codex 中直接用自然语言触发，例如：

- `使用 $gamevallies-generation-e2e 跑一次线上 create/iterate/fork 全流程并总结结果`

说明：

- 这个 skill 当前是本机资产，不随仓库自动分发
- 如需给团队共享，可以把 skill 内容再沉淀成仓库内脚本或统一安装说明

## 最近一次实测记录

最近一次完整主流程实测结果：

- 结果文件：[tmp_creation_session_e2e_batch_20260331_141447.json](/d:/Project/gamevallies/gamevallies-backend/tmp_creation_session_e2e_batch_20260331_141447.json)
- 总用例数：`5`
- 通过数：`5`

覆盖样例：

- `office_slacker_cn`
- `circuit_classroom_cn`
- `parkour_delivery_en`
- `fruit_merge_relax_en`
- `history_quiz_show_cn`

## 测试数据清理约定

这套 live E2E 会在生产环境创建：

- 测试用户
- 测试游戏
- 生成任务
- 生成 artifacts
- 创作会话

默认不自动清理，除非操作者显式要求。

完成清理后，建议保留：

- 原始结果文件
- 清理报告文件

最近一次对应清理报告：

- [tmp_test_data_cleanup_generation_e2e_20260331_151848.json](/d:/Project/gamevallies/gamevallies-backend/tmp_test_data_cleanup_generation_e2e_20260331_151848.json)

## 操作建议

- 发布前优先跑创作会话主链
- 如果主链失败，再决定是否补跑 legacy 流程
- 每次线上实测后都保留 JSON 结果文件，便于追查失败任务
- 如果做了多轮回放，建议按批次保留独立结果文件，不要覆盖
