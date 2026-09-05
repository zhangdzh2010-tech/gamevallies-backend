# AI Engine 运行说明

生产部署统一使用[阿里云部署与维护](../../docs/deployment/ALIYUN_DEPLOYMENT.md)。部署工作流从仓库根目录构建 AI 镜像，包含共享 contracts 和 Playwright Chromium。

本地开发：在 `packages/ai-engine` 安装 `requirements.txt`，填写本地环境配置，运行：

```bash
python -m playwright install --with-deps chromium
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

容器构建须从 **backend 仓库根目录**执行：

```bash
docker build -f packages/ai-engine/Dockerfile -t gamevallies-ai-engine:local .
```

HTTP 健康入口为 `/health`。环境变量、内部服务地址、生产发布和回滚以统一部署文档为准。
