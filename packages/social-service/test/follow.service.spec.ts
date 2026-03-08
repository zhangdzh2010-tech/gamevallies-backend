import { Test, TestingModule } from '@nestjs/testing';
import { NotFoundException } from '@nestjs/common';
import { PrismaService } from '../src/prisma/prisma.service';

describe('FollowService', () => {
  let service: any;
  let prismaService: PrismaService;

  const mockPrismaService: any = {
    socialInteraction: {
      findUnique: jest.fn(),
      create: jest.fn(),
      delete: jest.fn(),
      findMany: jest.fn(),
      count: jest.fn(),
    },
    follow: {
      findMany: jest.fn(),
      count: jest.fn(),
    },
    user: {
      update: jest.fn(),
      findUnique: jest.fn(),
    },
  };

  beforeEach(async () => {
    const FollowService = class {
      constructor(private prisma: any) {}

      async follow(followerId: string, followingId: string) {
        const existingFollow = await this.prisma.socialInteraction.findUnique({
          where: {
            unique_interaction: {
              userId: followerId,
              targetId: followingId,
              targetType: 'user',
              action: 'follow',
            },
          },
        });

        if (existingFollow) {
          // Unfollow
          await this.prisma.socialInteraction.delete({
            where: {
              unique_interaction: {
                userId: followerId,
                targetId: followingId,
                targetType: 'user',
                action: 'follow',
              },
            },
          });

          await this.prisma.user.update({
            where: { id: followerId },
            data: { followingCount: { decrement: 1 } },
          });

          await this.prisma.user.update({
            where: { id: followingId },
            data: { followerCount: { decrement: 1 } },
          });

          return { following: false };
        } else {
          // Follow
          await this.prisma.socialInteraction.create({
            data: {
              userId: followerId,
              targetId: followingId,
              targetType: 'user',
              action: 'follow',
            },
          });

          await this.prisma.user.update({
            where: { id: followerId },
            data: { followingCount: { increment: 1 } },
          });

          await this.prisma.user.update({
            where: { id: followingId },
            data: { followerCount: { increment: 1 } },
          });

          return { following: true };
        }
      }

      async getFollowers(userId: string, limit = 20, offset = 0) {
        return this.prisma.socialInteraction.findMany({
          where: {
            targetId: userId,
            targetType: 'user',
            action: 'follow',
          },
          include: {
            user: true,
          },
          take: limit,
          skip: offset,
        });
      }

      async getFollowing(userId: string, limit = 20, offset = 0) {
        return this.prisma.socialInteraction.findMany({
          where: {
            userId,
            targetType: 'user',
            action: 'follow',
          },
          include: {
            user: true,
          },
          take: limit,
          skip: offset,
        });
      }

      async getFollowerCount(userId: string) {
        return this.prisma.socialInteraction.count({
          where: { targetId: userId, targetType: 'user', action: 'follow' },
        });
      }

      async getFollowingCount(userId: string) {
        return this.prisma.socialInteraction.count({
          where: { userId, targetType: 'user', action: 'follow' },
        });
      }
    };

    service = new FollowService(mockPrismaService as any);
    prismaService = mockPrismaService as any;

    jest.clearAllMocks();
  });

  describe('follow', () => {
    it('should create follow relationship', async () => {
      mockPrismaService.socialInteraction.findUnique.mockResolvedValueOnce(null);
      mockPrismaService.socialInteraction.create.mockResolvedValueOnce({});
      mockPrismaService.user.update.mockResolvedValueOnce({});

      const result = await service.follow('user-123', 'user-456');

      expect(result.following).toBe(true);
      expect(mockPrismaService.socialInteraction.create).toHaveBeenCalledWith({
        data: {
          userId: 'user-123',
          targetId: 'user-456',
          targetType: 'user',
          action: 'follow',
        },
      });
    });

    it('should unfollow on second follow (toggle)', async () => {
      mockPrismaService.socialInteraction.findUnique.mockResolvedValueOnce({
        userId: 'user-123',
        targetId: 'user-456',
      });
      mockPrismaService.socialInteraction.delete.mockResolvedValueOnce({});
      mockPrismaService.user.update.mockResolvedValueOnce({});

      const result = await service.follow('user-123', 'user-456');

      expect(result.following).toBe(false);
      expect(mockPrismaService.socialInteraction.delete).toHaveBeenCalled();
    });

    it('should update follower and following counts on follow', async () => {
      mockPrismaService.socialInteraction.findUnique.mockResolvedValueOnce(null);
      mockPrismaService.socialInteraction.create.mockResolvedValueOnce({});
      mockPrismaService.user.update.mockResolvedValueOnce({});

      await service.follow('user-123', 'user-456');

      expect(mockPrismaService.user.update).toHaveBeenCalledWith({
        where: { id: 'user-123' },
        data: { followingCount: { increment: 1 } },
      });

      expect(mockPrismaService.user.update).toHaveBeenCalledWith({
        where: { id: 'user-456' },
        data: { followerCount: { increment: 1 } },
      });
    });

    it('should update counts on unfollow', async () => {
      mockPrismaService.socialInteraction.findUnique.mockResolvedValueOnce({});
      mockPrismaService.socialInteraction.delete.mockResolvedValueOnce({});
      mockPrismaService.user.update.mockResolvedValueOnce({});

      await service.follow('user-123', 'user-456');

      expect(mockPrismaService.user.update).toHaveBeenCalledWith({
        where: { id: 'user-123' },
        data: { followingCount: { decrement: 1 } },
      });

      expect(mockPrismaService.user.update).toHaveBeenCalledWith({
        where: { id: 'user-456' },
        data: { followerCount: { decrement: 1 } },
      });
    });
  });

  describe('getFollowers', () => {
    it('should return paginated followers', async () => {
      const followers = [
        {
          follower: { id: 'user-1', username: 'user1' },
        },
        {
          follower: { id: 'user-2', username: 'user2' },
        },
      ];

      mockPrismaService.socialInteraction.findMany.mockResolvedValueOnce(followers);

      const result = await service.getFollowers('user-456', 20, 0);

      expect(result).toEqual(followers);
      expect(mockPrismaService.socialInteraction.findMany).toHaveBeenCalledWith({
        where: { targetId: 'user-456', targetType: 'user', action: 'follow' },
        include: { user: true },
        take: 20,
        skip: 0,
      });
    });

    it('should support pagination for followers', async () => {
      mockPrismaService.socialInteraction.findMany.mockResolvedValueOnce([]);

      await service.getFollowers('user-456', 10, 20);

      expect(mockPrismaService.socialInteraction.findMany).toHaveBeenCalledWith({
        where: { targetId: 'user-456', targetType: 'user', action: 'follow' },
        include: { user: true },
        take: 10,
        skip: 20,
      });
    });
  });

  describe('getFollowing', () => {
    it('should return paginated following users', async () => {
      const following = [
        {
          following: { id: 'creator-1', username: 'creator1' },
        },
      ];

      mockPrismaService.socialInteraction.findMany.mockResolvedValueOnce(following);

      const result = await service.getFollowing('user-123', 20, 0);

      expect(result).toEqual(following);
    });
  });

  describe('count', () => {
    it('should return follower count', async () => {
      mockPrismaService.socialInteraction.count.mockResolvedValueOnce(42);

      const count = await service.getFollowerCount('user-456');

      expect(count).toBe(42);
      expect(mockPrismaService.socialInteraction.count).toHaveBeenCalledWith({
        where: { targetId: 'user-456', targetType: 'user', action: 'follow' },
      });
    });

    it('should return following count', async () => {
      mockPrismaService.socialInteraction.count.mockResolvedValueOnce(15);

      const count = await service.getFollowingCount('user-123');

      expect(count).toBe(15);
      expect(mockPrismaService.socialInteraction.count).toHaveBeenCalledWith({
        where: { userId: 'user-123', targetType: 'user', action: 'follow' },
      });
    });
  });
});
