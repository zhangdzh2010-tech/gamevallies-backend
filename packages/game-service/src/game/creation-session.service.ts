import {
  BadRequestException,
  ConflictException,
  Injectable,
  InternalServerErrorException,
  Logger,
  NotFoundException,
  ServiceUnavailableException,
} from "@nestjs/common";
import axios from "axios";
import { PrismaService } from "../prisma/prisma.service";
import { GameService } from "./game.service";
import { GameWebSocketGateway } from "../websocket/websocket.gateway";
import { CreationSessionRealtimeService } from "./creation-session-realtime.service";
import {
  CreateCreationSessionDto,
  CreateCreationSessionMessageDto,
  GenerateCreationSessionDto,
  SkipCreationSessionQuestionDto,
} from "./dto";
import {
  CREATION_SESSION_GENERATING_EXPIRE_MS,
  CREATION_SESSION_INTERACTIVE_STATUSES,
  DEFAULT_CREATION_SESSION_QUESTION_BUDGET,
} from "./creation-session.constants";
import {
  CreationSessionConversationMessage,
  CreationSessionPlanDraft,
  CreationSessionQuestion,
  CreationSessionSnapshot,
} from "./types/creation-session.types";
import {
  buildIntentBuildSnapshot,
  normalizeIntentBuildSnapshot,
} from "./intent-build.util";
import { sanitizeUserIdea } from "../common/sanitize-idea";

const REQUIRED_SLOT_KEYS = [
  "game_type",
  "core_mechanic",
  "theme",
  "input_method",
  "win_condition",
  "difficulty",
] as const;

type ExpandPromptResponsePayload = {
  expanded_prompt?: string;
  expandedPrompt?: string;
  fallback_used?: boolean;
  fallbackUsed?: boolean;
  fallback_reason?: string;
  fallbackReason?: string;
};

@Injectable()
export class CreationSessionService {
  private readonly logger = new Logger(CreationSessionService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly gameService: GameService,
    private readonly wsGateway: GameWebSocketGateway,
    private readonly realtimeService: CreationSessionRealtimeService,
  ) {}

  async createSession(
    userId: string,
    dto: CreateCreationSessionDto,
  ): Promise<CreationSessionSnapshot> {
    const repo = this.getRepo();
    const prompt = String(dto.prompt || "").trim();
    if (!prompt) {
      throw new BadRequestException("prompt is required");
    }

    const conversation: CreationSessionConversationMessage[] = [
      this.userMessage(prompt, "prompt"),
    ];

    // Phase 1: optimistic creation (synchronous, <200ms).
    // Create the session immediately with status='initializing' and
    // return it to the client. Prompt expansion runs in the background.
    const createData = {
      userId,
      status: "initializing" as const,
      entryMode: dto.entryMode || "create",
      initialPrompt: prompt,
      titleDraft: dto.title?.trim() || null,
      revision: 1,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: null as any,
      conversation,
      sourceGameId: dto.sourceGameId || null,
      questionBudget: DEFAULT_CREATION_SESSION_QUESTION_BUDGET,
      metadata: {
        orientation: dto.orientation || null,
        generationTier: dto.generationTier || "standard",
        regionHint: dto.regionHint || null,
        expandedPrompt: null,
        readyToGenerate: false,
        slotFillPct: 0,
        intentBuild: this.buildSessionIntentBuild({
          initialPrompt: prompt,
          title: dto.title,
          entryMode: dto.entryMode || "create",
          generationTier: dto.generationTier || "standard",
        }),
      },
    };

    const generatingExpireCutoff = new Date(
      Date.now() - CREATION_SESSION_GENERATING_EXPIRE_MS,
    );

    const created = this.prisma?.$transaction
      ? await this.prisma.$transaction(async (tx: any) => {
          const scopedRepo = tx?.gameCreationSession || repo;
          // Auto-abandon interactive sessions (initializing / collecting / ready)
          await scopedRepo.updateMany({
            where: {
              userId,
              status: { in: [...CREATION_SESSION_INTERACTIVE_STATUSES] },
            },
            data: {
              status: "abandoned",
            },
          });
          // Auto-abandon stale generating sessions (older than 10 min)
          await scopedRepo.updateMany({
            where: {
              userId,
              status: "generating",
              updatedAt: { lt: generatingExpireCutoff },
            },
            data: {
              status: "abandoned",
            },
          });
          return scopedRepo.create({ data: createData });
        })
      : await (async () => {
          await repo.updateMany({
            where: {
              userId,
              status: { in: [...CREATION_SESSION_INTERACTIVE_STATUSES] },
            },
            data: {
              status: "abandoned",
            },
          });
          await repo.updateMany({
            where: {
              userId,
              status: "generating",
              updatedAt: { lt: generatingExpireCutoff },
            },
            data: {
              status: "abandoned",
            },
          });
          return repo.create({ data: createData });
        })();

    // Phase 2: async prompt expansion (fire-and-forget).
    this._finalizeSessionInit(
      created.id,
      userId,
      prompt,
      dto,
      dto.regionHint,
    ).catch((err) =>
      this.logger.error(
        `Session init async phase failed: ${created.id} - ${err?.message}`,
        err?.stack,
      ),
    );

    const initTimeoutMs =
      await this.gameService.getCreationSessionInitTimeoutMs();

    // Timeout safety net: if analysis is still running after the init watchdog,
    // auto-abandon the session so it doesn't stay stuck in 'initializing'.
    setTimeout(
      () => this._expireStaleInit(created.id).catch(() => undefined),
      initTimeoutMs,
    );

    return this.toSnapshot(created);
  }

