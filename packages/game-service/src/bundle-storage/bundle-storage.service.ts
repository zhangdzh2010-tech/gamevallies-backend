import { Injectable, Logger } from '@nestjs/common';
import { PrismaClient } from '@prisma/client';
import { randomUUID } from 'crypto';

// 复用 PrismaClient 单例，避免函数服务冷启动时连接数爆炸
let prismaInstance: PrismaClient | null = null;

function getPrisma(): PrismaClient {
  if (!prismaInstance) {
    prismaInstance = new PrismaClient({
      datasources: { db: { url: process.env.DATABASE_URL } },
    });
  }
  return prismaInstance;
}

@Injectable()
export class BundleStorageService {
  private readonly logger = new Logger(BundleStorageService.name);

  private get prisma(): PrismaClient {
    return getPrisma();
  }

  private normalizeStrategy(strategy?: string): string {
    void strategy;
    return 'full_generation';
  }

  private compactObject<T extends Record<string, unknown>>(obj: T): Partial<T> {
    return Object.fromEntries(
      Object.entries(obj).filter(([, value]) => value !== undefined && value !== null),
    ) as Partial<T>;
  }

  private toBundleData(doc: {
    gameId: string;
    version?: number;
    htmlCode: string;
    cssCode?: string;
    jsCode?: string;
    metadata?: any;
    previewUrl?: string;
  }) {
    const metadata = doc.metadata || {};
    const spec = metadata.gameSpec ?? null;
    const generationMeta = this.compactObject({
      strategy: this.normalizeStrategy(metadata.strategy),
      template_id: metadata.templateId ?? undefined,
      model: metadata.model ?? undefined,
      tokens_used: metadata.tokensUsed ?? undefined,
      generation_time_ms: metadata.genTimeMs ?? metadata.generationTimeMs ?? 0,
      qa_passed: Boolean(metadata.qaPassed ?? metadata.qa_passed ?? false),
      qa_retries: metadata.qaRetries ?? metadata.qa_retries ?? 0,
    });

    return {
      id: randomUUID(),
      gameId: doc.gameId,
      version: doc.version ?? 1,
      htmlCode: doc.htmlCode,
      cssCode: doc.cssCode ?? '',
      jsCode: doc.jsCode ?? '',
      spec: spec ?? undefined,
      aiConversation: metadata.aiConversation ?? [],
      generationMeta,
      metadata,
      previewUrl: doc.previewUrl ?? null,
      codeSizeBytes: metadata.codeSizeBytes ?? Buffer.byteLength(doc.htmlCode, 'utf8'),
    };
  }

  private fromBundle(bundle: any): any | null {
    if (!bundle) return null;
    return {
      id: bundle.id,
      gameId: bundle.gameId,
      version: bundle.version,
      htmlCode: bundle.htmlCode,
      cssCode: bundle.cssCode ?? '',
      jsCode: bundle.jsCode ?? '',
      metadata: bundle.metadata ?? {},
      generatedAt: bundle.createdAt,
      previewUrl: bundle.previewUrl ?? null,
      createdAt: bundle.createdAt,
      updatedAt: bundle.updatedAt,
    };
  }

  async saveBundleDoc(doc: {
    gameId: string;
    version?: number;
    htmlCode: string;
    cssCode?: string;
    jsCode?: string;
    metadata?: any;
    previewUrl?: string;
  }): Promise<any> {
    try {
      const data = this.toBundleData(doc);
      const bundle = await this.prisma.gameBundle.create({ data });
      this.logger.log(`Bundle saved: gameId=${doc.gameId} version=${data.version}`);
      return this.fromBundle(bundle);
    } catch (error) {
      this.logger.error(`Failed to save bundle: ${error.message}`);
      throw error;
    }
  }

  async getBundleByGameId(gameId: string, version?: number): Promise<any | null> {
    try {
      if (version) {
        const bundle = await this.prisma.gameBundle.findUnique({
          where: { uk_game_version: { gameId, version } },
        });
        return this.fromBundle(bundle);
      }

      const bundle = await this.prisma.gameBundle.findFirst({
        where: { gameId },
        orderBy: { version: 'desc' },
      });
      return this.fromBundle(bundle);
    } catch (error) {
      this.logger.error(`Failed to get bundle: ${error.message}`);
      throw error;
    }
  }

  async getBundleHistory(gameId: string): Promise<any[]> {
    try {
      const bundles = await this.prisma.gameBundle.findMany({
        where: { gameId },
        orderBy: { version: 'asc' },
      });
      return bundles.map((b) => this.fromBundle(b));
    } catch (error) {
      this.logger.error(`Failed to get bundle history: ${error.message}`);
      throw error;
    }
  }

  async updateBundleMetadata(gameId: string, version: number, metadata: any): Promise<any> {
    try {
      const bundle = await this.prisma.gameBundle.update({
        where: { uk_game_version: { gameId, version } },
        data: { metadata },
      });
      return this.fromBundle(bundle);
    } catch (error) {
      this.logger.error(`Failed to update bundle metadata: ${error.message}`);
      throw error;
    }
  }

  async deleteBundlesByGameId(gameId: string): Promise<any> {
    try {
      return await this.prisma.gameBundle.deleteMany({ where: { gameId } });
    } catch (error) {
      this.logger.error(`Failed to delete bundles: ${error.message}`);
      throw error;
    }
  }
}
