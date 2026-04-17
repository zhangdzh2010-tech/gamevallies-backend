import {
  Injectable,
  NotFoundException,
  ForbiddenException,
} from "@nestjs/common";
import {
  GenerationTaskEventType,
  GenerationTaskStatus,
  GenerationTaskType,
  Prisma,
} from "@prisma/client";
import { createHash, randomUUID } from "crypto";
import { PrismaService } from "../prisma/prisma.service";
import { normalizeIntentBuildSnapshot } from "./intent-build.util";
import {
  resolvePublicGenerationStage,
} from "./generation-stage-contract";

type JsonMap = Record<string, unknown>;

type ArtifactStorageType =
  | "inline_json"
  | "inline_text"
  | "external"
  | "omitted";

type CreateGenerationTaskParams = {
  gameId: string;
  userId: string;
  taskType: GenerationTaskType;
  region: string;
  timeoutS: number;
  version?: number;
  pipelineVersion?: string | null;
  promptBundleId?: string | null;
  promptBundleVersion?: number | null;
  runtimeProfile?: string | null;
  contractVersion?: string | null;
  metadata?: JsonMap;
  client?: PrismaService | Prisma.TransactionClient;
};

type ProgressParams = {
  taskId?: string;
  gameId: string;
  userId: string;
  stage: string;
  percentage: number;
  message: string;
  details?: JsonMap;
};

type ActivityParams = {
  taskId?: string;
  gameId: string;
  userId: string;
  stage: string;
  stepKey?: string | null;
  message: string;
  percentage?: number | null;
  details?: JsonMap;
};

type FailureParams = {
  taskId: string;
  failedStage: string;
  errorMessage: string;
  retryCount?: number;
  fallback?: string | null;
  timedOut?: boolean;
  failureFamily?: string | null;
  primaryArtifactId?: string | null;
  details?: JsonMap;
};

type SuccessParams = {
  taskId: string;
  previewUrl?: string | null;
  resultSummary?: JsonMap;
  primaryArtifactId?: string | null;
  force?: boolean;
};

type ArtifactParams = {
  taskId?: string | null;
  gameId: string;
  userId: string;
  artifactType: string;
  contentType: string;
  payload: unknown;
  expiresAt?: Date | null;
  metadata?: JsonMap;
};

type StageSummaryParams = {
  taskId: string;
  stage: string;
  message: string;
  percentage?: number | null;
  conclusionType?: "summary" | "warning" | "failure";
  details?: JsonMap;
  artifactIds?: string[];
};

type LlmCallLogParams = {
  taskId?: string | null;
  gameId: string;
  userId: string;
  stage: string;
  stepKey: string;
  providerId?: string | null;
  providerName?: string | null;
  providerType?: string | null;
  region?: string | null;
  model?: string | null;
  requestTimeoutS?: number | null;
  connectTimeoutS?: number | null;
  latencyMs?: number | null;
  httpStatus?: number | null;
  inputTokens?: number | null;
  outputTokens?: number | null;
  totalTokens?: number | null;
  success?: boolean;
  upstreamRequestId?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  errorBodyExcerpt?: string | null;
  configVersion?: number | null;
  routeSnapshot?: JsonMap | null;
};

const MAX_INLINE_JSON_BYTES = 20 * 1024;
const MAX_INLINE_TEXT_BYTES = 512 * 1024;
const SUPPRESSED_TASK_ACTIVITY_STATES = new Set(["started", "completed"]);
const SLOW_LLM_CALL_THRESHOLD_MS = 30_000;

@Injectable()
export class GenerationTaskService {
  constructor(private readonly prisma: PrismaService) {}

  private isFinalStatus(status?: GenerationTaskStatus | null): boolean {
    return (
      status === GenerationTaskStatus.succeeded ||
      status === GenerationTaskStatus.failed ||
      status === GenerationTaskStatus.canceled ||
      status === GenerationTaskStatus.timed_out
    );
  }

