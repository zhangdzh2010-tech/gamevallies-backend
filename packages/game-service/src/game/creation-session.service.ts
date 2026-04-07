import {
  BadRequestException,
  ConflictException,
  Injectable,
  InternalServerErrorException,
  Logger,
  NotFoundException,
  ServiceUnavailableException,
} from '@nestjs/common';
import axios from 'axios';
import { PrismaService } from '../prisma/prisma.service';
import { GameService } from './game.service';
import { GameWebSocketGateway } from '../websocket/websocket.gateway';
import { CreationSessionRealtimeService } from './creation-session-realtime.service';
import {
  CreateCreationSessionDto,
  CreateCreationSessionMessageDto,
  GenerateCreationSessionDto,
  SkipCreationSessionQuestionDto,
} from './dto';
import {
  CREATION_SESSION_GENERATING_EXPIRE_MS,
  CREATION_SESSION_INIT_TIMEOUT_MS,
  CREATION_SESSION_INTERACTIVE_STATUSES,
  DEFAULT_CREATION_SESSION_QUESTION_BUDGET,
} from './creation-session.constants';
import {
  CreationSessionConfidenceSummary,
  CreationSessionConversationMessage,
  CreationSessionPlanDraft,
  CreationSessionQuestion,
  CreationSessionQuestionStrategy,
  CreationSessionSnapshot,
} from './types/creation-session.types';
import {
  buildIntentBuildSnapshot,
  normalizeIntentBuildSnapshot,
} from './intent-build.util';

const REQUIRED_SLOT_KEYS = [
  'game_type',
  'core_mechanic',
  'theme',
  'input_method',
  'win_condition',
  'difficulty',
] as const;

type AnalyzeTurnResponsePayload = {
  reply: string;
  slots: Record<string, unknown>;
  slots_updated: string[];
  missing_required: string[];
  slot_fill_pct: number;
  ready_to_generate: boolean;
  confidence_by_slot?: Record<string, unknown>;
  evidence_by_slot?: Record<string, unknown>;
  ambiguity_flags?: string[];
  next_best_question_reason?: string | null;
  question_strategy?: {
    mode?: string;
    slot_key?: string | null;
    slotKey?: string | null;
    reason?: string;
    impact?: number;
    confidence?: number;
    ambiguity_weight?: number;
    ambiguityWeight?: number;
  } | null;
  plan_draft?: {
    title?: string;
    summary?: string;
    concept?: string;
    interaction?: string;
    objective?: string;
    pacing?: string;
    visual_direction?: string;
    visualDirection?: string;
    signature_moment?: string;
    signatureMoment?: string;
  } | null;
  current_question?: {
    slot_key?: string;
    slotKey?: string;
    label?: string;
    prompt?: string;
    skippable?: boolean;
  } | null;
};

type AnalyzeTurnRequestPayload = {
  session_id?: string;
  user_id: string;
  conversation: CreationSessionConversationMessage[];
  current_slots: Record<string, unknown>;
  skipped_slots: string[];
  entry_mode: string;
  generation_tier: string;
  title?: string;
  initial_prompt?: string;
  answered_slot_key?: string;
  answered_slot_prompt?: string;
  latest_user_answer?: string;
  advance_only?: boolean;
};

