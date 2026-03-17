import { Injectable, BadRequestException, NotFoundException, ForbiddenException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class CommentService {
  constructor(private prisma: PrismaService) {}

  async createComment(userId: string, dto: any) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    const gameExists = await this.prisma.game.findUnique({
      where: { id: dto.gameId },
    });

    if (!gameExists) {
      throw new NotFoundException('Game not found');
    }

    if (dto.parentId) {
      const parentComment = await this.prisma.comment.findUnique({
        where: { id: dto.parentId },
      });

      if (!parentComment) {
        throw new NotFoundException('Parent comment not found');
      }

      if (parentComment.status === 'deleted') {
        throw new BadRequestException('Cannot reply to deleted comment');
      }
    }

    const comment = await this.prisma.comment.create({
      data: {
        content: dto.content,
        gameId: dto.gameId,
        userId,
        parentId: dto.parentId || null,
        status: 'visible',
        likeCount: 0,
      },
      include: {
        user: {
          select: {
            id: true,
            username: true,
            avatarUrl: true,
          },
        },
      },
    });

    await this.prisma.game.update({
      where: { id: dto.gameId },
      data: { commentCount: { increment: 1 } },
    });

    return comment;
  }

  async getGameComments(gameId: string, page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;

    const [comments, total] = await Promise.all([
      this.prisma.comment.findMany({
        where: {
          gameId,
          parentId: null,
          status: 'visible',
        },
        skip,
        take: limit,
        include: {
          user: {
            select: {
              id: true,
              username: true,
              avatarUrl: true,
            },
          },
          _count: {
            select: { replies: { where: { status: 'visible' } } },
          },
          replies: {
            take: 3,
            where: { status: 'visible' },
            orderBy: { createdAt: 'asc' },
            include: {
              user: {
                select: {
                  id: true,
                  username: true,
                  avatarUrl: true,
                },
              },
            },
          },
        },
        orderBy: { createdAt: 'desc' },
      }),
      this.prisma.comment.count({
        where: {
          gameId,
          parentId: null,
          status: 'visible',
        },
      }),
    ]);

    return {
      data: comments,
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
      },
    };
  }

  async getCommentReplies(commentId: string, page: number = 1, limit: number = 20) {
    const skip = (page - 1) * limit;

    const [replies, total] = await Promise.all([
      this.prisma.comment.findMany({
        where: {
          parentId: commentId,
          status: 'visible',
        },
        skip,
        take: limit,
        include: {
          user: {
            select: {
              id: true,
              username: true,
              avatarUrl: true,
            },
          },
        },
        orderBy: { createdAt: 'asc' },
      }),
      this.prisma.comment.count({
        where: {
          parentId: commentId,
          status: 'visible',
        },
      }),
    ]);

    return {
      data: replies,
      pagination: {
        page,
        limit,
        total,
        pages: Math.ceil(total / limit),
      },
    };
  }

  async deleteComment(commentId: string, userId: string) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    const comment = await this.prisma.comment.findUnique({
      where: { id: commentId },
    });

    if (!comment) {
      throw new NotFoundException('Comment not found');
    }

    if (comment.userId !== userId) {
      throw new ForbiddenException('Only comment owner can delete it');
    }

    await this.prisma.comment.update({
      where: { id: commentId },
      data: { status: 'deleted' },
    });

    await this.prisma.game.update({
      where: { id: comment.gameId },
      data: { commentCount: { decrement: 1 } },
    });

    return { success: true };
  }

  async likeComment(commentId: string, userId: string) {
    if (!userId || userId === 'anonymous') {
      throw new BadRequestException('User authentication required');
    }

    const comment = await this.prisma.comment.findUnique({
      where: { id: commentId },
    });

    if (!comment) {
      throw new NotFoundException('Comment not found');
    }

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

      const newCount = Math.max(0, comment.likeCount - 1);
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

      const newCount = comment.likeCount + 1;
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
}
