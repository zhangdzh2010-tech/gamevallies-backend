import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  Logger,
  NotFoundException,
} from '@nestjs/common';
import { randomUUID } from 'crypto';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class ForkService {
  private readonly logger = new Logger(ForkService.name);

  constructor(private prisma: PrismaService) {}

  private isPublicForkVisible(game: { status?: string | null; visibility?: string | null } | null | undefined): boolean {
    return game?.status === 'published' && (game.visibility || 'public') === 'public';
  }

  private isBundlePlayable(bundle: { htmlCode?: string | null } | null | undefined): boolean {
    return typeof bundle?.htmlCode === 'string' && bundle.htmlCode.trim().length > 0;
  }

  private cloneJsonValue<T>(value: T): T {
    if (value === null || value === undefined) {
      return value;
    }

    return JSON.parse(JSON.stringify(value)) as T;
  }

  async forkGame(gameId: string, userId: string): Promise<{ gameId: string }> {
    try {
      return await this.prisma.$transaction(async (tx) => {
        const sourceGame = await tx.game.findUnique({
          where: { id: gameId },
          select: {
            id: true,
            title: true,
            description: true,
            gameType: true,
            tags: true,
            status: true,
            visibility: true,
            allowComments: true,
            allowFork: true,
            forkDepth: true,
            thumbnailUrl: true,
          },
        });

        if (!sourceGame || !this.isPublicForkVisible(sourceGame)) {
          throw new NotFoundException('Game not found');
        }

        if (sourceGame.allowFork === false) {
          throw new ForbiddenException('Forking is disabled for this game');
        }

        const sourceBundle = await tx.gameBundle.findFirst({
          where: { gameId },
          orderBy: { version: 'desc' },
        });

        if (!sourceBundle || !this.isBundlePlayable(sourceBundle)) {
          throw new BadRequestException('Game bundle is not ready for forking');
        }

        const forkGameId = randomUUID();
        const forkSourceBundle = sourceBundle;
        const sourceMetadata = this.cloneJsonValue(forkSourceBundle.metadata);
        const sourceSpec = forkSourceBundle.spec ?? (
          sourceMetadata
          && typeof sourceMetadata === 'object'
          && !Array.isArray(sourceMetadata)
          ? (sourceMetadata as Record<string, unknown>).gameSpec ?? null
          : null
        );

        await tx.game.create({
          data: {
            id: forkGameId,
            authorId: userId,
            title: sourceGame.title,
            description: sourceGame.description,
            status: 'draft',
            failedStage: null,
            failedReason: null,
            retryCount: 0,
            lastErrorAt: null,
            gameType: sourceGame.gameType,
            tags: this.cloneJsonValue(sourceGame.tags ?? []),
            forkedFrom: sourceGame.id,
            forkDepth: Number(sourceGame.forkDepth ?? 0) + 1,
            version: 1,
            thumbnailUrl: sourceGame.thumbnailUrl,
            visibility: 'private',
            allowComments: sourceGame.allowComments,
            allowFork: sourceGame.allowFork,
            canPlay: true,
            requireSubscription: false,
            accessGrantSource: 'none',
            accessGrantSubscriptionId: null,
          },
        });

        await tx.gameBundle.create({
          data: {
            id: randomUUID(),
            gameId: forkGameId,
            version: 1,
            htmlCode: forkSourceBundle.htmlCode,
            cssCode: forkSourceBundle.cssCode ?? '',
            jsCode: forkSourceBundle.jsCode ?? '',
            spec: sourceSpec === null
              ? undefined
              : (this.cloneJsonValue(sourceSpec) as Prisma.InputJsonValue),
            aiConversation: this.cloneJsonValue(forkSourceBundle.aiConversation ?? []) as Prisma.InputJsonValue,
            generationMeta: forkSourceBundle.generationMeta === null
              ? undefined
              : (this.cloneJsonValue(forkSourceBundle.generationMeta) as Prisma.InputJsonValue),
            metadata: sourceMetadata === null
              ? undefined
              : (sourceMetadata as Prisma.InputJsonValue),
            previewUrl: null,
            codeSizeBytes: forkSourceBundle.codeSizeBytes ?? Buffer.byteLength(forkSourceBundle.htmlCode, 'utf8'),
          },
        });

        await tx.game.update({
          where: { id: sourceGame.id },
          data: {
            forkCount: { increment: 1 },
          },
        });

        return {
          gameId: forkGameId,
        };
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
