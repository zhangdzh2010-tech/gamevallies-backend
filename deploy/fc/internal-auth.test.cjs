const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const server = http.createServer((req,res)=>{res.end('authorized');});
// Simulate the FC HTTP edge, which does not forward custom x-fc-* fields.
const emit = http.Server.prototype.emit;
test('FC boundary rejects direct access and permits trusted service calls without following redirects', async () => {
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const url=`http://127.0.0.1:${server.address().port}`;
  process.env.FC_DEPLOYMENT='true';process.env.FC_INTERNAL_TOKEN='a'.repeat(64);process.env.FC_INTERNAL_ORIGINS=url;
  const originalFetch=global.fetch;
  require('./internal-auth.cjs');
  const boundary = http.Server.prototype.emit;
  http.Server.prototype.emit = function(event, req, res, ...args) {
    if (event === 'request') {
      for (const name of Object.keys(req.headers)) {
        if (name.startsWith('x-fc-')) delete req.headers[name];
      }
    }
    return boundary.call(this, event, req, res, ...args);
  };
  try {
    assert.equal((await originalFetch(url+'/private')).status,403);
    assert.equal((await originalFetch(url+'/health')).status,200);
    assert.equal((await fetch(url+'/private')).status,200);
    assert.equal((await originalFetch(url+'/private', {headers:{'x-fc-internal-token':'a'.repeat(64)}})).status,403);
    assert.equal((await originalFetch(url+'/private', {headers:{'x-gamevallies-internal-token':'a'.repeat(64)}})).status,200);
    const axios=require('axios');assert.equal((await axios.get(url+'/private')).status,200);
    const response=await originalFetch(url+'/private',{headers:{'x-gamevallies-internal-token':'wrong'}});
    assert.equal(response.status,403);
    process.env.FC_SERVICE='game-service';
    const control=await originalFetch(url+'/__fc/status',{headers:{'x-forwarded-host':'www.zlspace.ai'}});
    assert.equal(control.status,403);
  } finally { http.Server.prototype.emit = emit; server.closeAllConnections();await new Promise(r=>server.close(r)); }
});
