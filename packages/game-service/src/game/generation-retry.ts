import type { RetryOptions } from './game-service.types';

export class TaskAbortedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TaskAbortedError';
  }
}

export class TaskSupersededError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TaskSupersededError';
  }
}

/** Retry an async operation on transient network/5xx errors. */
export async function withRetry<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {},
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? 2;
  const delayMs = options.delayMs ?? 3000;
  const retryOnHttpResponse = options.retryOnHttpResponse ?? true;
  const retryOnNetworkError = options.retryOnNetworkError ?? true;
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err: any) {
      lastError = err;
      // Timeouts (ECONNABORTED) are retryable: async submissions carry an
      // X-Idempotency-Key, so the AI engine dedupes repeated submits instead
      // of launching a duplicate pipeline job.
      const isHttpRetryable =
        retryOnHttpResponse &&
        Boolean(err?.response) &&
        (err.response.status >= 500 || err.response.status === 429);
      const isNetworkRetryable =
        retryOnNetworkError &&
        !err?.response &&
        Boolean(err?.code);
      const isRetryable = isHttpRetryable || isNetworkRetryable;
      if (!isRetryable || attempt === maxAttempts) break;
      if (options.onRetry) {
        await options.onRetry({
          retry: attempt,
          maxRetries: maxAttempts - 1,
          attempt: attempt + 1,
          maxAttempts,
          error: err,
        });
      }
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  throw lastError;
}
