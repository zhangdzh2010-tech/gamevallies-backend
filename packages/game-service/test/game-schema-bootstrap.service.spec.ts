import { GameSchemaBootstrapService } from '../src/game/game-schema-bootstrap.service';

describe('GameSchemaBootstrapService', () => {
  let service: GameSchemaBootstrapService;
  let prisma: any;

  beforeEach(() => {
    prisma = {
      $executeRawUnsafe: jest.fn().mockResolvedValue(undefined),
      $queryRawUnsafe: jest.fn().mockResolvedValue([]),
      llmGatewayProvider: {
        updateMany: jest.fn().mockResolvedValue({ count: 1 }),
        findMany: jest.fn().mockResolvedValue([
          {
            id: 'provider-minimax',
            name: 'MiniMax Shanghai',
            baseUrl: 'https://api.minimaxi.com/v1',
            model: 'MiniMax-M2.5',
            priority: 100,
            region: 'cn_shanghai',
          },
        ]),
      },
      llmStepRoute: {
        updateMany: jest.fn().mockResolvedValue({ count: 1 }),
        findMany: jest.fn().mockResolvedValue([]),
        upsert: jest.fn().mockResolvedValue({}),
      },
    };

    service = new GameSchemaBootstrapService(prisma);
  });

  it('normalizes legacy llm region data and backfills default step routes', async () => {
    await service.onModuleInit();

    expect(prisma.llmGatewayProvider.updateMany).toHaveBeenCalledWith({
      where: { region: 'cn-shanghai' },
      data: expect.objectContaining({
        region: 'cn_shanghai',
        regionTargetId: '9d5307f1-9ee8-4f45-8b18-4e29c0012001',
        cloudVendor: 'volcengine',
        cloudRegionCode: 'cn-shanghai',
      }),
    });
    expect(prisma.llmStepRoute.updateMany).toHaveBeenCalledWith({
      where: { region: 'cn-shanghai' },
      data: { region: 'cn_shanghai' },
    });
    expect(prisma.llmStepRoute.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        where: {
          llm_step_routes_step_key_region_key: {
            stepKey: 'intent_parse',
            region: 'cn_shanghai',
          },
        },
        create: expect.objectContaining({
          stepKey: 'intent_parse',
          region: 'cn_shanghai',
          providerId: 'provider-minimax',
          enabled: true,
        }),
      }),
    );
  });
});
