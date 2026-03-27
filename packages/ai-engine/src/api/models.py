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
    source_description: str = ""
    intent_summary: str = ""
    ui_language: str = "en-US"
    core_mechanics: List[CoreMechanic] = Field(default_factory=list)
    entities: List[GameEntity] = Field(default_factory=list)
    rules: GameRules = Field(default_factory=GameRules)
    visual_style: VisualStyle = Field(default_factory=VisualStyle)
    audio_style: str = "none"
    difficulty_curve: str = "progressive"
    special_rules: List[str] = Field(default_factory=list)
    reference_game: Optional[str] = None
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


class EnrichedGDD(GDD):
    """Extended GDD with LLM-generated design details for deeper gameplay."""
    level_design: List[Dict[str, Any]] = Field(default_factory=list)
    enemy_behaviors: List[Dict[str, Any]] = Field(default_factory=list)
    difficulty_curve_params: Dict[str, Any] = Field(default_factory=dict)
    visual_effects: List[str] = Field(default_factory=list)
    gameplay_phases: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 04 – Template Matcher
# ---------------------------------------------------------------------------

class TemplateMatchResult(BaseModel):
    template_id: Optional[str] = None
    confidence: float = 0.0
    path: str = "llm"


# ---------------------------------------------------------------------------
# Stage 05 – Code Generator
# ---------------------------------------------------------------------------

class GenerateCodeResult(BaseModel):
    html_code: str
    strategy: str
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
    needs_regeneration: bool = False


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


class AsyncTaskType(str, Enum):
    pipeline_run = "pipeline_run"
    pipeline_iterate = "pipeline_iterate"


class AsyncTaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class AsyncTaskProgress(BaseModel):
    stage: str
    pct: int
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    updated_at: float


class AsyncTaskError(BaseModel):
    message: str
    failed_stage: Optional[str] = None
    retry_count: int = 0
    fallback: Optional[str] = None
    failure_family: Optional[str] = None
    primary_artifact_id: Optional[str] = None


class AsyncTaskHandleResponse(BaseModel):
    task_id: str
    task_type: AsyncTaskType
    status: AsyncTaskStatus
    game_id: str
    user_id: str
    timeout_s: int
    ws_channel: str
    poll_url: str
    cancel_url: str


class AsyncTaskResponse(BaseModel):
    task_id: str
    task_type: AsyncTaskType
    status: AsyncTaskStatus
    game_id: str
    user_id: str
    timeout_s: int
    created_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    progress: Optional[AsyncTaskProgress] = None
    error: Optional[AsyncTaskError] = None
    result: Optional[Dict[str, Any]] = None
    ws_channel: str


class ListAsyncTasksResponse(BaseModel):
    items: List[AsyncTaskResponse] = Field(default_factory=list)
    total: int = 0


class RunPipelineRequest(BaseModel):
    game_id: str
    description: str
    user_id: str
    platform: str = "wechat_webview"
    timeout_s: int = Field(default=600, ge=30, le=3600)
    task_id: Optional[str] = None


class RunPipelineResponse(BaseModel):
    game_id: str
    html_code: str
    game_spec: GameSpec
    strategy: str
    qa_passed: bool
    qa_retries: int
    generation_time_ms: int
    code_size_bytes: int
    quality_score: float = 0.0
    quality_breakdown: Optional[Dict[str, Any]] = None
    pipeline_version: Optional[str] = None
    prompt_bundle_id: Optional[str] = None
    prompt_bundle_version: Optional[int] = None
    runtime_profile: Optional[str] = None
    contract_version: Optional[str] = None
    primary_artifact_id: Optional[str] = None
    qa_warnings: List[Dict[str, Any]] = Field(default_factory=list)
    runtime_qa_report: Optional[Dict[str, Any]] = None


class FontClamp(BaseModel):
    hud_min: int = 14
    hud_max: int = 20
    title_min: int = 28
    title_max: int = 36


class CanvasContract(BaseModel):
    requires_canvas_2d: bool = True
    must_render_within_ms: int = 1500
    orientation: str = "portrait_first"
    ui_scale_mode: str = "short_edge"
    target_fps: int = 60


