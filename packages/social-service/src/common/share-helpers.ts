// H.5.1 / H.7.1 - Local copies of the sanitize & anonymous-handle helpers used
// by game-service. The social-service ShareService must stay aligned with the
// game-service presenter so C-end share cards never leak LLM prompt scaffolding
// or raw `wx_<openid>` style identifiers. When the shared package adds a
// canonical home for these helpers, this file should re-export from there.

const TEMPLATE_TAIL_PATTERNS: RegExp[] = [
  /\s*请把这条想法整理成[\s\S]*$/,
  /\s*(?:\n|^)?\s*(?:Game Type|Core Mechanic|Theme|Input Method|Win Condition|Difficulty Ramp|Scoring\s*\/\s*Rewards|Visual Direction|Special Rules[^:\n]*)\s*[:：][\s\S]*$/i,
];

const PREFIX_PATTERNS: RegExp[] = [
  /^\s*原始想法\s*[:：]\s*/,
  /^\s*用户想法\s*[:：]\s*/,
  /^\s*User\s*Idea\s*[:：]\s*/i,
];

export function sanitizeUserIdea(raw: string | null | undefined): string {
  if (raw == null) {
    return '';
  }
  let text = String(raw);

  for (const pattern of PREFIX_PATTERNS) {
    text = text.replace(pattern, '');
  }
  for (const pattern of TEMPLATE_TAIL_PATTERNS) {
    text = text.replace(pattern, '');
  }

  return text.trim();
}

export function pickPublicShareDescription(game: {
  userIdea?: string | null;
  description?: string | null;
}): string {
  const userIdea = game.userIdea?.trim();
  if (userIdea) {
    return userIdea;
  }
  return sanitizeUserIdea(game.description);
}

const ANONYMOUS_DISPLAY_NAME_PREFIX = '匿名玩家';

function isLikelyOpenIdBasedUsername(value?: string | null): boolean {
  if (!value) return false;
  return /^(wx_|wxopenid_|wxunionid_|openid_|unionid_|oauth_)[a-zA-Z0-9_.\-]+$/i.test(value);
}

function buildAnonymousDisplayName(userId?: string | null): string {
  const suffix = (userId || '').replace(/-/g, '').slice(0, 4) || 'xxxx';
  return `${ANONYMOUS_DISPLAY_NAME_PREFIX}_${suffix}`;
}

export function pickPublicShareAuthorName(author?: {
  id?: string | null;
  username?: string | null;
  displayName?: string | null;
} | null): string {
  if (!author) {
    return buildAnonymousDisplayName(null);
  }
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
