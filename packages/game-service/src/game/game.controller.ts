import {
  Controller,
  Post,
  Get,
  Delete,
  Patch,
  Param,
  Body,
  Query,
  UseGuards,
  Req,
  Res,
  HttpCode,
  HttpStatus,
  BadRequestException,
  HttpException,
  Header,
  Logger,
  MessageEvent,
} from '@nestjs/common';
import { Response } from 'express';
import { GameService } from './game.service';
import { CreationSessionService } from './creation-session.service';
import { CreationSessionRealtimeService } from './creation-session-realtime.service';
import { CreatorReputationService } from './creator-reputation.service';
import {
  CreateGameDto,
  CreateCreationSessionDto,
  CreateCreationSessionMessageDto,
  GenerateCreationSessionDto,
  IterateGameDto,
  PublishGameDto,
  SkipCreationSessionQuestionDto,
} from './dto';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok, toPage } from '../common/api-response';
import { presentGame } from '../common/game-presenter';

const SUPPORTED_GAME_TYPES = ['casual', 'puzzle', 'education', 'funny'] as const;
const GAME_ID_ROUTE = ':id([0-9a-fA-F-]{36})';
type StreamingResponse = Response & {
  flush?: () => void;
  flushHeaders?: () => void;
};

@Controller('games')
export class GameController {
  private readonly logger = new Logger(GameController.name);

  constructor(
    private gameService: GameService,
    private creationSessionService: CreationSessionService,
    private creationSessionRealtimeService: CreationSessionRealtimeService,
    private reputationService: CreatorReputationService,
  ) {}

  @Post('/expand-prompt')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async expandPrompt(@Body() body: any) {
    const description = body.description || body.prompt || '';
    if (!description) {
      throw new BadRequestException('description is required');
    }

    try {
      const aiEngineUrl = await this.gameService.getAiEngineBaseUrl(body.regionHint);
      const timeoutMs = await this.gameService.getExpandPromptRequestTimeoutMs();
      const response = await require('axios').post(
        `${aiEngineUrl}/api/v1/ai/expand-prompt`,
        { description },
        { timeout: timeoutMs },
      );
      return ok(response.data);
    } catch (error) {
      this.logger.error(`Expand prompt failed: ${error.message}`);
      const status = error?.response?.status;
      const detail = error?.response?.data?.detail
        || error?.response?.data?.message
        || error?.message
        || 'Expand prompt failed';
      if (typeof status === 'number') {
        throw new HttpException(detail, status);
      }
      throw error;
    }
  }

