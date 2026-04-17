import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import axios from 'axios';
import { randomUUID } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';
import { GameService } from '../game/game.service';
import { ForkService } from '../fork/fork.service';
import { GenerationTaskStatus, Prisma } from '@prisma/client';
import { CreateCreationSessionDto, CreationAbandonDto, CreationGenerateDto, CreationMessageDto, CreationSkipDto, EntryMode } from './creation-session.dto';

type SessionSnapshot = any;

type OkSnapshot<T> = { ok: true; snapshot: T };
type OkPayload<T> = { ok: true; payload: T };
type Conflict = { code: number; message: string; data: any };

const REQUIRED_SLOT_KEYS = [
  'game_type',
  'core_mechanic',
  'theme',
  'input_method',
  'win_condition',
  'difficulty',
] as const;

const SLOT_PRIORITY = [...REQUIRED_SLOT_KEYS];

// Fork mode: ask "difference" questions even if required slots are already filled.
// Keep it small (max 2) to align doc `questionBudget`.
const FORK_QUESTION_SLOT_KEYS: string[] = ['core_mechanic', 'theme'];

function getForkQuestionSlotKey(index: number): string {
  return FORK_QUESTION_SLOT_KEYS[index] ?? FORK_QUESTION_SLOT_KEYS[FORK_QUESTION_SLOT_KEYS.length - 1] ?? 'theme';
}

function computeSlotFillPct(slotState: any, skippedSlots: string[]): number {
  const skippedSet = new Set(skippedSlots || []);
  const effectiveKeys = REQUIRED_SLOT_KEYS.filter((k) => !skippedSet.has(k));
  if (effectiveKeys.length === 0) return 1;
  const filled = effectiveKeys.filter((k) => slotState?.[k] != null).length;
  return filled / effectiveKeys.length;
}

function computeMissingRequired(slotState: any, skippedSlots: string[]): string[] {
  const skippedSet = new Set(skippedSlots || []);
  return REQUIRED_SLOT_KEYS.filter((k) => slotState?.[k] == null && !skippedSet.has(k));
}

function conflict(code: number, message: string, data: any): Conflict {
  return { code, message, data };
}

function successSnapshot<T>(snapshot: T): OkSnapshot<T> {
  return { ok: true, snapshot };
}

function successPayload<T>(payload: T): OkPayload<T> {
  return { ok: true, payload };
}

function safeRole(role: string): 'user' | 'assistant' {
  return role === 'assistant' ? 'assistant' : 'user';
}

function buildConversationHistory(conversation: any[]): Array<{ role: 'user' | 'assistant'; content: string }> {
  return (conversation || []).map((m) => ({ role: safeRole(m.role), content: String(m.content ?? '') }));
}

function buildDescriptionFromSlots(sourceDescription: string, slotState: any, skippedSlots: string[]): string {
  const skipped = skippedSlots?.length ? `Skipped slots: ${skippedSlots.join(', ')}\n` : '';
  const filledLines = REQUIRED_SLOT_KEYS.map((k) => {
    const v = slotState?.[k];
    return `${k}: ${v ?? '(default)'}`;
  }).join('\n');

  return [
    `Source idea: ${sourceDescription}`,
    skipped ? skipped.trimEnd() : '',
    filledLines,
    'Please generate a playable game that matches the above slots.',
  ].filter(Boolean).join('\n');
}

function buildDescriptionFromGameSpec(sourceDescription: string, spec: any, slots: any, skippedSlots: string[]): string {
  const gameType = spec?.game_type ?? slots?.game_type ?? 'dodge';
  const theme = spec?.visual_style?.theme ?? slots?.theme ?? '';
  const inputMethod = spec?.core_mechanics?.[0]?.input ?? slots?.input_method ?? '';
  const difficulty = spec?.difficulty_curve ?? slots?.difficulty ?? 'progressive';
  const winCondition = spec?.rules?.win_condition ?? slots?.win_condition ?? '';
  const skipped = skippedSlots?.length ? `Skipped slots: ${skippedSlots.join(', ')}\n` : '';

  return [
    `Source idea: ${sourceDescription}`,
    skipped ? skipped.trimEnd() : '',
    `Game type: ${gameType}`,
    theme ? `Theme: ${theme}` : '',
    inputMethod ? `Input method: ${inputMethod}` : '',
    winCondition ? `Win condition: ${winCondition}` : '',
    `Difficulty: ${difficulty}`,
    'Please generate a playable game matching the above spec.',
  ].filter(Boolean).join('\n');
}

