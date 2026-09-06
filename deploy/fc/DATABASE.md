# FC 生产数据库初始化

部署复用现有 RDS 实例，但 DATABASE_URL 必须指向独立的 `gamevallies` 库，禁止指向 `zltokens`。建议使用已创建的 `gamevallies_app` 账号，仅授予此库的 DDL 和读写权限。

## 发布顺序

1. CI 使用临时 MySQL 8 验证空库、重复迁移、默认数据保留和部分建表保护。
2. 上传 OSS ZIP 后，部署器暂停并排空已有游戏任务。
3. 创建或更新 `<FC_PREFIX>-db-migrate`，复用 game-service ZIP、VPC、交换机与安全组。仅注入数据库连接及运行时必需配置。
4. 通过 RAM 签名的同步 InvokeFunction 调用执行数据库检查、`prisma migrate deploy`、结构差异检查和生产 seed。
5. 成功后才发布业务函数并检查健康状态。失败时停止发布，保留数据库供排查；不会回滚、重置或删除数据库。

迁移函数为 0.5 CPU / 1 GB、并发 1，无预置实例、无 HTTP 触发器、禁止公网出站。调用结束后关闭按需实例调度，下次发布再启用。不新增 ACR、ECS、OSS 包或 Redis。

## 已有配置

复用 GitHub Aliyun 环境的 `DATABASE_URL`、部署 AK/SK、FC_REGION、FC_EXECUTION_ROLE、FC_VPC_ID、FC_VSWITCH_IDS、FC_SECURITY_GROUP_ID 和 OSS 发布配置。无需新增 Secret。部署 RAM 凭据需要现有 FC 管理权限及 `fc:InvokeFunction` 对迁移函数的权限。

`apply=false` 只检查配置和打包，不连接生产数据库。`apply=true` 才迁移和发布。迁移器不会输出连接串、密码或原始 Prisma 日志，只返回安全错误码。

## 初始化内容

初始迁移包含 schema.prisma 的全部 31 张业务表、索引和外键。迁移历史由 Prisma 写入 `_prisma_migrations`。

生产 seed 写入提示词、提示词包、运行时模板目录、LLM 步骤目录、默认套餐及免费额度。只补缺失项，保留后台修改的内容和套餐价格/启停状态。不创建演示用户、作品、大模型凭据或支付凭据。套餐被管理员删除后，只要初始化标记存在，就不重新补建。

FC 业务函数强制设置 `DATABASE_SCHEMA_MANAGED=true`，停用游戏、计费、增长模块的启动 DDL 和旧的启动初始化；后台旧建表接口也拒绝执行。开发环境兼容路径暂时保留。

## 故障处理

- `EXISTING_SCHEMA_REQUIRES_REVIEWED_BASELINE`：已有业务表但无迁移历史。先备份并核对实际结构；由维护者制作兼容迁移或审核基线后再发布。不要对部分表直接标记初始迁移已应用。
- `FAILED_MIGRATION_REQUIRES_REPAIR` / `P3009`：检查失败迁移与已经执行的 DDL，人工修复后按 Prisma 流程 resolve；不要自动重置。MySQL DDL 不能保证整份迁移事务回滚。
- `MIGRATION_HISTORY_MISMATCH`：已应用迁移缺失或内容被修改。恢复原文件，新增后续迁移。
- `SCHEMA_DRIFT_REQUIRES_REVIEW`：实际表结构与仓库不符。检查手动 DDL 或旧版运行时建表，不自动修正生产结构。
- `DATABASE_NAME_MUST_BE_GAMEVALLIES`：修正 DATABASE_URL 的库名，避免误操作共用实例的其他业务库。
- `P1000` / `P1001`：分别检查数据库凭据及 VPC/白名单/连接地址。

后续结构变更提交新的 Prisma 迁移。应用回滚仅回滚函数代码，不逆向执行数据库迁移；采用兼容旧版代码的增量迁移。生产默认数据需经审核更新，原 `prisma/seed.ts` 仅用于开发演示。

参考：[Prisma 生产迁移](https://www.prisma.io/docs/orm/prisma-client/deployment/deploy-database-changes-with-prisma-migrate)、[已有数据库基线](https://www.prisma.io/docs/orm/v7/prisma-migrate/workflows/baselining)。
