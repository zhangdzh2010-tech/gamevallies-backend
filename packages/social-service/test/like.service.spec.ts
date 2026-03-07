import { Test, TestingModule } from '@nestjs/testing';
import { NotFoundException } from '@nestjs/common';
import { PrismaService } from '../src/prisma/prisma.service';

describe('LikeService', () => {
  let service: any;
  let prismaService: PrismaService;

  const mockPrismaService = {
    socialInteraction: {
      findUnique: jest.fn(),
      create: jest.fn(),
      delete: jest.fn(),
    },
    game: {
      update: jest.fn(),
      findUnique: jest.fn(),
    },
  };

  beforeEach(async () => {
    const LikeService = class {
      constructor(private prisma: PrismaService) {}

      async like(gameId: string, userId: string) {
        const existingLike = await this.prisma.socialInteraction.findUnique({
          where: {
            unique_interaction: {
              userId,
              targetId: gameId,
              targetType: 'game',
              action: 'like',
            },
          },
        });

        if (existingLike) {
          // Toggle: remove like
          await this.prisma.socialInteraction.delete({
            where: {
              unique_interaction: {
                userId,
                targetId: gameId,
                targetType: 'game',
                action: 'like',
              },
            },
          });

          await this.prisma.game.update({
            where: { id: gameId },
            data: { likeCount: { decrement: 1 } },
          });

          return { liked: false };
        } else {
          // Create like
          await this.prisma.socialInteraction.create({
            data: {
              userId,
              targetId: gameId,
              targetType: 'game',
              action: 'like',
            },
          });

          await this.prisma.game.update({
            where: { id: gameId },
            data: { likeCount: { increment: 1 } },
          });

          return { liked: true };
        }
      }
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        LikeService,
        {
          provide: PrismaService,
          useValue: mockPrismaService,
        },
      ],
    }).compile();

    service = module.get<any>(LikeService);
    prismaService = module.get<PrismaService>(PrismaService);

    jest.clearAllMocks();
  });

  describe('like', () => {
    it('should create a like interaction on first like', async () => {
      mockPrismaService.socialInteraction.findUnique.mockResolvedValueOnce(null);
      mockPrismaService.socialInteraction.create.mockResolvedValueOnce({
        userId: 'user-123',
        targetId: 'game-456',
        targetType: 'game',
      });
      mockPrismaService.game.update.mockResolvedValueOnce({
        id: 'game-456',
        likeCount: 51,
      });

      const result = await service.like('game-456', 'user-123');

      expect(result.liked).toBe(true);
      expect(mockPrismaService.socialInteraction.create).toHaveBeenCalled();
      expect(mockPrismaService.game.update).toHaveBeenCalledWith({
        where: { id: 'game-456' },
        data: { likeCount: { increment: 1 } },
      });
    });

    it('should remove like on second like (toggle)', async () => {
      const existingLike = {
        userId: 'user-123',
        targetId: 'game-456',
        targetType: 'game',
      };

      mockPrismaService.socialInteraction.findUnique.mockResolvedValueOnce(existingLike);
      mockPrismaService.socialInteraction.delete.mockResolvedValueOnce(existingLike);
      mockPrismaService.game.update.mockResolvedValueOnce({
        id: 'game-456',
        likeCount: 49,
      });

      const result = await service.like('game-456', 'user-123');

      expect(result.liked).toBe(false);
      expect(mockPrismaService.socialInteraction.delete).toHaveBeenCalled();
      expect(mockPrismaService.game.update).toHaveBeenCalledWith({
        where: { id: 'game-456' },
        data: { likeCount: { decrement: 1 } },
      });
    });

    it('should increment game like counter atomically', async () => {
      mockPrismaService.socialInteraction.findUnique.mockResolvedValueOnce(null);
      mockPrismaService.socialInteraction.create.mockResolvedValueOnce({});

      const initialLikeCount = 50;
      mockPrismaService.game.update.mockResolvedValueOnce({
        likeCount: initialLikeCount + 1,
      });

      await service.like('game-456', 'user-123');

      expect(mockPrismaService.game.update).toHaveBeenCalledWith({
        where: { id: 'game-456' },
        data: { likeCount: { increment: 1 } },
      });
    });

    it('should decrement game like counter on unlike', async () => {
      mockPrismaService.socialInteraction.findUnique.mockResolvedValueOnce({});
      mockPrismaService.socialInteraction.delete.mockResolvedValueOnce({});

      const initialLikeCount = 50;
      mockPrismaService.game.update.mockResolvedValueOnce({
        likeCount: initialLikeCount - 1,
      });

      await service.like('game-456', 'user-123');

      expect(mockPrismaService.game.update).toHaveBeenCalledWith({
        where: { id: 'game-456' },
        data: { likeCount: { decrement: 1 } },
      });
    });
  });
});
