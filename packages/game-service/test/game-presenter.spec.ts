import { presentGame } from '../src/common/game-presenter';

describe('presentGame', () => {
  const originalPublicApiBaseUrl = process.env.PUBLIC_API_BASE_URL;
  const originalAppUrl = process.env.APP_URL;

  beforeEach(() => {
    process.env.PUBLIC_API_BASE_URL = 'https://gamevallies.com';
    process.env.APP_URL = 'https://gamevallies.com';
  });

  afterAll(() => {
    process.env.PUBLIC_API_BASE_URL = originalPublicApiBaseUrl;
    process.env.APP_URL = originalAppUrl;
  });

  it('preserves external absolute thumbnail urls', () => {
    const presented = presentGame({
      id: 'game-cdn-cover',
      title: 'CDN Cover',
      status: 'published',
      previewUrl: 'https://gamevallies.com/games/game-cdn-cover/preview',
      thumbnailUrl: 'https://cdn.example.com/covers/game-cdn-cover.webp?sig=123',
    });

    expect(presented.coverUrl).toBe('https://cdn.example.com/covers/game-cdn-cover.webp?sig=123');
  });

  it('rewrites local cover urls to the API endpoint and propagates previewToken', () => {
    const presented = presentGame({
      id: 'game-local-cover',
      title: 'Local Cover',
      status: 'draft',
      previewUrl: 'https://gamevallies.com/games/game-local-cover/preview?previewToken=token-123',
      thumbnailUrl: 'https://old-host.test/games/game-local-cover/cover?taskId=task-local&v=2',
    });

    expect(presented.coverUrl).toBe(
      'https://gamevallies.com/api/v1/games/game-local-cover/cover?taskId=task-local&v=2&previewToken=token-123',
    );
  });

  it('normalizes raw game types into the curated 4-category catalog', () => {
    const presented = presentGame({
      id: 'game-runner',
      title: 'Runner',
      description: 'a simple endless runner',
      gameType: 'runner',
      status: 'published',
      previewUrl: 'https://gamevallies.com/games/game-runner/preview',
    });

    expect(presented.type).toBe('casual');
  });
});
