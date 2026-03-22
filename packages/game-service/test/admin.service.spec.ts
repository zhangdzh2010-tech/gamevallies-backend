import * as bcrypt from 'bcryptjs';
import { ConfigService } from '@nestjs/config';
import { AdminService } from '../src/admin/admin.service';

describe('AdminService', () => {
  let service: AdminService;
  let prisma: any;
  let configService: ConfigService;

  beforeEach(() => {
    prisma = {
      user: {
        findUnique: jest.fn(),
        create: jest.fn(),
        update: jest.fn(),
      },
      cloudProviderAccount: {
        findUnique: jest.fn(),
      },
      cloudRegionCatalog: {
        findUnique: jest.fn(),
      },
      aiEngineRegionTarget: {
        findUnique: jest.fn(),
        findMany: jest.fn(),
        upsert: jest.fn(),
        update: jest.fn(),
      },
      generationTask: {
        findUnique: jest.fn(),
      },
      generationTaskEvent: {
        findMany: jest.fn(),
      },
      llmStepCatalog: {
        findMany: jest.fn(),
        findUnique: jest.fn(),
      },
      llmGatewayProvider: {
        findUnique: jest.fn(),
        findMany: jest.fn(),
        upsert: jest.fn(),
      },
      llmStepRoute: {
        findMany: jest.fn(),
        findUnique: jest.fn(),
        upsert: jest.fn(),
      },
    };
    configService = {
      get: jest.fn((key: string, defaultValue?: string) => {
        const values: Record<string, string> = {
          AI_ENGINE_URL: 'http://ai-engine.test',
          AI_ENGINE_URL_CN_SHANGHAI: 'https://ai-cn.test',
          AI_ENGINE_URL_AP_SOUTHEAST_JOHOR: 'https://ai-jh.test',
        };
        return values[key] ?? defaultValue;
      }),
    } as unknown as ConfigService;

    service = new AdminService(prisma, configService);
  });

  it('hashes admin-created user passwords with bcrypt', async () => {
    prisma.user.findUnique
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
        id: 'user-1',
        username: 'smoke_user',
        displayName: 'Smoke User',
        email: null,
        phone: null,
        role: 'user',
        isPro: false,
        bio: null,
        avatarUrl: null,
        authProvider: 'email',
        followerCount: 0,
        followingCount: 0,
        gameCount: 0,
        totalPlays: 0,
        createdAt: new Date(),
        updatedAt: new Date(),
      });
    prisma.user.create.mockResolvedValue({ id: 'user-1' });

    await service.createUser({
      username: 'smoke_user',
      displayName: 'Smoke User',
      password: 'SmokePay123456',
    });

    const passwordHash = prisma.user.create.mock.calls[0][0].data.passwordHash;
    expect(typeof passwordHash).toBe('string');
    expect(passwordHash).not.toBe('SmokePay123456');
    await expect(bcrypt.compare('SmokePay123456', passwordHash)).resolves.toBe(true);
  });

  it('hashes reset passwords with bcrypt', async () => {
    prisma.user.findUnique.mockResolvedValue({ id: 'user-1' });
    prisma.user.update.mockResolvedValue({ id: 'user-1' });

    await service.resetUserPassword('user-1', 'ResetPass123');

    const passwordHash = prisma.user.update.mock.calls[0][0].data.passwordHash;
    expect(typeof passwordHash).toBe('string');
    expect(passwordHash).not.toBe('ResetPass123');
    await expect(bcrypt.compare('ResetPass123', passwordHash)).resolves.toBe(true);
  });

  it('lists enabled llm steps ordered by stepOrder then stepKey', async () => {
    prisma.llmStepCatalog.findMany.mockResolvedValue([
      { id: 'step-1', stepKey: 'intent_parse', stepOrder: 30, displayName: '意图解析', enabled: true },
      { id: 'step-2', stepKey: 'code_generate.hybrid', stepOrder: 40, displayName: '代码生成', enabled: true },
    ]);

    const result = await service.listLlmSteps();

    expect(prisma.llmStepCatalog.findMany).toHaveBeenCalledWith({
      where: { enabled: true },
      orderBy: [{ stepOrder: 'asc' }, { stepKey: 'asc' }],
    });
    expect(result).toHaveLength(2);
    expect(result[0].stepKey).toBe('intent_parse');
  });

  it('returns ordered generation task events for the admin detail panel', async () => {
    prisma.generationTask.findUnique.mockResolvedValue({ id: 'task-1' });
    prisma.generationTaskEvent.findMany.mockResolvedValue([
      { id: 'evt-1', taskId: 'task-1', message: '任务开始执行' },
      { id: 'evt-2', taskId: 'task-1', message: '解析游戏意图' },
    ]);

    const result = await service.listGenerationTaskEvents('task-1', 20);

    expect(prisma.generationTask.findUnique).toHaveBeenCalledWith({
      where: { id: 'task-1' },
      select: { id: true },
    });
    expect(prisma.generationTaskEvent.findMany).toHaveBeenCalledWith({
      where: { taskId: 'task-1' },
      orderBy: { createdAt: 'asc' },
      take: 20,
    });
    expect(result).toEqual({
      items: [
        { id: 'evt-1', taskId: 'task-1', message: '任务开始执行' },
        { id: 'evt-2', taskId: 'task-1', message: '解析游戏意图' },
      ],
    });
  });

  it('derives provider region fields from the selected region target', async () => {
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      vendor: 'volcengine',
      cloudRegionCode: 'cn-shanghai',
      executionRegion: 'cn_shanghai',
      deployEnabled: true,
      deployStatus: 'deployed',
      aiEngineUrl: 'https://ai-cn.example.com',
    });
    prisma.llmGatewayProvider.upsert.mockResolvedValue({
      id: 'provider-1',
      name: 'MiniMax Shanghai',
      providerType: 'openai_compatible',
      regionTargetId: 'target-1',
      cloudVendor: 'volcengine',
      cloudRegionCode: 'cn-shanghai',
      region: 'cn_shanghai',
      baseUrl: 'https://api.minimaxi.com/v1',
      apiKey: 'sk-test-1234',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
      requestTimeoutS: 600,
      connectTimeoutS: 15,
      enabled: true,
      priority: 100,
      description: null,
      extraConfig: null,
    });
    jest.spyOn(service, 'refreshLlmGateway').mockResolvedValue({ ok: true } as any);

    const result = await service.upsertLlmProvider(undefined, {
      name: 'MiniMax Shanghai',
      providerType: 'openai_compatible',
      regionTargetId: 'target-1',
      baseUrl: 'https://api.minimaxi.com/v1',
      apiKey: 'sk-test-1234',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
    });

    expect(prisma.llmGatewayProvider.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        create: expect.objectContaining({
          regionTargetId: 'target-1',
          cloudVendor: 'volcengine',
          cloudRegionCode: 'cn-shanghai',
          region: 'cn_shanghai',
        }),
      }),
    );
    expect(result.region).toBe('cn_shanghai');
    expect(result.apiKeyMasked).toBe('sk-t...1234');
  });

  it('allows explicit runtime endpoint updates through the standard region target edit flow', async () => {
    prisma.cloudProviderAccount.findUnique.mockResolvedValue({
      id: 'account-1',
      vendor: 'volcengine',
      enabled: true,
      defaultRegistry: 'registry.example.com',
      defaultRegistryNamespace: 'gamevallies',
    });
    prisma.cloudRegionCatalog.findUnique.mockResolvedValue({
      id: 'region-1',
      accountId: 'account-1',
      regionCode: 'cn-shanghai',
      enabled: true,
    });
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: null,
      deployStatus: 'pending',
      lastRevision: null,
      lastImageTag: null,
      lastReleaseStatus: null,
      lastDeployError: null,
      lastDeployedAt: null,
      serviceRegionEnv: 'cn_shanghai',
    });
    prisma.aiEngineRegionTarget.upsert.mockResolvedValue({ id: 'target-1' });

    await service.upsertAiEngineRegionTarget('target-1', {
      accountId: 'account-1',
      regionCatalogId: 'region-1',
      executionRegion: 'cn_shanghai',
      displayName: 'AI Engine 上海',
      functionName: 'gv-ai-engine-cn',
      aiEngineUrl: 'https://ai-cn.example.com',
      deployStatus: 'deployed',
      lastRevision: '12',
      lastImageTag: 'release-abc',
    });

    expect(prisma.aiEngineRegionTarget.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        update: expect.objectContaining({
          aiEngineUrl: 'https://ai-cn.example.com',
          deployStatus: 'deployed',
          lastRevision: '12',
          lastImageTag: 'release-abc',
        }),
      }),
    );
  });

  it('syncs deployed target runtime state through dedicated endpoint flow', async () => {
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: null,
      deployStatus: 'pending',
      lastRevision: null,
      lastImageTag: null,
      lastReleaseStatus: null,
      lastDeployError: null,
      lastDeployedAt: null,
    });
    prisma.aiEngineRegionTarget.update.mockResolvedValue({
      id: 'target-1',
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-cn.example.com',
      deployStatus: 'deployed',
      lastRevision: '12',
      lastImageTag: 'release-123',
      lastReleaseStatus: 'done',
      lastDeployError: null,
      lastDeployedAt: new Date('2026-03-22T08:00:00.000Z'),
      account: { id: 'account-1', vendor: 'volcengine', accountKey: 'volc-default', displayName: '火山' },
      regionCatalog: { id: 'region-1', regionCode: 'cn-shanghai', regionName: '上海', regionGroup: 'cn_mainland' },
    });

    await service.syncAiEngineRegionTargetDeployState({
      executionRegion: 'cn_shanghai',
      aiEngineUrl: 'https://ai-cn.example.com',
      lastRevision: '12',
      lastImageTag: 'release-123',
      lastReleaseStatus: 'done',
    });

    expect(prisma.aiEngineRegionTarget.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: 'target-1' },
        data: expect.objectContaining({
          aiEngineUrl: 'https://ai-cn.example.com',
          deployStatus: 'deployed',
          lastRevision: '12',
          lastImageTag: 'release-123',
          lastReleaseStatus: 'done',
        }),
      }),
    );
  });

  it('rejects region targets whose cloud region does not match executionRegion', async () => {
    prisma.cloudProviderAccount.findUnique.mockResolvedValue({
      id: 'account-1',
      vendor: 'volcengine',
      enabled: true,
      defaultRegistry: 'registry.example.com',
      defaultRegistryNamespace: 'gamevallies',
    });
    prisma.cloudRegionCatalog.findUnique.mockResolvedValue({
      id: 'region-1',
      accountId: 'account-1',
      regionCode: 'cn-shanghai',
      enabled: true,
    });
    prisma.aiEngineRegionTarget.findUnique.mockResolvedValue(null);

    await expect(
      service.upsertAiEngineRegionTarget(undefined, {
        accountId: 'account-1',
        regionCatalogId: 'region-1',
        executionRegion: 'ap_southeast_johor',
        displayName: 'AI Engine 柔佛',
        functionName: 'gv-ai-engine-global',
      }),
    ).rejects.toThrow('regionCatalogId does not match executionRegion=ap_southeast_johor');

    expect(prisma.aiEngineRegionTarget.upsert).not.toHaveBeenCalled();
  });

  it('keeps deployed targets selectable for providers when a resolved env endpoint exists', async () => {
    prisma.aiEngineRegionTarget.findMany.mockResolvedValue([
      {
        id: 'target-1',
        displayName: 'AI Engine 上海',
        executionRegion: 'cn_shanghai',
        deployEnabled: true,
        deployStatus: 'deployed',
        aiEngineUrl: null,
        account: { id: 'account-1', accountKey: 'volc-default', displayName: '火山', vendor: 'volcengine' },
        regionCatalog: { id: 'region-1', regionCode: 'cn-shanghai', regionName: '上海', regionGroup: 'cn_mainland' },
      },
      {
        id: 'target-2',
        displayName: 'AI Engine 柔佛',
        executionRegion: 'ap_southeast_johor',
        deployEnabled: true,
        deployStatus: 'pending',
        aiEngineUrl: null,
        account: { id: 'account-1', accountKey: 'volc-default', displayName: '火山', vendor: 'volcengine' },
        regionCatalog: { id: 'region-2', regionCode: 'ap-southeast-johor', regionName: '柔佛', regionGroup: 'overseas' },
      },
    ]);

    const result = await service.listAiEngineRegionTargets({ providerSelectableOnly: true });

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual(expect.objectContaining({
      id: 'target-1',
      resolvedAiEngineUrl: 'https://ai-cn.test',
    }));
  });

  it('derives route region from the selected provider and stores a single primary binding', async () => {
    prisma.llmStepCatalog.findUnique.mockResolvedValue({
      id: 'step-1',
      stepKey: 'code_generate.hybrid',
      stepOrder: 40,
      displayName: '代码生成（Hybrid）',
      enabled: true,
    });
    prisma.llmGatewayProvider.findUnique.mockResolvedValue({
      id: 'provider-1',
      name: 'MiniMax Shanghai',
      region: 'cn_shanghai',
      regionTargetId: 'target-1',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
    });
    prisma.llmStepRoute.upsert.mockResolvedValue({
      id: 'route-1',
      stepKey: 'code_generate.hybrid',
      region: 'cn_shanghai',
      providerId: 'provider-1',
      fallbackProviderIds: [],
      modelOverride: null,
      fastModelOverride: null,
      requestTimeoutS: null,
      connectTimeoutS: null,
      enabled: true,
      provider: {
        id: 'provider-1',
        name: 'MiniMax Shanghai',
        region: 'cn_shanghai',
        regionTargetId: 'target-1',
        providerType: 'openai_compatible',
        model: 'MiniMax-M2.5',
        fastModel: 'MiniMax-M2.5-fast',
      },
    });
    jest.spyOn(service, 'refreshLlmGateway').mockResolvedValue({ ok: true } as any);

    const result = await service.upsertLlmRoute(undefined, {
      stepKey: 'code_generate.hybrid',
      executionRegion: 'cn_shanghai',
      providerId: 'provider-1',
      enabled: true,
    });

    expect(prisma.llmStepRoute.upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        where: {
          llm_step_routes_step_key_region_key: {
            stepKey: 'code_generate.hybrid',
            region: 'cn_shanghai',
          },
        },
        create: expect.objectContaining({
          region: 'cn_shanghai',
          fallbackProviderIds: [],
          modelOverride: null,
          fastModelOverride: null,
        }),
        update: expect.objectContaining({
          region: 'cn_shanghai',
          fallbackProviderIds: [],
        }),
      }),
    );
    expect(result.stepMeta.stepKey).toBe('code_generate.hybrid');
    expect(result.region).toBe('cn_shanghai');
  });

  it('returns a single llm route by id for legacy admin callers', async () => {
    prisma.llmStepRoute.findUnique.mockResolvedValue({
      id: 'route-1',
      stepKey: 'intent_parse',
      region: 'cn_shanghai',
      providerId: 'provider-1',
      enabled: true,
      updatedAt: new Date('2026-03-22T10:00:00.000Z'),
      provider: {
        id: 'provider-1',
        name: 'Deepseek-cn-上海',
        region: 'cn_shanghai',
        regionTargetId: 'target-1',
        providerType: 'openai_compatible',
        model: 'deepseek-chat',
        fastModel: 'deepseek-chat',
      },
    });
    prisma.llmStepCatalog.findUnique.mockResolvedValue({
      id: 'step-1',
      stepKey: 'intent_parse',
      stepOrder: 30,
      stageLabel: 'Stage 02',
      displayName: '意图解析',
      description: '将描述解析为 GameSpec',
    });

    const result = await service.getLlmRoute('route-1');

    expect(prisma.llmStepRoute.findUnique).toHaveBeenCalledWith({
      where: { id: 'route-1' },
      include: {
        provider: {
          select: {
            id: true,
            name: true,
            region: true,
            regionTargetId: true,
            providerType: true,
            model: true,
            fastModel: true,
          },
        },
      },
    });
    expect(result).toEqual(expect.objectContaining({
      id: 'route-1',
      stepKey: 'intent_parse',
      executionRegion: 'cn_shanghai',
      providerId: 'provider-1',
      providerDisplayName: 'Deepseek-cn-上海',
      modelDefault: 'deepseek-chat',
    }));
  });

  it('rejects route bindings when executionRegion does not match the selected provider region', async () => {
    prisma.llmStepCatalog.findUnique.mockResolvedValue({
      id: 'step-1',
      stepKey: 'qa_fix',
      stepOrder: 60,
      displayName: 'QA 自动修复',
      enabled: true,
    });
    prisma.llmGatewayProvider.findUnique.mockResolvedValue({
      id: 'provider-1',
      name: 'MiniMax Shanghai',
      region: 'cn_shanghai',
      regionTargetId: 'target-1',
      model: 'MiniMax-M2.5',
      fastModel: 'MiniMax-M2.5-fast',
    });

    await expect(
      service.upsertLlmRoute(undefined, {
        stepKey: 'qa_fix',
        providerId: 'provider-1',
        executionRegion: 'ap_southeast_johor',
      }),
    ).rejects.toThrow('executionRegion must match the selected provider region');

    expect(prisma.llmStepRoute.upsert).not.toHaveBeenCalled();
  });
});
