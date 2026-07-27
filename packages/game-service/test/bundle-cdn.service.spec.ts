import { ConfigService } from '@nestjs/config';
import { BundleCdnService } from '../src/bundle-cdn/bundle-cdn.service';

const ENABLED_ENV: Record<string, string> = {
  BUNDLE_CDN_ENABLED: 'true',
  VOLCENGINE_ACCESS_KEY: 'test-ak',
  VOLCENGINE_SECRET_KEY: 'test-sk',
  VOLCENGINE_REGION: 'cn-shanghai',
  BUNDLE_CDN_TOS_BUCKET: 'bundle-bucket',
  BUNDLE_CDN_PUBLIC_BASE_URL: 'https://cdn.gamevallies.com',
};

function createConfigService(values: Record<string, string>): ConfigService {
  return {
    get: jest.fn((key: string, defaultValue?: string) => values[key] ?? defaultValue),
  } as unknown as ConfigService;
}

describe('BundleCdnService', () => {
  let prisma: any;
  let bundleStorage: any;
  let putObject: jest.Mock;

  beforeEach(() => {
    prisma = {
      game: { findUnique: jest.fn() },
      gameBundle: { findFirst: jest.fn() },
    };
    bundleStorage = {
      getBundleByGameId: jest.fn(),
      updateBundleMetadata: jest.fn().mockResolvedValue({}),
    };
    putObject = jest.fn().mockResolvedValue({});
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  function createService(env: Record<string, string> = ENABLED_ENV): BundleCdnService {
    const service = new BundleCdnService(prisma, createConfigService(env), bundleStorage);
    service.tosClientFactory = jest.fn(() => ({ putObject }));
    return service;
  }

  describe('syncBundleToCdn', () => {
    it('does nothing when the switch is off (default)', async () => {
      const service = createService({ ...ENABLED_ENV, BUNDLE_CDN_ENABLED: '' });

      const uploaded = await service.syncBundleToCdn('game-1');

      expect(uploaded).toBe(false);
      expect(prisma.game.findUnique).not.toHaveBeenCalled();
      expect(putObject).not.toHaveBeenCalled();
      expect(bundleStorage.updateBundleMetadata).not.toHaveBeenCalled();
    });

    it('uploads the bundle and merges cdnUrl into metadata without dropping existing keys', async () => {
      const service = createService();
      prisma.game.findUnique.mockResolvedValue({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      });
      bundleStorage.getBundleByGameId.mockResolvedValue({
        gameId: 'game-1',
        version: 3,
        htmlCode: '<html><body>hi</body></html>',
        metadata: { coverUrl: 'https://api.gamevallies.com/api/v1/games/game-1/cover', qaPassed: true },
      });

      const uploaded = await service.syncBundleToCdn('game-1');

      expect(uploaded).toBe(true);
      expect(putObject).toHaveBeenCalledWith(expect.objectContaining({
        bucket: 'bundle-bucket',
        key: 'game-bundles/game-1/3/index.html',
        contentType: 'text/html; charset=utf-8',
      }));
      expect(bundleStorage.updateBundleMetadata).toHaveBeenCalledWith(
        'game-1',
        3,
        expect.objectContaining({
          coverUrl: 'https://api.gamevallies.com/api/v1/games/game-1/cover',
          qaPassed: true,
          cdnUrl: 'https://cdn.gamevallies.com/game-bundles/game-1/3/index.html',
          cdnUploadedAt: expect.any(String),
        }),
      );
    });

    it('uploads a specific version when requested', async () => {
      const service = createService();
      prisma.game.findUnique.mockResolvedValue({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      });
      bundleStorage.getBundleByGameId.mockResolvedValue({
        gameId: 'game-1',
        version: 5,
        htmlCode: '<html>v5</html>',
        metadata: {},
      });

      await service.syncBundleToCdn('game-1', 5);

      expect(bundleStorage.getBundleByGameId).toHaveBeenCalledWith('game-1', 5);
      expect(putObject).toHaveBeenCalledWith(expect.objectContaining({
        key: 'game-bundles/game-1/5/index.html',
      }));
    });

    it('skips upload for games that are not published + public', async () => {
      const service = createService();
      prisma.game.findUnique.mockResolvedValue({
        id: 'game-1',
        status: 'published',
        visibility: 'private',
      });

      const uploaded = await service.syncBundleToCdn('game-1');

      expect(uploaded).toBe(false);
      expect(bundleStorage.getBundleByGameId).not.toHaveBeenCalled();
      expect(putObject).not.toHaveBeenCalled();
    });

    it('never throws when the TOS upload fails and leaves metadata untouched', async () => {
      const service = createService();
      prisma.game.findUnique.mockResolvedValue({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      });
      bundleStorage.getBundleByGameId.mockResolvedValue({
        gameId: 'game-1',
        version: 2,
        htmlCode: '<html>boom</html>',
        metadata: {},
      });
      putObject.mockRejectedValue(new Error('tos unavailable'));

      await expect(service.syncBundleToCdn('game-1')).resolves.toBe(false);
      expect(bundleStorage.updateBundleMetadata).not.toHaveBeenCalled();
    });

    it('never throws when metadata update fails after upload', async () => {
      const service = createService();
      prisma.game.findUnique.mockResolvedValue({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      });
      bundleStorage.getBundleByGameId.mockResolvedValue({
        gameId: 'game-1',
        version: 2,
        htmlCode: '<html>meta boom</html>',
        metadata: {},
      });
      bundleStorage.updateBundleMetadata.mockRejectedValue(new Error('db down'));

      await expect(service.syncBundleToCdn('game-1')).resolves.toBe(false);
    });

    it('warns and skips when enabled but TOS config is incomplete', async () => {
      const service = createService({ BUNDLE_CDN_ENABLED: 'true' });

      const uploaded = await service.syncBundleToCdn('game-1');

      expect(uploaded).toBe(false);
      expect(prisma.game.findUnique).not.toHaveBeenCalled();
      expect(putObject).not.toHaveBeenCalled();
    });
  });

  describe('scheduleBundleSync', () => {
    it('does not even start a sync when the switch is off', () => {
      const service = createService({ ...ENABLED_ENV, BUNDLE_CDN_ENABLED: 'false' });
      const syncSpy = jest.spyOn(service, 'syncBundleToCdn');

      service.scheduleBundleSync('game-1', 2);

      expect(syncSpy).not.toHaveBeenCalled();
    });

    it('fires the sync in the background when enabled', async () => {
      const service = createService();
      const syncSpy = jest.spyOn(service, 'syncBundleToCdn').mockResolvedValue(true);

      service.scheduleBundleSync('game-1', 2);
      await Promise.resolve();

      expect(syncSpy).toHaveBeenCalledWith('game-1', 2);
    });
  });

  describe('resolvePublicCdnUrl / buildCdnUrlPatch', () => {
    it('returns the cdnUrl for a published public game whose latest bundle has one', async () => {
      const service = createService();
      prisma.gameBundle.findFirst.mockResolvedValue({
        metadata: { cdnUrl: 'https://cdn.gamevallies.com/game-bundles/game-1/3/index.html' },
      });

      const url = await service.resolvePublicCdnUrl({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      });

      expect(url).toBe('https://cdn.gamevallies.com/game-bundles/game-1/3/index.html');
      expect(prisma.gameBundle.findFirst).toHaveBeenCalledWith(expect.objectContaining({
        where: { gameId: 'game-1' },
        orderBy: { version: 'desc' },
      }));
    });

    it('returns null for private, draft and banned games without querying bundles', async () => {
      const service = createService();

      await expect(service.resolvePublicCdnUrl({
        id: 'game-1',
        status: 'published',
        visibility: 'private',
      })).resolves.toBeNull();
      await expect(service.resolvePublicCdnUrl({
        id: 'game-1',
        status: 'draft',
        visibility: 'public',
      })).resolves.toBeNull();
      await expect(service.resolvePublicCdnUrl({
        id: 'game-1',
        status: 'banned',
        visibility: 'public',
      })).resolves.toBeNull();

      expect(prisma.gameBundle.findFirst).not.toHaveBeenCalled();
    });

    it('returns null when the latest bundle has no cdnUrl in metadata', async () => {
      const service = createService();
      prisma.gameBundle.findFirst.mockResolvedValue({ metadata: { qaPassed: true } });

      await expect(service.resolvePublicCdnUrl({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      })).resolves.toBeNull();
    });

    it('returns null when the switch is off', async () => {
      const service = createService({ ...ENABLED_ENV, BUNDLE_CDN_ENABLED: 'false' });

      await expect(service.resolvePublicCdnUrl({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      })).resolves.toBeNull();
      expect(prisma.gameBundle.findFirst).not.toHaveBeenCalled();
    });

    it('never throws when the bundle lookup fails', async () => {
      const service = createService();
      prisma.gameBundle.findFirst.mockRejectedValue(new Error('db down'));

      await expect(service.resolvePublicCdnUrl({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      })).resolves.toBeNull();
    });

    it('buildCdnUrlPatch returns {} when no cdn url applies and { cdnUrl } when it does', async () => {
      const service = createService();
      prisma.gameBundle.findFirst.mockResolvedValue({
        metadata: { cdnUrl: 'https://cdn.gamevallies.com/game-bundles/game-1/3/index.html' },
      });

      await expect(service.buildCdnUrlPatch({
        id: 'game-1',
        status: 'draft',
        visibility: 'public',
      })).resolves.toEqual({});
      await expect(service.buildCdnUrlPatch({
        id: 'game-1',
        status: 'published',
        visibility: 'public',
      })).resolves.toEqual({
        cdnUrl: 'https://cdn.gamevallies.com/game-bundles/game-1/3/index.html',
      });
    });
  });
});
