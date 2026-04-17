import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { HealthController } from './health.controller';
import { GameModule } from './game/game.module';
import { ForkModule } from './fork/fork.module';
import { BundleModule } from './bundle/bundle.module';
import { StatsModule } from './stats/stats.module';
import { WebSocketModule } from './websocket/websocket.module';
import { PrismaModule } from './prisma/prisma.module';
import { BundleStorageModule } from './bundle-storage/bundle-storage.module';
import { AdminModule } from './admin/admin.module';
import { GrowthModule } from './growth/growth.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: '.env',
    }),
    PrismaModule,
    BundleStorageModule,
    GameModule,
    ForkModule,
    BundleModule,
    StatsModule,
    WebSocketModule,
    AdminModule,
    GrowthModule,
  ],
  controllers: [HealthController],
})
export class AppModule {}
