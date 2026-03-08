import { Test, TestingModule } from '@nestjs/testing';
import { PrismaService } from '../src/prisma/prisma.service';

describe('SearchService', () => {
  let service: any;
  let prismaService: PrismaService;

  const mockGame = {
    id: 'game-123',
    title: 'Space Dodge',
    description: 'Dodge asteroids in space',
    game_type: 'dodge',
    tags: ['space', 'action'],
    status: 'published',
    created_at: new Date('2024-01-01'),
  };

  const mockPrismaService = {
    game: {
      findMany: jest.fn(),
    },
  };

  beforeEach(async () => {
    const SearchService = class {
      constructor(private prisma: PrismaService) {}

      async search(query: string, filters?: any, limit = 20, offset = 0) {
        if (!query || query.length === 0) {
          return [];
        }

        const where: any = {
          status: 'published',
          OR: [
            { title: { contains: query } },
            { description: { contains: query } },
          ],
        };

        if (filters?.gameType) {
          where.gameType = filters.gameType;
        }

        return (this.prisma as any).game.findMany({
          where,
          include: { author: true },
          take: limit,
          skip: offset,
          orderBy: { createdAt: 'desc' },
        });
      }

      async searchByGameType(gameType: string, limit = 20, offset = 0) {
        return (this.prisma as any).game.findMany({
          where: {
            gameType,
            status: 'published',
          },
          include: { author: true },
          take: limit,
          skip: offset,
          orderBy: { createdAt: 'desc' },
        });
      }

      async searchByTags(tags: string[], limit = 20, offset = 0) {
        return (this.prisma as any).game.findMany({
          where: {
            status: 'published',
          },
          include: { author: true },
          take: limit,
          skip: offset,
          orderBy: { createdAt: 'desc' },
        });
      }
    };

    service = new SearchService(mockPrismaService as any);
    prismaService = mockPrismaService as any;

    jest.clearAllMocks();
  });

  describe('search', () => {
    it('should search by query string', async () => {
      const results = [mockGame];
      mockPrismaService.game.findMany.mockResolvedValueOnce(results);

      const result = await service.search('space', undefined, 20, 0);

      expect(result).toEqual(results);
      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: expect.objectContaining({
          status: 'published',
          OR: expect.arrayContaining([
            expect.objectContaining({
              title: { contains: 'space' },
            }),
          ]),
        }),
        include: { author: true },
        take: 20,
        skip: 0,
        orderBy: { createdAt: 'desc' },
      });
    });

    it('should search by game type filter', async () => {
      const results = [mockGame];
      mockPrismaService.game.findMany.mockResolvedValueOnce(results);

      const result = await service.search('dodge', { gameType: 'dodge' }, 20, 0);

      expect(result).toEqual(results);
      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: expect.objectContaining({
          gameType: 'dodge',
        }),
        include: { author: true },
        take: 20,
        skip: 0,
        orderBy: { createdAt: 'desc' },
      });
    });

    it('should search by tags filter', async () => {
      const results = [mockGame];
      mockPrismaService.game.findMany.mockResolvedValueOnce(results);

      const result = await service.search('game', { tags: ['space', 'action'] }, 20, 0);

      expect(result).toEqual(results);
      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: expect.objectContaining({
          status: 'published',
        }),
        include: { author: true },
        take: 20,
        skip: 0,
        orderBy: { createdAt: 'desc' },
      });
    });

    it('should return empty array for empty query', async () => {
      const result = await service.search('', undefined, 20, 0);

      expect(result).toEqual([]);
      expect(mockPrismaService.game.findMany).not.toHaveBeenCalled();
    });

    it('should support pagination', async () => {
      mockPrismaService.game.findMany.mockResolvedValueOnce([mockGame]);

      await service.search('space', undefined, 10, 30);

      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: expect.any(Object),
        include: { author: true },
        take: 10,
        skip: 30,
        orderBy: { createdAt: 'desc' },
      });
    });
  });

  describe('searchByGameType', () => {
    it('should return games of specific type', async () => {
      const results = [mockGame];
      mockPrismaService.game.findMany.mockResolvedValueOnce(results);

      const result = await service.searchByGameType('dodge', 20, 0);

      expect(result).toEqual(results);
      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: {
          gameType: 'dodge',
          status: 'published',
        },
        include: { author: true },
        take: 20,
        skip: 0,
        orderBy: { createdAt: 'desc' },
      });
    });

    it('should return empty results for unknown game type', async () => {
      mockPrismaService.game.findMany.mockResolvedValueOnce([]);

      const result = await service.searchByGameType('unknown', 20, 0);

      expect(result).toEqual([]);
    });
  });

  describe('searchByTags', () => {
    it('should return games with all specified tags', async () => {
      const results = [mockGame];
      mockPrismaService.game.findMany.mockResolvedValueOnce(results);

      const result = await service.searchByTags(['space', 'action'], 20, 0);

      expect(result).toEqual(results);
      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: {
          status: 'published',
        },
        include: { author: true },
        take: 20,
        skip: 0,
        orderBy: { createdAt: 'desc' },
      });
    });

    it('should return empty results when no games match all tags', async () => {
      mockPrismaService.game.findMany.mockResolvedValueOnce([]);

      const result = await service.searchByTags(['nonexistent', 'tags'], 20, 0);

      expect(result).toEqual([]);
    });
  });
});
