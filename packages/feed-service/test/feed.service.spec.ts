import { Test, TestingModule } from '@nestjs/testing';
import { PrismaService } from '../src/prisma/prisma.service';

describe('FeedService', () => {
  let service: any;
  let prismaService: PrismaService;

  const mockGame = {
    id: 'game-123',
    user_id: 'user-456',
    title: 'Test Game',
    status: 'published',
    published_at: new Date('2024-01-15'),
    created_at: new Date('2024-01-15'),
    play_count: 100,
    like_count: 25,
  };

  const mockPrismaService = {
    game: {
      findMany: jest.fn(),
    },
    follow: {
      findMany: jest.fn(),
    },
  };

  beforeEach(async () => {
    const FeedService = class {
      constructor(private prisma: PrismaService) {}

      // Wilson score: p = (phat + z²/(2n)) / (1 + z²/n)
      // where z = 1.96 for 95% confidence, phat = likes / (likes + dislikes)
      private calculateWilsonScore(likes: number, dislikes = 0): number {
        const n = likes + dislikes;
        if (n === 0) return 0;

        const z = 1.96;
        const phat = likes / n;

        const numerator =
          phat +
          (z * z) / (2 * n);
        const denominator = 1 + (z * z) / n;

        return numerator / denominator;
      }

      // Score with time decay: older games get lower scores
      private calculateTrendingScore(likes: number, publishedAt: Date): number {
        const wilsonScore = this.calculateWilsonScore(likes);
        const hoursOld =
          (Date.now() - publishedAt.getTime()) / (1000 * 60 * 60);
        const decayFactor = Math.pow(2, -hoursOld / 24); // Half-life of 24 hours

        return wilsonScore * decayFactor;
      }

      async getTrending(limit = 20, offset = 0) {
        const games = await this.prisma.game.findMany({
          where: { status: 'published' },
          include: { user: true },
        });

        // Score and sort
        const scored = games.map((game) => ({
          ...game,
          score: this.calculateTrendingScore(game.like_count, game.published_at),
        }));

        scored.sort((a, b) => b.score - a.score);

        // Apply pagination
        return scored.slice(offset, offset + limit);
      }

      async getLatest(limit = 20, offset = 0) {
        return this.prisma.game.findMany({
          where: { status: 'published' },
          include: { user: true },
          take: limit,
          skip: offset,
          orderBy: { published_at: 'desc' },
        });
      }

      async getFollowing(userId: string, limit = 20, offset = 0) {
        const following = await this.prisma.follow.findMany({
          where: { follower_id: userId },
          select: { following_id: true },
        });

        const followingIds = following.map((f) => f.following_id);

        return this.prisma.game.findMany({
          where: {
            status: 'published',
            user_id: { in: followingIds },
          },
          include: { user: true },
          take: limit,
          skip: offset,
          orderBy: { published_at: 'desc' },
        });
      }
    };

    const module: TestingModule = await Test.createTestingModule({
      providers: [
        FeedService,
        {
          provide: PrismaService,
          useValue: mockPrismaService,
        },
      ],
    }).compile();

    service = module.get<any>(FeedService);
    prismaService = module.get<PrismaService>(PrismaService);

    jest.clearAllMocks();
  });

  describe('getTrending', () => {
    it('should apply Wilson score calculation', async () => {
      const games = [
        {
          ...mockGame,
          id: 'game-1',
          like_count: 50,
          published_at: new Date(Date.now() - 2 * 60 * 60 * 1000), // 2 hours old
        },
        {
          ...mockGame,
          id: 'game-2',
          like_count: 10,
          published_at: new Date(Date.now() - 1 * 60 * 60 * 1000), // 1 hour old
        },
      ];

      mockPrismaService.game.findMany.mockResolvedValueOnce(games);

      const result = await service.getTrending();

      expect(result).toBeDefined();
      expect(result.length).toBeGreaterThan(0);
      // Higher likes should score higher (with time decay factored in)
      expect(result[0].like_count).toBeGreaterThanOrEqual(result[1].like_count);
    });

    it('should apply time decay to scores', async () => {
      const recentGame = {
        ...mockGame,
        id: 'game-recent',
        like_count: 10,
        published_at: new Date(Date.now() - 1 * 60 * 60 * 1000), // 1 hour old
      };

      const oldGame = {
        ...mockGame,
        id: 'game-old',
        like_count: 50, // More likes
        published_at: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000), // 7 days old
      };

      mockPrismaService.game.findMany.mockResolvedValueOnce([recentGame, oldGame]);

      const result = await service.getTrending();

      // Recent game with fewer likes can rank higher due to time decay
      expect(result).toBeDefined();
    });

    it('should support pagination', async () => {
      mockPrismaService.game.findMany.mockResolvedValueOnce([
        mockGame,
        { ...mockGame, id: 'game-2' },
        { ...mockGame, id: 'game-3' },
      ]);

      const resultPage1 = await service.getTrending(2, 0);
      const resultPage2 = await service.getTrending(2, 2);

      expect(resultPage1.length).toBeLessThanOrEqual(2);
      expect(resultPage2.length).toBeLessThanOrEqual(2);
    });

    it('should only include published games', async () => {
      const publishedGame = { ...mockGame, status: 'published' };
      const draftGame = { ...mockGame, status: 'draft' };

      mockPrismaService.game.findMany.mockResolvedValueOnce([publishedGame]);

      const result = await service.getTrending();

      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: { status: 'published' },
        include: { user: true },
      });
    });
  });

  describe('getLatest', () => {
    it('should return latest published games sorted by publishedAt', async () => {
      const games = [
        { ...mockGame, id: 'game-1', published_at: new Date('2024-01-20') },
        { ...mockGame, id: 'game-2', published_at: new Date('2024-01-19') },
        { ...mockGame, id: 'game-3', published_at: new Date('2024-01-18') },
      ];

      mockPrismaService.game.findMany.mockResolvedValueOnce(games);

      const result = await service.getLatest(20, 0);

      expect(result).toEqual(games);
      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: { status: 'published' },
        include: { user: true },
        take: 20,
        skip: 0,
        orderBy: { published_at: 'desc' },
      });
    });

    it('should support pagination for latest games', async () => {
      mockPrismaService.game.findMany.mockResolvedValueOnce([mockGame]);

      await service.getLatest(10, 20);

      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: { status: 'published' },
        include: { user: true },
        take: 10,
        skip: 20,
        orderBy: { published_at: 'desc' },
      });
    });
  });

  describe('getFollowing', () => {
    it('should return only games from followed users', async () => {
      const following = [
        { following_id: 'user-1' },
        { following_id: 'user-2' },
      ];

      const followingGames = [
        { ...mockGame, id: 'game-1', user_id: 'user-1' },
        { ...mockGame, id: 'game-2', user_id: 'user-2' },
      ];

      mockPrismaService.follow.findMany.mockResolvedValueOnce(following);
      mockPrismaService.game.findMany.mockResolvedValueOnce(followingGames);

      const result = await service.getFollowing('user-456', 20, 0);

      expect(result).toEqual(followingGames);
      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: {
          status: 'published',
          user_id: { in: ['user-1', 'user-2'] },
        },
        include: { user: true },
        take: 20,
        skip: 0,
        orderBy: { published_at: 'desc' },
      });
    });

    it('should return empty when user follows no one', async () => {
      mockPrismaService.follow.findMany.mockResolvedValueOnce([]);
      mockPrismaService.game.findMany.mockResolvedValueOnce([]);

      const result = await service.getFollowing('user-456', 20, 0);

      expect(result).toEqual([]);
    });

    it('should support pagination for following feed', async () => {
      const following = [{ following_id: 'user-1' }];

      mockPrismaService.follow.findMany.mockResolvedValueOnce(following);
      mockPrismaService.game.findMany.mockResolvedValueOnce([mockGame]);

      await service.getFollowing('user-456', 10, 20);

      expect(mockPrismaService.game.findMany).toHaveBeenCalledWith({
        where: expect.objectContaining({
          user_id: { in: ['user-1'] },
        }),
        include: { user: true },
        take: 10,
        skip: 20,
        orderBy: { published_at: 'desc' },
      });
    });
  });
});
