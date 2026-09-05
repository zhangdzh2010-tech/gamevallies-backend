/** Shared value contracts for game orchestration and policy modules. */
import {
  GameAccessGrantSource,
} from '@prisma/client';
import {
  CreateGameDto,
  CreateGameGenerationTier,
  CreateGameOrientation,
} from './dto';

export type RuntimeOrientation = 'portrait_first' | 'landscape_first';
export type GenerationTier = CreateGameGenerationTier;

export interface RetryContext {
  retry: number;
  maxRetries: number;
  attempt: number;
  maxAttempts: number;
  error: unknown;
}

export interface RetryOptions {
  maxAttempts?: number;
  delayMs?: number;
  retryOnHttpResponse?: boolean;
  retryOnNetworkError?: boolean;
  onRetry?: (context: RetryContext) => void | Promise<void>;
}

export interface FailureContext {
  message: string;
  failedStage?: string;
  retryCount: number;
  fallback?: string;
  failureFamily?: string;
  primaryArtifactId?: string;
}

export interface CreateQualityGate {
  generationTier: GenerationTier;
  minQualityScore: number;
  requireStructuredReview: boolean;
  minReviewBonus?: number;
}

export interface AccessGrantDecision {
  canPlay: boolean;
  requireSubscription: boolean;
  quotaRemaining: number;
  accessGrantSource: GameAccessGrantSource;
  accessGrantSubscriptionId: string | null;
}

export interface GenerationTaskSummary {
  taskId: string;
  taskType: 'pipeline_run' | 'pipeline_iterate';
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'timed_out';
  timeoutS: number;
  wsChannel: string;
  pollUrl: string;
  artifactsUrl?: string;
  eventsUrl?: string;
  cancelUrl?: string;
}

export interface PreviewLinkOptions {
  previewToken?: string;
}

export interface CoverLinkOptions extends PreviewLinkOptions {
  taskId?: string;
  version?: number | string;
}

export type PipelineVersion = 'v2';
export type PipelineEntrypoint = 'create' | 'iterate';

export interface PromptBundleSnapshotPayload {
  bundle_id: string;
  bundle_version: number;
  resolved_at: string;
  layers: Record<string, unknown>;
}

export interface RuntimeContractPayload {
  version: string;
  runtime_profile: string;
  metadata: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SourceBundleRevisionPayload {
  version?: number | null;
  generated_at?: string | null;
  feedback?: string | null;
  iteration_type?: string | null;
  summary?: string | null;
}

export interface SourceBundleContextPayload {
  title?: string | null;
  latest_bundle_version?: number | null;
  latest_game_type?: string | null;
  latest_generation_tier?: GenerationTier | null;
  latest_orientation?: CreateGameOrientation | null;
  latest_feedback?: string | null;
  latest_iteration_type?: string | null;
  summary?: string | null;
  recent_revisions?: SourceBundleRevisionPayload[];
}

export interface DirectIntentParseResponsePayload {
  spec?: Record<string, unknown> | null;
  confidence?: number;
  missing_required?: string[];
  slot_fill_pct?: number;
}

export interface CreateGameCommand extends CreateGameDto {
  sourceSpec?: Record<string, unknown> | null;
  creationSessionId?: string | null;
  entryMode?: string | null;
  sourceGameId?: string | null;
  // H.5.1 - Clean, user-facing idea (the user's original 1-line typed text or
  // creation-session initialPrompt). Stored separately from `description`,
  // which historically holds the LLM-expanded prompt and must not leak to UI.
  userIdea?: string | null;
}

export interface CreateExecutionOptions {
  title?: string;
  orientation?: CreateGameOrientation;
  generationTier?: GenerationTier;
  access?: AccessGrantDecision;
  sourceSpec?: Record<string, unknown> | null;
  creationSessionId?: string | null;
  entryMode?: string | null;
  sourceGameId?: string | null;
  promptBundleSnapshot?: PromptBundleSnapshotPayload | null;
  runtimeContract?: RuntimeContractPayload | null;
}

export interface IterateExecutionOptions {
  promptBundleSnapshot?: PromptBundleSnapshotPayload | null;
  runtimeContract?: RuntimeContractPayload | null;
  orientation?: CreateGameOrientation;
  generationTier?: GenerationTier;
  sourceSpec?: Record<string, unknown> | null;
  sourceBundleContext?: SourceBundleContextPayload | null;
  game?: {
    gameType?: string | null;
    status?: string | null;
    visibility?: string | null;
    version?: number | null;
    canPlay?: boolean | null;
    requireSubscription?: boolean | null;
    accessGrantSource?: GameAccessGrantSource | string | null;
    accessGrantSubscriptionId?: string | null;
    forkedFrom?: string | null;
  };
}

export interface UpstreamAsyncTaskHandle {
  task_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled';
  poll_url?: string;
  cancel_url?: string;
  /** True when the AI engine matched our idempotency key and reused an existing task. */
  deduplicated?: boolean;
}

export interface ResolvedUpstreamAsyncTaskHandle extends UpstreamAsyncTaskHandle {
  aiEngineBaseUrl: string;
}

export interface UpstreamAsyncTaskFailure {
  message?: string;
  failed_stage?: string;
  retry_count?: number;
  fallback?: string;
  failure_family?: string;
  primary_artifact_id?: string;
}

export interface UpstreamAsyncTaskSnapshot {
  task_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled';
  result?: Record<string, any> | null;
  error?: UpstreamAsyncTaskFailure | null;
}

export type EffectiveTaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'timed_out';

export type EffectiveTaskResolution =
  | {
      status: 'succeeded';
      stage: string;
      message: string;
      retryCount?: number;
    }
  | {
      status: 'failed' | 'canceled' | 'timed_out';
      stage: string;
      message: string;
      retryCount?: number;
    };