  async createTask(params: CreateGenerationTaskParams) {
    const client = params.client || this.prisma;

    const task = await client.generationTask.create({
      data: {
        id: randomUUID(),
        gameId: params.gameId,
        userId: params.userId,
        taskType: params.taskType,
        region: params.region,
        status: GenerationTaskStatus.queued,
        timeoutS: params.timeoutS,
        version: params.version,
        pipelineVersion: params.pipelineVersion ?? undefined,
        promptBundleId: params.promptBundleId ?? undefined,
        promptBundleVersion: params.promptBundleVersion ?? undefined,
        runtimeProfile: params.runtimeProfile ?? undefined,
        contractVersion: params.contractVersion ?? undefined,
        wsChannel: `game:${params.gameId}`,
        metadata: (params.metadata || undefined) as
          | Prisma.InputJsonValue
          | undefined,
      },
    });

    await this.appendEvent(
      task.id,
      {
        gameId: task.gameId,
        userId: task.userId,
        eventType: GenerationTaskEventType.status,
        stage: "queued",
        percentage: 0,
        message: "任务已创建，等待执行",
        details: {
          taskType: task.taskType,
          region: task.region,
          timeoutS: task.timeoutS,
          version: task.version,
          pipelineVersion: task.pipelineVersion,
          promptBundleId: task.promptBundleId,
          promptBundleVersion: task.promptBundleVersion,
          runtimeProfile: task.runtimeProfile,
          contractVersion: task.contractVersion,
        },
      },
      client,
    );

    return task;
  }

