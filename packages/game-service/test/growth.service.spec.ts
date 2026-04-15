import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { BadRequestException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { GrowthService } from '../src/growth/growth.service';

describe('GrowthService', () => {
  let service: GrowthService;
  let prisma: any;
  let configService: ConfigService;
  let uploadDir: string;

  beforeEach(() => {
    uploadDir = fs.mkdtempSync(path.join(os.tmpdir(), 'growth-service-'));

    prisma = {
      $executeRawUnsafe: jest.fn(),
      $transaction: jest.fn(),
      systemConfig: {
        findMany: jest.fn(),
        upsert: jest.fn(),
      },
      appRelease: {
        findMany: jest.fn(),
        count: jest.fn(),
        findUnique: jest.fn(),
        create: jest.fn(),
        update: jest.fn(),
        updateMany: jest.fn(),
      },
      appPromoEvent: {
        create: jest.fn(),
        findMany: jest.fn(),
        count: jest.fn(),
      },
    };
    prisma.$executeRawUnsafe.mockResolvedValue(undefined);
    prisma.$transaction.mockImplementation(async (callback: (tx: any) => any) => callback(prisma));

    configService = {
      get: jest.fn((key: string, defaultValue?: string) => {
        const values: Record<string, string> = {
          APP_URL: 'https://gamevallies.com',
          PUBLIC_API_BASE_URL: 'https://api.gamevallies.com',
          APP_RELEASE_UPLOAD_DIR: uploadDir,
        };
        return values[key] ?? defaultValue;
      }),
    } as unknown as ConfigService;

    service = new GrowthService(prisma, configService);
  });

  afterEach(() => {
    fs.rmSync(uploadDir, { recursive: true, force: true });
    jest.clearAllMocks();
  });

  it('builds promo bootstrap from configs and active releases', async () => {
    prisma.systemConfig.findMany.mockResolvedValue([
      { configKey: 'growth.app_promo.enabled', configValue: 'false' },
      { configKey: 'growth.app_promo.play_nudge_min_sessions', configValue: '5' },
      {
        configKey: 'growth.app_promo.copy_json',
        configValue: JSON.stringify({
          playNudge: {
            title: '继续玩就去 APP',
          },
        }),
      },
    ]);
    prisma.appRelease.findMany.mockResolvedValue([
      {
        id: 'ios-1',
        platform: 'ios',
        versionName: '1.2.3',
        buildNumber: '123',
        downloadUrl: 'https://apps.apple.com/app/id123',
        qrCodeUrl: null,
        sourceType: 'app_store',
        publishedAt: new Date('2026-04-13T10:00:00.000Z'),
      },
      {
        id: 'android-1',
        platform: 'android',
        versionName: '2.0.0',
        buildNumber: '200',
        downloadUrl: 'https://cdn.gamevallies.com/app.apk',
        qrCodeUrl: 'https://cdn.gamevallies.com/app-qr.png',
        sourceType: 'upload',
        publishedAt: new Date('2026-04-13T11:00:00.000Z'),
      },
    ]);

    const result = await service.getAppPromoBootstrap();

    expect(result.enabled).toBe(false);
    expect(result.scenes.playNudge.minSessions).toBe(5);
    expect(result.copy.playNudge.title).toBe('继续玩就去 APP');
    expect(result.copy.playNudge.primaryCta).toBeTruthy();
    expect(result.releases.ios).toEqual(expect.objectContaining({
      id: 'ios-1',
      versionName: '1.2.3',
      sourceType: 'app_store',
    }));
    expect(result.releases.android).toEqual(expect.objectContaining({
      id: 'android-1',
      qrCodeUrl: 'https://cdn.gamevallies.com/app-qr.png',
      sourceType: 'upload',
    }));
  });

  it('upserts promo config rows under the growth category', async () => {
    prisma.systemConfig.findMany.mockResolvedValue([
      { configKey: 'growth.app_promo.enabled', configValue: 'false' },
      { configKey: 'growth.app_promo.play_nudge_min_sessions', configValue: '4' },
      { configKey: 'growth.app_promo.universal_url', configValue: 'https://app.gamevallies.com/open' },
      {
        configKey: 'growth.app_promo.copy_json',
        configValue: JSON.stringify({
          playNudge: {
            title: '去 APP 继续玩',
          },
        }),
      },
    ]);
    prisma.systemConfig.upsert.mockResolvedValue({});

    const result = await service.updateAppPromoConfig({
      enabled: false,
      scenes: {
        playNudge: {
          minSessions: 4,
        },
      },
      links: {
        universalUrl: 'https://app.gamevallies.com/open',
      },
      copy: {
        playNudge: {
          title: '去 APP 继续玩',
        },
      },
    });

    expect(prisma.systemConfig.upsert).toHaveBeenCalledWith(expect.objectContaining({
      where: { configKey: 'growth.app_promo.enabled' },
      update: expect.objectContaining({
        configValue: 'false',
        category: 'growth',
      }),
    }));
    expect(prisma.systemConfig.upsert).toHaveBeenCalledWith(expect.objectContaining({
      where: { configKey: 'growth.app_promo.play_nudge_min_sessions' },
      update: expect.objectContaining({
        configValue: '4',
      }),
    }));
    expect(result.links.universalUrl).toBe('https://app.gamevallies.com/open');
    expect(result.copy.playNudge.title).toBe('去 APP 继续玩');
    expect(result.copy.playNudge.secondaryCta).toBeTruthy();
  });

  it('rejects iOS upload releases in phase 1', async () => {
    await expect(service.createAppRelease({
      platform: 'ios',
      sourceType: 'upload',
      versionName: '1.0.0',
    })).rejects.toBeInstanceOf(BadRequestException);
  });

  it('requires android upload releases to remain draft until the apk is uploaded', async () => {
    await expect(service.createAppRelease({
      platform: 'android',
      sourceType: 'upload',
      versionName: '1.0.0',
      status: 'published',
    })).rejects.toBeInstanceOf(BadRequestException);
  });

  it('publishes one release and deactivates siblings in the same channel', async () => {
    prisma.appRelease.findUnique
      .mockResolvedValueOnce({
        id: 'release-1',
        platform: 'android',
        channel: 'production',
        sourceType: 'external_url',
        downloadUrl: 'https://cdn.gamevallies.com/app.apk',
        publishedAt: null,
      })
      .mockResolvedValueOnce({
        id: 'release-1',
        platform: 'android',
        channel: 'production',
        status: 'published',
        isActive: true,
      });
    prisma.appRelease.update.mockResolvedValue({
      id: 'release-1',
      status: 'published',
      isActive: true,
    });
    prisma.appRelease.updateMany.mockResolvedValue({ count: 1 });

    const result = await service.publishAppRelease('release-1');

    expect(prisma.appRelease.updateMany).toHaveBeenCalledWith({
      where: {
        platform: 'android',
        channel: 'production',
        isActive: true,
        NOT: { id: 'release-1' },
      },
      data: {
        isActive: false,
      },
    });
    expect(prisma.appRelease.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'release-1' },
      data: expect.objectContaining({
        status: 'published',
        isActive: true,
      }),
    }));
    expect(result).toEqual(expect.objectContaining({
      id: 'release-1',
      isActive: true,
    }));
  });

  it('stores uploaded android packages and updates the release metadata', async () => {
    prisma.appRelease.findUnique.mockResolvedValue({
      id: 'release-android',
      platform: 'android',
    });
    prisma.appRelease.update.mockImplementation(async ({ data }: any) => ({
      id: 'release-android',
      ...data,
    }));

    const result = await service.uploadAndroidReleasePackage('release-android', {
      originalname: 'GameVallies.apk',
      mimetype: 'application/vnd.android.package-archive',
      size: 8,
      buffer: Buffer.from('apk-data'),
    });

    expect(prisma.appRelease.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'release-android' },
      data: expect.objectContaining({
        sourceType: 'upload',
        fileName: 'GameVallies.apk',
        downloadUrl: 'https://api.gamevallies.com/api/v1/growth/app-releases/release-android/download',
      }),
    }));

    const updatePayload = prisma.appRelease.update.mock.calls[0][0].data;
    const relativeStorageKey = String(updatePayload.storageKey).replace(/^local:/, '');
    const absolutePath = path.join(uploadDir, relativeStorageKey);

    expect(updatePayload.storageKey).toMatch(/^local:android\/release-android\/\d+-GameVallies\.apk$/);
    expect(fs.existsSync(absolutePath)).toBe(true);
    expect(fs.readFileSync(absolutePath)).toEqual(Buffer.from('apk-data'));
    expect(result.downloadUrl).toBe('https://api.gamevallies.com/api/v1/growth/app-releases/release-android/download');
  });

  it('blocks publishing upload releases without an uploaded package during update', async () => {
    prisma.appRelease.findUnique.mockResolvedValue({
      id: 'release-android',
      platform: 'android',
      channel: 'production',
      sourceType: 'upload',
      versionName: '1.0.0',
      status: 'draft',
      isActive: false,
      storageKey: null,
      downloadUrl: null,
      buildNumber: null,
      releaseNotes: null,
      qrCodeUrl: null,
      publishedAt: null,
    });

    await expect(service.updateAppRelease('release-android', {
      status: 'published',
      isActive: true,
    })).rejects.toBeInstanceOf(BadRequestException);
  });

  it('uploads android packages to tos when tos config is available', async () => {
    prisma.appRelease.findUnique.mockResolvedValue({
      id: 'release-android',
      platform: 'android',
    });
    prisma.appRelease.update.mockImplementation(async ({ data }: any) => ({
      id: 'release-android',
      ...data,
    }));

    const putObject = jest.fn().mockResolvedValue({});
    jest.spyOn(service as any, 'getAppReleaseTosConfig').mockReturnValue({
      accessKeyId: 'ak',
      accessKeySecret: 'sk',
      region: 'cn-shanghai',
      bucket: 'gamevallies',
      endpoint: 'https://tos-cn-shanghai.volces.com',
      keyPrefix: 'app-releases',
      signedUrlExpires: 600,
    });
    jest.spyOn(service as any, 'createTosClient').mockReturnValue({
      putObject,
    });

    const result = await service.uploadAndroidReleasePackage('release-android', {
      originalname: 'GameVallies.apk',
      mimetype: 'application/vnd.android.package-archive',
      buffer: Buffer.from('apk-data'),
    });

    expect(putObject).toHaveBeenCalledWith(expect.objectContaining({
      bucket: 'gamevallies',
      key: expect.stringMatching(/^app-releases\/android\/release-android\/\d+-GameVallies\.apk$/),
      contentType: 'application/vnd.android.package-archive',
    }));
    expect(prisma.appRelease.update).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({
        storageKey: expect.stringMatching(/^tos:app-releases\/android\/release-android\/\d+-GameVallies\.apk$/),
      }),
    }));
    expect(result.downloadUrl).toBe('https://api.gamevallies.com/api/v1/growth/app-releases/release-android/download');
  });

  it('resolves tos uploads to signed download urls', async () => {
    prisma.appRelease.findUnique.mockResolvedValue({
      id: 'release-android',
      sourceType: 'upload',
      storageKey: 'tos:app-releases/android/release-android/123-GameVallies.apk',
      fileName: 'GameVallies.apk',
      mimeType: 'application/vnd.android.package-archive',
    });

    const getPreSignedUrl = jest.fn().mockReturnValue('https://signed.gamevallies.com/app.apk');
    jest.spyOn(service as any, 'getAppReleaseTosConfig').mockReturnValue({
      accessKeyId: 'ak',
      accessKeySecret: 'sk',
      region: 'cn-shanghai',
      bucket: 'gamevallies',
      endpoint: 'https://tos-cn-shanghai.volces.com',
      keyPrefix: 'app-releases',
      signedUrlExpires: 600,
    });
    jest.spyOn(service as any, 'createTosClient').mockReturnValue({
      getPreSignedUrl,
    });

    const result = await service.resolveReleaseDownload('release-android');

    expect(getPreSignedUrl).toHaveBeenCalledWith(expect.objectContaining({
      bucket: 'gamevallies',
      key: 'app-releases/android/release-android/123-GameVallies.apk',
      method: 'GET',
      expires: 600,
    }));
    expect(result).toEqual(expect.objectContaining({
      type: 'redirect',
      downloadUrl: 'https://signed.gamevallies.com/app.apk',
    }));
  });
});
