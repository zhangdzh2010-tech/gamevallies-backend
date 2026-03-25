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
      markRunning: jest.fn(async () => undefined),
      recordProgress: jest.fn(async () => undefined),
      markSucceeded: jest.fn(async () => undefined),
      markFailed: jest.fn(async () => undefined),
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
    prisma.generationTaskEvent.create.mockResolvedValue({});
    prisma.systemConfig.findMany.mockResolvedValue([]);
    prisma.userQuota.updateMany.mockResolvedValue({ count: 0 });
    prisma.promptBundle.findFirst.mockResolvedValue({
      id: 'runtime-v2-default',
      version: 1,
    });
    prisma.runtimeProfileCatalog.findMany.mockResolvedValue([
      {
        id: 'lane_runner',
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
        id: 'topdown_action',
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
        id: 'portrait_arcade',
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
        timeoutS: 1200,
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
        timeoutS: 1200,
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
      runtimeProfile: 'portrait_arcade',
      contractVersion: '1.0',
    }));

    await new Promise((resolve) => setImmediate(resolve));
    executePipelineTaskSpy.mockRestore();
  });

  it('clamps v2 create timeouts to at least 1200 seconds', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
        PIPELINE_TIMEOUT_S: '1200',
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
      timeoutS: 1200,
    }));

    await new Promise((resolve) => setImmediate(resolve));
    expect(executePipelineTaskSpy).toHaveBeenCalledWith(
      expect.any(String),
      'user-v2-timeout',
      'make a slow but valid v2 game',
      1200,
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
            profile_few_shot: 'lane_runner',
          }),
        }),
        runtime_contract: expect.objectContaining({
          runtime_profile: 'lane_runner',
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

  it('clamps v2 iteration timeouts to at least 1200 seconds', async () => {
    jest.useFakeTimers();
    (configService.get as jest.Mock).mockImplementation((key: string, defaultValue?: string) => {
      const values: Record<string, string> = {
        AI_ENGINE_URL: 'http://ai-engine.test',
        PUBLIC_API_BASE_URL: 'https://gamevallies.com',
        APP_URL: 'https://gamevallies.com',
        ADMIN_TOKEN: 'test-admin-token',
        PIPELINE_VERSION: 'v2',
        PIPELINE_TIMEOUT_S: '1200',
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
      timeoutS: 1200,
    }));

    jest.runOnlyPendingTimers();
    expect(executeIterationTaskSpy).toHaveBeenCalledWith(
      'game-v2-iter-timeout',
      'user-v2-iter-timeout',
      'add clearer combo feedback and speed up the pacing',
      3,
      expect.any(Array),
      '<!DOCTYPE html><html><body>old</body></html>',
      1200,
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
      },
      baseStatus: 'published',
    });

    expect(persistGeneratedGameResultSpy).toHaveBeenCalledWith(expect.objectContaining({
      gameId: 'game-published-live',
      version: 3,
      updateData: expect.objectContaining({
        status: 'published',
      }),
    }));
    const persistCall = persistGeneratedGameResultSpy.mock.calls[0]?.[0] as any;
    expect(persistCall.updateData.version).toBeUndefined();
    assertTaskCanPersistResultSpy.mockRestore();
    persistGeneratedGameResultSpy.mockRestore();
  });
});