function buildFeedbackFromConversation(conversation: any[], initialPrompt: string, maxUserAnswers: number): string {
  const userMessages = (conversation || [])
    .filter((m) => m.role === 'user')
    .map((m) => String(m.content ?? ''))
    .filter((c) => c && c !== initialPrompt);
  const last = userMessages.slice(-maxUserAnswers);
  return last.length ? `Fork changes: ${last.join('\n')}` : 'Fork and improve this game.';
}

function buildQuestion(slotKey: string, locale?: string): any {
  // Minimal templates to unblock the flow. UI is expected to rely on slotKey + answerMode + options.
  if (slotKey === 'game_type') {
    return {
      questionId: randomUUID(),
      slotKey,
      text: locale === 'zh-CN' ? '你更希望它偏向哪类玩法？' : 'Which play style do you prefer?',
      answerMode: 'single_choice_or_text',
      options: [
        { label: '解谜闯关', value: 'puzzle', description: null },
        { label: '跑酷躲避', value: 'runner', description: null },
        { label: '射击挑战', value: 'shooter', description: null },
        { label: '实验沙盒', value: 'sandbox', description: null },
      ],
      required: true,
      allowSkip: true,
      placeholder: locale === 'zh-CN' ? '也可以直接输入你自己的描述' : 'You can also type your own description',
    };
  }

  if (slotKey === 'input_method') {
    return {
      questionId: randomUUID(),
      slotKey,
      text: locale === 'zh-CN' ? '玩家主要通过什么方式操作？' : 'How should players control the game?',
      answerMode: 'single_choice_or_text',
      options: [
        { label: '点击', value: 'tap', description: null },
        { label: '滑动', value: 'swipe', description: null },
        { label: '拖拽', value: 'drag', description: null },
        { label: '虚拟摇杆', value: 'joystick', description: null },
      ],
      required: true,
      allowSkip: true,
      placeholder: locale === 'zh-CN' ? '也可以直接输入你自己的操作方式' : 'Describe controls in your own words',
    };
  }

  if (slotKey === 'difficulty') {
    return {
      questionId: randomUUID(),
      slotKey,
      text: locale === 'zh-CN' ? '希望整体难度偏简单、中等还是挑战型？' : 'Overall difficulty preference?',
      answerMode: 'single_choice_or_text',
      options: [
        { label: '简单（休闲）', value: 'easy', description: null },
        { label: '中等', value: 'medium', description: null },
        { label: '困难（挑战）', value: 'hard', description: null },
        { label: '渐进提升', value: 'progressive', description: null },
      ],
      required: true,
      allowSkip: true,
      placeholder: null,
    };
  }

  if (slotKey === 'theme') {
    return {
      questionId: randomUUID(),
      slotKey,
      text: locale === 'zh-CN' ? '你希望它的世界观/主题是什么？' : 'What theme or world setting do you want?',
      answerMode: 'single_choice_or_text',
      options: [
        { label: '太空', value: 'space', description: null },
        { label: '海底', value: 'ocean', description: null },
        { label: '森林', value: 'forest', description: null },
        { label: '西部', value: 'western', description: null },
        { label: '未来', value: 'future', description: null },
      ],
      required: true,
      allowSkip: true,
      placeholder: locale === 'zh-CN' ? '也可以输入更具体的主题设定' : 'Type your own theme',
    };
  }

  if (slotKey === 'win_condition') {
    return {
      questionId: randomUUID(),
      slotKey,
      text: locale === 'zh-CN' ? '你希望怎样才算赢？' : 'How do you win?',
      answerMode: 'single_choice_or_text',
      options: [
        { label: '存活尽量长', value: 'survive_long', description: null },
        { label: '到达终点', value: 'reach_end', description: null },
        { label: '消灭所有敌人', value: 'defeat_all', description: null },
        { label: '完成所有关卡', value: 'finish_levels', description: null },
      ],
      required: true,
      allowSkip: true,
      placeholder: locale === 'zh-CN' ? '也可以输入自定义胜利条件' : 'Describe your win condition',
    };
  }

  // core_mechanic
  return {
    questionId: randomUUID(),
    slotKey,
    text: locale === 'zh-CN' ? '游戏的核心玩法循环是什么？' : 'What is the core gameplay loop?',
    answerMode: 'free_text',
    options: [],
    required: true,
    allowSkip: true,
    placeholder: locale === 'zh-CN' ? '例如：躲避障碍并在关键时刻反击' : 'e.g. dodge obstacles and counter at key moments',
  };
}

@Injectable()
export class CreationSessionService {
  private readonly logger = new Logger(CreationSessionService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly gameService: GameService,
    private readonly forkService: ForkService,
  ) {}

