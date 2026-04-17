import {
  Body,
  Controller,
  Get,
  Headers,
  Param,
  Post,
  Put,
  Query,
  UploadedFile,
  UseInterceptors,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { ok } from '../common/api-response';
import { checkAdminToken } from '../common/admin-auth';
import { GrowthService } from './growth.service';

@Controller('admin')
export class GrowthAdminController {
  constructor(private readonly growthService: GrowthService) {}

  @Get('app-promo/config')
  async getAppPromoConfig(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.growthService.getAppPromoConfig());
  }

  @Put('app-promo/config')
  async updateAppPromoConfig(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.growthService.updateAppPromoConfig(body || {}), 'Promo config updated');
  }

  @Get('app-promo/events')
  async listPromoEvents(
    @Headers('x-admin-token') token: string,
    @Query('page') page?: string,
    @Query('limit') limit?: string,
    @Query('scene') scene?: string,
    @Query('eventType') eventType?: string,
    @Query('platform') platform?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.growthService.listPromoEvents({
      page,
      limit,
      scene,
      eventType,
      platform,
    }));
  }

  @Get('app-releases')
  async listAppReleases(
    @Headers('x-admin-token') token: string,
    @Query('page') page?: string,
    @Query('limit') limit?: string,
    @Query('platform') platform?: string,
    @Query('status') status?: string,
    @Query('channel') channel?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.growthService.listAppReleases({
      page,
      limit,
      platform,
      status,
      channel,
    }));
  }

  @Get('app-releases/:id')
  async getAppRelease(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
  ) {
    checkAdminToken(token);
    return ok(await this.growthService.getAppRelease(id));
  }

  @Post('app-releases')
  async createAppRelease(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.growthService.createAppRelease(body || {}), 'App release created');
  }

  @Put('app-releases/:id')
  async updateAppRelease(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.growthService.updateAppRelease(id, body || {}), 'App release updated');
  }

  @Post('app-releases/:id/publish')
  async publishAppRelease(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
  ) {
    checkAdminToken(token);
    return ok(await this.growthService.publishAppRelease(id), 'App release published');
  }

  @Post('app-releases/:id/upload')
  @UseInterceptors(FileInterceptor('file'))
  async uploadAppReleasePackage(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @UploadedFile() file: any,
  ) {
    checkAdminToken(token);
    return ok(
      await this.growthService.uploadReleasePackage(id, file),
      'Release package uploaded',
    );
  }
}
