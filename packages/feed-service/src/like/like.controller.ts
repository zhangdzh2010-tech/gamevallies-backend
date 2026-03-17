import {
  Controller,
  Post,
  Get,
  Body,
  UseGuards,
  Param,
  Request,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { IsString, IsEnum } from 'class-validator';
import { LikeService } from './like.service';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok } from '../common/api-response';

class LikeDto {
  @IsEnum(['game', 'comment'])
  targetType: 'game' | 'comment';

  @IsString()
  targetId: string;
}

@Controller('social')
export class LikeController {
  constructor(private readonly likeService: LikeService) {}

  @Post('like')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async toggleLike(
    @Body() dto: LikeDto,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id || 'anonymous';
    const result = await this.likeService.toggleLike(userId, dto.targetType, dto.targetId);
    return ok({
      liked: result.liked,
      likes: result.likeCount,
    });
  }

  @Get('like-status/:type/:id')
  @UseGuards(JwtAuthGuard)
  async getLikeStatus(
    @Param('type') type: 'game' | 'comment',
    @Param('id') id: string,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id || 'anonymous';
    return ok(await this.likeService.getLikeStatus(userId, type, id));
  }
}
