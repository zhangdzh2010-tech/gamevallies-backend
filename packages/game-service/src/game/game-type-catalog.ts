export const CURATED_GAME_TYPES = [
  { value: 'casual', label: '休闲', labelEn: 'Casual' },
  { value: 'puzzle', label: '益智', labelEn: 'Puzzle' },
  { value: 'educational', label: '教育', labelEn: 'Educational' },
  { value: 'funny', label: '搞笑', labelEn: 'Funny' },
] as const;

export type CuratedGameType = (typeof CURATED_GAME_TYPES)[number]['value'];

export const DEFAULT_GAME_TYPE: CuratedGameType = 'casual';

const DIRECT_GAME_TYPE_MAP: Record<string, CuratedGameType> = {
  casual: 'casual',
  puzzle: 'puzzle',
  educational: 'educational',
  education: 'educational',
  funny: 'funny',
  humor: 'funny',
  humour: 'funny',
  comedy: 'funny',
  comedic: 'funny',
  amusing: 'funny',

  action: 'casual',
  arcade: 'casual',
  strategy: 'casual',
  racing: 'casual',
  sports: 'casual',
  simulation: 'casual',
  adventure: 'casual',
  other: 'casual',
  runner: 'casual',
  platformer: 'casual',
  shooter: 'casual',
  dodge: 'casual',
  rhythm: 'funny',
  idle: 'casual',
  rpg: 'casual',
  rts: 'casual',
  survival: 'casual',
  sandbox: 'casual',
  clicker: 'casual',
  action_rpg: 'casual',
  casual_arcade: 'casual',
  casual_lane: 'casual',
  casual_action: 'casual',
  puzzle_grid: 'puzzle',
  tap_challenge: 'funny',
  topdown_action: 'casual',
  topdown_dodge: 'casual',
  lane_runner: 'casual',
  tower_defense: 'puzzle',

  logic: 'puzzle',
  matching: 'puzzle',
  match: 'puzzle',
  'match-3': 'puzzle',
  match3: 'puzzle',
  merge: 'puzzle',
  sorting: 'puzzle',
  sort: 'puzzle',
  word: 'puzzle',
  words: 'puzzle',
  sudoku: 'puzzle',
  crossword: 'puzzle',
  minesweeper: 'puzzle',
  connect: 'puzzle',
  connect4: 'puzzle',
  chess: 'puzzle',
  checkers: 'puzzle',

  quiz: 'educational',
  trivia: 'educational',
  learning: 'educational',
  study: 'educational',
  math: 'educational',
  spelling: 'educational',
  typing: 'educational',
  language: 'educational',
  science: 'educational',
};

const FUNNY_KEYWORDS = [
  'funny',
  'comedy',
  'humor',
  'humour',
  'meme',
  'parody',
  'joke',
  'jokes',
  '搞笑',
  '幽默',
  '恶搞',
  '整活',
  '玩梗',
  '沙雕',
  '抽象',
];

const EDUCATIONAL_KEYWORDS = [
  'educat',
  'learn',
  'study',
  'teach',
  'quiz',
  'trivia',
  'math',
  'spelling',
  'typing',
  'science',
  'history',
  'language',
  'vocabulary',
  '教育',
  '学习',
  '教学',
  '科普',
  '训练',
  '单词',
  '数学',
  '英语',
  '语文',
];

const PUZZLE_KEYWORDS = [
  'puzzle',
  'logic',
  'match',
  'merge',
  'sort',
  'solve',
  'maze',
  'sudoku',
  '2048',
  'word',
  'crossword',
  'minesweeper',
  '拼图',
  '益智',
  '解谜',
  '消除',
  '配对',
  '排序',
  '连线',
  '找茬',
  '数独',
  '华容道',
];

function collectNormalizedStrings(input: unknown): string[] {
  if (Array.isArray(input)) {
    return input.flatMap((item) => collectNormalizedStrings(item));
  }
  if (typeof input !== 'string') {
    return [];
  }
  const normalized = input.trim().toLowerCase();
  return normalized ? [normalized] : [];
}

function matchesKeyword(candidates: string[], keywords: string[]): boolean {
  return candidates.some((candidate) => keywords.some((keyword) => candidate.includes(keyword)));
}

export function normalizeGameType(...inputs: unknown[]): CuratedGameType {
  const candidates = inputs.flatMap((input) => collectNormalizedStrings(input));

  for (const candidate of candidates) {
    const mapped = DIRECT_GAME_TYPE_MAP[candidate];
    if (mapped) {
      return mapped;
    }
  }

  if (matchesKeyword(candidates, FUNNY_KEYWORDS)) {
    return 'funny';
  }

  if (matchesKeyword(candidates, EDUCATIONAL_KEYWORDS)) {
    return 'educational';
  }

  if (matchesKeyword(candidates, PUZZLE_KEYWORDS)) {
    return 'puzzle';
  }

  return DEFAULT_GAME_TYPE;
}
