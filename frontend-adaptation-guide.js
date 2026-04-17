const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, LevelFormat,
} = require('docx');

// ─── Color palette ──────────────────────────────────────────────────────
const C = {
  primary:   '1A5276',
  accent:    '2E86C1',
  success:   '27AE60',
  warning:   'E67E22',
  danger:    'C0392B',
  lightBg:   'EBF5FB',
  codeBg:    'F4F6F8',
  border:    'BDC3C7',
  lightBdr:  'D5DBDB',
  text:      '2C3E50',
  muted:     '7F8C8D',
  white:     'FFFFFF',
};

// ─── Helpers ────────────────────────────────────────────────────────────
const PAGE_W = 12240; // US Letter
const PAGE_H = 15840;
const MARGIN = 1440;  // 1 inch
const CONTENT_W = PAGE_W - MARGIN * 2; // 9360

const thinBorder = { style: BorderStyle.SINGLE, size: 1, color: C.lightBdr };
const borders = { top: thinBorder, bottom: thinBorder, left: thinBorder, right: thinBorder };
const noBorder = { style: BorderStyle.NONE, size: 0 };
const noBorders = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder };

function heading(text, level = HeadingLevel.HEADING_1) {
  return new Paragraph({ heading: level, children: [new TextRun(text)] });
}

function para(text, opts = {}) {
  const runs = typeof text === 'string'
    ? [new TextRun({ text, color: C.text, font: 'Arial', size: 22, ...opts })]
    : text;
  return new Paragraph({ spacing: { after: 120 }, children: runs });
}

function bold(text) {
  return new TextRun({ text, bold: true, font: 'Arial', size: 22, color: C.text });
}

function normal(text) {
  return new TextRun({ text, font: 'Arial', size: 22, color: C.text });
}

function code(text) {
  return new TextRun({ text, font: 'Courier New', size: 20, color: C.primary, shading: { fill: C.codeBg, type: ShadingType.CLEAR } });
}

function codeBlock(lines) {
  return lines.map(line =>
    new Paragraph({
      spacing: { after: 0 },
      shading: { fill: C.codeBg, type: ShadingType.CLEAR },
      indent: { left: 360 },
      children: [new TextRun({ text: line, font: 'Courier New', size: 18, color: C.text })],
    })
  );
}

function bullet(children, ref = 'bullets', level = 0) {
  const runs = typeof children === 'string'
    ? [new TextRun({ text: children, font: 'Arial', size: 22, color: C.text })]
    : children;
  return new Paragraph({
    numbering: { reference: ref, level },
    spacing: { after: 80 },
    children: runs,
  });
}

function alertBox(titleText, bodyRuns, color) {
  const cellW = CONTENT_W;
  return new Table({
    width: { size: cellW, type: WidthType.DXA },
    columnWidths: [cellW],
    rows: [new TableRow({
      children: [new TableCell({
        borders: {
          top: { style: BorderStyle.SINGLE, size: 1, color },
          bottom: { style: BorderStyle.SINGLE, size: 1, color },
          left: { style: BorderStyle.SINGLE, size: 12, color },
          right: { style: BorderStyle.SINGLE, size: 1, color },
        },
        width: { size: cellW, type: WidthType.DXA },
        shading: { fill: C.white, type: ShadingType.CLEAR },
        margins: { top: 120, bottom: 120, left: 200, right: 200 },
        children: [
          new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text: titleText, bold: true, font: 'Arial', size: 22, color })] }),
          new Paragraph({ spacing: { after: 0 }, children: bodyRuns }),
        ],
      })],
    })],
  });
}

function tableRow(cells, headerRow = false) {
  return new TableRow({
    children: cells.map(({ text, width }) =>
      new TableCell({
        borders,
        width: { size: width, type: WidthType.DXA },
        shading: { fill: headerRow ? C.primary : C.white, type: ShadingType.CLEAR },
        margins: { top: 60, bottom: 60, left: 120, right: 120 },
        children: [new Paragraph({
          children: [new TextRun({
            text,
            bold: headerRow,
            font: 'Arial',
            size: 20,
            color: headerRow ? C.white : C.text,
          })],
        })],
      })
    ),
  });
}

