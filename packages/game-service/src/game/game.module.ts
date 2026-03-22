import { Module } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { GameService } from './game.service';
import { GameController } from './game.controller';
import { GameContentController } from './game-content.controller';
import { GameSchemaBootstrapService } from './game-schema-bootstrap.service';
import { InternalGenerationController } from './internal-generation.controller';
import { CreatorReputationService } from './creator-reputation.service';
import { GenerationTaskService } from './generation-task.service';
import { PrismaModule } from '../prisma/prisma.module';
import { BundleModule } from '../bundle/bundle.module';
import { StatsModule } from '../stats/stats.module';
import { WebSocketModule } from '../websocket/websocket.module';

@Module({
  imports: [
    PrismaModule,
    BundleModule,
    StatsModule,
    WebSocketModule,
    JwtModule.registerAsync({
      useFactory: (configService: ConfigService) => ({
        secret: configService.get<string>('JWT_SECRET', 'your-secret-key'),
        signOptions: { expiresIn: 86400 },
      }),
      inject: [ConfigService],
    }),
  ],
  providers: [GameService, CreatorReputationService, GameSchemaBootstrapService, GenerationTaskService],
  controllers: [GameController, GameContentController, InternalGenerationController],
  exports: [GameService, CreatorReputationService],
})
export class GameModule {}
