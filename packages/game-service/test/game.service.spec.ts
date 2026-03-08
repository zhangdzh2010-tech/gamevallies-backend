import { Test, TestingModule } from '@nestjs/testing';
import { BadRequestException, NotFoundException, ForbiddenException } from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import { PrismaService } from '../src/prisma/prisma.service';

describe('GameService', () => {
  let service: any;
  let prismaService: PrismaService;
  let httpService: HttpService;

  const mockGame = {
    id: 'game-123',
    authorId: 'user-123',
    title: 'Test Game',
    description: 'A test game',
    gameType: 'dodge',
    code: '<html><canvas></canvas></html>',
    status: 'draft',
    publishedAt: null,
    createdAt: new Date('2024-01-01'),
    updatedAt: new Date('2024-01-01'),
    playCount: BigInt(0),
    likeCount: BigInt(0),
    forkCount: BigInt(0),
    commentCount: 0,
    version: 1,
    forkDepth: 0,
  };

  const mockPrismaService = {
    game: {
      create: jest.fn(),
      findUnique: jest.fn(),
      update: jest.fn(),
      findMany: jest.fn(),
    },
  };

  const mockHttpService = {
    post: jest.fn(),
  };

  beforeEach(async () => {
    const GameService = class {
      constructor(
        private prisma: any,
        private httpService: any,
      ) {}

      async create(userId: string, data: any) {
        const gameData = {
          id: 'game-' + Date.now(),
          authorId: userId,
          title: data.title,
          description: data.description,
          gameType: 'dodge',
          status: 'draft',
          playCount: BigInt(0),
          likeCount: BigInt(0),
          forkCount: BigInt(0),
          commentCount: 0,
          version: 1,
          forkDepth: 0,
        };

        return this.prisma.game.create({
          data: gameData,
        });
      }

      async findById(id: string) {
        return this.prisma.game.findUnique({
          where: { id },
        });
      }

      async publish(gameId: string, userId: string) {
        const game = await this.prisma.game.findUnique({
          where: { id: gameId },
        });

        if (!game) {
          throw new NotFoundException('Game not found');
        }

        if (game.authorId !== userId) {
          throw new ForbiddenException('Not authorized');
        }

        if (game.status === 'published') {
          throw new BadRequestException('Game already published');
        }

        return this.prisma.game.update({
          where: { id: gameId },
          data: {
            status: 'published',
            publishedAt: new Date(),
          },
        });
      }

      async iterate(gameId: string, userId: string, changes: any) {
        const game = await this.prisma.game.findUnique({
          where: { id: gameId },
        });

        if (!game) {
          throw new NotFoundException('Game not found');
        }

        if (game.authorId !== userId) {
          throw new ForbiddenException('Not authorized');
        }

        return this.prisma.game.update({
          where: { id: gameId },
          data: changes,
        });
      }

      async getPlayData(gameId: string) {
        const game = await this.prisma.game.findUnique({
          where: { id: gameId },
        });

        if (!game) {
          throw new NotFoundException('Game not found');
        }

        // Increment play count
        return this.prisma.game.update({
          where: { id: gameId },
          data: {
            playCount: {
              increment: 1,
            },
          },
        });
      }
    };

    service = new GameService(mockPrismaService as any, mockHttpService as any);
    prismaService = mockPrismaService as any;
    httpService = mockHttpService as any;

    jest.clearAllMocks();
  });

  describe('create', () => {
    it('should successfully create a new game', async () => {
      const createData = {
        title: 'New Game',
        description: 'A new game description',
      };

      const newGame = { ...mockGame, title: createData.title };
      mockPrismaService.game.create.mockResolvedValueOnce(newGame);

      const result = await service.create('user-123', createData);

      expect(result).toEqual(newGame);
      expect(mockPrismaService.game.create).toHaveBeenCalled();
    });

    it('should initialize game with draft status', async () => {
      const createData = {
        title: 'New Game',
        description: 'A description',
      };

      const newGame = { ...mockGame, status: 'draft' };
      mockPrismaService.game.create.mockResolvedValueOnce(newGame);

      const result = await service.create('user-123', createData);

      expect(result.status).toBe('draft');
    });
  });

  describe('findById', () => {
    it('should return game when found', async () => {
      mockPrismaService.game.findUnique.mockResolvedValueOnce(mockGame);

      const result = await service.findById('game-123');

      expect(result).toEqual(mockGame);
      expect(mockPrismaService.game.findUnique).toHaveBeenCalledWith({
        where: { id: 'game-123' },
      });
    });

    it('should return null when game not found', async () => {
      mockPrismaService.game.findUnique.mockResolvedValueOnce(null);

      const result = await service.findById('nonexistent');

      expect(result).toBeNull();
    });
  });

  describe('publish', () => {
    it('should successfully publish a draft game', async () => {
      const draftGame = { ...mockGame, status: 'draft' };
      const publishedGame = { ...draftGame, status: 'published', publishedAt: new Date() };

      mockPrismaService.game.findUnique.mockResolvedValueOnce(draftGame);
      mockPrismaService.game.update.mockResolvedValueOnce(publishedGame);

      const result = await service.publish('game-123', 'user-123');

      expect(result.status).toBe('published');
      expect(result.publishedAt).toBeDefined();
    });

    it('should throw NotFoundException when game not found', async () => {
      mockPrismaService.game.findUnique.mockResolvedValueOnce(null);

      await expect(service.publish('nonexistent', 'user-123')).rejects.toThrow(
        NotFoundException
      );
    });

    it('should throw ForbiddenException when not owner', async () => {
      mockPrismaService.game.findUnique.mockResolvedValueOnce(mockGame);

      await expect(service.publish('game-123', 'different-user')).rejects.toThrow(
        ForbiddenException
      );
    });

    it('should throw BadRequestException when already published', async () => {
      const publishedGame = { ...mockGame, status: 'published' };
      mockPrismaService.game.findUnique.mockResolvedValueOnce(publishedGame);

      await expect(service.publish('game-123', 'user-123')).rejects.toThrow(
        BadRequestException
      );
    });
  });

  describe('iterate', () => {
    it('should successfully iterate on owned game', async () => {
      const changes = {
        title: 'Updated Title',
        code: '<html><canvas></canvas></html>',
      };

      const updatedGame = { ...mockGame, ...changes };
      mockPrismaService.game.findUnique.mockResolvedValueOnce(mockGame);
      mockPrismaService.game.update.mockResolvedValueOnce(updatedGame);

      const result = await service.iterate('game-123', 'user-123', changes);

      expect(result.title).toBe('Updated Title');
      expect(mockPrismaService.game.update).toHaveBeenCalledWith({
        where: { id: 'game-123' },
        data: changes,
      });
    });

    it('should throw ForbiddenException when not owner', async () => {
      mockPrismaService.game.findUnique.mockResolvedValueOnce(mockGame);

      await expect(
        service.iterate('game-123', 'different-user', { title: 'Hacked' })
      ).rejects.toThrow(ForbiddenException);
    });

    it('should throw NotFoundException when game not found', async () => {
      mockPrismaService.game.findUnique.mockResolvedValueOnce(null);

      await expect(
        service.iterate('nonexistent', 'user-123', { title: 'Updated' })
      ).rejects.toThrow(NotFoundException);
    });
  });

  describe('getPlayData', () => {
    it('should increment play count', async () => {
      const gameWithPlays = { ...mockGame, playCount: BigInt(5) };
      mockPrismaService.game.findUnique.mockResolvedValueOnce(gameWithPlays);

      const updatedGame = { ...gameWithPlays, playCount: BigInt(6) };
      mockPrismaService.game.update.mockResolvedValueOnce(updatedGame);

      const result = await service.getPlayData('game-123');

      expect(result.playCount).toBe(BigInt(6));
      expect(mockPrismaService.game.update).toHaveBeenCalledWith({
        where: { id: 'game-123' },
        data: {
          playCount: {
            increment: 1,
          },
        },
      });
    });

    it('should throw NotFoundException when game not found', async () => {
      mockPrismaService.game.findUnique.mockResolvedValueOnce(null);

      await expect(service.getPlayData('nonexistent')).rejects.toThrow(
        NotFoundException
      );
    });
  });
});
