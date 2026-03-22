import axios from 'axios';
import { ConfigService } from '@nestjs/config';
import { GameAccessGrantSource } from '@prisma/client';
import { GameService } from '../src/game/game.service';

jest.mock('axios');

const mockedAxios = axios as jest.Mocked<typeof axios>;

describe('GameService', () => {
  let service: GameService;
  let prisma: any;
  let bundleService: any;
  let statsService: any;
  let configService: ConfigService;
  let wsGateway: any;
  let generationTaskService: any;

  beforeEach(() => {
    prisma = {
      $transaction: jest.fn(),
      game: {
        create: jest.fn(),
        findUnique: jest.fn(),
        update: jest.fn(),
        findMany: jest.fn(),
        count: jest.fn(),
      },
      userQuota: {
        upsert: jest.fn(),
        update: jest.fn(),
        updateMany: jest.fn(),
      },
      userSubscription: {
        updateMany: jest.fn(),
        findFirst: jest.fn(),
        update: jest.fn(),
      },
      systemConfig: {
        findUnique: jest.fn(),
      },
      aiEngineRegionTarget: {
        findFirst: jest.fn(),
      },
    };
    prisma.$transaction.mockImplementation(async (callback: (tx: any) => any) => callback(prisma));
    bundleService = {
      getBundle: jest.fn(),
      saveBundle: jest.fn(),
      getLatestBundle: jest.fn(),
      getBundleHistory: jest.fn(),
    };
    statsService = {
      incrementPlayCount: jest.fn(),
    };
    wsGateway = {
      emitGenerationProgress: jest.fn(),
      emitGenerationComplete: jest.fn(),
      emitGenerationError: jest.fn(),
      emitNotification: jest.fn(),
    };
    generationTaskService = {
      createTask: jest.fn(async ({ gameId, taskType, timeoutS, version }: any) => ({
        id: `${gameId}:${taskType}`,
        taskType,
        status: 'queued',
        timeoutS,
        wsChannel: `game:${gameId}`,
        gameId,
        version: version ?? 1,
        createdAt: new Date(),
        updatedAt: new Date(),
      })),
      markRunning: jest.fn(),
      recordProgress: jest.fn(),
      markSucceeded: jest.fn(),
      markFailed: jest.fn(),
      getLatestTaskForGame: jest.fn(),
      getTaskForUser: jest.fn(),
      listTaskEvents: jest.fn(),
      requestCancel: jest.fn(),
      toTaskSummary: jest.fn((task: any) => ({
        taskId: task.id,
        taskType: task.taskType,
        status: task.status,
        timeoutS: task.timeoutS,
        wsChannel: task.wsChannel,
        pollUrl: `/api/v1/games/tasks/${task.id}`,
      })),
    };
    configService = {
      get: jest.fn((key: string, defaultValue?: string) => {
        const values: Record<string, string> = {
          AI_ENGINE_URL: 'http://ai-engine.test',
          PUBLIC_API_BASE_URL: 'https://www.gamevallies.com',
          APP_URL: 'https://www.gamevallies.com',
        };
        return values[key] ?? defaultValue;
      }),
    } as unknown as ConfigService;

    mockedAxios.post.mockReset();
    jest.useRealTimers();

    service = new GameService(
      prisma,
      bundleService,
      statsService,
      configService,
      wsGateway,
      generationTaskService,
    );
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('does not retry ai-engine 504 responses and persists structured failure context', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-504',
      authorId: 'user-504',
      accessGrantSource: GameAccessGrantSource.subscription_quota,
      accessGrantSubscriptionId: 'sub-504',
    });
    prisma.userSubscription.updateMany.mockResolvedValue({ count: 1 });

    mockedAxios.post.mockRejectedValue({
      message: 'Request failed with status code 504',
      response: {
        status: 504,
        data: {
          detail: {
            message: 'Generated code failed QA',
            failed_stage: 'qa_checking',
            retry_count: 3,
          },
        },
      },
    });

    await (service as any).runPipeline('game-504', 'user-504', 'make a runner');

    expect(mockedAxios.post).toHaveBeenCalledTimes(1);
    expect(prisma.userSubscription.updateMany).toHaveBeenCalledWith({
      where: {
        id: 'sub-504',
        usedThisPeriod: { gt: 0 },
      },
      data: {
        usedThisPeriod: { decrement: 1 },
      },
    });
    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-504' },
      data: expect.objectContaining({
        status: 'failed',
        failedStage: 'qa_checking',
        failedReason: 'Generated code failed QA',
        retryCount: 3,
        lastErrorAt: expect.any(Date),
        accessGrantSource: GameAccessGrantSource.none,
        accessGrantSubscriptionId: null,
      }),
    });
    expect(wsGateway.emitNotification).toHaveBeenCalledWith(
      'user-504',
      expect.objectContaining({
        type: 'error',
        gameId: 'game-504',
      }),
    );
    expect(wsGateway.emitGenerationError).toHaveBeenCalledWith(
      'user-504',
      'game-504',
      'Generated code failed QA',
      expect.objectContaining({
        stage: 'qa_checking',
        retryCount: 3,
      }),
    );
  });

  it('retries transient network failures and publishes after a later success', async () => {
    jest.useFakeTimers();

    mockedAxios.post
      .mockRejectedValueOnce({
        code: 'ECONNRESET',
        message: 'socket hang up',
      })
      .mockResolvedValueOnce({
        data: {
          html_code: '<!DOCTYPE html><html><head><title>测试游戏</title></head><body></body></html>',
          strategy: 'llm',
          qa_passed: true,
          qa_retries: 1,
          game_spec: { game_type: 'runner' },
          generation_time_ms: 1234,
          code_size_bytes: 88,
          quality_score: 91,
          quality_breakdown: { qa_penalty: 0 },
        },
      });

    prisma.game.findUnique.mockResolvedValue({
      title: 'Game abcdef12',
    });
    prisma.game.update.mockResolvedValue({});
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue(undefined);

    const runPromise = (service as any).runPipeline(
      'game-network',
      'user-network',
      'make a runner',
    );

    await Promise.resolve();
    await jest.runOnlyPendingTimersAsync();
    await runPromise;

    expect(mockedAxios.post).toHaveBeenCalledTimes(2);
    expect(wsGateway.emitGenerationProgress).toHaveBeenCalledWith(
      'user-network',
      'game-network',
      'AI 生成服务请求失败，重试中（1/2）',
      60,
      expect.objectContaining({
        stage: 'code_generating',
        retry: 1,
        maxRetries: 2,
        attempt: 2,
        maxAttempts: 3,
      }),
    );
    expect(bundleService.saveBundle).toHaveBeenCalled();
    expect(wsGateway.emitGenerationComplete).toHaveBeenCalledWith(
      'user-network',
      'game-network',
      'https://www.gamevallies.com/games/game-network/preview',
    );
  });

  it('fails closed when ai-engine returns empty html output', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-empty',
      authorId: 'user-empty',
      accessGrantSource: GameAccessGrantSource.none,
      accessGrantSubscriptionId: null,
    });
    prisma.game.update.mockResolvedValue({});

    mockedAxios.post.mockResolvedValue({
      data: {
        strategy: 'llm',
        qa_passed: true,
        qa_retries: 0,
        game_spec: { game_type: 'runner' },
        generation_time_ms: 123,
        code_size_bytes: 0,
      },
    });

    await (service as any).runPipeline('game-empty', 'user-empty', 'make a runner');

    expect(bundleService.saveBundle).not.toHaveBeenCalled();
    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-empty' },
      data: expect.objectContaining({
        status: 'failed',
        failedReason: 'AI pipeline returned empty HTML output',
      }),
    });
    expect(wsGateway.emitGenerationError).toHaveBeenCalledWith(
      'user-empty',
      'game-empty',
      'AI pipeline returned empty HTML output',
      expect.objectContaining({
        stage: 'pipeline_run',
      }),
    );
  });

  it('retries iteration publish persistence before surfacing success', async () => {
    jest.useFakeTimers();

    mockedAxios.post.mockResolvedValue({
      data: {
        html_code: '<!DOCTYPE html><html><body>updated</body></html>',
        iteration_type: 'element_change',
        generation_time_ms: 456,
        qa_retries: 1,
        iteration_retries: 2,
      },
    });

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-iter',
      authorId: 'user-iter',
      version: 1,
      status: 'draft',
    });
    prisma.game.update.mockResolvedValue({});
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<html>old</html>',
    });
    bundleService.getBundleHistory.mockResolvedValue([]);
    bundleService.getBundle
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
        gameId: 'game-iter',
        version: 2,
      });
    bundleService.saveBundle
      .mockRejectedValueOnce(new Error('mongo temporarily unavailable'))
      .mockResolvedValueOnce(undefined);

    const runPromise = (service as any).runIteration(
      'game-iter',
      'user-iter',
      'make it faster',
      2,
      [],
      '<html>old</html>',
    );

    await Promise.resolve();
    await jest.runOnlyPendingTimersAsync();
    await runPromise;

    expect(bundleService.saveBundle).toHaveBeenCalledTimes(2);
    expect(wsGateway.emitGenerationProgress).toHaveBeenCalledWith(
      'user-iter',
      'game-iter',
      '发布生成结果失败，重试中（1/2）',
      90,
      expect.objectContaining({
        stage: 'publishing',
        retry: 1,
        maxRetries: 2,
      }),
    );
    expect(wsGateway.emitGenerationComplete).toHaveBeenCalledWith(
      'user-iter',
      'game-iter',
      'https://www.gamevallies.com/games/game-iter/preview',
    );
  });

  it('does not refund the original creation quota when an iteration fails later', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-iter-failed',
      authorId: 'user-iter',
      accessGrantSource: GameAccessGrantSource.free_quota,
      accessGrantSubscriptionId: null,
    });
    prisma.game.update.mockResolvedValue({});

    mockedAxios.post.mockRejectedValue({
      response: {
        data: {
          detail: {
            message: 'Iteration QA failed',
            failed_stage: 'qa_checking',
            retry_count: 1,
          },
        },
      },
      message: 'Request failed with status code 400',
    });

    await (service as any).runIteration(
      'game-iter-failed',
      'user-iter',
      'make it harder',
      2,
      [],
      '<html>old</html>',
    );

    expect(prisma.userQuota.updateMany).not.toHaveBeenCalled();
    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-iter-failed' },
      data: expect.objectContaining({
        failedStage: 'qa_checking',
        failedReason: 'Iteration QA failed',
        retryCount: 1,
      }),
    });
  });

  it('consumes free quota during creation and returns playable state', async () => {
    const runPipelineSpy = jest.spyOn(service as any, 'runPipeline').mockResolvedValue(undefined);

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-free',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.userQuota.update.mockResolvedValue({
      userId: 'user-free',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.game.create.mockResolvedValue({ id: 'game-free' });

    const result = await service.create('user-free', {
      description: 'make a puzzle game',
    } as any);

    expect(prisma.game.create).toHaveBeenCalledWith({
      data: expect.objectContaining({
        authorId: 'user-free',
        canPlay: true,
        requireSubscription: false,
      }),
    });
    expect(result).toEqual(expect.objectContaining({
      canPlay: true,
      quotaRemaining: 4,
      requireSubscription: false,
      generationTask: expect.objectContaining({
        taskType: 'pipeline_run',
        status: 'queued',
        timeoutS: 600,
        pollUrl: expect.stringContaining('/api/v1/games/'),
      }),
    }));

    await new Promise((resolve) => setImmediate(resolve));
    runPipelineSpy.mockRestore();
  });

  it('still creates a locked game when all quota is exhausted', async () => {
    const runPipelineSpy = jest.spyOn(service as any, 'runPipeline').mockResolvedValue(undefined);

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-locked',
      totalFreeQuota: 5,
      usedFreeQuota: 5,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.game.create.mockResolvedValue({ id: 'game-locked' });

    const result = await service.create('user-locked', {
      description: 'make a runner',
    } as any);

    expect(prisma.game.create).toHaveBeenCalledWith({
      data: expect.objectContaining({
        authorId: 'user-locked',
        canPlay: false,
        requireSubscription: true,
      }),
    });
    expect(result).toEqual(expect.objectContaining({
      canPlay: false,
      quotaRemaining: 0,
      requireSubscription: true,
      generationTask: expect.objectContaining({
        taskType: 'pipeline_run',
        timeoutS: 600,
      }),
    }));

    await new Promise((resolve) => setImmediate(resolve));
    runPipelineSpy.mockRestore();
  });

  it('unlocks a locked game by consuming one subscription quota', async () => {
    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-lock',
      authorId: 'user-lock',
      canPlay: false,
    });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-lock',
      totalFreeQuota: 5,
      usedFreeQuota: 5,
    });
    prisma.userSubscription.findFirst.mockResolvedValue({
      id: 'sub-lock',
      userId: 'user-lock',
      quotaThisPeriod: 10,
      usedThisPeriod: 1,
      expiresAt: new Date('2026-04-21T00:00:00.000Z'),
      plan: {
        id: 'plan_monthly_basic',
        name: '基础月卡',
      },
    });
    prisma.userSubscription.update.mockResolvedValue({
      id: 'sub-lock',
      quotaThisPeriod: 10,
      usedThisPeriod: 2,
    });
    prisma.game.update.mockResolvedValue(undefined);

    const result = await service.unlock('game-lock', 'user-lock');

    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-lock' },
      data: {
        canPlay: true,
        requireSubscription: false,
        accessGrantSource: GameAccessGrantSource.subscription_unlock,
        accessGrantSubscriptionId: 'sub-lock',
      },
    });
    expect(result).toEqual({
      unlocked: true,
      canPlay: true,
      quotaRemaining: 8,
    });
  });

  it('passes custom timeout through to ai-engine pipeline calls', async () => {
    mockedAxios.post.mockResolvedValue({
      data: {
        html_code: '<!DOCTYPE html><html><head><title>超时测试</title></head><body></body></html>',
        strategy: 'llm',
        qa_passed: true,
        qa_retries: 0,
        game_spec: { game_type: 'runner' },
        generation_time_ms: 1000,
        code_size_bytes: 88,
        quality_score: 90,
        quality_breakdown: {},
      },
    });
    prisma.game.findUnique.mockResolvedValue({
      title: 'Game abcdef12',
    });
    prisma.game.update.mockResolvedValue({});
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue(undefined);

    await (service as any).runPipeline('game-timeout', 'user-timeout', 'make a runner', 900);

    expect(mockedAxios.post).toHaveBeenCalledWith(
      'http://ai-engine.test/api/v1/ai/pipeline/run',
      expect.objectContaining({
        game_id: 'game-timeout',
        timeout_s: 900,
      }),
      expect.objectContaining({
        timeout: 960000,
      }),
    );
  });

  it('prefers deployed region target endpoints over legacy env fallbacks', async () => {
    prisma.aiEngineRegionTarget.findFirst.mockResolvedValue({
      aiEngineUrl: 'https://ai-db.example.com',
    });

    const resolvedUrl = await (service as any).resolveAiEngineEndpoint('cn_shanghai');

    expect(prisma.aiEngineRegionTarget.findFirst).toHaveBeenCalledWith({
      where: {
        executionRegion: 'cn_shanghai',
        deployEnabled: true,
        deployStatus: 'deployed',
        aiEngineUrl: { not: null },
      },
      select: {
        aiEngineUrl: true,
      },
      orderBy: { updatedAt: 'desc' },
    });
    expect(resolvedUrl).toBe('https://ai-db.example.com');
  });

  it('returns an author-only generation status payload for polling', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-status',
      authorId: 'user-status',
      status: 'failed',
      version: 2,
      failedStage: 'qa_checking',
      failedReason: 'Generated code failed QA',
      retryCount: 2,
      lastErrorAt: new Date('2026-03-21T10:00:00.000Z'),
      canPlay: false,
      requireSubscription: true,
    });

    const result = await service.getGenerationStatus('game-status', 'user-status');

    expect(result).toEqual(expect.objectContaining({
      taskId: 'game-status:pipeline',
      taskType: 'pipeline_run',
      status: 'failed',
      stage: 'qa_checking',
      gameId: 'game-status',
      version: 2,
      wsChannel: 'game:game-status',
      pollUrl: '/api/v1/games/game-status/generation-status',
      previewUrl: 'https://www.gamevallies.com/games/game-status/preview',
      failedReason: 'Generated code failed QA',
      retryCount: 2,
    }));
  });
});
