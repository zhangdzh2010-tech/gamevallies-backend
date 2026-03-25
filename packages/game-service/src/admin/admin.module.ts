import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { GameModule } from '../game/game.module';
import { AdminController } from './admin.controller';
import { AdminService } from './admin.service';

@Module({
  imports: [PrismaModule, GameModule],
  controllers: [AdminController],
  providers: [AdminService],
})
export class AdminModule {}
