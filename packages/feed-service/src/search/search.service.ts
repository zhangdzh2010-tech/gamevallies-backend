import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class SearchService {
  private readonly gameContentBaseUrl =
    (process.env.GAME_SERVICE_URL || 'http://localhost:3002').replace(/\/$/, '');

  constructor(private prisma: PrismaService) {}

  private withPreviewUrl<T extends { id: string }>(game: T): T & { previewUrl: string } {
    return {
      ...game,
      previewUrl: `${this.gameContentBaseUrl}/games/${game.id}/preview`,
    };
  }

  async searchGames(
    query: string,
    gameType?: string,
    tags?: string[],
    page: number = 1,
    limit: number = 20,
    sortBy: 'relevance' | 'popularity' = 'relevance',
  ) {
    const skip = (page - 1) * limit;

    const whereConditions: any = {
      status: 'published' as const,
      OR: [
        {
          title: {
            mode: 'insensitive',
            contains: query,
          },
        },
        {
          description: {
            mode: 'insensitive',
            contains: query,
          },
        },
      ],
    };

    if (gameType) {
      whereConditions.gameType = gameType;
    }

    // tags is a String[] field on Game, use hasSome
    if (tags && tags.length > 0) {
      whereConditions.tags = {
        hasSome: tags,
      };
    }

    const orderBy: any =
      sortBy === 'popularity'
        ? [{ playCount: 'desc' }, { likeCount: 'desc' }]
        : [{ createdAt: 'desc' }];

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where: whereConditions,
        skip,
        take: limit,
        include: {
          author: {
            select: {
              id: true,
              displayName: true,
              avatarUrl: true,
            },
          },
        },
        orderBy,
      }),
      this.prisma.game.count({
        where: whereConditions,
      }),
    ]);

    return {
      data: games.map((game) => this.withPreviewUrl(game)),
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
      },
    };
  }
}
