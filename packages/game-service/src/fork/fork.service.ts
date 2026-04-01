import {
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class ForkService {
  private readonly logger = new Logger(ForkService.name);

  constructor(private prisma: PrismaService) {}

  private isPublicForkVisible(game: { status?: string | null; visibility?: string | null } | null | undefined): boolean {
    return game?.status === 'published' && (game.visibility || 'public') === 'public';
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
