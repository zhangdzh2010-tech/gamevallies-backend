import { QUALITY_POLICY } from "./generated-quality-policy";
export const QUALITY_POLICY_VERSION = QUALITY_POLICY.version;

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
      minQualityScore: QUALITY_POLICY.tiers.showcase.final_score,
      requireStructuredReview: true,
      minReviewBonus: QUALITY_POLICY.tiers.showcase.min_review_bonus,
    };
  }
  if (normalizedGenerationTier === 'safe') {
    return {
      generationTier: normalizedGenerationTier,
      minQualityScore: QUALITY_POLICY.tiers.safe.final_score,
      requireStructuredReview: false,
    };
  }
  return {
    generationTier: normalizedGenerationTier,
    minQualityScore: QUALITY_POLICY.tiers.standard.final_score,
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
  runtimeProfile?: string;
  runtimeQaReport?: any;
}): void {
  if (params.runtimeProfile === 'interactive_experience') {
    const report = params.runtimeQaReport;
    if (report?.ran !== true || report?.passed !== true || report?.contentChanged !== true
      || !(Number(report?.controlsExercised) > 0)
      || !Array.isArray(report?.issues) || report.issues.length
      || !Array.isArray(report?.viewports) || report.viewports.length < 2
      || report.viewports.some((item: any) => item.horizontalOverflow !== false)) {
      const error = buildCreateQualityGateError({ generationTier: params.generationTier || 'standard', message: 'Desktop interaction checks did not pass' });
      error.failureFamily = 'interactive_validation';
      error.failedStage = 'runtime_simulation_qa';
      throw error;
    }
    const assessment = params.qualityBreakdown as any;
    const kind = assessment?.artifact_kind;
    const rubric = kind === 'tool' ? QUALITY_POLICY.artifact_rubrics.tool
      : kind === 'science' ? QUALITY_POLICY.artifact_rubrics.science : null;
    const score = normalizeQualityScore(params.qualityScore);
    const finiteScore = (v: any) => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 10;
    const metrics = assessment?.scores || {};
    const weighted = rubric ? Object.entries(rubric.weights).reduce((sum, [key, weight]) => sum + metrics[key] * weight, 0) : NaN;
    if (!rubric || assessment?.policy_version !== QUALITY_POLICY_VERSION
      || assessment?.review_ran !== true || assessment?.passed !== true
      || !finiteScore(assessment?.score)
      || !Array.isArray(assessment?.critical_issues) || assessment.critical_issues.length
      || !Object.keys(rubric.weights).every(key => finiteScore(metrics[key]) && typeof assessment?.evidence?.[key] === 'string' && assessment.evidence[key].trim())
      || !Object.entries(rubric.minimums).every(([key, minimum]) => metrics[key] >= minimum)
      || score === null || !Number.isFinite(weighted) || weighted + 0.005 < rubric.pass_score
      || Math.abs(score - weighted) > 0.011 || Math.abs(Number(assessment?.score) - score) > 0.011) {
      throw buildCreateQualityGateError({generationTier: params.generationTier || 'standard',
        message:'Artifact-specific quality assessment is missing, invalid or below threshold'});
    }
    return;
  }
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
