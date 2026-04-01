import {
  Controller,
  Get,
  Param,
  Query,
  HttpCode,
  HttpStatus,
  Logger,
} from '@nestjs/common';
import { ForkService } from './fork.service';
import { ok, toPage } from '../common/api-response';
import { presentGame } from '../common/game-presenter';

@Controller('games')
export class ForkController {
  private readonly logger = new Logger(ForkController.name);

  constructor(private forkService: ForkService) {}

  @Get(':id/forks')
  @HttpCode(HttpStatus.OK)
  async getForks(
    @Param('id') id: string,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '10',
  ) {
    try {
      const pageNum = Math.max(1, parseInt(page) || 1);
      const limitNum = Math.min(100, Math.max(1, parseInt(limit) || 10));

      const result = await this.forkService.getForks(id, pageNum, limitNum);
      return ok(toPage({
        data: result.data.map((game: any) => presentGame(game)),
        pagination: result.pagination,
      }));
    } catch (error) {
      this.logger.error(`Error getting forks: ${error.message}`);
      throw error;
    }
  }

  @Get(':id/fork-tree')
  @HttpCode(HttpStatus.OK)
  async getForkTree(@Param('id') id: string) {
    try {
      const result = await this.forkService.getForkTree(id);
      return ok({
        game: presentGame(result.game),
        parent: result.parent ? presentGame(result.parent) : null,
        children: result.children.map((game: any) => presentGame(game)),
      });
    } catch (error) {
      this.logger.error(`Error getting fork tree: ${error.message}`);
      throw error;
    }
  }

  @Get(':id/fork-lineage')
  @HttpCode(HttpStatus.OK)
  async getForkLineage(@Param('id') id: string) {
    try {
      const lineage = await this.forkService.getForkLineage(id);
      return ok({
        lineage,
      });
    } catch (error) {
      this.logger.error(`Error getting fork lineage: ${error.message}`);
      throw error;
    }
  }
}
