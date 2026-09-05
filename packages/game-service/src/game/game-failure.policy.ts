/** failure policy extracted without changing business rules. */
import {
  GenerationTaskType,
} from '@prisma/client';
import type {
  GenerationTier,
  FailureContext,
  UpstreamAsyncTaskSnapshot,
  EffectiveTaskStatus,
} from './game-service.types';

export function buildCreateQualityGateError(params: {
  generationTier: GenerationTier;
  message: string;
}): Error & {
  failedStage: string;
  failureFamily: string;
  retryCount: number;
} {
  const error = new Error(params.message) as Error & {
    failedStage: string;
    failureFamily: string;
    retryCount: number;
  };
  error.name = 'CreateQualityGateError';
  error.failedStage = 'code_review';
  error.failureFamily = 'quality_gate';
  error.retryCount = 0;
  return error;
}


export function buildAiGatewayStageError(
  error: unknown,
  fallback: string,
  failedStage: string,
  failureFamily: string,
): Error {
  const wrapped = new Error(extractAiGatewayErrorMessage(error, fallback));
  (wrapped as Error & { failedStage?: string; failureFamily?: string }).failedStage = failedStage;
  (wrapped as Error & { failedStage?: string; failureFamily?: string }).failureFamily = failureFamily;
  return wrapped;
}


export function extractAiGatewayErrorMessage(error: unknown, fallback: string): string {
  const raw = (error as any)?.response?.data?.detail
    ?? (error as any)?.response?.data?.message
    ?? (error as any)?.message
    ?? fallback;
  return stringifyAiGatewayErrorDetail(raw, fallback);
}


export function stringifyAiGatewayErrorDetail(value: unknown, fallback: string): string {
  if (value == null) {
    return fallback;
  }
  if (typeof value === 'string') {
    const normalized = value.trim();
    return normalized || fallback;
  }
  if (Array.isArray(value)) {
    const parts = value
      .map((item) => stringifyAiGatewayErrorDetail(item, ''))
      .map((item) => item.trim())
      .filter(Boolean);
    return parts.join('; ') || fallback;
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    for (const candidate of [record.detail, record.message, record.msg, record.reason, record.error]) {
      const rendered = stringifyAiGatewayErrorDetail(candidate, '');
      if (rendered) {
        return rendered;
      }
    }
    const parts = Object.entries(record)
      .map(([key, item]) => {
        const rendered = stringifyAiGatewayErrorDetail(item, '');
        return rendered ? `${key}=${rendered}` : '';
      })
      .filter(Boolean);
    return parts.join(', ') || fallback;
  }
  const normalized = String(value).trim();
  return normalized || fallback;
}


export function isTimeoutError(error: unknown): boolean {
  const message = extractErrorMessage(error as any);
  return /timed out|timeout|deadline exceeded|ECONNABORTED/i.test(message);
}


export function isTransientUpstreamSnapshotError(error: unknown): boolean {
  if (isTimeoutError(error)) {
    return true;
  }
  const status = Number((error as any)?.response?.status ?? 0);
  if (status >= 500) {
    return true;
  }
  const code = String((error as any)?.code || '').toUpperCase();
  return ['ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EAI_AGAIN'].includes(code);
}


export function isFinalTaskStatus(status?: string | null): status is Exclude<EffectiveTaskStatus, 'queued' | 'running'> {
  return status === 'succeeded' || status === 'failed' || status === 'canceled' || status === 'timed_out';
}


export function isActiveTaskStatus(status?: string | null): status is 'queued' | 'running' {
  return status === 'queued' || status === 'running';
}


export function buildUpstreamTaskFailureError(
  snapshot: UpstreamAsyncTaskSnapshot,
  fallbackStage: string,
): any {
  return {
    response: {
      data: {
        detail: {
          message: snapshot.error?.message || 'Upstream AI task failed',
          failed_stage: snapshot.error?.failed_stage || fallbackStage,
          retry_count: Number(snapshot.error?.retry_count ?? 0) || 0,
          fallback: snapshot.error?.fallback || undefined,
          failure_family: snapshot.error?.failure_family || undefined,
          primary_artifact_id: snapshot.error?.primary_artifact_id || undefined,
        },
      },
    },
  };
}


