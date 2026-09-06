import {
  BadRequestException,
  Injectable,
  Logger,
  NotFoundException,
  OnModuleInit,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createHash, randomUUID } from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { PrismaService } from '../prisma/prisma.service';
import {
  DEFAULT_APP_PROMO_CONFIG,
  DEFAULT_APP_PROMO_COPY,
  GROWTH_CONFIG_CATEGORY,
  GROWTH_PROMO_CONFIG_KEYS,
} from './growth.constants';

type PromoCopy = typeof DEFAULT_APP_PROMO_COPY;
type PromoConfig = typeof DEFAULT_APP_PROMO_CONFIG;
type AppReleasePlatform = 'ios' | 'android';
type AppReleaseSourceType = 'upload' | 'external_url' | 'app_store';
type AppReleaseStatus = 'draft' | 'published' | 'archived';

interface PromoEventRequest {
  scene?: string;
  eventType?: string;
  deviceId?: string;
  gameId?: string | null;
  platform?: string | null;
  channel?: string | null;
  extra?: Record<string, unknown> | null;
}

interface PromoEventQuery {
  page?: string;
  limit?: string;
  scene?: string;
  eventType?: string;
  platform?: string;
}

interface AppReleaseInput {
  platform?: string;
  channel?: string;
  versionName?: string;
  buildNumber?: string | null;
  releaseNotes?: string | null;
  downloadUrl?: string | null;
  qrCodeUrl?: string | null;
  sourceType?: string;
  status?: string;
  isActive?: boolean;
}

interface UploadedReleaseFile {
  originalname?: string;
  mimetype?: string;
  size?: number;
  buffer?: Buffer;
  path?: string;
}

interface AppReleaseTosConfig {
  accessKeyId: string;
  accessKeySecret: string;
  region: string;
  bucket: string;
  endpoint: string;
  keyPrefix: string;
  signedUrlExpires: number;
}

interface AppReleaseListQuery {
  page?: string;
  limit?: string;
  platform?: string;
  status?: string;
  channel?: string;
}

const APP_PROMO_CONFIG_DESCRIPTIONS: Record<string, string> = {
  [GROWTH_PROMO_CONFIG_KEYS.enabled]: 'Enable H5 to app download promo',
  [GROWTH_PROMO_CONFIG_KEYS.wechatMode]: 'WeChat browser handling mode',
  [GROWTH_PROMO_CONFIG_KEYS.createCompleteEnabled]: 'Enable create-complete promo scene',
  [GROWTH_PROMO_CONFIG_KEYS.createCompleteCooldownHours]: 'Create-complete promo cooldown in hours',
  [GROWTH_PROMO_CONFIG_KEYS.createCompleteMaxImpressions30d]: 'Create-complete promo max impressions per 30d',
  [GROWTH_PROMO_CONFIG_KEYS.playNudgeEnabled]: 'Enable repeated-play promo scene',
  [GROWTH_PROMO_CONFIG_KEYS.playNudgeMinSessions]: 'Minimum local play sessions before play promo',
  [GROWTH_PROMO_CONFIG_KEYS.playNudgeMinSeconds]: 'Minimum local play seconds before play promo',
  [GROWTH_PROMO_CONFIG_KEYS.playNudgeCooldownHours]: 'Play promo cooldown in hours',
  [GROWTH_PROMO_CONFIG_KEYS.playNudgeMaxImpressions30d]: 'Play promo max impressions per 30d',
  [GROWTH_PROMO_CONFIG_KEYS.universalUrl]: 'Universal app download URL',
  [GROWTH_PROMO_CONFIG_KEYS.copyJson]: 'Promo copy JSON payload',
};

const AppReleasePlatform = {
  ios: 'ios' as AppReleasePlatform,
  android: 'android' as AppReleasePlatform,
};

const AppReleaseSourceType = {
  upload: 'upload' as AppReleaseSourceType,
  external_url: 'external_url' as AppReleaseSourceType,
  app_store: 'app_store' as AppReleaseSourceType,
};

const AppReleaseStatus = {
  draft: 'draft' as AppReleaseStatus,
  published: 'published' as AppReleaseStatus,
  archived: 'archived' as AppReleaseStatus,
};

function normalizeString(value: unknown): string {
  if (value === undefined || value === null) {
    return '';
  }
  return String(value).trim();
}

function sanitizeFileName(fileName: string): string {
  return normalizeString(fileName)
    .replace(/[^a-zA-Z0-9._-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    || 'release.apk';
}

function mergePromoCopy(value: unknown, fallback: PromoCopy): PromoCopy {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return fallback;
  }

  const partial = value as Record<string, any>;

  return {
    createComplete: {
      ...fallback.createComplete,
      ...(partial.createComplete || {}),
    },
    playNudge: {
      ...fallback.playNudge,
      ...(partial.playNudge || {}),
    },
    wechatGuide: {
      ...fallback.wechatGuide,
      ...(partial.wechatGuide || {}),
    },
  };
}

function parseJsonObject(value: string | null | undefined, fallback: PromoCopy): PromoCopy {
  if (!value) {
    return fallback;
  }

  try {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return fallback;
    }

    return mergePromoCopy(parsed, fallback);
  } catch {
    return fallback;
  }
}

