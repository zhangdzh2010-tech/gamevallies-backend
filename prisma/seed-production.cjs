'use strict';
const path = require('node:path');
const catalog = name => require(path.join(__dirname, '../packages/game-service/src/game/catalogs', name));

// Insert missing defaults only. Never create demo identities or replace admin edits.
async function seedProduction(db, env = process.env) {
  await db.$transaction(async tx => {
    const regionCode = env.GAMEVALLIES_CLOUD_REGION || 'cn-hongkong';
    const account = await tx.cloudProviderAccount.upsert({ where: { accountKey: 'aliyun-default' }, update: {},
      create: { vendor: 'aliyun', accountKey: 'aliyun-default', displayName: '阿里云默认账号' } });
    const region = await tx.cloudRegionCatalog.upsert({
      where: { cloud_region_catalog_account_id_region_code_key: { accountId: account.id, regionCode } }, update: {},
      create: { accountId: account.id, vendor: 'aliyun', regionCode,
        regionName: regionCode === 'cn-hongkong' ? '香港' : regionCode,
        regionGroup: regionCode === 'cn-hongkong' ? 'hk' : 'cn_mainland' },
    });
    // Keep the existing logical routing key; the physical FC region is independent.
    // FC endpoints are resolved by the deployer before any API/worker is started.
    if (env.AI_ENGINE_URL) {
      await tx.aiEngineRegionTarget.upsert({ where: {
        ai_engine_region_targets_execution_region_key: { executionRegion: 'cn_shanghai' },
      }, update: {},
        create: { accountId: account.id, regionCatalogId: region.id, vendor: 'aliyun',
          cloudRegionCode: regionCode, executionRegion: 'cn_shanghai', displayName: 'AI Engine FC',
          functionName: `${env.GAMEVALLIES_FC_PREFIX}-ai-engine`, registry: '', registryNamespace: '',
          imageRepository: '', serviceRegionEnv: 'cn_shanghai', aiEngineUrl: env.AI_ENGINE_URL,
          deployEnabled: true, deployStatus: 'deployed' } });
    }
    for (const step of require('./production-steps.json')) {
      await tx.llmStepCatalog.upsert({ where: { stepKey: step.stepKey }, update: {}, create: step });
    }
    for (const prompt of catalog('prompt-catalog.json')) {
      await tx.systemConfig.upsert({ where: { configKey: prompt.key }, update: {},
        create: { configKey: prompt.key, configValue: prompt.value, description: prompt.description, category: 'prompt' } });
    }
    for (const bundle of catalog('prompt-bundle-catalog.json')) {
      await tx.promptBundle.upsert({ where: { id_version: { id: bundle.id, version: bundle.version } }, update: {}, create: bundle });
    }
    for (const profile of catalog('runtime-profile-catalog.json')) {
      await tx.runtimeProfileCatalog.upsert({ where: { id: profile.id }, update: {}, create: profile });
    }
    const marker = 'billing.subscription_plans_bootstrapped_at';
    if (!await tx.systemConfig.findUnique({ where: { configKey: marker } })) {
      if (await tx.subscriptionPlan.count() === 0) {
        await tx.subscriptionPlan.createMany({ data: require('./production-plans.json'), skipDuplicates: true });
      }
      await tx.systemConfig.upsert({ where: { configKey: marker }, update: {},
        create: { configKey: marker, configValue: new Date().toISOString(), category: 'billing' } });
    }
    await tx.systemConfig.upsert({ where: { configKey: 'billing.default_free_quota' }, update: {},
      create: { configKey: 'billing.default_free_quota', configValue: '5', category: 'billing' } });
  }, { timeout: 120000 });
}
module.exports = { seedProduction };
