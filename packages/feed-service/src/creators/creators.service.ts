import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class CreatorsService {
  constructor(private prisma: PrismaService) {}

  async getTrendingCreators(page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;
    const thirtyDaysAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);

    const [creators, total] = await Promise.all([
      this.prisma.user.findMany({
        where: {
          games: {
            some: {
              playCount: {
                gt: BigInt(0),
              },
              createdAt: {
                gte: thirtyDaysAgo,
              },
            },
          },
        },
        select: {
          id: true,
          displayName: true,
          avatarUrl: true,
          bio: true,
          followerCount: true,
          games: {
            where: {
              createdAt: {
                gte: thirtyDaysAgo,
              },
            },
            select: {
              playCount: true,
              likeCount: true,
            },
          },
        },
        skip,
        take: limit,
        orderBy: {
          games: {
            _count: 'desc',
          },
        },
      }),
      this.prisma.user.count({
        where: {
          games: {
            some: {
              playCount: {
                gt: BigInt(0),
              },
            },
          },
        },
      }),
    ]);

    const formattedCreators = creators.map((creator) => {
      const totalPlays = creator.games.reduce((sum, game) => sum + Number(game.playCount), 0);
      const totalLikes = creator.games.reduce((sum, game) => sum + Number(game.likeCount), 0);

      return {
        id: creator.id,
        displayName: creator.displayName,
        avatarUrl: creator.avatarUrl,
        bio: creator.bio,
        followerCount: creator.followerCount,
        stats: {
          gameCount: creator.games.length,
          totalPlays,
          totalLikes,
        },
      };
    });

    return {
      data: formattedCreators.sort((a, b) => b.stats.totalPlays - a.stats.totalPlays),
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
      },
    };
  }
}
