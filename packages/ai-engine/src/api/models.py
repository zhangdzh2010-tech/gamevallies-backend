"""Pydantic models for API requests and responses"""

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any


# ---------------------------------------------------------------------------
# Stage 01 – Dialogue Engine models
# ---------------------------------------------------------------------------

class DialogueState(str, Enum):
    greeting = "greeting"
    describing = "describing"
    clarifying = "clarifying"
    confirmed = "confirmed"


class SlotState(BaseModel):
    """10 slots defined in the Pipeline doc (6 required + 4 optional)"""
    # Required slots
    game_type: Optional[str] = None          # enum(12种)
    core_mechanic: Optional[str] = None      # string description
    theme: Optional[str] = None              # string
    input_method: Optional[str] = None       # touch/tap/swipe/tilt
    win_condition: Optional[str] = None      # string
    difficulty: Optional[str] = None         # easy/medium/hard/progressive

    # Optional slots
    visual_style: Optional[str] = None       # pixel/geometric/emoji/neon
    audio_style: Optional[str] = None        # chiptune/ambient/none
    special_rules: Optional[List[str]] = None
    reference_game: Optional[str] = None

    REQUIRED_SLOTS: List[str] = Field(
        default=["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"],
        exclude=True,
    )

    def fill_pct(self) -> float:
        required = ["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"]
        filled = sum(1 for s in required if getattr(self, s) is not None)
        return filled / len(required)

    def missing_required(self) -> List[str]:
        required = ["game_type", "core_mechanic", "theme", "input_method", "win_condition", "difficulty"]
        return [s for s in required if getattr(self, s) is None]


class ConversationMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Dialogue session ID")
    content: str = Field(..., description="User message")
    user_id: str = Field(..., description="User ID")


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    state: DialogueState
    slots_updated: List[str] = Field(default_factory=list)
    slot_fill_pct: float = Field(0.0, ge=0.0, le=1.0)
    ready_to_generate: bool = False


# ---------------------------------------------------------------------------
# Stage 02 – Intent Parser / GameSpec
# ---------------------------------------------------------------------------

class CoreMechanic(BaseModel):
    type: str
    input: str = "horizontal_move"
    difficulty_scaling: str = "progressive"


class GameEntity(BaseModel):
    name: str
    role: str  # player / obstacle / collectible / enemy / npc
    shape: Optional[str] = None
    color: Optional[str] = None
    spawn_rate: Optional[int] = None


class GameRules(BaseModel):
    win_condition: str = "survive"
    lose_condition: str = "lives_zero"
    scoring: str = "time"
    lives: int = Field(3, ge=1, le=99)
    time_limit: Optional[int] = None


class VisualStyle(BaseModel):
    theme: str = "space"
    palette: List[str] = Field(default_factory=lambda: ["#0a0a2e", "#6366f1", "#22c55e", "#f43f5e", "#ffffff"])
    art_style: str = "geometric"
    background: str = "gradient"
    effects: List[str] = Field(default_factory=list)


class PlatformConstraints(BaseModel):
    platform: str = "wechat_webview"
    max_code_size_kb: int = 300
    max_entities: int = 50
    input_mode: str = "touch_only"
    render_api: str = "canvas2d"
    max_memory_mb: int = 100
    target_fps: int = 60


class GameSpec(BaseModel):
    """Complete game specification – the core data contract of the Pipeline"""
    version: str = "1.0"
    game_type: str
    core_mechanics: List[CoreMechanic] = Field(default_factory=list)
    entities: List[GameEntity] = Field(default_factory=list)
    rules: GameRules = Field(default_factory=GameRules)
    visual_style: VisualStyle = Field(default_factory=VisualStyle)
    audio_style: str = "none"
    difficulty_curve: str = "progressive"
    platform_constraints: PlatformConstraints = Field(default_factory=PlatformConstraints)


# ---------------------------------------------------------------------------
# Stage 03 – Game Designer / GDD
# ---------------------------------------------------------------------------

class CanvasConfig(BaseModel):
    width: int = 420
    height: int = 600
    dpr_adaptive: bool = True
    target_fps: int = 60


class NumericsConfig(BaseModel):
    player_speed: float = 8.0
    base_obstacle_speed: float = 3.0
    speed_formula: str = "base + base * 0.02 * elapsed_s"
    spawn_interval_ms: int = 800
    score_per_second: int = 1
    score_per_collect: int = 10
    expected_survival_s: int = 60