  private async ensureSessionActive(session: any, userId: string): Promise<void> {
    if (!session || session.userId !== userId) {
      throw new NotFoundException('session not found');
    }
  }

  private async reconcileSession(session: any): Promise<any> {
    if (!session) return session;
    const now = new Date();

    if (session.expiresAt && now > session.expiresAt && session.status !== 'completed') {
      session.status = 'expired';
      await this.prisma.gameCreationSession.update({
        where: { id: session.id },
        data: { status: 'expired', revision: session.revision + 1 },
      });
      return session;
    }

    if (session.status === 'generating' && session.generationTaskId) {
      const task = await this.prisma.generationTask.findUnique({ where: { id: session.generationTaskId } });
      if (task) {
        let nextStatus: string | null = null;
        if (task.status === GenerationTaskStatus.succeeded) nextStatus = 'completed';
        if (task.status === GenerationTaskStatus.failed || task.status === GenerationTaskStatus.timed_out) nextStatus = 'failed';
        if (task.status === GenerationTaskStatus.canceled) nextStatus = 'abandoned';
        if (nextStatus && nextStatus !== session.status) {
          await this.prisma.gameCreationSession.update({
            where: { id: session.id },
            data: { status: nextStatus, revision: session.revision + 1 },
          });
          session.status = nextStatus;
        }
      }
    }

    return session;
  }

  private async buildSnapshot(session: any): Promise<SessionSnapshot> {
    await this.reconcileSession(session);
    const refreshed = await this.prisma.gameCreationSession.findUnique({ where: { id: session.id } });
    if (!refreshed) throw new NotFoundException('session not found');

    const slotStateRaw = refreshed.slotState as any;
    const slotState = slotStateRaw === Prisma.JsonNull || slotStateRaw == null ? {} : slotStateRaw;

    const skippedSlotsRaw = refreshed.skippedSlots as any;
    const skippedSlots = skippedSlotsRaw === Prisma.JsonNull || skippedSlotsRaw == null ? [] : (skippedSlotsRaw as string[]);

    const missingEffective = computeMissingRequired(slotState, skippedSlots);
    const slotFillPct = computeSlotFillPct(slotState, skippedSlots);
    const readyToGenerate =
      refreshed.entryMode === 'fork'
        ? refreshed.questionsAsked >= (refreshed.questionBudget || 0)
        : missingEffective.length === 0;

    const currentQuestionRaw = refreshed.currentQuestion as any;
    const currentQuestion = currentQuestionRaw === Prisma.JsonNull ? null : currentQuestionRaw || null;
    const terminalStatuses = ['completed', 'failed', 'abandoned', 'expired'];
    const isTerminal = terminalStatuses.includes(refreshed.status);

    const allowRetryGenerate = refreshed.status === 'failed';
    const generateAction = {
      allowed: (!isTerminal && readyToGenerate) || allowRetryGenerate,
      forceAllowed: true,
      reason: (!isTerminal && readyToGenerate) || allowRetryGenerate
        ? null
        : isTerminal
          ? 'session_terminal'
          : (readyToGenerate ? null : 'missing_required_slots'),
    };

    // Keep `missingRequired` field aligned with missingEffective for UI.
    if (Array.isArray(refreshed.missingRequired) && JSON.stringify(refreshed.missingRequired) !== JSON.stringify(missingEffective)) {
      await this.prisma.gameCreationSession.update({
        where: { id: refreshed.id },
        data: { missingRequired: missingEffective as any },
      });
      refreshed.missingRequired = missingEffective as any;
    }

    const snapshot: any = {
      session: {
        id: refreshed.id,
        status: refreshed.status,
        titleDraft: refreshed.titleDraft,
        initialPrompt: refreshed.initialPrompt,
        regionHint: refreshed.regionHint,
        locale: refreshed.locale,
        revision: refreshed.revision,
        aiState: refreshed.aiState,
        createdAt: refreshed.createdAt?.toISOString?.() ?? refreshed.createdAt,
        updatedAt: refreshed.updatedAt?.toISOString?.() ?? refreshed.updatedAt,
        expiresAt: refreshed.expiresAt?.toISOString?.() ?? refreshed.expiresAt,
        generatedGameId: refreshed.generatedGameId,
        generationTaskId: refreshed.generationTaskId,
      },
      conversation: refreshed.conversation ?? [],
      slotState,
      missingRequired: missingEffective,
      slotFillPct,
      readyToGenerate: isTerminal ? false : readyToGenerate,
      currentQuestion: isTerminal ? null : currentQuestion,
      generateAction,
    };

    if (!isTerminal) return snapshot;

    // Terminal snapshots: enrich with result/error per docs.
    if (refreshed.status === 'completed' && refreshed.generatedGameId) {
      const generationTask = refreshed.generationTaskId
        ? await this.prisma.generationTask.findUnique({ where: { id: refreshed.generationTaskId } })
        : null;
      const game = await this.prisma.game.findUnique({
        where: { id: refreshed.generatedGameId },
        select: { id: true, canPlay: true, requireSubscription: true },
      });

      snapshot.result = {
        gameId: refreshed.generatedGameId,
        taskId: refreshed.generationTaskId,
        status: 'ready',
        previewUrl: generationTask?.previewUrl ?? null,
        canPlay: game?.canPlay ?? false,
        requireSubscription: game?.requireSubscription ?? false,
      };
    }

    if (refreshed.status === 'failed') {
      const generationTask = refreshed.generationTaskId
        ? await this.prisma.generationTask.findUnique({ where: { id: refreshed.generationTaskId } })
        : null;
      snapshot.error = {
        failedStage: generationTask?.failedStage ?? generationTask?.progressStage ?? 'generation',
        message: generationTask?.errorMessage ?? generationTask?.progressMessage ?? 'Generation failed',
      };
      snapshot.retryAction = {
        canRetry: true,
        retryMode: 'reuse_session',
      };
    }

    if (refreshed.status === 'abandoned' || refreshed.status === 'expired') {
      snapshot.retryAction = {
        canRetry: false,
        retryMode: 'reuse_session',
      };
    }

    return snapshot;
  }

