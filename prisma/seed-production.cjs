'use strict';
const path = require('node:path');
const catalog = name => require(path.join(__dirname, '../packages/game-service/src/game/catalogs', name));

// Insert missing defaults only. Never create demo identities or replace admin edits.
async function seedProduction(db) {
  await db.$transaction(async tx => {
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
