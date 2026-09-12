/** generation payload extracted without changing business rules. */
import {
  createHash,
} from 'crypto';
import {
  GameAccessGrantSource,
  GameStatus,
} from '@prisma/client';
import {
  CreateGameOrientation,
} from './dto';
import {
  buildIntentBuildSnapshot,
} from './intent-build.util';
import type {
  GenerationTier,
  PromptBundleSnapshotPayload,
  RuntimeContractPayload,
  SourceBundleContextPayload,
  IterateExecutionOptions,
} from './game-service.types';
import {
  buildVisibilityModel,
} from './game-access.policy';
import {
  normalizeOptionalString,
  normalizeRequestedGenerationTier,
  normalizeRequestedOrientation,
  resolveRequestedOrientationFromRuntime,
  resolveRequestedPlatform,
  resolveRuntimeOrientationFromContract,
} from './game-runtime.policy';

export function buildCreateIntentVariationSeed(params: {
  userId: string;
  description: string;
  title?: string | null;
  generationTier: GenerationTier;
  entryMode?: string | null;
}): string {
  const fingerprint = JSON.stringify({
    userId: params.userId,
    description: params.description.trim(),
    title: normalizeOptionalString(params.title) || null,
    generationTier: params.generationTier,
    entryMode: normalizeOptionalString(params.entryMode) || 'create',
  });
  return createHash('sha256').update(fingerprint).digest('hex').slice(0, 24);
}


export function buildCreateIntentBuild(params: {
  title?: string | null;
  description: string;
  entryMode?: string | null;
  generationTier: GenerationTier;
  sourceSpec?: Record<string, unknown> | null;
}) {
  return buildIntentBuildSnapshot({
    title: normalizeOptionalString(params.title),
    initialPrompt: params.description,
    entryMode: normalizeOptionalString(params.entryMode) || 'create',
    generationTier: params.generationTier,
    sourceSpec: params.sourceSpec || null,
  });
}


export function buildEntitlementSnapshot(params: {
  canPlay: boolean;
  requireSubscription: boolean;
  accessGrantSource: GameAccessGrantSource | string;
  accessGrantSubscriptionId?: string | null;
  quotaRemaining?: number | null;
  refundOnFailure: boolean;
}) {
  return {
    can_play: params.canPlay,
    require_subscription: params.requireSubscription,
    grant_source: params.accessGrantSource || GameAccessGrantSource.none,
    grant_subscription_id: params.accessGrantSubscriptionId ?? null,
    quota_remaining: params.quotaRemaining ?? null,
    refund_on_failure: params.refundOnFailure,
  };
}


export function extractBundleGameSpec(bundleHistory: any[]): Record<string, unknown> | null {
  for (const bundle of [...bundleHistory].reverse()) {
    const gameSpec = bundle?.metadata?.gameSpec;
    if (
      gameSpec
      && typeof gameSpec === 'object'
      && !Array.isArray(gameSpec)
      && typeof (gameSpec as Record<string, unknown>).game_type === 'string'
      && String((gameSpec as Record<string, unknown>).game_type).trim()
    ) {
      return gameSpec as Record<string, unknown>;
    }
  }
  return null;
}


export function extractBundleOrientation(bundleHistory: any[]): CreateGameOrientation | undefined {
  for (const bundle of [...bundleHistory].reverse()) {
    const metadata = bundle?.metadata && typeof bundle.metadata === 'object'
      ? bundle.metadata as Record<string, unknown>
      : null;
    if (!metadata) {
      continue;
    }

    const requestedOrientation = normalizeRequestedOrientation(
      metadata.requestedOrientation ?? metadata.orientation ?? metadata.requested_orientation,
    );
    if (requestedOrientation) {
      return requestedOrientation;
    }

    const runtimeOrientation = metadata.runtimeOrientation
      ?? metadata.runtime_orientation
      ?? metadata.orientation;
    const recoveredOrientation = resolveRequestedOrientationFromRuntime(runtimeOrientation);
    if (recoveredOrientation) {
      return recoveredOrientation;
    }
  }

  return undefined;
}


