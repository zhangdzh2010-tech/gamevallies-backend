import { INestApplication, Logger } from '@nestjs/common';
import type { NextFunction, Request, Response } from 'express';

type ProxyTarget = {
  name: string;
  envKey: string;
  upstreamBaseUrl: string | null;
  prefixes: string[];
};

const REQUEST_HEADERS_TO_SKIP = new Set([
  'connection',
  'content-length',
  'host',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

const RESPONSE_HEADERS_TO_SKIP = new Set([
  'access-control-allow-credentials',
  'access-control-allow-headers',
  'access-control-allow-methods',
  'access-control-allow-origin',
  'connection',
  'content-encoding',
  'content-length',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

function normalizeBaseUrl(value: string | undefined): string | null {
  if (!value) {
    return null;
  }

  const trimmed = value.trim().replace(/\/$/, '');
  return trimmed || null;
}

function normalizeExecutionRegion(value: string | undefined): 'cn_shanghai' | 'ap_southeast_johor' {
  return (value || '').trim() === 'ap_southeast_johor' ? 'ap_southeast_johor' : 'cn_shanghai';
}

export function resolveAiEngineProxyBaseUrl(): string | null {
  const defaultRegion = normalizeExecutionRegion(
    process.env.AI_ENGINE_DEFAULT_REGION || process.env.SERVICE_REGION || 'cn_shanghai',
  );
  const regionSpecificUrl = defaultRegion === 'ap_southeast_johor'
    ? process.env.AI_ENGINE_URL_AP_SOUTHEAST_JOHOR
    : process.env.AI_ENGINE_URL_CN_SHANGHAI;

  return normalizeBaseUrl(regionSpecificUrl) || normalizeBaseUrl(process.env.AI_ENGINE_URL);
}

function matchesPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

function findProxyTarget(pathname: string, targets: ProxyTarget[]): ProxyTarget | undefined {
  return targets.find((target) => target.prefixes.some((prefix) => matchesPrefix(pathname, prefix)));
}

async function readRequestBody(req: Request): Promise<Uint8Array | undefined> {
  if (req.method === 'GET' || req.method === 'HEAD') {
    return undefined;
  }

  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];

    req.on('data', (chunk: Buffer | string) => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });

    req.on('end', () => {
      resolve(chunks.length > 0 ? new Uint8Array(Buffer.concat(chunks)) : undefined);
    });

    req.on('error', reject);
  });
}

function buildForwardHeaders(req: Request): Record<string, string> {
  const headers: Record<string, string> = {};

  for (const [key, value] of Object.entries(req.headers)) {
    const normalizedKey = key.toLowerCase();
    if (REQUEST_HEADERS_TO_SKIP.has(normalizedKey) || value === undefined) {
      continue;
    }

    headers[key] = Array.isArray(value) ? value.join(', ') : value;
  }

  if (req.headers.host) {
    headers['x-forwarded-host'] = req.headers.host;
  }
  if (req.ip) {
    headers['x-forwarded-for'] = req.ip;
  }
  headers['x-forwarded-proto'] = req.protocol;

  return headers;
}

function applyResponseHeaders(res: Response, upstreamRes: globalThis.Response): void {
  upstreamRes.headers.forEach((value, key) => {
    if (RESPONSE_HEADERS_TO_SKIP.has(key.toLowerCase())) {
      return;
    }

    res.setHeader(key, value);
  });
}

function buildTargetUrl(upstreamBaseUrl: string, originalUrl: string): string {
  return new URL(originalUrl, `${upstreamBaseUrl}/`).toString();
}

export function getProxyTargets(): ProxyTarget[] {
  return [
    {
      name: 'game-service',
      envKey: 'GAME_SERVICE_UPSTREAM_URL',
      upstreamBaseUrl: normalizeBaseUrl(process.env.GAME_SERVICE_UPSTREAM_URL),
      prefixes: ['/api/v1/games', '/games', '/admin', '/api/v1/admin'],
    },
    {
      name: 'feed-service',
      envKey: 'FEED_SERVICE_UPSTREAM_URL',
      upstreamBaseUrl: normalizeBaseUrl(process.env.FEED_SERVICE_UPSTREAM_URL),
      prefixes: [
        '/api/v1/feed',
        '/api/v1/social',
        '/api/v1/comments',
        '/api/v1/notifications',
        '/api/v1/tags',
        '/api/v1/challenges',
        '/api/v1/creators',
      ],
    },
    {
      name: 'ai-engine',
      envKey: 'AI_ENGINE_URL_CN_SHANGHAI|AI_ENGINE_URL_AP_SOUTHEAST_JOHOR|AI_ENGINE_URL',
      upstreamBaseUrl: resolveAiEngineProxyBaseUrl(),
      prefixes: ['/api/v1/ai', '/ai'],
    },
  ];
}

async function handleProxy(
  req: Request,
  res: Response,
  next: NextFunction,
  targets: ProxyTarget[],
  logger: Logger,
): Promise<void> {
  const pathname = req.path || req.originalUrl || '';
  const target = findProxyTarget(pathname, targets);

  if (!target) {
    next();
    return;
  }

  if (!target.upstreamBaseUrl) {
    res.status(503).json({
      message: `${target.name} upstream is not configured`,
      missingEnv: target.envKey,
    });
    return;
  }

  try {
    const requestBody = await readRequestBody(req);
    const requestInit: RequestInit = {
      method: req.method,
      headers: buildForwardHeaders(req),
      redirect: 'manual',
    };

    if (requestBody) {
      requestInit.body = requestBody as unknown as BodyInit;
    }

    const upstreamRes = await fetch(buildTargetUrl(target.upstreamBaseUrl, req.originalUrl), {
      ...requestInit,
    });

    applyResponseHeaders(res, upstreamRes);
    res.status(upstreamRes.status);

    const responseBody = Buffer.from(await upstreamRes.arrayBuffer());
    if (responseBody.length === 0) {
      res.end();
      return;
    }

    res.send(responseBody);
  } catch (error) {
    logger.error(
      `Failed to proxy ${req.method} ${req.originalUrl} to ${target.name}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );

    if (!res.headersSent) {
      res.status(502).json({
        message: 'Upstream service unavailable',
        target: target.name,
      });
    }
  }
}

export function registerUnifiedApiProxy(app: INestApplication): void {
  const logger = new Logger('UnifiedApiProxy');
  const targets = getProxyTargets();
  const enabledTargets = targets
    .filter((target) => target.upstreamBaseUrl)
    .map((target) => `${target.name} -> ${target.upstreamBaseUrl}`);

  if (enabledTargets.length > 0) {
    logger.log(`Enabled unified API proxy: ${enabledTargets.join(', ')}`);
  }

  app.use((req: Request, res: Response, next: NextFunction) => {
    if (req.method === 'OPTIONS') {
      next();
      return;
    }

    void handleProxy(req, res, next, targets, logger);
  });
}
