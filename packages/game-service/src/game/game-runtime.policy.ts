/** runtime policy extracted without changing business rules. */
import {
  Prisma,
} from '@prisma/client';
import {
  CreateGameOrientation,
} from './dto';
import type {
  RuntimeOrientation,
  GenerationTier,
  PipelineVersion,
  RuntimeContractPayload,
} from './game-service.types';

export function normalizeOptionalString(value: unknown): string | undefined {
  const normalized = String(value || '').trim();
  return normalized || undefined;
}


export function resolvePipelineVersion(): PipelineVersion {
  // The V1 generation pipeline has been removed; every generation task runs V2.
  return 'v2';
}



export function normalizeRuntimeContractSchema(
  profileId: string,
  rawSchema: Prisma.JsonValue | null | undefined,
): RuntimeContractPayload {
  const schema = typeof rawSchema === 'object' && rawSchema !== null
    ? rawSchema as Record<string, unknown>
    : {};
  const renderContract = typeof schema.renderContract === 'object' && schema.renderContract !== null
    ? schema.renderContract as Record<string, unknown>
    : {};

  return {
    version: '1.0',
    runtime_profile: profileId,
    metadata: {},
    canvas: {
      requires_canvas_2d: renderContract.requiresCanvas2D ?? false,
      allow_webgl: renderContract.allowWebgl ?? !(renderContract.requiresCanvas2D === true),
      must_render_within_ms: renderContract.mustRenderWithinMs ?? 1500,
      orientation: (schema.mobileLayoutContract as Record<string, unknown> | undefined)?.orientation ?? 'portrait_first',
      ui_scale_mode: (schema.mobileLayoutContract as Record<string, unknown> | undefined)?.uiScaleMode ?? 'short_edge',
      target_fps: renderContract.targetFps ?? 60,
    },
    input: toSnakeCaseRecord(schema.inputContract),
    state: toSnakeCaseRecord(schema.stateContract),
    mobile_layout: toSnakeCaseRecord(schema.mobileLayoutContract),
    safety: toSnakeCaseRecord(schema.safetyContract),
    gameplay: toSnakeCaseRecord(schema.gameplayContract),
  };
}


export function toSnakeCaseRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {};
  }

  return Object.entries(value as Record<string, unknown>).reduce<Record<string, unknown>>((acc, [key, entry]) => {
    const snakeKey = key
      .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
      .replace(/-/g, '_')
      .toLowerCase();
    if (Array.isArray(entry)) {
      acc[snakeKey] = entry;
    } else if (entry && typeof entry === 'object') {
      acc[snakeKey] = toSnakeCaseRecord(entry);
    } else {
      acc[snakeKey] = entry;
    }
    return acc;
  }, {});
}


export function resolveRuntimeOrientation(
  orientation?: CreateGameOrientation | null,
): RuntimeOrientation | undefined {
  if (orientation === 'landscape') {
    return 'landscape_first';
  }
  if (orientation === 'portrait') {
    return 'portrait_first';
  }
  return undefined;
}


export function normalizeRequestedOrientation(
  orientation?: unknown,
): CreateGameOrientation | undefined {
  if (orientation === 'landscape') {
    return 'landscape';
  }
  if (orientation === 'portrait') {
    return 'portrait';
  }
  return undefined;
}


export function normalizeRequestedGenerationTier(
  generationTier?: unknown,
): GenerationTier | undefined {
  if (generationTier === 'safe') {
    return 'safe';
  }
  if (generationTier === 'showcase') {
    return 'showcase';
  }
  if (generationTier === 'standard') {
    return 'standard';
  }
  return undefined;
}


export function inferRequestedOrientationFromText(
  description?: string | null,
  title?: string | null,
): CreateGameOrientation | undefined {
  const text = `${title ?? ''} ${description ?? ''}`.trim().toLowerCase();
  if (!text) {
    return undefined;
  }

  const hasLandscapeHint = [
    'landscape',
    'horizontal',
    'wide',
    '16:9',
    '横屏',
    '横版',
    '宽屏',
  ].some((token) => text.includes(token));
  const hasPortraitHint = [
    'portrait',
    'vertical',
    '9:16',
    '竖屏',
    '纵向',
  ].some((token) => text.includes(token));

  if (hasLandscapeHint && !hasPortraitHint) {
    return 'landscape';
  }
  if (hasPortraitHint && !hasLandscapeHint) {
    return 'portrait';
  }
  return undefined;
}


export function resolveRequestedOrientationFromRuntime(
  orientation?: unknown,
): CreateGameOrientation | undefined {
  if (orientation === 'landscape_first') {
    return 'landscape';
  }
  if (orientation === 'portrait_first') {
    return 'portrait';
  }
  return undefined;
}


