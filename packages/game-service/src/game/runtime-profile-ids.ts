export const DEFAULT_RUNTIME_PROFILE_ID = 'casual_arcade';

export const LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS: Record<string, string> = {
  portrait_arcade: 'casual_arcade',
  lane_runner: 'casual_lane',
  grid_puzzle: 'puzzle_grid',
  topdown_action: 'casual_action',
  tap_timing: 'tap_challenge',
  topdown_dodge: 'casual_action',
  topdown_shooter: 'casual_action',
};

export const CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS: Record<string, string[]> = {
  casual_arcade: ['portrait_arcade'],
  casual_arcade_burst: ['portrait_arcade'],
  casual_arcade_orbit: ['portrait_arcade'],
  casual_arcade_rescue: ['portrait_arcade'],
  casual_lane: ['lane_runner'],
  casual_lane_dash: ['lane_runner'],
  casual_lane_chase: ['lane_runner'],
  puzzle_grid: ['grid_puzzle'],
  puzzle_grid_match: ['grid_puzzle'],
  puzzle_grid_merge: ['grid_puzzle'],
  puzzle_grid_route: ['grid_puzzle'],
  casual_action: ['topdown_action', 'topdown_dodge', 'topdown_shooter'],
  casual_action_arena: ['topdown_action', 'topdown_dodge', 'topdown_shooter'],
  casual_action_survival: ['topdown_action', 'topdown_dodge', 'topdown_shooter'],
  tap_challenge: ['tap_timing'],
  tap_challenge_timing: ['tap_timing'],
  tap_challenge_combo: ['tap_timing'],
};

export function normalizeRuntimeProfileId(profileId?: string | null): string | undefined {
  const normalized = String(profileId || '').trim();
  if (!normalized) {
    return undefined;
  }
  return LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS[normalized] || normalized;
}

export function runtimeProfileLookupCandidates(profileId?: string | null): string[] {
  const canonical = normalizeRuntimeProfileId(profileId) || DEFAULT_RUNTIME_PROFILE_ID;
  const candidates = [canonical, ...(CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS[canonical] || [])];
  return candidates.filter((value, index) => value && candidates.indexOf(value) === index);
}
