import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { StatsService } from './stats.service';

@Module({
  imports: [PrismaModule],
  providers: [StatsService],
  exports: [StatsService],
})
export class StatsModule {}
