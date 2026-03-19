import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class ChallengeService {
  private readonly gameContentBaseUrl =
    (process.env.PUBLIC_API_BASE_URL || process.env.GAME_SERVICE_URL || 'http://localhost:3002').replace(/\/$/, '');

  constructor(private readonly prisma: PrismaService) {}

  private getCurrentWeekRange() {
    const now = new Date();
    const day = now.getUTCDay() || 7;
    const startDate = new Date(now);
    startDate.setUTCDate(now.getUTCDate() - day + 1);
    startDate.setUTCHours(0, 0, 0, 0);

    const endDate = new Date(startDate);
    endDate.setUTCDate(startDate.getUTCDate() + 7);

    return { startDate, endDate };
  }

  private withPreviewUrl<T extends { id: string }>(game: T): T & { previewUrl: string } {
    return {
      ...game,
      previewUrl: `${this.gameContentBaseUrl}/games/${game.id}/preview`,
    };
  }

  async getCurrentChallenge() {
    const { startDate, endDate } = this.getCurrentWeekRange();
    const publishedGames = await this.prisma.game.findMany({
      where: {
        status: 'published',
        publishedAt: {
          gte: startDate,
          lt: endDate,
        },
      },
      select: {
        gameType: true,
      },
    });

    const typeCounts = publishedGames.reduce<Record<string, number>>((acc, game) => {
      const key = game.gameType || 'casual';
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    const dominantType =
      Object.entries(typeCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || 'casual';
    const challengeId = `weekly-${startDate.toISOString().slice(0, 10)}`;

    return {
      id: challengeId,
      title: `Weekly ${dominantType} Challenge`,
      description: `Based on ${publishedGames.length} published games this week, create the most engaging ${dominantType} experience.`,
      startDate,
      endDate: new Date(endDate.getTime() - 1),
      participantCount: publishedGames.length,
      rules: [
        `Focus on ${dominantType} gameplay patterns from this week's published games`,
        'Use the real preview URL returned by the backend to load the generated HTML',
        'Keep the game playable on mobile touch devices',
        'Prefer published games from the current challenge window',
      ],
    };
  }

  async getChallengeGames(_challengeId: string, page: number = 1, limit: number = 20) {
    const { startDate, endDate } = this.getCurrentWeekRange();
    const skip = (page - 1) * limit;

    const where = {
      status: 'published' as const,
      publishedAt: {
        gte: startDate,
        lt: endDate,
      },
    };

    let [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where,
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
        orderBy: [{ likeCount: 'desc' }, { playCount: 'desc' }, { publishedAt: 'desc' }],
      }),
      this.prisma.game.count({ where }),
    ]);

    if (total === 0) {
      [games, total] = await Promise.all([
        this.prisma.game.findMany({
          where: { status: 'published' as const },
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
          orderBy: [{ publishedAt: 'desc' }],
        }),
        this.prisma.game.count({ where: { status: 'published' as const } }),
      ]);
    }

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
