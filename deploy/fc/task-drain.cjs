'use strict';
const ADMISSIONS = 'gamevallies:fc:admissions';
const MAINTENANCE = 'gamevallies:fc:maintenance';
const CREATION = /^\/api\/v1\/games\/(?:generate|creation-sessions(?:\/[^/]+\/(?:messages|skip|generate))?|[^/]+\/iterate)\/?$/;

async function pendingWork(queue, prisma, redis, now = Date.now()) {
  // A queue job finishes after dispatch. The SQL task stays running until
  // generation and publication actually finish, including orphaned workers.
  const [jobs, activeTasks] = await Promise.all([
    queue.getJobs(['wait', 'active', 'delayed', 'prioritized']),
    prisma.generationTask.count({where:{status:{in:['queued','running']}}}),
  ]);
  await redis.zremrangebyscore(ADMISSIONS, '-inf', now);
  const admissions = await redis.zcard(ADMISSIONS);
  const queued = jobs.filter(j => ['pipeline_run','pipeline_iterate'].includes(j.name)).length;
  return {pending:queued + activeTasks + admissions, queued, activeTasks, admissions,
    maintenance:Boolean(await redis.get(MAINTENANCE))};
}

async function admit(redis, id) {
  // Maintenance and admission must be atomic: deployment cannot slip between
  // checking the gate and recording a request still building its SQL task.
  return redis.eval(`if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
    redis.call('ZADD', KEYS[2], ARGV[2], ARGV[1]); return 1`,
    2, MAINTENANCE, ADMISSIONS, id, Date.now() + 3600000);
}

module.exports = {pendingWork, admit, ADMISSIONS, MAINTENANCE, CREATION};
