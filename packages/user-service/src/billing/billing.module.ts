import { Module } from '@nestjs/common';
import { BillingController } from './billing.controller';
import { BillingSchemaBootstrapService } from './billing-schema-bootstrap.service';
import { BillingService } from './billing.service';
import { WechatPayService } from './wechat-pay.service';

@Module({
  controllers: [BillingController],
  providers: [BillingService, WechatPayService, BillingSchemaBootstrapService],
  exports: [BillingService],
})
export class BillingModule {}
