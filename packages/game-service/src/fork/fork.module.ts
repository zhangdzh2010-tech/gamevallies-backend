import { Module } from '@nestjs/common';
import { ForkService } from './fork.service';
import { ForkController } from './fork.controller';
import { PrismaModule } from '../prisma/prisma.module';

@Module({
  imports: [PrismaModule],
  providers: [ForkService],
  controllers: [ForkController],
})
export class ForkModule {}
