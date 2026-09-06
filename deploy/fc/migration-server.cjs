'use strict';
const http = require('node:http');
const { migrateProduction, safeError } = require('../../prisma/migrate-production.cjs');
let busy = false;
// No HTTP trigger is created. Only RAM-authenticated InvokeFunction can call this.
const server = http.createServer(async (req, res) => {
  res.setHeader('content-type', 'application/json');
  if (req.method === 'GET' && req.url === '/health') return res.end('{"ok":true}');
  if (req.method !== 'POST' || req.url !== '/invoke') { res.statusCode = 404; return res.end('{}'); }
  req.resume();
  if (busy) { res.statusCode = 409; return res.end('{"ok":false,"code":"MIGRATION_BUSY"}'); }
  busy = true;
  try { res.end(JSON.stringify(await migrateProduction())); }
  catch (error) { res.statusCode = 500; res.end(JSON.stringify(safeError(error))); }
  finally { busy = false; }
});
server.requestTimeout = 0;
server.timeout = 0;
server.keepAliveTimeout = 0;
server.listen(9000, '0.0.0.0');
