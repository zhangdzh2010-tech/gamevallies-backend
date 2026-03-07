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
| `MONGO_URL` | — | MongoDB connection string |
| `MONGO_DB_NAME` | `playforge` | MongoDB database name |
| `REDIS_URL` | — | Redis connection string |
| `LLM_MODE` | `mock` | `mock` (no API calls) or `real` (Claude API) |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_MODE=real` |
| `CLAUDE_MODEL` | `claude-sonnet-4-5` | Model for full code generation |
| `CLAUDE_FAST_MODEL` | `claude-haiku-4-5-20251001` | Model for slot extraction & quick tasks |
| `TEMPLATE_CONFIDENCE_THRESHOLD` | `0.8` | Confidence ≥ this → template-fill path |
| `HYBRID_CONFIDENCE_THRESHOLD` | `0.5` | Confidence ≥ this → hybrid path |
| `QA_MAX_RETRIES` | `3` | Max auto-fix retries after QA failure |
| `PIPELINE_TIMEOUT_S` | `60` | Hard pipeline timeout (seconds) |
| `MAX_ITERATIONS` | `20` | Max iteration rounds per game |
| `SLOT_MIN_FILL_PCT` | `0.6` | Min slot fill % to enter clarifying state |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins (JSON array) |

### LLM Modes

**mock** (default) – Uses keyword matching + pre-built templates. No API key required. Instant response. Use for local development and CI.

**real** – Uses Claude Sonnet 4.5 for full generation and Claude Haiku for fast tasks (slot extraction, iteration classification). Set `ANTHROPIC_API_KEY` in `.env`.

---

## 8-Stage Pipeline

```
User description
      │
  Stage 01 ─── Dialogue Engine      Multi-turn Slot Filling (10 slots)
  Stage 02 ─── Intent Parser        SlotState → GameSpec JSON
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
| `POST` | `/api/v1/ai/pipeline/iterate` | Stage 07: feedback → updated HTML |

**POST /api/v1/ai/pipeline/run**
```json
{
  "game_id": "uuid",
  "description": "做个太空躲避游戏，玩家左右移动躲陨石",
  "user_id": "user-uuid",
  "platform": "wechat_webview"
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
  "current_code": "<!DOCTYPE html>..."
}
```

### Dialogue (Stage 01)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/ai/dialogue/chat` | One dialogue turn (Slot Filling) |
| `GET` | `/api/v1/ai/dialogue/session/{id}` | Current session state + slots |

**POST /api/v1/ai/dialogue/chat**
```json
{
  "session_id": "sess-uuid",
  "content": "我想做个太空躲避游戏",
  "user_id": "user-uuid"
}
```

Response includes `ready_to_generate: true` when all required slots are filled, signalling the client to call `/pipeline/run`.

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
docker build -t playforge-ai-engine:latest .

docker run -p 8000:8000 \
  -e ENVIRONMENT=production \
  -e LLM_MODE=real \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e MONGO_URL=mongodb://... \
  -e REDIS_URL=redis://... \
  playforge-ai-engine:latest
```

---

Copyright © 2026 PlayForge. All rights reserved.