@Injectable()
export class GrowthService implements OnModuleInit {
  private readonly logger = new Logger(GrowthService.name);
  private schemaReadyPromise: Promise<void> | null = null;

  constructor(
    private readonly prisma: PrismaService,
    private readonly configService: ConfigService,
  ) {}

  async onModuleInit() {
    await this.ensureGrowthSchema({ throwOnError: false });
  }

  private get appReleaseDelegate(): any {
    return this.prisma.appRelease;
  }

  private get appPromoEventDelegate(): any {
    return this.prisma.appPromoEvent;
  }

  private async ensureGrowthSchema(options: { throwOnError?: boolean } = {}) {
    if (!this.schemaReadyPromise) {
      this.schemaReadyPromise = this.createGrowthTables();
    }

    try {
      await this.schemaReadyPromise;
    } catch (error) {
      this.schemaReadyPromise = null;
      const message = error instanceof Error ? error.message : String(error);
      this.logger.error(`Failed to ensure growth tables: ${message}`);
      if (options.throwOnError !== false) {
        throw error;
      }
    }
  }

  private async createGrowthTables() {
    await this.prisma.$executeRawUnsafe(`
      CREATE TABLE IF NOT EXISTS \`app_releases\` (
        \`id\` VARCHAR(36) NOT NULL,
        \`platform\` ENUM('ios', 'android') NOT NULL,
        \`channel\` VARCHAR(32) NOT NULL DEFAULT 'production',
        \`version_name\` VARCHAR(64) NOT NULL,
        \`build_number\` VARCHAR(64) NULL,
        \`release_notes\` LONGTEXT NULL,
        \`download_url\` VARCHAR(512) NULL,
        \`qr_code_url\` VARCHAR(512) NULL,
        \`file_name\` VARCHAR(255) NULL,
        \`file_size\` BIGINT NULL,
        \`mime_type\` VARCHAR(128) NULL,
        \`storage_key\` VARCHAR(512) NULL,
        \`checksum_sha256\` VARCHAR(64) NULL,
        \`source_type\` ENUM('upload', 'external_url', 'app_store') NOT NULL DEFAULT 'upload',
        \`status\` ENUM('draft', 'published', 'archived') NOT NULL DEFAULT 'draft',
        \`is_active\` TINYINT(1) NOT NULL DEFAULT 0,
        \`published_at\` DATETIME(3) NULL,
        \`created_by\` VARCHAR(64) NULL,
        \`created_at\` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        \`updated_at\` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        PRIMARY KEY (\`id\`),
        INDEX \`app_releases_platform_channel_status_idx\` (\`platform\`, \`channel\`, \`status\`),
        INDEX \`app_releases_platform_channel_is_active_idx\` (\`platform\`, \`channel\`, \`is_active\`)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    `);

    await this.prisma.$executeRawUnsafe(`
      CREATE TABLE IF NOT EXISTS \`app_promo_events\` (
        \`id\` VARCHAR(36) NOT NULL,
        \`scene\` VARCHAR(64) NOT NULL,
        \`event_type\` VARCHAR(64) NOT NULL,
        \`user_id\` VARCHAR(36) NULL,
        \`game_id\` VARCHAR(36) NULL,
        \`device_id\` VARCHAR(128) NOT NULL,
        \`platform\` VARCHAR(32) NULL,
        \`channel\` VARCHAR(64) NULL,
        \`user_agent\` VARCHAR(512) NULL,
        \`ip_hash\` VARCHAR(128) NULL,
        \`extra_json\` JSON NULL,
        \`created_at\` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        PRIMARY KEY (\`id\`),
        INDEX \`app_promo_events_scene_event_type_created_at_idx\` (\`scene\`, \`event_type\`, \`created_at\`),
        INDEX \`app_promo_events_platform_created_at_idx\` (\`platform\`, \`created_at\`),
        INDEX \`app_promo_events_device_id_created_at_idx\` (\`device_id\`, \`created_at\`)
      ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    `);
  }

  private parseBoolean(value: unknown, fallback: boolean): boolean {
    const raw = normalizeString(value).toLowerCase();
    if (!raw) {
      return fallback;
    }
    if (['1', 'true', 'yes', 'on'].includes(raw)) {
      return true;
    }
    if (['0', 'false', 'no', 'off'].includes(raw)) {
      return false;
    }
    return fallback;
  }

