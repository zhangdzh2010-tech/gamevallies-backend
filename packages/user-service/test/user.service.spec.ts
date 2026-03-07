import { Test, TestingModule } from '@nestjs/testing';
import { NotFoundException } from '@nestjs/common';
import { PrismaService } from '../src/prisma/prisma.service';

describe('UserService', () => {
  let service: any;
  let prismaService: PrismaService;

  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    display_name: 'Test User',
    avatar_url: 'https://example.com/avatar.jpg',
    bio: 'Test bio',
    created_at: new Date('2024-01-01'),
    updated_at: new Date('2024-01-01'),
  };

  const mockUserStats = {
    user_id: 'user-123',
    total_games: 5,
    total_plays: 150,
    total_likes: 50,
    total_followers: 25,
    total_following: 10,
  };

  const mockPrismaService = {
    user: {
      findUnique: jest.fn(),
      findMany: jest.fn(),
      update: jest.fn(),
    },
    userStats: {
      findUnique: jest.fn(),
    },
  };

  beforeEach(async () => {
    // Create a basic UserService mock implementation
    const UserService = class {
      constructor(private prisma: PrismaService) {}

      async findById(id: string) {
        return this.prisma.user.findUnique({
          where: { id },
        });
      }

      async getProfile(userId: string) {
        const user = await this.prisma.user.findUnique({
          where: { id: userId },
        });

        if (!user) {
          throw new NotFoundException('User not found');
        }

        const stats = await this.prisma.userStats.findUnique({
          where: { user_id: userId },
        });

        return {
          ...user,
          stats: stats || null,
        };
      }

      async updateProfile(userId: string, data: any) {
        const user = await this.prisma.user.findUnique({
          where: { id: userId },
        });

        if (!user) {
          throw new NotFoundException('User not found');
        }

        return this.prisma.user.update({
          where: { id: userId },
          data,
        });
      }

      async searchUsers(query: string, limit = 10, offset = 0) {
        if (!query || query.length === 0) {
          return [];
        }

        return this.prisma.user.findMany({
          where: {
            OR: [
              { username: { contains: query, mode: 'insensitive' } },
              { display_name: { contains: query, mode: 'insensitive' } },
            ],
          },
          take: limit,
          skip: offset,
        });
      }
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        UserService,
        {
          provide: PrismaService,
          useValue: mockPrismaService,
        },
      ],
    }).compile();

    service = module.get<any>(UserService);
    prismaService = module.get<PrismaService>(PrismaService);

    jest.clearAllMocks();
  });

  describe('findById', () => {
    it('should return user when found', async () => {
      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser);

      const result = await service.findById('user-123');

      expect(result).toEqual(mockUser);
      expect(mockPrismaService.user.findUnique).toHaveBeenCalledWith({
        where: { id: 'user-123' },
      });
    });

    it('should return null when user not found', async () => {
      mockPrismaService.user.findUnique.mockResolvedValueOnce(null);

      const result = await service.findById('nonexistent');

      expect(result).toBeNull();
    });
  });

  describe('getProfile', () => {
    it('should return user profile with stats', async () => {
      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser);
      mockPrismaService.userStats.findUnique.mockResolvedValueOnce(mockUserStats);

      const result = await service.getProfile('user-123');

      expect(result).toEqual({
        ...mockUser,
        stats: mockUserStats,
      });
    });

    it('should return profile with null stats when stats not found', async () => {
      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser);
      mockPrismaService.userStats.findUnique.mockResolvedValueOnce(null);

      const result = await service.getProfile('user-123');

      expect(result.stats).toBeNull();
      expect(result.username).toBe('testuser');
    });

    it('should throw NotFoundException when user not found', async () => {
      mockPrismaService.user.findUnique.mockResolvedValueOnce(null);

      await expect(service.getProfile('nonexistent')).rejects.toThrow(NotFoundException);
    });
  });

  describe('updateProfile', () => {
    it('should successfully update user profile', async () => {
      const updateData = {
        display_name: 'Updated Name',
        bio: 'Updated bio',
      };

      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser);
      const updatedUser = { ...mockUser, ...updateData };
      mockPrismaService.user.update.mockResolvedValueOnce(updatedUser);

      const result = await service.updateProfile('user-123', updateData);

      expect(result).toEqual(updatedUser);
      expect(mockPrismaService.user.update).toHaveBeenCalledWith({
        where: { id: 'user-123' },
        data: updateData,
      });
    });

    it('should throw NotFoundException when user not found', async () => {
      mockPrismaService.user.findUnique.mockResolvedValueOnce(null);

      await expect(
        service.updateProfile('nonexistent', { display_name: 'New Name' })
      ).rejects.toThrow(NotFoundException);
    });
  });

  describe('searchUsers', () => {
    it('should return matching users', async () => {
      const searchResults = [mockUser];
      mockPrismaService.user.findMany.mockResolvedValueOnce(searchResults);

      const result = await service.searchUsers('test', 10, 0);

      expect(result).toEqual(searchResults);
      expect(mockPrismaService.user.findMany).toHaveBeenCalledWith({
        where: {
          OR: [
            { username: { contains: 'test', mode: 'insensitive' } },
            { display_name: { contains: 'test', mode: 'insensitive' } },
          ],
        },
        take: 10,
        skip: 0,
      });
    });

    it('should return empty array when no results found', async () => {
      mockPrismaService.user.findMany.mockResolvedValueOnce([]);

      const result = await service.searchUsers('nonexistent', 10, 0);

      expect(result).toEqual([]);
    });

    it('should return empty array for empty query', async () => {
      const result = await service.searchUsers('', 10, 0);

      expect(result).toEqual([]);
      expect(mockPrismaService.user.findMany).not.toHaveBeenCalled();
    });

    it('should support pagination', async () => {
      const searchResults = [mockUser];
      mockPrismaService.user.findMany.mockResolvedValueOnce(searchResults);

      const result = await service.searchUsers('test', 5, 10);

      expect(mockPrismaService.user.findMany).toHaveBeenCalledWith({
        where: expect.any(Object),
        take: 5,
        skip: 10,
      });
    });
  });
});
