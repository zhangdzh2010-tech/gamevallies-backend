import { GameController } from '../src/game/game.controller';
import { Subject } from 'rxjs';

describe('GameController', () => {
  let controller: GameController;
  let gameService: {
    create: jest.Mock;
    iterate: jest.Mock;
  };
  let reputationService: {
    getReputation: jest.Mock;
  };
  let creationSessionRealtimeService: {
    streamSession: jest.Mock;
  };
  let creationSessionService: {
    createSession: jest.Mock;
    getActiveSession: jest.Mock;
    getSession: jest.Mock;
    appendMessage: jest.Mock;
    skipCurrentQuestion: jest.Mock;
    generateFromSession: jest.Mock;
    abandonSession: jest.Mock;
  };

  beforeEach(() => {
    gameService = {
      create: jest.fn(),
      iterate: jest.fn(),
    };
    reputationService = {
      getReputation: jest.fn(),
    };
    creationSessionRealtimeService = {
      streamSession: jest.fn(),
    };
    creationSessionService = {
      createSession: jest.fn(),
      getActiveSession: jest.fn(),
      getSession: jest.fn(),
      appendMessage: jest.fn(),
      skipCurrentQuestion: jest.fn(),
      generateFromSession: jest.fn(),
      abandonSession: jest.fn(),
    };

    controller = new GameController(
      gameService as any,
      creationSessionService as any,
      creationSessionRealtimeService as any,
      reputationService as any,
    );
  });

  it('forwards timeoutS to game creation and returns the task payload', async () => {
    gameService.create.mockResolvedValue({
      gameId: 'game-1',
      status: 'generating',
      generationTask: {
        taskId: 'game-1:pipeline_run',
        timeoutS: 900,
      },
    });

    const result = await controller.generateGame(
      { user: { sub: 'user-1' } },
      {
        title: 'Runner',
        description: 'make a runner game',
        orientation: 'landscape',
        timeoutS: 900,
      } as any,
    );

    expect(gameService.create).toHaveBeenCalledWith('user-1', {
      title: 'Runner',
      description: 'make a runner game',
      orientation: 'landscape',
      timeoutS: 900,
    });
    expect(result).toEqual(
      expect.objectContaining({
        data: expect.objectContaining({
          gameId: 'game-1',
          generationTask: expect.objectContaining({
            timeoutS: 900,
          }),
        }),
      }),
    );
  });

  it('returns iterate task info together with iterationId', async () => {
    gameService.iterate.mockResolvedValue({
      gameId: 'game-2',
      version: 3,
      status: 'iterating',
      generationTask: {
        taskId: 'game-2:pipeline_iterate:v3',
        timeoutS: 600,
      },
    });

    const result = await controller.iterateGame(
      'game-2',
      { user: { sub: 'user-2' } },
      {
        feedback: 'make it faster',
        timeoutS: 600,
      } as any,
    );

    expect(gameService.iterate).toHaveBeenCalledWith('game-2', 'user-2', {
      feedback: 'make it faster',
      timeoutS: 600,
    });
    expect(result).toEqual(
      expect.objectContaining({
        data: expect.objectContaining({
          iterationId: 'game-2:v3',
          generationTask: expect.objectContaining({
            taskId: 'game-2:pipeline_iterate:v3',
          }),
        }),
      }),
    );
  });

  it('returns the supported game types from a static route handler', async () => {
    const result = await controller.getGameTypes();

    expect(result).toEqual(
      expect.objectContaining({
        data: ['casual', 'puzzle', 'education', 'funny'],
      }),
    );
  });

  it('creates a creation session for the current user', async () => {
    creationSessionService.createSession.mockResolvedValue({
      id: 'session-1',
      status: 'collecting',
    });

    const result = await controller.createCreationSession(
      { user: { sub: 'user-1' } },
      {
        prompt: '做一个办公室摸鱼游戏',
        orientation: 'portrait',
        generationTier: 'showcase',
      } as any,
    );

    expect(creationSessionService.createSession).toHaveBeenCalledWith('user-1', {
      prompt: '做一个办公室摸鱼游戏',
      orientation: 'portrait',
      generationTier: 'showcase',
    });
    expect(result).toEqual(
      expect.objectContaining({
        data: expect.objectContaining({
          id: 'session-1',
          status: 'collecting',
        }),
      }),
    );
  });

  it('forwards generate-from-session to the creation session service', async () => {
    creationSessionService.generateFromSession.mockResolvedValue({
      gameId: 'game-9',
      generationTask: {
        taskId: 'task-9',
      },
    });

    const result = await controller.generateFromCreationSession(
      { user: { sub: 'user-2' } },
      'session-2',
      {
        revision: 3,
        timeoutS: 900,
      } as any,
    );

    expect(creationSessionService.generateFromSession).toHaveBeenCalledWith('user-2', 'session-2', {
      revision: 3,
      timeoutS: 900,
    });
    expect(result).toEqual(
      expect.objectContaining({
        data: expect.objectContaining({
          gameId: 'game-9',
          generationTask: expect.objectContaining({
            taskId: 'task-9',
          }),
        }),
      }),
    );
  });

  it('streams creation session events through a raw SSE response and keeps the connection reusable', async () => {
    const stream = new Subject<any>();
    const writes: string[] = [];
    const requestListeners = new Map<string, () => void>();
    const responseListeners = new Map<string, () => void>();
    const req = {
      user: { sub: 'user-1' },
      on: jest.fn((event: string, handler: () => void) => {
        requestListeners.set(event, handler);
        return req;
      }),
      socket: {
        setKeepAlive: jest.fn(),
        setNoDelay: jest.fn(),
        setTimeout: jest.fn(),
      },
    } as any;
    const res = {
      writableEnded: false,
      status: jest.fn().mockReturnThis(),
      setHeader: jest.fn(),
      flushHeaders: jest.fn(),
      flush: jest.fn(),
      write: jest.fn((chunk: string) => {
        writes.push(String(chunk));
        return true;
      }),
      end: jest.fn(() => {
        res.writableEnded = true;
        return res;
      }),
      on: jest.fn((event: string, handler: () => void) => {
        responseListeners.set(event, handler);
        return res;
      }),
    } as any;

    creationSessionService.getSession.mockResolvedValue({
      id: 'session-1',
      status: 'initializing',
    });
    creationSessionRealtimeService.streamSession.mockReturnValue(stream.asObservable());

    await controller.streamCreationSessionEvents(req, res, 'session-1');

    stream.next({
      type: 'session.bootstrap',
      id: 'session-1:1:session.bootstrap',
      data: { sessionId: 'session-1', revision: 1 },
    });
    stream.next({
      type: 'assistant.reply.delta',
      id: 'session-1:2:assistant.reply.delta',
      data: { sessionId: 'session-1', delta: '第一段' },
    });
    stream.next({
      type: 'assistant.reply.done',
      id: 'session-1:3:assistant.reply.done',
      data: { sessionId: 'session-1', message: '第一轮完成' },
    });
    stream.next({
      type: 'assistant.reply.delta',
      id: 'session-1:4:assistant.reply.delta',
      data: { sessionId: 'session-1', delta: '第二轮继续' },
    });

    const payload = writes.join('');
    expect(res.status).toHaveBeenCalledWith(200);
    expect(res.setHeader).toHaveBeenCalledWith('Content-Type', 'text/event-stream; charset=utf-8');
    expect(payload).toContain(': sse-open');
    expect(payload).toContain('event: session.bootstrap');
    expect(payload).toContain('event: assistant.reply.delta');
    expect(payload).toContain('第一轮完成');
    expect(payload).toContain('第二轮继续');

    requestListeners.get('close')?.();
    expect(res.end).toHaveBeenCalled();
  });

  it('streams a minimal authenticated SSE probe immediately and closes cleanly', async () => {
    jest.useFakeTimers();

    const writes: string[] = [];
    const requestListeners = new Map<string, () => void>();
    const responseListeners = new Map<string, () => void>();
    const req = {
      user: { sub: 'user-9' },
      on: jest.fn((event: string, handler: () => void) => {
        requestListeners.set(event, handler);
        return req;
      }),
      socket: {
        setKeepAlive: jest.fn(),
        setNoDelay: jest.fn(),
        setTimeout: jest.fn(),
      },
    } as any;
    const res = {
      writableEnded: false,
      status: jest.fn().mockReturnThis(),
      setHeader: jest.fn(),
      flushHeaders: jest.fn(),
      flush: jest.fn(),
      write: jest.fn((chunk: string) => {
        writes.push(String(chunk));
        return true;
      }),
      end: jest.fn(() => {
        res.writableEnded = true;
        return res;
      }),
      on: jest.fn((event: string, handler: () => void) => {
        responseListeners.set(event, handler);
        return res;
      }),
    } as any;

    await controller.streamSseProbe(req, res);

    jest.advanceTimersByTime(600);

    const payload = writes.join('');
    expect(payload).toContain(': sse-open');
    expect(payload).toContain('event: probe.ready');
    expect(payload).toContain('event: probe.tick');
    expect(payload).toContain('event: probe.done');
    expect(res.end).toHaveBeenCalled();

    responseListeners.get('close')?.();
    requestListeners.get('close')?.();
    jest.useRealTimers();
  });
});
