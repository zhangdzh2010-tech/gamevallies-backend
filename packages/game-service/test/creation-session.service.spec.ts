import { CreationSessionService } from "../src/game/creation-session.service";

describe("CreationSessionService", () => {
  let service: CreationSessionService;
  let prisma: any;
  let repo: any;
  let gameService: any;
  let realtimeService: any;

  const rowFromCreateData = (id: string, userId: string, data: any) => ({
    id,
    userId,
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
  });

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
      create: jest.fn(),
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
      realtimeService as any,
    );
  });

  it("creates a ready session from the user's prompt without AI expansion", async () => {
    repo.updateMany.mockResolvedValue({ count: 1 });
    repo.create.mockImplementation(async ({ data }: any) =>
      rowFromCreateData("session-1", "user-1", data),
    );

    const snapshot = await service.createSession("user-1", {
      prompt: "Make an office slacking game",
      title: "Slack Hero",
      orientation: "landscape",
      regionHint: "cn-shanghai",
    });

    expect(snapshot).toEqual(
      expect.objectContaining({
        id: "session-1",
        status: "ready",
        initialPrompt: "Make an office slacking game",
        expandedPrompt: "Make an office slacking game",
        readyToGenerate: true,
        currentQuestion: null,
        generationTier: "showcase",
        metadata: null,
      }),
    );
    expect(repo.create).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          status: "ready",
          currentQuestion: null,
          metadata: expect.objectContaining({
            userPrompt: "Make an office slacking game",
            expandedPrompt: "Make an office slacking game",
            readyToGenerate: true,
            slotFillPct: 1,
            generationTier: "showcase",
          }),
        }),
      }),
    );
    expect(gameService.create).not.toHaveBeenCalled();
  });

  it("treats appended content as the user's latest generation brief", async () => {
    const existingSession = {
      id: "session-2",
      userId: "user-2",
      status: "ready",
      entryMode: "create",
      initialPrompt: "Make an office stealth game",
      titleDraft: "Slack Hero",
      revision: 1,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: null,
      conversation: [{ role: "user", content: "Make an office stealth game", kind: "prompt" }],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        orientation: "portrait",
        generationTier: "showcase",
        userPrompt: "Make an office stealth game",
        expandedPrompt: "Make an office stealth game",
        readyToGenerate: true,
        slotFillPct: 1,
      },
      createdAt: new Date("2026-04-16T10:00:00.000Z"),
      updatedAt: new Date("2026-04-16T10:00:00.000Z"),
    };
    const nextPrompt =
      "Make an office stealth comedy where tapping swaps between working and slacking while the boss patrols.";
    const updatedSession = {
      ...existingSession,
      revision: 2,
      conversation: [
        ...existingSession.conversation,
        { role: "user", content: nextPrompt, kind: "prompt" },
        { role: "assistant", content: "Updated. Ready to generate.", kind: "summary" },
      ],
      metadata: {
        ...existingSession.metadata,
        userPrompt: nextPrompt,
        expandedPrompt: nextPrompt,
      },
    };

    repo.findUnique
      .mockResolvedValueOnce(existingSession)
      .mockResolvedValueOnce(updatedSession);
    repo.updateMany.mockResolvedValue({ count: 1 });

    const snapshot = await service.appendMessage("user-2", "session-2", {
      content: nextPrompt,
      revision: 1,
    });

    expect(repo.updateMany).toHaveBeenCalledWith(
      expect.objectContaining({
        data: expect.objectContaining({
          status: "ready",
          currentQuestion: null,
          metadata: expect.objectContaining({
            userPrompt: nextPrompt,
            expandedPrompt: nextPrompt,
            readyToGenerate: true,
            slotFillPct: 1,
          }),
        }),
      }),
    );
    expect(realtimeService.publishReplyDone).toHaveBeenCalledWith(
      "user-2",
      "session-2",
      "Updated. Ready to generate.",
      "summary",
    );
    expect(snapshot.expandedPrompt).toBe(nextPrompt);
  });

  it("treats skip on an already-ready session as a no-op", async () => {
    const existingSession = {
      id: "session-skip-ready",
      userId: "user-skip",
      status: "ready",
      entryMode: "create",
      initialPrompt: "Make a puzzle game",
      titleDraft: "Puzzle",
      revision: 3,
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
        generationTier: "showcase",
        userPrompt: "Make a puzzle game",
        expandedPrompt: "Make a puzzle game",
        readyToGenerate: true,
        slotFillPct: 1,
      },
    };
    repo.findUnique.mockResolvedValue(existingSession);

    const snapshot = await service.skipCurrentQuestion(
      "user-skip",
      "session-skip-ready",
      { revision: 3 },
    );

    expect(repo.updateMany).not.toHaveBeenCalled();
    expect(realtimeService.publishReplyDone).not.toHaveBeenCalled();
    expect(snapshot).toEqual(
      expect.objectContaining({
        id: "session-skip-ready",
        status: "ready",
        expandedPrompt: "Make a puzzle game",
      }),
    );
  });

  it("uses the user's prompt, not a legacy expanded prompt, when generating", async () => {
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
        regionHint: "cn-shanghai",
        expandedPrompt:
          "Game Type: Internal scaffold that should not become the generation input",
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
        generationTier: "showcase",
        userPrompt: "Make an office slacking game",
        expandedPrompt: "Make an office slacking game",
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
      generationTask: { taskId: "task-1" },
    });

    const result = await service.generateFromSession("user-3", "session-3", {
      revision: 3,
      timeoutS: 900,
    });

    expect(gameService.create).toHaveBeenCalledTimes(1);
    expect(gameService.create.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        title: "Slack Hero",
        description: "Make an office slacking game",
        userIdea: "Make an office slacking game",
        timeoutS: 900,
        regionHint: "cn-shanghai",
        orientation: "landscape",
        generationTier: "showcase",
        creationSessionId: "session-3",
        entryMode: "fork",
        sourceGameId: "game-source-1",
      }),
    );
    expect(result.creationSession).toEqual(
      expect.objectContaining({
        id: "session-3",
        status: "completed",
        generationTaskId: "task-1",
        expandedPrompt: "Make an office slacking game",
      }),
    );
  });

  it("keeps internal diagnostics out of public snapshots", async () => {
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
        generationTier: "showcase",
        userPrompt: "Make an office slacking game",
        expandedPrompt: "Game Type: old internal scaffold",
        readyToGenerate: false,
        slotFillPct: 1,
        intentBuild: {
          brief: "Summary: Make an office slacking game",
          frozenSpec: { game_type: "funny" },
          intentFingerprint: "intent-fp",
          specFingerprint: "spec-fp",
        },
        confidenceBySlot: { game_type: 0.91 },
        initError: "Legacy initialization timed out",
        abandonedAt: "2026-04-08T09:00:00.000Z",
        expandFallbackUsed: true,
        expandFallbackReason: "legacy",
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
        expandedPrompt: "Make an office slacking game",
        intentBuild: { brief: "Summary: Make an office slacking game" },
        metadata: {
          initError: "Legacy initialization timed out",
          abandonedAt: "2026-04-08T09:00:00.000Z",
        },
      }),
    );
    expect((snapshot.intentBuild as any).frozenSpec).toBeUndefined();
    expect((snapshot.metadata as any).expandFallbackUsed).toBeUndefined();
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
      currentQuestion: null,
      conversation: [],
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      questionBudget: 4,
      metadata: {
        generationTier: "showcase",
        userPrompt: "Make a puzzle game.",
        expandedPrompt: "Make a puzzle game.",
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