  async createSession(userId: string, dto: CreateCreationSessionDto): Promise<any> {
    // Find active session first (unique collecting/ready/generating per user).
    const entryMode: EntryMode = dto.entryMode ?? 'fresh';

    const existingActive = await this.prisma.gameCreationSession.findFirst({
      where: {
        userId,
        status: { in: ['collecting', 'ready', 'generating'] },
      },
      orderBy: { updatedAt: 'desc' },
    });

    if (existingActive) {
      // Best-effort "same source" check: prompt + entryMode + sourceGameId.
      const sameSource =
        existingActive.initialPrompt === dto.prompt &&
        existingActive.entryMode === entryMode &&
        (entryMode === 'fresh' || existingActive.sourceGameId === dto.sourceGameId);

      if (sameSource) {
        if (dto.clientRequestId && existingActive.lastClientRequestId === dto.clientRequestId) {
          return successSnapshot(await this.buildSnapshot(existingActive));
        }
        return successSnapshot(await this.buildSnapshot(existingActive));
      }

      return conflict(409, 'active_session_exists', {
        reusedExisting: false,
        activeSession: await this.buildSnapshot(existingActive),
      });
    }

    // Create initial session record.
    const now = new Date();
    const expiresAt = new Date(now.getTime() + 24 * 60 * 60 * 1000);
    const qBudget = entryMode === 'fork' ? 2 : 6;

    // Fork prefill uses source spec as best-effort initial slotState.
    let initialSlotState: any = {};
    let skippedSlots: string[] = [];
    let titleDraft = dto.title?.trim() || null;
    let locale = dto.locale ?? null;
    let regionHint = dto.regionHint ?? null;

    if (entryMode === 'fork') {
      if (!dto.sourceGameId) {
        return conflict(400, 'sourceGameId_required_for_fork', { entryMode });
      }
      const latestBundle = await this.prisma.gameBundle.findFirst({
        where: { gameId: dto.sourceGameId },
        orderBy: { version: 'desc' },
        select: { spec: true, generationMeta: true, metadata: true },
      });
      const latestBundleAny = latestBundle as any;
      const spec = latestBundleAny?.spec ?? latestBundleAny?.metadata?.gameSpec ?? {};
      const theme = spec?.visual_style?.theme ?? null;
      const inputMethod = spec?.core_mechanics?.[0]?.input ?? null;
      const difficulty = spec?.difficulty_curve ?? null;
      const winCondition = spec?.rules?.win_condition ?? null;
      const gameType = spec?.game_type ?? null;
      initialSlotState = {
        game_type: gameType,
        core_mechanic: spec?.core_mechanics?.[0]
          ? `以${inputMethod || '交互方式'}实现${winCondition || '胜利条件'}的核心玩法`
          : '自定义核心玩法',
        theme,
        input_method: inputMethod,
        win_condition: winCondition,
        difficulty,
        visual_style: spec?.visual_style?.art_style ?? null,
        audio_style: null,
        special_rules: null,
        reference_game: null,
      };
      skippedSlots = [];
      titleDraft = null; // fork title is handled by forkGame
    }

    const session = await this.prisma.gameCreationSession.create({
      data: {
        userId,
        status: 'collecting',
        entryMode,
        sourceGameId: entryMode === 'fork' ? dto.sourceGameId : null,
        questionBudget: qBudget,
        questionsAsked: 0,
        titleDraft,
        initialPrompt: dto.prompt,
        regionHint: regionHint || null,
        locale: locale || null,
        aiState: 'greeting',
        revision: 1,
        slotState: initialSlotState || {},
        missingRequired: [],
        skippedSlots,
        currentQuestion: Prisma.JsonNull,
        conversation: [],
        expiresAt,
        lastClientRequestId: dto.clientRequestId || null,
      },
    });

    const conversation = [
      {
        id: randomUUID(),
        role: 'user',
        content: dto.prompt,
        createdAt: now.toISOString(),
      },
    ];

    let updatedSlots: any = session.slotState ?? {};
    let aiState = 'clarifying';
    let reply = '';

    // Fresh: run analyze-turn for initial prompt to fill slots.
    if (entryMode === 'fresh') {
      const aiEngineBaseUrl = await this.gameService.getAiEngineBaseUrl(regionHint || undefined);
      const request = {
        user_id: userId,
        session_id: session.id,
        history: buildConversationHistory(conversation),
        current_slots: updatedSlots,
        locale: locale || undefined,
      };
      try {
        const response = await axios.post(`${aiEngineBaseUrl}/api/v1/ai/dialogue/analyze-turn`, request, { timeout: 60000 });
        reply = response.data?.reply ?? '';
        updatedSlots = response.data?.slots ?? updatedSlots;
        aiState = response.data?.state ?? 'clarifying';
      } catch (e: any) {
        this.logger.warn(`analyze-turn failed (fresh create): ${e?.message || e}`);
      }
    } else {
      // Fork prefill already has slots; still produce a short assistant reply for UI consistency.
      aiState = 'clarifying';
      reply = '好的，我们先根据你的创作目标做一轮调整。';
    }

    // Append assistant reply if exists.
    if (reply) {
      conversation.push({ id: randomUUID(), role: 'assistant', content: reply, createdAt: new Date().toISOString() });
    }

    const skippedEffective = skippedSlots;
    const missingEffective = computeMissingRequired(updatedSlots, skippedEffective);
    let currentQuestion: any = null;
    if (entryMode === 'fork') {
      currentQuestion = qBudget > 0 ? buildQuestion(getForkQuestionSlotKey(0), locale || undefined) : null;
    } else {
      currentQuestion = missingEffective.length ? buildQuestion(missingEffective[0], locale || undefined) : null;
    }
    const status = currentQuestion ? 'collecting' : 'ready';

    await this.prisma.gameCreationSession.update({
      where: { id: session.id },
      data: {
        conversation: conversation as any,
        slotState: updatedSlots as any,
        missingRequired: missingEffective as any,
        currentQuestion: currentQuestion ? (currentQuestion as any) : Prisma.JsonNull,
        aiState,
        status,
      },
    });

    const updatedSession = await this.prisma.gameCreationSession.findUnique({ where: { id: session.id } });
    return successSnapshot(await this.buildSnapshot(updatedSession));
  }

