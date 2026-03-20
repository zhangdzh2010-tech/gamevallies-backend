import { Module } from '@nestjs/common';
import { HealthController } from './health.controller';
import { HealthService } from './health.service';
import { WechatVerificationController } from './wechat-verification.controller';

@Module({
  controllers: [HealthController, WechatVerificationController],
  providers: [HealthService],
})
export class HealthModule {}
