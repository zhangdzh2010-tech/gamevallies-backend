import { Test, TestingModule } from '@nestjs/testing';
import { NotFoundException } from '@nestjs/common';
import { PrismaService } from '../src/prisma/prisma.service';

describe('ForkService', () => {
  let service: any;
  let prismaService: PrismaService;

  const mockGame = {
    id: 'game-123',
    authorId: 'user-123',
    title: 'Original Game',
    description: 'Original description',
    gameType: 'dodge',
    status: 'published',
    forkedFrom: null,
    forkDepth: 0,
    createdAt: new Date('2024-01-01'),
    updatedAt: new Date('2024-01-01'),
    playCount: BigInt(0),
    likeCount: BigInt(0),
    forkCount: BigInt(0),
    commentCount: 0,
    version: 1,
  };

  const mockMockPrismaService = {
    game: {
      findUnique: jest.fn(),
      create: jest.fn(),
      findMany: jest.fn(),
    },
  };

  beforeEach(async () => {
    const ForkService = class {
      constructor(private prisma: PrismaService) {}

      async forkGame(sourceGameId: string, userId: string) {
        const sourceGame = await this.prisma.game.findUnique({
          where: { id: sourceGameId },
        });

        if (!sourceGame) {
          throw new NotFoundException('Source game not found');
        }

        const newFork = {
          id: 'game-' + Date.now(),
          authorId: userId,
          title: `${sourceGame.title} (Fork)`,
          description: sourceGame.description,
          gameType: sourceGame.gameType,
          status: 'draft',
          forkedFrom: sourceGameId,
          forkDepth: (sourceGame.forkDepth || 0) + 1,
          playCount: BigInt(0),
          likeCount: BigInt(0),
          forkCount: BigInt(0),
          commentCount: 0,
          version: 1,
        };

        return this.prisma.game.create({
          data: newFork,
        });
      }

      async getForks(gameId: string, limit = 10, offset = 0) {
        return this.prisma.game.findMany({
          where: {
            forkedFrom: gameId,
          },
          take: limit,
          skip: offset,
          orderBy: { createdAt: 'desc' },
        });
      }

      async getForkTree(gameId: string) {
        const game = await this.prisma.game.findUnique({
          where: { id: gameId },
        });

        if (!game) {
          throw new NotFoundException('Game not found');
        }

        let current = game;
        const lineage = [current];

        while (current.forkedFrom) {
          const parent = await this.prisma.game.findUnique({
            where: { id: current.forkedFrom },
          });

          if (!parent) break;
          lineage.unshift(parent);
          current = parent;
        }

        return lineage;
      }
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        ForkService,
        {
          provide: PrismaService,
          useValue: mockMockPrismaService,
        },
      ],
    }).compile();

    service = module.get<any>(ForkService);
    prismaService = module.get<PrismaService>(PrismaService);

    jest.clearAllMocks();
  });

  describe('forkGame', () => {
    it('should successfully fork a game', async () => {
      const forkedGame = {
        id: 'game-fork-123',
        authorId: 'user-456',
        title: 'Original Game (Fork)',
        forkedFrom: 'game-123',
        forkDepth: 1,
        status: 'draft',
      };

      mockMockPrismaService.game.findUnique.mockResolvedValueOnce(mockGame);
      mockMockPrismaService.game.create.mockResolvedValueOnce(forkedGame);

      const result = await service.forkGame('game-123', 'user-456');

      expect(result.forkedFrom).toBe('game-123');
      expect(result.forkDepth).toBe(1);
      expect(result.status).toBe('draft');
      expect(mockMockPrismaService.game.create).toHaveBeenCalled();
    });

    it('should throw NotFoundException when source game not found', async () => {
      mockMockPrismaService.game.findUnique.mockResolvedValueOnce(null);

      await expect(service.forkGame('nonexistent', 'user-456')).rejects.toThrow(
        NotFoundException
      );
    });

    it('should track fork depth correctly', async () => {
      const parentGame = { ...mockGame, forkDepth: 2, forkedFrom: 'game-parent' };
      const forkedGame = {
        id: 'game-fork-456',
        authorId: 'user-456',
        forkDepth: 3,
        forkedFrom: 'game-123',
      };

      mockMockPrismaService.game.findUnique.mockResolvedValueOnce(parentGame);
      mockMockPrismaService.game.create.mockResolvedValueOnce(forkedGame);

      const result = await service.forkGame('game-123', 'user-456');

      expect(result.forkDepth).toBe(3);
    });
  });

  describe('getForks', () => {
    it('should return paginated forks of a game', async () => {
      const forks = [
        { id: 'fork-1', forkedFrom: 'game-123' },
        { id: 'fork-2', forkedFrom: 'game-123' },
      ];

      mockMockPrismaService.game.findMany.mockResolvedValueOnce(forks);

      const result = await service.getForks('game-123', 10, 0);

      expect(result).toEqual(forks);
      expect(mockMockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: { forkedFrom: 'game-123' },
        take: 10,
        skip: 0,
        orderBy: { createdAt: 'desc' },
      });
    });

    it('should return empty array when no forks exist', async () => {
      mockMockPrismaService.game.findMany.mockResolvedValueOnce([]);

      const result = await service.getForks('game-123', 10, 0);

      expect(result).toEqual([]);
    });

    it('should support pagination', async () => {
      const forks = [{ id: 'fork-3', forkedFrom: 'game-123' }];

      mockMockPrismaService.game.findMany.mockResolvedValueOnce(forks);

      const result = await service.getForks('game-123', 5, 10);

      expect(mockMockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: { forkedFrom: 'game-123' },
        take: 5,
        skip: 10,
        orderBy: { createdAt: 'desc' },
      });
    });
  });

  describe('getForkTree', () => {
    it('should return recursive lineage of forked game', async () => {
      const grandparentGame = {
        id: 'game-original',
        forkedFrom: null,
        title: 'Original',
      };
      const parentGame = {
        id: 'game-123',
        forkedFrom: 'game-original',
        title: 'First Fork',
      };
      const childGame = {
        id: 'game-fork-456',
        forkedFrom: 'game-123',
        title: 'Second Fork',
      };

      mockMockPrismaService.game.findUnique
        .mockResolvedValueOnce(childGame) // Initial lookup
        .mockResolvedValueOnce(parentGame) // Parent lookup
        .mockResolvedValueOnce(grandparentGame); // Grandparent lookup

      const result = await service.getForkTree('game-fork-456');

      expect(result).toHaveLength(3);
      expect(result[0].id).toBe('game-original');
      expect(result[1].id).toBe('game-123');
      expect(result[2].id).toBe('game-fork-456');
    });

    it('should handle game with no parent', async () => {
      mockMockPrismaService.game.findUnique.mockResolvedValueOnce(mockGame);

      const result = await service.getForkTree('game-123');

      expect(result).toHaveLength(1);
      expect(result[0].id).toBe('game-123');
    });

    it('should throw NotFoundException when game not found', async () => {
      mockMockPrismaService.game.findUnique.mockResolvedValueOnce(null);

      await expect(service.getForkTree('nonexistent')).rejects.toThrow(
        NotFoundException
      );
    });
  });
});
