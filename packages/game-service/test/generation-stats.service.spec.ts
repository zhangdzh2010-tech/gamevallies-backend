import { BadRequestException } from '@nestjs/common';
import { GenerationStatsService } from '../src/generation-stats/generation-stats.service';

describe('GenerationStatsService', () => {
  let service: GenerationStatsService;
  let mockPrisma: any;

  beforeEach(() => {
    mockPrisma = {
      $queryRaw: jest.fn().mockResolvedValue([]),
      generationTask: {
        findMany: jest.fn().mockResolvedValue([]),
      },
      generationTaskEvent: {
        findMany: jest.fn().mockResolvedValue([]),
      },
    };
    service = new GenerationStatsService(mockPrisma as any);
  });

  describe('resolveRange()', () => {
    it('defaults to the last 7 days when no bounds are given', () => {
      const before = Date.now();
      const range = service.resolveRange(undefined, undefined);
      const after = Date.now();

      expect(range.toExclusive.getTime()).toBeGreaterThanOrEqual(before);
      expect(range.toExclusive.getTime()).toBeLessThanOrEqual(after);
      expect(range.toExclusive.getTime() - range.from.getTime()).toBe(
        7 * 24 * 60 * 60 * 1000,
      );
    });

    it('treats a date-only "to" as inclusive of that whole day', () => {
      const range = service.resolveRange('2026-07-01', '2026-07-03');
      expect(range.from.toISOString()).toBe('2026-07-01T00:00:00.000Z');
      expect(range.toExclusive.toISOString()).toBe('2026-07-04T00:00:00.000Z');
    });

    it('rejects invalid dates', () => {
      expect(() => service.resolveRange('not-a-date', undefined)).toThrow(
        BadRequestException,
      );
      expect(() => service.resolveRange(undefined, 'nope')).toThrow(
        BadRequestException,
      );
    });

    it('rejects from >= to', () => {
      expect(() =>
        service.resolveRange('2026-07-10T00:00:00Z', '2026-07-01T00:00:00Z'),
      ).toThrow(BadRequestException);
    });

    it('rejects ranges longer than 90 days', () => {
      expect(() => service.resolveRange('2026-01-01', '2026-07-01')).toThrow(
        BadRequestException,
      );
    });

    it('accepts a range of exactly 90 days', () => {
      expect(() =>
        service.resolveRange('2026-04-02T00:00:00Z', '2026-07-01T00:00:00Z'),
      ).not.toThrow();
    });
  });

  describe('parameter validation', () => {
    it('rejects an unknown taskType', () => {
      expect(() => service.resolveTaskType('pipeline_bogus')).toThrow(
        BadRequestException,
      );
    });

    it('accepts valid taskTypes and empty values', () => {
      expect(service.resolveTaskType('pipeline_run')).toBe('pipeline_run');
      expect(service.resolveTaskType('pipeline_iterate')).toBe('pipeline_iterate');
      expect(service.resolveTaskType(undefined)).toBeUndefined();
      expect(service.resolveTaskType('')).toBeUndefined();
    });

    it('rejects an unknown tier', () => {
      expect(() => service.resolveTier('platinum')).toThrow(BadRequestException);
    });

    it('accepts valid tiers and empty values', () => {
      expect(service.resolveTier('safe')).toBe('safe');
      expect(service.resolveTier('standard')).toBe('standard');
      expect(service.resolveTier('showcase')).toBe('showcase');
      expect(service.resolveTier(undefined)).toBeUndefined();
    });

    it('propagates validation errors through getOverview', async () => {
      await expect(
        service.getOverview({ taskType: 'bogus' }),
      ).rejects.toThrow(BadRequestException);
      expect(mockPrisma.$queryRaw).not.toHaveBeenCalled();
    });
  });

  describe('getOverview()', () => {
    it('maps grouped rows into daily buckets, totals and rates', async () => {
      mockPrisma.$queryRaw
        // status rows (BigInt counts, as MySQL COUNT(*) returns)
        .mockResolvedValueOnce([
          { day: '2026-07-01', taskType: 'pipeline_run', status: 'succeeded', cnt: BigInt(8) },
          { day: '2026-07-01', taskType: 'pipeline_run', status: 'failed', cnt: BigInt(2) },
          { day: '2026-07-01', taskType: 'pipeline_iterate', status: 'succeeded', cnt: BigInt(4) },
          { day: '2026-07-02', taskType: 'pipeline_run', status: 'timed_out', cnt: BigInt(1) },
          { day: '2026-07-02', taskType: 'pipeline_run', status: 'running', cnt: BigInt(3) },
        ])
        // failure family rows
        .mockResolvedValueOnce([
          { taskType: 'pipeline_run', family: 'quality_gate', cnt: BigInt(2) },
          { taskType: 'pipeline_run', family: 'unknown', cnt: BigInt(1) },
        ]);

      const result = await service.getOverview({
        from: '2026-07-01',
        to: '2026-07-03',
      });

      expect(result.meta.totalTasks).toBe(18);
      expect(result.daily).toHaveLength(2);

      const day1Run = result.daily[0].byTaskType['pipeline_run'];
      expect(day1Run.total).toBe(10);
      expect(day1Run.succeeded).toBe(8);
      expect(day1Run.failed).toBe(2);
      expect(day1Run.terminal).toBe(10);
      expect(day1Run.succeededRate).toBeCloseTo(0.8);
      expect(day1Run.failedRate).toBeCloseTo(0.2);

      const day2Run = result.daily[1].byTaskType['pipeline_run'];
      expect(day2Run.total).toBe(4);
      expect(day2Run.running).toBe(3);
      expect(day2Run.terminal).toBe(1);
      expect(day2Run.timedOutRate).toBe(1);

      expect(result.totals['pipeline_run'].total).toBe(14);
      expect(result.totals['pipeline_iterate'].succeededRate).toBe(1);

      expect(result.failureFamilies['pipeline_run']).toEqual([
        { family: 'quality_gate', count: 2 },
        { family: 'unknown', count: 1 },
      ]);
    });

    it('returns an empty but well-formed payload when there is no data', async () => {
      const result = await service.getOverview({});
      expect(result.meta.totalTasks).toBe(0);
      expect(result.daily).toEqual([]);
      expect(result.totals).toEqual({});
      expect(result.failureFamilies).toEqual({});
    });
  });

  describe('getLatency()', () => {
    it('computes end-to-end percentiles per task type', async () => {
      const durationRows = [
        ...Array.from({ length: 9 }, (_, i) => ({
          taskType: 'pipeline_run',
          status: 'succeeded',
          durationMs: BigInt((i + 1) * 1000),
        })),
        { taskType: 'pipeline_run', status: 'failed', durationMs: BigInt(100000) },
      ];
      mockPrisma.$queryRaw.mockResolvedValueOnce(durationRows);
      // No sampled tasks -> stage latency unavailable branch.
      mockPrisma.generationTask.findMany.mockResolvedValueOnce([]);

      const result = await service.getLatency({
        from: '2026-07-01',
        to: '2026-07-03',
      });

      expect(result.meta.sampleSize).toBe(10);
      expect(result.endToEnd.all.sampleSize).toBe(10);
      expect(result.endToEnd.all.p50Ms).toBe(5500);

      const run = result.endToEnd.byTaskType['pipeline_run'];
      expect(run.allTerminal.sampleSize).toBe(10);
      expect(run.succeeded.sampleSize).toBe(9);
      expect(run.succeeded.p50Ms).toBe(5000);
      expect(run.succeeded.p90Ms).toBe(8200);

      expect(result.stageLatency.available).toBe(false);
      expect(result.stageLatency.stages).toEqual([]);
    });

    it('reconstructs per-stage durations from task events', async () => {
      mockPrisma.$queryRaw.mockResolvedValueOnce([]);
      mockPrisma.generationTask.findMany.mockResolvedValueOnce([
        { id: 'task-1' },
        { id: 'task-2' },
      ]);
      const base = new Date('2026-07-01T00:00:00Z').getTime();
      mockPrisma.generationTaskEvent.findMany.mockResolvedValueOnce([
        { taskId: 'task-1', stage: 'spec_build', createdAt: new Date(base) },
        { taskId: 'task-1', stage: 'code_gen', createdAt: new Date(base + 2000) },
        { taskId: 'task-1', stage: 'completed', createdAt: new Date(base + 12000) },
        { taskId: 'task-2', stage: 'spec_build', createdAt: new Date(base) },
        { taskId: 'task-2', stage: 'code_gen', createdAt: new Date(base + 4000) },
        { taskId: 'task-2', stage: 'completed', createdAt: new Date(base + 24000) },
      ]);

      const result = await service.getLatency({
        from: '2026-07-01',
        to: '2026-07-03',
      });

      expect(result.stageLatency.available).toBe(true);
      expect(result.stageLatency.sampledTasks).toBe(2);

      const stages = Object.fromEntries(
        result.stageLatency.stages.map((s: any) => [s.stage, s]),
      );
      expect(stages['spec_build'].sampleSize).toBe(2);
      expect(stages['spec_build'].p50Ms).toBe(3000);
      expect(stages['code_gen'].p50Ms).toBe(15000);
      // The final stage has no successor event, so it never appears.
      expect(stages['completed']).toBeUndefined();
    });

    it('handles a completely empty range without throwing', async () => {
      const result = await service.getLatency({});
      expect(result.endToEnd.all.sampleSize).toBe(0);
      expect(result.endToEnd.all.p50Ms).toBeNull();
      expect(result.stageLatency.available).toBe(false);
    });
  });

  describe('getQuality()', () => {
    it('builds score distributions and the first-pass metric', async () => {
      mockPrisma.$queryRaw
        // qualityScore rows (DECIMAL comes back as string/Decimal-like)
        .mockResolvedValueOnce([
          { score: '8.5' },
          { score: '7.0' },
          { score: '5.5' },
          { score: '9.0' },
        ])
        // fun score rows
        .mockResolvedValueOnce([{ score: '7.5' }, { score: '6.0' }])
        // first-pass aggregate
        .mockResolvedValueOnce([
          {
            terminalTotal: BigInt(20),
            succeededTotal: BigInt(15),
            firstPassSucceeded: BigInt(12),
          },
        ]);

      const result = await service.getQuality({
        from: '2026-07-01',
        to: '2026-07-03',
      });

      expect(result.qualityScore.sampleSize).toBe(4);
      expect(result.qualityScore.mean).toBeCloseTo(7.5);
      expect(result.qualityScore.p50).toBeCloseTo(7.75);
      const buckets = Object.fromEntries(
        result.qualityScore.buckets.map((b: any) => [b.range, b.count]),
      );
      expect(buckets['[4,6)']).toBe(1);
      expect(buckets['[6,8)']).toBe(1);
      expect(buckets['[8,10]']).toBe(2);

      expect(result.funScore.sampleSize).toBe(2);
      expect(result.funScore.mean).toBeCloseTo(6.75);

      expect(result.qualityGateFirstPass.terminalTasks).toBe(20);
      expect(result.qualityGateFirstPass.succeededTasks).toBe(15);
      expect(result.qualityGateFirstPass.firstPassSucceeded).toBe(12);
      expect(result.qualityGateFirstPass.firstPassRateOfTerminal).toBeCloseTo(0.6);
      expect(result.qualityGateFirstPass.firstPassRateOfSucceeded).toBeCloseTo(0.8);
    });

    it('returns nulls and zero counts when there is no data', async () => {
      const result = await service.getQuality({});
      expect(result.qualityScore.sampleSize).toBe(0);
      expect(result.qualityScore.mean).toBeNull();
      expect(result.qualityScore.p50).toBeNull();
      expect(result.funScore.sampleSize).toBe(0);
      expect(result.qualityGateFirstPass.terminalTasks).toBe(0);
      expect(result.qualityGateFirstPass.firstPassRateOfTerminal).toBeNull();
    });
  });
});
