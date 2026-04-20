export function ok<T>(data: T, message = 'success') {
  return {
    code: 0,
    message,
    data,
  };
}

export function okAlt<T>(data: T) {
  return {
    success: true,
    data,
  };
}

export function toPage<T>(
  items: T[],
  page: number,
  limit: number,
  total: number,
) {
  return {
    items,
    hasMore: page * limit < total,
    page,
    limit,
    total,
  };
}

// H.7.1 - Anonymous fallback shared with game-service / feed-service.
const ANONYMOUS_DISPLAY_NAME_PREFIX = '匿名玩家';

function isLikelyOpenIdBasedUsername(value?: string | null): boolean {
  if (!value) return false;
  return /^(wx_|wxopenid_|wxunionid_|openid_|unionid_|oauth_)[a-zA-Z0-9_.\-]+$/i.test(value);
}

function buildAnonymousDisplayName(userId?: string | null): string {
  const suffix = (userId || '').replace(/-/g, '').slice(0, 4) || 'xxxx';
  return `${ANONYMOUS_DISPLAY_NAME_PREFIX}_${suffix}`;
}

export function pickPublicDisplayName(user: {
  id?: string;
  username?: string | null;
  displayName?: string | null;
}): string {
  const display = user.displayName?.trim();
  if (display && !isLikelyOpenIdBasedUsername(display)) {
    return display;
  }
  const username = user.username?.trim();
  if (username && !isLikelyOpenIdBasedUsername(username)) {
    return username;
  }
  return buildAnonymousDisplayName(user.id);
}

export function presentUser(user: any) {
  if (!user) {
    return null;
  }

  // H.7.1 - displayName must never fall back to a raw `wx_<openid>` style
  // identifier; use the anonymous handle instead.
  const displayName = pickPublicDisplayName(user);

  return {
    id: user.id,
    username: user.username || '',
    email: user.email || undefined,
    phone: user.phone || undefined,
    avatar: user.avatarUrl || user.avatar || '',
    avatarUrl: user.avatarUrl || user.avatar || '',
    bio: user.bio || '',
    displayName,
    createdAt: user.createdAt,
    followerCount: user.followerCount ?? 0,
    followingCount: user.followingCount ?? 0,
    gameCount: user.gameCount ?? 0,
    totalPlays: Number(user.totalPlays ?? 0),
  };
}
