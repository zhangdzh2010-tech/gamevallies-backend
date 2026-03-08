#!/bin/bash
# =============================================================
# 火山引擎函数服务 - NestJS 服务打包脚本
# 用法: ./scripts/build-lambda.sh [service-name|all]
# 示例: ./scripts/build-lambda.sh user-service
#       ./scripts/build-lambda.sh all
# =============================================================

set -e

SERVICES=("user-service" "game-service" "social-service" "feed-service")
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT_DIR/.lambda-dist"

build_service() {
  local SVC=$1
  local SVC_DIR="$ROOT_DIR/packages/$SVC"

  if [ ! -d "$SVC_DIR" ]; then
    echo "❌ 找不到服务目录: $SVC_DIR"
    exit 1
  fi

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔨 构建: $SVC"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  cd "$SVC_DIR"

  # 1. 安装依赖
  echo "📦 安装依赖..."
  npm install --prefer-offline 2>/dev/null || npm install

  # 2. 编译 TypeScript
  echo "🔧 编译 TypeScript..."
  rm -rf dist tsconfig.tsbuildinfo
  npx tsc -p tsconfig.json

  # 3. 生成 Prisma Client（针对 linux-musl 目标）
  if [ -f "$ROOT_DIR/prisma/schema.prisma" ]; then
    echo "🗄️  生成 Prisma Client (linux-musl)..."
    cd "$ROOT_DIR"
    npx prisma generate
    cd "$SVC_DIR"
  fi

  # 4. 准备输出目录
  local OUT_DIR="$DIST_DIR/$SVC"
  rm -rf "$OUT_DIR"
  mkdir -p "$OUT_DIR"

  # 5. 复制编译产物
  echo "📁 复制构建产物..."
  cp -r dist "$OUT_DIR/"

  # 6. 复制 Prisma schema 和 client（函数运行时需要）
  if [ -d "$ROOT_DIR/prisma" ]; then
    mkdir -p "$OUT_DIR/prisma"
    cp "$ROOT_DIR/prisma/schema.prisma" "$OUT_DIR/prisma/"
  fi

  # 7. 安装生产依赖到输出目录
  echo "📦 安装生产依赖..."
  cp package.json "$OUT_DIR/"
  cp package-lock.json "$OUT_DIR/" 2>/dev/null || true
  cd "$OUT_DIR"
  npm install --production --ignore-scripts 2>/dev/null || npm install --omit=dev --ignore-scripts

  # 8. 复制 Prisma 生成的 client 文件（二进制）
  if [ -d "$SVC_DIR/node_modules/.prisma" ]; then
    mkdir -p "$OUT_DIR/node_modules/.prisma"
    cp -r "$SVC_DIR/node_modules/.prisma" "$OUT_DIR/node_modules/"
  fi
  if [ -d "$ROOT_DIR/node_modules/.prisma" ]; then
    cp -r "$ROOT_DIR/node_modules/.prisma" "$OUT_DIR/node_modules/" 2>/dev/null || true
  fi

  # 9. 打 zip 包
  echo "🗜️  打包 zip..."
  local ZIP_FILE="$DIST_DIR/${SVC}.zip"
  rm -f "$ZIP_FILE"
  cd "$OUT_DIR"
  zip -r "$ZIP_FILE" . -x "*.DS_Store" -x "__pycache__/*" > /dev/null
  local SIZE=$(du -sh "$ZIP_FILE" | cut -f1)
  echo "✅ $SVC → $ZIP_FILE ($SIZE)"

  cd "$ROOT_DIR"
}

# 主逻辑
mkdir -p "$DIST_DIR"

TARGET=${1:-"all"}

if [ "$TARGET" = "all" ]; then
  echo "🚀 构建所有 NestJS 服务..."
  for SVC in "${SERVICES[@]}"; do
    build_service "$SVC"
  done
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🎉 全部构建完成！输出目录: $DIST_DIR"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  ls -lh "$DIST_DIR"/*.zip
else
  # 验证服务名称合法
  VALID=false
  for SVC in "${SERVICES[@]}"; do
    if [ "$TARGET" = "$SVC" ]; then
      VALID=true
      break
    fi
  done
  if [ "$VALID" = false ]; then
    echo "❌ 不支持的服务名: $TARGET"
    echo "   可用: ${SERVICES[*]} | all"
    exit 1
  fi
  build_service "$TARGET"
fi