function divider() {
  return new Paragraph({
    spacing: { before: 200, after: 200 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: C.lightBdr, space: 8 } },
    children: [],
  });
}

// ─── Document ───────────────────────────────────────────────────────────
const doc = new Document({
  styles: {
    default: {
      document: { run: { font: 'Arial', size: 22, color: C.text } },
    },
    paragraphStyles: [
      {
        id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 36, bold: true, font: 'Arial', color: C.primary },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 },
      },
      {
        id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 30, bold: true, font: 'Arial', color: C.accent },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 },
      },
      {
        id: 'Heading3', name: 'Heading 3', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 26, bold: true, font: 'Arial', color: C.text },
        paragraph: { spacing: { before: 200, after: 120 }, outlineLevel: 2 },
      },
    ],
  },
  numbering: {
    config: [
      {
        reference: 'bullets',
        levels: [{
          level: 0, format: LevelFormat.BULLET, text: '\u2022', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
      {
        reference: 'numbers',
        levels: [{
          level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
      {
        reference: 'numbers2',
        levels: [{
          level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
      {
        reference: 'numbers3',
        levels: [{
          level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
      {
        reference: 'numbers4',
        levels: [{
          level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: PAGE_W, height: PAGE_H },
        margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: C.accent, space: 4 } },
          children: [
            new TextRun({ text: 'GameVallies Creation Session', font: 'Arial', size: 18, color: C.muted }),
            new TextRun({ text: '  |  Frontend Adaptation Guide', font: 'Arial', size: 18, color: C.accent }),
          ],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ text: 'Page ', font: 'Arial', size: 16, color: C.muted }),
            new TextRun({ children: [PageNumber.CURRENT], font: 'Arial', size: 16, color: C.muted }),
          ],
        })],
      }),
    },
    children: [
      // ═══════════════════════════════════════════════════════════════
      // COVER
      // ═══════════════════════════════════════════════════════════════
      new Paragraph({ spacing: { before: 2400 }, children: [] }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 120 },
        children: [new TextRun({ text: 'Creation Session', font: 'Arial', size: 56, bold: true, color: C.primary })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 400 },
        children: [new TextRun({ text: 'Frontend Adaptation Guide', font: 'Arial', size: 40, color: C.accent })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        border: { top: { style: BorderStyle.SINGLE, size: 2, color: C.accent, space: 12 } },
        spacing: { before: 200, after: 80 },
        children: [new TextRun({ text: 'Version 2.0  \u00B7  2026-03-31', font: 'Arial', size: 22, color: C.muted })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: 'Optimistic Creation + WebSocket Real-time Push', font: 'Arial', size: 22, color: C.muted })],
      }),

      new Paragraph({ children: [new PageBreak()] }),

      // ═══════════════════════════════════════════════════════════════
      // 1. OVERVIEW
      // ═══════════════════════════════════════════════════════════════
      heading('1  Change Overview'),
      para([
        normal('This document covers two backend changes that require frontend adaptation: '),
        bold('Optimistic Session Creation'),
        normal(' (new '),
        code('initializing'),
        normal(' status) and '),
        bold('WebSocket Real-time Push'),
        normal(' (new '),
        code('session:updated'),
        normal(' / '),
        code('session:error'),
        normal(' events). All existing REST endpoints remain unchanged; the adaptation is purely additive.'),
      ]),

      alertBox('BACKWARD COMPATIBILITY', [
        normal('All 7 REST API endpoints (POST /creation-sessions, GET /active, GET /:id, POST /messages, POST /skip, POST /generate, POST /abandon) retain identical request/response contracts. The only differences: '),
        code('POST /creation-sessions'),
        normal(' now returns '),
        code('status: "initializing"'),
        normal(' instead of '),
        code('"collecting"'),
        normal(' / '),
        code('"ready"'),
        normal('. Polling-based frontends will continue to work without changes, but will experience a 2\u20135s delay.'),
      ], C.success),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      // Status lifecycle table
      heading('1.1  Session Status Lifecycle', HeadingLevel.HEADING_2),

      new Table({
        width: { size: CONTENT_W, type: WidthType.DXA },
        columnWidths: [1800, 4200, 3360],
        rows: [
          tableRow([
            { text: 'Status', width: 1800 },
            { text: 'Description', width: 4200 },
            { text: 'Frontend Action', width: 3360 },
          ], true),
          tableRow([
            { text: 'initializing', width: 1800 },
            { text: 'Session created, AI analysis running in background (2\u20135s)', width: 4200 },
            { text: 'Show loading skeleton / spinner', width: 3360 },
          ]),
          tableRow([
            { text: 'collecting', width: 1800 },
            { text: 'AI returned first question, waiting for user answers', width: 4200 },
            { text: 'Render question, enable input', width: 3360 },
          ]),
          tableRow([
            { text: 'ready', width: 1800 },
            { text: 'Enough slots filled, can trigger generation', width: 4200 },
            { text: 'Show "Generate" button', width: 3360 },
          ]),
          tableRow([
            { text: 'generating', width: 1800 },
            { text: 'Game generation task queued', width: 4200 },
            { text: 'Show generation progress (gen:progress)', width: 3360 },
          ]),
          tableRow([
            { text: 'completed', width: 1800 },
            { text: 'Generation task queued, session lifecycle done', width: 4200 },
            { text: 'Navigate to game preview', width: 3360 },
          ]),
          tableRow([
            { text: 'abandoned', width: 1800 },
            { text: 'Session cancelled (user or timeout or init failure)', width: 4200 },
            { text: 'Show error / allow retry', width: 3360 },
          ]),
        ],
      }),

      divider(),

      // ═══════════════════════════════════════════════════════════════
      // 2. OPTIMISTIC CREATION
      // ═══════════════════════════════════════════════════════════════
      heading('2  Optimistic Session Creation'),

      heading('2.1  Before vs After', HeadingLevel.HEADING_2),

      new Table({
        width: { size: CONTENT_W, type: WidthType.DXA },
        columnWidths: [4680, 4680],
        rows: [
          tableRow([
            { text: 'Before (v1)', width: 4680 },
            { text: 'After (v2)', width: 4680 },
          ], true),
          tableRow([
            { text: 'POST /creation-sessions blocks 2\u20135s while AI analysis runs synchronously', width: 4680 },
            { text: 'POST /creation-sessions returns <200ms with status="initializing"', width: 4680 },
          ]),
          tableRow([
            { text: 'Response includes first question and slots', width: 4680 },
            { text: 'Response has empty slots, null currentQuestion', width: 4680 },
          ]),
          tableRow([
            { text: 'Frontend can immediately show the question', width: 4680 },
            { text: 'Frontend shows loading state, then receives update via WS or poll', width: 4680 },
          ]),
        ],
      }),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      heading('2.2  Required TypeScript Changes', HeadingLevel.HEADING_2),

      para([bold('Step 1: Update the status union type to include '), code('initializing')]),

      ...codeBlock([
        '// types/creation-session.ts',
        'type CreationSessionStatus =',
        '  | "initializing"  // \u2190 NEW',
        '  | "collecting"',
        '  | "ready"',
        '  | "generating"',
        '  | "completed"',
        '  | "failed"',
        '  | "abandoned";',
      ]),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      para([bold('Step 2: Handle '), code('initializing'), bold(' in the session state machine')]),

      ...codeBlock([
        '// hooks/useCreationSession.ts',
        'function renderSessionUI(session: CreationSessionSnapshot) {',
        '  switch (session.status) {',
        '    case "initializing":',
        '      return <SessionSkeleton prompt={session.initialPrompt} />;',
        '    case "collecting":',
        '      return <QuestionForm question={session.currentQuestion} />;',
        '    case "ready":',
        '      return <GenerateButton session={session} />;',
        '    // ...other cases unchanged',
        '  }',
        '}',
      ]),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      para([bold('Step 3: Guard against '), code('initializing'), bold(' for user actions')]),

      ...codeBlock([
        '// Do NOT allow message/skip/generate while initializing',
        'const canInteract = session.status === "collecting"',
        '                  || session.status === "ready";',
        '',
        '// appendMessage / skipCurrentQuestion will throw 409',
        '// if called during "initializing"',
      ]),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      alertBox('IMPORTANT', [
        normal('The '),
        code('initializing'),
        normal(' status has a 30s timeout safety net. If AI analysis takes >30s, the session is auto-abandoned with '),
        code('metadata.initError = "Session initialization timed out"'),
        normal('. Frontend should detect this and allow the user to retry.'),
      ], C.warning),

      new Paragraph({ spacing: { after: 100 }, children: [] }),

      divider(),

      // ═══════════════════════════════════════════════════════════════
      // 3. WEBSOCKET
      // ═══════════════════════════════════════════════════════════════
      heading('3  WebSocket Real-time Push'),

      para([
        normal('Two new Socket.IO events are pushed from the backend. These complement (not replace) the existing REST polling. Frontend can subscribe for instant updates and fall back to polling as a degraded mode.'),
      ]),

      heading('3.1  Event: session:updated', HeadingLevel.HEADING_2),

      para([normal('Fired when the async AI analysis completes and the session transitions from '), code('initializing'), normal(' to '), code('collecting'), normal(' or '), code('ready'), normal('.')]),

      ...codeBlock([
        '// Payload structure',
        '{',
        '  type: "session:updated",',
        '  sessionId: string,',
        '  session: CreationSessionSnapshot,  // full snapshot',
        '  timestamp: number                  // Date.now()',
        '}',
      ]),

      new Paragraph({ spacing: { after: 120 }, children: [] }),

      para([bold('Key fields in '), code('session'), bold(' payload:')]),

      new Table({
        width: { size: CONTENT_W, type: WidthType.DXA },
        columnWidths: [2400, 2400, 4560],
        rows: [
          tableRow([
            { text: 'Field', width: 2400 },
            { text: 'Type', width: 2400 },
            { text: 'Description', width: 4560 },
          ], true),
          tableRow([
            { text: 'status', width: 2400 },
            { text: '"collecting" | "ready"', width: 2400 },
            { text: 'New session status after AI analysis', width: 4560 },
          ]),
          tableRow([
            { text: 'currentQuestion', width: 2400 },
            { text: 'Question | null', width: 2400 },
            { text: 'First question to show the user (null if ready)', width: 4560 },
          ]),
          tableRow([
            { text: 'conversation', width: 2400 },
            { text: 'Message[]', width: 2400 },
            { text: 'Includes AI reply from analysis', width: 4560 },
          ]),
          tableRow([
            { text: 'slotState', width: 2400 },
            { text: 'Record<string, any>', width: 2400 },
            { text: 'Extracted slot values from initial prompt', width: 4560 },
          ]),
          tableRow([
            { text: 'revision', width: 2400 },
            { text: 'number', width: 2400 },
            { text: 'Incremented to 2 (use for next CAS request)', width: 4560 },
          ]),
        ],
      }),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      heading('3.2  Event: session:error', HeadingLevel.HEADING_2),

      para([normal('Fired when the async AI analysis fails or when session initialization times out. The session is auto-abandoned.')]),

      ...codeBlock([
        '// Payload structure',
        '{',
        '  type: "session:error",',
        '  sessionId: string,',
        '  error: string,          // human-readable message',
        '  details: {',
        '    reason: "init_failed"  // AI analysis error',
        '          | "init_timeout" // 30s timeout exceeded',
        '  },',
        '  timestamp: number',
        '}',
      ]),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      heading('3.3  Integration Code', HeadingLevel.HEADING_2),

      ...codeBlock([
        '// hooks/useCreationSessionSocket.ts',
        'import { useEffect } from "react";',
        'import { useSocket } from "./useSocket";',
        'import { useSessionStore } from "../stores/session";',
        '',
        'export function useCreationSessionSocket() {',
        '  const socket = useSocket();   // existing Socket.IO hook',
        '  const setSession = useSessionStore(s => s.setSession);',
        '  const setError   = useSessionStore(s => s.setError);',
        '',
        '  useEffect(() => {',
        '    if (!socket) return;',
        '',
        '    const onUpdate = (data: any) => {',
        '      // Replace local session state with server snapshot',
        '      if (data.session) {',
        '        setSession(data.session);',
        '      }',
        '    };',
        '',
        '    const onError = (data: any) => {',
        '      setError({',
        '        sessionId: data.sessionId,',
        '        message: data.error,',
        '        reason: data.details?.reason,',
        '      });',
        '    };',
        '',
        '    socket.on("session:updated", onUpdate);',
        '    socket.on("session:error",   onError);',
        '',
        '    return () => {',
        '      socket.off("session:updated", onUpdate);',
        '      socket.off("session:error",   onError);',
        '    };',
        '  }, [socket, setSession, setError]);',
        '}',
      ]),

      new Paragraph({ spacing: { after: 200 }, children: [] }),

      alertBox('REVISION HANDLING', [
        normal('When receiving a '),
        code('session:updated'),
        normal(' event, always update the local '),
        code('revision'),
        normal(' field. Subsequent REST calls (appendMessage, skip, generate) send '),
        code('revision'),
        normal(' for CAS validation. Using a stale revision will result in a '),
        code('409 Conflict'),
        normal('.'),
      ], C.warning),

      divider(),

      // ═══════════════════════════════════════════════════════════════
      // 4. POLLING FALLBACK
      // ═══════════════════════════════════════════════════════════════
      heading('4  Polling Fallback Strategy'),

      para([normal('WebSocket push is best-effort. If the connection drops or the push fails, the frontend should poll as a fallback. Recommended pattern:')]),

      ...codeBlock([
        'async function waitForSessionReady(sessionId: string) {',
        '  const MAX_POLLS = 15;       // 30s total',
        '  const POLL_INTERVAL = 2000; // 2s',
        '',
        '  for (let i = 0; i < MAX_POLLS; i++) {',
        '    const res = await api.get(`/creation-sessions/${sessionId}`);',
        '    const session = res.data.data;',
        '',
        '    if (session.status !== "initializing") {',
        '      return session;  // collecting, ready, or abandoned',
        '    }',
        '',
        '    await sleep(POLL_INTERVAL);',
        '  }',
        '',
        '  throw new Error("Session initialization timed out");',
        '}',
      ]),

      new Paragraph({ spacing: { after: 120 }, children: [] }),

      para([bold('Recommended: combine WS + polling')]),

      ...codeBlock([
        '// Start polling when session is "initializing"',
        '// Stop polling when WS event arrives OR poll returns non-initializing',
        'useEffect(() => {',
        '  if (session?.status !== "initializing") return;',
        '',
        '  const timer = setInterval(async () => {',
        '    const fresh = await fetchSession(session.id);',
        '    if (fresh.status !== "initializing") {',
        '      setSession(fresh);',
        '      clearInterval(timer);',
        '    }',
        '  }, 2000);',
        '',
        '  return () => clearInterval(timer);',
        '}, [session?.status, session?.id]);',
      ]),

      divider(),

      // ═══════════════════════════════════════════════════════════════
      // 5. COMPLETE FLOW
      // ═══════════════════════════════════════════════════════════════
      heading('5  Complete Interaction Flow'),

      para([bold('User creates a new game:')]),

      new Paragraph({
        numbering: { reference: 'numbers2', level: 0 },
        spacing: { after: 80 },
        children: [normal('User submits prompt '), code('\u2192'), normal(' POST /creation-sessions')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers2', level: 0 },
        spacing: { after: 80 },
        children: [normal('API returns snapshot with '), code('status: "initializing"'), normal(' in <200ms')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers2', level: 0 },
        spacing: { after: 80 },
        children: [normal('Frontend shows loading skeleton with the prompt text')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers2', level: 0 },
        spacing: { after: 80 },
        children: [normal('Backend runs AI analysis in background (2\u20135s)')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers2', level: 0 },
        spacing: { after: 80 },
        children: [code('session:updated'), normal(' fires via WebSocket with full snapshot')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers2', level: 0 },
        spacing: { after: 80 },
        children: [normal('Frontend replaces skeleton with question UI ('), code('collecting'), normal(') or generate button ('), code('ready'), normal(')')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers2', level: 0 },
        spacing: { after: 80 },
        children: [normal('User answers questions via POST /messages (synchronous, as before)')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers2', level: 0 },
        spacing: { after: 120 },
        children: [normal('User triggers generation via POST /generate (synchronous, as before)')],
      }),

      para([bold('Error paths:')]),

      bullet([normal('AI analysis fails '), code('\u2192'), normal(' '), code('session:error'), normal(' with '), code('reason: "init_failed"'), normal(' '), code('\u2192'), normal(' show retry button')]),
      bullet([normal('30s timeout '), code('\u2192'), normal(' '), code('session:error'), normal(' with '), code('reason: "init_timeout"'), normal(' '), code('\u2192'), normal(' show timeout message + retry')]),
      bullet([normal('User creates another session while initializing '), code('\u2192'), normal(' old session auto-abandoned')]),

      divider(),

      // ═══════════════════════════════════════════════════════════════
      // 6. REST API
      // ═══════════════════════════════════════════════════════════════
      heading('6  REST API Quick Reference'),

      para([normal('All endpoints remain unchanged. New behavior notes marked with '), code('\u2605'), normal('.')]),

      new Table({
        width: { size: CONTENT_W, type: WidthType.DXA },
        columnWidths: [1200, 3600, 4560],
        rows: [
          tableRow([
            { text: 'Method', width: 1200 },
            { text: 'Endpoint', width: 3600 },
            { text: 'Notes', width: 4560 },
          ], true),
          tableRow([
            { text: 'POST', width: 1200 },
            { text: '/creation-sessions', width: 3600 },
            { text: '\u2605 Returns "initializing" (was "collecting"/"ready")', width: 4560 },
          ]),
          tableRow([
            { text: 'GET', width: 1200 },
            { text: '/creation-sessions/active', width: 3600 },
            { text: '\u2605 Also returns "initializing" sessions', width: 4560 },
          ]),
          tableRow([
            { text: 'GET', width: 1200 },
            { text: '/creation-sessions/:id', width: 3600 },
            { text: 'No change', width: 4560 },
          ]),
          tableRow([
            { text: 'POST', width: 1200 },
            { text: '/creation-sessions/:id/messages', width: 3600 },
            { text: '\u2605 Returns 409 if status is "initializing"', width: 4560 },
          ]),
          tableRow([
            { text: 'POST', width: 1200 },
            { text: '/creation-sessions/:id/skip', width: 3600 },
            { text: '\u2605 Returns 409 if status is "initializing"', width: 4560 },
          ]),
          tableRow([
            { text: 'POST', width: 1200 },
            { text: '/creation-sessions/:id/generate', width: 3600 },
            { text: 'No change (already checks status)', width: 4560 },
          ]),
          tableRow([
            { text: 'POST', width: 1200 },
            { text: '/creation-sessions/:id/abandon', width: 3600 },
            { text: 'Works for "initializing" sessions too', width: 4560 },
          ]),
        ],
      }),

      divider(),

      // ═══════════════════════════════════════════════════════════════
      // 7. WEBSOCKET EVENTS
      // ═══════════════════════════════════════════════════════════════
      heading('7  WebSocket Events Summary'),

      new Table({
        width: { size: CONTENT_W, type: WidthType.DXA },
        columnWidths: [2200, 2200, 4960],
        rows: [
          tableRow([
            { text: 'Event', width: 2200 },
            { text: 'Source', width: 2200 },
            { text: 'Description', width: 4960 },
          ], true),
          tableRow([
            { text: 'session:updated', width: 2200 },
            { text: 'NEW', width: 2200 },
            { text: 'Session initialized successfully, full snapshot', width: 4960 },
          ]),
          tableRow([
            { text: 'session:error', width: 2200 },
            { text: 'NEW', width: 2200 },
            { text: 'Session init failed or timed out', width: 4960 },
          ]),
          tableRow([
            { text: 'gen:progress', width: 2200 },
            { text: 'Existing', width: 2200 },
            { text: 'Game generation progress (unchanged)', width: 4960 },
          ]),
          tableRow([
            { text: 'gen:complete', width: 2200 },
            { text: 'Existing', width: 2200 },
            { text: 'Game generation done (unchanged)', width: 4960 },
          ]),
          tableRow([
            { text: 'gen:error', width: 2200 },
            { text: 'Existing', width: 2200 },
            { text: 'Game generation error (unchanged)', width: 4960 },
          ]),
        ],
      }),

      divider(),

      // ═══════════════════════════════════════════════════════════════
      // 8. CHECKLIST
      // ═══════════════════════════════════════════════════════════════
      heading('8  Frontend Adaptation Checklist'),

      para([bold('Required (must have):')]),

      new Paragraph({
        numbering: { reference: 'numbers3', level: 0 },
        spacing: { after: 80 },
        children: [normal('Add '), code('"initializing"'), normal(' to CreationSessionStatus type definition')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers3', level: 0 },
        spacing: { after: 80 },
        children: [normal('Handle '), code('initializing'), normal(' in session UI state machine (show loading skeleton)')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers3', level: 0 },
        spacing: { after: 80 },
        children: [normal('Guard user actions (message/skip/generate) to reject during '), code('initializing')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers3', level: 0 },
        spacing: { after: 80 },
        children: [normal('Handle '), code('409 Conflict'), normal(' from /messages and /skip endpoints gracefully')],
      }),

      new Paragraph({ spacing: { after: 120 }, children: [] }),

      para([bold('Recommended (for best UX):')]),

      new Paragraph({
        numbering: { reference: 'numbers4', level: 0 },
        spacing: { after: 80 },
        children: [normal('Subscribe to '), code('session:updated'), normal(' and '), code('session:error'), normal(' WebSocket events')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers4', level: 0 },
        spacing: { after: 80 },
        children: [normal('Implement polling fallback (2s interval, 15 max attempts) for '), code('initializing'), normal(' sessions')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers4', level: 0 },
        spacing: { after: 80 },
        children: [normal('Show '), code('initialPrompt'), normal(' in the loading state so user sees their input immediately')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers4', level: 0 },
        spacing: { after: 80 },
        children: [normal('Update local '), code('revision'), normal(' from WS payload to avoid CAS conflicts on next request')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers4', level: 0 },
        spacing: { after: 80 },
        children: [normal('Provide retry mechanism for '), code('init_failed'), normal(' and '), code('init_timeout'), normal(' errors')],
      }),
      new Paragraph({
        numbering: { reference: 'numbers4', level: 0 },
        spacing: { after: 80 },
        children: [normal('Display '), code('metadata.initError'), normal(' message when session is abandoned due to init failure')],
      }),
    ],
  }],
});

Packer.toBuffer(doc).then(buffer => {
  const outPath = '/sessions/eager-upbeat-curie/mnt/gamevallies-backend/CREATION_SESSION_FRONTEND_ADAPTATION_GUIDE.docx';
  fs.writeFileSync(outPath, buffer);
  console.log('Written to ' + outPath);
});
