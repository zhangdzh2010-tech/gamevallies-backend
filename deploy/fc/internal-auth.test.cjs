const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const server = http.createServer((req,res)=>{res.end('authorized');});
test('FC boundary rejects direct access and permits trusted service calls without following redirects', async () => {
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const url=`http://127.0.0.1:${server.address().port}`;
  process.env.FC_DEPLOYMENT='true';process.env.FC_INTERNAL_TOKEN='a'.repeat(64);process.env.FC_INTERNAL_ORIGINS=url;
  const originalFetch=global.fetch;
  require('./internal-auth.cjs');
  try {
    assert.equal((await originalFetch(url+'/private')).status,403);
    assert.equal((await originalFetch(url+'/health')).status,200);
    assert.equal((await fetch(url+'/private')).status,200);
    const axios=require('axios');assert.equal((await axios.get(url+'/private')).status,200);
    const response=await originalFetch(url+'/private',{headers:{'x-fc-internal-token':'wrong'}});
    assert.equal(response.status,403);
  } finally { server.closeAllConnections();await new Promise(r=>server.close(r)); }
});
