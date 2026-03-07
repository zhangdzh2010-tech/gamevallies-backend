import { Injectable, BadRequestException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class LikeService {
  constructor(private prisma: PrismaService) {}

  async toggleLike(userId: string, targetType: 'game' | 'comment', targetId: string) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    if (targetType === 'game') {
      return this.toggleGameLike(userId, targetId);
    } else if (targetType === 'comment') {
      return this.toggleCommentLike(userId, targetId);
    }

    throw new BadRequestException('Invalid target type');
  }

  private async toggleGameLike(userId: string, gameId: string) {
    const existingLike = await this.prisma.socialInteraction.findUnique({
      where: {
        unique_interaction: {
          userId,
          targetId: gameId,
          targetType: 'game',
          action: 'like',
        },
      },
    });

    if (existingLike) {
      await this.prisma.socialInteraction.delete({
        where: {
          unique_interaction: {
            userId,
            targetId: gameId,
            targetType: 'game',
            action: 'like',
          },
        },
      });

      const game = await this.prisma.game.findUnique({
        where: { id: gameId },
        select: { likeCount: true },
      });

      const newCount = Math.max(0, Number(game?.likeCount || 0) - 1);
      await this.prisma.game.update({
        where: { id: gameId },
        data: { likeCount: newCount },
      });

      return {
        liked: false,
        likeCount: newCount,
      };
    } else {
      await this.prisma.socialInteraction.create({
        data: {
          userId,
          targetId: gameId,
          targetType: 'game',
          action: 'like',
        },
      });

      const game = await this.prisma.game.findUnique({
        where: { id: gameId },
        select: { likeCount: true },
      });

      const newCount = Number(game?.likeCount || 0) + 1;
      await this.prisma.game.update({
        where: { id: gameId },
        data: { likeCount: newCount },
      });

      return {
        liked: true,
        likeCount: newCount,
      };
    }
  }

  private async toggleCommentLike(userId: string, commentId: string) {
    const existingLike = await this.prisma.socialInteraction.findUnique({
      where: {
        unique_interaction: {
          userId,
          targetId: commentId,
          targetType: 'comment',
          action: 'like',
        },
      },
    });

    if (existingLike) {
      await this.prisma.socialInteraction.delete({
        where: {
          unique_interaction: {
            userId,
            targetId: commentId,
            targetType: 'comment',
            action: 'like',
          },
        },
      });

      const comment = await this.prisma.comment.findUnique({
        where: { id: commentId },
        select: { likeCount: true },
      });

      const newCount = Math.max(0, (comment?.likeCount || 0) - 1);
      await this.prisma.comment.update({
        where: { id: commentId },
        data: { likeCount: newCount },
      });

      return {
        liked: false,
        likeCount: newCount,
      };
    } else {
      await this.prisma.socialInteraction.create({
        data: {
          userId,
          targetId: commentId,
          targetType: 'comment',
          action: 'like',
        },
      });

      const comment = await this.prisma.comment.findUnique({
        where: { id: commentId },
        select: { likeCount: true },
      });

      const newCount = (comment?.likeCount || 0) + 1;
      await this.prisma.comment.update({
        where: { id: commentId },
        data: { likeCount: newCount },
      });

      return {
        liked: true,
        likeCount: newCount,
      };
    }
  }

  async getLikeStatus(userId: string, targetType: 'game' | 'comment', targetId: string) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    const existingLike = await this.prisma.socialInteraction.findUnique({
      where: {
        unique_interaction: {
          userId,
          targetId,
          targetType,
          action: 'like',
        },
      },
    });

    return {
      liked: Boolean(existingLike),
    };
  }
}
