import { CreationSessionRealtimeService } from '../src/game/creation-session-realtime.service';

describe('CreationSessionRealtimeService', () => {
  it('streams bootstrap, assistant phases, reply deltas, and final snapshot updates', () => {
    const service = new CreationSessionRealtimeService();
    const initialSnapshot = {
      id: 'session-1',
      streamPath: '/api/v1/games/creation-sessions/session-1/events',
      status: 'collecting',
      entryMode: 'create',
      initialPrompt: 'make a game like 羊了个羊',
      titleDraft: null,
      revision: 1,
      slotState: {},
      missingRequired: ['theme'],
      skippedSlots: [],
      currentQuestion: {
        slotKey: 'theme',
        label: '题材情境',
        prompt: '玩法可以参考《羊了个羊》，但题材你想换成什么情境或世界观？',
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
      metadata: {},
    } as any;

    const events: any[] = [];
    const subscription = service
      .streamSession('user-1', 'session-1', initialSnapshot)
      .subscribe((event) => {
        events.push(event);
      });

    service.publishPhase('user-1', 'session-1', 'analyzing', 'analyzing_initial_brief');
    service.publishPhase('user-1', 'session-1', 'replying', 'streaming_first_reply');
    service.publishReply(
      'user-1',
      'session-1',
      '了解，我会把《羊了个羊》理解成层叠卡牌 + 三消收纳。为了别把关键体验做偏，我先追一个最重要的问题：玩法可以参考《羊了个羊》，但题材你想换成什么情境或世界观？',
      'question',
    );
    service.publishSnapshot('user-1', 'session-1', {
      ...initialSnapshot,
      revision: 2,
      conversation: [
        {
          role: 'assistant',
          content: '了解，我会把《羊了个羊》理解成层叠卡牌 + 三消收纳。为了别把关键体验做偏，我先追一个最重要的问题：玩法可以参考《羊了个羊》，但题材你想换成什么情境或世界观？',
          kind: 'question',
        },
      ],
    });

    subscription.unsubscribe();

    expect(events[0].type).toBe('session.bootstrap');
    expect(events.some((event) => event.type === 'assistant.phase' && event.data.phase === 'analyzing')).toBe(true);
    expect(events.some((event) => event.type === 'assistant.phase' && event.data.phase === 'replying')).toBe(true);
    expect(events.filter((event) => event.type === 'assistant.reply.delta').length).toBeGreaterThan(1);
    expect(events.some((event) => event.type === 'assistant.reply.done')).toBe(true);
    expect(events[events.length - 1].type).toBe('session.updated');
  });
});
