import {
  Body,
  Controller,
  Get,
  Headers,
  HttpCode,
  HttpStatus,
  Param,
  Post,
  Req,
  UseGuards,
} from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { ok } from '../common/api-response';
import { BillingService } from './billing.service';
import { CreateSubscriptionOrderDto } from './dto/create-subscription-order.dto';

@Controller()
export class BillingController {
  constructor(private readonly billingService: BillingService) {}

  private resolveClientIp(req: any): string {
    const forwarded = req.headers?.['x-forwarded-for'];
    const raw = Array.isArray(forwarded) ? forwarded[0] : forwarded || req.ip;
    if (typeof raw !== 'string' || !raw.trim()) {
      return '127.0.0.1';
    }

    return raw.split(',')[0].trim() || '127.0.0.1';
  }

  @Get('subscription/plans')
  async getPlans() {
    return ok(await this.billingService.listPlans());
  }

  @Post('subscription/order')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async createOrder(@Req() req: any, @Body() dto: CreateSubscriptionOrderDto) {
    return ok(
      await this.billingService.createOrder(
        req.user.userId,
        dto,
        this.resolveClientIp(req),
      ),
    );
  }

  @Get('subscription/status')
  @UseGuards(JwtAuthGuard)
  async getSubscriptionStatus(@Req() req: any) {
    return ok(await this.billingService.getSubscriptionStatus(req.user.userId));
  }

  @Get('subscription/orders/:id')
  @UseGuards(JwtAuthGuard)
  async getOrderStatus(@Req() req: any, @Param('id') id: string) {
    return ok(await this.billingService.getOrderStatus(req.user.userId, id));
  }

  @Post('subscription/orders/:id/mock-pay')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async mockPayOrder(@Req() req: any, @Param('id') id: string) {
    return ok(await this.billingService.mockPayOrder(req.user.userId, id));
  }

  @Post('subscription/wechat/notify')
  @HttpCode(HttpStatus.OK)
  async handleWechatNotify(
    @Headers() headers: Record<string, string | string[] | undefined>,
    @Req() req: any,
    @Body() body: unknown,
  ) {
    const rawBody = Buffer.isBuffer(req.rawBody)
      ? req.rawBody.toString('utf8')
      : typeof req.rawBody === 'string'
        ? req.rawBody
        : undefined;

    return this.billingService.handleWechatNotification(
      headers,
      body ?? req.body,
      rawBody,
    );
  }
}
