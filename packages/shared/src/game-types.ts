/**
 * Canonical game categories for the platform: 休闲 / 益智 / 教育
 * Stored values are English slugs; labels are Chinese for API consumers.
 */

export const GAME_TYPES = [
  { code: 'casual', label: '休闲' },
  { code: 'puzzle', label: '益智' },
  { code: 'education', label: '教育' },
] as const;

export type CanonicalGameType = (typeof GAME_TYPES)[number]['code'];

export const CANONICAL_GAME_TYPE_CODES: readonly CanonicalGameType[] = GAME_TYPES.map((t) => t.code);

const ZH_TO_CODE: Record<string, CanonicalGameType> = {
  休闲: 'casual',
  益智: 'puzzle',
  教育: 'education',
};

/** Lowercase English aliases that map to canonical codes (legacy / AI output). */
const LEGACY_TO_CANONICAL: Record<string, CanonicalGameType> = {
  casual: 'casual',
  puzzle: 'puzzle',
  education: 'education',
  educational: 'education',
  edu: 'education',
  logic: 'puzzle',
  brain: 'puzzle',
};

/**
 * Map free-form or legacy game type strings to a canonical value.
 * Unknown mechanics (e.g. dodge, shooter) become `casual`.
 */
export function normalizeLegacyGameType(input: string | null | undefined): CanonicalGameType {
  if (input == null) return 'casual';
  const s = String(input).trim();
  if (!s) return 'casual';

  if (ZH_TO_CODE[s]) return ZH_TO_CODE[s];

  const lower = s.toLowerCase();
  if (LEGACY_TO_CANONICAL[lower]) return LEGACY_TO_CANONICAL[lower];

  return 'casual';
}

/**
 * Parse a client-provided game type (strict). Accepts canonical codes or Chinese labels.
 */
export function parseStrictGameType(input: string): CanonicalGameType {
  const s = String(input).trim();
  if (!s) {
    throw new Error('gameType cannot be empty');
  }
  if (ZH_TO_CODE[s]) return ZH_TO_CODE[s];
  const lower = s.toLowerCase();
  if (lower === 'casual' || lower === 'puzzle' || lower === 'education') {
    return lower;
  }
  throw new Error(
    `Invalid gameType "${input}"; expected one of: casual, puzzle, education (or 休闲, 益智, 教育)`,
  );
}

/**
 * When publishing: optional override uses strict parsing; otherwise normalize existing DB value.
 */
export function resolvePublishGameType(
  dtoGameType: string | null | undefined,
  existingGameType: string | null | undefined,
): CanonicalGameType {
  if (dtoGameType !== undefined && dtoGameType !== null && String(dtoGameType).trim() !== '') {
    return parseStrictGameType(String(dtoGameType));
  }
  return normalizeLegacyGameType(existingGameType);
}

/** Normalize filter/query parameters (maps legacy names to canonical). */
export function normalizeGameTypeFilter(input: string | null | undefined): CanonicalGameType {
  return normalizeLegacyGameType(input);
}
