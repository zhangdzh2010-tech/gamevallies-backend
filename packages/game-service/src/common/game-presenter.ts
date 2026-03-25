function getPublicBaseUrl() {
  return (process.env.PUBLIC_API_BASE_URL || process.env.APP_URL || 'http://localhost:3002').replace(/\/$/, '');
}

function normalizePreviewUrl(gameId: string, previewUrl?: string) {
  const baseUrl = getPublicBaseUrl();
  if (!previewUrl) {
    return `${baseUrl}/games/${gameId}/preview`;
  }

  try {
    const parsed = new URL(previewUrl);
    return `${baseUrl}${parsed.pathname}${parsed.search}`;
  } catch {
    if (previewUrl.startsWith('/')) {
      return `${baseUrl}${previewUrl}`;
    }

    return `${baseUrl}/games/${gameId}/preview`;
  }
}

function buildIndexUrl(gameId: string, previewUrl?: string) {
  const normalizedPreviewUrl = normalizePreviewUrl(gameId, previewUrl);

  try {
    const parsed = new URL(normalizedPreviewUrl);
    parsed.pathname = parsed.pathname.replace(/\/preview$/, '/index.html');
    return parsed.toString();
  } catch {
    return normalizedPreviewUrl.replace(/\/preview(\?.*)?$/, '/index.html$1');
  }
}

function normalizeStatus(status?: string) {
  if (status === 'draft') {
    return 'ready';
  }

  return status || 'draft';
}

function presentAuthor(author?: {
  id?: string;
  username?: string | null;
  displayName?: string | null;
  avatarUrl?: string | null;
}) {
  if (!author?.id) {
    return null;
  }

  return {
    id: author.id,
    username: author.username || author.displayName || '',
    avatar: author.avatarUrl || '',
  };
}

export function presentGame(game: any) {
  const previewUrl = normalizePreviewUrl(game.id, game.previewUrl);
  const gameUrl = buildIndexUrl(game.id, previewUrl);

  return {
    id: game.id,
    title: game.title,
    description: game.description || '',
    status: normalizeStatus(game.status),
    gameUrl,
    previewUrl,
    coverUrl: game.thumbnailUrl || gameUrl,
    tags: game.tags || [],
    type: game.gameType || 'casual',
    plays: Number(game.playCount || game.plays || 0),
    likes: Number(game.likeCount || game.likes || 0),
    forks: Number(game.forkCount || game.forks || 0),
    commentCount: Number(game.commentCount || 0),
    qualityScore: game.qualityScore ?? 0,
    canPlay: game.canPlay ?? true,
    requireSubscription: game.requireSubscription ?? false,
    authorId: game.authorId || game.author?.id || null,
    author: presentAuthor(game.author),
    createdAt: game.createdAt,
    updatedAt: game.updatedAt,
    publishedAt: game.publishedAt,
  };
}
