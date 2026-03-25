import {
  Injectable,
  Logger,
  BadRequestException,
  NotFoundException,
  ForbiddenException,
  ConflictException,
  ServiceUnavailableException,
  OnModuleDestroy,
  OnModuleInit,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { JwtService } from '@nestjs/jwt';
import { randomUUID } from 'crypto';
import axios from 'axios';
import {
  GameAccessGrantSource,
  GenerationTaskEventType,
  GenerationTaskType,
  GameStatus,
  Prisma,
  UserSubscriptionStatus,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { BundleService } from '../bundle/bundle.service';
import { StatsService } from '../stats/stats.service';
import { GameWebSocketGateway } from '../websocket/websocket.gateway';
import { CreateGameDto, PublishGameDto, IterateGameDto } from './dto';
import { GenerationTaskService } from './generation-task.service';
import {
  TIMEOUT_CONFIG_CATALOG,
  TIMEOUT_CONFIG_CATALOG_BY_KEY,
} from './catalogs/timeout-catalog';

// Pipeline stage labels for WebSocket progress events
const STAGE_LABELS: Record<string, string> = {
  intent_parsing: '解析游戏意图',
  designing: '设计游戏参数',
  template_matching: '确认生成路径',
  code_generating: '生成游戏代码',
  qa_checking: '质量检测',
  publishing: '发布生成结果',
  completed: '生成完成',
  failed: '生成失败',
};

const STAGE_PCT: Record<string, number> = {
  intent_parsing: 15,
  designing: 30,
  template_matching: 40,
  code_generating: 60,
  qa_checking: 80,
  publishing: 90,
  completed: 100,
  failed: -1,
};

interface RetryContext {
  retry: number;
  maxRetries: number;
  attempt: number;
  maxAttempts: number;
  error: unknown;
}

interface RetryOptions {
  maxAttempts?: number;
  delayMs?: number;
  retryOnHttpResponse?: boolean;
  retryOnNetworkError?: boolean;
  onRetry?: (context: RetryContext) => void | Promise<void>;
}

interface FailureContext {
  message: string;
  failedStage?: string;
  retryCount: number;
  fallback?: string;
  failureFamily?: string;
  primaryArtifactId?: string;
}

interface AccessGrantDecision {
  canPlay: boolean;
  requireSubscription: boolean;
  quotaRemaining: number;
  accessGrantSource: GameAccessGrantSource;
  accessGrantSubscriptionId: string | null;
}

interface GenerationTaskSummary {
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

interface PreviewLinkOptions {
  previewToken?: string;
}

type PipelineVersion = 'v1' | 'v2';
type PipelineEntrypoint = 'create' | 'iterate';

interface PromptBundleSnapshotPayload {
  bundle_id: string;
  bundle_version: number;
  resolved_at: string;
  layers: Record<string, unknown>;
}

interface RuntimeContractPayload {
  version: string;
  runtime_profile: string;
  metadata: Record<string, unknown>;
  [key: string]: unknown;
}

interface CreateExecutionOptions {
  pipelineVersion?: PipelineVersion;
  title?: string;
  access?: AccessGrantDecision;
  promptBundleSnapshot?: PromptBundleSnapshotPayload | null;
  runtimeContract?: RuntimeContractPayload | null;
}

interface IterateExecutionOptions {
  pipelineVersion?: PipelineVersion;
  promptBundleSnapshot?: PromptBundleSnapshotPayload | null;
  runtimeContract?: RuntimeContractPayload | null;
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

interface UpstreamAsyncTaskHandle {
  task_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled';
  poll_url?: string;
  cancel_url?: string;
}

interface UpstreamAsyncTaskFailure {
  message?: string;
  failed_stage?: string;
  retry_count?: number;
  fallback?: string;
  failure_family?: string;
  primary_artifact_id?: string;
}

interface UpstreamAsyncTaskSnapshot {
  task_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled';
  result?: Record<string, any> | null;
  error?: UpstreamAsyncTaskFailure | null;
}

type EffectiveTaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'timed_out';

type EffectiveTaskResolution =
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

class TaskAbortedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TaskAbortedError';
  }
}

class TaskSupersededError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TaskSupersededError';
  }
}

/** Retry an async operation on transient network/5xx errors. */
async function withRetry<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {},
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? 2;
  const delayMs = options.delayMs ?? 3000;
  const retryOnHttpResponse = options.retryOnHttpResponse ?? true;
  const retryOnNetworkError = options.retryOnNetworkError ?? true;
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err: any) {
      lastError = err;
      // Do NOT retry on timeout (ECONNABORTED) — AI engine already started processing,
      // a retry would launch a duplicate pipeline job
      const isHttpRetryable =
        retryOnHttpResponse &&
        Boolean(err?.response) &&
        (err.response.status >= 500 || err.response.status === 429);
      const isNetworkRetryable =
        retryOnNetworkError &&
        !err?.response &&
        Boolean(err?.code);
      const isRetryable =
        err?.code !== 'ECONNABORTED' &&
        (isHttpRetryable || isNetworkRetryable);
      if (!isRetryable || attempt === maxAttempts) break;
      if (options.onRetry) {
        await options.onRetry({
          retry: attempt,
          maxRetries: maxAttempts - 1,
          attempt: attempt + 1,
          maxAttempts,
          error: err,
        });
      }
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  throw lastError;
}

