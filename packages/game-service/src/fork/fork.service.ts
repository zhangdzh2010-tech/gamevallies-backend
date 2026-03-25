import {
  Injectable,
  Logger,
  NotFoundException,
  BadRequestException,
} from '@nestjs/common';
import { randomUUID } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';
import { BundleService } from '../bundle/bundle.service';
import { StatsService } from '../stats/stats.service';

@Injectable()
export class ForkService {
  private readonly logger = new Logger(ForkService.name);

  constructor(
    private prisma: PrismaService,
    private bundleService: BundleService,
    private statsService: StatsService,
  ) {}

  private isPublicForkVisible(game: { status?: string | null; visibility?: string | null } | null | undefined): boolean {
    return game?.status === 'published' && (game.visibility || 'public') === 'public';
  }

  private isBundlePlayable(bundle: { htmlCode?: string | null } | null | undefined): boolean {
    return typeof bundle?.htmlCode === 'string' && bundle.htmlCode.trim().length > 0;
  }

  private async loadForkSourceBundle(game: { id: string; version?: number | null }) {
    if (Number.isFinite(game.version) && Number(game.version) > 0) {
      const liveBundle = await this.bundleService.getBundle(game.id, Number(game.version));
      if (liveBundle) {
        return liveBundle;
      }
    }

    return this.bundleService.getLatestBundle(game.id);
  }

  private sanitizeForkBundleMetadata(originalBundle: any, gameId: string) {
    const metadata = originalBundle?.metadata && typeof originalBundle.metadata === 'object'
      ? { ...originalBundle.metadata }
      : {};

    delete (metadata as any).generationTaskId;
    delete (metadata as any).routeSnapshot;
    delete (metadata as any).previewUrl;
    delete (metadata as any).pollUrl;
    delete (metadata as any).cancelUrl;
    delete (metadata as any).upstreamTaskId;

    return {
      ...metadata,
      forkedFromGameId: gameId,
      forkedFromBundleId: originalBundle?.id || null,
      forkedFromVersion: originalBundle?.version ?? 1,
      forkedAt: new Date().toISOString(),
    };
  }

  async forkGame(gameId: string, userId: string): Promise<any> {
    try {
      const originalGame = await this.prisma.game.findUnique({
        where: { id: gameId },
      });

      if (!originalGame) {
        throw new NotFoundException('Original game not found');
      }

      if (originalGame.authorId === userId) {
        throw new BadRequestException('Cannot fork your own game');
      }
      if (originalGame.status !== 'published') {
        throw new BadRequestException('Only published games can be forked');
      }
      if ((originalGame.visibility || 'public') !== 'public') {
        throw new BadRequestException('This game is not available for forking');
      }
      if (originalGame.allowFork === false) {
        throw new BadRequestException('Forking is disabled for this game');
      }

      const originalBundle = await this.loadForkSourceBundle(originalGame);
      if (!this.isBundlePlayable(originalBundle)) {
        throw new BadRequestException('Source game is not ready to be forked');
      }

      const newGameId = randomUUID();
      const newForkDepth = (originalGame.forkDepth || 0) + 1;

      const forkedGame = await this.prisma.game.create({
        data: {
          id: newGameId,
          authorId: userId,
          title: `${originalGame.title} (Fork)`,
          description: originalGame.description,
          tags: originalGame.tags ?? [],
          gameType: originalGame.gameType,
          status: 'draft',
          forkedFrom: gameId,
          forkDepth: newForkDepth,
          commentCount: 0,
          visibility: 'private',
          allowComments: originalGame.allowComments ?? true,
          allowFork: true,
          canPlay: true,
          requireSubscription: false,
        },
      });

      try {
        await this.bundleService.saveBundle({
          gameId: newGameId,
          version: 1,
          htmlCode: originalBundle.htmlCode,
          cssCode: originalBundle.cssCode,
          jsCode: originalBundle.jsCode,
          metadata: this.sanitizeForkBundleMetadata(originalBundle, gameId),
        });
      } catch (error: any) {
        await this.prisma.game.delete({ where: { id: newGameId } }).catch((cleanupError) => {
          this.logger.warn(`Failed to rollback fork ${newGameId}: ${cleanupError.message}`);
        });
        throw new BadRequestException(`Failed to copy source bundle: ${error.message}`);
      }

      await this.statsService.incrementForkCount(gameId).catch((error) => {
        this.logger.warn(`Failed to increment fork count for ${gameId}: ${error.message}`);
      });

      return await this.prisma.game.findUnique({
        where: { id: newGameId },
        include: {
          author: {
            select: {
              id: true,
              username: true,
              avatarUrl: true,
            },
          },
        },
      }) || forkedGame;
    } catch (error) {
      this.logger.error(`Failed to fork game: ${error.message}`);
      throw error;
    }
  }

