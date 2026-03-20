import axios from 'axios';
import { ConfigService } from '@nestjs/config';
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

  beforeEach(() => {
    prisma = {
      game: {
        create: jest.fn(),
        findUnique: jest.fn(),
        update: jest.fn(),
        findMany: jest.fn(),
        count: jest.fn(),
      },
    };
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
    );
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('does not retry ai-engine 504 responses and persists structured failure context', async () => {
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
    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-504' },
      data: expect.objectContaining({
        status: 'failed',
        failedStage: 'qa_checking',
        failedReason: 'Generated code failed QA',
        retryCount: 3,
        lastErrorAt: expect.any(Date),
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
});
