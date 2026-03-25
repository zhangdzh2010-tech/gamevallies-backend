import { BadRequestException } from '@nestjs/common';
import { ForkService } from '../src/fork/fork.service';

describe('ForkService', () => {
  let service: ForkService;
  let prisma: any;
  let bundleService: any;
  let statsService: any;

  const sourceGame = {
    id: 'game-source',
    authorId: 'user-source',
    title: 'Original Game',
    description: 'Original description',
    tags: ['arcade'],
    gameType: 'dodge',
    status: 'published',
    visibility: 'public',
    allowFork: true,
    allowComments: true,
    forkDepth: 1,
    version: 3,
  };

  const sourceBundle = {
    id: 'bundle-source',
    version: 3,
    htmlCode: '<!DOCTYPE html><html><body>source</body></html>',
    cssCode: 'body { background: #000; }',
    jsCode: 'console.log("source");',
    previewUrl: 'https://legacy.example/games/game-source/preview',
    metadata: {
      strategy: 'full_generation',
      customFlag: 'keep-me',
      generationTaskId: 'task-source',
      routeSnapshot: { region: 'cn_shanghai' },
    },
  };

  beforeEach(() => {
    prisma = {
      game: {
        findUnique: jest.fn(),
        create: jest.fn(),
        delete: jest.fn(),
        findMany: jest.fn(),
        count: jest.fn(),
      },
    };

    bundleService = {
      getBundle: jest.fn(),
      getLatestBundle: jest.fn(),
      saveBundle: jest.fn(),
    };

    statsService = {
      incrementForkCount: jest.fn(),
    };

    service = new ForkService(prisma, bundleService, statsService);
  });

  it('creates a private draft fork from a published public source game', async () => {
    prisma.game.findUnique
      .mockResolvedValueOnce(sourceGame)
      .mockResolvedValueOnce({
        id: 'game-forked',
        title: 'Original Game (Fork)',
        author: {
          id: 'user-target',
          username: 'target',
          avatarUrl: '',
        },
      });
    prisma.game.create.mockResolvedValue({
      id: 'game-forked',
    });
    bundleService.getBundle.mockResolvedValue(sourceBundle);
    bundleService.saveBundle.mockResolvedValue({});
    statsService.incrementForkCount.mockResolvedValue(undefined);

    const result = await service.forkGame('game-source', 'user-target');

    expect(prisma.game.create).toHaveBeenCalledWith({
      data: expect.objectContaining({
        authorId: 'user-target',
        title: 'Original Game (Fork)',
        status: 'draft',
        visibility: 'private',
        allowComments: true,
        allowFork: true,
        canPlay: true,
        requireSubscription: false,
        forkedFrom: 'game-source',
        forkDepth: 2,
      }),
    });

    const savedBundle = bundleService.saveBundle.mock.calls[0][0];
    expect(bundleService.getBundle).toHaveBeenCalledWith('game-source', 3);
    expect(bundleService.getLatestBundle).not.toHaveBeenCalled();
    expect(savedBundle.previewUrl).toBeUndefined();
    expect(savedBundle.metadata).toEqual(expect.objectContaining({
      customFlag: 'keep-me',
      forkedFromGameId: 'game-source',
      forkedFromBundleId: 'bundle-source',
      forkedFromVersion: 3,
    }));
    expect(savedBundle.metadata.generationTaskId).toBeUndefined();
    expect(savedBundle.metadata.routeSnapshot).toBeUndefined();
    expect(result.id).toBe('game-forked');
  });

  it('rejects forking games that are not published', async () => {
    prisma.game.findUnique.mockResolvedValue({
      ...sourceGame,
      status: 'draft',
    });

    await expect(service.forkGame('game-source', 'user-target')).rejects.toBeInstanceOf(BadRequestException);
    expect(bundleService.getBundle).not.toHaveBeenCalled();
    expect(bundleService.getLatestBundle).not.toHaveBeenCalled();
    expect(prisma.game.create).not.toHaveBeenCalled();
  });

  it('rejects forking when the source game has forking disabled', async () => {
    prisma.game.findUnique.mockResolvedValue({
      ...sourceGame,
      allowFork: false,
    });

    await expect(service.forkGame('game-source', 'user-target')).rejects.toBeInstanceOf(BadRequestException);
    expect(bundleService.getBundle).not.toHaveBeenCalled();
    expect(prisma.game.create).not.toHaveBeenCalled();
  });

  it('rolls back the forked game row when bundle copy fails', async () => {
    prisma.game.findUnique.mockResolvedValueOnce(sourceGame);
    prisma.game.create.mockResolvedValue({
      id: 'game-forked',
    });
    prisma.game.delete.mockResolvedValue({});
    bundleService.getBundle.mockResolvedValue(sourceBundle);
    bundleService.saveBundle.mockRejectedValue(new Error('save failed'));

    await expect(service.forkGame('game-source', 'user-target')).rejects.toBeInstanceOf(BadRequestException);
    expect(prisma.game.delete).toHaveBeenCalledWith({
      where: { id: expect.any(String) },
    });
  });

  it('falls back to the latest bundle when the live version bundle is missing', async () => {
    prisma.game.findUnique
      .mockResolvedValueOnce(sourceGame)
      .mockResolvedValueOnce({
        id: 'game-forked',
        title: 'Original Game (Fork)',
        author: {
          id: 'user-target',
          username: 'target',
          avatarUrl: '',
        },
      });
    prisma.game.create.mockResolvedValue({
      id: 'game-forked',
    });
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue(sourceBundle);
    bundleService.saveBundle.mockResolvedValue({});
    statsService.incrementForkCount.mockResolvedValue(undefined);

    await service.forkGame('game-source', 'user-target');

    expect(bundleService.getBundle).toHaveBeenCalledWith('game-source', 3);
    expect(bundleService.getLatestBundle).toHaveBeenCalledWith('game-source');
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
});
