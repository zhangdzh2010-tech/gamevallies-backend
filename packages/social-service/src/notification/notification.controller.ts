import {
  Controller,
  Post,
  Get,
  Body,
  Query,
  UseGuards,
  Request,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { IsArray, IsString, IsNumber, Min, Max } from 'class-validator';
import { Type } from 'class-transformer';
import { NotificationService } from './notification.service';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok, toPage } from '../common/api-response';
import { presentNotification } from '../common/social-presenter';

class MarkAsReadDto {
  @IsArray()
  @IsString({ each: true })
  ids: string[];
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

@Controller('notifications')
export class NotificationController {
  constructor(private readonly notificationService: NotificationService) {}

  @Get()
  @UseGuards(JwtAuthGuard)
  async getNotifications(
    @Query() pagination: PaginationDto,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id;
    const result = await this.notificationService.getNotifications(userId, pagination.page, pagination.limit);
    return ok(
      toPage(
        result.data.map((notification: any) => presentNotification(notification)),
        result.pagination.page,
        result.pagination.limit,
        result.pagination.total,
      ),
    );
  }

  @Post('mark-read')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async markAsRead(
    @Body() dto: MarkAsReadDto,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id;
    await this.notificationService.markAsRead(userId, dto.ids);
    return ok(null);
  }

  @Post('read')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async markAsReadLegacy(
    @Body() dto: MarkAsReadDto,
    @Request() req: any,
  ) {
    return this.markAsRead(dto, req);
  }

  @Post('mark-all-read')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async markAllAsRead(@Request() req: any) {
    const userId = req.user?.sub || req.user?.id;
    await this.notificationService.markAllAsRead(userId);
    return ok(null);
  }

  @Post('read-all')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async markAllAsReadLegacy(@Request() req: any) {
    return this.markAllAsRead(req);
  }

  @Get('unread-count')
  @UseGuards(JwtAuthGuard)
  async getUnreadCount(@Request() req: any) {
    const userId = req.user?.sub || req.user?.id;
    return ok(await this.notificationService.getUnreadCount(userId));
  }
}
