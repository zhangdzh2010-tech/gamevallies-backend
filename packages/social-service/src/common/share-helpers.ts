// H.5.1 / H.7.1 - Local copies of the sanitize & anonymous-handle helpers used
// by game-service. The social-service ShareService must stay aligned with the
// game-service presenter so C-end share cards never leak LLM prompt scaffolding
// or raw `wx_<openid>` style identifiers. When the shared package adds a
// canonical home for these helpers, this file should re-export from there.

const PREFIX_DROP_LINE_PATTERNS: RegExp[] = [
  /^\s*原始想法\s*[:：].*$/,
  /^\s*用户想法\s*[:：].*$/,
  /^\s*用户输入\s*[:：].*$/,
  /^\s*Original\s+Idea\s*[:：].*$/i,
  /^\s*User(?:'s)?\s+Idea\s*[:：].*$/i,
  /^\s*User\s+Input\s*[:：].*$/i,
];

const META_INSTRUCTION_LINE_PATTERNS: RegExp[] = [
  /^\s*请把这条想法整理成.*$/,
  /^\s*请将这条想法整理成.*$/,
  /^\s*请把以下想法整理成.*$/,
  /^\s*请将以下想法整理成.*$/,
  /^\s*至少(?:要)?覆盖这些要素.*$/,
  /^\s*请覆盖以下要素.*$/,
  /^\s*Please\s+turn\s+this\s+brief\s+into\s+a\s+mobile-friendly.*$/i,
  /^\s*Please\s+expand\s+the\s+(?:user\s+)?(?:idea|brief)\s+into.*$/i,
  /^\s*covers?\s+at\s+least\s+these\s+elements.*$/i,
];

const LABEL_PREFIXES = [
  'game type',
  'core mechanic',
  'theme',
  'input method',
  'win condition',
  'difficulty ramp',
  'scoring / rewards',
  'scoring/rewards',
  'scoring',
  'rewards',
  'visual direction',
  'special rules or reference inspiration',
  'special rules',
  'reference inspiration',
  '游戏类型',
  '核心玩法',
  '核心机制',
  '主题',
  '操作方式',
  '操作方法',
  '输入方式',
  '胜利条件',
  '通关条件',
  '难度节奏',
  '难度曲线',
  '积分',
  '奖励',
  '积分 / 奖励',
  '积分/奖励',
  '视觉方向',
  '视觉风格',
  '视觉',
  '特殊规则',
  '特殊规则或参考灵感',
  '参考灵感',
  '参考游戏',
] as const;

const LABEL_SET = new Set(LABEL_PREFIXES.map((label) => label.toLowerCase()));

function lineStartsWithLabel(line: string): boolean {
  const stripped = line.trim();
  if (!stripped) return false;
  let colonIndex = -1;
  for (let i = 0; i < stripped.length; i += 1) {
    const ch = stripped[i];
    if (ch === ':' || ch === '：') {
      colonIndex = i;
      break;
    }
  }
  if (colonIndex <= 0 || colonIndex > 40) return false;
  const head = stripped.slice(0, colonIndex).trim().toLowerCase();
  return head.length > 0 && LABEL_SET.has(head);
}

function matchesAny(line: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(line));
}

const LEGACY_TAIL_PATTERNS: RegExp[] = [
  /\s*请把这条想法整理成[\s\S]*$/,
  /\s*(?:\n|^)?\s*(?:Game Type|Core Mechanic|Theme|Input Method|Win Condition|Difficulty Ramp|Scoring\s*\/\s*Rewards|Visual Direction|Special Rules[^:\n]*)\s*[:：][\s\S]*$/i,
];

const LEGACY_PREFIX_PATTERNS: RegExp[] = [
  /^\s*原始想法\s*[:：]\s*/,
  /^\s*用户想法\s*[:：]\s*/,
  /^\s*User\s*Idea\s*[:：]\s*/i,
];

export function sanitizeUserIdea(raw: string | null | undefined): string {
  if (raw == null) return '';
  let text = String(raw);

  for (const pattern of LEGACY_PREFIX_PATTERNS) {
    text = text.replace(pattern, '');
  }
  for (const pattern of LEGACY_TAIL_PATTERNS) {
    text = text.replace(pattern, '');
  }

  const normalizedNewlines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const cleaned: string[] = [];
  for (const rawLine of normalizedNewlines.split('\n')) {
    const line = rawLine.replace(/\s+$/u, '');
    if (!line.trim()) {
      cleaned.push('');
      continue;
    }
    if (matchesAny(line, PREFIX_DROP_LINE_PATTERNS)) continue;
    if (matchesAny(line, META_INSTRUCTION_LINE_PATTERNS)) continue;
    if (lineStartsWithLabel(line)) continue;
    cleaned.push(line);
  }

  const collapsed: string[] = [];
  let previousBlank = false;
  for (const line of cleaned) {
    if (!line.trim()) {
      if (previousBlank) continue;
      previousBlank = true;
      collapsed.push('');
      continue;
    }
    previousBlank = false;
    collapsed.push(line);
  }
  while (collapsed.length && !collapsed[0].trim()) collapsed.shift();
  while (collapsed.length && !collapsed[collapsed.length - 1].trim()) collapsed.pop();

  return collapsed.join('\n').trim();
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
