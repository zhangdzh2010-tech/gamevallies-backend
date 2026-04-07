import { createHash } from 'crypto';

export interface IntentBuildPlanDraftLike {
  title?: string | null;
  summary?: string | null;
  concept?: string | null;
  interaction?: string | null;
  objective?: string | null;
  pacing?: string | null;
  visualDirection?: string | null;
  signatureMoment?: string | null;
}

export interface IntentBuildSnapshot {
  brief: string | null;
  frozenSpec: Record<string, unknown> | null;
  intentFingerprint: string | null;
  specFingerprint: string | null;
}

interface BuildIntentBuildSnapshotParams {
  title?: string | null;
  initialPrompt?: string | null;
  planDraft?: IntentBuildPlanDraftLike | null;
  slotState?: Record<string, unknown> | null;
  skippedSlots?: string[] | null;
  sourceSpec?: Record<string, unknown> | null;
  entryMode?: string | null;
  generationTier?: string | null;
  missingRequired?: string[] | null;
}

export function buildIntentBuildSnapshot(
  params: BuildIntentBuildSnapshotParams,
): IntentBuildSnapshot {
  const normalizedPlanDraft = normalizePlanDraftLike(params.planDraft);
  const normalizedSlotState = normalizePlainObject(params.slotState);
  const frozenSpec = normalizePlainObject(params.sourceSpec);
  const brief = buildIntentBrief({
    title: normalizeText(params.title) || normalizedPlanDraft?.title || null,
    initialPrompt: normalizeText(params.initialPrompt) || null,
    planDraft: normalizedPlanDraft,
    slotState: normalizedSlotState,
    sourceSpec: frozenSpec,
  });

  const intentFingerprint = fingerprintValue({
    title: normalizeText(params.title) || null,
    initialPrompt: normalizeText(params.initialPrompt) || null,
    entryMode: normalizeText(params.entryMode) || 'create',
    generationTier: normalizeText(params.generationTier) || 'standard',
    planDraft: normalizedPlanDraft,
    slotState: normalizedSlotState,
    skippedSlots: sortStrings(normalizeStringList(params.skippedSlots)),
    missingRequired: sortStrings(normalizeStringList(params.missingRequired)),
  });

  return {
    brief,
    frozenSpec,
    intentFingerprint,
    specFingerprint: frozenSpec ? fingerprintValue(frozenSpec) : null,
  };
}

export function normalizeIntentBuildSnapshot(value: unknown): IntentBuildSnapshot | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const brief = normalizeText(record.brief) || null;
  const frozenSpec = normalizePlainObject(record.frozenSpec);
  const intentFingerprint = normalizeText(record.intentFingerprint) || null;
  const specFingerprint = normalizeText(record.specFingerprint) || null;
  if (!brief && !frozenSpec && !intentFingerprint && !specFingerprint) {
    return null;
  }
  return {
    brief,
    frozenSpec,
    intentFingerprint,
    specFingerprint,
  };
}

function buildIntentBrief(params: {
  title?: string | null;
  initialPrompt?: string | null;
  planDraft?: IntentBuildPlanDraftLike | null;
  slotState?: Record<string, unknown> | null;
  sourceSpec?: Record<string, unknown> | null;
}): string | null {
  const lines = [
    params.title ? `Title: ${params.title}` : '',
    pickFirst([
      extractSpecString(params.sourceSpec, 'intent_summary'),
      normalizeText(params.planDraft?.summary),
      normalizeText(params.initialPrompt),
    ], 'Summary'),
    pickFirst([
      normalizeText(params.planDraft?.concept),
      normalizeSlotValue(params.slotState?.theme),
    ], 'Concept'),
    pickFirst([
      normalizeText(params.planDraft?.interaction),
      extractCoreMechanicsText(params.sourceSpec),
      normalizeSlotValue(params.slotState?.core_mechanic),
    ], 'Interaction'),
    pickFirst([
      normalizeText(params.planDraft?.objective),
      extractNestedSpecString(params.sourceSpec, ['rules', 'win_condition']),
      normalizeSlotValue(params.slotState?.win_condition),
    ], 'Objective'),
    pickFirst([
      normalizeText(params.planDraft?.pacing),
      extractSpecString(params.sourceSpec, 'session_length'),
      normalizeSlotValue(params.slotState?.difficulty),
    ], 'Pacing'),
    pickFirst([
      normalizeText(params.planDraft?.visualDirection),
      extractNestedSpecString(params.sourceSpec, ['visual_style', 'theme']),
      normalizeSlotValue(params.slotState?.theme),
    ], 'Visual'),
    pickFirst([
      normalizeText(params.planDraft?.signatureMoment),
      extractSpecString(params.sourceSpec, 'signature_moment'),
    ], 'Signature'),
  ].filter(Boolean);

  return lines.length > 0 ? lines.join('\n') : null;
}

