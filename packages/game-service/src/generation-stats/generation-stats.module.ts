import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { GenerationStatsController } from './generation-stats.controller';
import { GenerationStatsService } from './generation-stats.service';

@Module({
  imports: [PrismaModule],
  controllers: [GenerationStatsController],
  providers: [GenerationStatsService],
})
export class GenerationStatsModule {}
