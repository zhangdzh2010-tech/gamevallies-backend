import { CreatorReputationService } from '../src/game/creator-reputation.service';

describe('CreatorReputationService', () => {
  let service: CreatorReputationService;
  let mockPrisma: any;

  beforeEach(() => {
    mockPrisma = { game: { findMany: jest.fn() } };
    service = new CreatorReputationService(mockPrisma as any);
  });

  const makeGames = (overrides: any[]) =>
    overrides.map((o) => ({
      status: 'published',
      qualityScore: 7.0,
      playCount: BigInt(100),
      likeCount: BigInt(20),
      ...o,
    }));

  describe('getReputation()', () => {
    it('returns new tier for creator with < 3 published games', async () => {
      mockPrisma.game.findMany.mockResolvedValue(makeGames([
        { status: 'published', qualityScore: 8, playCount: BigInt(50), likeCount: BigInt(10) },
      ]));
      const rep = await service.getReputation('user-1');
      expect(rep.tier).toBe('new');
      expect(rep.publishedGames).toBe(1);
    });

    it('returns trusted tier for good creator with >= 3 games', async () => {
      mockPrisma.game.findMany.mockResolvedValue(makeGames([
        { qualityScore: 8, playCount: BigInt(200), likeCount: BigInt(80) },
        { qualityScore: 7.5, playCount: BigInt(150), likeCount: BigInt(50) },
        { qualityScore: 8.5, playCount: BigInt(300), likeCount: BigInt(100) },
      ]));
      const rep = await service.getReputation('user-2');
      expect(['trusted', 'verified']).toContain(rep.tier);
    });

    it('flags creator with > 30% failed generations', async () => {
      const games = [
        { status: 'failed', qualityScore: 0, playCount: BigInt(0), likeCount: BigInt(0) },
        { status: 'failed', qualityScore: 0, playCount: BigInt(0), likeCount: BigInt(0) },
        { status: 'published', qualityScore: 5, playCount: BigInt(50), likeCount: BigInt(5) },
      ];
      mockPrisma.game.findMany.mockResolvedValue(games);
      const rep = await service.getReputation('bad-creator');
      expect(rep.tier).toBe('flagged');
      expect(rep.failedGenerations).toBe(2);
    });

    it('returns zero avgQualityScore when no published games', async () => {
      mockPrisma.game.findMany.mockResolvedValue([
        { status: 'failed', qualityScore: 0, playCount: BigInt(0), likeCount: BigInt(0) },
      ]);
      const rep = await service.getReputation('new-creator');
      expect(rep.avgQualityScore).toBe(0);
      expect(rep.publishedGames).toBe(0);
    });

    it('computes reputationScore between 0 and 100', async () => {
      mockPrisma.game.findMany.mockResolvedValue(makeGames([
        { qualityScore: 9, playCount: BigInt(500), likeCount: BigInt(200) },
        { qualityScore: 8, playCount: BigInt(300), likeCount: BigInt(100) },
        { qualityScore: 7.5, playCount: BigInt(200), likeCount: BigInt(60) },
      ]));
      const rep = await service.getReputation('good-creator');
      expect(rep.reputationScore).toBeGreaterThanOrEqual(0);
      expect(rep.reputationScore).toBeLessThanOrEqual(100);
    });
  });

  describe('getQAConfig()', () => {
    it('returns strict config for flagged creator', async () => {
      mockPrisma.game.findMany.mockResolvedValue([
        { status: 'failed', qualityScore: 0, playCount: BigInt(0), likeCount: BigInt(0) },
        { status: 'failed', qualityScore: 0, playCount: BigInt(0), likeCount: BigInt(0) },
        { status: 'published', qualityScore: 3, playCount: BigInt(10), likeCount: BigInt(0) },
      ]);
      const config = await service.getQAConfig('bad-creator');
      expect(config.maxRetries).toBe(5);
      expect(config.requiresHumanReview).toBe(true);
    });

    it('returns normal config for new creator', async () => {
      mockPrisma.game.findMany.mockResolvedValue(makeGames([
        { qualityScore: 6, playCount: BigInt(20), likeCount: BigInt(5) },
      ]));
      const config = await service.getQAConfig('new-creator');
      expect(config.maxRetries).toBe(3);
      expect(config.requiresHumanReview).toBe(false);
    });
  });
});
