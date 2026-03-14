import { Injectable, NotFoundException, BadRequestException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { Prisma } from '@prisma/client';
import { randomUUID, createHash } from 'crypto';

@Injectable()
export class AdminService {
  constructor(private readonly prisma: PrismaService) {}

  async listGames(
    page: number,
    limit: number,
    search?: string,
    status?: string,
  ) {
    const where: Prisma.GameWhereInput = {};

    if (search) {
      where.title = { contains: search };
    }

    if (status && status !== 'all') {
      where.status = status as any;
    }

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where,
        include: {
          author: {
            select: {
              id: true,
              username: true,
              displayName: true,
            },
          },
          bundles: {
            select: {
              id: true,
              version: true,
              codeSizeBytes: true,
              createdAt: true,
            },
            orderBy: { version: 'desc' },
            take: 1,
          },
        },
        orderBy: { createdAt: 'desc' },
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.prisma.game.count({ where }),
    ]);

    return {
      items: games,
      total,
      page,
      limit,
      totalPages: Math.ceil(total / limit),
    };
  }

  async getGame(id: string) {
    const game = await this.prisma.game.findUnique({
      where: { id },
      include: {
        author: {
          select: {
            id: true,
            username: true,
            displayName: true,
          },
        },
        bundles: {
          orderBy: { version: 'desc' },
        },
      },
    });

    if (!game) {
      throw new NotFoundException('Game not found');
    }

    return game;
  }

  async createGame(data: {
    title: string;
    description?: string;
    slug?: string;
    gameType?: string;
    tags?: string[];
    htmlCode: string;
    cssCode?: string;
    jsCode?: string;
    authorId?: string;
  }) {
    const gameId = randomUUID();
    const bundleId = randomUUID();

    // Generate slug from title if not provided
    const slug =
      data.slug ||
      data.title
        .toLowerCase()
        .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
        .replace(/^-|-$/g, '') +
        '-' +
        Date.now().toString(36);

    const htmlCode = data.htmlCode || '';
    const codeSizeBytes =
      Buffer.byteLength(htmlCode, 'utf8') +
      Buffer.byteLength(data.cssCode || '', 'utf8') +
      Buffer.byteLength(data.jsCode || '', 'utf8');

    // If no authorId provided, try to find or create a system admin user
    let authorId = data.authorId;
    if (!authorId) {
      const adminUser = await this.prisma.user.findFirst({
        where: { role: 'admin' },
      });
      if (adminUser) {
        authorId = adminUser.id;
      } else {
        // Create a system admin user
        const adminId = randomUUID();
        await this.prisma.user.create({
          data: {
            id: adminId,
            username: 'system_admin',
            displayName: 'System Admin',
            role: 'admin',
            authProvider: 'email',
          },
        });
        authorId = adminId;
      }
    }

    const game = await this.prisma.game.create({
      data: {
        id: gameId,
        title: data.title,
        description: data.description || null,
        slug,
        gameType: data.gameType || null,
        tags: data.tags || [],
        author: { connect: { id: authorId! } },
        status: 'draft',
        codeBundleId: bundleId,
        version: 1,
      },
    });

    await this.prisma.gameBundle.create({
      data: {
        id: bundleId,
        gameId: game.id,
        version: 1,
        htmlCode,
        cssCode: data.cssCode || null,
        jsCode: data.jsCode || null,
        codeSizeBytes,
      },
    });

    return this.getGame(game.id);
  }

  async updateGame(
    id: string,
    data: {
      title?: string;
      description?: string;
      slug?: string;
      gameType?: string;
      tags?: string[];
      htmlCode?: string;
      cssCode?: string;
      jsCode?: string;
      status?: string;
    },
  ) {
    const existing = await this.prisma.game.findUnique({
      where: { id },
      include: {
        bundles: { orderBy: { version: 'desc' }, take: 1 },
      },
    });

    if (!existing) {
      throw new NotFoundException('Game not found');
    }

    // Update game metadata
    const gameUpdate: any = {};
    if (data.title !== undefined) gameUpdate.title = data.title;
    if (data.description !== undefined) gameUpdate.description = data.description;
    if (data.slug !== undefined) gameUpdate.slug = data.slug;
    if (data.gameType !== undefined) gameUpdate.gameType = data.gameType;
    if (data.tags !== undefined) gameUpdate.tags = data.tags;
    if (data.status !== undefined) gameUpdate.status = data.status;

    if (Object.keys(gameUpdate).length > 0) {
      await this.prisma.game.update({
        where: { id },
        data: gameUpdate,
      });
    }

    // Update or create bundle if code provided
    if (data.htmlCode !== undefined) {
      const newVersion = (existing.bundles[0]?.version || 0) + 1;
      const htmlCode = data.htmlCode || '';
      const codeSizeBytes =
        Buffer.byteLength(htmlCode, 'utf8') +
        Buffer.byteLength(data.cssCode || '', 'utf8') +
        Buffer.byteLength(data.jsCode || '', 'utf8');

      const bundleId = randomUUID();
      await this.prisma.gameBundle.create({
        data: {
          id: bundleId,
          gameId: id,
          version: newVersion,
          htmlCode,
          cssCode: data.cssCode || null,
          jsCode: data.jsCode || null,
          codeSizeBytes,
        },
      });

      await this.prisma.game.update({
        where: { id },
        data: {
          codeBundleId: bundleId,
          version: newVersion,
        },
      });
    }

    return this.getGame(id);
  }

  async deleteGame(id: string) {
    const game = await this.prisma.game.findUnique({ where: { id } });
    if (!game) {
      throw new NotFoundException('Game not found');
    }

    // Delete bundles first (cascade should handle this, but be explicit)
    await this.prisma.gameBundle.deleteMany({ where: { gameId: id } });
    await this.prisma.game.delete({ where: { id } });

    return { deleted: true };
  }

  async toggleStatus(id: string, status: string) {
    const game = await this.prisma.game.findUnique({ where: { id } });
    if (!game) {
      throw new NotFoundException('Game not found');
    }

    const updateData: any = { status };
    if (status === 'published' && !game.publishedAt) {
      updateData.publishedAt = new Date();
    }

    await this.prisma.game.update({
      where: { id },
      data: updateData,
    });

    return this.getGame(id);
  }

  // ===================== User Management =====================

  async listUsers(page: number, limit: number, search?: string, role?: string) {
    const where: Prisma.UserWhereInput = {};
    if (search) {
      where.OR = [
        { username: { contains: search } },
        { displayName: { contains: search } },
        { email: { contains: search } },
      ];
    }
    if (role && role !== 'all') {
      where.role = role as any;
    }

    const [users, total] = await Promise.all([
      this.prisma.user.findMany({
        where,
        select: {
          id: true, username: true, displayName: true, email: true, phone: true,
          role: true, isPro: true, bio: true, avatarUrl: true, authProvider: true,
          followerCount: true, followingCount: true, gameCount: true, totalPlays: true,
          createdAt: true, updatedAt: true,
        },
        orderBy: { createdAt: 'desc' },
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.prisma.user.count({ where }),
    ]);

    return { items: users, total, page, limit, totalPages: Math.ceil(total / limit) };
  }

  async getUser(id: string) {
    const user = await this.prisma.user.findUnique({
      where: { id },
      select: {
        id: true, username: true, displayName: true, email: true, phone: true,
        role: true, isPro: true, bio: true, avatarUrl: true, authProvider: true,
        followerCount: true, followingCount: true, gameCount: true, totalPlays: true,
        createdAt: true, updatedAt: true,
      },
    });
    if (!user) throw new NotFoundException('User not found');
    return user;
  }

  async updateUser(id: string, data: any) {
    const user = await this.prisma.user.findUnique({ where: { id } });
    if (!user) throw new NotFoundException('User not found');

    const update: any = {};
    if (data.username !== undefined) update.username = data.username;
    if (data.displayName !== undefined) update.displayName = data.displayName;
    if (data.email !== undefined) update.email = data.email || null;
    if (data.phone !== undefined) update.phone = data.phone || null;
    if (data.role !== undefined) update.role = data.role;
    if (data.isPro !== undefined) update.isPro = data.isPro;
    if (data.bio !== undefined) update.bio = data.bio;

    if (Object.keys(update).length > 0) {
      await this.prisma.user.update({ where: { id }, data: update });
    }
    return this.getUser(id);
  }

  async deleteUser(id: string) {
    const user = await this.prisma.user.findUnique({ where: { id } });
    if (!user) throw new NotFoundException('User not found');
    // Delete related data
    await this.prisma.gameBundle.deleteMany({ where: { game: { authorId: id } } });
    await this.prisma.game.deleteMany({ where: { authorId: id } });
    await this.prisma.user.delete({ where: { id } });
    return { deleted: true };
  }

  async resetUserPassword(id: string, newPassword: string) {
    const user = await this.prisma.user.findUnique({ where: { id } });
    if (!user) throw new NotFoundException('User not found');
    if (!newPassword || newPassword.length < 6) {
      throw new BadRequestException('Password must be at least 6 characters');
    }
    // Use bcryptjs if available, otherwise simple hash
    let hash: string;
    try {
      const bcrypt = require('bcryptjs');
      hash = await bcrypt.hash(newPassword, 12);
    } catch {
      hash = createHash('sha256').update(newPassword).digest('hex');
    }
    await this.prisma.user.update({ where: { id }, data: { passwordHash: hash } });
    return { success: true };
  }

  async createUser(data: any) {
    if (!data.username) throw new BadRequestException('Username is required');
    const existing = await this.prisma.user.findUnique({ where: { username: data.username } });
    if (existing) throw new BadRequestException('Username already exists');

    let passwordHash: string | null = null;
    if (data.password) {
      try {
        const bcrypt = require('bcryptjs');
        passwordHash = await bcrypt.hash(data.password, 12);
      } catch {
        passwordHash = createHash('sha256').update(data.password).digest('hex');
      }
    }

    const user = await this.prisma.user.create({
      data: {
        id: randomUUID(),
        username: data.username,
        displayName: data.displayName || data.username,
        email: data.email || null,
        phone: data.phone || null,
        role: data.role || 'user',
        bio: data.bio || null,
        passwordHash,
        authProvider: 'email',
      },
    });
    return this.getUser(user.id);
  }

  // ===================== Admin Token Management =====================

  async changeAdminToken(currentToken: string, newToken: string) {
    const envToken = process.env.ADMIN_TOKEN || 'admin123';
    if (currentToken !== envToken) {
      throw new BadRequestException('Current token is incorrect');
    }
    if (!newToken || newToken.length < 6) {
      throw new BadRequestException('New token must be at least 6 characters');
    }
    // Update the runtime env var (persists until restart)
    process.env.ADMIN_TOKEN = newToken;
    return { success: true, message: 'Admin token updated (runtime only, update .env.deploy for persistence)' };
  }

  // ===================== Stats =====================

  async getStats() {
    const [totalGames, totalUsers, gamesAgg] = await Promise.all([
      this.prisma.game.count(),
      this.prisma.user.count(),
      this.prisma.game.aggregate({
        _sum: {
          playCount: true,
          likeCount: true,
          forkCount: true,
        },
      }),
    ]);

    const statusCounts = await this.prisma.game.groupBy({
      by: ['status'],
      _count: { id: true },
    });

    const statusMap: Record<string, number> = {};
    for (const s of statusCounts) {
      statusMap[s.status] = s._count.id;
    }

    return {
      totalGames,
      totalUsers,
      totalPlays: gamesAgg._sum.playCount || 0,
      totalLikes: gamesAgg._sum.likeCount || 0,
      totalForks: gamesAgg._sum.forkCount || 0,
      byStatus: statusMap,
    };
  }
}
