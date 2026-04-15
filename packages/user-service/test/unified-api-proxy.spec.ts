import { EventEmitter } from 'events';
import {
  forwardUpstreamResponse,
  getProxyTargets,
  resolveAiEngineProxyBaseUrl,
  shouldStreamUpstreamResponse,
} from '../src/edge/unified-api-proxy';

function createMockResponse() {
  const headers = new Map<string, string>();
  const written: string[] = [];
  const sent: Buffer[] = [];

  const res: any = {
    headersSent: false,
    writableEnded: false,
    statusCode: 200,
    setHeader: jest.fn((key: string, value: string) => {
      headers.set(key.toLowerCase(), value);
    }),
    status: jest.fn((code: number) => {
      res.statusCode = code;
      return res;
    }),
    write: jest.fn((chunk: Buffer | string, callback?: (error?: Error | null) => void) => {
      written.push(Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk));
      if (callback) {
        callback(null);
      }
      return true;
    }),
    send: jest.fn((body: Buffer) => {
      sent.push(body);
      res.headersSent = true;
      res.writableEnded = true;
      return res;
    }),
    end: jest.fn(() => {
      res.headersSent = true;
      res.writableEnded = true;
      return res;
    }),
    flushHeaders: jest.fn(),
    flush: jest.fn(),
  };

  return { res, headers, written, sent };
}

describe('resolveAiEngineProxyBaseUrl', () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env.AI_ENGINE_DEFAULT_REGION;
    delete process.env.SERVICE_REGION;
    delete process.env.AI_ENGINE_URL;
    delete process.env.AI_ENGINE_URL_CN_SHANGHAI;
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  it('prefers the China region-specific AI engine URL by default', () => {
    process.env.AI_ENGINE_URL = 'https://legacy-ai.example.com';
    process.env.AI_ENGINE_URL_CN_SHANGHAI = 'https://ai-cn.example.com';

    expect(resolveAiEngineProxyBaseUrl()).toBe('https://ai-cn.example.com');
  });

  it('keeps using the China region-specific AI engine URL even when a legacy region hint is present', () => {
    process.env.AI_ENGINE_DEFAULT_REGION = 'legacy_region';
    process.env.AI_ENGINE_URL_CN_SHANGHAI = 'https://ai-cn.example.com';

    expect(resolveAiEngineProxyBaseUrl()).toBe('https://ai-cn.example.com');
  });

  it('falls back to the legacy AI engine URL when region-specific values are absent', () => {
    process.env.AI_ENGINE_URL = 'https://legacy-ai.example.com/';

    expect(resolveAiEngineProxyBaseUrl()).toBe('https://legacy-ai.example.com');
  });

  it('returns null when no AI engine upstream is configured', () => {
    expect(resolveAiEngineProxyBaseUrl()).toBeNull();
  });

  it('keeps supporting the stripped /ai proxy path after the global prefix is removed upstream', () => {
    process.env.AI_ENGINE_URL_CN_SHANGHAI = 'https://ai-cn.example.com';

    const aiTarget = getProxyTargets().find((target) => target.name === 'ai-engine');
    expect(aiTarget?.prefixes).toContain('/ai');
  });
});

describe('shouldStreamUpstreamResponse', () => {
  it('returns true for text/event-stream responses', () => {
    const upstreamRes = new Response('', {
      headers: {
        'content-type': 'text/event-stream; charset=utf-8',
      },
    });

    expect(shouldStreamUpstreamResponse(upstreamRes)).toBe(true);
  });

  it('returns false for normal json responses', () => {
    const upstreamRes = new Response('{}', {
      headers: {
        'content-type': 'application/json; charset=utf-8',
      },
    });

    expect(shouldStreamUpstreamResponse(upstreamRes)).toBe(false);
  });
});

describe('forwardUpstreamResponse', () => {
  it('streams SSE chunks instead of buffering the whole response', async () => {
    const encoder = new TextEncoder();
    const upstreamRes = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode('event: probe.ready\n'));
          controller.enqueue(encoder.encode('data: {"ok":true}\n\n'));
          controller.close();
        },
      }),
      {
        status: 200,
        headers: {
          'content-type': 'text/event-stream; charset=utf-8',
          'cache-control': 'no-cache, no-transform',
          'x-accel-buffering': 'no',
        },
      },
    );
    const req = new EventEmitter() as any;
    const { res, headers, written, sent } = createMockResponse();

    await forwardUpstreamResponse(req, res, upstreamRes);

    expect(res.status).toHaveBeenCalledWith(200);
    expect(res.flushHeaders).toHaveBeenCalled();
    expect(res.write).toHaveBeenCalled();
    expect(res.send).not.toHaveBeenCalled();
    expect(res.end).toHaveBeenCalled();
    expect(sent).toHaveLength(0);
    expect(written.join('')).toContain('event: probe.ready');
    expect(written.join('')).toContain('data: {"ok":true}');
    expect(headers.get('content-type')).toBe('text/event-stream; charset=utf-8');
    expect(headers.get('cache-control')).toBe('no-cache, no-transform');
    expect(headers.get('x-accel-buffering')).toBe('no');
  });

  it('keeps buffering normal non-stream responses', async () => {
    const upstreamRes = new Response('{"ok":true}', {
      status: 201,
      headers: {
        'content-type': 'application/json; charset=utf-8',
      },
    });
    const req = new EventEmitter() as any;
    const { res, sent, written } = createMockResponse();

    await forwardUpstreamResponse(req, res, upstreamRes);

    expect(res.status).toHaveBeenCalledWith(201);
    expect(res.send).toHaveBeenCalledTimes(1);
    expect(res.write).not.toHaveBeenCalled();
    expect(written).toHaveLength(0);
    expect(Buffer.concat(sent).toString('utf8')).toBe('{"ok":true}');
  });
});
