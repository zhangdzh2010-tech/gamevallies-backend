import {
  BadRequestException,
  Controller,
  Headers,
  HttpException,
  HttpStatus,
  Post,
  Body,
} from '@nestjs/common';
import { GameWebSocketGateway } from '../websocket/websocket.gateway';
import { ok } from '../common/api-response';
import { GenerationTaskService } from './generation-task.service';
import { GameService } from './game.service';

let startupAdminToken: string | null = null;

function getStartupAdminToken(): string {
  if (startupAdminToken === null) {
    const configuredToken = (process.env.ADMIN_TOKEN || '').trim();
    if (!configuredToken) {
      throw new HttpException('Admin token is not configured', HttpStatus.SERVICE_UNAVAILABLE);
    }
    startupAdminToken = configuredToken;
  }
  return startupAdminToken;
}

function assertInternalToken(token?: string): void {
  const expected = (process.env.ADMIN_TOKEN || '').trim();
  if (!expected) {
    throw new HttpException('Admin token is not configured', HttpStatus.SERVICE_UNAVAILABLE);
  }
  const startupToken = getStartupAdminToken();
  if (!token || (token !== expected && token !== startupToken)) {
    throw new HttpException('Unauthorized', HttpStatus.UNAUTHORIZED);
  }
}

function shouldSuppressTaskActivity(details: Record<string, unknown>): boolean {
  const activityState = typeof details.activityState === 'string' ? details.activityState : '';
  return activityState === 'started' || activityState === 'heartbeat' || activityState === 'completed';
}

@Controller('internal/generation')
export class InternalGenerationController {
  constructor(
    private readonly wsGateway: GameWebSocketGateway,
    private readonly generationTaskService: GenerationTaskService,
    private readonly gameService: GameService,
  ) {
    if (startupAdminToken === null) {
      startupAdminToken = getStartupAdminToken();
    }
  }

  @Post('progress')
  async relayProgress(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    assertInternalToken(token);

    const {
      taskId,
      userId,
      gameId,
      stage,
      percentage,
      message,
      details = {},
    } = body || {};

    if (
      !userId ||
      !gameId ||
      typeof stage !== 'string' ||
      typeof message !== 'string' ||
      typeof percentage !== 'number'
    ) {
      throw new BadRequestException('Invalid progress payload');
    }

    await this.generationTaskService.recordProgress({
      taskId,
      gameId,
      userId,
      stage,
      percentage,
      message,
      details,
    });

    this.wsGateway.emitGenerationProgress(
      userId,
      gameId,
      message,
      percentage,
      { ...details, stage },
    );

    return ok({ relayed: true });
  }

  @Post('llm-call-log')
  async ingestLlmCallLog(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    assertInternalToken(token);

    const {
      taskId,
      userId,
      gameId,
      stage,
      stepKey,
    } = body || {};

    if (
      !userId ||
      !gameId ||
      typeof stage !== 'string' ||
      typeof stepKey !== 'string'
    ) {
      throw new BadRequestException('Invalid llm call log payload');
    }

    const log = await this.generationTaskService.persistLlmCallLog({
      taskId,
      gameId,
      userId,
      stage,
      stepKey,
      providerId: body.providerId,
      providerName: body.providerName,
      providerType: body.providerType,
      region: body.region,
      model: body.model,
      requestTimeoutS: body.requestTimeoutS,
      connectTimeoutS: body.connectTimeoutS,
      latencyMs: body.latencyMs,
      httpStatus: body.httpStatus,
      inputTokens: body.inputTokens,
      outputTokens: body.outputTokens,
      totalTokens: body.totalTokens,
      success: body.success,
      upstreamRequestId: body.upstreamRequestId,
      errorCode: body.errorCode,
      errorMessage: body.errorMessage,
      errorBodyExcerpt: body.errorBodyExcerpt,
      configVersion: body.configVersion,
      routeSnapshot: body.routeSnapshot,
    });

    return ok({ relayed: true, id: log.id });
  }

