import { pickPublicDisplayName, presentUser } from '../src/common/api-response';

describe('pickPublicDisplayName (H.7.1)', () => {
  it('keeps a real displayName intact', () => {
    expect(
      pickPublicDisplayName({
        id: '1234abcd-1111-2222-3333-444455556666',
        displayName: '小明',
        username: 'wx_oabcdef123',
      }),
    ).toBe('小明');
  });

  it('falls back to username when displayName is missing and username is clean', () => {
    expect(
      pickPublicDisplayName({
        id: '1234abcd-1111-2222-3333-444455556666',
        displayName: null,
        username: 'happy_player',
      }),
    ).toBe('happy_player');
  });

  it('falls back to anonymous handle when displayName looks like an openid', () => {
    expect(
      pickPublicDisplayName({
        id: '1234abcd-1111-2222-3333-444455556666',
        displayName: 'wx_oabcdef123',
        username: 'wx_oabcdef123',
      }),
    ).toBe('匿名玩家_1234');
  });

  it('falls back to anonymous handle when both fields are missing', () => {
    expect(
      pickPublicDisplayName({
        id: 'deadbeef-aaaa-bbbb-cccc-000011112222',
      }),
    ).toBe('匿名玩家_dead');
  });
});

describe('presentUser (H.7.1)', () => {
  it('replaces openid-derived displayName with the anonymous handle', () => {
    expect(
      presentUser({
        id: '1234abcd-1111-2222-3333-444455556666',
        username: 'wx_oabcdef123',
        displayName: 'wx_oabcdef123',
        avatarUrl: '',
      }),
    ).toMatchObject({
      displayName: '匿名玩家_1234',
      username: 'wx_oabcdef123',
    });
  });
});
