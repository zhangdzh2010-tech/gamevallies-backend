import { Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class StatsService {
  private readonly logger = new Logger(StatsService.name);

  constructor(private prisma: PrismaService) {}

  async incrementPlayCount(gameId: string): Promise<void> {
    try {
      await this.prisma.game.update({
        where: { id: gameId },
        data: {
          playCount: {
            increment: 1,
          },
        },
      });
    } catch (error) {
      this.logger.error(`Failed to increment play count: ${error.message}`);
      throw error;
    }
  }

  async incrementLikeCount(gameId: string, delta: number = 1): Promise<void> {
    try {
      await this.prisma.game.update({
        where: { id: gameId },
        data: {
          likeCount: {
            increment: delta,
          },
        },
      });
    } catch (error) {
      this.logger.error(`Failed to increment like count: ${error.message}`);
      throw error;
    }
  }

  async incrementForkCount(gameId: string): Promise<void> {
    try {
      await this.prisma.game.update({
        where: { id: gameId },
        data: {
          forkCount: {
            increment: 1,
          },
        },
      });
    } catch (error) {
      this.logger.error(`Failed to increment fork count: ${error.message}`);
      throw error;
    }
  }

  async incrementCommentCount(gameId: string, delta: number = 1): Promise<void> {
    try {
      await this.prisma.game.update({
        where: { id: gameId },
        data: {
          commentCount: {
            increment: delta,
          },
        },
      });
    } catch (error) {
      this.logger.error(`Failed to increment comment count: ${error.message}`);
      throw error;
    }
  }

  async updateAvgPlayTime(gameId: string, playTime: number): Promise<void> {
    try {
      const game = await this.prisma.game.findUnique({
        where: { id: gameId },
        select: {
          avgPlayTime: true,
          playCount: true,
        },
      });

      if (!game) {
        throw new Error('Game not found');
      }

      const playCountNum = Number(game.playCount ?? 0);
      const currentAvgPlayTime = game.avgPlayTime ?? 0;
      const newTotalPlayTime = currentAvgPlayTime * playCountNum + playTime;
      const newPlayCount = playCountNum + 1;
      const newAvgPlayTime = newPlayCount > 0 ? newTotalPlayTime / newPlayCount : 0;

      await this.prisma.game.update({
        where: { id: gameId },
        data: {
          avgPlayTime: newAvgPlayTime,
        },
      });
    } catch (error) {
      this.logger.error(`Failed to update average play time: ${error.message}`);
      throw error;
    }
  }

  async getGameStats(gameId: string): Promise<any> {
    try {
      const stats = await this.prisma.game.findUnique({
        where: { id: gameId },
        select: {
          playCount: true,
          likeCount: true,
          forkCount: true,
          commentCount: true,
          avgPlayTime: true,
        },
      });

      if (!stats) {
        return null;
      }

      return {
        playCount: Number(stats.playCount ?? 0),
        likeCount: Number(stats.likeCount ?? 0),
        forkCount: Number(stats.forkCount ?? 0),
        commentCount: stats.commentCount ?? 0,
        avgPlayTime: stats.avgPlayTime ?? 0,
      };
    } catch (error) {
      this.logger.error(`Failed to get game stats: ${error.message}`);
      throw error;
    }
  }
}
