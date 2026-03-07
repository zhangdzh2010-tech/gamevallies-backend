import {
  Controller,
  Get,
  Query,
} from '@nestjs/common';
import { Type } from 'class-transformer';
import { IsNumber, Min, Max } from 'class-validator';
import { TagService } from './tag.service';
import { ok } from '../common/api-response';
import { presentTag } from '../common/feed-presenter';

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

@Controller('tags')
export class TagController {
  constructor(private readonly tagService: TagService) {}

  @Get('trending')
  async getTrendingTags(@Query() pagination: PaginationDto) {
    const result = await this.tagService.getTrendingTags(pagination.page, pagination.limit);
    return ok({
      items: result.data.map((tag: any) => presentTag(tag)),
      hasMore: result.pagination.page < result.pagination.pages,
      page: result.pagination.page,
      limit: result.pagination.limit,
      total: result.pagination.total,
    });
  }

  @Get('all')
  async getAllTags(@Query() pagination: PaginationDto) {
    const result = await this.tagService.getAllTags(pagination.page, pagination.limit);
    return ok({
      items: result.data.map((tag: any) => presentTag(tag)),
      hasMore: result.pagination.page < result.pagination.pages,
      page: result.pagination.page,
      limit: result.pagination.limit,
      total: result.pagination.total,
    });
  }
}
