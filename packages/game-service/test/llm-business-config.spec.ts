import { LLM_BUSINESS_STAGES, saveBusinessBindings, saveGatewayModel, listBusinessBindings } from '../src/admin/llm-business-config';

describe('LLM provider/model/business separation', () => {
  const provider = { id: 'provider', region: 'cn_shanghai', enabled: true, name: 'Service' };
  const models = ['a', 'b'].map(id => ({ id, providerId: provider.id, modelId: id, enabled: true, provider }));
  const rows = () => LLM_BUSINESS_STAGES.map(stage => ({ stage: stage.id, primaryModelId: 'a', fallbackModelId: 'b', revision: null }));
  function database() {
    const tx = {
      llmGatewayModel: { findMany: jest.fn().mockResolvedValue(models), findUnique: jest.fn(), create: jest.fn(), updateMany: jest.fn().mockResolvedValue({ count: 1 }) },
      llmBusinessBinding: { findUnique: jest.fn().mockResolvedValue(null), create: jest.fn(), updateMany: jest.fn().mockResolvedValue({ count: 1 }), count: jest.fn().mockResolvedValue(0) },
    };
    const db = { ...tx, llmGatewayProvider: { findUnique: jest.fn().mockResolvedValue(provider) },
      $transaction: jest.fn(async (fn: (db: any) => Promise<any>) => fn(tx)) };
    return { db: db as any, tx };
  }

  it('maps each internal step exactly once into three business stages', () => {
    const keys = LLM_BUSINESS_STAGES.flatMap(stage => stage.stepKeys);
    expect(LLM_BUSINESS_STAGES).toHaveLength(3);
    expect(keys).toHaveLength(10);
    expect(new Set(keys).size).toBe(10);
    expect(LLM_BUSINESS_STAGES.find(stage => stage.id === 'modify')!.stepKeys).toContain('iterate.classify');
  });

  it('atomically saves model identities, including backup on the same provider', async () => {
    const { db, tx } = database();
    await saveBusinessBindings(db, { region: 'cn_shanghai', bindings: rows() });
    expect(db.$transaction).toHaveBeenCalledTimes(1);
    expect(tx.llmBusinessBinding.create).toHaveBeenCalledTimes(3);
    expect(tx.llmBusinessBinding.create).toHaveBeenCalledWith({ data: expect.objectContaining({ primaryModelId: 'a', fallbackModelId: 'b', stage: 'generate', stepKeys: LLM_BUSINESS_STAGES[0].stepKeys }) });
  });

  it('rejects a stale revision rather than overwriting another admin', async () => {
    const { db, tx } = database();
    tx.llmBusinessBinding.findUnique.mockResolvedValue({ id: 'existing', revision: 2 });
    await expect(saveBusinessBindings(db, { region: 'cn_shanghai', bindings: rows() })).rejects.toThrow('已更新');
    expect(tx.llmBusinessBinding.updateMany).not.toHaveBeenCalled();
  });

  it.each(['missing', 'disabled', 'wrong-region'])('rejects %s models before live binding writes', async kind => {
    const { db, tx } = database();
    tx.llmGatewayModel.findMany.mockResolvedValue(kind === 'missing' ? [] : models.map(model => ({ ...model,
      enabled: kind !== 'disabled', provider: { ...provider, region: kind === 'wrong-region' ? 'elsewhere' : provider.region } })));
    await expect(saveBusinessBindings(db, { region: provider.region, bindings: rows() })).rejects.toThrow('不可用');
    expect(tx.llmBusinessBinding.create).not.toHaveBeenCalled();
  });

  it('does not activate business bindings when merely viewing legacy routes', async () => {
    const { db } = database();
    db.llmBusinessBinding.findMany = jest.fn().mockResolvedValue([]);
    const result = await listBusinessBindings(db, provider.region, [{ stepKey: 'code_generate.full', providerId: provider.id, effectiveModelDefault: 'old-model' }]);
    expect(result[0].status).toBe('legacy');
    expect(result[0].binding).toBeNull();
    expect(db.$transaction).not.toHaveBeenCalled();
  });

  it('model limits are optional, unknown capabilities are not written as false', async () => {
    const { db, tx } = database();
    await saveGatewayModel(db, undefined, { providerId: provider.id, modelId: 'new-model' });
    expect(tx.llmGatewayModel.create).toHaveBeenCalledWith(expect.objectContaining({ data: expect.objectContaining({ modelId: 'new-model', contextWindow: null, maxOutputTokens: null, capabilityFlags: {} }) }));
  });

  it('preserves old model restrictions and invalidates old tests on edit', async () => {
    const { db, tx } = database();
    db.llmGatewayModel.findUnique.mockResolvedValue({ id: 'a', providerId: provider.id, configurationVersion: 4, capabilityFlags: { supports_dialogue: false } });
    await saveGatewayModel(db, 'a', { providerId: provider.id, modelId: 'a', configurationVersion: 4 });
    expect(tx.llmGatewayModel.updateMany).toHaveBeenCalledWith(expect.objectContaining({ where: { id: 'a', configurationVersion: 4 }, data: expect.objectContaining({ capabilityFlags: { supports_dialogue: false }, configurationVersion: { increment: 1 } }) }));
  });

  it('cannot disable a model while a business stage still references it', async () => {
    const { db, tx } = database();
    db.llmGatewayModel.findUnique.mockResolvedValue({ id: 'a', providerId: provider.id, configurationVersion: 1 });
    tx.llmBusinessBinding.count.mockResolvedValue(1);
    await expect(saveGatewayModel(db, 'a', { modelId: 'a', enabled: false, configurationVersion: 1 })).rejects.toThrow('仍被业务环节使用');
  });
});
