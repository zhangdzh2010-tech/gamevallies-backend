import { Module } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { GameService } from './game.service';
import { GameController } from './game.controller';
import { GameContentController } from './game-content.controller';
import { GameShellController } from './game-shell.controller';
import { GameSchemaBootstrapService } from './game-schema-bootstrap.service';
import { InternalGenerationController } from './internal-generation.controller';
import { CreatorReputationService } from './creator-reputation.service';
import { GenerationQueueWorkerService } from './generation-queue.worker';
import { CreationSessionService } from './creation-session.service';
import { CreationSessionRealtimeService } from './creation-session-realtime.service';
import { PrismaModule } from '../prisma/prisma.module';
import { BundleModule } from '../bundle/bundle.module';
import { BundleCdnModule } from '../bundle-cdn/bundle-cdn.module';
import { StatsModule } from '../stats/stats.module';
import { PlatformConfigModule } from '../platform/config/config.module';
import { TaskingModule } from '../platform/tasking/tasking.module';
import { WebSocketModule } from '../websocket/websocket.module';

@Module({
  imports: [
    PrismaModule,
    BundleModule,
    BundleCdnModule,
    StatsModule,
    PlatformConfigModule,
    TaskingModule,
    WebSocketModule,
    JwtModule.registerAsync({
      useFactory: (configService: ConfigService) => ({
        secret: configService.get<string>('JWT_SECRET', 'your-secret-key'),
        signOptions: { expiresIn: 86400 },
      }),
      inject: [ConfigService],
    }),
  ],
  providers: [
    GameService,
    CreatorReputationService,
    GameSchemaBootstrapService,
    GenerationQueueWorkerService,
    CreationSessionService,
    CreationSessionRealtimeService,
  ],
  controllers: [GameController, GameContentController, GameShellController, InternalGenerationController],
  exports: [GameService, CreatorReputationService, CreationSessionService, CreationSessionRealtimeService],
})
export class GameModule {}
