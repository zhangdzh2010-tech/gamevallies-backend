# User Service 运行说明

生产部署采用阿里云函数计算 FC 自定义容器，统一使用[阿里云部署与维护](../../docs/deployment/ALIYUN_DEPLOYMENT.md)。服务依赖 MySQL 和 Redis，并作为统一业务 API 入口。

从 backend 仓库根目录安装依赖并构建：

```bash
npm ci
npx prisma generate
npm run build --workspace=packages/user-service
```

本地环境配置好后，可在 `packages/user-service` 运行 `node dist/main.js`，默认端口 3001。健康入口为 `/api/v1/health`。

容器构建从仓库根目录执行：

```bash
docker build --build-arg SERVICE=user-service -t gamevallies-user-service:local .
```

生产运行参数、数据库初始化边界、FC 域名、发布和回滚以统一部署文档为准。
