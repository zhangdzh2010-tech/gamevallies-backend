import {
  Controller,
  Get,
  Query,
} from '@nestjs/common';
import { Type } from 'class-transformer';
import { IsNumber, Min, Max } from 'class-validator';
import { CreatorsService } from './creators.service';
import { ok } from '../common/api-response';
import { presentCreator } from '../common/feed-presenter';

class PaginationDto {
  @Type(() => Number)
  @IsNumber()
  @Min(1)
  page: number = 1;

  @Type(() => Number)
  @IsNumber()
  @Min(1)
  @Max(100)
  limit: number = 20;
}

@Controller('creators')
export class CreatorsController {
  constructor(private readonly creatorsService: CreatorsService) {}

  @Get('trending')
  async getTrendingCreators(@Query() pagination: PaginationDto) {
    const result = await this.creatorsService.getTrendingCreators(
      pagination.page,
      pagination.limit,
    );

    return ok({
      items: result.data.map((creator: any) => presentCreator(creator)),
      hasMore: result.pagination.page < result.pagination.pages,
      page: result.pagination.page,
      limit: result.pagination.limit,
      total: result.pagination.total,
    });
  }
}
