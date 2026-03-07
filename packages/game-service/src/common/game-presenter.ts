function buildIndexUrl(gameId: string, previewUrl?: string) {
  if (previewUrl) {
    return previewUrl.replace(/\/preview$/, '/index.html');
  }

  const baseUrl = (process.env.APP_URL || 'http://localhost:3002').replace(/\/$/, '');
  return `${baseUrl}/games/${gameId}/index.html`;
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
  const previewUrl = game.previewUrl || buildIndexUrl(game.id);
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
    authorId: game.authorId || game.author?.id || null,
    author: presentAuthor(game.author),
    createdAt: game.createdAt,
    updatedAt: game.updatedAt,
    publishedAt: game.publishedAt,
  };
}
