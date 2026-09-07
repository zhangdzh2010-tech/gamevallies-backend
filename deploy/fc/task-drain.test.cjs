const {test} = require('node:test');
const assert = require('node:assert/strict');
const {pendingWork, CREATION} = require('./task-drain.cjs');

test('dispatched tasks still block release after BullMQ marks the job completed', async () => {
  const status = await pendingWork({getJobs:async()=>[]},
    {generationTask:{count:async(query)=>{ assert.deepEqual(query.where.status.in, ['queued','running']); return 1; }}},
    {zremrangebyscore:async()=>0,zcard:async()=>0,get:async()=> '1'});
  assert.equal(status.pending,1);
  assert.equal(status.activeTasks,1);
  assert.equal(status.maintenance,true);
});
test('in-flight admission blocks deployment even before the SQL task exists', async () => {
  const status = await pendingWork({getJobs:async()=>[]}, {generationTask:{count:async()=>0}},
    {zremrangebyscore:async()=>0,zcard:async()=>1,get:async()=> '1'});
  assert.equal(status.pending,1);
});
test('failure to read task state never reports a drained system', async () => {
  await assert.rejects(pendingWork({getJobs:async()=>[]},
    {generationTask:{count:async()=>{throw Error('database unavailable');}}}, {}));
});
test('maintenance gates submission while leaving cancellation and playback available', () => {
  for(const path of ['/api/v1/games/generate','/api/v1/games/creation-sessions',
    '/api/v1/games/creation-sessions/123/generate','/api/v1/games/creation-sessions/123/messages','/api/v1/games/123/iterate']) assert(CREATION.test(path),path);
  for(const path of ['/api/v1/games/tasks/123/cancel','/api/v1/games/123/play','/api/v1/games/creation-sessions/123/abandon']) assert(!CREATION.test(path),path);
});
