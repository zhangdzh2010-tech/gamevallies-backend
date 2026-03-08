import { Module } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { GameService } from './game.service';
import { GameController } from './game.controller';
import { GameContentController } from './game-content.controller';
import { CreatorReputationService } from './creator-reputation.service';
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
  providers: [GameService, CreatorReputationService],
  controllers: [GameController, GameContentController],
  exports: [GameService, CreatorReputationService],
})
export class GameModule {}
