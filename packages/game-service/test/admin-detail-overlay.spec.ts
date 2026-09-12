import * as fs from 'fs';
import * as path from 'path';
import * as vm from 'vm';

const adminDir = path.join(__dirname, '../src/admin');

type MiniEl = {
  tagName: string;
  className: string;
  id: string;
  dataset: Record<string, string>;
  style: Record<string, string>;
  attributes: Record<string, string>;
  children: MiniEl[];
  parentNode: MiniEl | null;
  innerHTML: string;
  textContent: string;
  scrollTop: number;
  _detailOnClose: (() => void) | null;
  listeners: Record<string, Array<(event: any) => void>>;
  setAttribute(name: string, value: string): void;
  appendChild(child: MiniEl): MiniEl;
  remove(): void;
  addEventListener(type: string, fn: (event: any) => void): void;
  querySelector(sel: string): MiniEl | null;
  querySelectorAll(sel: string): MiniEl[];
};

function matches(el: MiniEl, sel: string): boolean {
  if (sel === '.modal-overlay[data-overlay-key]') {
    return classList(el).includes('modal-overlay') && Boolean(el.dataset.overlayKey);
  }
  if (sel.startsWith('.')) return classList(el).includes(sel.slice(1));
  if (sel.startsWith('#')) return el.id === sel.slice(1);
  return el.tagName === sel.toUpperCase();
}

function classList(el: MiniEl): string[] {
  return String(el.className || '').split(/\s+/).filter(Boolean);
}

function walk(el: MiniEl, visit: (node: MiniEl) => void) {
  visit(el);
  for (const child of el.children) walk(child, visit);
}

function queryAll(root: MiniEl, sel: string): MiniEl[] {
  const parts = sel.trim().split(/\s+/);
  if (parts.length === 1) {
    const out: MiniEl[] = [];
    for (const child of root.children) {
      walk(child, (node) => {
        if (matches(node, sel)) out.push(node);
      });
    }
    return out;
  }
  const [first, ...rest] = parts;
  const next = queryAll(root, first);
  const out: MiniEl[] = [];
  for (const node of next) out.push(...queryAll(node, rest.join(' ')));
  return out;
}

function createEl(tag: string): MiniEl {
  const el: MiniEl = {
    tagName: String(tag).toUpperCase(),
    className: '',
    id: '',
    dataset: {},
    style: {},
    attributes: {},
    children: [],
    parentNode: null,
    innerHTML: '',
    textContent: '',
    scrollTop: 0,
    _detailOnClose: null,
    listeners: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
      if (name === 'id') this.id = value;
    },
    appendChild(child) {
      this.children.push(child);
      child.parentNode = this;
      return child;
    },
    remove() {
      if (!this.parentNode) return;
      this.parentNode.children = this.parentNode.children.filter((item: MiniEl) => item !== this);
      this.parentNode = null;
    },
    addEventListener(type, fn) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(fn);
    },
    querySelector(sel) {
      return this.querySelectorAll(sel)[0] || null;
    },
    querySelectorAll(sel) {
      return queryAll(this, sel);
    },
  };
  return el;
}

