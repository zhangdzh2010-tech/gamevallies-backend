import { BadRequestException, HttpException, HttpStatus } from '@nestjs/common';
import { InternalGenerationController } from '../src/game/internal-generation.controller';

describe('InternalGenerationController', () => {
  let controller: InternalGenerationController;
  let wsGateway: { emitGenerationProgress: jest.Mock };
  const previousToken = process.env.ADMIN_TOKEN;

  beforeEach(() => {
    process.env.ADMIN_TOKEN = 'unit-test-token';
    wsGateway = {
      emitGenerationProgress: jest.fn(),
    };
    controller = new InternalGenerationController(wsGateway as any);
  });

  afterAll(() => {
    if (previousToken === undefined) {
      delete process.env.ADMIN_TOKEN;
    } else {
      process.env.ADMIN_TOKEN = previousToken;
    }
  });

  it('relays progress into the default game-service websocket channel', () => {
    const result = controller.relayProgress('unit-test-token', {
      userId: 'user-1',
      gameId: 'game-1',
      stage: 'code_generating',
      percentage: 60,
      message: '代码生成失败，重试中（1/2）',
      details: {
        retry: 1,
        maxRetries: 2,
      },
    });

    expect(wsGateway.emitGenerationProgress).toHaveBeenCalledWith(
      'user-1',
      'game-1',
      '代码生成失败，重试中（1/2）',
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

  it('rejects invalid admin tokens', () => {
    try {
      controller.relayProgress('wrong-token', {
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

  it('validates required progress payload fields', () => {
    expect(() =>
      controller.relayProgress('unit-test-token', {
        userId: 'user-1',
        gameId: 'game-1',
        percentage: 60,
      }),
    ).toThrow(BadRequestException);
  });

  it('continues accepting the startup token after the admin token changes at runtime', () => {
    controller.relayProgress('unit-test-token', {
      userId: 'user-1',
      gameId: 'game-1',
      stage: 'intent_parsing',
      percentage: 15,
      message: '解析游戏意图',
    });

    process.env.ADMIN_TOKEN = 'rotated-token';

    expect(() =>
      controller.relayProgress('unit-test-token', {
        userId: 'user-2',
        gameId: 'game-2',
        stage: 'code_generating',
        percentage: 60,
        message: '生成游戏代码',
      }),
    ).not.toThrow();
  });
});
