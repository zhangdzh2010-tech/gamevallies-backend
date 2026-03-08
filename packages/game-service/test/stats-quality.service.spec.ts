import { StatsService } from '../src/stats/stats.service';

describe('StatsService – qualityScore refresh (P2.1)', () => {
  let service: StatsService;
  let mockPrisma: any;

  beforeEach(() => {
    mockPrisma = {
      game: {
        update: jest.fn().mockResolvedValue({ playCount: BigInt(10) }),
        findUnique: jest.fn(),
      },
    };
    service = new StatsService(mockPrisma as any);
  });

  describe('refreshQualityScore()', () => {
    it('skips update when playCount < 10', async () => {
      mockPrisma.game.findUnique.mockResolvedValue({
        qualityScore: 7.0,
        playCount: BigInt(5),
        likeCount: BigInt(2),
        avgPlayTime: 30,
      });
      await service.refreshQualityScore('game-1');
      expect(mockPrisma.game.update).not.toHaveBeenCalled();
    });

    it('increases score for high engagement', async () => {
      mockPrisma.game.findUnique.mockResolvedValue({
        qualityScore: 6.0,
        playCount: BigInt(100),
        likeCount: BigInt(80),
        avgPlayTime: 90,
      });
      await service.refreshQualityScore('game-2');
      const updateCall = mockPrisma.game.update.mock.calls[0][0];
      const newScore = updateCall.data.qualityScore;
      expect(newScore).toBeGreaterThan(6.0);
      expect(newScore).toBeLessThanOrEqual(10);
    });

    it('decreases score for low engagement', async () => {
      mockPrisma.game.findUnique.mockResolvedValue({
        qualityScore: 8.0,
        playCount: BigInt(100),
        likeCount: BigInt(1),
        avgPlayTime: 3,
      });
      await service.refreshQualityScore('game-3');
      const updateCall = mockPrisma.game.update.mock.calls[0][0];
      const newScore = updateCall.data.qualityScore;
      expect(newScore).toBeLessThan(8.0);
    });

    it('clamps score to 0-10 range', async () => {
      mockPrisma.game.findUnique.mockResolvedValue({
        qualityScore: 9.5,
        playCount: BigInt(1000),
        likeCount: BigInt(990),
        avgPlayTime: 300,
      });
      await service.refreshQualityScore('game-4');
      const updateCall = mockPrisma.game.update.mock.calls[0][0];
      expect(updateCall.data.qualityScore).toBeLessThanOrEqual(10);
      expect(updateCall.data.qualityScore).toBeGreaterThanOrEqual(0);
    });

    it('skips if game not found', async () => {
      mockPrisma.game.findUnique.mockResolvedValue(null);
      await expect(service.refreshQualityScore('nonexistent')).resolves.not.toThrow();
      expect(mockPrisma.game.update).not.toHaveBeenCalled();
    });
  });

  describe('incrementPlayCount() – triggers refresh at multiples of 10', () => {
    it('triggers refreshQualityScore at playCount=10', async () => {
      mockPrisma.game.update.mockResolvedValue({ playCount: BigInt(10) });
      const refreshSpy = jest.spyOn(service, 'refreshQualityScore').mockResolvedValue();
      await service.incrementPlayCount('game-5');
      // setImmediate callback
      await new Promise((r) => setImmediate(r));
      expect(refreshSpy).toHaveBeenCalledWith('game-5');
    });

    it('does not trigger refresh at playCount=7', async () => {
      mockPrisma.game.update.mockResolvedValue({ playCount: BigInt(7) });
      const refreshSpy = jest.spyOn(service, 'refreshQualityScore').mockResolvedValue();
      await service.incrementPlayCount('game-6');
      await new Promise((r) => setImmediate(r));
      expect(refreshSpy).not.toHaveBeenCalled();
    });
  });

  describe('incrementLikeCount() – always triggers refresh', () => {
    it('triggers refreshQualityScore on like', async () => {
      const refreshSpy = jest.spyOn(service, 'refreshQualityScore').mockResolvedValue();
      await service.incrementLikeCount('game-7');
      await new Promise((r) => setImmediate(r));
      expect(refreshSpy).toHaveBeenCalledWith('game-7');
    });
  });
});