  async getForks(
    gameId: string,
    page: number = 1,
    limit: number = 10,
  ): Promise<any> {
    try {
      const skip = (page - 1) * limit;

      const [forks, total] = await Promise.all([
        this.prisma.game.findMany({
          where: {
            forkedFrom: gameId,
            status: 'published',
            visibility: 'public',
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
          orderBy: {
            createdAt: 'desc',
          },
        }),
        this.prisma.game.count({
          where: {
            forkedFrom: gameId,
            status: 'published',
            visibility: 'public',
          },
        }),
      ]);

      return {
        data: forks,
        pagination: {
          page,
          limit,
          total,
          totalPages: Math.ceil(total / limit),
        },
      };
    } catch (error) {
      this.logger.error(`Failed to get forks: ${error.message}`);
      throw error;
    }
  }

  async getForkTree(gameId: string): Promise<any> {
    try {
      const game = await this.prisma.game.findUnique({
        where: { id: gameId },
        include: {
          author: {
            select: {
              id: true,
              username: true,
              avatarUrl: true,
            },
          },
        },
      });

      if (!game || !this.isPublicForkVisible(game)) {
        throw new NotFoundException('Game not found');
      }

      let parent = null;
      if (game.forkedFrom) {
        parent = await this.prisma.game.findUnique({
          where: { id: game.forkedFrom },
          include: {
            author: {
              select: {
                id: true,
                username: true,
                avatarUrl: true,
              },
            },
          },
        });
        if (parent && !this.isPublicForkVisible(parent)) {
          parent = null;
        }
      }

      const children = await this.prisma.game.findMany({
        where: {
          forkedFrom: gameId,
          status: 'published',
          visibility: 'public',
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
        orderBy: {
          createdAt: 'desc',
        },
      });

      return {
        game,
        parent,
        children,
      };
    } catch (error) {
      this.logger.error(`Failed to get fork tree: ${error.message}`);
      throw error;
    }
  }

  async getForkLineage(gameId: string): Promise<any[]> {
    try {
      const lineage = [];
      let currentGameId = gameId;
      let isFirstLookup = true;

      while (currentGameId) {
        const game = await this.prisma.game.findUnique({
          where: { id: currentGameId },
          select: {
            id: true,
            title: true,
            forkedFrom: true,
            forkDepth: true,
            authorId: true,
            createdAt: true,
            status: true,
            visibility: true,
          },
        });

        if (!game) {
          if (isFirstLookup) {
            throw new NotFoundException('Game not found');
          }
          break;
        }
        if (!this.isPublicForkVisible(game)) {
          if (isFirstLookup) {
            throw new NotFoundException('Game not found');
          }
          break;
        }

        lineage.unshift(game);
        currentGameId = game.forkedFrom!;
        isFirstLookup = false;
      }

      return lineage;
    } catch (error) {
      this.logger.error(`Failed to get fork lineage: ${error.message}`);
      throw error;
    }
  }
}
