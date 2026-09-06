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
        storageKey: 'tos:app-releases/android/android-1/app.apk',
        sourceType: 'upload',
        publishedAt: new Date('2026-04-13T11:00:00.000Z'),
      },
    ]);

    const result = await service.getAppPromoBootstrap();

    expect(result.enabled).toBe(false);
    expect(result.scenes.playNudge.minSessions).toBe(5);
    expect(result.copy.playNudge.title).toBe('继续玩就去 APP');
    expect(result.copy.playNudge.primaryCta).toBeTruthy();
    expect(result.links).toEqual(expect.objectContaining({
      iosUrl: 'https://apps.apple.com/app/id123',
      androidUrl: 'https://api.gamevallies.com/api/v1/growth/app-releases/android-1/download',
      universalUrl: 'https://apps.apple.com/app/id123',
    }));
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
    expect(result.links.universalUrl).toBe('');
    expect(result.copy.playNudge.title).toBe('去 APP 继续玩');
    expect(result.copy.playNudge.secondaryCta).toBeTruthy();
  });

  it('defaults new releases to upload sourcing when sourceType is omitted', async () => {
    prisma.appRelease.create.mockImplementation(async ({ data }: any) => ({
      id: 'release-ios',
      ...data,
    }));

    const result = await service.createAppRelease({
      platform: 'ios',
      versionName: '1.0.0',
    });

    expect(prisma.appRelease.create).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({
        platform: 'ios',
        sourceType: 'upload',
      }),
    }));
    expect(result).toEqual(expect.objectContaining({
      platform: 'ios',
      sourceType: 'upload',
      downloadUrl: null,
    }));
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

    const result = await service.uploadReleasePackage('release-android', {
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

  it('persists OSS uploads and signs private downloads without writing local files', async () => {
    const originalGet = (configService.get as jest.Mock).getMockImplementation()!;
    (configService.get as jest.Mock).mockImplementation((key: string) => ({
      OBJECT_STORAGE_PROVIDER: 'aliyun-oss', ALIYUN_OSS_PREFIX: 'gamevallies/prod/',
    }[key] ?? originalGet(key)));
    const put = jest.fn().mockResolvedValue({});
    const signatureUrl = jest.fn().mockReturnValue('https://bucket.example.com/signed-download');
    jest.spyOn(service as any, 'createOssClient').mockReturnValue({ put, signatureUrl });
    prisma.appRelease.findUnique.mockResolvedValue({ id: 'release-android', platform: 'android' });
    prisma.appRelease.update.mockImplementation(async ({ data }: any) => ({ id: 'release-android', ...data }));
    await service.uploadReleasePackage('release-android', { originalname: 'creative.apk', buffer: Buffer.from('package') });
    const saved = prisma.appRelease.update.mock.calls[0][0].data;
    expect(saved.storageKey).toMatch(/^oss:gamevallies\/prod\/app-releases\/android\/release-android\//);
    expect(put).toHaveBeenCalledTimes(1);
    expect(fs.readdirSync(uploadDir)).toEqual([]);
    prisma.appRelease.findUnique.mockResolvedValue({ id: 'release-android', ...saved });
    const download = await service.resolveReleaseDownload('release-android');
    expect(download.type).toBe('redirect');
    expect(signatureUrl).toHaveBeenCalledWith(saved.storageKey.slice(4), expect.objectContaining({ expires: 600 }));
  });

  it('never falls back to ephemeral FC storage when OSS credentials are missing', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string) => ({
      OBJECT_STORAGE_PROVIDER: 'aliyun-oss', ALIYUN_OSS_PREFIX: 'gamevallies/prod/', FC_DEPLOYMENT: 'true',
    }[key]));
    prisma.appRelease.findUnique.mockResolvedValue({ id: 'release-android', platform: 'android' });
    await expect(service.uploadReleasePackage('release-android', { originalname: 'creative.apk', buffer: Buffer.from('package') })).rejects.toBeInstanceOf(BadRequestException);
    expect(prisma.appRelease.update).not.toHaveBeenCalled();
    expect(fs.readdirSync(uploadDir)).toEqual([]);
  });

  it('rejects OSS download references outside the shared bucket application prefix', async () => {
    (configService.get as jest.Mock).mockImplementation((key: string) => key === 'ALIYUN_OSS_PREFIX' ? 'gamevallies/prod/' : undefined);
    prisma.appRelease.findUnique.mockResolvedValue({ sourceType: 'upload', storageKey: 'oss:clawworks/private/file.apk' });
    await expect(service.resolveReleaseDownload('release')).rejects.toBeInstanceOf(BadRequestException);
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

    const result = await service.uploadReleasePackage('release-android', {
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

  it('stores uploaded ios packages and returns the backend download url', async () => {
    prisma.appRelease.findUnique.mockResolvedValue({
      id: 'release-ios',
      platform: 'ios',
    });
    prisma.appRelease.update.mockImplementation(async ({ data }: any) => ({
      id: 'release-ios',
      ...data,
    }));

    const result = await service.uploadReleasePackage('release-ios', {
      originalname: 'GameVallies.ipa',
      mimetype: 'application/octet-stream',
      buffer: Buffer.from('ipa-data'),
    });

    expect(prisma.appRelease.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'release-ios' },
      data: expect.objectContaining({
        sourceType: 'upload',
        fileName: 'GameVallies.ipa',
        downloadUrl: 'https://api.gamevallies.com/api/v1/growth/app-releases/release-ios/download',
      }),
    }));
    expect(result.downloadUrl).toBe('https://api.gamevallies.com/api/v1/growth/app-releases/release-ios/download');
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