class InputContract(BaseModel):
    required_modes: List[str] = Field(default_factory=lambda: ["pointer", "touch"])
    allow_mouse_fallback: bool = True
    target: str = "canvas_or_document"
    gestures: List[str] = Field(default_factory=list)


class StateContract(BaseModel):
    required_states: List[str] = Field(default_factory=lambda: ["boot", "ready", "playing", "game_over"])
    restartable: bool = True
    required_flags: List[str] = Field(default_factory=list)


class MobileLayoutContract(BaseModel):
    orientation: str = "portrait_first"
    ui_scale_mode: str = "short_edge"
    safe_area_aware: bool = True
    font_clamp: FontClamp = Field(default_factory=FontClamp)


class SafetyContract(BaseModel):
    forbidden_apis: List[str] = Field(default_factory=lambda: [
        "localStorage",
        "sessionStorage",
        "fetch",
        "XMLHttpRequest",
        "WebSocket",
        "eval",
        "Function",
    ])


class GameplayContract(BaseModel):
    requires_player_entity: bool = True
    requires_scoring: bool = True
    requires_terminal_state: bool = True
    requires_restart_entry: bool = True
    terminal_state_aliases: List[str] = Field(default_factory=lambda: [
        "game_over",
        "gameover",
        "over",
        "ended",
        "lost",
        "failed",
        "dead",
        "win",
        "won",
        "victory",
        "complete",
        "completed",
        "level_complete",
        "clear",
        "cleared",
        "success",
        "succeeded",
        "solved",
    ])
    primary_goal: str = "clear_feedback_loop"


class GameRuntimeContract(BaseModel):
    version: str = "1.0"
    runtime_profile: str = "portrait_arcade"
    canvas: CanvasContract = Field(default_factory=CanvasContract)
    input: InputContract = Field(default_factory=InputContract)
    state: StateContract = Field(default_factory=StateContract)
    mobile_layout: MobileLayoutContract = Field(default_factory=MobileLayoutContract)
    safety: SafetyContract = Field(default_factory=SafetyContract)
    gameplay: GameplayContract = Field(default_factory=GameplayContract)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PromptBundleSnapshot(BaseModel):
    bundle_id: str = ""
    bundle_version: int = 0
    resolved_at: Optional[str] = None
    layers: Dict[str, Any] = Field(default_factory=dict)


class EntitlementSnapshot(BaseModel):
    can_play: bool = True
    require_subscription: bool = False
    grant_source: str = "none"
    grant_subscription_id: Optional[str] = None
    quota_remaining: Optional[int] = None
    refund_on_failure: bool = False


class VisibilityModel(BaseModel):
    public_preview_allowed: bool = False
    author_play_allowed: bool = True
    public_index_allowed: bool = False
    published_visibility: str = "private"


class RequestContextSnapshot(BaseModel):
    source: str = "game-service"
    entrypoint: str = "create"
    region: str = "cn_shanghai"
    pipeline_version: str = "v2"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExistingGameContext(BaseModel):
    status: str = "draft"
    visibility: str = "private"
    live_bundle_version: Optional[int] = None
    working_bundle_version: Optional[int] = None
    can_play: bool = True
    require_subscription: bool = False
    forked_from: Optional[str] = None


class IterationIntent(BaseModel):
    feedback: str
    conversation: List[Dict[str, str]] = Field(default_factory=list)


class SourceBundleRevision(BaseModel):
    version: Optional[int] = None
    generated_at: Optional[str] = None
    feedback: Optional[str] = None
    iteration_type: Optional[str] = None
    summary: Optional[str] = None


class SourceBundleContext(BaseModel):
    title: Optional[str] = None
    latest_bundle_version: Optional[int] = None
    latest_game_type: Optional[str] = None
    latest_feedback: Optional[str] = None
    latest_iteration_type: Optional[str] = None
    summary: Optional[str] = None
    recent_revisions: List[SourceBundleRevision] = Field(default_factory=list)


