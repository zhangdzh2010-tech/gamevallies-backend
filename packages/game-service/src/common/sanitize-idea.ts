// H.5.1 - Strip LLM prompt-template boilerplate from a description that will be
// shown directly to end users (game detail / iterate / fork / feed).
//
// Historical reason: GameService.create stores the EXPANDED LLM prompt
// (user idea + English spec template appended by the LLM) into `games.description`,
// which is also the field returned to the C-end. Until the schema is migrated to
// keep the user-facing idea separate (Game.userIdea), every read path that exposes
// description should run through `sanitizeUserIdea` so users never see raw prompt
// scaffolding like "Game Type: ..." / "Core Mechanic: ...".
//
// Patterns must stay in lockstep with frontend `src/utils/sanitizeIdea.js`.

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

export default sanitizeUserIdea;
