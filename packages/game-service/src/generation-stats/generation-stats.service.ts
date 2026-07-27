import { BadRequestException, Injectable } from '@nestjs/common';
import {
  GenerationTaskEventType,
  GenerationTaskStatus,
  GenerationTaskType,
  Prisma,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

const TERMINAL_STATUSES = [
  'succeeded',
  'failed',
  'canceled',
  'timed_out',
] as const;

const TASK_TYPES = ['pipeline_run', 'pipeline_iterate'] as const;
export type StatsTaskType = (typeof TASK_TYPES)[number];

const GENERATION_TIERS = ['safe', 'standard', 'showcase'] as const;
export type StatsGenerationTier = (typeof GENERATION_TIERS)[number];

const DEFAULT_RANGE_DAYS = 7;
const MAX_RANGE_DAYS = 90;
const DAY_MS = 24 * 60 * 60 * 1000;
// Data volume is designed for tens of thousands of tasks; a hard row cap keeps
// worst-case memory bounded even if the table grows beyond that.
const MAX_SAMPLE_ROWS = 100_000;
const STAGE_SAMPLE_TASKS = 300;
const FAILURE_FAMILY_TOP_N = 10;

const DATE_ONLY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

type ResolvedRange = {
  from: Date;
  /** Exclusive upper bound used in SQL comparisons. */
  toExclusive: Date;
};

type StatusBucket = {
  total: number;
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  canceled: number;
  timed_out: number;
  terminal: number;
  succeededRate: number | null;
  failedRate: number | null;
  canceledRate: number | null;
  timedOutRate: number | null;
};

type PercentileSummary = {
  sampleSize: number;
  avgMs: number | null;
  p50Ms: number | null;
  p90Ms: number | null;
  p95Ms: number | null;
};

type ScoreDistribution = {
  sampleSize: number;
  mean: number | null;
  p50: number | null;
  buckets: Array<{ range: string; count: number }>;
};

export type StatsQueryParams = {
  from?: string;
  to?: string;
  taskType?: string;
  tier?: string;
};

function toNum(value: unknown): number {
  if (value === null || value === undefined) {
    return 0;
  }
  if (typeof value === 'bigint') {
    return Number(value);
  }
  if (typeof value === 'object' && typeof (value as { toNumber?: unknown }).toNumber === 'function') {
    return (value as { toNumber: () => number }).toNumber();
  }
  return Number(value);
}

function round(value: number, digits = 4): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function ratio(numerator: number, denominator: number): number | null {
  if (denominator <= 0) {
    return null;
  }
  return round(numerator / denominator);
}

/** Linear-interpolation percentile over an unsorted numeric sample. */
function percentile(values: number[], p: number): number | null {
  if (values.length === 0) {
    return null;
  }
  const sorted = [...values].sort((a, b) => a - b);
  const idx = (sorted.length - 1) * p;
  const lower = Math.floor(idx);
  const upper = Math.ceil(idx);
  if (lower === upper) {
    return round(sorted[lower]);
  }
  const weight = idx - lower;
  return round(sorted[lower] * (1 - weight) + sorted[upper] * weight);
}

function emptyStatusBucket(): StatusBucket {
  return {
    total: 0,
    queued: 0,
    running: 0,
    succeeded: 0,
    failed: 0,
    canceled: 0,
    timed_out: 0,
    terminal: 0,
    succeededRate: null,
    failedRate: null,
    canceledRate: null,
    timedOutRate: null,
  };
}

function finalizeStatusBucket(bucket: StatusBucket): StatusBucket {
  bucket.terminal =
    bucket.succeeded + bucket.failed + bucket.canceled + bucket.timed_out;
  bucket.succeededRate = ratio(bucket.succeeded, bucket.terminal);
  bucket.failedRate = ratio(bucket.failed, bucket.terminal);
  bucket.canceledRate = ratio(bucket.canceled, bucket.terminal);
  bucket.timedOutRate = ratio(bucket.timed_out, bucket.terminal);
  return bucket;
}

function summarizeDurations(values: number[]): PercentileSummary {
  if (values.length === 0) {
    return { sampleSize: 0, avgMs: null, p50Ms: null, p90Ms: null, p95Ms: null };
  }
  const sum = values.reduce((acc, v) => acc + v, 0);
  return {
    sampleSize: values.length,
    avgMs: round(sum / values.length, 1),
    p50Ms: percentile(values, 0.5),
    p90Ms: percentile(values, 0.9),
    p95Ms: percentile(values, 0.95),
  };
}

function buildScoreDistribution(scores: number[]): ScoreDistribution {
  const bucketDefs: Array<{ range: string; min: number; max: number; maxInclusive: boolean }> = [
    { range: '[0,2)', min: 0, max: 2, maxInclusive: false },
    { range: '[2,4)', min: 2, max: 4, maxInclusive: false },
    { range: '[4,6)', min: 4, max: 6, maxInclusive: false },
    { range: '[6,8)', min: 6, max: 8, maxInclusive: false },
    { range: '[8,10]', min: 8, max: 10, maxInclusive: true },
  ];
  const buckets = bucketDefs.map((def) => ({
    range: def.range,
    count: scores.filter((score) =>
      score >= def.min && (def.maxInclusive ? score <= def.max : score < def.max),
    ).length,
  }));
  const mean =
    scores.length > 0
      ? round(scores.reduce((acc, v) => acc + v, 0) / scores.length, 2)
      : null;
  return {
    sampleSize: scores.length,
    mean,
    p50: percentile(scores, 0.5),
    buckets,
  };
}

@Injectable()
export class GenerationStatsService {
  constructor(private readonly prisma: PrismaService) {}

  // ---------------------------------------------------------------------
  // Parameter validation
  // ---------------------------------------------------------------------

  resolveRange(from?: string, to?: string): ResolvedRange {
    const toExclusive = this.parseBoundary(to, 'to', true) ?? new Date();
    const fromDate =
      this.parseBoundary(from, 'from', false) ??
      new Date(toExclusive.getTime() - DEFAULT_RANGE_DAYS * DAY_MS);

    if (fromDate.getTime() >= toExclusive.getTime()) {
      throw new BadRequestException('"from" must be earlier than "to"');
    }
    if (toExclusive.getTime() - fromDate.getTime() > MAX_RANGE_DAYS * DAY_MS) {
      throw new BadRequestException(
        `Date range must not exceed ${MAX_RANGE_DAYS} days`,
      );
    }
    return { from: fromDate, toExclusive };
  }

  private parseBoundary(
    value: string | undefined,
    label: 'from' | 'to',
    endOfDayForDateOnly: boolean,
  ): Date | null {
    if (value === undefined || value === null || value.trim() === '') {
      return null;
    }
    const trimmed = value.trim();
    const parsed = new Date(trimmed);
    if (Number.isNaN(parsed.getTime())) {
      throw new BadRequestException(
        `Invalid "${label}" date: expected an ISO date such as 2026-07-01`,
      );
    }
    // A date-only upper bound is treated as inclusive of that whole day.
    if (endOfDayForDateOnly && DATE_ONLY_PATTERN.test(trimmed)) {
      return new Date(parsed.getTime() + DAY_MS);
    }
    return parsed;
  }

  resolveTaskType(taskType?: string): StatsTaskType | undefined {
    if (taskType === undefined || taskType === null || taskType === '') {
      return undefined;
    }
    if ((TASK_TYPES as readonly string[]).includes(taskType)) {
      return taskType as StatsTaskType;
    }
    throw new BadRequestException(
      `Invalid "taskType": expected one of ${TASK_TYPES.join(', ')}`,
    );
  }

  resolveTier(tier?: string): StatsGenerationTier | undefined {
    if (tier === undefined || tier === null || tier === '') {
      return undefined;
    }
    if ((GENERATION_TIERS as readonly string[]).includes(tier)) {
      return tier as StatsGenerationTier;
    }
    throw new BadRequestException(
      `Invalid "tier": expected one of ${GENERATION_TIERS.join(', ')}`,
    );
  }

  private taskTypeSql(taskType?: StatsTaskType): Prisma.Sql {
    return taskType
      ? Prisma.sql`AND task_type = ${taskType}`
      : Prisma.empty;
  }

  private tierSql(tier?: StatsGenerationTier): Prisma.Sql {
    // The requested tier lives in the task metadata JSON; tasks created
    // without an explicit tier default to "standard".
    return tier
      ? Prisma.sql`AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.generationTier')), 'standard') = ${tier}`
      : Prisma.empty;
  }

  private buildMeta(
    range: ResolvedRange,
    extra: Record<string, unknown> = {},
  ): Record<string, unknown> {
    return {
      from: range.from.toISOString(),
      toExclusive: range.toExclusive.toISOString(),
      generatedAt: new Date().toISOString(),
      ...extra,
    };
  }

  // ---------------------------------------------------------------------
  // 1. Overview
  // ---------------------------------------------------------------------

  async getOverview(params: StatsQueryParams) {
    const range = this.resolveRange(params.from, params.to);
    const taskType = this.resolveTaskType(params.taskType);
    const tier = this.resolveTier(params.tier);

    const statusRows = await this.prisma.$queryRaw<
      Array<{ day: string; taskType: string; status: string; cnt: unknown }>
    >(Prisma.sql`
      SELECT DATE_FORMAT(created_at, '%Y-%m-%d') AS day,
             task_type AS taskType,
             status,
             COUNT(*) AS cnt
      FROM generation_tasks
      WHERE created_at >= ${range.from}
        AND created_at < ${range.toExclusive}
        ${this.taskTypeSql(taskType)}
        ${this.tierSql(tier)}
      GROUP BY day, taskType, status
      ORDER BY day ASC
    `);

    const failureRows = await this.prisma.$queryRaw<
      Array<{ taskType: string; family: string; cnt: unknown }>
    >(Prisma.sql`
      SELECT task_type AS taskType,
             COALESCE(failure_family, 'unknown') AS family,
             COUNT(*) AS cnt
      FROM generation_tasks
      WHERE created_at >= ${range.from}
        AND created_at < ${range.toExclusive}
        AND status IN ('failed', 'timed_out')
        ${this.taskTypeSql(taskType)}
        ${this.tierSql(tier)}
      GROUP BY taskType, family
      ORDER BY cnt DESC
    `);

    const dailyMap = new Map<string, Record<string, StatusBucket>>();
    const totals: Record<string, StatusBucket> = {};
    let totalTasks = 0;

    for (const row of statusRows) {
      const count = toNum(row.cnt);
      totalTasks += count;

      if (!dailyMap.has(row.day)) {
        dailyMap.set(row.day, {});
      }
      const dayBuckets = dailyMap.get(row.day)!;
      if (!dayBuckets[row.taskType]) {
        dayBuckets[row.taskType] = emptyStatusBucket();
      }
      if (!totals[row.taskType]) {
        totals[row.taskType] = emptyStatusBucket();
      }

      for (const bucket of [dayBuckets[row.taskType], totals[row.taskType]]) {
        bucket.total += count;
        if (row.status in bucket) {
          (bucket as unknown as Record<string, number>)[row.status] += count;
        }
      }
    }

    const daily = [...dailyMap.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, buckets]) => ({
        date,
        byTaskType: Object.fromEntries(
          Object.entries(buckets).map(([type, bucket]) => [
            type,
            finalizeStatusBucket(bucket),
          ]),
        ),
      }));

    const failureFamilies: Record<
      string,
      Array<{ family: string; count: number }>
    > = {};
    for (const row of failureRows) {
      if (!failureFamilies[row.taskType]) {
        failureFamilies[row.taskType] = [];
      }
      if (failureFamilies[row.taskType].length < FAILURE_FAMILY_TOP_N) {
        failureFamilies[row.taskType].push({
          family: row.family,
          count: toNum(row.cnt),
        });
      }
    }

    return {
      meta: this.buildMeta(range, {
        taskType: taskType ?? 'all',
        tier: tier ?? 'all',
        totalTasks,
        failureFamilyTopN: FAILURE_FAMILY_TOP_N,
      }),
      daily,
      totals: Object.fromEntries(
        Object.entries(totals).map(([type, bucket]) => [
          type,
          finalizeStatusBucket(bucket),
        ]),
      ),
      failureFamilies,
    };
  }

  // ---------------------------------------------------------------------
  // 2. Latency
  // ---------------------------------------------------------------------

  async getLatency(params: StatsQueryParams) {
    const range = this.resolveRange(params.from, params.to);
    const taskType = this.resolveTaskType(params.taskType);

    const durationRows = await this.prisma.$queryRaw<
      Array<{ taskType: string; status: string; durationMs: unknown }>
    >(Prisma.sql`
      SELECT task_type AS taskType,
             status,
             TIMESTAMPDIFF(MICROSECOND, created_at, completed_at) DIV 1000 AS durationMs
      FROM generation_tasks
      WHERE created_at >= ${range.from}
        AND created_at < ${range.toExclusive}
        AND completed_at IS NOT NULL
        AND status IN ('succeeded', 'failed', 'canceled', 'timed_out')
        ${this.taskTypeSql(taskType)}
      ORDER BY created_at DESC
      LIMIT ${MAX_SAMPLE_ROWS}
    `);

    const byTaskType: Record<
      string,
      { allTerminal: number[]; succeeded: number[] }
    > = {};
    const allDurations: number[] = [];
    for (const row of durationRows) {
      const duration = toNum(row.durationMs);
      if (!Number.isFinite(duration) || duration < 0) {
        continue;
      }
      allDurations.push(duration);
      if (!byTaskType[row.taskType]) {
        byTaskType[row.taskType] = { allTerminal: [], succeeded: [] };
      }
      byTaskType[row.taskType].allTerminal.push(duration);
      if (row.status === 'succeeded') {
        byTaskType[row.taskType].succeeded.push(duration);
      }
    }

    const stageLatency = await this.computeStageLatency(range, taskType);

    return {
      meta: this.buildMeta(range, {
        taskType: taskType ?? 'all',
        sampleSize: allDurations.length,
        sampleCap: MAX_SAMPLE_ROWS,
      }),
      endToEnd: {
        all: summarizeDurations(allDurations),
        byTaskType: Object.fromEntries(
          Object.entries(byTaskType).map(([type, groups]) => [
            type,
            {
              allTerminal: summarizeDurations(groups.allTerminal),
              succeeded: summarizeDurations(groups.succeeded),
            },
          ]),
        ),
        note: 'Duration is completed_at - created_at for tasks that reached a terminal status; failed/canceled/timed_out tasks are included in allTerminal.',
      },
      stageLatency,
    };
  }

  private async computeStageLatency(
    range: ResolvedRange,
    taskType?: StatsTaskType,
  ) {
    const sampledTasks = await this.prisma.generationTask.findMany({
      where: {
        createdAt: { gte: range.from, lt: range.toExclusive },
        status: { in: TERMINAL_STATUSES as unknown as GenerationTaskStatus[] },
        ...(taskType ? { taskType: taskType as GenerationTaskType } : {}),
      },
      orderBy: { createdAt: 'desc' },
      take: STAGE_SAMPLE_TASKS,
      select: { id: true },
    });

    if (sampledTasks.length === 0) {
      return {
        available: false,
        note: 'No terminal tasks in range; stage latency could not be reconstructed.',
        sampledTasks: 0,
        stages: [],
      };
    }

    const events = await this.prisma.generationTaskEvent.findMany({
      where: {
        taskId: { in: sampledTasks.map((task) => task.id) },
        eventType: {
          in: [GenerationTaskEventType.status, GenerationTaskEventType.progress],
        },
        stage: { not: null },
      },
      orderBy: [{ taskId: 'asc' }, { createdAt: 'asc' }],
      select: { taskId: true, stage: true, createdAt: true },
    });

    // Stage duration = time between the first event of a stage and the first
    // event of the next stage within the same task.
    const perTaskEvents = new Map<
      string,
      Array<{ stage: string; createdAt: Date }>
    >();
    for (const event of events) {
      if (!event.stage) {
        continue;
      }
      if (!perTaskEvents.has(event.taskId)) {
        perTaskEvents.set(event.taskId, []);
      }
      perTaskEvents.get(event.taskId)!.push({
        stage: event.stage,
        createdAt: event.createdAt,
      });
    }

    const stageDurations = new Map<string, number[]>();
    for (const taskEvents of perTaskEvents.values()) {
      const perStage = new Map<string, number>();
      for (let i = 0; i < taskEvents.length - 1; i++) {
        const current = taskEvents[i];
        const next = taskEvents[i + 1];
        const elapsed = next.createdAt.getTime() - current.createdAt.getTime();
        if (elapsed <= 0) {
          continue;
        }
        perStage.set(current.stage, (perStage.get(current.stage) || 0) + elapsed);
      }
      for (const [stage, duration] of perStage.entries()) {
        if (!stageDurations.has(stage)) {
          stageDurations.set(stage, []);
        }
        stageDurations.get(stage)!.push(duration);
      }
    }

    if (stageDurations.size === 0) {
      return {
        available: false,
        note: 'Task events did not contain enough stage transitions to reconstruct per-stage latency; only end-to-end latency is available.',
        sampledTasks: sampledTasks.length,
        stages: [],
      };
    }

    const stages = [...stageDurations.entries()]
      .map(([stage, durations]) => ({
        stage,
        sampleSize: durations.length,
        p50Ms: percentile(durations, 0.5),
        p95Ms: percentile(durations, 0.95),
      }))
      .sort((a, b) => b.sampleSize - a.sampleSize);

    return {
      available: true,
      note: `Approximated from generation_task_events stage transitions on the most recent ${sampledTasks.length} terminal tasks (sample cap ${STAGE_SAMPLE_TASKS}); the elapsed time of a task's last stage cannot be observed and is excluded.`,
      sampledTasks: sampledTasks.length,
      stages,
    };
  }

  // ---------------------------------------------------------------------
  // 3. Quality
  // ---------------------------------------------------------------------

  async getQuality(params: StatsQueryParams) {
    const range = this.resolveRange(params.from, params.to);

    const qualityScoreRows = await this.prisma.$queryRaw<
      Array<{ score: unknown }>
    >(Prisma.sql`
      SELECT CAST(JSON_EXTRACT(result_summary, '$.qualityScore') AS DECIMAL(10, 4)) AS score
      FROM generation_tasks
      WHERE created_at >= ${range.from}
        AND created_at < ${range.toExclusive}
        AND status = 'succeeded'
        AND JSON_EXTRACT(result_summary, '$.qualityScore') IS NOT NULL
      LIMIT ${MAX_SAMPLE_ROWS}
    `);

    const funScoreRows = await this.prisma.$queryRaw<
      Array<{ score: unknown }>
    >(Prisma.sql`
      SELECT CAST(JSON_EXTRACT(metadata, '$.qualityBreakdown.review_fun_score') AS DECIMAL(10, 4)) AS score
      FROM game_bundles
      WHERE created_at >= ${range.from}
        AND created_at < ${range.toExclusive}
        AND JSON_EXTRACT(metadata, '$.qualityBreakdown.review_fun_score') IS NOT NULL
      LIMIT ${MAX_SAMPLE_ROWS}
    `);

    const firstPassRows = await this.prisma.$queryRaw<
      Array<{
        terminalTotal: unknown;
        succeededTotal: unknown;
        firstPassSucceeded: unknown;
      }>
    >(Prisma.sql`
      SELECT COUNT(*) AS terminalTotal,
             SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeededTotal,
             SUM(CASE WHEN status = 'succeeded'
                       AND retry_count = 0
                       AND COALESCE(CAST(JSON_EXTRACT(result_summary, '$.qaRetries') AS SIGNED), 0) = 0
                 THEN 1 ELSE 0 END) AS firstPassSucceeded
      FROM generation_tasks
      WHERE created_at >= ${range.from}
        AND created_at < ${range.toExclusive}
        AND status IN ('succeeded', 'failed', 'canceled', 'timed_out')
    `);

    const qualityScores = qualityScoreRows
      .map((row) => toNum(row.score))
      .filter((score) => Number.isFinite(score));
    const funScores = funScoreRows
      .map((row) => toNum(row.score))
      .filter((score) => Number.isFinite(score));

    const firstPass = firstPassRows[0] || {
      terminalTotal: 0,
      succeededTotal: 0,
      firstPassSucceeded: 0,
    };
    const terminalTotal = toNum(firstPass.terminalTotal);
    const succeededTotal = toNum(firstPass.succeededTotal);
    const firstPassSucceeded = toNum(firstPass.firstPassSucceeded);

    return {
      meta: this.buildMeta(range, {
        qualityScoreSampleSize: qualityScores.length,
        funScoreSampleSize: funScores.length,
        terminalTasks: terminalTotal,
        sampleCap: MAX_SAMPLE_ROWS,
      }),
      qualityScore: {
        source:
          'generation_tasks.result_summary.qualityScore — persisted only for succeeded create (pipeline_run) tasks; iterate tasks do not record a quality score.',
        ...buildScoreDistribution(qualityScores),
      },
      funScore: {
        source:
          'game_bundles.metadata.qualityBreakdown.review_fun_score — persisted for create-path bundles when the structured code review ran; not stored on generation_tasks or generation_artifacts.',
        ...buildScoreDistribution(funScores),
      },
      qualityGateFirstPass: {
        definition:
          'Approximate first-pass rate: succeeded tasks with retry_count = 0 and result_summary.qaRetries in (0, absent), over all terminal tasks in range.',
        terminalTasks: terminalTotal,
        succeededTasks: succeededTotal,
        firstPassSucceeded,
        firstPassRateOfTerminal: ratio(firstPassSucceeded, terminalTotal),
        firstPassRateOfSucceeded: ratio(firstPassSucceeded, succeededTotal),
      },
    };
  }
}