function loadAdminDetailRuntime() {
  const body = createEl('body');
  const documentListeners: Record<string, Array<(event: any) => void>> = {};
  const document: any = {
    body,
    createElement: createEl,
    getElementById: (id: string) => {
      let found: MiniEl | null = null;
      walk(body, (node) => {
        if (node.id === id) found = node;
      });
      return found;
    },
    querySelector(sel: string) {
      return this.querySelectorAll(sel)[0] || null;
    },
    querySelectorAll(sel: string) {
      if (sel === '.confirm-overlay' || sel === '.modal-overlay' || sel === '.modal-overlay[data-overlay-key]') {
        const out: MiniEl[] = [];
        walk(body, (node) => {
          if (matches(node, sel)) out.push(node);
        });
        return out;
      }
      return queryAll(body, sel);
    },
    addEventListener(type: string, fn: (event: any) => void) {
      documentListeners[type] = documentListeners[type] || [];
      documentListeners[type].push(fn);
    },
  };

  let currentTaskDetailId: string | null = null;
  let currentTaskDetailMeta = { title: '任务详情', focusSection: null as string | null };
  let taskDetailRefreshTimer: any = null;
  const api = jest.fn(async (url: string) => ({
    id: url.replace('/tasks/', ''),
    status: 'running',
    taskType: 'generate',
    events: [],
    llmCallLogs: [],
  }));

  const context: any = vm.createContext({
    document,
    window: { location: { origin: 'https://example.test' } },
    currentTaskDetailId,
    currentTaskDetailMeta,
    taskDetailRefreshTimer,
    api,
    escHtml: (s: unknown) => String(s ?? ''),
    escAttr: (s: unknown) => String(s ?? ''),
    buildTaskDetailMarkup: (task: any, options: any = {}) =>
      `<div class="log-card" data-task-id="${task.id}" data-refresh="${options.refreshAction || ''}">${options.title || '任务详情'}</div>`,
    requestAnimationFrame: (fn: Function) => fn(),
    setTimeout,
    clearTimeout,
    console,
  });

  Object.defineProperty(context, 'currentTaskDetailId', {
    get: () => currentTaskDetailId,
    set: (value) => { currentTaskDetailId = value; },
  });
  Object.defineProperty(context, 'currentTaskDetailMeta', {
    get: () => currentTaskDetailMeta,
    set: (value) => { currentTaskDetailMeta = value; },
  });
  Object.defineProperty(context, 'taskDetailRefreshTimer', {
    get: () => taskDetailRefreshTimer,
    set: (value) => { taskDetailRefreshTimer = value; },
  });

  vm.runInContext(fs.readFileSync(path.join(adminDir, 'admin-panel-users-logs.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(adminDir, 'admin-panel-ops.js'), 'utf8'), context);
  return { context, document, body, documentListeners, api };
}

describe('admin detail overlays', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('removes the tasks-page append-below detail mount', () => {
    const html = fs.readFileSync(path.join(adminDir, 'admin-panel.html'), 'utf8');
    const ops = fs.readFileSync(path.join(adminDir, 'admin-panel-ops.js'), 'utf8');
    expect(html).not.toContain('taskDetailWrap');
    expect(ops).not.toContain('taskDetailWrap');
    expect(ops).toContain("onclick=\"showTaskDetail('${task.id}')\">详情</button>");
    expect(ops).toContain('showDetailOverlay');
    expect(fs.readFileSync(path.join(adminDir, 'admin-panel-games.js'), 'utf8')).toContain('返回列表');
    expect(fs.readFileSync(path.join(adminDir, 'admin-panel-users-logs.js'), 'utf8')).toContain('返回列表');
  });

  it('opens task 详情 in a viewport modal and keeps auto-refresh inside it', async () => {
    const { context, body, documentListeners, api } = loadAdminDetailRuntime();
    await context.showTaskDetail('task-1');
    expect(api).toHaveBeenCalledWith('/tasks/task-1');
    expect(body.children).toHaveLength(1);
    expect(body.children[0].className).toBe('modal-overlay');
    expect(body.children[0].dataset.overlayKey).toBe('task-detail:task-1');
    expect(body.children[0].querySelector('.modal-header h3')?.textContent).toBe('任务详情');
    expect(body.children[0].querySelector('.modal-body')?.innerHTML).toContain('data-task-id="task-1"');
    expect(context.currentTaskDetailId).toBe('task-1');
    expect(context.taskDetailRefreshTimer).toBeTruthy();

    await context.showTaskDetail('task-1', { silent: true });
    expect(body.children).toHaveLength(1);
    expect(body.children[0].querySelector('.modal-body')?.innerHTML).toContain('data-refresh="showTaskDetail(\'task-1\')"');
  });

  it('closes the task modal via ×, backdrop, and Escape, and stops auto-refresh', async () => {
    const first = loadAdminDetailRuntime();
    await first.context.showTaskDetail('task-close');
    const closeBtn = first.body.children[0].querySelector('.modal-close');
    closeBtn?.listeners.click[0]({ target: closeBtn });
    expect(first.body.children).toHaveLength(0);
    expect(first.context.currentTaskDetailId).toBeNull();
    expect(first.context.taskDetailRefreshTimer).toBeNull();

    const second = loadAdminDetailRuntime();
    await second.context.showTaskDetail('task-backdrop');
    const overlay = second.body.children[0];
    overlay.listeners.click[0]({ target: overlay });
    expect(second.body.children).toHaveLength(0);

    const third = loadAdminDetailRuntime();
    await third.context.showTaskDetail('task-esc', { title: '生成记录详情' });
    expect(third.body.children[0].querySelector('.modal-header h3')?.textContent).toBe('生成记录详情');
    for (const listener of third.documentListeners.keydown || []) {
      listener({ key: 'Escape' });
    }
    expect(third.body.children).toHaveLength(0);
    expect(third.context.currentTaskDetailId).toBeNull();
  });

  it('reuses the logs modal helper so both entry points share auto-refresh', async () => {
    const { context, body } = loadAdminDetailRuntime();
    await context.showTaskDetailModal('task-from-logs', { focusSection: 'source' });
    expect(body.children[0].dataset.overlayKey).toBe('task-detail:task-from-logs');
    expect(body.children[0].querySelector('.modal-header h3')?.textContent).toBe('生成记录详情');
    expect(body.children[0].querySelector('.modal-body')?.innerHTML).toContain("focusSection: 'source'");
    expect(context.taskDetailRefreshTimer).toBeTruthy();
  });
});
