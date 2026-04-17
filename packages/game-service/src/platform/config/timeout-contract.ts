import { loadContractJson } from './contract-loader';

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

let cachedTimeoutContract: TimeoutContractEntry[] | null = null;

export function loadTimeoutContract(): TimeoutContractEntry[] {
  if (!cachedTimeoutContract) {
    cachedTimeoutContract = loadContractJson<TimeoutContractEntry[]>('timeout-keys.json');
  }
  return cachedTimeoutContract;
}
