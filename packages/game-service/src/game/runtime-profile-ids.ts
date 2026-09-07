import { loadRuntimeProfileContract } from '../platform/config/runtime-profile-contract';

const runtimeProfileContract = loadRuntimeProfileContract();

export const DEFAULT_RUNTIME_PROFILE_ID = runtimeProfileContract.defaultRuntimeProfileId;

export const LEGACY_TO_CANONICAL_RUNTIME_PROFILE_IDS: Record<string, string> = {
  ...runtimeProfileContract.legacyToCanonical,
};

export const CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS: Record<string, string[]> = {
  ...runtimeProfileContract.canonicalToLegacy,
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
  const legacy = CANONICAL_TO_LEGACY_RUNTIME_PROFILE_IDS[canonical] || [];
  const candidates = [canonical, ...legacy.map(value => normalizeRuntimeProfileId(value) || value), ...legacy];
  return candidates.filter((value, index) => value && candidates.indexOf(value) === index);
}
