// CI-only MySQL 8 acceptance. Explicit opt-in prevents accidental live DB use.
const assert = require('node:assert/strict');
const { readdirSync } = require('node:fs');
const path = require('node:path');
const { PrismaClient } = require('@prisma/client');
const { migrateProduction } = require('../prisma/migrate-production.cjs');
async function main() {
  const url = new URL(process.env.DATABASE_URL);
  assert.equal(process.env.MIGRATION_TEST_ALLOW_LOCAL, 'true');
  assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname));
  const db = new PrismaClient();
  process.env.GAMEVALLIES_CLOUD_REGION = 'cn-hongkong';
  process.env.GAMEVALLIES_FC_PREFIX = 'gamevallies-test';
  process.env.AI_ENGINE_URL = 'https://ai.example.com';
  try {
    // Start from a truly empty DB, then simulate the legacy partial bootstrap.
    const existing = await db.$queryRawUnsafe('SHOW TABLES');
    assert.equal(existing.length, 0, 'test requires a fresh empty database');
    await db.$executeRawUnsafe('CREATE TABLE migration_test_sentinel (id INT PRIMARY KEY)');
    await db.$executeRawUnsafe('INSERT INTO migration_test_sentinel VALUES (7)');
    await assert.rejects(migrateProduction(), /BASELINE/);
    assert.equal((await db.$queryRawUnsafe('SELECT id FROM migration_test_sentinel'))[0].id, 7);
    await db.$executeRawUnsafe('DROP TABLE migration_test_sentinel');
    assert.equal((await migrateProduction()).ok, true);
    const records = await db.$queryRawUnsafe('SELECT * FROM _prisma_migrations');
    const migrationNames = readdirSync(path.join(__dirname, '../prisma/migrations'), {withFileTypes:true})
      .filter(entry => entry.isDirectory()).map(entry => entry.name).sort();
    assert.deepEqual(records.map(row => row.migration_name).sort(), migrationNames);
    assert.ok(records.every(row => row.finished_at && !row.rolled_back_at));
    const timeoutDefaults = await db.$queryRawUnsafe("SELECT COLUMN_DEFAULT AS value FROM information_schema.columns WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'llm_gateway_providers' AND COLUMN_NAME IN ('request_timeout_s', 'connect_timeout_s')");
    assert.equal(timeoutDefaults.length, 2);
    assert.ok(timeoutDefaults.every(row => Number(row.value) === 1800));
    assert.equal(await db.user.count(), 0);
    assert.equal(await db.game.count(), 0);
    assert.equal(await db.llmGatewayProvider.count(), 0);
    assert.equal(await db.cloudProviderAccount.count(), 1);
    assert.equal((await db.cloudRegionCatalog.findFirst()).regionCode, 'cn-hongkong');
    assert.equal((await db.aiEngineRegionTarget.findFirst()).aiEngineUrl, 'https://ai.example.com');
    assert.ok(await db.promptBundle.count() > 0);
    assert.ok(await db.runtimeProfileCatalog.count() > 0);
    assert.ok(await db.llmStepCatalog.count() > 0);
    const prompt = await db.systemConfig.findFirst({ where: { category: 'prompt' } });
    await db.systemConfig.update({ where: { id: prompt.id }, data: { configValue: 'admin-custom-prompt' } });
    await db.subscriptionPlan.update({ where: { id: 'plan_monthly_basic' }, data: { price: 1234, active: false } });
    assert.equal((await migrateProduction()).ok, true);
    assert.equal((await db.systemConfig.findUnique({ where: { id: prompt.id } })).configValue, 'admin-custom-prompt');
    const plan = await db.subscriptionPlan.findUnique({ where: { id: 'plan_monthly_basic' } });
    assert.equal(plan.price, 1234);
    assert.equal(plan.active, false);
    assert.equal(await db.subscriptionPlan.count(), 2);
    await db.$executeRawUnsafe('ALTER TABLE games ADD COLUMN migration_test_drift INT NULL');
    await assert.rejects(migrateProduction(), /SCHEMA_DRIFT/);
    await db.$executeRawUnsafe('ALTER TABLE games DROP COLUMN migration_test_drift');
    console.log('MySQL 8: empty DB, partial DB guard, migration replay, seed preservation and drift detection passed');
  } finally { await db.$disconnect(); }
}
main().catch(error => {
  // This test uses only an isolated local CI database and synthetic credentials.
  console.error(error); process.exitCode = 1;
});