export function extractBundleGenerationTier(bundleHistory: any[]): GenerationTier | undefined {
  for (const bundle of [...bundleHistory].reverse()) {
    const metadata = bundle?.metadata && typeof bundle.metadata === 'object'
      ? bundle.metadata as Record<string, unknown>
      : null;
    if (!metadata) {
      continue;
    }

    const generationTier = normalizeRequestedGenerationTier(
      metadata.generationTier ?? metadata.generation_tier,
    );
    if (generationTier) {
      return generationTier;
    }
  }

  return undefined;
}


export function buildIterationSourceBundleContext(params: {
  game: any;
  latestBundle: any | null;
  bundleHistory: any[];
}): SourceBundleContextPayload | null {
  const { game, latestBundle, bundleHistory } = params;
  const latestMetadata = latestBundle?.metadata || {};
  const recentRevisions = [...bundleHistory]
    .slice(-4)
    .reverse()
    .map((bundleVersion: any) => {
      const metadata = bundleVersion?.metadata || {};
      const feedback = typeof metadata.feedback === 'string' ? metadata.feedback.trim() : '';
      const iterationType = typeof metadata.iterationType === 'string' ? metadata.iterationType.trim() : '';
      const summary = [
        feedback || '',
        iterationType ? `type=${iterationType}` : '',
      ].filter(Boolean).join(' | ');

      return {
        version: typeof bundleVersion?.version === 'number' ? bundleVersion.version : null,
        generated_at: bundleVersion?.createdAt ? new Date(bundleVersion.createdAt).toISOString() : null,
        feedback: feedback || null,
        iteration_type: iterationType || null,
        summary: summary || null,
      };
    })
    .filter((revision) => (
      revision.version !== null
      || revision.feedback
      || revision.iteration_type
      || revision.summary
    ));

  const latestFeedback = typeof latestMetadata.feedback === 'string' ? latestMetadata.feedback.trim() : '';
  const latestIterationType = typeof latestMetadata.iterationType === 'string'
    ? latestMetadata.iterationType.trim()
    : '';
  const latestGameType = typeof latestMetadata?.gameSpec?.game_type === 'string'
    && latestMetadata.gameSpec.game_type.trim()
    ? latestMetadata.gameSpec.game_type.trim()
    : (
      typeof game?.gameType === 'string' && game.gameType.trim()
        ? game.gameType.trim()
        : null
    );
  const latestOrientation = extractBundleOrientation([
    ...bundleHistory,
    ...(latestBundle ? [latestBundle] : []),
  ]) ?? null;
  const latestGenerationTier = extractBundleGenerationTier([
    ...bundleHistory,
    ...(latestBundle ? [latestBundle] : []),
  ]) ?? 'standard';
  const summaryParts = [
    latestGameType ? `game_type=${latestGameType}` : '',
    latestGenerationTier ? `generation_tier=${latestGenerationTier}` : '',
    latestOrientation ? `orientation=${latestOrientation}` : '',
    latestFeedback ? `latest_feedback=${latestFeedback}` : '',
    latestIterationType ? `latest_iteration=${latestIterationType}` : '',
    latestBundle?.htmlCode ? `code_size=${Buffer.byteLength(String(latestBundle.htmlCode), 'utf8')}B` : '',
  ].filter(Boolean);

  return {
    title: typeof game?.title === 'string' ? game.title : null,
    latest_bundle_version: typeof latestBundle?.version === 'number'
      ? latestBundle.version
      : (typeof game?.version === 'number' ? game.version : null),
    latest_game_type: latestGameType,
    latest_generation_tier: latestGenerationTier,
    latest_orientation: latestOrientation,
    latest_feedback: latestFeedback || null,
    latest_iteration_type: latestIterationType || null,
    summary: summaryParts.length > 0 ? summaryParts.join('; ') : null,
    recent_revisions: recentRevisions,
  };
}


