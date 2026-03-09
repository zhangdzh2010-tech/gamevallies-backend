#!/usr/bin/env bash
# =============================================================
# 本地构建所有服务的 Docker 镜像（linux/amd64）
# 用法: bash scripts/build-docker.sh              # 构建全部
#       bash scripts/build-docker.sh user-service  # 构建单个
# =============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SERVICES=(user-service game-service social-service feed-service)
declare -A PORTS=([user-service]=3001 [game-service]=3002 [social-service]=3003 [feed-service]=3004)

build_image() {
  local SVC=$1
  local PORT=${PORTS[$SVC]}
  local TAG="gv-${SVC}:latest"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔨 构建: ${TAG}  (port ${PORT})"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  docker build \
    --platform linux/amd64 \
    --build-arg SERVICE="${SVC}" \
    --build-arg PORT="${PORT}" \
    -t "${TAG}" \
    "${ROOT_DIR}"

  echo "✅ ${TAG}  $(docker images "${TAG}" --format '{{.Size}}')"
}

TARGET="${1:-all}"

if [[ "${TARGET}" == "all" ]]; then
  for SVC in "${SERVICES[@]}"; do
    build_image "${SVC}"
  done
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🎉 全部构建完成"
else
  build_image "${TARGET}"
fi