  async markRunning(taskId: string) {
    const existing = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
    });

    if (!existing || this.isFinalStatus(existing.status)) {
      return existing;
    }

    const { task } = await this.ensureTaskRunning(existing);
    return task;
  }

  async recordProgress(params: ProgressParams) {
    const task = await this.findTaskForProgress(params);
    if (!task || this.isFinalStatus(task.status)) {
      return null;
    }

    const { task: runningTask } = await this.ensureTaskRunning(task);
    if (!runningTask || this.isFinalStatus(runningTask.status)) {
      return null;
    }

    const nextMessage = this.normalizeTaskMessage(params.message);
    const snapshotChanged = this.hasTaskSnapshotChanged(
      runningTask,
      params.stage,
      params.percentage,
      nextMessage,
    );

    if (!snapshotChanged) {
      return runningTask;
    }

    const updated = await this.prisma.generationTask.update({
      where: { id: runningTask.id },
      data: {
        progressStage: params.stage,
        progressPct: params.percentage,
        progressMessage: nextMessage,
        ...this.buildTaskProgressMetadataUpdate(
          runningTask,
          params.stage,
          params.details,
        ),
      },
    });

    await this.appendEvent(runningTask.id, {
      gameId: runningTask.gameId,
      userId: runningTask.userId,
      eventType: GenerationTaskEventType.progress,
      stage: params.stage,
      percentage: params.percentage,
      message: nextMessage,
      details: params.details,
    });

    return updated;
  }

  async recordActivity(params: ActivityParams) {
    const task = await this.findTaskForProgress({
      taskId: params.taskId,
      gameId: params.gameId,
      userId: params.userId,
      stage: params.stage,
      percentage: params.percentage ?? 0,
      message: params.message,
      details: params.details,
    });
    if (!task || this.isFinalStatus(task.status)) {
      return null;
    }

    if (this.isHeartbeatTaskActivity(params.details)) {
      const { task: runningTask } = await this.ensureTaskRunning(task);
      if (!runningTask || this.isFinalStatus(runningTask.status)) {
        return null;
      }

      const nextPercentage = params.percentage ?? runningTask.progressPct ?? 0;
      const nextMessage = this.normalizeTaskMessage(params.message);
      return this.prisma.generationTask.update({
        where: { id: runningTask.id },
        data: {
          progressStage: params.stage,
          progressPct: nextPercentage,
          progressMessage: nextMessage,
        },
      });
    }

    if (this.shouldSuppressTaskActivity(params.details)) {
      return task;
    }

    const { task: runningTask } = await this.ensureTaskRunning(task);
    if (!runningTask || this.isFinalStatus(runningTask.status)) {
      return null;
    }

    const nextPercentage = params.percentage ?? runningTask.progressPct ?? 0;
    const nextMessage = this.normalizeTaskMessage(params.message);
    const snapshotChanged = this.hasTaskSnapshotChanged(
      runningTask,
      params.stage,
      nextPercentage,
      nextMessage,
    );

    if (!snapshotChanged) {
      return runningTask;
    }

    const updated = await this.prisma.generationTask.update({
      where: { id: runningTask.id },
      data: {
        progressStage: params.stage,
        progressPct: nextPercentage,
        progressMessage: nextMessage,
      },
    });

    await this.appendEvent(runningTask.id, {
      gameId: runningTask.gameId,
      userId: runningTask.userId,
      eventType: GenerationTaskEventType.note,
      stage: params.stage,
      percentage: nextPercentage,
      message: nextMessage,
      details: {
        stepKey: params.stepKey ?? null,
        ...(params.details || {}),
      },
    });

    return updated;
  }

  async markSucceeded(params: SuccessParams) {
    const existing = await this.prisma.generationTask.findUnique({
      where: { id: params.taskId },
    });

    if (!existing) {
      return existing;
    }

    if (this.isFinalStatus(existing.status) && !params.force) {
      return existing;
    }

    const task = await this.prisma.generationTask.update({
      where: { id: params.taskId },
      data: {
        status: GenerationTaskStatus.succeeded,
        completedAt: new Date(),
        progressStage: "completed",
        progressPct: 100,
        progressMessage: "任务执行完成",
        previewUrl: params.previewUrl ?? undefined,
        resultSummary: (params.resultSummary || undefined) as
          | Prisma.InputJsonValue
          | undefined,
        primaryArtifactId: params.primaryArtifactId ?? undefined,
        errorMessage: null,
        failedStage: null,
        failureFamily: null,
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.status,
      stage: "completed",
      percentage: 100,
      message: "任务执行完成",
      details: params.resultSummary,
    });

    return task;
  }

  async markFailed(params: FailureParams) {
    const existing = await this.prisma.generationTask.findUnique({
      where: { id: params.taskId },
    });

    if (!existing || this.isFinalStatus(existing.status)) {
      return existing;
    }

    const task = await this.prisma.generationTask.update({
      where: { id: params.taskId },
      data: {
        status: params.timedOut
          ? GenerationTaskStatus.timed_out
          : GenerationTaskStatus.failed,
        completedAt: new Date(),
        failedStage: params.failedStage,
        errorMessage: params.errorMessage,
        retryCount: params.retryCount ?? 0,
        fallback: params.fallback ?? undefined,
        failureFamily: params.failureFamily ?? undefined,
        primaryArtifactId: params.primaryArtifactId ?? undefined,
        progressStage: params.failedStage,
        progressMessage: params.errorMessage.slice(0, 255),
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.error,
      stage: params.failedStage,
      percentage: task.progressPct ?? 0,
      message: params.errorMessage.slice(0, 255),
      details: {
        retryCount: params.retryCount ?? 0,
        fallback: params.fallback ?? null,
        timedOut: params.timedOut ?? false,
        failureFamily: params.failureFamily ?? null,
        primaryArtifactId: params.primaryArtifactId ?? null,
        ...(params.details || {}),
      },
    });

    return task;
  }

  async requestCancel(taskId: string, userId: string) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
    });

    if (!task) {
      throw new NotFoundException("Task not found");
    }
    if (task.userId !== userId) {
      throw new ForbiddenException(
        "You do not have permission to cancel this task",
      );
    }

    const updated = await this.prisma.generationTask.update({
      where: { id: taskId },
      data: {
        cancelRequested: true,
      },
    });

    await this.appendEvent(taskId, {
      gameId: updated.gameId,
      userId: updated.userId,
      eventType: GenerationTaskEventType.note,
      stage: updated.progressStage || "running",
      percentage: updated.progressPct ?? 0,
      message: "已收到取消请求",
    });

    return updated;
  }

  async markCanceled(taskId: string) {
    const task = await this.prisma.generationTask.update({
      where: { id: taskId },
      data: {
        status: GenerationTaskStatus.canceled,
        completedAt: new Date(),
        progressMessage: "任务已取消",
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.status,
      stage: task.progressStage || "canceled",
      percentage: task.progressPct ?? 0,
      message: "任务已取消",
    });

    return task;
  }

  async ingestLlmCallLog(params: LlmCallLogParams) {
    return this.persistLlmCallLog(params);
  }

  async persistLlmCallLog(params: LlmCallLogParams) {
    const taskId = params.taskId || null;

    const created = await this.prisma.llmCallLog.create({
      data: {
        id: randomUUID(),
        taskId,
        gameId: params.gameId,
        userId: params.userId,
        stage: params.stage,
        stepKey: params.stepKey,
        providerId: params.providerId ?? undefined,
        providerName: params.providerName ?? undefined,
        providerType: params.providerType ?? undefined,
        region: params.region ?? undefined,
        model: params.model ?? undefined,
        requestTimeoutS: params.requestTimeoutS ?? undefined,
        connectTimeoutS: params.connectTimeoutS ?? undefined,
        latencyMs: params.latencyMs ?? undefined,
        httpStatus: params.httpStatus ?? undefined,
        inputTokens: params.inputTokens ?? undefined,
        outputTokens: params.outputTokens ?? undefined,
        totalTokens: params.totalTokens ?? undefined,
        success: params.success ?? false,
        upstreamRequestId: params.upstreamRequestId ?? undefined,
        errorCode: params.errorCode ?? undefined,
        errorMessage: params.errorMessage ?? undefined,
        errorBodyExcerpt: params.errorBodyExcerpt ?? undefined,
        configVersion: params.configVersion ?? undefined,
        routeSnapshot: (params.routeSnapshot || undefined) as
          | Prisma.InputJsonValue
          | undefined,
      },
    });

    if (taskId) {
      await this.prisma.generationTask
        .update({
          where: { id: taskId },
          data: {
            gatewayConfigVersion: params.configVersion ?? undefined,
            routeSnapshot: (params.routeSnapshot || undefined) as
              | Prisma.InputJsonValue
              | undefined,
          },
        })
        .catch(() => undefined);

      await this.appendLlmCallTimelineEvent(taskId, params).catch(
        () => undefined,
      );
    }

    return created;
  }

  async createArtifact(params: ArtifactParams) {
    const normalized = this.normalizeArtifactPayload(params.payload);
    const metadata: JsonMap = {
      ...(params.metadata || {}),
      storageType: normalized.storageType,
      truncated: normalized.truncated,
      normalizedContentType: params.contentType,
    };

    return this.prisma.generationArtifact.create({
      data: {
        id: randomUUID(),
        taskId: params.taskId ?? undefined,
        gameId: params.gameId,
        userId: params.userId,
        artifactType: params.artifactType,
        contentType: params.contentType,
        storageType: normalized.storageType,
        payloadJson: normalized.payloadJson,
        payloadText: normalized.payloadText,
        payloadUrl: normalized.payloadUrl,
        sha256: normalized.sha256 ?? undefined,
        sizeBytes: normalized.sizeBytes ?? undefined,
        compression: normalized.compression ?? undefined,
        expiresAt: params.expiresAt ?? undefined,
        metadata: metadata as Prisma.InputJsonValue,
      },
    });
  }

  async listArtifactsForTask(taskId: string, userId: string, limit = 50) {
    await this.getTaskForUser(taskId, userId);

    return this.prisma.generationArtifact.findMany({
      where: { taskId, userId },
      orderBy: { createdAt: "desc" },
      take: Math.min(Math.max(limit, 1), 200),
    });
  }

  async findLatestArtifactForTask(
    taskId: string,
    artifactTypes: string | string[],
  ) {
    const types = Array.isArray(artifactTypes)
      ? artifactTypes.filter((item) => typeof item === "string" && item.trim())
      : [artifactTypes].filter(
          (item) => typeof item === "string" && item.trim(),
        );

    if (types.length === 0) {
      return null;
    }

    return this.prisma.generationArtifact.findFirst({
      where: {
        taskId,
        artifactType: {
          in: types,
        },
      },
      orderBy: { createdAt: "desc" },
    });
  }

  async findLatestArtifactForGame(
    gameId: string,
    artifactTypes: string | string[],
  ) {
    const types = Array.isArray(artifactTypes)
      ? artifactTypes.filter((item) => typeof item === "string" && item.trim())
      : [artifactTypes].filter(
          (item) => typeof item === "string" && item.trim(),
        );

    if (types.length === 0) {
      return null;
    }

    return this.prisma.generationArtifact.findFirst({
      where: {
        gameId,
        artifactType: {
          in: types,
        },
      },
      orderBy: { createdAt: "desc" },
    });
  }

  async findArtifactById(id: string) {
    if (typeof id !== "string" || !id.trim()) {
      return null;
    }

    return this.prisma.generationArtifact.findUnique({
      where: { id: id.trim() },
    });
  }

  async recordStageSummary(params: StageSummaryParams) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: params.taskId },
    });
    if (!task || this.isFinalStatus(task.status)) {
      return task;
    }

    const nextPercentage = params.percentage ?? task.progressPct ?? 0;
    const details: JsonMap = {
      conclusionType: params.conclusionType ?? "summary",
      artifactIds: params.artifactIds ?? [],
      ...(params.details || {}),
    };

    const updated = await this.prisma.generationTask.update({
      where: { id: params.taskId },
      data: {
        progressStage: params.stage,
        progressPct: nextPercentage,
        progressMessage: params.message.slice(0, 255),
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType:
        params.conclusionType === "failure"
          ? GenerationTaskEventType.error
          : GenerationTaskEventType.note,
      stage: params.stage,
      percentage: nextPercentage,
      message: params.message,
      details,
    });

    return updated;
  }

  async recordTaskFailure(params: FailureParams) {
    return this.markFailed(params);
  }

  async getTaskForUser(taskId: string, userId: string) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
    });

    if (!task) {
      throw new NotFoundException("Task not found");
    }
    if (task.userId !== userId) {
      throw new ForbiddenException(
        "You do not have permission to view this task",
      );
    }

    return task;
  }

  async listTaskEvents(taskId: string, userId: string, limit = 200) {
    await this.getTaskForUser(taskId, userId);

    return this.prisma.generationTaskEvent.findMany({
      where: { taskId, userId },
      orderBy: { createdAt: "asc" },
      take: Math.min(Math.max(limit, 1), 500),
    });
  }

  async getLatestTaskForGame(gameId: string, userId: string) {
    return this.prisma.generationTask.findFirst({
      where: { gameId, userId },
      orderBy: { createdAt: "desc" },
    });
  }

  private getDisplayStage(task: {
    status?: string | null;
    progressStage?: string | null;
    failedStage?: string | null;
    progressPct?: number | null;
  }) {
    const rawStage = String(
      task.failedStage ||
        task.progressStage ||
        (task.status === GenerationTaskStatus.succeeded
          ? "completed"
          : task.status || "submitting"),
    ).trim();
    const stage = resolvePublicGenerationStage(rawStage, task.progressPct);

    return {
      displayStageKey: stage.displayStageKey,
      displayStageLabel: stage.displayStageLabel,
      displayStageIndex: stage.displayStageIndex,
      displayStagePct: stage.displayStagePct,
      displayStageTotal: stage.displayStageTotal,
      rawStage: stage.rawStage,
    };
  }

  toTaskSummary(task: any) {
    const displayStage = this.getDisplayStage(task);
    const metadata =
      task?.metadata &&
      typeof task.metadata === "object" &&
      !Array.isArray(task.metadata)
        ? (task.metadata as JsonMap)
        : {};
    const intentBuild = normalizeIntentBuildSnapshot(metadata.intentBuild);

    return {
      taskId: task.id,
      taskType: task.taskType,
      region: task.region,
      status: task.status,
      timeoutS: task.timeoutS,
      wsChannel: task.wsChannel,
      pollUrl: `/api/v1/games/tasks/${task.id}`,
      eventsUrl: `/api/v1/games/tasks/${task.id}/events`,
      artifactsUrl: `/api/v1/games/tasks/${task.id}/artifacts`,
      cancelUrl: `/api/v1/games/tasks/${task.id}/cancel`,
      gameId: task.gameId,
      version: task.version ?? null,
      pipelineVersion: task.pipelineVersion ?? null,
      promptBundleId: task.promptBundleId ?? null,
      promptBundleVersion: task.promptBundleVersion ?? null,
      runtimeProfile: task.runtimeProfile ?? null,
      contractVersion: task.contractVersion ?? null,
      failureFamily: task.failureFamily ?? null,
      primaryArtifactId: task.primaryArtifactId ?? null,
      progressStage: task.progressStage,
      progressPct: task.progressPct,
      progressMessage: task.progressMessage,
      displayStageKey: displayStage.displayStageKey,
      displayStageLabel: displayStage.displayStageLabel,
      displayStageIndex: displayStage.displayStageIndex,
      displayStagePct: displayStage.displayStagePct,
      displayStageTotal: displayStage.displayStageTotal,
      rawStage: displayStage.rawStage,
      failedStage: task.failedStage,
      errorMessage: task.errorMessage,
      retryCount: task.retryCount,
      fallback: task.fallback,
      cancelRequested: task.cancelRequested,
      previewUrl: task.previewUrl,
      gatewayConfigVersion: task.gatewayConfigVersion,
      routeSnapshot: task.routeSnapshot,
      resultSummary: task.resultSummary,
      intentBuild,
      startedAt: task.startedAt,
      completedAt: task.completedAt,
      createdAt: task.createdAt,
      updatedAt: task.updatedAt,
    };
  }

  private async appendEvent(
    taskId: string,
    params: {
      gameId: string;
      userId: string;
      eventType: GenerationTaskEventType;
      stage?: string | null;
      percentage?: number | null;
      message: string;
      details?: JsonMap | null;
    },
    client: PrismaService | Prisma.TransactionClient = this.prisma,
  ) {
    return client.generationTaskEvent.create({
      data: {
        id: randomUUID(),
        taskId,
        gameId: params.gameId,
        userId: params.userId,
        eventType: params.eventType,
        stage: params.stage ?? undefined,
        percentage: params.percentage ?? undefined,
        message: params.message.slice(0, 255),
        details: (params.details || undefined) as
          | Prisma.InputJsonValue
          | undefined,
      },
    });
  }

  private normalizeTaskMessage(message: string): string {
    return message.slice(0, 255);
  }

  private shouldSuppressTaskActivity(details?: JsonMap | null): boolean {
    return SUPPRESSED_TASK_ACTIVITY_STATES.has(
      this.getTaskActivityState(details),
    );
  }

  private isHeartbeatTaskActivity(details?: JsonMap | null): boolean {
    return this.getTaskActivityState(details) === "heartbeat";
  }

  private getTaskActivityState(details?: JsonMap | null): string {
    return typeof details?.activityState === "string"
      ? details.activityState
      : "";
  }

  private hasTaskSnapshotChanged(
    task: {
      progressStage?: string | null;
      progressPct?: number | null;
      progressMessage?: string | null;
    },
    stage: string,
    percentage: number,
    message: string,
  ): boolean {
    return (
      task.progressStage !== stage ||
      (task.progressPct ?? null) !== percentage ||
      (task.progressMessage || "") !== message
    );
  }

  private async ensureTaskRunning(task: {
    id: string;
    gameId: string;
    userId: string;
    status: GenerationTaskStatus;
    startedAt?: Date | null;
    progressStage?: string | null;
    progressPct?: number | null;
    progressMessage?: string | null;
    runtimeProfile?: string | null;
    metadata?: Prisma.JsonValue | null;
  }) {
    if (this.isFinalStatus(task.status)) {
      return { task, transitioned: false };
    }

    if (task.status === GenerationTaskStatus.queued) {
      const startedAt = new Date();
      const transitioned = await this.prisma.generationTask.updateMany({
        where: {
          id: task.id,
          status: GenerationTaskStatus.queued,
        },
        data: {
          status: GenerationTaskStatus.running,
          startedAt,
        },
      });

      if (transitioned.count > 0) {
        const refreshed = await this.prisma.generationTask.findUnique({
          where: { id: task.id },
        });
        const runningTask = refreshed || {
          ...task,
          status: GenerationTaskStatus.running,
          startedAt,
        };

        await this.appendEvent(runningTask.id, {
          gameId: runningTask.gameId,
          userId: runningTask.userId,
          eventType: GenerationTaskEventType.status,
          stage: "running",
          percentage: 0,
          message: "任务开始执行",
        });

        return {
          task: runningTask,
          transitioned: true,
        };
      }

      const refreshed = await this.prisma.generationTask.findUnique({
        where: { id: task.id },
      });
      if (refreshed) {
        task = refreshed;
      }
    }

    if (task.status === GenerationTaskStatus.running && !task.startedAt) {
      const updated = await this.prisma.generationTask.update({
        where: { id: task.id },
        data: {
          startedAt: new Date(),
        },
      });

      return {
        task: updated,
        transitioned: false,
      };
    }

    return { task, transitioned: false };
  }

  private async appendLlmCallTimelineEvent(
    taskId: string,
    params: LlmCallLogParams,
  ) {
    const summary = this.buildLlmCallTimelineSummary(params);
    if (!summary) {
      return;
    }

    await this.appendEvent(taskId, {
      gameId: params.gameId,
      userId: params.userId,
      eventType: GenerationTaskEventType.llm_call,
      stage: params.stage,
      percentage: null,
      message: summary.message,
      details: summary.details,
    });
  }

  private buildLlmCallTimelineSummary(
    params: LlmCallLogParams,
  ): { message: string; details: JsonMap } | null {
    const providerLabel = params.providerName || params.providerType || "LLM";
    const httpStatus =
      typeof params.httpStatus === "number" ? params.httpStatus : null;
    const latencyMs =
      typeof params.latencyMs === "number" ? params.latencyMs : null;
    const inputTokens =
      typeof params.inputTokens === "number" ? params.inputTokens : null;
    const outputTokens =
      typeof params.outputTokens === "number" ? params.outputTokens : null;
    const totalTokens =
      typeof params.totalTokens === "number"
        ? params.totalTokens
        : inputTokens !== null && outputTokens !== null
          ? inputTokens + outputTokens
          : null;
    const isFailure =
      params.success === false ||
      !!params.errorCode ||
      !!params.errorMessage ||
      (httpStatus !== null && httpStatus >= 400);

    if (isFailure) {
      const reason = (
        params.errorMessage ||
        params.errorCode ||
        (httpStatus !== null ? `HTTP ${httpStatus}` : "unknown error")
      ).slice(0, 180);
      return {
        message: `${params.stepKey} 调用 ${providerLabel} 失败: ${reason}`,
        details: {
          summaryType: "failure",
          stepKey: params.stepKey,
          providerName: params.providerName ?? null,
          providerType: params.providerType ?? null,
          model: params.model ?? null,
          latencyMs,
          httpStatus,
          inputTokens,
          outputTokens,
          totalTokens,
          errorCode: params.errorCode ?? null,
          success: params.success ?? false,
        },
      };
    }

    if (latencyMs !== null && latencyMs >= SLOW_LLM_CALL_THRESHOLD_MS) {
      const durationLabel =
        latencyMs >= 100_000
          ? `${Math.round(latencyMs / 1000)}s`
          : `${(latencyMs / 1000).toFixed(1)}s`;
      return {
        message: `${params.stepKey} 调用 ${providerLabel} 较慢（${durationLabel}）`,
        details: {
          summaryType: "slow_call",
          stepKey: params.stepKey,
          providerName: params.providerName ?? null,
          providerType: params.providerType ?? null,
          model: params.model ?? null,
          latencyMs,
          httpStatus,
          inputTokens,
          outputTokens,
          totalTokens,
          success: params.success ?? true,
        },
      };
    }

    return null;
  }

  private buildTaskProgressMetadataUpdate(
    task: {
      runtimeProfile?: string | null;
      metadata?: Prisma.JsonValue | null;
    },
    stage: string,
    details?: JsonMap,
  ): Prisma.GenerationTaskUpdateInput {
    const data: Prisma.GenerationTaskUpdateInput = {};
    const runtimeProfile = this.extractRuntimeProfileFromProgress(
      stage,
      details,
    );
    if (runtimeProfile && runtimeProfile !== (task.runtimeProfile || null)) {
      data.runtimeProfile = runtimeProfile;
    }

    const selectedGameType = this.extractGameTypeFromProgress(stage, details);
    if (runtimeProfile || selectedGameType) {
      const metadata = this.mergeTaskMetadata(task.metadata, {
        ...(runtimeProfile ? { selectedRuntimeProfile: runtimeProfile } : {}),
        ...(selectedGameType ? { selectedGameType } : {}),
      });
      data.metadata = metadata as Prisma.InputJsonValue;
    }

    return data;
  }

  private extractRuntimeProfileFromProgress(
    stage: string,
    details?: JsonMap,
  ): string | undefined {
    if (!details) {
      return undefined;
    }
    if (
      stage !== "runtime_profile_select" &&
      stage !== "contract_compose" &&
      stage !== "logic_generate"
    ) {
      return undefined;
    }
    const value = details.runtimeProfile;
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
  }

  private extractGameTypeFromProgress(
    stage: string,
    details?: JsonMap,
  ): string | undefined {
    if (!details || stage !== "runtime_profile_select") {
      return undefined;
    }
    const value = details.gameType;
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
  }

  private mergeTaskMetadata(
    current: Prisma.JsonValue | null | undefined,
    patch: JsonMap,
  ): JsonMap {
    const base =
      current && typeof current === "object" && !Array.isArray(current)
        ? { ...(current as JsonMap) }
        : {};
    return {
      ...base,
      ...patch,
    };
  }

  private normalizeArtifactPayload(payload: unknown): {
    storageType: ArtifactStorageType;
    payloadJson?: Prisma.InputJsonValue;
    payloadText?: string;
    payloadUrl?: string;
    sha256?: string;
    sizeBytes?: number;
    compression?: string;
    truncated: boolean;
  } {
    if (typeof payload === "string") {
      const sizeBytes = Buffer.byteLength(payload, "utf8");
      const sha256 = createHash("sha256").update(payload).digest("hex");
      if (sizeBytes <= MAX_INLINE_TEXT_BYTES) {
        return {
          storageType: "inline_text",
          payloadText: payload,
          sha256,
          sizeBytes,
          truncated: false,
        };
      }
      return {
        storageType: "omitted",
        payloadText: payload.slice(0, MAX_INLINE_TEXT_BYTES),
        sha256,
        sizeBytes,
        truncated: true,
      };
    }

    const serialized = JSON.stringify(payload ?? null);
    const sizeBytes = Buffer.byteLength(serialized, "utf8");
    const sha256 = createHash("sha256").update(serialized).digest("hex");
    if (sizeBytes <= MAX_INLINE_JSON_BYTES) {
      return {
        storageType: "inline_json",
        payloadJson: (payload ?? null) as Prisma.InputJsonValue,
        sha256,
        sizeBytes,
        truncated: false,
      };
    }

    if (sizeBytes <= MAX_INLINE_TEXT_BYTES) {
      return {
        storageType: "inline_text",
        payloadText: serialized,
        sha256,
        sizeBytes,
        truncated: false,
      };
    }

    return {
      storageType: "omitted",
      payloadText: serialized.slice(0, MAX_INLINE_TEXT_BYTES),
      sha256,
      sizeBytes,
      truncated: true,
    };
  }

  private async findTaskForProgress(params: ProgressParams) {
    if (params.taskId) {
      return this.prisma.generationTask.findUnique({
        where: { id: params.taskId },
      });
    }

    return this.prisma.generationTask.findFirst({
      where: {
        gameId: params.gameId,
        userId: params.userId,
        status: {
          in: [GenerationTaskStatus.queued, GenerationTaskStatus.running],
        },
      },
      orderBy: { createdAt: "desc" },
    });
  }
}
