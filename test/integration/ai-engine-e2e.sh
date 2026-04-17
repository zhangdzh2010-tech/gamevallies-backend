#!/usr/bin/env bash
# ============================================================
# AI Engine E2E Test – 真实大模型完整游戏生成流程
# ============================================================
set -euo pipefail

BASE="http://localhost:8000"
PASS=0; FAIL=0
GENERATED_HTML=""

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

now_ms() { python3 -c "import time; print(int(time.time()*1000))"; }

check() {
  local name="$1" result="$2" expect="$3"
  if echo "$result" | grep -q "$expect" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} $name"
    PASS=$((PASS+1))
  else
    echo -e "  ${RED}✗${NC} $name"
    echo -e "    ${YELLOW}期望包含:${NC} $expect"
    echo -e "    ${YELLOW}实际(前300字):${NC} $(echo "$result" | head -c 300)"
    FAIL=$((FAIL+1))
  fi
}

section() { echo -e "\n${CYAN}▶ $1${NC}"; }

py() { python3 -c "$1" 2>/dev/null || echo "?"; }

# ────────────────────────────────────────────────────────────
section "?? 1: ????? - expand-prompt (Create Entry)"
# ============================================================

USER_ID="test-user-001"
PROMPT_TEXT="???????????????????????????"

echo "  [1/1] ???????..."
EXPAND=$(curl -sf -X POST "$BASE/api/v1/ai/expand-prompt" -H "Content-Type: application/json" -d "{\"description\":\"$PROMPT_TEXT\"}")

check "expand-prompt ??" "$EXPAND" "expanded_prompt"
EXPANDED_PROMPT=$(py "import json; d=json.loads('''$EXPAND'''); print(d['expanded_prompt'])")
EXPANDED_PREVIEW=$(py "import json; d=json.loads('''$EXPAND'''); text=d['expanded_prompt']; print(text[:120]+'...' if len(text)>120 else text)")
echo "  ? ????: $EXPANDED_PREVIEW"

# ============================================================
section "?? 2: ???? - parse-intent (Stage 02)"
# ────────────────────────────────────────────────────────────

echo "  调用 DeepSeek 解析游戏意图..."
T0=$(now_ms)
PARSE=$(curl -sf -X POST "$BASE/api/v1/ai/parse-intent" \
  -H "Content-Type: application/json" \
  -d "{\"description\":\"$EXPANDED_PROMPT\",\"user_id\":\"$USER_ID\"}")
T1=$(now_ms)
PARSE_MS=$((T1-T0))

check "意图解析成功" "$PARSE" "spec"
check "识别出 game_type" "$PARSE" "game_type"
check "置信度字段存在" "$PARSE" "confidence"
GAME_TYPE=$(py "import json; d=json.loads('$PARSE'); print(d['spec']['game_type'])")
CONF=$(py "import json; d=json.loads('$PARSE'); print('{:.2f}'.format(d['confidence']))")
echo "  → 游戏类型: $GAME_TYPE, 置信度: $CONF, 耗时: ${PARSE_MS}ms"

# ────────────────────────────────────────────────────────────
section "阶段 3-6: 完整 Pipeline 运行 - 真实 LLM 生成游戏"
# ────────────────────────────────────────────────────────────

echo "  正在调用 DeepSeek 生成完整 HTML5 游戏（预计 20-90s）..."
GAME_ID="test-game-$(date +%s)"
T0=$(now_ms)

PIPELINE=$(curl -sf --max-time 180 -X POST "$BASE/api/v1/ai/pipeline/run" \
  -H "Content-Type: application/json" \
  -d "{
    \"game_id\":\"$GAME_ID\",
    \"description\":\"一个可爱的横版跑酷游戏：小兔子在森林里奔跑，玩家点击屏幕让兔子跳跃，躲开树桩和石头障碍，收集胡萝卜加分。游戏速度随时间加快，碰到障碍游戏结束，显示最高分。\",
    \"user_id\":\"$USER_ID\",
    \"platform\":\"wechat_webview\"
  }")

T1=$(now_ms)
PIPELINE_MS=$((T1-T0))

check "Pipeline 执行成功" "$PIPELINE" "html_code"
check "game_id 正确" "$PIPELINE" "$GAME_ID"
check "生成策略字段" "$PIPELINE" "strategy"
check "QA 结果字段" "$PIPELINE" "qa_passed"
check "HTML DOCTYPE 声明" "$PIPELINE" "DOCTYPE"
check "HTML canvas 元素" "$PIPELINE" "canvas"

