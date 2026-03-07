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

export function presentUser(user: any) {
  if (!user) {
    return null;
  }

  return {
    id: user.id,
    username: user.username || '',
    email: user.email || undefined,
    phone: user.phone || undefined,
    avatar: user.avatarUrl || user.avatar || '',
    avatarUrl: user.avatarUrl || user.avatar || '',
    bio: user.bio || '',
    displayName: user.displayName || user.username || '',
    createdAt: user.createdAt,
    followerCount: user.followerCount ?? 0,
    followingCount: user.followingCount ?? 0,
    gameCount: user.gameCount ?? 0,
    totalPlays: Number(user.totalPlays ?? 0),
  };
}
