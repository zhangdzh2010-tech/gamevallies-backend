import {
  Body,
  Controller,
  Get,
  Headers,
  Param,
  Post,
  Req,
  Res,
} from '@nestjs/common';
import { Request, Response } from 'express';
import { ok } from '../common/api-response';
import { GrowthService } from './growth.service';

function resolveClientIp(req: Request, forwardedForHeader: string | string[] | undefined): string | undefined {
  const forwardedRaw = Array.isArray(forwardedForHeader)
    ? forwardedForHeader[0]
    : forwardedForHeader;
  const forwardedIp = String(forwardedRaw || '')
    .split(',')
    .map((item) => item.trim())
    .find(Boolean);

  return forwardedIp || req.ip || req.socket?.remoteAddress;
}

@Controller('growth')
export class GrowthController {
  constructor(private readonly growthService: GrowthService) {}

  @Get('app-promo/bootstrap')
  async getAppPromoBootstrap() {
    return ok(await this.growthService.getAppPromoBootstrap());
  }

  @Post('app-promo/events')
  async recordPromoEvent(
    @Body() body: any,
    @Req() req: Request,
    @Headers('x-forwarded-for') forwardedFor: string | string[] | undefined,
  ) {
    const event = await this.growthService.recordPromoEvent(body || {}, {
      userAgent: req.get('user-agent') || undefined,
      ip: resolveClientIp(req, forwardedFor),
    });
    return ok({ id: event.id }, 'Promo event recorded');
  }

  @Get('app-releases/:id/download')
  async downloadAppRelease(
    @Param('id') id: string,
    @Res() res: Response,
  ) {
    const result = await this.growthService.resolveReleaseDownload(id);

    if (result.type === 'redirect') {
      return res.redirect(result.downloadUrl);
    }

    if (result.release.mimeType) {
      res.type(result.release.mimeType);
    }

    return res.download(
      result.absolutePath,
      result.release.fileName || undefined,
    );
  }
}
