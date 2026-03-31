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

export const DEFAULT_CREATION_SESSION_QUESTION_BUDGET = 4;
