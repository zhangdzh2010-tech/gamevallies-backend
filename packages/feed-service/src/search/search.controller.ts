import {
  Controller,
  Get,
  Query,
} from '@nestjs/common';
import { Type } from 'class-transformer';
import { IsNumber, Min, Max, IsOptional, IsString } from 'class-validator';
import { SearchService } from './search.service';
import { ok, toPage } from '../common/api-response';
import { presentGame } from '../common/feed-presenter';

class SearchQueryDto {
  @IsString()
  q: string;

  @IsOptional()
  @IsString()
  gameType?: string;

  @IsOptional()
  @IsString()
  tags?: string;

  @Type(() => Number)
  @IsNumber()
  @Min(1)
  page: number = 1;

  @Type(() => Number)
  @IsNumber()
  @Min(1)
  @Max(100)
  limit: number = 20;

  @IsOptional()
  @IsString()
  sortBy: 'relevance' | 'popularity' = 'relevance';
}

@Controller('feed/search')
export class SearchController {
  constructor(private readonly searchService: SearchService) {}

  @Get()
  async search(@Query() query: SearchQueryDto) {
    const tags = query.tags ? query.tags.split(',').map((t) => t.trim()) : [];
    const result = await this.searchService.searchGames(
      query.q,
      query.gameType,
      tags,
      query.page,
      query.limit,
      query.sortBy,
    );

    return ok(toPage({
      data: result.data.map((game: any) => presentGame(game)),
      pagination: result.pagination,
    }));
  }
}
