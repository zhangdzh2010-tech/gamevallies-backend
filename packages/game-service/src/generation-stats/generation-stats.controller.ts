import { Controller, Get, Headers, Query } from '@nestjs/common';
import { ok } from '../common/api-response';
import { checkAdminToken } from '../common/admin-auth';
import { GenerationStatsService } from './generation-stats.service';

/**
 * Read-only observability endpoints for the generation pipeline.
 * Served under /api/v1/internal/generation/stats/* and guarded by the same
 * x-admin-token used by the other internal generation endpoints.
 */
@Controller('internal/generation/stats')
export class GenerationStatsController {
  constructor(
    private readonly generationStatsService: GenerationStatsService,
  ) {}

  @Get('overview')
  async getOverview(
    @Headers('x-admin-token') token: string,
    @Query('from') from?: string,
    @Query('to') to?: string,
    @Query('taskType') taskType?: string,
    @Query('tier') tier?: string,
  ) {
    checkAdminToken(token);
    return ok(
      await this.generationStatsService.getOverview({ from, to, taskType, tier }),
    );
  }

  @Get('latency')
  async getLatency(
    @Headers('x-admin-token') token: string,
    @Query('from') from?: string,
    @Query('to') to?: string,
    @Query('taskType') taskType?: string,
  ) {
    checkAdminToken(token);
    return ok(
      await this.generationStatsService.getLatency({ from, to, taskType }),
    );
  }

  @Get('quality')
  async getQuality(
    @Headers('x-admin-token') token: string,
    @Query('from') from?: string,
    @Query('to') to?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.generationStatsService.getQuality({ from, to }));
  }
}
