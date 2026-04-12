#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const http = require('node:http');
const https = require('node:https');
const { URL } = require('node:url');

const REPO_ROOT = path.resolve(__dirname, '..');
const DEFAULT_ENV_PATH = path.join(REPO_ROOT, '.env.deploy');
const BOOTSTRAP_EVENTS = new Set(['bootstrap']);
const DONE_EVENTS = new Set(['done']);

function nowIso() {
  return new Date().toISOString();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const raw = argv[i];
    if (!raw.startsWith('--')) {
      continue;
    }
    const key = raw.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith('--')) {
      args[key] = true;
      continue;
    }
    args[key] = next;
    i += 1;
  }
  return args;
}

function loadEnv(filePath) {
  const values = {};
  if (!fs.existsSync(filePath)) {
    return values;
  }
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/u);
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) {
      continue;
    }
    const separator = line.indexOf('=');
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim().replace(/^['"]|['"]$/gu, '');
    if (key) {
      values[key] = value;
    }
  }
  return values;
}

function buildUrl(baseUrl, pathname) {
  return new URL(pathname, `${String(baseUrl || '').replace(/\/$/u, '')}/`).toString();
}

async function httpJson(method, url, { headers = {}, payload, timeoutMs = 30000 } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const requestHeaders = { ...headers };
    let body;
    if (payload !== undefined) {
      requestHeaders['Content-Type'] = requestHeaders['Content-Type'] || 'application/json; charset=utf-8';
      body = JSON.stringify(payload);
    }
    const response = await fetch(url, {
      method: method.toUpperCase(),
      headers: requestHeaders,
      body,
      signal: controller.signal,
    });
    const text = await response.text();
    let parsed = null;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} ${url}: ${typeof parsed === 'string' ? parsed : JSON.stringify(parsed)}`);
    }
    return parsed;
  } finally {
    clearTimeout(timeout);
  }
}

async function login(baseUrl, username, password) {
  const response = await httpJson('POST', buildUrl(baseUrl, '/api/v1/auth/login'), {
    payload: { account: username, password },
    timeoutMs: 30000,
  });
  const token = (((response || {}).data || {}).token || '').trim();
  if (!token) {
    throw new Error(`Login succeeded without token: ${JSON.stringify(response)}`);
  }
  return token;
}

async function getSession(baseUrl, token, sessionId) {
  const response = await httpJson('GET', buildUrl(baseUrl, `/api/v1/games/creation-sessions/${sessionId}`), {
    headers: { Authorization: `Bearer ${token}` },
    timeoutMs: 60000,
  });
  return ((response || {}).data || {});
}

async function createTempUser(baseUrl, adminToken, username, password) {
  const response = await httpJson('POST', buildUrl(baseUrl, '/api/v1/admin/users'), {
    headers: { 'x-admin-token': adminToken },
    payload: {
      username,
      displayName: 'Creation SSE Verify',
      email: `${username}@example.com`,
      password,
    },
    timeoutMs: 30000,
  });
  return (((response || {}).data || {}).id || '').trim();
}

async function deleteUser(baseUrl, adminToken, userId) {
  if (!userId) {
    return null;
  }
  return httpJson('DELETE', buildUrl(baseUrl, `/api/v1/admin/users/${userId}`), {
    headers: { 'x-admin-token': adminToken },
    timeoutMs: 30000,
  });
}

function previewPayload(data) {
  if (data == null) {
    return undefined;
  }
  if (typeof data === 'string') {
    return data.slice(0, 280);
  }
  try {
    return JSON.stringify(data).slice(0, 280);
  } catch {
    return String(data).slice(0, 280);
  }
}

function parseSseFrame(frame) {
  let eventType = 'message';
  let eventId = null;
  let comment = null;
  const dataLines = [];

  for (const rawLine of frame.split('\n')) {
    const line = rawLine.replace(/\r$/u, '');
    if (!line) {
      continue;
    }
    if (line.startsWith(':')) {
      comment = line.slice(1).trim();
      continue;
    }
    const separator = line.indexOf(':');
    if (separator < 0) {
      continue;
    }
    const field = line.slice(0, separator);
    const value = line.slice(separator + 1).replace(/^ /u, '');
    if (field === 'event') {
      eventType = value || 'message';
    } else if (field === 'id') {
      eventId = value || null;
    } else if (field === 'data') {
      dataLines.push(value);
    }
  }

  const payloadRaw = dataLines.join('\n');
  let payload = null;
  if (payloadRaw) {
    try {
      payload = JSON.parse(payloadRaw);
    } catch {
      payload = payloadRaw;
    }
  }

  return {
    event: eventType,
    id: eventId,
    comment,
    data: payload,
  };
}

function answerForSlot(caseData, slotKey, prompt) {
  if (slotKey && caseData[slotKey]) {
    return String(caseData[slotKey]);
  }
  const lowered = String(prompt || '').toLowerCase();
  if (lowered.includes('input') || lowered.includes('control')) {
    return String(caseData.input_method || 'tap');
  }
  if (lowered.includes('theme')) {
    return String(caseData.theme || 'funny office puzzle');
  }
  if (lowered.includes('win') || lowered.includes('goal')) {
    return String(caseData.win_condition || 'clear the major layers within a limited move budget');
  }
  if (lowered.includes('difficulty')) {
    return String(caseData.difficulty || 'medium');
  }
  if (lowered.includes('mechanic') || lowered.includes('gameplay')) {
    return String(caseData.core_mechanic || 'tap to match and clear three identical tiles');
  }
  return null;
}

function openSse(url, headers, metrics, options = {}) {
  const connectTimeoutMs = options.connectTimeoutMs || 10000;
  const readTimeoutMs = options.readTimeoutMs || 240000;
  const transport = url.startsWith('https:') ? https : http;
  const startedAt = process.hrtime.bigint();
  const state = {
    buffer: '',
    closed: false,
    manualClose: false,
  };
  const listeners = {
    event: new Set(),
    error: new Set(),
    close: new Set(),
  };

  const emit = (type, payload) => {
    for (const handler of listeners[type]) {
      try {
        handler(payload);
      } catch {
        // Ignore observer failures during verification.
      }
    }
  };

  const request = transport.request(url, {
    method: 'GET',
    headers,
  });

  const setElapsed = () => Number(process.hrtime.bigint() - startedAt) / 1_000_000;

  request.setTimeout(connectTimeoutMs, () => {
    request.destroy(new Error(`SSE connect timeout after ${connectTimeoutMs}ms`));
  });

  request.on('response', (response) => {
    metrics.responseStatus = response.statusCode || null;
    metrics.headersReceivedMs = Number(setElapsed().toFixed(1));
    metrics.responseHeaders = Object.fromEntries(
      Object.entries(response.headers || {}).map(([key, value]) => [key.toLowerCase(), Array.isArray(value) ? value.join(', ') : String(value ?? '')]),
    );

    response.setEncoding('utf8');
    response.setTimeout(readTimeoutMs, () => {
      response.destroy(new Error(`SSE read timeout after ${readTimeoutMs}ms`));
    });

    response.on('data', (chunk) => {
      if (metrics.firstChunkMs == null) {
        metrics.firstChunkMs = Number(setElapsed().toFixed(1));
      }
      state.buffer += chunk.replace(/\r\n/gu, '\n');
      while (state.buffer.includes('\n\n')) {
        const separator = state.buffer.indexOf('\n\n');
        const frame = state.buffer.slice(0, separator);
        state.buffer = state.buffer.slice(separator + 2);
        if (!frame.trim()) {
          continue;
        }
        const parsed = parseSseFrame(frame);
        const elapsedMs = Number(setElapsed().toFixed(1));
        if (metrics.firstEventMs == null && parsed.event !== 'message') {
          metrics.firstEventMs = elapsedMs;
        }
        emit('event', {
          event: parsed.event,
          id: parsed.id,
          comment: parsed.comment,
          elapsedMs,
          receivedAt: nowIso(),
          data: parsed.data,
        });
      }
    });

    response.on('end', () => {
      state.closed = true;
      emit('close');
    });

    response.on('error', (error) => {
      state.closed = true;
      if (state.manualClose) {
        emit('close');
        return;
      }
      emit('error', error);
    });
  });

  request.on('error', (error) => {
    state.closed = true;
    if (state.manualClose) {
      emit('close');
      return;
    }
    emit('error', error);
  });

  request.end();

  return {
    on(type, handler) {
      listeners[type].add(handler);
      return () => listeners[type].delete(handler);
    },
    close() {
      if (state.closed) {
        return;
      }
      state.manualClose = true;
      state.closed = true;
      request.destroy();
    },
  };
}

function inferSuspectedLayer(result) {
  const authProbe = result.authProbe || {};
  const authLongProbe = result.authLongProbe || {};
  const sse = result.sse || {};
  const sessionAfterFirstRound = result.sessionAfterFirstRound || {};
  const readyReconnect = result.readyReconnect || {};
  const readySnapshot = readyReconnect.readySnapshot || {};
  const sessionProgressed = Boolean(
    sessionAfterFirstRound.status
    || readySnapshot.status
    || (sessionAfterFirstRound.revision != null && sessionAfterFirstRound.revision >= 2)
    || (readySnapshot.revision != null && readySnapshot.revision >= 2),
  );
  const hasCreationSessionTimeout = /timeout/i.test(String(sse.streamError || ''));
  const hasLongProbeTimeout = /timeout/i.test(String(authLongProbe.streamError || ''));

  if (sse.eventsArrivedIncrementally && authLongProbe.eventsArrivedIncrementally) {
    return {
      suspectedLayer: 'passed',
      suspectedLayerReason: 'Short probe, long probe, and creation-session stream all arrived incrementally.',
    };
  }

  if (
    authProbe.responseStatus === 200
    && authProbe.firstEventMs != null
    && authProbe.eventsArrivedIncrementally === false
    && (hasLongProbeTimeout || authLongProbe.responseStatus == null)
  ) {
    return {
      suspectedLayer: 'gateway_transport',
      suspectedLayerReason: 'Authenticated short probe is buffered while the long probe never establishes a usable live stream.',
    };
  }

  if (
    authLongProbe.eventsArrivedIncrementally
    && (hasCreationSessionTimeout || sse.responseStatus == null)
    && sessionProgressed
  ) {
    return {
      suspectedLayer: 'game_service_stream',
      suspectedLayerReason: 'Gateway-level probe streams, but creation-session SSE still times out while session state keeps progressing.',
    };
  }

  if ((hasCreationSessionTimeout || sse.responseStatus == null) && !sessionProgressed) {
    return {
      suspectedLayer: 'session_or_ai_pipeline',
      suspectedLayerReason: 'The creation session stream never became usable and the session itself did not progress to the next state.',
    };
  }

  return {
    suspectedLayer: 'unknown',
    suspectedLayerReason: 'Signals are mixed; inspect the saved result JSON together with the gateway audit output.',
  };
}

async function waitFor(condition, timeoutMs, intervalMs = 250) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (condition()) {
      return true;
    }
    await sleep(intervalMs);
  }
  return condition();
}

async function waitForSessionReady(baseUrl, token, sessionId, timeoutMs, pollIntervalMs) {
  const startedAt = Date.now();
  let latest = null;
  while (Date.now() - startedAt < timeoutMs) {
    latest = await getSession(baseUrl, token, sessionId);
    if (latest.readyToGenerate || latest.currentQuestion || String(latest.status || '').toLowerCase() === 'ready') {
      return latest;
    }
    await sleep(pollIntervalMs);
  }
  return latest;
}

async function probeReadySessionSse(baseUrl, token, sessionId, connectTimeoutMs, readTimeoutMs) {
  const metrics = {
    attempted: true,
    responseStatus: null,
    headersReceivedMs: null,
    firstChunkMs: null,
    firstEventMs: null,
    responseHeaders: {},
    bootstrapSeen: false,
    streamError: null,
    eventCounts: {},
  };

  const stream = openSse(buildUrl(baseUrl, `/api/v1/games/creation-sessions/${sessionId}/events`), {
    Authorization: `Bearer ${token}`,
    Accept: 'text/event-stream',
    'Cache-Control': 'no-cache',
  }, metrics, { connectTimeoutMs, readTimeoutMs });

  stream.on('event', (event) => {
    metrics.eventCounts[event.event] = (metrics.eventCounts[event.event] || 0) + 1;
    if (BOOTSTRAP_EVENTS.has(event.event)) {
      metrics.bootstrapSeen = true;
    }
  });

  stream.on('error', (error) => {
    metrics.streamError = error && error.message ? error.message : String(error);
  });

  const settled = await waitFor(
    () => Boolean(metrics.responseStatus || metrics.streamError || metrics.bootstrapSeen),
    Math.max(connectTimeoutMs, readTimeoutMs),
    200,
  );
  if (!settled && !metrics.streamError && !metrics.responseStatus && !metrics.bootstrapSeen) {
    metrics.streamError = `No SSE response headers or events within ${Math.max(connectTimeoutMs, readTimeoutMs)}ms`;
  }
  stream.close();
  return metrics;
}

async function probeAuthenticatedSse(
  baseUrl,
  token,
  connectTimeoutMs,
  readTimeoutMs,
  { durationMs = 500, tickMs = 200 } = {},
) {
  const metrics = {
    attempted: true,
    responseStatus: null,
    headersReceivedMs: null,
    firstChunkMs: null,
    firstEventMs: null,
    responseHeaders: {},
    probeReadySeen: false,
    probeDoneSeen: false,
    streamError: null,
    eventCounts: {},
    durationMs,
    tickMs,
    samples: [],
  };

  const stream = openSse(buildUrl(baseUrl, `/api/v1/games/sse-probe?durationMs=${encodeURIComponent(String(durationMs))}&tickMs=${encodeURIComponent(String(tickMs))}`), {
    Authorization: `Bearer ${token}`,
    Accept: 'text/event-stream',
    'Cache-Control': 'no-cache',
  }, metrics, { connectTimeoutMs, readTimeoutMs });

  stream.on('event', (event) => {
    metrics.eventCounts[event.event] = (metrics.eventCounts[event.event] || 0) + 1;
    if (metrics.samples.length < 4) {
      metrics.samples.push({
        event: event.event,
        elapsedMs: event.elapsedMs,
        preview: previewPayload(event.data),
      });
    }
    if (event.event === 'probe.ready') {
      metrics.probeReadySeen = true;
    }
    if (event.event === 'probe.done') {
      metrics.probeDoneSeen = true;
    }
  });

  stream.on('error', (error) => {
    metrics.streamError = error && error.message ? error.message : String(error);
  });

  const settled = await waitFor(
    () => Boolean(metrics.streamError || metrics.probeDoneSeen),
    Math.max(connectTimeoutMs, readTimeoutMs),
    100,
  );
  if (!settled && !metrics.streamError && !metrics.probeDoneSeen) {
    metrics.streamError = `No probe SSE response headers or events within ${Math.max(connectTimeoutMs, readTimeoutMs)}ms`;
  }
  metrics.eventsArrivedIncrementally = Boolean(
    metrics.firstEventMs != null
      && metrics.firstEventMs < durationMs
      && !('content-length' in metrics.responseHeaders),
  );
  stream.close();
  return metrics;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const env = loadEnv(args['env-file'] || DEFAULT_ENV_PATH);
  const baseUrl = String(args['base-url'] || env.PUBLIC_API_BASE_URL || '').replace(/\/$/u, '');
  const sessionBaseUrl = String(args['session-base-url'] || args['stream-base-url'] || baseUrl).replace(/\/$/u, '');
  const adminToken = String(args['admin-token'] || env.ADMIN_TOKEN || '');
  const outputPath = path.resolve(args.output || path.join(REPO_ROOT, `tmp_verify_creation_session_sse_${new Date().toISOString().replace(/[-:TZ.]/gu, '').slice(0, 14)}.json`));
  const connectTimeoutMs = Number(args['connect-timeout-ms'] || 10000);
  const readTimeoutMs = Number(args['read-timeout-ms'] || 240000);
  const firstRoundWaitMs = Number(args['first-round-wait-ms'] || 120000);
  const secondRoundWaitMs = Number(args['second-round-wait-ms'] || 120000);
  const readyPollMs = Number(args['ready-poll-ms'] || 180000);
  const readyPollIntervalMs = Number(args['ready-poll-interval-ms'] || 1000);
  const readyReconnectTimeoutMs = Number(args['ready-reconnect-timeout-ms'] || 20000);
  const keepUser = Boolean(args['keep-user']);

  if (!baseUrl) {
    throw new Error('PUBLIC_API_BASE_URL is required');
  }
  if (!sessionBaseUrl) {
    throw new Error('session base URL is required');
  }

  const suffix = `${Date.now()}_${crypto.randomBytes(3).toString('hex')}`;
  const username = String(args.username || `sse_verify_${suffix}`);
  const password = String(args.password || `CodexSSE!${Date.now()}`);
  const shouldCreateUser = !args.username && !args.password;

  const result = {
    startedAt: nowIso(),
    baseUrl,
    sessionBaseUrl,
    username,
    createdTempUser: false,
    sessionId: null,
    createUser: null,
    authProbe: null,
    authLongProbe: null,
    createSession: null,
    sse: {
      connectedAt: null,
      responseStatus: null,
      headersReceivedMs: null,
      firstChunkMs: null,
      firstEventMs: null,
      responseHeaders: {},
      events: [],
      eventsArrivedIncrementally: false,
      eventCounts: {},
      firstRoundDoneMs: null,
      secondRoundDoneMs: null,
      bootstrapSeen: false,
      firstRoundDoneSeen: false,
      secondRoundDoneSeen: false,
      secondRoundSeenOnSameStream: false,
      streamError: null,
    },
    sessionAfterFirstRound: null,
    secondTurn: null,
    readyReconnect: null,
    cleanup: null,
  };

  let createdUserId = null;
  let token = null;
  let sseStream = null;

  try {
    if (shouldCreateUser) {
      if (!adminToken) {
        throw new Error('ADMIN_TOKEN is required when no username/password are provided');
      }
      createdUserId = await createTempUser(baseUrl, adminToken, username, password);
      result.createdTempUser = true;
      result.createUser = { ok: Boolean(createdUserId), userId: createdUserId };
    }

    token = await login(baseUrl, username, password);
    const jsonHeaders = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json; charset=utf-8' };
    result.authProbe = await probeAuthenticatedSse(
      sessionBaseUrl,
      token,
      connectTimeoutMs,
      Math.min(readTimeoutMs, 15000),
    );
    result.authLongProbe = await probeAuthenticatedSse(
      sessionBaseUrl,
      token,
      connectTimeoutMs,
      Math.max(connectTimeoutMs + 4000, Math.min(readTimeoutMs, 20000)),
      {
        durationMs: Math.max(connectTimeoutMs + 2000, 12000),
        tickMs: 1000,
      },
    );

    const caseData = {
      title: `SSE Verify ${suffix}`,
      prompt: 'Make a polished mobile puzzle inspired by a layered tile-clearing hit game.',
      orientation: 'portrait',
      generationTier: 'standard',
      game_type: 'puzzle',
      core_mechanic: 'Tap to clear three matching tiles and gradually unlock covered layers.',
      theme: 'funny office puzzle',
      input_method: 'tap',
      win_condition: 'Clear the main stack within a limited move budget.',
      difficulty: 'medium',
    };

    const createSessionResponse = await httpJson('POST', buildUrl(sessionBaseUrl, '/api/v1/games/creation-sessions'), {
      headers: jsonHeaders,
      payload: {
        prompt: caseData.prompt,
        title: caseData.title,
        regionHint: 'cn_shanghai',
        orientation: caseData.orientation,
        generationTier: caseData.generationTier,
        entryMode: 'create',
      },
      timeoutMs: 60000,
    });
    const createdSnapshot = ((createSessionResponse || {}).data || {});
    const sessionId = createdSnapshot.id;
    if (!sessionId) {
      throw new Error(`Creation session did not return id: ${JSON.stringify(createSessionResponse)}`);
    }
    result.sessionId = sessionId;
    result.createSession = {
      status: createdSnapshot.status || null,
      revision: createdSnapshot.revision || null,
      streamPath: createdSnapshot.streamPath || null,
    };

    const firstRoundDone = { value: false };
    const secondRoundDone = { value: false };
    const bootstrapSeen = { value: false };

    result.sse.connectedAt = nowIso();
    sseStream = openSse(buildUrl(sessionBaseUrl, `/api/v1/games/creation-sessions/${sessionId}/events`), {
      Authorization: `Bearer ${token}`,
      Accept: 'text/event-stream',
      'Cache-Control': 'no-cache',
    }, result.sse, { connectTimeoutMs, readTimeoutMs });

    sseStream.on('event', (event) => {
      const record = {
        event: event.event,
        id: event.id,
        comment: event.comment,
        elapsedMs: event.elapsedMs,
        receivedAt: event.receivedAt,
        preview: previewPayload(event.data),
      };
      if (event.data && typeof event.data === 'object' && !Array.isArray(event.data)) {
        record.dataKeys = Object.keys(event.data).slice(0, 12).sort();
      }
      result.sse.events.push(record);
      if (event.event !== 'message') {
        result.sse.eventCounts[event.event] = (result.sse.eventCounts[event.event] || 0) + 1;
      }
      if (BOOTSTRAP_EVENTS.has(event.event)) {
        bootstrapSeen.value = true;
        result.sse.bootstrapSeen = true;
      }
      if (DONE_EVENTS.has(event.event)) {
        if (!firstRoundDone.value) {
          firstRoundDone.value = true;
          result.sse.firstRoundDoneSeen = true;
          result.sse.firstRoundDoneMs = event.elapsedMs;
        } else if (!secondRoundDone.value) {
          secondRoundDone.value = true;
          result.sse.secondRoundDoneSeen = true;
          result.sse.secondRoundDoneMs = event.elapsedMs;
          result.sse.secondRoundSeenOnSameStream = true;
        }
      }
    });

    sseStream.on('error', (error) => {
      result.sse.streamError = error && error.message ? error.message : String(error);
    });

    await waitFor(
      () => Boolean(firstRoundDone.value || result.sse.streamError),
      firstRoundWaitMs,
      250,
    );

    let sessionAfterFirstRound = await getSession(sessionBaseUrl, token, sessionId);
    result.sessionAfterFirstRound = {
      status: sessionAfterFirstRound.status || null,
      revision: sessionAfterFirstRound.revision || null,
      readyToGenerate: Boolean(sessionAfterFirstRound.readyToGenerate),
      slotFillPct: sessionAfterFirstRound.slotFillPct ?? null,
      currentQuestion: sessionAfterFirstRound.currentQuestion || null,
    };

    const currentQuestion = sessionAfterFirstRound.currentQuestion || null;
    const slotKey = currentQuestion && currentQuestion.slotKey ? currentQuestion.slotKey : null;
    const prompt = currentQuestion && currentQuestion.prompt ? currentQuestion.prompt : null;
    const revision = sessionAfterFirstRound.revision || null;
    const answer = answerForSlot(caseData, slotKey, prompt) || 'Tap to match and clear three identical tiles.';
    result.secondTurn = {
      slotKey,
      prompt,
      revision,
      answer,
      posted: false,
    };

    if (slotKey && revision && firstRoundDone.value) {
      const messageResponse = await httpJson('POST', buildUrl(sessionBaseUrl, `/api/v1/games/creation-sessions/${sessionId}/messages`), {
        headers: jsonHeaders,
        payload: {
          content: answer,
          revision,
        },
        timeoutMs: 60000,
      });
      result.secondTurn.posted = true;
      result.secondTurn.responseStatus = (((messageResponse || {}).data || {}).status || null);

      await waitFor(
        () => Boolean(secondRoundDone.value || result.sse.streamError),
        secondRoundWaitMs,
        250,
      );
    } else if (!result.sse.responseStatus) {
      const readySnapshot = await waitForSessionReady(sessionBaseUrl, token, sessionId, readyPollMs, readyPollIntervalMs);
      result.readyReconnect = {
        readySnapshot: readySnapshot ? {
          status: readySnapshot.status || null,
          revision: readySnapshot.revision || null,
          readyToGenerate: Boolean(readySnapshot.readyToGenerate),
          slotFillPct: readySnapshot.slotFillPct ?? null,
          currentQuestion: readySnapshot.currentQuestion || null,
        } : null,
        probe: null,
      };
      if (readySnapshot) {
        result.readyReconnect.probe = await probeReadySessionSse(
          sessionBaseUrl,
          token,
          sessionId,
          readyReconnectTimeoutMs,
          readyReconnectTimeoutMs,
        );
      }
    }

    result.sse.eventsArrivedIncrementally = Boolean(
      result.sse.firstChunkMs != null
      && result.sse.firstChunkMs < 10000
      && result.sse.firstEventMs != null
      && result.sse.firstEventMs < 10000
      && !('content-length' in result.sse.responseHeaders),
    );
    Object.assign(result, inferSuspectedLayer(result));
  } finally {
    if (sseStream) {
      sseStream.close();
    }
    if (createdUserId && adminToken && !keepUser) {
      try {
        result.cleanup = await deleteUser(baseUrl, adminToken, createdUserId);
      } catch (error) {
        result.cleanup = {
          error: error && error.message ? error.message : String(error),
          userId: createdUserId,
        };
      }
    }
    fs.writeFileSync(outputPath, JSON.stringify(result, null, 2), 'utf8');
  }

  console.log(JSON.stringify({
    outputPath,
    sessionId: result.sessionId,
    username: result.username,
    createdTempUser: result.createdTempUser,
    sessionBaseUrl: result.sessionBaseUrl,
    responseStatus: result.sse.responseStatus,
    headersReceivedMs: result.sse.headersReceivedMs,
    firstChunkMs: result.sse.firstChunkMs,
    firstEventMs: result.sse.firstEventMs,
    contentLength: result.sse.responseHeaders['content-length'] || null,
    transferEncoding: result.sse.responseHeaders['transfer-encoding'] || null,
    upstreamServiceTime: result.sse.responseHeaders['x-envoy-upstream-service-time'] || null,
    eventsArrivedIncrementally: result.sse.eventsArrivedIncrementally,
    authProbe: result.authProbe,
    authLongProbe: result.authLongProbe,
    bootstrapSeen: result.sse.bootstrapSeen,
    firstRoundDoneSeen: result.sse.firstRoundDoneSeen,
    secondRoundDoneSeen: result.sse.secondRoundDoneSeen,
    secondRoundSeenOnSameStream: result.sse.secondRoundSeenOnSameStream,
    readyReconnect: result.readyReconnect,
    eventCounts: result.sse.eventCounts,
    streamError: result.sse.streamError,
    suspectedLayer: result.suspectedLayer || null,
    suspectedLayerReason: result.suspectedLayerReason || null,
  }, null, 2));
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
