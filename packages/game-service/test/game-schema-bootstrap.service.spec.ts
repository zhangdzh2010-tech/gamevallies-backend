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

  it('seeds the default prompt catalog into system configs during bootstrap', async () => {
    await service.onModuleInit();

    const promptInsertCall = prisma.$executeRawUnsafe.mock.calls.find(
      ([sql, _id, key, value]: [string, string, string, string]) =>
        typeof sql === 'string'
        && sql.includes('system_configs')
        && key === 'prompt.dialogue_reply_system'
        && typeof value === 'string'
        && value.includes('{base_prompt}'),
    );

    expect(promptInsertCall).toBeTruthy();
    expect(promptInsertCall?.[0]).toContain('INSERT IGNORE INTO system_configs');
  });

  it('prunes removed prompt keys during bootstrap', async () => {
    await service.onModuleInit();

    const promptDeleteCall = prisma.$executeRawUnsafe.mock.calls.find(
      ([sql, ...params]: [string, ...string[]]) =>
        typeof sql === 'string'
        && sql.includes('DELETE FROM system_configs')
        && params.includes('prompt.iteration_mobile_layout_guardrails'),
    );

    expect(promptDeleteCall).toBeTruthy();
  });

  it('prunes removed llm step keys during bootstrap', async () => {
    await service.onModuleInit();

    const routeDeleteCall = prisma.$executeRawUnsafe.mock.calls.find(
      ([sql, ...params]: [string, ...string[]]) =>
        typeof sql === 'string'
        && sql.includes('DELETE FROM llm_step_routes')
        && params.includes('code_generate.hybrid'),
    );
    const catalogDeleteCall = prisma.$executeRawUnsafe.mock.calls.find(
      ([sql, ...params]: [string, ...string[]]) =>
        typeof sql === 'string'
        && sql.includes('DELETE FROM llm_step_catalog')
        && params.includes('code_generate.hybrid'),
    );

    expect(routeDeleteCall).toBeTruthy();
    expect(catalogDeleteCall).toBeTruthy();
  });

  it('aligns syntax repair route with the full code-generation route', async () => {
    prisma.llmStepRoute.findMany
      .mockResolvedValueOnce([
        {
          stepKey: 'code_generate.full',
          region: 'cn_shanghai',
        },
      ])
      .mockResolvedValueOnce([
        {
          id: 'route-code-generate',
          stepKey: 'code_generate.full',
          region: 'cn_shanghai',
          providerId: 'provider-code',
          fallbackProviderIds: ['provider-fallback'],
          modelOverride: null,
          fastModelOverride: null,
          enabled: true,
        },
      ]);

    await service.onModuleInit();

    expect(prisma.llmStepRoute.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        where: {
          llm_step_routes_step_key_region_key: {
            stepKey: 'qa_fix.syntax_structural',
            region: 'cn_shanghai',
          },
        },
        update: expect.objectContaining({
          providerId: 'provider-code',
          fallbackProviderIds: ['provider-fallback'],
          requestTimeoutS: 90,
          connectTimeoutS: 15,
        }),
      }),
    );
  });
});
