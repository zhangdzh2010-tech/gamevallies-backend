'use strict';
const { spawn } = require('node:child_process');
const { readdirSync, readFileSync } = require('node:fs');
const { createHash } = require('node:crypto');
const path = require('node:path');
const { seedProduction } = require('./seed-production.cjs');

function stop(code) { const error = new Error(code); error.safeCode = code; throw error; }
function databaseUrl(raw) {
  let url;
  try { url = new URL(raw); } catch { stop('DATABASE_URL_INVALID'); }
  // The RDS instance is shared with zltokens; never migrate its database.
  if (url.protocol !== 'mysql:' || url.pathname !== '/gamevallies') stop('DATABASE_NAME_MUST_BE_GAMEVALLIES');
  return url.toString();
}
function checkHistory(tables, history, migrations) {
  const businessTables = tables.filter(name => name !== '_prisma_migrations');
  if (!history.length && businessTables.length) stop('EXISTING_SCHEMA_REQUIRES_REVIEWED_BASELINE');
  for (const row of history) {
    if (row.rolled_back_at) continue;
    if (!row.finished_at) stop('FAILED_MIGRATION_REQUIRES_REPAIR');
    if (!migrations[row.migration_name] || migrations[row.migration_name] !== row.checksum) stop('MIGRATION_HISTORY_MISMATCH');
  }
}
function cli(args, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [require.resolve('prisma/build/index.js'), ...args], {
      cwd: path.join(__dirname, '..'), env, stdio: ['ignore', 'pipe', 'pipe'], timeout: 480000,
    });
    // Prisma diagnostics can contain the connection string. Return only a known P-code.
    let tail = '';
    for (const stream of [child.stdout, child.stderr]) stream.on('data', data => { tail = (tail + data).slice(-16384); });
    child.on('error', () => { const e = new Error(); e.safeCode = 'MIGRATION_PROCESS_FAILED'; reject(e); });
    child.on('close', code => resolve({ code, prismaCode: tail.match(/\bP\d{4}\b/)?.[0] }));
  });
}
async function migrateProduction(env = process.env) {
  const url = databaseUrl(env.DATABASE_URL);
  const { PrismaClient } = require('@prisma/client');
  const db = new PrismaClient({ datasources: { db: { url } }, log: [] });
  const childEnv = { ...env, DATABASE_URL: url, CHECKPOINT_DISABLE: '1' };
  try {
    const tables = await db.$queryRawUnsafe('SELECT TABLE_NAME AS name FROM information_schema.tables WHERE table_schema = DATABASE()');
    const names = tables.map(row => row.name);
    const history = names.includes('_prisma_migrations')
      ? await db.$queryRawUnsafe('SELECT migration_name, checksum, finished_at, rolled_back_at FROM _prisma_migrations') : [];
    const migrations = {};
    for (const dir of readdirSync(path.join(__dirname, 'migrations'), { withFileTypes: true }).filter(x => x.isDirectory())) {
      migrations[dir.name] = createHash('sha256').update(readFileSync(path.join(__dirname, 'migrations', dir.name, 'migration.sql'))).digest('hex');
    }
    checkHistory(names, history, migrations);
    const result = await cli(['migrate', 'deploy', '--schema', 'prisma/schema.prisma'], childEnv);
    if (result.code !== 0) stop(result.prismaCode || 'MIGRATE_DEPLOY_FAILED');
    const drift = await cli(['migrate', 'diff', '--from-schema-datasource', 'prisma/schema.prisma',
      '--to-schema-datamodel', 'prisma/schema.prisma', '--exit-code'], childEnv);
    if (drift.code !== 0) stop(drift.code === 2 ? 'SCHEMA_DRIFT_REQUIRES_REVIEW' : (drift.prismaCode || 'SCHEMA_VERIFICATION_FAILED'));
    await seedProduction(db, env);
    return { ok: true, stage: 'database-ready' };
  } finally { await db.$disconnect(); }
}
function safeError(error) {
  return { ok: false, code: error.safeCode || (/^P\d{4}$/.test(error.code || '') ? error.code : 'DATABASE_INITIALIZATION_FAILED') };
}
module.exports = { databaseUrl, checkHistory, migrateProduction, safeError };
if (require.main === module) migrateProduction().then(result => console.log(JSON.stringify(result))).catch(error => {
  console.error(JSON.stringify(safeError(error))); process.exitCode = 1;
});
