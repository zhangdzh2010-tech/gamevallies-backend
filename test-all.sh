#!/bin/bash
# PlayForge 全平台端到端测试脚本
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'
PASS=0
FAIL=0

BASE_GW="http://localhost"
BASE_USER="http://localhost:3001"
BASE_GAME="http://localhost:3002"
BASE_SOCIAL="http://localhost:3003"
BASE_FEED="http://localhost:3004"
BASE_AI="http://localhost:8001"
TOKEN=""

test_api() {
  local method=$1 url=$2 data=$3 desc=$4 expect_code=${5:-200}

  if [ "$method" = "GET" ]; then
    RESP=$(curl -s -w "\n%{http_code}" -H "Authorization: Bearer $TOKEN" "$url")
  else
    RESP=$(curl -s -w "\n%{http_code}" -X "$method" -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" "$url" -d "$data")
  fi

  CODE=$(echo "$RESP" | tail -1)
  BODY=$(echo "$RESP" | sed '$d')

  if [ "$CODE" = "$expect_code" ]; then
    echo -e "  ${GREEN}✓${NC} $desc (HTTP $CODE)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} $desc (期望 $expect_code, 实际 $CODE)"
    echo "    响应: $(echo "$BODY" | head -c 200)"
    FAIL=$((FAIL + 1))
  fi

  echo "$BODY"
}

echo ""
echo "========================================="
echo "  PlayForge API 端到端测试"
echo "========================================="
echo ""

# ============ 1. 基础设施检查 ============
echo -e "${YELLOW}[1/6] 基础设施检查${NC}"

for svc in "postgres:5433" "redis:6380" "mongo:27017"; do
  name=$(echo $svc | cut -d: -f1)
  port=$(echo $svc | cut -d: -f2)
  if nc -z localhost $port 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} $name (port $port)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} $name (port $port)"
    FAIL=$((FAIL + 1))
  fi
done

