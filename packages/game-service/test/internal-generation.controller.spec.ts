import { BadRequestException, HttpException, HttpStatus } from '@nestjs/common';
import { InternalGenerationController } from '../src/game/internal-generation.controller';

describe('InternalGenerationController', () => {
  let controller: InternalGenerationController;
  let wsGateway: { emitGenerationProgress: jest.Mock };
  let generationTaskService: {
    recordProgress: jest.Mock;
    ingestLlmCallLog: jest.Mock;
    recordActivity: jest.Mock;
    recordTaskFailure: jest.Mock;
  };
  let gameService: { reconcileRelayedTaskFailure: jest.Mock };
  const previousToken = process.env.ADMIN_TOKEN;

  beforeEach(() => {
    process.env.ADMIN_TOKEN = 'unit-test-token';
    wsGateway = {
      emitGenerationProgress: jest.fn(),
    };
    generationTaskService = {
      recordProgress: jest.fn(),
      ingestLlmCallLog: jest.fn(),
      recordActivity: jest.fn(),
      recordTaskFailure: jest.fn(),
    };
    gameService = {
      reconcileRelayedTaskFailure: jest.fn(),
    };
    controller = new InternalGenerationController(
      wsGateway as any,
      generationTaskService as any,
      gameService as any,
    );
  });

  afterAll(() => {
    if (previousToken === undefined) {
      delete process.env.ADMIN_TOKEN;
    } else {
      process.env.ADMIN_TOKEN = previousToken;
    }
  });

  it('relays progress into the default game-service websocket channel', async () => {
    const result = await controller.relayProgress('unit-test-token', {
      userId: 'user-1',
      gameId: 'game-1',
      stage: 'code_generating',
      percentage: 60,
      message: 'retrying code generation',
      details: {
        retry: 1,
        maxRetries: 2,
      },
    });

    expect(wsGateway.emitGenerationProgress).toHaveBeenCalledWith(
      'user-1',
      'game-1',
      'retrying code generation',
      60,
      {
        retry: 1,
        maxRetries: 2,
        stage: 'code_generating',
      },
    );
    expect(result).toEqual(
      expect.objectContaining({
        data: expect.objectContaining({ relayed: true }),
      }),
    );
  });

  it('rejects invalid admin tokens', async () => {
    try {
      await controller.relayProgress('wrong-token', {
        userId: 'user-1',
        gameId: 'game-1',
        stage: 'code_generating',
        percentage: 60,
        message: 'test',
      });
      throw new Error('expected relayProgress to throw');
    } catch (error) {
      expect(error).toBeInstanceOf(HttpException);
      expect((error as HttpException).getStatus()).toBe(HttpStatus.UNAUTHORIZED);
    }
  });

  it('validates required progress payload fields', async () => {
    await expect(
      controller.relayProgress('unit-test-token', {
        userId: 'user-1',
        gameId: 'game-1',
        percentage: 60,
      }),
    ).rejects.toThrow(BadRequestException);
  });

  it('continues accepting the startup token after the admin token changes at runtime', async () => {
    await controller.relayProgress('unit-test-token', {
      userId: 'user-1',
      gameId: 'game-1',
      stage: 'intent_parsing',
      percentage: 15,
      message: 'parsing intent',
    });

    process.env.ADMIN_TOKEN = 'rotated-token';

    await expect(
      controller.relayProgress('unit-test-token', {
        userId: 'user-2',
        gameId: 'game-2',
        stage: 'code_generating',
        percentage: 60,
        message: 'generating game code',
      }),
    ).resolves.toEqual(expect.objectContaining({
      data: expect.objectContaining({ relayed: true }),
    }));
  });

  it('suppresses heartbeat task activity noise before it reaches the timeline', async () => {
    const result = await controller.relayTaskActivity('unit-test-token', {
      taskId: 'task-1',
      userId: 'user-1',
      gameId: 'game-1',
      stage: 'qa_checking',
      stepKey: 'qa_fix',
      message: 'still waiting for provider response',
      details: {
        activityState: 'heartbeat',
      },
    });

    expect(generationTaskService.recordActivity).not.toHaveBeenCalled();
    expect(wsGateway.emitGenerationProgress).not.toHaveBeenCalled();
    expect(result).toEqual(expect.objectContaining({
      data: expect.objectContaining({ relayed: false, suppressed: true }),
    }));
  });

  it('reconciles game failure state when relaying task failures', async () => {
    const result = await controller.relayTaskFailure('unit-test-token', {
      taskId: 'task-1',
      failedStage: 'contract_qa',
      errorMessage: 'Contract QA failed',
      retryCount: 2,
      failureFamily: 'contract_qa',
    });

    expect(generationTaskService.recordTaskFailure).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-1',
      failedStage: 'contract_qa',
      errorMessage: 'Contract QA failed',
      retryCount: 2,
      failureFamily: 'contract_qa',
    }));
    expect(gameService.reconcileRelayedTaskFailure).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-1',
      failedStage: 'contract_qa',
      errorMessage: 'Contract QA failed',
      retryCount: 2,
      failureFamily: 'contract_qa',
    }));
    expect(result).toEqual(expect.objectContaining({
      data: expect.objectContaining({ relayed: true }),
    }));
  });
});