type SpecFromSlotsResponsePayload = {
  spec: Record<string, unknown>;
  missing_required: string[];
  slot_fill_pct: number;
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

  async createSession(userId: string, dto: CreateCreationSessionDto): Promise<CreationSessionSnapshot> {
    const repo = this.getRepo();
    const prompt = String(dto.prompt || '').trim();
    if (!prompt) {
      throw new BadRequestException('prompt is required');
    }

    const conversation: CreationSessionConversationMessage[] = [
      this.userMessage(prompt, 'prompt'),
    ];

    // ── Phase 1: Optimistic creation (synchronous, <200ms) ──────────
    // Create the session immediately with status='initializing' and
    // return it to the client. AI analysis runs in the background.
    const createData = {
      userId,
      status: 'initializing' as const,
      entryMode: dto.entryMode || 'create',
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
        generationTier: dto.generationTier || 'standard',
        regionHint: dto.regionHint || null,
        readyToGenerate: false,
        slotFillPct: 0,
        intentBuild: this.buildSessionIntentBuild({
          initialPrompt: prompt,
          title: dto.title,
          entryMode: dto.entryMode || 'create',
          generationTier: dto.generationTier || 'standard',
        }),
      },
    };

    const generatingExpireCutoff = new Date(Date.now() - CREATION_SESSION_GENERATING_EXPIRE_MS);

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
              status: 'abandoned',
            },
          });
          // Auto-abandon stale generating sessions (older than 10 min)
          await scopedRepo.updateMany({
            where: {
              userId,
              status: 'generating',
              updatedAt: { lt: generatingExpireCutoff },
            },
            data: {
              status: 'abandoned',
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
              status: 'abandoned',
            },
          });
          await repo.updateMany({
            where: {
              userId,
              status: 'generating',
              updatedAt: { lt: generatingExpireCutoff },
            },
            data: {
              status: 'abandoned',
            },
          });
          return repo.create({ data: createData });
        })();

    // ── Phase 2: Async analysis (fire-and-forget) ───────────────────
    const analyzePayload = this.buildAnalyzeTurnPayload({
      sessionId: created.id,
      userId,
      conversation,
      currentSlots: {},
      skippedSlots: [],
      entryMode: dto.entryMode || 'create',
      title: dto.title,
      generationTier: dto.generationTier || 'standard',
      initialPrompt: prompt,
    });

    this._finalizeSessionInit(created.id, userId, analyzePayload, dto, dto.regionHint)
      .catch((err) => this.logger.error(
        `Session init async phase failed: ${created.id} — ${err?.message}`,
        err?.stack,
      ));

    // Timeout safety net: if analysis is still running after INIT_TIMEOUT_MS,
    // auto-abandon the session so it doesn't stay stuck in 'initializing'.
    setTimeout(
      () => this._expireStaleInit(created.id).catch(() => undefined),
      CREATION_SESSION_INIT_TIMEOUT_MS,
    );

    return this.toSnapshot(created);
  }

  /**
   * Background async phase of session creation: run AI analysis and
   * update the session from 'initializing' to 'collecting'/'ready'.
   */
  private async _finalizeSessionInit(
    sessionId: string,
    userId: string,
    analyzePayload: AnalyzeTurnRequestPayload,
    dto: CreateCreationSessionDto,
    regionHint?: string,
  ): Promise<void> {
    const repo = this.getRepo();

    let analysis: AnalyzeTurnResponsePayload;
    try {
      analysis = await this.analyzeTurn(analyzePayload, regionHint);
    } catch (error: any) {
      // AI analysis failed → mark session as abandoned with error info
      const initError = this.extractAiError(error, 'Creation session initialization failed');
      await repo.updateMany({
        where: { id: sessionId, userId, status: 'initializing' },
        data: {
          status: 'abandoned',
          metadata: {
            orientation: dto.orientation || null,
            generationTier: dto.generationTier || 'standard',
            regionHint: dto.regionHint || null,
            initError,
            abandonedAt: new Date().toISOString(),
          },
        },
      });
      // Notify frontend immediately so it can show the error without polling
      this.wsGateway.emitSessionError(userId, sessionId, initError, {
        reason: 'init_failed',
      });
      this.realtimeService.publishError(userId, sessionId, initError, {
        reason: 'init_failed',
      });
      return;
    }

    // Build the full session data from analysis results
    const conversation: CreationSessionConversationMessage[] = [
      this.userMessage(String(analyzePayload.initial_prompt || ''), 'prompt'),
      this.assistantMessage(analysis.reply),
    ];
    const normalizedPlanDraft = this.normalizePlanDraft(analysis.plan_draft);
    const normalizedQuestionStrategy = this.normalizeQuestionStrategy(analysis.question_strategy);
    const confidenceSummary = this.buildConfidenceSummary(
      analysis.confidence_by_slot,
      analysis.ambiguity_flags,
      analysis.missing_required,
    );
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: analyzePayload.initial_prompt,
      title: dto.title,
      planDraft: normalizedPlanDraft,
      slotState: analysis.slots || {},
      entryMode: dto.entryMode || 'create',
      generationTier: dto.generationTier || 'standard',
      missingRequired: analysis.missing_required || [],
    });

    // CAS update: only proceed if session is still in 'initializing' (not abandoned by user)
    const result = await repo.updateMany({
      where: { id: sessionId, userId, status: 'initializing', revision: 1 },
      data: {
        revision: { increment: 1 },
        status: analysis.ready_to_generate ? 'ready' : 'collecting',
        slotState: analysis.slots || {},
        missingRequired: analysis.missing_required || [],
        currentQuestion: this.normalizeQuestion(analysis.current_question),
        conversation,
        metadata: {
          orientation: dto.orientation || null,
          generationTier: dto.generationTier || 'standard',
          regionHint: dto.regionHint || null,
          readyToGenerate: Boolean(analysis.ready_to_generate),
          slotFillPct: this.clampSlotFillPct(analysis.slot_fill_pct, analysis.slots || {}),
          planDraft: normalizedPlanDraft,
          confidenceSummary,
          questionStrategy: normalizedQuestionStrategy,
          intentBuild,
          confidenceBySlot: this.normalizeNumberMap(analysis.confidence_by_slot),
          evidenceBySlot: this.normalizeStringMap(analysis.evidence_by_slot),
          ambiguityFlags: this.normalizeStringList(analysis.ambiguity_flags),
          nextBestQuestionReason: this.asOptionalString(analysis.next_best_question_reason) || normalizedQuestionStrategy?.reason || null,
        },
      },
    });

    if (result.count !== 1) {
      // Session was already abandoned or modified by the user — silently discard analysis
      this.logger.warn(`Session init CAS miss: ${sessionId} (likely abandoned)`);
      return;
    }

    // Push real-time update to the frontend so it can display the first
    // question immediately instead of waiting for the next poll cycle.
    try {
      const updatedSession = await repo.findUnique({ where: { id: sessionId } });
      if (updatedSession) {
        const snapshot = this.toSnapshot(updatedSession);
        this.wsGateway.emitSessionUpdate(userId, sessionId, snapshot);
        this.realtimeService.publishSnapshot(userId, sessionId, snapshot);
      }
    } catch (wsError: any) {
      // Non-critical: frontend will still get the data on next poll
      this.logger.warn(`Session WS push failed: ${sessionId} — ${wsError?.message}`);
    }
  }

  /**
   * Timeout safety: abandon sessions stuck in 'initializing' too long.
   */
  private async _expireStaleInit(sessionId: string): Promise<void> {
    const repo = this.getRepo();
    // Look up the session first so we can get the userId for WS push
    const session = await repo.findUnique({ where: { id: sessionId } });
    if (!session || session.status !== 'initializing') {
      return; // Already transitioned — nothing to expire
    }

    const existingMetadata = this.normalizeMetadata(session.metadata);
    const result = await repo.updateMany({
      where: { id: sessionId, status: 'initializing' },
      data: {
        status: 'abandoned',
        metadata: {
          ...existingMetadata,
          initError: 'Session initialization timed out',
          abandonedAt: new Date().toISOString(),
        },
      },
    });
    if (result.count > 0) {
      this.logger.warn(`Session init expired: ${sessionId}`);
      this.wsGateway.emitSessionError(session.userId, sessionId, 'Session initialization timed out', {
        reason: 'init_timeout',
      });
      this.realtimeService.publishError(session.userId, sessionId, 'Session initialization timed out', {
        reason: 'init_timeout',
      });
    }
  }

  async getActiveSession(userId: string): Promise<CreationSessionSnapshot | null> {
    const repo = this.getRepo();
    // Return interactive sessions: initializing (AI analysis pending),
    // collecting (asking questions), or ready (can generate).
    // Generating sessions no longer occupy the active slot.
    const session = await repo.findFirst({
      where: {
        userId,
        status: { in: [...CREATION_SESSION_INTERACTIVE_STATUSES] },
      },
      orderBy: { updatedAt: 'desc' },
    });
    return session ? this.toSnapshot(session) : null;
  }

  async getSession(userId: string, sessionId: string): Promise<CreationSessionSnapshot> {
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

    const conversation = [
      ...this.normalizeConversation(session.conversation),
      this.userMessage(dto.content, 'answer'),
    ];
    const metadata = this.normalizeMetadata(session.metadata);
    const skippedSlots = this.normalizeStringList(session.skippedSlots);
    const currentQuestion = this.normalizeQuestion(session.currentQuestion);
    const analysis = await this.analyzeTurn(
      this.buildAnalyzeTurnPayload({
        sessionId: session.id,
        userId,
        conversation,
        currentSlots: this.normalizeSlotState(session.slotState),
        skippedSlots,
        entryMode: String(session.entryMode || 'create'),
        title: session.titleDraft || undefined,
        generationTier: String(metadata.generationTier || 'standard'),
        initialPrompt: session.initialPrompt,
        answeredSlot: currentQuestion,
        latestUserAnswer: dto.content,
      }),
      this.asOptionalString(metadata.regionHint),
    );

    const nextQuestion = this.normalizeQuestion(analysis.current_question);
    const normalizedPlanDraft = this.normalizePlanDraft(analysis.plan_draft);
    const normalizedQuestionStrategy = this.normalizeQuestionStrategy(analysis.question_strategy);
    const confidenceSummary = this.buildConfidenceSummary(
      analysis.confidence_by_slot,
      analysis.ambiguity_flags,
      analysis.missing_required,
    );
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: session.initialPrompt,
      title: session.titleDraft,
      planDraft: normalizedPlanDraft,
      slotState: analysis.slots || {},
      skippedSlots,
      entryMode: String(session.entryMode || 'create'),
      generationTier: String(metadata.generationTier || 'standard'),
      missingRequired: analysis.missing_required || [],
    });
    // Bug 4 fix: once a session reaches 'ready', it never reverts to 'collecting'.
    // This prevents unstable oscillation near the AI engine's fill_pct threshold.
    const nextStatus = analysis.ready_to_generate
      ? 'ready'
      : (session.status === 'ready' ? 'ready' : 'collecting');

    const updateResult = await repo.updateMany({
      where: {
        id: session.id,
        userId,
        revision: session.revision,
      },
      data: {
        revision: { increment: 1 },
        status: nextStatus,
        slotState: analysis.slots || {},
        missingRequired: analysis.missing_required || [],
        skippedSlots,
        currentQuestion: nextQuestion,
        conversation: [
          ...conversation,
          this.assistantMessage(analysis.reply),
        ],
        metadata: {
          ...metadata,
          readyToGenerate: Boolean(analysis.ready_to_generate || session.status === 'ready'),
          slotFillPct: this.clampSlotFillPct(analysis.slot_fill_pct, analysis.slots || {}),
          planDraft: normalizedPlanDraft,
          confidenceSummary,
          questionStrategy: normalizedQuestionStrategy,
          intentBuild,
          confidenceBySlot: this.normalizeNumberMap(analysis.confidence_by_slot),
          evidenceBySlot: this.normalizeStringMap(analysis.evidence_by_slot),
          ambiguityFlags: this.normalizeStringList(analysis.ambiguity_flags),
          nextBestQuestionReason: this.asOptionalString(analysis.next_best_question_reason) || normalizedQuestionStrategy?.reason || null,
        },
      },
    });

    if (updateResult.count !== 1) {
      throw new ConflictException('Creation session was updated by another request');
    }

    const next = await this.getSession(userId, session.id);
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
    const currentQuestion = this.normalizeQuestion(session.currentQuestion);
    const skippedSlots = this.uniqueStrings([
      ...this.normalizeStringList(session.skippedSlots),
      ...(currentQuestion?.slotKey ? [currentQuestion.slotKey] : []),
    ]);
    const conversation = this.normalizeConversation(session.conversation);
    const analysis = await this.analyzeTurn(
      this.buildAnalyzeTurnPayload({
        sessionId: session.id,
        userId,
        conversation,
        currentSlots: this.normalizeSlotState(session.slotState),
        skippedSlots,
        entryMode: String(session.entryMode || 'create'),
        title: session.titleDraft || undefined,
        generationTier: String(metadata.generationTier || 'standard'),
        initialPrompt: session.initialPrompt,
        advanceOnly: true,
      }),
      this.asOptionalString(metadata.regionHint),
    );
    const normalizedPlanDraft = this.normalizePlanDraft(analysis.plan_draft);
    const normalizedQuestionStrategy = this.normalizeQuestionStrategy(analysis.question_strategy);
    const confidenceSummary = this.buildConfidenceSummary(
      analysis.confidence_by_slot,
      analysis.ambiguity_flags,
      analysis.missing_required,
    );
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: session.initialPrompt,
      title: session.titleDraft,
      planDraft: normalizedPlanDraft,
      slotState: analysis.slots || {},
      skippedSlots,
      entryMode: String(session.entryMode || 'create'),
      generationTier: String(metadata.generationTier || 'standard'),
      missingRequired: analysis.missing_required || [],
    });

    // Bug 4 fix: same single-direction locking as in appendMessage()
    const nextStatus = analysis.ready_to_generate
      ? 'ready'
      : (session.status === 'ready' ? 'ready' : 'collecting');

    const updateResult = await repo.updateMany({
      where: {
        id: session.id,
        userId,
        revision: session.revision,
      },
      data: {
        revision: { increment: 1 },
        status: nextStatus,
        missingRequired: analysis.missing_required || [],
        skippedSlots,
        currentQuestion: this.normalizeQuestion(analysis.current_question),
        conversation: [
          ...conversation,
          this.assistantMessage(analysis.reply, 'question'),
        ],
        metadata: {
          ...metadata,
          readyToGenerate: Boolean(analysis.ready_to_generate || session.status === 'ready'),
          slotFillPct: this.clampSlotFillPct(analysis.slot_fill_pct, session.slotState || {}),
          planDraft: normalizedPlanDraft,
          confidenceSummary,
          questionStrategy: normalizedQuestionStrategy,
          intentBuild,
          confidenceBySlot: this.normalizeNumberMap(analysis.confidence_by_slot),
          evidenceBySlot: this.normalizeStringMap(analysis.evidence_by_slot),
          ambiguityFlags: this.normalizeStringList(analysis.ambiguity_flags),
          nextBestQuestionReason: this.asOptionalString(analysis.next_best_question_reason) || normalizedQuestionStrategy?.reason || null,
        },
      },
    });

    if (updateResult.count !== 1) {
      throw new ConflictException('Creation session was updated by another request');
    }

    const next = await this.getSession(userId, session.id);
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

    if (session.generatedGameId && session.generationTaskId && session.status === 'completed') {
      throw new ConflictException('Creation session already generated a game');
    }

    this.assertSessionMutable(session);
    this.assertRevision(session, dto.revision);

    const metadata = this.normalizeMetadata(session.metadata);
    const currentPlanDraft = this.normalizePlanDraft(metadata.planDraft);
    const specResponse = await this.specFromSlots({
      session_id: session.id,
      slots: this.normalizeSlotState(session.slotState),
      source_description: session.initialPrompt,
      title: session.titleDraft || undefined,
      generation_tier: String(metadata.generationTier || 'standard'),
      skipped_slots: this.normalizeStringList(session.skippedSlots),
      variation_seed: session.id,
    }, this.asOptionalString(metadata.regionHint));
    const intentBuild = this.buildSessionIntentBuild({
      initialPrompt: session.initialPrompt,
      title: session.titleDraft,
      planDraft: currentPlanDraft,
      slotState: this.normalizeSlotState(session.slotState),
      skippedSlots: this.normalizeStringList(session.skippedSlots),
      sourceSpec: specResponse.spec || null,
      entryMode: String(session.entryMode || 'create'),
      generationTier: String(metadata.generationTier || 'standard'),
      missingRequired: specResponse.missing_required || [],
    });

    const rollbackStatus = session.status === 'collecting' ? 'collecting' : 'ready';
    const claimResult = await repo.updateMany({
      where: {
        id: session.id,
        userId,
        revision: session.revision,
        status: session.status,
      },
      data: {
        revision: { increment: 1 },
        status: 'generating',
        metadata: {
          ...metadata,
          readyToGenerate: true,
          slotFillPct: specResponse.slot_fill_pct ?? this.computeSlotFillPct(session.slotState || {}),
          lastTaskStatus: 'starting',
          intentBuild,
        },
      },
    });

    if (claimResult.count !== 1) {
      throw new ConflictException('Creation session was updated by another request');
    }

    this.realtimeService.publishSnapshot(
      userId,
      session.id,
      this.toSnapshot({
        ...session,
        revision: Number(session.revision || 1) + 1,
        status: 'generating',
        metadata: {
          ...metadata,
          readyToGenerate: true,
          slotFillPct: specResponse.slot_fill_pct ?? this.computeSlotFillPct(session.slotState || {}),
          lastTaskStatus: 'starting',
          intentBuild,
        },
      }),
    );

    try {
      const result = await this.gameService.create(userId, {
        title: session.titleDraft || undefined,
        description: session.initialPrompt,
        timeoutS: dto.timeoutS,
        regionHint: this.asOptionalString(metadata.regionHint),
        orientation: this.normalizeOrientationValue(metadata.orientation),
        generationTier: this.normalizeGenerationTierValue(metadata.generationTier),
        sourceSpec: specResponse.spec || null,
        creationSessionId: session.id,
        entryMode: session.entryMode,
        sourceGameId: session.sourceGameId,
      });

      // Bug 5 fix: mark session as 'completed' once the generation task is
      // successfully queued. The session's role (slot collection → spec → kick
      // off generation) is done; keeping it as 'generating' would block new
      // session creation and cause active-slot residue (Bug 2).
      await repo.update({
        where: { id: session.id },
        data: {
          status: 'completed',
          generatedGameId: result.gameId || null,
          generationTaskId: result.generationTask?.taskId || null,
          metadata: {
            ...metadata,
            readyToGenerate: true,
            slotFillPct: specResponse.slot_fill_pct ?? this.computeSlotFillPct(session.slotState || {}),
            lastTaskStatus: 'queued',
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
      await Promise.resolve(repo.update({
        where: { id: session.id },
        data: {
          status: rollbackStatus,
          metadata: {
            ...metadata,
            readyToGenerate: true,
            slotFillPct: specResponse.slot_fill_pct ?? this.computeSlotFillPct(session.slotState || {}),
            lastTaskStatus: 'failed',
            lastErrorMessage: this.extractAiError(error, 'Creation session generation failed'),
            intentBuild,
          },
        },
      })).catch(() => undefined);
      try {
        const rollbackSnapshot = await this.getSession(userId, session.id);
        this.realtimeService.publishSnapshot(userId, session.id, rollbackSnapshot);
      } catch {
        // Ignore publish failures on rollback.
      }
      throw error;
    }
  }

  async abandonSession(userId: string, sessionId: string): Promise<CreationSessionSnapshot> {
    const repo = this.getRepo();
    const session = await this.requireOwnedSession(userId, sessionId);
    if (session.status === 'completed') {
      throw new ConflictException('Creation session already completed');
    }
    if (session.status === 'abandoned') {
      throw new ConflictException('Creation session was already abandoned');
    }
    const metadata = this.normalizeMetadata(session.metadata);
    const next = await repo.update({
      where: { id: session.id },
      data: {
        status: 'abandoned',
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

  private async analyzeTurn(
    payload: AnalyzeTurnRequestPayload,
    regionHint?: string,
  ): Promise<AnalyzeTurnResponsePayload> {
    const aiEngineUrl = await this.gameService.getAiEngineBaseUrl(regionHint);
    const timeoutMs = await this.gameService.getExpandPromptRequestTimeoutMs();
    try {
      const response = await axios.post(
        `${aiEngineUrl}/api/v1/ai/dialogue/analyze-turn`,
        payload,
        { timeout: timeoutMs },
      );
      return response.data;
    } catch (error: any) {
      const message = this.extractAiError(error, 'Creation session analyze-turn failed');
      const status = error?.response?.status;
      // AI engine returned a client error → treat as bad request
      if (status && status >= 400 && status < 500) {
        throw new BadRequestException(message);
      }
      // Network timeout / connection refused / AI engine 5xx → upstream failure
      if (error?.code === 'ECONNABORTED' || error?.code === 'ECONNREFUSED' || error?.code === 'ETIMEDOUT') {
        throw new ServiceUnavailableException(message);
      }
      throw new InternalServerErrorException(message);
    }
  }

  private async specFromSlots(
    payload: Record<string, unknown>,
    regionHint?: string,
  ): Promise<SpecFromSlotsResponsePayload> {
    const aiEngineUrl = await this.gameService.getAiEngineBaseUrl(regionHint);
    const timeoutMs = await this.gameService.getExpandPromptRequestTimeoutMs();
    try {
      const response = await axios.post(
        `${aiEngineUrl}/api/v1/ai/dialogue/spec-from-slots`,
        payload,
        { timeout: timeoutMs },
      );
      return response.data;
    } catch (error: any) {
      const message = this.extractAiError(error, 'Creation session spec compilation failed');
      const status = error?.response?.status;
      if (status && status >= 400 && status < 500) {
        throw new BadRequestException(message);
      }
      if (error?.code === 'ECONNABORTED' || error?.code === 'ECONNREFUSED' || error?.code === 'ETIMEDOUT') {
        throw new ServiceUnavailableException(message);
      }
      throw new InternalServerErrorException(message);
    }
  }

  private extractAiError(error: any, fallback: string): string {
    const raw = error?.response?.data?.detail
      ?? error?.response?.data?.message
      ?? error?.message
      ?? fallback;
    return this.stringifyErrorDetail(raw, fallback);
  }

  private stringifyErrorDetail(value: unknown, fallback: string): string {
    if (value == null) {
      return fallback;
    }
    if (typeof value === 'string') {
      const normalized = value.trim();
      return normalized || fallback;
    }
    if (Array.isArray(value)) {
      const rendered = value
        .map((item) => this.stringifyErrorDetail(item, ''))
        .map((item) => item.trim())
        .filter(Boolean);
      return rendered.join('; ') || fallback;
    }
    if (typeof value === 'object') {
      const record = value as Record<string, unknown>;
      const preferred = [
        record.detail,
        record.message,
        record.msg,
        record.reason,
        record.error,
      ];
      for (const item of preferred) {
        const rendered = this.stringifyErrorDetail(item, '');
        if (rendered.trim()) {
          return rendered;
        }
      }
      const parts = Object.entries(record)
        .map(([key, item]) => {
          const rendered = this.stringifyErrorDetail(item, '');
          return rendered ? `${key}=${rendered}` : '';
        })
        .filter(Boolean);
      return parts.join(', ') || fallback;
    }
    const normalized = String(value).trim();
    return normalized || fallback;
  }

  private getRepo(): any {
    const repo = (this.prisma as any).gameCreationSession;
    if (!repo) {
      throw new NotFoundException('Creation session store is unavailable');
    }
    return repo;
  }

  private async requireOwnedSession(userId: string, sessionId: string): Promise<any> {
    const repo = this.getRepo();
    const session = await repo.findUnique({ where: { id: sessionId } });
    if (!session || session.userId !== userId) {
      throw new NotFoundException('Creation session not found');
    }
    return session;
  }

  private assertSessionMutable(session: any): void {
    if (session.status === 'initializing') {
      throw new ConflictException('Creation session is still initializing');
    }
    if (session.status === 'abandoned') {
      throw new ConflictException('Creation session was abandoned');
    }
    if (session.status === 'completed') {
      throw new ConflictException('Creation session already completed');
    }
    if (session.status === 'generating') {
      throw new ConflictException('Creation session is already generating');
    }
  }

  private assertRevision(session: any, revision?: number): void {
    if (revision && Number(session.revision) !== Number(revision)) {
      throw new ConflictException('Creation session was updated by another request');
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
      entryMode: this.asOptionalString(params.entryMode) || 'create',
      generationTier: this.asOptionalString(params.generationTier) || 'standard',
      missingRequired: params.missingRequired || [],
    });
  }

  private toSnapshot(session: any): CreationSessionSnapshot {
    const metadata = this.normalizeMetadata(session?.metadata);
    const slotState = this.normalizeSlotState(session?.slotState);
    const slotFillPct = this.clampSlotFillPct(metadata.slotFillPct, slotState);
    const currentQuestion = this.normalizeQuestion(session?.currentQuestion);
    const planDraft = this.normalizePlanDraft(metadata.planDraft);
    const confidenceSummary = this.normalizeConfidenceSummary(metadata.confidenceSummary);
    const questionStrategy = this.normalizeQuestionStrategy(metadata.questionStrategy);
    const intentBuild = normalizeIntentBuildSnapshot(metadata.intentBuild);
    const sessionStatus = String(session.status || 'collecting');

    // Bug 3 fix: when status is ready/generating/completed/initializing, clear currentQuestion.
    // - ready/generating/completed: "can generate", not "please answer more"
    // - initializing: AI analysis hasn't produced a question yet
    const effectiveQuestion = (sessionStatus === 'initializing' || sessionStatus === 'ready' || sessionStatus === 'generating' || sessionStatus === 'completed')
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
      status: sessionStatus as CreationSessionSnapshot['status'],
      entryMode: String(session.entryMode || 'create') as CreationSessionSnapshot['entryMode'],
      initialPrompt: String(session.initialPrompt || ''),
      titleDraft: session.titleDraft ? String(session.titleDraft) : null,
      revision: Number(session.revision || 1),
      slotState,
      missingRequired: this.normalizeStringList(session.missingRequired),
      skippedSlots: this.normalizeStringList(session.skippedSlots),
      currentQuestion: effectiveQuestion,
      conversation: this.normalizeConversation(session.conversation),
      slotFillPct,
      readyToGenerate,
      generatedGameId: session.generatedGameId ? String(session.generatedGameId) : null,
      generationTaskId: session.generationTaskId ? String(session.generationTaskId) : null,
      sourceGameId: session.sourceGameId ? String(session.sourceGameId) : null,
      orientation: metadata.orientation ? String(metadata.orientation) as any : null,
      generationTier: String(metadata.generationTier || 'standard') as any,
      questionBudget: Number(session.questionBudget || DEFAULT_CREATION_SESSION_QUESTION_BUDGET),
      planDraft,
      confidenceSummary,
      questionStrategy,
      intentBuild,
      metadata,
    };
  }

  private normalizeMetadata(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return {};
    }
    return value as Record<string, unknown>;
  }

  private normalizeConversation(value: unknown): CreationSessionConversationMessage[] {
    if (!Array.isArray(value)) {
      return [];
    }

    return value
      .filter((item) => item && typeof item === 'object')
      .map((item: any) => {
        const role: CreationSessionConversationMessage['role'] = item.role === 'assistant' ? 'assistant' : 'user';
        return {
          role,
          content: String(item.content || ''),
          ...(item.kind ? { kind: String(item.kind) as CreationSessionConversationMessage['kind'] } : {}),
          ...(item.createdAt ? { createdAt: String(item.createdAt) } : {}),
        };
      })
      .filter((item) => item.content);
  }

  private normalizeSlotState(value: unknown): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return {};
    }
    return value as Record<string, unknown>;
  }

  private normalizeStringList(value: unknown): string[] {
    if (!Array.isArray(value)) {
      return [];
    }
    return value
      .map((item) => String(item || '').trim())
      .filter(Boolean);
  }

  private normalizeQuestion(value: unknown): CreationSessionQuestion | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return null;
    }

    const slotKey = String((value as any).slotKey || (value as any).slot_key || '').trim();
    const prompt = String((value as any).prompt || '').trim();
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
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return null;
    }

    const title = String((value as any).title || '').trim();
    const summary = String((value as any).summary || '').trim();
    const concept = String((value as any).concept || '').trim();
    const interaction = String((value as any).interaction || '').trim();
    const objective = String((value as any).objective || '').trim();
    const pacing = String((value as any).pacing || '').trim();
    const visualDirection = String((value as any).visualDirection || (value as any).visual_direction || '').trim();
    const signatureMoment = String((value as any).signatureMoment || (value as any).signature_moment || '').trim();
    if (!title && !summary && !concept && !interaction && !objective && !pacing && !visualDirection && !signatureMoment) {
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

  private normalizeConfidenceSummary(value: unknown): CreationSessionConfidenceSummary | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return null;
    }

    const overallConfidence = this.clampRatio((value as any).overallConfidence ?? (value as any).overall_confidence);
    return {
      overallConfidence,
      strongestSlots: this.normalizeStringList((value as any).strongestSlots || (value as any).strongest_slots),
      weakestSlots: this.normalizeStringList((value as any).weakestSlots || (value as any).weakest_slots),
      ambiguityFlags: this.normalizeStringList((value as any).ambiguityFlags || (value as any).ambiguity_flags),
      missingCriticalSlots: this.normalizeStringList((value as any).missingCriticalSlots || (value as any).missing_critical_slots),
    };
  }

  private normalizeQuestionStrategy(value: unknown): CreationSessionQuestionStrategy | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return null;
    }
    const reason = String((value as any).reason || '').trim();
    const slotKey = this.asOptionalString((value as any).slotKey || (value as any).slot_key) || null;
    if (!reason && !slotKey) {
      return null;
    }

    return {
      mode: String((value as any).mode || 'missing_required'),
      slotKey,
      reason,
      impact: this.clampBoundedNumber((value as any).impact, 0, 1.5),
      confidence: this.clampRatio((value as any).confidence),
      ambiguityWeight: this.clampRatio((value as any).ambiguityWeight ?? (value as any).ambiguity_weight),
    };
  }

  private normalizeStringMap(value: unknown): Record<string, string> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([key, item]) => [String(key), String(item || '').trim()])
        .filter(([, item]) => Boolean(item)),
    );
  }

  private normalizeNumberMap(value: unknown): Record<string, number> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([key, item]) => [String(key), this.clampRatio(item)])
        .filter(([, item]) => Number.isFinite(item)),
    );
  }

  private buildConfidenceSummary(
    confidenceBySlot: unknown,
    ambiguityFlags: unknown,
    missingRequired: unknown,
  ): CreationSessionConfidenceSummary {
    const normalizedConfidence = this.normalizeNumberMap(confidenceBySlot);
    const entries = Object.entries(normalizedConfidence);
    const ranked = [...entries].sort((a, b) => b[1] - a[1]);
    return {
      overallConfidence: entries.length
        ? entries.reduce((sum, [, value]) => sum + value, 0) / entries.length
        : 0,
      strongestSlots: ranked.slice(0, 2).map(([slot]) => slot),
      weakestSlots: [...ranked].reverse().slice(0, 2).map(([slot]) => slot),
      ambiguityFlags: this.normalizeStringList(ambiguityFlags),
      missingCriticalSlots: this.normalizeStringList(missingRequired)
        .filter((slot) => ['core_mechanic', 'win_condition', 'input_method', 'game_type'].includes(slot)),
    };
  }

  private clampSlotFillPct(value: unknown, slotState: Record<string, unknown>): number {
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
      return String(value || '').trim().length > 0;
    }).length;
    return filled / REQUIRED_SLOT_KEYS.length;
  }

  private buildAnalyzeTurnPayload(params: {
    sessionId?: string | null;
    userId: string;
    conversation: CreationSessionConversationMessage[];
    currentSlots?: Record<string, unknown>;
    skippedSlots?: string[];
    entryMode?: string | null;
    title?: string | null;
    generationTier?: string | null;
    initialPrompt?: string | null;
    answeredSlot?: CreationSessionQuestion | null;
    latestUserAnswer?: string | null;
    advanceOnly?: boolean;
  }): AnalyzeTurnRequestPayload {
    const sessionId = this.asOptionalString(params.sessionId);
    const title = this.asOptionalString(params.title);
    const initialPrompt = this.asOptionalString(params.initialPrompt);
    const answeredSlotKey = this.asOptionalString(params.answeredSlot?.slotKey);
    const answeredSlotPrompt = this.asOptionalString(params.answeredSlot?.prompt);
    const latestUserAnswer = this.asOptionalString(params.latestUserAnswer);

    return {
      ...(sessionId ? { session_id: sessionId } : {}),
      user_id: params.userId,
      conversation: params.conversation,
      current_slots: params.currentSlots || {},
      skipped_slots: params.skippedSlots || [],
      entry_mode: this.asOptionalString(params.entryMode) || 'create',
      generation_tier: this.asOptionalString(params.generationTier) || 'standard',
      ...(title ? { title } : {}),
      ...(initialPrompt ? { initial_prompt: initialPrompt } : {}),
      ...(answeredSlotKey ? { answered_slot_key: answeredSlotKey } : {}),
      ...(answeredSlotPrompt ? { answered_slot_prompt: answeredSlotPrompt } : {}),
      ...(latestUserAnswer ? { latest_user_answer: latestUserAnswer } : {}),
      ...(params.advanceOnly ? { advance_only: true } : {}),
    };
  }

  private userMessage(content: string, kind: CreationSessionConversationMessage['kind']): CreationSessionConversationMessage {
    return {
      role: 'user',
      content: String(content || '').trim(),
      kind,
      createdAt: new Date().toISOString(),
    };
  }

  private assistantMessage(content: string, kind: CreationSessionConversationMessage['kind'] = 'question'): CreationSessionConversationMessage {
    return {
      role: 'assistant',
      content: String(content || '').trim(),
      kind,
      createdAt: new Date().toISOString(),
    };
  }

  private uniqueStrings(values: string[]): string[] {
    return Array.from(new Set(values.map((item) => String(item || '').trim()).filter(Boolean)));
  }

  private asOptionalString(value: unknown): string | undefined {
    const normalized = String(value || '').trim();
    return normalized || undefined;
  }

  private clampRatio(value: unknown): number {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return 0;
    }
    if (numeric <= 0) {
      return 0;
    }
    if (numeric >= 1) {
      return 1;
    }
    return numeric;
  }

  private clampBoundedNumber(value: unknown, min: number, max: number): number {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return min;
    }
    if (numeric <= min) {
      return min;
    }
    if (numeric >= max) {
      return max;
    }
    return numeric;
  }

  private normalizeOrientationValue(value: unknown): 'portrait' | 'landscape' | undefined {
    if (value === 'portrait') {
      return 'portrait';
    }
    if (value === 'landscape') {
      return 'landscape';
    }
    return undefined;
  }

  private normalizeGenerationTierValue(value: unknown): 'safe' | 'standard' | 'showcase' | undefined {
    if (value === 'safe') {
      return 'safe';
    }
    if (value === 'showcase') {
      return 'showcase';
    }
    if (value === 'standard') {
      return 'standard';
    }
    return undefined;
  }
}
