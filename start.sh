#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(dirname "$SCRIPT_DIR")/playforge-miniprogram"
MODE=${1:-start}

case $MODE in
    stop)
        echo -e "${YELLOW}[STOP] 停止所有服务...${NC}"
        docker compose down 2>/dev/null || true
        pkill -f "nest start --watch" 2>/dev/null || true
        pkill -f "uvicorn src.main:app" 2>/dev/null || true
        pkill -f "taro build.*watch" 2>/dev/null || true
        [ -f "$SCRIPT_DIR/.pids" ] && kill $(cat "$SCRIPT_DIR/.pids") 2>/dev/null || true
        rm -f "$SCRIPT_DIR/.pids"
        echo -e "${GREEN}[OK] 全部停止${NC}"
        exit 0
        ;;
    clean)
        echo -e "${YELLOW}[CLEAN] 停止并清除所有数据...${NC}"
        docker compose down -v
        echo -e "${GREEN}[OK] 全部清除${NC}"
        exit 0
        ;;
    logs)
        docker compose logs -f
        exit 0
        ;;
    status)
        echo -e "${CYAN}=== PlayForge 服务状态 ===${NC}"
        echo ""
        echo -e "${BLUE}Docker 容器:${NC}"
        docker compose ps 2>/dev/null || echo "  (未运行)"
        echo ""
        echo -e "${BLUE}端口检测:${NC}"
        for port in 3001 3002 3003 3004 5433 6380 27017 80 8000 8001 10086; do
            if curl -s -o /dev/null -w "" --connect-timeout 1 http://localhost:$port &>/dev/null; then
                echo -e "  ${GREEN}●${NC} Port $port - 活跃"
            else
                echo -e "  ${RED}○${NC} Port $port - 未使用"
            fi
        done
        exit 0
        ;;
    dev)
        # ========== 本地开发模式 (Docker 基础设施 + 本地 Node 服务) ==========
        echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
        echo -e "${CYAN}║    🎮 PlayForge 本地开发模式启动          ║${NC}"
        echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"

        # Step 1: 检查环境
        echo -e "\n${YELLOW}[1/5] 检查环境...${NC}"
        command -v node &>/dev/null || { echo -e "${RED}Node.js 未安装${NC}"; exit 1; }
        command -v docker &>/dev/null || { echo -e "${RED}Docker 未安装${NC}"; exit 1; }
        docker info &>/dev/null || { echo -e "${RED}Docker 引擎未运行${NC}"; exit 1; }
        echo -e "${GREEN}  [OK] Node $(node -v), Docker $(docker --version | grep -oP '\d+\.\d+\.\d+')${NC}"

        # Step 2: 安装依赖
        echo -e "\n${YELLOW}[2/5] 安装依赖...${NC}"
        cd "$SCRIPT_DIR"
        if [ ! -d "node_modules" ]; then
            npm install
            echo -e "${GREEN}  [OK] 后端依赖已安装${NC}"
        else
            echo -e "${GREEN}  [OK] 后端依赖已存在${NC}"
        fi

        if [ -d "$FRONTEND_DIR" ] && [ ! -d "$FRONTEND_DIR/node_modules" ]; then
            cd "$FRONTEND_DIR" && npm install
            echo -e "${GREEN}  [OK] 前端依赖已安装${NC}"
        else
            echo -e "${GREEN}  [OK] 前端依赖已存在${NC}"
        fi

        # Step 3: 启动基础设施
        echo -e "\n${YELLOW}[3/5] 启动基础设施 (PostgreSQL, MongoDB, Redis)...${NC}"
        cd "$SCRIPT_DIR"
        docker compose up -d postgres mongo redis

        RETRIES=30
        while [ $RETRIES -gt 0 ]; do
            PG=$(docker inspect --format='{{.State.Health.Status}}' playforge-postgres 2>/dev/null || echo "starting")
            MG=$(docker inspect --format='{{.State.Health.Status}}' playforge-mongo 2>/dev/null || echo "starting")
            RD=$(docker inspect --format='{{.State.Health.Status}}' playforge-redis 2>/dev/null || echo "starting")
            if [ "$PG" = "healthy" ] && [ "$MG" = "healthy" ] && [ "$RD" = "healthy" ]; then
                echo -e "${GREEN}  [OK] PostgreSQL (5433), MongoDB (27017), Redis (6380) 就绪${NC}"
                break
            fi
            sleep 2
            RETRIES=$((RETRIES - 1))
        done
        [ $RETRIES -eq 0 ] && { echo -e "${RED}数据库启动超时${NC}"; exit 1; }

        # Step 4: 同步数据库
        echo -e "\n${YELLOW}[4/5] 同步数据库 Schema...${NC}"
        cd "$SCRIPT_DIR"
        npx prisma generate 2>/dev/null || true
        npx prisma db push --accept-data-loss 2>/dev/null && echo -e "${GREEN}  [OK] Schema 已同步${NC}" || {
            echo -e "${YELLOW}  [WARN] db push 失败，尝试 migrate...${NC}"
            npx prisma migrate dev --name init 2>/dev/null || true
        }

        # Step 5: 启动服务
        echo -e "\n${YELLOW}[5/5] 启动后端服务 + 前端...${NC}"
        cd "$SCRIPT_DIR"
        npm run dev:all &
        BACKEND_PID=$!
        echo "$BACKEND_PID" > "$SCRIPT_DIR/.pids"

        if [ -d "$FRONTEND_DIR" ]; then
            cd "$FRONTEND_DIR"
            npm run dev:h5 &
            FRONTEND_PID=$!
            echo "$FRONTEND_PID" >> "$SCRIPT_DIR/.pids"
        fi

        sleep 3

        echo ""
        echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
        echo -e "${CYAN}║       🎉 PlayForge 开发环境就绪！         ║${NC}"
        echo -e "${CYAN}╠══════════════════════════════════════════╣${NC}"
        echo -e "${CYAN}║  前端 (H5):    http://localhost:10086     ║${NC}"
        echo -e "${CYAN}║  User Service: http://localhost:3001      ║${NC}"
        echo -e "${CYAN}║  Game Service: http://localhost:3002      ║${NC}"
        echo -e "${CYAN}║  Social:       http://localhost:3003      ║${NC}"
        echo -e "${CYAN}║  Feed:         http://localhost:3004      ║${NC}"
        echo -e "${CYAN}║  AI Engine:    http://localhost:8000      ║${NC}"
        echo -e "${CYAN}╠══════════════════════════════════════════╣${NC}"
        echo -e "${CYAN}║  PostgreSQL:   localhost:5433             ║${NC}"
        echo -e "${CYAN}║  MongoDB:      localhost:27017            ║${NC}"
        echo -e "${CYAN}║  Redis:        localhost:6380             ║${NC}"
        echo -e "${CYAN}╠══════════════════════════════════════════╣${NC}"
        echo -e "${CYAN}║  停止: ./start.sh stop                    ║${NC}"
        echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"

        wait
        exit 0
        ;;
    test)
        bash "$(dirname "$0")/test-all.sh"
        exit 0
        ;;
    start|docker)
        ;;
    *)
        echo -e "${CYAN}PlayForge 启动脚本${NC}"
        echo ""
        echo "用法: ./start.sh [命令]"
        echo ""
        echo "  ${GREEN}dev${NC}     - 本地开发模式 (推荐！Docker基础设施 + 本地Node + 前端H5)"
        echo "  ${GREEN}start${NC}   - Docker全量部署 (所有服务容器化)"
        echo "  ${GREEN}stop${NC}    - 停止所有服务"
        echo "  ${GREEN}status${NC}  - 查看服务状态"
        echo "  ${GREEN}logs${NC}    - 查看实时日志"
        echo "  ${GREEN}clean${NC}   - 停止并删除所有数据卷"
        echo "  ${GREEN}test${NC}    - 运行API测试"
        exit 0
        ;;