class RunPipelineV2Request(BaseModel):
    game_id: str
    user_id: str
    raw_user_input: str
    title: Optional[str] = None
    platform: str = "wechat_webview"
    timeout_s: int = Field(default=600, ge=30, le=3600)
    task_id: Optional[str] = None
    request_context: RequestContextSnapshot = Field(default_factory=RequestContextSnapshot)
    entitlement: EntitlementSnapshot = Field(default_factory=EntitlementSnapshot)
    visibility_model: VisibilityModel = Field(default_factory=VisibilityModel)
    prompt_bundle_snapshot: PromptBundleSnapshot = Field(default_factory=PromptBundleSnapshot)
    runtime_contract: GameRuntimeContract = Field(default_factory=GameRuntimeContract)
    normalized_request: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IterateV2Request(BaseModel):
    game_id: str
    user_id: str
    current_code: str
    iteration_intent: IterationIntent
    existing_game: ExistingGameContext = Field(default_factory=ExistingGameContext)
    source_spec: Optional[GameSpec] = None
    source_bundle_context: SourceBundleContext = Field(default_factory=SourceBundleContext)
    platform: str = "wechat_webview"
    timeout_s: int = Field(default=600, ge=30, le=3600)
    task_id: Optional[str] = None
    request_context: RequestContextSnapshot = Field(default_factory=lambda: RequestContextSnapshot(entrypoint="iterate"))
    entitlement: EntitlementSnapshot = Field(default_factory=EntitlementSnapshot)
    visibility_model: VisibilityModel = Field(default_factory=VisibilityModel)
    prompt_bundle_snapshot: PromptBundleSnapshot = Field(default_factory=PromptBundleSnapshot)
    runtime_contract: GameRuntimeContract = Field(default_factory=GameRuntimeContract)
    normalized_request: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
    timeout_s: int = Field(default=600, ge=30, le=3600)


class GenerateCodeResponse(BaseModel):
    html_code: str
    strategy: str
    template_id: Optional[str] = None
    generation_time_ms: int
    code_size_bytes: int


class IterateRequest(BaseModel):
    game_id: str
    feedback: str
    user_id: str = "system"
    conversation: List[Dict[str, str]] = Field(default_factory=list)
    current_code: str
    timeout_s: int = Field(default=600, ge=30, le=3600)
    task_id: Optional[str] = None


class IterateResponse(BaseModel):
    html_code: str
    changes: List[str]
    iteration_type: str = "element_change"
    game_spec: Optional[GameSpec] = None
    generation_time_ms: int
    qa_retries: int = 0
    iteration_retries: int = 0
    pipeline_version: Optional[str] = None
    prompt_bundle_id: Optional[str] = None
    prompt_bundle_version: Optional[int] = None
    runtime_profile: Optional[str] = None
    contract_version: Optional[str] = None
    primary_artifact_id: Optional[str] = None
    qa_warnings: List[Dict[str, Any]] = Field(default_factory=list)
    runtime_qa_report: Optional[Dict[str, Any]] = None


class QACheckRequest(BaseModel):
    html_code: str


class ProviderCatalogPreviewRequest(BaseModel):
    provider_type: str = "openai_compatible"
    vendor_preset: str = "generic"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    catalog_api_url: Optional[str] = None
    catalog_auth_mode: str = "inherit_provider"
    catalog_api_key: Optional[str] = None


class ProviderCatalogEntry(BaseModel):
    id: str
    label: Optional[str] = None
    owned_by: Optional[str] = None
    created: Optional[int] = None


class ProviderCatalogPreviewResponse(BaseModel):
    models: List[ProviderCatalogEntry] = Field(default_factory=list)
    fetched_at: str
    resolved_catalog_api_url: str
    vendor_preset: str = "generic"


class ProviderTestChatRequest(BaseModel):
    messages: List[ConversationMessage] = Field(default_factory=list)
    use_fast_model: bool = False
    model: Optional[str] = None
    max_tokens: int = Field(default=256, ge=1, le=4096)
    system: Optional[str] = None


class ProviderTestChatResponse(BaseModel):
    provider_id: str
    provider_name: str
    provider_type: str
    region: str
    resolved_endpoint: str
    model: str
    latency_ms: int
    http_status: Optional[int] = None
    success: bool
    error_message: Optional[str] = None
    reply: str = ""
    tested_at: str


class GenerateProgress(BaseModel):
    stage: str
    pct: int = Field(..., ge=0, le=100)
    message: str
    details: Optional[Dict[str, Any]] = None