class CollisionConfig(BaseModel):
    method: str = "AABB"
    hitbox_ratio: float = 0.8
    on_hit: str = "lives_minus_1"


class GDD(BaseModel):
    """Game Design Document – fully codeable parameters for the Code Generator"""
    canvas: CanvasConfig = Field(default_factory=CanvasConfig)
    numerics: NumericsConfig = Field(default_factory=NumericsConfig)
    collision: CollisionConfig = Field(default_factory=CollisionConfig)
    ui_layout: Dict[str, Any] = Field(default_factory=dict)
    input_map: Dict[str, str] = Field(default_factory=dict)
    state_machine: Dict[str, Any] = Field(default_factory=dict)
    raw_description: str = ""


# ---------------------------------------------------------------------------
# Stage 04 – Template Matcher
# ---------------------------------------------------------------------------

class TemplateMatchResult(BaseModel):
    template_id: Optional[str] = None
    confidence: float = 0.0
    path: str = "llm"  # template / hybrid / llm


# ---------------------------------------------------------------------------
# Stage 05 – Code Generator
# ---------------------------------------------------------------------------

class GenerateCodeResult(BaseModel):
    html_code: str
    strategy: str  # template / hybrid / llm
    template_id: Optional[str] = None
    generation_time_ms: int
    code_size_bytes: int


# ---------------------------------------------------------------------------
# Stage 06 – QA Pipeline
# ---------------------------------------------------------------------------

class QACheckError(BaseModel):
    type: str
    message: str
    severity: str = "error"
    line: Optional[int] = None


class QACheckResponse(BaseModel):
    passed: bool
    errors: List[QACheckError] = Field(default_factory=list)
    warnings: List[QACheckError] = Field(default_factory=list)
    validation_summary: Dict[str, bool] = Field(default_factory=dict)


class QAResult(BaseModel):
    success: bool
    code: str
    retries: int = 0
    last_errors: List[QACheckError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 07 – Iteration Engine
# ---------------------------------------------------------------------------

class IterationType(str, Enum):
    param_adjust = "param_adjust"        # regex replace, 0 tokens
    element_change = "element_change"    # LLM snippet, ~500 tokens
    mechanic_change = "mechanic_change"  # LLM rewrite, ~2000 tokens
    major_overhaul = "major_overhaul"    # full pipeline restart


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

class PipelineStage(str, Enum):
    intent_parsing = "intent_parsing"
    designing = "designing"
    template_matching = "template_matching"
    code_generating = "code_generating"
    qa_checking = "qa_checking"
    completed = "completed"
    failed = "failed"


class RunPipelineRequest(BaseModel):
    game_id: str
    description: str
    user_id: str
    platform: str = "wechat_webview"


class RunPipelineResponse(BaseModel):
    game_id: str
    html_code: str
    game_spec: GameSpec
    strategy: str
    qa_passed: bool
    qa_retries: int
    generation_time_ms: int
    code_size_bytes: int


# ---------------------------------------------------------------------------
# Dialogue session (in-memory state)
# ---------------------------------------------------------------------------

class DialogueSession(BaseModel):
    session_id: str
    user_id: str
    state: DialogueState = DialogueState.greeting
    slots: SlotState = Field(default_factory=SlotState)
    history: List[ConversationMessage] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Legacy models kept for backward compatibility
# ---------------------------------------------------------------------------

class ParseIntentRequest(BaseModel):
    description: str
    user_id: str


class ParseIntentResponse(BaseModel):
    spec: GameSpec
    confidence: float = Field(..., ge=0.0, le=1.0)


class GenerateCodeRequest(BaseModel):
    game_id: str
    spec: Optional[GameSpec] = None
    description: Optional[str] = None
    template_id: Optional[str] = None
    platform: str = "wechat_webview"


class GenerateCodeResponse(BaseModel):
    html_code: str
    strategy: str
    template_id: Optional[str] = None
    generation_time_ms: int
    code_size_bytes: int


class IterateRequest(BaseModel):
    game_id: str
    feedback: str
    conversation: List[Dict[str, str]] = Field(default_factory=list)
    current_code: str


class IterateResponse(BaseModel):
    html_code: str
    changes: List[str]
    iteration_type: str = "element_change"
    generation_time_ms: int


class QACheckRequest(BaseModel):
    html_code: str


class GenerateProgress(BaseModel):
    stage: str
    pct: int = Field(..., ge=0, le=100)
    message: str
    details: Optional[Dict[str, Any]] = None
