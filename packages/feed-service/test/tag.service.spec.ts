import { TagService } from '../src/tag/tag.service';

describe('TagService', () => {
  let service: TagService;
  let prisma: any;

  beforeEach(() => {
    prisma = {
      game: {
        findMany: jest.fn(),
      },
    };

    service = new TagService(prisma);
  });

  it('only aggregates tags from public published games for trending tags', async () => {
    prisma.game.findMany.mockResolvedValue([
      { tags: ['runner', 'arcade'] },
      { tags: ['runner'] },
    ]);

    const result = await service.getTrendingTags(1, 10);

    expect(prisma.game.findMany).toHaveBeenCalledWith({
      where: {
        status: 'published',
        visibility: 'public',
        publishedAt: {
          gte: expect.any(Date),
        },
      },
      select: { tags: true },
    });
    expect(result.data).toEqual([
      { id: 'tag-0', name: 'runner', count: 2 },
      { id: 'tag-1', name: 'arcade', count: 1 },
    ]);
  });

  it('only aggregates tags from public published games for all tags', async () => {
    prisma.game.findMany.mockResolvedValue([
      { tags: ['zeta', 'alpha'] },
      { tags: ['alpha'] },
    ]);

    const result = await service.getAllTags(1, 10);

    expect(prisma.game.findMany).toHaveBeenCalledWith({
      where: {
        status: 'published',
        visibility: 'public',
      },
      select: { tags: true },
    });
    expect(result.data).toEqual([
      { id: 'tag-0', name: 'alpha', count: 2 },
      { id: 'tag-1', name: 'zeta', count: 1 },
    ]);
  });
});
