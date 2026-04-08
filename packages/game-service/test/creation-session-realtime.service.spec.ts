import { CreationSessionRealtimeService } from '../src/game/creation-session-realtime.service';

describe('CreationSessionRealtimeService', () => {
  it('streams only the public bootstrap/delta/done/snapshot protocol', () => {
    const service = new CreationSessionRealtimeService();
    const initialSnapshot = {
      id: 'session-1',
      streamPath: '/api/v1/games/creation-sessions/session-1/events',
      status: 'collecting',
      entryMode: 'create',
      initialPrompt: 'make a game like Triple Match',
      titleDraft: null,
      revision: 1,
      slotState: {},
      missingRequired: ['theme'],
      skippedSlots: [],
      currentQuestion: {
        slotKey: 'theme',
        label: 'Theme',
        prompt: 'What setting or world should this game take place in?',
        skippable: true,
      },
      conversation: [],
      slotFillPct: 0.67,
      readyToGenerate: false,
      generatedGameId: null,
      generationTaskId: null,
      sourceGameId: null,
      orientation: 'portrait',
      generationTier: 'standard',
      questionBudget: 4,
      planDraft: null,
      confidenceSummary: null,
      questionStrategy: null,
      intentBuild: null,
      metadata: null,
    } as any;

    const events: any[] = [];
    const subscription = service
      .streamSession('user-1', 'session-1', initialSnapshot)
      .subscribe((event) => {
        events.push(event);
      });

    service.publishPhase('user-1', 'session-1', 'analyzing', 'analyzing');
    service.publishPhase('user-1', 'session-1', 'replying', 'replying');
    service.publishReply(
      'user-1',
      'session-1',
      'I understand the reference. Before we generate, what setting should the game use?',
      'question',
    );
    service.publishSnapshot('user-1', 'session-1', {
      ...initialSnapshot,
      revision: 2,
      conversation: [
        {
          role: 'assistant',
          content: 'I understand the reference. Before we generate, what setting should the game use?',
          kind: 'question',
        },
      ],
    });

    subscription.unsubscribe();

    expect(events[0].type).toBe('bootstrap');
    expect(events[0].data.legacyEventType).toBe('session.bootstrap');
    expect(events.some((event) => event.type === 'assistant.phase')).toBe(false);

    const deltaEvents = events.filter((event) => event.type === 'delta');
    expect(deltaEvents.length).toBeGreaterThan(1);
    expect(deltaEvents.every((event) => event.data.legacyEventType === 'assistant.reply.delta')).toBe(true);
    expect(deltaEvents.every((event) => typeof event.data.messageId === 'string' && event.data.messageId.length > 0)).toBe(true);

    const doneEvent = events.find((event) => event.type === 'done');
    expect(doneEvent).toBeDefined();
    expect(doneEvent?.data.legacyEventType).toBe('assistant.reply.done');
    expect(doneEvent?.data.messageId).toBe(deltaEvents[0].data.messageId);

    expect(events[events.length - 1].type).toBe('snapshot');
    expect(events[events.length - 1].data.legacyEventType).toBe('session.updated');
  });
});
