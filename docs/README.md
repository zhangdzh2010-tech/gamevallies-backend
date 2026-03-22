# Gamevallies Backend Docs

当前 `docs/` 已按用途收敛为 4 个目录，只保留仍在使用、职责明确的文档。

## 目录结构

### `deployment/`

- [`deployment/DEPLOY_RUNBOOK.md`](./deployment/DEPLOY_RUNBOOK.md)
  当前唯一有效的生产发布说明，包含发布步骤、域名校验和故障排查。
- [`deployment/ENV_SYNC_GUIDE.md`](./deployment/ENV_SYNC_GUIDE.md)
  当前唯一有效的环境变量字段说明与同步规则。

### `integration/`

- [`integration/api-schema.md`](./integration/api-schema.md)
  后端 API、数据模型、WebSocket 事件和兼容路由说明。
- [`integration/FRONTEND_ADAPTATION_P0_P2.md`](./integration/FRONTEND_ADAPTATION_P0_P2.md)
  前端对接质量分、生成进度、创作者声誉等能力的适配说明。
- [`integration/GENERATION_PIPELINE_IMPROVEMENT_PLAN.md`](./integration/GENERATION_PIPELINE_IMPROVEMENT_PLAN.md)
  游戏生成 8 阶段的重试、用户提示、失败日志与落库改造任务清单。
- [`integration/LLM_GATEWAY_ASYNC_TASK_DESIGN.md`](./integration/LLM_GATEWAY_ASYNC_TASK_DESIGN.md)
  LLM 网关、多 Region、最末端错误日志与异步任务管理的详细设计文档。

### `testing/`

- [`testing/TEST_SUITE.md`](./testing/TEST_SUITE.md)
  当前保留的测试说明文档，包含测试范围、运行方式和排查建议。

### `reference/`

- [`reference/config-file.json`](./reference/config-file.json)
  历史配置样例文件，非发布入口。

## 使用约定

- 生产部署只认 `docs/deployment/DEPLOY_RUNBOOK.md` 和 `docs/deployment/ENV_SYNC_GUIDE.md`
- 不再新增“总览”“索引”“副本”“历史修复记录”类重复文档
- 新增文档前，先判断是否能并入现有分类目录
- 发现过期文档时，优先删除或合并，不继续堆叠

## 已清理内容

以下类型文档已经从 `docs/` 中移除：

- 重复的部署总览和 SOP
- 旧的 Kubernetes / 多方案部署说明
- Docker 修复历史和启动修复索引
- 重复的测试索引、测试清单、实现总结
- 前端适配文档副本