esac

# ========== Docker 全量部署模式 ==========
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     🎮 PlayForge Docker 全量部署         ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"

# Step 1: 检查环境
echo -e "\n${YELLOW}[1/5] 检查环境依赖...${NC}"

FAIL=0
if ! command -v docker &> /dev/null; then
    echo -e "${RED}  ✗ Docker 未安装${NC}"
    FAIL=1
else
    DOCKER_VER=$(docker --version | sed 's/[^0-9.]//g' | cut -d. -f1-3)
    echo -e "${GREEN}  ✓ Docker ${DOCKER_VER}${NC}"
fi

if ! docker info &> /dev/null; then
    echo -e "${RED}  ✗ Docker 引擎未运行，请启动 Docker Desktop${NC}"
    FAIL=1
else
    echo -e "${GREEN}  ✓ Docker 引擎运行中${NC}"
fi

if [ $FAIL -eq 1 ]; then
    echo -e "\n${RED}请先安装/启动缺失的依赖${NC}"
    exit 1
fi

# Step 2: 构建服务
echo -e "\n${YELLOW}[2/5] 构建并启动所有容器...${NC}"
echo -e "  首次构建约需 3-5 分钟，请耐心等待..."

docker compose up -d --build 2>&1 | tail -5

echo -e "${GREEN}  ✓ 容器已启动${NC}"

# Step 3: 等待数据库就绪
echo -e "\n${YELLOW}[3/5] 等待数据库就绪...${NC}"

