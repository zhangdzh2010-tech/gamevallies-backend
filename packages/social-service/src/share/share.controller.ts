import {
  Controller,
  Post,
  Body,
  Request,
  HttpCode,
  HttpStatus,
  UseGuards,
} from '@nestjs/common';
import { IsString, IsEnum } from 'class-validator';
import { ShareService } from './share.service';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok } from '../common/api-response';

class ShareDto {
  @IsString()
  gameId: string;

  @IsEnum(['wechat', 'weibo', 'link'])
  platform: 'wechat' | 'weibo' | 'link';
}

@Controller('social')
export class ShareController {
  constructor(private readonly shareService: ShareService) {}

  @Post('share')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async share(
    @Body() dto: ShareDto,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id;
    await this.shareService.recordShare(userId, dto.gameId, dto.platform);
    return ok(null);
  }
}

