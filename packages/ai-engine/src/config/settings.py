"""Configuration settings using Pydantic BaseSettings"""

from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    """Application settings"""

    # Environment
    ENVIRONMENT: Literal["development", "production", "testing"] = "development"

    # Database (MySQL，game_bundles/game_templates 已迁入 MySQL)
    DATABASE_URL: str = "mysql://gamevallies_user:change_me@localhost:3306/gamevallies"

    # Cache
    REDIS_URL: str = "redis://localhost:6379"

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
    REVIEW_REPAIR_MAX_RETRIES: int = 0
    REVIEW_REPAIR_MAX_TOKENS: int = 3072
    PIPELINE_TIMEOUT_S: int = 1200
    LLM_LONG_GENERATION_TIMEOUT_S: int = 240
    LLM_LONG_GENERATION_MAX_TOKENS: int = 6144
    LLM_PROVIDER_FAILOVER_ENABLED: bool = True
    QA_REPAIR_TIMEOUT_S: int = 180
    QA_FAST_REPAIR_TIMEOUT_S: int = 120
    RUNTIME_QA_REMEDIATION_MAX_RETRIES: int = 2
    RUNTIME_QA_TIMEOUT_S: float = 8.0
    RUNTIME_QA_REQUIRED: bool = False
    MAX_ITERATIONS: int = 20
    PIPELINE_UPGRADE_LEGACY_ENDPOINTS_TO_V2: bool = True

    # Slot Filling
    SLOT_MIN_FILL_PCT: float = 0.6   # >=60% required slots → move to clarifying

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

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