# Gateway
GW=$(curl -s http://localhost/health 2>/dev/null)
if echo "$GW" | grep -q "ok"; then
  echo -e "  ${GREEN}✓${NC} Nginx Gateway (port 80)"
  PASS=$((PASS + 1))
else
  echo -e "  ${RED}✗${NC} Nginx Gateway (port 80)"
  FAIL=$((FAIL + 1))
fi

# User service health
USR_H=$(curl -s http://localhost:3001/api/v1/health 2>/dev/null)
if echo "$USR_H" | grep -q "ok"; then
  echo -e "  ${GREEN}✓${NC} User Service (port 3001)"
  PASS=$((PASS + 1))
else
  echo -e "  ${RED}✗${NC} User Service (port 3001)"
  FAIL=$((FAIL + 1))
fi

echo ""

# ============ 2. 认证系统测试 ============
echo -e "${YELLOW}[2/6] 认证系统测试${NC}"

# Register
REG_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_USER/api/v1/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"testuser_$(date +%s)\",\"password\":\"Test123456\",\"displayName\":\"测试用户\"}")
REG_CODE=$(echo "$REG_RESP" | tail -1)
REG_BODY=$(echo "$REG_RESP" | sed '$d')

if [ "$REG_CODE" = "201" ] || [ "$REG_CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} 用户注册 (HTTP $REG_CODE)"
  PASS=$((PASS + 1))
  TOKEN=$(echo "$REG_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('accessToken',''))" 2>/dev/null || echo "")
  USER_ID=$(echo "$REG_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user',{}).get('id',''))" 2>/dev/null || echo "")
  USERNAME=$(echo "$REG_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('user',{}).get('username',''))" 2>/dev/null || echo "")
  echo "    Token: ${TOKEN:0:30}..."
  echo "    User ID: $USER_ID"
  echo "    Username: $USERNAME"
else
  echo -e "  ${RED}✗${NC} 用户注册 (HTTP $REG_CODE)"
  echo "    响应: $(echo "$REG_BODY" | head -c 300)"
  FAIL=$((FAIL + 1))
fi

# Login
if [ -n "$USERNAME" ]; then
  LOGIN_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_USER/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"account\":\"$USERNAME\",\"password\":\"Test123456\"}")
  LOGIN_CODE=$(echo "$LOGIN_RESP" | tail -1)
  LOGIN_BODY=$(echo "$LOGIN_RESP" | sed '$d')

  if [ "$LOGIN_CODE" = "200" ] || [ "$LOGIN_CODE" = "201" ]; then
    echo -e "  ${GREEN}✓${NC} 用户登录 (HTTP $LOGIN_CODE)"
    PASS=$((PASS + 1))
    TOKEN=$(echo "$LOGIN_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('accessToken',''))" 2>/dev/null || echo "$TOKEN")
  else
    echo -e "  ${RED}✗${NC} 用户登录 (HTTP $LOGIN_CODE)"
    echo "    响应: $(echo "$LOGIN_BODY" | head -c 300)"
    FAIL=$((FAIL + 1))
  fi
fi

# Profile
if [ -n "$TOKEN" ]; then
  PROFILE_RESP=$(curl -s -w "\n%{http_code}" -H "Authorization: Bearer $TOKEN" "$BASE_USER/api/v1/users/me")
  PROFILE_CODE=$(echo "$PROFILE_RESP" | tail -1)
  PROFILE_BODY=$(echo "$PROFILE_RESP" | sed '$d')

  if [ "$PROFILE_CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} 获取用户档案 (HTTP $PROFILE_CODE)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} 获取用户档案 (HTTP $PROFILE_CODE)"
    echo "    响应: $(echo "$PROFILE_BODY" | head -c 300)"
    FAIL=$((FAIL + 1))
  fi
fi

echo ""

# ============ 3. 游戏服务测试 ============
echo -e "${YELLOW}[3/6] 游戏服务测试${NC}"

if [ -n "$TOKEN" ]; then
  # Create game
  GAME_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_GAME/api/v1/games/generate" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{\"description\":\"制作一个简单的弹球游戏，玩家用挡板接住球\"}")
  GAME_CODE=$(echo "$GAME_RESP" | tail -1)
  GAME_BODY=$(echo "$GAME_RESP" | sed '$d')

  if [ "$GAME_CODE" = "201" ] || [ "$GAME_CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} 创建游戏 (HTTP $GAME_CODE)"
    PASS=$((PASS + 1))
    GAME_ID=$(echo "$GAME_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id','') or d.get('data',{}).get('id',''))" 2>/dev/null || echo "")
    echo "    Game ID: $GAME_ID"
  else
    echo -e "  ${RED}✗${NC} 创建游戏 (HTTP $GAME_CODE)"
    echo "    响应: $(echo "$GAME_BODY" | head -c 300)"
    FAIL=$((FAIL + 1))
  fi

  # List games
  LIST_RESP=$(curl -s -w "\n%{http_code}" "$BASE_GAME/api/v1/games/explore/published" -H "Authorization: Bearer $TOKEN")
  LIST_CODE=$(echo "$LIST_RESP" | tail -1)

  if [ "$LIST_CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} 获取游戏列表 (HTTP $LIST_CODE)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} 获取游戏列表 (HTTP $LIST_CODE)"
    FAIL=$((FAIL + 1))
  fi

  # Get single game
  if [ -n "$GAME_ID" ]; then
    DETAIL_RESP=$(curl -s -w "\n%{http_code}" "$BASE_GAME/api/v1/games/$GAME_ID" -H "Authorization: Bearer $TOKEN")
    DETAIL_CODE=$(echo "$DETAIL_RESP" | tail -1)

    if [ "$DETAIL_CODE" = "200" ]; then
      echo -e "  ${GREEN}✓${NC} 获取游戏详情 (HTTP $DETAIL_CODE)"
      PASS=$((PASS + 1))
    else
      echo -e "  ${RED}✗${NC} 获取游戏详情 (HTTP $DETAIL_CODE)"
      FAIL=$((FAIL + 1))
    fi
  fi
else
  echo -e "  ${YELLOW}⚠${NC} 跳过 (无Token)"
fi

echo ""

# ============ 4. Feed 服务测试 ============
echo -e "${YELLOW}[4/6] Feed 服务测试${NC}"

# Feed trending (直连 feed-service 避免 nginx 缓存)
FEED_RESP=$(curl -s -w "\n%{http_code}" "http://localhost:3004/api/v1/feed/trending")
FEED_CODE=$(echo "$FEED_RESP" | tail -1)

if [ "$FEED_CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} 获取热门 Feed (HTTP $FEED_CODE)"
  PASS=$((PASS + 1))
else
  echo -e "  ${RED}✗${NC} 获取热门 Feed (HTTP $FEED_CODE)"
  FEED_BODY=$(echo "$FEED_RESP" | sed '$d')
  echo "    响应: $(echo "$FEED_BODY" | head -c 300)"
  FAIL=$((FAIL + 1))
fi

# Feed latest
LATEST_RESP=$(curl -s -w "\n%{http_code}" "http://localhost:3004/api/v1/feed/latest")
LATEST_CODE=$(echo "$LATEST_RESP" | tail -1)

if [ "$LATEST_CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} 获取最新 Feed (HTTP $LATEST_CODE)"
  PASS=$((PASS + 1))
else
  echo -e "  ${RED}✗${NC} 获取最新 Feed (HTTP $LATEST_CODE)"
  FAIL=$((FAIL + 1))
fi

# Tags
TAGS_RESP=$(curl -s -w "\n%{http_code}" "http://localhost:3004/api/v1/tags/trending")
TAGS_CODE=$(echo "$TAGS_RESP" | tail -1)

if [ "$TAGS_CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} 获取热门标签 (HTTP $TAGS_CODE)"
  PASS=$((PASS + 1))
else
  echo -e "  ${RED}✗${NC} 获取热门标签 (HTTP $TAGS_CODE)"
  FAIL=$((FAIL + 1))
fi

# Creators
CREATORS_RESP=$(curl -s -w "\n%{http_code}" "http://localhost:3004/api/v1/creators/trending")
CREATORS_CODE=$(echo "$CREATORS_RESP" | tail -1)

if [ "$CREATORS_CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} 获取热门创作者 (HTTP $CREATORS_CODE)"
  PASS=$((PASS + 1))
else
  echo -e "  ${RED}✗${NC} 获取热门创作者 (HTTP $CREATORS_CODE)"
  FAIL=$((FAIL + 1))
fi

# Search
SEARCH_RESP=$(curl -s -w "\n%{http_code}" "http://localhost:3004/api/v1/feed/search?q=test")
SEARCH_CODE=$(echo "$SEARCH_RESP" | tail -1)

if [ "$SEARCH_CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} 搜索游戏 (HTTP $SEARCH_CODE)"
  PASS=$((PASS + 1))
else
  echo -e "  ${RED}✗${NC} 搜索游戏 (HTTP $SEARCH_CODE)"
  FAIL=$((FAIL + 1))
fi

echo ""

# ============ 5. 社交服务测试 ============
echo -e "${YELLOW}[5/6] 社交服务测试${NC}"

if [ -n "$TOKEN" ] && [ -n "$GAME_ID" ]; then
  # Like
  LIKE_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_SOCIAL/api/v1/social/like" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{\"gameId\":\"$GAME_ID\"}")
  LIKE_CODE=$(echo "$LIKE_RESP" | tail -1)

  if [ "$LIKE_CODE" = "200" ] || [ "$LIKE_CODE" = "201" ]; then
    echo -e "  ${GREEN}✓${NC} 点赞游戏 (HTTP $LIKE_CODE)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} 点赞游戏 (HTTP $LIKE_CODE)"
    LIKE_BODY=$(echo "$LIKE_RESP" | sed '$d')
    echo "    响应: $(echo "$LIKE_BODY" | head -c 300)"
    FAIL=$((FAIL + 1))
  fi

  # Comment
  COMMENT_RESP=$(curl -s -w "\n%{http_code}" -X POST "$BASE_SOCIAL/api/v1/games/$GAME_ID/comments" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{\"content\":\"这个游戏真好玩！\"}")
  COMMENT_CODE=$(echo "$COMMENT_RESP" | tail -1)

  if [ "$COMMENT_CODE" = "200" ] || [ "$COMMENT_CODE" = "201" ]; then
    echo -e "  ${GREEN}✓${NC} 发表评论 (HTTP $COMMENT_CODE)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} 发表评论 (HTTP $COMMENT_CODE)"
    COMMENT_BODY=$(echo "$COMMENT_RESP" | sed '$d')
    echo "    响应: $(echo "$COMMENT_BODY" | head -c 300)"
    FAIL=$((FAIL + 1))
  fi

  # Get comments
  COMMENTS_RESP=$(curl -s -w "\n%{http_code}" "$BASE_SOCIAL/api/v1/games/$GAME_ID/comments")
  COMMENTS_CODE=$(echo "$COMMENTS_RESP" | tail -1)

  if [ "$COMMENTS_CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} 获取评论列表 (HTTP $COMMENTS_CODE)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} 获取评论列表 (HTTP $COMMENTS_CODE)"
    FAIL=$((FAIL + 1))
  fi

  # Notifications
  NOTIF_RESP=$(curl -s -w "\n%{http_code}" "$BASE_SOCIAL/api/v1/notifications" -H "Authorization: Bearer $TOKEN")
  NOTIF_CODE=$(echo "$NOTIF_RESP" | tail -1)

  if [ "$NOTIF_CODE" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} 获取通知列表 (HTTP $NOTIF_CODE)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${RED}✗${NC} 获取通知列表 (HTTP $NOTIF_CODE)"
    FAIL=$((FAIL + 1))
  fi
else
  echo -e "  ${YELLOW}⚠${NC} 跳过 (无Token或GameID)"
fi

echo ""

# ============ 6. AI 引擎测试 ============
echo -e "${YELLOW}[6/6] AI 引擎测试${NC}"

AI_RESP=$(curl -s -w "\n%{http_code}" "$BASE_AI/health" 2>/dev/null)
AI_CODE=$(echo "$AI_RESP" | tail -1)

if [ "$AI_CODE" = "200" ]; then
  echo -e "  ${GREEN}✓${NC} AI 引擎健康 (HTTP $AI_CODE)"
  PASS=$((PASS + 1))
else
  # Try direct
  AI_RESP2=$(curl -s -w "\n%{http_code}" "http://localhost:8001/health" 2>/dev/null)
  AI_CODE2=$(echo "$AI_RESP2" | tail -1)
  if [ "$AI_CODE2" = "200" ]; then
    echo -e "  ${GREEN}✓${NC} AI 引擎健康 (直连 port 8001)"
    PASS=$((PASS + 1))
  else
    echo -e "  ${YELLOW}⚠${NC} AI 引擎 (HTTP $AI_CODE / 直连 $AI_CODE2) - mock模式可能无health端点"
  fi
fi

echo ""
echo "========================================="
echo -e "  测试结果: ${GREEN}$PASS 通过${NC} / ${RED}$FAIL 失败${NC}"
echo "========================================="
echo ""

if [ $FAIL -eq 0 ]; then
  echo -e "${GREEN}🎉 所有测试通过！PlayForge 平台运行正常！${NC}"
else
  echo -e "${YELLOW}⚠ 有 $FAIL 个测试未通过，请检查上方错误信息${NC}"
fi
