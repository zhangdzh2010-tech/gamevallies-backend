import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../prisma/prisma.service';
import { BundleStorageService } from '../bundle-storage/bundle-storage.service';

export interface BundleCdnTosConfig {
  accessKeyId: string;
  accessKeySecret: string;
  region: string;
  bucket: string;
  endpoint: string;
  publicBaseUrl: string;
}

export interface PublishableGameLike {
  id: string;
  status?: string | null;
  visibility?: string | null;
}

function normalizeString(value: unknown): string {
  if (value === undefined || value === null) {
    return '';
  }
  return String(value).trim();
}

/**
 * Mirrors published game bundles to Volcengine TOS so that public plays can be
 * served from a CDN direct link instead of hitting the function instance + RDS
 * on every load. Disabled by default (BUNDLE_CDN_ENABLED=false => zero behavior
 * change); all sync entry points are fire-and-forget and never throw.
 */
@Injectable()
export class BundleCdnService {
  private readonly logger = new Logger(BundleCdnService.name);

  // Overridable in unit tests so no real TOS SDK / network call is involved.
  tosClientFactory: (config: BundleCdnTosConfig) => any = (config) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { TosClient } = require('@volcengine/tos-sdk');
    return new TosClient({
      accessKeyId: config.accessKeyId,
      accessKeySecret: config.accessKeySecret,
      region: config.region,
      endpoint: config.endpoint,
    });
  };

  constructor(
    private readonly prisma: PrismaService,
    private readonly configService: ConfigService,
    private readonly bundleStorageService: BundleStorageService,
  ) {}

  isEnabled(): boolean {
    const raw = normalizeString(this.configService.get<string>('BUNDLE_CDN_ENABLED')).toLowerCase();
    return ['1', 'true', 'yes', 'on'].includes(raw);
  }

  buildObjectKey(gameId: string, version: number): string {
    return `game-bundles/${gameId}/${version}/index.html`;
  }

  private getTosConfig(): BundleCdnTosConfig | null {
    // Credentials follow the same convention as GrowthService app-release uploads.
    const accessKeyId = normalizeString(
      this.configService.get<string>('VOLCENGINE_ACCESS_KEY')
      || this.configService.get<string>('VOLCENGINE_ACCESS_KEY_ID'),
    );
    const accessKeySecret = normalizeString(
      this.configService.get<string>('VOLCENGINE_SECRET_KEY')
      || this.configService.get<string>('VOLCENGINE_ACCESS_KEY_SECRET'),
    );
    const region = normalizeString(
      this.configService.get<string>('BUNDLE_CDN_TOS_REGION')
      || this.configService.get<string>('VOLCENGINE_REGION'),
    ) || 'cn-shanghai';
    const bucket = normalizeString(
      this.configService.get<string>('BUNDLE_CDN_TOS_BUCKET')
      || this.configService.get<string>('TOS_BUCKET')
      || this.configService.get<string>('VOLCENGINE_TOS_BUCKET'),
    );
    const endpoint = normalizeString(
      this.configService.get<string>('BUNDLE_CDN_TOS_ENDPOINT')
      || this.configService.get<string>('TOS_ENDPOINT'),
    ) || `https://tos-${region}.volces.com`;

    if (!accessKeyId || !accessKeySecret || !bucket) {
      return null;
    }

    const publicBaseUrl = normalizeString(
      this.configService.get<string>('BUNDLE_CDN_PUBLIC_BASE_URL'),
    ) || this.buildDefaultPublicBaseUrl(bucket, endpoint);

    return {
      accessKeyId,
      accessKeySecret,
      region,
      bucket,
      endpoint,
      publicBaseUrl,
    };
  }

  private buildDefaultPublicBaseUrl(bucket: string, endpoint: string): string {
    try {
      const parsed = new URL(endpoint);
      return `${parsed.protocol}//${bucket}.${parsed.host}`;
    } catch {
      return `https://${bucket}.${endpoint.replace(/^https?:\/\//, '')}`;
    }
  }

  private buildPublicUrl(publicBaseUrl: string, gameId: string, version: number): string {
    return `${publicBaseUrl.replace(/\/+$/, '')}/${this.buildObjectKey(gameId, version)}`;
  }

  private isPubliclyPlayable(game: PublishableGameLike | null | undefined): boolean {
    return game?.status === 'published' && (game?.visibility || 'public') === 'public';
  }

  private extractCdnUrl(metadata: unknown): string | null {
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
      return null;
    }
    const raw = (metadata as Record<string, unknown>).cdnUrl;
    return typeof raw === 'string' && raw.trim() ? raw.trim() : null;
  }

  private extractErrorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  /**
   * Fire-and-forget hook used by publish / persist flows. Never throws and
   * never rejects, so callers can invoke it as a plain statement.
   */
  scheduleBundleSync(gameId: string, version?: number): void {
    if (!this.isEnabled()) {
      return;
    }
    void this.syncBundleToCdn(gameId, version).catch((error) => {
      this.logger.warn(
        `Bundle CDN sync crashed unexpectedly for game ${gameId}: ${this.extractErrorMessage(error)}`,
      );
    });
  }

  /**
   * Uploads a bundle version (latest when version is omitted) to TOS and
   * records the public URL in the bundle metadata (metadata.cdnUrl /
   * metadata.cdnUploadedAt, merged with the existing metadata keys).
   * Only published games with public visibility are mirrored. Returns whether
   * the upload happened; failures are logged as warnings and never thrown.
   */
  async syncBundleToCdn(gameId: string, version?: number): Promise<boolean> {
    try {
      if (!this.isEnabled()) {
        return false;
      }

      const config = this.getTosConfig();
      if (!config) {
        this.logger.warn(
          `Bundle CDN is enabled but TOS config is incomplete; skipping sync for game ${gameId}`,
        );
        return false;
      }

      const game = await this.prisma.game.findUnique({
        where: { id: gameId },
        select: { id: true, status: true, visibility: true },
      });
      if (!this.isPubliclyPlayable(game)) {
        return false;
      }

      const bundle = await this.bundleStorageService.getBundleByGameId(gameId, version);
      if (!bundle || typeof bundle.htmlCode !== 'string' || !bundle.htmlCode.trim()) {
        this.logger.warn(
          `Bundle CDN sync skipped: no playable bundle for game ${gameId} version ${version ?? 'latest'}`,
        );
        return false;
      }

      const bundleVersion = Number(bundle.version) || 1;
      const objectKey = this.buildObjectKey(gameId, bundleVersion);
      const cdnUrl = this.buildPublicUrl(config.publicBaseUrl, gameId, bundleVersion);
      const body = Buffer.from(bundle.htmlCode, 'utf8');
      const tosClient = this.tosClientFactory(config);

      await tosClient.putObject({
        bucket: config.bucket,
        key: objectKey,
        body,
        contentLength: body.length,
        contentType: 'text/html; charset=utf-8',
      });

      const existingMetadata = bundle.metadata && typeof bundle.metadata === 'object' && !Array.isArray(bundle.metadata)
        ? bundle.metadata
        : {};
      await this.bundleStorageService.updateBundleMetadata(gameId, bundleVersion, {
        ...existingMetadata,
        cdnUrl,
        cdnUploadedAt: new Date().toISOString(),
      });

      this.logger.log(`Bundle synced to CDN: game=${gameId} version=${bundleVersion} url=${cdnUrl}`);
      return true;
    } catch (error) {
      this.logger.warn(
        `Failed to sync bundle to CDN for game ${gameId}: ${this.extractErrorMessage(error)}`,
      );
      return false;
    }
  }

  /**
   * Returns `{ cdnUrl }` when the game should be played from the CDN direct
   * link, otherwise `{}` (self-hosted route stays in charge). Only published
   * + public games whose latest bundle metadata carries a cdnUrl qualify, so
   * banned / private / draft games automatically fall back by current state.
   */
  async buildCdnUrlPatch(game: PublishableGameLike): Promise<{ cdnUrl?: string }> {
    const cdnUrl = await this.resolvePublicCdnUrl(game);
    return cdnUrl ? { cdnUrl } : {};
  }

  async resolvePublicCdnUrl(game: PublishableGameLike | null | undefined): Promise<string | null> {
    try {
      if (!this.isEnabled() || !game?.id || !this.isPubliclyPlayable(game)) {
        return null;
      }

      const latestBundle = await this.prisma.gameBundle.findFirst({
        where: { gameId: game.id },
        orderBy: { version: 'desc' },
        select: { metadata: true },
      });

      return this.extractCdnUrl(latestBundle?.metadata);
    } catch (error) {
      this.logger.warn(
        `Failed to resolve CDN url for game ${game?.id}: ${this.extractErrorMessage(error)}`,
      );
      return null;
    }
  }
}
