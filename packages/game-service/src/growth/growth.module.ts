import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { GrowthAdminController } from './growth.admin.controller';
import { GrowthController } from './growth.controller';
import { GrowthService } from './growth.service';

@Module({
  imports: [PrismaModule],
  controllers: [GrowthController, GrowthAdminController],
  providers: [GrowthService],
  exports: [GrowthService],
})
export class GrowthModule {}
