import {
  Injectable,
  Logger,
  BadRequestException,
  NotFoundException,
  ForbiddenException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'crypto';
import axios from 'axios';
import { GameStatus } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { BundleService } from '../bundle/bundle.service';
import { StatsService } from '../stats/stats.service';
import { GameWebSocketGateway } from '../websocket/websocket.gateway';
import { CreateGameDto, PublishGameDto, IterateGameDto } from './dto';

// Pipeline stage labels for WebSocket progress events
const STAGE_LABELS: Record<string, string> = {
  intent_parsing: '解析游戏意图',
  designing: '设计游戏参数',
  template_matching: '匹配游戏模板',
  code_generating: '生成游戏代码',
  qa_checking: '质量检测',
  completed: '生成完成',
  failed: '生成失败',
};

const STAGE_PCT: Record<string, number> = {
  intent_parsing: 15,
  designing: 30,
  template_matching: 40,
  code_generating: 60,
  qa_checking: 80,
  completed: 100,
  failed: -1,
};

/** Retry an async operation up to `maxAttempts` times on network/5xx errors */
async function withRetry<T>(
  fn: () => Promise<T>,
  maxAttempts: number = 2,
  delayMs: number = 3000,
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err: any) {
      lastError = err;
      // Do NOT retry on timeout (ECONNABORTED) — AI engine already started processing,
      // a retry would launch a duplicate pipeline job
      const isRetryable =
        err.response && err.response.status >= 500;
      if (!isRetryable || attempt === maxAttempts) break;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  throw lastError;
}

@Injectable()
export class GameService {
  private readonly logger = new Logger(GameService.name);
  private aiEngineUrl: string;

  constructor(
    private prisma: PrismaService,
    private bundleService: BundleService,
    private statsService: StatsService,
    private configService: ConfigService,
    private wsGateway: GameWebSocketGateway,
  ) {
    this.aiEngineUrl = this.configService.get<string>(
      'AI_ENGINE_URL',
      'http://localhost:8000',
    );
  }

  private buildPreviewUrl(gameId: string): string {
    const appUrl = this.configService.get<string>('APP_URL', 'http://localhost:3002');
    return `${appUrl.replace(/\/$/, '')}/games/${gameId}/preview`;
  }

  private async attachPreviewUrl<T extends { id: string }>(game: T): Promise<T & { previewUrl: string }> {
    const bundle = await this.bundleService.getLatestBundle(game.id);
    return {
      ...game,
      previewUrl: bundle?.previewUrl || this.buildPreviewUrl(game.id),
    };
  }

  private async attachPreviewUrls<T extends { id: string }>(
    games: T[],
  ): Promise<Array<T & { previewUrl: string }>> {
    return Promise.all(games.map((game) => this.attachPreviewUrl(game)));
  }

  async create(userId: string, dto: CreateGameDto): Promise<any> {
    try {
      const gameId = randomUUID();
      const description = dto.description || dto.prompt || '';

      const game = await this.prisma.game.create({
        data: {
          id: gameId,
          authorId: userId,
          description,
          status: 'generating',
          title: dto.title?.trim() || `Game ${gameId.substring(0, 8)}`,
          commentCount: 0,
          forkDepth: 0,
        },
      });

      this.wsGateway.emitGenerationProgress(userId, gameId, 'started', 0);

      // Run pipeline asynchronously – client subscribes to WebSocket for progress
      setImmediate(() => {
        this.runPipeline(gameId, userId, description);
      });

      return {
        gameId,
        wsChannel: `game:${gameId}`,
        status: 'generating',
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
  ): Promise<void> {
    try {
      // Emit initial stage
      this.emitStage(userId, gameId, 'intent_parsing');

      const response = await withRetry(() =>
        axios.post(
          `${this.aiEngineUrl}/api/v1/ai/pipeline/run`,
          {
            game_id: gameId,
            description,
            user_id: userId,
            platform: 'wechat_webview',
          },
          { timeout: 660000 },
        )
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

      this.emitStage(userId, gameId, 'qa_checking');

      const bundlePreviewUrl = this.buildPreviewUrl(gameId);

      await this.bundleService.saveBundle({
        gameId,
        version: 1,
        htmlCode,
        cssCode: '',
        jsCode: '',
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
        previewUrl: bundlePreviewUrl,
      });

      // Extract <title> from generated HTML to use as game title if not user-set
      const htmlTitleMatch = htmlCode.match(/<title>([^<]{1,60})<\/title>/i);
      const aiTitle = htmlTitleMatch ? htmlTitleMatch[1].trim() : null;

      // Only overwrite title if it's still the auto-generated placeholder (Game [id])
      const currentGame = await this.prisma.game.findUnique({ where: { id: gameId }, select: { title: true } });
      const isPlaceholderTitle = /^Game\s+[0-9a-f]{8}$/i.test(currentGame?.title || '');

      await this.prisma.game.update({
        where: { id: gameId },
        data: {
          status: 'draft',
          version: 1,
          gameType: gameSpec?.game_type || null,
          qualityScore,
          ...(aiTitle && isPlaceholderTitle ? { title: aiTitle } : {}),
        },
      });

      this.emitStage(userId, gameId, 'completed');
      this.wsGateway.emitGenerationComplete(userId, gameId, bundlePreviewUrl);
    } catch (error) {
      this.logger.error(`Pipeline failed for game ${gameId}: ${error.message}`);

      await this.prisma.game.update({
        where: { id: gameId },
        data: { status: 'failed' },
      });

      this.wsGateway.emitNotification(userId, {
        type: 'error',
        message: `Game generation failed: ${error.message}`,
        gameId,
      });
    }
  }

  private emitStage(userId: string, gameId: string, stage: string): void {
    const pct = STAGE_PCT[stage] ?? 50;
    const label = STAGE_LABELS[stage] ?? stage;
    this.wsGateway.emitGenerationProgress(userId, gameId, label, pct);
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
              email: true,
              avatarUrl: true,
            },
          },
        },
      });

      if (!game) {
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
        data: await this.attachPreviewUrls(games),
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

  async getPlayData(id: string): Promise<string> {
    try {
      const game = await this.prisma.game.findUnique({ where: { id } });

      if (!game) throw new NotFoundException('Game not found');

      const bundle = await this.bundleService.getLatestBundle(id);
      if (!bundle) throw new NotFoundException('Game bundle not found');

      await this.statsService.incrementPlayCount(id);

      return bundle.htmlCode;
    } catch (error) {
      this.logger.error(`Failed to get play data: ${error.message}`);
      throw error;
    }
  }

  async publish(id: string, userId: string, dto: PublishGameDto): Promise<any> {
    try {
      const game = await this.prisma.game.findUnique({ where: { id } });
      if (!game) throw new NotFoundException('Game not found');
      if (game.authorId !== userId) {
        throw new ForbiddenException('You do not have permission to publish this game');
      }

      const publishedGame = await this.prisma.game.update({
        where: { id },
        data: {
          title: dto.title || game.title,
          description: dto.description || game.description,
          tags: dto.tags ?? game.tags ?? [],
          gameType: dto.gameType || game.gameType,
          status: 'published',
          publishedAt: new Date(),
        },
        include: {
          author: {
            select: { id: true, username: true, avatarUrl: true },
          },
        },
      });

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

      await this.prisma.game.update({
        where: { id },
        data: { status: 'banned' },
      });
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

      const version = (game.version || 1) + 1;
      this.wsGateway.emitGenerationProgress(userId, id, '分析修改意图', 20);

      const bundle = await this.bundleService.getLatestBundle(id);
      const bundleHistory = await this.bundleService.getBundleHistory(id);
      const conversationHistory = bundleHistory.map((b) => ({
        version: b.version,
        feedback: b.metadata?.feedback || '',
      }));

      setImmediate(() => {
        this.runIteration(id, userId, dto.feedback, version, conversationHistory, bundle?.htmlCode || '');
      });

      return { gameId: id, version, status: 'iterating' };
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
  ): Promise<void> {
    try {
      this.wsGateway.emitGenerationProgress(userId, gameId, '生成代码修改', 40);

      const response = await withRetry(() =>
        axios.post(
          `${this.aiEngineUrl}/api/v1/ai/pipeline/iterate`,
          {
            game_id: gameId,
            feedback,
            conversation: conversationHistory,
            current_code: currentCode,
          },
          { timeout: 660000 },
        )
      );

      const {
        html_code: htmlCode = currentCode,
        iteration_type: iterationType = 'element_change',
        generation_time_ms: genTimeMs = 0,
      } = response.data;

      this.wsGateway.emitGenerationProgress(userId, gameId, '质量检测', 80);

      const bundlePreviewUrl = this.buildPreviewUrl(gameId);

      await this.bundleService.saveBundle({
        gameId,
        version: nextVersion,
        htmlCode,
        cssCode: '',
        jsCode: '',
        metadata: { feedback, iterationType, genTimeMs },
        previewUrl: bundlePreviewUrl,
      });

      await this.prisma.game.update({
        where: { id: gameId },
        data: { version: nextVersion, status: 'draft' },
      });

      this.wsGateway.emitGenerationProgress(userId, gameId, '迭代完成', 100);
      this.wsGateway.emitGenerationComplete(userId, gameId, bundlePreviewUrl);
    } catch (error) {
      this.logger.error(`Iteration failed for game ${gameId}: ${error.message}`);
      this.wsGateway.emitNotification(userId, {
        type: 'error',
        message: `Game iteration failed: ${error.message}`,
        gameId,
      });
    }
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

    const appUrl = this.configService.get<string>('APP_URL', 'http://localhost:3002');
    const gameUrl = `${appUrl.replace(/\/$/, '')}/games/${id}`;

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
  ): Promise<any> {
    try {
      const skip = (page - 1) * limit;

      const [games, total] = await Promise.all([
        this.prisma.game.findMany({
          where: { status: status as GameStatus },
          include: {
            author: {
              select: { id: true, username: true, avatarUrl: true },
            },
          },
          skip,
          take: limit,
          orderBy: { createdAt: 'desc' },
        }),
        this.prisma.game.count({ where: { status: status as GameStatus } }),
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
