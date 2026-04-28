// H.5.1 / H.7.1 - Local copies of the sanitize & anonymous-handle helpers used
// by game-service. Creation sessions no longer expose AI-expanded prompts,
// but share cards still need to defend against legacy persisted scaffolding
// and raw `wx_<openid>` style identifiers. Keep in lockstep with:
//   - game-service `src/common/sanitize-idea.ts`
//   - feed-service `src/common/sanitize-idea.ts`
//   - frontend `src/utils/sanitizeIdea.js`

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
  /^\s*任务\s*[:：]\s*接收用户简短游戏描述.*$/,
  /^\s*要求\s*[:：]\s*$/,
  /^\s*输出必须覆盖的要素.*$/,
  /^\s*Please\s+turn\s+this\s+brief\s+into\s+a\s+mobile-friendly.*$/i,
  /^\s*Please\s+expand\s+the\s+(?:user\s+)?(?:idea|brief)\s+into.*$/i,
  /^\s*covers?\s+at\s+least\s+these\s+elements.*$/i,
];

const PLACEHOLDER_VALUE_MARKERS: readonly string[] = [
  '根据原始想法确定',
  '根据原始想法',
  '根据用户想法',
  '根据用户输入',
  '保留原始想法',
  '保留用户想法',
  '提炼玩家最常执行',
  '提炼玩家最常',
  '采用适合手机',
  '采用适合的手机',
  '明确玩家这一局',
  '明确玩家一局',
  '说明难度如何',
  '说明如何',
  '补充积分',
  '补充奖励',
  '给出匹配题材',
  '给出匹配',
  '仅在确有帮助',
  '仅在有帮助时',
  '仅在确有帮助时补充',
  '后续补充',
  '待定',
  '待补充',
  'to be determined',
  'to be added',
  'todo',
  'tbd',
  'determine based on',
  'choose the most fitting',
  'describe the main repeated',
  'preserve the setting',
  'use touch-friendly tap',
  'use touch-friendly',
  'define a clear round',
  'explain how the challenge',
  'add points, streaks',
  'add points and rewards',
  'suggest an art direction',
  'add only when',
];

const PLACEHOLDER_LOWERED = PLACEHOLDER_VALUE_MARKERS.map((m) => m.toLowerCase());

function splitLabel(line: string): { label: string; value: string } | null {
  const stripped = line.trim();
  if (!stripped) return null;
  let colonIndex = -1;
  for (let i = 0; i < stripped.length; i += 1) {
    const ch = stripped[i];
    if (ch === ':' || ch === '：') {
      colonIndex = i;
      break;
    }
  }
  if (colonIndex <= 0 || colonIndex > 40) return null;
  const label = stripped.slice(0, colonIndex).trim();
  const value = stripped.slice(colonIndex + 1).trim();
  if (!label) return null;
  return { label, value };
}

function isPlaceholderValue(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return true;
  const lowered = trimmed.toLowerCase();
  for (const marker of PLACEHOLDER_LOWERED) {
    if (lowered.startsWith(marker)) return true;
  }
  if (trimmed.length <= 30) {
    for (const marker of PLACEHOLDER_LOWERED) {
      if (lowered.includes(marker)) return true;
    }
  }
  return false;
}

function isPlaceholderOnlyLine(line: string): boolean {
  const stripped = line.trim();
  if (!stripped || stripped.length > 60) return false;
  return isPlaceholderValue(stripped);
}

function matchesAny(line: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(line));
}

const LEGACY_TAIL_PATTERNS: RegExp[] = [
  /\s*请把这条想法整理成[\s\S]*$/,
  /\s*(?:\n|^)?\s*(?:Game Type|Core Mechanic|Theme|Input Method|Win Condition|Difficulty Ramp|Scoring\s*\/\s*Rewards|Visual Direction|Special Rules[^:\n]*)\s*[:：]\s*(?:根据原始想法|提炼玩家|保留原始想法|采用适合手机|明确玩家|说明难度|补充积分|给出匹配|仅在确有帮助|determine based on|describe the main|preserve the setting|use touch-friendly|define a clear|explain how the challenge|add points|suggest an art|add only when)[\s\S]*$/i,
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
    const labeled = splitLabel(line);
    if (labeled !== null) {
      if (isPlaceholderValue(labeled.value)) {
        continue;
      }
    } else if (isPlaceholderOnlyLine(line)) {
      continue;
    }
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
