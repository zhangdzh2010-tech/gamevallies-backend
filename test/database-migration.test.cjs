const { test } = require('node:test');
const assert = require('node:assert/strict');
const { databaseUrl, checkHistory, safeError } = require('../prisma/migrate-production.cjs');

test('migration is confined to the dedicated GameVallies database', () => {
  assert.equal(new URL(databaseUrl('mysql://u:p@localhost/gamevallies')).pathname, '/gamevallies');
  for (const url of ['mysql://u:p@localhost/zltokens', 'postgres://u:p@localhost/gamevallies', '', 'mysql://u:p@localhost/']) {
    assert.throws(() => databaseUrl(url));
  }
});
test('empty database allowed, unmanaged partial database blocked', () => {
  checkHistory([], [], {});
  checkHistory(['_prisma_migrations'], [], {});
  assert.throws(() => checkHistory(['users'], [], {}), /BASELINE/);
});
test('failed or modified migrations block release', () => {
  assert.throws(() => checkHistory(['users'], [{ migration_name: 'init' }], {}), /REPAIR/);
  const row = { migration_name: 'init', checksum: 'abc', finished_at: new Date() };
  assert.throws(() => checkHistory(['users'], [row], { init: 'def' }), /MISMATCH/);
  checkHistory(['users'], [row], { init: 'abc' });
});
test('connection details never appear in migration errors', () => {
  assert.deepEqual(safeError(new Error('mysql://user:secret@host/gamevallies')), { ok: false, code: 'DATABASE_INITIALIZATION_FAILED' });
});
