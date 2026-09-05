/** access policy extracted without changing business rules. */
import {
  BadRequestException,
  NotFoundException,
  ForbiddenException,
} from '@nestjs/common';
import {
  GameStatus,
} from '@prisma/client';

export function buildVisibilityModel(params: {
  status?: string | null;
  visibility?: string | null;
  canPlay?: boolean | null;
}) {
  const publishedVisibility = params.visibility || 'private';
  const isPublishedPublic = params.status === GameStatus.published && publishedVisibility === 'public';
  const isPublishedPreviewVisible =
    params.status === GameStatus.published
    && (publishedVisibility === 'public' || publishedVisibility === 'unlisted');

  return {
    public_preview_allowed: isPublishedPreviewVisible,
    // Authors must always be able to review drafts/candidates even when
    // the public play entitlement is still locked.
    author_play_allowed: true,
    public_index_allowed: isPublishedPublic,
    published_visibility: publishedVisibility,
  };
}


export function extractPersistedCoverUrl(metadata: unknown): string | null {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return null;
  }

  const rawCoverUrl = (metadata as Record<string, unknown>).coverUrl
    ?? (metadata as Record<string, unknown>).cover_url;
  return typeof rawCoverUrl === 'string' && rawCoverUrl.trim()
    ? rawCoverUrl.trim()
    : null;
}


export function extractPersistedCoverArtifactId(metadata: unknown): string | null {
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
    return null;
  }

  const rawArtifactId = (metadata as Record<string, unknown>).coverArtifactId
    ?? (metadata as Record<string, unknown>).cover_artifact_id;
  return typeof rawArtifactId === 'string' && rawArtifactId.trim()
    ? rawArtifactId.trim()
    : null;
}


export function getIterationBaseStatus(
  taskOrMetadata?: { metadata?: any } | Record<string, unknown> | null,
  game?: { status?: GameStatus | string | null; publishedAt?: Date | null } | null,
): GameStatus {
  const metadata = taskOrMetadata && typeof taskOrMetadata === 'object' && 'metadata' in taskOrMetadata
    ? (taskOrMetadata as any).metadata
    : taskOrMetadata;
  const rawBaseStatus = metadata && typeof metadata === 'object'
    ? (metadata as any).baseStatus
    : undefined;

  if (
    rawBaseStatus === GameStatus.draft
    || rawBaseStatus === GameStatus.review
    || rawBaseStatus === GameStatus.published
  ) {
    return rawBaseStatus;
  }

  if (game?.status === GameStatus.review) {
    return GameStatus.review;
  }

  if (game?.status === GameStatus.published || game?.publishedAt) {
    return GameStatus.published;
  }

  return GameStatus.draft;
}


export function getInFlightIterationStatus(baseStatus: GameStatus): GameStatus {
  return baseStatus === GameStatus.published ? GameStatus.published : GameStatus.generating;
}


export function isBundlePlayable(bundle: { htmlCode?: string | null } | null | undefined): boolean {
  return typeof bundle?.htmlCode === 'string' && bundle.htmlCode.trim().length > 0;
}


export function isPubliclyVisibleGame(game: {
  status?: string | null;
  visibility?: string | null;
} | null | undefined): boolean {
  return game?.status === GameStatus.published && (game.visibility || 'public') === 'public';
}


export function isPreviewVisibleGame(game: {
  status?: string | null;
  visibility?: string | null;
} | null | undefined): boolean {
  const visibility = game?.visibility || 'public';
  return game?.status === GameStatus.published
    && (visibility === 'public' || visibility === 'unlisted');
}


export function assertPublicPreviewAllowed(game: {
  status?: string | null;
  visibility?: string | null;
}): void {
  if (isPreviewVisibleGame(game)) {
    return;
  }

  throw new NotFoundException('Game not found');
}


export function assertGamePublishable(
  game: { status?: string | null },
  bundle: { htmlCode?: string | null } | null,
): void {
  if (game.status === GameStatus.banned) {
    throw new ForbiddenException('This game is unavailable');
  }
  if (game.status === GameStatus.generating) {
    throw new BadRequestException('Game is still generating');
  }
  if (game.status === GameStatus.failed) {
    throw new BadRequestException('Game generation failed');
  }
  if (!isBundlePlayable(bundle)) {
    throw new BadRequestException('Game bundle is not ready for publishing');
  }
}


export function computeQuotaRemaining(
  quota: { totalFreeQuota: number; usedFreeQuota: number },
  subscription?: { quotaThisPeriod: number; usedThisPeriod: number } | null,
) {
  const freeRemaining = Math.max(quota.totalFreeQuota - quota.usedFreeQuota, 0);
  const subscriptionRemaining = subscription
    ? Math.max(subscription.quotaThisPeriod - subscription.usedThisPeriod, 0)
    : 0;
  return freeRemaining + subscriptionRemaining;
}
