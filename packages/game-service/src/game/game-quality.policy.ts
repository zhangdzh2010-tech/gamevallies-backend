/** quality policy extracted without changing business rules. */

import type {
  GenerationTier,
  CreateQualityGate,
} from './game-service.types';
import {
  buildCreateQualityGateError,
} from './game-failure.policy';
import {
  normalizeRequestedGenerationTier,
} from './game-runtime.policy';

export function resolveCreateQualityGate(
  generationTier?: GenerationTier | null,
): CreateQualityGate {
  const normalizedGenerationTier = normalizeRequestedGenerationTier(generationTier) || 'standard';
  if (normalizedGenerationTier === 'showcase') {
    return {
      generationTier: normalizedGenerationTier,
      minQualityScore: 8.5,
      requireStructuredReview: true,
      minReviewBonus: -1.5,
    };
  }
  if (normalizedGenerationTier === 'safe') {
    return {
      generationTier: normalizedGenerationTier,
      minQualityScore: 5.8,
      requireStructuredReview: false,
    };
  }
  return {
    generationTier: normalizedGenerationTier,
    minQualityScore: 6.6,
    requireStructuredReview: false,
  };
}


export function normalizeQualityScore(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}


export function extractQualityBreakdownMetric(
  qualityBreakdown: unknown,
  ...keys: string[]
): number | null {
  if (!qualityBreakdown || typeof qualityBreakdown !== 'object' || Array.isArray(qualityBreakdown)) {
    return null;
  }

  for (const key of keys) {
    const value = normalizeQualityScore((qualityBreakdown as Record<string, unknown>)[key]);
    if (value !== null) {
      return value;
    }
  }

  return null;
}


export function didStructuredReviewRun(qualityBreakdown: unknown): boolean {
  if (!qualityBreakdown || typeof qualityBreakdown !== 'object' || Array.isArray(qualityBreakdown)) {
    return false;
  }

  const rawReviewRan = (qualityBreakdown as Record<string, unknown>).reviewRan
    ?? (qualityBreakdown as Record<string, unknown>).review_ran;
  if (typeof rawReviewRan === 'boolean') {
    return rawReviewRan;
  }
  if (typeof rawReviewRan === 'string') {
    const normalized = rawReviewRan.trim().toLowerCase();
    return normalized === 'true' || normalized === '1' || normalized === 'yes';
  }

  return false;
}


export function assertCreateResultMeetsQualityGate(params: {
  generationTier?: GenerationTier | null;
  qualityScore: unknown;
  qualityBreakdown: unknown;
}): void {
  const gate = resolveCreateQualityGate(params.generationTier);
  const qualityScore = normalizeQualityScore(params.qualityScore);
  const reviewRan = didStructuredReviewRun(params.qualityBreakdown);
  const reviewBonus = extractQualityBreakdownMetric(
    params.qualityBreakdown,
    'review_bonus',
    'reviewBonus',
  );
  const tierLabel = gate.generationTier.charAt(0).toUpperCase() + gate.generationTier.slice(1);

  if (gate.requireStructuredReview && !reviewRan) {
    throw buildCreateQualityGateError({
      generationTier: gate.generationTier,
      message: `${tierLabel} quality gate failed: structured code review did not produce a usable result.`,
    });
  }

  if (qualityScore === null) {
    throw buildCreateQualityGateError({
      generationTier: gate.generationTier,
      message: `${tierLabel} quality gate failed: qualityScore was not produced.`,
    });
  }

  if (qualityScore + Number.EPSILON < gate.minQualityScore) {
    throw buildCreateQualityGateError({
      generationTier: gate.generationTier,
      message: `${tierLabel} quality gate failed: qualityScore ${qualityScore.toFixed(1)} is below required ${gate.minQualityScore.toFixed(1)}.`,
    });
  }

  if (
    gate.minReviewBonus !== undefined
    && reviewBonus !== null
    && reviewBonus + Number.EPSILON < gate.minReviewBonus
  ) {
    throw buildCreateQualityGateError({
      generationTier: gate.generationTier,
      message: `${tierLabel} quality gate failed: structured code review penalty ${reviewBonus.toFixed(1)} is below allowed ${gate.minReviewBonus.toFixed(1)}.`,
    });
  }
}


export function normalizeQaWarnings(value: unknown): Array<Record<string, any>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item) => item && typeof item === 'object') as Array<Record<string, any>>;
}


export function normalizeRuntimeQaReport(value: unknown): Record<string, any> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, any>;
}


export function buildRuntimeQaResultSummary(
  qaWarnings: Array<Record<string, any>>,
  runtimeQaReport?: Record<string, any>,
): Record<string, any> {
  const warningCount = qaWarnings.length;
  const unavailableKind = typeof runtimeQaReport?.unavailableKind === 'string'
    ? runtimeQaReport.unavailableKind
    : undefined;
  const unavailablePhase = typeof runtimeQaReport?.unavailablePhase === 'string'
    ? runtimeQaReport.unavailablePhase
    : undefined;
  const unavailableReason = typeof runtimeQaReport?.unavailableReason === 'string'
    ? runtimeQaReport.unavailableReason
    : undefined;

  return {
    qaWarningCount: warningCount,
    ...(warningCount > 0 ? { qaWarnings } : {}),
    ...(runtimeQaReport ? { runtimeQaReport } : {}),
    ...(unavailableKind || warningCount > 0 ? { runtimeQaUnavailable: Boolean(unavailableKind || warningCount > 0) } : {}),
    ...(unavailableKind ? { runtimeQaUnavailableKind: unavailableKind } : {}),
    ...(unavailablePhase ? { runtimeQaUnavailablePhase: unavailablePhase } : {}),
    ...(unavailableReason ? { runtimeQaUnavailableReason: unavailableReason } : {}),
  };
}
