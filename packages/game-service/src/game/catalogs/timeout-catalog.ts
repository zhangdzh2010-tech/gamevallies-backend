import {
  TimeoutConfigService,
  TimeoutConfigValueType,
  loadTimeoutContract,
} from '../../platform/config/timeout-contract';

export type TimeoutConfigSectionKind = 'flow' | 'extra' | 'infra';

export interface TimeoutConfigSectionMeta {
  id: string;
  kind: TimeoutConfigSectionKind;
  order: number;
  tag: string;
  title: string;
  description: string;
}

export interface TimeoutConfigCatalogEntry {
  key: string;
  defaultValue: string;
  description: string;
  service: TimeoutConfigService;
  group: string;
  unit: 'ms' | 's' | 'ratio' | 'count';
  valueType: TimeoutConfigValueType;
  sectionId: string;
  sectionKind: TimeoutConfigSectionKind;
  sectionOrder: number;
  sectionTag: string;
  sectionTitle: string;
  sectionDescription: string;
  itemOrder: number;
}

const TIMEOUT_CONFIG_SECTIONS: Record<string, TimeoutConfigSectionMeta> = {
  step1_intent_freeze: {
    id: 'step1_intent_freeze',
    kind: 'flow',
    order: 10,
    tag: 'Step 1',
    title: 'Intent Freeze',
    description: 'Prompt expansion and create-entry watchdogs.',
  },
  step2_spec_contract: {
    id: 'step2_spec_contract',
    kind: 'flow',
    order: 20,
    tag: 'Step 2',
    title: 'Spec & Contract',
    description:
      'Single-shot intent parsing and spec compilation timeout budgets.',
  },
  step3_code_synthesis: {
    id: 'step3_code_synthesis',
    kind: 'flow',
    order: 30,
    tag: 'Step 3',
    title: 'Code Synthesis',
    description: 'Primary create-generation timeout ceilings.',
  },
  step4_issue_repair: {
    id: 'step4_issue_repair',
    kind: 'flow',
    order: 40,
    tag: 'Step 4',
    title: 'Issue Repair & Runtime QA',
    description: 'QA repair windows and runtime validation watchdog budgets.',
  },
  step5_finalize: {
    id: 'step5_finalize',
    kind: 'flow',
    order: 50,
    tag: 'Step 5',
    title: 'Finalize',
    description:
      'Deterministic finalize currently has no standalone timeout knobs.',
  },
  extra_iterate: {
    id: 'extra_iterate',
    kind: 'extra',
    order: 60,
    tag: 'Extra',
    title: 'Iterate Flow',
    description:
      'Iterate classify and rewrite budgets outside the main create path.',
  },
  extra_tools: {
    id: 'extra_tools',
    kind: 'extra',
    order: 70,
    tag: 'Extra',
    title: 'Auxiliary Tools',
    description: 'Prompt expansion and other sidecar tools.',
  },
  infra_pipeline: {
    id: 'infra_pipeline',
    kind: 'infra',
    order: 80,
    tag: 'Infra',
    title: 'Pipeline Guardrails',
    description: 'Cross-service total-deadline and buffer settings.',
  },
  infra_transport: {
    id: 'infra_transport',
    kind: 'infra',
    order: 90,
    tag: 'Infra',
    title: 'Upstream Transport',
    description:
      'game-service to ai-engine request, polling, and cancellation timeouts.',
  },
  infra_relay: {
    id: 'infra_relay',
    kind: 'infra',
    order: 100,
    tag: 'Infra',
    title: 'Relay Backchannel',
    description:
      'ai-engine callbacks that report progress, artifacts, and failure details.',
  },
  infra_config: {
    id: 'infra_config',
    kind: 'infra',
    order: 110,
    tag: 'Infra',
    title: 'Config & Gateway Cache',
    description:
      'Admin refresh, config-store reads, and gateway cache refresh windows.',
  },
  infra_tasking: {
    id: 'infra_tasking',
    kind: 'infra',
    order: 120,
    tag: 'Infra',
    title: 'Background Tasking',
    description:
      'Sweep loops, heartbeat cadence, and completed-task retention.',
  },
};

export const TIMEOUT_CONFIG_CATALOG: TimeoutConfigCatalogEntry[] = loadTimeoutContract().map((input) => {
  const section = TIMEOUT_CONFIG_SECTIONS[input.sectionId];
  if (!section) {
    throw new Error(`Unknown timeout config section: ${input.sectionId}`);
  }

  return {
    ...input,
    sectionKind: section.kind,
    sectionOrder: section.order,
    sectionTag: section.tag,
    sectionTitle: section.title,
    sectionDescription: section.description,
  };
});

export const TIMEOUT_CONFIG_CATALOG_BY_KEY = new Map(
  TIMEOUT_CONFIG_CATALOG.map((catalogEntry) => [
    catalogEntry.key,
    catalogEntry,
  ]),
);
