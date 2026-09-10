// Isolated CI database only: exercise real row locks, idempotency and rollback.
const assert = require('node:assert/strict');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
require('ts-node').register({ project: path.join(__dirname, '../packages/game-service/tsconfig.json'), transpileOnly: true });
const { PrismaClient } = require('@prisma/client');
const { CreationQuotaGrantService } = require('../packages/game-service/src/admin/creation-quota-grant.service');
const { BillingService } = require('../packages/user-service/src/billing/billing.service');

async function main() {
  const url = new URL(process.env.DATABASE_URL);
  assert.equal(process.env.MIGRATION_TEST_ALLOW_LOCAL, 'true');
  assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname));
  assert.equal(url.pathname, '/gamevallies');
  const db = new PrismaClient();
  const userId = randomUUID(), otherId = randomUUID();
  const expiresAt = new Date(Date.now() + 86400000 * 30);
  try {
    await db.user.create({ data: { id: userId, username: `quota-${userId.slice(0, 12)}`, isPro: true, proExpires: expiresAt } });
    await db.user.create({ data: { id: otherId, username: `quota-${otherId.slice(0, 12)}` } });
    await db.userQuota.create({ data: { userId, totalFreeQuota: 35, usedFreeQuota: 35 } });
    const plan = await db.subscriptionPlan.findFirst({ where: { active: true } });
    assert.ok(plan);
    const subscription = await db.userSubscription.create({ data: {
      userId, planId: plan.id, startedAt: new Date(), expiresAt, quotaThisPeriod: 30, usedThisPeriod: 30,
      status: 'active', autoRenew: false,
    } });
    const grants = new CreationQuotaGrantService(db);
    const billing = new BillingService(db, { get: () => undefined }, {}, {});
    const request = { requestId: randomUUID(), userId, amount: 100, reason: 'CI isolated quota acceptance' };
    const results = await Promise.all(Array.from({ length: 5 }, () => grants.grant(request)));
    assert.equal(new Set(results.map(r => r.id)).size, 1);
    assert.equal(await db.adminCreationQuotaGrant.count({ where: { userId } }), 1);
    const quota = await billing.getQuota(userId);
    assert.equal(quota.freeQuota, 100);
    assert.equal(quota.totalFreeQuota, 135);
    assert.equal(quota.subscription.usedThisPeriod, 30);
    assert.equal(quota.subscription.active, true);
    await Promise.all([1, 2, 3, 4].map(amount => grants.grant({ ...request, requestId: randomUUID(), amount })));
    assert.equal((await billing.getQuota(userId)).freeQuota, 110);
    assert.equal((await db.userQuota.findUnique({ where: { userId } })).usedFreeQuota, 35);
    assert.deepEqual(await db.userSubscription.findUnique({ where: { id: subscription.id } }), subscription);
    assert.equal((await db.user.findUnique({ where: { id: userId } })).proExpires.getTime(), expiresAt.getTime());
    await assert.rejects(grants.grant({ ...request, userId: otherId }), /另一项授予/);
    assert.equal(await db.userQuota.findUnique({ where: { userId: otherId } }), null);
    // A forced audit storage failure must roll back the actual balance update.
    const failing = new CreationQuotaGrantService({ $transaction: (fn, options) => db.$transaction(tx => fn({
      $queryRaw: tx.$queryRaw.bind(tx), user: tx.user, systemConfig: tx.systemConfig, userQuota: tx.userQuota,
      adminCreationQuotaGrant: {
        findUnique: args => tx.adminCreationQuotaGrant.findUnique(args),
        create: () => { throw new Error('injected audit write failure'); },
      },
    }), options) });
    await assert.rejects(failing.grant({ ...request, requestId: randomUUID() }), /injected audit/);
    assert.equal((await billing.getQuota(userId)).freeQuota, 110);
    assert.equal(await db.adminCreationQuotaGrant.count({ where: { userId } }), 5);
    console.log('Quota grants: exhausted subscription, real billing read, duplicate requests, concurrent additions and atomic audit rollback passed');
  } finally {
    await db.adminCreationQuotaGrant.deleteMany({ where: { userId: { in: [userId, otherId] } } });
    await db.user.deleteMany({ where: { id: { in: [userId, otherId] } } });
    await db.$disconnect();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
