import { Injectable, BadRequestException } from '@nestjs/common';
import { NotificationType } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class NotificationService {
  constructor(private prisma: PrismaService) {}

  async createNotification(
    userId: string,
    actorId: string,
    type: string,
    targetId: string,
    content?: string,
  ) {
    if (!userId || !actorId || !type || !targetId) {
      throw new BadRequestException('Missing required fields for notification');
    }

    const notification = await this.prisma.notification.create({
      data: {
        userId,
        actorId,
        type: type as NotificationType,
        targetId,
        content: content || '',
        isRead: false,
      },
    });

    // Fetch actor info separately since there's no relation defined
    const actor = await this.prisma.user.findUnique({
      where: { id: actorId },
      select: { id: true, username: true, avatarUrl: true },
    });

    return { ...notification, actor };
  }

  async getNotifications(userId: string, page: number = 1, limit: number = 20) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    const skip = (page - 1) * limit;

    const [notifications, total] = await Promise.all([
      this.prisma.notification.findMany({
        where: { userId },
        skip,
        take: limit,
        orderBy: { createdAt: 'desc' },
      }),
      this.prisma.notification.count({
        where: { userId },
      }),
    ]);

    // Fetch actor info for all notifications
    const actorIds = [...new Set(notifications.map(n => n.actorId).filter(Boolean))] as string[];
    const actors = actorIds.length > 0
      ? await this.prisma.user.findMany({
          where: { id: { in: actorIds } },
          select: { id: true, username: true, avatarUrl: true },
        })
      : [];
    const actorMap = new Map(actors.map(a => [a.id, a]));

    const data = notifications.map(n => ({
      ...n,
      actor: n.actorId ? actorMap.get(n.actorId) || null : null,
    }));

    return {
      data,
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
      },
    };
  }

  async markAsRead(userId: string, notificationIds: string[]) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    await this.prisma.notification.updateMany({
      where: {
        id: { in: notificationIds },
        userId,
      },
      data: { isRead: true },
    });

    return { success: true };
  }

  async markAllAsRead(userId: string) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    await this.prisma.notification.updateMany({
      where: { userId },
      data: { isRead: true },
    });

    return { success: true };
  }

  async getUnreadCount(userId: string) {
    if (!userId || userId === 'anonymous') {
      return { count: 0 };
    }

    const count = await this.prisma.notification.count({
      where: {
        userId,
        isRead: false,
      },
    });

    return { count };
  }
}
