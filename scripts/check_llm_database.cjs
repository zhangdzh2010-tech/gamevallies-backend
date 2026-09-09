// Destructive fixtures ONLY in the disposable, socket-only gv_llm_qa database.
const assert = require('node:assert/strict');
const { PrismaClient } = require('@prisma/client');
const { saveGatewayModel, saveBusinessBindings, LLM_BUSINESS_STAGES } = require('../packages/game-service/dist/admin/llm-business-config');
const url = new URL(process.env.QA_DATABASE_URL || 'http://invalid');
if (url.protocol !== 'mysql:' || url.hostname !== 'localhost' || url.pathname !== '/gv_llm_qa' || !url.searchParams.get('socket')?.startsWith('/private/tmp/gv-llm-mysql.')) throw new Error('Only the isolated QA socket database is allowed');
const db = new PrismaClient({ datasources: { db: { url: url.toString() } } });
(async () => {
  try {
    const imported = await db.llmGatewayModel.findMany({ orderBy: { modelId: 'asc' } });
    assert.equal(imported.length, 6);
    assert.ok(imported.some(model => model.modelId === 'unused-model'));
    assert.ok(imported.some(model => model.modelId === 'object-model'));
    assert.ok(imported.every(model => model.maxOutputTokens === 20000 && model.capabilityFlags.supports_dialogue === false));
    assert.equal(await db.llmBusinessBinding.count(), 0);
    const original = await db.llmStepRoute.findMany();
    const a = await saveGatewayModel(db, undefined, { providerId: 'qa-provider', modelId: 'fresh-a' });
    const b = await saveGatewayModel(db, undefined, { providerId: 'qa-provider', modelId: 'fresh-b' });
    const rows = LLM_BUSINESS_STAGES.map(stage => ({ stage: stage.id, primaryModelId: a.id, fallbackModelId: b.id, revision: null }));
    await saveBusinessBindings(db, { region: 'cn_shanghai', bindings: rows });
    assert.equal(await db.llmBusinessBinding.count(), 3);
    const snapshot = await db.llmBusinessBinding.findMany({ orderBy: { stage: 'asc' } });
    const broken = rows.map(row => ({ ...row, revision: 1, primaryModelId: b.id, fallbackModelId: a.id }));
    broken[1].primaryModelId = 'missing-model';
    await assert.rejects(saveBusinessBindings(db, { region: 'cn_shanghai', bindings: broken }));
    assert.deepEqual(await db.llmBusinessBinding.findMany({ orderBy: { stage: 'asc' } }), snapshot, 'earlier writes must roll back');
    await assert.rejects(db.llmGatewayModel.delete({ where: { id: a.id } }), error => error.code === 'P2003');
    await assert.rejects(saveGatewayModel(db, a.id, { providerId: 'qa-provider', modelId: a.modelId, enabled: false, configurationVersion: 1 }));
    const update = { region: 'cn_shanghai', bindings: rows.map(row => ({ ...row, revision: 1 })) };
    const simultaneous = await Promise.allSettled([saveBusinessBindings(db, update), saveBusinessBindings(db, update)]);
    assert.equal(simultaneous.filter(result => result.status === 'fulfilled').length, 1);
    assert.ok((await db.llmBusinessBinding.findMany()).every(row => row.revision === 2));
    assert.deepEqual(await db.llmStepRoute.findMany(), original, 'legacy routes must remain untouched');
    const connection = await db.llmGatewayProvider.findUnique({ where: { id: 'qa-provider' } });
    assert.equal(connection.model, 'main-model');
    console.log('PASS: six legacy models imported, constraints retained, no automatic takeover, same-provider model backup, SQL transaction rollback, optimistic concurrency, FK protection, legacy routes unchanged');
  } finally { await db.$disconnect(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
