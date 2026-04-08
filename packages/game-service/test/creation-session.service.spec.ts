import axios from 'axios';
import { Readable } from 'stream';
import { CreationSessionService } from '../src/game/creation-session.service';

jest.mock('axios');

function buildSseEvent(event: string, payload: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
}

function buildAnalyzeTurnStream(payload: Record<string, any>) {
  const reply = String(payload.reply || '');
  const kind = payload.current_question ? 'question' : 'summary';
  const midpoint = Math.max(1, Math.floor(reply.length / 2));
  const firstDelta = reply.slice(0, midpoint);
  const secondDelta = reply.slice(midpoint);

  const events = [
    buildSseEvent('assistant.reply.delta', {
      delta: firstDelta,
      accumulated: firstDelta,
      kind,
      chunkIndex: 0,
      done: false,
    }),
    ...(secondDelta
      ? [buildSseEvent('assistant.reply.delta', {
          delta: secondDelta,
          accumulated: `${firstDelta}${secondDelta}`,
          kind,
          chunkIndex: 1,
          done: false,
        })]
      : []),
    buildSseEvent('assistant.reply.done', {
      message: reply,
      kind,
    }),
    buildSseEvent('analysis.result', payload),
  ];

  return Readable.from(events);
}

describe('CreationSessionService', () => {
  let service: CreationSessionService;
  let prisma: any;
  let repo: any;
  let gameService: any;
  let wsGateway: any;
  let realtimeService: any;

  beforeEach(() => {
    repo = {
      create: jest.fn(),
      findFirst: jest.fn(),
      findUnique: jest.fn(),
      update: jest.fn(),
      updateMany: jest.fn(),
    };
    prisma = {
      gameCreationSession: repo,
    };
    gameService = {
      getAiEngineBaseUrl: jest.fn().mockResolvedValue('https://ai-engine.example'),
      getExpandPromptRequestTimeoutMs: jest.fn().mockResolvedValue(5000),
      getCreationSessionInitTimeoutMs: jest.fn().mockResolvedValue(45000),
      create: jest.fn(),
    };
    wsGateway = {
      emitSessionUpdate: jest.fn(),
      emitSessionError: jest.fn(),
      emitToUser: jest.fn(),
    };
    realtimeService = {
      publishSnapshot: jest.fn(),
      publishPhase: jest.fn(),
      publishReply: jest.fn(),
      publishReplyDelta: jest.fn(),
      publishReplyDone: jest.fn(),
      publishError: jest.fn(),
    };
    service = new CreationSessionService(
      prisma as any,
      gameService as any,
      wsGateway as any,
      realtimeService as any,
    );
    (axios.post as jest.Mock).mockReset();
  });

  it('creates a session optimistically as initializing, then finalizes via async analysis', async () => {
    const originalSetTimeout = global.setTimeout;
    const setTimeoutSpy = jest.spyOn(global, 'setTimeout').mockImplementation(((handler: any, timeout?: any, ...args: any[]) => {
      if (timeout === 45000) {
        return 0 as any;
      }
      return originalSetTimeout(handler, timeout as any, ...args);
    }) as typeof setTimeout);
    // Phase 1: optimistic creation returns 'initializing' immediately
    repo.updateMany.mockResolvedValue({ count: 1 });
    repo.create.mockImplementation(async ({ data }: any) => ({
      id: 'session-1',
      userId: 'user-1',
      status: data.status,
      entryMode: data.entryMode,
      initialPrompt: data.initialPrompt,
      titleDraft: data.titleDraft,
      revision: data.revision,
      slotState: data.slotState,
      missingRequired: data.missingRequired,
      skippedSlots: data.skippedSlots,
      currentQuestion: data.currentQuestion,
      conversation: data.conversation,
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: data.sourceGameId,
      questionBudget: data.questionBudget,
      metadata: data.metadata,
      createdAt: new Date('2026-03-30T10:00:00.000Z'),
      updatedAt: new Date('2026-03-30T10:00:00.000Z'),
    }));
    // Phase 2: async _finalizeSessionInit will call analyzeTurn
    (axios.post as jest.Mock).mockResolvedValueOnce({
      data: buildAnalyzeTurnStream({
        reply: '我先补一个最关键的信息：玩家怎么才能赢？',
        slots: {
          game_type: 'funny',
          core_mechanic: 'tap to hide',
          theme: 'office',
          input_method: 'tap',
        },
        slots_updated: ['game_type', 'core_mechanic', 'theme', 'input_method'],
        missing_required: ['win_condition', 'difficulty'],
        slot_fill_pct: 0.67,
        ready_to_generate: true,
        confidence_by_slot: {
          game_type: 0.92,
          core_mechanic: 0.86,
          theme: 0.8,
          input_method: 0.78,
          win_condition: 0,
          difficulty: 0,
        },
        ambiguity_flags: ['win_condition:missing', 'difficulty:missing'],
        question_strategy: {
          mode: 'missing_required',
          slot_key: 'win_condition',
          reason: '因为"Win Condition"会直接决定玩法能否成型，而当前还没有明确答案。',
          impact: 0.95,
          confidence: 0,
          ambiguity_weight: 0.25,
        },
        plan_draft: {
          title: '办公室摸鱼计划',
          summary: '一款围绕办公室摸鱼展开的搞笑小游戏。',
          concept: '在办公室场景里快速做出摸鱼选择。',
          interaction: '点击不同摸鱼动作并及时躲避老板巡查。',
          objective: '撑到下班并积累足够摸鱼值。',
          pacing: '短局快节奏，每一轮都很快进入状态。',
          visual_direction: '霓虹办公室喜剧风格。',
          signature_moment: '老板突然巡查时触发夸张反转。',
        },
        current_question: {
          slot_key: 'win_condition',
          label: 'Win Condition',
          prompt: '玩家怎么才算赢？',
          skippable: true,
        },
      }),
    });
    // For the WS push findUnique after CAS update
    repo.findUnique.mockResolvedValue({
      id: 'session-1',
      userId: 'user-1',
      status: 'ready',
      entryMode: 'create',
      initialPrompt: '做一个办公室摸鱼游戏',
      titleDraft: '上班摸鱼',
      revision: 2,
      slotState: { game_type: 'funny', core_mechanic: 'tap to hide', theme: 'office', input_method: 'tap' },
      missingRequired: ['win_condition', 'difficulty'],
      skippedSlots: [],
      currentQuestion: { slotKey: 'win_condition', label: 'Win Condition', prompt: '玩家怎么才算赢？', skippable: true },
      conversation: [
        { role: 'user', content: '做一个办公室摸鱼游戏', kind: 'prompt' },
        { role: 'assistant', content: '我先补一个最关键的信息：玩家怎么才能赢？' },
      ],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        orientation: 'landscape',
        generationTier: 'showcase',
        readyToGenerate: true,
        slotFillPct: 0.67,
      },
    });

    const snapshot = await service.createSession('user-1', {
      prompt: '做一个办公室摸鱼游戏',
      title: '上班摸鱼',
      orientation: 'landscape',
      generationTier: 'showcase',
    });

    // Phase 1: optimistic creation stores 'initializing' status
    expect(repo.create).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.objectContaining({
        status: 'initializing',
        entryMode: 'create',
        titleDraft: '上班摸鱼',
        metadata: expect.objectContaining({
          orientation: 'landscape',
          generationTier: 'showcase',
          readyToGenerate: false,
          slotFillPct: 0,
          intentBuild: expect.objectContaining({
            brief: expect.any(String),
            frozenSpec: null,
            intentFingerprint: expect.any(String),
            specFingerprint: null,
          }),
        }),
      }),
    }));
    // Snapshot returned immediately reflects 'initializing'
    expect(snapshot).toEqual(expect.objectContaining({
      id: 'session-1',
      status: 'initializing',
      orientation: 'landscape',
      generationTier: 'showcase',
      intentBuild: expect.objectContaining({
        intentFingerprint: expect.any(String),
        specFingerprint: null,
        frozenSpec: null,
      }),
    }));
    expect(gameService.getCreationSessionInitTimeoutMs).toHaveBeenCalledTimes(1);
    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 45000);

    // Phase 2: wait for async _finalizeSessionInit to complete
    await new Promise((resolve) => setTimeout(resolve, 50));

    // AI analysis was called in the background
    expect(axios.post).toHaveBeenCalledWith(
      'https://ai-engine.example/api/v1/ai/dialogue/analyze-turn/stream',
      expect.objectContaining({
        session_id: 'session-1',
        user_id: 'user-1',
        title: '上班摸鱼',
        generation_tier: 'showcase',
        initial_prompt: '做一个办公室摸鱼游戏',
      }),
      { timeout: 5000, responseType: 'stream' },
    );
    // CAS update transitions from 'initializing' to 'ready'/'collecting'
    expect(repo.updateMany).toHaveBeenCalledWith(expect.objectContaining({
      where: expect.objectContaining({
        id: 'session-1',
        status: 'initializing',
        revision: 1,
      }),
      data: expect.objectContaining({
        status: 'ready',
        revision: { increment: 1 },
        metadata: expect.objectContaining({
          intentBuild: expect.objectContaining({
            brief: expect.any(String),
            frozenSpec: null,
            intentFingerprint: expect.any(String),
            specFingerprint: null,
          }),
        }),
      }),
    }));
    // WebSocket push was emitted with the finalized snapshot
    expect(wsGateway.emitSessionUpdate).toHaveBeenCalledWith(
      'user-1',
      'session-1',
      expect.objectContaining({
        status: 'ready',
        readyToGenerate: true,
      }),
    );
    expect(realtimeService.publishPhase).toHaveBeenCalledWith(
      'user-1',
      'session-1',
      'analyzing',
      'analyzing_initial_brief',
    );
    expect(realtimeService.publishReplyDelta).toHaveBeenCalled();
    expect(realtimeService.publishReplyDone).toHaveBeenCalledWith(
      'user-1',
      'session-1',
      expect.stringContaining('玩家怎么才能赢'),
      'question',
    );
    setTimeoutSpy.mockRestore();
  });

  it('abandons initialization after a single transient analyze-turn failure without retrying', async () => {
    repo.updateMany.mockResolvedValue({ count: 1 });
    repo.create.mockImplementation(async ({ data }: any) => ({
      id: 'session-retryless',
      userId: 'user-retryless',
      status: data.status,
      entryMode: data.entryMode,
      initialPrompt: data.initialPrompt,
      titleDraft: data.titleDraft,
      revision: data.revision,
      slotState: data.slotState,
      missingRequired: data.missingRequired,
      skippedSlots: data.skippedSlots,
      currentQuestion: data.currentQuestion,
      conversation: data.conversation,
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: data.sourceGameId,
      questionBudget: data.questionBudget,
      metadata: data.metadata,
      createdAt: new Date('2026-03-30T10:00:00.000Z'),
      updatedAt: new Date('2026-03-30T10:00:00.000Z'),
    }));
    (axios.post as jest.Mock).mockRejectedValueOnce({
      code: 'ETIMEDOUT',
      message: 'timeout of 5000ms exceeded',
    });

    const snapshot = await service.createSession('user-retryless', {
      prompt: 'Make a quick arcade game.',
      title: 'Retryless',
    });

    expect(snapshot).toEqual(expect.objectContaining({
      id: 'session-retryless',
      status: 'initializing',
    }));

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(repo.updateMany).toHaveBeenCalledWith({
      where: { id: 'session-retryless', userId: 'user-retryless', status: 'initializing' },
      data: expect.objectContaining({
        status: 'abandoned',
        metadata: expect.objectContaining({
          initError: 'timeout of 5000ms exceeded',
        }),
      }),
    });
    expect(wsGateway.emitSessionError).toHaveBeenCalledWith(
      'user-retryless',
      'session-retryless',
      'timeout of 5000ms exceeded',
      expect.objectContaining({ reason: 'init_failed' }),
    );
  });

  it('appends a user answer and advances the session revision', async () => {
    const existingSession = {
      id: 'session-2',
      userId: 'user-2',
      status: 'collecting',
      entryMode: 'create',
      initialPrompt: '做一个办公室摸鱼游戏',
      titleDraft: '上班摸鱼',
      revision: 1,
      slotState: {
        game_type: 'funny',
        core_mechanic: 'tap to hide',
        input_method: 'tap',
      },
      missingRequired: ['theme', 'win_condition', 'difficulty'],
      skippedSlots: [],
      currentQuestion: {
        slotKey: 'theme',
        label: 'Theme',
        prompt: '它发生在什么场景里？',
        skippable: true,
      },
      conversation: [
        { role: 'user', content: '做一个办公室摸鱼游戏' },
        { role: 'assistant', content: '我先补一个最关键的信息：它发生在什么场景里？' },
      ],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        orientation: 'portrait',
        generationTier: 'standard',
        readyToGenerate: false,
        slotFillPct: 0.4,
      },
      createdAt: new Date('2026-03-30T10:00:00.000Z'),
      updatedAt: new Date('2026-03-30T10:00:00.000Z'),
    };
    const updatedSession = {
      ...existingSession,
      revision: 2,
      status: 'ready',
      slotState: {
        ...existingSession.slotState,
        theme: 'office',
        win_condition: 'stay undiscovered until the timer ends',
        difficulty: 'medium',
      },
      missingRequired: [],
      currentQuestion: null,
      conversation: [
        ...existingSession.conversation,
        { role: 'user', content: '现代办公室，老板会突然巡查' },
        { role: 'assistant', content: '我已经整理出一版可生成方案了。' },
      ],
      metadata: {
        ...existingSession.metadata,
        readyToGenerate: true,
        slotFillPct: 1,
      },
    };

    repo.findUnique
      .mockResolvedValueOnce(existingSession)
      .mockResolvedValueOnce(updatedSession);
    repo.updateMany.mockResolvedValue({ count: 1 });
    (axios.post as jest.Mock).mockResolvedValueOnce({
      data: buildAnalyzeTurnStream({
        reply: '我已经整理出一版可生成方案了。',
        slots: updatedSession.slotState,
        slots_updated: ['theme', 'win_condition', 'difficulty'],
        missing_required: [],
        slot_fill_pct: 1,
        ready_to_generate: true,
        current_question: null,
      }),
    });

    const snapshot = await service.appendMessage('user-2', 'session-2', {
      content: '现代办公室，老板会突然巡查',
      revision: 1,
    });

    expect(axios.post).toHaveBeenCalledWith(
      'https://ai-engine.example/api/v1/ai/dialogue/analyze-turn/stream',
      expect.objectContaining({
        session_id: 'session-2',
        user_id: 'user-2',
        generation_tier: 'standard',
        answered_slot_key: 'theme',
        answered_slot_prompt: '它发生在什么场景里？',
        latest_user_answer: '现代办公室，老板会突然巡查',
      }),
      { timeout: 5000, responseType: 'stream' },
    );
    expect(repo.updateMany).toHaveBeenCalledWith(expect.objectContaining({
      where: expect.objectContaining({
        id: 'session-2',
        revision: 1,
      }),
      data: expect.objectContaining({
        status: 'ready',
        revision: { increment: 1 },
        metadata: expect.objectContaining({
          intentBuild: expect.objectContaining({
            brief: expect.any(String),
            frozenSpec: null,
            intentFingerprint: expect.any(String),
            specFingerprint: null,
          }),
        }),
      }),
    }));
    expect(snapshot).toEqual(expect.objectContaining({
      id: 'session-2',
      revision: 2,
      readyToGenerate: true,
    }));
    expect(realtimeService.publishPhase).toHaveBeenCalledWith(
      'user-2',
      'session-2',
      'analyzing',
      'analyzing_user_answer',
    );
    expect(realtimeService.publishReplyDelta).toHaveBeenCalled();
    expect(realtimeService.publishReplyDone).toHaveBeenCalledWith(
      'user-2',
      'session-2',
      expect.stringContaining('可生成方案'),
      'summary',
    );
  });

  it('compiles slots into a source spec before generating the game', async () => {
    const existingSession = {
      id: 'session-3',
      userId: 'user-3',
      status: 'ready',
      entryMode: 'fork',
      initialPrompt: '做一个搞笑办公室摸鱼游戏',
      titleDraft: '端水大师',
      revision: 3,
      slotState: {
        game_type: 'funny',
        core_mechanic: 'tap to hide',
        theme: 'office',
        input_method: 'tap',
        win_condition: 'survive the workday',
        difficulty: 'medium',
      },
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: null,
      conversation: [],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: 'game-source-1',
      questionBudget: 4,
      metadata: {
        orientation: 'landscape',
        generationTier: 'showcase',
        regionHint: 'cn-shanghai',
        readyToGenerate: true,
        slotFillPct: 1,
      },
      createdAt: new Date('2026-03-30T10:00:00.000Z'),
      updatedAt: new Date('2026-03-30T10:00:00.000Z'),
    };
    const generatingSession = {
      ...existingSession,
      revision: 4,
      status: 'generating',
      generatedGameId: 'game-1',
      generationTaskId: 'task-1',
      metadata: {
        ...existingSession.metadata,
        lastTaskStatus: 'queued',
      },
    };

    repo.findUnique
      .mockResolvedValueOnce(existingSession)
      .mockResolvedValueOnce(generatingSession);
    repo.updateMany.mockResolvedValue({ count: 1 });
    (axios.post as jest.Mock).mockResolvedValueOnce({
      data: {
        spec: {
          game_type: 'funny',
          intent_summary: '办公室摸鱼躲避老板巡查',
          generation_tier: 'showcase',
        },
        missing_required: [],
        slot_fill_pct: 1,
      },
    });
    gameService.create.mockResolvedValue({
      gameId: 'game-1',
      generationTask: {
        taskId: 'task-1',
      },
    });

    const result = await service.generateFromSession('user-3', 'session-3', {
      revision: 3,
      timeoutS: 900,
    });

    expect(axios.post).toHaveBeenCalledWith(
      'https://ai-engine.example/api/v1/ai/dialogue/spec-from-slots',
      expect.objectContaining({
        session_id: 'session-3',
        source_description: '做一个搞笑办公室摸鱼游戏',
        title: '端水大师',
        generation_tier: 'showcase',
        variation_seed: 'session-3',
      }),
      { timeout: 5000 },
    );
    expect(gameService.create).toHaveBeenCalledWith('user-3', expect.objectContaining({
      title: '端水大师',
      description: '做一个搞笑办公室摸鱼游戏',
      timeoutS: 900,
      regionHint: 'cn-shanghai',
      orientation: 'landscape',
      generationTier: 'showcase',
      sourceSpec: expect.objectContaining({
        game_type: 'funny',
        generation_tier: 'showcase',
      }),
      creationSessionId: 'session-3',
      entryMode: 'fork',
      sourceGameId: 'game-source-1',
    }));
    expect(repo.update).toHaveBeenCalledWith(expect.objectContaining({
      where: { id: 'session-3' },
      data: expect.objectContaining({
        metadata: expect.objectContaining({
          intentBuild: expect.objectContaining({
            brief: expect.any(String),
            frozenSpec: expect.objectContaining({
              game_type: 'funny',
            }),
            intentFingerprint: expect.any(String),
            specFingerprint: expect.any(String),
          }),
        }),
      }),
    }));
    expect(result).toEqual(expect.objectContaining({
      gameId: 'game-1',
      creationSession: expect.objectContaining({
        id: 'session-3',
        status: 'generating',
        generationTaskId: 'task-1',
      }),
    }));
  });

  it('surfaces nested ai-engine spec compilation errors as readable messages', async () => {
    const existingSession = {
      id: 'session-4',
      userId: 'user-4',
      status: 'ready',
      entryMode: 'create',
      initialPrompt: 'Make a landscape delivery game where the hero dashes across rooftops.',
      titleDraft: 'Parkour Delivery',
      revision: 2,
      slotState: {
        game_type: 'casual',
        core_mechanic: ['dash', 'avoid', 'drop'],
        theme: 'landscape delivery',
        input_method: 'swipe',
        win_condition: 'deliver parcels to target balconies',
        difficulty: 'medium',
      },
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: null,
      conversation: [],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        orientation: 'landscape',
        generationTier: 'showcase',
        readyToGenerate: true,
        slotFillPct: 1,
      },
      createdAt: new Date('2026-03-30T10:00:00.000Z'),
      updatedAt: new Date('2026-03-30T10:00:00.000Z'),
    };

    repo.findUnique.mockResolvedValue(existingSession);
    (axios.post as jest.Mock).mockRejectedValueOnce({
      response: {
        data: {
          detail: [
            {
              loc: ['body', 'slots', 'core_mechanic'],
              msg: 'Input should be a valid string',
            },
          ],
        },
      },
    });

    await expect(service.generateFromSession('user-4', 'session-4', {
      revision: 2,
      timeoutS: 900,
    })).rejects.toThrow('Input should be a valid string');
  });
});