function pickFirst(values: Array<string | null | undefined>, label: string): string {
  const value = values.find((item) => Boolean(item));
  return value ? `${label}: ${value}` : '';
}

function normalizePlanDraftLike(value: unknown): IntentBuildPlanDraftLike | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const normalized: IntentBuildPlanDraftLike = {
    title: normalizeText(record.title) || null,
    summary: normalizeText(record.summary) || null,
    concept: normalizeText(record.concept) || null,
    interaction: normalizeText(record.interaction) || null,
    objective: normalizeText(record.objective) || null,
    pacing: normalizeText(record.pacing) || null,
    visualDirection: normalizeText(record.visualDirection ?? record.visual_direction) || null,
    signatureMoment: normalizeText(record.signatureMoment ?? record.signature_moment) || null,
  };
  return Object.values(normalized).some((item) => Boolean(item)) ? normalized : null;
}

function normalizePlainObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => normalizeText(item))
      .filter((item): item is string => Boolean(item));
}

function sortStrings(values: string[]): string[] {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function extractSpecString(spec: Record<string, unknown> | null | undefined, key: string): string | null {
  if (!spec) {
    return null;
  }
  return normalizeText(spec[key]) || null;
}

function extractNestedSpecString(
  spec: Record<string, unknown> | null | undefined,
  path: string[],
): string | null {
  if (!spec) {
    return null;
  }
  let current: unknown = spec;
  for (const key of path) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) {
      return null;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return normalizeText(current) || null;
}

function extractCoreMechanicsText(spec: Record<string, unknown> | null | undefined): string | null {
  if (!spec) {
    return null;
  }
  const mechanics = spec.core_mechanics;
  if (!Array.isArray(mechanics)) {
    return null;
  }
  const labels = mechanics
    .map((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) {
        return '';
      }
      const record = item as Record<string, unknown>;
      const type = normalizeText(record.type);
      const input = normalizeText(record.input);
      return [type, input].filter(Boolean).join(' / ');
    })
    .filter(Boolean);
  return labels.length > 0 ? labels.join(', ') : null;
}

function normalizeSlotValue(value: unknown): string | null {
  if (Array.isArray(value)) {
    const normalized = value
      .map((item) => normalizeText(item))
      .filter((item): item is string => Boolean(item));
    return normalized.length > 0 ? normalized.join(', ') : null;
  }
  return normalizeText(value) || null;
}

function normalizeText(value: unknown): string | undefined {
  const normalized = String(value || '').trim();
  return normalized || undefined;
}

function fingerprintValue(value: unknown): string | null {
  if (value == null) {
    return null;
  }
  const serialized = stableSerialize(value);
  return serialized ? createHash('sha256').update(serialized).digest('hex') : null;
}

function stableSerialize(value: unknown): string {
  return JSON.stringify(sortValue(value));
}

function sortValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => sortValue(item));
  }
  if (!value || typeof value !== 'object') {
    return value;
  }
  return Object.keys(value as Record<string, unknown>)
    .sort()
    .reduce<Record<string, unknown>>((acc, key) => {
      acc[key] = sortValue((value as Record<string, unknown>)[key]);
      return acc;
    }, {});
}
