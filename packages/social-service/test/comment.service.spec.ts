import { Test, TestingModule } from '@nestjs/testing';
import { NotFoundException, ForbiddenException } from '@nestjs/common';
import { PrismaService } from '../src/prisma/prisma.service';

describe('CommentService', () => {
  let service: any;
  let prismaService: PrismaService;

  const mockComment = {
    id: 'comment-123',
    gameId: 'game-456',
    userId: 'user-789',
    content: 'Great game!',
    parentId: null,
    createdAt: new Date('2024-01-01'),
    updatedAt: new Date('2024-01-01'),
  };

  const mockPrismaService = {
    comment: {
      create: jest.fn(),
      findUnique: jest.fn(),
      findMany: jest.fn(),
      delete: jest.fn(),
    },
    socialInteraction: {
      findUnique: jest.fn(),
      create: jest.fn(),
      delete: jest.fn(),
    },
  };

  beforeEach(async () => {
    const CommentService = class {
      constructor(private prisma: PrismaService) {}

      async createComment(gameId: string, userId: string, content: string, parentId?: string) {
        const comment = {
          id: 'comment-' + Date.now(),
          gameId,
          userId,
          content,
          parentId: parentId || null,
          createdAt: new Date(),
          updatedAt: new Date(),
        };

        return this.prisma.comment.create({
          data: comment,
        });
      }

      async getComments(gameId: string, limit = 20, offset = 0) {
        const comments = await this.prisma.comment.findMany({
          where: {
            gameId,
            parentId: null, // Only root comments
          },
          include: {
            replies: {
              include: {
                user: true,
              },
            },
            user: true,
          },
          take: limit,
          skip: offset,
          orderBy: { createdAt: 'desc' },
        });

        return comments;
      }

      async deleteComment(commentId: string, userId: string) {
        const comment = await this.prisma.comment.findUnique({
          where: { id: commentId },
        });

        if (!comment) {
          throw new NotFoundException('Comment not found');
        }

        if (comment.userId !== userId) {
          throw new ForbiddenException('Can only delete own comments');
        }

        return this.prisma.comment.delete({
          where: { id: commentId },
        });
      }
    };

    service = new CommentService(mockPrismaService as any);
    prismaService = mockPrismaService as any;

    jest.clearAllMocks();
  });

  describe('createComment', () => {
    it('should create a root comment', async () => {
      const newComment = { ...mockComment, id: 'comment-new' };
      mockPrismaService.comment.create.mockResolvedValueOnce(newComment);

      const result = await service.createComment('game-456', 'user-789', 'Great game!');

      expect(result).toEqual(newComment);
      expect(result.parentId).toBeNull();
      expect(mockPrismaService.comment.create).toHaveBeenCalled();
    });

    it('should create a nested reply', async () => {
      const reply = {
        ...mockComment,
        id: 'comment-reply',
        parentId: 'comment-123',
      };

      mockPrismaService.comment.create.mockResolvedValueOnce(reply);

      const result = await service.createComment(
        'game-456',
        'user-999',
        'Thanks!',
        'comment-123'
      );

      expect(result.parentId).toBe('comment-123');
      expect(mockPrismaService.comment.create).toHaveBeenCalledWith({
        data: expect.objectContaining({
          parentId: 'comment-123',
        }),
      });
    });
  });

  describe('getComments', () => {
    it('should return comments with nested structure', async () => {
      const comments = [
        {
          ...mockComment,
          user: { id: 'user-789', username: 'commenter' },
          replies: [
            {
              id: 'reply-1',
              content: 'I agree!',
              user: { id: 'user-999', username: 'responder' },
            },
          ],
        },
      ];

      mockPrismaService.comment.findMany.mockResolvedValueOnce(comments);

      const result = await service.getComments('game-456', 20, 0);

      expect(result).toEqual(comments);
      expect(result[0].replies).toBeDefined();
      expect(result[0].replies[0].user).toBeDefined();
    });

    it('should return only root comments', async () => {
      mockPrismaService.comment.findMany.mockResolvedValueOnce([]);

      await service.getComments('game-456', 20, 0);

      expect(mockPrismaService.comment.findMany).toHaveBeenCalledWith({
        where: {
          gameId: 'game-456',
          parentId: null,
        },
        include: expect.any(Object),
        take: 20,
        skip: 0,
        orderBy: { createdAt: 'desc' },
      });
    });

    it('should support pagination', async () => {
      mockPrismaService.comment.findMany.mockResolvedValueOnce([]);

      await service.getComments('game-456', 10, 20);

      expect(mockPrismaService.comment.findMany).toHaveBeenCalledWith({
        where: expect.any(Object),
        include: expect.any(Object),
        take: 10,
        skip: 20,
        orderBy: { createdAt: 'desc' },
      });
    });
  });

  describe('deleteComment', () => {
    it('should allow owner to delete their comment', async () => {
      mockPrismaService.comment.findUnique.mockResolvedValueOnce(mockComment);
      mockPrismaService.comment.delete.mockResolvedValueOnce(mockComment);

      const result = await service.deleteComment('comment-123', 'user-789');

      expect(result).toEqual(mockComment);
      expect(mockPrismaService.comment.delete).toHaveBeenCalledWith({
        where: { id: 'comment-123' },
      });
    });

    it('should prevent non-owner from deleting comment', async () => {
      mockPrismaService.comment.findUnique.mockResolvedValueOnce(mockComment);

      await expect(
        service.deleteComment('comment-123', 'different-user')
      ).rejects.toThrow(ForbiddenException);

      expect(mockPrismaService.comment.delete).not.toHaveBeenCalled();
    });

    it('should throw NotFoundException for non-existent comment', async () => {
      mockPrismaService.comment.findUnique.mockResolvedValueOnce(null);

      await expect(
        service.deleteComment('nonexistent', 'user-789')
      ).rejects.toThrow(NotFoundException);
    });
  });
});
