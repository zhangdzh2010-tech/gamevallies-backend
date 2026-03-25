import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class TagService {
  constructor(private prisma: PrismaService) {}

  async getTrendingTags(page: number = 1, limit: number = 20) {
    const thirtyDaysAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);

    // Tags are stored as JSON on Game model (MySQL)
    // Aggregate tags from recently published games
    const games = await this.prisma.game.findMany({
      where: {
        status: 'published' as const,
        visibility: 'public' as const,
        publishedAt: { gte: thirtyDaysAgo },
      },
      select: { tags: true },
    });

    // Count tag occurrences
    const tagCounts = new Map<string, number>();
    for (const game of games) {
      for (const tag of (game.tags as string[]) || []) {
        tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
      }
    }

    // Sort by count and paginate
    const sorted = Array.from(tagCounts.entries())
      .sort((a, b) => b[1] - a[1]);

    const total = sorted.length;
    const skip = (page - 1) * limit;
    const paginated = sorted.slice(skip, skip + limit);

    return {
      data: paginated.map(([name, count], i) => ({
        id: `tag-${skip + i}`,
        name,
        count,
      })),
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
      },
    };
  }

  async getAllTags(page: number = 1, limit: number = 20) {
    const games = await this.prisma.game.findMany({
      where: {
        status: 'published' as const,
        visibility: 'public' as const,
      },
      select: { tags: true },
    });

    const tagCounts = new Map<string, number>();
    for (const game of games) {
      for (const tag of (game.tags as string[]) || []) {
        tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
      }
    }

    const sorted = Array.from(tagCounts.entries())
      .sort((a, b) => a[0].localeCompare(b[0]));

    const total = sorted.length;
    const skip = (page - 1) * limit;
    const paginated = sorted.slice(skip, skip + limit);

    return {
      data: paginated.map(([name, count], i) => ({
        id: `tag-${skip + i}`,
        name,
        count,
      })),
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
      },
    };
  }
}
