export interface RuntimeProfileContract {
  defaultRuntimeProfileId: string;
  legacyToCanonical: Record<string, string>;
  canonicalToLegacy: Record<string, string[]>;
}

export type TimeoutConfigValueType = 'int' | 'float';
export type TimeoutConfigService = 'shared' | 'game-service' | 'ai-engine';

export interface TimeoutContractEntry {
  key: string;
  defaultValue: string;
  description: string;
  service: TimeoutConfigService;
  group: string;
  unit: 'ms' | 's' | 'ratio' | 'count';
  valueType: TimeoutConfigValueType;
  sectionId: string;
  itemOrder: number;
}

export declare function loadContractJson<T>(name: string): T;
export declare function loadRuntimeProfileContract(): RuntimeProfileContract;
export declare function loadTimeoutContract(): TimeoutContractEntry[];
