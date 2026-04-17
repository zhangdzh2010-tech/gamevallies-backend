# PlayForge AI Engine

AI-powered game generation service. Converts natural language descriptions into fully playable single-file HTML5 games through an 8-stage pipeline.

## Quick Start

```bash
cd packages/ai-engine

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env – set LLM_MODE=mock for local dev (no API key needed)

# Start development server
python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

API: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

---

## Configuration

All configuration is via environment variables. See [`.env.example`](.env.example) for the full reference.

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` / `production` / `testing` |
| `PORT` | `8000` | HTTP server port |
| `DATABASE_URL` | `mysql://gamevallies_user:change_me@localhost:3306/gamevallies` | MySQL connection string |
| `REDIS_URL` | — | Redis connection string |
| `LLM_MODE` | `mock` | `mock` (no API calls) or `real` (Claude API) |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_MODE=real` |
| `CLAUDE_MODEL` | `claude-sonnet-4-5` | Model for full code generation |
| `CLAUDE_FAST_MODEL` | `claude-haiku-4-5-20251001` | Model for prompt expansion, iteration classification, and other fast auxiliary tasks |
| `TEMPLATE_CONFIDENCE_THRESHOLD` | `0.8` | Confidence ≥ this → template-fill path |
| `HYBRID_CONFIDENCE_THRESHOLD` | `0.5` | Confidence ≥ this → hybrid path |
| `QA_MAX_RETRIES` | `3` | Max auto-fix retries after QA failure |
| `PIPELINE_TIMEOUT_S` | `600` | Hard pipeline timeout (seconds) |
| `MAX_ITERATIONS` | `20` | Max iteration rounds per game |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins (JSON array) |

### LLM Modes

**mock** (default) – Uses keyword matching + pre-built templates. No API key required. Instant response. Use for local development and CI.

**real** – Uses Claude Sonnet 4.5 for full generation and Claude Haiku for fast tasks (prompt expansion and iteration classification). Set `ANTHROPIC_API_KEY` in `.env`.

---

## 8-Stage Pipeline

```
User description
      │
  Stage 01 ??? Prompt Expansion     Short brief ? user-confirmable design prompt
  Stage 02 ??? Intent Parser        Confirmed brief ? GameSpec JSON
  Stage 03 ─── Game Designer        GameSpec → GDD (numerical parameters)
  Stage 04 ─── Template Matcher     GDD → template_id + confidence score
  Stage 05 ─── Code Generator       GDD + template → HTML5 code  (dual-path)
  Stage 06 ─── QA Pipeline          6-checkpoint validation + auto-fix loop
  Stage 07 ─── Iteration Engine     User feedback → incremental code update
  Stage 08 ─── Publish Engine       QA-passed HTML → CDN URL  (game-service)
      │
  Playable HTML5 game
```

### Stage 05 – Code Generation Paths

| Path | Condition | Model | Latency | Cost |
|---|---|---|---|---|
| Template fill | confidence ≥ 0.8 | Haiku (param fill) | ~3s | ~$0.003 |
| Hybrid | 0.5 ≤ confidence < 0.8 | Sonnet | ~8s | ~$0.01 |
| Full LLM | confidence < 0.5 | Sonnet | ~12s | ~$0.02 |

### Stage 06 – QA Checkpoints

| # | Name | What is checked |
|---|---|---|
| L1 | Syntax | `<html>`, `<body>`, `</html>` present |
| L2 | Security | No `eval`, `fetch`, `localStorage`, `WebSocket`, etc. |
| L3 | Startup | `<canvas>`, `getContext()`, `requestAnimationFrame` |
| L4 | Playability | Touch events, game-over state, score variable |
| L5 | Performance | File size < 500 KB, no unbounded loops |
| L6 | Content Safety | Keyword filter (production: WeChat msgSecCheck) |

On failure, Claude is called with a targeted fix prompt. Retries up to `QA_MAX_RETRIES` times.

---

## API Endpoints

### Pipeline

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/ai/pipeline/run` | **Main**: description → HTML (stages 02–06) |
| `POST` | `/api/v1/ai/pipeline/run/async` | 创建异步生成任务，立即返回任务句柄 |
| `POST` | `/api/v1/ai/pipeline/iterate` | Stage 07: feedback → updated HTML |
| `POST` | `/api/v1/ai/pipeline/iterate/async` | 创建异步迭代任务 |
| `GET` | `/api/v1/ai/tasks/{task_id}` | 查询异步任务状态与结果 |
| `GET` | `/api/v1/ai/tasks` | 列出异步任务，可按 `user_id/game_id/status` 过滤 |
| `POST` | `/api/v1/ai/tasks/{task_id}/cancel` | 取消运行中的异步任务 |

**POST /api/v1/ai/pipeline/run**
```json
{
  "game_id": "uuid",
  "description": "做个太空躲避游戏，玩家左右移动躲陨石",
  "user_id": "user-uuid",
  "platform": "wechat_webview",
  "timeout_s": 600
}
```

Response:
```json
{
  "game_id": "uuid",
  "html_code": "<!DOCTYPE html>...",
  "game_spec": { "game_type": "dodge", ... },
  "strategy": "template",
  "qa_passed": true,
  "qa_retries": 0,
  "generation_time_ms": 1240,
  "code_size_bytes": 12300
}
```

**POST /api/v1/ai/pipeline/iterate**
```json
{
  "game_id": "uuid",
  "feedback": "飞船速度太快了，降低一半",
  "conversation": [],
  "current_code": "<!DOCTYPE html>...",
  "timeout_s": 600
}
```

