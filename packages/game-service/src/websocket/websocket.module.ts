import { Module } from '@nestjs/common';
import { GameWebSocketGateway } from './websocket.gateway';

@Module({
  providers: [GameWebSocketGateway],
  exports: [GameWebSocketGateway],
})
export class WebSocketModule {}
