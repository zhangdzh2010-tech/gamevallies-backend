export const CREATION_SESSION_ENTRY_MODES = ['create', 'fork', 'iterate'] as const;
export type CreationSessionEntryMode = (typeof CREATION_SESSION_ENTRY_MODES)[number];

export const CREATION_SESSION_STATUSES = [
  'collecting',
  'ready',
  'generating',
  'completed',
  'failed',
  'abandoned',
] as const;
export type CreationSessionStatus = (typeof CREATION_SESSION_STATUSES)[number];

export const CREATION_SESSION_ACTIVE_STATUSES = [
  'collecting',
  'ready',
  'generating',
] as const;

/** Statuses where the session is still mutable (user can append messages / skip). */
export const CREATION_SESSION_MUTABLE_STATUSES = [
  'collecting',
  'ready',
] as const;

/** Stale generating sessions older than this (ms) can be auto-abandoned. */
export const CREATION_SESSION_GENERATING_EXPIRE_MS = 10 * 60 * 1000; // 10 min

export const DEFAULT_CREATION_SESSION_QUESTION_BUDGET = 4;