**POST /api/v1/ai/pipeline/run/async**
```json
{
  "game_id": "uuid",
  "description": "做个太空躲避游戏，玩家左右移动躲陨石",
  "user_id": "user-uuid",
  "timeout_s": 600
}
```

Response:
```json
{
  "task_id": "2fd0...",
  "task_type": "pipeline_run",
  "status": "queued",
  "game_id": "uuid",
  "user_id": "user-uuid",
  "timeout_s": 600,
  "ws_channel": "game:uuid",
  "poll_url": "/api/v1/ai/tasks/2fd0...",
  "cancel_url": "/api/v1/ai/tasks/2fd0.../cancel"
}
```

### Async Task Lifecycle

- `queued`：任务已创建，等待执行
- `running`：任务执行中，可结合 `ws_channel` 订阅进度
- `succeeded`：任务完成，`result` 中返回与同步接口一致的结果体
- `failed`：任务失败，`error` 中包含失败阶段与重试次数
- `canceled`：任务已取消

前端推荐接法：

1. 调用 `/pipeline/run/async` 或 `/pipeline/iterate/async`
2. 保存 `task_id`、`ws_channel`、`poll_url`
3. 优先监听 WebSocket 进度，断线或冷启动时轮询 `GET /api/v1/ai/tasks/{task_id}`
4. 任务进入 `succeeded` 后直接读取 `result`

### Create Brief Expansion

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/ai/expand-prompt` | Expand a short create brief into a user-confirmable design prompt |
| `POST` | `/api/v1/ai/parse-intent` | Convert the confirmed brief into `GameSpec` |

**POST /api/v1/ai/expand-prompt**
```json
{
  "description": "?????????????????????"
}
```

Response:
```json
{
  "expanded_prompt": "Game Type: casual\nCore Mechanic: swipe left and right to dodge meteors\nTheme: neon space..."
}
```

Recommended usage for the current creation-session flow: call `/expand-prompt`, show the returned brief back to the user for confirmation or edits, then pass the confirmed text into `/parse-intent` or the create pipeline.

### Legacy (backward compatible)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/ai/generate-code` | Wraps `/pipeline/run` |
| `POST` | `/api/v1/ai/iterate-code` | Wraps `/pipeline/iterate` |
| `POST` | `/api/v1/ai/parse-intent` | Returns `GameSpec` from description |
| `POST` | `/api/v1/ai/qa-check` | Validates HTML code |
| `GET` | `/api/v1/ai/health` | Health check |

### WebSocket

`WS /ws/generation/{game_id}` – real-time generation progress.

```json
{ "type": "progress", "stage": "code_generating", "pct": 60, "message": "生成游戏代码…" }
```

---

## Project Structure

```
ai-engine/
├── src/
│   ├── main.py                          # FastAPI app entry point
│   ├── config/
│   │   └── settings.py                  # All config vars (pydantic-settings)
│   ├── api/
│   │   ├── models.py                    # All Pydantic request/response models
│   │   └── endpoints/
│   │       └── generate.py              # All API routes
│   ├── engine/
│   │   ├── dialogue_engine.py           # Stage 01+02: Slot Filling + GameSpec
│   │   ├── game_designer.py             # Stage 03: GDD + numerical balance
│   │   ├── template_engine.py           # Stage 04: Template matching
│   │   ├── code_generator.py            # Stage 05+07: Code gen + iteration
│   │   ├── qa_pipeline.py               # Stage 06: 6-checkpoint QA + auto-fix
│   │   └── pipeline_orchestrator.py     # Central coordinator (stages 02–07)
│   ├── services/
│   │   └── websocket_manager.py         # WebSocket connection manager
│   └── templates/                       # Pre-built HTML5 game templates
│       ├── space_dodge.html
│       ├── fruit_catcher.html
│       ├── maze_runner.html
│       ├── rhythm_tap.html
│       └── platform_jump.html
├── .env                                 # Local config (gitignored)
├── .env.example                         # Config reference
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## Game Templates

| Template | Game Type | Customisable Parameters |
|---|---|---|
| `space_dodge.html` | dodge | colors, lives, speed, entity shapes |
| `fruit_catcher.html` | catcher | colors, basket width, fruit types |
| `maze_runner.html` | runner | colors, obstacle types, speed |
| `rhythm_tap.html` | rhythm | colors, note speed, lanes |
| `platform_jump.html` | platformer | colors, gravity, platform gap |

Template placeholders: `{{BACKGROUND_COLOR}}`, `{{PRIMARY_COLOR}}`, `{{SECONDARY_COLOR}}`, `{{TEXT_COLOR}}`, `{{LIVES}}`, `{{SPEED_MULTIPLIER}}`

---

## Supported Game Types

`dodge` · `platformer` · `runner` · `shooter` · `puzzle` · `rhythm` · `tower_defense` · `sandbox` · `card` · `rpg` · `idle` · `racing`

---

## Development

```bash
# Format
black src/

# Lint
flake8 src/

# Type check
mypy src/

# Tests
pytest
pytest --cov=src tests/
```

## Docker

```bash
docker build -t gamevallies-ai-engine:latest .

docker run -p 8000:8000 \
  -e ENVIRONMENT=production \
  -e LLM_MODE=real \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e DATABASE_URL=mysql://gamevallies_user:change_me@mysql:3306/gamevallies \
  -e REDIS_URL=redis://... \
  gamevallies-ai-engine:latest
```

---

Copyright © 2026 PlayForge. All rights reserved.
