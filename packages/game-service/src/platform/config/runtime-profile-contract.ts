import { loadContractJson } from './contract-loader';

export interface RuntimeProfileContract {
  defaultRuntimeProfileId: string;
  legacyToCanonical: Record<string, string>;
  canonicalToLegacy: Record<string, string[]>;
}

let cachedContract: RuntimeProfileContract | null = null;

export function loadRuntimeProfileContract(): RuntimeProfileContract {
  if (!cachedContract) {
    cachedContract = loadContractJson<RuntimeProfileContract>('runtime-profiles.json');
  }
  return cachedContract;
}