  async getActiveSession(userId: string): Promise<{ session: any | null }> {
    const session = await this.prisma.gameCreationSession.findFirst({
      where: { userId, status: { in: ['collecting', 'ready', 'generating'] } },
      orderBy: { updatedAt: 'desc' },
    });
    if (!session) return { session: null };
    return { session: await this.buildSnapshot(session) };
  }

  async getSession(userId: string, sessionId: string): Promise<SessionSnapshot> {
    const session = await this.prisma.gameCreationSession.findUnique({ where: { id: sessionId } });
    if (!session) throw new NotFoundException('session not found');
    await this.ensureSessionActive(session, userId);
    return this.buildSnapshot(session);
  }

  async submitMessage(userId: string, sessionId: string, dto: CreationMessageDto): Promise<any> {
    const session = await this.prisma.gameCreationSession.findUnique({ where: { id: sessionId } });
    if (!session) return conflict(404, 'session_not_found', {});
    await this.ensureSessionActive(session, userId);

    if (session.status === 'generating' || session.status === 'completed' || session.status === 'abandoned' || session.status === 'expired') {
      return conflict(409, 'session_not_collecting', { status: session.status });
    }

    if (dto.clientMessageId && session.lastClientMessageId && dto.clientMessageId === session.lastClientMessageId) {
      return successSnapshot(await this.buildSnapshot(session));
    }

    if (dto.expectedRevision !== session.revision) {
      return conflict(409, 'revision_conflict', {
        expectedRevision: dto.expectedRevision,
        currentRevision: session.revision,
      });
    }

    const currentQuestion = session.currentQuestion as any;
    if (dto.questionId && currentQuestion?.questionId && dto.questionId !== currentQuestion.questionId) {
      return conflict(422, 'questionId_mismatch', { expectedQuestionId: currentQuestion.questionId });
    }

    const now = new Date();
    const conversation = (session.conversation as any[]) ?? [];
    conversation.push({
      id: dto.clientMessageId || randomUUID(),
      role: 'user',
      content: dto.content,
      createdAt: now.toISOString(),
    });

    const history = buildConversationHistory(conversation);

    let reply = '';
    let updatedSlots: any = session.slotState ?? {};
    let aiState = session.aiState || 'clarifying';
    const aiEngineBaseUrl = await this.gameService.getAiEngineBaseUrl(session.regionHint || undefined);
    try {
      const request = {
        user_id: userId,
        session_id: session.id,
        history,
        current_slots: updatedSlots,
        locale: session.locale || undefined,
      };
      const response = await axios.post(`${aiEngineBaseUrl}/api/v1/ai/dialogue/analyze-turn`, request, { timeout: 60000 });
      reply = response.data?.reply ?? '';
      updatedSlots = response.data?.slots ?? updatedSlots;
      aiState = response.data?.state ?? aiState;
    } catch (e: any) {
      this.logger.warn(`analyze-turn failed: ${e?.message || e}`);
      reply = '好的，我已经记录了你的选择。';
    }

    if (reply) {
      conversation.push({ id: randomUUID(), role: 'assistant', content: reply, createdAt: new Date().toISOString() });
    }

    const skippedSlots = (session.skippedSlots as string[]) ?? [];
    const missingEffective = computeMissingRequired(updatedSlots, skippedSlots);

    const questionsAsked = (session.questionsAsked || 0) + 1;
    const readyToGenerate =
      session.entryMode === 'fork'
        ? questionsAsked >= session.questionBudget
        : missingEffective.length === 0;

    const currentQuestionObj = readyToGenerate
      ? null
      : session.entryMode === 'fork'
        ? buildQuestion(getForkQuestionSlotKey(questionsAsked), session.locale || undefined)
        : buildQuestion(missingEffective[0], session.locale || undefined);
    const nextStatus = currentQuestionObj ? 'collecting' : 'ready';
    const currentQuestionToStore = currentQuestionObj ? (currentQuestionObj as any) : Prisma.JsonNull;

    await this.prisma.gameCreationSession.update({
      where: { id: sessionId },
      data: {
        conversation: conversation as any,
        slotState: updatedSlots as any,
        missingRequired: missingEffective as any,
        currentQuestion: currentQuestionToStore,
        aiState,
        revision: session.revision + 1,
        questionsAsked,
        lastClientMessageId: dto.clientMessageId || null,
        status: nextStatus,
      },
    });

    const updatedSession = await this.prisma.gameCreationSession.findUnique({ where: { id: sessionId } });
    return successSnapshot(await this.buildSnapshot(updatedSession));
  }

