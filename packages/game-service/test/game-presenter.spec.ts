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

  it('falls back to the local cover endpoint when thumbnailUrl is missing', () => {
    const presented = presentGame({
      id: 'game-cover-fallback',
      title: 'Fallback Cover',
      status: 'draft',
      previewUrl: 'https://gamevallies.com/games/game-cover-fallback/preview?previewToken=token-456',
      thumbnailUrl: null,
    });

    expect(presented.coverUrl).toBe(
      'https://gamevallies.com/api/v1/games/game-cover-fallback/cover?previewToken=token-456',
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

  // H.5.1
  it('exposes the clean userIdea instead of the raw description when present', () => {
    const presented = presentGame({
      id: 'game-h511',
      title: 'H.5.1',
      userIdea: '一只猫在跳房子',
      description:
        '一只猫在跳房子\nGame Type: casual\nCore Mechanic: tap to jump\nWin Condition: reach 10',
      status: 'published',
      previewUrl: 'https://gamevallies.com/games/game-h511/preview',
    });

    expect(presented.description).toBe('一只猫在跳房子');
    expect(presented.description).not.toMatch(/Game Type/);
    expect(presented.description).not.toMatch(/Core Mechanic/);
  });

  it('falls back to a sanitized description when userIdea is missing (legacy rows)', () => {
    const presented = presentGame({
      id: 'game-legacy',
      title: 'Legacy',
      userIdea: null,
      description:
        '一只猫在跳房子\n请把这条想法整理成 GameSpec\nGame Type: casual',
      status: 'published',
      previewUrl: 'https://gamevallies.com/games/game-legacy/preview',
    });

    expect(presented.description).toBe('一只猫在跳房子');
  });

  // H.7.1
  it('hides openid-derived author handles behind an anonymous label', () => {
    const presented = presentGame({
      id: 'game-h711',
      title: 'H.7.1',
      status: 'published',
      previewUrl: 'https://gamevallies.com/games/game-h711/preview',
      author: {
        id: '1234abcd-aaaa-bbbb-cccc-deadbeef0001',
        username: 'wx_oabcdef123',
        displayName: 'wx_oabcdef123',
        avatarUrl: '',
      },
    });

    expect(presented.author?.displayName).toBe('匿名玩家_1234');
    expect(presented.author?.username).toBe('匿名玩家_1234');
  });

  it('keeps a real displayName intact', () => {
    const presented = presentGame({
      id: 'game-realname',
      title: 'Real',
      status: 'published',
      previewUrl: 'https://gamevallies.com/games/game-realname/preview',
      author: {
        id: 'ffffaaaa-1111-2222-3333-444455556666',
        username: 'wx_oabcdef123',
        displayName: '小明',
        avatarUrl: '',
      },
    });

    expect(presented.author?.displayName).toBe('小明');
    expect(presented.author?.username).toBe('小明');
  });
});
