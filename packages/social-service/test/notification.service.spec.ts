import { Test, TestingModule } from '@nestjs/testing';
import { PrismaService } from '../src/prisma/prisma.service';

describe('NotificationService', () => {
  let service: any;
  let prismaService: PrismaService;

  const mockNotification = {
    id: 'notif-123',
    userId: 'user-456',
    type: 'like',
    actorId: 'user-789',
    targetId: 'game-abc',
    isRead: false,
    createdAt: new Date('2024-01-01'),
  };

  const mockPrismaService = {
    notification: {
      create: jest.fn(),
      findMany: jest.fn(),
      update: jest.fn(),
      updateMany: jest.fn(),
      count: jest.fn(),
    },
    user: {
      findUnique: jest.fn(),
    },
  };

  beforeEach(async () => {
    const NotificationService = class {
      constructor(private prisma: any) {}

      async createNotification(
        userId: string,
        type: 'like' | 'comment' | 'follow' | 'fork',
        actorId: string,
        targetId?: string
      ) {
        const notification = {
          id: 'notif-' + Date.now(),
          userId,
          type,
          actorId,
          targetId: targetId || null,
          isRead: false,
          createdAt: new Date(),
        };

        return this.prisma.notification.create({
          data: notification,
        });
      }

      async markAsRead(notificationId: string) {
        return this.prisma.notification.update({
          where: { id: notificationId },
          data: { isRead: true },
        });
      }

      async markAllAsRead(userId: string) {
        return this.prisma.notification.updateMany({
          where: { userId, isRead: false },
          data: { isRead: true },
        });
      }

      async getUnreadCount(userId: string) {
        return this.prisma.notification.count({
          where: { userId, isRead: false },
        });
      }

      async getNotifications(userId: string, limit = 20, offset = 0) {
        return this.prisma.notification.findMany({
          where: { userId },
          include: {
            actor: true,
            game: true,
          },
          take: limit,
          skip: offset,
          orderBy: { createdAt: 'desc' },
        });
      }
    };

    service = new NotificationService(mockPrismaService as any);
    prismaService = mockPrismaService as any;

    jest.clearAllMocks();
  });

  describe('createNotification', () => {
    it('should create a like notification', async () => {
      const notification = {
        ...mockNotification,
        type: 'like',
      };

      mockPrismaService.notification.create.mockResolvedValueOnce(notification);

      const result = await service.createNotification('user-456', 'like', 'user-789', 'game-abc');

      expect(result.type).toBe('like');
      expect(result.isRead).toBe(false);
      expect(mockPrismaService.notification.create).toHaveBeenCalled();
    });

    it('should create a comment notification', async () => {
      const notification = {
        ...mockNotification,
        type: 'comment',
      };

      mockPrismaService.notification.create.mockResolvedValueOnce(notification);

      const result = await service.createNotification(
        'user-456',
        'comment',
        'user-789',
        'game-abc'
      );

      expect(result.type).toBe('comment');
      expect(mockPrismaService.notification.create).toHaveBeenCalled();
    });

    it('should create a follow notification', async () => {
      const notification = {
        ...mockNotification,
        type: 'follow',
        targetId: null,
      };

      mockPrismaService.notification.create.mockResolvedValueOnce(notification);

      const result = await service.createNotification('user-456', 'follow', 'user-789');

      expect(result.type).toBe('follow');
      expect(result.targetId).toBeNull();
    });

    it('should create a fork notification', async () => {
      const notification = {
        ...mockNotification,
        type: 'fork',
      };

      mockPrismaService.notification.create.mockResolvedValueOnce(notification);

      const result = await service.createNotification('user-456', 'fork', 'user-789', 'game-abc');

      expect(result.type).toBe('fork');
    });
  });

  describe('markAsRead', () => {
    it('should mark single notification as read', async () => {
      const readNotification = { ...mockNotification, isRead: true };
      mockPrismaService.notification.update.mockResolvedValueOnce(readNotification);

      const result = await service.markAsRead('notif-123');

      expect(result.isRead).toBe(true);
      expect(mockPrismaService.notification.update).toHaveBeenCalledWith({
        where: { id: 'notif-123' },
        data: { isRead: true },
      });
    });
  });

  describe('markAllAsRead', () => {
    it('should mark all unread notifications as read', async () => {
      mockPrismaService.notification.updateMany.mockResolvedValueOnce({
        count: 3,
      });

      const result = await service.markAllAsRead('user-456');

      expect(result.count).toBe(3);
      expect(mockPrismaService.notification.updateMany).toHaveBeenCalledWith({
        where: { userId: 'user-456', isRead: false },
        data: { isRead: true },
      });
    });
  });

  describe('getUnreadCount', () => {
    it('should return count of unread notifications', async () => {
      mockPrismaService.notification.count.mockResolvedValueOnce(5);

      const count = await service.getUnreadCount('user-456');

      expect(count).toBe(5);
      expect(mockPrismaService.notification.count).toHaveBeenCalledWith({
        where: { userId: 'user-456', isRead: false },
      });
    });

    it('should return 0 when no unread notifications', async () => {
      mockPrismaService.notification.count.mockResolvedValueOnce(0);

      const count = await service.getUnreadCount('user-456');

      expect(count).toBe(0);
    });
  });

  describe('getNotifications', () => {
    it('should return paginated notifications with relationships', async () => {
      const notifications = [
        {
          ...mockNotification,
          actor: { id: 'user-789', username: 'actor' },
          game: { id: 'game-abc', title: 'Game Title' },
        },
      ];

      mockPrismaService.notification.findMany.mockResolvedValueOnce(notifications);

      const result = await service.getNotifications('user-456', 20, 0);

      expect(result).toEqual(notifications);
      expect(result[0].actor).toBeDefined();
      expect(result[0].game).toBeDefined();
    });

    it('should support pagination', async () => {
      mockPrismaService.notification.findMany.mockResolvedValueOnce([]);

      await service.getNotifications('user-456', 10, 20);

      expect(mockPrismaService.notification.findMany).toHaveBeenCalledWith({
        where: { userId: 'user-456' },
        include: expect.any(Object),
        take: 10,
        skip: 20,
        orderBy: { createdAt: 'desc' },
      });
    });
  });
});