  async skipQuestion(userId: string, sessionId: string, dto: CreationSkipDto): Promise<any> {
    const session = await this.prisma.gameCreationSession.findUnique({ where: { id: sessionId } });
    if (!session) return conflict(404, 'session_not_found', {});
    await this.ensureSessionActive(session, userId);

    if (session.status === 'generating' || session.status === 'completed' || session.status === 'abandoned' || session.status === 'expired') {
      return conflict(409, 'session_not_collecting', { status: session.status });
    }

    if (dto.expectedRevision !== session.revision) {
      return conflict(409, 'revision_conflict', {
        expectedRevision: dto.expectedRevision,
        currentRevision: session.revision,
      });
    }

    const cq = session.currentQuestion as any;
    if (!cq || !cq.slotKey) return conflict(422, 'no_current_question', {});

    if (dto.questionId && cq.questionId && dto.questionId !== cq.questionId) {
      return conflict(422, 'questionId_mismatch', { expectedQuestionId: cq.questionId });
    }

    const now = new Date();
    const skippedSlots = new Set<string>(session.skippedSlots as string[] | undefined);
    skippedSlots.add(cq.slotKey);
    const skippedArr = Array.from(skippedSlots);

    const questionsAsked = (session.questionsAsked || 0) + 1;

    const slotState = session.slotState ?? {};
    const missingEffective = computeMissingRequired(slotState, skippedArr);

    const readyToGenerate =
      session.entryMode === 'fork'
        ? questionsAsked >= session.questionBudget
        : missingEffective.length === 0;

    const currentQuestion = readyToGenerate
      ? null
      : session.entryMode === 'fork'
        ? buildQuestion(getForkQuestionSlotKey(questionsAsked), session.locale || undefined)
        : buildQuestion(missingEffective[0], session.locale || undefined);
    const nextStatus = currentQuestion ? 'collecting' : 'ready';
    const currentQuestionToStore = currentQuestion ? (currentQuestion as any) : Prisma.JsonNull;

    // Skip doesn't add a message; it only updates skippedSlots.
    await this.prisma.gameCreationSession.update({
      where: { id: sessionId },
      data: {
        skippedSlots: skippedArr as any,
        missingRequired: missingEffective as any,
        currentQuestion: currentQuestionToStore,
        revision: session.revision + 1,
        questionsAsked,
        status: nextStatus,
        aiState: nextStatus === 'ready' ? 'confirmed' : session.aiState ?? 'clarifying',
      },
    });

    const updatedSession = await this.prisma.gameCreationSession.findUnique({ where: { id: sessionId } });
    return successSnapshot(await this.buildSnapshot(updatedSession));
  }

