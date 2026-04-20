import { ForbiddenException, NotFoundException } from '@nestjs/common';
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

  // H.9.1 - cannot fork your own game; the error must carry FORK_FORBIDDEN_SELF
  // and the authorId so the frontend can route to "继续创作" instead of toasting
  // "加载失败".
  it('rejects forking your own game with FORK_FORBIDDEN_SELF', async () => {
    prisma.$transaction = jest.fn(async (cb: any) =>
      cb({
        game: {
          findUnique: jest.fn().mockResolvedValue({
            id: 'game-mine',
            authorId: 'author-self',
            title: 'Mine',
            description: 'desc',
            userIdea: 'desc',
            gameType: 'casual',
            tags: [],
            status: 'published',
            visibility: 'public',
            allowComments: true,
            allowFork: true,
            forkDepth: 0,
            thumbnailUrl: null,
          }),
          create: jest.fn(),
        },
        gameBundle: { findFirst: jest.fn(), create: jest.fn() },
      }),
    );

    await expect(
      service.forkGame('game-mine', 'author-self'),
    ).rejects.toMatchObject({
      response: expect.objectContaining({
        errorCode: 'FORK_FORBIDDEN_SELF',
        authorId: 'author-self',
      }),
    });
    await expect(
      service.forkGame('game-mine', 'author-self'),
    ).rejects.toBeInstanceOf(ForbiddenException);
  });

  it('rejects forking a game whose author disabled forking with FORK_FORBIDDEN_BY_AUTHOR', async () => {
    prisma.$transaction = jest.fn(async (cb: any) =>
      cb({
        game: {
          findUnique: jest.fn().mockResolvedValue({
            id: 'game-locked',
            authorId: 'author-other',
            title: 'Locked',
            description: 'desc',
            userIdea: 'desc',
            gameType: 'casual',
            tags: [],
            status: 'published',
            visibility: 'public',
            allowComments: true,
            allowFork: false,
            forkDepth: 0,
            thumbnailUrl: null,
          }),
          create: jest.fn(),
        },
        gameBundle: { findFirst: jest.fn(), create: jest.fn() },
      }),
    );

    await expect(
      service.forkGame('game-locked', 'visitor'),
    ).rejects.toMatchObject({
      response: expect.objectContaining({
        errorCode: 'FORK_FORBIDDEN_BY_AUTHOR',
        authorId: 'author-other',
      }),
    });
  });
});
