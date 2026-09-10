import { FeedModule } from '../../../feed-service/dist/feed/feed.module';
import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { GameModule } from '../game/game.module';
import { AdminController } from './admin.controller';
import { SubscriptionGrantService } from './subscription-grant.service';
import { CreationQuotaGrantService } from './creation-quota-grant.service';
import { AdminService } from './admin.service';

@Module({
  imports: [FeedModule, PrismaModule, GameModule],
  controllers: [AdminController],
  providers: [AdminService, SubscriptionGrantService, CreationQuotaGrantService],
})
export class AdminModule {}
