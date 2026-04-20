import { sanitizeUserIdea } from './sanitize-idea';

// H.7.1 - keep this in lockstep with game-service/src/common/game-presenter.ts.
const ANONYMOUS_DISPLAY_NAME_PREFIX = '匿名玩家';

function isLikelyOpenIdBasedUsername(value?: string | null): boolean {
  if (!value) return false;
  return /^(wx_|wxopenid_|wxunionid_|openid_|unionid_|oauth_)[a-zA-Z0-9_.\-]+$/i.test(value);
}

function buildAnonymousDisplayName(authorId?: string | null): string {
  const suffix = (authorId || '').replace(/-/g, '').slice(0, 4) || 'xxxx';
  return `${ANONYMOUS_DISPLAY_NAME_PREFIX}_${suffix}`;
}

export function pickPublicAuthorName(author: {
  id?: string;
  username?: string | null;
  displayName?: string | null;
}): string {
  const display = author.displayName?.trim();
  if (display && !isLikelyOpenIdBasedUsername(display)) {
    return display;
  }
  const username = author.username?.trim();
  if (username && !isLikelyOpenIdBasedUsername(username)) {
    return username;
  }
  return buildAnonymousDisplayName(author.id);
}

function buildGameUrl(gameId: string, previewUrl?: string) {
  if (previewUrl) {
    return previewUrl.replace(/\/preview$/, '/index.html');
  }

  const baseUrl = (
    process.env.PUBLIC_API_BASE_URL ||
    process.env.GAME_SERVICE_URL ||
    'http://localhost:3002'
  ).replace(/\/$/, '');
  return `${baseUrl}/games/${gameId}/index.html`;
}

function presentAuthor(author?: any) {
  if (!author?.id) {
    return null;
  }

  const publicName = pickPublicAuthorName(author);

  return {
    id: author.id,
    // H.7.1 - hide raw `wx_<openid>` style identifiers from the feed.
    username: publicName,
    displayName: publicName,
    avatar: author.avatarUrl || '',
  };
}

export function presentGame(game: any) {
  const previewUrl = game.previewUrl || buildGameUrl(game.id);
  const gameUrl = buildGameUrl(game.id, previewUrl);

  // H.5.1 - prefer the explicit user-facing tagline; fall back to a sanitized
  // version of the legacy `description` so feed cards never show raw LLM
  // prompt scaffolding for old rows.
  const publicDescription =
    (game.userIdea && String(game.userIdea).trim()) ||
    sanitizeUserIdea(game.description);

  return {
    id: game.id,
    title: game.title,
    description: publicDescription,
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
  const publicName = pickPublicAuthorName(creator);
  return {
    id: creator.id,
    username: publicName,
    displayName: publicName,
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
