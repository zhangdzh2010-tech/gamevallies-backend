import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

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
            username: true,
          },
        },
      },
    });

    if (!game) {
      throw new NotFoundException('Game not found');
    }

    const appUrl = process.env.PUBLIC_API_BASE_URL || process.env.APP_URL || 'https://playforge.app';
    const gameUrl = `${appUrl}/games/${gameId}`;

    return {
      title: game.title,
      description: game.description,
      thumbnailUrl: game.thumbnailUrl,
      url: gameUrl,
      author: game.author.username,
      stats: {
        plays: Number(game.playCount),
        likes: Number(game.likeCount),
        qualityScore: game.qualityScore,
      },
    };
  }
}
