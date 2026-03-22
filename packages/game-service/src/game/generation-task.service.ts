import { Injectable, NotFoundException, ForbiddenException } from '@nestjs/common';
import {
  GenerationTaskEventType,
  GenerationTaskStatus,
  GenerationTaskType,
  Prisma,
} from '@prisma/client';
import { randomUUID } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';

type JsonMap = Record<string, unknown>;

type CreateGenerationTaskParams = {
  gameId: string;
  userId: string;
  taskType: GenerationTaskType;
  region: string;
  timeoutS: number;
  version?: number;
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
};

type SuccessParams = {
  taskId: string;
  previewUrl?: string | null;
  resultSummary?: JsonMap;
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
  success?: boolean;
  upstreamRequestId?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
  errorBodyExcerpt?: string | null;
  configVersion?: number | null;
  routeSnapshot?: JsonMap | null;
};

@Injectable()
export class GenerationTaskService {
  constructor(private readonly prisma: PrismaService) {}

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
        wsChannel: `game:${params.gameId}`,
        metadata: (params.metadata || undefined) as Prisma.InputJsonValue | undefined,
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.status,
      stage: 'queued',
      percentage: 0,
      message: '任务已创建，等待执行',
      details: {
        taskType: task.taskType,
        region: task.region,
        timeoutS: task.timeoutS,
        version: task.version,
      },
    }, client);

    return task;
  }

  async markRunning(taskId: string) {
    const task = await this.prisma.generationTask.update({
      where: { id: taskId },
      data: {
        status: GenerationTaskStatus.running,
        startedAt: new Date(),
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.status,
      stage: 'running',
      percentage: 0,
      message: '任务开始执行',
    });

    return task;
  }

  async recordProgress(params: ProgressParams) {
    const task = await this.findTaskForProgress(params);
    if (!task) {
      return null;
    }

    const updated = await this.prisma.generationTask.update({
      where: { id: task.id },
      data: {
        status: task.status === GenerationTaskStatus.queued ? GenerationTaskStatus.running : task.status,
        startedAt: task.startedAt ?? new Date(),
        progressStage: params.stage,
        progressPct: params.percentage,
        progressMessage: params.message,
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.progress,
      stage: params.stage,
      percentage: params.percentage,
      message: params.message,
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
    if (!task) {
      return null;
    }

    const nextPercentage = params.percentage ?? task.progressPct ?? 0;
    const updated = await this.prisma.generationTask.update({
      where: { id: task.id },
      data: {
        status: task.status === GenerationTaskStatus.queued ? GenerationTaskStatus.running : task.status,
        startedAt: task.startedAt ?? new Date(),
        progressStage: params.stage,
        progressPct: nextPercentage,
        progressMessage: params.message.slice(0, 255),
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.note,
      stage: params.stage,
      percentage: nextPercentage,
      message: params.message,
      details: {
        stepKey: params.stepKey ?? null,
        ...(params.details || {}),
      },
    });

    return updated;
  }

  async markSucceeded(params: SuccessParams) {
    const task = await this.prisma.generationTask.update({
      where: { id: params.taskId },
      data: {
        status: GenerationTaskStatus.succeeded,
        completedAt: new Date(),
        progressStage: 'completed',
        progressPct: 100,
        progressMessage: '任务执行完成',
        previewUrl: params.previewUrl ?? undefined,
        resultSummary: (params.resultSummary || undefined) as Prisma.InputJsonValue | undefined,
        errorMessage: null,
        failedStage: null,
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.status,
      stage: 'completed',
      percentage: 100,
      message: '任务执行完成',
      details: params.resultSummary,
    });

    return task;
  }

  async markFailed(params: FailureParams) {
    const task = await this.prisma.generationTask.update({
      where: { id: params.taskId },
      data: {
        status: params.timedOut ? GenerationTaskStatus.timed_out : GenerationTaskStatus.failed,
        completedAt: new Date(),
        failedStage: params.failedStage,
        errorMessage: params.errorMessage,
        retryCount: params.retryCount ?? 0,
        fallback: params.fallback ?? undefined,
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
      },
    });

    return task;
  }

  async requestCancel(taskId: string, userId: string) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
    });

    if (!task) {
      throw new NotFoundException('Task not found');
    }
    if (task.userId !== userId) {
      throw new ForbiddenException('You do not have permission to cancel this task');
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
      stage: updated.progressStage || 'running',
      percentage: updated.progressPct ?? 0,
      message: '已收到取消请求',
    });

    return updated;
  }

  async markCanceled(taskId: string) {
    const task = await this.prisma.generationTask.update({
      where: { id: taskId },
      data: {
        status: GenerationTaskStatus.canceled,
        completedAt: new Date(),
        progressMessage: '任务已取消',
      },
    });

    await this.appendEvent(task.id, {
      gameId: task.gameId,
      userId: task.userId,
      eventType: GenerationTaskEventType.status,
      stage: task.progressStage || 'canceled',
      percentage: task.progressPct ?? 0,
      message: '任务已取消',
    });

    return task;
  }

  async ingestLlmCallLog(params: LlmCallLogParams) {
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
        success: params.success ?? false,
        upstreamRequestId: params.upstreamRequestId ?? undefined,
        errorCode: params.errorCode ?? undefined,
        errorMessage: params.errorMessage ?? undefined,
        errorBodyExcerpt: params.errorBodyExcerpt ?? undefined,
        configVersion: params.configVersion ?? undefined,
        routeSnapshot: (params.routeSnapshot || undefined) as Prisma.InputJsonValue | undefined,
      },
    });

    if (taskId) {
      await this.prisma.generationTask.update({
        where: { id: taskId },
        data: {
          gatewayConfigVersion: params.configVersion ?? undefined,
          routeSnapshot: (params.routeSnapshot || undefined) as Prisma.InputJsonValue | undefined,
        },
      }).catch(() => undefined);

      await this.appendEvent(taskId, {
        gameId: params.gameId,
        userId: params.userId,
        eventType: GenerationTaskEventType.llm_call,
        stage: params.stage,
        percentage: null,
        message: params.success
          ? `${params.stepKey} 调用 ${params.providerName || params.providerType || 'LLM'} 成功`
          : `${params.stepKey} 调用失败: ${(params.errorMessage || params.errorCode || 'unknown error').slice(0, 200)}`,
        details: {
          stepKey: params.stepKey,
          providerName: params.providerName,
          providerType: params.providerType,
          model: params.model,
          latencyMs: params.latencyMs,
          httpStatus: params.httpStatus,
          success: params.success ?? false,
          errorCode: params.errorCode ?? null,
        },
      }).catch(() => undefined);
    }

    return created;
  }

  async getTaskForUser(taskId: string, userId: string) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
    });

    if (!task) {
      throw new NotFoundException('Task not found');
    }
    if (task.userId !== userId) {
      throw new ForbiddenException('You do not have permission to view this task');
    }

    return task;
  }

  async listTaskEvents(taskId: string, userId: string, limit = 200) {
    await this.getTaskForUser(taskId, userId);

    return this.prisma.generationTaskEvent.findMany({
      where: { taskId, userId },
      orderBy: { createdAt: 'asc' },
      take: Math.min(Math.max(limit, 1), 500),
    });
  }

  async getLatestTaskForGame(gameId: string, userId: string) {
    return this.prisma.generationTask.findFirst({
      where: { gameId, userId },
      orderBy: { createdAt: 'desc' },
    });
  }

  toTaskSummary(task: any) {
    return {
      taskId: task.id,
      taskType: task.taskType,
      region: task.region,
      status: task.status,
      timeoutS: task.timeoutS,
      wsChannel: task.wsChannel,
      pollUrl: `/api/v1/games/tasks/${task.id}`,
      eventsUrl: `/api/v1/games/tasks/${task.id}/events`,
      cancelUrl: `/api/v1/games/tasks/${task.id}/cancel`,
      gameId: task.gameId,
      version: task.version ?? null,
      progressStage: task.progressStage,
      progressPct: task.progressPct,
      progressMessage: task.progressMessage,
      failedStage: task.failedStage,
      errorMessage: task.errorMessage,
      retryCount: task.retryCount,
      fallback: task.fallback,
      cancelRequested: task.cancelRequested,
      previewUrl: task.previewUrl,
      gatewayConfigVersion: task.gatewayConfigVersion,
      routeSnapshot: task.routeSnapshot,
      resultSummary: task.resultSummary,
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
        details: (params.details || undefined) as Prisma.InputJsonValue | undefined,
      },
    });
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
      orderBy: { createdAt: 'desc' },
    });
  }
}
