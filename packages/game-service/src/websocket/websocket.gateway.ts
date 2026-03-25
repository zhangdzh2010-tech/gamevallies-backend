import {
  WebSocketGateway,
  SubscribeMessage,
  OnGatewayConnection,
  OnGatewayDisconnect,
  WebSocketServer,
} from '@nestjs/websockets';
import { Server, Socket } from 'socket.io';
import { Logger } from '@nestjs/common';

interface ClientMap {
  [clientId: string]: string;
}

interface UserClientsMap {
  [userId: string]: string[];
}

@WebSocketGateway({
  namespace: '/ws',
  cors: {
    origin: '*',
    credentials: true,
  },
})
export class GameWebSocketGateway implements OnGatewayConnection, OnGatewayDisconnect {
  @WebSocketServer() server: Server;

  private readonly logger = new Logger(GameWebSocketGateway.name);
  private clientToUserMap: ClientMap = {};
  private userToClientsMap: UserClientsMap = {};

  handleConnection(client: Socket) {
    try {
      const token = client.handshake.query.token as string;

      if (!token) {
        this.logger.warn('Connection attempt without token');
        client.disconnect();
        return;
      }

      try {
        const decoded = JSON.parse(Buffer.from(token.split('.')[1], 'base64').toString());
        const userId = decoded.sub || decoded.id;

        if (!userId) {
          this.logger.warn('Token missing user ID');
          client.disconnect();
          return;
        }

        this.clientToUserMap[client.id] = userId;
        if (!this.userToClientsMap[userId]) {
          this.userToClientsMap[userId] = [];
        }
        this.userToClientsMap[userId].push(client.id);

        client.join(`user:${userId}`);
        this.logger.log(`Client ${client.id} connected for user ${userId}`);
      } catch (error) {
        this.logger.error(`Token decode failed: ${error.message}`);
        client.disconnect();
      }
    } catch (error) {
      this.logger.error(`Connection error: ${error.message}`);
      client.disconnect();
    }
  }

  handleDisconnect(client: Socket) {
    try {
      const userId = this.clientToUserMap[client.id];

      if (userId) {
        this.userToClientsMap[userId] = this.userToClientsMap[userId]?.filter(
          (clientId) => clientId !== client.id,
        );

        if (this.userToClientsMap[userId]?.length === 0) {
          delete this.userToClientsMap[userId];
        }
      }

      delete this.clientToUserMap[client.id];
      this.logger.log(`Client ${client.id} disconnected`);
    } catch (error) {
      this.logger.error(`Disconnect error: ${error.message}`);
    }
  }

  @SubscribeMessage('ping')
  handlePing(client: Socket, data: any) {
    client.emit('pong', { type: 'pong', timestamp: Date.now() });
  }

  emitToUser(userId: string, event: string, data: any): void {
    try {
      this.server.to(`user:${userId}`).emit(event, data);
      this.logger.debug(`Emitted ${event} to user ${userId}`);
    } catch (error) {
      this.logger.error(`Failed to emit to user: ${error.message}`);
    }
  }

  emitGenerationProgress(
    userId: string,
    gameId: string,
    stage: string,
    percentage: number,
    details?: Record<string, unknown>,
  ): void {
    try {
      const event = 'gen:progress';
      const stageCode =
        typeof details?.stage === 'string' ? String(details.stage) : stage;
      const data = {
        type: event,
        gameId,
        data: {
          progress: percentage,
          message: stage,
          details: details || {},
        },
        stage: stageCode,
        percentage,
        details: details || {},
        timestamp: Date.now(),
      };

      this.emitToUser(userId, event, data);
      this.logger.debug(
        `Generation progress: ${gameId} - ${stageCode} ${percentage}%`,
      );
    } catch (error) {
      this.logger.error(`Failed to emit generation progress: ${error.message}`);
    }
  }

  emitGenerationComplete(userId: string, gameId: string, previewUrl: string): void {
    try {
      const event = 'gen:complete';
      let gameUrl = previewUrl.replace(/\/preview$/, '/index.html');
      try {
        const parsed = new URL(previewUrl);
        parsed.pathname = parsed.pathname.replace(/\/preview$/, '/index.html');
        gameUrl = parsed.toString();
      } catch {
        // Keep the fallback string replacement for non-URL inputs.
      }

      const data = {
        type: event,
        gameId,
        data: {
          success: true,
          game: {
            id: gameId,
            gameUrl,
            previewUrl,
            status: 'ready',
          },
          error: null,
        },
        previewUrl,
        timestamp: Date.now(),
        status: 'success',
      };

      this.emitToUser(userId, event, data);
      this.logger.log(`Generation complete: ${gameId}`);
    } catch (error) {
      this.logger.error(`Failed to emit generation complete: ${error.message}`);
    }
  }

  emitGenerationError(
    userId: string,
    gameId: string,
    error: string,
    details?: Record<string, unknown>,
  ): void {
    try {
      const event = 'gen:error';
      const stageCode =
        typeof details?.stage === 'string' ? String(details.stage) : 'failed';
      const data = {
        type: event,
        gameId,
        data: {
          success: false,
          error,
          details: details || {},
        },
        stage: stageCode,
        details: details || {},
        timestamp: Date.now(),
        status: 'error',
      };

      this.emitToUser(userId, event, data);
      this.logger.error(`Generation error: ${gameId} - ${error}`);
    } catch (emitError) {
      this.logger.error(`Failed to emit generation error: ${emitError.message}`);
    }
  }

  emitNotification(userId: string, notification: any): void {
    try {
      const event = 'notification';
      const data = {
        ...notification,
        timestamp: Date.now(),
        id: `notif_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      };

      this.emitToUser(userId, event, data);
      this.logger.debug(`Notification sent to user ${userId}: ${notification.type}`);
    } catch (error) {
      this.logger.error(`Failed to emit notification: ${error.message}`);
    }
  }

  emitGameUpdate(userId: string, gameId: string, updates: any): void {
    try {
      const event = 'game:update';
      const data = {
        gameId,
        updates,
        timestamp: Date.now(),
      };

      this.emitToUser(userId, event, data);
      this.logger.debug(`Game update sent for ${gameId}`);
    } catch (error) {
      this.logger.error(`Failed to emit game update: ${error.message}`);
    }
  }

  emitError(userId: string, error: string, context?: string): void {
    try {
      const event = 'error';
      const data = {
        message: error,
        context,
        timestamp: Date.now(),
      };

      this.emitToUser(userId, event, data);
      this.logger.error(`Error sent to user ${userId}: ${error}`);
    } catch (error) {
      this.logger.error(`Failed to emit error: ${error.message}`);
    }
  }

  broadcastGameStats(gameId: string, stats: any): void {
    try {
      this.server.emit('game:stats', {
        gameId,
        stats,
        timestamp: Date.now(),
      });
      this.logger.debug(`Game stats broadcasted for ${gameId}`);
    } catch (error) {
      this.logger.error(`Failed to broadcast game stats: ${error.message}`);
    }
  }
}
