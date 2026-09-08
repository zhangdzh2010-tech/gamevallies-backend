import { FeedService } from '../src/feed/feed.service';

const redis = { get: jest.fn(), setex: jest.fn() };
jest.mock('ioredis', () => jest.fn(() => redis));

describe('Production trending query', () => {
  it('ranks actual engagement even before any work has likes', async () => {
    const publishedAt = new Date();
    const prisma = { game: { findMany: jest.fn().mockResolvedValue([
      { id: 'a', likeCount: 0, playCount: 0, forkCount: 0, publishedAt },
      { id: 'b', likeCount: 0, playCount: 20, forkCount: 2, publishedAt },
    ]) } };
    const result = await new FeedService(prisma as any).getTrendingFeed();
    expect(result.data.map((game: { id: string }) => game.id)).toEqual(['b', 'a']);
  });
  beforeEach(() => { jest.clearAllMocks(); redis.get.mockResolvedValue(null); });

  it('bounds the real query and uses the candidate pool for pagination', async () => {
    const prisma = { game: { findMany: jest.fn().mockResolvedValue([
      { id: 'b', likeCount: 0, playCount: 0, publishedAt: new Date() },
      { id: 'a', likeCount: 0, playCount: 0, publishedAt: new Date() },
    ]) } };
    const service = new FeedService(prisma as any);
    const result = await service.getTrendingFeed(1, 1, 'cats');
    expect(prisma.game.findMany).toHaveBeenCalledWith(expect.objectContaining({
      take: 1000, orderBy: [{ publishedAt: 'desc' }, { id: 'asc' }],
      where: expect.objectContaining({ status: 'published', visibility: 'public' }),
    }));
    expect(result.data[0].id).toBe('a');
    expect(result.pagination).toEqual({ page: 1, limit: 1, total: 2, pages: 2 });
  });

  it('serves cached ranking without querying the database', async () => {
    redis.get.mockResolvedValue(JSON.stringify({ data: [], pagination: { total: 0 } }));
    const prisma = { game: { findMany: jest.fn() } };
    await new FeedService(prisma as any).getTrendingFeed();
    expect(prisma.game.findMany).not.toHaveBeenCalled();
  });

  it('normalizes invalid pagination before building the query/cache key', async () => {
    const prisma = { game: { findMany: jest.fn().mockResolvedValue([]) } };
    const result = await new FeedService(prisma as any).getTrendingFeed(-2, Infinity);
    expect(result.pagination.page).toBe(1);
    expect(result.pagination.limit).toBe(20);
    expect(redis.get).toHaveBeenCalledWith(expect.stringContaining(':1:20:'));
  });
});
