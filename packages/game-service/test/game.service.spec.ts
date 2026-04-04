import axios from 'axios';
import {
  BadRequestException,
  ConflictException,
  ForbiddenException,
  NotFoundException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { JwtService } from '@nestjs/jwt';
import { GameService } from '../src/game/game.service';

jest.mock('axios');

const mockedAxios = axios as jest.Mocked<typeof axios>;
const GameAccessGrantSource = {
  free_quota: 'free_quota',
  subscription_quota: 'subscription_quota',
  none: 'none',
  subscription_unlock: 'subscription_unlock',
} as const;

describe('GameService', () => {
  let service: GameService;
  let prisma: any;
  let bundleService: any;
  let statsService: any;
  let configService: ConfigService;
  let jwtService: JwtService;
  let wsGateway: any;
  let generationTaskService: any;
  let generationQueueService: any;

  const mockAsyncSuccess = (upstreamTaskId: string, result: Record<string, unknown>) => {
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        task_id: upstreamTaskId,
        status: 'queued',
      },
    } as any);
    mockedAxios.get.mockResolvedValue({
      data: {
        task_id: upstreamTaskId,
        status: 'succeeded',
        result,
      },
    } as any);
  };

  beforeEach(() => {
    prisma = {
      $transaction: jest.fn(),
      generationTask: {
        findUnique: jest.fn(),
        findFirst: jest.fn(),
        findMany: jest.fn(),
        update: jest.fn(),
      },
      generationTaskEvent: {
        create: jest.fn(),
      },
      game: {
        create: jest.fn(),
        findUnique: jest.fn(),
        update: jest.fn(),
        updateMany: jest.fn(),
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
        findMany: jest.fn(),
      },
      promptBundle: {
        findFirst: jest.fn(),
      },
      runtimeProfileCatalog: {
        findMany: jest.fn(),
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
      createArtifact: jest.fn(async ({ taskId, artifactType, payload, metadata }: any) => ({
        id: `${taskId || 'artifact'}:${artifactType}`,
        taskId,
        artifactType,
        payloadText: typeof payload === 'string' ? payload : JSON.stringify(payload ?? null),
        metadata: metadata ?? {},
      })),
      markRunning: jest.fn(async () => undefined),
      recordProgress: jest.fn(async () => undefined),
      markSucceeded: jest.fn(async () => undefined),
      markFailed: jest.fn(async () => undefined),
      findArtifactById: jest.fn(),
      findLatestArtifactForTask: jest.fn(),
      findLatestArtifactForGame: jest.fn(),
      getLatestTaskForGame: jest.fn(),
      getTaskForUser: jest.fn(),
      listTaskEvents: jest.fn(),
      requestCancel: jest.fn(async () => undefined),
      toTaskSummary: jest.fn((task: any) => ({
        taskId: task.id,
        taskType: task.taskType,
        status: task.status,
        timeoutS: task.timeoutS,
        wsChannel: task.wsChannel,
        gameId: task.gameId,
        pollUrl: `/api/v1/games/tasks/${task.id}`,
        artifactsUrl: `/api/v1/games/tasks/${task.id}/artifacts`,
      })),
    };
    generationQueueService = {
      ensureReady: jest.fn(async () => false),
      ensureOperational: jest.fn(async () => false),
      enqueueJob: jest.fn(async () => false),
      ensureActiveTaskSweepScheduler: jest.fn(async () => false),
      registerWorkerProcessor: jest.fn(async () => false),
      closeWorker: jest.fn(async () => undefined),
      closeQueue: jest.fn(async () => undefined),
    };
    jwtService = {
      sign: jest.fn((payload: any) => Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url')),
      verify: jest.fn((token: string) => JSON.parse(Buffer.from(token, 'base64url').toString('utf8'))),
    } as unknown as JwtService;
    configService = {
      get: jest.fn((key: string, defaultValue?: string) => {
        const values: Record<string, string> = {
          AI_ENGINE_URL: 'http://ai-engine.test',
          PUBLIC_API_BASE_URL: 'https://gamevallies.com',
          APP_URL: 'https://gamevallies.com',
          ADMIN_TOKEN: 'test-admin-token',
        };
        return values[key] ?? defaultValue;
      }),
    } as unknown as ConfigService;

    mockedAxios.post.mockReset();
    mockedAxios.get.mockReset();
    jest.useRealTimers();
    prisma.game.updateMany.mockResolvedValue({ count: 1 });
    prisma.generationTask.findMany.mockResolvedValue([]);
    prisma.generationTaskEvent.create.mockResolvedValue({});
    prisma.systemConfig.findMany.mockResolvedValue([]);
    prisma.userQuota.updateMany.mockResolvedValue({ count: 0 });
    prisma.promptBundle.findFirst.mockResolvedValue({
      id: 'runtime-v2-default',
      version: 1,
    });
    prisma.runtimeProfileCatalog.findMany.mockResolvedValue([
      {
        id: 'casual_lane',
        contractSchema: {
          inputContract: {
            requiredModes: ['pointer', 'touch'],
            gestures: ['tap', 'swipe'],
          },
          stateContract: {
            requiredStates: ['boot', 'ready', 'playing', 'game_over'],
            restartable: true,
          },
          mobileLayoutContract: {
            orientation: 'portrait_first',
            uiScaleMode: 'short_edge',
          },
          renderContract: {
            requiresCanvas2D: true,
            mustRenderWithinMs: 1500,
          },
        },
        metadata: {},
      },
      {
        id: 'casual_action',
        contractSchema: {
          inputContract: {
            requiredModes: ['pointer', 'touch'],
            gestures: ['drag', 'tap'],
          },
          stateContract: {
            requiredStates: ['boot', 'ready', 'playing', 'game_over'],
            restartable: true,
          },
          mobileLayoutContract: {
            orientation: 'portrait_first',
            uiScaleMode: 'short_edge',
          },
          renderContract: {
            requiresCanvas2D: true,
            mustRenderWithinMs: 1500,
          },
        },
        metadata: {},
      },
      {
        id: 'casual_arcade',
        contractSchema: {
          inputContract: {
            requiredModes: ['pointer', 'touch'],
          },
          stateContract: {
            requiredStates: ['boot', 'ready', 'playing', 'game_over'],
            restartable: true,
          },
          mobileLayoutContract: {
            orientation: 'portrait_first',
            uiScaleMode: 'short_edge',
          },
          safetyContract: {
            forbiddenApis: ['eval', 'Function'],
          },
          renderContract: {
            requiresCanvas2D: true,
            mustRenderWithinMs: 1500,
          },
        },
        metadata: { default: true },
      },
    ]);

    service = new GameService(
      prisma,
      bundleService,
      statsService,
      configService,
      jwtService,
      wsGateway,
      generationTaskService,
      generationQueueService,
    );
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('does not retry async task creation on upstream 504 and persists structured failure context', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-504',
      authorId: 'user-504',
      status: 'generating',
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

  it('retries transient async task creation failures and publishes after upstream success', async () => {
    jest.useFakeTimers();

    mockedAxios.post
      .mockRejectedValueOnce({
        code: 'ECONNRESET',
        message: 'socket hang up',
      })
      .mockResolvedValueOnce({
        data: {
          task_id: 'upstream-network',
          status: 'queued',
        },
      } as any);
    mockedAxios.get.mockResolvedValue({
      data: {
        task_id: 'upstream-network',
        status: 'succeeded',
        result: {
          html_code: '<!DOCTYPE html><html><head><title>Test Game</title></head><body></body></html>',
          strategy: 'llm',
          qa_passed: true,
          qa_retries: 1,
          game_spec: { game_type: 'runner' },
          generation_time_ms: 1234,
          code_size_bytes: 88,
          quality_score: 91,
          quality_breakdown: { qa_penalty: 0 },
        },
      },
    } as any);

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
    expect(mockedAxios.get).toHaveBeenCalledWith(
      'http://ai-engine.test/api/v1/ai/tasks/upstream-network',
      expect.objectContaining({ timeout: 10000 }),
    );
    expect(bundleService.saveBundle).toHaveBeenCalled();
    expect(wsGateway.emitGenerationComplete).toHaveBeenCalledWith(
      'user-network',
      'game-network',
      expect.stringContaining('https://gamevallies.com/games/game-network/preview?previewToken='),
    );
  });

  it('falls back across ai-engine endpoints and stores the selected upstream base URL', async () => {
    mockedAxios.post
      .mockRejectedValueOnce({
        message: 'Request failed with status code 404',
        response: { status: 404 },
      })
      .mockResolvedValueOnce({
        data: {
          task_id: 'upstream-failover',
          status: 'queued',
        },
      } as any);
    prisma.generationTask.findUnique.mockResolvedValue({
      metadata: {
        description: 'persist me',
      },
    });

    const handle = await (service as any).requestUpstreamAsyncTask({
      aiEngineBaseUrls: ['http://ai-engine-primary.test', 'http://ai-engine-backup.test'],
      endpoint: '/api/v1/ai/pipeline/v2/run/async',
      payload: {
        game_id: 'game-failover',
      },
      taskId: 'task-failover',
      userId: 'user-failover',
      gameId: 'game-failover',
    });

    expect(handle).toEqual(expect.objectContaining({
      task_id: 'upstream-failover',
      aiEngineBaseUrl: 'http://ai-engine-backup.test',
    }));
    expect(mockedAxios.post).toHaveBeenNthCalledWith(
      1,
      'http://ai-engine-primary.test/api/v1/ai/pipeline/v2/run/async',
      { game_id: 'game-failover' },
      expect.objectContaining({ timeout: 30000 }),
    );
    expect(mockedAxios.post).toHaveBeenNthCalledWith(
      2,
      'http://ai-engine-backup.test/api/v1/ai/pipeline/v2/run/async',
      { game_id: 'game-failover' },
      expect.objectContaining({ timeout: 30000 }),
    );
    expect(prisma.generationTask.update).toHaveBeenCalledWith({
      where: { id: 'task-failover' },
      data: {
        upstreamTaskId: 'upstream-failover',
        metadata: expect.objectContaining({
          description: 'persist me',
          upstreamBaseUrl: 'http://ai-engine-backup.test',
        }),
      },
    });
  });

  it('biases classroom and quiz prompts toward the grid puzzle runtime profile', () => {
    expect(
      (service as any).inferRuntimeProfileHint(
        '请设计一个课堂小游戏，包含3道配套练习题，帮助学生巩固浮力知识点',
        '浮力的故事',
      ),
    ).toBe('puzzle_grid');
  });

  it('prefers persisted upstream base URLs when fetching upstream snapshots', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        task_id: 'upstream-persisted',
        status: 'succeeded',
        result: {
          html_code: '<!DOCTYPE html><html><body>ok</body></html>',
        },
      },
    } as any);

    const snapshot = await (service as any).fetchUpstreamTaskSnapshotWithFailover(
      {
        region: 'cn_shanghai',
        metadata: {
          upstreamBaseUrl: 'http://ai-engine-persisted.test',
        },
      },
      'upstream-persisted',
    );

    expect(snapshot).toEqual(expect.objectContaining({
      task_id: 'upstream-persisted',
      status: 'succeeded',
    }));
    expect(mockedAxios.get).toHaveBeenCalledTimes(1);
    expect(mockedAxios.get).toHaveBeenCalledWith(
      'http://ai-engine-persisted.test/api/v1/ai/tasks/upstream-persisted',
      expect.objectContaining({ timeout: 10000 }),
    );
  });

  it('keeps polling after a transient upstream snapshot timeout and returns the later terminal snapshot', async () => {
    jest.useFakeTimers();
    prisma.generationTask.findUnique.mockResolvedValue(null);
    mockedAxios.get
      .mockRejectedValueOnce({
        code: 'ECONNABORTED',
        message: 'timeout of 10000ms exceeded',
      })
      .mockResolvedValueOnce({
        data: {
          task_id: 'upstream-recover',
          status: 'succeeded',
          result: {
            html_code: '<!DOCTYPE html><html><body>ok</body></html>',
          },
        },
      } as any);

    const waitPromise = (service as any).waitForUpstreamTaskTerminal({
      aiEngineBaseUrl: 'http://ai-engine.test',
      upstreamTaskId: 'upstream-recover',
      timeoutS: 30,
      taskId: 'task-recover',
    });

    await Promise.resolve();
    await jest.advanceTimersByTimeAsync(1500);

    await expect(waitPromise).resolves.toEqual(expect.objectContaining({
      task_id: 'upstream-recover',
      status: 'succeeded',
    }));
    expect(mockedAxios.get).toHaveBeenCalledTimes(2);
    expect(prisma.generationTask.findUnique).toHaveBeenCalled();
  });

  it('cancels upstream tasks by failing over from persisted to configured ai-engine endpoints', async () => {
    mockedAxios.post
      .mockRejectedValueOnce({
        message: 'Request failed with status code 404',
        response: { status: 404 },
      })
      .mockResolvedValueOnce({ data: {} } as any);

    await (service as any).cancelUpstreamTask({
      id: 'task-cancel-failover',
      region: 'cn_shanghai',
      upstreamTaskId: 'upstream-cancel-failover',
      metadata: {
        upstreamBaseUrl: 'http://ai-engine-persisted.test',
      },
    });

    expect(mockedAxios.post).toHaveBeenNthCalledWith(
      1,
      'http://ai-engine-persisted.test/api/v1/ai/tasks/upstream-cancel-failover/cancel',
      {},
      expect.objectContaining({ timeout: 10000 }),
    );
    expect(mockedAxios.post).toHaveBeenNthCalledWith(
      2,
      'http://ai-engine.test/api/v1/ai/tasks/upstream-cancel-failover/cancel',
      {},
      expect.objectContaining({ timeout: 10000 }),
    );
  });

  it('fails closed when upstream async task returns empty html output', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-empty',
      authorId: 'user-empty',
      status: 'generating',
      accessGrantSource: GameAccessGrantSource.none,
      accessGrantSubscriptionId: null,
    });
    prisma.game.update.mockResolvedValue({});
    mockAsyncSuccess('upstream-empty', {
      strategy: 'llm',
      qa_passed: true,
      qa_retries: 0,
      game_spec: { game_type: 'runner' },
      generation_time_ms: 123,
      code_size_bytes: 0,
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
  });

  it('retries iteration publish persistence before surfacing success', async () => {
    mockAsyncSuccess('upstream-iter', {
      html_code: '<!DOCTYPE html><html><body>updated</body></html>',
      iteration_type: 'element_change',
      generation_time_ms: 456,
      qa_retries: 1,
      iteration_retries: 2,
    });

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-iter',
      authorId: 'user-iter',
      status: 'generating',
      version: 1,
      accessGrantSource: GameAccessGrantSource.none,
      accessGrantSubscriptionId: null,
    });
    prisma.game.update.mockResolvedValue({});
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

    await runPromise;

    expect(bundleService.saveBundle).toHaveBeenCalledTimes(2);
    expect(wsGateway.emitGenerationComplete).toHaveBeenCalledWith(
      'user-iter',
      'game-iter',
      expect.stringContaining('https://gamevallies.com/games/game-iter/preview?previewToken='),
    );
  });

  it('does not refund the original creation quota when an iteration task fails later', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-iter-failed',
      authorId: 'user-iter',
      status: 'generating',
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
    const runPipelineSpy = jest.spyOn(service as any, 'executePipelineTask').mockResolvedValue(undefined);

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
      gameId: expect.any(String),
      canPlay: true,
      quotaRemaining: 4,
      requireSubscription: false,
      taskId: expect.any(String),
      artifactsUrl: expect.any(String),
      generationTask: expect.objectContaining({
        taskType: 'pipeline_run',
        status: 'queued',
        timeoutS: 1800,
      }),
    }));
    expect(result.taskId).toBe(`${result.gameId}:pipeline_run`);
    expect(result.artifactsUrl).toBe(`/api/v1/games/tasks/${result.taskId}/artifacts`);

    await new Promise((resolve) => setImmediate(resolve));
    runPipelineSpy.mockRestore();
  });

  it('still creates a locked game when all quota is exhausted', async () => {
    const runPipelineSpy = jest.spyOn(service as any, 'executePipelineTask').mockResolvedValue(undefined);

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
        timeoutS: 1800,
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
        name: 'basic',
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

  it('passes custom timeout through to async ai-engine pipeline calls', async () => {
    prisma.game.findUnique.mockResolvedValue({
      title: 'Game abcdef12',
    });
    prisma.game.update.mockResolvedValue({});
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue(undefined);
    mockAsyncSuccess('upstream-timeout', {
      html_code: '<!DOCTYPE html><html><head><title>Timeout Test</title></head><body></body></html>',
      strategy: 'llm',
      qa_passed: true,
      qa_retries: 0,
      game_spec: { game_type: 'runner' },
      generation_time_ms: 1000,
      code_size_bytes: 88,
      quality_score: 90,
      quality_breakdown: {},
    });

    await (service as any).runPipeline('game-timeout', 'user-timeout', 'make a runner', 900);

    expect(mockedAxios.post).toHaveBeenCalledWith(
      'http://ai-engine.test/api/v1/ai/pipeline/v2/run/async',
      expect.objectContaining({
        game_id: 'game-timeout',
        raw_user_input: 'make a runner',
        timeout_s: 900,
      }),
      expect.objectContaining({
        timeout: 30000,
      }),
    );
  });

  it('stores pipeline v2 metadata on create tasks when the feature flag is enabled', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
        PIPELINE_V2_ENTRYPOINTS: 'create,iterate',
      };
      return values[key] ?? defaultValue;
    });
    const executePipelineTaskSpy = jest
      .spyOn(service as any, 'executePipelineTask')
      .mockResolvedValue(undefined);

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-v2-create',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.userQuota.update.mockResolvedValue({
      userId: 'user-v2-create',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.game.create.mockResolvedValue({ id: 'game-v2-create' });

    await service.create('user-v2-create', {
      description: 'make a v2 puzzle game',
    } as any);

    expect(prisma.game.create).toHaveBeenCalledWith({
      data: expect.objectContaining({
        authorId: 'user-v2-create',
        visibility: 'private',
      }),
    });
    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      pipelineVersion: 'v2',
      promptBundleId: 'runtime-v2-default',
      promptBundleVersion: 1,
      runtimeProfile: 'casual_arcade',
      contractVersion: '1.0',
    }));

    await new Promise((resolve) => setImmediate(resolve));
    executePipelineTaskSpy.mockRestore();
  });

  it('passes the requested landscape orientation into v2 creation tasks', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
        PIPELINE_V2_ENTRYPOINTS: 'create,iterate',
      };
      return values[key] ?? defaultValue;
    });
    const executePipelineTaskSpy = jest
      .spyOn(service as any, 'executePipelineTask')
      .mockResolvedValue(undefined);

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-v2-landscape',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.userQuota.update.mockResolvedValue({
      userId: 'user-v2-landscape',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.game.create.mockResolvedValue({ id: 'game-v2-landscape' });

    await service.create('user-v2-landscape', {
      description: 'make a wide runner game',
      orientation: 'landscape',
    } as any);

    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      metadata: expect.objectContaining({
        orientation: 'landscape',
      }),
    }));

    await new Promise((resolve) => setImmediate(resolve));
    expect(executePipelineTaskSpy).toHaveBeenCalledWith(
      expect.any(String),
      'user-v2-landscape',
      'make a wide runner game',
      expect.any(Number),
      expect.any(String),
      expect.any(String),
      expect.objectContaining({
        orientation: 'landscape',
        runtimeContract: expect.objectContaining({
          canvas: expect.objectContaining({
            orientation: 'landscape_first',
          }),
          mobile_layout: expect.objectContaining({
            orientation: 'landscape_first',
          }),
          metadata: expect.objectContaining({
            requested_orientation: 'landscape',
            orientation: 'landscape_first',
          }),
        }),
      }),
    );
    executePipelineTaskSpy.mockRestore();
  });

  it('infers landscape orientation from request text when clients omit the field', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
        PIPELINE_V2_ENTRYPOINTS: 'create,iterate',
      };
      return values[key] ?? defaultValue;
    });
    const executePipelineTaskSpy = jest
      .spyOn(service as any, 'executePipelineTask')
      .mockResolvedValue(undefined);

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-v2-inferred-landscape',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.userQuota.update.mockResolvedValue({
      userId: 'user-v2-inferred-landscape',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.game.create.mockResolvedValue({ id: 'game-v2-inferred-landscape' });

    await service.create('user-v2-inferred-landscape', {
      title: '横屏闯关',
      description: '做一个横屏跑跳闯关小游戏',
    } as any);

    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      metadata: expect.objectContaining({
        orientation: 'landscape',
      }),
    }));

    await new Promise((resolve) => setImmediate(resolve));
    expect(executePipelineTaskSpy).toHaveBeenCalledWith(
      expect.any(String),
      'user-v2-inferred-landscape',
      '做一个横屏跑跳闯关小游戏',
      expect.any(Number),
      expect.any(String),
      expect.any(String),
      expect.objectContaining({
        orientation: 'landscape',
        runtimeContract: expect.objectContaining({
          canvas: expect.objectContaining({
            orientation: 'landscape_first',
          }),
          mobile_layout: expect.objectContaining({
            orientation: 'landscape_first',
          }),
          metadata: expect.objectContaining({
            requested_orientation: 'landscape',
            orientation: 'landscape_first',
          }),
        }),
      }),
    );
    executePipelineTaskSpy.mockRestore();
  });

  it('persists requested and runtime orientation in bundle metadata for completed create tasks', async () => {
    prisma.game.findUnique
      .mockResolvedValueOnce({ title: 'Game abcdef12' })
      .mockResolvedValueOnce({ version: 0, status: 'generating' });
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue({});

    await (service as any).completePipelineTask({
      gameId: 'game-orientation-meta',
      userId: 'user-orientation-meta',
      description: 'make a wide runner game',
      orientation: 'landscape',
      runtimeContract: {
        version: '1.0',
        runtime_profile: 'casual_lane',
        metadata: {
          orientation: 'landscape_first',
        },
        canvas: {
          orientation: 'landscape_first',
        },
        mobile_layout: {
          orientation: 'landscape_first',
        },
      },
      responseData: {
        html_code: '<!DOCTYPE html><html><head><title>Wide Runner</title></head><body></body></html>',
        game_spec: {
          game_type: 'runner',
        },
      },
    });

    expect(bundleService.saveBundle).toHaveBeenCalledWith(expect.objectContaining({
      metadata: expect.objectContaining({
        requestedOrientation: 'landscape',
        runtimeOrientation: 'landscape_first',
      }),
    }));
  });

  it('normalizes generated game types into the curated 4-category catalog when create completes', async () => {
    const persistGeneratedGameResultSpy = jest
      .spyOn(service as any, 'persistGeneratedGameResult')
      .mockResolvedValue(undefined);
    const assertTaskCanPersistResultSpy = jest
      .spyOn(service as any, 'assertTaskCanPersistResult')
      .mockResolvedValue(undefined);

    prisma.game.findUnique.mockResolvedValue({ title: 'Game abcdef12' });

    await (service as any).completePipelineTask({
      gameId: 'game-type-normalized',
      userId: 'user-type-normalized',
      description: 'make a runner game',
      taskId: 'task-type-normalized',
      responseData: {
        html_code: '<!DOCTYPE html><html><head><title>Runner</title></head><body></body></html>',
        game_spec: {
          game_type: 'runner',
        },
      },
    });

    const persistCall = persistGeneratedGameResultSpy.mock.calls[0]?.[0] as any;
    expect(persistCall.metadata.gameType).toBe('casual');
    expect(persistCall.updateData.gameType).toBe('casual');
    expect(generationTaskService.markSucceeded).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-type-normalized',
      resultSummary: expect.objectContaining({
        gameType: 'casual',
      }),
    }));

    assertTaskCanPersistResultSpy.mockRestore();
    persistGeneratedGameResultSpy.mockRestore();
  });

  it('clamps v2 create timeouts to at least 1800 seconds', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
        PIPELINE_TIMEOUT_S: '1800',
      };
      return values[key] ?? defaultValue;
    });
    const executePipelineTaskSpy = jest
      .spyOn(service as any, 'executePipelineTask')
      .mockResolvedValue(undefined);

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-v2-timeout',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.userQuota.update.mockResolvedValue({
      userId: 'user-v2-timeout',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.game.create.mockResolvedValue({ id: 'game-v2-timeout' });

    await service.create('user-v2-timeout', {
      description: 'make a slow but valid v2 game',
      timeoutS: 600,
    } as any);

    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      pipelineVersion: 'v2',
      timeoutS: 1800,
    }));

    await new Promise((resolve) => setImmediate(resolve));
    expect(executePipelineTaskSpy).toHaveBeenCalledWith(
      expect.any(String),
      'user-v2-timeout',
      'make a slow but valid v2 game',
      1800,
      expect.any(String),
      expect.any(String),
      expect.any(Object),
    );
    executePipelineTaskSpy.mockRestore();
  });

  it('allows scoped v2 rollout when the global pipeline version is v1', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v1',
        PIPELINE_V2_ENTRYPOINTS: 'create',
        PIPELINE_V2_USER_IDS: 'user-canary',
      };
      return values[key] ?? defaultValue;
    });
    const executePipelineTaskSpy = jest
      .spyOn(service as any, 'executePipelineTask')
      .mockResolvedValue(undefined);

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-canary',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.userQuota.update.mockResolvedValue({
      userId: 'user-canary',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.game.create.mockResolvedValue({ id: 'game-canary' });

    await service.create('user-canary', {
      description: 'make a canary game',
    } as any);

    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      pipelineVersion: 'v2',
    }));

    await new Promise((resolve) => setImmediate(resolve));
    executePipelineTaskSpy.mockRestore();
  });

  it('allows scoped v1 rollback when the global pipeline version is v2', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
        PIPELINE_V1_ENTRYPOINTS: 'create',
        PIPELINE_V1_USER_IDS: 'user-rollback',
      };
      return values[key] ?? defaultValue;
    });
    const executePipelineTaskSpy = jest
      .spyOn(service as any, 'executePipelineTask')
      .mockResolvedValue(undefined);

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-rollback',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.userQuota.update.mockResolvedValue({
      userId: 'user-rollback',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.game.create.mockResolvedValue({ id: 'game-rollback' });

    await service.create('user-rollback', {
      description: 'make a rollback game',
    } as any);

    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      pipelineVersion: 'v1',
    }));

    await new Promise((resolve) => setImmediate(resolve));
    executePipelineTaskSpy.mockRestore();
  });

  it('routes create generation through the v2 async endpoint with a structured payload', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
      };
      return values[key] ?? defaultValue;
    });
    prisma.game.findUnique.mockResolvedValue({
      title: 'Game abcdef12',
    });
    prisma.game.update.mockResolvedValue({});
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue(undefined);
    mockAsyncSuccess('upstream-v2-create', {
      html_code: '<!DOCTYPE html><html><head><title>V2 Create</title></head><body></body></html>',
      strategy: 'llm',
      qa_passed: true,
      qa_retries: 0,
      game_spec: { game_type: 'runner' },
      generation_time_ms: 1000,
      code_size_bytes: 88,
      quality_score: 90,
      quality_breakdown: {},
    });

    await (service as any).executePipelineTask(
      'game-v2-create',
      'user-v2-create',
      'make a runner',
      900,
      'task-v2-create',
      'cn_shanghai',
      {
        pipelineVersion: 'v2',
        title: 'V2 Runner',
        orientation: 'landscape',
        access: {
          canPlay: true,
          requireSubscription: false,
          quotaRemaining: 4,
          accessGrantSource: GameAccessGrantSource.free_quota,
          accessGrantSubscriptionId: null,
        },
      },
    );

    expect(mockedAxios.post).toHaveBeenCalledWith(
      'http://ai-engine.test/api/v1/ai/pipeline/v2/run/async',
      expect.objectContaining({
        game_id: 'game-v2-create',
        raw_user_input: 'make a runner',
        request_context: expect.objectContaining({
          entrypoint: 'create',
          pipeline_version: 'v2',
          region: 'cn_shanghai',
          metadata: expect.objectContaining({
            orientation: 'landscape',
          }),
        }),
        entitlement: expect.objectContaining({
          can_play: true,
          require_subscription: false,
          refund_on_failure: true,
        }),
        visibility_model: expect.objectContaining({
          published_visibility: 'private',
          public_preview_allowed: false,
        }),
        prompt_bundle_snapshot: expect.objectContaining({
          bundle_id: 'runtime-v2-default',
          layers: expect.objectContaining({
            profile_few_shot: 'casual_lane',
          }),
        }),
        runtime_contract: expect.objectContaining({
          runtime_profile: 'casual_lane',
          canvas: expect.objectContaining({
            orientation: 'landscape_first',
          }),
          mobile_layout: expect.objectContaining({
            orientation: 'landscape_first',
          }),
        }),
        normalized_request: expect.objectContaining({
          orientation: 'landscape',
        }),
        metadata: expect.objectContaining({
          orientation: 'landscape',
        }),
      }),
      expect.objectContaining({
        timeout: 30000,
      }),
    );
  });

  it('persists upstream primary artifact ids on successful v2 tasks', async () => {
    prisma.generationTask.findUnique.mockImplementation(({ select }: any) => {
      if (select?.cancelRequested) {
        return Promise.resolve({
          status: 'running',
          cancelRequested: false,
          gameId: 'game-v2-artifact',
        });
      }

      return Promise.resolve({
        status: 'running',
      });
    });
    prisma.game.findUnique.mockImplementation(({ select }: any) => {
      if (select?.title) {
        return Promise.resolve({
          title: 'Game artifact12',
        });
      }

      if (select?.version) {
        return Promise.resolve({
          version: 0,
          status: 'generating',
        });
      }

      return Promise.resolve({
        status: 'generating',
      });
    });
    prisma.game.update.mockResolvedValue({});
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue(undefined);
    mockAsyncSuccess('upstream-v2-artifact', {
      html_code: '<!DOCTYPE html><html><head><title>Artifact Test</title></head><body></body></html>',
      strategy: 'llm',
      qa_passed: true,
      qa_retries: 0,
      game_spec: { game_type: 'runner' },
      generation_time_ms: 1000,
      code_size_bytes: 88,
      quality_score: 90,
      quality_breakdown: {},
      primary_artifact_id: 'artifact-success-1',
    });

    await (service as any).executePipelineTask(
      'game-v2-artifact',
      'user-v2-artifact',
      'make a runner',
      900,
      'task-v2-artifact',
      'cn_shanghai',
      {
        pipelineVersion: 'v2',
      },
    );

    expect(generationTaskService.markSucceeded).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-v2-artifact',
      primaryArtifactId: 'artifact-success-1',
    }));
  });

  it('recovers create tasks from stored pipeline artifacts after upstream wait timeout', async () => {
    prisma.generationTask.findUnique.mockImplementation(({ where, select }: any) => {
      if (where?.id === 'task-v2-recover' && select?.cancelRequested) {
        return Promise.resolve({
          status: 'running',
          cancelRequested: false,
          gameId: 'game-v2-recover',
        });
      }

      if (where?.id === 'task-v2-recover') {
        return Promise.resolve({
          id: 'task-v2-recover',
          status: 'succeeded',
        });
      }

      return Promise.resolve(null);
    });
    prisma.game.findUnique.mockImplementation(({ select }: any) => {
      if (select?.title) {
        return Promise.resolve({
          title: 'Recovered task game',
        });
      }

      if (select?.version) {
        return Promise.resolve({
          version: 0,
          status: 'generating',
        });
      }

      return Promise.resolve({
        status: 'generating',
      });
    });
    prisma.game.update.mockResolvedValue({});
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue(undefined);
    generationTaskService.findLatestArtifactForTask.mockResolvedValue({
      payloadJson: {
        html_code: '<!DOCTYPE html><html><head><title>Recovered Create</title></head><body></body></html>',
        strategy: 'llm',
        qa_passed: true,
        qa_retries: 0,
        game_spec: { game_type: 'runner' },
        generation_time_ms: 1200,
        code_size_bytes: 99,
        quality_score: 91,
        quality_breakdown: {},
        primary_artifact_id: 'artifact-recovered-1',
      },
    });

    jest.spyOn(service as any, 'requestUpstreamAsyncTask').mockResolvedValue({
      task_id: 'upstream-v2-recover',
      status: 'queued',
    });
    jest.spyOn(service as any, 'waitForUpstreamTaskTerminal').mockRejectedValue(
      new Error('Upstream AI task upstream-v2-recover timed out while waiting for completion'),
    );

    await (service as any).executePipelineTask(
      'game-v2-recover',
      'user-v2-recover',
      'make a runner',
      900,
      'task-v2-recover',
      'cn_shanghai',
      {
        pipelineVersion: 'v2',
      },
    );

    expect(generationTaskService.findLatestArtifactForTask).toHaveBeenCalledWith(
      'task-v2-recover',
      ['pipeline_response'],
    );
    expect(generationTaskService.markSucceeded).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-v2-recover',
      primaryArtifactId: 'artifact-recovered-1',
    }));
    expect(generationTaskService.markFailed).not.toHaveBeenCalled();
  });

  it('recovers completed tasks from stored artifacts before timing them out', async () => {
    prisma.generationTask.findUnique.mockImplementation(({ where, select }: any) => {
      if (where?.id === 'task-timeout-recover' && select?.cancelRequested) {
        return Promise.resolve({
          status: 'running',
          cancelRequested: false,
          gameId: 'game-timeout-recover',
        });
      }

      if (where?.id === 'task-timeout-recover') {
        return Promise.resolve({
          id: 'task-timeout-recover',
          status: 'succeeded',
        });
      }

      return Promise.resolve(null);
    });
    prisma.game.findUnique.mockImplementation(({ select }: any) => {
      if (select?.title) {
        return Promise.resolve({
          title: 'Recovered timeout game',
        });
      }

      if (select?.version) {
        return Promise.resolve({
          version: 0,
          status: 'generating',
        });
      }

      return Promise.resolve({
        status: 'generating',
      });
    });
    prisma.game.update.mockResolvedValue({});
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue(undefined);
    generationTaskService.findLatestArtifactForTask.mockResolvedValue({
      payloadJson: {
        html_code: '<!DOCTYPE html><html><head><title>Recovered Timeout</title></head><body></body></html>',
        strategy: 'llm',
        qa_passed: true,
        qa_retries: 0,
        game_spec: { game_type: 'runner' },
        generation_time_ms: 1400,
        code_size_bytes: 101,
        quality_score: 92,
        quality_breakdown: {},
        primary_artifact_id: 'artifact-recovered-timeout',
      },
    });
    const cancelUpstreamTaskSpy = jest
      .spyOn(service as any, 'cancelUpstreamTask')
      .mockResolvedValue(undefined);

    const result = await (service as any).persistDerivedTaskResolution(
      {
        id: 'task-timeout-recover',
        gameId: 'game-timeout-recover',
        userId: 'user-timeout-recover',
        taskType: 'pipeline_run',
        progressStage: 'completed',
        retryCount: 0,
        metadata: {
          description: 'make a runner',
        },
      },
      {
        id: 'game-timeout-recover',
        status: 'generating',
      },
      {
        status: 'timed_out',
        stage: 'completed',
        message: 'Task timed out after 1200s',
        retryCount: 0,
      },
    );

    expect(generationTaskService.findLatestArtifactForTask).toHaveBeenCalledWith(
      'task-timeout-recover',
      ['pipeline_response'],
    );
    expect(cancelUpstreamTaskSpy).not.toHaveBeenCalled();
    expect(generationTaskService.markSucceeded).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-timeout-recover',
      primaryArtifactId: 'artifact-recovered-timeout',
    }));
    expect(generationTaskService.markFailed).not.toHaveBeenCalled();
    expect(result).toEqual(expect.objectContaining({
      id: 'task-timeout-recover',
      status: 'succeeded',
    }));
    cancelUpstreamTaskSpy.mockRestore();
  });

  it('persists failure family and primary artifact ids from failed upstream async tasks', async () => {
    prisma.generationTask.findUnique.mockImplementation(({ select }: any) => {
      if (select?.cancelRequested) {
        return Promise.resolve({
          status: 'running',
          cancelRequested: false,
          gameId: 'game-v2-fail',
        });
      }

      return Promise.resolve({
        status: 'running',
      });
    });
    prisma.game.findUnique.mockImplementation(({ select }: any) => {
      if (select?.status) {
        return Promise.resolve({
          status: 'generating',
        });
      }

      return Promise.resolve({
        id: 'game-v2-fail',
        authorId: 'user-v2-fail',
        status: 'generating',
        accessGrantSource: GameAccessGrantSource.none,
        accessGrantSubscriptionId: null,
      });
    });
    prisma.game.update.mockResolvedValue({});

    mockedAxios.post.mockResolvedValueOnce({
      data: {
        task_id: 'upstream-v2-fail',
        status: 'queued',
      },
    } as any);
    mockedAxios.get.mockResolvedValue({
      data: {
        task_id: 'upstream-v2-fail',
        status: 'failed',
        error: {
          message: 'Contract QA failed',
          failed_stage: 'qa_checking',
          retry_count: 2,
          failure_family: 'contract_qa',
          primary_artifact_id: 'artifact-failure-1',
        },
      },
    } as any);

    await (service as any).executePipelineTask(
      'game-v2-fail',
      'user-v2-fail',
      'make a broken runner',
      900,
      'task-v2-fail',
      'cn_shanghai',
      {
        pipelineVersion: 'v2',
      },
    );

    expect(generationTaskService.markFailed).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-v2-fail',
      failedStage: 'qa_checking',
      failureFamily: 'contract_qa',
      primaryArtifactId: 'artifact-failure-1',
    }));
  });

  it('reconciles relayed pipeline failures even after the task row is already marked failed', async () => {
    prisma.generationTask.findUnique.mockImplementation(({ select }: any) => {
      if (select?.taskType) {
        return Promise.resolve({
          id: 'task-relayed-failure',
          gameId: 'game-relayed-failure',
          userId: 'user-relayed-failure',
          taskType: 'pipeline_run',
        });
      }

      return Promise.resolve({
        status: 'failed',
      });
    });
    prisma.game.findUnique.mockImplementation(({ select }: any) => {
      if (select?.status) {
        return Promise.resolve({
          status: 'generating',
        });
      }

      return Promise.resolve({
        id: 'game-relayed-failure',
        authorId: 'user-relayed-failure',
        accessGrantSource: GameAccessGrantSource.none,
        accessGrantSubscriptionId: null,
      });
    });
    prisma.game.update.mockResolvedValue({});

    await service.reconcileRelayedTaskFailure({
      taskId: 'task-relayed-failure',
      failedStage: 'contract_qa',
      errorMessage: 'Runtime contract requires a restart entry point',
      retryCount: 3,
      failureFamily: 'contract_qa',
      primaryArtifactId: 'artifact-relayed-failure',
    });

    expect(prisma.game.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'game-relayed-failure' },
      data: expect.objectContaining({
        status: 'failed',
        failedStage: 'contract_qa',
        failedReason: 'Runtime contract requires a restart entry point',
        retryCount: 3,
      }),
    }));
    expect(generationTaskService.markFailed).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-relayed-failure',
      failedStage: 'contract_qa',
      errorMessage: 'Runtime contract requires a restart entry point',
      failureFamily: 'contract_qa',
      primaryArtifactId: 'artifact-relayed-failure',
    }));
  });

  it('routes iteration through the v2 async endpoint with structured context', async () => {
    const completeIterationTaskSpy = jest
      .spyOn(service as any, 'completeIterationTask')
      .mockResolvedValue(undefined);
    mockAsyncSuccess('upstream-v2-iterate', {
      html_code: '<!DOCTYPE html><html><body>new</body></html>',
      iteration_type: 'element_change',
      generation_time_ms: 1000,
      qa_retries: 0,
      iteration_retries: 0,
    });

    await (service as any).executeIterationTask(
      'game-v2-iterate',
      'user-v2-iterate',
      'make it faster',
      3,
      [{ role: 'assistant', content: 'old context' }],
      '<!DOCTYPE html><html><body>old</body></html>',
      900,
      'task-v2-iterate',
      'cn_shanghai',
      {
        pipelineVersion: 'v2',
        orientation: 'landscape',
        runtimeContract: {
          version: '1.0',
          runtime_profile: 'casual_lane',
          metadata: {
            orientation: 'landscape_first',
          },
          canvas: {
            orientation: 'landscape_first',
          },
          mobile_layout: {
            orientation: 'landscape_first',
          },
        },
        game: {
          status: 'published',
          visibility: 'public',
          version: 2,
          canPlay: true,
          requireSubscription: false,
          accessGrantSource: GameAccessGrantSource.free_quota,
          accessGrantSubscriptionId: null,
          forkedFrom: null,
        },
      },
    );

    expect(mockedAxios.post).toHaveBeenCalledWith(
      'http://ai-engine.test/api/v1/ai/pipeline/v2/iterate/async',
      expect.objectContaining({
        game_id: 'game-v2-iterate',
        current_code: '<!DOCTYPE html><html><body>old</body></html>',
        iteration_intent: expect.objectContaining({
          feedback: 'make it faster',
          conversation: [{ role: 'assistant', content: 'old context' }],
        }),
        existing_game: expect.objectContaining({
          status: 'published',
          visibility: 'public',
          live_bundle_version: 2,
        }),
        entitlement: expect.objectContaining({
          refund_on_failure: false,
        }),
        request_context: expect.objectContaining({
          entrypoint: 'iterate',
          pipeline_version: 'v2',
          metadata: expect.objectContaining({
            orientation: 'landscape',
          }),
        }),
        runtime_contract: expect.objectContaining({
          mobile_layout: expect.objectContaining({
            orientation: 'landscape_first',
          }),
        }),
        normalized_request: expect.objectContaining({
          orientation: 'landscape',
        }),
        metadata: expect.objectContaining({
          orientation: 'landscape',
        }),
      }),
      expect.objectContaining({
        timeout: 30000,
      }),
    );

    completeIterationTaskSpy.mockRestore();
  });

  it('supports the v2 business journey from create to author preview and public publish', async () => {
    let gameState: any = null;
    const taskState = {
      id: 'task-v2-journey-create',
      status: 'running',
      cancelRequested: false,
      gameId: 'game-v2-journey',
    };
    const bundles = new Map<number, any>();

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-v2-journey',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);
    prisma.userQuota.update.mockResolvedValue({
      userId: 'user-v2-journey',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.game.create.mockImplementation(({ data }: any) => {
      gameState = {
        id: 'game-v2-journey',
        title: data.title,
        description: data.description,
        authorId: data.authorId,
        status: data.status,
        version: data.version ?? 0,
        visibility: data.visibility,
        canPlay: data.canPlay,
        requireSubscription: data.requireSubscription,
        accessGrantSource: data.accessGrantSource,
        accessGrantSubscriptionId: data.accessGrantSubscriptionId,
        gameType: data.gameType ?? null,
        qualityScore: data.qualityScore ?? null,
        tags: [],
        forkedFrom: null,
      };
      return Promise.resolve({ id: gameState.id });
    });
    prisma.generationTask.findUnique.mockImplementation(({ where, select }: any) => {
      if (where?.id !== taskState.id) {
        return Promise.resolve(null);
      }
      if (select?.cancelRequested) {
        return Promise.resolve({
          status: taskState.status,
          cancelRequested: taskState.cancelRequested,
          gameId: taskState.gameId,
        });
      }
      return Promise.resolve({ status: taskState.status });
    });
    prisma.generationTask.update.mockResolvedValue({});
    prisma.game.findUnique.mockImplementation(({ where, select }: any) => {
      if (!gameState || where?.id !== gameState.id) {
        return Promise.resolve(null);
      }
      if (select) {
        const projection: any = {};
        Object.keys(select).forEach((key) => {
          projection[key] = gameState[key];
        });
        return Promise.resolve(projection);
      }
      return Promise.resolve({ ...gameState });
    });
    prisma.game.update.mockImplementation(({ data }: any) => {
      gameState = { ...gameState, ...data };
      return Promise.resolve({ ...gameState });
    });
    prisma.game.updateMany.mockImplementation(({ where, data }: any) => {
      if (
        gameState
        && where?.id === gameState.id
        && (where?.version === undefined || where.version === gameState.version)
        && (where?.status === undefined || where.status === gameState.status)
      ) {
        gameState = { ...gameState, ...data };
        return Promise.resolve({ count: 1 });
      }
      return Promise.resolve({ count: 0 });
    });
    bundleService.getBundle.mockImplementation(async (gameId: string, version: number) => {
      if (gameId !== gameState?.id) {
        return null;
      }
      return bundles.get(version) ?? null;
    });
    bundleService.getLatestBundle.mockImplementation(async (gameId: string) => {
      if (gameId !== gameState?.id || bundles.size === 0) {
        return null;
      }
      const version = Math.max(...Array.from(bundles.keys()));
      return bundles.get(version) ?? null;
    });
    bundleService.saveBundle.mockImplementation(async (payload: any) => {
      bundles.set(payload.version, {
        gameId: payload.gameId,
        version: payload.version,
        htmlCode: payload.htmlCode,
        previewUrl: payload.previewUrl,
        metadata: payload.metadata,
      });
    });
    mockAsyncSuccess('upstream-v2-journey-create', {
      html_code: '<!DOCTYPE html><html><head><title>Journey One</title></head><body>v1</body></html>',
      strategy: 'llm',
      qa_passed: true,
      qa_retries: 0,
      game_spec: { game_type: 'runner' },
      generation_time_ms: 1000,
      code_size_bytes: 88,
      quality_score: 90,
      quality_breakdown: {},
      primary_artifact_id: 'artifact-journey-create',
    });
    generationTaskService.createTask.mockResolvedValue({
      id: taskState.id,
      taskType: 'pipeline_run',
      status: 'queued',
      timeoutS: 1200,
      wsChannel: 'game:game-v2-journey',
      gameId: 'game-v2-journey',
      version: 1,
      createdAt: new Date(),
      updatedAt: new Date(),
    });
    const executePipelineTaskSpy = jest
      .spyOn(service as any, 'executePipelineTask')
      .mockResolvedValue(undefined);

    await service.create('user-v2-journey', {
      description: 'make a runner game',
    } as any);
    await new Promise((resolve) => setImmediate(resolve));
    executePipelineTaskSpy.mockRestore();

    await (service as any).executePipelineTask(
      'game-v2-journey',
      'user-v2-journey',
      'make a runner game',
      1200,
      taskState.id,
      'cn_shanghai',
      {
        pipelineVersion: 'v2',
        title: gameState.title,
        access: {
          canPlay: true,
          requireSubscription: false,
          quotaRemaining: 4,
          accessGrantSource: GameAccessGrantSource.free_quota,
          accessGrantSubscriptionId: null,
        },
      },
    );

    expect(gameState.status).toBe('draft');
    expect(gameState.visibility).toBe('private');
    expect(gameState.version).toBe(1);

    const authorPreview = await service.getPlayableHtml('game-v2-journey', 'user-v2-journey');
    expect(authorPreview).toContain('Journey One');
    await expect(service.getPlayData('game-v2-journey')).rejects.toThrow('Game not found');

    await service.publish('game-v2-journey', 'user-v2-journey', {
      title: 'Journey One',
      description: 'runner',
      tags: ['runner'],
      gameType: 'runner',
    } as any);

    const publicPreview = await service.getPlayData('game-v2-journey');
    expect(publicPreview).toContain('Journey One');
    expect(gameState.status).toBe('published');
    expect(gameState.visibility).toBe('public');
    expect(gameState.version).toBe(1);
  });

  it('persists a versioned cover url when create completion has a stored cover artifact', async () => {
    let gameState: any = {
      id: 'game-cover-create',
      title: 'Game abcdef12',
      authorId: 'user-cover-create',
      status: 'generating',
      version: 0,
    };

    prisma.generationTask.findUnique.mockImplementation(({ where, select }: any) => {
      if (where?.id !== 'task-cover-create') {
        return Promise.resolve(null);
      }
      if (select?.cancelRequested) {
        return Promise.resolve({
          status: 'running',
          cancelRequested: false,
          gameId: 'game-cover-create',
        });
      }
      return Promise.resolve({ status: 'running' });
    });
    prisma.game.findUnique.mockImplementation(({ where, select }: any) => {
      if (where?.id !== 'game-cover-create') {
        return Promise.resolve(null);
      }
      if (select) {
        const projection: any = {};
        Object.keys(select).forEach((key) => {
          projection[key] = gameState[key];
        });
        return Promise.resolve(projection);
      }
      return Promise.resolve({ ...gameState });
    });
    prisma.game.updateMany.mockImplementation(({ where, data }: any) => {
      if (where?.id === gameState.id) {
        gameState = { ...gameState, ...data };
        return Promise.resolve({ count: 1 });
      }
      return Promise.resolve({ count: 0 });
    });
    bundleService.getBundle.mockResolvedValue(null);
    bundleService.saveBundle.mockResolvedValue(undefined);
    generationTaskService.findLatestArtifactForTask.mockResolvedValue({
      gameId: 'game-cover-create',
      contentType: 'image/jpeg',
      payloadText: 'ZmFrZS1pbWFnZS1kYXRh',
      metadata: { encoding: 'base64', truncated: false },
    });

    await (service as any).completePipelineTask({
      gameId: 'game-cover-create',
      userId: 'user-cover-create',
      description: 'make a tiny arcade game',
      taskId: 'task-cover-create',
      responseData: {
        html_code: '<!DOCTYPE html><html><head><title>Cover Create</title></head><body>cover</body></html>',
        strategy: 'llm',
        qa_passed: true,
        qa_retries: 0,
        game_spec: { game_type: 'runner' },
        generation_time_ms: 1200,
        code_size_bytes: 100,
        quality_score: 88,
        quality_breakdown: {},
      },
    });

    expect(gameState.thumbnailUrl).toBe(
      'https://gamevallies.com/api/v1/games/game-cover-create/cover?taskId=task-cover-create&v=1',
    );
    expect(generationTaskService.markSucceeded).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-cover-create',
      resultSummary: expect.objectContaining({
        coverGenerated: true,
      }),
    }));
  });

  it('serves stored cover images for author preview tokens on draft games', async () => {
    const previewToken = Buffer.from(JSON.stringify({
      type: 'game_preview',
      sub: 'user-cover-preview',
      gameId: 'game-cover-preview',
    }), 'utf8').toString('base64url');

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-cover-preview',
      authorId: 'user-cover-preview',
      status: 'draft',
      visibility: 'private',
    });
    generationTaskService.findLatestArtifactForTask.mockResolvedValue({
      gameId: 'game-cover-preview',
      contentType: 'image/jpeg',
      payloadText: Buffer.from('fake-image').toString('base64'),
      metadata: { encoding: 'base64', truncated: false },
    });

    const cover = await service.getGameCoverContent('game-cover-preview', {
      previewToken,
      taskId: 'task-cover-preview',
    });

    expect(cover.contentType).toBe('image/jpeg');
    expect(cover.cacheControl).toBe('private, no-store');
    expect(cover.buffer.toString()).toBe('fake-image');
  });

  it('serves the live bundle cover artifact instead of the newest game artifact for public cover requests', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-cover-live',
      authorId: 'user-cover-live',
      status: 'published',
      visibility: 'public',
      version: 2,
        thumbnailUrl: 'https://gamevallies.com/api/v1/games/game-cover-live/cover?taskId=task-old&v=1',
    });
    bundleService.getBundle.mockResolvedValue({
      gameId: 'game-cover-live',
      version: 2,
      htmlCode: '<!DOCTYPE html><html><body>live</body></html>',
      metadata: {
        coverTaskId: 'task-live',
          coverUrl: 'https://gamevallies.com/api/v1/games/game-cover-live/cover?taskId=task-live&v=2',
      },
    });
    generationTaskService.findLatestArtifactForTask.mockImplementation(async (taskId: string) => (
      taskId === 'task-live'
        ? {
            gameId: 'game-cover-live',
            contentType: 'image/jpeg',
            payloadText: Buffer.from('live-cover').toString('base64'),
            metadata: { encoding: 'base64', truncated: false },
          }
        : null
    ));
    generationTaskService.findLatestArtifactForGame.mockResolvedValue({
      gameId: 'game-cover-live',
      contentType: 'image/jpeg',
      payloadText: Buffer.from('candidate-cover').toString('base64'),
      metadata: { encoding: 'base64', truncated: false },
    });

    const cover = await service.getGameCoverContent('game-cover-live');

    expect(cover.buffer.toString()).toBe('live-cover');
    expect(cover.cacheControl).toBe('public, max-age=300');
    expect(bundleService.getBundle).toHaveBeenCalledWith('game-cover-live', 2);
    expect(generationTaskService.findLatestArtifactForTask).toHaveBeenCalledWith('task-live', 'cover_image');
    expect(generationTaskService.findLatestArtifactForGame).not.toHaveBeenCalled();
  });

  it('serves bundle-linked cover artifacts directly by artifact id', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-cover-artifact',
      authorId: 'user-cover-artifact',
      status: 'published',
      visibility: 'public',
      version: 3,
      forkedFrom: null,
      thumbnailUrl: 'https://gamevallies.com/api/v1/games/game-cover-artifact/cover?v=3',
    });
    bundleService.getBundle.mockResolvedValue({
      gameId: 'game-cover-artifact',
      version: 3,
      htmlCode: '<!DOCTYPE html><html><body>cover</body></html>',
      metadata: {
        coverArtifactId: 'artifact-cover-direct',
      },
    });
    generationTaskService.findArtifactById.mockResolvedValue({
      id: 'artifact-cover-direct',
      gameId: 'game-cover-artifact',
      contentType: 'image/jpeg',
      payloadText: Buffer.from('artifact-cover').toString('base64'),
      metadata: { encoding: 'base64', truncated: false },
    });

    const cover = await service.getGameCoverContent('game-cover-artifact');

    expect(cover.buffer.toString()).toBe('artifact-cover');
    expect(cover.cacheControl).toBe('public, max-age=300');
    expect(generationTaskService.findArtifactById).toHaveBeenCalledWith('artifact-cover-direct');
    expect(generationTaskService.findLatestArtifactForTask).not.toHaveBeenCalled();
  });

  it('serves source-game cover artifacts for published forks that still reference the parent cover task', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-cover-fork',
      authorId: 'user-cover-fork',
      status: 'published',
      visibility: 'public',
      version: 1,
      forkedFrom: 'game-cover-parent',
      thumbnailUrl: 'https://gamevallies.com/api/v1/games/game-cover-fork/cover?taskId=task-parent-cover&v=1',
    });
    bundleService.getBundle.mockResolvedValue({
      gameId: 'game-cover-fork',
      version: 1,
      htmlCode: '<!DOCTYPE html><html><body>fork</body></html>',
      metadata: {
        coverTaskId: 'task-parent-cover',
      },
    });
    generationTaskService.findLatestArtifactForTask.mockResolvedValue({
      gameId: 'game-cover-parent',
      contentType: 'image/jpeg',
      payloadText: Buffer.from('parent-cover').toString('base64'),
      metadata: { encoding: 'base64', truncated: false },
    });

    const cover = await service.getGameCoverContent('game-cover-fork');

    expect(cover.buffer.toString()).toBe('parent-cover');
    expect(cover.cacheControl).toBe('public, max-age=300');
    expect(generationTaskService.findLatestArtifactForTask).toHaveBeenCalledWith('task-parent-cover', 'cover_image');
  });

  it('supports the published v2 iterate journey with live version promotion on publish', async () => {
    let gameState: any = {
      id: 'game-v2-journey-iter',
      title: 'Journey Two',
      description: 'runner',
      authorId: 'user-v2-journey',
      status: 'published',
      version: 1,
      visibility: 'public',
      canPlay: true,
      requireSubscription: false,
      accessGrantSource: GameAccessGrantSource.free_quota,
      accessGrantSubscriptionId: null,
      gameType: 'runner',
      qualityScore: 90,
      tags: ['runner'],
      forkedFrom: null,
    };
    const taskState = {
      id: 'task-v2-journey-iterate',
      status: 'running',
      cancelRequested: false,
      gameId: 'game-v2-journey-iter',
    };
    const bundles = new Map<number, any>([
      [1, {
        gameId: 'game-v2-journey-iter',
        version: 1,
        htmlCode: '<!DOCTYPE html><html><head><title>Journey Two</title></head><body>live-v1</body></html>',
        previewUrl: 'https://gamevallies.com/games/game-v2-journey-iter/preview',
        metadata: {},
      }],
    ]);

    prisma.generationTask.findUnique.mockImplementation(({ where, select }: any) => {
      if (where?.id !== taskState.id) {
        return Promise.resolve(null);
      }
      if (select?.metadata) {
        return Promise.resolve({
          metadata: {
            baseStatus: 'published',
          },
        });
      }
      if (select?.cancelRequested) {
        return Promise.resolve({
          status: taskState.status,
          cancelRequested: taskState.cancelRequested,
          gameId: taskState.gameId,
        });
      }
      return Promise.resolve({ status: taskState.status });
    });
    prisma.generationTask.update.mockResolvedValue({});
    prisma.game.findUnique.mockImplementation(({ where, select }: any) => {
      if (!gameState || where?.id !== gameState.id) {
        return Promise.resolve(null);
      }
      if (select) {
        const projection: any = {};
        Object.keys(select).forEach((key) => {
          projection[key] = gameState[key];
        });
        return Promise.resolve(projection);
      }
      return Promise.resolve({ ...gameState });
    });
    prisma.game.update.mockImplementation(({ data }: any) => {
      gameState = { ...gameState, ...data };
      return Promise.resolve({ ...gameState });
    });
    prisma.game.updateMany.mockImplementation(({ where, data }: any) => {
      if (
        gameState
        && where?.id === gameState.id
        && (where?.version === undefined || where.version === gameState.version)
        && (where?.status === undefined || where.status === gameState.status)
      ) {
        gameState = { ...gameState, ...data };
        return Promise.resolve({ count: 1 });
      }
      return Promise.resolve({ count: 0 });
    });
    bundleService.getBundle.mockImplementation(async (gameId: string, version: number) => {
      if (gameId !== gameState?.id) {
        return null;
      }
      return bundles.get(version) ?? null;
    });
    bundleService.getLatestBundle.mockImplementation(async (gameId: string) => {
      if (gameId !== gameState?.id || bundles.size === 0) {
        return null;
      }
      const version = Math.max(...Array.from(bundles.keys()));
      return bundles.get(version) ?? null;
    });
    bundleService.saveBundle.mockImplementation(async (payload: any) => {
      bundles.set(payload.version, {
        gameId: payload.gameId,
        version: payload.version,
        htmlCode: payload.htmlCode,
        previewUrl: payload.previewUrl,
        metadata: payload.metadata,
      });
    });
    mockAsyncSuccess('upstream-v2-journey-iterate', {
      html_code: '<!DOCTYPE html><html><head><title>Journey Two</title></head><body>candidate-v2</body></html>',
      iteration_type: 'element_change',
      generation_time_ms: 900,
      qa_retries: 0,
      iteration_retries: 0,
      primary_artifact_id: 'artifact-journey-iterate',
    });

    await (service as any).executeIterationTask(
      'game-v2-journey-iter',
      'user-v2-journey',
      'make it faster',
      2,
      [],
      '<!DOCTYPE html><html><head><title>Journey Two</title></head><body>live-v1</body></html>',
      900,
      taskState.id,
      'cn_shanghai',
      {
        pipelineVersion: 'v2',
        game: { ...gameState },
      },
    );

    expect(gameState.status).toBe('published');
    expect(gameState.version).toBe(1);

    const publicBeforePromote = await service.getPlayData('game-v2-journey-iter');
    expect(publicBeforePromote).toContain('live-v1');

    const authorReview = await service.getPlayableHtml('game-v2-journey-iter', 'user-v2-journey');
    expect(authorReview).toContain('candidate-v2');

    await service.publish('game-v2-journey-iter', 'user-v2-journey', {
      title: 'Journey Two',
      description: 'runner',
      tags: ['runner'],
      gameType: 'runner',
    } as any);

    const publicAfterPromote = await service.getPlayData('game-v2-journey-iter');
    expect(publicAfterPromote).toContain('candidate-v2');
    expect(gameState.version).toBe(2);
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
      previewUrl: expect.stringContaining('https://gamevallies.com/games/game-status/preview?previewToken='),
      gameUrl: expect.stringContaining('https://gamevallies.com/games/game-status/index.html?previewToken='),
      failedReason: 'Generated code failed QA',
      retryCount: 2,
    }));
  });

  it('blocks iteration when another generation task is still active', async () => {
    prisma.game.findUnique
      .mockResolvedValueOnce({
        id: 'game-active',
        authorId: 'user-active',
        version: 1,
        status: 'draft',
      })
      .mockResolvedValueOnce({
        id: 'game-active',
        authorId: 'user-active',
        status: 'generating',
        version: 1,
      });
    prisma.generationTask.findFirst.mockResolvedValue({
      id: 'task-active',
      gameId: 'game-active',
      userId: 'user-active',
      status: 'running',
      timeoutS: 600,
      createdAt: new Date(),
      updatedAt: new Date(),
    });

    await expect(service.iterate('game-active', 'user-active', {
      feedback: 'make it faster',
    } as any)).rejects.toBeInstanceOf(ConflictException);
  });

  it('rejects iteration when the game row was already claimed by another concurrent request', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-race',
      authorId: 'user-race',
      version: 3,
      status: 'draft',
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    prisma.game.updateMany.mockResolvedValue({ count: 0 });
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>old</body></html>',
    });
    bundleService.getBundleHistory.mockResolvedValue([]);
    generationTaskService.getLatestTaskForGame.mockResolvedValue(null);

    await expect(service.iterate('game-race', 'user-race', {
      feedback: 'make it faster',
    } as any)).rejects.toBeInstanceOf(ConflictException);
  });

  it('cancels the upstream ai task before marking the local task as canceled', async () => {
    const activeTask = {
      id: 'task-cancel',
      gameId: 'game-cancel',
      userId: 'user-cancel',
      taskType: 'pipeline_run',
      status: 'running',
      region: 'cn_shanghai',
      upstreamTaskId: 'upstream-cancel',
      retryCount: 0,
      progressStage: 'code_generating',
      progressPct: 60,
      progressMessage: 'working',
      game: {
        id: 'game-cancel',
        authorId: 'user-cancel',
        status: 'generating',
        publishedAt: null,
        failedStage: null,
        failedReason: null,
        retryCount: 0,
        accessGrantSource: GameAccessGrantSource.none,
        accessGrantSubscriptionId: null,
      },
    };
    prisma.generationTask.findUnique
      .mockResolvedValueOnce(activeTask)
      .mockResolvedValueOnce(activeTask);
    prisma.generationTask.update.mockResolvedValue({
      ...activeTask,
      status: 'canceled',
      cancelRequested: true,
    });
    mockedAxios.post.mockResolvedValue({ data: { task_id: 'upstream-cancel', status: 'canceled' } } as any);

    await service.terminateTask('task-cancel', { userId: 'user-cancel' });

    expect(mockedAxios.post).toHaveBeenCalledWith(
      'http://ai-engine.test/api/v1/ai/tasks/upstream-cancel/cancel',
      {},
      expect.objectContaining({ timeout: 10000 }),
    );
    expect(prisma.generationTask.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'task-cancel' },
    }));
  });

  it('serves preview html for public published games from the live bundle version', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-preview',
      version: 3,
      status: 'published',
      visibility: 'public',
    });
    bundleService.getBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>live-preview</body></html>',
    });
    statsService.incrementPlayCount.mockResolvedValue(undefined);

    await expect(service.getPlayData('game-preview')).resolves.toContain('live-preview');
    expect(bundleService.getBundle).toHaveBeenCalledWith('game-preview', 3);
    expect(bundleService.getLatestBundle).not.toHaveBeenCalled();
    expect(statsService.incrementPlayCount).toHaveBeenCalledWith('game-preview');
  });

  it('serves preview html for unlisted published games from the live bundle version', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-preview-unlisted',
      version: 2,
      status: 'published',
      visibility: 'unlisted',
    });
    bundleService.getBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>unlisted-preview</body></html>',
    });
    statsService.incrementPlayCount.mockResolvedValue(undefined);

    await expect(service.getPlayData('game-preview-unlisted')).resolves.toContain('unlisted-preview');
    expect(bundleService.getBundle).toHaveBeenCalledWith('game-preview-unlisted', 2);
    expect(bundleService.getLatestBundle).not.toHaveBeenCalled();
    expect(statsService.incrementPlayCount).toHaveBeenCalledWith('game-preview-unlisted');
  });

  it('blocks preview html for public draft games before they are published', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-preview-draft',
      status: 'draft',
      visibility: 'public',
      canPlay: true,
    });
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>preview</body></html>',
    });

    await expect(service.getPlayData('game-preview-draft')).rejects.toBeInstanceOf(NotFoundException);
    expect(statsService.incrementPlayCount).not.toHaveBeenCalled();
  });

  it('allows author preview tokens to open draft game previews', async () => {
    const previewToken = Buffer.from(JSON.stringify({
      type: 'game_preview',
      sub: 'user-preview-author',
      gameId: 'game-preview-author',
    }), 'utf8').toString('base64url');

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-preview-author',
      authorId: 'user-preview-author',
      status: 'draft',
      visibility: 'private',
      canPlay: true,
    });
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>author-preview</body></html>',
    });
    statsService.incrementPlayCount.mockResolvedValue(undefined);

    await expect(service.getPlayData('game-preview-author', previewToken)).resolves.toContain('author-preview');
    expect(bundleService.getLatestBundle).toHaveBeenCalledWith('game-preview-author');
    expect(bundleService.getBundle).not.toHaveBeenCalled();
    expect(statsService.incrementPlayCount).toHaveBeenCalledWith('game-preview-author');
  });

  it('prefers the latest candidate bundle when an author opens a signed preview link', async () => {
    const previewToken = Buffer.from(JSON.stringify({
      type: 'game_preview',
      sub: 'user-preview-author',
      gameId: 'game-preview-candidate',
    }), 'utf8').toString('base64url');

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-preview-candidate',
      authorId: 'user-preview-author',
      version: 2,
      status: 'published',
      visibility: 'public',
      canPlay: true,
    });
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>candidate-preview</body></html>',
    });
    statsService.incrementPlayCount.mockResolvedValue(undefined);

    await expect(service.getPlayData('game-preview-candidate', previewToken)).resolves.toContain('candidate-preview');
    expect(bundleService.getLatestBundle).toHaveBeenCalledWith('game-preview-candidate');
    expect(bundleService.getBundle).not.toHaveBeenCalled();
  });

  it('hides private published games from the public detail endpoint', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-private-detail',
      status: 'published',
      visibility: 'private',
      author: {
        id: 'author-private',
        username: 'author',
        displayName: 'Author',
        avatarUrl: '',
      },
    });

    await expect(service.findById('game-private-detail')).rejects.toBeInstanceOf(NotFoundException);
  });

  it('hides private published games from public share metadata', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-private-share',
      status: 'published',
      visibility: 'private',
      title: 'Private Game',
      description: 'hidden',
      thumbnailUrl: null,
      playCount: 0,
      likeCount: 0,
      qualityScore: 0,
      author: {
        username: 'author-private',
      },
    });

    await expect(service.getShareData('game-private-share')).rejects.toBeInstanceOf(NotFoundException);
  });

  it('keeps unlisted published games hidden from the public detail endpoint', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-unlisted-detail',
      status: 'published',
      visibility: 'unlisted',
      author: {
        id: 'author-unlisted',
        username: 'author',
        displayName: 'Author',
        avatarUrl: '',
      },
    });

    await expect(service.findById('game-unlisted-detail')).rejects.toBeInstanceOf(NotFoundException);
  });

  it('keeps unlisted published games hidden from public share metadata', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-unlisted-share',
      status: 'published',
      visibility: 'unlisted',
      title: 'Unlisted Game',
      description: 'hidden',
      thumbnailUrl: null,
      playCount: 0,
      likeCount: 0,
      qualityScore: 0,
      author: {
        username: 'author-unlisted',
      },
    });

    await expect(service.getShareData('game-unlisted-share')).rejects.toBeInstanceOf(NotFoundException);
  });

  it('only queries public visibility when listing published games for explore', async () => {
    prisma.game.findMany.mockResolvedValue([]);
    prisma.game.count.mockResolvedValue(0);

    await service.getGamesByStatus('published', 1, 10, 'runner');

    expect(prisma.game.findMany).toHaveBeenCalledWith(expect.objectContaining({
      where: expect.objectContaining({
        status: 'published',
        visibility: 'public',
      }),
    }));
    expect(prisma.game.count).toHaveBeenCalledWith(expect.objectContaining({
      where: expect.objectContaining({
        status: 'published',
        visibility: 'public',
      }),
    }));
  });

  it('blocks published private games from non-author play access', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-private',
      authorId: 'author-private',
      status: 'published',
      visibility: 'private',
    });
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>private</body></html>',
    });

    await expect(service.getPlayableHtml('game-private', 'viewer-private')).rejects.toBeInstanceOf(ForbiddenException);
    expect(statsService.incrementPlayCount).not.toHaveBeenCalled();
  });

  it('lets the author preview the latest bundle for a published game during iteration review', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-author-preview',
      authorId: 'author-preview',
      status: 'published',
      visibility: 'public',
      version: 2,
    });
    bundleService.getLatestBundle.mockResolvedValue({
      version: 3,
      htmlCode: '<!DOCTYPE html><html><body>latest-review</body></html>',
    });

    await expect(service.getPlayableHtml('game-author-preview', 'author-preview')).resolves.toContain('latest-review');
    expect(bundleService.getLatestBundle).toHaveBeenCalledWith('game-author-preview');
    expect(bundleService.getBundle).not.toHaveBeenCalled();
    expect(statsService.incrementPlayCount).toHaveBeenCalledWith('game-author-preview');
  });

  it('lets the author preview an unpublished locked draft while it is still private', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-author-locked-draft',
      authorId: 'author-preview',
      status: 'draft',
      visibility: 'private',
      canPlay: false,
      version: 1,
    });
    bundleService.getLatestBundle.mockResolvedValue({
      version: 1,
      htmlCode: '<!DOCTYPE html><html><body>draft-review</body></html>',
    });

    await expect(service.getPlayableHtml('game-author-locked-draft', 'author-preview')).resolves.toContain('draft-review');
    expect(bundleService.getLatestBundle).toHaveBeenCalledWith('game-author-locked-draft');
    expect(statsService.incrementPlayCount).toHaveBeenCalledWith('game-author-locked-draft');
  });

  it('rejects publishing when no playable bundle exists', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-publish',
      authorId: 'user-publish',
      status: 'draft',
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue(null);

    await expect(service.publish('game-publish', 'user-publish', {} as any)).rejects.toBeInstanceOf(BadRequestException);
    expect(prisma.game.update).not.toHaveBeenCalled();
  });

  it('publishes forked draft games as public so preview can be opened', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-fork-publish',
      authorId: 'user-publish',
      status: 'draft',
      visibility: 'private',
      forkedFrom: 'parent-game',
      title: 'Fork Draft',
      description: 'Fork draft description',
      tags: ['fork'],
      gameType: 'runner',
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>fork</body></html>',
    });
    prisma.game.update.mockResolvedValue({
      id: 'game-fork-publish',
      author: { id: 'user-publish', username: 'publisher', avatarUrl: '' },
    });

    await service.publish('game-fork-publish', 'user-publish', {} as any);

    expect(prisma.game.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'game-fork-publish' },
      data: expect.objectContaining({
        status: 'published',
        visibility: 'public',
      }),
    }));
  });

  it('defaults first publish to public for a non-fork draft game', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-private-publish',
      authorId: 'user-publish',
      status: 'draft',
      visibility: 'private',
      forkedFrom: null,
      title: 'Private Draft',
      description: 'Private draft description',
      tags: [],
      gameType: 'runner',
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>private</body></html>',
    });
    prisma.game.update.mockResolvedValue({
      id: 'game-private-publish',
      author: { id: 'user-publish', username: 'publisher', avatarUrl: '' },
    });

    await service.publish('game-private-publish', 'user-publish', {} as any);

    expect(prisma.game.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'game-private-publish' },
      data: expect.objectContaining({
        status: 'published',
        visibility: 'public',
      }),
    }));
  });

  it('promotes the newest bundle version only when the author explicitly publishes', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-republish',
      authorId: 'user-publish',
      status: 'published',
      visibility: 'public',
      version: 2,
      forkedFrom: null,
      title: 'Published Game',
      description: 'Published description',
      tags: ['runner'],
      gameType: 'runner',
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      version: 3,
      htmlCode: '<!DOCTYPE html><html><body>candidate</body></html>',
    });
    prisma.game.update.mockResolvedValue({
      id: 'game-republish',
      author: { id: 'user-publish', username: 'publisher', avatarUrl: '' },
    });

    await service.publish('game-republish', 'user-publish', {} as any);

    expect(prisma.game.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'game-republish' },
      data: expect.objectContaining({
        version: 3,
        status: 'published',
      }),
    }));
  });

  it('updates thumbnailUrl to the promoted bundle cover when the author publishes a newer version', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-republish-cover',
      authorId: 'user-publish',
      status: 'published',
      visibility: 'public',
      version: 2,
        thumbnailUrl: 'https://gamevallies.com/api/v1/games/game-republish-cover/cover?taskId=task-old&v=2',
      forkedFrom: null,
      title: 'Published Game',
      description: 'Published description',
      tags: ['runner'],
      gameType: 'runner',
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      gameId: 'game-republish-cover',
      version: 3,
      htmlCode: '<!DOCTYPE html><html><body>candidate</body></html>',
      metadata: {
        coverTaskId: 'task-new',
          coverUrl: 'https://gamevallies.com/api/v1/games/game-republish-cover/cover?taskId=task-new&v=3',
      },
    });
    prisma.game.update.mockResolvedValue({
      id: 'game-republish-cover',
      author: { id: 'user-publish', username: 'publisher', avatarUrl: '' },
    });

    await service.publish('game-republish-cover', 'user-publish', {} as any);

    expect(prisma.game.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'game-republish-cover' },
      data: expect.objectContaining({
        version: 3,
        thumbnailUrl: 'https://gamevallies.com/api/v1/games/game-republish-cover/cover?taskId=task-new&v=3',
        status: 'published',
      }),
    }));
  });

  it('rejects iteration when the source game has no playable bundle', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-no-bundle',
      authorId: 'user-no-bundle',
      version: 1,
      status: 'draft',
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue(null);

    await expect(service.iterate('game-no-bundle', 'user-no-bundle', {
      feedback: 'make it faster',
    } as any)).rejects.toBeInstanceOf(BadRequestException);
    expect(generationTaskService.createTask).not.toHaveBeenCalled();
  });

  it('keeps published games published while an iteration task is running', async () => {
    jest.useFakeTimers();
    const executeIterationTaskSpy = jest
      .spyOn(service as any, 'executeIterationTask')
      .mockResolvedValue(undefined);

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-published',
      authorId: 'user-published',
      version: 2,
      status: 'published',
      updatedAt: new Date('2026-03-23T10:00:00.000Z'),
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>old</body></html>',
    });
    bundleService.getBundleHistory.mockResolvedValue([]);
    generationTaskService.getLatestTaskForGame.mockResolvedValue(null);

    const result = await service.iterate('game-published', 'user-published', {
      feedback: 'make it faster',
    } as any);

    expect(prisma.game.updateMany).toHaveBeenCalledWith(expect.objectContaining({
      where: expect.objectContaining({
        id: 'game-published',
        authorId: 'user-published',
        version: 2,
        status: 'published',
        updatedAt: expect.any(Date),
      }),
      data: expect.objectContaining({
        status: 'published',
      }),
    }));
    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      metadata: expect.objectContaining({
        baseStatus: 'published',
      }),
    }));
    expect(result.status).toBe('iterating');

    jest.runOnlyPendingTimers();
    executeIterationTaskSpy.mockRestore();
    jest.useRealTimers();
  });

  it('allocates a fresh candidate version when a published game already has an unpublished review bundle', async () => {
    jest.useFakeTimers();
    const executeIterationTaskSpy = jest
      .spyOn(service as any, 'executeIterationTask')
      .mockResolvedValue(undefined);

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-published-candidate',
      authorId: 'user-published-candidate',
      version: 4,
      status: 'published',
      updatedAt: new Date('2026-03-30T06:00:00.000Z'),
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      version: 5,
      htmlCode: '<!DOCTYPE html><html><body>candidate-v5</body></html>',
      metadata: {},
    });
    bundleService.getBundleHistory.mockResolvedValue([
      {
        version: 5,
        htmlCode: '<!DOCTYPE html><html><body>candidate-v5</body></html>',
        metadata: {},
      },
    ]);
    generationTaskService.getLatestTaskForGame.mockResolvedValue(null);

    const result = await service.iterate('game-published-candidate', 'user-published-candidate', {
      feedback: 'add a sixth stage and more polish',
    } as any);

    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      gameId: 'game-published-candidate',
      version: 6,
      metadata: expect.objectContaining({
        baseStatus: 'published',
      }),
    }));
    jest.runOnlyPendingTimers();
    expect(executeIterationTaskSpy).toHaveBeenCalledWith(
      'game-published-candidate',
      'user-published-candidate',
      'add a sixth stage and more polish',
      6,
      expect.any(Array),
      expect.stringContaining('candidate-v5'),
      expect.any(Number),
      expect.any(String),
      expect.any(String),
      expect.objectContaining({
        game: expect.objectContaining({
          version: 4,
          status: 'published',
        }),
      }),
    );
    expect(result.version).toBe(6);

    executeIterationTaskSpy.mockRestore();
    jest.useRealTimers();
  });

  it('passes source spec and bundle history context into v2 iteration execution', async () => {
    jest.useFakeTimers();
    const executeIterationTaskSpy = jest
      .spyOn(service as any, 'executeIterationTask')
      .mockResolvedValue(undefined);

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-history-aware',
      authorId: 'user-history-aware',
      title: '逮小猪',
      gameType: 'runner',
      version: 2,
      status: 'draft',
      updatedAt: new Date('2026-03-23T10:00:00.000Z'),
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      version: 2,
      htmlCode: '<!DOCTYPE html><html><head><title>逮小猪</title></head><body>old</body></html>',
      metadata: {
        feedback: '把障碍再清楚一些',
        iterationType: 'element_change',
        gameSpec: {
          game_type: 'runner',
          intent_summary: '逮住小猪并躲开障碍',
          ui_language: 'zh-CN',
        },
      },
    });
    bundleService.getBundleHistory.mockResolvedValue([
      {
        version: 1,
        createdAt: new Date('2026-03-22T10:00:00.000Z'),
        metadata: {
          feedback: '做一个逮小猪游戏',
          gameSpec: {
            game_type: 'runner',
            intent_summary: '逮住小猪并躲开障碍',
            ui_language: 'zh-CN',
          },
        },
      },
      {
        version: 2,
        createdAt: new Date('2026-03-23T10:00:00.000Z'),
        metadata: {
          feedback: '把障碍再清楚一些',
          iterationType: 'element_change',
        },
      },
    ]);
    generationTaskService.getLatestTaskForGame.mockResolvedValue(null);

    await service.iterate('game-history-aware', 'user-history-aware', {
      feedback: '继续增加关卡，设置5个关卡',
    } as any);

    jest.runOnlyPendingTimers();
    expect(executeIterationTaskSpy).toHaveBeenCalledWith(
      'game-history-aware',
      'user-history-aware',
      '继续增加关卡，设置5个关卡',
      3,
      expect.any(Array),
      expect.stringContaining('<title>逮小猪</title>'),
      expect.any(Number),
      expect.any(String),
      expect.any(String),
      expect.objectContaining({
        sourceSpec: expect.objectContaining({
          game_type: 'runner',
          ui_language: 'zh-CN',
        }),
        sourceBundleContext: expect.objectContaining({
          title: '逮小猪',
          latest_game_type: 'runner',
          latest_feedback: '把障碍再清楚一些',
          recent_revisions: expect.any(Array),
        }),
      }),
    );
    executeIterationTaskSpy.mockRestore();
    jest.useRealTimers();
  });

  it('persists the iterate source snapshot before queue dispatch', async () => {
    generationQueueService.enqueueJob.mockResolvedValue(true);

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-iter-snapshot',
      authorId: 'user-iter-snapshot',
      version: 2,
      status: 'published',
      updatedAt: new Date('2026-04-04T10:00:00.000Z'),
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      version: 2,
      htmlCode: '<!DOCTYPE html><html><body>snapshot-source</body></html>',
      metadata: {},
    });
    bundleService.getBundleHistory.mockResolvedValue([]);
    generationTaskService.getLatestTaskForGame.mockResolvedValue(null);

    await service.iterate('game-iter-snapshot', 'user-iter-snapshot', {
      feedback: 'make the pacing tighter',
    } as any);

    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      metadata: expect.objectContaining({
        currentBundleVersion: 2,
      }),
    }));
    expect(generationTaskService.createArtifact).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'game-iter-snapshot:pipeline_iterate',
      gameId: 'game-iter-snapshot',
      userId: 'user-iter-snapshot',
      artifactType: 'iteration_source_code',
      contentType: 'text/html',
      payload: '<!DOCTYPE html><html><body>snapshot-source</body></html>',
      metadata: expect.objectContaining({
        bundleVersion: 2,
        source: 'iterate_request_snapshot',
      }),
    }));
  });

  it('reconciles queued pipeline jobs that already have an upstream task id instead of resubmitting them', async () => {
    prisma.generationTask.findUnique.mockResolvedValue({
      id: 'task-existing-upstream',
      gameId: 'game-existing-upstream',
      userId: 'user-existing-upstream',
      status: 'running',
      timeoutS: 900,
      upstreamTaskId: 'upstream-existing',
      metadata: {
        description: 'runner',
      },
      game: {
        id: 'game-existing-upstream',
        title: 'Existing upstream game',
        description: 'runner',
        canPlay: true,
        requireSubscription: false,
        accessGrantSource: GameAccessGrantSource.none,
        accessGrantSubscriptionId: null,
      },
    });

    const reconcileSpy = jest.spyOn(service, 'reconcileGenerationTask').mockResolvedValue({} as any);
    const executePipelineTaskSpy = jest.spyOn(service as any, 'executePipelineTask').mockResolvedValue(undefined);

    await service.processQueuedPipelineRunTask('task-existing-upstream');

    expect(reconcileSpy).toHaveBeenCalledWith(expect.objectContaining({
      id: 'task-existing-upstream',
      upstreamTaskId: 'upstream-existing',
    }));
    expect(executePipelineTaskSpy).not.toHaveBeenCalled();
  });

  it('replays queued iteration jobs from the persisted request snapshot instead of the latest bundle', async () => {
    prisma.generationTask.findUnique.mockResolvedValue({
      id: 'task-iter-queued',
      gameId: 'game-iter-queued',
      userId: 'user-iter-queued',
      status: 'queued',
      timeoutS: 900,
      version: 3,
      upstreamTaskId: null,
      metadata: {
        feedback: 'make it tighter',
        conversation: [{ role: 'user', content: 'make it tighter' }],
        pipelineVersion: 'v2',
        currentBundleVersion: 2,
      },
      game: {
        id: 'game-iter-queued',
        title: 'Queued iterate game',
        description: 'runner',
        version: 2,
        status: 'published',
        visibility: 'public',
        canPlay: true,
        requireSubscription: false,
        accessGrantSource: GameAccessGrantSource.none,
        accessGrantSubscriptionId: null,
        forkedFrom: null,
      },
    });
    generationTaskService.findLatestArtifactForTask.mockResolvedValue({
      payloadText: '<!DOCTYPE html><html><body>persisted-source</body></html>',
      metadata: { truncated: false },
      contentType: 'text/html',
    });
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>latest-source</body></html>',
    });

    const executeIterationTaskSpy = jest.spyOn(service as any, 'executeIterationTask').mockResolvedValue(undefined);

    await service.processQueuedIterationTask('task-iter-queued');

    expect(executeIterationTaskSpy).toHaveBeenCalledWith(
      'game-iter-queued',
      'user-iter-queued',
      'make it tighter',
      3,
      [{ role: 'user', content: 'make it tighter' }],
      '<!DOCTYPE html><html><body>persisted-source</body></html>',
      900,
      'task-iter-queued',
      undefined,
      expect.objectContaining({
        game: expect.objectContaining({
          version: 2,
          status: 'published',
        }),
      }),
    );
  });

  it('inherits persisted landscape orientation when scheduling v2 iteration', async () => {
    jest.useFakeTimers();
    const executeIterationTaskSpy = jest
      .spyOn(service as any, 'executeIterationTask')
      .mockResolvedValue(undefined);

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-landscape-iter',
      authorId: 'user-landscape-iter',
      title: 'Wide Runner',
      gameType: 'runner',
      version: 2,
      status: 'draft',
      updatedAt: new Date('2026-03-23T10:00:00.000Z'),
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      version: 2,
      htmlCode: '<!DOCTYPE html><html><head><title>Wide Runner</title></head><body>old</body></html>',
      metadata: {
        requestedOrientation: 'landscape',
        runtimeOrientation: 'landscape_first',
        gameSpec: {
          game_type: 'runner',
        },
      },
    });
    bundleService.getBundleHistory.mockResolvedValue([
      {
        version: 1,
        createdAt: new Date('2026-03-22T10:00:00.000Z'),
        metadata: {
          requestedOrientation: 'landscape',
          runtimeOrientation: 'landscape_first',
          gameSpec: {
            game_type: 'runner',
          },
        },
      },
    ]);
    generationTaskService.getLatestTaskForGame.mockResolvedValue(null);

    await service.iterate('game-landscape-iter', 'user-landscape-iter', {
      feedback: 'add wider lanes and co-op hazards',
    } as any);

    jest.runOnlyPendingTimers();
    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      metadata: expect.objectContaining({
        orientation: 'landscape',
      }),
    }));
    expect(executeIterationTaskSpy).toHaveBeenCalledWith(
      'game-landscape-iter',
      'user-landscape-iter',
      'add wider lanes and co-op hazards',
      3,
      expect.any(Array),
      expect.stringContaining('<title>Wide Runner</title>'),
      expect.any(Number),
      expect.any(String),
      expect.any(String),
      expect.objectContaining({
        orientation: 'landscape',
        runtimeContract: expect.objectContaining({
          canvas: expect.objectContaining({
            orientation: 'landscape_first',
          }),
          mobile_layout: expect.objectContaining({
            orientation: 'landscape_first',
          }),
        }),
        sourceBundleContext: expect.objectContaining({
          latest_orientation: 'landscape',
        }),
      }),
    );
    executeIterationTaskSpy.mockRestore();
    jest.useRealTimers();
  });

  it('clamps v2 iteration timeouts to at least 1800 seconds', async () => {
    jest.useFakeTimers();
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
        PIPELINE_TIMEOUT_S: '1800',
      };
      return values[key] ?? defaultValue;
    });
    const executeIterationTaskSpy = jest
      .spyOn(service as any, 'executeIterationTask')
      .mockResolvedValue(undefined);

    prisma.game.findUnique.mockResolvedValue({
      id: 'game-v2-iter-timeout',
      authorId: 'user-v2-iter-timeout',
      version: 2,
      status: 'published',
      updatedAt: new Date('2026-03-23T10:00:00.000Z'),
    });
    prisma.generationTask.findFirst.mockResolvedValue(null);
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<!DOCTYPE html><html><body>old</body></html>',
    });
    bundleService.getBundleHistory.mockResolvedValue([]);
    generationTaskService.getLatestTaskForGame.mockResolvedValue(null);

    await service.iterate('game-v2-iter-timeout', 'user-v2-iter-timeout', {
      feedback: 'add clearer combo feedback and speed up the pacing',
      timeoutS: 600,
    } as any);

    expect(generationTaskService.createTask).toHaveBeenCalledWith(expect.objectContaining({
      pipelineVersion: 'v2',
      timeoutS: 1800,
    }));

    jest.runOnlyPendingTimers();
    expect(executeIterationTaskSpy).toHaveBeenCalledWith(
      'game-v2-iter-timeout',
      'user-v2-iter-timeout',
      'add clearer combo feedback and speed up the pacing',
      3,
      expect.any(Array),
      '<!DOCTYPE html><html><body>old</body></html>',
      1800,
      expect.any(String),
      expect.any(String),
      expect.any(Object),
    );
    executeIterationTaskSpy.mockRestore();
    jest.useRealTimers();
  });

  it('restores published status when iteration fails', async () => {
    prisma.generationTask.findUnique
      .mockResolvedValueOnce({
        status: 'running',
      })
      .mockResolvedValueOnce({
        metadata: { baseStatus: 'published' },
      });
    prisma.game.findUnique
      .mockResolvedValueOnce({
        status: 'published',
        publishedAt: new Date('2026-03-23T10:00:00.000Z'),
      })
      .mockResolvedValueOnce({
        id: 'game-iter-fail',
        authorId: 'user-iter-fail',
        accessGrantSource: GameAccessGrantSource.none,
        accessGrantSubscriptionId: null,
      });
    prisma.game.update.mockResolvedValue({});

    await (service as any).failIterationTask({
      gameId: 'game-iter-fail',
      userId: 'user-iter-fail',
      taskId: 'task-iter-fail',
      error: new Error('boom'),
    });

    expect(prisma.game.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'game-iter-fail' },
      data: expect.objectContaining({
        status: 'published',
        failedStage: 'iteration',
        failedReason: 'boom',
      }),
    }));
    expect(generationTaskService.markFailed).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-iter-fail',
      failedStage: 'iteration',
      errorMessage: 'boom',
    }));
  });

  it('keeps the live version unchanged when a published iteration completes', async () => {
    const persistGeneratedGameResultSpy = jest
      .spyOn(service as any, 'persistGeneratedGameResult')
      .mockResolvedValue(undefined);
    const assertTaskCanPersistResultSpy = jest
      .spyOn(service as any, 'assertTaskCanPersistResult')
      .mockResolvedValue(undefined);
    generationTaskService.findLatestArtifactForTask.mockResolvedValue({
      gameId: 'game-published-live',
      contentType: 'image/jpeg',
      payloadText: 'ZmFrZS1pbWFnZS1kYXRh',
      metadata: { encoding: 'base64', truncated: false },
    });

    await (service as any).completeIterationTask({
      gameId: 'game-published-live',
      userId: 'user-published-live',
      feedback: 'make it faster',
      conversationHistory: [],
      nextVersion: 3,
      taskId: 'task-published-live',
      currentCode: '<!DOCTYPE html><html><body>old</body></html>',
      responseData: {
        html_code: '<!DOCTYPE html><html><body>new</body></html>',
        game_spec: {
          game_type: 'runner',
        },
      },
      baseStatus: 'published',
    });

    expect(persistGeneratedGameResultSpy).toHaveBeenCalledWith(expect.objectContaining({
      gameId: 'game-published-live',
      version: 3,
      metadata: expect.objectContaining({
        gameSpec: expect.objectContaining({
          game_type: 'runner',
        }),
      }),
      updateData: expect.objectContaining({
        status: 'published',
      }),
    }));
    const persistCall = persistGeneratedGameResultSpy.mock.calls[0]?.[0] as any;
    expect(persistCall.updateData.version).toBeUndefined();
    expect(persistCall.updateData.gameType).toBeUndefined();
    expect(persistCall.updateData.thumbnailUrl).toBeUndefined();
    expect(persistCall.metadata.coverUrl).toBe(
      'https://gamevallies.com/api/v1/games/game-published-live/cover?taskId=task-published-live&v=3',
    );
    expect(persistCall.metadata.coverTaskId).toBe('task-published-live');
    assertTaskCanPersistResultSpy.mockRestore();
    persistGeneratedGameResultSpy.mockRestore();
  });

  it('persists runtime qa warnings on published iteration candidates', async () => {
    const persistGeneratedGameResultSpy = jest
      .spyOn(service as any, 'persistGeneratedGameResult')
      .mockResolvedValue(undefined);
    const assertTaskCanPersistResultSpy = jest
      .spyOn(service as any, 'assertTaskCanPersistResult')
      .mockResolvedValue(undefined);

    await (service as any).completeIterationTask({
      gameId: 'game-published-warning',
      userId: 'user-published-warning',
      feedback: 'add five levels',
      conversationHistory: [],
      nextVersion: 4,
      taskId: 'task-published-warning',
      currentCode: '<!DOCTYPE html><html><body>old</body></html>',
      responseData: {
        html_code: '<!DOCTYPE html><html><body>candidate</body></html>',
        iteration_type: 'mechanic_change',
        qa_warnings: [
          {
            type: 'runtime_qa_unavailable',
            severity: 'warning',
            message: 'Runtime QA unavailable: runtime_qa_timeout:60.00s',
            kind: 'timeout',
            phase: 'overall',
            softFailed: true,
          },
        ],
        runtime_qa_report: {
          ran: false,
          unavailableReason: 'runtime_qa_timeout:60.00s',
          unavailableKind: 'timeout',
          unavailablePhase: 'overall',
          phaseMetrics: {
            total_elapsed_ms: 60000,
          },
        },
      },
      baseStatus: 'published',
    });

    expect(persistGeneratedGameResultSpy).toHaveBeenCalledWith(expect.objectContaining({
      metadata: expect.objectContaining({
        qaWarnings: expect.arrayContaining([
          expect.objectContaining({
            type: 'runtime_qa_unavailable',
            kind: 'timeout',
          }),
        ]),
        runtimeQaReport: expect.objectContaining({
          unavailableKind: 'timeout',
          unavailablePhase: 'overall',
        }),
      }),
    }));
    expect(generationTaskService.markSucceeded).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-published-warning',
      resultSummary: expect.objectContaining({
        qaWarningCount: 1,
        runtimeQaUnavailable: true,
        runtimeQaUnavailableKind: 'timeout',
        runtimeQaUnavailablePhase: 'overall',
        runtimeQaUnavailableReason: 'runtime_qa_timeout:60.00s',
      }),
    }));
    assertTaskCanPersistResultSpy.mockRestore();
    persistGeneratedGameResultSpy.mockRestore();
  });

  it('persists generation tier in prompt bundles and runtime contracts', async () => {
    const promptBundle = await (service as any).buildPromptBundleSnapshot(
      'create',
      'casual_arcade',
      'showcase',
    );
    const runtimeContract = await (service as any).buildDefaultRuntimeContract(
      'create',
      'casual_arcade',
      undefined,
      'showcase',
    );

    expect(promptBundle.layers.generation_tier).toBe('showcase');
    expect(runtimeContract.metadata.generation_tier).toBe('showcase');
  });

  it('includes generation tier in create v2 payload metadata', () => {
    const payload = (service as any).buildCreateV2Payload({
      gameId: 'game-tier-create',
      userId: 'user-tier-create',
      title: 'Showcase Game',
      description: 'build a premium arcade experience',
      executionRegion: 'cn_shanghai',
      timeoutS: 1200,
      generationTier: 'showcase',
      promptBundleSnapshot: {
        bundle_id: 'runtime-v2-default',
        bundle_version: 1,
        resolved_at: new Date().toISOString(),
        layers: {
          entrypoint: 'create',
          source: 'game-service',
          generation_tier: 'showcase',
        },
      },
      runtimeContract: {
        runtime_profile: 'casual_arcade',
        metadata: {
          generation_tier: 'showcase',
        },
      },
    });

    expect(payload.generation_tier).toBe('showcase');
    expect(payload.request_context.metadata.generation_tier).toBe('showcase');
    expect(payload.normalized_request.generation_tier).toBe('showcase');
    expect(payload.metadata.generation_tier).toBe('showcase');
  });

  it('inherits generation tier from bundle history for iterate payloads', () => {
    const sourceBundleContext = (service as any).buildIterationSourceBundleContext({
      game: {
        id: 'game-tier-iter',
        title: 'Tiered Game',
        gameType: 'casual',
        version: 2,
      },
      latestBundle: {
        version: 2,
        metadata: {
          generationTier: 'showcase',
          gameSpec: {
            game_type: 'casual',
          },
        },
      },
      bundleHistory: [
        {
          version: 1,
          metadata: {
            generationTier: 'safe',
          },
        },
        {
          version: 2,
          metadata: {
            generationTier: 'showcase',
          },
        },
      ],
    });

    const payload = (service as any).buildIterateV2Payload({
      gameId: 'game-tier-iter',
      userId: 'user-tier-iter',
      feedback: 'make it more premium',
      conversationHistory: [],
      currentCode: '<!DOCTYPE html><html><body>old</body></html>',
      executionRegion: 'cn_shanghai',
      timeoutS: 1200,
      game: {
        status: 'draft',
        visibility: 'private',
        version: 2,
        canPlay: true,
        requireSubscription: false,
      },
      sourceSpec: {
        game_type: 'casual',
      },
      sourceBundleContext,
      promptBundleSnapshot: {
        bundle_id: 'runtime-v2-default',
        bundle_version: 1,
        resolved_at: new Date().toISOString(),
        layers: {
          entrypoint: 'iterate',
          source: 'game-service',
          generation_tier: 'showcase',
        },
      },
      runtimeContract: {
        runtime_profile: 'casual_arcade',
        metadata: {
          generation_tier: 'showcase',
        },
      },
    });

    expect(sourceBundleContext.latest_generation_tier).toBe('showcase');
    expect(payload.generation_tier).toBe('showcase');
    expect(payload.request_context.metadata.generation_tier).toBe('showcase');
    expect(payload.normalized_request.generation_tier).toBe('showcase');
  });
});
