import { FeedService } from '../../../feed-service/dist/feed/feed.service';
import {
  Injectable,
  Optional,
  NotFoundException,
  BadRequestException,
  BadGatewayException,
} from "@nestjs/common";
import { ConfigService } from "@nestjs/config";
import { PrismaService } from "../prisma/prisma.service";
import {
  GameStatus,
  GenerationTaskStatus,
  InteractionAction,
  InteractionTargetType,
  Prisma,
  SubscriptionOrderStatus,
  SubscriptionPeriod,
  UserRole,
  UserSubscriptionStatus,
} from "@prisma/client";
import { randomUUID } from "crypto";
import * as bcrypt from "bcryptjs";
import axios from "axios";
import { GameService } from "../game/game.service";
import promptCatalog from "../game/catalogs/prompt-catalog.json";
import promptBundleCatalog from "../game/catalogs/prompt-bundle-catalog.json";
import runtimeProfileCatalog from "../game/catalogs/runtime-profile-catalog.json";
import promptPipelineCatalog from "../game/catalogs/prompt-pipeline-catalog.json";
import {
  TIMEOUT_CONFIG_CATALOG,
  TIMEOUT_CONFIG_CATALOG_BY_KEY,
} from "../game/catalogs/timeout-catalog";
import { normalizeGameType } from "../game/game-type-catalog";
import { normalizeIntentBuildSnapshot } from "../game/intent-build.util";
import {
  expandBusinessTimeoutConfigUpdates,
  getBusinessTimeoutConfig,
  isBusinessTimeoutConfigKey,
  listBusinessTimeoutConfigs,
} from "./timeout-admin-catalog";

interface LegacyPreviewBackfillOptions {
  limit?: number;
  dryRun?: boolean | string;
  gameIds?: string[] | string;
}

interface GameCoverBackfillOptions {
  limit?: number;
  dryRun?: boolean | string;
  gameIds?: string[] | string;
  overwriteExisting?: boolean | string;
}

interface AdminGameCoverUpdateInput {
  imageDataUrl?: string;
  imageUrl?: string;
  fileName?: string;
}

interface LlmProviderCatalogConfig {
  mode: "auto" | "custom";
  apiUrl: string;
  authMode: "inherit_provider" | "bearer_token";
  apiKey: string;
}

interface NormalizedLlmProviderExtraConfig {
  vendorPreset: string;
  modelCatalog: LlmProviderCatalogConfig;
  contextWindow: number | null;
  maxTokens: number | null;
  [key: string]: any;
}

interface DashboardDateRange {
  from: Date | null;
  to: Date | null;
}

interface PromptCatalogEntry {
  key: string;
  description?: string;
  value?: string;
}

interface PromptBundleCatalogEntry {
  id: string;
  version: number;
  status?: string;
  productPolicy?: string;
  lockedContractOverride?: string | null;
  repairPlaybook?: string;
  profileOverrides?: any;
  metadata?: any;
}

interface RuntimeProfileCatalogEntry {
  id: string;
  displayName?: string;
  enabled?: boolean;
  skeletonVersion?: string;
  contractSchema?: any;
  fewShotPrompt?: string | null;
  metadata?: any;
}

interface PromptPipelineSupportCatalog {
  kind: string;
  title: string;
  description?: string;
}

interface PromptPipelineSectionCatalog {
  id: string;
  stepNumber?: number;
  tag?: string;
  title: string;
  description?: string;
  color?: string;
  bg?: string;
  border?: string;
  promptKeys: string[];
  support?: PromptPipelineSupportCatalog[];
}

interface PromptPipelineCatalog {
  steps: PromptPipelineSectionCatalog[];
  extras?: PromptPipelineSectionCatalog[];
  itemMeta?: Record<
    string,
    { displayName?: string; variables?: string[]; note?: string }
  >;
}

interface LlmStepFlowMeta {
  flowGroup: string;
  flowOrder: number;
  flowSummary: string;
  triggerSummary: string;
  journeys: string[];
  journeySummary: string;
  optional?: boolean;
}

interface LlmStepOutputMeta {
  outputClass: string;
  minOutputTokens: number | null;
  maxOutputTokens: number | null;
  outputSummary: string;
}

const DEFAULT_PROMPT_CATALOG: PromptCatalogEntry[] = Array.isArray(
  promptCatalog,
)
  ? (promptCatalog as PromptCatalogEntry[])
  : [];
const DEFAULT_PROMPT_BUNDLE_CATALOG: PromptBundleCatalogEntry[] = Array.isArray(
  promptBundleCatalog,
)
  ? (promptBundleCatalog as PromptBundleCatalogEntry[])
  : [];
const DEFAULT_RUNTIME_PROFILE_CATALOG: RuntimeProfileCatalogEntry[] =
  Array.isArray(runtimeProfileCatalog)
    ? (runtimeProfileCatalog as RuntimeProfileCatalogEntry[])
    : [];
const PROMPT_PIPELINE_CATALOG: PromptPipelineCatalog =
  promptPipelineCatalog &&
  typeof promptPipelineCatalog === "object" &&
  !Array.isArray(promptPipelineCatalog)
    ? (promptPipelineCatalog as PromptPipelineCatalog)
    : { steps: [], extras: [], itemMeta: {} };
const PROMPT_PIPELINE_ITEM_META = PROMPT_PIPELINE_CATALOG.itemMeta || {};
const PROMPT_CATALOG_BY_KEY = new Map(
  DEFAULT_PROMPT_CATALOG.map((entry) => [entry.key, entry]),
);
const LLM_STEP_FLOW_META: Record<string, LlmStepFlowMeta> = {
  intent_parse: {
    flowGroup: "Flow 02 - Structured Intent",
    flowOrder: 30,
    flowSummary: "Create + iterate spec compilation",
    triggerSummary: "Used by direct create, iterate, and sourceSpec backfill",
    journeys: ["direct_create", "session_generate", "iterate"],
    journeySummary: "Direct create + session generate + iterate",
  },
  "code_generate.full": {
    flowGroup: "Flow 03 - Create Generation",
    flowOrder: 40,
    flowSummary: "Create primary generation",
    triggerSummary: "Main create path full HTML generation",
    journeys: ["direct_create", "session_generate"],
    journeySummary: "Create only",
  },
  code_review: {
    flowGroup: "Flow 03 - Create Generation",
    flowOrder: 50,
    flowSummary: "Create review pass",
    triggerSummary:
      "Runs after successful create candidates; failures degrade the quality score instead of blocking delivery by themselves",
    journeys: ["direct_create", "session_generate"],
    journeySummary: "Create only",
    optional: true,
  },
  "iterate.classify": {
    flowGroup: "Flow 04 - Iterate Generation",
    flowOrder: 70,
    flowSummary: "Iterate entry classification",
    triggerSummary: "Chooses param / element / mechanic path",
    journeys: ["iterate"],
    journeySummary: "Iterate only",
  },
  "iterate.param_adjust": {
    flowGroup: "Flow 04 - Iterate Generation",
    flowOrder: 80,
    flowSummary: "Iterate parameter patch",
    triggerSummary: "Used for numeric and tuning changes",
    journeys: ["iterate"],
    journeySummary: "Iterate only",
  },
  "iterate.element_change": {
    flowGroup: "Flow 04 - Iterate Generation",
    flowOrder: 90,
    flowSummary: "Iterate element patch",
    triggerSummary: "Used for entity / UI / visual structure changes",
    journeys: ["iterate"],
    journeySummary: "Iterate only",
  },
  "iterate.mechanic_change": {
    flowGroup: "Flow 04 - Iterate Generation",
    flowOrder: 100,
    flowSummary: "Iterate mechanic rewrite",
    triggerSummary: "Used for larger gameplay logic changes",
    journeys: ["iterate"],
    journeySummary: "Iterate only",
  },
  "qa_fix.syntax_structural": {
    flowGroup: "Flow 05 - QA Repair Families",
    flowOrder: 110,
    flowSummary: "Syntax-only QA repair",
    triggerSummary:
      "Full-document syntax and structural repair for invalid HTML / JS output",
    journeys: ["direct_create", "session_generate", "iterate"],
    journeySummary: "Create + iterate",
  },
};

const LLM_STEP_OUTPUT_META: Record<string, LlmStepOutputMeta> = {
  intent_parse: {
    outputClass: "small_json",
    minOutputTokens: null,
    maxOutputTokens: 1024,
    outputSummary: "Small JSON spec extraction",
  },
  "code_generate.full": {
    outputClass: "full_document",
    minOutputTokens: 16384,
    maxOutputTokens: 16384,
    outputSummary: "Full single-file HTML generation",
  },
  code_review: {
    outputClass: "small_text",
    minOutputTokens: null,
    maxOutputTokens: 1024,
    outputSummary: "Small structured quality review",
  },
  "iterate.classify": {
    outputClass: "small_text",
    minOutputTokens: null,
    maxOutputTokens: 1024,
    outputSummary: "Small iteration intent classification",
  },
  "iterate.param_adjust": {
    outputClass: "large_patch",
    minOutputTokens: 4096,
    maxOutputTokens: 16384,
    outputSummary: "Large patch or rewrite",
  },
  "iterate.element_change": {
    outputClass: "large_patch",
    minOutputTokens: 4096,
    maxOutputTokens: 16384,
    outputSummary: "Large patch or rewrite",
  },
  "iterate.mechanic_change": {
    outputClass: "large_patch",
    minOutputTokens: 4096,
    maxOutputTokens: 16384,
    outputSummary: "Large patch or rewrite",
  },
  "qa_fix.syntax_structural": {
    outputClass: "full_document",
    minOutputTokens: 16384,
    maxOutputTokens: 16384,
    outputSummary: "Full-document syntax and structural repair",
  },
};

const LLM_STEP_REQUIRED_CAPABILITIES: Record<string, string[]> = {
  "code_generate.full": ["supports_full_html_rewrite"],
  "iterate.mechanic_change": ["supports_patch_generation"],
  "iterate.element_change": ["supports_patch_generation"],
  "iterate.param_adjust": ["supports_patch_generation"],
  "qa_fix.syntax_structural": ["supports_full_html_rewrite"],
  intent_parse: ["supports_dialogue"],
  "iterate.classify": ["supports_dialogue"],
};

