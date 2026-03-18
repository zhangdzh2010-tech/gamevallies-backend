# ─────────────────────────────────────────────────────────────────────────────
# Gamevallies — 通用多阶段 Dockerfile（支持所有 4 个服务）
#
# 用法:
#   docker build --build-arg SERVICE=user-service --build-arg PORT=3001 -t gv-user-service .
#   docker build --build-arg SERVICE=game-service  --build-arg PORT=3002 -t gv-game-service .
#   docker build --build-arg SERVICE=social-service --build-arg PORT=3003 -t gv-social-service .
#   docker build --build-arg SERVICE=feed-service  --build-arg PORT=3004 -t gv-feed-service .
# ─────────────────────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1: 编译
# ═══════════════════════════════════════════════════════════════════════════════
FROM node:20-alpine AS builder

ARG SERVICE=user-service
ARG PORT=3001

WORKDIR /app

# 安装所有依赖（含 devDeps，nest build 需要）
COPY package.json package-lock.json* ./
COPY packages/shared ./packages/shared
COPY packages/${SERVICE} ./packages/${SERVICE}
COPY tsconfig.base.json ./
COPY prisma ./prisma

RUN npm install --legacy-peer-deps

# 生成 Prisma Client（linux-musl 目标）
RUN npx prisma generate

# 编译 shared 包（服务依赖它）
WORKDIR /app/packages/shared
RUN npx tsc

# 编译目标服务
WORKDIR /app/packages/${SERVICE}
RUN npx nest build

# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2: 运行时（精简镜像）
# ═══════════════════════════════════════════════════════════════════════════════
FROM node:20-alpine

ARG SERVICE=user-service
ARG PORT=3001

RUN apk add --no-cache openssl

WORKDIR /app

# 安装生产依赖
COPY package.json package-lock.json* ./
COPY packages/shared/package.json  ./packages/shared/
COPY packages/${SERVICE}/package.json ./packages/${SERVICE}/
RUN npm install --omit=dev --legacy-peer-deps

# 复制编译产物
COPY --from=builder /app/packages/${SERVICE}/dist  ./packages/${SERVICE}/dist
COPY --from=builder /app/packages/shared/dist       ./packages/shared/dist

# 复制 Prisma Client（二进制引擎）
COPY --from=builder /app/node_modules/.prisma       ./node_modules/.prisma
COPY prisma ./prisma

WORKDIR /app/packages/${SERVICE}

ENV NODE_ENV=production
ENV PORT=${PORT}

EXPOSE ${PORT}

# VeFaaS (native/v1) 默认执行 /opt/application/run.sh 启动容器
RUN mkdir -p /opt/application && \
    echo '#!/bin/sh' > /opt/application/run.sh && \
    echo "exec /usr/local/bin/node /app/packages/${SERVICE}/dist/main.js" >> /opt/application/run.sh && \
    chmod +x /opt/application/run.sh

CMD ["node", "dist/main.js"]