export function buildIterateV2Payload(params: {
  gameId: string;
  userId: string;
  feedback: string;
  conversationHistory: Array<{ role: string; content: string }>;
  currentCode: string;
  executionRegion: string;
  timeoutS: number;
  taskId?: string;
  game?: IterateExecutionOptions['game'];
  orientation?: CreateGameOrientation;
  generationTier?: GenerationTier;
  sourceSpec?: Record<string, unknown> | null;
  sourceBundleContext?: SourceBundleContextPayload | null;
  promptBundleSnapshot: PromptBundleSnapshotPayload;
  runtimeContract: RuntimeContractPayload;
}): Record<string, unknown> {
  const game = params.game || {};
  const orientation = normalizeRequestedOrientation(params.orientation)
    ?? resolveRequestedOrientationFromRuntime(
      resolveRuntimeOrientationFromContract(params.runtimeContract),
    );
  const generationTier = normalizeRequestedGenerationTier(params.generationTier)
    ?? normalizeRequestedGenerationTier(params.sourceBundleContext?.latest_generation_tier)
    ?? 'standard';

  return {
    game_id: params.gameId,
    user_id: params.userId,
    current_code: params.currentCode,
    generation_tier: generationTier,
    platform: resolveRequestedPlatform({
      description: params.feedback,
      orientation,
    }),
    timeout_s: params.timeoutS,
    task_id: params.taskId,
    request_context: {
      source: 'game-service',
      entrypoint: 'iterate',
      region: params.executionRegion,
      pipeline_version: 'v2',
    },
    iteration_intent: {
      feedback: params.feedback,
      conversation: params.conversationHistory,
    },
    source_spec: params.sourceSpec || null,
    source_bundle_context: params.sourceBundleContext || null,
    existing_game: {
      status: game.status || GameStatus.draft,
      visibility: game.visibility || 'private',
      live_bundle_version: game.version ?? null,
      working_bundle_version: null,
      can_play: Boolean(game.canPlay ?? true),
      require_subscription: Boolean(game.requireSubscription ?? false),
      forked_from: game.forkedFrom ?? null,
    },
    entitlement: buildEntitlementSnapshot({
      canPlay: Boolean(game.canPlay ?? true),
      requireSubscription: Boolean(game.requireSubscription ?? false),
      accessGrantSource: String(game.accessGrantSource || GameAccessGrantSource.none),
      accessGrantSubscriptionId: game.accessGrantSubscriptionId ?? null,
      quotaRemaining: null,
      refundOnFailure: false,
    }),
    visibility_model: buildVisibilityModel({
      status: game.status,
      visibility: game.visibility,
      canPlay: game.canPlay,
    }),
    prompt_bundle_snapshot: params.promptBundleSnapshot,
    runtime_contract: params.runtimeContract,
    // normalized_request retained as minimal fallback for ai-engine lookups
    normalized_request: {
      feedback: params.feedback,
    },
    // single source of truth for adapter + dimensional metadata
    metadata: {
      adapter: 'compat_v1',
      pipeline_version: 'v2',
      generation_tier: generationTier,
      ...(orientation ? { orientation } : {}),
      live_bundle_version: game.version ?? null,
    },
  };
}


export function normalizeTaskMetadataRecord(metadata: unknown): Record<string, any> {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return {};
  }
  return metadata as Record<string, any>;
}


export function normalizeConversationHistory(value: unknown): Array<{ role: string; content: string }> {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .filter((item) => item && typeof item === 'object')
    .map((item: any) => ({
      role: typeof item.role === 'string' ? item.role : '',
      content: typeof item.content === 'string' ? item.content : '',
    }))
    .filter((item) => item.role && item.content);
}


export function deriveTitle(gameSpec: any, description: string): string {
  // Try to extract a meaningful name from the game type
  const typeMap: Record<string, string> = {
    casual: '休闲游戏',
    puzzle: '益智游戏',
    educational: '教育游戏',
    funny: '搞笑游戏',
  };
  const gameType = gameSpec?.game_type || '';
  if (typeMap[gameType]) return typeMap[gameType];
  if (gameType) return gameType.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase());

  // Fallback: extract from first ~15 chars of description
  const desc = (description || '').replace(/^(做|创建|生成|制作|来)(一个|个)/, '').trim();
  if (desc.length > 2) return desc.substring(0, 15).replace(/[，。,.]$/, '');
  return '新游戏';
}
