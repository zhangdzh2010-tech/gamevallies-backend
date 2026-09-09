import { GenerationTaskEventType, GenerationTaskStatus } from '@prisma/client';
import { GenerationTaskService } from '../src/game/generation-task.service';

describe('GenerationTaskService', () => {
  let service: GenerationTaskService;
  let prisma: any;

  beforeEach(() => {
    prisma = {
      generationTask: {
        findUnique: jest.fn(),
        findFirst: jest.fn(),
        update: jest.fn(),
        updateMany: jest.fn(),
      },
      generationTaskEvent: {
        create: jest.fn(),
      },
      llmCallLog: {
        create: jest.fn(),
      },
    };

    service = new GenerationTaskService(prisma);
  });

  it('marks running only once and appends a single running event', async () => {
    const startedAt = new Date('2026-03-25T01:00:00.000Z');

    prisma.generationTask.findUnique
      .mockResolvedValueOnce({
        id: 'task-1',
        gameId: 'game-1',
        userId: 'user-1',
        status: GenerationTaskStatus.queued,
        startedAt: null,
      })
      .mockResolvedValueOnce({
        id: 'task-1',
        gameId: 'game-1',
        userId: 'user-1',
        status: GenerationTaskStatus.running,
        startedAt,
      })
      .mockResolvedValueOnce({
        id: 'task-1',
        gameId: 'game-1',
        userId: 'user-1',
        status: GenerationTaskStatus.running,
        startedAt,
      });
    prisma.generationTask.updateMany.mockResolvedValue({ count: 1 });
    prisma.generationTaskEvent.create.mockResolvedValue({});

    const first = await service.markRunning('task-1');
    const second = await service.markRunning('task-1');

    expect(prisma.generationTask.updateMany).toHaveBeenCalledTimes(1);
    expect(prisma.generationTaskEvent.create).toHaveBeenCalledTimes(1);
    expect(first).toEqual(expect.objectContaining({
      id: 'task-1',
      status: GenerationTaskStatus.running,
    }));
    expect(second).toEqual(expect.objectContaining({
      id: 'task-1',
      status: GenerationTaskStatus.running,
    }));
  });

  it('skips duplicate progress snapshots', async () => {
    const runningTask = {
      id: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      status: GenerationTaskStatus.running,
      startedAt: new Date('2026-03-25T01:00:00.000Z'),
      progressStage: 'qa_checking',
      progressPct: 78,
      progressMessage: 'QA running',
    };
    prisma.generationTask.findUnique.mockResolvedValue(runningTask);

    const result = await service.recordProgress({
      taskId: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      stage: 'qa_checking',
      percentage: 78,
      message: 'QA running',
    });

    expect(prisma.generationTask.update).not.toHaveBeenCalled();
    expect(prisma.generationTaskEvent.create).not.toHaveBeenCalled();
    expect(result).toBe(runningTask);
  });

  it('persists heartbeat activity as liveness without appending timeline noise', async () => {
    const runningTask = {
      id: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      status: GenerationTaskStatus.running,
      startedAt: new Date('2026-03-25T01:00:00.000Z'),
      progressStage: 'qa_checking',
      progressPct: 78,
      progressMessage: 'QA running',
    };
    prisma.generationTask.findUnique.mockResolvedValue(runningTask);
    prisma.generationTask.update.mockResolvedValue({
      ...runningTask,
      progressStage: 'qa_checking',
      progressPct: 78,
      progressMessage: 'qa_fix is still running',
    });

    const result = await service.recordActivity({
      taskId: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      stage: 'qa_checking',
      stepKey: 'qa_fix',
      message: 'qa_fix is still running',
      details: {
        activityState: 'heartbeat',
      },
    });

    expect(prisma.generationTask.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'task-1' },
      data: expect.objectContaining({
        progressStage: 'qa_checking',
        progressPct: 78,
        progressMessage: 'qa_fix is still running',
      }),
    }));
    expect(prisma.generationTaskEvent.create).not.toHaveBeenCalled();
    expect(result).toEqual(expect.objectContaining({
      id: 'task-1',
      progressMessage: 'qa_fix is still running',
    }));
  });

  it('syncs selected runtime profile and game type from progress details', async () => {
    const runningTask = {
      id: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      status: GenerationTaskStatus.running,
      startedAt: new Date('2026-03-26T07:00:00.000Z'),
      progressStage: 'spec_build',
      progressPct: 15,
      progressMessage: 'Building spec',
      runtimeProfile: 'casual_arcade',
      metadata: {
        description: 'build a circuit puzzle',
      },
    };
    prisma.generationTask.findUnique.mockResolvedValue(runningTask);
    prisma.generationTask.update.mockResolvedValue({
      ...runningTask,
      progressStage: 'contract_compose',
      progressPct: 40,
      progressMessage: 'Composing runtime contract',
      runtimeProfile: 'puzzle_grid',
      metadata: {
        description: 'build a circuit puzzle',
        selectedRuntimeProfile: 'puzzle_grid',
        selectedGameType: 'puzzle',
      },
    });
    prisma.generationTaskEvent.create.mockResolvedValue({});

    await service.recordProgress({
      taskId: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      stage: 'contract_compose',
      percentage: 40,
      message: 'Composing runtime contract',
      details: {
        runtimeProfile: 'puzzle_grid',
        gameType: 'puzzle',
      },
    });

    expect(prisma.generationTask.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'task-1' },
      data: expect.objectContaining({
        runtimeProfile: 'puzzle_grid',
        metadata: expect.objectContaining({
          description: 'build a circuit puzzle',
          selectedRuntimeProfile: 'puzzle_grid',
        }),
      }),
    }));
  });

  it('only appends llm timeline events for failures or slow calls', async () => {
    prisma.llmCallLog.create.mockResolvedValue({ id: 'log-1' });
    prisma.generationTask.update.mockResolvedValue({});
    prisma.generationTaskEvent.create.mockResolvedValue({});

    await service.persistLlmCallLog({
      taskId: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      stage: 'code_generating',
      stepKey: 'code_generate.full',
      providerName: 'MiniMax Shanghai',
      success: true,
      latencyMs: 1200,
    });

    expect(prisma.generationTaskEvent.create).not.toHaveBeenCalled();

    await service.persistLlmCallLog({
      taskId: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      stage: 'code_generating',
      stepKey: 'code_generate.full',
      providerName: 'MiniMax Shanghai',
      success: true,
      latencyMs: 45_000,
    });

    await service.persistLlmCallLog({
      taskId: 'task-1',
      gameId: 'game-1',
      userId: 'user-1',
      stage: 'code_generating',
      stepKey: 'code_generate.full',
      providerName: 'MiniMax Shanghai',
      success: false,
      errorMessage: 'upstream timeout',
    });

    expect(prisma.generationTaskEvent.create).toHaveBeenCalledTimes(2);
    expect(prisma.generationTaskEvent.create).toHaveBeenNthCalledWith(1, expect.objectContaining({
      data: expect.objectContaining({
        taskId: 'task-1',
        eventType: GenerationTaskEventType.llm_call,
        message: 'code_generate.full 调用 MiniMax Shanghai 较慢（45.0s）',
      }),
    }));
    expect(prisma.generationTaskEvent.create).toHaveBeenNthCalledWith(2, expect.objectContaining({
      data: expect.objectContaining({
        taskId: 'task-1',
        eventType: GenerationTaskEventType.llm_call,
        message: 'code_generate.full 调用 MiniMax Shanghai 失败: upstream timeout',
      }),
    }));
  });
  it('preserves upstream evidence in the author task timeline without raw bodies', async () => {
    prisma.llmCallLog.create.mockResolvedValue({ id: 'log-http' });
    prisma.generationTask.update.mockResolvedValue({});
    prisma.generationTaskEvent.create.mockResolvedValue({});
    const evidence = {
      source: 'upstream_http_response', httpStatus: 504,
      observedAt: '2026-09-09T15:45:00Z', bodySha256: 'abc',
    };
    await service.persistLlmCallLog({
      taskId: 'task-1', gameId: 'game-1', userId: 'user-1',
      stage: 'code_generating', stepKey: 'code_generate.full',
      success: false, httpStatus: 504, errorCode: 'HTTPStatusError',
      upstreamRequestId: 'req-123', requestTimeoutS: 1800, configVersion: 123,
      routeSnapshot: { transportEvidence: evidence },
      errorBodyExcerpt: 'must not be exposed in the timeline',
    });
    const details = prisma.generationTaskEvent.create.mock.calls[0][0].data.details;
    expect(details).toMatchObject({
      httpStatus: 504, upstreamRequestId: 'req-123', requestTimeoutS: 1800,
      configVersion: 123, transportEvidence: evidence,
    });
    expect(details).not.toHaveProperty('errorBodyExcerpt');
  });

  it('persists exact token usage on llm call logs', async () => {
    prisma.llmCallLog.create.mockResolvedValue({ id: 'log-usage-1' });
    prisma.generationTask.update.mockResolvedValue({});

    await service.persistLlmCallLog({
      taskId: 'task-usage-1',
      gameId: 'game-usage-1',
      userId: 'user-usage-1',
      stage: 'intent_parsing',
      stepKey: 'intent_parse',
      providerName: 'DeepSeek Shanghai',
      model: 'deepseek-chat',
      success: true,
      inputTokens: 123,
      outputTokens: 45,
      totalTokens: 168,
    });

    expect(prisma.llmCallLog.create).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({
        taskId: 'task-usage-1',
        gameId: 'game-usage-1',
        userId: 'user-usage-1',
        inputTokens: 123,
        outputTokens: 45,
        totalTokens: 168,
      }),
    }));
  });

  it('maps raw task stages into the unified 5-stage public contract', () => {
    const summary = service.toTaskSummary({
      id: 'task-display-1',
      taskType: 'pipeline_run',
      region: 'cn',
      status: GenerationTaskStatus.running,
      timeoutS: 900,
      wsChannel: 'game:task-display-1',
      gameId: 'game-display-1',
      progressStage: 'contract_qa',
      progressPct: 82,
      progressMessage: 'running contract QA',
      createdAt: new Date('2026-04-16T10:00:00.000Z'),
      updatedAt: new Date('2026-04-16T10:00:10.000Z'),
      metadata: {},
    });

    expect(summary).toEqual(expect.objectContaining({
      displayStageKey: 'validating',
      displayStageLabel: '质量校验与修复',
      displayStageIndex: 3,
      displayStagePct: 85,
      displayStageTotal: 5,
      rawStage: 'contract_qa',
    }));
  });
});