  async generateGame(userId: string, sessionId: string, dto: CreationGenerateDto): Promise<any> {
    const session = await this.prisma.gameCreationSession.findUnique({ where: { id: sessionId } });
    if (!session) return conflict(404, 'session_not_found', {});
    await this.ensureSessionActive(session, userId);

    if (session.status === 'generating' && session.generationTaskId) {
      return successPayload({
        gameId: session.generatedGameId,
        status: 'generating',
        generationTask: await this.buildGenerationTaskSummary(session.generationTaskId),
        session: {
          id: session.id,
          status: 'generating',
          revision: session.revision,
          generatedGameId: session.generatedGameId,
          generationTaskId: session.generationTaskId,
        },
      });
    }

    if (session.status === 'completed') {
      // Already done; return snapshot of the existing generation.
      return successPayload({
        gameId: session.generatedGameId,
        status: 'completed',
        generationTask: session.generationTaskId
          ? await this.buildGenerationTaskSummary(session.generationTaskId)
          : null,
        session: {
          id: session.id,
          status: 'completed',
          revision: session.revision,
          generatedGameId: session.generatedGameId,
          generationTaskId: session.generationTaskId,
        },
      });
    }

    const force = Boolean(dto.force);
    if (dto.idempotencyKey && session.lastGenerateKey && dto.idempotencyKey === session.lastGenerateKey && session.generationTaskId) {
      return successPayload({
        gameId: session.generatedGameId,
        status: 'generating',
        generationTask: await this.buildGenerationTaskSummary(session.generationTaskId),
        session: {
          id: session.id,
          status: 'generating',
          revision: session.revision,
          generatedGameId: session.generatedGameId,
          generationTaskId: session.generationTaskId,
        },
      });
    }

    if (dto.expectedRevision !== session.revision) {
      return conflict(409, 'revision_conflict', {
        expectedRevision: dto.expectedRevision,
        currentRevision: session.revision,
      });
    }

    const skippedSlots = (session.skippedSlots as string[]) ?? [];
    const missingEffective = computeMissingRequired(session.slotState as any ?? {}, skippedSlots);

    const readyToGenerate =
      session.entryMode === 'fork'
        ? (session.questionsAsked || 0) >= session.questionBudget
        : missingEffective.length === 0;

    if (!readyToGenerate && !force && session.status !== 'failed') {
      const currentQuestion = session.currentQuestion;
      return conflict(409, 'missing_required_slots', {
        missingRequired: missingEffective,
        currentQuestion,
        readyToGenerate: false,
        forceAllowed: true,
      });
    }

    // Build spec via ai-engine (stateless) and convert it into a compatible description.
    let finalDescription = buildDescriptionFromSlots(session.initialPrompt, session.slotState as any ?? {}, skippedSlots);
    try {
      const aiEngineBaseUrl = await this.gameService.getAiEngineBaseUrl(session.regionHint || undefined);
      const specRes = await axios.post(
        `${aiEngineBaseUrl}/api/v1/ai/dialogue/spec-from-slots`,
        {
          sourceDescription: session.initialPrompt,
          slots: session.slotState as any ?? {},
        },
        { timeout: 60000 },
      );
      const spec = specRes.data?.spec;
      if (spec) {
        finalDescription = buildDescriptionFromGameSpec(session.initialPrompt, spec, session.slotState as any ?? {}, skippedSlots);
      }
    } catch (e: any) {
      this.logger.warn(`spec-from-slots failed; fallback to buildDescriptionFromSlots: ${e?.message || e}`);
    }

    let generationTask: any = null;
    let gameId: string | null = null;

    // Mark session generating.
    let nextRevision = session.revision + 1;

    if (session.entryMode === 'fork') {
      if (!session.sourceGameId) {
        return conflict(400, 'source_game_id_missing', {});
      }

      const forked = await this.forkService.forkGame(session.sourceGameId, userId);
      gameId = forked.gameId;

      const conversation = (session.conversation as any[]) ?? [];
      const feedback = buildFeedbackFromConversation(conversation, session.initialPrompt, 2);
      const result = await this.gameService.iterate(forked.gameId, userId, {
        feedback,
        regionHint: session.regionHint || undefined,
        timeoutS: 1200,
      });

      generationTask = result.generationTask;
    } else {
      const title = session.titleDraft?.trim() || `Game ${randomUUID().slice(0, 8)}`;
      const result = await this.gameService.create(userId, {
        title,
        description: finalDescription,
        timeoutS: 1200,
        regionHint: session.regionHint || undefined,
      });

      gameId = result.gameId;
      generationTask = result.generationTask;
    }

    await this.prisma.gameCreationSession.update({
      where: { id: sessionId },
      data: {
        status: 'generating',
        generatedGameId: gameId,
        generationTaskId: generationTask?.taskId ?? null,
        currentQuestion: Prisma.JsonNull,
        revision: nextRevision,
        lastGenerateKey: dto.idempotencyKey || session.lastGenerateKey,
      },
    });

    return successPayload({
      gameId,
      status: 'generating',
      generationTask: generationTask,
      session: {
        id: session.id,
        status: 'generating',
        revision: nextRevision,
        generatedGameId: gameId,
        generationTaskId: generationTask?.taskId ?? null,
      },
    });
  }