export function resolveRuntimeOrientationFromContract(
  runtimeContract?: RuntimeContractPayload | null,
): RuntimeOrientation | undefined {
  if (!runtimeContract || typeof runtimeContract !== 'object') {
    return undefined;
  }

  const mobileLayout = runtimeContract.mobile_layout && typeof runtimeContract.mobile_layout === 'object'
    ? runtimeContract.mobile_layout as Record<string, unknown>
    : null;
  const canvas = runtimeContract.canvas && typeof runtimeContract.canvas === 'object'
    ? runtimeContract.canvas as Record<string, unknown>
    : null;
  const metadata = runtimeContract.metadata && typeof runtimeContract.metadata === 'object'
    ? runtimeContract.metadata as Record<string, unknown>
    : null;

  const runtimeOrientation = [
    mobileLayout?.orientation,
    canvas?.orientation,
    metadata?.orientation,
    metadata?.runtimeOrientation,
    metadata?.runtime_orientation,
  ].find((value) => value === 'portrait_first' || value === 'landscape_first');

  return runtimeOrientation as RuntimeOrientation | undefined;
}


export function buildPersistedOrientationMetadata(params: {
  orientation?: CreateGameOrientation | null;
  runtimeContract?: RuntimeContractPayload | null;
}): Record<string, unknown> {
  const requestedOrientation = normalizeRequestedOrientation(params.orientation)
    ?? resolveRequestedOrientationFromRuntime(
      resolveRuntimeOrientationFromContract(params.runtimeContract),
    );
  const runtimeOrientation = resolveRuntimeOrientationFromContract(params.runtimeContract)
    ?? resolveRuntimeOrientation(requestedOrientation);

  return {
    ...(requestedOrientation ? { requestedOrientation } : {}),
    ...(runtimeOrientation ? { runtimeOrientation } : {}),
  };
}


export function buildPersistedGenerationTierMetadata(
  generationTier?: GenerationTier | null,
): Record<string, unknown> {
  const normalizedGenerationTier = normalizeRequestedGenerationTier(generationTier) || 'standard';
  return {
    generationTier: normalizedGenerationTier,
  };
}


export function resolveNextIterationVersion(params: {
  gameVersion?: number | null;
  latestBundle?: { version?: number | null } | null;
  bundleHistory?: Array<{ version?: number | null } | null> | null;
}): number {
  const versions = [
    Number(params.gameVersion || 0),
    Number(params.latestBundle?.version || 0),
    ...((params.bundleHistory || []).map((bundle) => Number(bundle?.version || 0))),
  ].filter((value) => Number.isFinite(value) && value >= 0);

  return Math.max(0, ...versions) + 1;
}


export function applyRequestedCreateOrientation(
  runtimeContract: RuntimeContractPayload,
  orientation?: CreateGameOrientation | null,
): RuntimeContractPayload {
  const runtimeOrientation = resolveRuntimeOrientation(orientation);
  if (!runtimeOrientation) {
    return runtimeContract;
  }

  const canvas = runtimeContract.canvas && typeof runtimeContract.canvas === 'object'
    ? runtimeContract.canvas as Record<string, unknown>
    : {};
  const mobileLayout = runtimeContract.mobile_layout && typeof runtimeContract.mobile_layout === 'object'
    ? runtimeContract.mobile_layout as Record<string, unknown>
    : {};
  const metadata = runtimeContract.metadata && typeof runtimeContract.metadata === 'object'
    ? runtimeContract.metadata as Record<string, unknown>
    : {};

  return {
    ...runtimeContract,
    canvas: {
      ...canvas,
      orientation: runtimeOrientation,
    },
    mobile_layout: {
      ...mobileLayout,
      orientation: runtimeOrientation,
    },
    metadata: {
      ...metadata,
      requested_orientation: orientation,
      orientation: runtimeOrientation,
    },
  };
}



export function normalizeAiEngineBaseUrl(value?: string | null): string {
  return (value || '').trim().replace(/\/$/, '');
}


export function ensurePersistableGeneratedHtml(htmlCode: string): string {
  const normalized = (htmlCode || '').trim();
  if (!normalized) {
    throw new Error('AI pipeline returned empty HTML output');
  }

  const lower = normalized.toLowerCase();
  if (!lower.includes('<html') || !lower.includes('<body') || !lower.includes('</html>')) {
    throw new Error('AI pipeline returned incomplete HTML output');
  }

  return htmlCode;
}


export function getPersistedUpstreamBaseUrl(task: { metadata?: any } | null | undefined): string {
  const metadata = task?.metadata;
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return '';
  }
  return normalizeAiEngineBaseUrl((metadata as Record<string, unknown>).upstreamBaseUrl as string | undefined);
}
