import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { GameModule } from '../game/game.module';
import { ForkModule } from '../fork/fork.module';
import { BundleModule } from '../bundle/bundle.module';
import { CreationSessionController } from './creation-session.controller';
import { CreationSessionService } from './creation-session.service';

@Module({
  imports: [PrismaModule, GameModule, ForkModule, BundleModule],
  controllers: [CreationSessionController],
  providers: [CreationSessionService],
  exports: [CreationSessionService],
})
export class CreationSessionModule {}

