// ===================== Auth =====================
function checkAuth() {
  if (!token) {
    document.getElementById('loginOverlay').style.display = 'flex';
    document.getElementById('app').style.display = 'none';
  } else {
    document.getElementById('loginOverlay').style.display = 'none';
    document.getElementById('app').style.display = 'block';
    document.getElementById('accountDisplay').textContent =
      '账号: ' + (adminUsername || 'admin');
    init();
  }
}

async function doLogin() {
  const username = document.getElementById('usernameInput').value.trim();
  const password = document.getElementById('passwordInput').value;
  if (!username || !password) {
    toast('请输入账号和密码', 'error');
    return;
  }
  try {
    const res = await fetch(API_BASE + '/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const rawText = await res.text();
    const data = tryParseJson(rawText);
    if (!res.ok) {
      throw new Error(extractApiErrorMessage(data, rawText, res.status));
    }
    if (data && data.code !== undefined && data.code !== 0) {
      throw new Error(extractApiErrorMessage(data, rawText, res.status));
    }
    const payload = data && Object.prototype.hasOwnProperty.call(data, 'data')
      ? data.data
      : data;
    if (!payload?.token) {
      throw new Error('登录失败：未返回会话令牌');
    }
    token = payload.token;
    adminUsername = payload.username || username;
    localStorage.setItem('gv_admin_token', token);
    localStorage.setItem('gv_admin_username', adminUsername);
    document.getElementById('passwordInput').value = '';
    checkAuth();
  } catch (e) {
    toast('登录失败: ' + e.message, 'error');
  }
}

function doLogout() {
  token = '';
  adminUsername = '';
  localStorage.removeItem('gv_admin_token');
  localStorage.removeItem('gv_admin_username');
  checkAuth();
}

document.getElementById('passwordInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') doLogin();
});
document.getElementById('usernameInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('passwordInput').focus();
});

// ===================== API =====================
function tryParseJson(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function formatFcPlatformError(payload, rawText) {
  const code = payload?.Code || payload?.code || '';
  const message = payload?.Message || payload?.message || '';
  if (code === 'ExternalRedirectForbidden') {
    return 'API 代理配置错误：请把 www 域名上的 /api、/admin 路由直接指向 game-service（运行 sync_public_domain_routes.py），不要把 FC 默认域名当作 FC_API_URL。';
  }
  if (code && message) return `${code}: ${message}`;
  if (typeof rawText === 'string') {
    const embedded = tryParseJson(rawText.trim());
    if (embedded?.Code === 'ExternalRedirectForbidden') {
      return formatFcPlatformError(embedded, rawText);
    }
  }
  return '';
}

function extractApiErrorMessage(payload, rawText, status) {
  const fcError = formatFcPlatformError(payload, rawText);
  if (fcError) return fcError;
  const fromDetailArray = Array.isArray(payload?.detail)
    ? payload.detail.map(item => {
      if (!item) return '';
      if (typeof item === 'string') return item;
      const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
      const msg = item.msg || item.message || item.error || '';
      return loc && msg ? `${loc}: ${msg}` : msg;
    }).filter(Boolean).join('; ')
    : '';
  const fromDetail = typeof payload?.detail === 'string' ? payload.detail : '';
  const fromMessage = typeof payload?.message === 'string' ? payload.message : '';
  const fromError = typeof payload?.error === 'string' ? payload.error : '';
  const strippedText = typeof rawText === 'string'
    ? rawText.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
    : '';
  return fromDetailArray || fromDetail || fromMessage || fromError || strippedText || `HTTP ${status}`;
}

function isRetryableApiError(message, status) {
  if (!message) return false;
  if (/ExternalRedirectForbidden|API 代理配置错误/i.test(message)) return false;
  if (status === 429 || status === 503) return true;
  return /ResourceExhausted|DisableColdStart|Too Many Requests|Service Unavailable/i.test(message);
}

async function api(path, options = {}) {
  const url = API_BASE + path;
  const opts = {
    headers: { 'x-admin-token': token, 'Content-Type': 'application/json', ...options.headers },
    ...options,
  };
  if (opts.body && typeof opts.body === 'object') {
    opts.body = JSON.stringify(opts.body);
  }
  const maxAttempts = options.retry === false ? 1 : 4;
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const res = await fetch(url, opts);
      const rawText = await res.text();
      const data = tryParseJson(rawText);
      if (!res.ok) {
        const message = extractApiErrorMessage(data, rawText, res.status);
        if (attempt < maxAttempts && isRetryableApiError(message, res.status)) {
          await new Promise(resolve => setTimeout(resolve, 400 * attempt));
          continue;
        }
        throw new Error(message);
      }
      if (data && data.code !== undefined && data.code !== 0) {
        const message = extractApiErrorMessage(data, rawText, res.status);
        if (attempt < maxAttempts && isRetryableApiError(message, res.status)) {
          await new Promise(resolve => setTimeout(resolve, 400 * attempt));
          continue;
        }
        throw new Error(message);
      }
      if (data && Object.prototype.hasOwnProperty.call(data, 'data')) {
        return data.data;
      }
      return data;
    } catch (err) {
      lastError = err;
      if (err.message && err.message.includes('Unauthorized')) {
        doLogout();
      }
      if (attempt < maxAttempts && isRetryableApiError(err.message)) {
        await new Promise(resolve => setTimeout(resolve, 400 * attempt));
        continue;
      }
      throw err;
    }
  }
  throw lastError || new Error('Request failed');
}

