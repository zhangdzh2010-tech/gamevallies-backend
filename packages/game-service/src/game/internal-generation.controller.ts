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

let startupAdminToken: string | null = null;

function getStartupAdminToken(): string {
  if (startupAdminToken === null) {
    startupAdminToken = process.env.ADMIN_TOKEN || 'admin123';
  }
  return startupAdminToken;
}

function assertInternalToken(token?: string): void {
  const expected = process.env.ADMIN_TOKEN || 'admin123';
  const startupToken = getStartupAdminToken();
  if (!token || (token !== expected && token !== startupToken)) {
    throw new HttpException('Unauthorized', HttpStatus.UNAUTHORIZED);
  }
}

@Controller('internal/generation')
export class InternalGenerationController {
  constructor(
    private readonly wsGateway: GameWebSocketGateway,
    private readonly generationTaskService: GenerationTaskService,
  ) {
    if (startupAdminToken === null) {
      startupAdminToken = process.env.ADMIN_TOKEN || 'admin123';
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

    const log = await this.generationTaskService.ingestLlmCallLog({
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
}
