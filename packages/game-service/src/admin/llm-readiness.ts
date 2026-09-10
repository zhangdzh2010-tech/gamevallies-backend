import { LLM_ROUTING_POLICY } from './generated-llm-routing-policy';

export const LLM_BUSINESS_STAGES = LLM_ROUTING_POLICY.stages.map(stage => ({ ...stage, stepKeys: [...stage.stepKeys] }));
export const LLM_STEP_REQUIRED_CAPABILITIES: Record<string, readonly string[]> = LLM_ROUTING_POLICY.stepCapabilities;

export function capabilityState(raw: unknown) {
  const flags: Record<string, any> = raw && typeof raw === 'object' && !Array.isArray(raw) ? { ...raw } : {};
  const sentinel = LLM_ROUTING_POLICY.legacyUncheckedFlags;
  // The old form serialized every unchecked box, including "verified", as false.
  // Match only that exact default signature. Partial/verified/explicit restrictions survive.
  const legacyUnchecked = Object.keys(flags).length === Object.keys(sentinel).length &&
    Object.entries(sentinel).every(([key, value]) => flags[key] === value);
  return { flags: legacyUnchecked ? { verified: false } as Record<string, any> : flags, legacyUnchecked };
}

export function requiredCapabilities(stepKey: string): readonly string[] {
  let key = stepKey;
  while (key) {
    if (LLM_STEP_REQUIRED_CAPABILITIES[key]) return LLM_STEP_REQUIRED_CAPABILITIES[key];
    key = key.includes('.') ? key.slice(0, key.lastIndexOf('.')) : '';
  }
  return [];
}

export function stepReadiness(model: any, region: string, stepKey: string) {
  const reasons: string[] = [];
  const { flags, legacyUnchecked } = capabilityState(model?.capabilityFlags);
  if (!model?.enabled || !model?.provider?.enabled || model.provider.region !== region) reasons.push('模型或服务未启用，或区域不匹配');
  const unsafe = Array.isArray(flags.unsafe_for_steps) ? flags.unsafe_for_steps : [];
  if (unsafe.some((key: string) => stepKey === key || stepKey.startsWith(key + '.'))) reasons.push('此步骤被明确限制');
  for (const cap of requiredCapabilities(stepKey)) {
    if (flags[cap] === false || unsafe.includes(cap)) reasons.push('能力限制：' + cap);
  }
  return { stepKey, ready: reasons.length === 0, reasons, legacyUnchecked,
    unknownCapabilities: requiredCapabilities(stepKey).filter(cap => flags[cap] !== true && flags[cap] !== false) };
}

export function businessReadiness(stage: { stepKeys: readonly string[] }, models: any[], region: string) {
  const steps = stage.stepKeys.map(stepKey => {
    const candidates = models.map(model => ({ modelId: model?.id, modelName: model?.name || model?.modelId || '模型不存在', ...stepReadiness(model, region, stepKey) }));
    return { stepKey, ready: candidates.some(candidate => candidate.ready), candidates };
  });
  return { ready: steps.every(step => step.ready), steps };
}