STRATEGY=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(d.get('strategy','?'))" <<< "$PIPELINE")
QA_PASSED=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(d.get('qa_passed','?'))" <<< "$PIPELINE")
QA_RETRIES=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(d.get('qa_retries','?'))" <<< "$PIPELINE")
CODE_SIZE=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(d.get('code_size_bytes','?'))" <<< "$PIPELINE")
GEN_TIME=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(d.get('generation_time_ms','?'))" <<< "$PIPELINE")

echo "  → 生成策略: $STRATEGY"
echo "  → QA 通过: $QA_PASSED (重试次数: $QA_RETRIES)"
echo "  → 代码大小: $CODE_SIZE bytes"
echo "  → 总耗时: ${PIPELINE_MS}ms (引擎: ${GEN_TIME}ms)"

# 提取 HTML 代码
python3 -c "
import json, sys
d = json.loads(open('/dev/stdin').read())
html = d.get('html_code','')
open('/tmp/generated-game.html','w').write(html)
print(f'HTML 大小: {len(html)} 字符')
" <<< "$PIPELINE"

GENERATED_HTML=$(cat /tmp/generated-game.html 2>/dev/null || echo "")
HTML_LEN=${#GENERATED_HTML}
if [ "$HTML_LEN" -gt 1000 ]; then
  echo -e "  ${GREEN}✓${NC} HTML 代码有效 (${HTML_LEN} 字符, >1KB)"
  PASS=$((PASS+1))
else
  echo -e "  ${RED}✗${NC} HTML 代码过短: ${HTML_LEN} 字符"
  FAIL=$((FAIL+1))
fi

# ────────────────────────────────────────────────────────────
section "阶段 3-6: Legacy generate-code 接口兼容性测试"
# ────────────────────────────────────────────────────────────

echo "  测试旧版 /generate-code 接口（贪吃蛇）..."
T0=$(now_ms)
LEGACY=$(curl -sf --max-time 180 -X POST "$BASE/api/v1/ai/generate-code" \
  -H "Content-Type: application/json" \
  -d "{\"game_id\":\"legacy-$(date +%s)\",\"description\":\"经典贪吃蛇游戏，方向键或触控控制蛇移动，吃食物变长，撞墙或自身结束\"}")
T1=$(now_ms)
LEGACY_MS=$((T1-T0))

check "Legacy 接口响应成功" "$LEGACY" "html_code"
check "Legacy HTML 包含 DOCTYPE" "$LEGACY" "DOCTYPE"
check "Legacy 包含 strategy" "$LEGACY" "strategy"
LEGACY_SIZE=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(d.get('code_size_bytes','?'))" <<< "$LEGACY")
echo "  → 代码大小: $LEGACY_SIZE bytes, 耗时: ${LEGACY_MS}ms"

# ────────────────────────────────────────────────────────────
section "阶段 6: QA Pipeline 独立检测"
# ────────────────────────────────────────────────────────────

if [ -f /tmp/generated-game.html ] && [ -s /tmp/generated-game.html ]; then
  echo "  对生成的 HTML 进行 QA 独立检测..."
  QA_PAYLOAD=$(python3 -c "
import json
html = open('/tmp/generated-game.html').read()
print(json.dumps({'html_code': html}))
")
  QA_RESULT=$(echo "$QA_PAYLOAD" | curl -sf --max-time 30 -X POST "$BASE/api/v1/ai/qa-check" \
    -H "Content-Type: application/json" \
    -d @-)

  check "QA 检测有响应" "$QA_RESULT" "passed"
  QA_PASS=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(d.get('passed','?'))" <<< "$QA_RESULT")
  QA_ERRORS=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(len(d.get('errors',[])))" <<< "$QA_RESULT")
  QA_WARNS=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(len(d.get('warnings',[])))" <<< "$QA_RESULT")
  echo "  → QA 通过: $QA_PASS, 错误: $QA_ERRORS, 警告: $QA_WARNS"
else
  echo "  跳过 QA 检测（无生成代码）"
fi

# ────────────────────────────────────────────────────────────
section "阶段 7: 迭代引擎 - 用户反馈修改"
# ────────────────────────────────────────────────────────────

if [ -f /tmp/generated-game.html ] && [ -s /tmp/generated-game.html ]; then
  echo "  提交修改请求：改变外观和增加功能..."
  T0=$(now_ms)
  python3 - <<'PYEOF'
import json, subprocess

html = open('/tmp/generated-game.html').read()
payload = json.dumps({
    "game_id": "iter-test-001",
    "feedback": "把游戏背景改为深蓝色星空效果，兔子角色改为红色，并在右上角增加显示存活秒数的计时器",
    "current_code": html,
    "conversation": [
        {"role": "user", "content": "我想改一下游戏的外观"},
        {"role": "assistant", "content": "好的，请告诉我您想修改什么？"}
    ]
})
with open('/tmp/iter-payload.json', 'w') as f:
    f.write(payload)
print("  迭代 payload 已准备 (大小: {} bytes)".format(len(payload)))
PYEOF

  ITER=$(curl -sf --max-time 180 -X POST "$BASE/api/v1/ai/pipeline/iterate" \
    -H "Content-Type: application/json" \
    -d @/tmp/iter-payload.json)
  T1=$(now_ms)
  ITER_MS=$((T1-T0))

  check "迭代响应成功" "$ITER" "html_code"
  check "迭代类型字段" "$ITER" "iteration_type"
  check "迭代后 HTML 有效" "$ITER" "DOCTYPE"
  check "changes 字段存在" "$ITER" "changes"

  ITER_TYPE=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(d.get('iteration_type','?'))" <<< "$ITER")
  ITER_SIZE=$(py "import json; d=json.loads(open('/dev/stdin').read()); print(len(d.get('html_code','')))" <<< "$ITER")
  echo "  → 迭代类型: $ITER_TYPE, 迭代后大小: $ITER_SIZE 字符, 耗时: ${ITER_MS}ms"

  python3 -c "
import json
d = json.loads(open('/dev/stdin').read())
open('/tmp/iterated-game.html','w').write(d.get('html_code',''))
" <<< "$ITER"
  echo "  → 迭代结果已保存: /tmp/iterated-game.html"
else
  echo "  跳过迭代测试（无初始代码）"
fi

# ────────────────────────────────────────────────────────────
section "HTML 内容质量深度验证"
# ────────────────────────────────────────────────────────────

if [ -f /tmp/generated-game.html ] && [ -s /tmp/generated-game.html ]; then
  python3 - <<'PYEOF'
import re, sys

html = open('/tmp/generated-game.html').read()

checks = [
    ("DOCTYPE 声明",        bool(re.search(r'<!DOCTYPE', html, re.I))),
    ("canvas 元素",          bool(re.search(r'<canvas', html, re.I))),
    ("JavaScript 存在",      bool(re.search(r'<script', html, re.I))),
    ("requestAnimationFrame / setInterval", bool(re.search(r'requestAnimationFrame|setInterval', html))),
    ("分数/计分系统",         bool(re.search(r'score|Score|分数|得分', html))),
    ("用户输入处理",          bool(re.search(r'addEventListener|onclick|touchstart|keydown', html))),
    ("游戏结束逻辑",          bool(re.search(r'gameOver|game_over|gameover|结束|Game Over', html, re.I))),
    ("canvas getContext",    bool(re.search(r'getContext', html))),
    ("代码 > 5KB",           len(html.encode()) > 5000),
]

green = '\033[0;32m'; red = '\033[0;31m'; nc = '\033[0m'
for name, ok in checks:
    icon = f"{green}✓{nc}" if ok else f"{red}✗{nc}"
    print(f"  {icon} {name}")

print(f"\n  → 文件大小: {len(html):,} 字符 / {len(html.encode()):,} bytes")
print(f"  → script 标签数: {len(re.findall(r'<script', html, re.I))}")
print(f"  → canvas 元素数: {len(re.findall(r'<canvas', html, re.I))}")
PYEOF
fi

# ────────────────────────────────────────────────────────────
section "输出文件概览"
# ────────────────────────────────────────────────────────────

for f in /tmp/generated-game.html /tmp/iterated-game.html; do
  if [ -f "$f" ] && [ -s "$f" ]; then
    SIZE=$(wc -c < "$f")
    echo "  → $f ($SIZE bytes)"
    echo "    前100字符: $(head -c 100 "$f")"
  fi
done

# ────────────────────────────────────────────────────────────
section "测试汇总"
# ────────────────────────────────────────────────────────────

TOTAL=$((PASS+FAIL))
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  总计: $TOTAL 项 | ${GREEN}通过: $PASS${NC} | ${RED}失败: $FAIL${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ $FAIL -eq 0 ]; then
  echo -e "  ${GREEN}所有测试通过！AI 游戏生成完整流程验证成功。${NC}"
  exit 0
else
  echo -e "  ${RED}有 $FAIL 项测试失败。${NC}"
  exit 1
fi