  /**
   * Background async phase of session creation: expand the user's idea into
   * a ready-to-edit generation prompt and persist it on the session.
   */
  private async _finalizeSessionInit(
    sessionId: string,
    userId: string,
    initialPrompt: string,
    dto: CreateCreationSessionDto,
    regionHint?: string,
  ): Promise<void> {
    const repo = this.getRepo();
    let expandedPrompt = "";
    let expandFallbackUsed = false;
    let expandFallbackReason: string | null = null;
    try {
        const detail = await this.expandPromptForUserDetailed(
          initialPrompt,
          regionHint,
        );
        expandedPrompt = detail.expandedPrompt;
        expandFallbackUsed = detail.fallbackUsed;
        expandFallbackReason = detail.fallbackReason;
    } catch (error: any) {
      const initError = this.extractAiError(
        error,
        "Creation session prompt expansion failed",
      );
      await repo.updateMany({
        where: { id: sessionId, userId, status: "initializing" },
        data: {
          status: "abandoned",
          metadata: {
            orientation: dto.orientation || null,
            generationTier: dto.generationTier || "standard",
            regionHint: dto.regionHint || null,
            expandedPrompt: null,
            initError,
            abandonedAt: new Date().toISOString(),
          },
        },
      });
      this.wsGateway.emitSessionError(userId, sessionId, initError, {
        reason: "init_failed",
      });
      this.realtimeService.publishError(userId, sessionId, initError, {
        reason: "init_failed",
      });
      return;
    }

    const resolution = this.buildInitSessionResolution({
      initialPrompt,
      dto,
      expandedPrompt,
      expandFallbackUsed,
      expandFallbackReason,
    });

    const result = await repo.updateMany({
      where: { id: sessionId, userId, status: "initializing", revision: 1 },
      data: {
        revision: { increment: 1 },
        status: resolution.nextStatus,
        slotState: resolution.slotState,
        missingRequired: resolution.missingRequired,
        currentQuestion: resolution.currentQuestion,
        conversation: resolution.conversation,
        metadata: resolution.metadata,
      },
    });

    if (result.count !== 1) {
      this.logger.warn(
        `Session init CAS miss: ${sessionId} (likely abandoned)`,
      );
      return;
    }

    if (resolution.assistantReply) {
      this.realtimeService.publishReplyDone(
        userId,
        sessionId,
        resolution.assistantReply,
        "summary",
      );
    }

    await this.publishRealtimeSessionSnapshot(userId, sessionId);
  }
  /**
   * Timeout safety: abandon sessions stuck in 'initializing' too long.
   */
  private async _expireStaleInit(sessionId: string): Promise<void> {
    const repo = this.getRepo();
    // Look up the session first so we can get the userId for WS push.
    const session = await repo.findUnique({ where: { id: sessionId } });
    if (!session || session.status !== "initializing") {
      return; // Already transitioned; nothing to expire.
    }

    const existingMetadata = this.normalizeMetadata(session.metadata);
    const result = await repo.updateMany({
      where: { id: sessionId, status: "initializing" },
      data: {
        status: "abandoned",
        metadata: {
          ...existingMetadata,
          initError: "Session initialization timed out",
          abandonedAt: new Date().toISOString(),
        },
      },
    });
    if (result.count > 0) {
      this.logger.warn(`Session init expired: ${sessionId}`);
      this.wsGateway.emitSessionError(
        session.userId,
        sessionId,
        "Session initialization timed out",
        {
          reason: "init_timeout",
        },
      );
      this.realtimeService.publishError(
        session.userId,
        sessionId,
        "Session initialization timed out",
        {
          reason: "init_timeout",
        },
      );
    }
  }

  async getActiveSession(
    userId: string,
  ): Promise<CreationSessionSnapshot | null> {
    const repo = this.getRepo();
    // Return interactive sessions: initializing (prompt expansion pending),
    // collecting (waiting for prompt confirmation, plus legacy question rows),
    // or ready (can generate).
    // Generating sessions no longer occupy the active slot.
    const session = await repo.findFirst({
      where: {
        userId,
        status: { in: [...CREATION_SESSION_INTERACTIVE_STATUSES] },
      },
      orderBy: { updatedAt: "desc" },
    });
    return session ? this.toSnapshot(session) : null;
  }

  async getSession(
    userId: string,
    sessionId: string,
  ): Promise<CreationSessionSnapshot> {
    const session = await this.requireOwnedSession(userId, sessionId);
    return this.toSnapshot(session);
  }

