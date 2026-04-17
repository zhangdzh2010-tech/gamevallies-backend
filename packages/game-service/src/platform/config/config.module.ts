import { Module } from '@nestjs/common';
import { PrismaModule } from '../../prisma/prisma.module';
import { RuntimeProfileService } from './runtime-profile.service';
import { SystemConfigRepository } from './system-config.repository';
import { TimeoutConfigService } from './timeout-config.service';

@Module({
  imports: [PrismaModule],
  providers: [
    SystemConfigRepository,
    TimeoutConfigService,
    RuntimeProfileService,
  ],
  exports: [
    SystemConfigRepository,
    TimeoutConfigService,
    RuntimeProfileService,
  ],
})
export class PlatformConfigModule {}
