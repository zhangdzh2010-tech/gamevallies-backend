import { Injectable, NotFoundException, BadRequestException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../prisma/prisma.service';
import { Prisma } from '@prisma/client';
import { randomUUID } from 'crypto';
import * as bcrypt from 'bcryptjs';
import axios from 'axios';

@Injectable()
export class AdminService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly configService: ConfigService,
  ) {}

  private getFallbackAiEngineAdminBaseUrl(): string {
    return this.configService.get<string>('AI_ENGINE_URL', 'http://localhost:8000').replace(/\/$/, '');
  }

  private normalizeExecutionRegion(rawValue?: string | null): string {
    return (rawValue || '').trim() === 'ap_southeast_johor' ? 'ap_southeast_johor' : 'cn_shanghai';
  }

  private getConfiguredAiEngineAdminBaseUrlForRegion(executionRegion?: string | null): string {
    const normalizedRegion = this.normalizeExecutionRegion(executionRegion);
    const defaultRegion = this.normalizeExecutionRegion(
      this.configService.get<string>('AI_ENGINE_DEFAULT_REGION')
      || this.configService.get<string>('SERVICE_REGION')
      || 'cn_shanghai',
    );

    const regionSpecificUrl = normalizedRegion === 'ap_southeast_johor'
      ? this.configService.get<string>('AI_ENGINE_URL_AP_SOUTHEAST_JOHOR', '')
      : this.configService.get<string>('AI_ENGINE_URL_CN_SHANGHAI', '');
    const normalizedSpecificUrl = (regionSpecificUrl || '').trim().replace(/\/$/, '');
    if (normalizedSpecificUrl) {
      return normalizedSpecificUrl;
    }

    if (defaultRegion === normalizedRegion) {
      return this.getFallbackAiEngineAdminBaseUrl();
    }

    return '';
  }

  private getDefaultExecutionRegion(): string {
    const region = (
      this.configService.get<string>('AI_ENGINE_DEFAULT_REGION')
      || this.configService.get<string>('SERVICE_REGION')
      || 'cn_shanghai'
    ).trim();
    return region === 'ap_southeast_johor' ? region : 'cn_shanghai';
  }

  private async getAiEngineAdminBaseUrls(regionTargetId?: string): Promise<string[]> {
    const urls: string[] = [];
    const appendUrl = (value?: string | null) => {
      const normalized = (value || '').trim().replace(/\/$/, '');
      if (normalized && !urls.includes(normalized)) {
        urls.push(normalized);
      }
    };

    if (regionTargetId) {
      const target = await this.prisma.aiEngineRegionTarget.findUnique({
        where: { id: regionTargetId },
        select: {
          executionRegion: true,
          aiEngineUrl: true,
          deployEnabled: true,
          deployStatus: true,
        },
      }).catch(() => null);

      if (target?.deployEnabled && target?.deployStatus === 'deployed') {
        appendUrl(target.aiEngineUrl);
      }
      appendUrl(this.getConfiguredAiEngineAdminBaseUrlForRegion(target?.executionRegion));
    } else {
      const targets = await this.prisma.aiEngineRegionTarget.findMany({
        where: {
          deployEnabled: true,
          deployStatus: 'deployed',
          aiEngineUrl: { not: null },
        },
        select: {
          aiEngineUrl: true,
        },
      }).catch(() => []);

      for (const target of targets) {
        appendUrl(target.aiEngineUrl);
      }

      appendUrl(this.getConfiguredAiEngineAdminBaseUrlForRegion('cn_shanghai'));
      appendUrl(this.getConfiguredAiEngineAdminBaseUrlForRegion('ap_southeast_johor'));
    }

    if (urls.length > 0) {
      return urls;
    }

    const fallback = this.getFallbackAiEngineAdminBaseUrl();
    return fallback ? [fallback] : [];
  }

  private getAdminToken(): string {
    return process.env.ADMIN_TOKEN || 'admin123';
  }

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
    const hash = await bcrypt.hash(newPassword, 10);
    await this.prisma.user.update({ where: { id }, data: { passwordHash: hash } });
    return { success: true };
  }

  async createUser(data: any) {
    if (!data.username) throw new BadRequestException('Username is required');
    const existing = await this.prisma.user.findUnique({ where: { username: data.username } });
    if (existing) throw new BadRequestException('Username already exists');

    const passwordHash = data.password
      ? await bcrypt.hash(data.password, 10)
      : null;

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

  // ===================== Generation Logs =====================

  async listGenerationLogs(page: number, limit: number, status?: string, search?: string) {
    const filters: Prisma.GameWhereInput[] = [];

    if (status && status !== 'all') {
      if (status === 'failed') {
        filters.push({
          OR: [
            { status: 'failed' as any },
            { failedStage: { not: null } },
            { failedReason: { not: null } },
          ],
        });
      } else {
        filters.push({ status: status as any });
      }
    }
    if (search) {
      filters.push({
        OR: [
          { title: { contains: search } },
          { description: { contains: search } },
          { author: { username: { contains: search } } },
        ],
      });
    }

    const where: Prisma.GameWhereInput =
      filters.length > 0 ? { AND: filters } : {};

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where,
        include: {
          author: {
            select: { id: true, username: true, displayName: true },
          },
          bundles: {
            select: {
              id: true,
              version: true,
              metadata: true,
              generationMeta: true,
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

    const items = games.map(g => {
      const bundle = g.bundles[0];
      const meta = (bundle?.metadata as any) || {};
      return {
        gameId: g.id,
        title: g.title,
        description: g.description,
        status: g.status,
        failedStage: g.failedStage,
        failedReason: g.failedReason,
        retryCount: g.retryCount,
        lastErrorAt: g.lastErrorAt,
        gameType: g.gameType,
        createdAt: g.createdAt,
        updatedAt: g.updatedAt,
        author: g.author,
        strategy: meta.strategy || null,
        qaPassed: meta.qaPassed ?? null,
        qaRetries: meta.qaRetries ?? null,
        iterationRetries: meta.iterationRetries ?? null,
        genTimeMs: meta.genTimeMs || null,
        codeSizeBytes: bundle?.codeSizeBytes || null,
        qualityScore: meta.qualityScore || null,
        version: bundle?.version || 0,
      };
    });

    return { items, total, page, limit, totalPages: Math.ceil(total / limit) };
  }

  async listGenerationTasks(page: number, limit: number, status?: string, search?: string) {
    const where: Prisma.GenerationTaskWhereInput = {};

    if (status && status !== 'all') {
      where.status = status as any;
    }
    if (search) {
      where.OR = [
        { id: { contains: search } },
        { gameId: { contains: search } },
        { user: { username: { contains: search } } },
        { game: { title: { contains: search } } },
      ];
    }

    const [tasks, total] = await Promise.all([
      this.prisma.generationTask.findMany({
        where,
        include: {
          game: { select: { id: true, title: true, status: true } },
          user: { select: { id: true, username: true, displayName: true } },
        },
        orderBy: { createdAt: 'desc' },
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.prisma.generationTask.count({ where }),
    ]);

    return { items: tasks, total, page, limit, totalPages: Math.ceil(total / limit) };
  }

  async getGenerationTask(taskId: string) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
      include: {
        game: { select: { id: true, title: true, status: true } },
        user: { select: { id: true, username: true, displayName: true } },
        events: {
          orderBy: { createdAt: 'asc' },
          take: 300,
        },
        llmCallLogs: {
          orderBy: { createdAt: 'asc' },
          take: 300,
        },
      },
    });

    if (!task) {
      throw new NotFoundException('Generation task not found');
    }

    return task;
  }

  async listGenerationTaskEvents(taskId: string, limit = 100) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
      select: { id: true },
    });

    if (!task) {
      throw new NotFoundException('Generation task not found');
    }

    return {
      items: await this.prisma.generationTaskEvent.findMany({
        where: { taskId },
        orderBy: { createdAt: 'asc' },
        take: Math.max(1, Math.min(limit, 500)),
      }),
    };
  }

  async listCloudAccounts() {
    return this.prisma.cloudProviderAccount.findMany({
      orderBy: [{ enabled: 'desc' }, { createdAt: 'asc' }],
    });
  }

  async listCloudRegions() {
    return this.prisma.cloudRegionCatalog.findMany({
      include: {
        account: {
          select: {
            id: true,
            vendor: true,
            accountKey: true,
            displayName: true,
            enabled: true,
          },
        },
      },
      orderBy: [{ vendor: 'asc' }, { regionCode: 'asc' }],
    });
  }

  async listAiEngineRegionTargets(params?: { providerSelectableOnly?: boolean }) {
    const where: Prisma.AiEngineRegionTargetWhereInput = {};
    if (params?.providerSelectableOnly) {
      where.deployEnabled = true;
      where.deployStatus = 'deployed';
    }

    const targets = await this.prisma.aiEngineRegionTarget.findMany({
      where,
      include: {
        account: {
          select: {
            id: true,
            vendor: true,
            accountKey: true,
            displayName: true,
          },
        },
        regionCatalog: {
          select: {
            id: true,
            regionCode: true,
            regionName: true,
            regionGroup: true,
            deploySupported: true,
            enabled: true,
          },
        },
      },
      orderBy: [{ executionRegion: 'asc' }, { createdAt: 'asc' }],
    });

    const enrichedTargets = targets.map((target) => ({
      ...target,
      resolvedAiEngineUrl: target.aiEngineUrl || this.getConfiguredAiEngineAdminBaseUrlForRegion(target.executionRegion) || null,
    }));

    if (params?.providerSelectableOnly) {
      return enrichedTargets.filter((target) => (
        target.deployEnabled !== false
        && target.deployStatus === 'deployed'
        && Boolean(target.resolvedAiEngineUrl)
      ));
    }

    return enrichedTargets;
  }

  async upsertAiEngineRegionTarget(id: string | undefined, body: any) {
    if (!body?.accountId) {
      throw new BadRequestException('accountId is required');
    }
    if (!body?.regionCatalogId) {
      throw new BadRequestException('regionCatalogId is required');
    }
    if (!body?.executionRegion) {
      throw new BadRequestException('executionRegion is required');
    }
    if (!body?.displayName) {
      throw new BadRequestException('displayName is required');
    }
    if (!body?.functionName) {
      throw new BadRequestException('functionName is required');
    }

    const [account, regionCatalog, existing] = await Promise.all([
      this.prisma.cloudProviderAccount.findUnique({ where: { id: body.accountId } }),
      this.prisma.cloudRegionCatalog.findUnique({ where: { id: body.regionCatalogId } }),
      id ? this.prisma.aiEngineRegionTarget.findUnique({ where: { id } }) : Promise.resolve(null),
    ]);

    if (!account || !account.enabled) {
      throw new BadRequestException('Cloud account not found or disabled');
    }
    if (!regionCatalog || !regionCatalog.enabled) {
      throw new BadRequestException('Cloud region not found or disabled');
    }
    if (regionCatalog.accountId !== account.id) {
      throw new BadRequestException('regionCatalogId does not belong to the selected account');
    }
    if (!['cn_shanghai', 'ap_southeast_johor'].includes(body.executionRegion)) {
      throw new BadRequestException('executionRegion must be cn_shanghai or ap_southeast_johor');
    }
    const expectedCloudRegionCode = body.executionRegion === 'ap_southeast_johor'
      ? 'ap-southeast-johor'
      : 'cn-shanghai';
    if (regionCatalog.regionCode !== expectedCloudRegionCode) {
      throw new BadRequestException(`regionCatalogId does not match executionRegion=${body.executionRegion}`);
    }
    if (existing && existing.executionRegion !== body.executionRegion) {
      throw new BadRequestException('executionRegion cannot be changed after creation');
    }

    const explicitAiEngineUrl = body?.aiEngineUrl === undefined
      ? undefined
      : ((body.aiEngineUrl || '').trim() || null);
    const explicitDeployStatus = body?.deployStatus === undefined
      ? undefined
      : String(body.deployStatus || '').trim() || null;
    const explicitLastDeployedAt = body?.lastDeployedAt
      ? new Date(body.lastDeployedAt)
      : undefined;

    const targetId = id || randomUUID();
    return this.prisma.aiEngineRegionTarget.upsert({
      where: { id: targetId },
      create: {
        id: targetId,
        accountId: account.id,
        regionCatalogId: regionCatalog.id,
        vendor: account.vendor,
        cloudRegionCode: regionCatalog.regionCode,
        executionRegion: body.executionRegion,
        displayName: body.displayName,
        functionName: body.functionName,
        registry: body.registry || account.defaultRegistry || '',
        registryNamespace: body.registryNamespace || account.defaultRegistryNamespace || '',
        imageRepository: body.imageRepository || body.functionName,
        serviceRegionEnv: body.serviceRegionEnv || body.executionRegion,
        aiEngineUrl: explicitAiEngineUrl ?? null,
        deployEnabled: body.deployEnabled !== false,
        deployStatus: explicitDeployStatus || existing?.deployStatus || (explicitAiEngineUrl ? 'deployed' : 'pending'),
        lastRevision: body?.lastRevision ?? existing?.lastRevision ?? null,
        lastImageTag: body?.lastImageTag ?? existing?.lastImageTag ?? null,
        lastReleaseStatus: body?.lastReleaseStatus ?? existing?.lastReleaseStatus ?? null,
        lastDeployError: body?.lastDeployError ?? existing?.lastDeployError ?? null,
        lastDeployedAt: explicitLastDeployedAt ?? existing?.lastDeployedAt ?? (explicitAiEngineUrl ? new Date() : null),
      },
      update: {
        accountId: account.id,
        regionCatalogId: regionCatalog.id,
        vendor: account.vendor,
        cloudRegionCode: regionCatalog.regionCode,
        displayName: body.displayName,
        functionName: body.functionName,
        registry: body.registry || account.defaultRegistry || '',
        registryNamespace: body.registryNamespace || account.defaultRegistryNamespace || '',
        imageRepository: body.imageRepository || body.functionName,
        serviceRegionEnv: body.serviceRegionEnv || existing?.serviceRegionEnv || body.executionRegion,
        aiEngineUrl: explicitAiEngineUrl !== undefined ? explicitAiEngineUrl : existing?.aiEngineUrl ?? null,
        deployEnabled: body.deployEnabled !== false,
        deployStatus: explicitDeployStatus || existing?.deployStatus || (explicitAiEngineUrl ? 'deployed' : 'pending'),
        lastRevision: body?.lastRevision ?? existing?.lastRevision ?? null,
        lastImageTag: body?.lastImageTag ?? existing?.lastImageTag ?? null,
        lastReleaseStatus: body?.lastReleaseStatus ?? existing?.lastReleaseStatus ?? null,
        lastDeployError: body?.lastDeployError ?? existing?.lastDeployError ?? null,
        lastDeployedAt: explicitLastDeployedAt ?? existing?.lastDeployedAt ?? (explicitAiEngineUrl ? new Date() : null),
      },
      include: {
        account: {
          select: {
            id: true,
            accountKey: true,
            displayName: true,
            vendor: true,
          },
        },
        regionCatalog: {
          select: {
            id: true,
            regionCode: true,
            regionName: true,
            regionGroup: true,
          },
        },
      },
    });
  }

  async syncAiEngineRegionTargetDeployState(body: any) {
    if (!body?.executionRegion) {
      throw new BadRequestException('executionRegion is required');
    }
    const executionRegion = this.normalizeExecutionRegion(body?.executionRegion);
    const existing = await this.prisma.aiEngineRegionTarget.findUnique({
      where: { executionRegion },
    });

    if (!existing) {
      throw new NotFoundException(`Region target not found for executionRegion=${executionRegion}`);
    }

    const nextAiEngineUrl = body?.aiEngineUrl === undefined
      ? existing.aiEngineUrl
      : (body.aiEngineUrl || '').trim() || null;
    const nextDeployStatus = body?.deployStatus
      || (body?.lastDeployError ? 'failed' : nextAiEngineUrl ? 'deployed' : existing.deployStatus || 'pending');
    const nextLastDeployedAt = nextDeployStatus === 'deployed'
      ? new Date(body?.lastDeployedAt || new Date())
      : body?.lastDeployedAt
        ? new Date(body.lastDeployedAt)
        : existing.lastDeployedAt;

    return this.prisma.aiEngineRegionTarget.update({
      where: { id: existing.id },
      data: {
        aiEngineUrl: nextAiEngineUrl,
        deployStatus: nextDeployStatus,
        lastRevision: body?.lastRevision ?? existing.lastRevision,
        lastImageTag: body?.lastImageTag ?? existing.lastImageTag,
        lastReleaseStatus: body?.lastReleaseStatus ?? existing.lastReleaseStatus,
        lastDeployError: body?.lastDeployError ?? null,
        lastDeployedAt: nextLastDeployedAt,
      },
      include: {
        account: {
          select: {
            id: true,
            vendor: true,
            accountKey: true,
            displayName: true,
          },
        },
        regionCatalog: {
          select: {
            id: true,
            regionCode: true,
            regionName: true,
            regionGroup: true,
          },
        },
      },
    });
  }

  async listLlmProviders() {
    const providers = await this.prisma.llmGatewayProvider.findMany({
      include: {
        regionTarget: {
          select: {
            id: true,
            displayName: true,
            executionRegion: true,
            aiEngineUrl: true,
            deployStatus: true,
            deployEnabled: true,
          },
        },
      },
      orderBy: [{ priority: 'asc' }, { createdAt: 'desc' }],
    });

    return providers.map((provider) => ({
      ...provider,
      apiKey: undefined,
      apiKeySet: Boolean(provider.apiKey),
      apiKeyMasked: provider.apiKey
        ? `${provider.apiKey.slice(0, 4)}...${provider.apiKey.slice(-4)}`
        : null,
      regionDisplayName: provider.regionTarget?.displayName || provider.region,
    }));
  }

  async listLlmSteps() {
    return this.prisma.llmStepCatalog.findMany({
      where: { enabled: true },
      orderBy: [{ stepOrder: 'asc' }, { stepKey: 'asc' }],
    });
  }

  async upsertLlmProvider(id: string | undefined, body: any) {
    if (!body?.name) {
      throw new BadRequestException('Provider name is required');
    }
    if (!body?.providerType) {
      throw new BadRequestException('providerType is required');
    }
    if (!body?.baseUrl && body.providerType !== 'anthropic') {
      throw new BadRequestException('baseUrl is required');
    }
    if (!body?.model) {
      throw new BadRequestException('model is required');
    }
    if (!body?.regionTargetId) {
      throw new BadRequestException('regionTargetId is required');
    }

    const providerId = id || randomUUID();
    const [existing, regionTarget] = await Promise.all([
      id ? this.prisma.llmGatewayProvider.findUnique({ where: { id } }) : Promise.resolve(null),
      this.prisma.aiEngineRegionTarget.findUnique({ where: { id: body.regionTargetId } }),
    ]);
    if (!regionTarget) {
      throw new BadRequestException('regionTargetId is invalid');
    }
    const resolvedAiEngineUrl =
      (regionTarget.aiEngineUrl || '').trim()
      || this.getConfiguredAiEngineAdminBaseUrlForRegion(regionTarget.executionRegion);
    if (!regionTarget.deployEnabled || regionTarget.deployStatus !== 'deployed' || !resolvedAiEngineUrl) {
      throw new BadRequestException('Selected region target is not deployed and provider-selectable');
    }
    const apiKey = body.apiKey || existing?.apiKey;
    if (!apiKey) {
      throw new BadRequestException('apiKey is required');
    }

    const provider = await this.prisma.llmGatewayProvider.upsert({
      where: { id: providerId },
      create: {
        id: providerId,
        name: body.name,
        providerType: body.providerType,
        regionTargetId: regionTarget.id,
        cloudVendor: regionTarget.vendor,
        cloudRegionCode: regionTarget.cloudRegionCode,
        region: regionTarget.executionRegion,
        baseUrl: body.baseUrl || '',
        apiKey,
        model: body.model,
        fastModel: body.fastModel || null,
        requestTimeoutS: Number(body.requestTimeoutS || 600),
        connectTimeoutS: Number(body.connectTimeoutS || 15),
        enabled: body.enabled !== false,
        priority: Number(body.priority || 100),
        description: body.description || null,
        extraConfig: body.extraConfig || undefined,
      },
      update: {
        name: body.name,
        providerType: body.providerType,
        regionTargetId: regionTarget.id,
        cloudVendor: regionTarget.vendor,
        cloudRegionCode: regionTarget.cloudRegionCode,
        region: regionTarget.executionRegion,
        baseUrl: body.baseUrl || '',
        apiKey,
        model: body.model,
        fastModel: body.fastModel || null,
        requestTimeoutS: Number(body.requestTimeoutS || 600),
        connectTimeoutS: Number(body.connectTimeoutS || 15),
        enabled: body.enabled !== false,
        priority: Number(body.priority || 100),
        description: body.description || null,
        extraConfig: body.extraConfig || undefined,
      },
    });

    await this.refreshLlmGateway();
    return {
      ...provider,
      apiKey: undefined,
      apiKeySet: true,
      apiKeyMasked: `${apiKey.slice(0, 4)}...${apiKey.slice(-4)}`,
    };
  }

  async deleteLlmProvider(id: string) {
    await this.prisma.llmGatewayProvider.delete({ where: { id } });
    await this.refreshLlmGateway();
    return { deleted: true };
  }

  async listLlmRoutes(executionRegion?: string) {
    const resolvedRegion = executionRegion || this.getDefaultExecutionRegion();
    const [steps, routes] = await Promise.all([
      this.prisma.llmStepCatalog.findMany({
        where: { enabled: true },
        orderBy: [{ stepOrder: 'asc' }, { stepKey: 'asc' }],
      }),
      this.prisma.llmStepRoute.findMany({
        where: {
          region: resolvedRegion,
        },
        include: {
          provider: {
            select: {
              id: true,
              name: true,
              region: true,
              regionTargetId: true,
              providerType: true,
              model: true,
              fastModel: true,
            },
          },
        },
      }),
    ]);

    const routeMap = new Map(routes.map((route) => [route.stepKey, route]));
    return steps.map((step) => {
      const route = routeMap.get(step.stepKey);
      return {
        id: route?.id || null,
        stepKey: step.stepKey,
        stepOrder: step.stepOrder,
        stageLabel: step.stageLabel,
        displayName: step.displayName,
        description: step.description,
        executionRegion: resolvedRegion,
        enabled: route?.enabled ?? false,
        providerId: route?.providerId ?? null,
        providerKey: route?.provider?.name ?? null,
        providerDisplayName: route?.provider?.name ?? null,
        providerRegionTargetId: route?.provider?.regionTargetId ?? null,
        providerRegionDisplayName: route?.provider?.region ?? null,
        modelDefault: route?.provider?.model ?? null,
        modelFast: route?.provider?.fastModel ?? null,
        updatedAt: route?.updatedAt ?? null,
      };
    });
  }

  async getLlmRoute(id: string) {
    const route = await this.prisma.llmStepRoute.findUnique({
      where: { id },
      include: {
        provider: {
          select: {
            id: true,
            name: true,
            region: true,
            regionTargetId: true,
            providerType: true,
            model: true,
            fastModel: true,
          },
        },
      },
    });

    if (!route) {
      throw new NotFoundException('Route not found');
    }

    const step = await this.prisma.llmStepCatalog.findUnique({
      where: { stepKey: route.stepKey },
    });

    return {
      id: route.id,
      stepKey: route.stepKey,
      stepOrder: step?.stepOrder ?? null,
      stageLabel: step?.stageLabel ?? null,
      displayName: step?.displayName ?? null,
      description: step?.description ?? null,
      executionRegion: route.region,
      enabled: route.enabled,
      providerId: route.providerId,
      providerKey: route.provider?.name ?? null,
      providerDisplayName: route.provider?.name ?? null,
      providerRegionTargetId: route.provider?.regionTargetId ?? null,
      providerRegionDisplayName: route.provider?.region ?? null,
      modelDefault: route.provider?.model ?? null,
      modelFast: route.provider?.fastModel ?? null,
      updatedAt: route.updatedAt,
    };
  }

  async upsertLlmRoute(id: string | undefined, body: any) {
    if (!body?.stepKey) {
      throw new BadRequestException('stepKey is required');
    }
    if (!body?.providerId) {
      throw new BadRequestException('providerId is required');
    }

    const [step, provider] = await Promise.all([
      this.prisma.llmStepCatalog.findUnique({
        where: { stepKey: body.stepKey },
      }),
      this.prisma.llmGatewayProvider.findUnique({
        where: { id: body.providerId },
      }),
    ]);

    if (!step || step.enabled === false) {
      throw new BadRequestException('Unknown or disabled stepKey');
    }
    if (!provider) {
      throw new BadRequestException('Provider not found');
    }
    const requestedRegion = body.executionRegion || body.region || provider.region;
    const routeRegion = provider.region || this.getDefaultExecutionRegion();
    if (requestedRegion && requestedRegion !== routeRegion) {
      throw new BadRequestException('executionRegion must match the selected provider region');
    }
    const routeId = id || randomUUID();

    const route = await this.prisma.llmStepRoute.upsert({
      where: id ? { id } : { llm_step_routes_step_key_region_key: { stepKey: body.stepKey, region: routeRegion } },
      create: {
        id: routeId,
        stepKey: body.stepKey,
        region: routeRegion,
        providerId: body.providerId,
        fallbackProviderIds: [],
        modelOverride: null,
        fastModelOverride: null,
        requestTimeoutS: null,
        connectTimeoutS: null,
        enabled: body.enabled !== false,
      },
      update: {
        stepKey: body.stepKey,
        region: routeRegion,
        providerId: body.providerId,
        fallbackProviderIds: [],
        modelOverride: null,
        fastModelOverride: null,
        requestTimeoutS: null,
        connectTimeoutS: null,
        enabled: body.enabled !== false,
      },
      include: {
        provider: {
          select: {
            id: true,
            name: true,
            region: true,
            regionTargetId: true,
            providerType: true,
            model: true,
            fastModel: true,
          },
        },
      },
    });

    await this.refreshLlmGateway();
    return {
      ...route,
      stepMeta: step,
    };
  }

  async deleteLlmRoute(id: string) {
    await this.prisma.llmStepRoute.delete({ where: { id } });
    await this.refreshLlmGateway();
    return { deleted: true };
  }

  async refreshLlmGateway() {
    const urls = await this.getAiEngineAdminBaseUrls();
    const responses = await Promise.all(
      urls.map(async (baseUrl) => {
        const response = await axios.post(
          `${baseUrl}/api/v1/ai/llm-gateway/refresh`,
          {},
          {
            headers: {
              'x-admin-token': this.getAdminToken(),
            },
            timeout: 10000,
          },
        );
        return {
          baseUrl,
          data: response.data,
        };
      }),
    );
    return {
      refreshed: responses.length,
      results: responses,
    };
  }

  async testLlmProvider(providerId: string) {
    const provider = await this.prisma.llmGatewayProvider.findUnique({
      where: { id: providerId },
      select: {
        regionTargetId: true,
      },
    });
    if (!provider) {
      throw new NotFoundException('Provider not found');
    }
    const [baseUrl] = await this.getAiEngineAdminBaseUrls(provider.regionTargetId || undefined);
    if (!baseUrl) {
      throw new BadRequestException('No reachable ai-engine endpoint found for the selected provider');
    }
    const response = await axios.post(
      `${baseUrl}/api/v1/ai/llm-gateway/providers/${providerId}/test`,
      {},
      {
        headers: {
          'x-admin-token': this.getAdminToken(),
        },
        timeout: 30000,
      },
    );
    return response.data;
  }

  // ===================== Stats =====================

  async getStats() {
    const [totalGames, totalUsers, gamesAgg, averages, gameMetrics] = await Promise.all([
      this.prisma.game.count(),
      this.prisma.user.count(),
      this.prisma.game.aggregate({
        _sum: {
          playCount: true,
          likeCount: true,
          forkCount: true,
        },
      }),
      this.prisma.game.aggregate({
        _avg: {
          qualityScore: true,
          retryCount: true,
        },
      }),
      this.prisma.game.findMany({
        select: {
          status: true,
          qualityScore: true,
          failedStage: true,
          failedReason: true,
          retryCount: true,
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

    const failedStageMap: Record<string, number> = {};
    const retryBuckets: Record<string, number> = {
      '0次': 0,
      '1次': 0,
      '2次': 0,
      '3次及以上': 0,
    };
    const qualityBuckets: Record<string, number> = {
      '90分以上': 0,
      '80-89分': 0,
      '70-79分': 0,
      '70分以下': 0,
    };
    const failureReasonMap = new Map<string, { stage: string; reason: string; count: number }>();

    for (const game of gameMetrics) {
      const retries = Number(game.retryCount || 0);
      if (retries <= 0) retryBuckets['0次'] += 1;
      else if (retries === 1) retryBuckets['1次'] += 1;
      else if (retries === 2) retryBuckets['2次'] += 1;
      else retryBuckets['3次及以上'] += 1;

      if (game.qualityScore !== null && game.qualityScore !== undefined) {
        const score = Number(game.qualityScore);
        if (score >= 90) qualityBuckets['90分以上'] += 1;
        else if (score >= 80) qualityBuckets['80-89分'] += 1;
        else if (score >= 70) qualityBuckets['70-79分'] += 1;
        else qualityBuckets['70分以下'] += 1;
      }

      if (!game.failedStage && !game.failedReason) {
        continue;
      }

      const stage = game.failedStage || 'unknown';
      failedStageMap[stage] = (failedStageMap[stage] || 0) + 1;

      const normalizedReason = (game.failedReason || 'unknown error')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 80);
      const key = `${stage}::${normalizedReason}`;
      const current = failureReasonMap.get(key);
      if (current) current.count += 1;
      else {
        failureReasonMap.set(key, {
          stage,
          reason: normalizedReason,
          count: 1,
        });
      }
    }

    const topFailureReasons = Array.from(failureReasonMap.values())
      .sort((a, b) => b.count - a.count)
      .slice(0, 5);

    return {
      totalGames,
      totalUsers,
      totalPlays: gamesAgg._sum.playCount || 0,
      totalLikes: gamesAgg._sum.likeCount || 0,
      totalForks: gamesAgg._sum.forkCount || 0,
      avgQualityScore: Number(averages._avg.qualityScore || 0),
      avgRetryCount: Number(averages._avg.retryCount || 0),
      byStatus: statusMap,
      failedByStage: failedStageMap,
      retryBuckets,
      qualityBuckets,
      topFailureReasons,
    };
  }

  // ===================== System Config =====================

  async listConfigs(category?: string) {
    const where: any = {};
    if (category) where.category = category;
    return this.prisma.systemConfig.findMany({
      where,
      orderBy: [{ category: 'asc' }, { configKey: 'asc' }],
    });
  }

  async getConfig(key: string) {
    const config = await this.prisma.systemConfig.findUnique({
      where: { configKey: key },
    });
    if (!config) throw new NotFoundException(`Config '${key}' not found`);
    return config;
  }

  async upsertConfig(key: string, data: { value: string; description?: string; category?: string }) {
    return this.prisma.systemConfig.upsert({
      where: { configKey: key },
      update: {
        configValue: data.value,
        ...(data.description !== undefined && { description: data.description }),
        ...(data.category !== undefined && { category: data.category }),
      },
      create: {
        id: randomUUID(),
        configKey: key,
        configValue: data.value,
        description: data.description || null,
        category: data.category || 'prompt',
      },
    });
  }

  async initDefaultPrompts() {
    const defaults = [
      {
        key: 'prompt.slot_extraction_system',
        description: 'Stage 1-2: 槽位提取系统提示词',
        value: `You are PlayForge's Slot Filling agent. Extract game design information from the user conversation and return a JSON object with exactly these keys (use null for missing/uncertain values):

{
  "game_type": null,         // one of: dodge, platformer, runner, shooter, puzzle, rhythm, tower_defense, sandbox, card, rpg, idle, racing
  "core_mechanic": null,     // concise Chinese description of the primary gameplay loop
  "theme": null,             // e.g. 太空, 海底, 森林, 西部, 未来
  "input_method": null,      // one of: touch, tap, swipe, tilt
  "win_condition": null,     // e.g. 存活60秒, 到达终点, 消灭所有敌人
  "difficulty": null,        // one of: easy, medium, hard, progressive
  "visual_style": null,      // one of: pixel, geometric, emoji, neon
  "audio_style": null,       // one of: chiptune, ambient, none
  "special_rules": null,     // array of strings, e.g. ["分裂机制"]
  "reference_game": null     // e.g. "Flappy Bird"
}

Return ONLY the JSON object with no extra text. Keep existing non-null values unchanged unless the user explicitly corrects them.`,
      },
      {
        key: 'prompt.dialogue_system',
        description: 'Stage 1: 对话引擎系统提示词',
        value: `You are PlayForge's friendly game creation assistant. You help users describe their game idea in 2-4 conversational turns.

Current slot fill state: {slot_summary}
Missing required info: {missing_slots}

Your job:
- If state is "greeting": Welcome the user and invite them to describe their game idea
- If state is "describing": Acknowledge what they said, extract info, ask about the most important missing slot in a natural way (one question at a time)
- If state is "clarifying": Confirm what you understood, ask about remaining missing slots
- If state is "confirmed": Summarize the complete game design and ask for confirmation

Rules:
- Be concise, friendly, and enthusiastic
- Ask at most ONE clarifying question per turn
- Respond in the same language the user uses (Chinese or English)
- Never mention "slots" or "JSON" to the user`,
      },
      {
        key: 'prompt.code_gen_system',
        description: 'Stage 5: 代码生成主系统提示词',
        value: `You are PlayForge GameEngine, an expert HTML5 game developer.

OUTPUT FORMAT:
- Return ONLY a single complete HTML file (<!DOCTYPE html> ... </html>)
- No markdown code fences, no explanations, no extra text
- Inline all CSS and JavaScript inside the HTML

HARD RULES:
- Single self-contained file, zero external dependencies
- Use Canvas 2D API (no WebGL, no libraries)
- Touch-friendly: implement touchstart/touchmove/touchend events
- Target 60fps with requestAnimationFrame game loop
- Maximum 500 lines of code
- ES2017 syntax only
- FORBIDDEN APIs: eval, Function(), import, require, fetch, XMLHttpRequest, WebSocket, localStorage, document.cookie, document.write`,
      },
      {
        key: 'prompt.game_design_template',
        description: 'Stage 5: GDD 转代码提示词模板',
        value: `GAME DESIGN DOCUMENT:

Game Type: {game_type}
Theme: {theme} | Art Style: {art_style}
Color Palette: {palette}

Canvas: {canvas_w}×{canvas_h}px, DPR adaptive, target 60fps

Player: speed={player_speed}px/frame, hitbox={hitbox_ratio}x
Obstacle/Spawn: base_speed={obstacle_speed}, interval={spawn_interval}ms
Difficulty: {speed_formula}
Score: +{score_per_second}/s, +{score_per_collect} per collectible
Lives: {lives} | Expected survival: {expected_s}s

Win condition: {win_condition}
Lose condition: {lose_condition}

Entities:
{entities_desc}

Input mapping:
{input_map}

Game states: init → playing → [paused | game_over] → init

UI:
- Score: top-left at (16, 36)
- Lives: top-right
- Game Over overlay: centered, show score + "Tap to restart"

Implement the complete, playable game following every detail above.`,
      },
      {
        key: 'prompt.platform_standard',
        description: 'Stage 5: 标准 H5 平台约束提示词',
        value: `PLATFORM: Standard H5 Mobile Browser
- Max file size: 500 KB
- Input: touch + mouse fallback
- Canvas: single canvas element, id="gameCanvas" `,
      },
      {
        key: 'prompt.iterate_classify',
        description: 'Stage 7: 用户反馈分类提示词',
        value: `Classify this user feedback into one category. Return ONLY the category name.

Categories:
- param_adjust: change a numeric value (speed, color, size, lives, score)
- element_change: add or remove a game element (new entity, background effect, UI element)
- mechanic_change: change how the game works (new ability, different win condition, gameplay rule)
- major_overhaul: fundamentally different game type or complete redesign

Feedback: "{feedback}"

Category:`,
      },
      {
        key: 'prompt.param_adjust',
        description: 'Stage 7: 参数调整提示词',
        value: `You are editing HTML5 game code. The user wants to change a parameter.
Apply ONLY the requested parameter change. Keep everything else identical.

User feedback: {feedback}

Current code:
{code}

Return ONLY the complete updated HTML file with no extra text.`,
      },
      {
        key: 'prompt.element_change',
        description: 'Stage 7: 元素修改提示词',
        value: `You are editing HTML5 game code. Add or remove one game element as requested.
Make the minimal change needed. Keep the rest of the code identical.

User feedback: {feedback}

Current code:
{code}

Return ONLY the complete updated HTML file with no extra text.`,
      },
      {
        key: 'prompt.mechanic_change',
        description: 'Stage 7: 机制修改提示词',
        value: `You are editing HTML5 game code. Modify the game mechanics as requested.
You may rewrite the relevant section(s) of the code. Keep the rest unchanged.

User feedback: {feedback}
Conversation history: {history}

Current code:
{code}

Return ONLY the complete updated HTML file with no extra text.`,
      },
      {
        key: 'prompt.qa_fix',
        description: 'Stage 6: QA 自动修复提示词',
        value: `You are fixing a HTML5 game. The code has the following issues that MUST be fixed:

{error_list}

Game type: {game_type}

Fix ONLY the listed issues. Do not change the game logic or visual design.
Return ONLY the complete fixed HTML file with no extra text.

Current code:
{code}`,
      },
    ];

    let created = 0;
    let skipped = 0;
    for (const d of defaults) {
      const existing = await this.prisma.systemConfig.findUnique({
        where: { configKey: d.key },
      });
      if (existing) {
        skipped++;
        continue;
      }
      await this.prisma.systemConfig.create({
        data: {
          id: randomUUID(),
          configKey: d.key,
          configValue: d.value,
          description: d.description,
          category: 'prompt',
        },
      });
      created++;
    }
    return { created, skipped, total: defaults.length };
  }

  // ===================== Migration =====================

  async runMigration() {
    const sql = `
      CREATE TABLE IF NOT EXISTS system_configs (
        id VARCHAR(36) NOT NULL,
        config_key VARCHAR(128) NOT NULL,
        config_value LONGTEXT NOT NULL,
        description VARCHAR(255) NULL,
        category VARCHAR(64) NOT NULL DEFAULT 'general',
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        PRIMARY KEY (id),
        UNIQUE INDEX system_configs_config_key_key (config_key),
        INDEX system_configs_category_idx (category)
      ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    `;
    await this.prisma.$executeRawUnsafe(sql);
    return { success: true, message: 'system_configs table created' };
  }
}