async function apiForm(path, formData, options = {}) {
  const url = API_BASE + path;
  const headers = { 'x-admin-token': token, ...(options.headers || {}) };
  delete headers['Content-Type'];

  try {
    const res = await fetch(url, {
      ...options,
      headers,
      body: formData,
    });
    const rawText = await res.text();
    const data = tryParseJson(rawText);
    if (!res.ok) {
      throw new Error(extractApiErrorMessage(data, rawText, res.status));
    }
    if (data && data.code !== undefined && data.code !== 0) {
      throw new Error(extractApiErrorMessage(data, rawText, res.status));
    }
    if (data && Object.prototype.hasOwnProperty.call(data, 'data')) {
      return data.data;
    }
    return data;
  } catch (err) {
    if (err.message && err.message.includes('Unauthorized')) {
      doLogout();
    }
    throw err;
  }
}

// ===================== Toast =====================
function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = 'toast toast-' + type;
  el.textContent = msg;
  document.getElementById('toastContainer').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

const logSourceCache = {};

function getLogSourceSections(bundle) {
  return [
    { key: 'html', label: 'HTML', code: bundle?.htmlCode || '' },
    { key: 'css', label: 'CSS', code: bundle?.cssCode || '' },
    { key: 'js', label: 'JavaScript', code: bundle?.jsCode || '' },
  ].filter(section => section.code && section.code.trim());
}

function formatTextSize(text) {
  const bytes = new TextEncoder().encode(text || '').length;
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
}

async function copyText(text, successMessage) {
  if (!text || !String(text).trim()) {
    toast('当前没有可复制的源码', 'error');
    return;
  }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand('copy');
      textarea.remove();
    }
    toast(successMessage || '已复制');
  } catch (e) {
    toast('复制失败，请手动复制', 'error');
  }
}

function buildCombinedLogSource(gameId) {
  const source = logSourceCache[gameId];
  if (!source) return '';
  const sections = [];
  if (source.html && source.html.trim()) sections.push('<!-- HTML -->\n' + source.html);
  if (source.css && source.css.trim()) sections.push('/* CSS */\n' + source.css);
  if (source.js && source.js.trim()) sections.push('// JavaScript\n' + source.js);
  return sections.join('\n\n');
}

function copyLogSourceSection(gameId, key) {
  const source = logSourceCache[gameId];
  if (!source) {
    toast('源码缓存不存在，请重新打开详情', 'error');
    return;
  }
  return copyText(source[key] || '', '已复制 ' + key.toUpperCase() + ' 源码');
}

function copyAllLogSource(gameId) {
  return copyText(buildCombinedLogSource(gameId), '已复制全部源码');
}

function getTaskSourceBundle(task) {
  return task?.sourceBundle || task?.game?.bundles?.[0] || null;
}

function cacheTaskSource(task, cacheKey) {
  const bundle = getTaskSourceBundle(task);
  logSourceCache[cacheKey] = {
    html: bundle?.htmlCode || '',
    css: bundle?.cssCode || '',
    js: bundle?.jsCode || '',
  };
  return bundle;
}

