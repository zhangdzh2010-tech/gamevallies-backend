import { NotFoundException } from '@nestjs/common';
import { ForkService } from '../src/fork/fork.service';

describe('ForkService', () => {
  let service: ForkService;
  let prisma: any;

  beforeEach(() => {
    prisma = {
      game: {
        findUnique: jest.fn(),
        findMany: jest.fn(),
        count: jest.fn(),
      },
    };

    service = new ForkService(prisma);
  });

  it('only lists published public forks in fork listings', async () => {
    prisma.game.findMany.mockResolvedValue([]);
    prisma.game.count.mockResolvedValue(0);

    await service.getForks('game-source', 2, 20);

    expect(prisma.game.findMany).toHaveBeenCalledWith(expect.objectContaining({
      where: {
        forkedFrom: 'game-source',
        status: 'published',
        visibility: 'public',
      },
      skip: 20,
      take: 20,
    }));
    expect(prisma.game.count).toHaveBeenCalledWith({
      where: {
        forkedFrom: 'game-source',
        status: 'published',
        visibility: 'public',
      },
    });
  });

  it('filters non-public parents from fork tree results', async () => {
    prisma.game.findUnique
      .mockResolvedValueOnce({
        id: 'game-child',
        title: 'Child',
        status: 'published',
        visibility: 'public',
        forkedFrom: 'game-parent',
        author: { id: 'author-child', username: 'child', avatarUrl: '' },
      })
      .mockResolvedValueOnce({
        id: 'game-parent',
        title: 'Parent',
        status: 'draft',
        visibility: 'private',
        author: { id: 'author-parent', username: 'parent', avatarUrl: '' },
      });
    prisma.game.findMany.mockResolvedValue([]);

    const result = await service.getForkTree('game-child');

    expect(result.parent).toBeNull();
    expect(result.game.id).toBe('game-child');
    expect(result.children).toEqual([]);
  });

  it('rejects lineage lookups for non-public games', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-private',
      title: 'Private',
      status: 'draft',
      visibility: 'private',
      forkedFrom: null,
      forkDepth: 0,
      authorId: 'author-1',
      createdAt: new Date(),
    });

    await expect(service.getForkLineage('game-private')).rejects.toBeInstanceOf(NotFoundException);
  });
});
