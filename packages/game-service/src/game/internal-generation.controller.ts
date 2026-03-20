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
  constructor(private readonly wsGateway: GameWebSocketGateway) {
    if (startupAdminToken === null) {
      startupAdminToken = process.env.ADMIN_TOKEN || 'admin123';
    }
  }

  @Post('progress')
  relayProgress(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    assertInternalToken(token);

    const {
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

    this.wsGateway.emitGenerationProgress(
      userId,
      gameId,
      message,
      percentage,
      { ...details, stage },
    );

    return ok({ relayed: true });
  }
}
