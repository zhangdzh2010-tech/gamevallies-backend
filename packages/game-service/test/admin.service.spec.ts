import * as bcrypt from 'bcryptjs';
import axios from 'axios';
import { ConfigService } from '@nestjs/config';
import { AdminService } from '../src/admin/admin.service';

describe('AdminService', () => {
  let service: AdminService;
  let prisma: any;
  let configService: ConfigService;
  let gameService: any;

  beforeEach(() => {
    prisma = {
      game: {
        findMany: jest.fn(),
        update: jest.fn(),
        count: jest.fn(),
      },
      user: {
        findUnique: jest.fn(),
        create: jest.fn(),
        update: jest.fn(),
      },
      cloudProviderAccount: {
        findUnique: jest.fn(),
      },
      cloudRegionCatalog: {
        findUnique: jest.fn(),
      },
      aiEngineRegionTarget: {
        findUnique: jest.fn(),
        findMany: jest.fn(),
        upsert: jest.fn(),
        update: jest.fn(),
      },
      generationTask: {
        findMany: jest.fn(),
        count: jest.fn(),
        findUnique: jest.fn(),
      },
      generationTaskEvent: {
        findMany: jest.fn(),
      },
      systemConfig: {
        findMany: jest.fn(),
        findUnique: jest.fn(),
        upsert: jest.fn(),
        create: jest.fn(),
      },
      llmStepCatalog: {
        findMany: jest.fn(),
        findUnique: jest.fn(),
      },
      llmGatewayProvider: {
        findUnique: jest.fn(),
        findMany: jest.fn(),
        upsert: jest.fn(),
      },
      llmStepRoute: {
        findMany: jest.fn(),
        findUnique: jest.fn(),
        upsert: jest.fn(),
      },
    };
    configService = {
      get: jest.fn((key: string, defaultValue?: string) => {
        const values: Record<string, string> = {
          AI_ENGINE_URL: 'http://ai-engine.test',
          AI_ENGINE_URL_CN_SHANGHAI: 'https://ai-cn.test',
          AI_ENGINE_URL_AP_SOUTHEAST_JOHOR: 'https://ai-jh.test',
        };
        return values[key] ?? defaultValue;
      }),
    } as unknown as ConfigService;
    gameService = {
      terminateActiveTasksForGame: jest.fn(),
      reconcileGenerationTask: jest.fn(async (task: any) => task),
      terminateTask: jest.fn(),
      refreshTimeoutConfigCache: jest.fn(),
      buildAdminPreviewUrls: jest.fn((gameId: string) => ({
        previewUrl: `https://gamevallies.com/games/${gameId}/preview?previewToken=admin`,
        gameUrl: `https://gamevallies.com/games/${gameId}/index.html?previewToken=admin`,
      })),
    };

    service = new AdminService(prisma, configService, gameService);
  });

  it('lists timeout configs by merging catalog defaults with db values', async () => {
    prisma.systemConfig.findMany.mockResolvedValue([
      {
        id: 'cfg-1',
        configKey: 'timeout.pipeline.default_s',
        configValue: '1800',
        description: 'custom timeout',
        category: 'timeout',
        createdAt: new Date('2026-03-25T00:00:00.000Z'),
        updatedAt: new Date('2026-03-25T00:05:00.000Z'),
      },
    ]);

    const result = await service.listConfigs('timeout');

    expect(prisma.systemConfig.findMany).toHaveBeenCalledWith({
      where: { category: 'timeout' },
      orderBy: [{ category: 'asc' }, { configKey: 'asc' }],
    });
    expect(result).toEqual(expect.arrayContaining([
      expect.objectContaining({
        configKey: 'timeout.pipeline.default_s',
        configValue: '1800',
        category: 'timeout',
        source: 'db',
        isDefault: false,
      }),
      expect.objectContaining({
        configKey: 'timeout.ai_engine.runtime_qa.max_s',
        category: 'timeout',
        source: 'catalog',
        isDefault: true,
      }),
    ]));
  });

  it('upserts timeout configs into timeout category and refreshes timeout caches', async () => {
    prisma.systemConfig.upsert.mockResolvedValue({
      id: 'cfg-timeout-1',
      configKey: 'timeout.pipeline.default_s',
      configValue: '1500',
      description: 'updated',
      category: 'timeout',
    });
    const refreshSpy = jest
      .spyOn(service, 'refreshTimeoutConfigs')
      .mockResolvedValue({ refreshed: 1, failed: 0, partialFailure: false } as any);

    const result = await service.upsertConfig('timeout.pipeline.default_s', {
      value: '1500',
    });

    expect(prisma.systemConfig.upsert).toHaveBeenCalledWith(expect.objectContaining({
      where: { configKey: 'timeout.pipeline.default_s' },
      update: expect.objectContaining({
        configValue: '1500',
        category: 'timeout',
      }),
      create: expect.objectContaining({
        configKey: 'timeout.pipeline.default_s',
        configValue: '1500',
        category: 'timeout',
      }),
    }));
    expect(refreshSpy).toHaveBeenCalledTimes(1);
    expect(result).toEqual(expect.objectContaining({
      configKey: 'timeout.pipeline.default_s',
      configValue: '1500',
      category: 'timeout',
      refreshResult: expect.objectContaining({
        refreshed: 1,
        failed: 0,
        partialFailure: false,
      }),
    }));

    refreshSpy.mockRestore();
  });

  it('refreshes timeout configs with partial success when one ai node is down', async () => {
    const originalAdminToken = process.env.ADMIN_TOKEN;
    process.env.ADMIN_TOKEN = 'admin-test-token';
    prisma.systemConfig.findUnique.mockResolvedValue({
      configValue: '2500',
    });
    prisma.aiEngineRegionTarget.findMany.mockResolvedValue([]);
    gameService.refreshTimeoutConfigCache.mockResolvedValue(undefined);
    const postSpy = jest.spyOn(axios, 'post')
      .mockResolvedValueOnce({ data: { ok: true, refreshed: 12 } } as any)
      .mockRejectedValueOnce(new Error('region unavailable'));

    try {
      const result = await service.refreshTimeoutConfigs();

      expect(gameService.refreshTimeoutConfigCache).toHaveBeenCalledTimes(1);
      expect(prisma.systemConfig.findUnique).toHaveBeenCalledWith({
        where: { configKey: 'timeout.game_service.admin_refresh_timeout_ms' },
        select: { configValue: true },
      });
      expect(postSpy).toHaveBeenCalledTimes(2);
      expect(postSpy).toHaveBeenNthCalledWith(
        1,
        'https://ai-cn.test/api/v1/ai/config/timeouts/refresh',
        {},
        expect.objectContaining({
          timeout: 2500,
          headers: { 'x-admin-token': 'admin-test-token' },
        }),
      );
      expect(postSpy).toHaveBeenNthCalledWith(
        2,
        'https://ai-jh.test/api/v1/ai/config/timeouts/refresh',
        {},
        expect.objectContaining({
          timeout: 2500,
          headers: { 'x-admin-token': 'admin-test-token' },
        }),
      );
      expect(result).toEqual(expect.objectContaining({
        refreshed: 2,
        failed: 1,
        partialFailure: true,
        gameService: { status: 'ok' },
      }));
      expect(result.aiEngine).toEqual([
        expect.objectContaining({
          baseUrl: 'https://ai-cn.test',
          status: 'ok',
          data: { ok: true, refreshed: 12 },
        }),
        expect.objectContaining({
          baseUrl: 'https://ai-jh.test',
          status: 'error',
          errorMessage: 'region unavailable',
        }),
      ]);
    } finally {
      postSpy.mockRestore();
      if (originalAdminToken === undefined) {
        delete process.env.ADMIN_TOKEN;
      } else {
        process.env.ADMIN_TOKEN = originalAdminToken;
      }
    }
  });

  it('backfills legacy preview-only games to published unlisted without touching tokenized tasks', async () => {
    const legacyCreatedAt = new Date('2026-03-20T08:00:00.000Z');
    prisma.game.findMany.mockResolvedValue([
      {
        id: 'game-legacy',
        title: 'Legacy Preview Game',
        status: 'draft',
        visibility: 'private',
        version: 1,
        codeBundleId: 'bundle-old',
        createdAt: legacyCreatedAt,
        publishedAt: null,
        bundles: [
          {
            id: 'bundle-new',
            version: 2,
            htmlCode: '<!DOCTYPE html><html><body>legacy</body></html>',
          },
        ],
        generationTasks: [
          {
            id: 'task-legacy',
            previewUrl: 'https://gamevallies.com/games/game-legacy/preview',
            createdAt: legacyCreatedAt,
          },
        ],
      },
      {
        id: 'game-tokenized',
        title: 'Tokenized Preview Game',
        status: 'draft',
        visibility: 'private',
        version: 1,
        codeBundleId: 'bundle-tokenized',
        createdAt: legacyCreatedAt,
        publishedAt: null,
        bundles: [
          {
            id: 'bundle-tokenized',
            version: 1,
            htmlCode: '<!DOCTYPE html><html><body>tokenized</body></html>',
          },
        ],
        generationTasks: [
          {
            id: 'task-tokenized',
            previewUrl: 'https://gamevallies.com/games/game-tokenized/preview?previewToken=admin',
            createdAt: legacyCreatedAt,
          },
        ],
      },
      {
        id: 'game-empty',
        title: 'Empty Bundle Game',
        status: 'review',
        visibility: 'private',
        version: 1,
        codeBundleId: null,
        createdAt: legacyCreatedAt,
        publishedAt: null,
        bundles: [
          {
            id: 'bundle-empty',
            version: 1,
            htmlCode: '   ',
          },
        ],
        generationTasks: [
          {
            id: 'task-empty',
            previewUrl: null,
            createdAt: legacyCreatedAt,
          },
        ],
      },
    ]);
    prisma.game.update.mockResolvedValue({});
    const invalidateFeedCacheSpy = jest
      .spyOn(service as any, 'invalidateFeedCache')
      .mockResolvedValue(undefined);

    const result = await service.backfillLegacyPreviewGames({ limit: 50 });

    expect(prisma.game.findMany).toHaveBeenCalledWith(expect.objectContaining({
      where: expect.objectContaining({
        status: {
          in: ['draft', 'review', 'published'],
        },
        visibility: {
          notIn: ['public', 'unlisted'],
        },
      }),
      take: 50,
    }));
    expect(prisma.game.update).toHaveBeenCalledTimes(1);
    expect(prisma.game.update).toHaveBeenCalledWith({
      where: {
        id: 'game-legacy',
      },
      data: {
        status: 'published',
        visibility: 'unlisted',
        publishedAt: legacyCreatedAt,
        version: 2,
        codeBundleId: 'bundle-new',
      },
    });
    expect(invalidateFeedCacheSpy).toHaveBeenCalledTimes(1);
    expect(result).toEqual(expect.objectContaining({
      dryRun: false,
      scanned: 3,
      eligible: 1,
      updated: 1,
    }));
    expect(result.items).toEqual([
      expect.objectContaining({
        id: 'game-legacy',
        previousStatus: 'draft',
        previousVisibility: 'private',
        latestBundleVersion: 2,
        targetStatus: 'published',
        targetVisibility: 'unlisted',
      }),
    ]);

    invalidateFeedCacheSpy.mockRestore();
  });

  it('aggregates generation log rows from the latest task summary instead of stale game failure fields', async () => {
    const gameCreatedAt = new Date('2026-03-20T08:00:00.000Z');
    const taskCreatedAt = new Date('2026-03-25T01:10:00.000Z');
    const taskCompletedAt = new Date('2026-03-25T01:12:00.000Z');

    prisma.game.findMany.mockResolvedValue([
      {
        id: 'game-1',
        title: 'Task Derived Game',
        description: 'latest result should come from task',
        status: 'draft',
        failedStage: 'stale_stage',
        failedReason: 'stale game failure',
        retryCount: 9,
        lastErrorAt: new Date('2026-03-20T09:00:00.000Z'),
        gameType: 'legacy_type',
        version: 2,
        createdAt: gameCreatedAt,
        updatedAt: new Date('2026-03-20T10:00:00.000Z'),
        author: {
          id: 'user-1',
          username: 'tester',
          displayName: 'Tester',
        },
        bundles: [
          {
            id: 'bundle-2',
            version: 2,
            metadata: {
              strategy: 'bundle-fallback',
              qaPassed: false,
              qaRetries: 8,
              genTimeMs: 8000,
              qualityScore: 0.42,
            },
            generationMeta: null,
            codeSizeBytes: 2048,
            createdAt: new Date('2026-03-20T08:30:00.000Z'),
          },
        ],
        generationTasks: [
          {
            id: 'task-1',
            status: 'succeeded',
            failedStage: null,
            errorMessage: null,
            retryCount: 1,
            resultSummary: {
              strategy: 'task-first',
              qaPassed: true,
              qaRetries: 2,
              iterationRetries: 1,
              generationTimeMs: 3456,
              codeSizeBytes: 4096,
              qualityScore: 0.91,
              version: 3,
              gameType: 'runner',
            },
            previewUrl: 'https://old-preview.example.com',
            createdAt: taskCreatedAt,
            updatedAt: taskCompletedAt,
            completedAt: taskCompletedAt,
          },
        ],
      },
      {
        id: 'game-2',
        title: 'Failed Attempt',
        description: 'task failure should win',
        status: 'published',
        failedStage: null,
        failedReason: null,
        retryCount: 0,
        lastErrorAt: null,
        gameType: 'arcade',
        version: 5,
        createdAt: gameCreatedAt,
        updatedAt: new Date('2026-03-20T11:00:00.000Z'),
        author: {
          id: 'user-2',
          username: 'operator',
          displayName: 'Operator',
        },
        bundles: [
          {
            id: 'bundle-5',
            version: 5,
            metadata: {},
            generationMeta: null,
            codeSizeBytes: 1024,
            createdAt: new Date('2026-03-20T11:10:00.000Z'),
          },
        ],
        generationTasks: [
          {
            id: 'task-failed',
            status: 'failed',
            failedStage: 'qa_checking',
            errorMessage: 'QA failed in latest task',
            retryCount: 3,
            resultSummary: {},
            previewUrl: null,
            createdAt: new Date('2026-03-25T02:00:00.000Z'),
            updatedAt: new Date('2026-03-25T02:03:00.000Z'),
            completedAt: new Date('2026-03-25T02:03:00.000Z'),
          },
        ],
      },
    ]);
    prisma.game.count.mockResolvedValue(2);

    const result = await service.listGenerationLogs(1, 20, 'failed', 'task');

    expect(prisma.game.findMany).toHaveBeenCalledWith(expect.objectContaining({
      where: expect.objectContaining({
        AND: expect.arrayContaining([
          expect.objectContaining({
            OR: expect.arrayContaining([
              expect.objectContaining({
                generationTasks: {
                  some: {
                    status: {
                      in: ['failed', 'timed_out', 'canceled'],
                    },
                  },
                },
              }),
            ]),
          }),
          expect.objectContaining({
            OR: expect.arrayContaining([
              { id: { contains: 'task' } },
              {
                generationTasks: {
                  some: {
                    id: { contains: 'task' },
                  },
                },
              },
            ]),
          }),
        ]),
      }),
      include: expect.objectContaining({
        generationTasks: expect.objectContaining({
          take: 1,
          orderBy: { createdAt: 'desc' },
        }),
      }),
    }));

    expect(result.items[0]).toEqual(expect.objectContaining({
      gameId: 'game-1',
      taskId: 'task-1',
      taskStatus: 'succeeded',
      status: 'draft',
      failedStage: null,
      failedReason: null,
      retryCount: 1,
      gameType: 'runner',
      strategy: 'task-first',
      qaPassed: true,
      qaRetries: 2,
      iterationRetries: 1,
      genTimeMs: 3456,
      codeSizeBytes: 4096,
      qualityScore: 0.91,
      version: 3,
      previewUrl: 'https://gamevallies.com/games/game-1/preview?previewToken=admin',
      gameUrl: 'https://gamevallies.com/games/game-1/index.html?previewToken=admin',
      createdAt: taskCreatedAt,
      updatedAt: taskCompletedAt,
      lastErrorAt: null,
    }));

    expect(result.items[1]).toEqual(expect.objectContaining({
      gameId: 'game-2',
      taskId: 'task-failed',
      taskStatus: 'failed',
      status: 'failed',
      failedStage: 'qa_checking',
      failedReason: 'QA failed in latest task',
      retryCount: 3,
    }));
  });

  it('hashes admin-created user passwords with bcrypt', async () => {
    prisma.user.findUnique
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
        id: 'user-1',
        username: 'smoke_user',
        displayName: 'Smoke User',
        email: null,
        phone: null,
        role: 'user',
        isPro: false,
        bio: null,
        avatarUrl: null,
        authProvider: 'email',
        followerCount: 0,
        followingCount: 0,
        gameCount: 0,
        totalPlays: 0,
        createdAt: new Date(),
        updatedAt: new Date(),
      });
    prisma.user.create.mockResolvedValue({ id: 'user-1' });

    await service.createUser({
      username: 'smoke_user',
      displayName: 'Smoke User',
      password: 'SmokePay123456',
    });

    const passwordHash = prisma.user.create.mock.calls[0][0].data.passwordHash;
    expect(typeof passwordHash).toBe('string');
    expect(passwordHash).not.toBe('SmokePay123456');
    await expect(bcrypt.compare('SmokePay123456', passwordHash)).resolves.toBe(true);
  });

  it('hashes reset passwords with bcrypt', async () => {
    prisma.user.findUnique.mockResolvedValue({ id: 'user-1' });
    prisma.user.update.mockResolvedValue({ id: 'user-1' });

    await service.resetUserPassword('user-1', 'ResetPass123');

    const passwordHash = prisma.user.update.mock.calls[0][0].data.passwordHash;
    expect(typeof passwordHash).toBe('string');
    expect(passwordHash).not.toBe('ResetPass123');
    await expect(bcrypt.compare('ResetPass123', passwordHash)).resolves.toBe(true);
  });

  it('lists enabled llm steps ordered by stepOrder then stepKey', async () => {
    prisma.llmStepCatalog.findMany.mockResolvedValue([
      { id: 'step-1', stepKey: 'intent_parse', stepOrder: 30, displayName: 'Intent Parse', enabled: true },
      { id: 'step-2', stepKey: 'code_generate.hybrid', stepOrder: 40, displayName: 'Code Generate', enabled: true },
    ]);

    const result = await service.listLlmSteps();

    expect(prisma.llmStepCatalog.findMany).toHaveBeenCalledWith({
      where: { enabled: true },
      orderBy: [{ stepOrder: 'asc' }, { stepKey: 'asc' }],
    });
    expect(result).toHaveLength(2);
    expect(result[0].stepKey).toBe('intent_parse');
  });

  it('returns ordered generation task events for the admin detail panel', async () => {
    prisma.generationTask.findUnique.mockResolvedValue({ id: 'task-1' });
    prisma.generationTaskEvent.findMany.mockResolvedValue([
      { id: 'evt-1', taskId: 'task-1', message: 'task started' },
      { id: 'evt-2', taskId: 'task-1', message: 'parse game intent' },
    ]);

    const result = await service.listGenerationTaskEvents('task-1', 20);

    expect(prisma.generationTask.findUnique).toHaveBeenCalledWith({
      where: { id: 'task-1' },
      select: { id: true },
    });
    expect(prisma.generationTaskEvent.findMany).toHaveBeenCalledWith({
      where: { taskId: 'task-1' },
      orderBy: { createdAt: 'asc' },
      take: 20,
    });
    expect(result).toEqual({
      items: [
        { id: 'evt-1', taskId: 'task-1', message: 'task started' },
        { id: 'evt-2', taskId: 'task-1', message: 'parse game intent' },
      ],
    });
  });

  it('returns task-centered generation detail payload with prompt and latest source bundle', async () => {
    prisma.generationTask.findUnique.mockResolvedValue({
      id: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      taskType: 'pipeline_run',
      status: 'succeeded',
      progressStage: 'completed',
      failedStage: null,
      game: {
        id: 'game-1',
        title: 'Task Game',
        status: 'draft',
        description: 'build a runner game',
        createdAt: new Date('2026-03-25T01:00:00.000Z'),
        updatedAt: new Date('2026-03-25T01:10:00.000Z'),
        publishedAt: null,
        failedStage: null,
        failedReason: null,
        bundles: [
          {
            id: 'bundle-1',
            version: 1,
            htmlCode: '<html><body>runner</body></html>',
            cssCode: 'body { color: red; }',
            jsCode: 'console.log(\"runner\")',
            metadata: { qaPassed: true },
            generationMeta: { strategy: 'llm' },
            codeSizeBytes: 1234,
            createdAt: new Date('2026-03-25T01:08:00.000Z'),
          },
        ],
      },
      user: {
        id: 'user-1',
        username: 'tester',
        displayName: 'Tester',
      },
      events: [],
      llmCallLogs: [],
    });
    gameService.reconcileGenerationTask.mockResolvedValue({
      id: 'task-1',
      gameId: 'game-1',
      status: 'succeeded',
      progressStage: 'completed',
      failedStage: null,
    });

    const result = await service.getGenerationTask('task-1');

    expect(prisma.generationTask.findUnique).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'task-1' },
      include: expect.objectContaining({
        game: expect.objectContaining({
          select: expect.objectContaining({
            description: true,
            bundles: expect.objectContaining({
              take: 1,
              orderBy: { version: 'desc' },
            }),
          }),
        }),
      }),
    }));
    expect(result).toEqual(expect.objectContaining({
      id: 'task-1',
      inputPrompt: 'build a runner game',
      sourceBundle: expect.objectContaining({
        id: 'bundle-1',
        version: 1,
        htmlCode: '<html><body>runner</body></html>',
      }),
      previewUrl: 'https://gamevallies.com/games/game-1/preview?previewToken=admin',
      gameUrl: 'https://gamevallies.com/games/game-1/index.html?previewToken=admin',
      game: expect.objectContaining({
        id: 'game-1',
        description: 'build a runner game',
        bundles: expect.any(Array),
      }),
    }));
  });

  it('reconciles stale generation tasks before returning the admin list', async () => {
    prisma.generationTask.findMany.mockResolvedValue([
      {
        id: 'task-1',
        gameId: 'game-1',
        userId: 'user-1',
        taskType: 'pipeline_run',
        status: 'running',
        progressStage: null,
        failedStage: null,
        game: { id: 'game-1', title: 'Test Game', status: 'banned' },
        user: { id: 'user-1', username: 'tester', displayName: 'Tester' },
      },
    ]);
    prisma.generationTask.count.mockResolvedValue(1);
    gameService.reconcileGenerationTask.mockResolvedValue({
      id: 'task-1',
      status: 'canceled',
      progressStage: 'canceled',
      game: { id: 'game-1', title: 'Test Game', status: 'banned' },
    });

    const result = await service.listGenerationTasks(1, 20);

    expect(gameService.reconcileGenerationTask).toHaveBeenCalledTimes(1);
    expect(result.items[0]).toEqual(expect.objectContaining({
      id: 'task-1',
      status: 'canceled',
      progressStage: 'canceled',
    }));
  });

  it('delegates admin task termination to game service', async () => {
    gameService.terminateTask.mockResolvedValue({
      taskId: 'task-terminate',
      status: 'canceled',
    });

    const result = await service.terminateGenerationTask('task-terminate');

    expect(gameService.terminateTask).toHaveBeenCalledWith('task-terminate', {
      admin: true,
      reason: 'Task terminated by admin',
    });
    expect(result).toEqual({
      taskId: 'task-terminate',
      status: 'canceled',
    });
  });

  it('derives provider region fields from the selected region target', async () => {
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      vendor: 'volcengine',
      cloudRegionCode: 'cn-shanghai',
      executionRegion: 'cn_shanghai',
      deployEnabled: true,
      deployStatus: 'deployed',
      aiEngineUrl: 'https://ai-cn.example.com',
    });
    prisma.llmGatewayProvider.upsert.mockResolvedValue({
      id: 'provider-1',
      name: 'MiniMax Shanghai',
      providerType: 'openai_compatible',
      regionTargetId: 'target-1',
      cloudVendor: 'volcengine',
      cloudRegionCode: 'cn-shanghai',
      region: 'cn_shanghai',
      baseUrl: 'https://api.minimaxi.com/v1',
      apiKey: 'sk-test-1234',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
      requestTimeoutS: 600,
      connectTimeoutS: 15,
      enabled: true,
      priority: 100,
      description: null,
      extraConfig: null,
    });
    jest.spyOn(service, 'refreshLlmGateway').mockResolvedValue({ ok: true } as any);

    const result = await service.upsertLlmProvider(undefined, {
      name: 'MiniMax Shanghai',
      providerType: 'openai_compatible',
      regionTargetId: 'target-1',
      baseUrl: 'https://api.minimaxi.com/v1',
      apiKey: 'sk-test-1234',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
    });

    expect(prisma.llmGatewayProvider.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        create: expect.objectContaining({
          regionTargetId: 'target-1',
          cloudVendor: 'volcengine',
          cloudRegionCode: 'cn-shanghai',
          region: 'cn_shanghai',
        }),
      }),
    );
    expect(result.region).toBe('cn_shanghai');
    expect(result.apiKeyMasked).toBe('sk-t...1234');
  });

  it('allows explicit runtime endpoint updates through the standard region target edit flow', async () => {
    prisma.cloudProviderAccount.findUnique.mockResolvedValue({
      id: 'account-1',
      vendor: 'volcengine',
      enabled: true,
      defaultRegistry: 'registry.example.com',
      defaultRegistryNamespace: 'gamevallies',
    });
    prisma.cloudRegionCatalog.findUnique.mockResolvedValue({
      id: 'region-1',
      accountId: 'account-1',
      regionCode: 'cn-shanghai',
      enabled: true,
    });
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: null,
      deployStatus: 'pending',
      lastRevision: null,
      lastImageTag: null,
      lastReleaseStatus: null,
      lastDeployError: null,
      lastDeployedAt: null,
      serviceRegionEnv: 'cn_shanghai',
    });
    prisma.aiEngineRegionTarget.upsert.mockResolvedValue({ id: 'target-1' });

    await service.upsertAiEngineRegionTarget('target-1', {
      accountId: 'account-1',
      regionCatalogId: 'region-1',
      executionRegion: 'cn_shanghai',
      displayName: 'AI Engine Shanghai',
      functionName: 'gv-ai-engine-cn',
      aiEngineUrl: 'https://ai-cn.example.com',
      deployStatus: 'deployed',
      lastRevision: '12',
      lastImageTag: 'release-abc',
    });

    expect(prisma.aiEngineRegionTarget.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        update: expect.objectContaining({
          aiEngineUrl: 'https://ai-cn.example.com',
          deployStatus: 'deployed',
          lastRevision: '12',
          lastImageTag: 'release-abc',
        }),
      }),
    );
  });

  it('syncs deployed target runtime state through dedicated endpoint flow', async () => {
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: null,
      deployStatus: 'pending',
      lastRevision: null,
      lastImageTag: null,
      lastReleaseStatus: null,
      lastDeployError: null,
      lastDeployedAt: null,
    });
    prisma.aiEngineRegionTarget.update.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-cn.example.com',
      deployStatus: 'deployed',
      lastRevision: '12',
      lastImageTag: 'release-123',
      lastReleaseStatus: 'done',
      lastDeployError: null,
      lastDeployedAt: new Date('2026-03-22T08:00:00.000Z'),
      account: { id: 'account-1', vendor: 'volcengine', accountKey: 'volc-default', displayName: 'Volcengine' },
      regionCatalog: { id: 'region-1', regionCode: 'cn-shanghai', regionName: 'Shanghai', regionGroup: 'cn_mainland' },
    });

    await service.syncAiEngineRegionTargetDeployState({
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-cn.example.com',
      lastRevision: '12',
      lastImageTag: 'release-123',
      lastReleaseStatus: 'done',
    });

    expect(prisma.aiEngineRegionTarget.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: 'target-1' },
        data: expect.objectContaining({
          aiEngineUrl: 'https://ai-cn.example.com',
          deployStatus: 'deployed',
          lastRevision: '12',
          lastImageTag: 'release-123',
          lastReleaseStatus: 'done',
        }),
      }),
    );
  });

  it('rejects region targets whose cloud region does not match executionRegion', async () => {
    prisma.cloudProviderAccount.findUnique.mockResolvedValue({
      id: 'account-1',
      vendor: 'volcengine',
      enabled: true,
      defaultRegistry: 'registry.example.com',
      defaultRegistryNamespace: 'gamevallies',
    });
    prisma.cloudRegionCatalog.findUnique.mockResolvedValue({
      id: 'region-1',
      accountId: 'account-1',
      regionCode: 'cn-shanghai',
      enabled: true,
    });
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue(null);

    await expect(
      service.upsertAiEngineRegionTarget(undefined, {
        accountId: 'account-1',
        regionCatalogId: 'region-1',
        executionRegion: 'ap_southeast_johor',
        displayName: 'AI Engine Johor',
        functionName: 'gv-ai-engine-global',
      }),
    ).rejects.toThrow('regionCatalogId does not match executionRegion=ap_southeast_johor');

    expect(prisma.aiEngineRegionTarget.upsert).not.toHaveBeenCalled();
  });

  it('keeps deployed targets selectable for providers when a resolved env endpoint exists', async () => {
    prisma.aiEngineRegionTarget.findMany.mockResolvedValue([
      {
        id: 'target-1',
        displayName: 'AI Engine Shanghai',
        executionRegion: 'cn_shanghai',
        deployEnabled: true,
        deployStatus: 'deployed',
        aiEngineUrl: null,
        account: { id: 'account-1', accountKey: 'volc-default', displayName: 'Volcengine', vendor: 'volcengine' },
        regionCatalog: { id: 'region-1', regionCode: 'cn-shanghai', regionName: 'Shanghai', regionGroup: 'cn_mainland' },
      },
      {
        id: 'target-2',
        displayName: 'AI Engine Johor',
        executionRegion: 'ap_southeast_johor',
        deployEnabled: true,
        deployStatus: 'pending',
        aiEngineUrl: null,
        account: { id: 'account-1', accountKey: 'volc-default', displayName: 'Volcengine', vendor: 'volcengine' },
        regionCatalog: { id: 'region-2', regionCode: 'ap-southeast-johor', regionName: 'Johor', regionGroup: 'overseas' },
      },
    ]);

    const result = await service.listAiEngineRegionTargets({ providerSelectableOnly: true });

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual(expect.objectContaining({
      id: 'target-1',
      resolvedAiEngineUrl: 'https://ai-cn.test',
    }));
  });

  it('derives route region from the selected provider and stores a single primary binding', async () => {
    prisma.llmStepCatalog.findUnique.mockResolvedValue({
      id: 'step-1',
      stepKey: 'code_generate.hybrid',
      stepOrder: 40,
      displayName: 'Code Generate (Hybrid)',
      enabled: true,
    });
    prisma.llmGatewayProvider.findUnique.mockResolvedValue({
      id: 'provider-1',
      name: 'MiniMax Shanghai',
      region: 'cn_shanghai',
      regionTargetId: 'target-1',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
    });
    prisma.llmStepRoute.upsert.mockResolvedValue({
      id: 'route-1',
      stepKey: 'code_generate.hybrid',
      region: 'cn_shanghai',
      providerId: 'provider-1',
      fallbackProviderIds: [],
      modelOverride: null,
      fastModelOverride: null,
      requestTimeoutS: null,
      connectTimeoutS: null,
      enabled: true,
      provider: {
        id: 'provider-1',
        name: 'MiniMax Shanghai',
        region: 'cn_shanghai',
        regionTargetId: 'target-1',
        providerType: 'openai_compatible',
        model: 'MiniMax-M2.5',
        fastModel: 'MiniMax-M2.5-fast',
      },
    });
    jest.spyOn(service, 'refreshLlmGateway').mockResolvedValue({ ok: true } as any);

    const result = await service.upsertLlmRoute(undefined, {
      stepKey: 'code_generate.hybrid',
      executionRegion: 'cn_shanghai',
      providerId: 'provider-1',
      enabled: true,
    });

    expect(prisma.llmStepRoute.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        where: {
          llm_step_routes_step_key_region_key: {
            stepKey: 'code_generate.hybrid',
            region: 'cn_shanghai',
          },
        },
        create: expect.objectContaining({
          region: 'cn_shanghai',
          fallbackProviderIds: [],
          modelOverride: null,
          fastModelOverride: null,
        }),
        update: expect.objectContaining({
          region: 'cn_shanghai',
          fallbackProviderIds: [],
        }),
      }),
    );
    expect(result.stepMeta.stepKey).toBe('code_generate.hybrid');
    expect(result.region).toBe('cn_shanghai');
  });

  it('returns a single llm route by id for legacy admin callers', async () => {
    prisma.llmStepRoute.findUnique.mockResolvedValue({
      id: 'route-1',
      stepKey: 'intent_parse',
      region: 'cn_shanghai',
      providerId: 'provider-1',
      enabled: true,
      updatedAt: new Date('2026-03-22T10:00:00.000Z'),
      provider: {
        id: 'provider-1',
        name: 'Deepseek-cn-Shanghai',
        region: 'cn_shanghai',
        regionTargetId: 'target-1',
        providerType: 'openai_compatible',
        model: 'deepseek-chat',
        fastModel: 'deepseek-chat',
      },
    });
    prisma.llmStepCatalog.findUnique.mockResolvedValue({
      id: 'step-1',
      stepKey: 'intent_parse',
      stepOrder: 30,
      stageLabel: 'Stage 02',
      displayName: 'Intent Parse',
      description: 'Parse the description into a GameSpec',
    });

    const result = await service.getLlmRoute('route-1');

    expect(prisma.llmStepRoute.findUnique).toHaveBeenCalledWith({
      where: { id: 'route-1' },
      include: {
        provider: {
          select: {
            id: true,
            name: true,
            region: true,
            regionTargetId: true,
            providerType: true,
            model: true,
            fastModel: true,
          },
        },
      },
    });
    expect(result).toEqual(expect.objectContaining({
      id: 'route-1',
      stepKey: 'intent_parse',
      executionRegion: 'cn_shanghai',
      providerId: 'provider-1',
      providerDisplayName: 'Deepseek-cn-Shanghai',
      modelDefault: 'deepseek-chat',
    }));
  });

  it('rejects route bindings when executionRegion does not match the selected provider region', async () => {
    prisma.llmStepCatalog.findUnique.mockResolvedValue({
      id: 'step-1',
      stepKey: 'qa_fix',
      stepOrder: 60,
      displayName: 'Code Generate (Hybrid)',
      enabled: true,
    });
    prisma.llmGatewayProvider.findUnique.mockResolvedValue({
      id: 'provider-1',
      name: 'MiniMax Shanghai',
      region: 'cn_shanghai',
      regionTargetId: 'target-1',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
    });

    await expect(
      service.upsertLlmRoute(undefined, {
        stepKey: 'qa_fix',
        providerId: 'provider-1',
        executionRegion: 'ap_southeast_johor',
      }),
    ).rejects.toThrow('executionRegion must match the selected provider region');

    expect(prisma.llmStepRoute.upsert).not.toHaveBeenCalled();
  });
});
