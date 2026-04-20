import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { sanitizeUserIdea } from '../common/sanitize-idea';
import { pickPublicAuthorName } from '../common/feed-presenter';

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

    const appUrl = process.env.PUBLIC_API_BASE_URL || process.env.APP_URL || 'https://playforge.app';
    const gameUrl = `${appUrl}/games/${gameId}`;

    // H.5.1 - Prefer the curated `userIdea` tagline; fall back to a sanitized
    // version of the legacy LLM-expanded `description` so C-end share previews
    // never surface raw prompt scaffolding.
    const shareDescription =
      (game.userIdea && String(game.userIdea).trim()) ||
      sanitizeUserIdea(game.description);

    return {
      title: game.title,
      description: shareDescription,
      thumbnailUrl: game.thumbnailUrl,
      url: gameUrl,
      // H.7.1 - Avoid leaking raw `wx_<openid>` style usernames into the share
      // card author line.
      author: pickPublicAuthorName(game.author),
      stats: {
        plays: Number(game.playCount),
        likes: Number(game.likeCount),
        qualityScore: game.qualityScore,
      },
    };
  }
}
