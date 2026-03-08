import { Injectable, NotFoundException, BadRequestException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { UpdateProfileDto } from './dto';

@Injectable()
export class UserService {
  constructor(private prisma: PrismaService) {}

  async findById(id: string) {
    const user = await this.prisma.user.findUnique({
      where: { id },
      select: {
        id: true,
        username: true,
        email: true,
        phone: true,
        displayName: true,
        bio: true,
        avatarUrl: true,
        followerCount: true,
        followingCount: true,
        gameCount: true,
        totalPlays: true,
        createdAt: true,
        updatedAt: true,
      },
    });

    if (!user) {
      throw new NotFoundException(`User with ID "${id}" not found`);
    }

    return user;
  }

  async findByUsername(username: string) {
    const user = await this.prisma.user.findUnique({
      where: { username: username.toLowerCase() },
      select: {
        id: true,
        username: true,
        email: true,
        displayName: true,
        bio: true,
        avatarUrl: true,
        createdAt: true,
        updatedAt: true,
      },
    });

    if (!user) {
      throw new NotFoundException(`User with username "${username}" not found`);
    }

    return user;
  }

  async getProfile(id: string) {
    const user = await this.prisma.user.findUnique({
      where: { id },
      select: {
        id: true,
        username: true,
        email: true,
        displayName: true,
        bio: true,
        avatarUrl: true,
        role: true,
        gameCount: true,
        totalPlays: true,
        followerCount: true,
        followingCount: true,
        createdAt: true,
        updatedAt: true,
      },
    });

    if (!user) {
      throw new NotFoundException(`User with ID "${id}" not found`);
    }

    return {
      ...user,
      stats: {
        gameCount: user.gameCount,
        totalPlays: user.totalPlays,
        followerCount: user.followerCount,
        followingCount: user.followingCount,
      },
    };
  }

  async updateProfile(id: string, dto: UpdateProfileDto) {
    // Validate user exists
    const user = await this.prisma.user.findUnique({
      where: { id },
    });

    if (!user) {
      throw new NotFoundException(`User with ID "${id}" not found`);
    }

    if (dto.username && dto.username.toLowerCase() !== user.username) {
      const existingUser = await this.prisma.user.findUnique({
        where: { username: dto.username.toLowerCase() },
      });

      if (existingUser && existingUser.id !== id) {
        throw new BadRequestException('Username already taken');
      }
    }

    // Update profile
    const updatedUser = await this.prisma.user.update({
      where: { id },
      data: {
        username: dto.username ? dto.username.toLowerCase() : user.username,
        displayName: dto.displayName || user.displayName,
        bio: dto.bio !== undefined ? dto.bio : user.bio,
        avatarUrl:
          dto.avatarUrl !== undefined
            ? dto.avatarUrl
            : dto.avatar !== undefined
              ? dto.avatar
              : user.avatarUrl,
        updatedAt: new Date(),
      },
      select: {
        id: true,
        username: true,
        email: true,
        phone: true,
        displayName: true,
        bio: true,
        avatarUrl: true,
        followerCount: true,
        followingCount: true,
        gameCount: true,
        totalPlays: true,
        createdAt: true,
        updatedAt: true,
      },
    });

    return updatedUser;
  }

  async searchUsers(query: string, page: number = 1, limit: number = 20) {
    if (!query || query.trim().length === 0) {
      throw new BadRequestException('Search query cannot be empty');
    }

    if (limit > 100) {
      limit = 100;
    }

    if (page < 1) {
      page = 1;
    }

    const skip = (page - 1) * limit;

    const users = await this.prisma.user.findMany({
      where: {
        OR: [
          {
            username: {
              contains: query.toLowerCase(),
            },
          },
          {
            displayName: {
              contains: query,
            },
          },
        ],
      },
      select: {
        id: true,
        username: true,
        email: true,
        displayName: true,
        avatarUrl: true,
        bio: true,
        phone: true,
        createdAt: true,
      },
      skip,
      take: limit,
      orderBy: {
        createdAt: 'desc',
      },
    });

    const total = await this.prisma.user.count({
      where: {
        OR: [
          {
            username: {
              contains: query.toLowerCase(),
            },
          },
          {
            displayName: {
              contains: query,
            },
          },
        ],
      },
    });

    return {
      data: users,
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
      },
    };
  }

  async updateAvatar(id: string, filePath: string) {
    const user = await this.prisma.user.findUnique({
      where: { id },
      select: { id: true },
    });

    if (!user) {
      throw new NotFoundException(`User with ID "${id}" not found`);
    }

    await this.prisma.user.update({
      where: { id },
      data: {
        avatarUrl: filePath,
        updatedAt: new Date(),
      },
    });

    return {
      url: filePath,
    };
  }

  async getFollowers(userId: string, page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;

    const [interactions, total] = await Promise.all([
      this.prisma.userFollow.findMany({
        where: { followingId: userId },
        skip,
        take: limit,
        include: {
          follower: {
            select: {
              id: true,
              username: true,
              email: true,
              phone: true,
              avatarUrl: true,
              bio: true,
              createdAt: true,
            },
          },
        },
        orderBy: { createdAt: 'desc' },
      }),
      this.prisma.userFollow.count({
        where: { followingId: userId },
      }),
    ]);

    return {
      data: interactions.map((interaction) => interaction.follower),
      pagination: {
        page,
        limit,
        total,
      },
    };
  }

  async getFollowing(userId: string, page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;

    const [interactions, total] = await Promise.all([
      this.prisma.userFollow.findMany({
        where: { followerId: userId },
        skip,
        take: limit,
        orderBy: { createdAt: 'desc' },
        include: {
          following: {
            select: {
              id: true,
              username: true,
              email: true,
              phone: true,
              avatarUrl: true,
              bio: true,
              createdAt: true,
            },
          },
        },
      }),
      this.prisma.userFollow.count({
        where: { followerId: userId },
      }),
    ]);

    return {
      data: interactions.map((interaction) => interaction.following),
      pagination: {
        page,
        limit,
        total,
      },
    };
  }

  async followUser(userId: string, targetId: string) {
    if (userId === targetId) {
      throw new BadRequestException('Cannot follow yourself');
    }

    const targetUser = await this.prisma.user.findUnique({
      where: { id: targetId },
      select: { id: true },
    });

    if (!targetUser) {
      throw new NotFoundException(`User with ID "${targetId}" not found`);
    }

    const existing = await this.prisma.userFollow.findUnique({
      where: {
        unique_follow: {
          followerId: userId,
          followingId: targetId,
        },
      },
    });

    if (!existing) {
      await this.prisma.userFollow.create({
        data: {
          followerId: userId,
          followingId: targetId,
        },
      });

      await this.prisma.user.update({
        where: { id: targetId },
        data: { followerCount: { increment: 1 } },
      });
      await this.prisma.user.update({
        where: { id: userId },
        data: { followingCount: { increment: 1 } },
      });
    }
  }

  async unfollowUser(userId: string, targetId: string) {
    const existing = await this.prisma.userFollow.findUnique({
      where: {
        unique_follow: {
          followerId: userId,
          followingId: targetId,
        },
      },
    });

    if (existing) {
      await this.prisma.userFollow.delete({
        where: {
          unique_follow: {
            followerId: userId,
            followingId: targetId,
          },
        },
      });

      await this.prisma.user.update({
        where: { id: targetId },
        data: { followerCount: { decrement: 1 } },
      });
      await this.prisma.user.update({
        where: { id: userId },
        data: { followingCount: { decrement: 1 } },
      });
    }
  }

  async getUserStats(userId: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        gameCount: true,
        totalPlays: true,
        followerCount: true,
        followingCount: true,
      },
    });

    if (!user) {
      throw new NotFoundException(`User with ID "${userId}" not found`);
    }

    const gamesAgg = await this.prisma.game.aggregate({
      where: {
        authorId: userId,
        status: { not: 'banned' },
      },
      _sum: {
        likeCount: true,
      },
    });

    return {
      gamesCreated: user.gameCount,
      totalPlays: Number(user.totalPlays),
      totalLikes: Number(gamesAgg._sum.likeCount || 0),
      followers: user.followerCount,
      following: user.followingCount,
    };
  }

  async deactivateUser(id: string) {
    const user = await this.prisma.user.findUnique({
      where: { id },
    });

    if (!user) {
      throw new NotFoundException(`User with ID "${id}" not found`);
    }

    return this.prisma.user.update({
      where: { id },
      data: {
        updatedAt: new Date(),
      },
      select: {
        id: true,
        username: true,
      },
    });
  }
}
