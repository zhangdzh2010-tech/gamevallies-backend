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

  private getPublicBaseUrl(): string {
    const publicApiBaseUrl = this.configService.get<string>('PUBLIC_API_BASE_URL');
    const appUrl = this.configService.get<string>('APP_URL', 'http://localhost:3002');
    return (publicApiBaseUrl || appUrl).replace(/\/$/, '');
  }

  private buildPreviewUrl(gameId: string): string {
    return `${this.getPublicBaseUrl()}/games/${gameId}/preview`;
  }

  private async attachPreviewUrl<T extends { id: string }>(game: T): Promise<T & { previewUrl: string }> {
    return {
      ...game,
      previewUrl: this.buildPreviewUrl(game.id),
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
          failedStage: null,
          failedReason: null,
          retryCount: 0,
          lastErrorAt: null,
          title: dto.title?.trim() || `Game ${gameId.substring(0, 8)}`,
          commentCount: 0,
          forkDepth: 0,
        },
      });

      this.emitProgress(userId, gameId, 'started', 0, {
        stage: 'started',
        attempt: 1,
        maxAttempts: 1,
      });

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
              },
            );
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

      this.emitStage(userId, gameId, 'completed', {
        stage: 'completed',
        qaRetries,
      });
    } catch (error) {
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
      });

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
      };
    }

    return {
      message: this.extractErrorMessage(error),
      failedStage: undefined,
      retryCount: 0,
      fallback: undefined,
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
  }): Promise<void> {
    const { gameId, failedStage, failedReason, retryCount, status } = params;
    await this.prisma.game.update({
      where: { id: gameId },
      data: {
        ...(status ? { status } : {}),
        failedStage,
        failedReason,
        retryCount,
        lastErrorAt: new Date(),
      },
    });
  }

  private async persistGeneratedGameResult(params: {
    gameId: string;
    userId: string;
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
        const existingBundle = await this.bundleService.getBundle(gameId, version);
        if (!existingBundle) {
          await this.bundleService.saveBundle({
            gameId,
            version,
            htmlCode,
            cssCode: '',
            jsCode: '',
            metadata,
            previewUrl,
          });
        }

        await this.prisma.game.update({
          where: { id: gameId },
          data: {
            ...updateData,
            ...(gameTitle ? { title: gameTitle } : {}),
          },
        });

        this.wsGateway.emitGenerationComplete(userId, gameId, previewUrl);
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
      const response = await withRetry(() =>
        axios.post(
          `${this.aiEngineUrl}/api/v1/ai/pipeline/iterate`,
          {
            game_id: gameId,
            feedback,
            user_id: userId,
            conversation: conversationHistory,
            current_code: currentCode,
          },
          { timeout: 660000 },
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

      this.wsGateway.emitGenerationProgress(userId, gameId, '迭代完成', 100);
    } catch (error) {
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
