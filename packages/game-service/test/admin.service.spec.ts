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
    process.env.ADMIN_TOKEN = process.env.ADMIN_TOKEN || 'admin123';
    prisma = {
      $transaction: jest.fn(),
      game: {
        findMany: jest.fn(),
        findUnique: jest.fn(),
        update: jest.fn(),
        deleteMany: jest.fn(),
        count: jest.fn(),
        aggregate: jest.fn(),
        groupBy: jest.fn(),
        create: jest.fn(),
      },
      gameBundle: {
        create: jest.fn(),
        findMany: jest.fn(),
        findFirst: jest.fn(),
        update: jest.fn(),
        deleteMany: jest.fn(),
      },
      generationArtifact: {
        create: jest.fn(),
      },
      user: {
        findUnique: jest.fn(),
        findFirst: jest.fn(),
        create: jest.fn(),
        update: jest.fn(),
        count: jest.fn(),
      },
      subscriptionPlan: {
        count: jest.fn(),
        findMany: jest.fn(),
        upsert: jest.fn(),
        findUnique: jest.fn(),
        update: jest.fn(),
        delete: jest.fn(),
        updateMany: jest.fn(),
      },
      userSubscription: {
        count: jest.fn(),
        groupBy: jest.fn(),
      },
      subscriptionOrder: {
        aggregate: jest.fn(),
        groupBy: jest.fn(),
        count: jest.fn(),
      },
      cloudProviderAccount: {
        findUnique: jest.fn(),
      },
      cloudRegionCatalog: {
        findUnique: jest.fn(),
      },
      aiEngineRegionTarget: {
        findUnique: jest.fn(),
        findFirst: jest.fn(),
        findMany: jest.fn(),
        upsert: jest.fn(),
        update: jest.fn(),
      },
      generationTask: {
        findMany: jest.fn(),
        count: jest.fn(),
        findUnique: jest.fn(),
        update: jest.fn(),
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
        delete: jest.fn(),
      },
      llmGatewayTestRecord: {
        findMany: jest.fn(),
      },
      llmStepRoute: {
        findMany: jest.fn(),
        findUnique: jest.fn(),
        upsert: jest.fn(),
        delete: jest.fn(),
      },
    };
    prisma.$transaction.mockImplementation(async (callback: (tx: any) => any) => callback(prisma));
    configService = {
      get: jest.fn((key: string, defaultValue?: string) => {
        const values: Record<string, string> = {
          AI_ENGINE_URL: 'http://ai-engine.test',
          AI_ENGINE_URL_CN_SHANGHAI: 'https://ai-cn.test',
          AI_ENGINE_URL_AP_SOUTHEAST_JOHOR: 'https://ai-jh.test',
          PUBLIC_API_BASE_URL: 'https://gamevallies.com',
          APP_URL: 'https://gamevallies.com',
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

  it('returns dashboard subscription overview with a date range filter', async () => {
    prisma.game.count.mockResolvedValue(12);
    prisma.user.count.mockResolvedValue(5);
    prisma.game.aggregate
      .mockResolvedValueOnce({ _sum: { playCount: 1234, likeCount: 88, forkCount: 12 } })
      .mockResolvedValueOnce({ _avg: { qualityScore: 91.2, retryCount: 0.8 } });
    prisma.game.findMany.mockResolvedValue([
      { status: 'published', qualityScore: 96, failedStage: null, failedReason: null, retryCount: 0 },
      { status: 'failed', qualityScore: 68, failedStage: 'contract_qa', failedReason: 'syntax error', retryCount: 2 },
    ]);
    prisma.game.groupBy.mockResolvedValue([
      { status: 'published', _count: { id: 9 } },
      { status: 'failed', _count: { id: 3 } },
    ]);
    prisma.subscriptionPlan.count
      .mockResolvedValueOnce(4)
      .mockResolvedValueOnce(3);
    prisma.userSubscription.count.mockResolvedValue(18);
    prisma.subscriptionOrder.aggregate.mockResolvedValue({
      _sum: { amount: 128800 },
      _count: { id: 27 },
    });

    const result = await service.getStats('2026-03-01', '2026-03-31');

    expect(prisma.subscriptionOrder.aggregate).toHaveBeenCalledWith({
      where: {
        status: 'paid',
        paidAt: {
          gte: new Date('2026-03-01'),
          lte: new Date('2026-03-31'),
        },
      },
      _sum: { amount: true },
      _count: { id: true },
    });
    expect(result.subscriptionOverview).toEqual({
      totalPlans: 4,
      activePlans: 3,
      activeSubscribers: 18,
      paidOrderCount: 27,
      totalRevenueCents: 128800,
      totalRevenueYuan: 1288,
      range: {
        from: new Date('2026-03-01'),
        to: new Date('2026-03-31'),
      },
    });
  });

  it('lists subscription plans with usage summary', async () => {
    prisma.subscriptionPlan.findMany.mockResolvedValue([
      {
        id: 'plan_pro',
        name: '专业月卡',
        description: '每月 30 次额度',
        price: 1990,
        currency: 'CNY',
        period: 'monthly',
        quota: 30,
        features: ['每月30次创建', '无限AI迭代'],
        recommended: true,
        badge: '推荐',
        sortOrder: 20,
        active: true,
        createdAt: new Date('2026-03-01T00:00:00.000Z'),
        updatedAt: new Date('2026-03-02T00:00:00.000Z'),
      },
    ]);
    prisma.subscriptionOrder.groupBy.mockResolvedValue([
      { planId: 'plan_pro', _count: { _all: 7 }, _sum: { amount: 13930 } },
    ]);
    prisma.userSubscription.groupBy.mockResolvedValue([
      { planId: 'plan_pro', _count: { _all: 5 } },
    ]);
    prisma.userSubscription.count.mockResolvedValue(5);
    prisma.subscriptionOrder.aggregate.mockResolvedValue({
      _sum: { amount: 13930 },
      _count: { id: 7 },
    });

    const result = await service.listSubscriptionPlans('2026-03-01', '2026-03-31');

    expect(result.summary).toEqual({
      totalPlans: 1,
      activePlans: 1,
      activeSubscribers: 5,
      paidOrderCount: 7,
      totalRevenueCents: 13930,
      totalRevenueYuan: 139.3,
      range: {
        from: new Date('2026-03-01'),
        to: new Date('2026-03-31'),
      },
    });
    expect(result.items[0]).toEqual(expect.objectContaining({
      id: 'plan_pro',
      name: '专业月卡',
      priceYuan: 19.9,
      quotaLabel: '30次/月',
      orderCount: 7,
      revenueYuan: 139.3,
      activeSubscribers: 5,
      recommended: true,
    }));
  });

  it('archives subscription plans with historical orders instead of deleting them', async () => {
    prisma.subscriptionPlan.findUnique.mockResolvedValue({
      id: 'plan_basic',
      name: '基础月卡',
      active: true,
      recommended: true,
    });
    prisma.subscriptionOrder.count.mockResolvedValue(2);
    prisma.userSubscription.count.mockResolvedValue(1);
    prisma.subscriptionPlan.update.mockResolvedValue({
      id: 'plan_basic',
      active: false,
      recommended: false,
    });

    const result = await service.deleteSubscriptionPlan('plan_basic');

    expect(prisma.subscriptionPlan.update).toHaveBeenCalledWith({
      where: { id: 'plan_basic' },
      data: {
        active: false,
        recommended: false,
      },
    });
    expect(result).toEqual({
      deleted: false,
      deactivated: true,
      reason: 'Plan has historical orders or subscriptions and was archived instead of deleted',
    });
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

  it('backfills historical covers by capturing a polished cover artifact and wiring it to the live bundle', async () => {
    prisma.game.findMany.mockResolvedValue([
      {
        id: 'game-cover-backfill',
        authorId: 'user-cover-backfill',
        title: 'Orbital Office',
        description: 'A funny office chaos game',
        status: 'draft',
        visibility: 'private',
        version: 1,
        gameType: 'funny',
        thumbnailUrl: null,
        bundles: [
          {
            id: 'bundle-cover-backfill',
            version: 1,
            htmlCode: '<!DOCTYPE html><html><body>orbital office</body></html>',
            metadata: {
              gameSpec: {
                tags: ['office', 'chaos'],
                visual_style: {
                  theme: 'neon_city',
                },
              },
              runtimeOrientation: 'landscape_first',
              runtimeProfile: 'casual_arcade',
            },
          },
        ],
      },
    ]);
    prisma.generationArtifact.create.mockResolvedValue({
      id: 'artifact-cover-backfill',
    });
    prisma.gameBundle.update.mockResolvedValue({});
    prisma.game.update.mockResolvedValue({});
    const captureSpy = jest
      .spyOn(service as any, 'postAiEngineAdminWithFailover')
      .mockResolvedValue({
        data: {
          captured: true,
          payload: 'ZmFrZS1jb3Zlcg==',
          content_type: 'image/jpeg',
          metadata: {
            coverStyle: 'posterized_overlay',
            coverVariant: 'neon_glass_poster_v2',
          },
        },
      } as any);
    const invalidateFeedCacheSpy = jest
      .spyOn(service as any, 'invalidateFeedCache')
      .mockResolvedValue(undefined);

    const result = await service.backfillGameCovers({
      limit: 10,
    });

    expect(prisma.game.findMany).toHaveBeenCalledWith(expect.objectContaining({
      where: expect.objectContaining({
        OR: [
          { thumbnailUrl: null },
          { thumbnailUrl: '' },
        ],
      }),
    }));
    expect(captureSpy).toHaveBeenCalledWith(
      undefined,
      '/api/v1/ai/covers/capture',
      expect.objectContaining({
        game_id: 'game-cover-backfill',
        user_id: 'user-cover-backfill',
        orientation: 'landscape_first',
        title: 'Orbital Office',
        game_type: 'funny',
        theme: 'neon_city',
        runtime_profile: 'casual_arcade',
      }),
      60000,
      'No reachable ai-engine endpoint found for cover backfill',
    );
    expect(prisma.generationArtifact.create).toHaveBeenCalledWith({
      data: expect.objectContaining({
        gameId: 'game-cover-backfill',
        userId: 'user-cover-backfill',
        artifactType: 'cover_image',
        contentType: 'image/jpeg',
        storageType: 'inline_text',
        payloadText: 'ZmFrZS1jb3Zlcg==',
        metadata: expect.objectContaining({
          encoding: 'base64',
          backfillSource: 'admin_cover_backfill',
          coverStyle: 'posterized_overlay',
          coverVariant: 'neon_glass_poster_v2',
        }),
      }),
    });
    expect(prisma.gameBundle.update).toHaveBeenCalledWith({
      where: { id: 'bundle-cover-backfill' },
      data: {
        metadata: expect.objectContaining({
          coverArtifactId: 'artifact-cover-backfill',
          coverUrl: 'https://gamevallies.com/api/v1/games/game-cover-backfill/cover?v=1',
        }),
      },
    });
    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-cover-backfill' },
      data: {
        thumbnailUrl: 'https://gamevallies.com/api/v1/games/game-cover-backfill/cover?v=1',
      },
    });
    expect(invalidateFeedCacheSpy).toHaveBeenCalledTimes(1);
    expect(result).toEqual(expect.objectContaining({
      dryRun: false,
      scanned: 1,
      eligible: 1,
      regenerated: 1,
      failed: 0,
    }));
    expect(result.items).toEqual([
      expect.objectContaining({
        id: 'game-cover-backfill',
        status: 'regenerated',
        overlayStyle: 'posterized_overlay',
      }),
    ]);

    captureSpy.mockRestore();
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
      gameType: 'casual',
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
      extraConfig: {
        vendorPreset: 'modelverse',
        contextWindow: 128000,
        maxTokens: 16384,
        modelCatalog: {
          mode: 'custom',
          apiUrl: 'https://api.modelverse.cn/v1/models',
          authMode: 'bearer_token',
          apiKey: 'catalog-secret-1234',
        },
      },
    });
    const refreshSpy = jest.spyOn(service, 'refreshLlmGateway').mockResolvedValue({ ok: true } as any);

    const result = await service.upsertLlmProvider(undefined, {
      name: 'MiniMax Shanghai',
      providerType: 'openai_compatible',
      regionTargetId: 'target-1',
      baseUrl: 'https://api.minimaxi.com/v1',
      apiKey: 'sk-test-1234',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
      vendorPreset: 'modelverse',
      contextWindow: 128000,
      maxTokens: 16384,
      catalogMode: 'custom',
      catalogApiUrl: 'https://api.modelverse.cn/v1/models',
      catalogAuthMode: 'bearer_token',
      catalogApiKey: 'catalog-secret-1234',
    });

    expect(prisma.llmGatewayProvider.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        create: expect.objectContaining({
          regionTargetId: 'target-1',
          cloudVendor: 'volcengine',
          cloudRegionCode: 'cn-shanghai',
          region: 'cn_shanghai',
          extraConfig: expect.objectContaining({
            vendorPreset: 'modelverse',
            contextWindow: 128000,
            maxTokens: 16384,
            modelCatalog: expect.objectContaining({
              mode: 'custom',
              apiUrl: 'https://api.modelverse.cn/v1/models',
              authMode: 'bearer_token',
              apiKey: 'catalog-secret-1234',
            }),
          }),
        }),
      }),
    );
    expect(result.region).toBe('cn_shanghai');
    expect(result.apiKeyMasked).toBe('sk-t...1234');
    expect(result.vendorPreset).toBe('modelverse');
    expect(result.contextWindow).toBe(128000);
    expect(result.maxTokens).toBe(16384);
    expect(result.catalogApiKeyMasked).toBe('cata...1234');
    expect(result.extraConfig).toBeUndefined();
    expect(refreshSpy).toHaveBeenCalledWith('target-1');
  });

  it('creates admin games for a selected author and normalizes game type to the curated catalog', async () => {
    prisma.game.create.mockResolvedValue({ id: 'game-create-1' });
    prisma.gameBundle.create.mockResolvedValue({ id: 'bundle-create-1' });
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-create-1',
      title: 'Action Demo',
      description: 'test',
      slug: 'action-demo',
      gameType: 'casual',
      tags: [],
      author: {
        id: 'user-owner',
        username: 'owner',
        displayName: 'Owner',
      },
      bundles: [],
    });

    await service.createGame({
      title: 'Action Demo',
      description: 'test',
      gameType: 'action',
      htmlCode: '<!DOCTYPE html><html><body>demo</body></html>',
      authorId: 'user-owner',
    });

    expect(prisma.game.create).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({
        author: { connect: { id: 'user-owner' } },
        gameType: 'casual',
      }),
    }));
    expect(prisma.user.findFirst).not.toHaveBeenCalled();
  });

  it('refreshes historical game types across games, bundles, and task summaries', async () => {
    const invalidateFeedCacheSpy = jest
      .spyOn(service as any, 'invalidateFeedCache')
      .mockResolvedValue(undefined);

    prisma.game.findMany
      .mockResolvedValueOnce([
        {
          id: 'game-legacy-1',
          title: 'Runner Legacy',
          description: 'a classic runner',
          tags: [],
          gameType: 'runner',
        },
      ])
      .mockResolvedValueOnce([]);
    prisma.gameBundle.findMany
      .mockResolvedValueOnce([
        {
          id: 'bundle-legacy-1',
          metadata: {
            gameType: 'action',
            gameSpec: { game_type: 'runner' },
          },
          game: {
            title: 'Runner Legacy',
            description: 'a classic runner',
            tags: [],
            gameType: 'runner',
          },
        },
      ])
      .mockResolvedValueOnce([]);
    prisma.generationTask.findMany
      .mockResolvedValueOnce([
        {
          id: 'task-legacy-1',
          resultSummary: {
            gameType: 'action',
          },
          metadata: {
            selectedGameType: 'other',
          },
          game: {
            title: 'Runner Legacy',
            description: 'a classic runner',
            tags: [],
            gameType: 'runner',
          },
        },
      ])
      .mockResolvedValueOnce([]);

    const result = await service.refreshGameTypes();

    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-legacy-1' },
      data: { gameType: 'casual' },
    });
    expect(prisma.gameBundle.update).toHaveBeenCalledWith({
      where: { id: 'bundle-legacy-1' },
      data: {
        metadata: {
          gameType: 'casual',
          gameSpec: { game_type: 'runner' },
        },
      },
    });
    expect(prisma.generationTask.update).toHaveBeenCalledWith({
      where: { id: 'task-legacy-1' },
      data: {
        resultSummary: {
          gameType: 'casual',
        },
        metadata: {
          selectedGameType: 'casual',
        },
      },
    });
    expect(result).toEqual(expect.objectContaining({
      dryRun: false,
      gamesUpdated: 1,
      bundlesUpdated: 1,
      tasksUpdated: 1,
    }));
    expect(invalidateFeedCacheSpy).toHaveBeenCalledTimes(1);

    invalidateFeedCacheSpy.mockRestore();
  });

  it('proxies provider catalog preview to the selected ai-engine region', async () => {
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-cn.example.com',
      deployEnabled: true,
      deployStatus: 'deployed',
    });
    const postSpy = jest.spyOn(axios, 'post').mockResolvedValue({
      data: {
        models: [{ id: 'MiniMax-M2.7' }],
        fetched_at: '2026-03-26T12:00:00.000Z',
        resolved_catalog_api_url: 'https://api.modelverse.cn/v1/models',
        vendor_preset: 'modelverse',
      },
    } as any);

    try {
      const result = await service.previewLlmProviderCatalog({
        regionTargetId: 'target-1',
        vendorPreset: 'modelverse',
        catalogApiUrl: 'https://api.modelverse.cn/v1/models',
      });

      expect(postSpy).toHaveBeenCalledWith(
        'https://ai-cn.test/api/v1/ai/llm-gateway/providers/catalog/preview',
        expect.objectContaining({
          provider_type: 'openai_compatible',
          vendor_preset: 'modelverse',
          catalog_api_url: 'https://api.modelverse.cn/v1/models',
        }),
        expect.objectContaining({
          headers: { 'x-admin-token': expect.any(String) },
          timeout: 30000,
        }),
      );
      expect(result.models).toEqual([{ id: 'MiniMax-M2.7' }]);
      expect(result.fetchedAt).toBe('2026-03-26T12:00:00.000Z');
      expect(result.resolvedCatalogApiUrl).toBe('https://api.modelverse.cn/v1/models');
      expect(result.vendorPreset).toBe('modelverse');
    } finally {
      postSpy.mockRestore();
    }
  });

  it('reuses stored provider secrets for catalog preview when editing an existing provider', async () => {
    prisma.llmGatewayProvider.findUnique.mockResolvedValue({
      providerType: 'openai_compatible',
      regionTargetId: 'target-1',
      baseUrl: 'https://api.modelverse.cn/v1',
      apiKey: 'provider-key-123',
      extraConfig: {
        vendorPreset: 'modelverse',
        modelCatalog: {
          mode: 'custom',
          apiUrl: 'https://api.modelverse.cn/v1/models',
          authMode: 'bearer_token',
          apiKey: 'catalog-key-456',
        },
      },
    });
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-cn.example.com',
      deployEnabled: true,
      deployStatus: 'deployed',
    });
    const postSpy = jest.spyOn(axios, 'post').mockResolvedValue({
      data: {
        models: [{ id: 'model-a' }],
        fetchedAt: '2026-03-26T12:00:00.000Z',
      },
    } as any);

    try {
      await service.previewLlmProviderCatalog({
        providerId: 'provider-1',
        catalogAuthMode: 'bearer_token',
      });

      expect(postSpy).toHaveBeenCalledWith(
        'https://ai-cn.test/api/v1/ai/llm-gateway/providers/catalog/preview',
        expect.objectContaining({
          provider_type: 'openai_compatible',
          vendor_preset: 'modelverse',
          base_url: 'https://api.modelverse.cn/v1',
          api_key: 'provider-key-123',
          catalog_api_url: 'https://api.modelverse.cn/v1/models',
          catalog_auth_mode: 'bearer_token',
          catalog_api_key: 'catalog-key-456',
        }),
        expect.any(Object),
      );
    } finally {
      postSpy.mockRestore();
    }
  });

  it('lists games with a resolved coverUrl from the latest bundle metadata', async () => {
    prisma.game.findMany.mockResolvedValue([
      {
        id: 'game-1',
        title: 'Cover Ready',
        description: 'desc',
        slug: 'cover-ready',
        gameType: 'casual',
        tags: [],
        status: 'draft',
        playCount: 0,
        likeCount: 0,
        qualityScore: 0,
        thumbnailUrl: null,
        version: 3,
        author: {
          id: 'user-1',
          username: 'tester',
          displayName: 'Tester',
        },
        bundles: [
          {
            id: 'bundle-1',
            version: 3,
            codeSizeBytes: 1024,
            createdAt: new Date('2026-03-29T08:00:00.000Z'),
            metadata: {
              coverArtifactId: 'artifact-1',
            },
          },
        ],
      },
    ]);
    prisma.game.count.mockResolvedValue(1);

    const result = await service.listGames(1, 20, '', 'all');

    expect(result.items[0]).toEqual(expect.objectContaining({
      id: 'game-1',
      coverUrl: 'https://gamevallies.com/api/v1/games/game-1/cover?v=3&previewToken=admin',
    }));
  });

  it('updates a game cover from an uploaded image and stores a cover artifact', async () => {
    prisma.game.findUnique
      .mockResolvedValueOnce({
        id: 'game-1',
        authorId: 'user-1',
        status: 'draft',
        version: 4,
        bundles: [
          {
            id: 'bundle-4',
            version: 4,
            metadata: {
              runtimeProfile: 'casual_arcade',
            },
          },
        ],
      })
      .mockResolvedValueOnce({
        id: 'game-1',
        title: 'Cover Ready',
        description: 'desc',
        slug: 'cover-ready',
        gameType: 'casual',
        tags: [],
        status: 'draft',
        playCount: 0,
        likeCount: 0,
        forkCount: 0,
        avgPlayTime: 0,
        qualityScore: 0,
        version: 4,
        thumbnailUrl: 'https://gamevallies.com/api/v1/games/game-1/cover?v=4',
        author: {
          id: 'user-1',
          username: 'cover_user',
          displayName: 'Cover User',
        },
        bundles: [
          {
            id: 'bundle-4',
            version: 4,
            metadata: {
              coverArtifactId: 'artifact-cover-upload',
              coverUrl: 'https://gamevallies.com/api/v1/games/game-1/cover?v=4',
            },
          },
        ],
      });
    prisma.generationArtifact.create.mockResolvedValue({
      id: 'artifact-cover-upload',
    });
    prisma.gameBundle.update.mockResolvedValue({});
    prisma.game.update.mockResolvedValue({});
    const invalidateFeedCacheSpy = jest
      .spyOn(service as any, 'invalidateFeedCache')
      .mockResolvedValue(undefined);

    const result = await service.updateGameCover('game-1', {
      imageDataUrl: 'data:image/png;base64,aGVsbG8=',
      fileName: 'cover.png',
    });

    expect(prisma.generationArtifact.create).toHaveBeenCalledWith({
      data: expect.objectContaining({
        gameId: 'game-1',
        userId: 'user-1',
        artifactType: 'cover_image',
        contentType: 'image/png',
        storageType: 'inline_text',
        payloadText: 'aGVsbG8=',
        sizeBytes: 5,
        metadata: expect.objectContaining({
          encoding: 'base64',
          manualCover: true,
          manualCoverSource: 'admin_upload',
          manualCoverFileName: 'cover.png',
        }),
      }),
    });
    expect(prisma.gameBundle.update).toHaveBeenCalledWith({
      where: { id: 'bundle-4' },
      data: {
        metadata: expect.objectContaining({
          coverArtifactId: 'artifact-cover-upload',
          coverUrl: 'https://gamevallies.com/api/v1/games/game-1/cover?v=4',
          manualCover: true,
          manualCoverSource: 'admin_upload',
        }),
      },
    });
    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-1' },
      data: {
        thumbnailUrl: 'https://gamevallies.com/api/v1/games/game-1/cover?v=4',
      },
    });
    expect(invalidateFeedCacheSpy).toHaveBeenCalledTimes(1);
    expect(result).toEqual(expect.objectContaining({
      id: 'game-1',
      coverUrl: 'https://gamevallies.com/api/v1/games/game-1/cover?v=4&previewToken=admin',
    }));
  });

  it('updates a game cover from an external image url without creating an artifact', async () => {
    prisma.game.findUnique
      .mockResolvedValueOnce({
        id: 'game-2',
        authorId: 'user-2',
        status: 'draft',
        version: 2,
        bundles: [
          {
            id: 'bundle-2',
            version: 2,
            metadata: {
              coverArtifactId: 'artifact-old',
              coverTaskId: 'task-old',
            },
          },
        ],
      })
      .mockResolvedValueOnce({
        id: 'game-2',
        title: 'External Cover',
        description: 'desc',
        slug: 'external-cover',
        gameType: 'casual',
        tags: [],
        status: 'draft',
        playCount: 0,
        likeCount: 0,
        forkCount: 0,
        avgPlayTime: 0,
        qualityScore: 0,
        version: 2,
        thumbnailUrl: 'https://cdn.example.com/covers/game-2.jpg',
        author: {
          id: 'user-2',
          username: 'external_user',
          displayName: 'External User',
        },
        bundles: [
          {
            id: 'bundle-2',
            version: 2,
            metadata: {
              coverUrl: 'https://cdn.example.com/covers/game-2.jpg',
              manualCover: true,
              manualCoverSource: 'admin_url',
            },
          },
        ],
      });
    prisma.gameBundle.update.mockResolvedValue({});
    prisma.game.update.mockResolvedValue({});
    const invalidateFeedCacheSpy = jest
      .spyOn(service as any, 'invalidateFeedCache')
      .mockResolvedValue(undefined);

    const result = await service.updateGameCover('game-2', {
      imageUrl: 'https://cdn.example.com/covers/game-2.jpg',
    });

    expect(prisma.generationArtifact.create).not.toHaveBeenCalled();
    expect(prisma.gameBundle.update).toHaveBeenCalledWith({
      where: { id: 'bundle-2' },
      data: {
        metadata: expect.objectContaining({
          coverUrl: 'https://cdn.example.com/covers/game-2.jpg',
          manualCover: true,
          manualCoverSource: 'admin_url',
        }),
      },
    });
    const metadata = prisma.gameBundle.update.mock.calls[0][0].data.metadata;
    expect(metadata.coverArtifactId).toBeUndefined();
    expect(metadata.coverTaskId).toBeUndefined();
    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-2' },
      data: {
        thumbnailUrl: 'https://cdn.example.com/covers/game-2.jpg',
      },
    });
    expect(invalidateFeedCacheSpy).toHaveBeenCalledTimes(1);
    expect(result).toEqual(expect.objectContaining({
      id: 'game-2',
      coverUrl: 'https://cdn.example.com/covers/game-2.jpg',
    }));
  });

  it('batch updates game statuses and reports missing ids', async () => {
    prisma.game.findMany.mockResolvedValue([
      { id: 'game-1', publishedAt: null },
      { id: 'game-2', publishedAt: new Date('2026-03-20T00:00:00.000Z') },
    ]);
    const invalidateFeedCacheSpy = jest
      .spyOn(service as any, 'invalidateFeedCache')
      .mockResolvedValue(undefined);
    prisma.game.update.mockResolvedValue({});

    const result = await service.batchUpdateGameStatus(['game-1', 'game-2', 'game-missing'], 'published');

    expect(prisma.game.findMany).toHaveBeenCalledWith({
      where: { id: { in: ['game-1', 'game-2', 'game-missing'] } },
      select: { id: true, publishedAt: true },
    });
    expect(prisma.game.update).toHaveBeenCalledTimes(2);
    expect(result).toEqual({
      requested: 3,
      updated: 2,
      status: 'published',
      updatedIds: ['game-1', 'game-2'],
      missingIds: ['game-missing'],
    });
    expect(invalidateFeedCacheSpy).toHaveBeenCalledTimes(1);
  });

  it('batch deletes games after terminating active tasks', async () => {
    prisma.game.findMany.mockResolvedValue([
      { id: 'game-1' },
      { id: 'game-2' },
    ]);
    prisma.gameBundle.deleteMany.mockResolvedValue({ count: 2 });
    prisma.game.deleteMany.mockResolvedValue({ count: 2 });
    const invalidateFeedCacheSpy = jest
      .spyOn(service as any, 'invalidateFeedCache')
      .mockResolvedValue(undefined);

    const result = await service.batchDeleteGames(['game-1', 'game-2', 'game-3']);

    expect(gameService.terminateActiveTasksForGame).toHaveBeenCalledTimes(2);
    expect(prisma.gameBundle.deleteMany).toHaveBeenCalledWith({
      where: { gameId: { in: ['game-1', 'game-2'] } },
    });
    expect(prisma.game.deleteMany).toHaveBeenCalledWith({
      where: { id: { in: ['game-1', 'game-2'] } },
    });
    expect(result).toEqual({
      requested: 3,
      deleted: 2,
      deletedIds: ['game-1', 'game-2'],
      missingIds: ['game-3'],
    });
    expect(invalidateFeedCacheSpy).toHaveBeenCalledTimes(1);
  });

  it('proxies provider chat tests and returns recent test records', async () => {
    prisma.llmGatewayProvider.findUnique
      .mockResolvedValueOnce({ regionTargetId: 'target-1' })
      .mockResolvedValueOnce({ id: 'provider-1' });
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-cn.example.com',
      deployEnabled: true,
      deployStatus: 'deployed',
    });
    prisma.llmGatewayTestRecord.findMany.mockResolvedValue([
      {
        id: 'record-1',
        success: true,
        region: 'cn_shanghai',
        resolvedEndpoint: 'https://api.modelverse.cn/v1/chat/completions',
        model: 'gpt-5.4-mini',
        latencyMs: 812,
        httpStatus: 200,
        errorMessage: null,
        testedAt: new Date('2026-03-26T12:01:00.000Z'),
      },
    ]);
    const postSpy = jest.spyOn(axios, 'post').mockResolvedValue({
      data: {
        reply: '你好，我准备好了。',
        latency_ms: 812,
        model: 'gpt-5.4-mini',
        success: true,
        tested_at: '2026-03-26T12:01:00.000Z',
      },
    } as any);

    try {
      const chat = await service.testLlmProviderChat('provider-1', {
        messages: [{ role: 'user', content: '你好' }],
      });
      const records = await service.listLlmProviderTestRecords('provider-1', 5);

      expect(postSpy).toHaveBeenCalledWith(
        'https://ai-cn.test/api/v1/ai/llm-gateway/providers/provider-1/test-chat',
        { messages: [{ role: 'user', content: '你好' }] },
        expect.objectContaining({
          headers: { 'x-admin-token': expect.any(String) },
          timeout: 60000,
        }),
      );
      expect(chat.reply).toBe('你好，我准备好了。');
      expect(chat.latencyMs).toBe(812);
      expect(chat.testedAt).toBe('2026-03-26T12:01:00.000Z');
      expect(prisma.llmGatewayTestRecord.findMany).toHaveBeenCalledWith({
        where: { providerId: 'provider-1' },
        orderBy: { testedAt: 'desc' },
        take: 5,
        select: {
          id: true,
          success: true,
          region: true,
          resolvedEndpoint: true,
          model: true,
          latencyMs: true,
          httpStatus: true,
          errorMessage: true,
          testedAt: true,
        },
      });
      expect(records).toHaveLength(1);
    } finally {
      postSpy.mockRestore();
    }
  });

  it('falls back to the next ai-engine admin base url when provider test hits a stale endpoint first', async () => {
    prisma.llmGatewayProvider.findUnique.mockResolvedValue({
      regionTargetId: 'target-1',
    });
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-stale.example.com',
      deployEnabled: true,
      deployStatus: 'deployed',
    });
    const postSpy = jest.spyOn(axios, 'post')
      .mockRejectedValueOnce(Object.assign(new Error('connect ECONNREFUSED'), {
        isAxiosError: true,
        response: {
          data: { message: 'configured gateway unavailable' },
        },
      }))
      .mockResolvedValueOnce({
        data: {
          success: true,
          latencyMs: 812,
        },
      } as any);

    try {
      const result = await service.testLlmProvider('provider-1');

      expect(postSpy).toHaveBeenNthCalledWith(
        1,
        'https://ai-cn.test/api/v1/ai/llm-gateway/providers/provider-1/test',
        {},
        expect.any(Object),
      );
      expect(postSpy).toHaveBeenNthCalledWith(
        2,
        'https://ai-stale.example.com/api/v1/ai/llm-gateway/providers/provider-1/test',
        {},
        expect.any(Object),
      );
      expect(result).toEqual({
        success: true,
        latencyMs: 812,
      });
    } finally {
      postSpy.mockRestore();
    }
  });

  it('surfaces upstream ai-engine errors when every admin endpoint fails', async () => {
    prisma.llmGatewayProvider.findUnique.mockResolvedValue({
      regionTargetId: 'target-1',
    });
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-stale.example.com',
      deployEnabled: true,
      deployStatus: 'deployed',
    });
    const postSpy = jest.spyOn(axios, 'post')
      .mockRejectedValueOnce(Object.assign(new Error('first failure'), {
        isAxiosError: true,
        response: {
          data: { detail: 'configured gateway unavailable' },
        },
      }))
      .mockRejectedValueOnce(Object.assign(new Error('second failure'), {
        isAxiosError: true,
        response: {
          data: { message: 'region runtime unreachable' },
        },
      }));

    try {
      await expect(service.testLlmProvider('provider-1')).rejects.toThrow(
        'All ai-engine admin endpoints failed. https://ai-cn.test: configured gateway unavailable | https://ai-stale.example.com: region runtime unreachable',
      );
    } finally {
      postSpy.mockRestore();
    }
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
    prisma.aiEngineRegionTarget.findFirst.mockResolvedValue({
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
    prisma.aiEngineRegionTarget.findFirst.mockResolvedValue({
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

    expect(prisma.aiEngineRegionTarget.findFirst).toHaveBeenCalledWith({
      where: { executionRegion: 'cn_shanghai' },
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
    const refreshSpy = jest.spyOn(service, 'refreshLlmGateway').mockResolvedValue({ ok: true } as any);

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
    expect(refreshSpy).toHaveBeenCalledWith('target-1');
  });

  it('refreshes only the selected llm gateway region and tolerates a stale endpoint when another one succeeds', async () => {
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-stale.example.com',
      deployEnabled: true,
      deployStatus: 'deployed',
    });
    const postSpy = jest.spyOn(axios, 'post')
      .mockResolvedValueOnce({
        data: { refreshed: 3 },
      } as any)
      .mockRejectedValueOnce(Object.assign(new Error('connect ECONNREFUSED'), {
        isAxiosError: true,
        response: {
          data: { message: 'region runtime unreachable' },
        },
      }));

    try {
      const result = await service.refreshLlmGateway('target-1');

      expect(postSpy).toHaveBeenNthCalledWith(
        1,
        'https://ai-cn.test/api/v1/ai/llm-gateway/refresh',
        {},
        expect.objectContaining({
          headers: { 'x-admin-token': expect.any(String) },
          timeout: 10000,
        }),
      );
      expect(postSpy).toHaveBeenNthCalledWith(
        2,
        'https://ai-stale.example.com/api/v1/ai/llm-gateway/refresh',
        {},
        expect.any(Object),
      );
      expect(result).toEqual({
        refreshed: 1,
        failed: 1,
        partialFailure: true,
        results: [
          {
            baseUrl: 'https://ai-cn.test',
            data: { refreshed: 3 },
          },
        ],
        failures: [
          {
            baseUrl: 'https://ai-stale.example.com',
            message: 'region runtime unreachable',
          },
        ],
      });
    } finally {
      postSpy.mockRestore();
    }
  });

  it('surfaces a clear refresh error when every llm gateway admin endpoint fails', async () => {
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-stale.example.com',
      deployEnabled: true,
      deployStatus: 'deployed',
    });
    const postSpy = jest.spyOn(axios, 'post')
      .mockRejectedValueOnce(Object.assign(new Error('configured failure'), {
        isAxiosError: true,
        response: {
          data: { detail: 'configured gateway unavailable' },
        },
      }))
      .mockRejectedValueOnce(Object.assign(new Error('stale failure'), {
        isAxiosError: true,
        response: {
          data: { message: 'region runtime unreachable' },
        },
      }));

    try {
      await expect(service.refreshLlmGateway('target-1')).rejects.toThrow(
        'All ai-engine admin endpoints failed during llm gateway refresh. https://ai-cn.test: configured gateway unavailable | https://ai-stale.example.com: region runtime unreachable',
      );
    } finally {
      postSpy.mockRestore();
    }
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
