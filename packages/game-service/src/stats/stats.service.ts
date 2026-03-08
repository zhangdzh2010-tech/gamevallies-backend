import { Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class StatsService {
  private readonly logger = new Logger(StatsService.name);

  constructor(private prisma: PrismaService) {}

  async incrementPlayCount(gameId: string): Promise<void> {
    try {
      const updated = await this.prisma.game.update({
        where: { id: gameId },
        data: { playCount: { increment: 1 } },
        select: { playCount: true },
      });
      // Refresh quality score every 10 plays
      if (Number(updated.playCount) % 10 === 0) {
        setImmediate(() => this.refreshQualityScore(gameId));
      }
    } catch (error) {
      this.logger.error(`Failed to increment play count: ${error.message}`);
      throw error;
    }
  }

  async incrementLikeCount(gameId: string, delta: number = 1): Promise<void> {
    try {
      await this.prisma.game.update({
        where: { id: gameId },
        data: { likeCount: { increment: delta } },
      });
      // Refresh quality score on every like (likes are less frequent)
      setImmediate(() => this.refreshQualityScore(gameId));
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

  /**
   * P2.1 – Recompute qualityScore after behavior data accumulates.
   * Called after play/like events once playCount >= 10.
   * Formula: 40% AI score + 40% retention + 20% like rate
   */
  async refreshQualityScore(gameId: string): Promise<void> {
    try {
      const game = await this.prisma.game.findUnique({
        where: { id: gameId },
        select: {
          qualityScore: true,
          playCount: true,
          likeCount: true,
          avgPlayTime: true,
        },
      });
      if (!game) return;

      const playCount = Number(game.playCount ?? 0);
      if (playCount < 10) return; // not enough data yet

      const likeCount = Number(game.likeCount ?? 0);
      const avgPlayTime = game.avgPlayTime ?? 0;
      const currentAiScore = game.qualityScore ?? 5.0;
      const expectedPlayTime = 60.0;

      const likeRate = Math.min(likeCount / playCount, 1.0);
      const likeScore = likeRate * 10.0;
      const retentionRatio = Math.min(avgPlayTime / expectedPlayTime, 2.0);
      const retentionScore = Math.min(retentionRatio * 5.0, 10.0);

      const newScore = Math.min(
        10,
        Math.max(0, 0.4 * currentAiScore + 0.4 * retentionScore + 0.2 * likeScore),
      );

      await this.prisma.game.update({
        where: { id: gameId },
        data: { qualityScore: Math.round(newScore * 100) / 100 },
      });

      this.logger.log(
        `qualityScore refreshed ${gameId}: ${currentAiScore.toFixed(2)} → ${newScore.toFixed(2)}` +
          ` (plays=${playCount}, likes=${likeCount}, avgTime=${avgPlayTime.toFixed(1)}s)`,
      );
    } catch (error) {
      this.logger.error(`Failed to refresh quality score: ${error.message}`);
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
