import { Injectable, BadRequestException, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class FollowService {
  constructor(private prisma: PrismaService) {}

  async followUser(userId: string, targetId: string) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    if (userId === targetId) {
      throw new BadRequestException('Cannot follow yourself');
    }

    const targetUser = await this.prisma.user.findUnique({
      where: { id: targetId },
    });

    if (!targetUser) {
      throw new NotFoundException('Target user not found');
    }

    const follow = await this.prisma.userFollow.findUnique({
      where: {
        unique_follow: {
          followerId: userId,
          followingId: targetId,
        },
      },
    });

    if (!follow) {
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

    return { following: true };
  }

  async unfollowUser(userId: string, targetId: string) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    const follow = await this.prisma.userFollow.findUnique({
      where: {
        unique_follow: {
          followerId: userId,
          followingId: targetId,
        },
      },
    });

    if (follow) {
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

    return { following: false };
  }

  async getFollowStatus(userId: string, targetId: string) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    const existingFollow = await this.prisma.userFollow.findUnique({
      where: {
        unique_follow: {
          followerId: userId,
          followingId: targetId,
        },
      },
    });

    return {
      following: Boolean(existingFollow),
    };
  }

  async toggleFollow(userId: string, targetId: string) {
    const existingFollow = await this.prisma.userFollow.findUnique({
      where: {
        unique_follow: {
          followerId: userId,
          followingId: targetId,
        },
      },
    });

    if (existingFollow) {
      return this.unfollowUser(userId, targetId);
    }

    return this.followUser(userId, targetId);
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
              avatarUrl: true,
              bio: true,
              followerCount: true,
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
      data: interactions.map((f) => f.follower),
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
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
              avatarUrl: true,
              bio: true,
              followerCount: true,
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
        pages: Math.ceil(total / limit),
      },
    };
  }
}