@Injectable()
export class GameService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(GameService.name);
  private aiEngineUrl: string;
  private aiEngineTargetCache = new Map<string, { url: string; cachedAt: number }>();
  private activeTaskSweepTimer: NodeJS.Timeout | null = null;
  private activeTaskSweepInFlight = false;
  private timeoutConfigCache = new Map<string, string>();
  private timeoutConfigLoadedAt = 0;
  private timeoutConfigRefreshPromise: Promise<void> | null = null;
  private currentSweepIntervalMs = 0;

  constructor(
    private prisma: PrismaService,
    private bundleService: BundleService,
    private statsService: StatsService,
    private configService: ConfigService,
    private jwtService: JwtService,
    private wsGateway: GameWebSocketGateway,
    private generationTaskService: GenerationTaskService,
  ) {
    this.aiEngineUrl = this.configService.get<string>(
      'AI_ENGINE_URL',
      'http://localhost:8000',
    );
  }

  onModuleInit(): void {
    void this.refreshTimeoutConfigCache().catch((error) => {
      this.logger.warn(`Failed to warm timeout config cache on init: ${error?.message || error}`);
    });
    this.startActiveTaskSweep();
  }

  onModuleDestroy(): void {
    this.stopActiveTaskSweep();
  }

  private startActiveTaskSweep(): void {
    if (this.activeTaskSweepTimer) {
      return;
    }
    const intervalMs = this.getActiveTaskSweepIntervalMs();
    this.currentSweepIntervalMs = intervalMs;
    this.activeTaskSweepTimer = setInterval(() => {
      void this.reconcileActiveTasksInBackground();
    }, intervalMs);
    this.activeTaskSweepTimer.unref?.();
  }

  private stopActiveTaskSweep(): void {
    if (!this.activeTaskSweepTimer) {
      return;
    }
    clearInterval(this.activeTaskSweepTimer);
    this.activeTaskSweepTimer = null;
  }

  private restartActiveTaskSweepIfNeeded(): void {
    const nextIntervalMs = this.getActiveTaskSweepIntervalMs();
    if (this.activeTaskSweepTimer && this.currentSweepIntervalMs === nextIntervalMs) {
      return;
    }
    this.stopActiveTaskSweep();
    this.startActiveTaskSweep();
  }

  public async refreshTimeoutConfigCache(): Promise<void> {
    if (!this.timeoutConfigRefreshPromise) {
      this.timeoutConfigRefreshPromise = (async () => {
        const rows = await this.prisma.systemConfig.findMany({
          where: { category: 'timeout' },
          select: {
            configKey: true,
            configValue: true,
          },
        });

        const nextCache = new Map<string, string>();
        for (const row of rows) {
          nextCache.set(row.configKey, row.configValue);
        }

        this.timeoutConfigCache = nextCache;
        this.timeoutConfigLoadedAt = Date.now();
        this.restartActiveTaskSweepIfNeeded();
      })().finally(() => {
        this.timeoutConfigRefreshPromise = null;
      });
    }

    await this.timeoutConfigRefreshPromise;
  }

  private async ensureTimeoutConfigCache(): Promise<void> {
    if (this.timeoutConfigLoadedAt > 0) {
      return;
    }
    await this.refreshTimeoutConfigCache();
  }

  private resolveTimeoutCatalogValue(
    key: string,
    options?: {
      min?: number;
      max?: number;
    },
  ): number {
    const catalogEntry = TIMEOUT_CONFIG_CATALOG_BY_KEY.get(key);
    if (!catalogEntry) {
      throw new Error(`Unknown timeout config key: ${key}`);
    }

    const fallback = catalogEntry.defaultValue;
    const raw = this.timeoutConfigCache.has(key)
      ? this.timeoutConfigCache.get(key)
      : fallback;
    const parseValue = (value: string): number => (
      catalogEntry.valueType === 'float'
        ? Number.parseFloat(value)
        : Number.parseInt(value, 10)
    );
    const parsed = parseValue(String(raw));
    if (!Number.isFinite(parsed)) {
      return parseValue(String(fallback));
    }

    const min = options?.min ?? Number.NEGATIVE_INFINITY;
    const max = options?.max ?? Number.POSITIVE_INFINITY;
    return Math.min(max, Math.max(min, parsed));
  }

  private getAiEngineTargetCacheTtlMs(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.ai_target_cache_ttl_ms', { min: 0 });
  }

  private getActiveTaskSweepIntervalMs(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.active_task_sweep_interval_ms', { min: 1_000 });
  }

  public async getExpandPromptRequestTimeoutMs(): Promise<number> {
    await this.ensureTimeoutConfigCache();
    return this.resolveTimeoutCatalogValue('timeout.game_service.expand_prompt_request_ms', { min: 1_000 });
  }

  private getUpstreamRequestTimeoutMs(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.upstream_request_ms', { min: 1_000 });
  }

  private getUpstreamRequestRetryDelayMs(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.upstream_request_retry_delay_ms', { min: 0 });
  }

  private getUpstreamSnapshotTimeoutMs(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.upstream_snapshot_ms', { min: 1_000 });
  }

  private getUpstreamCancelTimeoutMs(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.upstream_cancel_ms', { min: 1_000 });
  }

  private getUpstreamPollIntervalMs(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.upstream_poll_interval_ms', { min: 100 });
  }

  private getUpstreamTimeoutBufferS(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.upstream_timeout_buffer_s', { min: 0 });
  }

  private getUpstreamDeadlineGraceMs(): number {
    return this.resolveTimeoutCatalogValue('timeout.game_service.upstream_deadline_grace_ms', { min: 0 });
  }

  private async reconcileActiveTasksInBackground(): Promise<void> {
    if (this.activeTaskSweepInFlight) {
      return;
    }

    this.activeTaskSweepInFlight = true;
    try {
      const activeTasks = await this.prisma.generationTask.findMany({
        where: {
          status: {
            in: ['queued', 'running'],
          },
        },
        orderBy: { updatedAt: 'asc' },
        take: 50,
      });

      for (const task of activeTasks) {
        await this.reconcileGenerationTask(task).catch((error) => {
          this.logger.warn(`Failed to sweep task ${task.id}: ${error.message}`);
        });
      }
    } catch (error: any) {
      this.logger.warn(`Failed to sweep active generation tasks: ${error?.message || error}`);
    } finally {
      this.activeTaskSweepInFlight = false;
    }
  }

  private resolveExecutionRegion(rawValue?: unknown): string {
    const normalized = String(
      rawValue
      ?? this.configService.get<string>('AI_ENGINE_DEFAULT_REGION')
      ?? this.configService.get<string>('SERVICE_REGION')
      ?? 'cn_shanghai',
    ).trim();

    return normalized === 'ap_southeast_johor' ? normalized : 'cn_shanghai';
  }

  private parseConfigList(key: string): Set<string> {
    const raw = String(this.configService.get<string>(key, '') || '').trim();
    if (!raw) {
      return new Set();
    }

    return new Set(
      raw
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean),
    );
  }

  private matchesPipelineRoute(
    params: {
      entrypoint: PipelineEntrypoint;
      userId: string;
      executionRegion?: string;
    },
    version: PipelineVersion,
  ): boolean {
    const scopePrefix = version === 'v1' ? 'PIPELINE_V1' : 'PIPELINE_V2';
    const allowedEntrypoints = this.parseConfigList(`${scopePrefix}_ENTRYPOINTS`);
    const allowedUsers = this.parseConfigList(`${scopePrefix}_USER_IDS`);
    const allowedRegions = this.parseConfigList(`${scopePrefix}_REGIONS`);

    if (allowedEntrypoints.size === 0 && allowedUsers.size === 0 && allowedRegions.size === 0) {
      return false;
    }

    if (allowedEntrypoints.size > 0 && !allowedEntrypoints.has(params.entrypoint)) {
      return false;
    }

    if (allowedUsers.size > 0 && !allowedUsers.has(params.userId)) {
      return false;
    }

    const executionRegion = this.resolveExecutionRegion(params.executionRegion);
    if (allowedRegions.size > 0 && !allowedRegions.has(executionRegion)) {
      return false;
    }

    return true;
  }

  private resolvePipelineVersion(params: {
    entrypoint: PipelineEntrypoint;
    userId: string;
    executionRegion?: string;
  }): PipelineVersion {
    const configured = String(this.configService.get<string>('PIPELINE_VERSION', 'v2') || 'v2')
      .trim()
      .toLowerCase();

    if (configured === 'v1') {
      return this.matchesPipelineRoute(params, 'v2') ? 'v2' : 'v1';
    }

    return this.matchesPipelineRoute(params, 'v1') ? 'v1' : 'v2';
  }

  private async resolveActivePromptBundleIdentity(): Promise<{ id: string; version: number }> {
    const bundle = await this.prisma.promptBundle.findFirst({
      where: { status: 'active' },
      orderBy: [{ updatedAt: 'desc' }, { version: 'desc' }],
      select: {
        id: true,
        version: true,
      },
    });
    if (!bundle) {
      throw new ServiceUnavailableException('No active prompt bundle is configured');
    }
    return bundle;
  }

  private inferRuntimeProfileHint(...inputs: Array<string | null | undefined>): string | undefined {
    const text = inputs
      .map((value) => String(value || '').trim().toLowerCase())
      .filter(Boolean)
      .join(' ');

    if (!text) {
      return undefined;
    }

    if (/(runner|race|racing|lane|endless runner|跑酷|赛道|lane runner)/.test(text)) {
      return 'lane_runner';
    }
    if (/(puzzle|grid|tile|match|merge|拼图|消除|方块)/.test(text)) {
      return 'grid_puzzle';
    }
    if (/(shooter|shoot|top-down|top down|action|射击|飞船|弹幕|俯视|动作)/.test(text)) {
      return 'topdown_action';
    }
    if (/(rhythm|timing|beat|music|节奏|音游|点按)/.test(text)) {
      return 'tap_timing';
    }
    return undefined;
  }

  private async resolveRuntimeProfile(profileHint?: string): Promise<{
    id: string;
    contractSchema?: Prisma.JsonValue | null;
    metadata?: Prisma.JsonValue | null;
  }> {
    const profiles = await this.prisma.runtimeProfileCatalog.findMany({
      where: { enabled: true },
      select: {
        id: true,
        contractSchema: true,
        metadata: true,
      },
      orderBy: [{ updatedAt: 'desc' }, { id: 'asc' }],
    });
    if (profiles.length === 0) {
      throw new ServiceUnavailableException('No enabled runtime profile is configured');
    }

    const defaultProfile = profiles.find((profile) => {
      const metadata = profile.metadata;
      return typeof metadata === 'object' && metadata !== null && (metadata as Record<string, unknown>).default === true;
    });

    const normalizedHint = String(profileHint || '').trim();
    if (normalizedHint) {
      const hintedProfile = profiles.find((profile) => profile.id === normalizedHint);
      if (hintedProfile) {
        return hintedProfile;
      }
    }

    return defaultProfile || profiles[0];
  }

  private normalizeRuntimeContractSchema(
    profileId: string,
    rawSchema: Prisma.JsonValue | null | undefined,
  ): RuntimeContractPayload {
    const schema = typeof rawSchema === 'object' && rawSchema !== null
      ? rawSchema as Record<string, unknown>
      : {};
    const renderContract = typeof schema.renderContract === 'object' && schema.renderContract !== null
      ? schema.renderContract as Record<string, unknown>
      : {};

    return {
      version: '1.0',
      runtime_profile: profileId,
      metadata: {},
      canvas: {
        requires_canvas_2d: renderContract.requiresCanvas2D ?? true,
        must_render_within_ms: renderContract.mustRenderWithinMs ?? 1500,
        orientation: (schema.mobileLayoutContract as Record<string, unknown> | undefined)?.orientation ?? 'portrait_first',
        ui_scale_mode: (schema.mobileLayoutContract as Record<string, unknown> | undefined)?.uiScaleMode ?? 'short_edge',
        target_fps: renderContract.targetFps ?? 60,
      },
      input: this.toSnakeCaseRecord(schema.inputContract),
      state: this.toSnakeCaseRecord(schema.stateContract),
      mobile_layout: this.toSnakeCaseRecord(schema.mobileLayoutContract),
      safety: this.toSnakeCaseRecord(schema.safetyContract),
      gameplay: this.toSnakeCaseRecord(schema.gameplayContract),
    };
  }

  private toSnakeCaseRecord(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return {};
    }

    return Object.entries(value as Record<string, unknown>).reduce<Record<string, unknown>>((acc, [key, entry]) => {
      const snakeKey = key
        .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
        .replace(/-/g, '_')
        .toLowerCase();
      if (Array.isArray(entry)) {
        acc[snakeKey] = entry;
      } else if (entry && typeof entry === 'object') {
        acc[snakeKey] = this.toSnakeCaseRecord(entry);
      } else {
        acc[snakeKey] = entry;
      }
      return acc;
    }, {});
  }

  private async buildPromptBundleSnapshot(
    entrypoint: PipelineEntrypoint,
    runtimeProfile?: string,
  ): Promise<PromptBundleSnapshotPayload> {
    const bundle = await this.resolveActivePromptBundleIdentity();
    return {
      bundle_id: bundle.id,
      bundle_version: bundle.version,
      resolved_at: new Date().toISOString(),
      layers: {
        entrypoint,
        source: 'game-service',
        ...(runtimeProfile ? { profile_few_shot: runtimeProfile } : {}),
      },
    };
  }

  private async buildDefaultRuntimeContract(
    entrypoint: PipelineEntrypoint,
    runtimeProfile?: string,
  ): Promise<RuntimeContractPayload> {
    const profile = await this.resolveRuntimeProfile(runtimeProfile);
    const normalizedContract = this.normalizeRuntimeContractSchema(profile.id, profile.contractSchema);

    return {
      ...normalizedContract,
      metadata: {
        ...((normalizedContract.metadata as Record<string, unknown> | undefined) ?? {}),
        entrypoint,
        source: 'game-service',
      },
    };
  }

  private buildEntitlementSnapshot(params: {
    canPlay: boolean;
    requireSubscription: boolean;
    accessGrantSource: GameAccessGrantSource | string;
    accessGrantSubscriptionId?: string | null;
    quotaRemaining?: number | null;
    refundOnFailure: boolean;
  }) {
    return {
      can_play: params.canPlay,
      require_subscription: params.requireSubscription,
      grant_source: params.accessGrantSource || GameAccessGrantSource.none,
      grant_subscription_id: params.accessGrantSubscriptionId ?? null,
      quota_remaining: params.quotaRemaining ?? null,
      refund_on_failure: params.refundOnFailure,
    };
  }

  private buildVisibilityModel(params: {
    status?: string | null;
    visibility?: string | null;
    canPlay?: boolean | null;
  }) {
    const publishedVisibility = params.visibility || 'private';
    const isPublishedPublic = params.status === GameStatus.published && publishedVisibility === 'public';
    const isPublishedPreviewVisible =
      params.status === GameStatus.published
      && (publishedVisibility === 'public' || publishedVisibility === 'unlisted');

    return {
      public_preview_allowed: isPublishedPreviewVisible,
      // Authors must always be able to review drafts/candidates even when
      // the public play entitlement is still locked.
      author_play_allowed: true,
      public_index_allowed: isPublishedPublic,
      published_visibility: publishedVisibility,
    };
  }

  private buildCreateV2Payload(params: {
    gameId: string;
    userId: string;
    title?: string;
    description: string;
    executionRegion: string;
    timeoutS: number;
    taskId?: string;
    access?: AccessGrantDecision;
    promptBundleSnapshot: PromptBundleSnapshotPayload;
    runtimeContract: RuntimeContractPayload;
  }): Record<string, unknown> {
    return {
      game_id: params.gameId,
      user_id: params.userId,
      raw_user_input: params.description,
      title: params.title || null,
      platform: 'wechat_webview',
      timeout_s: params.timeoutS,
      task_id: params.taskId,
      request_context: {
        source: 'game-service',
        entrypoint: 'create',
        region: params.executionRegion,
        pipeline_version: 'v2',
        metadata: {
          game_id: params.gameId,
        },
      },
      entitlement: this.buildEntitlementSnapshot({
        canPlay: params.access?.canPlay ?? true,
        requireSubscription: params.access?.requireSubscription ?? false,
        accessGrantSource: params.access?.accessGrantSource ?? GameAccessGrantSource.none,
        accessGrantSubscriptionId: params.access?.accessGrantSubscriptionId ?? null,
        quotaRemaining: params.access?.quotaRemaining ?? null,
        refundOnFailure: true,
      }),
      visibility_model: this.buildVisibilityModel({
        status: GameStatus.draft,
        visibility: 'private',
        canPlay: params.access?.canPlay ?? true,
      }),
      prompt_bundle_snapshot: params.promptBundleSnapshot,
      runtime_contract: params.runtimeContract,
      normalized_request: {
        description: params.description,
        title: params.title || null,
        region: params.executionRegion,
        entrypoint: 'create',
      },
      metadata: {
        adapter: 'compat_v1',
        pipeline_version: 'v2',
      },
    };
  }

  private buildIterateV2Payload(params: {
    gameId: string;
    userId: string;
    feedback: string;
    conversationHistory: Array<{ role: string; content: string }>;
    currentCode: string;
    executionRegion: string;
    timeoutS: number;
    taskId?: string;
    game?: IterateExecutionOptions['game'];
    promptBundleSnapshot: PromptBundleSnapshotPayload;
    runtimeContract: RuntimeContractPayload;
  }): Record<string, unknown> {
    const game = params.game || {};

    return {
      game_id: params.gameId,
      user_id: params.userId,
      current_code: params.currentCode,
      platform: 'wechat_webview',
      timeout_s: params.timeoutS,
      task_id: params.taskId,
      request_context: {
        source: 'game-service',
        entrypoint: 'iterate',
        region: params.executionRegion,
        pipeline_version: 'v2',
        metadata: {
          game_id: params.gameId,
          live_bundle_version: game.version ?? null,
        },
      },
      iteration_intent: {
        feedback: params.feedback,
        conversation: params.conversationHistory,
      },
      existing_game: {
        status: game.status || GameStatus.draft,
        visibility: game.visibility || 'private',
        live_bundle_version: game.version ?? null,
        working_bundle_version: null,
        can_play: Boolean(game.canPlay ?? true),
        require_subscription: Boolean(game.requireSubscription ?? false),
        forked_from: game.forkedFrom ?? null,
      },
      entitlement: this.buildEntitlementSnapshot({
        canPlay: Boolean(game.canPlay ?? true),
        requireSubscription: Boolean(game.requireSubscription ?? false),
        accessGrantSource: String(game.accessGrantSource || GameAccessGrantSource.none),
        accessGrantSubscriptionId: game.accessGrantSubscriptionId ?? null,
        quotaRemaining: null,
        refundOnFailure: false,
      }),
      visibility_model: this.buildVisibilityModel({
        status: game.status,
        visibility: game.visibility,
        canPlay: game.canPlay,
      }),
      prompt_bundle_snapshot: params.promptBundleSnapshot,
      runtime_contract: params.runtimeContract,
      normalized_request: {
        feedback: params.feedback,
        region: params.executionRegion,
        entrypoint: 'iterate',
      },
      metadata: {
        adapter: 'compat_v1',
        pipeline_version: 'v2',
      },
    };
  }

  async getAiEngineBaseUrl(executionRegion?: string): Promise<string> {
    return this.resolveAiEngineEndpoint(executionRegion);
  }

  private getConfiguredAiEngineUrlForRegion(executionRegion?: string): string {
    const region = this.resolveExecutionRegion(executionRegion);
    const defaultRegion = this.resolveExecutionRegion(
      this.configService.get<string>('AI_ENGINE_DEFAULT_REGION')
      || this.configService.get<string>('SERVICE_REGION')
      || 'cn_shanghai',
    );

    const regionSpecificUrl = region === 'ap_southeast_johor'
      ? this.configService.get<string>('AI_ENGINE_URL_AP_SOUTHEAST_JOHOR', '')
      : this.configService.get<string>('AI_ENGINE_URL_CN_SHANGHAI', '');
    const normalizedRegionSpecificUrl = (regionSpecificUrl || '').trim().replace(/\/$/, '');
    if (normalizedRegionSpecificUrl) {
      return normalizedRegionSpecificUrl;
    }

    if (defaultRegion === region) {
      return (this.aiEngineUrl || '').trim().replace(/\/$/, '');
    }

    return '';
  }

  private async resolveAiEngineEndpoint(executionRegion?: string): Promise<string> {
    await this.ensureTimeoutConfigCache();
    const region = this.resolveExecutionRegion(executionRegion);
    const cached = this.aiEngineTargetCache.get(region);
    const now = Date.now();

    if (cached && (now - cached.cachedAt) < this.getAiEngineTargetCacheTtlMs()) {
      return cached.url;
    }

    const targetRepo = (this.prisma as any).aiEngineRegionTarget;
    const targetQuery = targetRepo?.findFirst
      ? targetRepo.findFirst({
          where: {
            executionRegion: region,
            deployEnabled: true,
            deployStatus: 'deployed',
            aiEngineUrl: { not: null },
          },
          select: {
            aiEngineUrl: true,
          },
          orderBy: { updatedAt: 'desc' },
        })
      : null;
    const target = targetQuery
      ? await Promise.resolve(targetQuery).catch(() => null)
      : null;

    const targetUrl = (target?.aiEngineUrl || '').trim().replace(/\/$/, '');
    if (targetUrl) {
      this.aiEngineTargetCache.set(region, {
        url: targetUrl,
        cachedAt: now,
      });
      return targetUrl;
    }

    const configuredUrl = this.getConfiguredAiEngineUrlForRegion(region);
    if (configuredUrl) {
      this.aiEngineTargetCache.set(region, {
        url: configuredUrl,
        cachedAt: now,
      });
      return configuredUrl;
    }

    const fallbackUrl = (this.aiEngineUrl || '').trim().replace(/\/$/, '');
    if (fallbackUrl && process.env.NODE_ENV !== 'production') {
      return fallbackUrl;
    }

    throw new Error(`missing_ai_engine_region_target: no deployed ai-engine target for region ${region}`);
  }

  private getPublicBaseUrl(): string {
    const publicApiBaseUrl = this.configService.get<string>('PUBLIC_API_BASE_URL');
    const appUrl = this.configService.get<string>('APP_URL', 'http://localhost:3002');
    return (publicApiBaseUrl || appUrl).replace(/\/$/, '');
  }

  private buildPreviewUrl(gameId: string, options: PreviewLinkOptions = {}): string {
    const previewUrl = new URL(`${this.getPublicBaseUrl()}/games/${gameId}/preview`);
    if (options.previewToken) {
      previewUrl.searchParams.set('previewToken', options.previewToken);
    }
    return previewUrl.toString();
  }

  private buildGameUrl(gameId: string, options: PreviewLinkOptions = {}): string {
    const gameUrl = new URL(`${this.getPublicBaseUrl()}/games/${gameId}/index.html`);
    if (options.previewToken) {
      gameUrl.searchParams.set('previewToken', options.previewToken);
    }
    return gameUrl.toString();
  }

  private resolvePreviewTokenTtlSeconds(): number {
    const raw = Number.parseInt(this.configService.get<string>('GAME_PREVIEW_TOKEN_TTL_S', '2592000'), 10);
    return Number.isFinite(raw) && raw > 0 ? raw : 2_592_000;
  }

  private createAuthorPreviewToken(gameId: string, userId: string): string {
    return this.jwtService.sign(
      {
        type: 'game_preview',
        sub: userId,
        gameId,
      },
      {
        expiresIn: this.resolvePreviewTokenTtlSeconds(),
      },
    );
  }

  private createAdminPreviewToken(gameId: string): string {
    return this.jwtService.sign(
      {
        type: 'game_admin_preview',
        gameId,
      },
      {
        expiresIn: this.resolvePreviewTokenTtlSeconds(),
      },
    );
  }

  private resolveAuthorPreviewAccess(
    game: { id: string; authorId?: string | null },
    previewToken?: string,
  ): { userId: string } | null {
    if (!previewToken) {
      return null;
    }

    try {
      const payload = this.jwtService.verify(previewToken) as Record<string, unknown>;
      const userId = String(payload?.sub || payload?.id || '').trim();
      const tokenType = String(payload?.type || '').trim();
      const tokenGameId = String(payload?.gameId || '').trim();

      if (!userId || tokenType !== 'game_preview' || tokenGameId !== game.id) {
        return null;
      }

      if (game.authorId && game.authorId !== userId) {
        return null;
      }

      return { userId };
    } catch {
      return null;
    }
  }

  private hasAdminPreviewAccess(gameId: string, previewToken?: string): boolean {
    if (!previewToken) {
      return false;
    }

    try {
      const payload = this.jwtService.verify(previewToken) as Record<string, unknown>;
      return String(payload?.type || '').trim() === 'game_admin_preview'
        && String(payload?.gameId || '').trim() === gameId;
    } catch {
      return false;
    }
  }

  public buildAuthorPreviewUrls(gameId: string, userId: string): { previewUrl: string; gameUrl: string } {
    const previewToken = this.createAuthorPreviewToken(gameId, userId);
    return {
      previewUrl: this.buildPreviewUrl(gameId, { previewToken }),
      gameUrl: this.buildGameUrl(gameId, { previewToken }),
    };
  }

  public buildAdminPreviewUrls(gameId: string): { previewUrl: string; gameUrl: string } {
    const previewToken = this.createAdminPreviewToken(gameId);
    return {
      previewUrl: this.buildPreviewUrl(gameId, { previewToken }),
      gameUrl: this.buildGameUrl(gameId, { previewToken }),
    };
  }

  private withAuthorPreviewUrls<T extends { gameId?: string | null }>(
    payload: T,
    userId: string,
  ): T & { previewUrl?: string | null; gameUrl?: string | null } {
    const gameId = String(payload.gameId || '').trim();
    if (!gameId) {
      return payload;
    }

    const urls = this.buildAuthorPreviewUrls(gameId, userId);
    return {
      ...payload,
      previewUrl: urls.previewUrl,
      gameUrl: urls.gameUrl,
    };
  }

  private getIterationBaseStatus(
    taskOrMetadata?: { metadata?: any } | Record<string, unknown> | null,
    game?: { status?: GameStatus | string | null; publishedAt?: Date | null } | null,
  ): GameStatus {
    const metadata = taskOrMetadata && typeof taskOrMetadata === 'object' && 'metadata' in taskOrMetadata
      ? (taskOrMetadata as any).metadata
      : taskOrMetadata;
    const rawBaseStatus = metadata && typeof metadata === 'object'
      ? (metadata as any).baseStatus
      : undefined;

    if (
      rawBaseStatus === GameStatus.draft
      || rawBaseStatus === GameStatus.review
      || rawBaseStatus === GameStatus.published
    ) {
      return rawBaseStatus;
    }

    if (game?.status === GameStatus.review) {
      return GameStatus.review;
    }

    if (game?.status === GameStatus.published || game?.publishedAt) {
      return GameStatus.published;
    }

    return GameStatus.draft;
  }

  private getInFlightIterationStatus(baseStatus: GameStatus): GameStatus {
    return baseStatus === GameStatus.published ? GameStatus.published : GameStatus.generating;
  }

  private isBundlePlayable(bundle: { htmlCode?: string | null } | null | undefined): boolean {
    return typeof bundle?.htmlCode === 'string' && bundle.htmlCode.trim().length > 0;
  }

  private isPubliclyVisibleGame(game: {
    status?: string | null;
    visibility?: string | null;
  } | null | undefined): boolean {
    return game?.status === GameStatus.published && (game.visibility || 'public') === 'public';
  }

  private isPreviewVisibleGame(game: {
    status?: string | null;
    visibility?: string | null;
  } | null | undefined): boolean {
    const visibility = game?.visibility || 'public';
    return game?.status === GameStatus.published
      && (visibility === 'public' || visibility === 'unlisted');
  }

  private assertPublicPreviewAllowed(game: {
    status?: string | null;
    visibility?: string | null;
  }): void {
    if (this.isPreviewVisibleGame(game)) {
      return;
    }

    throw new NotFoundException('Game not found');
  }

  private assertGamePublishable(
    game: { status?: string | null },
    bundle: { htmlCode?: string | null } | null,
  ): void {
    if (game.status === GameStatus.banned) {
      throw new ForbiddenException('This game is unavailable');
    }
    if (game.status === GameStatus.generating) {
      throw new BadRequestException('Game is still generating');
    }
    if (game.status === GameStatus.failed) {
      throw new BadRequestException('Game generation failed');
    }
    if (!this.isBundlePlayable(bundle)) {
      throw new BadRequestException('Game bundle is not ready for publishing');
    }
  }

  private async loadBundleForGame(
    game: { id: string; version?: number | null },
    options: { preferLiveVersion?: boolean } = {},
  ) {
    let bundle = null;

    if (options.preferLiveVersion && Number.isFinite(game.version) && Number(game.version) > 0) {
      bundle = await this.bundleService.getBundle(game.id, Number(game.version));
    }

    if (!bundle) {
      bundle = await this.bundleService.getLatestBundle(game.id);
    }

    if (!bundle) {
      throw new NotFoundException('Game bundle not found');
    }

    return bundle;
  }

  private async loadGameAndBundle(id: string, options: { preferLiveVersion?: boolean } = {}) {
    const game = await this.prisma.game.findUnique({ where: { id } });
    if (!game) {
      throw new NotFoundException('Game not found');
    }

    const bundle = await this.loadBundleForGame(game, options);
    return { game, bundle };
  }

  private async attachPreviewUrl<T extends { id: string }>(game: T): Promise<T & { previewUrl: string }> {
    return {
      ...game,
      previewUrl: this.buildPreviewUrl(game.id),
    };
  }

  private async attachAuthorPreviewUrl<T extends { id: string }>(
    game: T,
    userId: string,
  ): Promise<T & { previewUrl: string }> {
    const urls = this.buildAuthorPreviewUrls(game.id, userId);
    return {
      ...game,
      previewUrl: urls.previewUrl,
    };
  }

  private async attachPreviewUrls<T extends { id: string }>(
    games: T[],
  ): Promise<Array<T & { previewUrl: string }>> {
    return Promise.all(games.map((game) => this.attachPreviewUrl(game)));
  }

  private async attachAuthorPreviewUrls<T extends { id: string }>(
    games: T[],
    userId: string,
  ): Promise<Array<T & { previewUrl: string }>> {
    return Promise.all(games.map((game) => this.attachAuthorPreviewUrl(game, userId)));
  }

  private async resolveDefaultFreeQuota(
    client: PrismaService | Prisma.TransactionClient,
  ): Promise<number> {
    const envValue = Number.parseInt(process.env.BILLING_DEFAULT_FREE_QUOTA || '', 10);
    if (Number.isFinite(envValue) && envValue >= 0) {
      return envValue;
    }

    const config = await client.systemConfig.findUnique({
      where: { configKey: 'billing.default_free_quota' },
      select: { configValue: true },
    });
    const configValue = Number.parseInt(config?.configValue || '', 10);

    if (Number.isFinite(configValue) && configValue >= 0) {
      return configValue;
    }

    return 5;
  }

  private resolvePipelineTimeout(rawValue?: unknown): number {
    const fallback = this.resolveTimeoutCatalogValue('timeout.pipeline.default_s', {
      min: 30,
      max: 3600,
    });
    const parsed = Number.parseInt(String(rawValue ?? fallback), 10);

    if (!Number.isFinite(parsed)) {
      return fallback;
    }

    return Math.min(3600, Math.max(30, parsed));
  }

  private resolveTaskTimeoutForPipelineVersion(
    rawValue: unknown,
    pipelineVersion: PipelineVersion,
  ): number {
    const resolved = this.resolvePipelineTimeout(rawValue);
    if (pipelineVersion !== 'v2') {
      return resolved;
    }

    const configuredDefault = this.resolveTimeoutCatalogValue('timeout.pipeline.default_s', {
      min: 30,
      max: 3600,
    });
    const v2Minimum = this.resolveTimeoutCatalogValue('timeout.pipeline.v2_min_s', {
      min: 30,
      max: 3600,
    });
    return Math.max(resolved, Math.max(v2Minimum, configuredDefault));
  }

  private buildUpstreamTimeoutMs(timeoutS?: unknown): number {
    return (this.resolvePipelineTimeout(timeoutS) + this.getUpstreamTimeoutBufferS()) * 1000;
  }

  private ensurePersistableGeneratedHtml(htmlCode: string): string {
    const normalized = (htmlCode || '').trim();
    if (!normalized) {
      throw new Error('AI pipeline returned empty HTML output');
    }

    const lower = normalized.toLowerCase();
    if (!lower.includes('<html') || !lower.includes('<body') || !lower.includes('</html>')) {
      throw new Error('AI pipeline returned incomplete HTML output');
    }

    return htmlCode;
  }

  private buildGenerationTaskSummary(
    gameId: string,
    taskType: 'pipeline_run' | 'pipeline_iterate',
    timeoutS?: unknown,
    version?: number,
  ): GenerationTaskSummary {
    const resolvedTimeoutS = this.resolvePipelineTimeout(timeoutS);
    const suffix = taskType === 'pipeline_iterate' && version ? `:v${version}` : '';

    return {
      taskId: `${gameId}:${taskType}${suffix}`,
      taskType,
      status: 'queued',
      timeoutS: resolvedTimeoutS,
      wsChannel: `game:${gameId}`,
      pollUrl: `/api/v1/games/${gameId}/generation-status`,
    };
  }

  private isTimeoutError(error: unknown): boolean {
    const message = this.extractErrorMessage(error as any);
    return /timed out|timeout|deadline exceeded|ECONNABORTED/i.test(message);
  }

  private isFinalTaskStatus(status?: string | null): status is Exclude<EffectiveTaskStatus, 'queued' | 'running'> {
    return status === 'succeeded' || status === 'failed' || status === 'canceled' || status === 'timed_out';
  }

  private isActiveTaskStatus(status?: string | null): status is 'queued' | 'running' {
    return status === 'queued' || status === 'running';
  }

  private getTaskDeadlineMs(task: {
    timeoutS?: number | null;
    startedAt?: Date | string | null;
    createdAt?: Date | string | null;
  }): number | null {
    const timeoutS = Number(task.timeoutS || 0);
    if (!Number.isFinite(timeoutS) || timeoutS <= 0) {
      return null;
    }

    const anchorValue = task.startedAt || task.createdAt;
    if (!anchorValue) {
      return null;
    }

    const anchorMs = new Date(anchorValue).getTime();
    if (!Number.isFinite(anchorMs)) {
      return null;
    }

    return anchorMs + (timeoutS * 1000) + (this.getUpstreamTimeoutBufferS() * 1000);
  }

  private deriveEffectiveTaskResolution(
    task: any,
    game: any,
  ): EffectiveTaskResolution | null {
    if (!task || this.isFinalTaskStatus(task.status)) {
      return null;
    }

    if (task.cancelRequested) {
      return {
        status: 'canceled',
        stage: task.progressStage || 'canceled',
        message: task.errorMessage || task.progressMessage || 'Task canceled',
      };
    }

    if (game?.status === 'banned') {
      return {
        status: 'canceled',
        stage: task.progressStage || 'canceled',
        message: 'Task canceled because the game is unavailable',
      };
    }

    if (game?.status === 'failed') {
      return {
        status: 'failed',
        stage: game.failedStage || task.failedStage || task.progressStage || 'failed',
        message: game.failedReason || task.errorMessage || 'Task failed',
        retryCount: game.retryCount ?? task.retryCount ?? 0,
      };
    }

    if (
      game
      && (game.status === 'draft' || game.status === 'published')
      && (game.version ?? 0) >= (task.version ?? 0)
    ) {
      return {
        status: 'succeeded',
        stage: 'completed',
        message: task.progressMessage || 'Task completed',
      };
    }

    const deadlineMs = this.getTaskDeadlineMs(task);
    if (deadlineMs && Date.now() > deadlineMs) {
      return {
        status: 'timed_out',
        stage: task.progressStage || 'timed_out',
        message: `Task timed out after ${task.timeoutS}s`,
        retryCount: task.retryCount ?? 0,
      };
    }

    return null;
  }

  private getInternalServiceToken(): string | null {
    const token = (process.env.ADMIN_TOKEN || '').trim();
    return token || null;
  }

  private async bindUpstreamTaskId(taskId: string, upstreamTaskId: string): Promise<void> {
    await Promise.resolve(this.prisma.generationTask.update({
      where: { id: taskId },
      data: {
        upstreamTaskId,
      },
    })).catch((error) => {
      this.logger.warn(`Failed to bind upstream task ${upstreamTaskId} to ${taskId}: ${error.message}`);
    });
  }

  private async requestUpstreamAsyncTask(params: {
    aiEngineBaseUrl: string;
    endpoint:
      | '/api/v1/ai/pipeline/run/async'
      | '/api/v1/ai/pipeline/iterate/async'
      | '/api/v1/ai/pipeline/v2/run/async'
      | '/api/v1/ai/pipeline/v2/iterate/async';
    payload: Record<string, unknown>;
    taskId?: string;
    userId: string;
    gameId: string;
  }): Promise<UpstreamAsyncTaskHandle> {
    await this.ensureTimeoutConfigCache();
    const response = await withRetry(() =>
      axios.post(
        `${params.aiEngineBaseUrl}${params.endpoint}`,
        params.payload,
        { timeout: this.getUpstreamRequestTimeoutMs() },
      ),
      {
        maxAttempts: 3,
        delayMs: this.getUpstreamRequestRetryDelayMs(),
        retryOnHttpResponse: false,
        onRetry: async ({ retry, maxRetries, attempt, maxAttempts, error }) => {
          this.emitProgress(
            params.userId,
            params.gameId,
            `AI generation request failed, retrying (${retry}/${maxRetries})`,
            STAGE_PCT.code_generating,
            {
              stage: 'code_generating',
              retry,
              maxRetries,
              attempt,
              maxAttempts,
              error: this.extractErrorMessage(error),
              taskId: params.taskId,
            },
          );
          if (params.taskId) {
            await this.generationTaskService.recordProgress({
              taskId: params.taskId,
              gameId: params.gameId,
              userId: params.userId,
              stage: 'code_generating',
              percentage: STAGE_PCT.code_generating,
              message: `AI generation request failed, retrying (${retry}/${maxRetries})`,
              details: {
                retry,
                maxRetries,
                attempt,
                maxAttempts,
                error: this.extractErrorMessage(error),
              },
            });
          }
        },
      },
    );

    const handle = response.data as UpstreamAsyncTaskHandle;
    if (!handle?.task_id) {
      throw new Error('AI engine did not return an async task handle');
    }

    if (params.taskId) {
      await this.bindUpstreamTaskId(params.taskId, handle.task_id);
    }

    return handle;
  }

  private async fetchUpstreamTaskSnapshot(
    aiEngineBaseUrl: string,
    upstreamTaskId: string,
  ): Promise<UpstreamAsyncTaskSnapshot | null> {
    await this.ensureTimeoutConfigCache();
    try {
      const response = await axios.get(
        `${aiEngineBaseUrl}/api/v1/ai/tasks/${upstreamTaskId}`,
        { timeout: this.getUpstreamSnapshotTimeoutMs() },
      );
      return response.data as UpstreamAsyncTaskSnapshot;
    } catch (error: any) {
      if (error?.response?.status === 404) {
        return null;
      }
      throw error;
    }
  }

  private async waitForUpstreamTaskTerminal(params: {
    aiEngineBaseUrl: string;
    upstreamTaskId: string;
    timeoutS?: number;
    taskId?: string;
    gameId?: string;
  }): Promise<UpstreamAsyncTaskSnapshot> {
    await this.ensureTimeoutConfigCache();
    const deadlineMs = Date.now() + this.buildUpstreamTimeoutMs(params.timeoutS) + this.getUpstreamDeadlineGraceMs();
    let runningMarked = false;

    while (Date.now() <= deadlineMs) {
      if (params.taskId && params.gameId) {
        await this.assertTaskCanPersistResult(params.taskId, params.gameId);
      }

      const snapshot = await this.fetchUpstreamTaskSnapshot(params.aiEngineBaseUrl, params.upstreamTaskId);
      if (snapshot) {
        if (snapshot.status === 'running' && params.taskId && !runningMarked) {
          runningMarked = await Promise.resolve(this.generationTaskService.markRunning(params.taskId))
            .then(() => true)
            .catch(() => false);
        }
        if (snapshot.status !== 'queued' && snapshot.status !== 'running') {
          return snapshot;
        }
      }

      await new Promise((resolve) => setTimeout(resolve, this.getUpstreamPollIntervalMs()));
    }

    throw new Error(`Upstream AI task ${params.upstreamTaskId} timed out while waiting for completion`);
  }

  private buildUpstreamTaskFailureError(
    snapshot: UpstreamAsyncTaskSnapshot,
    fallbackStage: string,
  ): any {
    return {
      response: {
        data: {
          detail: {
            message: snapshot.error?.message || 'Upstream AI task failed',
            failed_stage: snapshot.error?.failed_stage || fallbackStage,
            retry_count: Number(snapshot.error?.retry_count ?? 0) || 0,
            fallback: snapshot.error?.fallback || undefined,
            failure_family: snapshot.error?.failure_family || undefined,
            primary_artifact_id: snapshot.error?.primary_artifact_id || undefined,
          },
        },
      },
    };
  }

  private async cancelUpstreamTask(task: {
    id?: string;
    region?: string | null;
    upstreamTaskId?: string | null;
  }): Promise<void> {
    if (!task?.upstreamTaskId) {
      return;
    }

    await this.ensureTimeoutConfigCache();
    const aiEngineBaseUrl = await this.resolveAiEngineEndpoint(task.region || undefined);
    await axios.post(
      `${aiEngineBaseUrl}/api/v1/ai/tasks/${task.upstreamTaskId}/cancel`,
      {},
      { timeout: this.getUpstreamCancelTimeoutMs() },
    ).catch((error) => {
      this.logger.warn(
        `Failed to cancel upstream task ${task.upstreamTaskId} for ${task.id || 'unknown'}: ${this.extractErrorMessage(error)}`,
      );
    });
  }

  private async ensureNoActiveTaskForGame(gameId: string): Promise<void> {
    const activeTask = await this.prisma.generationTask.findFirst({
      where: {
        gameId,
        status: {
          in: ['queued', 'running'],
        },
      },
      orderBy: { createdAt: 'desc' },
    });

    if (!activeTask) {
      return;
    }

    const resolvedTask = await this.reconcileGenerationTask(activeTask);
    const effectiveTask = resolvedTask || activeTask;
    if (this.isActiveTaskStatus(effectiveTask.status)) {
      throw new ConflictException('Another generation task is already running for this game');
    }
  }

  private async invalidateFeedCache(): Promise<void> {
    const adminToken = this.getInternalServiceToken();
    if (!adminToken) {
      this.logger.warn('Skipping feed cache invalidation because ADMIN_TOKEN is not configured');
      return;
    }

    const baseUrl = (
      this.configService.get<string>('FEED_SERVICE_UPSTREAM_URL')
      || this.configService.get<string>('FEED_SERVICE_URL')
      || this.getPublicBaseUrl()
    ).replace(/\/$/, '');

    try {
      await axios.post(
        `${baseUrl}/api/v1/feed/internal/cache/invalidate`,
        {},
        {
          timeout: 5000,
          headers: {
            'x-admin-token': adminToken,
          },
        },
      );
    } catch (error: any) {
      this.logger.warn(`Failed to invalidate feed cache: ${this.extractErrorMessage(error)}`);
    }
  }

  private async getTaskWithGame(taskId: string) {
    return this.prisma.generationTask.findUnique({
      where: { id: taskId },
      include: {
        game: {
          select: {
            id: true,
            title: true,
            authorId: true,
            status: true,
            version: true,
            publishedAt: true,
            failedStage: true,
            failedReason: true,
            retryCount: true,
            lastErrorAt: true,
            accessGrantSource: true,
            accessGrantSubscriptionId: true,
            canPlay: true,
            requireSubscription: true,
          },
        },
      },
    });
  }

  private async persistTaskCancellation(task: any, reason: string) {
    return this.prisma.$transaction(async (tx) => {
      const currentTask = await tx.generationTask.findUnique({
        where: { id: task.id },
        include: {
          game: {
            select: {
              id: true,
              status: true,
              publishedAt: true,
              failedStage: true,
              failedReason: true,
              retryCount: true,
              accessGrantSource: true,
              accessGrantSubscriptionId: true,
              authorId: true,
            },
          },
        },
      });

      if (!currentTask) {
        throw new NotFoundException('Task not found');
      }

      const game = currentTask.game;
      if (game?.status === 'generating') {
        if (currentTask.taskType === GenerationTaskType.pipeline_run) {
          const refundApplied = await this.refundConsumedGenerationAccess(tx, {
            id: currentTask.gameId,
            authorId: game.authorId,
            accessGrantSource: game.accessGrantSource,
            accessGrantSubscriptionId: game.accessGrantSubscriptionId,
          });

          await tx.game.update({
            where: { id: currentTask.gameId },
            data: {
              status: 'failed',
              failedStage: currentTask.progressStage || 'canceled',
              failedReason: reason,
              retryCount: currentTask.retryCount ?? 0,
              lastErrorAt: new Date(),
              ...(refundApplied
                ? {
                    canPlay: false,
                    requireSubscription: true,
                    accessGrantSource: GameAccessGrantSource.none,
                    accessGrantSubscriptionId: null,
                  }
                : {}),
            },
          });
        } else {
          const baseStatus = this.getIterationBaseStatus(currentTask, game);
          await tx.game.update({
            where: { id: currentTask.gameId },
            data: {
              status: baseStatus,
              failedStage: null,
              failedReason: null,
              retryCount: 0,
              lastErrorAt: null,
            },
          });
        }
      }

      const canceled = await tx.generationTask.update({
        where: { id: currentTask.id },
        data: {
          status: 'canceled',
          cancelRequested: true,
          completedAt: new Date(),
          progressStage: 'canceled',
          progressMessage: reason.slice(0, 255),
          errorMessage: reason,
        },
      });

      await tx.generationTaskEvent.create({
        data: {
          id: randomUUID(),
          taskId: currentTask.id,
          gameId: currentTask.gameId,
          userId: currentTask.userId,
          eventType: GenerationTaskEventType.status,
          stage: 'canceled',
          percentage: currentTask.progressPct ?? 0,
          message: reason.slice(0, 255),
        },
      });

      return canceled;
    });
  }

  private async persistDerivedTaskResolution(task: any, game: any, resolution: EffectiveTaskResolution) {
    if (resolution.status === 'succeeded') {
      await this.generationTaskService.markSucceeded({
        taskId: task.id,
        previewUrl: task.previewUrl || this.buildPreviewUrl(task.gameId),
      });
      return this.prisma.generationTask.findUnique({ where: { id: task.id } });
    }

    if (resolution.status === 'failed') {
      await this.generationTaskService.markFailed({
        taskId: task.id,
        failedStage: resolution.stage,
        errorMessage: resolution.message,
        retryCount: resolution.retryCount ?? task.retryCount ?? 0,
      });
      return this.prisma.generationTask.findUnique({ where: { id: task.id } });
    }

    if (resolution.status === 'timed_out') {
      await this.cancelUpstreamTask(task);
      if (game?.status === 'generating') {
        if (task.taskType === GenerationTaskType.pipeline_run) {
          await this.persistFailureState({
            gameId: task.gameId,
            failedStage: resolution.stage,
            failedReason: resolution.message,
            retryCount: resolution.retryCount ?? task.retryCount ?? 0,
            status: 'failed',
            refundConsumedAccess: true,
          });
        } else {
          const baseStatus = this.getIterationBaseStatus(task, game);
          await this.prisma.game.update({
            where: { id: task.gameId },
            data: {
              status: baseStatus,
              failedStage: null,
              failedReason: null,
              retryCount: 0,
              lastErrorAt: null,
            },
          });
        }
      }

      await this.generationTaskService.markFailed({
        taskId: task.id,
        failedStage: resolution.stage,
        errorMessage: resolution.message,
        retryCount: resolution.retryCount ?? task.retryCount ?? 0,
        timedOut: true,
      });
      return this.prisma.generationTask.findUnique({ where: { id: task.id } });
    }

    await this.cancelUpstreamTask(task);
    await this.persistTaskCancellation(task, resolution.message);
    return this.prisma.generationTask.findUnique({ where: { id: task.id } });
  }

  private async reconcileTaskWithUpstream(task: any): Promise<any | null> {
    if (!task?.upstreamTaskId || this.isFinalTaskStatus(task.status)) {
      return null;
    }

    let aiEngineBaseUrl: string;
    try {
      aiEngineBaseUrl = await this.resolveAiEngineEndpoint(task.region);
    } catch (error: any) {
      this.logger.warn(`Failed to resolve AI engine endpoint for task ${task.id}: ${error?.message || error}`);
      return null;
    }

    let snapshot: UpstreamAsyncTaskSnapshot | null;
    try {
      snapshot = await this.fetchUpstreamTaskSnapshot(aiEngineBaseUrl, task.upstreamTaskId);
    } catch (error: any) {
      this.logger.warn(`Failed to fetch upstream task ${task.upstreamTaskId}: ${this.extractErrorMessage(error)}`);
      return null;
    }

    if (!snapshot) {
      return null;
    }

    if (snapshot.status === 'queued') {
      return null;
    }

    if (snapshot.status === 'running') {
      if (task.status === 'queued') {
        await Promise.resolve(this.generationTaskService.markRunning(task.id))
          .catch(() => undefined);
        return this.prisma.generationTask.findUnique({ where: { id: task.id } });
      }
      return null;
    }

    if (snapshot.status === 'canceled') {
      await this.persistTaskCancellation(task, snapshot.error?.message || 'Upstream task canceled');
      return this.prisma.generationTask.findUnique({ where: { id: task.id } });
    }

    try {
      if (snapshot.status === 'failed') {
        if (task.taskType === GenerationTaskType.pipeline_run) {
          await this.failPipelineTask({
            gameId: task.gameId,
            userId: task.userId,
            taskId: task.id,
            error: this.buildUpstreamTaskFailureError(snapshot, 'pipeline_run'),
          });
        } else {
          await this.failIterationTask({
            gameId: task.gameId,
            userId: task.userId,
            taskId: task.id,
            error: this.buildUpstreamTaskFailureError(snapshot, 'iteration'),
          });
        }
        return this.prisma.generationTask.findUnique({ where: { id: task.id } });
      }

      if (task.taskType === GenerationTaskType.pipeline_run) {
        await this.completePipelineTask({
          gameId: task.gameId,
          userId: task.userId,
          description: String(task.metadata?.description || ''),
          taskId: task.id,
          responseData: snapshot.result || {},
        });
      } else {
        const latestBundle = await Promise.resolve(this.bundleService.getLatestBundle(task.gameId))
          .catch(() => null);
        await this.completeIterationTask({
          gameId: task.gameId,
          userId: task.userId,
          feedback: String(task.metadata?.feedback || ''),
          conversationHistory: this.normalizeConversationHistory(task.metadata?.conversation),
          nextVersion: task.version || 1,
          taskId: task.id,
          currentCode: latestBundle?.htmlCode || '',
          responseData: snapshot.result || {},
        });
      }
      return this.prisma.generationTask.findUnique({ where: { id: task.id } });
    } catch (error) {
      if (error instanceof TaskSupersededError) {
        await this.persistTaskCancellation(task, error.message);
        return this.prisma.generationTask.findUnique({ where: { id: task.id } });
      }
      if (error instanceof TaskAbortedError) {
        return this.prisma.generationTask.findUnique({ where: { id: task.id } });
      }
      throw error;
    }
  }

  async reconcileGenerationTask(taskOrId: any): Promise<any> {
    const isSnapshotObject = typeof taskOrId === 'object' && taskOrId !== null;
    const task = typeof taskOrId === 'string'
      ? await this.getTaskWithGame(taskOrId)
      : taskOrId;

    if (!task) {
      return null;
    }

    const game = task.game
      || await this.prisma.game.findUnique({
        where: { id: task.gameId },
        select: {
          id: true,
          title: true,
          authorId: true,
          status: true,
          version: true,
          publishedAt: true,
          failedStage: true,
          failedReason: true,
          retryCount: true,
          lastErrorAt: true,
          canPlay: true,
          requireSubscription: true,
          accessGrantSource: true,
          accessGrantSubscriptionId: true,
        },
      });

    const resolution = this.deriveEffectiveTaskResolution(task, game);
    if (!resolution) {
      const upstreamReconciled = await this.reconcileTaskWithUpstream(task);
      if (upstreamReconciled) {
        return upstreamReconciled;
      }
      return task;
    }

    const reconciled = await this.persistDerivedTaskResolution(task, game, resolution);
    if (!reconciled) {
      return task;
    }

    if (isSnapshotObject) {
      const latestGame = await Promise.resolve(this.prisma.game.findUnique({
        where: { id: task.gameId },
        select: {
          id: true,
          title: true,
          authorId: true,
          status: true,
          version: true,
          publishedAt: true,
          failedStage: true,
          failedReason: true,
          retryCount: true,
          lastErrorAt: true,
          canPlay: true,
          requireSubscription: true,
          accessGrantSource: true,
          accessGrantSubscriptionId: true,
        },
      })).catch(() => null);

      return {
        ...task,
        ...reconciled,
        game: latestGame || task.game || game,
      };
    }

    return reconciled;
  }

  private async assertTaskCanPersistResult(taskId?: string, gameId?: string): Promise<void> {
    if (!taskId) {
      return;
    }

    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
      select: {
        status: true,
        cancelRequested: true,
        gameId: true,
      },
    });

    if (!task) {
      throw new TaskAbortedError('Task no longer exists');
    }

    if (task.cancelRequested || this.isFinalTaskStatus(task.status)) {
      throw new TaskAbortedError('Task was terminated before completion');
    }

    const effectiveGameId = gameId || task.gameId;
    const game = await this.prisma.game.findUnique({
      where: { id: effectiveGameId },
      select: { status: true },
    });

    if (!game || game.status === 'banned') {
      throw new TaskAbortedError('Game is unavailable');
    }
  }

  async terminateTask(taskId: string, options: { userId?: string; reason?: string; admin?: boolean } = {}) {
    const task = await this.getTaskWithGame(taskId);
    if (!task) {
      throw new NotFoundException('Task not found');
    }

    if (!options.admin && options.userId && task.userId !== options.userId) {
      throw new ForbiddenException('You do not have permission to cancel this task');
    }

    const derivedResolution = this.deriveEffectiveTaskResolution(task, task.game);
    if (derivedResolution) {
      const reconciled = await this.persistDerivedTaskResolution(task, task.game, derivedResolution);
      return this.generationTaskService.toTaskSummary(reconciled || task);
    }

    if (this.isFinalTaskStatus(task.status)) {
      return this.generationTaskService.toTaskSummary(task);
    }

    const reason = options.reason || (options.admin ? 'Task terminated by admin' : 'Task canceled by user');
    await this.cancelUpstreamTask(task);
    const canceled = await this.persistTaskCancellation(task, reason);
    return this.generationTaskService.toTaskSummary(canceled);
  }

  async terminateActiveTasksForGame(
    gameId: string,
    options: { reason: string; skipTaskIds?: string[] } = { reason: 'Task canceled because the game was removed' },
  ): Promise<void> {
    const activeTasks = await this.prisma.generationTask.findMany({
      where: {
        gameId,
        status: {
          in: ['queued', 'running'],
        },
      },
      orderBy: { createdAt: 'desc' },
    });

    for (const task of activeTasks) {
      if (options.skipTaskIds?.includes(task.id)) {
        continue;
      }
      await this.terminateTask(task.id, {
        admin: true,
        reason: options.reason,
      }).catch((error) => {
        this.logger.warn(`Failed to terminate active task ${task.id}: ${error.message}`);
      });
    }
  }

  private async ensureUserQuota(
    client: PrismaService | Prisma.TransactionClient,
    userId: string,
  ) {
    const totalFreeQuota = await this.resolveDefaultFreeQuota(client);
    return client.userQuota.upsert({
      where: { userId },
      update: {},
      create: {
        userId,
        totalFreeQuota,
        usedFreeQuota: 0,
      },
    });
  }

  private async markExpiredSubscriptions(
    client: PrismaService | Prisma.TransactionClient,
    userId: string,
  ) {
    const now = new Date();
    await client.userSubscription.updateMany({
      where: {
        userId,
        status: UserSubscriptionStatus.active,
        expiresAt: { lte: now },
      },
      data: {
        status: UserSubscriptionStatus.expired,
      },
    });
  }

  private async findActiveSubscription(
    client: PrismaService | Prisma.TransactionClient,
    userId: string,
  ) {
    return client.userSubscription.findFirst({
      where: {
        userId,
        status: UserSubscriptionStatus.active,
        expiresAt: { gt: new Date() },
      },
      orderBy: {
        expiresAt: 'desc',
      },
      include: {
        plan: true,
      },
    });
  }

  private computeQuotaRemaining(
    quota: { totalFreeQuota: number; usedFreeQuota: number },
    subscription?: { quotaThisPeriod: number; usedThisPeriod: number } | null,
  ) {
    const freeRemaining = Math.max(quota.totalFreeQuota - quota.usedFreeQuota, 0);
    const subscriptionRemaining = subscription
      ? Math.max(subscription.quotaThisPeriod - subscription.usedThisPeriod, 0)
      : 0;
    return freeRemaining + subscriptionRemaining;
  }

  async create(userId: string, dto: CreateGameDto): Promise<any> {
    try {
      const gameId = randomUUID();
      const description = dto.description || dto.prompt || '';
      const title = dto.title?.trim() || `Game ${gameId.substring(0, 8)}`;
      const executionRegion = this.resolveExecutionRegion(dto.regionHint);
      const pipelineVersion = this.resolvePipelineVersion({
        entrypoint: 'create',
        userId,
        executionRegion,
      });
      const timeoutS = this.resolveTaskTimeoutForPipelineVersion(dto.timeoutS, pipelineVersion);
      const runtimeProfileHint = this.inferRuntimeProfileHint(description, dto.title);
      const promptBundleSnapshot = pipelineVersion === 'v2'
        ? await this.buildPromptBundleSnapshot('create', runtimeProfileHint)
        : null;
      const runtimeContract = pipelineVersion === 'v2'
        ? await this.buildDefaultRuntimeContract('create', runtimeProfileHint)
        : null;
      const { access, task } = await this.prisma.$transaction(async (tx) => {
        await this.markExpiredSubscriptions(tx, userId);
        const quota = await this.ensureUserQuota(tx, userId);
        const subscription = await this.findActiveSubscription(tx, userId);
        const freeRemaining = Math.max(quota.totalFreeQuota - quota.usedFreeQuota, 0);
        const subscriptionRemaining = subscription
          ? Math.max(subscription.quotaThisPeriod - subscription.usedThisPeriod, 0)
          : 0;

        let canPlay = false;
        let requireSubscription = true;
        let quotaRemaining = 0;
        let accessGrantSource: GameAccessGrantSource = GameAccessGrantSource.none;
        let accessGrantSubscriptionId: string | null = null;

        if (freeRemaining > 0) {
          const updatedQuota = await tx.userQuota.update({
            where: { userId },
            data: {
              usedFreeQuota: { increment: 1 },
            },
          });
          canPlay = true;
          requireSubscription = false;
          quotaRemaining = this.computeQuotaRemaining(updatedQuota, subscription);
          accessGrantSource = GameAccessGrantSource.free_quota;
        } else if (subscription && subscriptionRemaining > 0) {
          const updatedSubscription = await tx.userSubscription.update({
            where: { id: subscription.id },
            data: {
              usedThisPeriod: { increment: 1 },
            },
          });
          canPlay = true;
          requireSubscription = false;
          quotaRemaining = this.computeQuotaRemaining(quota, updatedSubscription);
          accessGrantSource = GameAccessGrantSource.subscription_quota;
          accessGrantSubscriptionId = subscription.id;
        }

        await tx.game.create({
          data: {
            id: gameId,
            authorId: userId,
            description,
            status: 'generating',
            failedStage: null,
            failedReason: null,
            retryCount: 0,
            lastErrorAt: null,
            title,
            commentCount: 0,
            forkDepth: 0,
            visibility: 'private',
            canPlay,
            requireSubscription,
            accessGrantSource,
            accessGrantSubscriptionId,
          },
        });

        const task = await this.generationTaskService.createTask({
          gameId,
          userId,
          taskType: GenerationTaskType.pipeline_run,
          region: executionRegion,
          timeoutS,
          version: 1,
          pipelineVersion,
          promptBundleId: promptBundleSnapshot?.bundle_id ?? null,
          promptBundleVersion: promptBundleSnapshot?.bundle_version ?? null,
          runtimeProfile: runtimeContract?.runtime_profile ?? null,
          contractVersion: runtimeContract?.version ?? null,
          metadata: {
            description,
            region: executionRegion,
            pipelineVersion,
          },
          client: tx,
        });

        return {
          access: {
            canPlay,
            requireSubscription,
            quotaRemaining,
            accessGrantSource,
            accessGrantSubscriptionId,
          },
          task,
        };
      });

      this.emitProgress(userId, gameId, 'started', 0, {
        stage: 'started',
        attempt: 1,
        maxAttempts: 1,
      });

      // Run pipeline asynchronously – client subscribes to WebSocket for progress
      setImmediate(() => {
        void this.executePipelineTask(
          gameId,
          userId,
          description,
          timeoutS,
          task.id,
          executionRegion,
          {
            pipelineVersion,
            title,
            access,
            promptBundleSnapshot,
            runtimeContract,
          },
        ).catch((error) => {
          this.logger.error(
            `Background pipeline task crashed for game ${gameId}: ${this.extractErrorMessage(error)}`,
          );
        });
      });

      const generationTask = this.withAuthorPreviewUrls(
        this.generationTaskService.toTaskSummary(task),
        userId,
      );

      return {
        ...generationTask,
        gameId,
        title,
        description,
        status: 'generating',
        canPlay: access.canPlay,
        quotaRemaining: access.quotaRemaining,
        requireSubscription: access.requireSubscription,
        generationTask,
      };
    } catch (error) {
      this.logger.error(`Failed to create game: ${error.message}`);
      throw error;
    }
  }

  /**
   * Calls the AI engine's full pipeline (stages 02-06).
   * Emits fine-grained WebSocket progress for each pipeline stage.
   */
  private async runPipeline(
    gameId: string,
    userId: string,
    description: string,
    timeoutS?: number,
    taskId?: string,
    executionRegion?: string,
  ): Promise<void> {
    return this.executePipelineTask(gameId, userId, description, timeoutS, taskId, executionRegion);
    /*
    try {
      const resolvedTimeoutS = this.resolvePipelineTimeout(timeoutS);
      const aiEngineBaseUrl = await this.resolveAiEngineEndpoint(executionRegion);
      if (taskId) {
        await this.generationTaskService.markRunning(taskId!);
      }
      const response = await withRetry(() =>
        axios.post(
          `${aiEngineBaseUrl}/api/v1/ai/pipeline/run`,
          {
            game_id: gameId,
            description,
            user_id: userId,
            platform: 'wechat_webview',
            region: this.resolveExecutionRegion(executionRegion),
            timeout_s: resolvedTimeoutS,
            task_id: taskId,
          },
          { timeout: this.buildUpstreamTimeoutMs(resolvedTimeoutS) },
        ),
        {
          maxAttempts: 3,
          delayMs: 3000,
          retryOnHttpResponse: false,
          onRetry: async ({ retry, maxRetries, attempt, maxAttempts, error }) => {
            this.emitProgress(
              userId,
              gameId,
              `AI 生成服务请求失败，重试中（${retry}/${maxRetries}）`,
              STAGE_PCT.code_generating,
              {
                stage: 'code_generating',
                retry,
                maxRetries,
                attempt,
                maxAttempts,
                error: this.extractErrorMessage(error),
                taskId,
              },
            );
            if (taskId) {
              await this.generationTaskService.recordProgress({
                taskId,
                gameId,
                userId,
                stage: 'code_generating',
                percentage: STAGE_PCT.code_generating,
                message: `AI 生成服务请求失败，重试中（${retry}/${maxRetries}）`,
                details: {
                  retry,
                  maxRetries,
                  attempt,
                  maxAttempts,
                  error: this.extractErrorMessage(error),
                },
              });
            }
          },
        },
      );

      const {
        html_code: htmlCode = '',
        strategy = 'llm',
        qa_passed: qaPassed = false,
        qa_retries: qaRetries = 0,
        game_spec: gameSpec = {},
        generation_time_ms: genTimeMs = 0,
        code_size_bytes: codeSizeBytes = 0,
        quality_score: qualityScore = 0,
        quality_breakdown: qualityBreakdown = {},
      } = response.data;
      this.ensurePersistableGeneratedHtml(htmlCode);
      await this.assertTaskCanPersistResult(taskId, gameId);

      const bundlePreviewUrl = this.buildPreviewUrl(gameId);

      // Extract <title> from HTML; fallback to type-based deriveTitle
      const htmlTitleMatch = htmlCode.match(/<title>([^<]{1,60})<\/title>/i);
      const aiTitle = htmlTitleMatch ? htmlTitleMatch[1].trim() : null;
      const gameTitle = (aiTitle && aiTitle.length > 2) ? aiTitle : this.deriveTitle(gameSpec, description);

      // Only overwrite title if it's still the auto-generated placeholder (Game [id])
      const currentGame = await this.prisma.game.findUnique({ where: { id: gameId }, select: { title: true } });
      const isPlaceholderTitle = /^Game\s+[0-9a-f]{8}$/i.test(currentGame?.title || '');

      this.emitStage(userId, gameId, 'publishing', {
        stage: 'publishing',
        attempt: 1,
        maxAttempts: 3,
      });

      await this.persistGeneratedGameResult({
        gameId,
        userId,
        version: 1,
        htmlCode,
        previewUrl: bundlePreviewUrl,
        metadata: {
          strategy,
          qaPassed,
          qaRetries,
          gameSpec,
          genTimeMs,
          codeSizeBytes,
          qualityScore,
          qualityBreakdown,
        },
        gameTitle: isPlaceholderTitle ? gameTitle : undefined,
        updateData: {
          status: 'draft',
          version: 1,
          gameType: gameSpec?.game_type || null,
          qualityScore,
          failedStage: null,
          failedReason: null,
          retryCount: 0,
          lastErrorAt: null,
        },
      });

      if (taskId) {
        await this.generationTaskService.markSucceeded({
          taskId: taskId!,
          previewUrl: bundlePreviewUrl,
          resultSummary: {
            strategy,
            qaPassed,
            qaRetries,
            gameType: gameSpec?.game_type || null,
            generationTimeMs: genTimeMs,
            codeSizeBytes,
            qualityScore,
          },
        });
      }

      this.emitStage(userId, gameId, 'completed', {
        stage: 'completed',
        qaRetries,
      });
    } catch (error) {
      if (error instanceof TaskSupersededError) {
        if (taskId) {
          await this.persistTaskCancellation({
            id: taskId,
            gameId,
            userId,
            taskType: GenerationTaskType.pipeline_run,
            progressPct: STAGE_PCT.publishing,
          }, error.message);
        }
        return;
      }
      if (error instanceof TaskAbortedError) {
        this.logger.warn(`Pipeline result discarded for task ${taskId || 'n/a'}: ${error.message}`);
        return;
      }

      const failure = this.extractFailureContext(error);
      const errorMessage = failure.message;
      this.logger.error(`Pipeline failed for game ${gameId}: ${errorMessage}`);
      this.logStructuredFailure('PIPELINE_RUN_FAILURE', {
        gameId,
        userId,
        stage: failure.failedStage || 'failed',
        retryCount: failure.retryCount,
        error: errorMessage,
      });

      await this.persistFailureState({
        gameId,
        failedStage: failure.failedStage || 'pipeline_run',
        failedReason: errorMessage,
        retryCount: failure.retryCount,
        status: 'failed',
        refundConsumedAccess: true,
      });

      if (taskId) {
        await this.generationTaskService.markFailed({
          taskId: taskId!,
          failedStage: failure.failedStage || 'pipeline_run',
          errorMessage,
          retryCount: failure.retryCount,
          fallback: failure.fallback,
          timedOut: this.isTimeoutError(error),
        }).catch((taskError) => {
          this.logger.warn(`Failed to update generation task ${taskId}: ${taskError.message}`);
        });
      }

      this.wsGateway.emitGenerationError(userId, gameId, errorMessage, {
        stage: failure.failedStage || 'pipeline_run',
        retryCount: failure.retryCount,
        fallback: failure.fallback,
      });
      this.wsGateway.emitNotification(userId, {
        type: 'error',
        message: `Game generation failed: ${errorMessage}`,
        gameId,
      });
    }
    */
  }

  private async completePipelineTask(params: {
    gameId: string;
    userId: string;
    description: string;
    taskId?: string;
    responseData: Record<string, any>;
  }): Promise<void> {
    const {
      gameId,
      userId,
      description,
      taskId,
      responseData,
    } = params;
    const {
      html_code: htmlCode = '',
      strategy = 'llm',
      qa_passed: qaPassed = false,
      qa_retries: qaRetries = 0,
      game_spec: gameSpec = {},
      generation_time_ms: genTimeMs = 0,
      code_size_bytes: codeSizeBytes = 0,
      quality_score: qualityScore = 0,
      quality_breakdown: qualityBreakdown = {},
      primary_artifact_id: primaryArtifactId = undefined,
    } = responseData || {};
    this.ensurePersistableGeneratedHtml(htmlCode);
    await this.assertTaskCanPersistResult(taskId, gameId);

    const bundlePreviewUrl = this.buildPreviewUrl(gameId);
    const htmlTitleMatch = htmlCode.match(/<title>([^<]{1,60})<\/title>/i);
    const aiTitle = htmlTitleMatch ? htmlTitleMatch[1].trim() : null;
    const gameTitle = (aiTitle && aiTitle.length > 2) ? aiTitle : this.deriveTitle(gameSpec, description);
    const currentGame = await this.prisma.game.findUnique({ where: { id: gameId }, select: { title: true } });
    const isPlaceholderTitle = /^Game\s+[0-9a-f]{8}$/i.test(currentGame?.title || '');

    this.emitStage(userId, gameId, 'publishing', {
      stage: 'publishing',
      attempt: 1,
      maxAttempts: 3,
    });

    await this.persistGeneratedGameResult({
      gameId,
      userId,
      taskId,
      version: 1,
      htmlCode,
      previewUrl: bundlePreviewUrl,
      metadata: {
        strategy,
        qaPassed,
        qaRetries,
        gameSpec,
        genTimeMs,
        codeSizeBytes,
        qualityScore,
        qualityBreakdown,
      },
      gameTitle: isPlaceholderTitle ? gameTitle : undefined,
      updateData: {
        status: 'draft',
        version: 1,
        gameType: gameSpec?.game_type || null,
        qualityScore,
        failedStage: null,
        failedReason: null,
        retryCount: 0,
        lastErrorAt: null,
      },
    });

    if (taskId) {
      await this.generationTaskService.markSucceeded({
        taskId,
        previewUrl: bundlePreviewUrl,
        primaryArtifactId:
          typeof primaryArtifactId === 'string' && primaryArtifactId.trim()
            ? primaryArtifactId
            : undefined,
        resultSummary: {
          strategy,
          qaPassed,
          qaRetries,
          gameType: gameSpec?.game_type || null,
          generationTimeMs: genTimeMs,
          codeSizeBytes,
          qualityScore,
        },
      });
    }

    this.emitStage(userId, gameId, 'completed', {
      stage: 'completed',
      qaRetries,
    });
  }

  private async failPipelineTask(params: {
    gameId: string;
    userId: string;
    taskId?: string;
    error: unknown;
  }): Promise<void> {
    const {
      gameId,
      userId,
      taskId,
      error,
    } = params;
    if (taskId) {
      const currentTask = await this.prisma.generationTask.findUnique({
        where: { id: taskId },
        select: { status: true },
      });
      if (currentTask && this.isFinalTaskStatus(currentTask.status)) {
        return;
      }
    }
    const currentGame = await this.prisma.game.findUnique({
      where: { id: gameId },
      select: { status: true },
    });
    if (
      currentGame
      && typeof currentGame.status === 'string'
      && currentGame.status !== 'generating'
      && currentGame.status !== 'failed'
    ) {
      return;
    }

    const failure = this.extractFailureContext(error);
    const errorMessage = failure.message;
    this.logger.error(`Pipeline failed for game ${gameId}: ${errorMessage}`);
    this.logStructuredFailure('PIPELINE_RUN_FAILURE', {
      gameId,
      userId,
      stage: failure.failedStage || 'failed',
      retryCount: failure.retryCount,
      error: errorMessage,
    });

    await this.persistFailureState({
      gameId,
      failedStage: failure.failedStage || 'pipeline_run',
      failedReason: errorMessage,
      retryCount: failure.retryCount,
      status: 'failed',
      refundConsumedAccess: true,
    });

    if (taskId) {
      await Promise.resolve(this.generationTaskService.markFailed({
        taskId,
        failedStage: failure.failedStage || 'pipeline_run',
        errorMessage,
        retryCount: failure.retryCount,
        fallback: failure.fallback,
        timedOut: this.isTimeoutError(error),
        failureFamily: failure.failureFamily,
        primaryArtifactId: failure.primaryArtifactId,
      })).catch((taskError) => {
        this.logger.warn(`Failed to update generation task ${taskId}: ${taskError.message}`);
      });
    }

    this.wsGateway.emitGenerationError(userId, gameId, errorMessage, {
      stage: failure.failedStage || 'pipeline_run',
      retryCount: failure.retryCount,
      fallback: failure.fallback,
    });
    this.wsGateway.emitNotification(userId, {
      type: 'error',
      message: `Game generation failed: ${errorMessage}`,
      gameId,
    });
  }

  private normalizeConversationHistory(value: unknown): Array<{ role: string; content: string }> {
    if (!Array.isArray(value)) {
      return [];
    }

    return value
      .filter((item) => item && typeof item === 'object')
      .map((item: any) => ({
        role: typeof item.role === 'string' ? item.role : '',
        content: typeof item.content === 'string' ? item.content : '',
      }))
      .filter((item) => item.role && item.content);
  }

  private async completeIterationTask(params: {
    gameId: string;
    userId: string;
    feedback: string;
    conversationHistory: Array<{ role: string; content: string }>;
    nextVersion: number;
    taskId?: string;
    currentCode: string;
    responseData: Record<string, any>;
    baseStatus?: GameStatus;
  }): Promise<void> {
    const {
      gameId,
      userId,
      feedback,
      conversationHistory,
      nextVersion,
      taskId,
      currentCode,
      responseData,
      baseStatus,
    } = params;
    const {
      html_code: htmlCode = currentCode,
      iteration_type: iterationType = 'element_change',
      generation_time_ms: genTimeMs = 0,
      qa_retries: qaRetries = 0,
      iteration_retries: iterationRetries = 0,
      primary_artifact_id: primaryArtifactId = undefined,
    } = responseData || {};
    this.ensurePersistableGeneratedHtml(htmlCode);
    await this.assertTaskCanPersistResult(taskId, gameId);

    const bundlePreviewUrl = this.buildPreviewUrl(gameId);
    this.emitStage(userId, gameId, 'publishing', {
      stage: 'publishing',
      attempt: 1,
      maxAttempts: 3,
    });

    await this.persistGeneratedGameResult({
      gameId,
      userId,
      taskId,
      version: nextVersion,
      htmlCode,
      previewUrl: bundlePreviewUrl,
      metadata: {
        feedback,
        iterationType,
        genTimeMs,
        qaRetries,
        iterationRetries,
        aiConversation: [
          ...conversationHistory,
          { role: 'user', content: feedback },
        ],
      },
      updateData: {
        ...(baseStatus === GameStatus.published ? {} : { version: nextVersion }),
        status: baseStatus || GameStatus.draft,
        failedStage: null,
        failedReason: null,
        retryCount: 0,
        lastErrorAt: null,
      },
    });

    if (taskId) {
      await this.generationTaskService.markSucceeded({
        taskId,
        previewUrl: bundlePreviewUrl,
        primaryArtifactId:
          typeof primaryArtifactId === 'string' && primaryArtifactId.trim()
            ? primaryArtifactId
            : undefined,
        resultSummary: {
          feedback,
          iterationType,
          generationTimeMs: genTimeMs,
          qaRetries,
          iterationRetries,
          version: nextVersion,
        },
      });
    }

    this.wsGateway.emitGenerationProgress(userId, gameId, '杩唬瀹屾垚', 100);
  }

  private async failIterationTask(params: {
    gameId: string;
    userId: string;
    taskId?: string;
    error: unknown;
  }): Promise<void> {
    const {
      gameId,
      userId,
      taskId,
      error,
    } = params;
    if (taskId) {
      const currentTask = await this.prisma.generationTask.findUnique({
        where: { id: taskId },
        select: { status: true },
      });
      if (currentTask && this.isFinalTaskStatus(currentTask.status)) {
        return;
      }
    }
    const currentGame = await this.prisma.game.findUnique({
      where: { id: gameId },
      select: { status: true, publishedAt: true },
    });
    if (!currentGame || currentGame.status === GameStatus.banned) {
      return;
    }
    const baseStatus = taskId
      ? this.getIterationBaseStatus(
        await this.prisma.generationTask.findUnique({
          where: { id: taskId },
          select: { metadata: true },
        }),
        currentGame,
      )
      : this.getIterationBaseStatus(null, currentGame);

    const failure = this.extractFailureContext(error);
    this.logger.error(`Iteration failed for game ${gameId}: ${failure.message}`);
    this.logStructuredFailure('PIPELINE_ITERATION_FAILURE', {
      gameId,
      userId,
      stage: failure.failedStage || 'iteration',
      retryCount: failure.retryCount,
      error: failure.message,
    });
    await this.persistFailureState({
      gameId,
      failedStage: failure.failedStage || 'iteration',
      failedReason: failure.message,
      retryCount: failure.retryCount,
      status: baseStatus,
    });
    if (taskId) {
      await Promise.resolve(this.generationTaskService.markFailed({
        taskId,
        failedStage: failure.failedStage || 'iteration',
        errorMessage: failure.message,
        retryCount: failure.retryCount,
        fallback: failure.fallback,
        timedOut: this.isTimeoutError(error),
        failureFamily: failure.failureFamily,
        primaryArtifactId: failure.primaryArtifactId,
      })).catch((taskError) => {
        this.logger.warn(`Failed to update iteration task ${taskId}: ${taskError.message}`);
      });
    }
    this.wsGateway.emitGenerationError(userId, gameId, failure.message, {
      stage: failure.failedStage || 'iteration',
      retryCount: failure.retryCount,
      fallback: failure.fallback,
    });
    this.wsGateway.emitNotification(userId, {
      type: 'error',
      message: `Game iteration failed: ${failure.message}`,
      gameId,
    });
  }

  private async executePipelineTask(
    gameId: string,
    userId: string,
    description: string,
    timeoutS?: number,
    taskId?: string,
    executionRegion?: string,
    options: CreateExecutionOptions = {},
  ): Promise<void> {
    try {
      const resolvedTimeoutS = this.resolvePipelineTimeout(timeoutS);
      const aiEngineBaseUrl = await this.resolveAiEngineEndpoint(executionRegion);
      const resolvedRegion = this.resolveExecutionRegion(executionRegion);
      const pipelineVersion = options.pipelineVersion === 'v1' ? 'v1' : 'v2';
      const runtimeProfileHint = this.inferRuntimeProfileHint(description, options.title);
      const promptBundleSnapshot = pipelineVersion === 'v2'
        ? (options.promptBundleSnapshot ?? await this.buildPromptBundleSnapshot('create', runtimeProfileHint))
        : null;
      const runtimeContract = pipelineVersion === 'v2'
        ? (options.runtimeContract ?? await this.buildDefaultRuntimeContract('create', runtimeProfileHint))
        : null;
      if (taskId) {
        await this.generationTaskService.markRunning(taskId!);
      }

      const handle = await this.requestUpstreamAsyncTask({
        aiEngineBaseUrl,
        endpoint: pipelineVersion === 'v2'
          ? '/api/v1/ai/pipeline/v2/run/async'
          : '/api/v1/ai/pipeline/run/async',
        payload: pipelineVersion === 'v2'
          ? this.buildCreateV2Payload({
            gameId,
            userId,
            title: options.title,
            description,
            executionRegion: resolvedRegion,
            timeoutS: resolvedTimeoutS,
            taskId,
            access: options.access,
            promptBundleSnapshot: promptBundleSnapshot!,
            runtimeContract: runtimeContract!,
          })
          : {
            game_id: gameId,
            description,
            user_id: userId,
            platform: 'wechat_webview',
            region: resolvedRegion,
            timeout_s: resolvedTimeoutS,
            task_id: taskId,
          },
        taskId,
        userId,
        gameId,
      });
      const snapshot = await this.waitForUpstreamTaskTerminal({
        aiEngineBaseUrl,
        upstreamTaskId: handle.task_id,
        timeoutS: resolvedTimeoutS,
        taskId,
        gameId,
      });

      if (snapshot.status === 'canceled') {
        throw new TaskAbortedError(snapshot.error?.message || 'Upstream task was canceled');
      }
      if (snapshot.status === 'failed') {
        throw this.buildUpstreamTaskFailureError(snapshot, 'pipeline_run');
      }

      await this.completePipelineTask({
        gameId,
        userId,
        description,
        taskId,
        responseData: snapshot.result || {},
      });
    } catch (error) {
      if (error instanceof TaskSupersededError) {
        if (taskId) {
          await this.persistTaskCancellation({
            id: taskId,
            gameId,
            userId,
            taskType: GenerationTaskType.pipeline_run,
            progressPct: STAGE_PCT.publishing,
          }, error.message);
        }
        return;
      }
      if (error instanceof TaskAbortedError) {
        this.logger.warn(`Pipeline result discarded for task ${taskId || 'n/a'}: ${error.message}`);
        return;
      }

      await this.failPipelineTask({
        gameId,
        userId,
        taskId,
        error,
      });
    }
  }

  private async executeIterationTask(
    gameId: string,
    userId: string,
    feedback: string,
    nextVersion: number,
    conversationHistory: Array<{ role: string; content: string }>,
    currentCode: string,
    timeoutS?: number,
    taskId?: string,
    executionRegion?: string,
    options: IterateExecutionOptions = {},
  ): Promise<void> {
    try {
      const resolvedTimeoutS = this.resolvePipelineTimeout(timeoutS);
      const aiEngineBaseUrl = await this.resolveAiEngineEndpoint(executionRegion);
      const resolvedRegion = this.resolveExecutionRegion(executionRegion);
      const pipelineVersion = options.pipelineVersion === 'v1' ? 'v1' : 'v2';
      const runtimeProfileHint = this.inferRuntimeProfileHint(options.game?.gameType, feedback);
      const promptBundleSnapshot = pipelineVersion === 'v2'
        ? (options.promptBundleSnapshot ?? await this.buildPromptBundleSnapshot('iterate', runtimeProfileHint))
        : null;
      const runtimeContract = pipelineVersion === 'v2'
        ? (options.runtimeContract ?? await this.buildDefaultRuntimeContract('iterate', runtimeProfileHint))
        : null;
      if (taskId) {
        await this.generationTaskService.markRunning(taskId!);
      }

      const handle = await this.requestUpstreamAsyncTask({
        aiEngineBaseUrl,
        endpoint: pipelineVersion === 'v2'
          ? '/api/v1/ai/pipeline/v2/iterate/async'
          : '/api/v1/ai/pipeline/iterate/async',
        payload: pipelineVersion === 'v2'
          ? this.buildIterateV2Payload({
            gameId,
            userId,
            feedback,
            conversationHistory,
            currentCode,
            executionRegion: resolvedRegion,
            timeoutS: resolvedTimeoutS,
            taskId,
            game: options.game,
            promptBundleSnapshot: promptBundleSnapshot!,
            runtimeContract: runtimeContract!,
          })
          : {
            game_id: gameId,
            feedback,
            user_id: userId,
            conversation: conversationHistory,
            current_code: currentCode,
            region: resolvedRegion,
            timeout_s: resolvedTimeoutS,
            task_id: taskId,
          },
        taskId,
        userId,
        gameId,
      });
      const snapshot = await this.waitForUpstreamTaskTerminal({
        aiEngineBaseUrl,
        upstreamTaskId: handle.task_id,
        timeoutS: resolvedTimeoutS,
        taskId,
        gameId,
      });

      if (snapshot.status === 'canceled') {
        throw new TaskAbortedError(snapshot.error?.message || 'Upstream task was canceled');
      }
      if (snapshot.status === 'failed') {
        throw this.buildUpstreamTaskFailureError(snapshot, 'iteration');
      }

      await this.completeIterationTask({
        gameId,
        userId,
        feedback,
        conversationHistory,
        nextVersion,
        taskId,
        currentCode,
        responseData: snapshot.result || {},
        baseStatus: taskId
          ? this.getIterationBaseStatus(
            await this.prisma.generationTask.findUnique({
              where: { id: taskId },
              select: { metadata: true },
            }),
          )
          : GameStatus.draft,
      });
    } catch (error) {
      if (error instanceof TaskSupersededError) {
        if (taskId) {
          await this.persistTaskCancellation({
            id: taskId,
            gameId,
            userId,
            taskType: GenerationTaskType.pipeline_iterate,
            version: nextVersion,
            progressPct: STAGE_PCT.publishing,
          }, error.message);
        }
        return;
      }
      if (error instanceof TaskAbortedError) {
        this.logger.warn(`Iteration result discarded for task ${taskId || 'n/a'}: ${error.message}`);
        return;
      }

      await this.failIterationTask({
        gameId,
        userId,
        taskId,
        error,
      });
    }
  }

  private deriveTitle(gameSpec: any, description: string): string {
    // Try to extract a meaningful name from the game type
    const typeMap: Record<string, string> = {
      snake: '贪吃蛇', platformer: '跑酷冒险', shooter: '太空射击',
      puzzle: '益智谜题', rhythm: '音乐节奏', breakout: '打砖块',
      whack_a_mole: '打地鼠', racing: '极速竞赛', defense: '防御塔',
      card: '卡牌对决', rpg: '角色扮险', arcade: '街机游戏',
      '2048': '2048', runner: '无尽跑酷', space: '太空飞船',
    };
    const gameType = gameSpec?.game_type || '';
    if (typeMap[gameType]) return typeMap[gameType];
    if (gameType) return gameType.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());

    // Fallback: extract from first ~15 chars of description
    const desc = (description || '').replace(/^(做|创建|生成|制作|来)(一个|个)/, '').trim();
    if (desc.length > 2) return desc.substring(0, 15).replace(/[，。,.]$/, '');
    return '新游戏';
  }

  private emitProgress(
    userId: string,
    gameId: string,
    message: string,
    percentage: number,
    details?: Record<string, unknown>,
  ): void {
    this.wsGateway.emitGenerationProgress(userId, gameId, message, percentage, details);
  }

  private emitStage(
    userId: string,
    gameId: string,
    stage: string,
    details?: Record<string, unknown>,
  ): void {
    const pct = STAGE_PCT[stage] ?? 50;
    const label = STAGE_LABELS[stage] ?? stage;
    this.emitProgress(userId, gameId, label, pct, details);
  }

  private extractErrorMessage(error: any): string {
    const detail = error?.response?.data?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      return detail.message || detail.error || error?.message || 'unknown error';
    }
    return (
      detail ||
      error?.response?.data?.message ||
      error?.message ||
      'unknown error'
    );
  }

  private extractFailureContext(error: any): FailureContext {
    const detail = error?.response?.data?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      return {
        message:
          detail.message ||
          detail.error ||
          error?.message ||
          'unknown error',
        failedStage: detail.failed_stage || detail.failedStage || undefined,
        retryCount: Number(detail.retry_count ?? detail.retryCount ?? 0) || 0,
        fallback: detail.fallback || undefined,
        failureFamily: detail.failure_family || detail.failureFamily || undefined,
        primaryArtifactId: detail.primary_artifact_id || detail.primaryArtifactId || undefined,
      };
    }

    return {
      message: this.extractErrorMessage(error),
      failedStage: undefined,
      retryCount: 0,
      fallback: undefined,
      failureFamily: typeof error?.failure_family === 'string'
        ? error.failure_family
        : (typeof error?.failureFamily === 'string' ? error.failureFamily : undefined),
      primaryArtifactId: typeof error?.primary_artifact_id === 'string'
        ? error.primary_artifact_id
        : (typeof error?.primaryArtifactId === 'string' ? error.primaryArtifactId : undefined),
    };
  }

  private logStructuredFailure(event: string, payload: Record<string, unknown>): void {
    this.logger.error(`${event} ${JSON.stringify(payload)}`);
  }

  private async persistFailureState(params: {
    gameId: string;
    failedStage: string;
    failedReason: string;
    retryCount: number;
    status?: GameStatus;
    refundConsumedAccess?: boolean;
  }): Promise<void> {
    const {
      gameId,
      failedStage,
      failedReason,
      retryCount,
      status,
      refundConsumedAccess,
    } = params;

    await this.prisma.$transaction(async (tx) => {
      const game = await tx.game.findUnique({
        where: { id: gameId },
        select: {
          id: true,
          authorId: true,
          accessGrantSource: true,
          accessGrantSubscriptionId: true,
        },
      });

      if (!game) {
        throw new NotFoundException('Game not found');
      }

      const refundApplied = refundConsumedAccess
        ? await this.refundConsumedGenerationAccess(tx, game)
        : false;

      await tx.game.update({
        where: { id: gameId },
        data: {
          ...(status ? { status } : {}),
          failedStage,
          failedReason,
          retryCount,
          lastErrorAt: new Date(),
          ...(refundApplied
            ? {
                canPlay: false,
                requireSubscription: true,
                accessGrantSource: GameAccessGrantSource.none,
                accessGrantSubscriptionId: null,
              }
            : {}),
        },
      });
    });
  }

  private async refundConsumedGenerationAccess(
    tx: Prisma.TransactionClient,
    game: {
      id: string;
      authorId: string;
      accessGrantSource: GameAccessGrantSource;
      accessGrantSubscriptionId: string | null;
    },
  ): Promise<boolean> {
    if (game.accessGrantSource === GameAccessGrantSource.free_quota) {
      const result = await tx.userQuota.updateMany({
        where: {
          userId: game.authorId,
          usedFreeQuota: { gt: 0 },
        },
        data: {
          usedFreeQuota: { decrement: 1 },
        },
      });
      return result.count > 0;
    }

    if (
      game.accessGrantSource === GameAccessGrantSource.subscription_quota
      && game.accessGrantSubscriptionId
    ) {
      const result = await tx.userSubscription.updateMany({
        where: {
          id: game.accessGrantSubscriptionId,
          usedThisPeriod: { gt: 0 },
        },
        data: {
          usedThisPeriod: { decrement: 1 },
        },
      });
      return result.count > 0;
    }

    return false;
  }

  private async persistGeneratedGameResult(params: {
    gameId: string;
    userId: string;
    taskId?: string;
    version: number;
    htmlCode: string;
    previewUrl: string;
    metadata: any;
    gameTitle?: string;
    updateData: any;
  }): Promise<void> {
    const {
      gameId,
      userId,
      taskId,
      version,
      htmlCode,
      previewUrl,
      metadata,
      gameTitle,
      updateData,
    } = params;

    const maxAttempts = 3;
    let lastError: unknown;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        const currentGame = await this.prisma.game.findUnique({
          where: { id: gameId },
          select: {
            version: true,
            status: true,
          },
        });

        if (!currentGame) {
          throw new NotFoundException('Game not found');
        }

        if ((currentGame.version || 0) > version) {
          throw new TaskSupersededError(
            `Task result discarded because a newer game version (v${currentGame.version}) already exists`,
          );
        }

        let existingBundle = await this.bundleService.getBundle(gameId, version);
        const existingOwnerTaskId = typeof existingBundle?.metadata?.generationTaskId === 'string'
          ? existingBundle.metadata.generationTaskId
          : null;
        if (taskId && existingOwnerTaskId && existingOwnerTaskId !== taskId) {
          throw new TaskSupersededError(
            `Task result discarded because version ${version} was already persisted by another task`,
          );
        }

        if (!existingBundle) {
          try {
            await this.bundleService.saveBundle({
              gameId,
              version,
              htmlCode,
              cssCode: '',
              jsCode: '',
              metadata: taskId
                ? {
                    ...metadata,
                    generationTaskId: taskId,
                  }
                : metadata,
              previewUrl,
            });
          } catch (error: any) {
            if (!(error instanceof Prisma.PrismaClientKnownRequestError) || error.code !== 'P2002') {
              throw error;
            }

            existingBundle = await this.bundleService.getBundle(gameId, version);
            const conflictOwnerTaskId = typeof existingBundle?.metadata?.generationTaskId === 'string'
              ? existingBundle.metadata.generationTaskId
              : null;
            if (taskId && conflictOwnerTaskId && conflictOwnerTaskId !== taskId) {
              throw new TaskSupersededError(
                `Task result discarded because version ${version} was already persisted by another task`,
              );
            }
            if (!existingBundle) {
              throw error;
            }
          }
        }

        const updateResult = await this.prisma.game.updateMany({
          where: {
            id: gameId,
            version: currentGame.version,
            status: currentGame.status,
          },
          data: {
            ...updateData,
            ...(gameTitle ? { title: gameTitle } : {}),
          },
        });

        if (updateResult.count !== 1) {
          const latestGame = await this.prisma.game.findUnique({
            where: { id: gameId },
            select: {
              version: true,
              status: true,
            },
          });

          if (latestGame && (latestGame.version || 0) > version) {
            throw new TaskSupersededError(
              `Task result discarded because a newer game version (v${latestGame.version}) already exists`,
            );
          }

          throw new Error('Game state changed while publishing generated result');
        }

        this.wsGateway.emitGenerationComplete(
          userId,
          gameId,
          this.buildAuthorPreviewUrls(gameId, userId).previewUrl,
        );
        return;
      } catch (error) {
        lastError = error;
        if (attempt < maxAttempts) {
          this.emitProgress(
            userId,
            gameId,
            `发布生成结果失败，重试中（${attempt}/${maxAttempts - 1}）`,
            STAGE_PCT.publishing,
            {
              stage: 'publishing',
              retry: attempt,
              maxRetries: maxAttempts - 1,
              attempt: attempt + 1,
              maxAttempts,
              error: this.extractErrorMessage(error),
            },
          );
          await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
          continue;
        }
      }
    }

    this.logStructuredFailure('PIPELINE_PUBLISH_FAILURE', {
      gameId,
      userId,
      stage: 'publishing',
      retryCount: maxAttempts - 1,
      error: this.extractErrorMessage(lastError),
    });
    throw lastError;
  }

  async findById(id: string): Promise<any> {
    try {
      const game = await this.prisma.game.findUnique({
        where: { id },
        include: {
          author: {
            select: {
              id: true,
              username: true,
              displayName: true,
              avatarUrl: true,
            },
          },
        },
      });

      if (!game) {
        throw new NotFoundException('Game not found');
      }

      if (!this.isPubliclyVisibleGame(game)) {
        throw new NotFoundException('Game not found');
      }

      return this.attachPreviewUrl(game);
    } catch (error) {
      this.logger.error(`Failed to find game: ${error.message}`);
      throw error;
    }
  }

  async findByAuthor(
    authorId: string,
    page: number = 1,
    limit: number = 10,
  ): Promise<any> {
    try {
      const skip = (page - 1) * limit;

      const [games, total] = await Promise.all([
        this.prisma.game.findMany({
          where: {
            authorId,
            status: { not: 'banned' },
          },
          include: {
            author: {
              select: {
                id: true,
                username: true,
                avatarUrl: true,
              },
            },
          },
          skip,
          take: limit,
          orderBy: { createdAt: 'desc' },
        }),
        this.prisma.game.count({
          where: {
            authorId,
            status: { not: 'banned' },
          },
        }),
      ]);

      return {
        data: await this.attachAuthorPreviewUrls(games, authorId),
        pagination: {
          page,
          limit,
          total,
          totalPages: Math.ceil(total / limit),
        },
      };
    } catch (error) {
      this.logger.error(`Failed to find games by author: ${error.message}`);
      throw error;
    }
  }

  async getPlayData(id: string, previewToken?: string): Promise<string> {
    try {
      const game = await this.prisma.game.findUnique({ where: { id } });
      if (!game) {
        throw new NotFoundException('Game not found');
      }

      const hasPrivilegedPreviewAccess =
        this.hasAdminPreviewAccess(game.id, previewToken)
        || Boolean(this.resolveAuthorPreviewAccess(game, previewToken));

      if (hasPrivilegedPreviewAccess) {
        if (game.status === 'banned') {
          throw new ForbiddenException('This game is unavailable');
        }
        if (game.status === 'generating') {
          throw new BadRequestException('Game is still generating');
        }
        if (game.status === 'failed') {
          throw new BadRequestException('Game generation failed');
        }
      } else {
        this.assertPublicPreviewAllowed(game);
      }

      const bundle = await this.loadBundleForGame(game, {
        preferLiveVersion: !hasPrivilegedPreviewAccess,
      });
      this.ensurePersistableGeneratedHtml(bundle.htmlCode);

      await this.statsService.incrementPlayCount(id);

      return bundle.htmlCode;
    } catch (error) {
      this.logger.error(`Failed to get play data: ${error.message}`);
      throw error;
    }
  }

  async getPlayableHtml(id: string, userId: string): Promise<string> {
    try {
      const game = await this.prisma.game.findUnique({ where: { id } });
      if (!game) {
        throw new NotFoundException('Game not found');
      }

      if (game.status === 'banned') {
        throw new ForbiddenException('This game is unavailable');
      }
      if (game.status === 'generating') {
        throw new BadRequestException('Game is still generating');
      }
      if (game.status === 'failed') {
        throw new BadRequestException('Game generation failed');
      }

      const isPublished = game.status === 'published';
      const isAuthor = game.authorId === userId;

      if (isPublished && !isAuthor && (game.visibility || 'public') !== 'public') {
        throw new ForbiddenException('This game is private');
      }

      if (!isPublished) {
        if (!isAuthor) {
          throw new ForbiddenException('You do not have permission to play this game');
        }
      }

      const bundle = await this.loadBundleForGame(game, { preferLiveVersion: !isAuthor });
      this.ensurePersistableGeneratedHtml(bundle.htmlCode);
      await this.statsService.incrementPlayCount(id);
      return bundle.htmlCode;
    } catch (error) {
      this.logger.error(`Failed to get playable html: ${error.message}`);
      throw error;
    }
  }

  async unlock(id: string, userId: string): Promise<any> {
    return this.prisma.$transaction(async (tx) => {
      await this.markExpiredSubscriptions(tx, userId);

      const game = await tx.game.findUnique({
        where: { id },
      });

      if (!game) {
        throw new NotFoundException('Game not found');
      }

      if (game.authorId !== userId) {
        throw new ForbiddenException('You do not have permission to unlock this game');
      }

      const quota = await this.ensureUserQuota(tx, userId);
      const currentSubscription = await this.findActiveSubscription(tx, userId);

      if (game.canPlay) {
        return {
          unlocked: true,
          canPlay: true,
          quotaRemaining: this.computeQuotaRemaining(quota, currentSubscription),
        };
      }

      if (!currentSubscription) {
        throw new ForbiddenException('No active subscription available to unlock this game');
      }

      if (currentSubscription.usedThisPeriod >= currentSubscription.quotaThisPeriod) {
        throw new ForbiddenException('Your current subscription quota has been exhausted');
      }

      const updatedSubscription = await tx.userSubscription.update({
        where: { id: currentSubscription.id },
        data: {
          usedThisPeriod: { increment: 1 },
        },
      });

      await tx.game.update({
        where: { id },
        data: {
          canPlay: true,
          requireSubscription: false,
          accessGrantSource: GameAccessGrantSource.subscription_unlock,
          accessGrantSubscriptionId: currentSubscription.id,
        },
      });

      return {
        unlocked: true,
        canPlay: true,
        quotaRemaining: this.computeQuotaRemaining(quota, updatedSubscription),
      };
    });
  }

  async publish(id: string, userId: string, dto: PublishGameDto): Promise<any> {
    try {
      const game = await this.prisma.game.findUnique({ where: { id } });
      if (!game) throw new NotFoundException('Game not found');
      if (game.authorId !== userId) {
        throw new ForbiddenException('You do not have permission to publish this game');
      }
      await this.ensureNoActiveTaskForGame(id);
      const bundle = await this.bundleService.getLatestBundle(id);
      this.assertGamePublishable(game, bundle);
      const requestedVisibility = dto.visibility === 'private' || dto.visibility === 'public'
        ? dto.visibility
        : undefined;
      const publishVisibility = requestedVisibility
        ?? (
          game.status === GameStatus.published
            ? (game.visibility || 'public')
            : 'public'
        );
      const liveVersion = Number.isFinite(bundle?.version) && Number(bundle.version) > 0
        ? Number(bundle.version)
        : Number.isFinite(game.version) && Number(game.version) > 0
          ? Number(game.version)
          : 1;

      const publishedGame = await this.prisma.game.update({
        where: { id },
        data: {
          title: dto.title || game.title,
          description: dto.description || game.description,
          tags: dto.tags ?? game.tags ?? [],
          gameType: dto.gameType || game.gameType,
          version: liveVersion,
          status: 'published',
          visibility: publishVisibility,
          publishedAt: new Date(),
        },
        include: {
          author: {
            select: { id: true, username: true, avatarUrl: true },
          },
        },
      });

      await this.invalidateFeedCache();
      return this.attachPreviewUrl(publishedGame);
    } catch (error) {
      this.logger.error(`Failed to publish game: ${error.message}`);
      throw error;
    }
  }

  async updateSettings(id: string, userId: string, settings: { visibility?: string; allowComments?: boolean; allowFork?: boolean }): Promise<any> {
    try {
      const game = await this.prisma.game.findUnique({ where: { id } });
      if (!game) throw new NotFoundException('Game not found');
      if (game.authorId !== userId) {
        throw new ForbiddenException('You do not have permission to update this game');
      }

      const data: any = {};
      if (settings.visibility !== undefined) data.visibility = settings.visibility;
      if (settings.allowComments !== undefined) data.allowComments = settings.allowComments;
      if (settings.allowFork !== undefined) data.allowFork = settings.allowFork;

      const updated = await this.prisma.game.update({ where: { id }, data });
      return { id: updated.id, visibility: updated.visibility, allowComments: updated.allowComments, allowFork: updated.allowFork };
    } catch (error) {
      this.logger.error(`Failed to update game settings: ${error.message}`);
      throw error;
    }
  }

  async delete(id: string, userId: string): Promise<void> {
    try {
      const game = await this.prisma.game.findUnique({ where: { id } });
      if (!game) throw new NotFoundException('Game not found');
      if (game.authorId !== userId) {
        throw new ForbiddenException('You do not have permission to delete this game');
      }

      await this.terminateActiveTasksForGame(id, {
        reason: 'Task canceled because the game was deleted',
      });

      await this.prisma.game.update({
        where: { id },
        data: { status: 'banned' },
      });
      await this.invalidateFeedCache();
    } catch (error) {
      this.logger.error(`Failed to delete game: ${error.message}`);
      throw error;
    }
  }

  async iterate(id: string, userId: string, dto: IterateGameDto): Promise<any> {
    try {
      const game = await this.prisma.game.findUnique({ where: { id } });
      if (!game) throw new NotFoundException('Game not found');
      if (game.authorId !== userId) {
        throw new ForbiddenException('You do not have permission to iterate on this game');
      }
      if (game.status === GameStatus.banned) {
        throw new ForbiddenException('This game is unavailable');
      }
      if (game.status === GameStatus.failed) {
        throw new BadRequestException('Cannot iterate a failed game');
      }
      if (game.status === GameStatus.generating) {
        throw new ConflictException('Another generation task is already running for this game');
      }
      await this.ensureNoActiveTaskForGame(id);

      const version = (game.version || 1) + 1;

      const bundle = await this.bundleService.getLatestBundle(id);
      if (!this.isBundlePlayable(bundle)) {
        throw new BadRequestException('Cannot iterate a game without a playable bundle');
      }
      const bundleHistory = await this.bundleService.getBundleHistory(id);
      const latestStoredConversation = [...bundleHistory]
        .reverse()
        .map((bundleVersion) => (
          Array.isArray(bundleVersion.metadata?.aiConversation)
            ? bundleVersion.metadata.aiConversation
              .filter((item: any) => item && typeof item.role === 'string' && typeof item.content === 'string')
              .map((item: any) => ({ role: item.role, content: item.content }))
            : []
        ))
        .find((conversation) => conversation.length > 0);

      const conversationHistory = latestStoredConversation || bundleHistory.flatMap((bundleVersion) => {
        const feedback = String(bundleVersion.metadata?.feedback || '').trim();
        return feedback ? [{ role: 'user', content: feedback }] : [];
      });

      const latestTask = await Promise.resolve(this.generationTaskService.getLatestTaskForGame(id, userId))
        .catch(() => null);
      const executionRegion = this.resolveExecutionRegion(dto.regionHint || latestTask?.region);
      const pipelineVersion = this.resolvePipelineVersion({
        entrypoint: 'iterate',
        userId,
        executionRegion,
      });
      const timeoutS = this.resolveTaskTimeoutForPipelineVersion(dto.timeoutS, pipelineVersion);
      const promptBundleSnapshot = pipelineVersion === 'v2'
        ? await this.buildPromptBundleSnapshot('iterate', this.inferRuntimeProfileHint(game.gameType, dto.feedback))
        : null;
      const runtimeContract = pipelineVersion === 'v2'
        ? await this.buildDefaultRuntimeContract('iterate', this.inferRuntimeProfileHint(game.gameType, dto.feedback))
        : null;
      const baseStatus = this.getIterationBaseStatus(null, game);
      const inFlightStatus = this.getInFlightIterationStatus(baseStatus);

      const task = await this.prisma.$transaction(async (tx) => {
        const claimWhere: Record<string, unknown> = {
          id,
          authorId: userId,
          version: game.version,
          status: game.status,
        };
        if (game.updatedAt) {
          claimWhere.updatedAt = game.updatedAt;
        }

        const claimed = await tx.game.updateMany({
          where: claimWhere,
          data: {
            status: inFlightStatus,
            failedStage: null,
            failedReason: null,
            retryCount: 0,
            lastErrorAt: null,
          },
        });

        if (claimed.count !== 1) {
          throw new ConflictException('Another generation task is already running for this game');
        }

        return this.generationTaskService.createTask({
          gameId: id,
          userId,
          taskType: GenerationTaskType.pipeline_iterate,
          region: executionRegion,
          timeoutS,
          version,
          pipelineVersion,
          promptBundleId: promptBundleSnapshot?.bundle_id ?? null,
          promptBundleVersion: promptBundleSnapshot?.bundle_version ?? null,
          runtimeProfile: runtimeContract?.runtime_profile ?? null,
          contractVersion: runtimeContract?.version ?? null,
          metadata: {
            feedback: dto.feedback,
            region: executionRegion,
            conversation: conversationHistory,
            baseStatus,
            pipelineVersion,
          },
          client: tx,
        });
      });

      setImmediate(() => {
        void this.executeIterationTask(
          id,
          userId,
          dto.feedback,
          version,
          conversationHistory,
          bundle?.htmlCode || '',
          timeoutS,
          task.id,
          executionRegion,
          {
            pipelineVersion,
            promptBundleSnapshot,
            runtimeContract,
            game,
          },
        ).catch((error) => {
          this.logger.error(
            `Background iteration task crashed for game ${id}: ${this.extractErrorMessage(error)}`,
          );
        });
      });

      const generationTask = this.withAuthorPreviewUrls(
        this.generationTaskService.toTaskSummary(task),
        userId,
      );

      return {
        ...generationTask,
        gameId: id,
        version,
        status: 'iterating',
        generationTask,
      };
    } catch (error) {
      this.logger.error(`Failed to iterate game: ${error.message}`);
      throw error;
    }
  }

  /**
   * Calls Stage 07: iteration engine (incremental code modification).
   */
  private async runIteration(
    gameId: string,
    userId: string,
    feedback: string,
    nextVersion: number,
    conversationHistory: any[],
    currentCode: string,
    timeoutS?: number,
    taskId?: string,
    executionRegion?: string,
  ): Promise<void> {
    return this.executeIterationTask(
      gameId,
      userId,
      feedback,
      nextVersion,
      conversationHistory,
      currentCode,
      timeoutS,
      taskId,
      executionRegion,
    );
    /*
    try {
      const resolvedTimeoutS = this.resolvePipelineTimeout(timeoutS);
      const aiEngineBaseUrl = await this.resolveAiEngineEndpoint(executionRegion);
      if (taskId) {
        await this.generationTaskService.markRunning(taskId!);
      }
      const response = await withRetry(() =>
        axios.post(
          `${aiEngineBaseUrl}/api/v1/ai/pipeline/iterate`,
          {
            game_id: gameId,
            feedback,
            user_id: userId,
            conversation: conversationHistory,
            current_code: currentCode,
            region: this.resolveExecutionRegion(executionRegion),
            timeout_s: resolvedTimeoutS,
            task_id: taskId,
          },
          { timeout: this.buildUpstreamTimeoutMs(resolvedTimeoutS) },
        )
        ,
        {
          retryOnHttpResponse: false,
        }
      );

      const {
        html_code: htmlCode = currentCode,
        iteration_type: iterationType = 'element_change',
        generation_time_ms: genTimeMs = 0,
        qa_retries: qaRetries = 0,
        iteration_retries: iterationRetries = 0,
      } = response.data;
      this.ensurePersistableGeneratedHtml(htmlCode);
      await this.assertTaskCanPersistResult(taskId, gameId);

      const bundlePreviewUrl = this.buildPreviewUrl(gameId);

      this.emitStage(userId, gameId, 'publishing', {
        stage: 'publishing',
        attempt: 1,
        maxAttempts: 3,
      });

      await this.persistGeneratedGameResult({
        gameId,
        userId,
        version: nextVersion,
        htmlCode,
        previewUrl: bundlePreviewUrl,
        metadata: {
          feedback,
          iterationType,
          genTimeMs,
          qaRetries,
          iterationRetries,
          aiConversation: [
            ...conversationHistory,
            { role: 'user', content: feedback },
          ],
        },
        updateData: {
          version: nextVersion,
          status: 'draft',
          failedStage: null,
          failedReason: null,
          retryCount: 0,
          lastErrorAt: null,
        },
      });

      if (taskId) {
        await this.generationTaskService.markSucceeded({
          taskId: taskId!,
          previewUrl: bundlePreviewUrl,
          resultSummary: {
            feedback,
            iterationType,
            generationTimeMs: genTimeMs,
            qaRetries,
            iterationRetries,
            version: nextVersion,
          },
        });
      }

      this.wsGateway.emitGenerationProgress(userId, gameId, '迭代完成', 100);
    } catch (error) {
      if (error instanceof TaskAbortedError) {
        this.logger.warn(`Iteration result discarded for task ${taskId || 'n/a'}: ${error.message}`);
        return;
      }

      const failure = this.extractFailureContext(error);
      this.logger.error(`Iteration failed for game ${gameId}: ${failure.message}`);
      this.logStructuredFailure('PIPELINE_ITERATION_FAILURE', {
        gameId,
        userId,
        stage: failure.failedStage || 'iteration',
        retryCount: failure.retryCount,
        error: failure.message,
      });
      await this.persistFailureState({
        gameId,
        failedStage: failure.failedStage || 'iteration',
        failedReason: failure.message,
        retryCount: failure.retryCount,
      });
      if (taskId) {
        await this.generationTaskService.markFailed({
          taskId: taskId!,
          failedStage: failure.failedStage || 'iteration',
          errorMessage: failure.message,
          retryCount: failure.retryCount,
          fallback: failure.fallback,
          timedOut: this.isTimeoutError(error),
        }).catch((taskError) => {
          this.logger.warn(`Failed to update iteration task ${taskId}: ${taskError.message}`);
        });
      }
      this.wsGateway.emitGenerationError(userId, gameId, failure.message, {
        stage: failure.failedStage || 'iteration',
        retryCount: failure.retryCount,
        fallback: failure.fallback,
      });
      this.wsGateway.emitNotification(userId, {
        type: 'error',
        message: `Game iteration failed: ${failure.message}`,
        gameId,
      });
    }
    */
  }

  async getGenerationStatus(id: string, userId: string): Promise<any> {
    const game = await this.prisma.game.findUnique({
      where: { id },
      select: {
        id: true,
        authorId: true,
        status: true,
        version: true,
        publishedAt: true,
        failedStage: true,
        failedReason: true,
        retryCount: true,
        lastErrorAt: true,
        canPlay: true,
        requireSubscription: true,
      },
    });

    if (!game) {
      throw new NotFoundException('Game not found');
    }
    if (game.authorId !== userId) {
      throw new ForbiddenException('You do not have permission to view this task');
    }

    const latestTask = await this.generationTaskService.getLatestTaskForGame(id, userId);
    if (latestTask) {
      const resolvedTask = await this.reconcileGenerationTask({
        ...latestTask,
        game,
      });
      const effectiveGame = await this.prisma.game.findUnique({
        where: { id },
        select: {
          id: true,
          authorId: true,
          status: true,
          version: true,
          publishedAt: true,
          failedStage: true,
          failedReason: true,
          retryCount: true,
          lastErrorAt: true,
          canPlay: true,
          requireSubscription: true,
        },
      }) || game;
      return this.withAuthorPreviewUrls({
        ...this.generationTaskService.toTaskSummary(resolvedTask || latestTask),
        stage: (resolvedTask || latestTask).progressStage || (resolvedTask || latestTask).failedStage || 'queued',
        gameId: id,
        version: (resolvedTask || latestTask).version ?? (effectiveGame.version || 1),
        previewUrl: (resolvedTask || latestTask).previewUrl || this.buildPreviewUrl(id),
        gameStatus: effectiveGame.status,
        canPlay: effectiveGame.canPlay,
        requireSubscription: effectiveGame.requireSubscription,
        failedStage: (resolvedTask || latestTask).failedStage || effectiveGame.failedStage,
        failedReason: (resolvedTask || latestTask).errorMessage || effectiveGame.failedReason,
        retryCount: (resolvedTask || latestTask).retryCount ?? (effectiveGame.retryCount || 0),
        lastErrorAt: effectiveGame.lastErrorAt,
      }, userId);
    }

    const taskStatus = game.status === 'failed'
      ? 'failed'
      : game.status === 'generating'
        ? 'running'
        : game.status === 'banned'
          ? 'canceled'
        : 'succeeded';
    const stage = game.status === 'failed'
      ? (game.failedStage || 'failed')
      : game.status === 'generating'
        ? 'generating'
        : game.status === 'banned'
          ? 'canceled'
        : 'completed';

    return this.withAuthorPreviewUrls({
      taskId: `${id}:pipeline`,
      taskType: 'pipeline_run',
      status: taskStatus,
      stage,
      gameId: id,
      version: game.version || 1,
      wsChannel: `game:${id}`,
      pollUrl: `/api/v1/games/${id}/generation-status`,
      previewUrl: this.buildPreviewUrl(id),
      gameStatus: game.status,
      canPlay: game.canPlay,
      requireSubscription: game.requireSubscription,
      failedStage: game.failedStage,
      failedReason: game.failedReason,
      retryCount: game.retryCount || 0,
      lastErrorAt: game.lastErrorAt,
    }, userId);
  }

  async getTask(taskId: string, userId: string): Promise<any> {
    const task = await this.generationTaskService.getTaskForUser(taskId, userId);
    const resolvedTask = await this.reconcileGenerationTask(task);
    return this.withAuthorPreviewUrls(
      this.generationTaskService.toTaskSummary(resolvedTask || task),
      userId,
    );
  }

  async getTaskArtifacts(taskId: string, userId: string, limit?: number): Promise<any> {
    return this.generationTaskService.listArtifactsForTask(taskId, userId, limit);
  }

  async getTaskEvents(taskId: string, userId: string, limit?: number): Promise<any> {
    return this.generationTaskService.listTaskEvents(taskId, userId, limit);
  }

  async cancelTask(taskId: string, userId: string): Promise<any> {
    return this.terminateTask(taskId, { userId });
  }

  async getShareData(id: string): Promise<any> {
    const game = await this.prisma.game.findUnique({
      where: { id },
      include: {
        author: {
          select: { username: true },
        },
      },
    });

    if (!game) throw new NotFoundException('Game not found');
    if (!this.isPubliclyVisibleGame(game)) throw new NotFoundException('Game not found');

    const gameUrl = `${this.getPublicBaseUrl()}/games/${id}`;

    return {
      title: game.title,
      description: game.description,
      thumbnailUrl: game.thumbnailUrl,
      url: gameUrl,
      author: game.author?.username || '',
      stats: {
        plays: Number(game.playCount || 0),
        likes: Number(game.likeCount || 0),
        qualityScore: game.qualityScore ?? 0,
      },
    };
  }

  async getGamesByStatus(
    status: string,
    page: number = 1,
    limit: number = 10,
    search?: string,
  ): Promise<any> {
    try {
      const skip = (page - 1) * limit;
      const where: Prisma.GameWhereInput = {
        status: status as GameStatus,
      };

      if (status === GameStatus.published) {
        where.visibility = 'public';
      }

      if (search?.trim()) {
        const q = search.trim();
        where.OR = [
          { title: { contains: q } },
          { description: { contains: q } },
          { author: { username: { contains: q } } },
          { author: { displayName: { contains: q } } },
        ];
      }

      const [games, total] = await Promise.all([
        this.prisma.game.findMany({
          where,
          include: {
            author: {
              select: { id: true, username: true, displayName: true, avatarUrl: true },
            },
          },
          skip,
          take: limit,
          orderBy: { createdAt: 'desc' },
        }),
        this.prisma.game.count({ where }),
      ]);

      return {
        data: await this.attachPreviewUrls(games),
        pagination: {
          page,
          limit,
          total,
          totalPages: Math.ceil(total / limit),
        },
      };
    } catch (error) {
      this.logger.error(`Failed to get games by status: ${error.message}`);
      throw error;
    }
  }
}
