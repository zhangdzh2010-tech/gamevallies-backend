const { test } = require('node:test');
const assert = require('node:assert/strict');
const { databaseUrl, checkHistory, safeError } = require('../prisma/migrate-production.cjs');

test('formal schema covers every column from legacy runtime patches', () => {
  const fs = require('node:fs');
  const { Prisma } = require('@prisma/client');
  const source = fs.readFileSync(require('node:path').join(__dirname,
    '../packages/game-service/src/game/game-schema-bootstrap.service.ts'), 'utf8');
  const patches = source.slice(source.indexOf('const TABLE_COLUMN_PATCHES'), source.indexOf('const GAME_SCHEMA_STATEMENTS'));
  const matches = [...patches.matchAll(/table: "([a-z_]+)",\s+name: "([a-z_]+)"/g)];
  assert.ok(matches.length >= 35);
  for (const [, table, column] of matches) {
    const model = Prisma.dmmf.datamodel.models.find(model => model.dbName === table);
    assert.ok(model?.fields.some(field => (field.dbName || field.name) === column), `${table}.${column} is missing from the formal schema`);
  }
});

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
