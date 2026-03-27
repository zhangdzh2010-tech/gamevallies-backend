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

  it('suppresses heartbeat activity notes at the service layer', async () => {
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

    expect(prisma.generationTask.update).not.toHaveBeenCalled();
    expect(prisma.generationTaskEvent.create).not.toHaveBeenCalled();
    expect(result).toBe(runningTask);
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
      runtimeProfile: 'portrait_arcade',
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
      runtimeProfile: 'grid_puzzle',
      metadata: {
        description: 'build a circuit puzzle',
        selectedRuntimeProfile: 'grid_puzzle',
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
        runtimeProfile: 'grid_puzzle',
        gameType: 'puzzle',
      },
    });

    expect(prisma.generationTask.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'task-1' },
      data: expect.objectContaining({
        runtimeProfile: 'grid_puzzle',
        metadata: expect.objectContaining({
          description: 'build a circuit puzzle',
          selectedRuntimeProfile: 'grid_puzzle',
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
});
