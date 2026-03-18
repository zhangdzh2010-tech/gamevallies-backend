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
    LLM_MODE: Literal["mock", "real"] = "mock"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "MiniMax-M2.5"
    LLM_FAST_MODEL: str = "MiniMax-M2.5"
    LLM_BASE_URL: str = "https://api.minimaxi.com/v1"

    # Anthropic / Claude Configuration
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-5"
    CLAUDE_FAST_MODEL: str = "claude-haiku-4-5-20251001"

    # Pipeline Configuration
    TEMPLATE_CONFIDENCE_THRESHOLD: float = 0.8   # >= 0.8 → template path
    HYBRID_CONFIDENCE_THRESHOLD: float = 0.5     # 0.5-0.8 → hybrid path
    QA_MAX_RETRIES: int = 3
    PIPELINE_TIMEOUT_S: int = 60
    MAX_ITERATIONS: int = 20

    # Slot Filling
    SLOT_MIN_FILL_PCT: float = 0.6   # >=60% required slots → move to clarifying

    # API Settings
    API_TITLE: str = "PlayForge AI Engine"
    API_VERSION: str = "1.0.0"

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = 30

    PORT: int = 8000

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
