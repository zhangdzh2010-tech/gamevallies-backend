import { NotFoundException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { JwtService } from '@nestjs/jwt';
import { GameService } from '../src/game/game.service';

describe('Game locking behavior', () => {
  let service: GameService;
  let prisma: any;
  let bundleService: any;
  let statsService: any;
  let wsGateway: any;
  let configService: ConfigService;
  let jwtService: JwtService;
  let generationTaskService: any;

  beforeEach(() => {
    prisma = {
      game: {
        findUnique: jest.fn(),
      },
    };
    bundleService = {
      getLatestBundle: jest.fn(),
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
      createTask: jest.fn(),
      markRunning: jest.fn(),
      recordProgress: jest.fn(),
      markSucceeded: jest.fn(),
      markFailed: jest.fn(),
      getLatestTaskForGame: jest.fn(),
      getTaskForUser: jest.fn(),
      listTaskEvents: jest.fn(),
      requestCancel: jest.fn(),
      toTaskSummary: jest.fn(),
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
        };
        return values[key] ?? defaultValue;
      }),
    } as unknown as ConfigService;

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

  it('does not expose draft game content through the public preview route', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-draft',
      status: 'draft',
      visibility: 'public',
      canPlay: true,
    });
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<html><body>draft preview</body></html>',
    });
    statsService.incrementPlayCount.mockResolvedValue(undefined);

    await expect(service.getPlayData('game-draft')).rejects.toBeInstanceOf(NotFoundException);
    expect(statsService.incrementPlayCount).not.toHaveBeenCalled();
  });
});
