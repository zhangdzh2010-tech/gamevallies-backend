function buildGameUrl(gameId: string, previewUrl?: string) {
  if (previewUrl) {
    return previewUrl.replace(/\/preview$/, '/index.html');
  }

  const baseUrl = (process.env.GAME_SERVICE_URL || 'http://localhost:3002').replace(/\/$/, '');
  return `${baseUrl}/games/${gameId}/index.html`;
}

function presentAuthor(author?: any) {
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
  const previewUrl = game.previewUrl || buildGameUrl(game.id);
  const gameUrl = buildGameUrl(game.id, previewUrl);

  return {
    id: game.id,
    title: game.title,
    description: game.description || '',
    status: game.status === 'draft' ? 'ready' : game.status,
    gameUrl,
    previewUrl,
    coverUrl: game.thumbnailUrl || gameUrl,
    tags: game.tags || [],
    type: game.gameType || 'casual',
    plays: Number(game.playCount || game.plays || 0),
    likes: Number(game.likeCount || game.likes || 0),
    forks: Number(game.forkCount || game.forks || 0),
    authorId: game.authorId || game.author?.id || null,
    author: presentAuthor(game.author),
    createdAt: game.createdAt,
    publishedAt: game.publishedAt,
  };
}

export function presentTag(tag: any) {
  return {
    tag: tag.name || tag.tag,
    count: Number(tag.count || 0),
  };
}

export function presentCreator(creator: any) {
  return {
    id: creator.id,
    username: creator.displayName || creator.username || '',
    avatar: creator.avatarUrl || '',
    bio: creator.bio || '',
    gamesCount: creator.stats?.gameCount ?? creator.gamesCount ?? 0,
    totalPlays: creator.stats?.totalPlays ?? creator.totalPlays ?? 0,
    followerCount: creator.followerCount ?? 0,
  };
}

export function presentChallenge(challenge: any) {
  return {
    id: challenge.id,
    title: challenge.title,
    description: challenge.description,
    endsAt: challenge.endDate || challenge.endsAt,
    startDate: challenge.startDate,
    participantCount: challenge.participantCount ?? 0,
    rules: challenge.rules || [],
  };
}
