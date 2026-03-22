import { ConfigService } from '@nestjs/config';
import { GameService } from '../src/game/game.service';

describe('Game locking behavior', () => {
  let service: GameService;
  let prisma: any;
  let bundleService: any;
  let statsService: any;
  let wsGateway: any;
  let configService: ConfigService;
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
    configService = {
      get: jest.fn((key: string, defaultValue?: string) => {
        const values: Record<string, string> = {
          AI_ENGINE_URL: 'http://ai-engine.test',
          PUBLIC_API_BASE_URL: 'https://www.gamevallies.com',
        };
        return values[key] ?? defaultValue;
      }),
    } as unknown as ConfigService;

    service = new GameService(
      prisma,
      bundleService,
      statsService,
      configService,
      wsGateway,
      generationTaskService,
    );
  });

  it('still serves game content for locked games so non-authors are not blocked', async () => {
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-locked',
      canPlay: false,
    });
    bundleService.getLatestBundle.mockResolvedValue({
      htmlCode: '<html><body>playable for public viewers</body></html>',
    });
    statsService.incrementPlayCount.mockResolvedValue(undefined);

    const result = await service.getPlayData('game-locked');

    expect(result).toContain('playable for public viewers');
    expect(statsService.incrementPlayCount).toHaveBeenCalledWith('game-locked');
  });
});