export function inferFailedStageFromLlmSignal(
  stepKey?: string | null,
  stage?: string | null,
  fallbackStage?: string | null,
): string {
  const normalizedStepKey = String(stepKey || '').trim().toLowerCase();
  const normalizedStage = String(stage || '').trim().toLowerCase();
  if (normalizedStepKey === 'code_generate.full' || normalizedStage === 'code_generating') {
    return 'logic_generate';
  }
  if (normalizedStepKey.startsWith('qa_fix.') || normalizedStage === 'qa_checking') {
    return 'qa_checking';
  }
  return fallbackStage || 'pipeline_run';
}


export function inferFailureFamilyFromLlmSignal(
  failedStage: string,
  stepKey?: string | null,
  errorCode?: string | null,
  errorMessage?: string | null,
): string {
  const normalizedStepKey = String(stepKey || '').trim().toLowerCase();
  const normalizedErrorCode = String(errorCode || '').trim().toLowerCase();
  const normalizedMessage = String(errorMessage || '').trim().toLowerCase();
  if (normalizedStepKey === 'code_generate.full' || failedStage === 'logic_generate') {
    return 'code_generation';
  }
  if (normalizedStepKey.startsWith('qa_fix.') || failedStage === 'qa_checking') {
    return 'qa_validation';
  }
  if (normalizedErrorCode.includes('timeout') || normalizedMessage.includes('timed out')) {
    return 'timeout';
  }
  return 'pipeline';
}


export function buildLlmFailureMessage(signal: {
  stepKey?: string | null;
  errorCode?: string | null;
  errorMessage?: string | null;
}): string {
  const stepKey = String(signal.stepKey || '').trim() || 'llm';
  const rawMessage = String(signal.errorMessage || '').trim();
  if (rawMessage) {
    return `${stepKey} failed: ${rawMessage}`;
  }
  const rawCode = String(signal.errorCode || '').trim();
  if (rawCode) {
    return `${stepKey} failed: ${rawCode}`;
  }
  return `${stepKey} failed before completion`;
}


export function inferFailedStageFromTaskProgress(progressStage?: string | null): string {
  return inferFailedStageFromLlmSignal(undefined, progressStage, progressStage || 'pipeline_run');
}


export function isUpstreamWaitTimeoutError(error: unknown): boolean {
  const message = extractErrorMessage(error as any);
  return /upstream ai task .*timed out while waiting for completion/i.test(message);
}


export function getSuccessArtifactTypes(taskType: GenerationTaskType): string[] {
  return taskType === GenerationTaskType.pipeline_iterate
    ? ['iteration_response']
    : ['pipeline_response'];
}


export function extractErrorMessage(error: any): string {
  const detail = error?.response?.data?.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return detail.message || detail.error || error?.message || 'unknown error';
  }
  return (
    detail ||
    error?.response?.data?.message ||
    error?.message ||
    'unknown error'
  );
}


export function extractFailureContext(error: any): FailureContext {
  const detail = error?.response?.data?.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return {
      message:
        detail.message ||
        detail.error ||
        error?.message ||
        'unknown error',
      failedStage: detail.failed_stage || detail.failedStage || undefined,
      retryCount: Number(detail.retry_count ?? detail.retryCount ?? 0) || 0,
      fallback: detail.fallback || undefined,
      failureFamily: detail.failure_family || detail.failureFamily || undefined,
      primaryArtifactId: detail.primary_artifact_id || detail.primaryArtifactId || undefined,
    };
  }

  return {
    message: extractErrorMessage(error),
    failedStage: typeof error?.failed_stage === 'string'
      ? error.failed_stage
      : (typeof error?.failedStage === 'string' ? error.failedStage : undefined),
    retryCount: 0,
    fallback: undefined,
    failureFamily: typeof error?.failure_family === 'string'
      ? error.failure_family
      : (typeof error?.failureFamily === 'string' ? error.failureFamily : undefined),
    primaryArtifactId: typeof error?.primary_artifact_id === 'string'
      ? error.primary_artifact_id
      : (typeof error?.primaryArtifactId === 'string' ? error.primaryArtifactId : undefined),
  };
}
