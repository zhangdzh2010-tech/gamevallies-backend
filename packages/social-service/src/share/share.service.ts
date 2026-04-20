import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { pickPublicShareDescription, pickPublicShareAuthorName } from '../common/share-helpers';

@Injectable()
export class ShareService {
  constructor(private prisma: PrismaService) {}

  async recordShare(userId: string, gameId: string, platform: string) {
    const game = await this.prisma.game.findUnique({
      where: { id: gameId },
    });

    if (!game) {
      throw new NotFoundException('Game not found');
    }

    // Record share via SocialInteraction since there's no Share model
    const interaction = await this.prisma.socialInteraction.create({
      data: {
        userId: userId === 'anonymous' ? '' : userId,
        targetId: gameId,
        targetType: 'game',
        action: 'share',
        metadata: { platform },
      },
    });

    return { success: true, interactionId: interaction.id };
  }

  async getShareData(gameId: string) {
    const game = await this.prisma.game.findUnique({
      where: { id: gameId },
      include: {
        author: {
          select: {
            id: true,
            username: true,
            displayName: true,
          },
        },
      },
    });

    if (!game) {
      throw new NotFoundException('Game not found');
    }

    const appUrl = process.env.APP_URL || 'https://playforge.app';
    const gameUrl = `${appUrl}/games/${gameId}`;

    return {
      title: game.title,
      // H.5.1 - Prefer curated tagline, fall back to sanitized description so
      // share previews never surface LLM prompt scaffolding.
      description: pickPublicShareDescription(game),
      thumbnailUrl: game.thumbnailUrl,
      url: gameUrl,
      // H.7.1 - Keep raw `wx_<openid>` style usernames out of the share card.
      author: pickPublicShareAuthorName(game.author),
      stats: {
        plays: Number(game.playCount),
        likes: Number(game.likeCount),
        qualityScore: game.qualityScore,
      },
    };
  }
}
