import { CreationSessionRealtimeService } from "../src/game/creation-session-realtime.service";

describe("CreationSessionRealtimeService", () => {
  it("streams only the public bootstrap/delta/done/snapshot protocol", () => {
    const service = new CreationSessionRealtimeService();
    const initialSnapshot = {
      id: "session-1",
      streamPath: "/api/v1/games/creation-sessions/session-1/events",
      status: "collecting",
      entryMode: "create",
      initialPrompt: "make a game like Triple Match",
      expandedPrompt:
        "Game Type: Match puzzle\nCore Mechanic: Tap connected groups to clear the board\nTheme: Cozy bakery shelves\nInput Method: Single tap\nWin Condition: Reach the order target before moves run out\nDifficulty Ramp: Add blockers and tighter move limits in later rounds",
      titleDraft: null,
      revision: 1,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: {
        slotKey: "expanded_prompt",
        label: "Prompt Confirmation",
        prompt:
          "I expanded your idea into a generation prompt that covers game type, core mechanic, controls, win condition, and difficulty ramp. Confirm it as-is or edit the prompt before confirming.",
        skippable: true,
      },
      conversation: [],
      slotFillPct: 1,
      readyToGenerate: false,
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      orientation: "portrait",
      generationTier: "standard",
      questionBudget: 4,
      planDraft: null,
      confidenceSummary: null,
      questionStrategy: null,
      intentBuild: null,
      metadata: null,
    } as any;

    const events: any[] = [];
    const subscription = service
      .streamSession("user-1", "session-1", initialSnapshot)
      .subscribe((event) => {
        events.push(event);
      });

    service.publishReplyDelta(
      "user-1",
      "session-1",
      "This prompt is confirmed ",
      "This prompt is confirmed ",
      "summary",
    );
    service.publishReplyDelta(
      "user-1",
      "session-1",
      "and ready for generation.",
      "This prompt is confirmed and ready for generation.",
      "summary",
    );
    service.publishReplyDone(
      "user-1",
      "session-1",
      "This prompt is confirmed and ready for generation.",
      "summary",
    );
    service.publishSnapshot("user-1", "session-1", {
      ...initialSnapshot,
      revision: 2,
      currentQuestion: null,
      readyToGenerate: true,
      conversation: [
        {
          role: "assistant",
          content: "This prompt is confirmed and ready for generation.",
          kind: "summary",
        },
      ],
    });

    subscription.unsubscribe();

    expect(events[0].type).toBe("bootstrap");
    expect(events[0].data).not.toHaveProperty("legacyEventType");
    expect(events.some((event) => event.type === "assistant.phase")).toBe(
      false,
    );

    const deltaEvents = events.filter((event) => event.type === "delta");
    expect(deltaEvents).toHaveLength(2);
    expect(
      deltaEvents.every(
        (event) =>
          typeof event.data.messageId === "string" &&
          event.data.messageId.length > 0,
      ),
    ).toBe(true);
    expect(
      deltaEvents.every((event) => !("legacyEventType" in event.data)),
    ).toBe(true);
    expect(deltaEvents[0].data.messageId).toBe(deltaEvents[1].data.messageId);

    const doneEvent = events.find((event) => event.type === "done");
    expect(doneEvent).toBeDefined();
    expect(doneEvent?.data).not.toHaveProperty("legacyEventType");
    expect(doneEvent?.data.messageId).toBe(deltaEvents[0].data.messageId);

    expect(events[events.length - 1].type).toBe("snapshot");
    expect(events[events.length - 1].data).not.toHaveProperty(
      "legacyEventType",
    );
  });

  it("uses the latest cached snapshot for bootstrap after an early publish", () => {
    const service = new CreationSessionRealtimeService();
    const initialSnapshot = {
      id: "session-2",
      streamPath: "/api/v1/games/creation-sessions/session-2/events",
      status: "initializing",
      entryMode: "create",
      initialPrompt: "build a fruit merge game",
      expandedPrompt: null,
      titleDraft: null,
      revision: 1,
      slotState: {},
      missingRequired: [],
      skippedSlots: [],
      currentQuestion: null,
      conversation: [],
      slotFillPct: 0,
      readyToGenerate: false,
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      orientation: "portrait",
      generationTier: "standard",
      questionBudget: 4,
      planDraft: null,
      confidenceSummary: null,
      questionStrategy: null,
      intentBuild: null,
      metadata: null,
    } as any;
    const updatedSnapshot = {
      ...initialSnapshot,
      status: "collecting",
      revision: 2,
      expandedPrompt: "build a fruit merge game with a playful market theme",
      currentQuestion: {
        slotKey: "expanded_prompt",
        label: "Prompt Confirmation",
        prompt:
          "I expanded your idea into a generation prompt that covers game type, core mechanic, controls, win condition, and difficulty ramp. Confirm it as-is or edit the prompt before confirming.",
        skippable: true,
      },
      slotFillPct: 1,
    } as any;

    service.publishSnapshot("user-2", "session-2", updatedSnapshot);

    const events: any[] = [];
    const subscription = service
      .streamSession("user-2", "session-2", initialSnapshot)
      .subscribe((event) => {
        events.push(event);
      });

    subscription.unsubscribe();

    expect(events[0].type).toBe("bootstrap");
    expect(events[0].data.session.status).toBe("collecting");
    expect(events[0].data.session.revision).toBe(2);
    expect(events[0].data.session.currentQuestion?.slotKey).toBe(
      "expanded_prompt",
    );
  });
});
