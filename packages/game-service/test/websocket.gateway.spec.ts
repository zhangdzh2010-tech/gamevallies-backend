import 'reflect-metadata';
import { GATEWAY_OPTIONS } from '@nestjs/websockets/constants';
import { GameWebSocketGateway } from '../src/websocket/websocket.gateway';

describe('GameWebSocketGateway', () => {
  let gateway: GameWebSocketGateway;
  let emitMock: jest.Mock;
  let toMock: jest.Mock;

  beforeEach(() => {
    gateway = new GameWebSocketGateway();
    emitMock = jest.fn();
    toMock = jest.fn().mockReturnValue({ emit: emitMock });
    gateway.server = {
      to: toMock,
      emit: jest.fn(),
    } as any;
  });

  it('uses the public websocket route prefix for Socket.IO handshakes', () => {
    const options = Reflect.getMetadata(GATEWAY_OPTIONS, GameWebSocketGateway);

    expect(options).toEqual(expect.objectContaining({
      namespace: '/ws',
      path: '/ws/socket.io',
    }));
  });

  it('joins the user room when token payload contains sub', () => {
    const payload = Buffer.from(JSON.stringify({ sub: 'user-123' })).toString(
      'base64',
    );
    const client = {
      id: 'client-1',
      handshake: {
        query: {
          token: `header.${payload}.sig`,
        },
      },
      join: jest.fn(),
      disconnect: jest.fn(),
    };

    gateway.handleConnection(client as any);

    expect(client.join).toHaveBeenCalledWith('user:user-123');
    expect(client.disconnect).not.toHaveBeenCalled();
  });

  it('disconnects clients whose token payload does not contain a user id', () => {
    const payload = Buffer.from(JSON.stringify({ foo: 'bar' })).toString(
      'base64',
    );
    const client = {
      id: 'client-2',
      handshake: {
        query: {
          token: `header.${payload}.sig`,
        },
      },
      join: jest.fn(),
      disconnect: jest.fn(),
    };

    gateway.handleConnection(client as any);

    expect(client.disconnect).toHaveBeenCalled();
    expect(client.join).not.toHaveBeenCalled();
  });

  it('emits generation progress with stage code and original details', () => {
    gateway.emitGenerationProgress(
      'user-456',
      'game-456',
      '代码生成失败，重试中（1/2）',
      60,
      {
        stage: 'code_generating',
        retry: 1,
        maxRetries: 2,
      },
    );

    expect(toMock).toHaveBeenCalledWith('user:user-456');
    expect(emitMock).toHaveBeenCalledWith(
      'gen:progress',
      expect.objectContaining({
        type: 'gen:progress',
        gameId: 'game-456',
        stage: 'code_generating',
        percentage: 60,
        details: expect.objectContaining({
          stage: 'code_generating',
          retry: 1,
          maxRetries: 2,
        }),
        data: expect.objectContaining({
          progress: 60,
          message: '代码生成失败，重试中（1/2）',
        }),
      }),
    );
  });

  it('emits a dedicated gen:error event for terminal generation failures', () => {
    gateway.emitGenerationError('user-789', 'game-789', 'Generated code failed QA', {
      stage: 'qa_checking',
      retryCount: 3,
    });

    expect(toMock).toHaveBeenCalledWith('user:user-789');
    expect(emitMock).toHaveBeenCalledWith(
      'gen:error',
      expect.objectContaining({
        type: 'gen:error',
        gameId: 'game-789',
        stage: 'qa_checking',
        status: 'error',
        data: expect.objectContaining({
          success: false,
          error: 'Generated code failed QA',
        }),
        details: expect.objectContaining({
          stage: 'qa_checking',
          retryCount: 3,
        }),
      }),
    );
  });
});