function renderTaskSourceSection(task, sourceCacheKey) {
  const sourceBundle = cacheTaskSource(task, sourceCacheKey);
  const sourceSections = getLogSourceSections(sourceBundle);
  if (!sourceSections.length) {
    return '<div data-task-source-card="true"><div class="log-empty">当前任务关联的最新 bundle 没有可查看的 HTML / CSS / JavaScript 源码。</div></div>';
  }

  return `
    <div data-task-source-card="true">
      <div class="log-source-actions">
        <div class="log-source-summary">当前源码版本 v${escHtml(String(sourceBundle?.version || 0))}，共 ${sourceSections.length} 段源码，可直接复制排查问题。</div>
        <button class="btn btn-sm btn-secondary" onclick="copyAllLogSource('${escAttr(sourceCacheKey)}')">复制全部源码</button>
      </div>
      <div class="log-source-grid">
        ${sourceSections.map(section => `
          <div class="log-source-block">
            <div class="log-source-head">
              <div class="log-source-meta">
                <div class="log-source-name">${escHtml(section.label)}</div>
                <div class="log-source-size">${escHtml(formatTextSize(section.code))}</div>
              </div>
              <button class="btn btn-sm btn-secondary" onclick="copyLogSourceSection('${escAttr(sourceCacheKey)}', '${escAttr(section.key)}')">复制</button>
            </div>
            <pre class="log-code">${escHtml(section.code)}</pre>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

function renderTaskCandidateEvidence(task) {
  if (task.candidateArtifactsUnavailable) {
    return '<div class="log-empty">候选证据读取失败，请刷新重试。</div>';
  }
  const artifacts = task.candidateArtifacts || [];
  if (!artifacts.length) return '<div class="log-empty">当前任务未保存候选源码证据。</div>';
  return '<div class="log-source-summary">最近 4 份候选快照，用于复现生成失败；这些快照未作为作品版本交付。</div>' + artifacts.map(artifact => {
    const complete = artifact.storageType === 'inline_text' && !artifact.metadata?.truncated && typeof artifact.payloadText === 'string';
    const availability = complete ? '完整快照' : '内容不完整或不可用，不能据此完整复现';
    return `<details class="log-source-block" data-candidate-evidence="true">
      <summary>${escHtml(artifact.artifactType || 'candidate')} · ${escHtml(artifact.id || '')} · ${escHtml(availability)}</summary>
      <div class="log-source-meta">${escHtml(fmtDateTime(artifact.createdAt))} · SHA-256 ${escHtml(artifact.sha256 || '未记录')} · ${escHtml(String(artifact.sizeBytes ?? '-'))} bytes</div>
      <pre class="log-code">${escHtml(JSON.stringify(artifact.metadata || {}, null, 2))}</pre>
      <pre class="log-code">${escHtml(artifact.payloadText || '无内联源码')}</pre>
    </details>`;
  }).join('');
}

function buildTaskDetailMarkup(task, options = {}) {
  const sourceCacheKey = options.sourceCacheKey || ('task:' + task.id);
  const sourceSection = renderTaskSourceSection(task, sourceCacheKey);
  const events = renderTaskEvents(task.events || []);
  const llmLogs = renderLlmLogs(task.llmCallLogs || []);
  const llmUsageSummary = renderLlmUsageSummary(task.llmCallLogs || []);
  const durationMs = task.startedAt && task.completedAt
    ? new Date(task.completedAt).getTime() - new Date(task.startedAt).getTime()
    : null;
  const routeSnapshot = task.routeSnapshot
    ? renderRouteSnapshotPanel(task.routeSnapshot)
    : '<div class="log-empty">暂无最近路由快照</div>';
  const resultSummary = task.resultSummary
    ? `<pre class="log-code" style="max-height:220px">${escHtml(JSON.stringify(task.resultSummary, null, 2))}</pre>`
    : '<div class="log-empty">暂无结果摘要</div>';
  const previewLink = task.previewUrl
    ? `<div class="log-linkbox" style="margin-top:14px"><div class="log-linkicon">▶</div><div class="log-linkmeta"><div class="label">Preview</div><a href="${escAttr(task.previewUrl)}" target="_blank" rel="noopener noreferrer">${escHtml(task.previewUrl)}</a></div></div>`
    : '';
  const gameLink = task.gameUrl
    ? `<div class="log-linkbox" style="margin-top:10px"><div class="log-linkicon">↗</div><div class="log-linkmeta"><div class="label">Play</div><a href="${escAttr(task.gameUrl)}" target="_blank" rel="noopener noreferrer">${escHtml(task.gameUrl)}</a></div></div>`
    : '';
  const intentBuildPanel = renderIntentBuildPanel(task.intentBuild);
  const generationSummary = renderTaskGenerationSummary(task);
  const issueListPanel = renderIssueListPanel(task.issueList, {
    emptyText: '<div class="log-empty">当前任务没有记录结构化 QA 问题。</div>',
  });
  const qaArtifactsPanel = renderQaArtifactsPanel(task.qaArtifacts || []);
  const inputPrompt = task.inputPrompt || task.game?.description || '';
  const gameStatus = task.game?.status || '-';

  return `
    <div class="log-card">
      <div class="log-card-title">
        <div>
          <h4>${escHtml(options.title || '任务详情')}</h4>
          <div class="log-card-sub" style="margin-top:4px"><code>${escHtml(task.id)}</code></div>
        </div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span class="badge ${taskStatusBadge(task.status)}">${escHtml(task.status)}</span>
          ${options.showRefresh === false ? '' : `<button class="btn btn-secondary btn-sm" onclick="${escAttr(options.refreshAction || `showTaskDetail('${task.id}')`)}">刷新详情</button>`}
          ${options.showTerminate === false || !canTerminateTask(task.status) ? '' : `<button class="btn btn-danger btn-sm" onclick="terminateTask('${task.id}')">结束任务</button>`}
        </div>
      </div>
      <div class="log-kv-grid">
        <div class="log-kv"><div class="k">游戏</div><div class="v">${escHtml(task.game?.title || task.gameId || '-')}</div></div>
        <div class="log-kv"><div class="k">游戏状态</div><div class="v">${escHtml(statusLabel(gameStatus))}</div></div>
        <div class="log-kv"><div class="k">用户</div><div class="v">${escHtml(task.user?.displayName || task.user?.username || task.userId || '-')}</div></div>
        <div class="log-kv"><div class="k">类型</div><div class="v">${escHtml(task.taskType || '-')}</div></div>
        <div class="log-kv"><div class="k">阶段</div><div class="v">${escHtml(task.displayStageLabel || task.progressStage || task.failedStage || '-')}</div></div>
        <div class="log-kv"><div class="k">原始阶段</div><div class="v">${escHtml(task.rawStage || task.progressStage || task.failedStage || '-')}</div></div>
        <div class="log-kv"><div class="k">进度</div><div class="v">${task.progressPct ?? '-'}%</div></div>
        <div class="log-kv"><div class="k">当前消息</div><div class="v">${escHtml(task.progressMessage || '-')}</div></div>
        <div class="log-kv"><div class="k">版本</div><div class="v">v${escHtml(String(task.version || task.resultSummary?.version || getTaskSourceBundle(task)?.version || '-'))}</div></div>
        <div class="log-kv"><div class="k">Region</div><div class="v">${escHtml(task.region || '-')}</div></div>
        <div class="log-kv"><div class="k">超时</div><div class="v">${escHtml(String(task.timeoutS || '-'))}s</div></div>
        <div class="log-kv"><div class="k">耗时</div><div class="v">${escHtml(fmtDurationMs(durationMs))}</div></div>
        <div class="log-kv"><div class="k">创建时间</div><div class="v">${escHtml(fmtDateTime(task.createdAt))}</div></div>
        <div class="log-kv"><div class="k">开始时间</div><div class="v">${escHtml(fmtDateTime(task.startedAt))}</div></div>
        <div class="log-kv"><div class="k">完成时间</div><div class="v">${escHtml(fmtDateTime(task.completedAt))}</div></div>
        <div class="log-kv"><div class="k">配置版本</div><div class="v">${task.gatewayConfigVersion ?? '-'}</div></div>
        <div class="log-kv"><div class="k">Fallback</div><div class="v">${escHtml(task.fallback || '-')}</div></div>
        <div class="log-kv"><div class="k">错误</div><div class="v">${escHtml(task.errorMessage || '-')}</div></div>
      </div>
      ${previewLink}
      ${gameLink}
      <div class="section-title">意图构建</div>
      ${intentBuildPanel}
      <div class="section-title">生成摘要</div>
      ${generationSummary}
      <div class="section-title">QA 问题清单</div>
      ${issueListPanel}
      <div class="section-title">QA 报告</div>
      ${qaArtifactsPanel}
      <div class="section-title">原始输入</div>
      ${inputPrompt ? `<div class="log-textbox">${escHtml(inputPrompt)}</div>` : '<div class="log-empty">暂无原始输入</div>'}
      <div class="section-title">结果摘要</div>
      ${resultSummary}
      <div class="section-title">最新源码</div>
      ${sourceSection}
      <div class="section-title">候选源码证据</div>
      ${renderTaskCandidateEvidence(task)}
      <div class="section-title">最近路由快照</div>
      ${routeSnapshot}
      <div class="section-title">阶段时间线</div>
      <div style="display:flex;flex-direction:column;gap:12px">${events}</div>
      <div class="section-title">LLM 调用明细</div>
      ${llmUsageSummary}
      <div style="display:flex;flex-direction:column;gap:12px">${llmLogs}</div>
    </div>
  `;
}

// ===================== Confirm =====================
function confirmDialog(title, msg) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'confirm-overlay';
    overlay.innerHTML = `<div class="confirm-box"><h4>${title}</h4><p>${msg}</p><div class="btns"><button class="btn btn-secondary" id="cfmNo">取消</button><button class="btn btn-danger" id="cfmYes">确认</button></div></div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#cfmNo').onclick = () => { overlay.remove(); resolve(false); };
    overlay.querySelector('#cfmYes').onclick = () => { overlay.remove(); resolve(true); };
  });
}

// ===================== Tabs =====================
function switchTab(tab) {
  if (tab !== 'tasks') clearTaskDetailRefresh();
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + tab));
  if (tab === 'dashboard') loadStats();
  if (tab === 'games') { showGameList(); loadGames(); }
  if (tab === 'create') loadCreateAuthorOptions();
  if (tab === 'logs') loadLogs();
  if (tab === 'tasks') loadTasks();
  if (tab === 'users') { showUserList(); loadUsers(); }
  if (tab === 'subscriptions') loadSubscriptionPlans();
  if (tab === 'grants') loadGrantPage();
  if (tab === 'prompts') loadConfigs();
  if (tab === 'appdist') loadAppDistribution();
  if (tab === 'timeouts') loadTimeoutConfigs();
  if (tab === 'llm') loadLlmGateway();
  if (tab === 'settings') {}  // settings has no async load
}


// ===================== Admin Password =====================
async function changePassword() {
  const current = document.getElementById('set-current-password').value;
  const newPassword = document.getElementById('set-new-password').value;
  const confirm = document.getElementById('set-confirm-password').value;
  if (!current) { toast('请输入当前密码', 'error'); return; }
  if (newPassword.length < 6) { toast('新密码至少6个字符', 'error'); return; }
  if (newPassword !== confirm) { toast('两次输入不一致', 'error'); return; }
  try {
    await api('/change-password', {
      method: 'POST',
      body: { currentPassword: current, newPassword },
    });
    document.getElementById('set-current-password').value = '';
    document.getElementById('set-new-password').value = '';
    document.getElementById('set-confirm-password').value = '';
    toast('管理员密码已更新');
  } catch (e) { toast('更新失败: ' + e.message, 'error'); }
}

// ===================== Helpers =====================
function fmtNum(n) {
  n = Number(n || 0);
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e4) return (n / 1e4).toFixed(1) + 'W';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toString();
}

function fmtSize(bytes) {
  if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
  if (bytes > 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
}

function fmtDate(s) {
  if (!s) return '-';
  const d = new Date(s);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

function fmtDateTime(s) {
  if (!s) return '-';
  const d = new Date(s);
  return d.getFullYear() + '-'
    + String(d.getMonth() + 1).padStart(2, '0') + '-'
    + String(d.getDate()).padStart(2, '0') + ' '
    + String(d.getHours()).padStart(2, '0') + ':'
    + String(d.getMinutes()).padStart(2, '0') + ':'
    + String(d.getSeconds()).padStart(2, '0');
}

function fmtDurationMs(ms) {
  if (ms === null || ms === undefined || Number.isNaN(Number(ms))) return '-';
  const total = Math.max(0, Number(ms));
  if (total < 1000) return total + 'ms';
  const seconds = Math.floor(total / 1000);
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins <= 0) return seconds + 's';
  return mins + 'm ' + secs + 's';
}

function statusLabel(s) {
  const m = { published: '已发布', draft: '草稿', banned: '已封禁', review: '审核中', generating: '生成中', failed: '失败' };
  return m[s] || s;
}

function escHtml(s) {
  const div = document.createElement('div');
  div.textContent = s || '';
  return div.innerHTML;
}

function escAttr(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
