// Loaded only by FC containers. Keep service credentials off external requests.
const http = require('node:http');
const crypto = require('node:crypto');
const token = process.env.FC_INTERNAL_TOKEN || '';
if (process.env.FC_DEPLOYMENT === 'true') {
  if (!/^[a-f0-9]{64}$/.test(token)) throw new Error('FC_INTERNAL_TOKEN must be 32 random bytes encoded as hex');
  const origins = new Set((process.env.FC_INTERNAL_ORIGINS || '').split(',').filter(Boolean).map(v => new URL(v).origin));
  const trusted = value => { try { return origins.has(new URL(value).origin); } catch { return false; } };
  let controlQueue, controlRedis;
  async function control(req, res, path) {
    const Redis = require('ioredis');
    const { Queue } = require('bullmq');
    if (!controlRedis) controlRedis = new Redis(process.env.REDIS_URL, { maxRetriesPerRequest: 1 });
    if (!controlQueue) controlQueue = new Queue('generation-execution', { connection: controlRedis });
    if (path === '/__fc/drain' && req.method === 'POST') await controlRedis.set('gamevallies:fc:maintenance', '1', 'EX', 7200);
    else if (path === '/__fc/resume' && req.method === 'POST') await controlRedis.del('gamevallies:fc:maintenance');
    else if (!(path === '/__fc/status' && req.method === 'GET')) { res.writeHead(404); res.end(); return; }
    const jobs = await controlQueue.getJobs(['wait', 'active', 'delayed', 'prioritized']);
    const pending = jobs.filter(j => ['pipeline_run', 'pipeline_iterate'].includes(j.name)).length;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ pending, maintenance: Boolean(await controlRedis.get('gamevallies:fc:maintenance')) }));
  }
  const emit = http.Server.prototype.emit;
  http.Server.prototype.emit = function(event, req, res, ...rest) {
    if (event === 'request' || event === 'upgrade') {
      const path = (req.url || '').split('?')[0];
      const health = event === 'request' && req.method === 'GET' && ['/health', '/api/v1/health'].includes(path);
      const supplied = Buffer.from(String(req.headers['x-gamevallies-internal-token'] || ''));
      const expected = Buffer.from(token);
      if (!health && !(supplied.length === expected.length && crypto.timingSafeEqual(supplied, expected))) {
        if (event === 'upgrade') res.destroy();
        else { res.writeHead(403, { 'Content-Type': 'application/json' }); res.end('{"error":"Forbidden"}'); }
        return true;
      }
      if (event === 'request' && path.startsWith('/__fc/') && process.env.FC_SERVICE === 'game-service') {
        control(req, res, path).catch(() => { res.writeHead(503); res.end('Control unavailable'); });
        return true;
      }
      // Do not allow the caller's transport credential to escape via a proxy.
      delete req.headers['x-gamevallies-internal-token'];
    }
    return emit.call(this, event, req, res, ...rest);
  };
  const nativeFetch = globalThis.fetch;
  globalThis.fetch = (input, options = {}) => {
    const url = typeof input === 'string' || input instanceof URL ? String(input) : input.url;
    const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
    headers.delete('x-gamevallies-internal-token');
    if (trusted(url)) headers.set('x-gamevallies-internal-token', token);
    return nativeFetch(input, { ...options, headers, ...(trusted(url) ? { redirect: 'error' } : {}) });
  };
  const axios = require('axios');
  axios.interceptors.request.use(config => {
    const url = config.baseURL ? new URL(config.url, config.baseURL).href : config.url;
    config.headers.delete('x-gamevallies-internal-token');
    if (trusted(url)) {
      config.headers.set('x-gamevallies-internal-token', token);
      config.maxRedirects = 0;
    }
    return config;
  });
}