  async abandonSession(userId: string, sessionId: string, dto: CreationAbandonDto): Promise<any> {
    const session = await this.prisma.gameCreationSession.findUnique({ where: { id: sessionId } });
    if (!session) return conflict(404, 'session_not_found', {});
    await this.ensureSessionActive(session, userId);

    if (dto.expectedRevision !== session.revision) {
      return conflict(409, 'revision_conflict', {
        expectedRevision: dto.expectedRevision,
        currentRevision: session.revision,
      });
    }

    if (session.status === 'generating') {
      return conflict(409, 'session_generating_cannot_abandon', {});
    }

    await this.prisma.gameCreationSession.update({
      where: { id: sessionId },
      data: { status: 'abandoned', revision: session.revision + 1, currentQuestion: Prisma.JsonNull },
    });

    const updated = await this.prisma.gameCreationSession.findUnique({ where: { id: sessionId } });
    return successPayload({
      session: {
        id: updated?.id,
        status: updated?.status,
        revision: updated?.revision,
      },
    });
  }

  private async buildGenerationTaskSummary(taskId: string): Promise<any> {
    const task = await this.prisma.generationTask.findUnique({ where: { id: taskId } });
    if (!task) return null;
    return {
      taskId: task.id,
      taskType: task.taskType,
      region: task.region,
      status: task.status,
      timeoutS: task.timeoutS,
      wsChannel: task.wsChannel,
      pollUrl: `/api/v1/games/tasks/${task.id}`,
      eventsUrl: `/api/v1/games/tasks/${task.id}/events`,
      cancelUrl: `/api/v1/games/tasks/${task.id}/cancel`,
      gameId: task.gameId,
      version: task.version ?? null,
      progressStage: (task as any).progressStage,
      progressPct: (task as any).progressPct,
      progressMessage: (task as any).progressMessage,
      failedStage: (task as any).failedStage,
      errorMessage: (task as any).errorMessage,
      retryCount: (task as any).retryCount ?? null,
      fallback: (task as any).fallback ?? null,
      cancelRequested: (task as any).cancelRequested ?? null,
      previewUrl: (task as any).previewUrl ?? null,
      gatewayConfigVersion: (task as any).gatewayConfigVersion ?? null,
      routeSnapshot: (task as any).routeSnapshot ?? null,
      resultSummary: (task as any).resultSummary ?? null,
      startedAt: (task as any).startedAt,
      completedAt: (task as any).completedAt,
      createdAt: (task as any).createdAt,
      updatedAt: (task as any).updatedAt,
    };
  }
}

