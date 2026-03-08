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
        },
      });

      const originalBundle = await this.bundleService.getLatestBundle(gameId);

      if (originalBundle) {
        await this.bundleService.saveBundle({
          gameId: newGameId,
          version: 1,
          htmlCode: originalBundle.htmlCode,
          cssCode: originalBundle.cssCode,
          jsCode: originalBundle.jsCode,
          metadata: {
            ...originalBundle.metadata,
            forkedFromGameId: gameId,
          },
          previewUrl: originalBundle.previewUrl,
        });
      }

      await this.statsService.incrementForkCount(gameId);

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
      });
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
          orderBy: {
            createdAt: 'desc',
          },
        }),
        this.prisma.game.count({
          where: {
            forkedFrom: gameId,
            status: { not: 'banned' },
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

      if (!game) {
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
      }

      const children = await this.prisma.game.findMany({
        where: {
          forkedFrom: gameId,
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
          },
        });

        if (!game) {
          break;
        }

        lineage.unshift(game);
        currentGameId = game.forkedFrom!;
      }

      return lineage;
    } catch (error) {
      this.logger.error(`Failed to get fork lineage: ${error.message}`);
      throw error;
    }
  }
}
