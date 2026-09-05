"""Configuration settings using Pydantic BaseSettings"""

from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    """Application settings"""

    # Environment
    ENVIRONMENT: Literal["development", "production", "testing"] = "development"

    # Database (MySQL，game_bundles/game_templates 已迁入 MySQL)
    DATABASE_URL: str = "mysql://gamevallies_user:change_me@localhost:3306/gamevallies"

    # Cache / async task persistence. Empty (default) keeps the async task
    # manager purely in-memory; when set, task snapshots and the idempotency
    # index are persisted to Redis (best effort) so polling survives instance
    # recycling on VeFaaS.
    REDIS_URL: str = ""

    # LLM Configuration (MiniMax)
    LLM_MODE: Literal["real"] = "real"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "MiniMax-M2.5"
    LLM_FAST_MODEL: str = "MiniMax-M2.5"
    LLM_BASE_URL: str = "https://api.minimaxi.com/v1"

    # Anthropic / Claude Configuration
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-5"
    CLAUDE_FAST_MODEL: str = "claude-haiku-4-5-20251001"

    # Pipeline Configuration
    QA_MAX_RETRIES: int = 3
    PIPELINE_TIMEOUT_S: int = 1800
    LLM_LONG_GENERATION_TIMEOUT_S: int = 300
    LLM_LONG_GENERATION_MAX_TOKENS: int = 16384
    LLM_GENERATION_TOKEN_BUDGET_SIMPLE: int = 6144
    LLM_GENERATION_TOKEN_BUDGET_STANDARD: int = 12288
    LLM_GENERATION_TOKEN_BUDGET_COMPLEX: int = 16384
    LLM_PROVIDER_FAILOVER_ENABLED: bool = True
    LLM_PROVIDER_HEDGING_ENABLED: bool = True
    LLM_PROVIDER_HEDGING_DELAY_S: int = 45
    QA_REPAIR_TIMEOUT_S: int = 180
    QA_FAST_REPAIR_TIMEOUT_S: int = 120
    RUNTIME_QA_TIMEOUT_S: float = 8.0
    RUNTIME_QA_REQUIRED: bool = False
    CREATIVE_DESIGN_ENABLED: bool = True
    MAX_ITERATIONS: int = 20
    LLM_CODE_REVIEW_MIN_TIER: Literal["safe", "standard", "showcase"] = "standard"
    # Quality-gate targeted patch repair (create pipeline): when a candidate
    # narrowly misses the quality gate, attempt one section-patch fix before
    # falling back to a full regeneration round. Disable for fast rollback.
    QUALITY_GATE_PATCH_REPAIR_ENABLED: bool = True
    # Iterate-path non-blocking quality assessment: after all iterate
    # validations pass, run a fast LLM code review + quality scoring and
    # attach quality_score/quality_breakdown to the iterate response. Any
    # failure or timeout only logs a warning and never blocks the iteration.
    ITERATE_QUALITY_REVIEW_ENABLED: bool = True
    # Create-pipeline progress heartbeat during the long logic_generate LLM
    # call: emits pseudo-progress (60% -> 74%) every ~15s so clients don't
    # see the bar frozen at 60% for minutes. Disable for fast rollback.
    GENERATION_PROGRESS_HEARTBEAT_ENABLED: bool = True

    # Provider capability governance
    LLM_PROVIDER_VERIFICATION_ENABLED: bool = False
    LLM_PROVIDER_VERIFICATION_TIMEOUT_S: int = 30
    LLM_REQUIRE_VERIFIED_FOR_PRODUCTION: bool = False

    # Adaptive timeouts & token budgets (Phase 4)
    LLM_ADAPTIVE_REPAIR_TIMEOUTS_ENABLED: bool = True
    LLM_ADAPTIVE_TOKEN_BUDGET_ENABLED: bool = True
    LLM_ADAPTIVE_TOKEN_BUDGET_HISTORY_SIZE: int = 20

    # Intent parsing
    INTENT_PARSE_REQUEST_TIMEOUT_S: int = 45
    INTENT_PARSE_OVERALL_TIMEOUT_S: int = 90

    # API Settings
    API_TITLE: str = "PlayForge AI Engine"
    API_VERSION: str = "1.0.0"

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = 30

    # Service upstreams
    GAME_SERVICE_UPSTREAM_URL: str = ""
    ADMIN_TOKEN: str = ""
    SERVICE_REGION: str = "global"
    LLM_GATEWAY_CACHE_TTL_S: int = 10

    PORT: int = 8000

    # ------------------------------------------------------------------
    # P1 feature flags (PR-07 .. PR-12) — rolled out ON by default.
    # Each flag remains independently toggleable via env override so any
    # single component can be disabled without redeploy if a regression
    # is observed. All paths retain guarded-import + try/except fallbacks
    # so disabling a flag at runtime degrades cleanly to legacy behavior.
    # ------------------------------------------------------------------
    P1_CREATIVE_ANCHORS_ENABLED: bool = True             # PR-07
    P1_RANGE_SAMPLING_ENABLED: bool = True               # PR-08
    P1_DIVERSITY_PLANNER_ENABLED: bool = True            # PR-09
    P1_QA_TIERING_ENABLED: bool = True                   # PR-10
    P1_QA_CREATIVE_PRESERVE_THRESHOLD: float = 7.0       # PR-10
    P1_RUNTIME_QA_DEFERRED_ENABLED: bool = True          # PR-11
    P1_TEMPLATE_INSPIRATION_ENABLED: bool = True         # PR-12
    P1_TEMPLATE_LANE_SHARE: float = 0.2                  # PR-12 wire-up lane share
    P1_TEMPLATE_INSPIRATION_K: int = 2                   # PR-12 top-k references
    P1_GENERIC_ENTITY_MIX_SHARE: float = 0.2             # PR-08 generic-pool mix

    # ------------------------------------------------------------------
    # P2 feature flags — observability & adaptive policies. Rolled out ON
    # by default; disable via env override for targeted rollback.
    # ------------------------------------------------------------------
    P2_TELEMETRY_ENABLED: bool = True                    # P2.1 structured log telemetry
    # P2.2 adaptive creative-preserve threshold — reshape the PR-10 preserve
    # line per (tier, game_type).
    P2_ADAPTIVE_THRESHOLD_ENABLED: bool = True
    # P2.3 inspiration quality regression guard — circuit-breaker on the
    # PR-12 inspiration lane.
    P2_INSPIRATION_GUARD_ENABLED: bool = True
    P2_GUARD_WINDOW_SIZE: int = 20                       # rolling window per tier
    P2_GUARD_MIN_SAMPLES: int = 6                        # minimum hits AND misses before judging
    P2_GUARD_TRIP_DELTA: float = 0.75                    # miss_mean − hit_mean gap that trips

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
