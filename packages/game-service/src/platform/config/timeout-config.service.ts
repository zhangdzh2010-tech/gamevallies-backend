import { Injectable } from '@nestjs/common';
import { TIMEOUT_CONFIG_CATALOG_BY_KEY } from '../../game/catalogs/timeout-catalog';
import { SystemConfigRepository } from './system-config.repository';

@Injectable()
export class TimeoutConfigService {
  private timeoutConfigCache = new Map<string, string>();
  private timeoutConfigLoadedAt = 0;
  private timeoutConfigRefreshPromise: Promise<void> | null = null;

  constructor(private readonly systemConfigRepository: SystemConfigRepository) {}

  isWarm(): boolean {
    return this.timeoutConfigLoadedAt > 0;
  }

  async refresh(): Promise<void> {
    if (!this.timeoutConfigRefreshPromise) {
      this.timeoutConfigRefreshPromise = (async () => {
        const rows = await this.systemConfigRepository.findByCategory('timeout');
        const nextCache = new Map<string, string>();

        for (const row of rows) {
          nextCache.set(row.configKey, row.configValue);
        }

        this.timeoutConfigCache = nextCache;
        this.timeoutConfigLoadedAt = Date.now();
      })().finally(() => {
        this.timeoutConfigRefreshPromise = null;
      });
    }

    await this.timeoutConfigRefreshPromise;
  }

  async ensureLoaded(): Promise<void> {
    if (this.timeoutConfigLoadedAt > 0) {
      return;
    }
    await this.refresh();
  }

  resolveCatalogValue(
    key: string,
    options?: {
      min?: number;
      max?: number;
    },
  ): number {
    const catalogEntry = TIMEOUT_CONFIG_CATALOG_BY_KEY.get(key);
    if (!catalogEntry) {
      throw new Error(`Unknown timeout config key: ${key}`);
    }

    const fallback = catalogEntry.defaultValue;
    const raw = this.timeoutConfigCache.has(key)
      ? this.timeoutConfigCache.get(key)
      : fallback;
    const parseValue = (value: string): number => (
      catalogEntry.valueType === 'float'
        ? Number.parseFloat(value)
        : Number.parseInt(value, 10)
    );
    const parsed = parseValue(String(raw));
    if (!Number.isFinite(parsed)) {
      return parseValue(String(fallback));
    }

    const min = options?.min ?? Number.NEGATIVE_INFINITY;
    const max = options?.max ?? Number.POSITIVE_INFINITY;
    return Math.min(max, Math.max(min, parsed));
  }

  getAiEngineTargetCacheTtlMs(): number {
    return this.resolveCatalogValue('timeout.game_service.ai_target_cache_ttl_ms', { min: 0 });
  }

  getActiveTaskSweepIntervalMs(maxIntervalMs: number): number {
    return Math.min(
      this.resolveCatalogValue('timeout.game_service.active_task_sweep_interval_ms', { min: 1_000 }),
      maxIntervalMs,
    );
  }

  async getSourceSpecParseTimeoutMs(): Promise<number> {
    await this.ensureLoaded();
    const sourceSpecParseRequestMs = this.resolveCatalogValue(
      'timeout.game_service.source_spec_parse_request_ms',
      { min: 1_000 },
    );
    return Math.max(sourceSpecParseRequestMs, 90_000);
  }

  getUpstreamRequestTimeoutMs(): number {
    return this.resolveCatalogValue('timeout.game_service.upstream_request_ms', { min: 1_000 });
  }

  getUpstreamRequestRetryDelayMs(): number {
    return this.resolveCatalogValue('timeout.game_service.upstream_request_retry_delay_ms', { min: 0 });
  }

  getUpstreamSnapshotTimeoutMs(): number {
    return this.resolveCatalogValue('timeout.game_service.upstream_snapshot_ms', { min: 1_000 });
  }

  getUpstreamCancelTimeoutMs(): number {
    return this.resolveCatalogValue('timeout.game_service.upstream_cancel_ms', { min: 1_000 });
  }

  getUpstreamPollIntervalMs(): number {
    return this.resolveCatalogValue('timeout.game_service.upstream_poll_interval_ms', { min: 100 });
  }

  getUpstreamTimeoutBufferS(): number {
    return this.resolveCatalogValue('timeout.game_service.upstream_timeout_buffer_s', { min: 0 });
  }

  getUpstreamDeadlineGraceMs(): number {
    return this.resolveCatalogValue('timeout.game_service.upstream_deadline_grace_ms', { min: 0 });
  }

  resolvePipelineTimeout(rawValue?: unknown): number {
    const fallback = this.resolveCatalogValue('timeout.pipeline.default_s', {
      min: 30,
      max: 3600,
    });
    const parsed = Number.parseInt(String(rawValue ?? fallback), 10);

    if (!Number.isFinite(parsed)) {
      return fallback;
    }

    return Math.min(3600, Math.max(30, parsed));
  }
}
