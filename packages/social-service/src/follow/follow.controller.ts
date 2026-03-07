import {
  Controller,
  Post,
  Delete,
  Get,
  Body,
  Param,
  Query,
  UseGuards,
  Request,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { IsString, IsNumber, Min, Max } from 'class-validator';
import { Type } from 'class-transformer';
import { FollowService } from './follow.service';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok, toPage } from '../common/api-response';
import { presentUser } from '../common/social-presenter';

class FollowDto {
  @IsString()
  targetId: string;
}

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

@Controller('social')
export class FollowController {
  constructor(private readonly followService: FollowService) {}

  @Post('follow')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async toggleFollow(
    @Body() dto: FollowDto,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id || 'anonymous';
    await this.followService.followUser(userId, dto.targetId);
    return ok(null);
  }

  @Delete('follow/:userId')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async unfollow(
    @Param('userId') targetId: string,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id || 'anonymous';
    await this.followService.unfollowUser(userId, targetId);
    return ok(null);
  }

  @Get('followers/:userId')
  async getFollowers(
    @Param('userId') userId: string,
    @Query() pagination: PaginationDto,
  ) {
    const result = await this.followService.getFollowers(userId, pagination.page, pagination.limit);
    return ok(
      toPage(
        result.data.map((user: any) => presentUser(user)),
        result.pagination.page,
        result.pagination.limit,
        result.pagination.total,
      ),
    );
  }

  @Get('following/:userId')
  async getFollowing(
    @Param('userId') userId: string,
    @Query() pagination: PaginationDto,
  ) {
    const result = await this.followService.getFollowing(userId, pagination.page, pagination.limit);
    return ok(
      toPage(
        result.data.map((user: any) => presentUser(user)),
        result.pagination.page,
        result.pagination.limit,
        result.pagination.total,
      ),
    );
  }

  @Get('follow-status/:userId')
  @UseGuards(JwtAuthGuard)
  async getFollowStatus(
    @Param('userId') targetId: string,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id || 'anonymous';
    return ok(await this.followService.getFollowStatus(userId, targetId));
  }
}