  private parseInteger(value: unknown, fallback: number): number {
    const parsed = Number.parseInt(normalizeString(value), 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  private getPublicBaseUrl(): string {
    const publicApiBaseUrl = this.configService.get<string>('PUBLIC_API_BASE_URL');
    const appUrl = this.configService.get<string>('APP_URL', 'http://localhost:3002');
    return normalizeString(publicApiBaseUrl || appUrl).replace(/\/$/, '');
  }

  private getAppReleaseUploadDir(): string {
    const configured = normalizeString(this.configService.get<string>('APP_RELEASE_UPLOAD_DIR'));
    return path.resolve(process.cwd(), configured || 'files/app-releases');
  }

  private getAppReleaseTosConfig(): AppReleaseTosConfig | null {
    const accessKeyId = normalizeString(
      this.configService.get<string>('VOLCENGINE_ACCESS_KEY')
      || this.configService.get<string>('VOLCENGINE_ACCESS_KEY_ID'),
    );
    const accessKeySecret = normalizeString(
      this.configService.get<string>('VOLCENGINE_SECRET_KEY')
      || this.configService.get<string>('VOLCENGINE_ACCESS_KEY_SECRET'),
    );
    const region = normalizeString(
      this.configService.get<string>('VOLCENGINE_REGION'),
    ) || 'cn-shanghai';
    const bucket = normalizeString(
      this.configService.get<string>('TOS_BUCKET')
      || this.configService.get<string>('VOLCENGINE_TOS_BUCKET'),
    );
    const endpoint = normalizeString(
      this.configService.get<string>('TOS_ENDPOINT'),
    ) || `https://tos-${region}.volces.com`;

    if (!accessKeyId || !accessKeySecret || !bucket) {
      return null;
    }

    return {
      accessKeyId,
      accessKeySecret,
      region,
      bucket,
      endpoint,
      keyPrefix: normalizeString(this.configService.get<string>('APP_RELEASE_TOS_PREFIX')) || 'app-releases',
      signedUrlExpires: Math.max(
        this.parseInteger(
          this.configService.get<string>('APP_RELEASE_TOS_SIGNED_URL_EXPIRES'),
          600,
        ),
        60,
      ),
    };
  }

  private createTosClient(config: AppReleaseTosConfig): any {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { TosClient } = require('@volcengine/tos-sdk');
    return new TosClient({
      accessKeyId: config.accessKeyId,
      accessKeySecret: config.accessKeySecret,
      region: config.region,
      endpoint: config.endpoint,
    });
  }

  private createOssClient(): any {
    const read = (key: string) => normalizeString(this.configService.get<string>(key));
    const accessKeyId = read('ALIYUN_OSS_ACCESS_KEY_ID');
    const accessKeySecret = read('ALIYUN_OSS_ACCESS_KEY_SECRET');
    const bucket = read('ALIYUN_OSS_BUCKET');
    const region = read('ALIYUN_OSS_REGION');
    const endpoint = read('ALIYUN_OSS_ENDPOINT');
    if (!accessKeyId || !accessKeySecret || !bucket || !region || endpoint !== `https://oss-${region}.aliyuncs.com`) {
      throw new BadRequestException('OSS configuration is incomplete or invalid');
    }
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const OSS = require('ali-oss');
    return new OSS({ accessKeyId, accessKeySecret, bucket, region: `oss-${region}`, endpoint, secure: true });
  }

  private getOssPrefix(): string {
    const prefix = normalizeString(this.configService.get<string>('ALIYUN_OSS_PREFIX')).replace(/^\/+|\/+$/g, '');
    if (!prefix || !/^[a-zA-Z0-9/_-]+$/.test(prefix) || prefix.split('/').includes('..')) {
      throw new BadRequestException('ALIYUN_OSS_PREFIX must isolate application objects');
    }
    return prefix;
  }

  private encodeLocalStorageKey(relativeKey: string): string {
    return `local:${relativeKey}`;
  }

  private encodeTosStorageKey(objectKey: string): string {
    return `tos:${objectKey}`;
  }

  private decodeStorageKey(storageKey: string): { provider: 'local' | 'tos' | 'oss'; objectKey: string } {
    if (storageKey.startsWith('oss:')) return { provider: 'oss', objectKey: storageKey.slice(4) };
    if (storageKey.startsWith('tos:')) {
      return {
        provider: 'tos',
        objectKey: storageKey.slice(4),
      };
    }

    if (storageKey.startsWith('local:')) {
      return {
        provider: 'local',
        objectKey: storageKey.slice(6),
      };
    }

    return {
      provider: 'local',
      objectKey: storageKey,
    };
  }

  private buildReleaseObjectKey(platform: AppReleasePlatform, releaseId: string, fileName: string): string {
    return path.posix.join(
      normalizeString(this.getAppReleaseTosConfig()?.keyPrefix) || 'app-releases',
      String(platform),
      releaseId,
      fileName,
    );
  }

  private getPublicReleaseDownloadUrl(releaseId: string): string {
    return `${this.getPublicBaseUrl()}/api/v1/growth/app-releases/${releaseId}/download`;
  }

  private getResolvedReleaseDownloadUrl(release: any): string | null {
    if (!release) {
      return null;
    }

    if (release.sourceType === AppReleaseSourceType.upload) {
      return normalizeString(release.storageKey)
        ? this.getPublicReleaseDownloadUrl(release.id)
        : null;
    }

    return normalizeString(release.downloadUrl) || null;
  }

  private serializeAppRelease(release: any) {
    if (!release) {
      return release;
    }

    return {
      ...release,
      downloadUrl: this.getResolvedReleaseDownloadUrl(release),
    };
  }

  private buildReleaseLinks(releases: Record<string, any>) {
    const iosUrl = normalizeString(releases?.ios?.downloadUrl);
    const androidUrl = normalizeString(releases?.android?.downloadUrl);

    return {
      universalUrl: iosUrl || androidUrl || '',
      iosUrl,
      androidUrl,
    };
  }

  private async getPromoConfigRows() {
    return this.prisma.systemConfig.findMany({
      where: {
        category: GROWTH_CONFIG_CATEGORY,
        configKey: {
          in: Object.values(GROWTH_PROMO_CONFIG_KEYS),
        },
      },
      orderBy: { configKey: 'asc' },
    });
  }

  private buildPromoConfig(rows: Array<{ configKey: string; configValue: string }>): PromoConfig {
    const map = new Map(rows.map((row) => [row.configKey, row.configValue]));
    const copy = parseJsonObject(
      map.get(GROWTH_PROMO_CONFIG_KEYS.copyJson),
      DEFAULT_APP_PROMO_COPY,
    );

    return {
      enabled: this.parseBoolean(
        map.get(GROWTH_PROMO_CONFIG_KEYS.enabled),
        DEFAULT_APP_PROMO_CONFIG.enabled,
      ),
      wechatMode: normalizeString(
        map.get(GROWTH_PROMO_CONFIG_KEYS.wechatMode),
      ) || DEFAULT_APP_PROMO_CONFIG.wechatMode,
      scenes: {
        createComplete: {
          enabled: this.parseBoolean(
            map.get(GROWTH_PROMO_CONFIG_KEYS.createCompleteEnabled),
            DEFAULT_APP_PROMO_CONFIG.scenes.createComplete.enabled,
          ),
          cooldownHours: this.parseInteger(
            map.get(GROWTH_PROMO_CONFIG_KEYS.createCompleteCooldownHours),
            DEFAULT_APP_PROMO_CONFIG.scenes.createComplete.cooldownHours,
          ),
          maxImpressions30d: this.parseInteger(
            map.get(GROWTH_PROMO_CONFIG_KEYS.createCompleteMaxImpressions30d),
            DEFAULT_APP_PROMO_CONFIG.scenes.createComplete.maxImpressions30d,
          ),
        },
        playNudge: {
          enabled: this.parseBoolean(
            map.get(GROWTH_PROMO_CONFIG_KEYS.playNudgeEnabled),
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.enabled,
          ),
          minSessions: this.parseInteger(
            map.get(GROWTH_PROMO_CONFIG_KEYS.playNudgeMinSessions),
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.minSessions,
          ),
          minSeconds: this.parseInteger(
            map.get(GROWTH_PROMO_CONFIG_KEYS.playNudgeMinSeconds),
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.minSeconds,
          ),
          cooldownHours: this.parseInteger(
            map.get(GROWTH_PROMO_CONFIG_KEYS.playNudgeCooldownHours),
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.cooldownHours,
          ),
          maxImpressions30d: this.parseInteger(
            map.get(GROWTH_PROMO_CONFIG_KEYS.playNudgeMaxImpressions30d),
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.maxImpressions30d,
          ),
        },
      },
      links: {
        universalUrl: normalizeString(
          map.get(GROWTH_PROMO_CONFIG_KEYS.universalUrl),
        ) || DEFAULT_APP_PROMO_CONFIG.links.universalUrl,
        iosUrl: DEFAULT_APP_PROMO_CONFIG.links.iosUrl,
        androidUrl: DEFAULT_APP_PROMO_CONFIG.links.androidUrl,
      },
      copy,
    };
  }

  private normalizePromoConfigInput(input: any): PromoConfig {
    return {
      enabled: this.parseBoolean(input?.enabled, DEFAULT_APP_PROMO_CONFIG.enabled),
      wechatMode: normalizeString(input?.wechatMode) || DEFAULT_APP_PROMO_CONFIG.wechatMode,
      scenes: {
        createComplete: {
          enabled: this.parseBoolean(
            input?.scenes?.createComplete?.enabled,
            DEFAULT_APP_PROMO_CONFIG.scenes.createComplete.enabled,
          ),
          cooldownHours: this.parseInteger(
            input?.scenes?.createComplete?.cooldownHours,
            DEFAULT_APP_PROMO_CONFIG.scenes.createComplete.cooldownHours,
          ),
          maxImpressions30d: this.parseInteger(
            input?.scenes?.createComplete?.maxImpressions30d,
            DEFAULT_APP_PROMO_CONFIG.scenes.createComplete.maxImpressions30d,
          ),
        },
        playNudge: {
          enabled: this.parseBoolean(
            input?.scenes?.playNudge?.enabled,
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.enabled,
          ),
          minSessions: this.parseInteger(
            input?.scenes?.playNudge?.minSessions,
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.minSessions,
          ),
          minSeconds: this.parseInteger(
            input?.scenes?.playNudge?.minSeconds,
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.minSeconds,
          ),
          cooldownHours: this.parseInteger(
            input?.scenes?.playNudge?.cooldownHours,
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.cooldownHours,
          ),
          maxImpressions30d: this.parseInteger(
            input?.scenes?.playNudge?.maxImpressions30d,
            DEFAULT_APP_PROMO_CONFIG.scenes.playNudge.maxImpressions30d,
          ),
        },
      },
      links: {
        universalUrl: DEFAULT_APP_PROMO_CONFIG.links.universalUrl,
        iosUrl: DEFAULT_APP_PROMO_CONFIG.links.iosUrl,
        androidUrl: DEFAULT_APP_PROMO_CONFIG.links.androidUrl,
      },
      copy: mergePromoCopy(input?.copy, DEFAULT_APP_PROMO_COPY),
    };
  }

  private normalizePlatform(value: unknown): AppReleasePlatform {
    const raw = normalizeString(value).toLowerCase();
    if (raw === 'ios') {
      return AppReleasePlatform.ios;
    }
    if (raw === 'android') {
      return AppReleasePlatform.android;
    }
    throw new BadRequestException('Invalid release platform');
  }

  private normalizeSourceType(value: unknown, _platform: AppReleasePlatform): AppReleaseSourceType {
    const raw = normalizeString(value).toLowerCase();
    if (!raw) {
      return AppReleaseSourceType.upload;
    }
    if (raw === 'upload') {
      return AppReleaseSourceType.upload;
    }
    if (raw === 'external_url') {
      return AppReleaseSourceType.external_url;
    }
    if (raw === 'app_store') {
      return AppReleaseSourceType.app_store;
    }
    throw new BadRequestException('Invalid release source type');
  }

  private normalizeStatus(value: unknown): AppReleaseStatus {
    const raw = normalizeString(value).toLowerCase();
    if (!raw || raw === 'draft') {
      return AppReleaseStatus.draft;
    }
    if (raw === 'published') {
      return AppReleaseStatus.published;
    }
    if (raw === 'archived') {
      return AppReleaseStatus.archived;
    }
    throw new BadRequestException('Invalid release status');
  }

  private validateReleasePayload(
    payload: {
      platform: AppReleasePlatform;
      sourceType: AppReleaseSourceType;
      versionName: string;
      downloadUrl?: string | null;
    },
  ) {
    if (!payload.versionName) {
      throw new BadRequestException('versionName is required');
    }

    if (
      [AppReleaseSourceType.external_url, AppReleaseSourceType.app_store].includes(payload.sourceType)
      && !normalizeString(payload.downloadUrl)
    ) {
      throw new BadRequestException('downloadUrl is required for external or App Store releases');
    }
  }

  async getAppPromoConfig(): Promise<PromoConfig> {
    const rows = await this.getPromoConfigRows();
    return this.buildPromoConfig(rows);
  }

  async updateAppPromoConfig(input: any) {
    const normalized = this.normalizePromoConfigInput(input);
    const entries = [
      [GROWTH_PROMO_CONFIG_KEYS.enabled, String(normalized.enabled)],
      [GROWTH_PROMO_CONFIG_KEYS.wechatMode, normalized.wechatMode],
      [GROWTH_PROMO_CONFIG_KEYS.createCompleteEnabled, String(normalized.scenes.createComplete.enabled)],
      [GROWTH_PROMO_CONFIG_KEYS.createCompleteCooldownHours, String(normalized.scenes.createComplete.cooldownHours)],
      [GROWTH_PROMO_CONFIG_KEYS.createCompleteMaxImpressions30d, String(normalized.scenes.createComplete.maxImpressions30d)],
      [GROWTH_PROMO_CONFIG_KEYS.playNudgeEnabled, String(normalized.scenes.playNudge.enabled)],
      [GROWTH_PROMO_CONFIG_KEYS.playNudgeMinSessions, String(normalized.scenes.playNudge.minSessions)],
      [GROWTH_PROMO_CONFIG_KEYS.playNudgeMinSeconds, String(normalized.scenes.playNudge.minSeconds)],
      [GROWTH_PROMO_CONFIG_KEYS.playNudgeCooldownHours, String(normalized.scenes.playNudge.cooldownHours)],
      [GROWTH_PROMO_CONFIG_KEYS.playNudgeMaxImpressions30d, String(normalized.scenes.playNudge.maxImpressions30d)],
      [GROWTH_PROMO_CONFIG_KEYS.copyJson, JSON.stringify(normalized.copy)],
    ] as const;

    for (const [configKey, configValue] of entries) {
      await this.prisma.systemConfig.upsert({
        where: { configKey },
        update: {
          configValue,
          description: APP_PROMO_CONFIG_DESCRIPTIONS[configKey],
          category: GROWTH_CONFIG_CATEGORY,
        },
        create: {
          id: randomUUID(),
          configKey,
          configValue,
          description: APP_PROMO_CONFIG_DESCRIPTIONS[configKey],
          category: GROWTH_CONFIG_CATEGORY,
        },
      });
    }

    return this.getAppPromoConfig();
  }

  async getAppPromoBootstrap() {
    await this.ensureGrowthSchema();

    const [config, activeReleases] = await Promise.all([
      this.getAppPromoConfig(),
      this.appReleaseDelegate.findMany({
        where: {
          isActive: true,
          status: AppReleaseStatus.published,
        },
        orderBy: [
          { platform: 'asc' },
          { updatedAt: 'desc' },
        ],
      }),
    ]);

    const releases = activeReleases.reduce((acc: Record<string, any>, release: any) => {
      const normalizedRelease = this.serializeAppRelease(release);
      acc[release.platform] = {
        id: normalizedRelease.id,
        versionName: normalizedRelease.versionName,
        buildNumber: normalizedRelease.buildNumber,
        downloadUrl: normalizedRelease.downloadUrl,
        qrCodeUrl: normalizedRelease.qrCodeUrl,
        sourceType: normalizedRelease.sourceType,
        publishedAt: normalizedRelease.publishedAt,
      };
      return acc;
    }, {} as Record<string, any>);

    return {
      ...config,
      links: this.buildReleaseLinks(releases),
      releases,
    };
  }

  async recordPromoEvent(input: PromoEventRequest, reqMeta?: { userAgent?: string; ip?: string }) {
    await this.ensureGrowthSchema();

    const scene = normalizeString(input.scene);
    const eventType = normalizeString(input.eventType);
    const deviceId = normalizeString(input.deviceId);

    if (!scene) {
      throw new BadRequestException('scene is required');
    }
    if (!eventType) {
      throw new BadRequestException('eventType is required');
    }
    if (!deviceId) {
      throw new BadRequestException('deviceId is required');
    }

    const ipHash = normalizeString(reqMeta?.ip)
      ? createHash('sha256').update(normalizeString(reqMeta?.ip)).digest('hex')
      : null;

    return this.appPromoEventDelegate.create({
      data: {
        id: randomUUID(),
        scene,
        eventType,
        deviceId,
        gameId: normalizeString(input.gameId) || null,
        platform: normalizeString(input.platform) || null,
        channel: normalizeString(input.channel) || null,
        userAgent: normalizeString(reqMeta?.userAgent) || null,
        ipHash,
        extraJson: input.extra && typeof input.extra === 'object' ? input.extra : null,
      },
    });
  }

  async listPromoEvents(query: PromoEventQuery) {
    await this.ensureGrowthSchema();

    const page = Math.max(Number.parseInt(query.page || '1', 10), 1);
    const limit = Math.min(Math.max(Number.parseInt(query.limit || '20', 10), 1), 100);
    const where: Record<string, unknown> = {};

    if (normalizeString(query.scene)) {
      where.scene = normalizeString(query.scene);
    }
    if (normalizeString(query.eventType)) {
      where.eventType = normalizeString(query.eventType);
    }
    if (normalizeString(query.platform)) {
      where.platform = normalizeString(query.platform);
    }

    const [items, total] = await Promise.all([
      this.appPromoEventDelegate.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.appPromoEventDelegate.count({ where }),
    ]);

    return {
      items: items.map((item: any) => this.serializeAppRelease(item)),
      page,
      limit,
      total,
      totalPages: Math.max(Math.ceil(total / limit), 1),
    };
  }

  async listAppReleases(query: AppReleaseListQuery) {
    await this.ensureGrowthSchema();

    const page = Math.max(Number.parseInt(query.page || '1', 10), 1);
    const limit = Math.min(Math.max(Number.parseInt(query.limit || '20', 10), 1), 100);
    const where: Record<string, unknown> = {};

    if (normalizeString(query.platform)) {
      where.platform = this.normalizePlatform(query.platform);
    }
    if (normalizeString(query.status)) {
      where.status = this.normalizeStatus(query.status);
    }
    if (normalizeString(query.channel)) {
      where.channel = normalizeString(query.channel);
    }

    const [items, total] = await Promise.all([
      this.appReleaseDelegate.findMany({
        where,
        orderBy: [
          { isActive: 'desc' },
          { publishedAt: 'desc' },
          { createdAt: 'desc' },
        ],
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.appReleaseDelegate.count({ where }),
    ]);

    return {
      items,
      page,
      limit,
      total,
      totalPages: Math.max(Math.ceil(total / limit), 1),
    };
  }

  async getAppRelease(id: string) {
    await this.ensureGrowthSchema();

    const release = await this.appReleaseDelegate.findUnique({ where: { id } });
    if (!release) {
      throw new NotFoundException('App release not found');
    }
    return this.serializeAppRelease(release);
  }

  async createAppRelease(input: AppReleaseInput, createdBy = 'admin') {
    await this.ensureGrowthSchema();

    const platform = this.normalizePlatform(input.platform);
    const sourceType = this.normalizeSourceType(input.sourceType, platform);
    const status = this.normalizeStatus(input.status);
    const versionName = normalizeString(input.versionName);
    const downloadUrl = normalizeString(input.downloadUrl) || null;
    const shouldActivate = Boolean(input.isActive) && status === AppReleaseStatus.published;
    const releaseData = {
      id: randomUUID(),
      platform,
      channel: normalizeString(input.channel) || 'production',
      versionName,
      buildNumber: normalizeString(input.buildNumber) || null,
      releaseNotes: normalizeString(input.releaseNotes) || null,
      downloadUrl,
      qrCodeUrl: normalizeString(input.qrCodeUrl) || null,
      sourceType,
      status,
      isActive: shouldActivate,
      publishedAt: status === AppReleaseStatus.published ? new Date() : null,
      createdBy,
    };

    this.validateReleasePayload({
      platform,
      sourceType,
      versionName,
      downloadUrl,
    });

    if (sourceType === AppReleaseSourceType.upload && status === AppReleaseStatus.published) {
      throw new BadRequestException('Upload releases must stay draft until an APK package is uploaded');
    }

    if (!shouldActivate) {
      const created = await this.appReleaseDelegate.create({ data: releaseData });
      return this.serializeAppRelease(created);
    }

    const created = await this.prisma.$transaction(async (tx) => {
      await (tx as any).appRelease.updateMany({
        where: {
          platform,
          channel: releaseData.channel,
          isActive: true,
        },
        data: {
          isActive: false,
        },
      });

      return (tx as any).appRelease.create({ data: releaseData });
    });
    return this.serializeAppRelease(created);
  }

  async updateAppRelease(id: string, input: AppReleaseInput) {
    await this.ensureGrowthSchema();

    const existing = await this.appReleaseDelegate.findUnique({ where: { id } });
    if (!existing) {
      throw new NotFoundException('App release not found');
    }

    const platform = input.platform ? this.normalizePlatform(input.platform) : existing.platform;
    const sourceType = input.sourceType
      ? this.normalizeSourceType(input.sourceType, platform)
      : existing.sourceType;
    const status = input.status ? this.normalizeStatus(input.status) : existing.status;
    const versionName = normalizeString(input.versionName) || existing.versionName;
    const requestedDownloadUrl = normalizeString(input.downloadUrl);
    const downloadUrl = sourceType === AppReleaseSourceType.upload
      ? (existing.sourceType === AppReleaseSourceType.upload ? existing.downloadUrl || null : null)
      : (requestedDownloadUrl || existing.downloadUrl || null);
    const channel = normalizeString(input.channel) || existing.channel;
    const nextIsActive = status === AppReleaseStatus.published
      ? (typeof input.isActive === 'boolean' ? input.isActive : existing.isActive)
      : false;
    const uploadMetadata = sourceType === AppReleaseSourceType.upload
      ? {}
      : {
          fileName: null,
          fileSize: null,
          mimeType: null,
          storageKey: null,
          checksumSha256: null,
        };
    const updateData = {
      platform,
      channel,
      versionName,
      buildNumber: normalizeString(input.buildNumber) || existing.buildNumber,
      releaseNotes: normalizeString(input.releaseNotes) || existing.releaseNotes,
      downloadUrl,
      qrCodeUrl: normalizeString(input.qrCodeUrl) || existing.qrCodeUrl,
      sourceType,
      status,
      isActive: nextIsActive,
      publishedAt: status === AppReleaseStatus.published
        ? existing.publishedAt || new Date()
        : null,
      ...uploadMetadata,
    };

    this.validateReleasePayload({
      platform,
      sourceType,
      versionName,
      downloadUrl,
    });

    if (
      sourceType === AppReleaseSourceType.upload
      && status === AppReleaseStatus.published
      && !normalizeString(existing.storageKey)
    ) {
      throw new BadRequestException('Upload releases must receive an APK package before publishing');
    }

    if (!nextIsActive) {
      const updated = await this.appReleaseDelegate.update({
        where: { id },
        data: updateData,
      });
      return this.serializeAppRelease(updated);
    }

    const updated = await this.prisma.$transaction(async (tx) => {
      await (tx as any).appRelease.updateMany({
        where: {
          platform,
          channel,
          isActive: true,
          NOT: { id },
        },
        data: {
          isActive: false,
        },
      });

      return (tx as any).appRelease.update({
        where: { id },
        data: updateData,
      });
    });
    return this.serializeAppRelease(updated);
  }

  async publishAppRelease(id: string) {
    await this.ensureGrowthSchema();

    const existing = await this.appReleaseDelegate.findUnique({ where: { id } });
    if (!existing) {
      throw new NotFoundException('App release not found');
    }

    if (existing.sourceType === AppReleaseSourceType.upload && !existing.storageKey) {
      throw new BadRequestException('Uploaded release package is required before publishing');
    }
    if (
      [AppReleaseSourceType.external_url, AppReleaseSourceType.app_store].includes(existing.sourceType)
      && !normalizeString(existing.downloadUrl)
    ) {
      throw new BadRequestException('downloadUrl is required before publishing');
    }

    await this.prisma.$transaction(async (tx) => {
      await (tx as any).appRelease.updateMany({
        where: {
          platform: existing.platform,
          channel: existing.channel,
          isActive: true,
          NOT: { id: existing.id },
        },
        data: {
          isActive: false,
        },
      });

      await (tx as any).appRelease.update({
        where: { id: existing.id },
        data: {
          status: AppReleaseStatus.published,
          isActive: true,
          publishedAt: existing.publishedAt || new Date(),
        },
      });
    });

    const release = await this.appReleaseDelegate.findUnique({ where: { id } });
    return this.serializeAppRelease(release);
  }

  async uploadReleasePackage(
    releaseId: string,
    file: UploadedReleaseFile,
  ) {
    await this.ensureGrowthSchema();

    if (!normalizeString(releaseId)) {
      throw new BadRequestException('releaseId is required');
    }
    if (!file || !file.originalname) {
      throw new BadRequestException('APK file is required');
    }

    const release = await this.appReleaseDelegate.findUnique({ where: { id: releaseId } });
    if (!release) {
      throw new NotFoundException('App release not found');
    }
    const extension = path.extname(file.originalname || '').toLowerCase();
    const expectedExtension = release.platform === AppReleasePlatform.ios ? '.ipa' : '.apk';
    if (extension !== expectedExtension) {
      throw new BadRequestException(`Only ${expectedExtension} files are supported for ${release.platform} releases`);
    }

    const fileBuffer = file.buffer || (file.path ? fs.readFileSync(file.path) : null);
    if (!fileBuffer) {
      throw new BadRequestException('APK file buffer is missing');
    }

    const safeName = sanitizeFileName(file.originalname);
    const finalName = `${Date.now()}-${safeName}`;
    const checksumSha256 = createHash('sha256').update(fileBuffer).digest('hex');
    const downloadUrl = this.getPublicReleaseDownloadUrl(release.id);
    const contentType = normalizeString(file.mimetype)
      || (release.platform === AppReleasePlatform.ios
        ? 'application/octet-stream'
        : 'application/vnd.android.package-archive');
    const tosConfig = this.getAppReleaseTosConfig();
    let storageKey = '';

    if (this.configService.get<string>('OBJECT_STORAGE_PROVIDER') === 'aliyun-oss') {
      const objectKey = path.posix.join(this.getOssPrefix(), 'app-releases', String(release.platform), release.id, finalName);
      await this.createOssClient().put(objectKey, fileBuffer, { headers: { 'Content-Type': contentType } });
      storageKey = `oss:${objectKey}`;
    } else if (tosConfig) {
      const objectKey = this.buildReleaseObjectKey(release.platform, release.id, finalName);
      const tosClient = this.createTosClient(tosConfig);

      await tosClient.putObject({
        bucket: tosConfig.bucket,
        key: objectKey,
        body: fileBuffer,
        contentLength: fileBuffer.length,
        contentType,
      });

      storageKey = this.encodeTosStorageKey(objectKey);
    } else {
      if (this.configService.get<string>('FC_DEPLOYMENT') === 'true') {
        throw new BadRequestException('Persistent object storage is required on FC');
      }
      this.logger.warn(
        `TOS config is incomplete. Falling back to local APK storage for release ${release.id}.`,
      );

      const uploadDir = this.getAppReleaseUploadDir();
      const relativeDir = path.posix.join(String(release.platform), release.id);
      const absoluteDir = path.join(uploadDir, String(release.platform), release.id);
      fs.mkdirSync(absoluteDir, { recursive: true });

      const absolutePath = path.join(absoluteDir, finalName);
      fs.writeFileSync(absolutePath, fileBuffer);
      storageKey = this.encodeLocalStorageKey(path.posix.join(relativeDir, finalName));
    }

    const updated = await this.appReleaseDelegate.update({
      where: { id: release.id },
      data: {
        sourceType: AppReleaseSourceType.upload,
        fileName: file.originalname,
        fileSize: BigInt(file.size || fileBuffer.length),
        mimeType: contentType,
        storageKey,
        checksumSha256,
        downloadUrl,
      },
    });
    return this.serializeAppRelease(updated);
  }

  async resolveReleaseDownload(id: string) {
    await this.ensureGrowthSchema();

    const release = await this.appReleaseDelegate.findUnique({ where: { id } });
    if (!release) {
      throw new NotFoundException('App release not found');
    }

    if (release.sourceType !== AppReleaseSourceType.upload) {
      if (!normalizeString(release.downloadUrl)) {
        throw new NotFoundException('Release download URL is not configured');
      }
      return {
        type: 'redirect' as const,
        release,
        downloadUrl: release.downloadUrl,
      };
    }

    if (!normalizeString(release.storageKey)) {
      throw new NotFoundException('Release package is not available');
    }

    const storedObject = this.decodeStorageKey(release.storageKey);

    if (storedObject.provider === 'oss') {
      if (!storedObject.objectKey.startsWith(this.getOssPrefix() + '/app-releases/') || storedObject.objectKey.split('/').some((part) => part === '..' || part === '.') || /[\\\x00-\x1f]/.test(storedObject.objectKey)) {
        throw new BadRequestException('Release object is outside the application prefix');
      }
      const downloadUrl = this.createOssClient().signatureUrl(storedObject.objectKey, {
        expires: 600,
        response: {
          'content-disposition': `attachment; filename="${encodeURIComponent(release.fileName || 'download')}"`,
          'content-type': 'application/octet-stream',
        },
      });
      return { type: 'redirect' as const, release, downloadUrl };
    }

    if (storedObject.provider === 'tos') {
      const tosConfig = this.getAppReleaseTosConfig();
      if (!tosConfig) {
        throw new NotFoundException('TOS config is unavailable for this release package');
      }

      const downloadUrl = this.createTosClient(tosConfig).getPreSignedUrl({
        bucket: tosConfig.bucket,
        key: storedObject.objectKey,
        method: 'GET',
        expires: tosConfig.signedUrlExpires,
        response: release.fileName ? {
          contentDisposition: `attachment; filename="${encodeURIComponent(release.fileName)}"`,
          contentType: release.mimeType || undefined,
        } : undefined,
      });

      return {
        type: 'redirect' as const,
        release,
        downloadUrl,
      };
    }

    const absolutePath = path.join(this.getAppReleaseUploadDir(), storedObject.objectKey);
    if (!fs.existsSync(absolutePath)) {
      throw new NotFoundException('Release package file is missing');
    }

    return {
      type: 'file' as const,
      release,
      absolutePath,
    };
  }
}

