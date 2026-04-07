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
import { createHash, randomUUID } from 'crypto';
import axios from 'axios';
import {
  GameAccessGrantSource,
  GenerationTaskEventType,
  GenerationTaskStatus,
  GenerationTaskType,
  GameStatus,
  Prisma,
  UserSubscriptionStatus,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { BundleService } from '../bundle/bundle.service';
import { StatsService } from '../stats/stats.service';
import { GameWebSocketGateway } from '../websocket/websocket.gateway';
import {
  CreateGameDto,
  CreateGameGenerationTier,
  CreateGameOrientation,
  PublishGameDto,
  IterateGameDto,
} from './dto';
import { GenerationTaskService } from './generation-task.service';
import { GenerationQueueService } from './generation-queue.service';
import {
  GENERATION_QUEUE_JOB_PIPELINE_ITERATE,
  GENERATION_QUEUE_JOB_PIPELINE_RUN,
} from './generation-queue.types';
import {
  TIMEOUT_CONFIG_CATALOG,
  TIMEOUT_CONFIG_CATALOG_BY_KEY,
} from './catalogs/timeout-catalog';
import { normalizeGameType } from './game-type-catalog';
import {
  DEFAULT_RUNTIME_PROFILE_ID,
  normalizeRuntimeProfileId,
  runtimeProfileLookupCandidates,
} from './runtime-profile-ids';
import {
  buildIntentBuildSnapshot,
  normalizeIntentBuildSnapshot,
} from './intent-build.util';

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

type RuntimeOrientation = 'portrait_first' | 'landscape_first';
type GenerationTier = CreateGameGenerationTier;

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

interface CoverLinkOptions extends PreviewLinkOptions {
  taskId?: string;
  version?: number | string;
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

interface SourceBundleRevisionPayload {
  version?: number | null;
  generated_at?: string | null;
  feedback?: string | null;
  iteration_type?: string | null;
  summary?: string | null;
}

interface SourceBundleContextPayload {
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

interface DirectIntentAnalyzeResponsePayload {
  slots?: Record<string, unknown>;
  missing_required?: string[];
  slot_fill_pct?: number;
  ready_to_generate?: boolean;
}

interface DirectIntentSpecResponsePayload {
  spec?: Record<string, unknown> | null;
  missing_required?: string[];
  slot_fill_pct?: number;
}

interface CreateGameCommand extends CreateGameDto {
  sourceSpec?: Record<string, unknown> | null;
  creationSessionId?: string | null;
  entryMode?: string | null;
  sourceGameId?: string | null;
}

interface CreateExecutionOptions {
  pipelineVersion?: PipelineVersion;
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

interface IterateExecutionOptions {
  pipelineVersion?: PipelineVersion;
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

interface UpstreamAsyncTaskHandle {
  task_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled';
  poll_url?: string;
  cancel_url?: string;
}

interface ResolvedUpstreamAsyncTaskHandle extends UpstreamAsyncTaskHandle {
  aiEngineBaseUrl: string;
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
    private generationQueueService: GenerationQueueService,
  ) {
    this.aiEngineUrl = this.configService.get<string>(
      'AI_ENGINE_URL',
      'http://localhost:8000',
    );
  }

  async onModuleInit(): Promise<void> {
    await this.refreshTimeoutConfigCache().catch((error) => {
      this.logger.warn(`Failed to warm timeout config cache on init: ${error?.message || error}`);
    });
    if (!this.timeoutConfigLoadedAt) {
      await this.syncBackgroundExecutionMode().catch((error) => {
        this.logger.warn(`Failed to initialize background execution mode on init: ${error?.message || error}`);
      });
    }
  }

  onModuleDestroy(): void {
    this.stopActiveTaskSweep();
  }

  private async syncBackgroundExecutionMode(): Promise<void> {
    const queueReady = await this.generationQueueService.ensureOperational().catch((error) => {
      this.logger.warn(`Failed to initialize generation queue: ${error?.message || error}`);
      return false;
    });
    if (queueReady) {
      this.stopActiveTaskSweep();
      const scheduled = await this.generationQueueService.ensureActiveTaskSweepScheduler(
        this.getActiveTaskSweepIntervalMs(),
      );
      if (scheduled) {
        return;
      }
    }
    this.startActiveTaskSweep();
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
    void this.syncBackgroundExecutionMode().catch((error) => {
      this.logger.warn(`Failed to resync active task sweep mode: ${error?.message || error}`);
    });
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
        await this.syncBackgroundExecutionMode();
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

  private normalizeOptionalString(value: unknown): string | undefined {
    const normalized = String(value || '').trim();
    return normalized || undefined;
  }

  private buildCreateIntentVariationSeed(params: {
    userId: string;
    description: string;
    title?: string | null;
    generationTier: GenerationTier;
    entryMode?: string | null;
  }): string {
    const fingerprint = JSON.stringify({
      userId: params.userId,
      description: params.description.trim(),
      title: this.normalizeOptionalString(params.title) || null,
      generationTier: params.generationTier,
      entryMode: this.normalizeOptionalString(params.entryMode) || 'create',
    });
    return createHash('sha256').update(fingerprint).digest('hex').slice(0, 24);
  }

  private buildCreateIntentBuild(params: {
    title?: string | null;
    description: string;
    entryMode?: string | null;
    generationTier: GenerationTier;
    sourceSpec?: Record<string, unknown> | null;
  }) {
    return buildIntentBuildSnapshot({
      title: this.normalizeOptionalString(params.title),
      initialPrompt: params.description,
      entryMode: this.normalizeOptionalString(params.entryMode) || 'create',
      generationTier: params.generationTier,
      sourceSpec: params.sourceSpec || null,
    });
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

    if (/(classroom|teacher|lesson|quiz|worksheet|practice question|practice quiz|learning game|teaching|knowledge point|课堂|教学|老师|练习题|知识点|问答|测验|小测|学习游戏|教学游戏)/.test(text)) {
      return 'puzzle_grid';
    }
    if (/(runner|race|racing|lane|endless runner|跑酷|赛道|lane runner)/.test(text)) {
      return 'casual_lane';
    }
    if (/(puzzle|grid|tile|match|merge|circuit|wire|battery|bulb|switch|connect|drag|assemble|拼图|消除|方块|电路|导线|电池|灯泡|开关|连接|拖拽|组装)/.test(text)) {
      return 'puzzle_grid';
    }
    if (/(shooter|shoot|top-down|top down|action|射击|飞船|弹幕|俯视|动作)/.test(text)) {
      return 'casual_action';
    }
    if (/(rhythm|timing|beat|music|节奏|音游|点按)/.test(text)) {
      return 'tap_challenge';
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

    const normalizedHint = normalizeRuntimeProfileId(profileHint);
    if (normalizedHint) {
      for (const candidate of runtimeProfileLookupCandidates(normalizedHint)) {
        const hintedProfile = profiles.find((profile) => profile.id === candidate);
        if (hintedProfile) {
          return {
            ...hintedProfile,
            id: normalizeRuntimeProfileId(hintedProfile.id) || DEFAULT_RUNTIME_PROFILE_ID,
          };
        }
      }
    }

    const selected = defaultProfile || profiles[0];
    return {
      ...selected,
      id: normalizeRuntimeProfileId(selected.id) || DEFAULT_RUNTIME_PROFILE_ID,
    };
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
        requires_canvas_2d: renderContract.requiresCanvas2D ?? false,
        allow_webgl: renderContract.allowWebgl ?? !(renderContract.requiresCanvas2D === true),
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
    generationTier: GenerationTier = 'standard',
  ): Promise<PromptBundleSnapshotPayload> {
    const bundle = await this.resolveActivePromptBundleIdentity();
    return {
      bundle_id: bundle.id,
      bundle_version: bundle.version,
      resolved_at: new Date().toISOString(),
      layers: {
        entrypoint,
        source: 'game-service',
        generation_tier: generationTier,
        ...(runtimeProfile ? { profile_few_shot: runtimeProfile } : {}),
      },
    };
  }

  private async buildDefaultRuntimeContract(
    entrypoint: PipelineEntrypoint,
    runtimeProfile?: string,
    requestedOrientation?: CreateGameOrientation,
    generationTier: GenerationTier = 'standard',
  ): Promise<RuntimeContractPayload> {
    const profile = await this.resolveRuntimeProfile(runtimeProfile);
    const normalizedContract = this.normalizeRuntimeContractSchema(profile.id, profile.contractSchema);

    return this.applyRequestedCreateOrientation({
      ...normalizedContract,
      metadata: {
        ...((normalizedContract.metadata as Record<string, unknown> | undefined) ?? {}),
        entrypoint,
        source: 'game-service',
        generation_tier: generationTier,
      },
    }, requestedOrientation);
  }

  private resolveRuntimeOrientation(
    orientation?: CreateGameOrientation | null,
  ): RuntimeOrientation | undefined {
    if (orientation === 'landscape') {
      return 'landscape_first';
    }
    if (orientation === 'portrait') {
      return 'portrait_first';
    }
    return undefined;
  }

  private normalizeRequestedOrientation(
    orientation?: unknown,
  ): CreateGameOrientation | undefined {
    if (orientation === 'landscape') {
      return 'landscape';
    }
    if (orientation === 'portrait') {
      return 'portrait';
    }
    return undefined;
  }

  private normalizeRequestedGenerationTier(
    generationTier?: unknown,
  ): GenerationTier | undefined {
    if (generationTier === 'safe') {
      return 'safe';
    }
    if (generationTier === 'showcase') {
      return 'showcase';
    }
    if (generationTier === 'standard') {
      return 'standard';
    }
    return undefined;
  }

  private inferRequestedOrientationFromText(
    description?: string | null,
    title?: string | null,
  ): CreateGameOrientation | undefined {
    const text = `${title ?? ''} ${description ?? ''}`.trim().toLowerCase();
    if (!text) {
      return undefined;
    }

    const hasLandscapeHint = [
      'landscape',
      'horizontal',
      'wide',
      '16:9',
      '横屏',
      '横版',
      '宽屏',
    ].some((token) => text.includes(token));
    const hasPortraitHint = [
      'portrait',
      'vertical',
      '9:16',
      '竖屏',
      '纵向',
    ].some((token) => text.includes(token));

    if (hasLandscapeHint && !hasPortraitHint) {
      return 'landscape';
    }
    if (hasPortraitHint && !hasLandscapeHint) {
      return 'portrait';
    }
    return undefined;
  }

  private resolveRequestedOrientationFromRuntime(
    orientation?: unknown,
  ): CreateGameOrientation | undefined {
    if (orientation === 'landscape_first') {
      return 'landscape';
    }
    if (orientation === 'portrait_first') {
      return 'portrait';
    }
    return undefined;
  }

  private resolveRuntimeOrientationFromContract(
    runtimeContract?: RuntimeContractPayload | null,
  ): RuntimeOrientation | undefined {
    if (!runtimeContract || typeof runtimeContract !== 'object') {
      return undefined;
    }

    const mobileLayout = runtimeContract.mobile_layout && typeof runtimeContract.mobile_layout === 'object'
      ? runtimeContract.mobile_layout as Record<string, unknown>
      : null;
    const canvas = runtimeContract.canvas && typeof runtimeContract.canvas === 'object'
      ? runtimeContract.canvas as Record<string, unknown>
      : null;
    const metadata = runtimeContract.metadata && typeof runtimeContract.metadata === 'object'
      ? runtimeContract.metadata as Record<string, unknown>
      : null;

    const runtimeOrientation = [
      mobileLayout?.orientation,
      canvas?.orientation,
      metadata?.orientation,
      metadata?.runtimeOrientation,
      metadata?.runtime_orientation,
    ].find((value) => value === 'portrait_first' || value === 'landscape_first');

    return runtimeOrientation as RuntimeOrientation | undefined;
  }

  private buildPersistedOrientationMetadata(params: {
    orientation?: CreateGameOrientation | null;
    runtimeContract?: RuntimeContractPayload | null;
  }): Record<string, unknown> {
    const requestedOrientation = this.normalizeRequestedOrientation(params.orientation)
      ?? this.resolveRequestedOrientationFromRuntime(
        this.resolveRuntimeOrientationFromContract(params.runtimeContract),
      );
    const runtimeOrientation = this.resolveRuntimeOrientationFromContract(params.runtimeContract)
      ?? this.resolveRuntimeOrientation(requestedOrientation);

    return {
      ...(requestedOrientation ? { requestedOrientation } : {}),
      ...(runtimeOrientation ? { runtimeOrientation } : {}),
    };
  }

  private buildPersistedGenerationTierMetadata(
    generationTier?: GenerationTier | null,
  ): Record<string, unknown> {
    const normalizedGenerationTier = this.normalizeRequestedGenerationTier(generationTier) || 'standard';
    return {
      generationTier: normalizedGenerationTier,
    };
  }

  private resolveNextIterationVersion(params: {
    gameVersion?: number | null;
    latestBundle?: { version?: number | null } | null;
    bundleHistory?: Array<{ version?: number | null } | null> | null;
  }): number {
    const versions = [
      Number(params.gameVersion || 0),
      Number(params.latestBundle?.version || 0),
      ...((params.bundleHistory || []).map((bundle) => Number(bundle?.version || 0))),
    ].filter((value) => Number.isFinite(value) && value >= 0);

    return Math.max(0, ...versions) + 1;
  }

  private applyRequestedCreateOrientation(
    runtimeContract: RuntimeContractPayload,
    orientation?: CreateGameOrientation | null,
  ): RuntimeContractPayload {
    const runtimeOrientation = this.resolveRuntimeOrientation(orientation);
    if (!runtimeOrientation) {
      return runtimeContract;
    }

    const canvas = runtimeContract.canvas && typeof runtimeContract.canvas === 'object'
      ? runtimeContract.canvas as Record<string, unknown>
      : {};
    const mobileLayout = runtimeContract.mobile_layout && typeof runtimeContract.mobile_layout === 'object'
      ? runtimeContract.mobile_layout as Record<string, unknown>
      : {};
    const metadata = runtimeContract.metadata && typeof runtimeContract.metadata === 'object'
      ? runtimeContract.metadata as Record<string, unknown>
      : {};

    return {
      ...runtimeContract,
      canvas: {
        ...canvas,
        orientation: runtimeOrientation,
      },
      mobile_layout: {
        ...mobileLayout,
        orientation: runtimeOrientation,
      },
      metadata: {
        ...metadata,
        requested_orientation: orientation,
        orientation: runtimeOrientation,
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

  private extractBundleGameSpec(bundleHistory: any[]): Record<string, unknown> | null {
    for (const bundle of [...bundleHistory].reverse()) {
      const gameSpec = bundle?.metadata?.gameSpec;
      if (
        gameSpec
        && typeof gameSpec === 'object'
        && !Array.isArray(gameSpec)
        && typeof (gameSpec as Record<string, unknown>).game_type === 'string'
        && String((gameSpec as Record<string, unknown>).game_type).trim()
      ) {
        return gameSpec as Record<string, unknown>;
      }
    }
    return null;
  }

  private resolveRuntimeHintGameType(
    sourceSpec?: Record<string, unknown> | null,
    game?: { gameType?: string | null } | null,
  ): string | null {
    if (
      sourceSpec
      && typeof sourceSpec.game_type === 'string'
      && sourceSpec.game_type.trim()
    ) {
      return sourceSpec.game_type.trim();
    }

    if (typeof game?.gameType === 'string' && game.gameType.trim()) {
      return game.gameType.trim();
    }

    return null;
  }

  private extractBundleOrientation(bundleHistory: any[]): CreateGameOrientation | undefined {
    for (const bundle of [...bundleHistory].reverse()) {
      const metadata = bundle?.metadata && typeof bundle.metadata === 'object'
        ? bundle.metadata as Record<string, unknown>
        : null;
      if (!metadata) {
        continue;
      }

      const requestedOrientation = this.normalizeRequestedOrientation(
        metadata.requestedOrientation ?? metadata.orientation ?? metadata.requested_orientation,
      );
      if (requestedOrientation) {
        return requestedOrientation;
      }

      const runtimeOrientation = metadata.runtimeOrientation
        ?? metadata.runtime_orientation
        ?? metadata.orientation;
      const recoveredOrientation = this.resolveRequestedOrientationFromRuntime(runtimeOrientation);
      if (recoveredOrientation) {
        return recoveredOrientation;
      }
    }

    return undefined;
  }

  private extractBundleGenerationTier(bundleHistory: any[]): GenerationTier | undefined {
    for (const bundle of [...bundleHistory].reverse()) {
      const metadata = bundle?.metadata && typeof bundle.metadata === 'object'
        ? bundle.metadata as Record<string, unknown>
        : null;
      if (!metadata) {
        continue;
      }

      const generationTier = this.normalizeRequestedGenerationTier(
        metadata.generationTier ?? metadata.generation_tier,
      );
      if (generationTier) {
        return generationTier;
      }
    }

    return undefined;
  }

  private buildIterationSourceBundleContext(params: {
    game: any;
    latestBundle: any | null;
    bundleHistory: any[];
  }): SourceBundleContextPayload | null {
    const { game, latestBundle, bundleHistory } = params;
    const latestMetadata = latestBundle?.metadata || {};
    const recentRevisions = [...bundleHistory]
      .slice(-4)
      .reverse()
      .map((bundleVersion: any) => {
        const metadata = bundleVersion?.metadata || {};
        const feedback = typeof metadata.feedback === 'string' ? metadata.feedback.trim() : '';
        const iterationType = typeof metadata.iterationType === 'string' ? metadata.iterationType.trim() : '';
        const summary = [
          feedback || '',
          iterationType ? `type=${iterationType}` : '',
        ].filter(Boolean).join(' | ');

        return {
          version: typeof bundleVersion?.version === 'number' ? bundleVersion.version : null,
          generated_at: bundleVersion?.createdAt ? new Date(bundleVersion.createdAt).toISOString() : null,
          feedback: feedback || null,
          iteration_type: iterationType || null,
          summary: summary || null,
        };
      })
      .filter((revision) => (
        revision.version !== null
        || revision.feedback
        || revision.iteration_type
        || revision.summary
      ));

    const latestFeedback = typeof latestMetadata.feedback === 'string' ? latestMetadata.feedback.trim() : '';
    const latestIterationType = typeof latestMetadata.iterationType === 'string'
      ? latestMetadata.iterationType.trim()
      : '';
    const latestGameType = typeof latestMetadata?.gameSpec?.game_type === 'string'
      && latestMetadata.gameSpec.game_type.trim()
      ? latestMetadata.gameSpec.game_type.trim()
      : (
        typeof game?.gameType === 'string' && game.gameType.trim()
          ? game.gameType.trim()
          : null
      );
    const latestOrientation = this.extractBundleOrientation([
      ...bundleHistory,
      ...(latestBundle ? [latestBundle] : []),
    ]) ?? null;
    const latestGenerationTier = this.extractBundleGenerationTier([
      ...bundleHistory,
      ...(latestBundle ? [latestBundle] : []),
    ]) ?? 'standard';
    const summaryParts = [
      latestGameType ? `game_type=${latestGameType}` : '',
      latestGenerationTier ? `generation_tier=${latestGenerationTier}` : '',
      latestOrientation ? `orientation=${latestOrientation}` : '',
      latestFeedback ? `latest_feedback=${latestFeedback}` : '',
      latestIterationType ? `latest_iteration=${latestIterationType}` : '',
      latestBundle?.htmlCode ? `code_size=${Buffer.byteLength(String(latestBundle.htmlCode), 'utf8')}B` : '',
    ].filter(Boolean);

    return {
      title: typeof game?.title === 'string' ? game.title : null,
      latest_bundle_version: typeof latestBundle?.version === 'number'
        ? latestBundle.version
        : (typeof game?.version === 'number' ? game.version : null),
      latest_game_type: latestGameType,
      latest_generation_tier: latestGenerationTier,
      latest_orientation: latestOrientation,
      latest_feedback: latestFeedback || null,
      latest_iteration_type: latestIterationType || null,
      summary: summaryParts.length > 0 ? summaryParts.join('; ') : null,
      recent_revisions: recentRevisions,
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
    orientation?: CreateGameOrientation;
    generationTier?: GenerationTier;
    access?: AccessGrantDecision;
    sourceSpec?: Record<string, unknown> | null;
    creationSessionId?: string | null;
    entryMode?: string | null;
    sourceGameId?: string | null;
    promptBundleSnapshot: PromptBundleSnapshotPayload;
    runtimeContract: RuntimeContractPayload;
  }): Record<string, unknown> {
    const generationTier = this.normalizeRequestedGenerationTier(params.generationTier) || 'standard';
    return {
      game_id: params.gameId,
      user_id: params.userId,
      raw_user_input: params.description,
      generation_tier: generationTier,
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
          generation_tier: generationTier,
          ...(params.orientation ? { orientation: params.orientation } : {}),
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
        source_spec: params.sourceSpec || null,
        prompt_bundle_snapshot: params.promptBundleSnapshot,
        runtime_contract: params.runtimeContract,
        normalized_request: {
          description: params.description,
          title: params.title || null,
          region: params.executionRegion,
          entrypoint: 'create',
          generation_tier: generationTier,
          ...(params.orientation ? { orientation: params.orientation } : {}),
          ...(params.creationSessionId ? { creation_session_id: params.creationSessionId } : {}),
        },
        metadata: {
          adapter: 'compat_v1',
          pipeline_version: 'v2',
          generation_tier: generationTier,
          ...(params.orientation ? { orientation: params.orientation } : {}),
          ...(params.creationSessionId ? { creation_session_id: params.creationSessionId } : {}),
          ...(params.entryMode ? { entry_mode: params.entryMode } : {}),
          ...(params.sourceGameId ? { source_game_id: params.sourceGameId } : {}),
        },
      };
    }

  private async mergeGenerationTaskMetadata(
    taskId: string,
    patch: Record<string, unknown>,
  ): Promise<void> {
    const currentTask = await Promise.resolve(this.prisma.generationTask.findUnique({
      where: { id: taskId },
      select: { metadata: true },
    })).catch(() => null);
    const metadata = currentTask?.metadata && typeof currentTask.metadata === 'object' && !Array.isArray(currentTask.metadata)
      ? { ...(currentTask.metadata as Record<string, unknown>) }
      : {};

    await Promise.resolve(this.prisma.generationTask.update({
      where: { id: taskId },
      data: {
        metadata: {
          ...metadata,
          ...patch,
        } as Prisma.InputJsonValue,
      },
    })).catch((error) => {
      this.logger.warn(`Failed to update task metadata for ${taskId}: ${this.extractErrorMessage(error)}`);
    });
  }

  private async analyzeCreateIntentTurn(params: {
    userId: string;
    description: string;
    title?: string;
    generationTier: GenerationTier;
    entryMode?: string | null;
    executionRegion?: string;
  }): Promise<DirectIntentAnalyzeResponsePayload> {
    const aiEngineUrl = await this.getAiEngineBaseUrl(params.executionRegion);
    const timeoutMs = await this.getExpandPromptRequestTimeoutMs();
    const response = await axios.post(
      `${aiEngineUrl}/api/v1/ai/dialogue/analyze-turn`,
      {
        user_id: params.userId,
        conversation: [
          {
            role: 'user',
            content: params.description,
            kind: 'prompt',
          },
        ],
        current_slots: {},
        skipped_slots: [],
        entry_mode: this.normalizeOptionalString(params.entryMode) || 'create',
        generation_tier: params.generationTier,
        ...(params.title ? { title: params.title } : {}),
        initial_prompt: params.description,
      },
      { timeout: timeoutMs },
    );
    return response.data || {};
  }

  private async buildCreateSpecFromPrompt(params: {
    userId: string;
    description: string;
    title?: string;
    generationTier: GenerationTier;
    executionRegion?: string;
    slots: Record<string, unknown>;
    variationSeed: string;
  }): Promise<DirectIntentSpecResponsePayload> {
    const aiEngineUrl = await this.getAiEngineBaseUrl(params.executionRegion);
    const timeoutMs = await this.getExpandPromptRequestTimeoutMs();
    const response = await axios.post(
      `${aiEngineUrl}/api/v1/ai/dialogue/spec-from-slots`,
      {
        slots: params.slots,
        source_description: params.description,
        ...(params.title ? { title: params.title } : {}),
        generation_tier: params.generationTier,
        skipped_slots: [],
        variation_seed: params.variationSeed,
      },
      { timeout: timeoutMs },
    );
    return response.data || {};
  }

  private buildAiGatewayStageError(
    error: unknown,
    fallback: string,
    failedStage: string,
    failureFamily: string,
  ): Error {
    const wrapped = new Error(this.extractAiGatewayErrorMessage(error, fallback));
    (wrapped as Error & { failedStage?: string; failureFamily?: string }).failedStage = failedStage;
    (wrapped as Error & { failedStage?: string; failureFamily?: string }).failureFamily = failureFamily;
    return wrapped;
  }

  private extractAiGatewayErrorMessage(error: unknown, fallback: string): string {
    const raw = (error as any)?.response?.data?.detail
      ?? (error as any)?.response?.data?.message
      ?? (error as any)?.message
      ?? fallback;
    return this.stringifyAiGatewayErrorDetail(raw, fallback);
  }

  private stringifyAiGatewayErrorDetail(value: unknown, fallback: string): string {
    if (value == null) {
      return fallback;
    }
    if (typeof value === 'string') {
      const normalized = value.trim();
      return normalized || fallback;
    }
    if (Array.isArray(value)) {
      const parts = value
        .map((item) => this.stringifyAiGatewayErrorDetail(item, ''))
        .map((item) => item.trim())
        .filter(Boolean);
      return parts.join('; ') || fallback;
    }
    if (typeof value === 'object') {
      const record = value as Record<string, unknown>;
      for (const candidate of [record.detail, record.message, record.msg, record.reason, record.error]) {
        const rendered = this.stringifyAiGatewayErrorDetail(candidate, '');
        if (rendered) {
          return rendered;
        }
      }
      const parts = Object.entries(record)
        .map(([key, item]) => {
          const rendered = this.stringifyAiGatewayErrorDetail(item, '');
          return rendered ? `${key}=${rendered}` : '';
        })
        .filter(Boolean);
      return parts.join(', ') || fallback;
    }
    const normalized = String(value).trim();
    return normalized || fallback;
  }

  private async ensureCreateSourceSpec(params: {
    gameId: string;
    userId: string;
    description: string;
    taskId?: string;
    executionRegion?: string;
    title?: string;
    generationTier: GenerationTier;
    entryMode?: string | null;
    sourceSpec?: Record<string, unknown> | null;
  }): Promise<Record<string, unknown> | null> {
    if (params.sourceSpec && Object.keys(params.sourceSpec).length > 0) {
      return params.sourceSpec;
    }

    const variationSeed = this.buildCreateIntentVariationSeed({
      userId: params.userId,
      description: params.description,
      title: params.title,
      generationTier: params.generationTier,
      entryMode: params.entryMode,
    });

    if (params.taskId) {
      await Promise.resolve(this.generationTaskService.recordProgress({
        taskId: params.taskId,
        gameId: params.gameId,
        userId: params.userId,
        stage: 'spec_build',
        percentage: 15,
        message: 'Building structured source spec',
        details: {
          source: 'zero_question',
        },
      })).catch(() => undefined);
    }

    let analysis: DirectIntentAnalyzeResponsePayload;
    try {
      analysis = await this.analyzeCreateIntentTurn({
        userId: params.userId,
        description: params.description,
        title: params.title,
        generationTier: params.generationTier,
        entryMode: params.entryMode,
        executionRegion: params.executionRegion,
      });
    } catch (error) {
      throw this.buildAiGatewayStageError(
        error,
        'Create intent analysis failed',
        'spec_build',
        'intent_analysis',
      );
    }

    let specResponse: DirectIntentSpecResponsePayload;
    try {
      specResponse = await this.buildCreateSpecFromPrompt({
        userId: params.userId,
        description: params.description,
        title: params.title,
        generationTier: params.generationTier,
        executionRegion: params.executionRegion,
        slots: analysis.slots || {},
        variationSeed,
      });
    } catch (error) {
      throw this.buildAiGatewayStageError(
        error,
        'Create source spec compilation failed',
        'spec_build',
        'source_spec_compile',
      );
    }

    const missingRequired = Array.isArray(specResponse.missing_required)
      ? specResponse.missing_required
      : [];
    const compiledSpec = specResponse.spec && typeof specResponse.spec === 'object' && !Array.isArray(specResponse.spec)
      ? specResponse.spec
      : null;
    if (!compiledSpec || missingRequired.length > 0) {
      const details = missingRequired.length > 0
        ? `missing: ${missingRequired.join(', ')}`
        : 'spec payload missing';
      const error = new Error(`Zero-question source spec is incomplete (${details})`);
      (error as Error & { failedStage?: string; failureFamily?: string }).failedStage = 'spec_build';
      (error as Error & { failedStage?: string; failureFamily?: string }).failureFamily = 'source_spec_incomplete';
      throw error;
    }

    const intentBuild = this.buildCreateIntentBuild({
      title: params.title,
      description: params.description,
      entryMode: params.entryMode,
      generationTier: params.generationTier,
      sourceSpec: compiledSpec,
    });
    if (params.taskId) {
      await this.mergeGenerationTaskMetadata(params.taskId, {
        sourceSpec: compiledSpec,
        intentBuild,
        sourceSpecBuild: {
          source: 'zero_question',
          variationSeed,
          readyToGenerate: Boolean(analysis.ready_to_generate),
          slotFillPct: Number(specResponse.slot_fill_pct ?? analysis.slot_fill_pct ?? 0) || 0,
          missingRequired,
          builtAt: new Date().toISOString(),
        },
      });
      await Promise.resolve(this.generationTaskService.createArtifact({
        taskId: params.taskId,
        gameId: params.gameId,
        userId: params.userId,
        artifactType: 'source_spec',
        contentType: 'application/json',
        payload: compiledSpec,
        metadata: {
          source: 'zero_question',
          variationSeed,
          readyToGenerate: Boolean(analysis.ready_to_generate),
          slotFillPct: Number(specResponse.slot_fill_pct ?? analysis.slot_fill_pct ?? 0) || 0,
          missingRequired,
          intentFingerprint: intentBuild.intentFingerprint,
          specFingerprint: intentBuild.specFingerprint,
        },
      })).catch((error) => {
        this.logger.warn(`Failed to persist source spec artifact for task ${params.taskId}: ${this.extractErrorMessage(error)}`);
      });
      await Promise.resolve(this.generationTaskService.createArtifact({
        taskId: params.taskId,
        gameId: params.gameId,
        userId: params.userId,
        artifactType: 'intent_build',
        contentType: 'application/json',
        payload: intentBuild,
        metadata: {
          source: 'zero_question',
          variationSeed,
        },
      })).catch((error) => {
        this.logger.warn(`Failed to persist intent build artifact for task ${params.taskId}: ${this.extractErrorMessage(error)}`);
      });
    }

    return compiledSpec;
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
    orientation?: CreateGameOrientation;
    generationTier?: GenerationTier;
    sourceSpec?: Record<string, unknown> | null;
    sourceBundleContext?: SourceBundleContextPayload | null;
    promptBundleSnapshot: PromptBundleSnapshotPayload;
    runtimeContract: RuntimeContractPayload;
  }): Record<string, unknown> {
    const game = params.game || {};
    const orientation = this.normalizeRequestedOrientation(params.orientation)
      ?? this.resolveRequestedOrientationFromRuntime(
        this.resolveRuntimeOrientationFromContract(params.runtimeContract),
      );
    const generationTier = this.normalizeRequestedGenerationTier(params.generationTier)
      ?? this.normalizeRequestedGenerationTier(params.sourceBundleContext?.latest_generation_tier)
      ?? 'standard';

    return {
      game_id: params.gameId,
      user_id: params.userId,
      current_code: params.currentCode,
      generation_tier: generationTier,
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
          generation_tier: generationTier,
          ...(orientation ? { orientation } : {}),
        },
      },
      iteration_intent: {
        feedback: params.feedback,
        conversation: params.conversationHistory,
      },
      source_spec: params.sourceSpec || null,
      source_bundle_context: params.sourceBundleContext || null,
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
        generation_tier: generationTier,
        ...(orientation ? { orientation } : {}),
      },
      metadata: {
        adapter: 'compat_v1',
        pipeline_version: 'v2',
        generation_tier: generationTier,
        ...(orientation ? { orientation } : {}),
      },
    };
  }

  async getAiEngineBaseUrl(executionRegion?: string): Promise<string> {
    return this.resolveAiEngineEndpoint(executionRegion);
  }

  private normalizeAiEngineBaseUrl(value?: string | null): string {
    return (value || '').trim().replace(/\/$/, '');
  }

  private appendAiEngineBaseUrl(urls: string[], value?: string | null): void {
    const normalized = this.normalizeAiEngineBaseUrl(value);
    if (normalized && !urls.includes(normalized)) {
      urls.push(normalized);
    }
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

  private async getDeployedAiEngineTargetUrl(executionRegion?: string): Promise<string> {
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

    const targetUrl = this.normalizeAiEngineBaseUrl(target?.aiEngineUrl);
    if (targetUrl) {
      this.aiEngineTargetCache.set(region, {
        url: targetUrl,
        cachedAt: now,
      });
      return targetUrl;
    }

    return '';
  }

  private async resolveAiEngineEndpointCandidates(
    executionRegion?: string,
    preferredBaseUrl?: string | null,
  ): Promise<string[]> {
    await this.ensureTimeoutConfigCache();
    const region = this.resolveExecutionRegion(executionRegion);
    const urls: string[] = [];
    this.appendAiEngineBaseUrl(urls, preferredBaseUrl);
    this.appendAiEngineBaseUrl(urls, this.getConfiguredAiEngineUrlForRegion(region));
    this.appendAiEngineBaseUrl(urls, await this.getDeployedAiEngineTargetUrl(region));
    if (!urls.length && process.env.NODE_ENV !== 'production') {
      this.appendAiEngineBaseUrl(urls, this.aiEngineUrl);
    }

    if (urls.length > 0) {
      return urls;
    }

    throw new Error(`missing_ai_engine_region_target: no deployed ai-engine target for region ${region}`);
  }

  private async resolveAiEngineEndpoint(executionRegion?: string): Promise<string> {
    const urls = await this.resolveAiEngineEndpointCandidates(executionRegion);
    return urls[0];
  }

  private getPublicBaseUrl(): string {
    const publicApiBaseUrl = this.configService.get<string>('PUBLIC_API_BASE_URL');
    const appUrl = this.configService.get<string>('APP_URL', 'http://localhost:3002');
    return (publicApiBaseUrl || appUrl).replace(/\/$/, '');
  }

  private getPublicApiBaseUrl(): string {
    const baseUrl = this.getPublicBaseUrl();

    try {
      const parsed = new URL(baseUrl);
      const normalizedPath = (parsed.pathname || '').replace(/\/$/, '');
      parsed.pathname = normalizedPath.endsWith('/api/v1')
        ? normalizedPath
        : `${normalizedPath}/api/v1`.replace(/\/{2,}/g, '/');
      return parsed.toString().replace(/\/$/, '');
    } catch {
      return baseUrl.endsWith('/api/v1') ? baseUrl : `${baseUrl}/api/v1`;
    }
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

  private buildCoverUrl(gameId: string, options: CoverLinkOptions = {}): string {
    const coverUrl = new URL(`${this.getPublicApiBaseUrl()}/games/${gameId}/cover`);
    if (options.taskId) {
      coverUrl.searchParams.set('taskId', options.taskId);
    }
    if (typeof options.version !== 'undefined' && options.version !== null) {
      coverUrl.searchParams.set('v', String(options.version));
    }
    if (options.previewToken) {
      coverUrl.searchParams.set('previewToken', options.previewToken);
    }
    return coverUrl.toString();
  }

  private extractPersistedCoverUrl(metadata: unknown): string | null {
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
      return null;
    }

    const rawCoverUrl = (metadata as Record<string, unknown>).coverUrl
      ?? (metadata as Record<string, unknown>).cover_url;
    return typeof rawCoverUrl === 'string' && rawCoverUrl.trim()
      ? rawCoverUrl.trim()
      : null;
  }

  private extractCoverTaskIdFromUrl(gameId: string, coverUrl?: string | null): string | null {
    if (typeof coverUrl !== 'string' || !coverUrl.trim()) {
      return null;
    }

    try {
      const parsed = new URL(coverUrl, this.getPublicBaseUrl());
      if (!parsed.pathname.endsWith(`/games/${gameId}/cover`)) {
        return null;
      }
      const taskId = parsed.searchParams.get('taskId');
      return typeof taskId === 'string' && taskId.trim() ? taskId.trim() : null;
    } catch {
      return null;
    }
  }

  private extractPersistedCoverTaskId(gameId: string, metadata: unknown): string | null {
    if (metadata && typeof metadata === 'object' && !Array.isArray(metadata)) {
      const rawTaskId = (metadata as Record<string, unknown>).coverTaskId
        ?? (metadata as Record<string, unknown>).cover_task_id;
      if (typeof rawTaskId === 'string' && rawTaskId.trim()) {
        return rawTaskId.trim();
      }
    }

    return this.extractCoverTaskIdFromUrl(gameId, this.extractPersistedCoverUrl(metadata));
  }

  private extractPersistedCoverArtifactId(metadata: unknown): string | null {
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
      return null;
    }

    const rawArtifactId = (metadata as Record<string, unknown>).coverArtifactId
      ?? (metadata as Record<string, unknown>).cover_artifact_id;
    return typeof rawArtifactId === 'string' && rawArtifactId.trim()
      ? rawArtifactId.trim()
      : null;
  }

  private buildPersistedCoverMetadata(params: {
    gameId: string;
    coverUrl?: string | null;
    taskId?: string;
    artifactId?: string | null;
  }): Record<string, unknown> {
    const persistedCoverUrl = typeof params.coverUrl === 'string' && params.coverUrl.trim()
      ? params.coverUrl.trim()
      : null;
    const persistedCoverTaskId = typeof params.taskId === 'string' && params.taskId.trim()
      ? params.taskId.trim()
      : this.extractCoverTaskIdFromUrl(params.gameId, persistedCoverUrl);
    const persistedCoverArtifactId = typeof params.artifactId === 'string' && params.artifactId.trim()
      ? params.artifactId.trim()
      : null;

    return {
      ...(persistedCoverUrl ? { coverUrl: persistedCoverUrl } : {}),
      ...(persistedCoverTaskId ? { coverTaskId: persistedCoverTaskId } : {}),
      ...(persistedCoverArtifactId ? { coverArtifactId: persistedCoverArtifactId } : {}),
    };
  }

  private resolvePersistedCoverUrlForBundle(
    gameId: string,
    bundle?: { version?: number | string | null; metadata?: unknown } | null,
  ): string | null {
    const coverArtifactId = this.extractPersistedCoverArtifactId(bundle?.metadata);
    if (coverArtifactId) {
      return this.buildCoverUrl(gameId, {
        version: bundle?.version ?? undefined,
      });
    }

    const coverTaskId = this.extractPersistedCoverTaskId(gameId, bundle?.metadata);
    if (coverTaskId) {
      return this.buildCoverUrl(gameId, {
        taskId: coverTaskId,
        version: bundle?.version ?? undefined,
      });
    }

    return this.extractPersistedCoverUrl(bundle?.metadata);
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

  private async patchCreationSession(
    sessionId: string | null | undefined,
    patch: {
      status?: string;
      generatedGameId?: string | null;
      generationTaskId?: string | null;
      metadataPatch?: Record<string, unknown>;
    },
  ): Promise<void> {
    if (!sessionId) {
      return;
    }

    const repo = (this.prisma as any).gameCreationSession;
    if (!repo?.findUnique || !repo?.update) {
      return;
    }

    const existing = await Promise.resolve(repo.findUnique({
      where: { id: sessionId },
      select: { metadata: true },
    })).catch(() => null);

    const metadata = existing?.metadata && typeof existing.metadata === 'object'
      ? existing.metadata as Record<string, unknown>
      : {};

    const nextMetadata = patch.metadataPatch
      ? {
          ...metadata,
          ...patch.metadataPatch,
        }
      : metadata;

    await Promise.resolve(repo.update({
      where: { id: sessionId },
      data: {
        ...(patch.status ? { status: patch.status } : {}),
        ...(patch.generatedGameId !== undefined ? { generatedGameId: patch.generatedGameId } : {}),
        ...(patch.generationTaskId !== undefined ? { generationTaskId: patch.generationTaskId } : {}),
        ...(patch.metadataPatch ? { metadata: nextMetadata } : {}),
      },
    })).catch((error: Error) => {
      this.logger.warn(`Failed to patch creation session ${sessionId}: ${error.message}`);
      return null;
    });
  }

  private async syncCreationSessionByTaskId(
    taskId: string | undefined,
    patch: {
      status?: string;
      generatedGameId?: string | null;
      generationTaskId?: string | null;
      metadataPatch?: Record<string, unknown>;
    },
  ): Promise<void> {
    if (!taskId) {
      return;
    }

    let task: { metadata?: unknown } | null = null;
    try {
      const query = this.prisma?.generationTask?.findUnique?.({
        where: { id: taskId },
        select: { metadata: true },
      });
      task = await Promise.resolve(query ?? null);
    } catch {
      task = null;
    }

    const metadata = task?.metadata && typeof task.metadata === 'object'
      ? task.metadata as Record<string, unknown>
      : null;
    const creationSessionId = typeof metadata?.creationSessionId === 'string'
      ? metadata.creationSessionId
      : null;

    await this.patchCreationSession(creationSessionId, patch);
  }

  private isTimeoutError(error: unknown): boolean {
    const message = this.extractErrorMessage(error as any);
    return /timed out|timeout|deadline exceeded|ECONNABORTED/i.test(message);
  }

  private isTransientUpstreamSnapshotError(error: unknown): boolean {
    if (this.isTimeoutError(error)) {
      return true;
    }
    const status = Number((error as any)?.response?.status ?? 0);
    if (status >= 500) {
      return true;
    }
    const code = String((error as any)?.code || '').toUpperCase();
    return ['ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EAI_AGAIN'].includes(code);
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

  private getPersistedUpstreamBaseUrl(task: { metadata?: any } | null | undefined): string {
    const metadata = task?.metadata;
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
      return '';
    }
    return this.normalizeAiEngineBaseUrl((metadata as Record<string, unknown>).upstreamBaseUrl as string | undefined);
  }

  private async bindUpstreamTaskId(
    taskId: string,
    upstreamTaskId: string,
    upstreamBaseUrl?: string,
  ): Promise<void> {
    const currentTask = await Promise.resolve(this.prisma.generationTask.findUnique({
      where: { id: taskId },
      select: {
        metadata: true,
      },
    })).catch(() => null);
    const metadata = currentTask?.metadata && typeof currentTask.metadata === 'object' && !Array.isArray(currentTask.metadata)
      ? { ...(currentTask.metadata as Record<string, unknown>) }
      : {};
    const normalizedBaseUrl = this.normalizeAiEngineBaseUrl(upstreamBaseUrl);
    if (normalizedBaseUrl) {
      metadata.upstreamBaseUrl = normalizedBaseUrl;
    }

    await Promise.resolve(this.prisma.generationTask.update({
      where: { id: taskId },
      data: {
        upstreamTaskId,
        metadata: Object.keys(metadata).length > 0
          ? (metadata as Prisma.InputJsonValue)
          : undefined,
      },
    })).catch((error) => {
      this.logger.warn(`Failed to bind upstream task ${upstreamTaskId} to ${taskId}: ${error.message}`);
    });
  }

  private async requestUpstreamAsyncTask(params: {
    aiEngineBaseUrls: string[];
    endpoint:
      | '/api/v1/ai/pipeline/run/async'
      | '/api/v1/ai/pipeline/iterate/async'
      | '/api/v1/ai/pipeline/v2/run/async'
      | '/api/v1/ai/pipeline/v2/iterate/async';
    payload: Record<string, unknown>;
    taskId?: string;
    userId: string;
    gameId: string;
  }): Promise<ResolvedUpstreamAsyncTaskHandle> {
    await this.ensureTimeoutConfigCache();
    const candidateUrls = params.aiEngineBaseUrls
      .map((value) => this.normalizeAiEngineBaseUrl(value))
      .filter(Boolean)
      .filter((value, index, list) => list.indexOf(value) === index);
    if (!candidateUrls.length) {
      throw new Error('No reachable ai-engine base URL configured');
    }

    const failures: Array<{ baseUrl: string; message: string }> = [];
    let lastError: unknown = null;
    for (const aiEngineBaseUrl of candidateUrls) {
      try {
        const response = await withRetry(() =>
          axios.post(
            `${aiEngineBaseUrl}${params.endpoint}`,
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
                  aiEngineBaseUrl,
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
                    aiEngineBaseUrl,
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
          await this.bindUpstreamTaskId(params.taskId, handle.task_id, aiEngineBaseUrl);
        }

        return {
          ...handle,
          aiEngineBaseUrl,
        };
      } catch (error) {
        lastError = error;
        failures.push({
          baseUrl: aiEngineBaseUrl,
          message: this.extractErrorMessage(error),
        });
      }
    }

    if (failures.length <= 1 && lastError) {
      throw lastError;
    }

    throw new Error(
      `All ai-engine endpoints failed for ${params.endpoint}: ${failures.map((item) => `${item.baseUrl}: ${item.message}`).join(' | ')}`,
    );
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

  private async fetchUpstreamTaskSnapshotWithFailover(task: {
    region?: string | null;
    metadata?: any;
  }, upstreamTaskId: string): Promise<UpstreamAsyncTaskSnapshot | null> {
    const baseUrls = await this.resolveAiEngineEndpointCandidates(
      task.region || undefined,
      this.getPersistedUpstreamBaseUrl(task),
    );
    const failures: Array<{ baseUrl: string; message: string }> = [];
    let sawNotFound = false;
    for (const baseUrl of baseUrls) {
      try {
        const snapshot = await this.fetchUpstreamTaskSnapshot(baseUrl, upstreamTaskId);
        if (snapshot) {
          return snapshot;
        }
        sawNotFound = true;
      } catch (error) {
        failures.push({
          baseUrl,
          message: this.extractErrorMessage(error),
        });
      }
    }

    if (sawNotFound || !failures.length) {
      return null;
    }

    throw new Error(
      `All ai-engine snapshot endpoints failed for ${upstreamTaskId}: ${
        failures.map((item) => `${item.baseUrl}: ${item.message}`).join(' | ')
      }`,
    );
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
    let consecutiveSnapshotFailures = 0;
    let lastSnapshotError: string | null = null;

    while (Date.now() <= deadlineMs) {
      if (params.taskId && params.gameId) {
        await this.assertTaskCanPersistResult(params.taskId, params.gameId);
      }

      if (params.taskId) {
        const localTerminalSnapshot = await this.resolveDurableLocalTaskSnapshot(params.taskId);
        if (localTerminalSnapshot) {
          return localTerminalSnapshot;
        }
      }

      try {
        const snapshot = await this.fetchUpstreamTaskSnapshot(params.aiEngineBaseUrl, params.upstreamTaskId);
        consecutiveSnapshotFailures = 0;
        lastSnapshotError = null;
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
      } catch (error) {
        const message = this.extractErrorMessage(error);
        if (!this.isTransientUpstreamSnapshotError(error)) {
          throw error;
        }
        consecutiveSnapshotFailures += 1;
        lastSnapshotError = message;
        if (params.taskId) {
          const localTerminalSnapshot = await this.resolveDurableLocalTaskSnapshot(params.taskId);
          if (localTerminalSnapshot) {
            return localTerminalSnapshot;
          }
        }
        if (consecutiveSnapshotFailures === 1 || consecutiveSnapshotFailures % 5 === 0) {
          this.logger.warn(
            `Transient upstream snapshot failure for ${params.upstreamTaskId} (${consecutiveSnapshotFailures}): ${message}`,
          );
        }
      }

      await new Promise((resolve) => setTimeout(resolve, this.getUpstreamPollIntervalMs()));
    }

    throw new Error(
      lastSnapshotError
        ? `Upstream AI task ${params.upstreamTaskId} timed out while waiting for completion (last snapshot error: ${lastSnapshotError})`
        : `Upstream AI task ${params.upstreamTaskId} timed out while waiting for completion`,
    );
  }

  private async getStoredFailureResponseData(taskId: string): Promise<Record<string, any> | null> {
    const artifact = await this.generationTaskService.findLatestArtifactForTask(taskId, ['task_failure']);
    return this.parseArtifactPayload<Record<string, any>>(artifact);
  }

  private async resolveDurableLocalTaskSnapshot(taskId: string): Promise<UpstreamAsyncTaskSnapshot | null> {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
      select: {
        id: true,
        status: true,
        taskType: true,
        progressStage: true,
        errorMessage: true,
        failedStage: true,
        retryCount: true,
        fallback: true,
        failureFamily: true,
        primaryArtifactId: true,
      },
    });

    if (!task) {
      return null;
    }

    const buildFailureSnapshot = (message: string, stage?: string | null, extra?: Record<string, any>): UpstreamAsyncTaskSnapshot => ({
      task_id: task.id,
      status: task.status === 'canceled' ? 'canceled' : 'failed',
      error: {
        message,
        failed_stage: stage || task.failedStage || task.progressStage || 'failed',
        retry_count: task.retryCount || 0,
        fallback: task.fallback || undefined,
        failure_family: task.failureFamily || undefined,
        primary_artifact_id: task.primaryArtifactId || undefined,
        ...(extra || {}),
      },
    });

    if (task.status === 'succeeded') {
      const responseData = await this.getStoredTerminalResponseData(task.id, task.taskType as GenerationTaskType);
      if (!responseData) {
        return null;
      }
      return {
        task_id: task.id,
        status: 'succeeded',
        result: responseData,
      };
    }

    if (task.status === 'failed' || task.status === 'timed_out') {
      return buildFailureSnapshot(task.errorMessage || 'Local task failed');
    }

    if (task.status === 'canceled') {
      return buildFailureSnapshot(task.errorMessage || 'Local task was canceled');
    }

    const responseData = await this.getStoredTerminalResponseData(task.id, task.taskType as GenerationTaskType);
    if (responseData) {
      return {
        task_id: task.id,
        status: 'succeeded',
        result: responseData,
      };
    }

    const failureData = await this.getStoredFailureResponseData(task.id);
    if (failureData) {
      return buildFailureSnapshot(
        String(failureData.message || failureData.errorMessage || task.errorMessage || 'Local task failed'),
        String(failureData.failed_stage || failureData.failedStage || task.failedStage || task.progressStage || 'failed'),
        {
          retry_count: Number(failureData.retry_count || failureData.retryCount || task.retryCount || 0) || 0,
          fallback: failureData.fallback || task.fallback || undefined,
          failure_family: failureData.failure_family || failureData.failureFamily || task.failureFamily || undefined,
          primary_artifact_id: failureData.primary_artifact_id || failureData.primaryArtifactId || task.primaryArtifactId || undefined,
        },
      );
    }

    return null;
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
    metadata?: any;
  }): Promise<void> {
    if (!task?.upstreamTaskId) {
      return;
    }

    await this.ensureTimeoutConfigCache();
    const baseUrls = await this.resolveAiEngineEndpointCandidates(
      task.region || undefined,
      this.getPersistedUpstreamBaseUrl(task),
    ).catch((error) => {
      this.logger.warn(
        `Failed to resolve ai-engine endpoints for cancel ${task.upstreamTaskId}: ${this.extractErrorMessage(error)}`,
      );
      return [] as string[];
    });
    if (!baseUrls.length) {
      return;
    }

    const failures: Array<{ baseUrl: string; message: string }> = [];
    for (const aiEngineBaseUrl of baseUrls) {
      try {
        await axios.post(
          `${aiEngineBaseUrl}/api/v1/ai/tasks/${task.upstreamTaskId}/cancel`,
          {},
          { timeout: this.getUpstreamCancelTimeoutMs() },
        );
        return;
      } catch (error) {
        failures.push({
          baseUrl: aiEngineBaseUrl,
          message: this.extractErrorMessage(error),
        });
      }
    }

    this.logger.warn(
      `Failed to cancel upstream task ${task.upstreamTaskId} for ${task.id || 'unknown'}: ${
        failures.map((item) => `${item.baseUrl}: ${item.message}`).join(' | ')
      }`,
    );
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
            description: true,
            authorId: true,
            gameType: true,
            status: true,
            visibility: true,
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
            forkedFrom: true,
          },
        },
      },
    });
  }

  private async persistTaskCancellation(task: any, reason: string) {
    const canceled = await this.prisma.$transaction(async (tx) => {
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

      const canceledTask = await tx.generationTask.update({
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

      return canceledTask;
    });

    await this.syncCreationSessionByTaskId(task.id, {
      status: 'abandoned',
      metadataPatch: {
        lastTaskStatus: 'canceled',
        lastErrorMessage: reason,
      },
    });

    return canceled;
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
      if (task.progressStage === 'completed') {
        const recovered = await this.recoverTaskFromStoredTerminalResponse({ task, game }).catch((error) => {
          this.logger.warn(
            `Failed to recover completed task ${task.id} before timeout finalization: ${this.extractErrorMessage(error)}`,
          );
          return null;
        });
        if (recovered) {
          return recovered;
        }
      }

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
    const game = task.game || null;

    let snapshot: UpstreamAsyncTaskSnapshot | null;
    try {
      snapshot = await this.fetchUpstreamTaskSnapshotWithFailover(task, task.upstreamTaskId);
    } catch (error: any) {
      this.logger.warn(`Failed to fetch upstream task ${task.upstreamTaskId}: ${this.extractErrorMessage(error)}`);
      return null;
    }

    if (!snapshot) {
      if (task.progressStage === 'completed') {
        const recovered = await this.recoverTaskFromStoredTerminalResponse({ task, game }).catch((error) => {
          this.logger.warn(
            `Failed to recover task ${task.id} from stored completion artifacts after missing upstream snapshot: ${this.extractErrorMessage(error)}`,
          );
          return null;
        });
        if (recovered) {
          return recovered;
        }
      }
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
      if (task.progressStage === 'completed') {
        const recovered = await this.recoverTaskFromStoredTerminalResponse({ task, game }).catch((error) => {
          this.logger.warn(
            `Failed to recover task ${task.id} from stored completion artifacts while upstream still reports running: ${this.extractErrorMessage(error)}`,
          );
          return null;
        });
        if (recovered) {
          return recovered;
        }
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
          orientation: this.normalizeRequestedOrientation(task.metadata?.orientation),
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
          orientation: this.normalizeRequestedOrientation(task.metadata?.orientation),
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

  async create(userId: string, dto: CreateGameCommand): Promise<any> {
    try {
      const gameId = randomUUID();
      const description = dto.description || dto.prompt || '';
      const title = dto.title?.trim() || `Game ${gameId.substring(0, 8)}`;
      const requestedOrientation = this.normalizeRequestedOrientation(dto.orientation)
        ?? this.inferRequestedOrientationFromText(description, title);
      const requestedGenerationTier = this.normalizeRequestedGenerationTier(dto.generationTier) || 'standard';
      const initialIntentBuild = this.buildCreateIntentBuild({
        title,
        description,
        entryMode: dto.entryMode,
        generationTier: requestedGenerationTier,
        sourceSpec: dto.sourceSpec ?? null,
      });
      const executionRegion = this.resolveExecutionRegion(dto.regionHint);
      const pipelineVersion = this.resolvePipelineVersion({
        entrypoint: 'create',
        userId,
        executionRegion,
      });
      const timeoutS = this.resolveTaskTimeoutForPipelineVersion(dto.timeoutS, pipelineVersion);
        const runtimeProfileHint = this.inferRuntimeProfileHint(
          this.resolveRuntimeHintGameType(dto.sourceSpec, null),
          description,
          dto.title,
        );
      const promptBundleSnapshot = pipelineVersion === 'v2'
        ? await this.buildPromptBundleSnapshot('create', runtimeProfileHint, requestedGenerationTier)
        : null;
      const runtimeContract = pipelineVersion === 'v2'
        ? await this.buildDefaultRuntimeContract('create', runtimeProfileHint, requestedOrientation, requestedGenerationTier)
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
              title,
              region: executionRegion,
              pipelineVersion,
              orientation: requestedOrientation ?? null,
              generationTier: requestedGenerationTier,
              creationSessionId: dto.creationSessionId ?? null,
              entryMode: dto.entryMode ?? null,
              sourceGameId: dto.sourceGameId ?? null,
              sourceSpec: dto.sourceSpec ?? null,
              intentBuild: initialIntentBuild,
              promptBundleSnapshot: promptBundleSnapshot ?? null,
              runtimeContract: runtimeContract ?? null,
              canPlay,
              requireSubscription,
              quotaRemaining,
              accessGrantSource,
              accessGrantSubscriptionId,
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

      if (initialIntentBuild.frozenSpec) {
        await Promise.resolve(this.generationTaskService.createArtifact({
          taskId: task.id,
          gameId,
          userId,
          artifactType: 'intent_build',
          contentType: 'application/json',
          payload: initialIntentBuild,
          metadata: {
            source: dto.creationSessionId ? 'creation_session' : 'create_request',
          },
        })).catch((error) => {
          this.logger.warn(`Failed to persist intent build artifact for task ${task.id}: ${this.extractErrorMessage(error)}`);
        });
        await Promise.resolve(this.generationTaskService.createArtifact({
          taskId: task.id,
          gameId,
          userId,
          artifactType: 'source_spec',
          contentType: 'application/json',
          payload: initialIntentBuild.frozenSpec,
          metadata: {
            source: dto.creationSessionId ? 'creation_session' : 'create_request',
            intentFingerprint: initialIntentBuild.intentFingerprint,
            specFingerprint: initialIntentBuild.specFingerprint,
          },
        })).catch((error) => {
          this.logger.warn(`Failed to persist source spec artifact for task ${task.id}: ${this.extractErrorMessage(error)}`);
        });
      }

      this.emitProgress(userId, gameId, 'started', 0, {
        stage: 'started',
        attempt: 1,
        maxAttempts: 1,
      });

      // Run pipeline asynchronously – client subscribes to WebSocket for progress
      const enqueued = await this.generationQueueService.enqueueJob(
        GENERATION_QUEUE_JOB_PIPELINE_RUN,
        task.id,
      );
      if (!enqueued) {
        this.scheduleLocalPipelineExecution(
          gameId,
          userId,
          description,
          timeoutS,
          task.id,
          executionRegion,
          {
            pipelineVersion,
            title,
            orientation: requestedOrientation,
            generationTier: requestedGenerationTier,
            access,
            sourceSpec: dto.sourceSpec ?? null,
            creationSessionId: dto.creationSessionId ?? null,
            entryMode: dto.entryMode ?? null,
            sourceGameId: dto.sourceGameId ?? null,
            promptBundleSnapshot,
            runtimeContract,
          },
        );
      }

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

  private scheduleLocalPipelineExecution(
    gameId: string,
    userId: string,
    description: string,
    timeoutS?: number,
    taskId?: string,
    executionRegion?: string,
    options: CreateExecutionOptions = {},
  ): void {
    setImmediate(() => {
      void this.executePipelineTask(
        gameId,
        userId,
        description,
        timeoutS,
        taskId,
        executionRegion,
        options,
      ).catch((error) => {
        this.logger.error(
          `Background pipeline task crashed for game ${gameId}: ${this.extractErrorMessage(error)}`,
        );
      });
    });
  }

  private scheduleLocalIterationExecution(
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
  ): void {
    setImmediate(() => {
      void this.executeIterationTask(
        gameId,
        userId,
        feedback,
        nextVersion,
        conversationHistory,
        currentCode,
        timeoutS,
        taskId,
        executionRegion,
        options,
      ).catch((error) => {
        this.logger.error(
          `Background iteration task crashed for game ${gameId}: ${this.extractErrorMessage(error)}`,
        );
      });
    });
  }

  private normalizeTaskMetadataRecord(metadata: unknown): Record<string, any> {
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
      return {};
    }
    return metadata as Record<string, any>;
  }

  private extractTaskMetadataObject<T = Record<string, unknown>>(
    metadata: Record<string, any>,
    key: string,
  ): T | null {
    const value = metadata[key];
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return null;
    }
    return value as T;
  }

  private async restoreQueuedIterationSourceCode(
    taskId: string,
    gameId: string,
    metadata: Record<string, any>,
  ): Promise<string> {
    const sourceArtifact = await this.generationTaskService.findLatestArtifactForTask(
      taskId,
      'iteration_source_code',
    ).catch(() => null);

    const truncated = Boolean(
      sourceArtifact?.metadata
      && typeof sourceArtifact.metadata === 'object'
      && !Array.isArray(sourceArtifact.metadata)
      && (sourceArtifact.metadata as Record<string, unknown>).truncated,
    );
    if (!truncated && typeof sourceArtifact?.payloadText === 'string' && sourceArtifact.payloadText.trim()) {
      return sourceArtifact.payloadText;
    }

    const bundleVersion = Number.parseInt(String(metadata.currentBundleVersion ?? ''), 10);
    if (Number.isFinite(bundleVersion) && bundleVersion > 0) {
      const sourceBundle = await this.bundleService.getBundle(gameId, bundleVersion).catch(() => null);
      if (typeof sourceBundle?.htmlCode === 'string' && sourceBundle.htmlCode.trim()) {
        return sourceBundle.htmlCode;
      }
    }

    const latestBundle = await this.bundleService.getLatestBundle(gameId).catch(() => null);
    return typeof latestBundle?.htmlCode === 'string' ? latestBundle.htmlCode : '';
  }

  async processQueuedPipelineRunTask(taskId: string): Promise<void> {
    const task = await this.getTaskWithGame(taskId);
    if (!task || this.isFinalTaskStatus(task.status)) {
      return;
    }
    if (task.upstreamTaskId) {
      await this.reconcileGenerationTask(task);
      return;
    }

    const metadata = this.normalizeTaskMetadataRecord(task.metadata);
    const access: AccessGrantDecision = {
      canPlay: Boolean(metadata.canPlay ?? task.game?.canPlay ?? true),
      requireSubscription: Boolean(metadata.requireSubscription ?? task.game?.requireSubscription ?? false),
      quotaRemaining: Number(metadata.quotaRemaining ?? 0) || 0,
      accessGrantSource: (metadata.accessGrantSource || task.game?.accessGrantSource || GameAccessGrantSource.none) as GameAccessGrantSource,
      accessGrantSubscriptionId: (metadata.accessGrantSubscriptionId || task.game?.accessGrantSubscriptionId || null) as string | null,
    };

    await this.executePipelineTask(
      task.gameId,
      task.userId,
      String(metadata.description || task.game?.description || ''),
      task.timeoutS ?? undefined,
      task.id,
      task.region || undefined,
      {
        pipelineVersion: metadata.pipelineVersion === 'v1' ? 'v1' : 'v2',
        title: String(metadata.title || task.game?.title || '').trim() || undefined,
        orientation: this.normalizeRequestedOrientation(metadata.orientation),
        generationTier: this.normalizeRequestedGenerationTier(metadata.generationTier) || 'standard',
        access,
        sourceSpec: this.extractTaskMetadataObject<Record<string, unknown>>(metadata, 'sourceSpec'),
        creationSessionId: typeof metadata.creationSessionId === 'string' ? metadata.creationSessionId : null,
        entryMode: typeof metadata.entryMode === 'string' ? metadata.entryMode : null,
        sourceGameId: typeof metadata.sourceGameId === 'string' ? metadata.sourceGameId : null,
        promptBundleSnapshot: this.extractTaskMetadataObject<PromptBundleSnapshotPayload>(metadata, 'promptBundleSnapshot'),
        runtimeContract: this.extractTaskMetadataObject<RuntimeContractPayload>(metadata, 'runtimeContract'),
      },
    );
  }

  async processQueuedIterationTask(taskId: string): Promise<void> {
    const task = await this.getTaskWithGame(taskId);
    if (!task || this.isFinalTaskStatus(task.status)) {
      return;
    }
    if (task.upstreamTaskId) {
      await this.reconcileGenerationTask(task);
      return;
    }

    const metadata = this.normalizeTaskMetadataRecord(task.metadata);
    const currentCode = await this.restoreQueuedIterationSourceCode(task.id, task.gameId, metadata);
    await this.executeIterationTask(
      task.gameId,
      task.userId,
      String(metadata.feedback || ''),
      task.version || 1,
      this.normalizeConversationHistory(metadata.conversation),
      currentCode,
      task.timeoutS ?? undefined,
      task.id,
      task.region || undefined,
      {
        pipelineVersion: metadata.pipelineVersion === 'v1' ? 'v1' : 'v2',
        promptBundleSnapshot: this.extractTaskMetadataObject<PromptBundleSnapshotPayload>(metadata, 'promptBundleSnapshot'),
        runtimeContract: this.extractTaskMetadataObject<RuntimeContractPayload>(metadata, 'runtimeContract'),
        orientation: this.normalizeRequestedOrientation(metadata.orientation),
        generationTier: this.normalizeRequestedGenerationTier(metadata.generationTier) || 'standard',
        game: task.game ? {
          gameType: task.game.gameType ?? null,
          status: task.game.status ?? null,
          visibility: task.game.visibility ?? null,
          version: task.game.version ?? null,
          canPlay: task.game.canPlay ?? null,
          requireSubscription: task.game.requireSubscription ?? null,
          accessGrantSource: task.game.accessGrantSource ?? null,
          accessGrantSubscriptionId: task.game.accessGrantSubscriptionId ?? null,
          forkedFrom: task.game.forkedFrom ?? null,
        } : undefined,
        sourceSpec: this.extractTaskMetadataObject<Record<string, unknown>>(metadata, 'sourceSpec'),
        sourceBundleContext: this.extractTaskMetadataObject<SourceBundleContextPayload>(metadata, 'sourceBundleContext'),
      },
    );
  }

  async processQueuedActiveTaskSweep(): Promise<void> {
    await this.reconcileActiveTasksInBackground();
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
      const normalizedGameType = normalizeGameType(
        gameSpec?.game_type,
        gameTitle,
        description,
      );

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
          gameType: normalizedGameType,
          genTimeMs,
          codeSizeBytes,
          qualityScore,
          qualityBreakdown,
        },
        gameTitle: isPlaceholderTitle ? gameTitle : undefined,
        updateData: {
          status: 'draft',
          version: 1,
          gameType: normalizedGameType,
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
            gameType: normalizedGameType,
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
    orientation?: CreateGameOrientation;
    generationTier?: GenerationTier;
    runtimeContract?: RuntimeContractPayload | null;
    allowFinalTaskRecovery?: boolean;
  }): Promise<void> {
    const {
      gameId,
      userId,
      description,
      taskId,
      responseData,
      orientation,
      generationTier,
      runtimeContract,
      allowFinalTaskRecovery = false,
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
      qa_warnings: rawQaWarnings = [],
      runtime_qa_report: rawRuntimeQaReport = undefined,
    } = responseData || {};
    const qaWarnings = this.normalizeQaWarnings(rawQaWarnings);
    const runtimeQaReport = this.normalizeRuntimeQaReport(rawRuntimeQaReport);
    const runtimeQaSummary = this.buildRuntimeQaResultSummary(qaWarnings, runtimeQaReport);
    this.ensurePersistableGeneratedHtml(htmlCode);
    if (allowFinalTaskRecovery) {
      await this.assertTaskCanRecoverResult(taskId, gameId, { allowUnavailableGame: true });
    } else {
      await this.assertTaskCanPersistResult(taskId, gameId);
    }

    const bundlePreviewUrl = this.buildPreviewUrl(gameId);
    const coverUrl = await this.resolvePersistedCoverUrl({
      taskId,
      gameId,
      version: 1,
    });
    const htmlTitleMatch = htmlCode.match(/<title>([^<]{1,60})<\/title>/i);
    const aiTitle = htmlTitleMatch ? htmlTitleMatch[1].trim() : null;
    const gameTitle = (aiTitle && aiTitle.length > 2) ? aiTitle : this.deriveTitle(gameSpec, description);
    const normalizedGameType = normalizeGameType(
      gameSpec?.game_type,
      gameTitle,
      description,
    );
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
        gameType: normalizedGameType,
        ...this.buildPersistedGenerationTierMetadata(generationTier),
        ...this.buildPersistedOrientationMetadata({ orientation, runtimeContract }),
        ...this.buildPersistedCoverMetadata({ gameId, coverUrl, taskId }),
        genTimeMs,
        codeSizeBytes,
        qualityScore,
        qualityBreakdown,
        ...(qaWarnings.length > 0 ? { qaWarnings } : {}),
        ...(runtimeQaReport ? { runtimeQaReport } : {}),
      },
      gameTitle: isPlaceholderTitle ? gameTitle : undefined,
      updateData: {
        status: 'draft',
        version: 1,
        gameType: normalizedGameType,
        ...(coverUrl ? { thumbnailUrl: coverUrl } : {}),
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
        force: allowFinalTaskRecovery,
        resultSummary: {
          strategy,
          qaPassed,
          qaRetries,
          gameType: normalizedGameType,
          generationTier: this.normalizeRequestedGenerationTier(generationTier) || 'standard',
          generationTimeMs: genTimeMs,
          codeSizeBytes,
          qualityScore,
            ...(coverUrl ? { coverGenerated: true } : {}),
            ...runtimeQaSummary,
          },
        });
        await this.syncCreationSessionByTaskId(taskId, {
          status: 'completed',
          generatedGameId: gameId,
          generationTaskId: taskId,
          metadataPatch: {
            lastTaskStatus: 'succeeded',
            generatedGameId: gameId,
            generationTaskId: taskId,
          },
        });
      }

    this.emitStage(userId, gameId, 'completed', {
      stage: 'completed',
      qaRetries,
    });
  }

  async reconcileRelayedTaskFailure(params: {
    taskId: string;
    failedStage: string;
    errorMessage: string;
    retryCount?: number;
    fallback?: string | null;
    timedOut?: boolean;
    failureFamily?: string | null;
    primaryArtifactId?: string | null;
  }): Promise<void> {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: params.taskId },
      select: {
        id: true,
        gameId: true,
        userId: true,
        taskType: true,
      },
    });

    if (!task) {
      return;
    }

    const relayedError = {
      response: {
        data: {
          detail: {
            message: params.errorMessage,
            failed_stage: params.failedStage,
            retry_count: params.retryCount ?? 0,
            fallback: params.fallback ?? undefined,
            failure_family: params.failureFamily ?? undefined,
            primary_artifact_id: params.primaryArtifactId ?? undefined,
            timed_out: Boolean(params.timedOut),
          },
        },
      },
      message: params.errorMessage,
    };

    if (task.taskType === GenerationTaskType.pipeline_run) {
      await this.failPipelineTask({
        gameId: task.gameId,
        userId: task.userId,
        taskId: task.id,
        error: relayedError,
      });
      return;
    }

    await this.failIterationTask({
      gameId: task.gameId,
      userId: task.userId,
      taskId: task.id,
      error: relayedError,
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
      if (
        currentTask
        && (currentTask.status === GenerationTaskStatus.succeeded
          || currentTask.status === GenerationTaskStatus.canceled)
      ) {
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
        await this.syncCreationSessionByTaskId(taskId, {
          status: 'failed',
          generatedGameId: gameId,
          generationTaskId: taskId,
          metadataPatch: {
            lastTaskStatus: 'failed',
            lastErrorMessage: errorMessage,
            lastFailedStage: failure.failedStage || 'pipeline_run',
            generatedGameId: gameId,
            generationTaskId: taskId,
          },
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

  private normalizeQaWarnings(value: unknown): Array<Record<string, any>> {
    if (!Array.isArray(value)) {
      return [];
    }
    return value.filter((item) => item && typeof item === 'object') as Array<Record<string, any>>;
  }

  private normalizeRuntimeQaReport(value: unknown): Record<string, any> | undefined {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return undefined;
    }
    return value as Record<string, any>;
  }

  private buildRuntimeQaResultSummary(
    qaWarnings: Array<Record<string, any>>,
    runtimeQaReport?: Record<string, any>,
  ): Record<string, any> {
    const warningCount = qaWarnings.length;
    const unavailableKind = typeof runtimeQaReport?.unavailableKind === 'string'
      ? runtimeQaReport.unavailableKind
      : undefined;
    const unavailablePhase = typeof runtimeQaReport?.unavailablePhase === 'string'
      ? runtimeQaReport.unavailablePhase
      : undefined;
    const unavailableReason = typeof runtimeQaReport?.unavailableReason === 'string'
      ? runtimeQaReport.unavailableReason
      : undefined;

    return {
      qaWarningCount: warningCount,
      ...(warningCount > 0 ? { qaWarnings } : {}),
      ...(runtimeQaReport ? { runtimeQaReport } : {}),
      ...(unavailableKind || warningCount > 0 ? { runtimeQaUnavailable: Boolean(unavailableKind || warningCount > 0) } : {}),
      ...(unavailableKind ? { runtimeQaUnavailableKind: unavailableKind } : {}),
      ...(unavailablePhase ? { runtimeQaUnavailablePhase: unavailablePhase } : {}),
      ...(unavailableReason ? { runtimeQaUnavailableReason: unavailableReason } : {}),
    };
  }

  private isUpstreamWaitTimeoutError(error: unknown): boolean {
    const message = this.extractErrorMessage(error as any);
    return /upstream ai task .*timed out while waiting for completion/i.test(message);
  }

  private getSuccessArtifactTypes(taskType: GenerationTaskType): string[] {
    return taskType === GenerationTaskType.pipeline_iterate
      ? ['iteration_response']
      : ['pipeline_response'];
  }

  private parseArtifactPayload<T extends Record<string, any>>(artifact: {
    payloadJson?: unknown;
    payloadText?: string | null;
  } | null | undefined): T | null {
    if (!artifact) {
      return null;
    }

    if (artifact.payloadJson && typeof artifact.payloadJson === 'object' && !Array.isArray(artifact.payloadJson)) {
      return artifact.payloadJson as T;
    }

    if (typeof artifact.payloadText === 'string' && artifact.payloadText.trim()) {
      try {
        const parsed = JSON.parse(artifact.payloadText);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
          return parsed as T;
        }
      } catch (error) {
        this.logger.warn(`Failed to parse stored artifact payload as JSON: ${this.extractErrorMessage(error)}`);
      }
    }

    return null;
  }

  private async getStoredTerminalResponseData(
    taskId: string,
    taskType: GenerationTaskType,
  ): Promise<Record<string, any> | null> {
    const artifact = await this.generationTaskService.findLatestArtifactForTask(
      taskId,
      this.getSuccessArtifactTypes(taskType),
    );
    return this.parseArtifactPayload<Record<string, any>>(artifact);
  }

  private async resolvePersistedCoverUrl(params: {
    taskId?: string;
    gameId: string;
    version: number;
  }): Promise<string | null> {
    const { taskId, gameId, version } = params;
    if (!taskId) {
      return null;
    }

    const artifact = await this.generationTaskService.findLatestArtifactForTask(taskId, 'cover_image');
    if (!artifact || artifact.gameId !== gameId) {
      return null;
    }

    const truncated = Boolean(
      artifact.metadata
      && typeof artifact.metadata === 'object'
      && !Array.isArray(artifact.metadata)
      && (artifact.metadata as Record<string, unknown>).truncated,
    );
    if (truncated) {
      return null;
    }

    if (typeof artifact.payloadText !== 'string' || !artifact.payloadText.trim()) {
      return null;
    }
    if (typeof artifact.contentType !== 'string' || !artifact.contentType.startsWith('image/')) {
      return null;
    }

    return this.buildCoverUrl(gameId, { taskId, version });
  }

  private async assertTaskCanRecoverResult(
    taskId?: string,
    gameId?: string,
    options: { allowUnavailableGame?: boolean } = {},
  ): Promise<void> {
    if (!taskId) {
      return;
    }

    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
      select: {
        cancelRequested: true,
        gameId: true,
      },
    });

    if (!task) {
      throw new TaskAbortedError('Task no longer exists');
    }

    if (task.cancelRequested) {
      throw new TaskAbortedError('Task was terminated before completion');
    }

    const effectiveGameId = gameId || task.gameId;
    const game = await this.prisma.game.findUnique({
      where: { id: effectiveGameId },
      select: { status: true },
    });

    if (!game) {
      throw new TaskAbortedError('Game is unavailable');
    }

    if (!options.allowUnavailableGame && game.status === 'banned') {
      throw new TaskAbortedError('Game is unavailable');
    }
  }

  private async recoverTaskFromStoredTerminalResponse(params: {
    task: any;
    game?: any;
    allowFinalTaskRecovery?: boolean;
  }): Promise<any | null> {
    const { task, game, allowFinalTaskRecovery = false } = params;
    if (!task?.id) {
      return null;
    }

    const responseData = await this.getStoredTerminalResponseData(task.id, task.taskType);
    if (!responseData) {
      return null;
    }

    const currentGame = game || await this.prisma.game.findUnique({
      where: { id: task.gameId },
      select: {
        id: true,
        status: true,
        publishedAt: true,
      },
    });

    if (task.taskType === GenerationTaskType.pipeline_run) {
      await this.completePipelineTask({
        gameId: task.gameId,
        userId: task.userId,
        description: String(task.metadata?.description || ''),
        taskId: task.id,
        responseData,
        orientation: this.normalizeRequestedOrientation(task.metadata?.orientation),
        generationTier: this.normalizeRequestedGenerationTier(task.metadata?.generationTier),
        allowFinalTaskRecovery,
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
        responseData,
        orientation: this.normalizeRequestedOrientation(task.metadata?.orientation),
        generationTier: this.normalizeRequestedGenerationTier(task.metadata?.generationTier),
        baseStatus: this.getIterationBaseStatus(task, currentGame),
        allowFinalTaskRecovery,
      });
    }

    return this.prisma.generationTask.findUnique({ where: { id: task.id } });
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
    orientation?: CreateGameOrientation;
    generationTier?: GenerationTier;
    runtimeContract?: RuntimeContractPayload | null;
    baseStatus?: GameStatus;
    allowFinalTaskRecovery?: boolean;
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
      orientation,
      generationTier,
      runtimeContract,
      baseStatus,
      allowFinalTaskRecovery = false,
    } = params;
    const {
      html_code: htmlCode = currentCode,
      iteration_type: iterationType = 'element_change',
      game_spec: gameSpec = undefined,
      generation_time_ms: genTimeMs = 0,
      qa_retries: qaRetries = 0,
      iteration_retries: iterationRetries = 0,
      primary_artifact_id: primaryArtifactId = undefined,
      qa_warnings: rawQaWarnings = [],
      runtime_qa_report: rawRuntimeQaReport = undefined,
    } = responseData || {};
    const qaWarnings = this.normalizeQaWarnings(rawQaWarnings);
    const runtimeQaReport = this.normalizeRuntimeQaReport(rawRuntimeQaReport);
    const runtimeQaSummary = this.buildRuntimeQaResultSummary(qaWarnings, runtimeQaReport);
    this.ensurePersistableGeneratedHtml(htmlCode);
    if (allowFinalTaskRecovery) {
      await this.assertTaskCanRecoverResult(taskId, gameId, { allowUnavailableGame: true });
    } else {
      await this.assertTaskCanPersistResult(taskId, gameId);
    }

    const bundlePreviewUrl = this.buildPreviewUrl(gameId);
    const coverUrl = await this.resolvePersistedCoverUrl({
      taskId,
      gameId,
      version: nextVersion,
    });
    const normalizedGameType = gameSpec?.game_type
      ? normalizeGameType(gameSpec.game_type, feedback)
      : null;
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
        ...(gameSpec ? { gameSpec } : {}),
        ...(normalizedGameType ? { gameType: normalizedGameType } : {}),
        ...this.buildPersistedGenerationTierMetadata(generationTier),
        ...this.buildPersistedOrientationMetadata({ orientation, runtimeContract }),
        ...this.buildPersistedCoverMetadata({ gameId, coverUrl, taskId }),
        genTimeMs,
        qaRetries,
        iterationRetries,
        ...(qaWarnings.length > 0 ? { qaWarnings } : {}),
        ...(runtimeQaReport ? { runtimeQaReport } : {}),
        aiConversation: [
          ...conversationHistory,
          { role: 'user', content: feedback },
        ],
      },
      updateData: {
        ...(baseStatus === GameStatus.published ? {} : { version: nextVersion }),
        status: baseStatus || GameStatus.draft,
        ...(baseStatus === GameStatus.published
          ? {}
          : (normalizedGameType ? { gameType: normalizedGameType } : {})),
        ...(baseStatus === GameStatus.published
          ? {}
          : (coverUrl ? { thumbnailUrl: coverUrl } : {})),
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
        force: allowFinalTaskRecovery,
        resultSummary: {
          feedback,
          iterationType,
          generationTimeMs: genTimeMs,
          qaRetries,
          iterationRetries,
          generationTier: this.normalizeRequestedGenerationTier(generationTier) || 'standard',
          version: nextVersion,
          ...(normalizedGameType ? { gameType: normalizedGameType } : {}),
          ...(coverUrl ? { coverGenerated: true } : {}),
          ...runtimeQaSummary,
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
      if (
        currentTask
        && (currentTask.status === GenerationTaskStatus.succeeded
          || currentTask.status === GenerationTaskStatus.canceled)
      ) {
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
    const generationTier = this.normalizeRequestedGenerationTier(options.generationTier) || 'standard';
    try {
      const resolvedTimeoutS = this.resolvePipelineTimeout(timeoutS);
      const aiEngineBaseUrls = await this.resolveAiEngineEndpointCandidates(executionRegion);
      const resolvedRegion = this.resolveExecutionRegion(executionRegion);
      const pipelineVersion = options.pipelineVersion === 'v1' ? 'v1' : 'v2';
      const normalizedTitle = this.normalizeOptionalString(options.title);
      if (taskId) {
        await this.generationTaskService.markRunning(taskId!);
      }
      const resolvedSourceSpec = pipelineVersion === 'v2'
        ? await this.ensureCreateSourceSpec({
          gameId,
          userId,
          description,
          taskId,
          executionRegion: resolvedRegion,
          title: normalizedTitle,
          generationTier,
          entryMode: options.entryMode,
          sourceSpec: options.sourceSpec,
        })
        : (options.sourceSpec ?? null);
      const runtimeProfileHint = this.inferRuntimeProfileHint(
        this.resolveRuntimeHintGameType(resolvedSourceSpec, null),
        description,
        normalizedTitle,
      );
      const promptBundleSnapshot = pipelineVersion === 'v2'
        ? (options.promptBundleSnapshot ?? await this.buildPromptBundleSnapshot('create', runtimeProfileHint, generationTier))
        : null;
      const runtimeContract = pipelineVersion === 'v2'
        ? (options.runtimeContract ?? await this.buildDefaultRuntimeContract('create', runtimeProfileHint, options.orientation, generationTier))
        : null;

      const handle = await this.requestUpstreamAsyncTask({
        aiEngineBaseUrls,
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
              orientation: options.orientation,
              generationTier,
              access: options.access,
              sourceSpec: resolvedSourceSpec,
              creationSessionId: options.creationSessionId,
              entryMode: options.entryMode,
              sourceGameId: options.sourceGameId,
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
        aiEngineBaseUrl: handle.aiEngineBaseUrl,
        upstreamTaskId: handle.task_id,
        timeoutS: resolvedTimeoutS,
        taskId,
        gameId,
      });

      if (taskId) {
        const localTask = await this.prisma.generationTask.findUnique({
          where: { id: taskId },
          select: { status: true },
        });
        if (localTask && this.isFinalTaskStatus(localTask.status)) {
          return;
        }
      }

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
        orientation: options.orientation,
        generationTier,
        runtimeContract,
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

      if (taskId && this.isUpstreamWaitTimeoutError(error)) {
        const recovered = await this.recoverTaskFromStoredTerminalResponse({
          task: {
            id: taskId,
            gameId,
            userId,
            taskType: GenerationTaskType.pipeline_run,
            metadata: {
              description,
              orientation: options.orientation ?? null,
              generationTier,
            },
          },
        }).catch((recoveryError) => {
          this.logger.warn(
            `Failed to recover timed-out pipeline task ${taskId} from stored artifacts: ${this.extractErrorMessage(recoveryError)}`,
          );
          return null;
        });
        if (recovered) {
          return;
        }
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
    const generationTier = this.normalizeRequestedGenerationTier(options.generationTier)
      ?? this.normalizeRequestedGenerationTier(options.sourceBundleContext?.latest_generation_tier)
      ?? 'standard';
    try {
      const resolvedTimeoutS = this.resolvePipelineTimeout(timeoutS);
      const aiEngineBaseUrls = await this.resolveAiEngineEndpointCandidates(executionRegion);
      const resolvedRegion = this.resolveExecutionRegion(executionRegion);
      const pipelineVersion = options.pipelineVersion === 'v1' ? 'v1' : 'v2';
      const runtimeProfileHint = this.inferRuntimeProfileHint(
        this.resolveRuntimeHintGameType(options.sourceSpec, options.game),
        feedback,
      );
      const promptBundleSnapshot = pipelineVersion === 'v2'
        ? (options.promptBundleSnapshot ?? await this.buildPromptBundleSnapshot('iterate', runtimeProfileHint, generationTier))
        : null;
      const runtimeContract = pipelineVersion === 'v2'
        ? (options.runtimeContract ?? await this.buildDefaultRuntimeContract('iterate', runtimeProfileHint, options.orientation, generationTier))
        : null;
      if (taskId) {
        await this.generationTaskService.markRunning(taskId!);
      }

      const handle = await this.requestUpstreamAsyncTask({
        aiEngineBaseUrls,
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
            orientation: options.orientation,
            generationTier,
            sourceSpec: options.sourceSpec,
            sourceBundleContext: options.sourceBundleContext,
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
        aiEngineBaseUrl: handle.aiEngineBaseUrl,
        upstreamTaskId: handle.task_id,
        timeoutS: resolvedTimeoutS,
        taskId,
        gameId,
      });

      if (taskId) {
        const localTask = await this.prisma.generationTask.findUnique({
          where: { id: taskId },
          select: { status: true },
        });
        if (localTask && this.isFinalTaskStatus(localTask.status)) {
          return;
        }
      }

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
        orientation: options.orientation,
        generationTier,
        runtimeContract,
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

      if (taskId && this.isUpstreamWaitTimeoutError(error)) {
        const recovered = await this.recoverTaskFromStoredTerminalResponse({
          task: {
            id: taskId,
            gameId,
            userId,
            taskType: GenerationTaskType.pipeline_iterate,
            version: nextVersion,
            metadata: {
              feedback,
              conversation: conversationHistory,
              baseStatus: options.game?.status || null,
              orientation: options.orientation ?? null,
              generationTier,
            },
          },
        }).catch((recoveryError) => {
          this.logger.warn(
            `Failed to recover timed-out iteration task ${taskId} from stored artifacts: ${this.extractErrorMessage(recoveryError)}`,
          );
          return null;
        });
        if (recovered) {
          return;
        }
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
      casual: '休闲游戏',
      puzzle: '益智游戏',
      educational: '教育游戏',
      funny: '搞笑游戏',
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
      failedStage: typeof error?.failed_stage === 'string'
        ? error.failed_stage
        : (typeof error?.failedStage === 'string' ? error.failedStage : undefined),
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

  async getGameCoverContent(
    id: string,
    options: { previewToken?: string; taskId?: string } = {},
  ): Promise<{ buffer: Buffer; contentType: string; cacheControl: string }> {
    try {
      const game = await this.prisma.game.findUnique({
        where: { id },
        select: {
          id: true,
          authorId: true,
          status: true,
          visibility: true,
          version: true,
          thumbnailUrl: true,
          forkedFrom: true,
        },
      });
      if (!game) {
        throw new NotFoundException('Game not found');
      }

      const hasPrivilegedPreviewAccess =
        this.hasAdminPreviewAccess(game.id, options.previewToken)
        || Boolean(this.resolveAuthorPreviewAccess(game, options.previewToken));

      if (hasPrivilegedPreviewAccess) {
        if (game.status === 'banned') {
          throw new ForbiddenException('This game is unavailable');
        }
      } else {
        this.assertPublicPreviewAllowed(game);
      }

      let artifact = null;
      const explicitTaskId = typeof options.taskId === 'string' && options.taskId.trim()
        ? options.taskId.trim()
        : null;
      let artifactTaskId = explicitTaskId;
      let artifactId: string | null = null;

      if (!artifactTaskId) {
        const bundle = await this.loadBundleForGame(game, {
          preferLiveVersion: !hasPrivilegedPreviewAccess,
        });
        artifactId = this.extractPersistedCoverArtifactId(bundle?.metadata);
        artifactTaskId = this.extractPersistedCoverTaskId(id, bundle?.metadata)
          ?? this.extractCoverTaskIdFromUrl(id, game.thumbnailUrl);
      }

      if (artifactId) {
        artifact = await this.generationTaskService.findArtifactById(artifactId);
        const isOwnArtifact = artifact?.gameId === id;
        const isForkSourceArtifact = Boolean(
          artifact
          && game.forkedFrom
          && artifact.gameId === game.forkedFrom,
        );
        if (artifact && !isOwnArtifact && !isForkSourceArtifact) {
          artifact = null;
        }
      }

      if (!artifact && artifactTaskId) {
        artifact = await this.generationTaskService.findLatestArtifactForTask(artifactTaskId, 'cover_image');
        const isOwnArtifact = artifact?.gameId === id;
        const isForkSourceArtifact = Boolean(
          artifact
          && game.forkedFrom
          && artifact.gameId === game.forkedFrom,
        );
        if (artifact && !isOwnArtifact && !isForkSourceArtifact) {
          artifact = null;
        }
      }
      if (!artifact && !artifactTaskId && hasPrivilegedPreviewAccess) {
        artifact = await this.generationTaskService.findLatestArtifactForGame(id, 'cover_image');
      }

      if (!artifact || (artifact.gameId !== id && artifact.gameId !== game.forkedFrom)) {
        throw new NotFoundException('Game cover not found');
      }

      const truncated = Boolean(
        artifact.metadata
        && typeof artifact.metadata === 'object'
        && !Array.isArray(artifact.metadata)
        && (artifact.metadata as Record<string, unknown>).truncated,
      );
      if (truncated || typeof artifact.payloadText !== 'string' || !artifact.payloadText.trim()) {
        throw new NotFoundException('Game cover not found');
      }

      const buffer = Buffer.from(artifact.payloadText, 'base64');
      if (!buffer.length) {
        throw new NotFoundException('Game cover not found');
      }

      return {
        buffer,
        contentType: typeof artifact.contentType === 'string' && artifact.contentType.startsWith('image/')
          ? artifact.contentType
          : 'image/jpeg',
        cacheControl: options.previewToken
          ? 'private, no-store'
          : (explicitTaskId ? 'public, max-age=31536000, immutable' : 'public, max-age=300'),
      };
    } catch (error) {
      this.logger.error(`Failed to get game cover: ${error.message}`);
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
      const promotedCoverUrl = this.resolvePersistedCoverUrlForBundle(id, bundle);
      const publishGameType = dto.gameType !== undefined
        ? normalizeGameType(dto.gameType, dto.title, dto.description, dto.tags)
        : normalizeGameType(game.gameType, game.title, game.description, game.tags);

      const publishedGame = await this.prisma.game.update({
        where: { id },
        data: {
          title: dto.title || game.title,
          description: dto.description || game.description,
          tags: dto.tags ?? game.tags ?? [],
          gameType: publishGameType,
          version: liveVersion,
          ...(promotedCoverUrl ? { thumbnailUrl: promotedCoverUrl } : {}),
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

      const bundle = await this.bundleService.getLatestBundle(id);
      if (!this.isBundlePlayable(bundle)) {
        throw new BadRequestException('Cannot iterate a game without a playable bundle');
      }
      const bundleHistory = await this.bundleService.getBundleHistory(id);
      const version = this.resolveNextIterationVersion({
        gameVersion: game.version,
        latestBundle: bundle,
        bundleHistory,
      });
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
      const requestedOrientation = this.extractBundleOrientation([
        ...bundleHistory,
        ...(bundle ? [bundle] : []),
      ]);
      const requestedGenerationTier = this.normalizeRequestedGenerationTier(dto.generationTier)
        ?? this.extractBundleGenerationTier([
          ...bundleHistory,
          ...(bundle ? [bundle] : []),
        ])
        ?? 'standard';
      const sourceSpec = this.extractBundleGameSpec([
        ...bundleHistory,
        ...(bundle ? [bundle] : []),
      ]);
      const sourceBundleContext = this.buildIterationSourceBundleContext({
        game,
        latestBundle: bundle,
        bundleHistory,
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
      const runtimeHintGameType = this.resolveRuntimeHintGameType(sourceSpec, game);
      const promptBundleSnapshot = pipelineVersion === 'v2'
        ? await this.buildPromptBundleSnapshot(
          'iterate',
          this.inferRuntimeProfileHint(runtimeHintGameType, dto.feedback),
          requestedGenerationTier,
        )
        : null;
      const runtimeContract = pipelineVersion === 'v2'
        ? await this.buildDefaultRuntimeContract(
          'iterate',
          this.inferRuntimeProfileHint(runtimeHintGameType, dto.feedback),
          requestedOrientation,
          requestedGenerationTier,
        )
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
            currentBundleVersion: bundle?.version ?? null,
            pipelineVersion,
            orientation: requestedOrientation ?? null,
            generationTier: requestedGenerationTier,
            sourceSpec: sourceSpec ?? null,
            sourceBundleContext: sourceBundleContext ?? null,
            promptBundleSnapshot: promptBundleSnapshot ?? null,
            runtimeContract: runtimeContract ?? null,
          },
          client: tx,
        });
      });

      if (typeof bundle?.htmlCode === 'string' && bundle.htmlCode.trim()) {
        await this.generationTaskService.createArtifact({
          taskId: task.id,
          gameId: id,
          userId,
          artifactType: 'iteration_source_code',
          contentType: 'text/html',
          payload: bundle.htmlCode,
          metadata: {
            bundleVersion: bundle.version ?? null,
            source: 'iterate_request_snapshot',
          },
        }).catch((error) => {
          this.logger.warn(
            `Failed to persist iteration source snapshot for task ${task.id}: ${this.extractErrorMessage(error)}`,
          );
        });
      }

      const enqueued = await this.generationQueueService.enqueueJob(
        GENERATION_QUEUE_JOB_PIPELINE_ITERATE,
        task.id,
      );
      if (!enqueued) {
        this.scheduleLocalIterationExecution(
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
            orientation: requestedOrientation,
            generationTier: requestedGenerationTier,
            game,
            sourceSpec,
            sourceBundleContext,
          },
        );
      }

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
