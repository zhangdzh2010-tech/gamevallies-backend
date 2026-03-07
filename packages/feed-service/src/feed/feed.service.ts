import { Injectable, Inject } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import Redis from 'ioredis';

@Injectable()
export class FeedService {
  private redis: Redis;
  private readonly gameContentBaseUrl =
    (process.env.GAME_SERVICE_URL || 'http://localhost:3002').replace(/\/$/, '');

  constructor(private prisma: PrismaService) {
    const redisUrl = process.env.REDIS_URL;
    if (redisUrl) {
      this.redis = new Redis(redisUrl);
    } else {
      this.redis = new Redis({
        host: process.env.REDIS_HOST || 'localhost',
        port: parseInt(process.env.REDIS_PORT || '6379'),
        password: process.env.REDIS_PASSWORD || undefined,
        db: 0,
      });
    }
  }

  private withPreviewUrl<T extends { id: string }>(game: T): T & { previewUrl: string } {
    return {
      ...game,
      previewUrl: `${this.gameContentBaseUrl}/games/${game.id}/preview`,
    };
  }

  async getTrendingFeed(page: number = 1, limit: number = 20) {
    const cacheKey = `feed:trending:${page}:${limit}`;
    const cached = await this.redis.get(cacheKey);

    if (cached) {
      return JSON.parse(cached);
    }

    const games = await this.prisma.game.findMany({
      where: { status: 'published' as const },
      include: {
        author: {
          select: {
            id: true,
            displayName: true,
            avatarUrl: true,
          },
        },
      },
    });

    const scored = games.map((game) => {
      const likes = Number(game.likeCount);
      const plays = Number(game.playCount);
      const score = this.calculateWilsonScore(likes, plays + likes);
      const publishedTime = game.publishedAt ? new Date(game.publishedAt).getTime() : Date.now();
      const ageHours = (Date.now() - publishedTime) / (1000 * 60 * 60);
      const decayFactor = Math.exp(-0.01 * ageHours);
      const finalScore = score * decayFactor;

      return { ...game, score: finalScore };
    });

    const sorted = scored
      .sort((a, b) => b.score - a.score)
      .slice((page - 1) * limit, page * limit)
      .map((game) => this.withPreviewUrl(game));

    const result = {
      data: sorted,
      pagination: {
        page,
        limit,
        total: games.length,
        pages: Math.ceil(games.length / limit),
      },
    };

    await this.redis.setex(cacheKey, 1800, JSON.stringify(result));
    return result;
  }

  async getLatestFeed(page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;

    const [games, total] = await Promise.all([
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
        orderBy: { publishedAt: 'desc' },
      }),
      this.prisma.game.count({
        where: { status: 'published' as const },
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

  async getFeaturedFeed(page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;

    const [games, total] = await Promise.all([
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
        orderBy: [
          { qualityScore: 'desc' },
          { likeCount: 'desc' },
          { playCount: 'desc' },
          { publishedAt: 'desc' },
        ],
      }),
      this.prisma.game.count({
        where: { status: 'published' as const },
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

  async getFollowingFeed(userId: string, page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;

    const followedAuthors = await this.prisma.userFollow.findMany({
      where: {
        followerId: userId,
      },
      select: {
        followingId: true,
      },
    });

    const followedAuthorIds = [...new Set(followedAuthors.map((follow) => follow.followingId))];

    if (followedAuthorIds.length === 0) {
      return {
        data: [],
        pagination: {
          page,
          limit,
          total: 0,
          pages: 0,
        },
      };
    }

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where: {
          status: 'published' as const,
          authorId: {
            in: followedAuthorIds,
          },
        },
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
        orderBy: { publishedAt: 'desc' },
      }),
      this.prisma.game.count({
        where: {
          status: 'published' as const,
          authorId: {
            in: followedAuthorIds,
          },
        },
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

  async getFeedByType(gameType: string, page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where: {
          status: 'published' as const,
          gameType,
        },
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
        orderBy: { publishedAt: 'desc' },
      }),
      this.prisma.game.count({
        where: {
          status: 'published' as const,
          gameType,
        },
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

  private calculateWilsonScore(likes: number, total: number): number {
    if (total === 0) return 0;

    const z = 1.96;
    const p = likes / total;

    const numerator =
      p +
      (z * z) / (2 * total) -
      z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total));
    const denominator = 1 + (z * z) / total;

    return numerator / denominator;
  }
}
