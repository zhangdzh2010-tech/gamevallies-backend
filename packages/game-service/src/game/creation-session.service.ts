import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from "@nestjs/common";
import { PrismaService } from "../prisma/prisma.service";
import { GameService } from "./game.service";
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
  DEFAULT_CREATION_SESSION_GENERATION_TIER,
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

const REQUIRED_SLOT_KEYS = [
  "game_type",
  "core_mechanic",
  "theme",
  "input_method",
  "win_condition",
  "difficulty",
] as const;

@Injectable()
export class CreationSessionService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly gameService: GameService,
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

    const generationTier =
      this.normalizeGenerationTierValue(dto.generationTier) ||
      DEFAULT_CREATION_SESSION_GENERATION_TIER;

    const createData = {
      userId,
      status: "ready" as const,
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
        generationTier,
        regionHint: dto.regionHint || null,
        userPrompt: prompt,
        // Backward-compatible alias for older frontends. This is no longer an
        // AI-expanded prompt; it mirrors the user's editable generation brief.
        expandedPrompt: prompt,
        readyToGenerate: true,
        slotFillPct: 1,
        planDraft: null,
        intentBuild: this.buildSessionIntentBuild({
          initialPrompt: prompt,
          title: dto.title,
          entryMode: dto.entryMode || "create",
          generationTier,
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

    return this.toSnapshot(created);
  }

  async getActiveSession(
    userId: string,
  ): Promise<CreationSessionSnapshot | null> {
    const repo = this.getRepo();
    // Return interactive sessions. New sessions are ready immediately; legacy
    // initializing/collecting rows are still included so old clients can
    // recover instead of losing their active workspace.
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

    const nextPrompt = String(dto.content || "").trim();
    if (!nextPrompt) {
      throw new BadRequestException("content is required");
    }

    const metadata = this.normalizeMetadata(session.metadata);
    const confirmationReply = this.buildPromptUpdatedReply(nextPrompt);
    const conversation = [
      ...this.normalizeConversation(session.conversation),
      this.userMessage(nextPrompt, "prompt"),
      this.assistantMessage(confirmationReply, "summary"),
    ];
    const generationTier =
      this.normalizeGenerationTierValue(metadata.generationTier) ||
      DEFAULT_CREATION_SESSION_GENERATION_TIER;
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: nextPrompt,
      title: session.titleDraft,
      slotState: {},
      skippedSlots: [],
      entryMode: String(session.entryMode || "create"),
      generationTier,
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
          generationTier,
          userPrompt: nextPrompt,
          expandedPrompt: nextPrompt,
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

    if (session.status === "ready" && !session.currentQuestion) {
      return this.toSnapshot(session);
    }

    const metadata = this.normalizeMetadata(session.metadata);
    const skippedSlots = this.normalizeStringList(session.skippedSlots);
    const finalPrompt =
      this.resolveSessionPrompt(session, metadata) || session.initialPrompt;
    const confirmationReply = this.buildPromptUpdatedReply(finalPrompt);
    const conversation = [
      ...this.normalizeConversation(session.conversation),
      this.assistantMessage(confirmationReply, "summary"),
    ];
    const generationTier =
      this.normalizeGenerationTierValue(metadata.generationTier) ||
      DEFAULT_CREATION_SESSION_GENERATION_TIER;
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: finalPrompt,
      title: session.titleDraft,
      planDraft: null,
      slotState: {},
      skippedSlots,
      entryMode: String(session.entryMode || "create"),
      generationTier,
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
          generationTier,
          userPrompt: finalPrompt,
          expandedPrompt: finalPrompt,
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
        "Creation session does not have a generation prompt",
      );
    }
    const generationTier =
      this.normalizeGenerationTierValue(metadata.generationTier) ||
      DEFAULT_CREATION_SESSION_GENERATION_TIER;
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: finalPrompt,
      title: session.titleDraft,
      planDraft: currentPlanDraft,
      slotState: {},
      skippedSlots: [],
      entryMode: String(session.entryMode || "create"),
      generationTier,
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
          generationTier,
          userPrompt: finalPrompt,
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
          generationTier,
          userPrompt: finalPrompt,
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
        // Keep C-end display copy anchored to the user's own wording.
        userIdea: session.initialPrompt || undefined,
        timeoutS: dto.timeoutS,
        regionHint: this.asOptionalString(metadata.regionHint),
        orientation: this.normalizeOrientationValue(metadata.orientation),
        generationTier,
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
            generationTier,
            userPrompt: finalPrompt,
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
              generationTier,
              userPrompt: finalPrompt,
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
        this.asOptionalString(params.generationTier) ||
        DEFAULT_CREATION_SESSION_GENERATION_TIER,
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
      this.asOptionalString(metadata.userPrompt) ||
      this.asOptionalString(session?.initialPrompt) ||
      this.asOptionalString(metadata.expandedPrompt) ||
      null;
    const sessionStatus = String(session.status || "collecting");

    // Bug 3 fix: when status is ready/generating/completed/initializing, clear currentQuestion.
    // - ready/generating/completed: "can generate", not "please answer more"
    // - initializing: legacy rows should not surface stale questions
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
      generationTier: String(
        metadata.generationTier || DEFAULT_CREATION_SESSION_GENERATION_TIER,
      ) as any,
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
    if (!initError && !abandonedAt) {
      return null;
    }
    return {
      initError: initError || null,
      abandonedAt: abandonedAt || null,
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

  private buildPromptUpdatedReply(prompt: string): string {
    if (/[\u3400-\u9fff]/.test(String(prompt || ""))) {
      return "已更新创意，可以开始生成了。";
    }
    return "Updated. Ready to generate.";
  }

  private resolveSessionPrompt(
    session: any,
    metadata?: Record<string, unknown>,
  ): string | null {
    const normalizedMetadata =
      metadata || this.normalizeMetadata(session?.metadata);
    return (
      this.asOptionalString(normalizedMetadata.userPrompt) ||
      this.asOptionalString(session?.initialPrompt) ||
      this.asOptionalString(normalizedMetadata.expandedPrompt) ||
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

}