  @Post('/generate')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.CREATED)
  async generateGame(@Req() req: any, @Body() dto: CreateGameDto) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.create(userId, dto));
  }

  @Post('/creation-sessions')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.CREATED)
  async createCreationSession(@Req() req: any, @Body() dto: CreateCreationSessionDto) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.creationSessionService.createSession(userId, dto));
  }

  @Get('/creation-sessions/active')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getActiveCreationSession(@Req() req: any) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.creationSessionService.getActiveSession(userId));
  }

  @Get('/sse-probe')
  @UseGuards(JwtAuthGuard)
  async streamSseProbe(
    @Req() req: any,
    @Res() res: Response,
    @Query('durationMs') durationMsRaw?: string,
    @Query('tickMs') tickMsRaw?: string,
  ): Promise<void> {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    const traceId = `probe:${userId}:${Date.now()}`;
    const durationMs = this.normalizeProbeMs(durationMsRaw, 500, 100, 30_000);
    const tickMs = this.normalizeProbeMs(tickMsRaw, 200, 50, 5_000);
    this.logger.debug(`[creation-session-sse:${traceId}] probe accepted durationMs=${durationMs} tickMs=${tickMs}`);

    this.prepareSseResponse(req, res as StreamingResponse, traceId);
    this.logger.debug(`[creation-session-sse:${traceId}] probe response prepared`);
    this.writeSseFrame(
      res as StreamingResponse,
      'probe.ready',
      {
        ok: true,
        userId,
        durationMs,
        tickMs,
        timestamp: Date.now(),
      },
      undefined,
      traceId,
    );

    let tickCount = 0;
    const timers = [
      setInterval(() => {
        if (!res.writableEnded) {
          tickCount += 1;
          this.writeSseFrame(
            res as StreamingResponse,
            'probe.tick',
            { step: tickCount, durationMs, tickMs, timestamp: Date.now() },
            undefined,
            traceId,
          );
        }
      }, tickMs),
      setTimeout(() => {
        if (!res.writableEnded) {
          this.writeSseFrame(
            res as StreamingResponse,
            'probe.done',
            { ok: true, durationMs, tickMs, timestamp: Date.now() },
            undefined,
            traceId,
          );
          res.end();
        }
      }, durationMs),
    ];

    const cleanup = () => {
      this.logger.debug(`[creation-session-sse:${traceId}] probe cleanup invoked`);
      timers.forEach((timer) => {
        clearTimeout(timer as NodeJS.Timeout);
        clearInterval(timer as NodeJS.Timeout);
      });
      if (!res.writableEnded) {
        res.end();
      }
    };

    req.on('close', cleanup);
    req.on('aborted', cleanup);
    res.on('close', cleanup);
  }

  private normalizeProbeMs(
    value: string | undefined,
    fallback: number,
    min: number,
    max: number,
  ): number {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.min(max, Math.max(min, Math.round(parsed)));
  }

  @Get('/creation-sessions/:sessionId/events')
  @UseGuards(JwtAuthGuard)
  async streamCreationSessionEvents(
    @Req() req: any,
    @Res() res: Response,
    @Param('sessionId') sessionId: string,
  ): Promise<void> {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    const traceId = `${sessionId}:${Date.now()}`;
    this.logger.debug(`[creation-session-sse:${traceId}] request accepted`);

    const snapshot = await this.creationSessionService.getSession(userId, sessionId);
    this.logger.debug(`[creation-session-sse:${traceId}] session snapshot loaded status=${snapshot?.status ?? 'unknown'}`);
    const stream$ = this.creationSessionRealtimeService.streamSession(userId, sessionId, snapshot);

    this.prepareSseResponse(req, res as StreamingResponse, traceId);
    this.logger.debug(`[creation-session-sse:${traceId}] response prepared`);

    let emittedEventCount = 0;
    const subscription = stream$.subscribe({
      next: (event) => {
        emittedEventCount += 1;
        if (emittedEventCount <= 3) {
          this.logger.debug(
            `[creation-session-sse:${traceId}] forwarding event ${String(event?.type || 'message')}`,
          );
        }
        this.writeSseEvent(res as StreamingResponse, event, traceId);
      },
      error: (error) => {
        const message = error?.message || 'Creation session stream failed';
        this.logger.warn(`[creation-session-sse:${traceId}] stream error: ${message}`);
        if (!res.writableEnded) {
          this.writeSseFrame(res as StreamingResponse, 'error', {
            sessionId,
            message,
          }, undefined, traceId);
          res.end();
        }
      },
      complete: () => {
        this.logger.debug(`[creation-session-sse:${traceId}] stream completed`);
        if (!res.writableEnded) {
          res.end();
        }
      },
    });

    const cleanup = () => {
      this.logger.debug(`[creation-session-sse:${traceId}] cleanup invoked`);
      if (!subscription.closed) {
        subscription.unsubscribe();
      }
      if (!res.writableEnded) {
        res.end();
      }
    };

    req.on('close', cleanup);
    req.on('aborted', cleanup);
    res.on('close', cleanup);
  }

  @Get('/creation-sessions/:sessionId')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getCreationSession(@Req() req: any, @Param('sessionId') sessionId: string) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.creationSessionService.getSession(userId, sessionId));
  }

  private prepareSseResponse(req: any, res: StreamingResponse, traceId: string): void {
    const headers: Record<string, string> = {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      'X-Accel-Buffering': 'no',
      'X-Content-Type-Options': 'nosniff',
    };

    if (typeof (res as any).writeHead === 'function') {
      (res as any).writeHead(HttpStatus.OK, headers);
    } else {
      res.status(HttpStatus.OK);
      Object.entries(headers).forEach(([key, value]) => res.setHeader(key, value));
    }

    req.socket?.setKeepAlive?.(true);
    req.socket?.setNoDelay?.(true);
    req.socket?.setTimeout?.(0);
    res.flushHeaders?.();

    const wrotePrelude = res.write(': sse-open\n\n');
    this.logger.debug(`[creation-session-sse:${traceId}] prelude written=${String(wrotePrelude)}`);
    res.flush?.();
  }

  private writeSseEvent(res: StreamingResponse, event: MessageEvent, traceId?: string): void {
    if (res.writableEnded) {
      return;
    }
    const eventType = typeof event.type === 'string' && event.type.trim()
      ? event.type.trim()
      : 'message';
    this.writeSseFrame(res, eventType, event.data, event.id, traceId);
  }

  private writeSseFrame(
    res: StreamingResponse,
    eventType: string,
    data: unknown,
    id?: string | number,
    traceId?: string,
  ): void {
    if (id !== undefined && id !== null) {
      res.write(`id: ${String(id)}\n`);
    }
    res.write(`event: ${eventType}\n`);
    const payload = JSON.stringify(data ?? null);
    res.write(`data: ${payload}\n\n`);
    res.flush?.();
    if (traceId && eventType !== 'heartbeat') {
      this.logger.debug(`[creation-session-sse:${traceId}] frame flushed type=${eventType} bytes=${payload.length}`);
    }
  }

  @Post('/creation-sessions/:sessionId/messages')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async appendCreationSessionMessage(
    @Req() req: any,
    @Param('sessionId') sessionId: string,
    @Body() dto: CreateCreationSessionMessageDto,
  ) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.creationSessionService.appendMessage(userId, sessionId, dto));
  }

  @Post('/creation-sessions/:sessionId/skip')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async skipCreationSessionQuestion(
    @Req() req: any,
    @Param('sessionId') sessionId: string,
    @Body() dto: SkipCreationSessionQuestionDto,
  ) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.creationSessionService.skipCurrentQuestion(userId, sessionId, dto));
  }

  @Post('/creation-sessions/:sessionId/generate')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.CREATED)
  async generateFromCreationSession(
    @Req() req: any,
    @Param('sessionId') sessionId: string,
    @Body() dto: GenerateCreationSessionDto,
  ) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.creationSessionService.generateFromSession(userId, sessionId, dto));
  }

  @Post('/creation-sessions/:sessionId/abandon')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async abandonCreationSession(@Req() req: any, @Param('sessionId') sessionId: string) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.creationSessionService.abandonSession(userId, sessionId));
  }

  @Get('/my/games')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getMyGames(
    @Req() req: any,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '10',
  ) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      const pageNum = Math.max(1, parseInt(page) || 1);
      const limitNum = Math.min(100, Math.max(1, parseInt(limit) || 10));

      const result = await this.gameService.findByAuthor(userId, pageNum, limitNum);
      return ok(toPage({
        data: result.data.map((game: any) => presentGame(game)),
        pagination: result.pagination,
      }));
    } catch (error) {
      this.logger.error(`Error getting user games: ${error.message}`);
      throw error;
    }
  }

  @Get('/my')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getMyGamesAlias(
    @Req() req: any,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '10',
  ) {
    return this.getMyGames(req, page, limit);
  }

  @Get('/explore/published')
  @HttpCode(HttpStatus.OK)
  async getPublishedGames(
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '10',
    @Query('search') search?: string,
  ) {
    try {
      const pageNum = Math.max(1, parseInt(page) || 1);
      const limitNum = Math.min(100, Math.max(1, parseInt(limit) || 10));

      const result = await this.gameService.getGamesByStatus('published', pageNum, limitNum, search);
      return ok(toPage({
        data: result.data.map((game: any) => presentGame(game)),
        pagination: result.pagination,
      }));
    } catch (error) {
      this.logger.error(`Error getting published games: ${error.message}`);
      throw error;
    }
  }

  @Get('/game-types')
  @HttpCode(HttpStatus.OK)
  async getGameTypes() {
    return ok([...SUPPORTED_GAME_TYPES]);
  }

  @Get(':id/generation-status')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getGenerationStatus(@Param('id') id: string, @Req() req: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      return ok(await this.gameService.getGenerationStatus(id, userId));
    } catch (error) {
      this.logger.error(`Error getting generation status: ${error.message}`);
      throw error;
    }
  }

  @Get('/tasks/:taskId')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getTask(@Param('taskId') taskId: string, @Req() req: any) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.getTask(taskId, userId));
  }

  @Get('/tasks/:taskId/events')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getTaskEvents(
    @Param('taskId') taskId: string,
    @Req() req: any,
    @Query('limit') limit?: string,
  ) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.getTaskEvents(taskId, userId, limit ? Number.parseInt(limit, 10) : undefined));
  }

  @Get('/tasks/:taskId/artifacts')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getTaskArtifacts(
    @Param('taskId') taskId: string,
    @Req() req: any,
    @Query('limit') limit?: string,
  ) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.getTaskArtifacts(taskId, userId, limit ? Number.parseInt(limit, 10) : undefined));
  }

  @Post('/tasks/:taskId/cancel')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async cancelTask(@Param('taskId') taskId: string, @Req() req: any) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.cancelTask(taskId, userId));
  }

  @Get(GAME_ID_ROUTE)
  @HttpCode(HttpStatus.OK)
  async getGame(@Param('id') id: string) {
    try {
      const game = await this.gameService.findById(id);
      return ok(presentGame(game));
    } catch (error) {
      this.logger.error(`Error getting game: ${error.message}`);
      throw error;
    }
  }

  @Get(':id/play')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async playGame(@Param('id') id: string, @Req() req: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }
      const htmlCode = await this.gameService.getPlayableHtml(id, userId);
      return ok({
        htmlCode,
        gameId: id,
      });
    } catch (error) {
      this.logger.error(`Error playing game: ${error.message}`);
      throw error;
    }
  }

  @Post(':id/unlock')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async unlockGame(@Param('id') id: string, @Req() req: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      return ok(await this.gameService.unlock(id, userId));
    } catch (error) {
      this.logger.error(`Error unlocking game: ${error.message}`);
      throw error;
    }
  }

  @Post(':id/iterate')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async iterateGame(
    @Param('id') id: string,
    @Req() req: any,
    @Body() dto: IterateGameDto,
  ) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      const result = await this.gameService.iterate(id, userId, dto);
      return ok({
        ...result,
        iterationId: `${result.gameId}:v${result.version}`,
      });
    } catch (error) {
      this.logger.error(`Error iterating game: ${error.message}`);
      throw error;
    }
  }

  @Post(':id/publish')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async publishGame(
    @Param('id') id: string,
    @Req() req: any,
    @Body() dto: PublishGameDto,
  ) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      const game = await this.gameService.publish(id, userId, dto);
      return ok(presentGame(game));
    } catch (error) {
      this.logger.error(`Error publishing game: ${error.message}`);
      throw error;
    }
  }

  @Get(':id/share-data')
  @HttpCode(HttpStatus.OK)
  async getShareData(@Param('id') id: string) {
    try {
      return ok(await this.gameService.getShareData(id));
    } catch (error) {
      this.logger.error(`Error getting share data: ${error.message}`);
      throw error;
    }
  }

  @Get('/creator/:creatorId/reputation')
  @HttpCode(HttpStatus.OK)
  async getCreatorReputation(@Param('creatorId') creatorId: string) {
    try {
      const reputation = await this.reputationService.getReputation(creatorId);
      return ok(reputation);
    } catch (error) {
      this.logger.error(`Error getting creator reputation: ${error.message}`);
      throw error;
    }
  }

  @Patch(':id/settings')
  @UseGuards(JwtAuthGuard)
  async updateGameSettings(@Param('id') id: string, @Req() req: any, @Body() body: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) throw new BadRequestException('Invalid token');
      const result = await this.gameService.updateSettings(id, userId, body);
      return ok(result);
    } catch (error) {
      this.logger.error(`Error updating game settings: ${error.message}`);
      throw error;
    }
  }

  @Delete(':id')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.NO_CONTENT)
  async deleteGame(@Param('id') id: string, @Req() req: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      await this.gameService.delete(id, userId);
    } catch (error) {
      this.logger.error(`Error deleting game: ${error.message}`);
      throw error;
    }
  }
}
