import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class SearchService {
  private readonly gameContentBaseUrl =
    (process.env.PUBLIC_API_BASE_URL || process.env.GAME_SERVICE_URL || 'http://localhost:3002').replace(/\/$/, '');

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
    _tags?: string[],
    page: number = 1,
    limit: number = 20,
    sortBy: 'relevance' | 'popularity' = 'relevance',
  ) {
    const skip = (page - 1) * limit;

    const whereConditions: any = {
      status: 'published' as const,
      visibility: 'public',
    };

    // MySQL collation (utf8mb4_unicode_ci) is case-insensitive by default
    // mode:'insensitive' is PostgreSQL-only and not supported in MySQL
    if (query && query.trim()) {
      whereConditions.OR = [
        { title: { contains: query } },
        { description: { contains: query } },
        { author: { username: { contains: query } } },
        { author: { displayName: { contains: query } } },
      ];
    }

    if (gameType) {
      whereConditions.gameType = gameType;
    }

    // tags is a Json field in MySQL; hasSome is not supported.
    // Tag filtering via JSON_CONTAINS requires raw query — skipped here,
    // handled by client-side filtering or a future migration to a tags table.

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
              username: true,
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
