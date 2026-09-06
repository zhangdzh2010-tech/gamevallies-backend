import { Injectable } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import Redis from 'ioredis';

@Injectable()
export class FeedService {
  private redis: Redis;
  private readonly gameContentBaseUrl =
    (process.env.PUBLIC_API_BASE_URL || process.env.GAME_SERVICE_URL || 'http://localhost:3002').replace(/\/$/, '');

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

  async onModuleDestroy(): Promise<void> { await this.redis.quit(); }

  private async cacheGet(key: string): Promise<string | null> {
    try { return await this.redis.get(key); } catch { return null; }
  }

  private async cacheSet(key: string, ttl: number, value: string): Promise<void> {
    try { await this.redis.setex(key, ttl, value); } catch { /* cache miss is acceptable */ }
  }

  private buildPublishedWhere(query?: string, extra: Prisma.GameWhereInput = {}): Prisma.GameWhereInput {
    const where: Prisma.GameWhereInput = {
      status: 'published' as const,
      visibility: 'public',
      ...extra,
    };

    if (query && query.trim()) {
      const q = query.trim();
      where.AND = [
        ...(Array.isArray(where.AND) ? where.AND : where.AND ? [where.AND] : []),
        {
          OR: [
            { title: { contains: q } },
            { description: { contains: q } },
            { author: { username: { contains: q } } },
            { author: { displayName: { contains: q } } },
          ],
        },
      ];
    }

    return where;
  }

  private async deleteCachePattern(pattern: string): Promise<number> {
    let cursor = '0';
    let deleted = 0;

    do {
      let nextCursor = '0';
      let keys: string[] = [];
      try {
        const result = await this.redis.scan(cursor, 'MATCH', pattern, 'COUNT', 200);
        nextCursor = result[0];
        keys = result[1];
      } catch {
        return deleted;
      }
      cursor = nextCursor;
      if (keys.length > 0) {
        await this.redis.del(...keys).catch(() => 0);
        deleted += keys.length;
      }
    } while (cursor !== '0');

    return deleted;
  }

  async invalidateGameFeedCache() {
    const deleted = await this.deleteCachePattern('feed:*');
    return { deleted };
  }

  async getTrendingFeed(page: number = 1, limit: number = 20, query?: string) {
    page = Number.isFinite(page) ? Math.max(1, Math.floor(page)) : 1;
    limit = Number.isFinite(limit) ? Math.min(100, Math.max(1, Math.floor(limit))) : 20;
    const configuredLimit = Number(process.env.TRENDING_CANDIDATE_LIMIT || 1000);
    const candidateLimit = Number.isFinite(configuredLimit)
      ? Math.min(10000, Math.max(100, Math.floor(configuredLimit)))
      : 1000;
    const normalizedQuery = (query || '').trim().toLowerCase();
    const cacheKey = `feed:trending:v2:${candidateLimit}:${page}:${limit}:${normalizedQuery || '*'}`;
    const cached = await this.cacheGet(cacheKey);

    if (cached) {
      try { return JSON.parse(cached); } catch { /* corrupt cache, fall through */ }
    }

    const games = await this.prisma.game.findMany({
      where: this.buildPublishedWhere(query),
      // Rank a bounded recent candidate pool. Pagination describes this pool,
      // while latest/search retain their separate catalogue semantics.
      take: candidateLimit,
      orderBy: [{ publishedAt: 'desc' }, { id: 'asc' }],
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
    });

    const now = Date.now();
    const scored = games.map((game) => {
      const likes = Number(game.likeCount);
      const plays = Number(game.playCount);
      const score = this.calculateWilsonScore(likes, plays + likes);
      const publishedTime = game.publishedAt ? new Date(game.publishedAt).getTime() : now;
      const ageHours = Math.max(0, now - publishedTime) / (1000 * 60 * 60);
      const decayFactor = Math.exp(-0.01 * ageHours);
      const finalScore = score * decayFactor;

      return { ...game, score: finalScore };
    });

    const sorted = scored
      .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
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

    await this.cacheSet(cacheKey, 1800, JSON.stringify(result));
    return result;
  }

  async getLatestFeed(page: number = 1, limit: number = 20, query?: string) {
    const skip = (page - 1) * limit;

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where: this.buildPublishedWhere(query),
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
        orderBy: { publishedAt: 'desc' },
      }),
      this.prisma.game.count({
        where: this.buildPublishedWhere(query),
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

  async getFeaturedFeed(page: number = 1, limit: number = 20, query?: string) {
    const skip = (page - 1) * limit;

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where: this.buildPublishedWhere(query),
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
        orderBy: [
          { qualityScore: 'desc' },
          { likeCount: 'desc' },
          { playCount: 'desc' },
          { publishedAt: 'desc' },
        ],
      }),
      this.prisma.game.count({
        where: this.buildPublishedWhere(query),
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

  async getFollowingFeed(userId: string, page: number = 1, limit: number = 20, query?: string) {
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
        where: this.buildPublishedWhere(query, {
          authorId: {
            in: followedAuthorIds,
          },
        }),
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
        orderBy: { publishedAt: 'desc' },
      }),
      this.prisma.game.count({
        where: this.buildPublishedWhere(query, {
          authorId: {
            in: followedAuthorIds,
          },
        }),
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

  async getFeedByType(gameType: string, page: number = 1, limit: number = 20, query?: string) {
    const skip = (page - 1) * limit;

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where: this.buildPublishedWhere(query, {
          gameType,
        }),
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
        orderBy: { publishedAt: 'desc' },
      }),
      this.prisma.game.count({
        where: this.buildPublishedWhere(query, {
          gameType,
        }),
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