  @Post('task-activity')
  async relayTaskActivity(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    assertInternalToken(token);

    const {
      taskId,
      userId,
      gameId,
      stage,
      stepKey,
      message,
      percentage,
      details = {},
    } = body || {};

    if (
      !userId ||
      !gameId ||
      typeof stage !== 'string' ||
      typeof message !== 'string'
    ) {
      throw new BadRequestException('Invalid task activity payload');
    }

    const task = await this.generationTaskService.recordActivity({
      taskId,
      userId,
      gameId,
      stage,
      stepKey,
      message,
      percentage: typeof percentage === 'number' ? percentage : undefined,
      details,
    });

    if (shouldSuppressTaskActivity(details)) {
      return ok({ relayed: false, suppressed: true, persisted: Boolean(task) });
    }

    const progressPct = task?.progressPct ?? (typeof percentage === 'number' ? percentage : 0);
    this.wsGateway.emitGenerationProgress(
      userId,
      gameId,
      message,
      progressPct,
      {
        ...details,
        stage,
        stepKey: stepKey || undefined,
      },
    );

    return ok({ relayed: true });
  }

  @Post('stage-summary')
  async relayStageSummary(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    assertInternalToken(token);

    const {
      taskId,
      stage,
      message,
      percentage,
      conclusionType,
      details = {},
      artifactIds = [],
    } = body || {};

    if (
      typeof taskId !== 'string'
      || typeof stage !== 'string'
      || typeof message !== 'string'
    ) {
      throw new BadRequestException('Invalid stage summary payload');
    }

    await this.generationTaskService.recordStageSummary({
      taskId,
      stage,
      message,
      percentage: typeof percentage === 'number' ? percentage : undefined,
      conclusionType,
      details,
      artifactIds: Array.isArray(artifactIds) ? artifactIds : [],
    });

    return ok({ relayed: true });
  }

  @Post('artifact')
  async createArtifact(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    assertInternalToken(token);

    const {
      taskId,
      gameId,
      userId,
      artifactType,
      contentType,
      payload,
      metadata = {},
      expiresAt,
    } = body || {};

    if (
      typeof gameId !== 'string'
      || typeof userId !== 'string'
      || typeof artifactType !== 'string'
      || typeof contentType !== 'string'
      || typeof payload === 'undefined'
    ) {
      throw new BadRequestException('Invalid artifact payload');
    }

    const artifact = await this.generationTaskService.createArtifact({
      taskId: typeof taskId === 'string' ? taskId : undefined,
      gameId,
      userId,
      artifactType,
      contentType,
      payload,
      metadata,
      expiresAt: typeof expiresAt === 'string' ? new Date(expiresAt) : undefined,
    });

    return ok({ relayed: true, id: artifact.id });
  }

  @Post('task-failure')
  async relayTaskFailure(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    assertInternalToken(token);

    const {
      taskId,
      failedStage,
      errorMessage,
      retryCount,
      fallback,
      timedOut,
      failureFamily,
      primaryArtifactId,
      details = {},
    } = body || {};

    if (
      typeof taskId !== 'string'
      || typeof failedStage !== 'string'
      || typeof errorMessage !== 'string'
    ) {
      throw new BadRequestException('Invalid task failure payload');
    }

    await this.generationTaskService.recordTaskFailure({
      taskId,
      failedStage,
      errorMessage,
      retryCount: typeof retryCount === 'number' ? retryCount : undefined,
      fallback: typeof fallback === 'string' ? fallback : undefined,
      timedOut: Boolean(timedOut),
      failureFamily: typeof failureFamily === 'string' ? failureFamily : undefined,
      primaryArtifactId: typeof primaryArtifactId === 'string' ? primaryArtifactId : undefined,
      details,
    });

    await this.gameService.reconcileRelayedTaskFailure({
      taskId,
      failedStage,
      errorMessage,
      retryCount: typeof retryCount === 'number' ? retryCount : undefined,
      fallback: typeof fallback === 'string' ? fallback : undefined,
      timedOut: Boolean(timedOut),
      failureFamily: typeof failureFamily === 'string' ? failureFamily : undefined,
      primaryArtifactId: typeof primaryArtifactId === 'string' ? primaryArtifactId : undefined,
    });

    return ok({ relayed: true });
  }
}
