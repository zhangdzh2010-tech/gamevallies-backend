import {
  Controller,
  Get,
  Post,
  Headers,
  HttpException,
  HttpStatus,
  Param,
  Query,
  Request,
  UseGuards,
} from '@nestjs/common';
import { IsNumber, Min, Max, IsOptional, IsString } from 'class-validator';
import { Type } from 'class-transformer';
import { FeedService } from './feed.service';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok, toPage } from '../common/api-response';
import { presentGame } from '../common/feed-presenter';

class FeedPaginationDto {
  @IsOptional()
  @IsString()
  q?: string;

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

class FilteredFeedDto extends FeedPaginationDto {
  @IsOptional()
  @IsString()
  gameType?: string;

  @IsOptional()
  @IsString()
  category?: string;
}

function assertAdminToken(token?: string) {
  const expected = (process.env.ADMIN_TOKEN || '').trim();
  if (!expected) {
    throw new HttpException('Admin token is not configured', HttpStatus.SERVICE_UNAVAILABLE);
  }
  if (!token || token !== expected) {
    throw new HttpException('Unauthorized', HttpStatus.UNAUTHORIZED);
  }
}

@Controller('feed')
export class FeedController {
  constructor(private readonly feedService: FeedService) {}

  @Get('trending')
  async getTrendingFeed(@Query() pagination: FeedPaginationDto) {
    const result = await this.feedService.getTrendingFeed(pagination.page, pagination.limit, pagination.q);
    return ok(toPage({
      data: result.data.map((game: any) => presentGame(game)),
      pagination: result.pagination,
    }));
  }

  @Get('latest')
  async getLatestFeed(@Query() pagination: FeedPaginationDto) {
    const result = await this.feedService.getLatestFeed(pagination.page, pagination.limit, pagination.q);
    return ok(toPage({
      data: result.data.map((game: any) => presentGame(game)),
      pagination: result.pagination,
    }));
  }

  @Get('featured')
  async getFeaturedFeed(@Query() pagination: FeedPaginationDto) {
    const result = await this.feedService.getFeaturedFeed(pagination.page, pagination.limit, pagination.q);
    return ok(toPage({
      data: result.data.map((game: any) => presentGame(game)),
      pagination: result.pagination,
    }));
  }

  @Get('following')
  @UseGuards(JwtAuthGuard)
  async getFollowingFeed(
    @Query() pagination: FeedPaginationDto,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id;
    const result = await this.feedService.getFollowingFeed(userId, pagination.page, pagination.limit, pagination.q);
    return ok(toPage({
      data: result.data.map((game: any) => presentGame(game)),
      pagination: result.pagination,
    }));
  }

  @Get('category')
  async getFeedByCategory(@Query() query: FilteredFeedDto) {
    const category = query.gameType || query.category;
    const result = await this.feedService.getFeedByType(category || 'casual', query.page, query.limit, query.q);
    return ok(toPage({
      data: result.data.map((game: any) => presentGame(game)),
      pagination: result.pagination,
    }));
  }

  @Get('by-type/:type')
  async getFeedByTypeLegacy(
    @Param('type') type: string,
    @Query() query: FilteredFeedDto,
  ) {
    const result = await this.feedService.getFeedByType(
      type || query.gameType || 'casual',
      query.page,
      query.limit,
      query.q,
    );
    return ok(toPage({
      data: result.data.map((game: any) => presentGame(game)),
      pagination: result.pagination,
    }));
  }

  @Post('internal/cache/invalidate')
  async invalidateCache(@Headers('x-admin-token') token?: string) {
    assertAdminToken(token);
    return ok(await this.feedService.invalidateGameFeedCache());
  }
}
