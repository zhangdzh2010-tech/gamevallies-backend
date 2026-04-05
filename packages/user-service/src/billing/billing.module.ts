import { Module } from '@nestjs/common';
import { AlipayPayService } from './alipay-pay.service';
import { BillingController } from './billing.controller';
import { BillingSchemaBootstrapService } from './billing-schema-bootstrap.service';
import { BillingService } from './billing.service';
import { WechatPayService } from './wechat-pay.service';

@Module({
  controllers: [BillingController],
  providers: [
    BillingService,
    WechatPayService,
    AlipayPayService,
    BillingSchemaBootstrapService,
  ],
  exports: [BillingService],
})
export class BillingModule {}