RETRIES=30
while [ $RETRIES -gt 0 ]; do
    PG=$(docker inspect --format='{{.State.Health.Status}}' playforge-postgres 2>/dev/null || echo "starting")
    MG=$(docker inspect --format='{{.State.Health.Status}}' playforge-mongo 2>/dev/null || echo "starting")
    RD=$(docker inspect --format='{{.State.Health.Status}}' playforge-redis 2>/dev/null || echo "starting")

    if [ "$PG" = "healthy" ] && [ "$MG" = "healthy" ] && [ "$RD" = "healthy" ]; then
        echo -e "${GREEN}  ✓ PostgreSQL  - healthy${NC}"
        echo -e "${GREEN}  ✓ MongoDB     - healthy${NC}"
        echo -e "${GREEN}  ✓ Redis       - healthy${NC}"
        break
    fi

    sleep 2
    RETRIES=$((RETRIES - 1))
done

if [ $RETRIES -eq 0 ]; then
    echo -e "${RED}  ✗ 数据库启动超时${NC}"
    exit 1
fi

# Step 4: 同步数据库 Schema
echo -e "\n${YELLOW}[4/5] 同步数据库 Schema...${NC}"

sleep 5
docker compose exec -T user-service npx prisma db push --schema=/app/prisma/schema.prisma --accept-data-loss 2>&1 | grep -E "sync|Done|error" || true
echo -e "${GREEN}  ✓ 数据库 Schema 已同步${NC}"

# Step 5: 验证服务健康
echo -e "\n${YELLOW}[5/5] 验证服务状态...${NC}"

sleep 8

ALL_OK=1

USR=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3001/api/v1/health 2>/dev/null || echo "000")
if [ "$USR" = "200" ]; then
    echo -e "${GREEN}  ✓ User Service    (port 3001)${NC}"
else
    echo -e "${YELLOW}  ⚠ User Service    (port 3001) - HTTP $USR${NC}"
    ALL_OK=0
fi

GAME=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3002/api/v1/games/explore/published 2>/dev/null || echo "000")
if [ "$GAME" = "200" ]; then
    echo -e "${GREEN}  ✓ Game Service    (port 3002)${NC}"
else
    echo -e "${YELLOW}  ⚠ Game Service    (port 3002) - HTTP $GAME${NC}"
    ALL_OK=0
fi

SOCIAL=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3003/api/v1/notifications 2>/dev/null || echo "000")
if [ "$SOCIAL" != "000" ]; then
    echo -e "${GREEN}  ✓ Social Service  (port 3003)${NC}"
else
    echo -e "${YELLOW}  ⚠ Social Service  (port 3003) - 无法连接${NC}"
    ALL_OK=0
fi

FEED=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3004/api/v1/feed/trending 2>/dev/null || echo "000")
if [ "$FEED" = "200" ]; then
    echo -e "${GREEN}  ✓ Feed Service    (port 3004)${NC}"
else
    echo -e "${YELLOW}  ⚠ Feed Service    (port 3004) - HTTP $FEED${NC}"
    ALL_OK=0
fi

AI=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/health 2>/dev/null || echo "000")
if [ "$AI" = "200" ]; then
    echo -e "${GREEN}  ✓ AI Engine       (port 8001)${NC}"
else
    echo -e "${YELLOW}  ⚠ AI Engine       (port 8001) - HTTP $AI${NC}"
    ALL_OK=0
fi

GW=$(curl -s -o /dev/null -w "%{http_code}" http://localhost/health 2>/dev/null || echo "000")
if [ "$GW" = "200" ]; then
    echo -e "${GREEN}  ✓ API Gateway     (port 80)${NC}"
else
    echo -e "${YELLOW}  ⚠ API Gateway     (port 80) - HTTP $GW${NC}"
    ALL_OK=0
fi

# ========== 完成 ==========
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
if [ $ALL_OK -eq 1 ]; then
echo -e "${CYAN}║       🎉 PlayForge 启动成功！            ║${NC}"
else
echo -e "${CYAN}║       ⚠  PlayForge 部分启动              ║${NC}"
fi
echo -e "${CYAN}╠══════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║  API Gateway:  http://localhost           ║${NC}"
echo -e "${CYAN}║  User Service: http://localhost:3001      ║${NC}"
echo -e "${CYAN}║  Game Service: http://localhost:3002      ║${NC}"
echo -e "${CYAN}║  Social:       http://localhost:3003      ║${NC}"
echo -e "${CYAN}║  Feed:         http://localhost:3004      ║${NC}"
echo -e "${CYAN}║  AI Engine:    http://localhost:8001      ║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║  PostgreSQL:   localhost:5433             ║${NC}"
echo -e "${CYAN}║  MongoDB:      localhost:27017            ║${NC}"
echo -e "${CYAN}║  Redis:        localhost:6380             ║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║  停止:  ./start.sh stop                   ║${NC}"
echo -e "${CYAN}║  日志:  ./start.sh logs                   ║${NC}"
echo -e "${CYAN}║  测试:  ./start.sh test                   ║${NC}"
echo -e "${CYAN}║  清理:  ./start.sh clean                  ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
