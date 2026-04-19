import axios from "axios";
import { CreationSessionService } from "../src/game/creation-session.service";

jest.mock("axios");

describe("CreationSessionService", () => {
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
      getAiEngineBaseUrl: jest
        .fn()
        .mockResolvedValue("https://ai-engine.example"),
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

  it("creates a session optimistically, then expands into a collecting session waiting for confirmation", async () => {
    const originalSetTimeout = global.setTimeout;
    const setTimeoutSpy = jest.spyOn(global, "setTimeout").mockImplementation(((
      handler: any,
      timeout?: any,
      ...args: any[]
    ) => {
      if (timeout === 45000) {
        return 0 as any;
      }
      return originalSetTimeout(handler, timeout as any, ...args);
    }) as typeof setTimeout);

    const rawExpandedPrompt =
      'Original Idea: Make an office slacking game\n\nPlease turn this brief into a mobile-friendly game generation prompt that covers at least these elements:\nGame Type: Funny stealth comedy\nCore Mechanic: Tap to swap between working and slacking states while hiding from surprise inspections\nTheme: Open-plan office satire';
    const expandedPrompt = [
      'Create a mobile HTML5 game based on this brief: "Make an office slacking game".',
      "Keep the core actions, setting, character fantasy, and mood from the original idea so the player understands the goal almost immediately through touch-first controls.",
      "Each round should have a clear success condition, visible escalation, and rewards or feedback that reinforce the same fantasy instead of drifting into generic filler.",
      "Make the scene, props, and any main character feel intentionally designed and visually coherent rather than like placeholder geometry.",
    ].join("\n");
    const confirmationQuestion =
      "I turned your idea into a user-facing game brief. Confirm it as-is, or edit the wording first if you want to refine it before generation.";

    repo.updateMany.mockResolvedValue({ count: 1 });
    repo.create.mockImplementation(async ({ data }: any) => ({
      id: "session-1",
      userId: "user-1",
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
      createdAt: new Date("2026-04-16T10:00:00.000Z"),
      updatedAt: new Date("2026-04-16T10:00:00.000Z"),
    }));
    (axios.post as jest.Mock).mockResolvedValueOnce({
      data: {
        expanded_prompt: rawExpandedPrompt,
      },
    });
    repo.findUnique.mockResolvedValue({
      id: "session-1",
      userId: "user-1",
      status: "collecting",
      entryMode: "create",
      initialPrompt: "Make an office slacking game",
      titleDraft: "Slack Hero",
      revision: 2,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: {
        slotKey: "expanded_prompt",
        label: "Prompt Confirmation",
        prompt: confirmationQuestion,
        skippable: true,
      },
      conversation: [
        {
          role: "user",
          content: "Make an office slacking game",
          kind: "prompt",
        },
        {
          role: "assistant",
          content: expandedPrompt,
          kind: "summary",
        },
      ],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        orientation: "landscape",
        generationTier: "showcase",
        regionHint: "cn-shanghai",
        expandedPrompt,
        readyToGenerate: false,
        slotFillPct: 1,
        intentBuild: {
          brief: expect.any(String),
          frozenSpec: null,
          intentFingerprint: "intent-fp",
          specFingerprint: null,
        },
      },
    });

    const snapshot = await service.createSession("user-1", {
      prompt: "Make an office slacking game",
      title: "Slack Hero",
      orientation: "landscape",
      generationTier: "showcase",
      regionHint: "cn-shanghai",
    });

    expect(snapshot).toEqual(
      expect.objectContaining({
        id: "session-1",
        status: "initializing",
        expandedPrompt: null,
        readyToGenerate: false,
        metadata: null,
      }),
    );

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(axios.post).toHaveBeenCalledWith(
      "https://ai-engine.example/api/v1/ai/expand-prompt",
      { description: "Make an office slacking game" },
      { timeout: 5000 },
    );
    expect(repo.updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: expect.objectContaining({
          id: "session-1",
          userId: "user-1",
          status: "initializing",
          revision: 1,
        }),
        data: expect.objectContaining({
          status: "collecting",
          slotState: {},
          missingRequired: [],
          currentQuestion: expect.objectContaining({
            slotKey: "expanded_prompt",
            prompt: confirmationQuestion,
          }),
          metadata: expect.objectContaining({
            expandedPrompt,
            readyToGenerate: false,
            slotFillPct: 1,
          }),
        }),
      }),
    );
    expect(realtimeService.publishReplyDone).toHaveBeenCalledWith(
      "user-1",
      "session-1",
      expandedPrompt,
      "summary",
    );
    expect(wsGateway.emitSessionUpdate).toHaveBeenCalledWith(
      "user-1",
      "session-1",
      expect.objectContaining({
        status: "collecting",
        expandedPrompt,
        readyToGenerate: false,
        currentQuestion: expect.objectContaining({
          slotKey: "expanded_prompt",
        }),
      }),
    );

    setTimeoutSpy.mockRestore();
  });

  it("abandons initialization when prompt expansion fails", async () => {
    const originalSetTimeout = global.setTimeout;
    const setTimeoutSpy = jest.spyOn(global, "setTimeout").mockImplementation(((
      handler: any,
      timeout?: any,
      ...args: any[]
    ) => {
      if (timeout === 45000) {
        return 0 as any;
      }
      return originalSetTimeout(handler, timeout as any, ...args);
    }) as typeof setTimeout);

    repo.updateMany.mockResolvedValue({ count: 1 });
    repo.create.mockImplementation(async ({ data }: any) => ({
      id: "session-init-fail",
      userId: "user-init-fail",
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
      createdAt: new Date("2026-04-16T10:00:00.000Z"),
      updatedAt: new Date("2026-04-16T10:00:00.000Z"),
    }));
    (axios.post as jest.Mock).mockRejectedValueOnce({
      code: "ETIMEDOUT",
      message: "timeout of 5000ms exceeded",
    });

    const snapshot = await service.createSession("user-init-fail", {
      prompt: "Make a quick arcade game.",
      title: "Retryless",
    });

    expect(snapshot).toEqual(
      expect.objectContaining({
        id: "session-init-fail",
        status: "initializing",
        expandedPrompt: null,
      }),
    );

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(repo.updateMany).toHaveBeenCalledWith({
      where: {
        id: "session-init-fail",
        userId: "user-init-fail",
        status: "initializing",
      },
      data: expect.objectContaining({
        status: "abandoned",
        metadata: expect.objectContaining({
          expandedPrompt: null,
          initError: "timeout of 5000ms exceeded",
        }),
      }),
    });
    expect(wsGateway.emitSessionError).toHaveBeenCalledWith(
      "user-init-fail",
      "session-init-fail",
      "timeout of 5000ms exceeded",
      expect.objectContaining({ reason: "init_failed" }),
    );

    setTimeoutSpy.mockRestore();
  });

  it("treats appended content as the edited prompt confirmation and moves the session to ready", async () => {
    const existingSession = {
      id: "session-2",
      userId: "user-2",
      status: "collecting",
      entryMode: "create",
      initialPrompt: "Make an office stealth game",
      titleDraft: "Slack Hero",
      revision: 1,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: {
        slotKey: "expanded_prompt",
        label: "Prompt Confirmation",
        prompt:
        "I turned your idea into a user-facing game brief. Confirm it as-is, or edit the wording first if you want to refine it before generation.",
        skippable: true,
      },
      conversation: [
        {
          role: "user",
          content: "Make an office stealth game",
          kind: "prompt",
        },
        {
          role: "assistant",
          content: "Old expanded prompt",
          kind: "summary",
        },
      ],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        orientation: "portrait",
        generationTier: "standard",
        expandedPrompt: "Old expanded prompt",
        readyToGenerate: false,
        slotFillPct: 1,
      },
      createdAt: new Date("2026-04-16T10:00:00.000Z"),
      updatedAt: new Date("2026-04-16T10:00:00.000Z"),
    };
    const updatedSession = {
      ...existingSession,
      revision: 2,
      status: "ready",
      currentQuestion: null,
      conversation: [
        ...existingSession.conversation,
        {
          role: "user",
          content:
            "Game Type: Funny stealth comedy\nCore Mechanic: Tap to fake working while dodging inspections\nTheme: Chaotic startup office\nInput Method: Single tap\nWin Condition: Survive until clock-out\nDifficulty Ramp: Inspections become more frequent each round",
          kind: "prompt",
        },
        {
          role: "assistant",
          content: "This prompt is confirmed and ready for generation.",
          kind: "summary",
        },
      ],
      metadata: {
        ...existingSession.metadata,
        expandedPrompt:
          "Game Type: Funny stealth comedy\nCore Mechanic: Tap to fake working while dodging inspections\nTheme: Chaotic startup office\nInput Method: Single tap\nWin Condition: Survive until clock-out\nDifficulty Ramp: Inspections become more frequent each round",
        readyToGenerate: true,
      },
    };

    repo.findUnique
      .mockResolvedValueOnce(existingSession)
      .mockResolvedValueOnce(updatedSession);
    repo.updateMany.mockResolvedValue({ count: 1 });

    const snapshot = await service.appendMessage("user-2", "session-2", {
      content:
        "Game Type: Funny stealth comedy\nCore Mechanic: Tap to fake working while dodging inspections\nTheme: Chaotic startup office\nInput Method: Single tap\nWin Condition: Survive until clock-out\nDifficulty Ramp: Inspections become more frequent each round",
      revision: 1,
    });

    expect(axios.post).not.toHaveBeenCalled();
    expect(repo.updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: expect.objectContaining({
          id: "session-2",
          revision: 1,
        }),
        data: expect.objectContaining({
          status: "ready",
          slotState: {},
          missingRequired: [],
          currentQuestion: null,
          metadata: expect.objectContaining({
            expandedPrompt:
              "Game Type: Funny stealth comedy\nCore Mechanic: Tap to fake working while dodging inspections\nTheme: Chaotic startup office\nInput Method: Single tap\nWin Condition: Survive until clock-out\nDifficulty Ramp: Inspections become more frequent each round",
            readyToGenerate: true,
            slotFillPct: 1,
          }),
        }),
      }),
    );
    expect(realtimeService.publishReplyDone).toHaveBeenCalledWith(
      "user-2",
      "session-2",
      "This prompt is confirmed and ready for generation.",
      "summary",
    );
    expect(snapshot).toEqual(
      expect.objectContaining({
        id: "session-2",
        revision: 2,
        status: "ready",
        expandedPrompt:
          "Game Type: Funny stealth comedy\nCore Mechanic: Tap to fake working while dodging inspections\nTheme: Chaotic startup office\nInput Method: Single tap\nWin Condition: Survive until clock-out\nDifficulty Ramp: Inspections become more frequent each round",
        readyToGenerate: true,
      }),
    );
  });

  it("treats skip as confirming the expanded prompt as-is and moves the session to ready", async () => {
    const existingSession = {
      id: "session-skip-1",
      userId: "user-skip-1",
      status: "collecting",
      entryMode: "create",
      initialPrompt: "Make an office puzzle game.",
      titleDraft: "Office Layers",
      revision: 1,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: {
        slotKey: "expanded_prompt",
        label: "Prompt Confirmation",
        prompt:
        "I turned your idea into a user-facing game brief. Confirm it as-is, or edit the wording first if you want to refine it before generation.",
        skippable: true,
      },
      conversation: [
        {
          role: "user",
          content: "Make an office puzzle game.",
          kind: "prompt",
        },
        {
          role: "assistant",
          content:
            "Game Type: Puzzle\nCore Mechanic: Tap to clear office clutter combos\nTheme: Overloaded office desk\nInput Method: Single tap\nWin Condition: Clear the target clutter before moves run out\nDifficulty Ramp: Add blockers and tighter move budgets each stage",
          kind: "summary",
        },
      ],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        orientation: "portrait",
        generationTier: "standard",
        expandedPrompt:
          "Game Type: Puzzle\nCore Mechanic: Tap to clear office clutter combos\nTheme: Overloaded office desk\nInput Method: Single tap\nWin Condition: Clear the target clutter before moves run out\nDifficulty Ramp: Add blockers and tighter move budgets each stage",
        readyToGenerate: false,
        slotFillPct: 1,
      },
      createdAt: new Date("2026-04-16T10:00:00.000Z"),
      updatedAt: new Date("2026-04-16T10:00:00.000Z"),
    };
    const updatedSession = {
      ...existingSession,
      revision: 2,
      status: "ready",
      currentQuestion: null,
      skippedSlots: [],
      conversation: [
        ...existingSession.conversation,
        {
          role: "assistant",
          content: "This prompt is confirmed and ready for generation.",
          kind: "summary",
        },
      ],
      metadata: {
        ...existingSession.metadata,
        readyToGenerate: true,
      },
    };

    repo.findUnique
      .mockResolvedValueOnce(existingSession)
      .mockResolvedValueOnce(updatedSession);
    repo.updateMany.mockResolvedValue({ count: 1 });

    const snapshot = await service.skipCurrentQuestion(
      "user-skip-1",
      "session-skip-1",
      {
        revision: 1,
      },
    );

    expect(axios.post).not.toHaveBeenCalled();
    expect(repo.updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          status: "ready",
          skippedSlots: [],
          currentQuestion: null,
          metadata: expect.objectContaining({
            expandedPrompt:
              "Game Type: Puzzle\nCore Mechanic: Tap to clear office clutter combos\nTheme: Overloaded office desk\nInput Method: Single tap\nWin Condition: Clear the target clutter before moves run out\nDifficulty Ramp: Add blockers and tighter move budgets each stage",
            readyToGenerate: true,
            slotFillPct: 1,
          }),
        }),
      }),
    );
    expect(realtimeService.publishReplyDone).toHaveBeenCalledWith(
      "user-skip-1",
      "session-skip-1",
      "This prompt is confirmed and ready for generation.",
      "summary",
    );
    expect(snapshot).toEqual(
      expect.objectContaining({
        id: "session-skip-1",
        status: "ready",
        expandedPrompt:
          "Game Type: Puzzle\nCore Mechanic: Tap to clear office clutter combos\nTheme: Overloaded office desk\nInput Method: Single tap\nWin Condition: Clear the target clutter before moves run out\nDifficulty Ramp: Add blockers and tighter move budgets each stage",
        readyToGenerate: true,
      }),
    );
  });

  it("uses the confirmed expanded prompt when generating the game", async () => {
    const expandedPrompt =
      "Game Type: Funny stealth comedy\nCore Mechanic: Tap to swap between working and slacking states while hiding from surprise inspections\nTheme: Open-plan office satire\nInput Method: Single-tap interactions\nWin Condition: Stay undiscovered until the shift ends\nDifficulty Ramp: Boss inspections happen more often and react faster each round\nScoring: Earn points for every successful slacking streak\nVisual Direction: Exaggerated office comedy with bright props";
    const existingSession = {
      id: "session-3",
      userId: "user-3",
      status: "ready",
      entryMode: "fork",
      initialPrompt: "Make an office slacking game",
      titleDraft: "Slack Hero",
      revision: 3,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: null,
      conversation: [],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: "game-source-1",
      questionBudget: 4,
      metadata: {
        orientation: "landscape",
        generationTier: "showcase",
        regionHint: "cn-shanghai",
        expandedPrompt,
        readyToGenerate: true,
        slotFillPct: 1,
      },
      createdAt: new Date("2026-04-16T10:00:00.000Z"),
      updatedAt: new Date("2026-04-16T10:00:00.000Z"),
    };
    const completedSession = {
      ...existingSession,
      revision: 4,
      status: "completed",
      generatedGameId: "game-1",
      generationTaskId: "task-1",
      metadata: {
        ...existingSession.metadata,
        lastTaskStatus: "queued",
      },
    };

    repo.findUnique
      .mockResolvedValueOnce(existingSession)
      .mockResolvedValueOnce(completedSession);
    repo.updateMany.mockResolvedValue({ count: 1 });
    repo.update.mockResolvedValue(completedSession);
    gameService.create.mockResolvedValue({
      gameId: "game-1",
      generationTask: {
        taskId: "task-1",
      },
    });

    const result = await service.generateFromSession("user-3", "session-3", {
      revision: 3,
      timeoutS: 900,
    });

    expect(gameService.create).toHaveBeenCalledTimes(1);
    const createPayload = gameService.create.mock.calls[0][1];
    expect(createPayload).toEqual(
      expect.objectContaining({
        title: "Slack Hero",
        description: expandedPrompt,
        timeoutS: 900,
        regionHint: "cn-shanghai",
        orientation: "landscape",
        generationTier: "showcase",
        creationSessionId: "session-3",
        entryMode: "fork",
        sourceGameId: "game-source-1",
      }),
    );
    expect(createPayload.sourceSpec).toBeUndefined();
    expect(repo.update).toHaveBeenCalledWith(
      expect.objectContaining({
        where: { id: "session-3" },
        data: expect.objectContaining({
          status: "completed",
          metadata: expect.objectContaining({
            expandedPrompt,
            intentBuild: expect.objectContaining({
              brief: expect.any(String),
              frozenSpec: null,
              intentFingerprint: expect.any(String),
            }),
          }),
        }),
      }),
    );
    expect(result).toEqual(
      expect.objectContaining({
        gameId: "game-1",
        creationSession: expect.objectContaining({
          id: "session-3",
          status: "completed",
          generationTaskId: "task-1",
          expandedPrompt,
        }),
      }),
    );
  });

  it("strips internal diagnostics from public session snapshots while exposing expandedPrompt", async () => {
    repo.findUnique.mockResolvedValue({
      id: "session-public-1",
      userId: "user-public-1",
      status: "abandoned",
      entryMode: "create",
      initialPrompt: "Make an office slacking game",
      titleDraft: "Slack Hero",
      revision: 2,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: null,
      conversation: [],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        orientation: "portrait",
        generationTier: "standard",
        expandedPrompt:
          "Game Type: Funny stealth comedy\nCore Mechanic: Tap to fake working while dodging inspections\nTheme: Startup office\nInput Method: Single tap\nWin Condition: Reach clock-out without being caught\nDifficulty Ramp: Inspections accelerate each round",
        readyToGenerate: false,
        slotFillPct: 1,
        intentBuild: {
          brief:
            "Summary: Game Type: Funny stealth comedy\nConcept: Startup office\nInteraction: Tap to fake working while dodging inspections\nObjective: Reach clock-out without being caught\nPacing: Inspections accelerate each round",
          frozenSpec: { game_type: "funny" },
          intentFingerprint: "intent-fp",
          specFingerprint: "spec-fp",
        },
        confidenceBySlot: {
          game_type: 0.91,
        },
        evidenceBySlot: {
          game_type: "user clearly asked for a funny game",
        },
        initError: "Session initialization timed out",
        abandonedAt: "2026-04-08T09:00:00.000Z",
      },
      createdAt: new Date("2026-04-08T09:00:00.000Z"),
      updatedAt: new Date("2026-04-08T09:00:00.000Z"),
    });

    const snapshot = await service.getSession(
      "user-public-1",
      "session-public-1",
    );

    expect(snapshot).toEqual(
      expect.objectContaining({
        id: "session-public-1",
        status: "abandoned",
        expandedPrompt:
          "Game Type: Funny stealth comedy\nCore Mechanic: Tap to fake working while dodging inspections\nTheme: Startup office\nInput Method: Single tap\nWin Condition: Reach clock-out without being caught\nDifficulty Ramp: Inspections accelerate each round",
        orientation: "portrait",
        generationTier: "standard",
        intentBuild: {
          brief:
            "Summary: Game Type: Funny stealth comedy\nConcept: Startup office\nInteraction: Tap to fake working while dodging inspections\nObjective: Reach clock-out without being caught\nPacing: Inspections accelerate each round",
        },
        metadata: {
          initError: "Session initialization timed out",
          abandonedAt: "2026-04-08T09:00:00.000Z",
        },
      }),
    );
    expect((snapshot.intentBuild as any).frozenSpec).toBeUndefined();
    expect((snapshot.metadata as any).confidenceBySlot).toBeUndefined();
  });

  it("rejects generation before the session reaches ready", async () => {
    repo.findUnique.mockResolvedValue({
      id: "session-not-ready",
      userId: "user-not-ready",
      status: "collecting",
      entryMode: "create",
      initialPrompt: "Make a puzzle game.",
      titleDraft: "Puzzle",
      revision: 1,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: {
        slotKey: "expanded_prompt",
        label: "Prompt Confirmation",
        prompt:
        "I turned your idea into a user-facing game brief. Confirm it as-is, or edit the wording first if you want to refine it before generation.",
        skippable: true,
      },
      conversation: [],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        generationTier: "standard",
        expandedPrompt:
          "Game Type: Puzzle\nCore Mechanic: Tap to clear matching desk items\nTheme: Office desk cleanup\nInput Method: Single tap\nWin Condition: Clear all clutter before moves run out\nDifficulty Ramp: Add blockers after each stage",
        readyToGenerate: false,
        slotFillPct: 1,
      },
      createdAt: new Date("2026-04-16T10:00:00.000Z"),
      updatedAt: new Date("2026-04-16T10:00:00.000Z"),
    });

    await expect(
      service.generateFromSession("user-not-ready", "session-not-ready", {
        revision: 1,
      }),
    ).rejects.toThrow("Creation session is not ready to generate");
  });
});
