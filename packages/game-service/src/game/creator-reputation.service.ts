import { Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

export interface CreatorReputation {
  creatorId: string;
  totalGames: number;
  publishedGames: number;
  avgQualityScore: number;
  totalPlays: number;
  totalLikes: number;
  failedGenerations: number;
  reputationScore: number;   // 0-100
  tier: 'new' | 'trusted' | 'verified' | 'flagged';
}

/**
 * P2.2 – Creator Reputation System
 *
 * Tier thresholds:
 *   flagged   – >30% failed generations OR reputationScore < 20
 *   new       – < 3 published games
 *   trusted   – reputationScore >= 50
 *   verified  – reputationScore >= 80
 *
 * reputationScore formula (0-100):
 *   40% avg qualityScore of published games (0-10 → 0-40)
 *   30% publish rate (published / total attempts)
 *   20% engagement rate (likes / plays)
 *   10% volume bonus (capped at 10 published games)
 */
@Injectable()
export class CreatorReputationService {
  private readonly logger = new Logger(CreatorReputationService.name);

  constructor(private prisma: PrismaService) {}

  async getReputation(creatorId: string): Promise<CreatorReputation> {
    const games = await this.prisma.game.findMany({
      where: { authorId: creatorId, status: { not: 'banned' } },
      select: {
        status: true,
        qualityScore: true,
        playCount: true,
        likeCount: true,
      },
    });

    const totalGames = games.length;
    const published = games.filter((g) => g.status === 'published');
    const failed = games.filter((g) => g.status === 'failed');

    const publishedGames = published.length;
    const failedGenerations = failed.length;

    const totalPlays = published.reduce((s, g) => s + Number(g.playCount ?? 0), 0);
    const totalLikes = published.reduce((s, g) => s + Number(g.likeCount ?? 0), 0);
    const avgQualityScore =
      publishedGames > 0
        ? published.reduce((s, g) => s + (g.qualityScore ?? 0), 0) / publishedGames
        : 0;

    const reputationScore = this._computeReputationScore({
      totalGames,
      publishedGames,
      failedGenerations,
      avgQualityScore,
      totalPlays,
      totalLikes,
    });

    const tier = this._computeTier({ failedGenerations, totalGames, publishedGames, reputationScore });

    return {
      creatorId,
      totalGames,
      publishedGames,
      avgQualityScore: Math.round(avgQualityScore * 100) / 100,
      totalPlays,
      totalLikes,
      failedGenerations,
      reputationScore,
      tier,
    };
  }

  /**
   * Returns the QA strictness multiplier for this creator:
   *   verified  → lighter QA (max_retries = 1)
   *   trusted   → normal QA (max_retries = 3)
   *   new       → normal QA
   *   flagged   → strict QA (max_retries = 5, human review required)
   */
  async getQAConfig(creatorId: string): Promise<{ maxRetries: number; requiresHumanReview: boolean }> {
    const rep = await this.getReputation(creatorId);
    switch (rep.tier) {
      case 'verified':
        return { maxRetries: 1, requiresHumanReview: false };
      case 'flagged':
        return { maxRetries: 5, requiresHumanReview: true };
      default:
        return { maxRetries: 3, requiresHumanReview: false };
    }
  }

  private _computeReputationScore(data: {
    totalGames: number;
    publishedGames: number;
    failedGenerations: number;
    avgQualityScore: number;
    totalPlays: number;
    totalLikes: number;
  }): number {
    // 40%: avg quality score (0-10 → 0-40)
    const qualityComponent = (data.avgQualityScore / 10) * 40;

    // 30%: publish rate
    const publishRate =
      data.totalGames > 0 ? data.publishedGames / data.totalGames : 0;
    const publishComponent = publishRate * 30;

    // 20%: like rate among plays
    const engagementRate =
      data.totalPlays > 0 ? Math.min(data.totalLikes / data.totalPlays, 1) : 0;
    const engagementComponent = engagementRate * 20;

    // 10%: volume bonus (1 point per published game, cap 10)
    const volumeComponent = Math.min(data.publishedGames, 10);

    const raw = qualityComponent + publishComponent + engagementComponent + volumeComponent;
    return Math.round(Math.min(100, Math.max(0, raw)));
  }

  private _computeTier(data: {
    failedGenerations: number;
    totalGames: number;
    publishedGames: number;
    reputationScore: number;
  }): CreatorReputation['tier'] {
    const failRate =
      data.totalGames > 0 ? data.failedGenerations / data.totalGames : 0;
    if (failRate > 0.3 || data.reputationScore < 20) return 'flagged';
    if (data.reputationScore >= 80 && data.publishedGames >= 5) return 'verified';
    if (data.reputationScore >= 50 && data.publishedGames >= 3) return 'trusted';
    return 'new';
  }
}