@Injectable()
export class AdminService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly configService: ConfigService,
    private readonly gameService: GameService,
    @Optional() private readonly localFeedService?: FeedService,
  ) {}

  private getFallbackAiEngineAdminBaseUrl(): string {
    return this.configService
      .get<string>("AI_ENGINE_URL", "http://localhost:8000")
      .replace(/\/$/, "");
  }

  private getPublicBaseUrl(): string {
    const publicApiBaseUrl = this.configService.get<string>(
      "PUBLIC_API_BASE_URL",
    );
    const appUrl = this.configService.get<string>(
      "APP_URL",
      "http://localhost:3002",
    );
    return (publicApiBaseUrl || appUrl).replace(/\/$/, "");
  }

  private getPublicApiBaseUrl(): string {
    const baseUrl = this.getPublicBaseUrl();

    try {
      const parsed = new URL(baseUrl);
      const normalizedPath = (parsed.pathname || "").replace(/\/$/, "");
      parsed.pathname = normalizedPath.endsWith("/api/v1")
        ? normalizedPath
        : `${normalizedPath}/api/v1`.replace(/\/{2,}/g, "/");
      return parsed.toString().replace(/\/$/, "");
    } catch {
      return baseUrl.endsWith("/api/v1") ? baseUrl : `${baseUrl}/api/v1`;
    }
  }

  private buildPublicCoverUrl(gameId: string, version?: number | null): string {
    const coverUrl = new URL(
      `${this.getPublicApiBaseUrl()}/games/${gameId}/cover`,
    );
    if (
      typeof version === "number" &&
      Number.isFinite(version) &&
      version > 0
    ) {
      coverUrl.searchParams.set("v", String(version));
    }
    return coverUrl.toString();
  }

  private resolveAdminGameCoverUrl(game: any): string | null {
    if (typeof game?.thumbnailUrl === "string" && game.thumbnailUrl.trim()) {
      return game.thumbnailUrl.trim();
    }

    const latestBundle = Array.isArray(game?.bundles) ? game.bundles[0] : null;
    const metadata = latestBundle?.metadata;
    if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
      return null;
    }

    const rawCoverUrl =
      (metadata as Record<string, unknown>).coverUrl ??
      (metadata as Record<string, unknown>).cover_url;
    if (typeof rawCoverUrl === "string" && rawCoverUrl.trim()) {
      return rawCoverUrl.trim();
    }

    const hasCoverArtifact =
      typeof (
        (metadata as Record<string, unknown>).coverArtifactId ??
        (metadata as Record<string, unknown>).cover_artifact_id
      ) === "string";
    const hasCoverTask =
      typeof (
        (metadata as Record<string, unknown>).coverTaskId ??
        (metadata as Record<string, unknown>).cover_task_id
      ) === "string";

    if (hasCoverArtifact || hasCoverTask) {
      return this.buildPublicCoverUrl(
        game.id,
        latestBundle?.version ?? game.version ?? null,
      );
    }

    return null;
  }

  private attachAdminPreviewTokenToCoverUrl(
    gameId: string,
    status: string | null | undefined,
    coverUrl: string | null,
    previewUrl: string,
  ): string | null {
    if (!coverUrl || status === GameStatus.published) {
      return coverUrl;
    }

    try {
      const parsedCoverUrl = new URL(coverUrl, this.getPublicBaseUrl());
      if (!parsedCoverUrl.pathname.endsWith(`/games/${gameId}/cover`)) {
        return coverUrl;
      }

      const parsedPreviewUrl = new URL(previewUrl, this.getPublicBaseUrl());
      const previewToken = parsedPreviewUrl.searchParams.get("previewToken");
      if (!previewToken || parsedCoverUrl.searchParams.has("previewToken")) {
        return parsedCoverUrl.toString();
      }

      parsedCoverUrl.searchParams.set("previewToken", previewToken);
      return parsedCoverUrl.toString();
    } catch {
      return coverUrl;
    }
  }

  private presentAdminGame(game: any) {
    const adminPreviewUrls = this.gameService.buildAdminPreviewUrls(game.id);
    return {
      ...game,
      gameType: normalizeGameType(
        game.gameType,
        game.title,
        game.description,
        game.tags,
      ),
      coverUrl: this.attachAdminPreviewTokenToCoverUrl(
        game.id,
        game.status,
        this.resolveAdminGameCoverUrl(game),
        adminPreviewUrls.previewUrl,
      ),
      ...adminPreviewUrls,
    };
  }

  private normalizeGameIds(ids: unknown): string[] {
    const rawItems = Array.isArray(ids)
      ? ids
      : typeof ids === "string"
        ? ids.split(",")
        : [];
    const normalized = rawItems
      .map((item) =>
        typeof item === "string" ? item.trim() : String(item || "").trim(),
      )
      .filter(Boolean);
    return Array.from(new Set(normalized));
  }

  private normalizeUserIds(ids: unknown): string[] {
    const rawItems = Array.isArray(ids)
      ? ids
      : typeof ids === "string"
        ? ids.split(",")
        : [];
    const normalized = rawItems
      .map((item) =>
        typeof item === "string" ? item.trim() : String(item || "").trim(),
      )
      .filter(Boolean);
    return Array.from(new Set(normalized));
  }

  private normalizeLlmFallbackProviderIds(ids: unknown): string[] {
    const rawItems = Array.isArray(ids)
      ? ids
      : typeof ids === "string"
        ? ids.split(",")
        : [];
    const normalized = rawItems
      .map((item) =>
        typeof item === "string" ? item.trim() : String(item || "").trim(),
      )
      .filter(Boolean);
    return Array.from(new Set(normalized));
  }

  private normalizeManualCoverUrl(rawValue: unknown): string | null {
    if (typeof rawValue !== "string" || !rawValue.trim()) {
      return null;
    }

    const normalized = rawValue.trim();
    try {
      const parsed = new URL(normalized);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        throw new Error("unsupported protocol");
      }
      return parsed.toString();
    } catch {
      throw new BadRequestException("imageUrl must be a valid http(s) URL");
    }
  }

  private decodeManualCoverImageDataUrl(rawValue: unknown): {
    payload: string;
    contentType: string;
    sizeBytes: number;
  } | null {
    if (typeof rawValue !== "string" || !rawValue.trim()) {
      return null;
    }

    const trimmed = rawValue.trim();
    const matched = trimmed.match(
      /^data:(image\/[a-zA-Z0-9.+-]+);base64,([a-zA-Z0-9+/=]+)$/,
    );
    if (!matched) {
      throw new BadRequestException("imageDataUrl must be a base64 data URL");
    }

    const [, contentType, base64Payload] = matched;
    const allowedContentTypes = new Set([
      "image/jpeg",
      "image/png",
      "image/webp",
      "image/avif",
      "image/gif",
    ]);
    if (!allowedContentTypes.has(contentType)) {
      throw new BadRequestException(
        `Unsupported cover content type: ${contentType}`,
      );
    }

    let buffer: Buffer;
    try {
      buffer = Buffer.from(base64Payload, "base64");
    } catch {
      throw new BadRequestException(
        "imageDataUrl contains invalid base64 payload",
      );
    }

    if (!buffer.length) {
      throw new BadRequestException("imageDataUrl payload is empty");
    }

    const maxBytes = 5 * 1024 * 1024;
    if (buffer.length > maxBytes) {
      throw new BadRequestException("Cover image must be 5 MB or smaller");
    }

    return {
      payload: buffer.toString("base64"),
      contentType,
      sizeBytes: buffer.length,
    };
  }

  private async resolveGameCoverTargetBundle(game: {
    id: string;
    status: GameStatus;
    version: number;
    bundles?: Array<{
      id: string;
      version: number;
      metadata: Prisma.JsonValue;
    }>;
  }) {
    let bundle = game.bundles?.[0] || null;

    if (
      game.status === GameStatus.published &&
      Number.isFinite(game.version) &&
      Number(game.version) > 0
    ) {
      const liveVersion = Number(game.version);
      if (!bundle || bundle.version !== liveVersion) {
        bundle = await this.prisma.gameBundle.findFirst({
          where: {
            gameId: game.id,
            version: liveVersion,
          },
          select: {
            id: true,
            version: true,
            metadata: true,
          },
        });
      }
    }

    if (!bundle) {
      throw new NotFoundException("Game bundle not found");
    }

    return bundle;
  }

  private validateBatchStatus(status: string): "published" | "draft" {
    if (status === "published" || status === "draft") {
      return status;
    }
    throw new BadRequestException("status must be published or draft");
  }

  private validateUserRole(role: string): UserRole {
    if (
      role === "user" ||
      role === "creator" ||
      role === "moderator" ||
      role === "admin"
    ) {
      return role;
    }
    throw new BadRequestException(
      "role must be one of user, creator, moderator, admin",
    );
  }

  private normalizeUserAuthProvider(
    value?: string | null,
  ): "all" | "email" | "phone" | "wechat" {
    const normalized = String(value || "")
      .trim()
      .toLowerCase();
    if (!normalized || normalized === "all") {
      return "all";
    }
    if (
      normalized === "email" ||
      normalized === "phone" ||
      normalized === "wechat"
    ) {
      return normalized;
    }
    throw new BadRequestException(
      "authProvider must be one of all, email, phone, wechat",
    );
  }

  private async recalculateForkDepths(
    tx: Prisma.TransactionClient,
    rootGameIds: string[],
  ) {
    let currentParentIds = Array.from(new Set(rootGameIds.filter(Boolean)));
    let nextDepth = 1;

    while (currentParentIds.length) {
      const childGames = await tx.game.findMany({
        where: { forkedFrom: { in: currentParentIds } },
        select: { id: true },
      });
      if (!childGames.length) {
        return;
      }

      const childIds = childGames.map((item) => item.id);
      await tx.game.updateMany({
        where: { id: { in: childIds } },
        data: { forkDepth: nextDepth },
      });

      currentParentIds = childIds;
      nextDepth += 1;
    }
  }

  private async deleteUsersByIds(userIds: string[]) {
    const normalizedIds = this.normalizeUserIds(userIds);
    if (!normalizedIds.length) {
      throw new BadRequestException("ids must be a non-empty array");
    }

    return this.prisma.$transaction(async (tx) => {
      const existingUsers = await tx.user.findMany({
        where: { id: { in: normalizedIds } },
        select: { id: true },
      });
      if (existingUsers.length !== normalizedIds.length) {
        const existingSet = new Set(existingUsers.map((item) => item.id));
        const missingIds = normalizedIds.filter((id) => !existingSet.has(id));
        throw new NotFoundException(`User not found: ${missingIds.join(", ")}`);
      }

      const authoredGames = await tx.game.findMany({
        where: { authorId: { in: normalizedIds } },
        select: { id: true, forkedFrom: true },
      });
      const authoredGameIds = authoredGames.map((item) => item.id);
      const forkParentIds = Array.from(
        new Set(
          authoredGames
            .map((item) => item.forkedFrom)
            .filter(
              (value): value is string =>
                Boolean(value) && !authoredGameIds.includes(value as string),
            ),
        ),
      );

      const affectedFollowRelations = await tx.userFollow.findMany({
        where: {
          OR: [
            { followerId: { in: normalizedIds } },
            { followingId: { in: normalizedIds } },
          ],
        },
        select: {
          followerId: true,
          followingId: true,
        },
      });
      const affectedUserIdsToRecount = Array.from(
        new Set(
          affectedFollowRelations.flatMap((relation) => [
            relation.followerId,
            relation.followingId,
          ]),
        ),
      ).filter((id) => !normalizedIds.includes(id));

      const likedGameInteractions = await tx.socialInteraction.findMany({
        where: {
          userId: { in: normalizedIds },
          targetType: InteractionTargetType.game,
          action: InteractionAction.like,
        },
        select: { targetId: true },
      });
      const likedGameIdsToRecount = Array.from(
        new Set(
          likedGameInteractions
            .map((item) => item.targetId)
            .filter((id) => !authoredGameIds.includes(id)),
        ),
      );

      const commentsToDelete = await tx.comment.findMany({
        where: {
          OR: [
            { userId: { in: normalizedIds } },
            ...(authoredGameIds.length
              ? [{ gameId: { in: authoredGameIds } }]
              : []),
          ],
        },
        select: { id: true, gameId: true },
      });
      const commentIds = commentsToDelete.map((item) => item.id);
      const commentGameIdsToRecount = Array.from(
        new Set(
          commentsToDelete
            .map((item) => item.gameId)
            .filter((id) => !authoredGameIds.includes(id)),
        ),
      );

      if (commentIds.length) {
        await tx.comment.updateMany({
          where: { parentId: { in: commentIds } },
          data: { parentId: null },
        });
      }

      const interactionTargets: Prisma.SocialInteractionWhereInput[] = [
        { userId: { in: normalizedIds } },
        {
          targetType: InteractionTargetType.user,
          targetId: { in: normalizedIds },
        },
      ];
      if (authoredGameIds.length) {
        interactionTargets.push({
          targetType: InteractionTargetType.game,
          targetId: { in: authoredGameIds },
        });
      }
      if (commentIds.length) {
        interactionTargets.push({
          targetType: InteractionTargetType.comment,
          targetId: { in: commentIds },
        });
      }

      await tx.socialInteraction.deleteMany({
        where: { OR: interactionTargets },
      });

      await tx.notification.deleteMany({
        where: {
          OR: [
            { userId: { in: normalizedIds } },
            { actorId: { in: normalizedIds } },
            { targetId: { in: normalizedIds } },
            ...(authoredGameIds.length
              ? [{ targetId: { in: authoredGameIds } }]
              : []),
            ...(commentIds.length ? [{ targetId: { in: commentIds } }] : []),
          ],
        },
      });

      await tx.creatorEarning.deleteMany({
        where: {
          OR: [
            { creatorId: { in: normalizedIds } },
            ...(authoredGameIds.length
              ? [{ gameId: { in: authoredGameIds } }]
              : []),
          ],
        },
      });

      if (commentIds.length) {
        await tx.comment.deleteMany({
          where: { id: { in: commentIds } },
        });
      }

      if (authoredGameIds.length) {
        const survivingForkRoots = await tx.game.findMany({
          where: {
            forkedFrom: { in: authoredGameIds },
            id: { notIn: authoredGameIds },
          },
          select: { id: true },
        });

        await tx.game.updateMany({
          where: {
            forkedFrom: { in: authoredGameIds },
            id: { notIn: authoredGameIds },
          },
          data: { forkedFrom: null, forkDepth: 0 },
        });

        await this.recalculateForkDepths(
          tx,
          survivingForkRoots.map((item) => item.id),
        );

        await tx.game.deleteMany({
          where: { id: { in: authoredGameIds } },
        });
      }

      await tx.user.deleteMany({
        where: { id: { in: normalizedIds } },
      });

      await Promise.all(
        affectedUserIdsToRecount.map(async (userId) => {
          const [followerCount, followingCount] = await Promise.all([
            tx.userFollow.count({ where: { followingId: userId } }),
            tx.userFollow.count({ where: { followerId: userId } }),
          ]);
          await tx.user.update({
            where: { id: userId },
            data: {
              followerCount,
              followingCount,
            },
          });
        }),
      );

      await Promise.all(
        commentGameIdsToRecount.map(async (gameId) => {
          const commentCount = await tx.comment.count({ where: { gameId } });
          await tx.game.update({
            where: { id: gameId },
            data: { commentCount },
          });
        }),
      );

      await Promise.all(
        forkParentIds.map(async (gameId) => {
          const forkCount = await tx.game.count({
            where: { forkedFrom: gameId },
          });
          await tx.game.update({
            where: { id: gameId },
            data: { forkCount },
          });
        }),
      );

      await Promise.all(
        likedGameIdsToRecount.map(async (gameId) => {
          const likeCount = await tx.socialInteraction.count({
            where: {
              targetType: InteractionTargetType.game,
              targetId: gameId,
              action: InteractionAction.like,
            },
          });
          await tx.game.update({
            where: { id: gameId },
            data: { likeCount },
          });
        }),
      );

      return {
        deleted: existingUsers.length,
        ids: normalizedIds,
      };
    });
  }

  private normalizeExecutionRegion(rawValue?: string | null): string {
    return "cn_shanghai";
  }

  private getConfiguredAiEngineAdminBaseUrlForRegion(
    executionRegion?: string | null,
  ): string {
    const normalizedRegion = this.normalizeExecutionRegion(executionRegion);
    const defaultRegion = this.normalizeExecutionRegion(
      this.configService.get<string>("AI_ENGINE_DEFAULT_REGION") ||
        this.configService.get<string>("SERVICE_REGION") ||
        "cn_shanghai",
    );

    const regionSpecificUrl = this.configService.get<string>(
      "AI_ENGINE_URL_CN_SHANGHAI",
      "",
    );
    const normalizedSpecificUrl = (regionSpecificUrl || "")
      .trim()
      .replace(/\/$/, "");
    if (normalizedSpecificUrl) {
      return normalizedSpecificUrl;
    }

    if (defaultRegion === normalizedRegion) {
      return this.getFallbackAiEngineAdminBaseUrl();
    }

    return "";
  }

  private getDefaultExecutionRegion(): string {
    return "cn_shanghai";
  }

  private async getAiEngineAdminBaseUrls(
    regionTargetId?: string,
  ): Promise<string[]> {
    const urls: string[] = [];
    const appendUrl = (value?: string | null) => {
      const normalized = (value || "").trim().replace(/\/$/, "");
      if (normalized && !urls.includes(normalized)) {
        urls.push(normalized);
      }
    };

    if (regionTargetId) {
      const target = await this.prisma.aiEngineRegionTarget
        .findUnique({
          where: { id: regionTargetId },
          select: {
            executionRegion: true,
            aiEngineUrl: true,
            deployEnabled: true,
            deployStatus: true,
          },
        })
        .catch(() => null);

      appendUrl(
        this.getConfiguredAiEngineAdminBaseUrlForRegion(
          target?.executionRegion,
        ),
      );
      if (target?.deployEnabled && target?.deployStatus === "deployed") {
        appendUrl(target.aiEngineUrl);
      }
    } else {
      appendUrl(this.getConfiguredAiEngineAdminBaseUrlForRegion("cn_shanghai"));

      const targets = await this.prisma.aiEngineRegionTarget
        .findMany({
          where: {
            executionRegion: "cn_shanghai",
            deployEnabled: true,
            deployStatus: "deployed",
            aiEngineUrl: { not: null },
          },
          select: {
            aiEngineUrl: true,
          },
        })
        .catch(() => []);

      for (const target of targets) {
        appendUrl(target.aiEngineUrl);
      }
    }

    if (urls.length > 0) {
      return urls;
    }

    const fallback = this.getFallbackAiEngineAdminBaseUrl();
    return fallback ? [fallback] : [];
  }

  private getAdminToken(): string {
    const token = (process.env.ADMIN_TOKEN || "").trim();
    if (!token) {
      throw new BadRequestException("ADMIN_TOKEN is not configured");
    }
    return token;
  }

  private getOptionalAdminToken(): string | null {
    const token = (process.env.ADMIN_TOKEN || "").trim();
    return token || null;
  }

  private parseDashboardDateRange(
    from?: string,
    to?: string,
  ): DashboardDateRange {
    const parseValue = (value?: string, label?: string) => {
      if (!value || !value.trim()) {
        return null;
      }
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) {
        throw new BadRequestException(`${label || "date"} is invalid`);
      }
      return parsed;
    };

    const resolvedFrom = parseValue(from, "from");
    const resolvedTo = parseValue(to, "to");
    if (resolvedFrom && resolvedTo && resolvedFrom > resolvedTo) {
      throw new BadRequestException("from must be earlier than to");
    }
    return {
      from: resolvedFrom,
      to: resolvedTo,
    };
  }

  private buildPaidSubscriptionOrderWhere(
    range: DashboardDateRange,
  ): Prisma.SubscriptionOrderWhereInput {
    return {
      status: SubscriptionOrderStatus.paid,
      ...(range.from || range.to
        ? {
            paidAt: {
              ...(range.from ? { gte: range.from } : {}),
              ...(range.to ? { lte: range.to } : {}),
            },
          }
        : {}),
    };
  }

  private normalizeSubscriptionFeatures(input: unknown): string[] {
    if (Array.isArray(input)) {
      return input
        .map((item) => (typeof item === "string" ? item.trim() : ""))
        .filter(Boolean);
    }
    if (typeof input === "string") {
      return input
        .split(/\r?\n|,/)
        .map((item) => item.trim())
        .filter(Boolean);
    }
    return [];
  }

  private buildSubscriptionPlanId(name: string): string {
    const stamp = Date.now().toString(36);
    return `plan_${stamp}_${randomUUID().slice(0, 8)}`;
  }

  private parseSubscriptionPlanPrice(body: any): number {
    if (
      body?.priceYuan !== undefined &&
      body?.priceYuan !== null &&
      String(body.priceYuan).trim() !== ""
    ) {
      const yuan = Number.parseFloat(String(body.priceYuan));
      if (!Number.isFinite(yuan) || yuan < 0) {
        throw new BadRequestException(
          "priceYuan must be a non-negative number",
        );
      }
      return Math.round(yuan * 100);
    }
    const cents = Number.parseInt(String(body?.price ?? ""), 10);
    if (!Number.isFinite(cents) || cents < 0) {
      throw new BadRequestException("price must be a non-negative integer");
    }
    return cents;
  }

  private presentSubscriptionPlan(
    plan: any,
    usage?: {
      orderCount?: number;
      revenueCents?: number;
      activeSubscribers?: number;
    },
  ) {
    const features = Array.isArray(plan.features) ? plan.features : [];
    const periodLabel = plan.period === SubscriptionPeriod.yearly ? "年" : "月";
    const priceYuan = Number(plan.price || 0) / 100;
    return {
      id: plan.id,
      name: plan.name,
      description: plan.description || null,
      price: Number(plan.price || 0),
      priceYuan,
      priceDisplay: priceYuan.toFixed(2),
      currency: plan.currency,
      period: plan.period,
      periodLabel,
      quota: plan.quota,
      quotaLabel: `${plan.quota}次/${periodLabel}`,
      features,
      recommended: Boolean(plan.recommended),
      badge: plan.badge || null,
      sortOrder: plan.sortOrder,
      active: Boolean(plan.active),
      createdAt: plan.createdAt,
      updatedAt: plan.updatedAt,
      orderCount: Number(usage?.orderCount || 0),
      revenueCents: Number(usage?.revenueCents || 0),
      revenueYuan: Number((Number(usage?.revenueCents || 0) / 100).toFixed(2)),
      activeSubscribers: Number(usage?.activeSubscribers || 0),
    };
  }

  private stringifyAiEngineErrorDetail(detail: any): string {
    if (detail == null) {
      return "";
    }
    if (typeof detail === "string") {
      return detail.trim();
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item) => this.stringifyAiEngineErrorDetail(item))
        .filter(Boolean)
        .join("; ");
    }
    if (typeof detail === "object") {
      const loc = Array.isArray(detail.loc) ? detail.loc.join(".") : "";
      const message =
        typeof detail.msg === "string"
          ? detail.msg
          : typeof detail.message === "string"
            ? detail.message
            : typeof detail.error === "string"
              ? detail.error
              : "";
      if (loc && message) {
        return `${loc}: ${message}`;
      }
      if (message) {
        return message;
      }
      try {
        return JSON.stringify(detail);
      } catch {
        return "";
      }
    }
    return String(detail);
  }

  private extractAiEngineAdminErrorMessage(error: any): string {
    if (axios.isAxiosError(error)) {
      const data = error.response?.data;
      const statusText = error.response?.statusText || "";
      const detail = this.stringifyAiEngineErrorDetail(
        data?.message ?? data?.detail ?? data?.error ?? data,
      );
      if (detail) {
        return detail;
      }
      if (statusText) {
        return statusText;
      }
      if (error.code === "ECONNABORTED") {
        return "request timed out";
      }
      if (typeof error.message === "string" && error.message.trim()) {
        return error.message.trim();
      }
    }
    if (error instanceof Error && error.message.trim()) {
      return error.message.trim();
    }
    return String(error || "unknown error");
  }

  private async postAiEngineAdminWithFailover<T>(
    regionTargetId: string | undefined,
    path: string,
    body: any,
    timeout: number,
    emptyUrlMessage: string,
  ): Promise<{ baseUrl: string; data: T }> {
    const urls = await this.getAiEngineAdminBaseUrls(regionTargetId);
    if (!urls.length) {
      throw new BadRequestException(emptyUrlMessage);
    }

    const failures: Array<{ baseUrl: string; message: string }> = [];
    for (const baseUrl of urls) {
      try {
        const response = await axios.post<T>(`${baseUrl}${path}`, body, {
          headers: {
            "x-admin-token": this.getAdminToken(),
          },
          timeout,
        });
        return {
          baseUrl,
          data: response.data,
        };
      } catch (error) {
        failures.push({
          baseUrl,
          message: this.extractAiEngineAdminErrorMessage(error),
        });
      }
    }

    const summary = failures
      .map((entry) => `${entry.baseUrl}: ${entry.message}`)
      .join(" | ");
    throw new BadGatewayException(
      `All ai-engine admin endpoints failed. ${summary || "No upstream error details available."}`,
    );
  }

  private normalizeLlmProviderExtraConfig(
    extraConfig: any,
  ): NormalizedLlmProviderExtraConfig {
    const normalized =
      extraConfig &&
      typeof extraConfig === "object" &&
      !Array.isArray(extraConfig)
        ? { ...extraConfig }
        : {};
    const rawCatalog =
      normalized.modelCatalog &&
      typeof normalized.modelCatalog === "object" &&
      !Array.isArray(normalized.modelCatalog)
        ? normalized.modelCatalog
        : {};
    const vendorPreset =
      typeof normalized.vendorPreset === "string" &&
      normalized.vendorPreset.trim()
        ? normalized.vendorPreset.trim()
        : "generic";
    const modelCatalog: LlmProviderCatalogConfig = {
      mode: rawCatalog.mode === "custom" ? "custom" : "auto",
      apiUrl:
        typeof rawCatalog.apiUrl === "string" ? rawCatalog.apiUrl.trim() : "",
      authMode:
        rawCatalog.authMode === "bearer_token"
          ? "bearer_token"
          : "inherit_provider",
      apiKey:
        typeof rawCatalog.apiKey === "string" ? rawCatalog.apiKey.trim() : "",
    };
    const contextWindow = this.coerceOptionalPositiveInteger(
      normalized.contextWindow,
    );
    const maxTokens = this.coerceOptionalPositiveInteger(normalized.maxTokens);
    return {
      ...normalized,
      vendorPreset,
      modelCatalog,
      contextWindow,
      maxTokens,
    };
  }

  private resolveOptionalPositiveInteger(
    value: unknown,
    fallback: number | null,
    fieldName: string,
  ): number | null {
    if (value === undefined) {
      return fallback;
    }
    if (value === null || value === "") {
      return null;
    }
    const normalized = this.coerceOptionalPositiveInteger(value);
    if (normalized === null) {
      throw new BadRequestException(`${fieldName} must be a positive integer`);
    }
    return normalized;
  }

  private coerceOptionalPositiveInteger(value: unknown): number | null {
    if (value === null || value === undefined || value === "") {
      return null;
    }
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return null;
    }
    const rounded = Math.floor(parsed);
    return rounded > 0 ? rounded : null;
  }

  private normalizeLlmCapabilityFlags(extraConfig: any): Record<string, any> {
    const normalized =
      extraConfig &&
      typeof extraConfig === "object" &&
      !Array.isArray(extraConfig)
        ? extraConfig
        : {};
    const raw =
      normalized.capabilityFlags ??
      normalized.capability_flags ??
      normalized.capabilities ??
      {};
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      return {};
    }
    return { ...raw };
  }

  private getLlmRequiredCapabilities(stepKey: string): string[] {
    let current = String(stepKey || "").trim();
    while (current) {
      const capabilities = LLM_STEP_REQUIRED_CAPABILITIES[current];
      if (capabilities) {
        return [...capabilities];
      }
      if (!current.includes(".")) {
        break;
      }
      current = current.slice(0, current.lastIndexOf("."));
    }
    return [];
  }

  private getLlmStepOutputMeta(step: any): LlmStepOutputMeta {
    const fallback = LLM_STEP_OUTPUT_META[step?.stepKey] || {
      outputClass: "medium_structured",
      minOutputTokens: null,
      maxOutputTokens: 2048,
      outputSummary: "Medium structured output",
    };
    const outputClass =
      typeof step?.outputClass === "string" && step.outputClass.trim()
        ? step.outputClass.trim()
        : typeof step?.output_class === "string" && step.output_class.trim()
          ? step.output_class.trim()
          : fallback.outputClass;
    return {
      outputClass,
      minOutputTokens:
        this.coerceOptionalPositiveInteger(
          step?.minOutputTokens ?? step?.min_output_tokens,
        ) ?? fallback.minOutputTokens,
      maxOutputTokens:
        this.coerceOptionalPositiveInteger(
          step?.maxOutputTokens ?? step?.max_output_tokens,
        ) ?? fallback.maxOutputTokens,
      outputSummary: fallback.outputSummary,
    };
  }

  private buildLlmCapabilitySummary(flags: Record<string, any>) {
    const unsafeForSteps = Array.isArray(flags.unsafe_for_steps)
      ? flags.unsafe_for_steps.map((item) => String(item)).filter(Boolean)
      : [];
    const knownCapabilities = Object.entries(flags)
      .filter(([key, value]) => key !== "unsafe_for_steps" && typeof value === "boolean")
      .map(([key, value]) => ({ key, enabled: Boolean(value) }));
    return {
      knownCapabilities,
      unsafeForSteps,
      compatibilityMode: knownCapabilities.length === 0 && unsafeForSteps.length === 0,
    };
  }

  private buildLlmProviderReadiness(
    provider: any,
    stepKey: string,
    outputMeta: LlmStepOutputMeta,
  ) {
    const extraConfig = this.normalizeLlmProviderExtraConfig(
      provider?.extraConfig,
    );
    const flags = this.normalizeLlmCapabilityFlags(extraConfig);
    const capabilitySummary = this.buildLlmCapabilitySummary(flags);
    const requiredCapabilities = this.getLlmRequiredCapabilities(stepKey);
    const unsafeForSteps = capabilitySummary.unsafeForSteps;
    const missingCapabilities = requiredCapabilities.filter(
      (capability) =>
        unsafeForSteps.includes(capability) || flags[capability] === false,
    );
    const unknownCapabilities = capabilitySummary.compatibilityMode
      ? requiredCapabilities
      : requiredCapabilities.filter(
          (capability) =>
            flags[capability] === undefined &&
            !unsafeForSteps.includes(capability),
        );
    const maxTokens = extraConfig.maxTokens;
    let tokenState: "not_required" | "ok" | "unknown" | "insufficient" =
      "not_required";
    if (outputMeta.minOutputTokens) {
      if (!maxTokens) {
        tokenState = "unknown";
      } else if (maxTokens >= outputMeta.minOutputTokens) {
        tokenState = "ok";
      } else {
        tokenState = "insufficient";
      }
    }
    const state =
      missingCapabilities.length > 0 || tokenState === "insufficient"
        ? "risk"
        : unknownCapabilities.length > 0 || tokenState === "unknown"
          ? "assumed"
          : "ok";
    return {
      state,
      requiredCapabilities,
      missingCapabilities,
      unknownCapabilities,
      capabilityCompatibilityMode: capabilitySummary.compatibilityMode,
      tokenState,
      requiredOutputTokens: outputMeta.minOutputTokens,
      providerMaxTokens: maxTokens,
      providerContextWindow: extraConfig.contextWindow,
    };
  }

  private buildLlmProviderExtraConfig(
    body: any,
    existing?: any,
  ): NormalizedLlmProviderExtraConfig {
    const current = this.normalizeLlmProviderExtraConfig(existing?.extraConfig);
    const vendorPreset =
      typeof body?.vendorPreset === "string" && body.vendorPreset.trim()
        ? body.vendorPreset.trim()
        : current.vendorPreset || "generic";
    const catalogMode = body?.catalogMode === "custom" ? "custom" : "auto";
    const nextCatalogApiKey =
      typeof body?.catalogApiKey === "string" && body.catalogApiKey.trim()
        ? body.catalogApiKey.trim()
        : current.modelCatalog.apiKey || "";
    const nextCatalogApiUrl =
      typeof body?.catalogApiUrl === "string"
        ? body.catalogApiUrl.trim()
        : current.modelCatalog.apiUrl || "";
    const nextCatalogAuthMode =
      body?.catalogAuthMode === "bearer_token"
        ? "bearer_token"
        : "inherit_provider";
    const contextWindow = this.resolveOptionalPositiveInteger(
      body?.contextWindow,
      current.contextWindow,
      "contextWindow",
    );
    const maxTokens = this.resolveOptionalPositiveInteger(
      body?.maxTokens,
      current.maxTokens,
      "maxTokens",
    );
    return {
      ...current,
      vendorPreset,
      modelCatalog: {
        mode: catalogMode,
        apiUrl: nextCatalogApiUrl,
        authMode: nextCatalogAuthMode,
        apiKey: nextCatalogApiKey,
      },
      contextWindow,
      maxTokens,
    };
  }

  private maskSecret(secret?: string | null): string | null {
    const value = (secret || "").trim();
    if (!value) {
      return null;
    }
    if (value.length <= 8) {
      return `${value.slice(0, 2)}...${value.slice(-2)}`;
    }
    return `${value.slice(0, 4)}...${value.slice(-4)}`;
  }

  private presentLlmProvider(provider: any) {
    const extraConfig = this.normalizeLlmProviderExtraConfig(
      provider?.extraConfig,
    );
    const latestTest =
      Array.isArray(provider?.testRecords) && provider.testRecords.length
        ? provider.testRecords[0]
        : null;
    const apiKeyMasked = this.maskSecret(provider?.apiKey);
    const catalogApiKeyMasked = this.maskSecret(
      extraConfig.modelCatalog.apiKey,
    );
    const capabilityFlags = this.normalizeLlmCapabilityFlags(extraConfig);
    return {
      ...provider,
      extraConfig: undefined,
      apiKey: undefined,
      apiKeySet: Boolean((provider?.apiKey || "").trim()),
      apiKeyMasked,
      regionDisplayName:
        provider?.regionTarget?.displayName || provider?.region,
      vendorPreset: extraConfig.vendorPreset,
      catalogMode: extraConfig.modelCatalog.mode,
      catalogApiUrl: extraConfig.modelCatalog.apiUrl,
      catalogAuthMode: extraConfig.modelCatalog.authMode,
      catalogApiKey: undefined,
      catalogApiKeySet: Boolean(extraConfig.modelCatalog.apiKey),
      catalogApiKeyMasked,
      contextWindow: extraConfig.contextWindow,
      maxTokens: extraConfig.maxTokens,
      capabilityFlags,
      capabilitySummary: this.buildLlmCapabilitySummary(capabilityFlags),
      latestTest: latestTest
        ? {
            success: latestTest.success,
            latencyMs: latestTest.latencyMs,
            httpStatus: latestTest.httpStatus,
            errorMessage: latestTest.errorMessage,
            model: latestTest.model,
            testedAt: latestTest.testedAt,
          }
        : null,
    };
  }

  async getLlmProviderSecret(id: string, kind: string) {
    const normalizedKind = String(kind || "")
      .trim()
      .toLowerCase();
    if (normalizedKind !== "api-key" && normalizedKind !== "catalog-api-key") {
      throw new BadRequestException("Unsupported provider secret kind");
    }

    const provider = await this.prisma.llmGatewayProvider.findUnique({
      where: { id },
      select: {
        id: true,
        apiKey: true,
        extraConfig: true,
      },
    });

    if (!provider) {
      throw new NotFoundException("Provider not found");
    }

    const extraConfig = this.normalizeLlmProviderExtraConfig(
      provider.extraConfig,
    );
    const value =
      normalizedKind === "api-key"
        ? String(provider.apiKey || "").trim()
        : String(extraConfig.modelCatalog.apiKey || "").trim();

    if (!value) {
      throw new NotFoundException("Requested secret is not configured");
    }

    return {
      providerId: provider.id,
      kind: normalizedKind,
      value,
    };
  }

  private async invalidateFeedCache(): Promise<void> {
    if (this.localFeedService) {
      try { await this.localFeedService.invalidateGameFeedCache(); }
      catch { console.warn('Local feed cache invalidation failed'); }
      return;
    }

    const adminToken = this.getOptionalAdminToken();
    if (!adminToken) {
      console.warn(
        "[ADMIN] Skipping feed cache invalidation because ADMIN_TOKEN is not configured",
      );
      return;
    }

    const baseUrl = (
      this.configService.get<string>("FEED_SERVICE_UPSTREAM_URL") ||
      this.configService.get<string>("FEED_SERVICE_URL") ||
      this.configService.get<string>(
        "PUBLIC_API_BASE_URL",
        "http://localhost:3002",
      )
    ).replace(/\/$/, "");

    try {
      await axios.post(
        `${baseUrl}/api/v1/feed/internal/cache/invalidate`,
        {},
        {
          timeout: 5000,
          headers: {
            "x-admin-token": adminToken,
          },
        },
      );
    } catch (error: any) {
      // Mutations should not fail just because cache eviction missed.
      console.warn(
        "[ADMIN] Failed to invalidate feed cache:",
        error?.message || error,
      );
    }
  }

  private normalizeLegacyPreviewBackfillLimit(rawValue?: unknown): number {
    const parsed = Number.parseInt(String(rawValue ?? "200"), 10);
    if (!Number.isFinite(parsed)) {
      return 200;
    }
    return Math.min(Math.max(parsed, 1), 2000);
  }

  private parseLegacyPreviewBackfillDryRun(
    rawValue?: boolean | string,
  ): boolean {
    return (
      String(rawValue ?? "false")
        .trim()
        .toLowerCase() === "true"
    );
  }

  private parseLegacyPreviewBackfillGameIds(
    rawValue?: string[] | string,
  ): string[] {
    if (Array.isArray(rawValue)) {
      return rawValue
        .map((value) => String(value || "").trim())
        .filter(Boolean);
    }

    return String(rawValue || "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
  }

  private parseGameCoverBackfillOverwriteExisting(
    rawValue?: boolean | string,
  ): boolean {
    return (
      String(rawValue ?? "false")
        .trim()
        .toLowerCase() === "true"
    );
  }

  async listGames(
    page: number,
    limit: number,
    search?: string,
    status?: string,
  ) {
    const where: Prisma.GameWhereInput = {};

    if (search) {
      where.title = { contains: search };
    }

    if (status && status !== "all") {
      where.status = status as any;
    }

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where,
        include: {
          author: {
            select: {
              id: true,
              username: true,
              displayName: true,
            },
          },
          bundles: {
            select: {
              id: true,
              version: true,
              codeSizeBytes: true,
              metadata: true,
              createdAt: true,
            },
            orderBy: { version: "desc" },
            take: 1,
          },
        },
        orderBy: { createdAt: "desc" },
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.prisma.game.count({ where }),
    ]);

    return {
      items: games.map((game: any) => this.presentAdminGame(game)),
      total,
      page,
      limit,
      totalPages: Math.ceil(total / limit),
    };
  }

  async getGame(id: string) {
    const game = await this.prisma.game.findUnique({
      where: { id },
      include: {
        author: {
          select: {
            id: true,
            username: true,
            displayName: true,
          },
        },
        bundles: {
          orderBy: { version: "desc" },
        },
      },
    });

    if (!game) {
      throw new NotFoundException("Game not found");
    }

    return this.presentAdminGame(game);
  }

  async createGame(data: {
    title: string;
    description?: string;
    slug?: string;
    gameType?: string;
    tags?: string[];
    htmlCode: string;
    cssCode?: string;
    jsCode?: string;
    authorId?: string;
  }) {
    const gameId = randomUUID();
    const bundleId = randomUUID();

    // Generate slug from title if not provided
    const slug =
      data.slug ||
      data.title
        .toLowerCase()
        .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-")
        .replace(/^-|-$/g, "") +
        "-" +
        Date.now().toString(36);

    const htmlCode = data.htmlCode || "";
    const codeSizeBytes =
      Buffer.byteLength(htmlCode, "utf8") +
      Buffer.byteLength(data.cssCode || "", "utf8") +
      Buffer.byteLength(data.jsCode || "", "utf8");

    // If no authorId provided, try to find or create a system admin user
    let authorId = data.authorId;
    if (!authorId) {
      const adminUser = await this.prisma.user.findFirst({
        where: { role: "admin" },
      });
      if (adminUser) {
        authorId = adminUser.id;
      } else {
        // Create a system admin user
        const adminId = randomUUID();
        await this.prisma.user.create({
          data: {
            id: adminId,
            username: "system_admin",
            displayName: "System Admin",
            role: "admin",
            authProvider: "email",
          },
        });
        authorId = adminId;
      }
    }

    const normalizedGameType = normalizeGameType(
      data.gameType,
      data.title,
      data.description,
      data.tags,
    );

    const game = await this.prisma.game.create({
      data: {
        id: gameId,
        title: data.title,
        description: data.description || null,
        slug,
        gameType: normalizedGameType,
        tags: data.tags || [],
        author: { connect: { id: authorId! } },
        status: "draft",
        codeBundleId: bundleId,
        version: 1,
      },
    });

    await this.prisma.gameBundle.create({
      data: {
        id: bundleId,
        gameId: game.id,
        version: 1,
        htmlCode,
        cssCode: data.cssCode || null,
        jsCode: data.jsCode || null,
        codeSizeBytes,
      },
    });

    await this.invalidateFeedCache();
    return this.getGame(game.id);
  }

  async updateGame(
    id: string,
    data: {
      title?: string;
      description?: string;
      slug?: string;
      gameType?: string;
      tags?: string[];
      htmlCode?: string;
      cssCode?: string;
      jsCode?: string;
      status?: string;
    },
  ) {
    const existing = await this.prisma.game.findUnique({
      where: { id },
      include: {
        bundles: { orderBy: { version: "desc" }, take: 1 },
      },
    });

    if (!existing) {
      throw new NotFoundException("Game not found");
    }

    if (data.status === "banned") {
      await this.gameService.terminateActiveTasksForGame(id, {
        reason: "Task canceled because the game was banned by admin",
      });
    }

    // Update game metadata
    const gameUpdate: any = {};
    if (data.title !== undefined) gameUpdate.title = data.title;
    if (data.description !== undefined)
      gameUpdate.description = data.description;
    if (data.slug !== undefined) gameUpdate.slug = data.slug;
    if (data.gameType !== undefined) {
      gameUpdate.gameType = normalizeGameType(
        data.gameType,
        data.title ?? existing.title,
        data.description ?? existing.description,
        data.tags ?? existing.tags,
      );
    }
    if (data.tags !== undefined) gameUpdate.tags = data.tags;
    if (data.status !== undefined) gameUpdate.status = data.status;

    if (Object.keys(gameUpdate).length > 0) {
      await this.prisma.game.update({
        where: { id },
        data: gameUpdate,
      });
    }

    // Update or create bundle if code provided
    if (data.htmlCode !== undefined) {
      const newVersion = (existing.bundles[0]?.version || 0) + 1;
      const htmlCode = data.htmlCode || "";
      const codeSizeBytes =
        Buffer.byteLength(htmlCode, "utf8") +
        Buffer.byteLength(data.cssCode || "", "utf8") +
        Buffer.byteLength(data.jsCode || "", "utf8");

      const bundleId = randomUUID();
      await this.prisma.gameBundle.create({
        data: {
          id: bundleId,
          gameId: id,
          version: newVersion,
          htmlCode,
          cssCode: data.cssCode || null,
          jsCode: data.jsCode || null,
          codeSizeBytes,
        },
      });

      await this.prisma.game.update({
        where: { id },
        data: {
          codeBundleId: bundleId,
          version: newVersion,
        },
      });
    }

    await this.invalidateFeedCache();
    return this.getGame(id);
  }

  async updateGameCover(id: string, data: AdminGameCoverUpdateInput) {
    const game = await this.prisma.game.findUnique({
      where: { id },
      select: {
        id: true,
        authorId: true,
        status: true,
        version: true,
        bundles: {
          select: {
            id: true,
            version: true,
            metadata: true,
          },
          orderBy: { version: "desc" },
          take: 1,
        },
      },
    });

    if (!game) {
      throw new NotFoundException("Game not found");
    }

    if (typeof game.authorId !== "string" || !game.authorId.trim()) {
      throw new BadRequestException("Game author is missing");
    }

    const uploadedImage = this.decodeManualCoverImageDataUrl(
      data?.imageDataUrl,
    );
    const externalImageUrl = uploadedImage
      ? null
      : this.normalizeManualCoverUrl(data?.imageUrl);
    if (!uploadedImage && !externalImageUrl) {
      throw new BadRequestException(
        "Either imageDataUrl or imageUrl is required",
      );
    }

    const bundle = await this.resolveGameCoverTargetBundle(game);
    const metadata =
      bundle.metadata &&
      typeof bundle.metadata === "object" &&
      !Array.isArray(bundle.metadata)
        ? { ...(bundle.metadata as Record<string, any>) }
        : {};
    const manualUpdatedAt = new Date().toISOString();
    let targetCoverUrl = externalImageUrl || "";
    let coverArtifactId: string | null = null;

    if (uploadedImage) {
      const artifact = await this.prisma.generationArtifact.create({
        data: {
          id: randomUUID(),
          taskId: undefined,
          gameId: game.id,
          userId: game.authorId,
          artifactType: "cover_image",
          contentType: uploadedImage.contentType,
          storageType: "inline_text",
          payloadText: uploadedImage.payload,
          payloadJson: undefined,
          payloadUrl: undefined,
          sizeBytes: uploadedImage.sizeBytes,
          metadata: {
            encoding: "base64",
            manualCover: true,
            manualCoverSource: "admin_upload",
            manualCoverFileName:
              typeof data?.fileName === "string" && data.fileName.trim()
                ? data.fileName.trim().slice(0, 255)
                : undefined,
            manualCoverUpdatedAt: manualUpdatedAt,
          } as Prisma.InputJsonValue,
        },
      });
      coverArtifactId = artifact.id;
      targetCoverUrl = this.buildPublicCoverUrl(game.id, bundle.version);
    }

    const nextMetadata = {
      ...metadata,
      coverUrl: targetCoverUrl,
      manualCover: true,
      manualCoverSource: uploadedImage ? "admin_upload" : "admin_url",
      manualCoverUpdatedAt: manualUpdatedAt,
      ...(coverArtifactId ? { coverArtifactId } : {}),
    };
    delete (nextMetadata as any).coverTaskId;
    delete (nextMetadata as any).cover_task_id;
    delete (nextMetadata as any).cover_artifact_id;
    if (!coverArtifactId) {
      delete (nextMetadata as any).coverArtifactId;
    }

    await this.prisma.gameBundle.update({
      where: { id: bundle.id },
      data: {
        metadata: nextMetadata as Prisma.InputJsonValue,
      },
    });

    await this.prisma.game.update({
      where: { id: game.id },
      data: {
        thumbnailUrl: targetCoverUrl,
      },
    });

    await this.invalidateFeedCache();
    return this.getGame(id);
  }

  async deleteGame(id: string) {
    const game = await this.prisma.game.findUnique({ where: { id } });
    if (!game) {
      throw new NotFoundException("Game not found");
    }

    await this.gameService.terminateActiveTasksForGame(id, {
      reason: "Task canceled because the game was deleted by admin",
    });

    // Delete bundles first (cascade should handle this, but be explicit)
    await this.prisma.gameBundle.deleteMany({ where: { gameId: id } });
    await this.prisma.game.delete({ where: { id } });
    await this.invalidateFeedCache();

    return { deleted: true };
  }

  async batchUpdateGameStatus(ids: unknown, status: string) {
    const normalizedIds = this.normalizeGameIds(ids);
    if (!normalizedIds.length) {
      throw new BadRequestException("ids must contain at least one game id");
    }

    const nextStatus = this.validateBatchStatus(status);
    const games = await this.prisma.game.findMany({
      where: { id: { in: normalizedIds } },
      select: {
        id: true,
        publishedAt: true,
      },
    });

    const foundIdSet = new Set(games.map((game: any) => game.id));
    const missingIds = normalizedIds.filter((id) => !foundIdSet.has(id));
    const updatedAt = new Date();

    await this.prisma.$transaction(async (tx) => {
      for (const game of games) {
        const updateData: Prisma.GameUpdateInput = {
          status: nextStatus as any,
        };
        if (nextStatus === "published" && !game.publishedAt) {
          updateData.publishedAt = updatedAt;
        }
        await tx.game.update({
          where: { id: game.id },
          data: updateData,
        });
      }
    });

    if (games.length > 0) {
      await this.invalidateFeedCache();
    }

    return {
      requested: normalizedIds.length,
      updated: games.length,
      status: nextStatus,
      updatedIds: games.map((game: any) => game.id),
      missingIds,
    };
  }

  async batchDeleteGames(ids: unknown) {
    const normalizedIds = this.normalizeGameIds(ids);
    if (!normalizedIds.length) {
      throw new BadRequestException("ids must contain at least one game id");
    }

    const games = await this.prisma.game.findMany({
      where: { id: { in: normalizedIds } },
      select: { id: true },
    });

    const foundIds = games.map((game: any) => game.id);
    const foundIdSet = new Set(foundIds);
    const missingIds = normalizedIds.filter((id) => !foundIdSet.has(id));

    await Promise.all(
      foundIds.map((gameId) =>
        this.gameService.terminateActiveTasksForGame(gameId, {
          reason: "Task canceled because the game was batch-deleted by admin",
        }),
      ),
    );

    if (foundIds.length > 0) {
      await this.prisma.$transaction(async (tx) => {
        await tx.gameBundle.deleteMany({ where: { gameId: { in: foundIds } } });
        await tx.game.deleteMany({ where: { id: { in: foundIds } } });
      });
      await this.invalidateFeedCache();
    }

    return {
      requested: normalizedIds.length,
      deleted: foundIds.length,
      deletedIds: foundIds,
      missingIds,
    };
  }

  async toggleStatus(id: string, status: string) {
    const game = await this.prisma.game.findUnique({ where: { id } });
    if (!game) {
      throw new NotFoundException("Game not found");
    }

    if (status === "banned") {
      await this.gameService.terminateActiveTasksForGame(id, {
        reason: "Task canceled because the game was banned by admin",
      });
    }

    const updateData: any = { status };
    if (status === "published" && !game.publishedAt) {
      updateData.publishedAt = new Date();
    }

    await this.prisma.game.update({
      where: { id },
      data: updateData,
    });

    await this.invalidateFeedCache();

    return this.getGame(id);
  }

  async refreshGameTypes(options: { dryRun?: boolean | string } = {}) {
    const dryRun =
      String(options?.dryRun ?? "false")
        .trim()
        .toLowerCase() === "true";
    const batchSize = 200;
    const summary = {
      dryRun,
      gamesScanned: 0,
      gamesUpdated: 0,
      bundlesScanned: 0,
      bundlesUpdated: 0,
      tasksScanned: 0,
      tasksUpdated: 0,
    };

    let gameCursor: string | undefined;
    while (true) {
      const games = await this.prisma.game.findMany({
        ...(gameCursor ? { cursor: { id: gameCursor }, skip: 1 } : {}),
        take: batchSize,
        orderBy: { id: "asc" },
        select: {
          id: true,
          title: true,
          description: true,
          tags: true,
          gameType: true,
        },
      });
      if (games.length === 0) {
        break;
      }

      for (const game of games) {
        summary.gamesScanned += 1;
        const normalizedGameType = normalizeGameType(
          game.gameType,
          game.title,
          game.description,
          game.tags,
        );
        if (game.gameType !== normalizedGameType) {
          summary.gamesUpdated += 1;
          if (!dryRun) {
            await this.prisma.game.update({
              where: { id: game.id },
              data: { gameType: normalizedGameType },
            });
          }
        }
      }

      gameCursor = games[games.length - 1]?.id;
    }

    let bundleCursor: string | undefined;
    while (true) {
      const bundles = await this.prisma.gameBundle.findMany({
        ...(bundleCursor ? { cursor: { id: bundleCursor }, skip: 1 } : {}),
        take: batchSize,
        orderBy: { id: "asc" },
        select: {
          id: true,
          metadata: true,
          game: {
            select: {
              title: true,
              description: true,
              tags: true,
              gameType: true,
            },
          },
        },
      });
      if (bundles.length === 0) {
        break;
      }

      for (const bundle of bundles) {
        summary.bundlesScanned += 1;
        const metadata = this.asPlainObject(bundle.metadata);
        const metadataGameSpec = this.asPlainObject(metadata.gameSpec);
        const normalizedGameType = normalizeGameType(
          metadata.gameType,
          metadataGameSpec.game_type,
          bundle.game?.gameType,
          bundle.game?.title,
          bundle.game?.description,
          bundle.game?.tags,
        );
        if (metadata.gameType !== normalizedGameType) {
          summary.bundlesUpdated += 1;
          if (!dryRun) {
            await this.prisma.gameBundle.update({
              where: { id: bundle.id },
              data: {
                metadata: {
                  ...metadata,
                  gameType: normalizedGameType,
                } as Prisma.InputJsonValue,
              },
            });
          }
        }
      }

      bundleCursor = bundles[bundles.length - 1]?.id;
    }

    let taskCursor: string | undefined;
    while (true) {
      const tasks = await this.prisma.generationTask.findMany({
        ...(taskCursor ? { cursor: { id: taskCursor }, skip: 1 } : {}),
        take: batchSize,
        orderBy: { id: "asc" },
        select: {
          id: true,
          resultSummary: true,
          metadata: true,
          game: {
            select: {
              title: true,
              description: true,
              tags: true,
              gameType: true,
            },
          },
        },
      });
      if (tasks.length === 0) {
        break;
      }

      for (const task of tasks) {
        summary.tasksScanned += 1;
        const resultSummary = this.asPlainObject(task.resultSummary);
        const metadata = this.asPlainObject(task.metadata);
        const normalizedGameType = normalizeGameType(
          resultSummary.gameType,
          metadata.selectedGameType,
          metadata.gameType,
          task.game?.gameType,
          task.game?.title,
          task.game?.description,
          task.game?.tags,
        );

        let nextResultSummary = resultSummary;
        let nextMetadata = metadata;
        let changed = false;

        if (resultSummary.gameType !== normalizedGameType) {
          nextResultSummary = {
            ...resultSummary,
            gameType: normalizedGameType,
          };
          changed = true;
        }

        if (
          Object.prototype.hasOwnProperty.call(metadata, "selectedGameType") &&
          metadata.selectedGameType !== normalizedGameType
        ) {
          nextMetadata = {
            ...nextMetadata,
            selectedGameType: normalizedGameType,
          };
          changed = true;
        }

        if (
          Object.prototype.hasOwnProperty.call(metadata, "gameType") &&
          metadata.gameType !== normalizedGameType
        ) {
          nextMetadata = {
            ...nextMetadata,
            gameType: normalizedGameType,
          };
          changed = true;
        }

        if (changed) {
          summary.tasksUpdated += 1;
          if (!dryRun) {
            await this.prisma.generationTask.update({
              where: { id: task.id },
              data: {
                resultSummary: nextResultSummary as Prisma.InputJsonValue,
                metadata: nextMetadata as Prisma.InputJsonValue,
              },
            });
          }
        }
      }

      taskCursor = tasks[tasks.length - 1]?.id;
    }

    if (
      !dryRun &&
      (summary.gamesUpdated > 0 ||
        summary.bundlesUpdated > 0 ||
        summary.tasksUpdated > 0)
    ) {
      await this.invalidateFeedCache();
    }

    return summary;
  }

  async backfillLegacyPreviewGames(options: LegacyPreviewBackfillOptions = {}) {
    const limit = this.normalizeLegacyPreviewBackfillLimit(options.limit);
    const dryRun = this.parseLegacyPreviewBackfillDryRun(options.dryRun);
    const gameIds = this.parseLegacyPreviewBackfillGameIds(options.gameIds);
    const where: Prisma.GameWhereInput = {
      status: {
        in: [GameStatus.draft, GameStatus.review, GameStatus.published],
      },
      visibility: {
        notIn: ["public", "unlisted"],
      },
      generationTasks: {
        some: {
          status: "succeeded",
        },
      },
      bundles: {
        some: {},
      },
      ...(gameIds.length > 0
        ? {
            id: {
              in: gameIds,
            },
          }
        : {}),
    };

    const candidates = await this.prisma.game.findMany({
      where,
      select: {
        id: true,
        title: true,
        status: true,
        visibility: true,
        version: true,
        codeBundleId: true,
        createdAt: true,
        publishedAt: true,
        bundles: {
          select: {
            id: true,
            version: true,
            htmlCode: true,
          },
          orderBy: {
            version: "desc",
          },
          take: 1,
        },
        generationTasks: {
          where: {
            status: "succeeded",
          },
          select: {
            id: true,
            previewUrl: true,
            createdAt: true,
          },
          orderBy: {
            createdAt: "desc",
          },
          take: 1,
        },
      },
      orderBy: {
        createdAt: "asc",
      },
      take: limit,
    });

    const eligibleGames = candidates.reduce<
      Array<{
        id: string;
        title: string;
        previousStatus: GameStatus;
        previousVisibility: string;
        previousVersion: number;
        previousCodeBundleId: string | null;
        createdAt: Date;
        publishedAt: Date | null;
        latestBundleId: string;
        latestBundleVersion: number;
        sourceTaskId: string;
        sourcePreviewUrl: string | null;
      }>
    >((items, game) => {
      const latestBundle = game.bundles[0];
      const latestSucceededTask = game.generationTasks[0];
      const previewUrl = latestSucceededTask?.previewUrl || "";
      const isLegacyTask = !previewUrl || !previewUrl.includes("previewToken=");
      const hasPlayableBundle =
        typeof latestBundle?.htmlCode === "string" &&
        latestBundle.htmlCode.trim().length > 0;

      if (
        !latestBundle ||
        !latestSucceededTask ||
        !isLegacyTask ||
        !hasPlayableBundle
      ) {
        return items;
      }

      items.push({
        id: game.id,
        title: game.title,
        previousStatus: game.status,
        previousVisibility: game.visibility || "private",
        previousVersion: game.version,
        previousCodeBundleId: game.codeBundleId,
        createdAt: game.createdAt,
        publishedAt: game.publishedAt,
        latestBundleId: latestBundle.id,
        latestBundleVersion: latestBundle.version,
        sourceTaskId: latestSucceededTask.id,
        sourcePreviewUrl: latestSucceededTask.previewUrl,
      });
      return items;
    }, []);

    if (!dryRun) {
      for (const game of eligibleGames) {
        await this.prisma.game.update({
          where: {
            id: game.id,
          },
          data: {
            status: GameStatus.published,
            visibility: "unlisted",
            publishedAt: game.publishedAt || game.createdAt,
            version: Math.max(
              game.previousVersion || 0,
              game.latestBundleVersion || 1,
            ),
            codeBundleId: game.latestBundleId,
          },
        });
      }

      if (eligibleGames.length > 0) {
        await this.invalidateFeedCache();
      }
    }

    return {
      dryRun,
      scanned: candidates.length,
      eligible: eligibleGames.length,
      updated: dryRun ? 0 : eligibleGames.length,
      filters: {
        limit,
        gameIds: gameIds.length > 0 ? gameIds : null,
      },
      items: eligibleGames.map((game) => ({
        id: game.id,
        title: game.title,
        sourceTaskId: game.sourceTaskId,
        sourcePreviewUrl: game.sourcePreviewUrl,
        previousStatus: game.previousStatus,
        previousVisibility: game.previousVisibility,
        latestBundleVersion: game.latestBundleVersion,
        targetStatus: GameStatus.published,
        targetVisibility: "unlisted",
      })),
    };
  }

  async backfillGameCovers(options: GameCoverBackfillOptions = {}) {
    const limit = this.normalizeLegacyPreviewBackfillLimit(options.limit);
    const dryRun = this.parseLegacyPreviewBackfillDryRun(options.dryRun);
    const gameIds = this.parseLegacyPreviewBackfillGameIds(options.gameIds);
    const overwriteExisting = this.parseGameCoverBackfillOverwriteExisting(
      options.overwriteExisting,
    );
    const summary = {
      dryRun,
      overwriteExisting,
      scanned: 0,
      eligible: 0,
      regenerated: 0,
      skipped: 0,
      failed: 0,
      items: [] as Array<Record<string, unknown>>,
    };

    const candidates = await this.prisma.game.findMany({
      where: {
        status: {
          in: [GameStatus.draft, GameStatus.review, GameStatus.published],
        },
        bundles: {
          some: {},
        },
        ...(!overwriteExisting
          ? {
              OR: [{ thumbnailUrl: null }, { thumbnailUrl: "" }],
            }
          : {}),
        ...(gameIds.length > 0
          ? {
              id: {
                in: gameIds,
              },
            }
          : {}),
      },
      select: {
        id: true,
        authorId: true,
        title: true,
        description: true,
        status: true,
        visibility: true,
        version: true,
        gameType: true,
        thumbnailUrl: true,
        bundles: {
          select: {
            id: true,
            version: true,
            htmlCode: true,
            metadata: true,
          },
          orderBy: {
            version: "desc",
          },
          take: 1,
        },
      },
      orderBy: {
        createdAt: "asc",
      },
      take: limit,
    });

    summary.scanned = candidates.length;

    for (const game of candidates) {
      if (typeof game.authorId !== "string" || !game.authorId.trim()) {
        summary.skipped += 1;
        summary.items.push({
          id: game.id,
          title: game.title,
          status: "skipped",
          reason: "missing_author",
        });
        continue;
      }

      let bundle: {
        id: string;
        version: number;
        htmlCode: string;
        metadata: Prisma.JsonValue;
      } | null = game.bundles[0] || null;
      if (
        game.status === GameStatus.published &&
        Number.isFinite(game.version) &&
        Number(game.version) > 0
      ) {
        const liveVersion = Number(game.version);
        if (!bundle || bundle.version !== liveVersion) {
          bundle = await this.prisma.gameBundle.findFirst({
            where: {
              gameId: game.id,
              version: liveVersion,
            },
            select: {
              id: true,
              version: true,
              htmlCode: true,
              metadata: true,
            },
          });
        }
      }

      const metadata =
        bundle?.metadata &&
        typeof bundle.metadata === "object" &&
        !Array.isArray(bundle.metadata)
          ? { ...(bundle.metadata as Record<string, any>) }
          : {};
      const hasPlayableBundle =
        typeof bundle?.htmlCode === "string" &&
        bundle.htmlCode.trim().length > 0;
      const hasExistingCover = Boolean(
        game.thumbnailUrl ||
        metadata.coverUrl ||
        metadata.coverTaskId ||
        metadata.coverArtifactId ||
        metadata.cover_task_id ||
        metadata.cover_artifact_id,
      );

      if (!bundle || !hasPlayableBundle) {
        summary.skipped += 1;
        summary.items.push({
          id: game.id,
          title: game.title,
          status: "skipped",
          reason: "no_playable_bundle",
        });
        continue;
      }

      if (!overwriteExisting && hasExistingCover) {
        summary.skipped += 1;
        summary.items.push({
          id: game.id,
          title: game.title,
          status: "skipped",
          reason: "existing_cover",
          bundleVersion: bundle.version,
        });
        continue;
      }

      summary.eligible += 1;
      const gameSpec =
        metadata.gameSpec &&
        typeof metadata.gameSpec === "object" &&
        !Array.isArray(metadata.gameSpec)
          ? (metadata.gameSpec as Record<string, any>)
          : {};
      const visualStyle =
        gameSpec.visual_style &&
        typeof gameSpec.visual_style === "object" &&
        !Array.isArray(gameSpec.visual_style)
          ? (gameSpec.visual_style as Record<string, any>)
          : {};
      const runtimeOrientation =
        typeof metadata.runtimeOrientation === "string"
          ? metadata.runtimeOrientation
          : typeof metadata.requestedOrientation === "string"
            ? metadata.requestedOrientation === "landscape"
              ? "landscape_first"
              : "portrait_first"
            : undefined;

      try {
        const captureResponse = await this.postAiEngineAdminWithFailover<any>(
          undefined,
          "/api/v1/ai/covers/capture",
          {
            game_id: game.id,
            user_id: game.authorId,
            html_code: bundle.htmlCode,
            orientation: runtimeOrientation,
            title: game.title,
            game_type: normalizeGameType(
              game.gameType,
              game.title,
              game.description,
              Array.isArray(gameSpec.tags) ? gameSpec.tags : [],
            ),
            theme:
              typeof visualStyle.theme === "string" ? visualStyle.theme : null,
            runtime_profile:
              typeof metadata.runtimeProfile === "string"
                ? metadata.runtimeProfile
                : null,
            visual_pack:
              typeof visualStyle.visual_pack === "string"
                ? visualStyle.visual_pack
                : null,
            render_style_intensity:
              typeof visualStyle.render_style_intensity === "string"
                ? visualStyle.render_style_intensity
                : null,
            updated:
              game.status === GameStatus.published &&
              bundle.version < Number(game.version || bundle.version),
          },
          60000,
          "No reachable ai-engine endpoint found for cover backfill",
        );
        const captured = captureResponse.data?.captured !== false;
        const payload =
          typeof captureResponse.data?.payload === "string"
            ? captureResponse.data.payload
            : "";
        const contentType =
          typeof captureResponse.data?.content_type === "string"
            ? captureResponse.data.content_type
            : typeof captureResponse.data?.contentType === "string"
              ? captureResponse.data.contentType
              : "image/jpeg";
        const coverMetadata =
          captureResponse.data?.metadata &&
          typeof captureResponse.data.metadata === "object"
            ? captureResponse.data.metadata
            : {};

        if (!captured || !payload) {
          summary.failed += 1;
          summary.items.push({
            id: game.id,
            title: game.title,
            status: "failed",
            reason: "cover_capture_empty",
            bundleVersion: bundle.version,
          });
          continue;
        }

        const targetCoverUrl = this.buildPublicCoverUrl(
          game.id,
          bundle.version,
        );
        if (!dryRun) {
          const artifact = await this.prisma.generationArtifact.create({
            data: {
              id: randomUUID(),
              taskId: undefined,
              gameId: game.id,
              userId: game.authorId,
              artifactType: "cover_image",
              contentType,
              storageType: "inline_text",
              payloadText: payload,
              payloadJson: undefined,
              payloadUrl: undefined,
              metadata: {
                encoding: "base64",
                backfillSource: "admin_cover_backfill",
                backfilledAt: new Date().toISOString(),
                ...(coverMetadata as Record<string, unknown>),
              } as Prisma.InputJsonValue,
            },
          });

          const nextMetadata = {
            ...metadata,
            coverArtifactId: artifact.id,
            coverUrl: targetCoverUrl,
          };
          delete (nextMetadata as any).coverTaskId;
          delete (nextMetadata as any).cover_task_id;

          await this.prisma.gameBundle.update({
            where: { id: bundle.id },
            data: {
              metadata: nextMetadata as Prisma.InputJsonValue,
            },
          });

          await this.prisma.game.update({
            where: { id: game.id },
            data: {
              thumbnailUrl: targetCoverUrl,
            },
          });
        }

        summary.regenerated += 1;
        summary.items.push({
          id: game.id,
          title: game.title,
          status: dryRun ? "planned" : "regenerated",
          bundleVersion: bundle.version,
          coverUrl: targetCoverUrl,
          overlayStyle: coverMetadata.coverStyle || null,
        });
      } catch (error: any) {
        summary.failed += 1;
        summary.items.push({
          id: game.id,
          title: game.title,
          status: "failed",
          reason: this.extractAiEngineAdminErrorMessage(error),
          bundleVersion: bundle.version,
        });
      }
    }

    if (!dryRun && summary.regenerated > 0) {
      await this.invalidateFeedCache();
    }

    return summary;
  }

  // ===================== User Management =====================

  async listUsers(
    page: number,
    limit: number,
    search?: string,
    role?: string,
    authProvider?: string,
  ) {
    const where: Prisma.UserWhereInput = {};
    if (search) {
      where.OR = [
        { username: { contains: search } },
        { displayName: { contains: search } },
        { email: { contains: search } },
        { phone: { contains: search } },
      ];
    }
    if (role && role !== "all") {
      where.role = role as any;
    }
    const providerFilter = this.normalizeUserAuthProvider(authProvider);
    if (providerFilter !== "all") {
      where.authProvider = providerFilter;
    }

    const [users, total] = await Promise.all([
      this.prisma.user.findMany({
        where,
        select: {
          id: true,
          username: true,
          displayName: true,
          email: true,
          phone: true,
          role: true,
          isPro: true,
          bio: true,
          avatarUrl: true,
          authProvider: true,
          followerCount: true,
          followingCount: true,
          gameCount: true,
          totalPlays: true,
          createdAt: true,
          updatedAt: true,
        },
        orderBy: { createdAt: "desc" },
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.prisma.user.count({ where }),
    ]);

    return {
      items: users,
      total,
      page,
      limit,
      totalPages: Math.ceil(total / limit),
    };
  }

  async getUser(id: string) {
    const user = await this.prisma.user.findUnique({
      where: { id },
      select: {
        id: true,
        username: true,
        displayName: true,
        email: true,
        phone: true,
        role: true,
        isPro: true,
        bio: true,
        avatarUrl: true,
        authProvider: true,
        followerCount: true,
        followingCount: true,
        gameCount: true,
        totalPlays: true,
        createdAt: true,
        updatedAt: true,
      },
    });
    if (!user) throw new NotFoundException("User not found");
    return user;
  }

  async updateUser(id: string, data: any) {
    const user = await this.prisma.user.findUnique({ where: { id } });
    if (!user) throw new NotFoundException("User not found");

    const update: any = {};
    if (data.username !== undefined) update.username = data.username;
    if (data.displayName !== undefined) update.displayName = data.displayName;
    if (data.email !== undefined) update.email = data.email || null;
    if (data.phone !== undefined) update.phone = data.phone || null;
    if (data.role !== undefined) update.role = data.role;
    if (data.isPro !== undefined) update.isPro = data.isPro;
    if (data.bio !== undefined) update.bio = data.bio;

    if (Object.keys(update).length > 0) {
      await this.prisma.user.update({ where: { id }, data: update });
    }
    return this.getUser(id);
  }

  async batchUpdateUsers(ids: unknown, data: any) {
    const normalizedIds = this.normalizeUserIds(ids);
    if (!normalizedIds.length) {
      throw new BadRequestException("ids must be a non-empty array");
    }

    const update: Prisma.UserUpdateManyMutationInput = {};
    if (data?.role !== undefined) {
      update.role = this.validateUserRole(String(data.role));
    }
    if (data?.isPro !== undefined) {
      if (typeof data.isPro === "boolean") {
        update.isPro = data.isPro;
      } else if (String(data.isPro).toLowerCase() === "true") {
        update.isPro = true;
      } else if (String(data.isPro).toLowerCase() === "false") {
        update.isPro = false;
      } else {
        throw new BadRequestException("isPro must be a boolean");
      }
    }

    if (!Object.keys(update).length) {
      throw new BadRequestException("At least one updatable field is required");
    }

    await this.prisma.user.updateMany({
      where: { id: { in: normalizedIds } },
      data: update,
    });

    const items = await this.prisma.user.findMany({
      where: { id: { in: normalizedIds } },
      select: {
        id: true,
        username: true,
        displayName: true,
        email: true,
        phone: true,
        role: true,
        isPro: true,
        bio: true,
        avatarUrl: true,
        authProvider: true,
        followerCount: true,
        followingCount: true,
        gameCount: true,
        totalPlays: true,
        createdAt: true,
        updatedAt: true,
      },
    });

    return {
      updated: items.length,
      ids: normalizedIds,
      items,
    };
  }

  async batchDeleteUsers(ids: unknown) {
    const normalizedIds = this.normalizeUserIds(ids);
    return this.deleteUsersByIds(normalizedIds);
  }

  async deleteUser(id: string) {
    const result = await this.deleteUsersByIds([id]);
    return {
      deleted: true,
      count: result.deleted,
      ids: result.ids,
    };
  }

  async resetUserPassword(id: string, newPassword: string) {
    const user = await this.prisma.user.findUnique({ where: { id } });
    if (!user) throw new NotFoundException("User not found");
    if (!newPassword || newPassword.length < 6) {
      throw new BadRequestException("Password must be at least 6 characters");
    }
    const hash = await bcrypt.hash(newPassword, 10);
    await this.prisma.user.update({
      where: { id },
      data: { passwordHash: hash },
    });
    return { success: true };
  }

  async createUser(data: any) {
    if (!data.username) throw new BadRequestException("Username is required");
    if (String(data.username).length > 32) {
      throw new BadRequestException("Username must be 32 characters or fewer");
    }
    const existing = await this.prisma.user.findUnique({
      where: { username: data.username },
    });
    if (existing) throw new BadRequestException("Username already exists");

    const passwordHash = data.password
      ? await bcrypt.hash(data.password, 10)
      : null;

    let user;
    try {
      user = await this.prisma.user.create({
        data: {
          id: randomUUID(),
          username: data.username,
          displayName: data.displayName || data.username,
          email: data.email || null,
          phone: data.phone || null,
          role: data.role || "user",
          bio: data.bio || null,
          passwordHash,
          authProvider: "email",
        },
      });
    } catch (error) {
      if (
        error instanceof Prisma.PrismaClientKnownRequestError &&
        error.code === "P2002"
      ) {
        throw new BadRequestException("Username already exists");
      }
      throw error;
    }
    return this.getUser(user.id);
  }

  async listSubscriptionPlans(from?: string, to?: string) {
    const range = this.parseDashboardDateRange(from, to);
    const paidOrderWhere = this.buildPaidSubscriptionOrderWhere(range);
    const now = new Date();

    const [
      plans,
      orderGroups,
      activeSubscriberGroups,
      activeSubscriberCount,
      paidOrderAggregate,
    ] = await Promise.all([
      this.prisma.subscriptionPlan.findMany({
        orderBy: [{ sortOrder: "asc" }, { price: "asc" }, { createdAt: "asc" }],
      }),
      this.prisma.subscriptionOrder.groupBy({
        by: ["planId"],
        where: paidOrderWhere,
        _count: { _all: true },
        _sum: { amount: true },
      }),
      this.prisma.userSubscription.groupBy({
        by: ["planId"],
        where: {
          status: UserSubscriptionStatus.active,
          expiresAt: { gt: now },
        },
        _count: { _all: true },
      }),
      this.prisma.userSubscription.count({
        where: {
          status: UserSubscriptionStatus.active,
          expiresAt: { gt: now },
        },
      }),
      this.prisma.subscriptionOrder.aggregate({
        where: paidOrderWhere,
        _sum: { amount: true },
        _count: { id: true },
      }),
    ]);

    const orderMap = new Map(
      orderGroups.map((group) => [
        group.planId,
        {
          orderCount: group._count?._all || 0,
          revenueCents: Number(group._sum?.amount || 0),
        },
      ]),
    );
    const activeMap = new Map(
      activeSubscriberGroups.map((group) => [
        group.planId,
        group._count?._all || 0,
      ]),
    );

    const items = plans.map((plan) =>
      this.presentSubscriptionPlan(plan, {
        ...orderMap.get(plan.id),
        activeSubscribers: activeMap.get(plan.id) || 0,
      }),
    );

    return {
      items,
      summary: {
        totalPlans: plans.length,
        activePlans: plans.filter((plan) => plan.active).length,
        activeSubscribers: activeSubscriberCount,
        paidOrderCount: paidOrderAggregate._count.id || 0,
        totalRevenueCents: Number(paidOrderAggregate._sum.amount || 0),
        totalRevenueYuan: Number(
          (Number(paidOrderAggregate._sum.amount || 0) / 100).toFixed(2),
        ),
        range: {
          from: range.from,
          to: range.to,
        },
      },
    };
  }

  async upsertSubscriptionPlan(id: string | undefined, body: any) {
    if (!body?.name || !String(body.name).trim()) {
      throw new BadRequestException("name is required");
    }

    const planId = id || this.buildSubscriptionPlanId(String(body.name).trim());
    const price = this.parseSubscriptionPlanPrice(body);
    const quota = Number.parseInt(String(body?.quota ?? ""), 10);
    const sortOrder = Number.parseInt(String(body?.sortOrder ?? "0"), 10);
    const period =
      body?.period === SubscriptionPeriod.yearly
        ? SubscriptionPeriod.yearly
        : SubscriptionPeriod.monthly;
    const currency =
      (body?.currency || "CNY").toString().trim().toUpperCase() || "CNY";
    const features = this.normalizeSubscriptionFeatures(body?.features);
    const recommended = body?.recommended === true;
    const active = body?.active !== false;
    const badge = body?.badge === "" ? null : body?.badge || null;

    if (!Number.isFinite(quota) || quota < 0) {
      throw new BadRequestException("quota must be a non-negative integer");
    }
    if (!Number.isFinite(sortOrder)) {
      throw new BadRequestException("sortOrder must be an integer");
    }

    const plan = await this.prisma.$transaction(async (tx) => {
      if (recommended) {
        await tx.subscriptionPlan.updateMany({
          where: { NOT: { id: planId } },
          data: { recommended: false },
        });
      }

      return tx.subscriptionPlan.upsert({
        where: { id: planId },
        create: {
          id: planId,
          name: String(body.name).trim(),
          description: body?.description
            ? String(body.description).trim()
            : null,
          price,
          currency,
          period,
          quota,
          features: features as unknown as Prisma.InputJsonValue,
          recommended,
          badge,
          sortOrder,
          active,
        },
        update: {
          name: String(body.name).trim(),
          description: body?.description
            ? String(body.description).trim()
            : null,
          price,
          currency,
          period,
          quota,
          features: features as unknown as Prisma.InputJsonValue,
          recommended,
          badge,
          sortOrder,
          active,
        },
      });
    });

    return this.presentSubscriptionPlan(plan);
  }

  async deleteSubscriptionPlan(id: string) {
    const [plan, orderCount, subscriptionCount] = await Promise.all([
      this.prisma.subscriptionPlan.findUnique({ where: { id } }),
      this.prisma.subscriptionOrder.count({ where: { planId: id } }),
      this.prisma.userSubscription.count({ where: { planId: id } }),
    ]);

    if (!plan) {
      throw new NotFoundException("Subscription plan not found");
    }

    if (orderCount > 0 || subscriptionCount > 0) {
      await this.prisma.subscriptionPlan.update({
        where: { id },
        data: {
          active: false,
          recommended: false,
        },
      });
      return {
        deleted: false,
        deactivated: true,
        reason:
          "Plan has historical orders or subscriptions and was archived instead of deleted",
      };
    }

    await this.prisma.subscriptionPlan.delete({ where: { id } });
    return {
      deleted: true,
      deactivated: false,
    };
  }

  // ===================== Admin Token Management =====================

  async changeAdminToken(currentToken: string, newToken: string) {
    const envToken = this.getAdminToken();
    if (currentToken !== envToken) {
      throw new BadRequestException("Current token is incorrect");
    }
    if (!newToken || newToken.length < 6) {
      throw new BadRequestException("New token must be at least 6 characters");
    }
    // Update the runtime env var (persists until restart)
    process.env.ADMIN_TOKEN = newToken;
    return {
      success: true,
      message:
        "Admin token updated (runtime only, update .env.production for persistence)",
    };
  }

  // ===================== Generation Logs =====================

  private asPlainObject(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  }

  private asPlainArray(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
  }

  private pickFirstString(...values: unknown[]): string | null {
    for (const value of values) {
      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }
    }
    return null;
  }

  private pickFirstNumber(...values: unknown[]): number | null {
    for (const value of values) {
      if (typeof value === "number" && Number.isFinite(value)) {
        return value;
      }
    }
    return null;
  }

  private pickFirstBoolean(...values: unknown[]): boolean | null {
    for (const value of values) {
      if (typeof value === "boolean") {
        return value;
      }
    }
    return null;
  }

  private pickFirstObject(
    ...values: unknown[]
  ): Record<string, unknown> | null {
    for (const value of values) {
      const record = this.asPlainObject(value);
      if (Object.keys(record).length > 0) {
        return record;
      }
    }
    return null;
  }

  private normalizeIssueItem(
    value: unknown,
    fallbackSeverity: "error" | "warning",
  ): Record<string, unknown> | null {
    if (typeof value === "string" && value.trim()) {
      return {
        message: value.trim(),
        severity: fallbackSeverity,
        family: "generic",
        blocking: fallbackSeverity !== "warning",
      };
    }

    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }

    const record = value as Record<string, unknown>;
    const message = this.pickFirstString(
      record.message,
      record.reason,
      record.title,
    );
    if (!message) {
      return null;
    }

    const severity =
      this.pickFirstString(record.severity, fallbackSeverity) ||
      fallbackSeverity;
    const family =
      this.pickFirstString(record.family, record.type) || "generic";
    const repairHint = this.pickFirstString(
      record.repairHint,
      record.repair_hint,
      record.hint,
    );
    const location = this.asPlainObject(record.location);

    return {
      message,
      severity,
      family,
      blocking:
        typeof record.blocking === "boolean"
          ? record.blocking
          : severity !== "warning",
      ...(repairHint ? { repairHint } : {}),
      ...(Object.keys(location).length > 0 ? { location } : {}),
    };
  }

  private summarizeIssueItems(
    items: Record<string, unknown>[],
  ): Record<string, unknown> | null {
    if (!items.length) {
      return null;
    }

    const warningCount = items.filter(
      (item) => String(item.severity || "").toLowerCase() === "warning",
    ).length;
    const errorCount = items.length - warningCount;
    const blockingCount = items.filter(
      (item) => item.blocking !== false,
    ).length;

    return {
      items,
      errorCount,
      warningCount,
      blockingCount,
    };
  }

  private buildIssueListFromArrays(
    errorsValue: unknown,
    warningsValue: unknown,
  ): Record<string, unknown> | null {
    const items = [
      ...this.asPlainArray(errorsValue)
        .map((item) => this.normalizeIssueItem(item, "error"))
        .filter((item): item is Record<string, unknown> => Boolean(item)),
      ...this.asPlainArray(warningsValue)
        .map((item) => this.normalizeIssueItem(item, "warning"))
        .filter((item): item is Record<string, unknown> => Boolean(item)),
    ];

    return this.summarizeIssueItems(items);
  }

  private normalizeIssueList(value: unknown): Record<string, unknown> | null {
    const record = this.asPlainObject(value);
    if (!Object.keys(record).length) {
      return null;
    }

    const normalizedItems = this.asPlainArray(record.items)
      .map((item) => this.normalizeIssueItem(item, "error"))
      .filter((item): item is Record<string, unknown> => Boolean(item));

    if (normalizedItems.length > 0) {
      const fallbackSummary = this.summarizeIssueItems(normalizedItems) || {
        items: normalizedItems,
        errorCount: 0,
        warningCount: 0,
        blockingCount: 0,
      };
      return {
        items: normalizedItems,
        errorCount:
          this.pickFirstNumber(record.errorCount, record.error_count) ??
          fallbackSummary.errorCount,
        warningCount:
          this.pickFirstNumber(record.warningCount, record.warning_count) ??
          fallbackSummary.warningCount,
        blockingCount:
          this.pickFirstNumber(record.blockingCount, record.blocking_count) ??
          fallbackSummary.blockingCount,
      };
    }

    return this.buildIssueListFromArrays(record.errors, record.warnings);
  }

  private mergeIssueLists(
    issueLists: Array<Record<string, unknown> | null | undefined>,
  ): Record<string, unknown> | null {
    const seen = new Set<string>();
    const mergedItems: Record<string, unknown>[] = [];

    for (const issueList of issueLists) {
      const normalizedIssueList = this.normalizeIssueList(issueList);
      if (!normalizedIssueList) {
        continue;
      }
      for (const item of this.asPlainArray(normalizedIssueList.items)) {
        const normalizedItem = this.normalizeIssueItem(item, "error");
        if (!normalizedItem) {
          continue;
        }
        const key = JSON.stringify({
          message: normalizedItem.message ?? "",
          severity: normalizedItem.severity ?? "",
          family: normalizedItem.family ?? "",
          blocking: normalizedItem.blocking !== false,
          repairHint: normalizedItem.repairHint ?? "",
          location: this.asPlainObject(normalizedItem.location),
        });
        if (seen.has(key)) {
          continue;
        }
        seen.add(key);
        mergedItems.push(normalizedItem);
      }
    }

    return this.summarizeIssueItems(mergedItems);
  }

  private extractIssueListFromReport(
    reportValue: unknown,
  ): Record<string, unknown> | null {
    const report = this.asPlainObject(reportValue);
    if (!Object.keys(report).length) {
      return null;
    }

    return (
      this.normalizeIssueList(report.issueList ?? report.issue_list) ||
      this.buildIssueListFromArrays(report.errors, report.warnings)
    );
  }

  private buildQaArtifactSummary(artifact: any) {
    const report = this.asPlainObject(artifact?.payloadJson);
    return {
      id: artifact.id,
      artifactType: artifact.artifactType,
      createdAt: artifact.createdAt,
      report,
      issueList: this.extractIssueListFromReport(report),
    };
  }

  private buildTaskDiagnostics(
    task: any,
    sourceBundle?: any,
    qaArtifacts: any[] = [],
  ) {
    const summary = this.asPlainObject(task?.resultSummary);
    const metadata = this.asPlainObject(task?.metadata);
    const bundleMeta = this.asPlainObject(sourceBundle?.metadata);
    const intentBuild = normalizeIntentBuildSnapshot(metadata.intentBuild);
    const runtimeQaReport = this.pickFirstObject(
      summary.runtimeQaReport,
      bundleMeta.runtimeQaReport,
    );
    const qaWarnings = this.asPlainArray(summary.qaWarnings);
    const qaArtifactSummaries = qaArtifacts.map((artifact) =>
      this.buildQaArtifactSummary(artifact),
    );
    const issueList = this.mergeIssueLists([
      this.extractIssueListFromReport(summary),
      ...qaArtifactSummaries.map((artifact) => artifact.issueList),
    ]);

    return {
      intentBuild,
      generationTier: this.pickFirstString(
        summary.generationTier,
        summary.generation_tier,
        metadata.generationTier,
        metadata.generation_tier,
        bundleMeta.generationTier,
        bundleMeta.generation_tier,
      ),
      qaWarnings,
      runtimeQaReport,
      runtimeQaUnavailable: this.pickFirstBoolean(
        summary.runtimeQaUnavailable,
        bundleMeta.runtimeQaUnavailable,
        runtimeQaReport?.unavailableKind ? true : null,
      ),
      runtimeQaUnavailableKind: this.pickFirstString(
        summary.runtimeQaUnavailableKind,
        bundleMeta.runtimeQaUnavailableKind,
        runtimeQaReport?.unavailableKind,
      ),
      runtimeQaUnavailablePhase: this.pickFirstString(
        summary.runtimeQaUnavailablePhase,
        bundleMeta.runtimeQaUnavailablePhase,
        runtimeQaReport?.unavailablePhase,
      ),
      runtimeQaUnavailableReason: this.pickFirstString(
        summary.runtimeQaUnavailableReason,
        bundleMeta.runtimeQaUnavailableReason,
        runtimeQaReport?.unavailableReason,
      ),
      issueList,
      qaArtifacts: qaArtifactSummaries,
    };
  }

  private deriveGenerationLogStatus(
    gameStatus: GameStatus,
    taskStatus?: GenerationTaskStatus | null,
  ): GameStatus {
    if (gameStatus === GameStatus.banned) {
      return GameStatus.banned;
    }

    if (
      taskStatus === GenerationTaskStatus.queued ||
      taskStatus === GenerationTaskStatus.running
    ) {
      return GameStatus.generating;
    }

    if (
      taskStatus === GenerationTaskStatus.failed ||
      taskStatus === GenerationTaskStatus.timed_out ||
      taskStatus === GenerationTaskStatus.canceled
    ) {
      return GameStatus.failed;
    }

    if (taskStatus === GenerationTaskStatus.succeeded) {
      if (
        gameStatus === GameStatus.published ||
        gameStatus === GameStatus.review
      ) {
        return gameStatus;
      }
      return GameStatus.draft;
    }

    return gameStatus;
  }

  private buildGenerationLogItem(game: any) {
    const bundle = game.bundles?.[0] || null;
    const task = game.generationTasks?.[0] || null;
    const summary = this.asPlainObject(task?.resultSummary);
    const taskMetadata = this.asPlainObject(task?.metadata);
    const bundleMeta = this.asPlainObject(bundle?.metadata);
    const diagnostics = this.buildTaskDiagnostics(task, bundle);
    const previewUrls = this.gameService.buildAdminPreviewUrls(game.id);
    const taskHasError =
      task &&
      (task.status === GenerationTaskStatus.failed ||
        task.status === GenerationTaskStatus.timed_out ||
        task.status === GenerationTaskStatus.canceled);

    return {
      gameId: game.id,
      taskId: task?.id ?? null,
      taskStatus: task?.status ?? null,
      title: game.title,
      description: game.description,
      status: this.deriveGenerationLogStatus(game.status, task?.status),
      failedStage: task ? task.failedStage || null : game.failedStage || null,
      failedReason: task
        ? task.errorMessage || null
        : game.failedReason || null,
      retryCount: task ? (task.retryCount ?? 0) : game.retryCount,
      lastErrorAt: task
        ? taskHasError
          ? task.completedAt || null
          : null
        : game.lastErrorAt || null,
      gameType: normalizeGameType(
        this.pickFirstString(
          summary.gameType,
          bundleMeta.gameType,
          game.gameType,
        ),
        game.title,
        game.description,
        game.tags,
      ),
      createdAt: task?.createdAt || bundle?.createdAt || game.createdAt,
      updatedAt: task?.updatedAt || game.updatedAt,
      author: game.author,
      strategy: this.pickFirstString(summary.strategy, bundleMeta.strategy),
      generationTier: diagnostics.generationTier,
      qaPassed: this.pickFirstBoolean(summary.qaPassed, bundleMeta.qaPassed),
      qaRetries: this.pickFirstNumber(summary.qaRetries, bundleMeta.qaRetries),
      iterationRetries: this.pickFirstNumber(
        summary.iterationRetries,
        bundleMeta.iterationRetries,
      ),
      qaWarningCount: diagnostics.qaWarnings.length,
      runtimeQaUnavailable: diagnostics.runtimeQaUnavailable,
      runtimeQaUnavailableKind: diagnostics.runtimeQaUnavailableKind,
      runtimeQaUnavailableReason: diagnostics.runtimeQaUnavailableReason,
      intentBrief: diagnostics.intentBuild?.brief || null,
      intentFingerprint: diagnostics.intentBuild?.intentFingerprint || null,
      specFingerprint: diagnostics.intentBuild?.specFingerprint || null,
      genTimeMs: this.pickFirstNumber(
        summary.generationTimeMs,
        summary.genTimeMs,
        bundleMeta.generationTimeMs,
        bundleMeta.genTimeMs,
      ),
      codeSizeBytes: this.pickFirstNumber(
        summary.codeSizeBytes,
        bundle?.codeSizeBytes,
      ),
      qualityScore: this.pickFirstNumber(
        summary.qualityScore,
        bundleMeta.qualityScore,
      ),
      version:
        this.pickFirstNumber(summary.version, bundle?.version, game.version) ||
        0,
      previewUrl: previewUrls.previewUrl,
      gameUrl: previewUrls.gameUrl,
    };
  }

  async listGenerationLogs(
    page: number,
    limit: number,
    status?: string,
    search?: string,
  ) {
    const filters: Prisma.GameWhereInput[] = [];

    if (status && status !== "all") {
      if (status === "failed") {
        filters.push({
          OR: [
            { status: "failed" as any },
            { failedStage: { not: null } },
            { failedReason: { not: null } },
            {
              generationTasks: {
                some: {
                  status: {
                    in: [
                      GenerationTaskStatus.failed,
                      GenerationTaskStatus.timed_out,
                      GenerationTaskStatus.canceled,
                    ],
                  },
                },
              },
            },
          ],
        });
      } else if (status === "generating") {
        filters.push({
          OR: [
            { status: status as any },
            {
              generationTasks: {
                some: {
                  status: {
                    in: [
                      GenerationTaskStatus.queued,
                      GenerationTaskStatus.running,
                    ],
                  },
                },
              },
            },
          ],
        });
      } else {
        filters.push({ status: status as any });
      }
    }
    if (search) {
      filters.push({
        OR: [
          { id: { contains: search } },
          { title: { contains: search } },
          { description: { contains: search } },
          { author: { username: { contains: search } } },
          { author: { displayName: { contains: search } } },
          {
            generationTasks: {
              some: {
                id: { contains: search },
              },
            },
          },
        ],
      });
    }

    const where: Prisma.GameWhereInput =
      filters.length > 0 ? { AND: filters } : {};

    const [games, total] = await Promise.all([
      this.prisma.game.findMany({
        where,
        include: {
          author: {
            select: { id: true, username: true, displayName: true },
          },
          bundles: {
            select: {
              id: true,
              version: true,
              metadata: true,
              generationMeta: true,
              codeSizeBytes: true,
              createdAt: true,
            },
            orderBy: { version: "desc" },
            take: 1,
          },
          generationTasks: {
            select: {
              id: true,
              status: true,
              failedStage: true,
              errorMessage: true,
              retryCount: true,
              resultSummary: true,
              metadata: true,
              previewUrl: true,
              createdAt: true,
              updatedAt: true,
              completedAt: true,
            },
            orderBy: { createdAt: "desc" },
            take: 1,
          },
        },
        orderBy: { createdAt: "desc" },
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.prisma.game.count({ where }),
    ]);

    const items = games.map((game) => this.buildGenerationLogItem(game));

    return { items, total, page, limit, totalPages: Math.ceil(total / limit) };
  }

  async listGenerationTasks(
    page: number,
    limit: number,
    status?: string,
    search?: string,
  ) {
    const where: Prisma.GenerationTaskWhereInput = {};

    if (status && status !== "all") {
      where.status = status as any;
    }
    if (search) {
      where.OR = [
        { id: { contains: search } },
        { gameId: { contains: search } },
        { user: { username: { contains: search } } },
        { user: { displayName: { contains: search } } },
        { game: { title: { contains: search } } },
      ];
    }

    const [tasks, total] = await Promise.all([
      this.prisma.generationTask.findMany({
        where,
        include: {
          game: { select: { id: true, title: true, status: true } },
          user: { select: { id: true, username: true, displayName: true } },
        },
        orderBy: { createdAt: "desc" },
        skip: (page - 1) * limit,
        take: limit,
      }),
      this.prisma.generationTask.count({ where }),
    ]);

    const resolvedTasks = await Promise.all(
      tasks.map(async (task: any) => {
        const reconciled = await this.gameService.reconcileGenerationTask(task);
        const gameId = String(reconciled?.gameId || task.gameId || "").trim();
        return {
          ...task,
          ...(reconciled || {}),
          game: reconciled?.game || task.game,
          user: task.user,
          ...(gameId ? this.gameService.buildAdminPreviewUrls(gameId) : {}),
        };
      }),
    );

    return {
      items: resolvedTasks,
      total,
      page,
      limit,
      totalPages: Math.ceil(total / limit),
    };
  }

  async getGenerationTask(taskId: string) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
      include: {
        game: {
          select: {
            id: true,
            title: true,
            status: true,
            description: true,
            createdAt: true,
            updatedAt: true,
            publishedAt: true,
            failedStage: true,
            failedReason: true,
            bundles: {
              select: {
                id: true,
                version: true,
                htmlCode: true,
                cssCode: true,
                jsCode: true,
                metadata: true,
                generationMeta: true,
                codeSizeBytes: true,
                createdAt: true,
              },
              orderBy: { version: "desc" },
              take: 1,
            },
          },
        },
        user: { select: { id: true, username: true, displayName: true } },
        events: {
          orderBy: { createdAt: "asc" },
          take: 300,
        },
        llmCallLogs: {
          orderBy: { createdAt: "asc" },
          take: 300,
        },
      },
    });

    if (!task) {
      throw new NotFoundException("Generation task not found");
    }

    const [reconciled, qaArtifacts] = await Promise.all([
      this.gameService.reconcileGenerationTask(task),
      this.prisma.generationArtifact
        .findMany({
          where: {
            taskId,
            artifactType: {
              in: ["contract_qa_report", "runtime_qa_report"],
            },
          },
          select: {
            id: true,
            artifactType: true,
            payloadJson: true,
            createdAt: true,
          },
          orderBy: [{ createdAt: "desc" }],
          take: 8,
        })
        .catch(() => []),
    ]);
    if (
      reconciled &&
      (reconciled.status !== task.status ||
        reconciled.progressStage !== task.progressStage ||
        reconciled.failedStage !== task.failedStage)
    ) {
      const refreshed = await this.prisma.generationTask.findUnique({
        where: { id: taskId },
        include: {
          game: {
            select: {
              id: true,
              title: true,
              status: true,
              description: true,
              createdAt: true,
              updatedAt: true,
              publishedAt: true,
              failedStage: true,
              failedReason: true,
              bundles: {
                select: {
                  id: true,
                  version: true,
                  htmlCode: true,
                  cssCode: true,
                  jsCode: true,
                  metadata: true,
                  generationMeta: true,
                  codeSizeBytes: true,
                  createdAt: true,
                },
                orderBy: { version: "desc" },
                take: 1,
              },
            },
          },
          user: { select: { id: true, username: true, displayName: true } },
          events: {
            orderBy: { createdAt: "asc" },
            take: 300,
          },
          llmCallLogs: {
            orderBy: { createdAt: "asc" },
            take: 300,
          },
        },
      });
      if (refreshed) {
        const mergedGame = refreshed.game;
        const diagnostics = this.buildTaskDiagnostics(
          refreshed,
          mergedGame?.bundles?.[0] || null,
          qaArtifacts,
        );
        return {
          ...refreshed,
          inputPrompt: mergedGame?.description || null,
          sourceBundle: mergedGame?.bundles?.[0] || null,
          ...diagnostics,
          ...this.gameService.buildAdminPreviewUrls(refreshed.gameId),
        };
      }
    }

    const gameId = String((reconciled || task).gameId || "").trim();
    const mergedGame = {
      ...(task.game || {}),
      ...(reconciled?.game || {}),
    } as any;
    const diagnostics = this.buildTaskDiagnostics(
      { ...task, ...(reconciled || {}) },
      mergedGame?.bundles?.[0] || null,
      qaArtifacts,
    );

    return {
      ...task,
      ...(reconciled || {}),
      game: mergedGame,
      user: task.user,
      events: task.events,
      llmCallLogs: task.llmCallLogs,
      inputPrompt: mergedGame?.description || null,
      sourceBundle: mergedGame?.bundles?.[0] || null,
      ...diagnostics,
      ...(gameId ? this.gameService.buildAdminPreviewUrls(gameId) : {}),
    };
  }

  async terminateGenerationTask(taskId: string, reason?: string) {
    return this.gameService.terminateTask(taskId, {
      admin: true,
      reason: reason || "Task terminated by admin",
    });
  }

  async listGenerationTaskEvents(taskId: string, limit = 100) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
      select: { id: true },
    });

    if (!task) {
      throw new NotFoundException("Generation task not found");
    }

    return {
      items: await this.prisma.generationTaskEvent.findMany({
        where: { taskId },
        orderBy: { createdAt: "asc" },
        take: Math.max(1, Math.min(limit, 500)),
      }),
    };
  }

  async listGenerationTaskArtifacts(taskId: string, limit = 100) {
    const task = await this.prisma.generationTask.findUnique({
      where: { id: taskId },
      select: { id: true },
    });

    if (!task) {
      throw new NotFoundException("Generation task not found");
    }

    return {
      items: await this.prisma.generationArtifact.findMany({
        where: { taskId },
        orderBy: { createdAt: "desc" },
        take: Math.max(1, Math.min(limit, 500)),
      }),
    };
  }

  async listCloudAccounts() {
    return this.prisma.cloudProviderAccount.findMany({
      orderBy: [{ enabled: "desc" }, { createdAt: "asc" }],
    });
  }

  async listCloudRegions() {
    return this.prisma.cloudRegionCatalog.findMany({
      where: {
        regionCode: process.env.DATABASE_SCHEMA_MANAGED === 'true'
          ? process.env.GAMEVALLIES_CLOUD_REGION || 'cn-hongkong'
          : "cn-shanghai",
      },
      include: {
        account: {
          select: {
            id: true,
            vendor: true,
            accountKey: true,
            displayName: true,
            enabled: true,
          },
        },
      },
      orderBy: [{ vendor: "asc" }, { regionCode: "asc" }],
    });
  }

  async listAiEngineRegionTargets(params?: {
    providerSelectableOnly?: boolean;
  }) {
    const where: Prisma.AiEngineRegionTargetWhereInput = {
      executionRegion: "cn_shanghai",
    };
    if (params?.providerSelectableOnly) {
      where.deployEnabled = true;
      where.deployStatus = "deployed";
    }

    const targets = await this.prisma.aiEngineRegionTarget.findMany({
      where,
      include: {
        account: {
          select: {
            id: true,
            vendor: true,
            accountKey: true,
            displayName: true,
          },
        },
        regionCatalog: {
          select: {
            id: true,
            regionCode: true,
            regionName: true,
            regionGroup: true,
            deploySupported: true,
            enabled: true,
          },
        },
      },
      orderBy: [{ executionRegion: "asc" }, { createdAt: "asc" }],
    });

    const enrichedTargets = targets.map((target) => ({
      ...target,
      resolvedAiEngineUrl:
        target.aiEngineUrl ||
        this.getConfiguredAiEngineAdminBaseUrlForRegion(
          target.executionRegion,
        ) ||
        null,
    }));

    if (params?.providerSelectableOnly) {
      return enrichedTargets.filter(
        (target) =>
          target.deployEnabled !== false &&
          target.deployStatus === "deployed" &&
          Boolean(target.resolvedAiEngineUrl),
      );
    }

    return enrichedTargets;
  }

  async upsertAiEngineRegionTarget(id: string | undefined, body: any) {
    if (!body?.accountId) {
      throw new BadRequestException("accountId is required");
    }
    if (!body?.regionCatalogId) {
      throw new BadRequestException("regionCatalogId is required");
    }
    if (!body?.executionRegion) {
      throw new BadRequestException("executionRegion is required");
    }
    if (!body?.displayName) {
      throw new BadRequestException("displayName is required");
    }
    if (!body?.functionName) {
      throw new BadRequestException("functionName is required");
    }

    const [account, regionCatalog, existing] = await Promise.all([
      this.prisma.cloudProviderAccount.findUnique({
        where: { id: body.accountId },
      }),
      this.prisma.cloudRegionCatalog.findUnique({
        where: { id: body.regionCatalogId },
      }),
      id
        ? this.prisma.aiEngineRegionTarget.findUnique({ where: { id } })
        : Promise.resolve(null),
    ]);

    if (!account || !account.enabled) {
      throw new BadRequestException("Cloud account not found or disabled");
    }
    if (!regionCatalog || !regionCatalog.enabled) {
      throw new BadRequestException("Cloud region not found or disabled");
    }
    if (regionCatalog.accountId !== account.id) {
      throw new BadRequestException(
        "regionCatalogId does not belong to the selected account",
      );
    }
    if (body.executionRegion !== "cn_shanghai") {
      throw new BadRequestException("executionRegion must be cn_shanghai");
    }
    const expectedCloudRegionCode = "cn-shanghai";
    if (regionCatalog.regionCode !== expectedCloudRegionCode) {
      throw new BadRequestException(
        `regionCatalogId does not match executionRegion=${body.executionRegion}`,
      );
    }
    if (existing && existing.executionRegion !== body.executionRegion) {
      throw new BadRequestException(
        "executionRegion cannot be changed after creation",
      );
    }

    const explicitAiEngineUrl =
      body?.aiEngineUrl === undefined
        ? undefined
        : (body.aiEngineUrl || "").trim() || null;
    const explicitDeployStatus =
      body?.deployStatus === undefined
        ? undefined
        : String(body.deployStatus || "").trim() || null;
    const explicitLastDeployedAt = body?.lastDeployedAt
      ? new Date(body.lastDeployedAt)
      : undefined;

    const targetId = id || randomUUID();
    return this.prisma.aiEngineRegionTarget.upsert({
      where: { id: targetId },
      create: {
        id: targetId,
        accountId: account.id,
        regionCatalogId: regionCatalog.id,
        vendor: account.vendor,
        cloudRegionCode: regionCatalog.regionCode,
        executionRegion: body.executionRegion,
        displayName: body.displayName,
        functionName: body.functionName,
        registry: body.registry || account.defaultRegistry || "",
        registryNamespace:
          body.registryNamespace || account.defaultRegistryNamespace || "",
        imageRepository: body.imageRepository || body.functionName,
        serviceRegionEnv: body.serviceRegionEnv || body.executionRegion,
        aiEngineUrl: explicitAiEngineUrl ?? null,
        deployEnabled: body.deployEnabled !== false,
        deployStatus:
          explicitDeployStatus ||
          existing?.deployStatus ||
          (explicitAiEngineUrl ? "deployed" : "pending"),
        lastRevision: body?.lastRevision ?? existing?.lastRevision ?? null,
        lastImageTag: body?.lastImageTag ?? existing?.lastImageTag ?? null,
        lastReleaseStatus:
          body?.lastReleaseStatus ?? existing?.lastReleaseStatus ?? null,
        lastDeployError:
          body?.lastDeployError ?? existing?.lastDeployError ?? null,
        lastDeployedAt:
          explicitLastDeployedAt ??
          existing?.lastDeployedAt ??
          (explicitAiEngineUrl ? new Date() : null),
      },
      update: {
        accountId: account.id,
        regionCatalogId: regionCatalog.id,
        vendor: account.vendor,
        cloudRegionCode: regionCatalog.regionCode,
        displayName: body.displayName,
        functionName: body.functionName,
        registry: body.registry || account.defaultRegistry || "",
        registryNamespace:
          body.registryNamespace || account.defaultRegistryNamespace || "",
        imageRepository: body.imageRepository || body.functionName,
        serviceRegionEnv:
          body.serviceRegionEnv ||
          existing?.serviceRegionEnv ||
          body.executionRegion,
        aiEngineUrl:
          explicitAiEngineUrl !== undefined
            ? explicitAiEngineUrl
            : (existing?.aiEngineUrl ?? null),
        deployEnabled: body.deployEnabled !== false,
        deployStatus:
          explicitDeployStatus ||
          existing?.deployStatus ||
          (explicitAiEngineUrl ? "deployed" : "pending"),
        lastRevision: body?.lastRevision ?? existing?.lastRevision ?? null,
        lastImageTag: body?.lastImageTag ?? existing?.lastImageTag ?? null,
        lastReleaseStatus:
          body?.lastReleaseStatus ?? existing?.lastReleaseStatus ?? null,
        lastDeployError:
          body?.lastDeployError ?? existing?.lastDeployError ?? null,
        lastDeployedAt:
          explicitLastDeployedAt ??
          existing?.lastDeployedAt ??
          (explicitAiEngineUrl ? new Date() : null),
      },
      include: {
        account: {
          select: {
            id: true,
            accountKey: true,
            displayName: true,
            vendor: true,
          },
        },
        regionCatalog: {
          select: {
            id: true,
            regionCode: true,
            regionName: true,
            regionGroup: true,
          },
        },
      },
    });
  }

  async syncAiEngineRegionTargetDeployState(body: any) {
    if (!body?.executionRegion) {
      throw new BadRequestException("executionRegion is required");
    }
    const executionRegion = this.normalizeExecutionRegion(
      body?.executionRegion,
    );
    const existing = await this.prisma.aiEngineRegionTarget.findFirst({
      where: { executionRegion },
    });

    if (!existing) {
      throw new NotFoundException(
        `Region target not found for executionRegion=${executionRegion}`,
      );
    }

    const nextAiEngineUrl =
      body?.aiEngineUrl === undefined
        ? existing.aiEngineUrl
        : (body.aiEngineUrl || "").trim() || null;
    const nextDeployStatus =
      body?.deployStatus ||
      (body?.lastDeployError
        ? "failed"
        : nextAiEngineUrl
          ? "deployed"
          : existing.deployStatus || "pending");
    const nextLastDeployedAt =
      nextDeployStatus === "deployed"
        ? new Date(body?.lastDeployedAt || new Date())
        : body?.lastDeployedAt
          ? new Date(body.lastDeployedAt)
          : existing.lastDeployedAt;

    return this.prisma.aiEngineRegionTarget.update({
      where: { id: existing.id },
      data: {
        aiEngineUrl: nextAiEngineUrl,
        deployStatus: nextDeployStatus,
        lastRevision: body?.lastRevision ?? existing.lastRevision,
        lastImageTag: body?.lastImageTag ?? existing.lastImageTag,
        lastReleaseStatus:
          body?.lastReleaseStatus ?? existing.lastReleaseStatus,
        lastDeployError: body?.lastDeployError ?? null,
        lastDeployedAt: nextLastDeployedAt,
      },
      include: {
        account: {
          select: {
            id: true,
            vendor: true,
            accountKey: true,
            displayName: true,
          },
        },
        regionCatalog: {
          select: {
            id: true,
            regionCode: true,
            regionName: true,
            regionGroup: true,
          },
        },
      },
    });
  }

  async listLlmProviders() {
    const providers = await this.prisma.llmGatewayProvider.findMany({
      include: {
        regionTarget: {
          select: {
            id: true,
            displayName: true,
            executionRegion: true,
            aiEngineUrl: true,
            deployStatus: true,
            deployEnabled: true,
          },
        },
        testRecords: {
          orderBy: {
            testedAt: "desc",
          },
          take: 1,
          select: {
            success: true,
            latencyMs: true,
            httpStatus: true,
            errorMessage: true,
            model: true,
            testedAt: true,
          },
        },
      },
      orderBy: [{ priority: "asc" }, { updatedAt: "desc" }],
    });

    return providers.map((provider) => this.presentLlmProvider(provider));
  }

  private getLlmStepFlowMeta(stepKey: string): LlmStepFlowMeta {
    return (
      LLM_STEP_FLOW_META[stepKey] || {
        flowGroup: "Flow 99 - Unclassified",
        flowOrder: 999,
        flowSummary: "Unclassified step",
        triggerSummary:
          "Present in the gateway catalog but not mapped to the current game generation flow.",
        journeys: ["unclassified"],
        journeySummary: "Unclassified",
        optional: true,
      }
    );
  }

  private sortLlmFlowRows<
    T extends { flowOrder?: number | null; stepKey: string },
  >(rows: T[]): T[] {
    return [...rows].sort((left, right) => {
      const leftOrder = Number.isFinite(Number(left.flowOrder))
        ? Number(left.flowOrder)
        : 999;
      const rightOrder = Number.isFinite(Number(right.flowOrder))
        ? Number(right.flowOrder)
        : 999;
      if (leftOrder !== rightOrder) {
        return leftOrder - rightOrder;
      }
      return left.stepKey.localeCompare(right.stepKey);
    });
  }

  private isVisibleGameGenerationLlmStep(stepKey: string): boolean {
    const meta = this.getLlmStepFlowMeta(stepKey);
    return (
      !meta.journeys.includes("auxiliary") &&
      !meta.journeys.includes("unclassified")
    );
  }

  private getLlmRouteLookupCandidates(
    stepKey: string,
  ): Array<{ stepKey: string; routeMatchStrategy: string }> {
    const candidates: Array<{ stepKey: string; routeMatchStrategy: string }> = [
      { stepKey, routeMatchStrategy: "exact" },
    ];
    let parentStepKey = stepKey;
    while (parentStepKey.includes(".")) {
      parentStepKey = parentStepKey.slice(0, parentStepKey.lastIndexOf("."));
      candidates.push({
        stepKey: parentStepKey,
        routeMatchStrategy: "parent_step",
      });
    }
    return candidates;
  }

  private sortLlmProvidersForRuntime(
    providers: any[],
    executionRegion: string,
  ): any[] {
    const sorted = [...providers].sort((left, right) => {
      const leftPriority = Number.isFinite(Number(left?.priority))
        ? Number(left.priority)
        : 100;
      const rightPriority = Number.isFinite(Number(right?.priority))
        ? Number(right.priority)
        : 100;
      if (leftPriority !== rightPriority) {
        return leftPriority - rightPriority;
      }
      const leftUpdatedAt = left?.updatedAt
        ? new Date(left.updatedAt).getTime()
        : 0;
      const rightUpdatedAt = right?.updatedAt
        ? new Date(right.updatedAt).getTime()
        : 0;
      if (leftUpdatedAt !== rightUpdatedAt) {
        return rightUpdatedAt - leftUpdatedAt;
      }
      return String(left?.id || "").localeCompare(String(right?.id || ""));
    });
    return [
      ...sorted.filter((provider) => provider?.region === executionRegion),
      ...sorted.filter((provider) => provider?.region !== executionRegion),
    ];
  }

  private resolveLlmRouteBinding(
    stepKey: string,
    executionRegion: string,
    routeMap: Map<string, any>,
    runtimeProviders: any[],
  ) {
    const exactRoute = routeMap.get(stepKey) || null;
    const runtimeProviderMap = new Map(
      runtimeProviders.map((provider) => [provider.id, provider]),
    );
    for (const candidate of this.getLlmRouteLookupCandidates(stepKey)) {
      const candidateRoute = routeMap.get(candidate.stepKey);
      if (!candidateRoute || candidateRoute.enabled === false) {
        continue;
      }
      const configuredProviderIds = Array.from(
        new Set(
          [
            candidateRoute.providerId,
            ...this.normalizeLlmFallbackProviderIds(
              candidateRoute.fallbackProviderIds,
            ),
          ].filter(Boolean),
        ),
      );
      const configuredProviders = configuredProviderIds
        .map((providerId) => runtimeProviderMap.get(providerId))
        .filter((provider): provider is any => Boolean(provider));
      const effectiveProvider = configuredProviders[0] || null;
      if (!effectiveProvider) {
        return {
          exactRoute,
          effectiveRoute: candidateRoute,
          effectiveProvider: null,
          matchedStepKey: candidate.stepKey,
          routeMatchStrategy: candidate.routeMatchStrategy,
          routeBindingState: "invalid_provider",
          bindingNote:
            candidate.routeMatchStrategy === "exact"
              ? "启用路由绑定到了已禁用或不存在的 Provider；运行时会离开显式路由并尝试环境兜底，若环境兜底不可用则失败。"
              : `父级步骤 ${candidate.stepKey} 的启用路由绑定到了已禁用或不存在的 Provider；运行时会离开显式路由并尝试环境兜底，若环境兜底不可用则失败。`,
        };
      }
      const usingExplicitFallback =
        effectiveProvider.id !== candidateRoute.providerId;

      if (candidate.routeMatchStrategy === "exact") {
        return {
          exactRoute,
          effectiveRoute: candidateRoute,
          effectiveProvider,
          matchedStepKey: candidate.stepKey,
          routeMatchStrategy: candidate.routeMatchStrategy,
          routeBindingState: usingExplicitFallback
            ? "fallback_active"
            : "configured",
          bindingNote: usingExplicitFallback
            ? `主 Provider 当前不可用，运行时会回退到显式 fallback ${effectiveProvider.name}。`
            : null,
        };
      }

      return {
        exactRoute,
        effectiveRoute: candidateRoute,
        effectiveProvider,
        matchedStepKey: candidate.stepKey,
        routeMatchStrategy: candidate.routeMatchStrategy,
        routeBindingState: usingExplicitFallback
          ? "fallback_active"
          : "inherited",
        bindingNote: usingExplicitFallback
          ? `当前步骤会继承父级步骤 ${candidate.stepKey}，并在主 Provider 不可用时回退到显式 fallback ${effectiveProvider.name}。`
          : exactRoute && exactRoute.enabled === false
            ? `当前步骤自己的路由已禁用，运行时会回退到父级步骤 ${candidate.stepKey}。`
            : `当前步骤没有单独路由，运行时会继承父级步骤 ${candidate.stepKey}。`,
      };
    }

    const providerPoolFallback =
      this.sortLlmProvidersForRuntime(runtimeProviders, executionRegion)[0] ||
      null;
    if (providerPoolFallback) {
      return {
        exactRoute,
        effectiveRoute: null,
        effectiveProvider: providerPoolFallback,
        matchedStepKey: null,
        routeMatchStrategy: "provider_pool",
        routeBindingState: "provider_pool_fallback",
        bindingNote:
          exactRoute && exactRoute.enabled === false
            ? "当前步骤路由已禁用，且没有可继承的父级路由；运行时会退回到启用中的 Provider 池，可运行但不可精确控模。"
            : "当前步骤没有精确或父级路由；运行时会退回到启用中的 Provider 池，可运行但不可精确控模。",
      };
    }

    return {
      exactRoute,
      effectiveRoute: null,
      effectiveProvider: null,
      matchedStepKey: null,
      routeMatchStrategy: "none",
      routeBindingState: "missing",
      bindingNote:
        exactRoute && exactRoute.enabled === false
          ? "当前步骤路由已禁用，且当前区域没有可用的继承路由或 Provider 池。"
          : "当前步骤没有可用的精确路由、父级路由或 Provider 池。",
    };
  }

  private presentLlmRouteRow(
    step: any,
    executionRegion: string,
    routeMap: Map<string, any>,
    runtimeProviders: any[],
  ) {
    const meta = this.getLlmStepFlowMeta(step.stepKey);
    const bindingRequired =
      !meta.optional && meta.flowGroup !== "Flow 90 - Auxiliary";
    const binding = this.resolveLlmRouteBinding(
      step.stepKey,
      executionRegion,
      routeMap,
      runtimeProviders,
    );
    const exactRoute = binding.exactRoute;
    const exactProvider = exactRoute?.provider || null;
    const effectiveProvider = binding.effectiveProvider || null;
    const outputMeta = this.getLlmStepOutputMeta(step);
    const requiredCapabilities = this.getLlmRequiredCapabilities(step.stepKey);
    const exactProviderReadiness = exactProvider
      ? this.buildLlmProviderReadiness(exactProvider, step.stepKey, outputMeta)
      : null;
    const effectiveProviderReadiness = effectiveProvider
      ? this.buildLlmProviderReadiness(
          effectiveProvider,
          step.stepKey,
          outputMeta,
        )
      : null;
    const routeRiskState = effectiveProviderReadiness
      ? effectiveProviderReadiness.state
      : binding.routeBindingState === "missing" ||
          binding.routeBindingState === "invalid_provider"
        ? "risk"
        : "unknown";
    const routeRiskSummary =
      routeRiskState === "risk"
        ? "能力或输出 token 配置存在风险"
        : routeRiskState === "assumed"
          ? "可运行，但能力或 token 上限未显式声明"
          : routeRiskState === "ok"
            ? "Provider 已声明所需能力和 token 预算"
            : "暂无有效 Provider 就绪度数据";
    const runtimeProviderMap = new Map(
      runtimeProviders.map((provider) => [provider.id, provider]),
    );
    const fallbackProviderIds = this.normalizeLlmFallbackProviderIds(
      exactRoute?.fallbackProviderIds,
    );
    const fallbackProviders = fallbackProviderIds.map((providerId) => {
      const provider = runtimeProviderMap.get(providerId);
      return {
        id: providerId,
        name: provider?.name || providerId,
        region: provider?.region || null,
        model: provider?.model || null,
        fastModel: provider?.fastModel || null,
        enabled: Boolean(provider) && provider.enabled !== false,
      };
    });
    return {
      id: exactRoute?.id || null,
      stepKey: step.stepKey,
      stepOrder: step.stepOrder,
      stageLabel: meta.flowGroup,
      flowGroup: meta.flowGroup,
      flowOrder: meta.flowOrder,
      flowSummary: meta.flowSummary,
      triggerSummary: meta.triggerSummary,
      journeys: meta.journeys,
      journeySummary: meta.journeySummary,
      outputClass: outputMeta.outputClass,
      outputSummary: outputMeta.outputSummary,
      minOutputTokens: outputMeta.minOutputTokens,
      maxOutputTokens: outputMeta.maxOutputTokens,
      requiredCapabilities,
      exactProviderReadiness,
      effectiveProviderReadiness,
      routeRiskState,
      routeRiskSummary,
      optional: Boolean(meta.optional),
      bindingRequired,
      displayName: step.displayName || step.stepKey,
      description:
        step.description || `${meta.flowSummary}. ${meta.triggerSummary}`,
      executionRegion,
      enabled: exactRoute?.enabled ?? false,
      providerId: exactRoute?.providerId ?? null,
      providerKey: exactProvider?.name ?? null,
      providerDisplayName: exactProvider?.name ?? null,
      providerRegionTargetId: exactProvider?.regionTargetId ?? null,
      providerRegionDisplayName: exactProvider?.region ?? null,
      providerEnabled: exactProvider?.enabled ?? null,
      modelDefault: effectiveProvider?.model ?? exactProvider?.model ?? null,
      modelFast:
        effectiveProvider?.fastModel ?? exactProvider?.fastModel ?? null,
      fallbackProviderIds,
      fallbackProviders,
      fallbackProviderSummary: fallbackProviders.length
        ? fallbackProviders
            .map((provider) =>
              provider.enabled === false
                ? `${provider.name}（不可用）`
                : provider.name,
            )
            .join(" / ")
        : null,
      updatedAt: exactRoute?.updatedAt ?? null,
      routeBindingState: binding.routeBindingState,
      routeMatchStrategy: binding.routeMatchStrategy,
      matchedStepKey: binding.matchedStepKey,
      bindingNote: binding.bindingNote,
      exactRouteId: exactRoute?.id ?? null,
      exactRouteEnabled: exactRoute?.enabled ?? null,
      effectiveProviderId: effectiveProvider?.id ?? null,
      effectiveProviderDisplayName: effectiveProvider?.name ?? null,
      effectiveProviderRegion: effectiveProvider?.region ?? null,
      effectiveModelDefault: effectiveProvider?.model ?? null,
      effectiveModelFast: effectiveProvider?.fastModel ?? null,
    };
  }

  async upsertLlmProvider(id: string | undefined, body: any) {
    if (!body?.name) {
      throw new BadRequestException("Provider name is required");
    }
    if (!body?.providerType) {
      throw new BadRequestException("providerType is required");
    }
    if (!body?.baseUrl && body.providerType !== "anthropic") {
      throw new BadRequestException("baseUrl is required");
    }
    if (!body?.model) {
      throw new BadRequestException("model is required");
    }
    if (!body?.regionTargetId) {
      throw new BadRequestException("regionTargetId is required");
    }

    const providerId = id || randomUUID();
    const [existing, regionTarget] = await Promise.all([
      id
        ? this.prisma.llmGatewayProvider.findUnique({ where: { id } })
        : Promise.resolve(null),
      this.prisma.aiEngineRegionTarget.findUnique({
        where: { id: body.regionTargetId },
      }),
    ]);
    if (!regionTarget) {
      throw new BadRequestException("regionTargetId is invalid");
    }
    const resolvedAiEngineUrl =
      (regionTarget.aiEngineUrl || "").trim() ||
      this.getConfiguredAiEngineAdminBaseUrlForRegion(
        regionTarget.executionRegion,
      );
    if (
      !regionTarget.deployEnabled ||
      regionTarget.deployStatus !== "deployed" ||
      !resolvedAiEngineUrl
    ) {
      throw new BadRequestException(
        "Selected region target is not deployed and provider-selectable",
      );
    }
    const apiKey = body.apiKey || existing?.apiKey;
    if (!apiKey) {
      throw new BadRequestException("apiKey is required");
    }
    const extraConfig = this.buildLlmProviderExtraConfig(body, existing);

    const provider = await this.prisma.llmGatewayProvider.upsert({
      where: { id: providerId },
      create: {
        id: providerId,
        name: body.name,
        providerType: body.providerType,
        regionTargetId: regionTarget.id,
        cloudVendor: regionTarget.vendor,
        cloudRegionCode: regionTarget.cloudRegionCode,
        region: regionTarget.executionRegion,
        baseUrl: body.baseUrl || "",
        apiKey,
        model: body.model,
        fastModel: body.fastModel || null,
        requestTimeoutS: Number(body.requestTimeoutS || 600),
        connectTimeoutS: Number(body.connectTimeoutS || 15),
        enabled: body.enabled !== false,
        priority: Number(body.priority || 100),
        description: body.description || null,
        extraConfig,
      },
      update: {
        name: body.name,
        providerType: body.providerType,
        regionTargetId: regionTarget.id,
        cloudVendor: regionTarget.vendor,
        cloudRegionCode: regionTarget.cloudRegionCode,
        region: regionTarget.executionRegion,
        baseUrl: body.baseUrl || "",
        apiKey,
        model: body.model,
        fastModel: body.fastModel || null,
        requestTimeoutS: Number(body.requestTimeoutS || 600),
        connectTimeoutS: Number(body.connectTimeoutS || 15),
        enabled: body.enabled !== false,
        priority: Number(body.priority || 100),
        description: body.description || null,
        extraConfig,
      },
    });

    await this.refreshLlmGateway(regionTarget.id);
    return this.presentLlmProvider({
      ...provider,
      apiKey,
      regionTarget: regionTarget,
      testRecords: [],
    });
  }

  async deleteLlmProvider(id: string) {
    const existing = await this.prisma.llmGatewayProvider.findUnique({
      where: { id },
      select: {
        regionTargetId: true,
      },
    });
    await this.prisma.llmGatewayProvider.delete({ where: { id } });
    await this.refreshLlmGateway(existing?.regionTargetId || undefined);
    return { deleted: true };
  }

  async listLlmRoutes(executionRegion?: string) {
    const resolvedRegion = executionRegion || this.getDefaultExecutionRegion();
    const [steps, routes, runtimeProviders] = await Promise.all([
      this.prisma.llmStepCatalog.findMany({
        where: { enabled: true },
        orderBy: [{ stepOrder: "asc" }, { stepKey: "asc" }],
      }),
      this.prisma.llmStepRoute.findMany({
        where: {
          region: resolvedRegion,
        },
        include: {
          provider: {
            select: {
              id: true,
              name: true,
              region: true,
              regionTargetId: true,
              providerType: true,
              model: true,
              fastModel: true,
              extraConfig: true,
              enabled: true,
              priority: true,
              updatedAt: true,
            },
          },
        },
      }),
      this.prisma.llmGatewayProvider.findMany({
        where: { enabled: true },
        select: {
          id: true,
          name: true,
          region: true,
          regionTargetId: true,
          providerType: true,
          model: true,
          fastModel: true,
          extraConfig: true,
          enabled: true,
          priority: true,
          updatedAt: true,
        },
        orderBy: [{ priority: "asc" }, { updatedAt: "desc" }],
      }),
    ]);

    const routeMap = new Map(routes.map((route) => [route.stepKey, route]));
    return this.sortLlmFlowRows(
      steps
        .filter((step) => this.isVisibleGameGenerationLlmStep(step.stepKey))
        .map((step) =>
          this.presentLlmRouteRow(
            step,
            resolvedRegion,
            routeMap,
            runtimeProviders,
          ),
        ),
    );
  }

  async getLlmRoute(id: string) {
    const route = await this.prisma.llmStepRoute.findUnique({
      where: { id },
      include: {
        provider: {
          select: {
            id: true,
            name: true,
            region: true,
            regionTargetId: true,
            providerType: true,
            model: true,
            fastModel: true,
            extraConfig: true,
            enabled: true,
            priority: true,
            updatedAt: true,
          },
        },
      },
    });

    if (!route) {
      throw new NotFoundException("Route not found");
    }

    const [step, routes, runtimeProviders] = await Promise.all([
      this.prisma.llmStepCatalog.findUnique({
        where: { stepKey: route.stepKey },
      }),
      this.prisma.llmStepRoute.findMany({
        where: { region: route.region },
        include: {
          provider: {
            select: {
              id: true,
              name: true,
              region: true,
              regionTargetId: true,
              providerType: true,
              model: true,
              fastModel: true,
              extraConfig: true,
              enabled: true,
              priority: true,
              updatedAt: true,
            },
          },
        },
      }),
      this.prisma.llmGatewayProvider.findMany({
        where: { enabled: true },
        select: {
          id: true,
          name: true,
          region: true,
          regionTargetId: true,
          providerType: true,
          model: true,
          fastModel: true,
          extraConfig: true,
          enabled: true,
          priority: true,
          updatedAt: true,
        },
        orderBy: [{ priority: "asc" }, { updatedAt: "desc" }],
      }),
    ]);

    const routeMap = new Map(routes.map((item) => [item.stepKey, item]));
    return this.presentLlmRouteRow(
      step || {
        stepKey: route.stepKey,
        stepOrder: null,
        displayName: route.stepKey,
        description: null,
      },
      route.region,
      routeMap,
      runtimeProviders,
    );
  }

  async upsertLlmRoute(id: string | undefined, body: any) {
    if (!body?.stepKey) {
      throw new BadRequestException("stepKey is required");
    }
    if (!body?.providerId) {
      throw new BadRequestException("providerId is required");
    }

    const [step, provider] = await Promise.all([
      this.prisma.llmStepCatalog.findUnique({
        where: { stepKey: body.stepKey },
      }),
      this.prisma.llmGatewayProvider.findUnique({
        where: { id: body.providerId },
        select: {
          id: true,
          name: true,
          region: true,
          regionTargetId: true,
          providerType: true,
          model: true,
          fastModel: true,
          enabled: true,
        },
      }),
    ]);

    if (!step || step.enabled === false) {
      throw new BadRequestException("Unknown or disabled stepKey");
    }
    if (!provider) {
      throw new BadRequestException("Provider not found");
    }
    if (provider.enabled === false) {
      throw new BadRequestException(
        "Selected provider is disabled and cannot be bound to a live generation step",
      );
    }
    const requestedRegion =
      body.executionRegion || body.region || provider.region;
    const routeRegion = provider.region || this.getDefaultExecutionRegion();
    if (requestedRegion && requestedRegion !== routeRegion) {
      throw new BadRequestException(
        "executionRegion must match the selected provider region",
      );
    }
    const fallbackProviderIds = this.normalizeLlmFallbackProviderIds(
      body?.fallbackProviderIds,
    ).filter((providerId) => providerId !== body.providerId);
    const fallbackProviders = fallbackProviderIds.length
      ? await this.prisma.llmGatewayProvider.findMany({
          where: { id: { in: fallbackProviderIds } },
          select: {
            id: true,
            name: true,
            region: true,
            regionTargetId: true,
            enabled: true,
          },
        })
      : [];
    if (fallbackProviders.length !== fallbackProviderIds.length) {
      const existingIds = new Set(fallbackProviders.map((item) => item.id));
      const missingIds = fallbackProviderIds.filter(
        (providerId) => !existingIds.has(providerId),
      );
      throw new BadRequestException(
        `Fallback provider not found: ${missingIds.join(", ")}`,
      );
    }
    const disabledFallback = fallbackProviders.find(
      (item) => item.enabled === false,
    );
    if (disabledFallback) {
      throw new BadRequestException(
        `Fallback provider is disabled: ${disabledFallback.name}`,
      );
    }
    const crossRegionFallback = fallbackProviders.find(
      (item) => item.region !== routeRegion,
    );
    if (crossRegionFallback) {
      throw new BadRequestException(
        "Fallback providers must match the selected provider region",
      );
    }
    const routeId = id || randomUUID();

    const route = await this.prisma.llmStepRoute.upsert({
      where: id
        ? { id }
        : {
            llm_step_routes_step_key_region_key: {
              stepKey: body.stepKey,
              region: routeRegion,
            },
          },
      create: {
        id: routeId,
        stepKey: body.stepKey,
        region: routeRegion,
        providerId: body.providerId,
        fallbackProviderIds,
        modelOverride: null,
        fastModelOverride: null,
        requestTimeoutS: null,
        connectTimeoutS: null,
        enabled: body.enabled !== false,
      },
      update: {
        stepKey: body.stepKey,
        region: routeRegion,
        providerId: body.providerId,
        fallbackProviderIds,
        modelOverride: null,
        fastModelOverride: null,
        requestTimeoutS: null,
        connectTimeoutS: null,
        enabled: body.enabled !== false,
      },
      include: {
        provider: {
          select: {
            id: true,
            name: true,
            region: true,
            regionTargetId: true,
            providerType: true,
            model: true,
            fastModel: true,
          },
        },
      },
    });

    await this.refreshLlmGateway(route.provider?.regionTargetId || undefined);
    return {
      ...route,
      stepMeta: step,
    };
  }

  async deleteLlmRoute(id: string) {
    const existing = await this.prisma.llmStepRoute.findUnique({
      where: { id },
      select: {
        provider: {
          select: {
            regionTargetId: true,
          },
        },
      },
    });
    await this.prisma.llmStepRoute.delete({ where: { id } });
    await this.refreshLlmGateway(
      existing?.provider?.regionTargetId || undefined,
    );
    return { deleted: true };
  }

  async refreshLlmGateway(regionTargetId?: string) {
    const urls = await this.getAiEngineAdminBaseUrls(regionTargetId);
    const responses: Array<{ baseUrl: string; data: any }> = [];
    const failures: Array<{ baseUrl: string; message: string }> = [];

    for (const baseUrl of urls) {
      try {
        const response = await axios.post(
          `${baseUrl}/api/v1/ai/llm-gateway/refresh`,
          {},
          {
            headers: {
              "x-admin-token": this.getAdminToken(),
            },
            timeout: 10000,
          },
        );
        responses.push({
          baseUrl,
          data: response.data,
        });
      } catch (error) {
        failures.push({
          baseUrl,
          message: this.extractAiEngineAdminErrorMessage(error),
        });
      }
    }

    if (!responses.length) {
      const summary = failures
        .map((entry) => `${entry.baseUrl}: ${entry.message}`)
        .join(" | ");
      throw new BadGatewayException(
        `All ai-engine admin endpoints failed during llm gateway refresh. ${summary || "No upstream error details available."}`,
      );
    }

    return {
      refreshed: responses.length,
      failed: failures.length,
      partialFailure: failures.length > 0,
      results: responses,
      failures,
    };
  }

  async testLlmProvider(providerId: string) {
    const provider = await this.prisma.llmGatewayProvider.findUnique({
      where: { id: providerId },
      select: {
        regionTargetId: true,
      },
    });
    if (!provider) {
      throw new NotFoundException("Provider not found");
    }
    const response = await this.postAiEngineAdminWithFailover<any>(
      provider.regionTargetId || undefined,
      `/api/v1/ai/llm-gateway/providers/${providerId}/test`,
      {},
      30000,
      "No reachable ai-engine endpoint found for the selected provider",
    );
    return response.data;
  }

  async previewLlmProviderCatalog(body: any) {
    let payload = { ...(body || {}) };
    if (payload?.providerId) {
      const existing = await this.prisma.llmGatewayProvider.findUnique({
        where: { id: payload.providerId },
        select: {
          providerType: true,
          regionTargetId: true,
          baseUrl: true,
          apiKey: true,
          extraConfig: true,
        },
      });
      if (!existing) {
        throw new NotFoundException("Provider not found");
      }
      const normalizedExtra = this.normalizeLlmProviderExtraConfig(
        existing.extraConfig,
      );
      payload = {
        ...payload,
        providerType: payload.providerType || existing.providerType,
        regionTargetId: payload.regionTargetId || existing.regionTargetId,
        baseUrl: payload.baseUrl || existing.baseUrl,
        apiKey: payload.apiKey || existing.apiKey,
        vendorPreset: payload.vendorPreset || normalizedExtra.vendorPreset,
        catalogApiUrl:
          payload.catalogApiUrl || normalizedExtra.modelCatalog.apiUrl,
        catalogAuthMode:
          payload.catalogAuthMode || normalizedExtra.modelCatalog.authMode,
        catalogApiKey:
          payload.catalogApiKey || normalizedExtra.modelCatalog.apiKey,
      };
    }
    const requestBody = {
      provider_type: payload.providerType || "openai_compatible",
      vendor_preset: payload.vendorPreset || "generic",
      base_url: payload.baseUrl || "",
      api_key: payload.apiKey || "",
      catalog_api_url: payload.catalogApiUrl || "",
      catalog_auth_mode: payload.catalogAuthMode || "inherit_provider",
      catalog_api_key: payload.catalogApiKey || "",
    };
    const response = await this.postAiEngineAdminWithFailover<any>(
      payload?.regionTargetId || undefined,
      "/api/v1/ai/llm-gateway/providers/catalog/preview",
      requestBody,
      30000,
      "No reachable ai-engine endpoint found for the selected region target",
    );
    return {
      models: response.data?.models || [],
      fetchedAt: response.data?.fetchedAt || response.data?.fetched_at || null,
      resolvedCatalogApiUrl:
        response.data?.resolvedCatalogApiUrl ||
        response.data?.resolved_catalog_api_url ||
        "",
      vendorPreset:
        response.data?.vendorPreset ||
        response.data?.vendor_preset ||
        requestBody.vendor_preset ||
        "generic",
    };
  }

  async testLlmProviderChat(providerId: string, body: any) {
    const provider = await this.prisma.llmGatewayProvider.findUnique({
      where: { id: providerId },
      select: {
        regionTargetId: true,
      },
    });
    if (!provider) {
      throw new NotFoundException("Provider not found");
    }
    const response = await this.postAiEngineAdminWithFailover<any>(
      provider.regionTargetId || undefined,
      `/api/v1/ai/llm-gateway/providers/${providerId}/test-chat`,
      body || {},
      60000,
      "No reachable ai-engine endpoint found for the selected provider",
    );
    return {
      providerId:
        response.data?.providerId || response.data?.provider_id || providerId,
      providerName:
        response.data?.providerName || response.data?.provider_name || null,
      providerType:
        response.data?.providerType || response.data?.provider_type || null,
      region: response.data?.region || null,
      resolvedEndpoint:
        response.data?.resolvedEndpoint ||
        response.data?.resolved_endpoint ||
        null,
      model: response.data?.model || null,
      latencyMs: response.data?.latencyMs ?? response.data?.latency_ms ?? null,
      httpStatus:
        response.data?.httpStatus ?? response.data?.http_status ?? null,
      success: response.data?.success !== false,
      errorMessage:
        response.data?.errorMessage || response.data?.error_message || null,
      reply: response.data?.reply || "",
      testedAt: response.data?.testedAt || response.data?.tested_at || null,
    };
  }

  async listLlmProviderTestRecords(providerId: string, limit = 20) {
    const provider = await this.prisma.llmGatewayProvider.findUnique({
      where: { id: providerId },
      select: {
        id: true,
      },
    });
    if (!provider) {
      throw new NotFoundException("Provider not found");
    }
    const take = Math.min(Math.max(Number(limit) || 20, 1), 50);
    return this.prisma.llmGatewayTestRecord.findMany({
      where: { providerId },
      orderBy: { testedAt: "desc" },
      take,
      select: {
        id: true,
        success: true,
        region: true,
        resolvedEndpoint: true,
        model: true,
        latencyMs: true,
        httpStatus: true,
        errorMessage: true,
        testedAt: true,
      },
    });
  }

  // ===================== Stats =====================

  async getStats(from?: string, to?: string) {
    const range = this.parseDashboardDateRange(from, to);
    const paidOrderWhere = this.buildPaidSubscriptionOrderWhere(range);
    const now = new Date();

    const [
      totalGames,
      totalUsers,
      gamesAgg,
      averages,
      gameMetrics,
      planCount,
      activePlanCount,
      activeSubscriberCount,
      paidOrderAggregate,
    ] = await Promise.all([
      this.prisma.game.count(),
      this.prisma.user.count(),
      this.prisma.game.aggregate({
        _sum: {
          playCount: true,
          likeCount: true,
          forkCount: true,
        },
      }),
      this.prisma.game.aggregate({
        _avg: {
          qualityScore: true,
          retryCount: true,
        },
      }),
      this.prisma.game.findMany({
        select: {
          status: true,
          qualityScore: true,
          failedStage: true,
          failedReason: true,
          retryCount: true,
        },
      }),
      this.prisma.subscriptionPlan.count(),
      this.prisma.subscriptionPlan.count({ where: { active: true } }),
      this.prisma.userSubscription.count({
        where: {
          status: UserSubscriptionStatus.active,
          expiresAt: { gt: now },
        },
      }),
      this.prisma.subscriptionOrder.aggregate({
        where: paidOrderWhere,
        _sum: { amount: true },
        _count: { id: true },
      }),
    ]);

    const statusCounts = await this.prisma.game.groupBy({
      by: ["status"],
      _count: { id: true },
    });

    const statusMap: Record<string, number> = {};
    for (const s of statusCounts) {
      statusMap[s.status] = s._count.id;
    }

    const failedStageMap: Record<string, number> = {};
    const retryBuckets: Record<string, number> = {
      "0 retries": 0,
      "1 retry": 0,
      "2 retries": 0,
      "3+ retries": 0,
    };
    const qualityBuckets: Record<string, number> = {
      "90+": 0,
      "80-89": 0,
      "70-79": 0,
      "<70": 0,
    };
    const failureReasonMap = new Map<
      string,
      { stage: string; reason: string; count: number }
    >();

    for (const game of gameMetrics) {
      const retries = Number(game.retryCount || 0);
      if (retries <= 0) retryBuckets["0 retries"] += 1;
      else if (retries === 1) retryBuckets["1 retry"] += 1;
      else if (retries === 2) retryBuckets["2 retries"] += 1;
      else retryBuckets["3+ retries"] += 1;

      if (game.qualityScore !== null && game.qualityScore !== undefined) {
        const score = Number(game.qualityScore);
        if (score >= 90) qualityBuckets["90+"] += 1;
        else if (score >= 80) qualityBuckets["80-89"] += 1;
        else if (score >= 70) qualityBuckets["70-79"] += 1;
        else qualityBuckets["<70"] += 1;
      }

      if (!game.failedStage && !game.failedReason) {
        continue;
      }

      const stage = game.failedStage || "unknown";
      failedStageMap[stage] = (failedStageMap[stage] || 0) + 1;

      const normalizedReason = (game.failedReason || "unknown error")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 80);
      const key = `${stage}::${normalizedReason}`;
      const current = failureReasonMap.get(key);
      if (current) current.count += 1;
      else {
        failureReasonMap.set(key, {
          stage,
          reason: normalizedReason,
          count: 1,
        });
      }
    }

    const topFailureReasons = Array.from(failureReasonMap.values())
      .sort((a, b) => b.count - a.count)
      .slice(0, 5);

    return {
      totalGames,
      totalUsers,
      totalPlays: gamesAgg._sum.playCount || 0,
      totalLikes: gamesAgg._sum.likeCount || 0,
      totalForks: gamesAgg._sum.forkCount || 0,
      avgQualityScore: Number(averages._avg.qualityScore || 0),
      avgRetryCount: Number(averages._avg.retryCount || 0),
      byStatus: statusMap,
      failedByStage: failedStageMap,
      retryBuckets,
      qualityBuckets,
      topFailureReasons,
      subscriptionOverview: {
        totalPlans: planCount,
        activePlans: activePlanCount,
        activeSubscribers: activeSubscriberCount,
        paidOrderCount: paidOrderAggregate._count.id || 0,
        totalRevenueCents: Number(paidOrderAggregate._sum.amount || 0),
        totalRevenueYuan: Number(
          (Number(paidOrderAggregate._sum.amount || 0) / 100).toFixed(2),
        ),
        range: {
          from: range.from,
          to: range.to,
        },
      },
    };
  }

  // ===================== System Config =====================

  private promptItemMeta(key: string) {
    return PROMPT_PIPELINE_ITEM_META[key] || {};
  }

  private promptDisplayName(key: string) {
    const meta = this.promptItemMeta(key);
    if (typeof meta.displayName === "string" && meta.displayName.trim()) {
      return meta.displayName.trim();
    }
    return key.replace(/^(prompt|bundle)\./, "");
  }

  /**
   * R-PREVIEW-1: Replace `{var}` placeholders in template with values, returning
   * (rendered, missing, unused). Non-raising: missing variables stay as literal
   * `{var}` in the output so the caller can still see the un-bound slot.
   */
  private renderTemplate(
    template: string,
    variables: Record<string, any> | null | undefined,
  ): { rendered: string; missing: string[]; unused: string[] } {
    const src = typeof template === "string" ? template : "";
    const vars =
      variables && typeof variables === "object"
        ? variables
        : ({} as Record<string, any>);

    const declared = new Set<string>();
    const placeholderRe = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
    let match: RegExpExecArray | null;
    while ((match = placeholderRe.exec(src)) !== null) {
      declared.add(match[1]);
    }

    const missing: string[] = [];
    const rendered = src.replace(placeholderRe, (full, name) => {
      if (Object.prototype.hasOwnProperty.call(vars, name)) {
        const raw = vars[name];
        return raw === null || raw === undefined ? "" : String(raw);
      }
      if (!missing.includes(name)) missing.push(name);
      return full;
    });

    const unused = Object.keys(vars).filter((k) => !declared.has(k));
    return { rendered, missing, unused };
  }

  /** Non-throwing variant of getConfig — returns null if missing. */
  private async safeGetConfig(key: string): Promise<any> {
    try {
      return await this.getConfig(key);
    } catch {
      return null;
    }
  }

  private mergePromptConfigs(configs: any[]) {
    const existingMap = new Map(
      configs.map((config) => [config.configKey, config]),
    );
    const merged = DEFAULT_PROMPT_CATALOG.map((entry) => {
      const existing = existingMap.get(entry.key);
      const meta = this.promptItemMeta(entry.key);
      const variables = Array.isArray(meta.variables)
        ? meta.variables.filter(
            (value) => typeof value === "string" && value.trim(),
          )
        : [];
      return {
        id: existing?.id || `catalog:${entry.key}`,
        configKey: entry.key,
        configValue: existing?.configValue ?? entry.value ?? "",
        description: existing?.description ?? entry.description ?? null,
        category: "prompt",
        createdAt: existing?.createdAt ?? null,
        updatedAt: existing?.updatedAt ?? null,
        defaultValue: entry.value ?? "",
        source: existing ? "db" : "catalog",
        isDefault: !existing,
        displayName: this.promptDisplayName(entry.key),
        variables,
      };
    });

    const extras = configs
      .filter((config) => !PROMPT_CATALOG_BY_KEY.has(config.configKey))
      .map((config) => ({
        ...config,
        defaultValue: null,
        source: "db",
        isDefault: false,
        displayName: this.promptDisplayName(config.configKey),
        variables: [],
      }));

    return [...merged, ...extras];
  }

  private mergeCatalogConfigs(category: string | undefined, configs: any[]) {
    if (category === "prompt") {
      return this.mergePromptConfigs(configs);
    }
    if (category !== "timeout") {
      return configs;
    }
    return listBusinessTimeoutConfigs(configs);
  }

  private async resolveTimeoutConfigValue(
    key: string,
    options?: {
      min?: number;
      max?: number;
    },
  ): Promise<number> {
    const catalogEntry = TIMEOUT_CONFIG_CATALOG_BY_KEY.get(key);
    if (!catalogEntry) {
      throw new Error(`Unknown timeout config key: ${key}`);
    }

    const row = await this.prisma.systemConfig.findUnique({
      where: { configKey: key },
      select: { configValue: true },
    });
    const rawValue = row?.configValue ?? catalogEntry.defaultValue;
    const parsed =
      catalogEntry.valueType === "float"
        ? Number.parseFloat(String(rawValue))
        : Number.parseInt(String(rawValue), 10);
    const fallback =
      catalogEntry.valueType === "float"
        ? Number.parseFloat(catalogEntry.defaultValue)
        : Number.parseInt(catalogEntry.defaultValue, 10);
    const value = Number.isFinite(parsed) ? parsed : fallback;
    const min = options?.min ?? Number.NEGATIVE_INFINITY;
    const max = options?.max ?? Number.POSITIVE_INFINITY;
    return Math.min(max, Math.max(min, value));
  }

  async listConfigs(category?: string) {
    const where: any = {};
    if (category) where.category = category;
    const configs = await this.prisma.systemConfig.findMany({
      where,
      orderBy: [{ category: "asc" }, { configKey: "asc" }],
    });
    return this.mergeCatalogConfigs(category, configs);
  }

  async getConfig(key: string) {
    if (isBusinessTimeoutConfigKey(key)) {
      const configs = await this.prisma.systemConfig.findMany({
        where: { category: "timeout" },
        orderBy: [{ category: "asc" }, { configKey: "asc" }],
      });
      const businessConfig = getBusinessTimeoutConfig(key, configs);
      if (businessConfig) {
        return businessConfig;
      }
    }

    const config = await this.prisma.systemConfig.findUnique({
      where: { configKey: key },
    });
    if (!config) {
      const promptEntry = PROMPT_CATALOG_BY_KEY.get(key);
      if (promptEntry) {
        const meta = this.promptItemMeta(key);
        return {
          id: `catalog:${key}`,
          configKey: key,
          configValue: promptEntry.value ?? "",
          description: promptEntry.description ?? null,
          category: "prompt",
          createdAt: null,
          updatedAt: null,
          defaultValue: promptEntry.value ?? "",
          source: "catalog",
          isDefault: true,
          displayName: this.promptDisplayName(key),
          variables: Array.isArray(meta.variables) ? meta.variables : [],
        };
      }
      const timeoutCatalog = TIMEOUT_CONFIG_CATALOG_BY_KEY.get(key);
      if (timeoutCatalog) {
        return {
          id: `catalog:${timeoutCatalog.key}`,
          configKey: timeoutCatalog.key,
          configValue: timeoutCatalog.defaultValue,
          description: timeoutCatalog.description,
          category: "timeout",
          createdAt: null,
          updatedAt: null,
          defaultValue: timeoutCatalog.defaultValue,
          unit: timeoutCatalog.unit,
          valueType: timeoutCatalog.valueType,
          service: timeoutCatalog.service,
          group: timeoutCatalog.group,
          sectionId: timeoutCatalog.sectionId,
          sectionKind: timeoutCatalog.sectionKind,
          sectionOrder: timeoutCatalog.sectionOrder,
          sectionTag: timeoutCatalog.sectionTag,
          sectionTitle: timeoutCatalog.sectionTitle,
          sectionDescription: timeoutCatalog.sectionDescription,
          itemOrder: timeoutCatalog.itemOrder,
          source: "catalog",
          isDefault: true,
        };
      }
      throw new NotFoundException(`Config '${key}' not found`);
    }
    return config;
  }

  /**
   * P0-1: Render a prompt template with the supplied variable bindings. Returns
   * the rendered text plus bookkeeping that the admin UI can surface (missing
   * placeholders, unused vars, declared-but-not-in-template vars).
   */
  async renderPromptPreview(
    key: string,
    variables: Record<string, any> | null | undefined,
  ) {
    const config = await this.getConfig(key);
    const template = String((config as any).configValue ?? "");
    const metaVars = Array.isArray((config as any).variables)
      ? ((config as any).variables as string[])
      : [];
    const declaredByMeta = metaVars
      .map((v) => v.replace(/^\{|\}$/g, ""))
      .filter((v) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(v));

    const { rendered, missing, unused } = this.renderTemplate(
      template,
      variables,
    );

    const declaredNotInTemplate = declaredByMeta.filter(
      (v) => !new RegExp(`\\{${v}\\}`).test(template),
    );

    return {
      configKey: key,
      category: (config as any).category || "prompt",
      templateLength: template.length,
      declaredVariables: declaredByMeta,
      missingVariables: missing,
      unusedVariables: unused,
      declaredButNotInTemplate: declaredNotInTemplate,
      rendered,
      source: (config as any).source || "db",
      isDefault: Boolean((config as any).isDefault),
    };
  }

  /**
   * P0-2: Compose the Step-3 (logic_generate) system prompt by concatenating
   * code_gen_system + tier override + alignment reminder + request-context
   * template + optional bundle policy/locked-contract, each rendered with the
   * supplied variables. Returns both the assembled text and a per-piece
   * breakdown so admins can see provenance.
   */
  async assembleStage3Prompt(input: {
    tier?: string;
    gameType?: string;
    bundleId?: string;
    bundleVersion?: number;
    variables?: Record<string, any>;
  }) {
    const tier = (input.tier || "").toUpperCase().trim();
    const variables = input.variables || {};

    const codeGenSystem = await this.getConfig("prompt.code_gen_system");
    const alignmentReminder = await this.safeGetConfig(
      "prompt.generate_alignment_reminder",
    );
    const tierOverride = tier
      ? await this.safeGetConfig(
          `prompt.code_gen_system_${tier.toLowerCase()}`,
        )
      : null;
    const requestContext = await this.safeGetConfig(
      "prompt.generate_request_context_template",
    );

    let activeBundle: any = null;
    if (input.bundleId) {
      const bundles = await this.listPromptBundles();
      activeBundle =
        bundles.find(
          (b: any) =>
            b.id === input.bundleId &&
            (input.bundleVersion === undefined ||
              Number(b.version) === Number(input.bundleVersion)),
        ) || null;
    }

    const pieces: Array<{ label: string; source: string; text: string }> = [];
    const allMissing: string[] = [];

    const pushPiece = (label: string, source: string, text: string) => {
      if (!text.trim()) return;
      const r = this.renderTemplate(text, variables);
      pieces.push({ label, source, text: r.rendered });
      for (const m of r.missing) {
        if (!allMissing.includes(m)) allMissing.push(m);
      }
    };

    pushPiece(
      "prompt.code_gen_system",
      (codeGenSystem as any).source || "db",
      String((codeGenSystem as any).configValue ?? ""),
    );

    if (tierOverride) {
      pushPiece(
        `prompt.code_gen_system_${tier.toLowerCase()}`,
        (tierOverride as any).source || "db",
        String((tierOverride as any).configValue ?? ""),
      );
    }

    if (alignmentReminder) {
      pushPiece(
        "prompt.generate_alignment_reminder",
        (alignmentReminder as any).source || "db",
        String((alignmentReminder as any).configValue ?? ""),
      );
    }

    if (requestContext) {
      pushPiece(
        "prompt.generate_request_context_template",
        (requestContext as any).source || "db",
        String((requestContext as any).configValue ?? ""),
      );
    }

    if (activeBundle) {
      if (
        activeBundle.productPolicy &&
        String(activeBundle.productPolicy).trim()
      ) {
        pushPiece(
          `bundle.product.policy@${activeBundle.id}:v${activeBundle.version}`,
          activeBundle.source || "db",
          String(activeBundle.productPolicy),
        );
      }
      if (
        activeBundle.lockedContractOverride &&
        String(activeBundle.lockedContractOverride).trim()
      ) {
        pushPiece(
          `bundle.runtime.locked_contract@${activeBundle.id}:v${activeBundle.version}`,
          activeBundle.source || "db",
          String(activeBundle.lockedContractOverride),
        );
      }
    }

    const assembled = pieces
      .map((p) => `--- ${p.label} ---\n${p.text}`)
      .join("\n\n");

    return {
      tier: tier || null,
      gameType: input.gameType || null,
      bundleId: input.bundleId || null,
      bundleVersion:
        input.bundleVersion !== undefined ? Number(input.bundleVersion) : null,
      pieces,
      assembled,
      assembledLength: assembled.length,
      pieceCount: pieces.length,
      warnings: {
        missingVariables: allMissing,
      },
    };
  }

  async upsertConfig(
    key: string,
    data: { value: string; description?: string; category?: string },
  ) {
    if (isBusinessTimeoutConfigKey(key)) {
      let updates;
      try {
        updates = expandBusinessTimeoutConfigUpdates(key, data.value);
      } catch (error: any) {
        throw new BadRequestException(
          error?.message || "Invalid timeout value",
        );
      }

      for (const update of updates) {
        const timeoutCatalog = TIMEOUT_CONFIG_CATALOG_BY_KEY.get(update.key);
        await this.prisma.systemConfig.upsert({
          where: { configKey: update.key },
          update: {
            configValue: update.value,
            description: timeoutCatalog?.description ?? null,
            category: "timeout",
          },
          create: {
            id: randomUUID(),
            configKey: update.key,
            configValue: update.value,
            description: timeoutCatalog?.description ?? null,
            category: "timeout",
          },
        });
      }

      const refreshResult = await this.refreshTimeoutConfigs();
      const configs = await this.prisma.systemConfig.findMany({
        where: { category: "timeout" },
        orderBy: [{ category: "asc" }, { configKey: "asc" }],
      });
      const businessConfig = getBusinessTimeoutConfig(key, configs);
      if (!businessConfig) {
        return {
          id: `business:${key}`,
          configKey: key,
          configValue: String(data.value),
          category: "timeout",
          refreshResult,
        };
      }
      return {
        ...businessConfig,
        refreshResult,
      };
    }

    const timeoutCatalog = TIMEOUT_CONFIG_CATALOG_BY_KEY.get(key);
    const promptCatalogEntry = PROMPT_CATALOG_BY_KEY.get(key);
    const category = data.category || (timeoutCatalog ? "timeout" : "prompt");
    const description =
      data.description ??
      timeoutCatalog?.description ??
      promptCatalogEntry?.description ??
      null;
    const config = await this.prisma.systemConfig.upsert({
      where: { configKey: key },
      update: {
        configValue: data.value,
        description,
        category,
      },
      create: {
        id: randomUUID(),
        configKey: key,
        configValue: data.value,
        description,
        category,
      },
    });
    if (category === "timeout") {
      const refreshResult = await this.refreshTimeoutConfigs();
      return {
        ...config,
        refreshResult,
      };
    }
    const refreshResult = await this.refreshPromptConfigs();
    return {
      ...config,
      refreshResult,
    };
  }

  async initDefaultPrompts() {
    const defaults = Array.isArray(promptCatalog) ? promptCatalog : [];

    let created = 0;
    let updated = 0;
    let skipped = 0;
    for (const d of defaults) {
      const nextValue = typeof d.value === "string" ? d.value : "";
      const nextDescription = d.description ?? null;
      const existing = await this.prisma.systemConfig.findUnique({
        where: { configKey: d.key },
      });
      if (!existing) {
        await this.prisma.systemConfig.create({
          data: {
            id: randomUUID(),
            configKey: d.key,
            configValue: nextValue,
            description: nextDescription,
            category: "prompt",
          },
        });
        created++;
        continue;
      }

      const existingDescription = existing.description ?? null;
      const existingCategory =
        typeof existing.category === "string" ? existing.category : "prompt";
      const needsUpdate =
        existing.configValue !== nextValue ||
        existingDescription !== nextDescription ||
        existingCategory !== "prompt";

      if (!needsUpdate) {
        skipped++;
        continue;
      }

      await this.prisma.systemConfig.update({
        where: { configKey: d.key },
        data: {
          configValue: nextValue,
          description: nextDescription,
          category: "prompt",
        },
      });
      updated++;
    }
    const refreshResult = await this.refreshPromptConfigs();
    return {
      created,
      updated,
      skipped,
      total: defaults.length,
      refreshResult,
    };
  }

  async refreshPromptConfigs() {
    const urls = await this.getAiEngineAdminBaseUrls();
    const adminToken = this.getAdminToken();
    const requestTimeoutMs = await this.resolveTimeoutConfigValue(
      "timeout.game_service.admin_refresh_timeout_ms",
      { min: 1000 },
    );
    const settledResults = await Promise.allSettled(
      urls.map(async (baseUrl) => {
        const response = await axios.post(
          `${baseUrl}/api/v1/ai/prompts/refresh`,
          {},
          {
            headers: {
              "x-admin-token": adminToken,
            },
            timeout: requestTimeoutMs,
          },
        );
        return {
          baseUrl,
          data: response.data,
        };
      }),
    );
    const aiEngine = settledResults.map((result, index) => {
      const baseUrl = urls[index];
      if (result.status === "fulfilled") {
        return {
          baseUrl,
          status: "ok",
          data: result.value.data,
        };
      }
      return {
        baseUrl,
        status: "error",
        errorMessage:
          result.reason?.message || String(result.reason || "unknown error"),
      };
    });
    const successCount = aiEngine.filter((item) => item.status === "ok").length;
    const failureCount = aiEngine.length - successCount;
    return {
      refreshed: successCount,
      failed: failureCount,
      partialFailure: failureCount > 0,
      aiEngine,
    };
  }

  async initDefaultTimeouts() {
    let created = 0;
    let skipped = 0;
    for (const entry of TIMEOUT_CONFIG_CATALOG) {
      const existing = await this.prisma.systemConfig.findUnique({
        where: { configKey: entry.key },
      });
      if (existing) {
        skipped++;
        continue;
      }
      await this.prisma.systemConfig.create({
        data: {
          id: randomUUID(),
          configKey: entry.key,
          configValue: entry.defaultValue,
          description: entry.description,
          category: "timeout",
        },
      });
      created++;
    }
    await this.refreshTimeoutConfigs();
    return { created, skipped, total: TIMEOUT_CONFIG_CATALOG.length };
  }

  async refreshTimeoutConfigs() {
    await this.gameService.refreshTimeoutConfigCache();
    const urls = await this.getAiEngineAdminBaseUrls();
    const adminToken = this.getAdminToken();
    const requestTimeoutMs = await this.resolveTimeoutConfigValue(
      "timeout.game_service.admin_refresh_timeout_ms",
      { min: 1000 },
    );
    const settledResults = await Promise.allSettled(
      urls.map(async (baseUrl) => {
        const response = await axios.post(
          `${baseUrl}/api/v1/ai/config/timeouts/refresh`,
          {},
          {
            headers: {
              "x-admin-token": adminToken,
            },
            timeout: requestTimeoutMs,
          },
        );
        return {
          baseUrl,
          data: response.data,
        };
      }),
    );
    const aiEngine = settledResults.map((result, index) => {
      const baseUrl = urls[index];
      if (result.status === "fulfilled") {
        return {
          baseUrl,
          status: "ok",
          data: result.value.data,
        };
      }
      return {
        baseUrl,
        status: "error",
        errorMessage:
          result.reason?.message || String(result.reason || "unknown error"),
      };
    });
    const successCount = aiEngine.filter((item) => item.status === "ok").length;
    const failureCount = aiEngine.length - successCount;

    return {
      refreshed: successCount + 1,
      failed: failureCount,
      partialFailure: failureCount > 0,
      gameService: { status: "ok" },
      aiEngine,
    };
  }

  async listPromptBundles(status?: string) {
    const where: Prisma.PromptBundleWhereInput = {};
    if (status) {
      where.status = status;
    }
    const bundles = await this.prisma.promptBundle.findMany({
      where,
      orderBy: [{ id: "asc" }, { version: "desc" }],
    });
    const existingMap = new Map(
      bundles.map((bundle) => [`${bundle.id}::${bundle.version}`, bundle]),
    );
    const merged = DEFAULT_PROMPT_BUNDLE_CATALOG.map((entry) => {
      const existing = existingMap.get(`${entry.id}::${entry.version}`);
      return {
        id: entry.id,
        version: entry.version,
        status: existing?.status ?? entry.status ?? "draft",
        productPolicy: existing?.productPolicy ?? entry.productPolicy ?? "",
        lockedContractOverride:
          existing?.lockedContractOverride ??
          entry.lockedContractOverride ??
          null,
        repairPlaybook: existing?.repairPlaybook ?? entry.repairPlaybook ?? "",
        profileOverrides:
          existing?.profileOverrides ?? entry.profileOverrides ?? null,
        metadata: existing?.metadata ?? entry.metadata ?? null,
        createdAt: existing?.createdAt ?? null,
        updatedAt: existing?.updatedAt ?? null,
        source: existing ? "db" : "catalog",
      };
    });
    const extras = bundles
      .filter(
        (bundle) =>
          !DEFAULT_PROMPT_BUNDLE_CATALOG.some(
            (entry) =>
              entry.id === bundle.id && entry.version === bundle.version,
          ),
      )
      .map((bundle) => ({
        ...bundle,
        source: "db",
      }));
    const result = [...merged, ...extras]
      .filter(
        (bundle) =>
          !status ||
          String(bundle.status || "").toLowerCase() === status.toLowerCase(),
      )
      .sort((a, b) => {
        if (a.id === b.id) {
          return Number(b.version || 0) - Number(a.version || 0);
        }
        return String(a.id).localeCompare(String(b.id));
      });
    return result;
  }

  async listRuntimeProfiles(enabledOnly = false) {
    const where: Prisma.RuntimeProfileCatalogWhereInput = {};
    if (enabledOnly) {
      where.enabled = true;
    }
    const profiles = await this.prisma.runtimeProfileCatalog.findMany({
      where,
      orderBy: [{ enabled: "desc" }, { id: "asc" }],
    });
    const existingMap = new Map(
      profiles.map((profile) => [profile.id, profile]),
    );
    const merged = DEFAULT_RUNTIME_PROFILE_CATALOG.map((entry) => {
      const existing = existingMap.get(entry.id);
      return {
        id: entry.id,
        displayName: existing?.displayName ?? entry.displayName ?? entry.id,
        enabled: existing?.enabled ?? entry.enabled ?? true,
        skeletonVersion:
          existing?.skeletonVersion ?? entry.skeletonVersion ?? "v1",
        contractSchema: existing?.contractSchema ?? entry.contractSchema ?? {},
        fewShotPrompt: existing?.fewShotPrompt ?? entry.fewShotPrompt ?? "",
        metadata: existing?.metadata ?? entry.metadata ?? null,
        createdAt: existing?.createdAt ?? null,
        updatedAt: existing?.updatedAt ?? null,
        source: existing ? "db" : "catalog",
      };
    });
    const extras = profiles
      .filter(
        (profile) =>
          !DEFAULT_RUNTIME_PROFILE_CATALOG.some(
            (entry) => entry.id === profile.id,
          ),
      )
      .map((profile) => ({
        ...profile,
        source: "db",
      }));
    return [...merged, ...extras]
      .filter((profile) => !enabledOnly || Boolean(profile.enabled))
      .sort((a, b) => {
        if (Boolean(a.enabled) !== Boolean(b.enabled)) {
          return Boolean(a.enabled) ? -1 : 1;
        }
        return String(a.id).localeCompare(String(b.id));
      });
  }

  private summarizeRuntimeProfileContract(contractSchema: any) {
    const contract = this.asPlainObject(contractSchema);
    const inputContract = this.asPlainObject(contract.inputContract);
    const stateContract = this.asPlainObject(contract.stateContract);
    const safetyContract = this.asPlainObject(contract.safetyContract);
    const mobileLayoutContract = this.asPlainObject(
      contract.mobileLayoutContract,
    );
    return {
      runtimeProfile:
        typeof contract.runtimeProfile === "string"
          ? contract.runtimeProfile
          : null,
      requiredStates: Array.isArray(stateContract.requiredStates)
        ? stateContract.requiredStates
        : [],
      inputModes: Array.isArray(inputContract.requiredModes)
        ? inputContract.requiredModes
        : [],
      gestures: Array.isArray(inputContract.gestures)
        ? inputContract.gestures
        : [],
      forbiddenApis: Array.isArray(safetyContract.forbiddenApis)
        ? safetyContract.forbiddenApis
        : [],
      orientation:
        typeof mobileLayoutContract.orientation === "string"
          ? mobileLayoutContract.orientation
          : null,
      uiScaleMode:
        typeof mobileLayoutContract.uiScaleMode === "string"
          ? mobileLayoutContract.uiScaleMode
          : null,
    };
  }

  async getPromptPipeline() {
    const [promptConfigs, promptBundles, runtimeProfiles] = await Promise.all([
      this.listConfigs("prompt"),
      this.listPromptBundles(),
      this.listRuntimeProfiles(false),
    ]);

    const promptMap = new Map(
      promptConfigs.map((item: any) => [item.configKey, item]),
    );
    const assignedKeys = new Set<string>();

    const buildPromptItems = (keys: string[]) =>
      keys
        .map((key) => {
          assignedKeys.add(key);
          const config = promptMap.get(key);
          if (!config) {
            return null;
          }
          const meta = this.promptItemMeta(key);
          return {
            kind: "prompt",
            configKey: config.configKey,
            shortKey: String(config.configKey || "").replace(
              /^(prompt|bundle)\./,
              "",
            ),
            displayName: this.promptDisplayName(key),
            description: config.description || null,
            configValue: config.configValue ?? "",
            defaultValue: config.defaultValue ?? "",
            source: config.source || "db",
            isDefault: Boolean(config.isDefault),
            updatedAt: config.updatedAt ?? null,
            variables: Array.isArray(config.variables)
              ? config.variables
              : Array.isArray(meta.variables)
                ? meta.variables
                : [],
            note: typeof meta.note === "string" ? meta.note : null,
          };
        })
        .filter(Boolean);

    const buildSupportItems = (step: PromptPipelineSectionCatalog) =>
      (step.support || []).map((support) => {
        if (support.kind === "runtime_profiles") {
          return {
            kind: "runtime_profiles",
            title: support.title,
            description: support.description || "",
            items: runtimeProfiles.map((profile: any) => ({
              kind: "runtime_profile",
              id: profile.id,
              displayName: profile.displayName,
              enabled: Boolean(profile.enabled),
              skeletonVersion: profile.skeletonVersion || "v1",
              fewShotPrompt: profile.fewShotPrompt || "",
              source: profile.source || "db",
              isDefault: Boolean(this.asPlainObject(profile.metadata).default),
              metadata: profile.metadata ?? null,
              contractSchema: profile.contractSchema ?? {},
              contractSummary: this.summarizeRuntimeProfileContract(
                profile.contractSchema,
              ),
              updatedAt: profile.updatedAt ?? null,
            })),
          };
        }
        if (support.kind === "prompt_bundle_policy") {
          return {
            kind: "prompt_bundle_policy",
            title: support.title,
            description: support.description || "",
            items: promptBundles.map((bundle: any) => ({
              kind: "prompt_bundle_policy_item",
              id: bundle.id,
              version: bundle.version,
              status: bundle.status || "draft",
              source: bundle.source || "db",
              productPolicy: bundle.productPolicy || "",
              lockedContractOverride: bundle.lockedContractOverride || "",
              profileOverrides: bundle.profileOverrides ?? null,
              metadata: bundle.metadata ?? null,
              updatedAt: bundle.updatedAt ?? null,
            })),
          };
        }
        if (support.kind === "prompt_bundle_repair") {
          return {
            kind: "prompt_bundle_repair",
            title: support.title,
            description: support.description || "",
            items: promptBundles.map((bundle: any) => ({
              kind: "prompt_bundle_repair_item",
              id: bundle.id,
              version: bundle.version,
              status: bundle.status || "draft",
              source: bundle.source || "db",
              repairPlaybook: bundle.repairPlaybook || "",
              metadata: bundle.metadata ?? null,
              updatedAt: bundle.updatedAt ?? null,
            })),
          };
        }
        return {
          kind: "note",
          title: support.title,
          description: support.description || "",
          items: [],
        };
      });

    const steps = (PROMPT_PIPELINE_CATALOG.steps || []).map((step) => {
      const promptItems = buildPromptItems(step.promptKeys || []);
      return {
        id: step.id,
        stepNumber: step.stepNumber || null,
        tag: step.tag || null,
        title: step.title,
        description: step.description || "",
        color: step.color || "#2563eb",
        bg: step.bg || "#eff6ff",
        border: step.border || "#bfdbfe",
        prompts: promptItems,
        support: buildSupportItems(step),
        itemCount: promptItems.length + (step.support || []).length,
      };
    });

    const extras = (PROMPT_PIPELINE_CATALOG.extras || []).map((section) => ({
      id: section.id,
      tag: section.tag || "附加",
      title: section.title,
      description: section.description || "",
      color: section.color || "#475569",
      bg: section.bg || "#f8fafc",
      border: section.border || "#cbd5e1",
      prompts: buildPromptItems(section.promptKeys || []),
    }));

    const unassignedPrompts = promptConfigs
      .filter((item: any) => !assignedKeys.has(item.configKey))
      .map((item: any) => ({
        kind: "prompt",
        configKey: item.configKey,
        shortKey: String(item.configKey || "").replace(
          /^(prompt|bundle)\./,
          "",
        ),
        displayName: item.displayName || this.promptDisplayName(item.configKey),
        description: item.description || null,
        configValue: item.configValue ?? "",
        defaultValue: item.defaultValue ?? "",
        source: item.source || "db",
        isDefault: Boolean(item.isDefault),
        updatedAt: item.updatedAt ?? null,
        variables: Array.isArray(item.variables) ? item.variables : [],
        note: null,
      }));

    if (unassignedPrompts.length) {
      extras.push({
        id: "extra_unassigned",
        tag: "附加",
        title: "未归档 Prompt",
        description: "catalog 之外或尚未归入五步流程的 prompt 项。",
        color: "#475569",
        bg: "#f8fafc",
        border: "#cbd5e1",
        prompts: unassignedPrompts,
      });
    }

    return {
      summary: {
        promptConfigCount: promptConfigs.length,
        promptBundleCount: promptBundles.length,
        activePromptBundleCount: promptBundles.filter(
          (bundle: any) =>
            String(bundle.status || "").toLowerCase() === "active",
        ).length,
        runtimeProfileCount: runtimeProfiles.length,
        enabledRuntimeProfileCount: runtimeProfiles.filter((profile: any) =>
          Boolean(profile.enabled),
        ).length,
      },
      steps,
      extras,
    };
  }

  async updatePromptBundle(id: string, version: number, body: any) {
    const existing = await this.prisma.promptBundle.findFirst({
      where: { id, version },
    });
    if (!existing) {
      throw new NotFoundException("Prompt bundle not found");
    }

    const section = body?.section === "repair" ? "repair" : "policy";
    const payload =
      body?.payload &&
      typeof body.payload === "object" &&
      !Array.isArray(body.payload)
        ? body.payload
        : {};
    const nextMetadata =
      payload.metadata !== undefined ? payload.metadata : existing.metadata;
    const data =
      section === "repair"
        ? {
            status:
              typeof payload.status === "string" && payload.status.trim()
                ? payload.status.trim()
                : existing.status,
            repairPlaybook:
              typeof payload.repairPlaybook === "string"
                ? payload.repairPlaybook
                : existing.repairPlaybook,
            metadata: nextMetadata,
          }
        : {
            status:
              typeof payload.status === "string" && payload.status.trim()
                ? payload.status.trim()
                : existing.status,
            productPolicy:
              typeof payload.productPolicy === "string"
                ? payload.productPolicy
                : existing.productPolicy,
            lockedContractOverride:
              payload.lockedContractOverride === null
                ? null
                : typeof payload.lockedContractOverride === "string"
                  ? payload.lockedContractOverride
                  : existing.lockedContractOverride,
            profileOverrides:
              payload.profileOverrides !== undefined
                ? payload.profileOverrides
                : existing.profileOverrides,
            metadata: nextMetadata,
          };

    await this.prisma.promptBundle.updateMany({
      where: { id, version },
      data,
    });
    const updated = await this.prisma.promptBundle.findFirst({
      where: { id, version },
    });
    const refreshResult = await this.refreshPromptConfigs();
    return {
      ...(updated || existing),
      refreshResult,
    };
  }

  async updateRuntimeProfile(id: string, body: any) {
    const existing = await this.prisma.runtimeProfileCatalog.findUnique({
      where: { id },
    });
    if (!existing) {
      throw new NotFoundException("Runtime profile not found");
    }

    const payload =
      body?.payload &&
      typeof body.payload === "object" &&
      !Array.isArray(body.payload)
        ? body.payload
        : {};
    const updated = await this.prisma.runtimeProfileCatalog.update({
      where: { id },
      data: {
        displayName:
          typeof payload.displayName === "string" && payload.displayName.trim()
            ? payload.displayName.trim()
            : existing.displayName,
        enabled:
          payload.enabled !== undefined
            ? Boolean(payload.enabled)
            : existing.enabled,
        skeletonVersion:
          typeof payload.skeletonVersion === "string" &&
          payload.skeletonVersion.trim()
            ? payload.skeletonVersion.trim()
            : existing.skeletonVersion,
        fewShotPrompt:
          payload.fewShotPrompt === null
            ? null
            : typeof payload.fewShotPrompt === "string"
              ? payload.fewShotPrompt
              : existing.fewShotPrompt,
        metadata:
          payload.metadata !== undefined ? payload.metadata : existing.metadata,
      },
    });
    const refreshResult = await this.refreshPromptConfigs();
    return {
      ...updated,
      refreshResult,
    };
  }

  // ===================== Migration =====================

  async runMigration() {
    if (process.env.DATABASE_SCHEMA_MANAGED === 'true') {
      throw new BadRequestException('Database migrations are managed by the deployment pipeline');
    }
    const sql = `
      CREATE TABLE IF NOT EXISTS system_configs (
        id VARCHAR(36) NOT NULL,
        config_key VARCHAR(128) NOT NULL,
        config_value LONGTEXT NOT NULL,
        description VARCHAR(255) NULL,
        category VARCHAR(64) NOT NULL DEFAULT 'general',
        created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        PRIMARY KEY (id),
        UNIQUE INDEX system_configs_config_key_key (config_key),
        INDEX system_configs_category_idx (category)
      ) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    `;
    await this.prisma.$executeRawUnsafe(sql);
    return { success: true, message: "system_configs table created" };
  }
}
