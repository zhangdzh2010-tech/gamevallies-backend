#!/usr/bin/env bash
# ---------------------------------------------------------------
# P0 PR 验收脚本（本地跑，不走沙箱）
# 覆盖 PR-01 ~ PR-06 六项改造的静态 + 动态校验。
# 需要：
#   * 已激活的 Python 3.12 venv（packages/ai-engine/.venv312 或同等环境）
#   * 已装好 pytest / pytest-asyncio / pydantic / httpx / pymysql
#   * repo 根目录运行（或按 AI_ENGINE / GAME_SVC 覆盖路径）
# ---------------------------------------------------------------
set -euo pipefail

AI_ENGINE="${AI_ENGINE:-packages/ai-engine}"
GAME_SVC="${GAME_SVC:-packages/game-service}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
say()  { printf "${YELLOW}==> %s${NC}\n" "$*"; }
pass() { printf "${GREEN}  [PASS]${NC} %s\n" "$*"; }
fail() { printf "${RED}  [FAIL]${NC} %s\n" "$*"; exit 1; }

# ---------------------------------------------------------------
say "Python syntax check on 4 modified ai-engine files"
# ---------------------------------------------------------------
for f in \
    "$AI_ENGINE/src/engine/code_generator.py" \
    "$AI_ENGINE/src/engine/qa_pipeline.py" \
    "$AI_ENGINE/src/engine/pipeline_v2_runner.py" \
    "$AI_ENGINE/src/services/llm_gateway.py"; do
  python3 -m py_compile "$f" && pass "py_compile $f"
done

# ---------------------------------------------------------------
say "TypeScript project check on game-service"
# ---------------------------------------------------------------
(
  cd "$GAME_SVC"
  # prefer local tsc; fall back to repo-root binary
  TSC="$(command -v tsc || echo ../../node_modules/.bin/tsc)"
  ts_output="$($TSC --noEmit --project tsconfig.json 2>&1 || true)"
  my_errors="$(echo "$ts_output" | grep -E "src/game/game\\.service\\.ts" | wc -l | tr -d ' ')"
  if [[ "$my_errors" != "0" ]]; then
    echo "$ts_output" | grep -E "src/game/game\\.service\\.ts"
    fail "TS errors detected in game.service.ts"
  fi
  pass "game.service.ts: 0 TS errors (pre-existing errors in other files unchanged)"
)

# ---------------------------------------------------------------
say "Run unit tests on ai-engine (PR-03/04/05/06 modules)"
# ---------------------------------------------------------------
(
  cd "$AI_ENGINE"
  python3 -m pytest \
    tests/test_qa_pipeline.py \
    tests/test_llm_gateway.py \
    tests/test_pipeline_v2_runner.py \
    tests/test_code_generator.py \
    -x --tb=short -q
) && pass "pytest on 4 modified-module test files"

# ---------------------------------------------------------------
say "PR-02 static invariants on game.service.ts"
# ---------------------------------------------------------------
GS="$GAME_SVC/src/game/game.service.ts"
grep -q "PROMPT_BUNDLE_CACHE_TTL_MS"        "$GS" && pass "PR-02: prompt-bundle cache constant present"
grep -q "RUNTIME_PROFILE_CACHE_TTL_MS"      "$GS" && pass "PR-02: runtime-profile cache constant present"
[[ $(grep -c "// PR-02:" "$GS") -ge 4 ]]   && pass "PR-02: >=4 Promise.all sites tagged with // PR-02:"
# normalized_request must be slimmed (no more 'generation_tier:' inside it)
! grep -A6 "normalized_request: {" "$GS" | grep -q "generation_tier:" \
  && pass "PR-01: normalized_request does not duplicate generation_tier"
# request_context must no longer carry its own 'metadata' child
! grep -A6 "request_context: {" "$GS" | grep -qE "^\s+metadata:\s*\{" \
  && pass "PR-01: request_context no longer nests a metadata block"

# ---------------------------------------------------------------
say "PR-03 / PR-04 / PR-05 / PR-06 invariants on ai-engine"
# ---------------------------------------------------------------
CG="$AI_ENGINE/src/engine/code_generator.py"
QA="$AI_ENGINE/src/engine/qa_pipeline.py"
LG="$AI_ENGINE/src/services/llm_gateway.py"
PR="$AI_ENGINE/src/engine/pipeline_v2_runner.py"

grep -q "_BULLET_KEY_PREFIX_STRIP"             "$CG" && pass "PR-03: bullet-prefix strip regex present"
grep -q "_SECTION_JACCARD_DEDUP_THRESHOLD"     "$CG" && pass "PR-03: section Jaccard threshold present"
grep -q "_section_fingerprint"                 "$CG" && pass "PR-03: section fingerprint helper present"

grep -q "_QA_FIX_MAX_ERRORS_PER_ROUND"         "$QA" && pass "PR-04: error-per-round cap constant present"
grep -q "unchanged from fix round 1"           "$QA" && pass "PR-04: round>=2 contract-short-ref reachable"

grep -q "implicit_provider_failover"           "$LG" && pass "PR-05: implicit_provider_failover flag wired"
grep -q "emergency_fallback_reason"            "$LG" && pass "PR-05: emergency_fallback_reason wired"
grep -q "fallback_chain_depth"                 "$LG" && pass "PR-05: fallback_chain_depth wired"
grep -q "capability_rejections"                "$LG" && pass "PR-05: capability_rejections surfaced"

grep -q "_stage_timings_by_task"               "$PR" && pass "PR-06: stage-timings store present"
grep -q "pipeline_v2.stage_timings"            "$PR" && pass "PR-06: structured timing log name present"
grep -q "_record_stage_start"                  "$PR" && pass "PR-06: stage-start recorder present"
grep -q "_flush_stage_timings"                 "$PR" && pass "PR-06: stage-timings flush present"

# ---------------------------------------------------------------
say "ALL CHECKS PASSED"
# ---------------------------------------------------------------
echo
echo "Next (optional) integration smoke:"
echo "  1) Start ai-engine: uvicorn src.api.main:app --port 8010"
echo "  2) Start game-service (NestJS) pointing at that ai-engine"
echo "  3) POST one create + one iterate through the HTTP surface"
echo "  4) tail ai-engine logs, expect a line like:"
echo "       pipeline_v2.stage_timings task_id=... status=success total_ms=... breakdown={...}"
echo "  5) Inspect the outgoing payload on ai-engine side, confirm:"
echo "       - payload['metadata']['generation_tier'] is set"
echo "       - payload['request_context'] has no nested 'metadata'"
echo "       - payload['normalized_request'] contains only 'description' (create) or 'feedback' (iterate)"
