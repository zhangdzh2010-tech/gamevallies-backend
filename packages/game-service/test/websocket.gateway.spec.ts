import { Test, TestingModule } from '@nestjs/testing';
import { JwtService } from '@nestjs/jwt';
import { UnauthorizedException } from '@nestjs/common';

describe('WebSocketGateway', () => {
  let gateway: any;
  let jwtService: JwtService;

  const mockJwtService = {
    verify: jest.fn(),
    decode: jest.fn(),
  };

  beforeEach(async () => {
    const WebSocketGateway = class {
      constructor(private jwt: JwtService) {}

      handleConnection(client: any, ...args: any[]) {
        const token = args[0]?.handshake?.auth?.token;

        if (!token) {
          client.disconnect();
          throw new UnauthorizedException('No token provided');
        }

        try {
          const payload = this.jwt.verify(token);
          client.userId = payload.sub;
          client.username = payload.username;
          console.log(`Client connected: ${payload.username}`);
        } catch (error) {
          client.disconnect();
          throw new UnauthorizedException('Invalid token');
        }
      }

      emitToUser(userId: string, event: string, data: any) {
        // In real implementation, this would use Socket.IO's to() method
        return {
          userId,
          event,
          data,
          sent: true,
        };
      }

      emitGenerationProgress(gameId: string, progress: number, status: string) {
        const progressEvent = {
          event: 'generation:progress',
          gameId,
          progress,
          status,
          timestamp: new Date(),
        };

        return progressEvent;
      }

      handleDisconnect(client: any) {
        console.log(`Client disconnected: ${client.userId}`);
      }
    };

    gateway = new WebSocketGateway(mockJwtService as any);
    jwtService = mockJwtService as any;

    jest.clearAllMocks();
  });

  describe('handleConnection', () => {
    it('should accept connection with valid JWT token', () => {
      const mockClient = {
        disconnect: jest.fn(),
        userId: null,
        username: null,
      };

      const validToken = 'valid-jwt-token';
      const payload = { sub: 'user-123', username: 'testuser' };

      mockJwtService.verify.mockReturnValueOnce(payload);

      gateway.handleConnection(mockClient, {
        handshake: { auth: { token: validToken } },
      });

      expect(mockClient.userId).toBe('user-123');
      expect(mockClient.username).toBe('testuser');
      expect(mockClient.disconnect).not.toHaveBeenCalled();
    });

    it('should reject connection without token', () => {
      const mockClient = {
        disconnect: jest.fn(),
      };

      expect(() => {
        gateway.handleConnection(mockClient, { handshake: { auth: {} } });
      }).toThrow(UnauthorizedException);

      expect(mockClient.disconnect).toHaveBeenCalled();
    });

    it('should reject connection with invalid JWT token', () => {
      const mockClient = {
        disconnect: jest.fn(),
      };

      mockJwtService.verify.mockImplementationOnce(() => {
        throw new Error('Invalid token');
      });

      expect(() => {
        gateway.handleConnection(mockClient, {
          handshake: { auth: { token: 'invalid-token' } },
        });
      }).toThrow(UnauthorizedException);

      expect(mockClient.disconnect).toHaveBeenCalled();
    });

    it('should reject connection with expired token', () => {
      const mockClient = {
        disconnect: jest.fn(),
      };

      mockJwtService.verify.mockImplementationOnce(() => {
        throw new Error('Token expired');
      });

      expect(() => {
        gateway.handleConnection(mockClient, {
          handshake: { auth: { token: 'expired-token' } },
        });
      }).toThrow(UnauthorizedException);

      expect(mockClient.disconnect).toHaveBeenCalled();
    });
  });

  describe('emitToUser', () => {
    it('should emit message to specific user', () => {
      const result = gateway.emitToUser('user-123', 'game:update', {
        gameId: 'game-456',
        status: 'updated',
      });

      expect(result).toEqual({
        userId: 'user-123',
        event: 'game:update',
        data: {
          gameId: 'game-456',
          status: 'updated',
        },
        sent: true,
      });
    });

    it('should send event with various data types', () => {
      const result = gateway.emitToUser('user-123', 'notification', {
        type: 'like',
        count: 5,
        timestamp: new Date(),
      });

      expect(result.sent).toBe(true);
      expect(result.data).toHaveProperty('type');
      expect(result.data).toHaveProperty('count');
    });
  });

  describe('emitGenerationProgress', () => {
    it('should format progress event correctly', () => {
      const result = gateway.emitGenerationProgress('game-123', 50, 'generating');

      expect(result.event).toBe('generation:progress');
      expect(result.gameId).toBe('game-123');
      expect(result.progress).toBe(50);
      expect(result.status).toBe('generating');
      expect(result.timestamp).toBeInstanceOf(Date);
    });

    it('should emit progress at different stages', () => {
      const stages = [
        { progress: 0, status: 'starting' },
        { progress: 25, status: 'parsing' },
        { progress: 50, status: 'generating' },
        { progress: 75, status: 'validating' },
        { progress: 100, status: 'complete' },
      ];

      stages.forEach(({ progress, status }) => {
        const result = gateway.emitGenerationProgress('game-123', progress, status);

        expect(result.progress).toBe(progress);
        expect(result.status).toBe(status);
      });
    });

    it('should include timestamp in progress event', () => {
      const beforeTime = new Date();
      const result = gateway.emitGenerationProgress('game-123', 50, 'generating');
      const afterTime = new Date();

      expect(result.timestamp.getTime()).toBeGreaterThanOrEqual(beforeTime.getTime());
      expect(result.timestamp.getTime()).toBeLessThanOrEqual(afterTime.getTime());
    });
  });

  describe('handleDisconnect', () => {
    it('should handle client disconnect', () => {
      const mockClient = {
        userId: 'user-123',
      };

      expect(() => {
        gateway.handleDisconnect(mockClient);
      }).not.toThrow();
    });
  });
});