  async appendMessage(
    userId: string,
    sessionId: string,
    dto: CreateCreationSessionMessageDto,
  ): Promise<CreationSessionSnapshot> {
    const repo = this.getRepo();
    const session = await this.requireOwnedSession(userId, sessionId);
    this.assertSessionMutable(session);
    this.assertRevision(session, dto.revision);

    const nextExpandedPrompt = String(dto.content || "").trim();
    if (!nextExpandedPrompt) {
      throw new BadRequestException("content is required");
    }

    const metadata = this.normalizeMetadata(session.metadata);
    const confirmationReply =
      this.buildPromptConfirmedReply(nextExpandedPrompt);
    const conversation = [
      ...this.normalizeConversation(session.conversation),
      this.userMessage(nextExpandedPrompt, "prompt"),
      this.assistantMessage(confirmationReply, "summary"),
    ];
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: nextExpandedPrompt,
      title: session.titleDraft,
      slotState: {},
      skippedSlots: [],
      entryMode: String(session.entryMode || "create"),
      generationTier: String(metadata.generationTier || "standard"),
      missingRequired: [],
    });

    const updateResult = await repo.updateMany({
      where: {
        id: session.id,
        userId,
        revision: session.revision,
      },
      data: {
        revision: { increment: 1 },
        status: "ready",
        slotState: {},
        missingRequired: [],
        skippedSlots: [],
        currentQuestion: null,
        conversation,
        metadata: {
          ...metadata,
          expandedPrompt: nextExpandedPrompt,
          readyToGenerate: true,
          slotFillPct: 1,
          planDraft: null,
          intentBuild,
        },
      },
    });

    if (updateResult.count !== 1) {
      throw new ConflictException(
        "Creation session was updated by another request",
      );
    }

    const next = await this.getSession(userId, session.id);
    this.realtimeService.publishReplyDone(
      userId,
      session.id,
      confirmationReply,
      "summary",
    );
    this.realtimeService.publishSnapshot(userId, session.id, next);
    return next;
  }

  async skipCurrentQuestion(
    userId: string,
    sessionId: string,
    dto: SkipCreationSessionQuestionDto,
  ): Promise<CreationSessionSnapshot> {
    const repo = this.getRepo();
    const session = await this.requireOwnedSession(userId, sessionId);
    this.assertSessionMutable(session);
    this.assertRevision(session, dto.revision);

    const metadata = this.normalizeMetadata(session.metadata);
    const skippedSlots = this.normalizeStringList(session.skippedSlots);
    const expandedPrompt = this.resolveSessionPrompt(session, metadata);
    const confirmationReply = this.buildPromptConfirmedReply(
      expandedPrompt || session.initialPrompt,
    );
    const conversation = [
      ...this.normalizeConversation(session.conversation),
      this.assistantMessage(confirmationReply, "summary"),
    ];
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: expandedPrompt || session.initialPrompt,
      title: session.titleDraft,
      planDraft: null,
      slotState: {},
      skippedSlots,
      entryMode: String(session.entryMode || "create"),
      generationTier: String(metadata.generationTier || "standard"),
      missingRequired: [],
    });

    const updateResult = await repo.updateMany({
      where: {
        id: session.id,
        userId,
        revision: session.revision,
      },
      data: {
        revision: { increment: 1 },
        status: "ready",
        slotState: {},
        missingRequired: [],
        skippedSlots,
        currentQuestion: null,
        conversation,
        metadata: {
          ...metadata,
          expandedPrompt: expandedPrompt || session.initialPrompt,
          readyToGenerate: true,
          slotFillPct: 1,
          planDraft: null,
          intentBuild,
        },
      },
    });

    if (updateResult.count !== 1) {
      throw new ConflictException(
        "Creation session was updated by another request",
      );
    }

    const next = await this.getSession(userId, session.id);
    this.realtimeService.publishReplyDone(
      userId,
      session.id,
      confirmationReply,
      "summary",
    );
    this.realtimeService.publishSnapshot(userId, session.id, next);
    return next;
  }

  async generateFromSession(
    userId: string,
    sessionId: string,
    dto: GenerateCreationSessionDto,
  ): Promise<any> {
    const repo = this.getRepo();
    const session = await this.requireOwnedSession(userId, sessionId);

    if (
      session.generatedGameId &&
      session.generationTaskId &&
      session.status === "completed"
    ) {
      throw new ConflictException("Creation session already generated a game");
    }

    this.assertSessionMutable(session);
    this.assertRevision(session, dto.revision);
    if (session.status !== "ready") {
      throw new ConflictException("Creation session is not ready to generate");
    }

    const metadata = this.normalizeMetadata(session.metadata);
    const currentPlanDraft = this.normalizePlanDraft(metadata.planDraft);
    const finalPrompt = this.resolveSessionPrompt(session, metadata);
    if (!finalPrompt) {
      throw new ConflictException(
        "Creation session does not have a confirmed prompt",
      );
    }
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: finalPrompt,
      title: session.titleDraft,
      planDraft: currentPlanDraft,
      slotState: {},
      skippedSlots: [],
      entryMode: String(session.entryMode || "create"),
      generationTier: String(metadata.generationTier || "standard"),
      missingRequired: [],
    });
    const claimResult = await repo.updateMany({
      where: {
        id: session.id,
        userId,
        revision: session.revision,
        status: session.status,
      },
      data: {
        revision: { increment: 1 },
        status: "generating",
        metadata: {
          ...metadata,
          expandedPrompt: finalPrompt,
          readyToGenerate: true,
          slotFillPct: 1,
          lastTaskStatus: "starting",
          intentBuild,
        },
      },
    });

    if (claimResult.count !== 1) {
      throw new ConflictException(
        "Creation session was updated by another request",
      );
    }

    this.realtimeService.publishSnapshot(
      userId,
      session.id,
      this.toSnapshot({
        ...session,
        revision: Number(session.revision || 1) + 1,
        status: "generating",
        metadata: {
          ...metadata,
          expandedPrompt: finalPrompt,
          readyToGenerate: true,
          slotFillPct: 1,
          lastTaskStatus: "starting",
          intentBuild,
        },
      }),
    );

    try {
      const result = await this.gameService.create(userId, {
        title: session.titleDraft || undefined,
        description: finalPrompt,
        // H.5.1 - userIdea preserves the user's original 1-line typed text
        // (initialPrompt) so the C-end never has to display the LLM-expanded
        // finalPrompt, which contains internal "Game Type: ..." spec scaffolding.
        userIdea: session.initialPrompt || undefined,
        timeoutS: dto.timeoutS,
        regionHint: this.asOptionalString(metadata.regionHint),
        orientation: this.normalizeOrientationValue(metadata.orientation),
        generationTier: this.normalizeGenerationTierValue(
          metadata.generationTier,
        ),
        creationSessionId: session.id,
        entryMode: session.entryMode,
        sourceGameId: session.sourceGameId,
      });

      // Bug 5 fix: mark session as 'completed' once the generation task is
      // successfully queued. The session's role (slot collection -> spec -> kick
      // off generation) is done; keeping it as 'generating' would block new
      // session creation and cause active-slot residue (Bug 2).
      await repo.update({
        where: { id: session.id },
        data: {
          status: "completed",
          generatedGameId: result.gameId || null,
          generationTaskId: result.generationTask?.taskId || null,
          metadata: {
            ...metadata,
            expandedPrompt: finalPrompt,
            readyToGenerate: true,
            slotFillPct: 1,
            lastTaskStatus: "queued",
            completedAt: new Date().toISOString(),
            intentBuild,
          },
        },
      });

      const creationSession = await this.getSession(userId, session.id);
      this.realtimeService.publishSnapshot(userId, session.id, creationSession);

      return {
        ...result,
        creationSession,
      };
    } catch (error) {
      await Promise.resolve(
        repo.update({
          where: { id: session.id },
          data: {
            status: "ready",
            metadata: {
              ...metadata,
              expandedPrompt: finalPrompt,
              readyToGenerate: true,
              slotFillPct: 1,
              lastTaskStatus: "failed",
              lastErrorMessage: this.extractAiError(
                error,
                "Creation session generation failed",
              ),
              intentBuild,
            },
          },
        }),
      ).catch(() => undefined);
      try {
        const rollbackSnapshot = await this.getSession(userId, session.id);
        this.realtimeService.publishSnapshot(
          userId,
          session.id,
          rollbackSnapshot,
        );
      } catch {
        // Ignore publish failures on rollback.
      }
      throw error;
    }
  }

  async abandonSession(
    userId: string,
    sessionId: string,
  ): Promise<CreationSessionSnapshot> {
    const repo = this.getRepo();
    const session = await this.requireOwnedSession(userId, sessionId);
    if (session.status === "completed") {
      throw new ConflictException("Creation session already completed");
    }
    if (session.status === "abandoned") {
      throw new ConflictException("Creation session was already abandoned");
    }
    const metadata = this.normalizeMetadata(session.metadata);
    const next = await repo.update({
      where: { id: session.id },
      data: {
        status: "abandoned",
        metadata: {
          ...metadata,
          abandonedAt: new Date().toISOString(),
        },
      },
    });
    const snapshot = this.toSnapshot(next);
    this.realtimeService.publishSnapshot(userId, session.id, snapshot);
    return snapshot;
  }

  async expandPromptForUser(
    description: string,
    regionHint?: string,
  ): Promise<string> {
    const { expandedPrompt } = await this.expandPromptForUserDetailed(
      description,
      regionHint,
    );
    return expandedPrompt;
  }

  /**
   * Same as {@link expandPromptForUser} but also surfaces whether the ai-engine
   * had to fall back to deterministic output and why. Callers that persist the
   * expanded prompt on a session (e.g. creation session init) should use this
   * variant so the UI can show a "AI fell back to a default brief, please edit"
   * hint instead of silently displaying a lower-quality result.
   *
   * Trust boundary:
   *   - ai-engine `/expand-prompt` is the single source of truth for quality
   *     detection and deterministic fallback content.
   *   - game-service still runs the returned brief through `sanitizeUserIdea`
   *     as a defensive layer so that no scaffolding text can land in
   *     `session.metadata.expandedPrompt` even if the ai-engine deploy is
   *     stale or a third-party tool writes directly to that field.
   */
  async expandPromptForUserDetailed(
    description: string,
    regionHint?: string,
  ): Promise<{
    expandedPrompt: string;
    fallbackUsed: boolean;
    fallbackReason: string | null;
  }> {
    const aiEngineUrl = await this.gameService.getAiEngineBaseUrl(regionHint);
    const timeoutMs = await this.gameService.getExpandPromptRequestTimeoutMs();
    try {
      const response = await axios.post<ExpandPromptResponsePayload>(
        `${aiEngineUrl}/api/v1/ai/expand-prompt`,
        { description },
        { timeout: timeoutMs },
      );
      const rawExpanded = this.asOptionalString(
        response.data?.expanded_prompt ?? response.data?.expandedPrompt,
      );
      if (!rawExpanded) {
        throw new BadRequestException(
          "Creation session prompt expansion returned an empty prompt",
        );
      }
      const sanitizedExpanded = sanitizeUserIdea(rawExpanded);
      let fallbackUsed = Boolean(
        response.data?.fallback_used ?? response.data?.fallbackUsed ?? false,
      );
      let fallbackReason =
        this.asOptionalString(
          response.data?.fallback_reason ?? response.data?.fallbackReason,
        ) || null;
      const sanitizedLen = sanitizedExpanded.length;
      const rawLen = rawExpanded.length;
      const normalizedDescription = (description || "").trim();
      const normalizedSanitized = sanitizedExpanded.trim();
      // Defensive: detect cases where the ai-engine output is not really an
      // expansion at all and flag them as a fallback so the frontend can
      // warn the user:
      //   1. Sanitization wiped almost everything — the LLM only produced
      //      scaffolding.
      //   2. The sanitized brief is basically the user's idea echoed back
      //      verbatim; that isn't an expansion, it's a no-op.
      //   3. The sanitized brief is suspiciously short relative to what the
      //      model returned, meaning the output was >40% scaffolding.
      if (!sanitizedExpanded || sanitizedLen < 20) {
        this.logger.warn(
          `expand-prompt output still looked like scaffolding after sanitization ` +
            `(raw_len=${rawLen}, sanitized_len=${sanitizedLen}); ` +
            `marking as fallback so the UI warns the user.`,
        );
        fallbackUsed = true;
        if (!fallbackReason) {
          fallbackReason = "empty_after_sanitization";
        }
      } else if (
        normalizedDescription.length > 0 &&
        (normalizedSanitized === normalizedDescription ||
          (sanitizedLen <= normalizedDescription.length * 1.15 &&
            normalizedSanitized.includes(normalizedDescription)))
      ) {
        // The "expanded" brief is essentially just the user's idea echoed
        // back — no expansion actually happened.
        this.logger.warn(
          `expand-prompt output is an echo of the user's idea ` +
            `(description_len=${normalizedDescription.length}, sanitized_len=${sanitizedLen}); ` +
            `marking as fallback.`,
        );
        fallbackUsed = true;
        if (!fallbackReason) {
          fallbackReason = "echoed_user_idea";
        }
      } else if (
        fallbackUsed === false &&
        rawLen > 0 &&
        sanitizedLen < rawLen * 0.6
      ) {
        // Substantial scaffolding was stripped even though ai-engine claimed
        // the output was clean. Log so we can spot drift between services,
        // but don't raise the banner — the remaining prose is still usable.
        this.logger.warn(
          `expand-prompt output lost >40% of content to sanitization ` +
            `(raw_len=${rawLen}, sanitized_len=${sanitizedLen}).`,
        );
      }
      if (fallbackUsed) {
        this.logger.warn(
          `ai-engine expand-prompt fallback used (reason=${fallbackReason ?? "unknown"})`,
        );
      }
      return {
        expandedPrompt: sanitizedExpanded || rawExpanded,
        fallbackUsed,
        fallbackReason,
      };
    } catch (error: any) {
      if (error instanceof BadRequestException) {
        throw error;
      }
      const message = this.extractAiError(
        error,
        "Creation session prompt expansion failed",
      );
      const status = error?.response?.status;
      if (status && status >= 400 && status < 500) {
        throw new BadRequestException(message);
      }
      if (
        error?.code === "ECONNABORTED" ||
        error?.code === "ECONNREFUSED" ||
        error?.code === "ETIMEDOUT"
      ) {
        throw new ServiceUnavailableException(message);
      }
      throw new InternalServerErrorException(message);
    }
  }

  private extractAiError(error: any, fallback: string): string {
    const raw =
      error?.response?.data?.detail ??
      error?.response?.data?.message ??
      error?.message ??
      fallback;
    return this.stringifyErrorDetail(raw, fallback);
  }

  private stringifyErrorDetail(value: unknown, fallback: string): string {
    if (value == null) {
      return fallback;
    }
    if (typeof value === "string") {
      const normalized = value.trim();
      return normalized || fallback;
    }
    if (Array.isArray(value)) {
      const rendered = value
        .map((item) => this.stringifyErrorDetail(item, ""))
        .map((item) => item.trim())
        .filter(Boolean);
      return rendered.join("; ") || fallback;
    }
    if (typeof value === "object") {
      const record = value as Record<string, unknown>;
      const preferred = [
        record.detail,
        record.message,
        record.msg,
        record.reason,
        record.error,
      ];
      for (const item of preferred) {
        const rendered = this.stringifyErrorDetail(item, "");
        if (rendered.trim()) {
          return rendered;
        }
      }
      const parts = Object.entries(record)
        .map(([key, item]) => {
          const rendered = this.stringifyErrorDetail(item, "");
          return rendered ? `${key}=${rendered}` : "";
        })
        .filter(Boolean);
      return parts.join(", ") || fallback;
    }
    const normalized = String(value).trim();
    return normalized || fallback;
  }

  private getRepo(): any {
    const repo = (this.prisma as any).gameCreationSession;
    if (!repo) {
      throw new NotFoundException("Creation session store is unavailable");
    }
    return repo;
  }

  private async requireOwnedSession(
    userId: string,
    sessionId: string,
  ): Promise<any> {
    const repo = this.getRepo();
    const session = await repo.findUnique({ where: { id: sessionId } });
    if (!session || session.userId !== userId) {
      throw new NotFoundException("Creation session not found");
    }
    return session;
  }

  private assertSessionMutable(session: any): void {
    if (session.status === "initializing") {
      throw new ConflictException("Creation session is still initializing");
    }
    if (session.status === "abandoned") {
      throw new ConflictException("Creation session was abandoned");
    }
    if (session.status === "completed") {
      throw new ConflictException("Creation session already completed");
    }
    if (session.status === "generating") {
      throw new ConflictException("Creation session is already generating");
    }
  }

  private assertRevision(session: any, revision?: number): void {
    if (revision && Number(session.revision) !== Number(revision)) {
      throw new ConflictException(
        "Creation session was updated by another request",
      );
    }
  }

  private buildSessionIntentBuild(params: {
    initialPrompt?: string | null;
    title?: string | null;
    planDraft?: CreationSessionPlanDraft | null;
    slotState?: Record<string, unknown> | null;
    skippedSlots?: string[] | null;
    sourceSpec?: Record<string, unknown> | null;
    entryMode?: string | null;
    generationTier?: string | null;
    missingRequired?: string[] | null;
  }) {
    return buildIntentBuildSnapshot({
      title: this.asOptionalString(params.title),
      initialPrompt: this.asOptionalString(params.initialPrompt),
      planDraft: params.planDraft,
      slotState: params.slotState || {},
      skippedSlots: params.skippedSlots || [],
      sourceSpec: params.sourceSpec || null,
      entryMode: this.asOptionalString(params.entryMode) || "create",
      generationTier:
        this.asOptionalString(params.generationTier) || "standard",
      missingRequired: params.missingRequired || [],
    });
  }

  private toSnapshot(session: any): CreationSessionSnapshot {
    const metadata = this.normalizeMetadata(session?.metadata);
    const slotState = this.normalizeSlotState(session?.slotState);
    const slotFillPct = this.clampSlotFillPct(metadata.slotFillPct, slotState);
    const currentQuestion = this.normalizeQuestion(session?.currentQuestion);
    const planDraft = this.normalizePlanDraft(metadata.planDraft);
    const intentBuild = normalizeIntentBuildSnapshot(metadata.intentBuild);
    const expandedPrompt =
      this.asOptionalString(metadata.expandedPrompt) || null;
    const sessionStatus = String(session.status || "collecting");

    // Bug 3 fix: when status is ready/generating/completed/initializing, clear currentQuestion.
    // - ready/generating/completed: "can generate", not "please answer more"
    // - initializing: AI analysis hasn't produced a question yet
    const effectiveQuestion =
      sessionStatus === "initializing" ||
      sessionStatus === "ready" ||
      sessionStatus === "generating" ||
      sessionStatus === "completed"
        ? null
        : currentQuestion;

    // Use effectiveQuestion (not raw currentQuestion) so that ready/completed
    // sessions always compute readyToGenerate=true even for legacy rows where
    // metadata.readyToGenerate is missing.
    const readyToGenerate = Boolean(
      metadata.readyToGenerate ?? (slotFillPct >= 0.67 || !effectiveQuestion),
    );

    return {
      id: String(session.id),
      streamPath: `/api/v1/games/creation-sessions/${String(session.id)}/events`,
      status: sessionStatus as CreationSessionSnapshot["status"],
      entryMode: String(
        session.entryMode || "create",
      ) as CreationSessionSnapshot["entryMode"],
      initialPrompt: String(session.initialPrompt || ""),
      expandedPrompt,
      titleDraft: session.titleDraft ? String(session.titleDraft) : null,
      revision: Number(session.revision || 1),
      slotState,
      missingRequired: this.normalizeStringList(session.missingRequired),
      skippedSlots: this.normalizeStringList(session.skippedSlots),
      currentQuestion: effectiveQuestion,
      conversation: this.normalizeConversation(session.conversation),
      slotFillPct,
      readyToGenerate,
      generatedGameId: session.generatedGameId
        ? String(session.generatedGameId)
        : null,
      generationTaskId: session.generationTaskId
        ? String(session.generationTaskId)
        : null,
      sourceGameId: session.sourceGameId ? String(session.sourceGameId) : null,
      orientation: metadata.orientation
        ? (String(metadata.orientation) as any)
        : null,
      generationTier: String(metadata.generationTier || "standard") as any,
      questionBudget: Number(
        session.questionBudget || DEFAULT_CREATION_SESSION_QUESTION_BUDGET,
      ),
      planDraft,
      confidenceSummary: null,
      questionStrategy: null,
      intentBuild: this.toPublicIntentBuild(intentBuild),
      metadata: this.toPublicMetadata(metadata),
    };
  }

  private toPublicIntentBuild(
    value: ReturnType<typeof normalizeIntentBuildSnapshot>,
  ) {
    if (!value?.brief) {
      return null;
    }
    return {
      brief: value.brief,
    };
  }

  private toPublicMetadata(
    metadata: Record<string, unknown>,
  ): CreationSessionSnapshot["metadata"] {
    const initError = this.asOptionalString(metadata.initError);
    const abandonedAt = this.asOptionalString(metadata.abandonedAt);
    const expandFallbackUsed =
      metadata.expandFallbackUsed === true ||
      metadata.expandFallbackUsed === "true";
    const expandFallbackReason =
      this.asOptionalString(metadata.expandFallbackReason) || null;
    if (!initError && !abandonedAt && !expandFallbackUsed) {
      return null;
    }
    return {
      initError: initError || null,
      abandonedAt: abandonedAt || null,
      expandFallbackUsed: expandFallbackUsed || false,
      expandFallbackReason,
    };
  }

  private normalizeMetadata(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return {};
    }
    return value as Record<string, unknown>;
  }

  private normalizeConversation(
    value: unknown,
  ): CreationSessionConversationMessage[] {
    if (!Array.isArray(value)) {
      return [];
    }

    return value
      .filter((item) => item && typeof item === "object")
      .map((item: any) => {
        const role: CreationSessionConversationMessage["role"] =
          item.role === "assistant" ? "assistant" : "user";
        return {
          role,
          content: String(item.content || ""),
          ...(item.kind
            ? {
                kind: String(
                  item.kind,
                ) as CreationSessionConversationMessage["kind"],
              }
            : {}),
          ...(item.createdAt ? { createdAt: String(item.createdAt) } : {}),
        };
      })
      .filter((item) => item.content);
  }

  private normalizeSlotState(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return {};
    }
    return value as Record<string, unknown>;
  }

  private normalizeStringList(value: unknown): string[] {
    if (!Array.isArray(value)) {
      return [];
    }
    return value.map((item) => String(item || "").trim()).filter(Boolean);
  }

  private normalizeQuestion(value: unknown): CreationSessionQuestion | null {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }

    const slotKey = String(
      (value as any).slotKey || (value as any).slot_key || "",
    ).trim();
    const prompt = String((value as any).prompt || "").trim();
    if (!slotKey || !prompt) {
      return null;
    }

    return {
      slotKey,
      label: String((value as any).label || slotKey),
      prompt,
      skippable: (value as any).skippable !== false,
    };
  }

  private normalizePlanDraft(value: unknown): CreationSessionPlanDraft | null {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }

    const title = String((value as any).title || "").trim();
    const summary = String((value as any).summary || "").trim();
    const concept = String((value as any).concept || "").trim();
    const interaction = String((value as any).interaction || "").trim();
    const objective = String((value as any).objective || "").trim();
    const pacing = String((value as any).pacing || "").trim();
    const visualDirection = String(
      (value as any).visualDirection || (value as any).visual_direction || "",
    ).trim();
    const signatureMoment = String(
      (value as any).signatureMoment || (value as any).signature_moment || "",
    ).trim();
    if (
      !title &&
      !summary &&
      !concept &&
      !interaction &&
      !objective &&
      !pacing &&
      !visualDirection &&
      !signatureMoment
    ) {
      return null;
    }

    return {
      title,
      summary,
      concept,
      interaction,
      objective,
      pacing,
      visualDirection,
      signatureMoment,
    };
  }

  private clampSlotFillPct(
    value: unknown,
    slotState: Record<string, unknown>,
  ): number {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric >= 0 && numeric <= 1) {
      return numeric;
    }
    return this.computeSlotFillPct(slotState);
  }

  private computeSlotFillPct(slotState: Record<string, unknown>): number {
    const filled = REQUIRED_SLOT_KEYS.filter((key) => {
      const value = slotState[key];
      if (Array.isArray(value)) {
        return value.length > 0;
      }
      return String(value || "").trim().length > 0;
    }).length;
    return filled / REQUIRED_SLOT_KEYS.length;
  }

  private userMessage(
    content: string,
    kind: CreationSessionConversationMessage["kind"],
  ): CreationSessionConversationMessage {
    return {
      role: "user",
      content: String(content || "").trim(),
      kind,
      createdAt: new Date().toISOString(),
    };
  }

  private assistantMessage(
    content: string,
    kind: CreationSessionConversationMessage["kind"] = "question",
  ): CreationSessionConversationMessage {
    return {
      role: "assistant",
      content: String(content || "").trim(),
      kind,
      createdAt: new Date().toISOString(),
    };
  }

  private buildPromptConfirmationQuestion(
    expandedPrompt: string,
  ): CreationSessionQuestion {
    if (this.prefersChineseCopy(expandedPrompt)) {
      return {
        slotKey: "expanded_prompt",
        label: "Prompt Confirmation",
        prompt:
          "我已经把你的想法整理成一版可直接用于生成的游戏需求说明。你可以直接确认，也可以先按自己的表达改一改，再继续生成。",
        skippable: true,
      };
    }

    return {
      slotKey: "expanded_prompt",
      label: "Prompt Confirmation",
      prompt:
        "I turned your idea into a user-facing game brief. Confirm it as-is, or edit the wording first if you want to refine it before generation.",
      skippable: true,
    };
  }

  private buildPromptConfirmedReply(expandedPrompt: string): string {
    if (this.prefersChineseCopy(expandedPrompt)) {
      return "这版生成提示词已确认，可以开始生成了。";
    }
    return "This prompt is confirmed and ready for generation.";
  }

  private prefersChineseCopy(value: string): boolean {
    return /[\u3400-\u9fff]/.test(String(value || ""));
  }

  private resolveSessionPrompt(
    session: any,
    metadata?: Record<string, unknown>,
  ): string | null {
    const normalizedMetadata =
      metadata || this.normalizeMetadata(session?.metadata);
    return (
      this.asOptionalString(normalizedMetadata.expandedPrompt) ||
      this.asOptionalString(session?.initialPrompt) ||
      null
    );
  }

  private asOptionalString(value: unknown): string | undefined {
    const normalized = String(value || "").trim();
    return normalized || undefined;
  }

  private normalizeOrientationValue(
    value: unknown,
  ): "portrait" | "landscape" | undefined {
    if (value === "portrait") {
      return "portrait";
    }
    if (value === "landscape") {
      return "landscape";
    }
    return undefined;
  }

  private normalizeGenerationTierValue(
    value: unknown,
  ): "safe" | "standard" | "showcase" | undefined {
    if (value === "safe") {
      return "safe";
    }
    if (value === "showcase") {
      return "showcase";
    }
    if (value === "standard") {
      return "standard";
    }
    return undefined;
  }

  private buildInitSessionResolution(params: {
    initialPrompt: string;
    dto: CreateCreationSessionDto;
    expandedPrompt: string;
    expandFallbackUsed?: boolean;
    expandFallbackReason?: string | null;
  }): {
    nextStatus: "collecting";
    slotState: Record<string, unknown>;
    missingRequired: string[];
    currentQuestion: CreationSessionQuestion | null;
    conversation: CreationSessionConversationMessage[];
    metadata: Record<string, unknown>;
    assistantReply: string;
  } {
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: params.expandedPrompt,
      title: params.dto.title,
      planDraft: null,
      slotState: {},
      entryMode: params.dto.entryMode || "create",
      generationTier: params.dto.generationTier || "standard",
      missingRequired: [],
    });
    const assistantReply = params.expandedPrompt;
    return {
      nextStatus: "collecting",
      slotState: {},
      missingRequired: [],
      currentQuestion: this.buildPromptConfirmationQuestion(
        params.expandedPrompt,
      ),
      conversation: [
        this.userMessage(params.initialPrompt, "prompt"),
        this.assistantMessage(assistantReply, "summary"),
      ],
      metadata: {
        orientation: params.dto.orientation || null,
        generationTier: params.dto.generationTier || "standard",
        regionHint: params.dto.regionHint || null,
        expandedPrompt: params.expandedPrompt,
        // Surface ai-engine's fallback signal so the UI can show a hint like
        // "AI expansion fell back to a default brief, please edit" instead of
        // pretending a low-quality brief came straight from the model.
        expandFallbackUsed: Boolean(params.expandFallbackUsed),
        expandFallbackReason: params.expandFallbackReason ?? null,
        readyToGenerate: false,
        slotFillPct: 1,
        planDraft: null,
        intentBuild,
      },
      assistantReply,
    };
  }

  private async publishRealtimeSessionSnapshot(
    userId: string,
    sessionId: string,
  ): Promise<void> {
    try {
      const updatedSession = await this.getRepo().findUnique({
        where: { id: sessionId },
      });
      if (!updatedSession) {
        return;
      }
      const snapshot = this.toSnapshot(updatedSession);
      this.wsGateway.emitSessionUpdate(userId, sessionId, snapshot);
      this.realtimeService.publishSnapshot(userId, sessionId, snapshot);
    } catch (wsError: any) {}
  }
}
