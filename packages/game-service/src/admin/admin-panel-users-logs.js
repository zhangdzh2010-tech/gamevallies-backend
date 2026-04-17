// ===================== User Management =====================
let userPage = 1;
let userSearchTimeout = null;

function debouncedUserSearch() {
  clearTimeout(userSearchTimeout);
  userSearchTimeout = setTimeout(() => { userPage = 1; loadUsers(); }, 300);
}

async function loadUsers() {
  const search = document.getElementById('userSearchInput').value.trim();
  const role = document.getElementById('roleFilter').value;
  const wrap = document.getElementById('userTableWrap');
  wrap.innerHTML = '<div class="workspace-empty"><div class="spinner"></div><div style="margin-top:8px">加载中...</div></div>';
  try {
    const d = await api(`/users?page=${userPage}&limit=20&search=${encodeURIComponent(search)}&role=${role}`);
    renderUserTable(d);
  } catch (e) {
    wrap.innerHTML = `<div class="workspace-empty" style="color:#dc2626">加载失败: ${escHtml(e.message)}</div>`;
  }
}

function renderUserTable(d) {
  const wrap = document.getElementById('userTableWrap');
  setHtml('usersSummary', `<span class="llm-pill">共 ${fmtNum(d.total || 0)} 位用户</span><span class="llm-pill">第 ${fmtNum(d.page || 1)} / ${fmtNum(d.totalPages || 1)} 页</span>`);
  const search = document.getElementById('userSearchInput')?.value?.trim() || '';
  const role = document.getElementById('roleFilter')?.value || 'all';
  const roleLabels = { all: '全部角色', admin: '管理员', creator: '创作者', user: '普通用户', moderator: '版主' };
  const summaryPill = document.getElementById('usersSummaryPill');
  if (summaryPill) {
    summaryPill.textContent = `${roleLabels[role] || role} · ${search ? `关键词「${search}」` : '默认排序'}`;
  }
  if (!d.items || d.items.length === 0) {
    wrap.innerHTML = '<div class="workspace-empty">暂无用户</div>';
    return;
  }
  const roleBadge = (r) => `<span class="badge badge-${r === 'admin' ? 'published' : r === 'creator' ? 'review' : 'draft'}">${roleLabels[r] || r}</span>`;
  let html = '<div class="table-wrap"><table><thead><tr><th>用户名</th><th>昵称</th><th>邮箱</th><th>角色</th><th>游戏数</th><th>注册时间</th><th style="min-width:200px">操作</th></tr></thead><tbody>';
  for (const u of d.items) {
    html += `<tr>
      <td><strong>${escHtml(u.username)}</strong></td>
      <td>${escHtml(u.displayName || '-')}</td>
      <td>${escHtml(u.email || '-')}</td>
      <td>${roleBadge(u.role)}</td>
      <td>${u.gameCount || 0}</td>
      <td>${fmtDate(u.createdAt)}</td>
      <td><div class="action-cell">
        <button class="abtn abtn-edit" onclick="showUserDetail('${u.id}')">编辑</button>
        <button class="abtn abtn-preview" onclick="resetPasswordDialog('${u.id}','${escAttr(u.username)}')">重置密码</button>
        <button class="abtn abtn-delete" onclick="deleteUserConfirm('${u.id}','${escAttr(u.username)}')">删除</button>
      </div></td>
    </tr>`;
  }
  html += '</tbody></table>';
  html += '<div class="pagination"><div class="info">';
  html += `共 ${d.total} 条，第 ${d.page}/${d.totalPages} 页`;
  html += '</div><div class="pages">';
  html += `<button ${d.page <= 1 ? 'disabled' : ''} onclick="userPage=${d.page-1};loadUsers()">上一页</button>`;
  for (let i = Math.max(1, d.page-2); i <= Math.min(d.totalPages, d.page+2); i++) {
    html += `<button class="${i===d.page?'active':''}" onclick="userPage=${i};loadUsers()">${i}</button>`;
  }
  html += `<button ${d.page >= d.totalPages ? 'disabled' : ''} onclick="userPage=${d.page+1};loadUsers()">下一页</button>`;
  html += '</div></div></div>';
  wrap.innerHTML = html;
}

async function showUserDetail(id) {
  document.getElementById('userListView').style.display = 'none';
  const dv = document.getElementById('userDetailView');
  dv.style.display = 'block';
  dv.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    const u = await api('/users/' + id);
    renderUserDetail(u);
  } catch (e) {
    dv.innerHTML = `<div class="loading" style="color:#dc2626">${escHtml(e.message)}</div>`;
  }
}

function renderUserDetail(u) {
  const dv = document.getElementById('userDetailView');
  let html = '<div class="workspace-detail-shell">';
  html += `<div class="back-btn" onclick="showUserList()">← 返回列表</div>`;
  html += `<div class="detail-header"><h2>${escHtml(u.displayName || u.username)}</h2></div>`;
  html += '<div class="detail-stats">';
  html += `<div class="detail-stat"><div class="v">${u.gameCount||0}</div><div class="l">游戏</div></div>`;
  html += `<div class="detail-stat"><div class="v">${fmtNum(u.totalPlays)}</div><div class="l">总播放</div></div>`;
  html += `<div class="detail-stat"><div class="v">${u.followerCount||0}</div><div class="l">粉丝</div></div>`;
  html += `<div class="detail-stat"><div class="v">${u.followingCount||0}</div><div class="l">关注</div></div>`;
  html += '</div>';
  html += '<div class="section-title">编辑用户信息</div>';
  html += '<div class="form-grid">';
  html += `<div class="form-group"><label>用户名</label><input type="text" id="u-username" value="${escAttr(u.username)}"></div>`;
  html += `<div class="form-group"><label>昵称</label><input type="text" id="u-displayName" value="${escAttr(u.displayName||'')}"></div>`;
  html += `<div class="form-group"><label>邮箱</label><input type="text" id="u-email" value="${escAttr(u.email||'')}"></div>`;
  html += `<div class="form-group"><label>手机</label><input type="text" id="u-phone" value="${escAttr(u.phone||'')}"></div>`;
  html += `<div class="form-group"><label>角色</label><select id="u-role">
    <option value="user" ${u.role==='user'?'selected':''}>用户</option>
    <option value="creator" ${u.role==='creator'?'selected':''}>创作者</option>
    <option value="moderator" ${u.role==='moderator'?'selected':''}>版主</option>
    <option value="admin" ${u.role==='admin'?'selected':''}>管理员</option>
  </select></div>`;
  html += `<div class="form-group"><label>Pro 会员</label><select id="u-isPro">
    <option value="false" ${!u.isPro?'selected':''}>否</option>
    <option value="true" ${u.isPro?'selected':''}>是</option>
  </select></div>`;
  html += `<div class="form-group full"><label>简介</label><textarea id="u-bio" rows="2">${escHtml(u.bio||'')}</textarea></div>`;
  html += '</div>';
  html += `<div style="margin-top:16px;display:flex;gap:10px">
    <button class="btn btn-primary" onclick="saveUser('${u.id}')">保存修改</button>
    <button class="btn btn-secondary" onclick="resetPasswordDialog('${u.id}','${escAttr(u.username)}')">重置密码</button>
    <button class="btn btn-danger" onclick="deleteUserConfirm('${u.id}','${escAttr(u.username)}')">删除用户</button>
  </div>`;
  html += '</div>';
  dv.innerHTML = html;
}

function showUserList() {
  document.getElementById('userListView').style.display = 'block';
  document.getElementById('userDetailView').style.display = 'none';
}

async function saveUser(id) {
  const data = {
    username: document.getElementById('u-username').value.trim(),
    displayName: document.getElementById('u-displayName').value.trim(),
    email: document.getElementById('u-email').value.trim(),
    phone: document.getElementById('u-phone').value.trim(),
    role: document.getElementById('u-role').value,
    isPro: document.getElementById('u-isPro').value === 'true',
    bio: document.getElementById('u-bio').value.trim(),
  };
  if (!data.username) { toast('用户名不能为空', 'error'); return; }
  try {
    await api('/users/' + id, { method: 'PUT', body: data });
    toast('用户信息已更新');
    showUserDetail(id);
  } catch (e) { toast('更新失败: ' + e.message, 'error'); }
}

async function deleteUserConfirm(id, name) {
  const yes = await confirmDialog('确认删除用户', `删除用户「${name}」将同时删除其所有游戏，此操作不可撤销。`);
  if (!yes) return;
  try {
    await api('/users/' + id, { method: 'DELETE' });
    toast('用户已删除');
    showUserList();
    loadUsers();
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

async function resetPasswordDialog(id, name) {
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `<div class="confirm-box" style="width:400px">
    <h4>重置密码 - ${escHtml(name)}</h4>
    <div class="form-group" style="margin:12px 0"><label>新密码</label><input type="password" id="rp-password" placeholder="至少6个字符"></div>
    <div class="form-group" style="margin:12px 0"><label>确认密码</label><input type="password" id="rp-confirm" placeholder="再次输入"></div>
    <div class="btns"><button class="btn btn-secondary" id="rpCancel">取消</button><button class="btn btn-primary" id="rpOk">确认重置</button></div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#rpCancel').onclick = () => overlay.remove();
  overlay.querySelector('#rpOk').onclick = async () => {
    const pw = document.getElementById('rp-password').value;
    const cf = document.getElementById('rp-confirm').value;
    if (pw.length < 6) { toast('密码至少6个字符', 'error'); return; }
    if (pw !== cf) { toast('两次密码不一致', 'error'); return; }
    try {
      await api('/user-password/' + id, { method: 'POST', body: { password: pw } });
      toast('密码已重置');
      overlay.remove();
    } catch (e) { toast('重置失败: ' + e.message, 'error'); }
  };
}

function showCreateUser() {
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `<div class="confirm-box" style="width:450px">
    <h4>新建用户</h4>
    <div class="form-group" style="margin:8px 0"><label>用户名 *</label><input type="text" id="nu-username" placeholder="必填"></div>
    <div class="form-group" style="margin:8px 0"><label>昵称</label><input type="text" id="nu-displayName"></div>
    <div class="form-group" style="margin:8px 0"><label>邮箱</label><input type="text" id="nu-email"></div>
    <div class="form-group" style="margin:8px 0"><label>密码</label><input type="password" id="nu-password" placeholder="至少6个字符"></div>
    <div class="form-group" style="margin:8px 0"><label>角色</label><select id="nu-role">
      <option value="user">用户</option><option value="creator">创作者</option>
      <option value="moderator">版主</option><option value="admin">管理员</option>
    </select></div>
    <div class="btns" style="margin-top:16px"><button class="btn btn-secondary" id="nuCancel">取消</button><button class="btn btn-primary" id="nuOk">创建</button></div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('#nuCancel').onclick = () => overlay.remove();
  overlay.querySelector('#nuOk').onclick = async () => {
    const data = {
      username: document.getElementById('nu-username').value.trim(),
      displayName: document.getElementById('nu-displayName').value.trim(),
      email: document.getElementById('nu-email').value.trim(),
      password: document.getElementById('nu-password').value,
      role: document.getElementById('nu-role').value,
    };
    if (!data.username) { toast('用户名必填', 'error'); return; }
    try {
      await api('/users', { method: 'POST', body: data });
      toast('用户已创建');
      overlay.remove();
      loadUsers();
    } catch (e) { toast('创建失败: ' + e.message, 'error'); }
  };
}

// ===================== Generation Logs =====================
let logPage = 1;
const LOG_DETAIL_STAGES = [
  { id: 'understanding', label: '理解游戏需求', desc: '接收请求并整理成可执行的游戏需求', icon: '1' },
  { id: 'designing', label: '构建游戏设计', desc: '确定玩法结构、运行时约束和核心参数', icon: '2' },
  { id: 'generating', label: '生成游戏代码', desc: '生成或改写 HTML5 游戏代码', icon: '3' },
  { id: 'validating', label: '质量校验与修复', desc: '执行 QA、自动修复和运行时校验', icon: '4' },
  { id: 'finalizing', label: '发布生成结果', desc: '保存 bundle、更新状态并交付结果', icon: '5' },
];
const LOG_STAGE_ALIASES = {
  queued: 'understanding',
  started: 'understanding',
  running: 'understanding',
  submitting: 'understanding',
  request_normalized: 'understanding',
  intent_parse: 'understanding',
  intent_parsing: 'understanding',
  spec_build: 'understanding',
  runtime_profile_select: 'designing',
  template_match: 'designing',
  template_matching: 'designing',
  contract_compose: 'designing',
  designing: 'designing',
  pipeline_run: 'generating',
  iteration: 'generating',
  code_generate: 'generating',
  code_generating: 'generating',
  logic_generate: 'generating',
  qa_fix: 'validating',
  qa_checking: 'validating',
  contract_qa: 'validating',
  targeted_remediation: 'validating',
  runtime_qa: 'validating',
  runtime_simulation_qa: 'validating',
  code_review: 'validating',
  failed: 'validating',
  publishing: 'finalizing',
  completed: 'finalizing',
  succeeded: 'finalizing',
};

function resolveLogDetailStage(rawStage) {
  return LOG_STAGE_ALIASES[rawStage] || rawStage || 'understanding';
}

async function loadLogs() {
  const search = document.getElementById('log-search').value;
  const status = document.getElementById('log-status').value;
  const params = new URLSearchParams({ page: logPage, limit: 20, status });
  if (search) params.set('search', search);
  try {
    const d = await api('/genlog?' + params);
    renderLogs(d);
  } catch(e) {
    document.getElementById('logTableWrap').innerHTML = '<div class="workspace-empty" style="color:#dc2626">加载失败: ' + escHtml(e.message) + '</div>';
  }
}

async function showLogDetail(gameId, focusSection) {
  try {
    const g = await api('/games/' + gameId);
    const bundle = g.bundles && g.bundles[0];
    logSourceCache[gameId] = {
      html: bundle?.htmlCode || '',
      css: bundle?.cssCode || '',
      js: bundle?.jsCode || '',
    };
    const sourceSections = getLogSourceSections(bundle);
    const meta = Object.assign({}, (bundle && bundle.generationMeta) || {}, (bundle && bundle.metadata) || {});
    const spec = meta.gameSpec || bundle?.spec || null;
    const gameBaseUrl = window.location.origin;
    const previewUrl = g.previewUrl || (gameBaseUrl + '/games/' + gameId + '/preview');
    const statusText = statusLabel(g.status);
    const author = g.author?.displayName || g.author?.username || '-';
    const finalStageRaw = g.failedStage || (g.status === 'draft' || g.status === 'published' ? 'completed' : g.status === 'generating' ? 'code_generating' : null);
    const finalStage = resolveLogDetailStage(finalStageRaw);
    const finalStageLabel = LOG_DETAIL_STAGES.find(s => s.id === finalStage)?.label || finalStage || '-';
    const finalStageIndex = LOG_DETAIL_STAGES.findIndex(s => s.id === finalStage);
    const genSeconds = meta.genTimeMs ? (meta.genTimeMs / 1000).toFixed(1) + 's' : '-';
    const codeSize = bundle?.codeSizeBytes ? (bundle.codeSizeBytes / 1024).toFixed(1) + ' KB' : '-';
    const retryCount = Number(g.retryCount || 0);
    const qaRetries = Number(meta.qaRetries || 0);
    const iterationRetries = Number(meta.iterationRetries || 0);
    const qaText = meta.qaPassed === true ? '通过' : meta.qaPassed === false ? '未通过' : '-';
    const qualityText = meta.qualityScore !== undefined && meta.qualityScore !== null ? String(meta.qualityScore) : '-';
    const strategyText = meta.strategy || '-';
    const summaryNote = g.failedReason
      ? '当前后台持久化的是最后一次失败摘要。若需要完整逐步骤重试与报错细节，请到 VeFaaS / 应用日志查看。'
      : '当前记录没有最终失败摘要。若要查看生成过程中的细粒度进度与临时重试，仍需结合实时 WebSocket 或 VeFaaS 日志。';

    let detail = '<div class="log-detail">';
    detail += '<div class="log-hero">';
    detail += '<div class="log-hero-main">';
    detail += '<div class="log-hero-eyebrow">生成记录详情</div>';
    detail += '<div class="log-hero-title">' + escHtml(g.title || '未命名游戏') + '</div>';
    detail += '<div class="log-hero-meta">';
    detail += '<span class="log-chip">作者 <strong>' + escHtml(author) + '</strong></span>';
    detail += '<span class="log-chip">游戏 ID <code>' + escHtml(gameId) + '</code></span>';
    detail += '<span class="log-chip">创建于 ' + new Date(g.createdAt).toLocaleString('zh-CN') + '</span>';
    detail += '</div></div>';
    detail += '<span class="log-badge-lg badge badge-' + g.status + '">' + escHtml(statusText) + '</span>';
    detail += '</div>';

    detail += '<div class="log-stat-grid">';
    detail += '<div class="log-stat-card"><div class="value">' + retryCount + '</div><div class="label">总重试次数</div></div>';
    detail += '<div class="log-stat-card"><div class="value">' + escHtml(finalStageLabel) + '</div><div class="label">最终阶段</div></div>';
    detail += '<div class="log-stat-card"><div class="value">' + genSeconds + '</div><div class="label">生成耗时</div></div>';
    detail += '<div class="log-stat-card"><div class="value">' + qaRetries + '</div><div class="label">QA 重试</div></div>';
    detail += '<div class="log-stat-card"><div class="value">' + qualityText + '</div><div class="label">质量分</div></div>';
    detail += '<div class="log-stat-card"><div class="value">v' + (bundle?.version || 0) + '</div><div class="label">当前版本</div></div>';
    detail += '</div>';

    detail += '<div class="log-grid">';
    detail += '<div class="log-column">';

    detail += '<div class="log-card">';
    detail += '<div class="log-card-title"><h4>阶段概览</h4><span class="log-card-sub">参照主流程展示当前能确认的最终阶段</span></div>';
    detail += '<div class="log-stage-grid">';
    detail += LOG_DETAIL_STAGES.map((stage, index) => {
      let stageCls = 'pending';
      let stageState = '未到达';
      if (g.failedStage === stage.id) {
        stageCls = 'failed';
        stageState = '失败';
      } else if (finalStageIndex >= 0 && index < finalStageIndex) {
        stageCls = 'done';
        stageState = '已完成';
      } else if (finalStageIndex >= 0 && index === finalStageIndex && !g.failedStage) {
        stageCls = 'current';
        stageState = stage.id === 'completed' ? '已完成' : '当前';
      }
      return '<div class="log-stage ' + stageCls + '">' +
        '<div class="log-stage-top"><div class="log-stage-icon">' + stage.icon + '</div><span class="log-stage-state">' + stageState + '</span></div>' +
        '<div class="log-stage-name">' + escHtml(stage.label) + '</div>' +
        '<div class="log-stage-desc">' + escHtml(stage.desc) + '</div>' +
      '</div>';
    }).join('');
    detail += '</div></div>';

    detail += '<div class="log-card">';
    detail += '<div class="log-card-title"><h4>失败记录</h4><span class="log-card-sub">当前后台仅保存最终失败摘要</span></div>';
    detail += '<div class="log-kv-grid">';
    detail += '<div class="log-kv"><div class="k">失败阶段</div><div class="v">' + escHtml(finalStageLabel) + '</div></div>';
    detail += '<div class="log-kv"><div class="k">原始阶段</div><div class="v">' + escHtml(g.failedStage || finalStageRaw || '-') + '</div></div>';
    detail += '<div class="log-kv"><div class="k">最后失败时间</div><div class="v">' + (g.lastErrorAt ? new Date(g.lastErrorAt).toLocaleString('zh-CN') : '-') + '</div></div>';
    detail += '<div class="log-kv"><div class="k">总重试次数</div><div class="v">' + retryCount + '</div></div>';
    detail += '<div class="log-kv"><div class="k">定位建议</div><div class="v">后台详情看最终摘要，完整逐步骤报错请查 VeFaaS / 应用日志</div></div>';
    detail += '</div>';
    detail += g.failedReason
      ? '<div class="log-alert" style="margin-top:14px">' + escHtml(g.failedReason) + '</div>'
      : '<div class="log-note" style="margin-top:14px"><strong>暂无最终失败摘要。</strong> 这通常表示该任务最终成功，或中途临时重试未被持久化到数据库。</div>';
    detail += '<div class="log-note" style="margin-top:12px">' + summaryNote + '</div>';
    detail += '</div>';

    detail += '<div class="log-card">';
    detail += '<div class="log-card-title"><h4>用户输入</h4><span class="log-card-sub">用于触发生成的原始提示词</span></div>';
    detail += '<div class="log-textbox">' + escHtml(g.description || '(无)') + '</div>';
    detail += '</div>';

    if (spec) {
      detail += '<div class="log-card">';
      detail += '<div class="log-card-title"><h4>Game Spec</h4><span class="log-card-sub">已保存的结构化规格</span></div>';
      detail += '<pre class="log-code">' + escHtml(JSON.stringify(spec, null, 2)) + '</pre>';
      detail += '</div>';
    }

    detail += '<div class="log-card" data-log-source-card="true">';
    detail += '<div class="log-card-title"><h4>游戏源码</h4><span class="log-card-sub">查看当前版本 bundle 保存的源码</span></div>';
    if (sourceSections.length) {
      detail += '<div class="log-source-actions">';
      detail += '<div class="log-source-summary">当前版本 v' + (bundle?.version || 0) + '，共 ' + sourceSections.length + ' 段源码，可直接复制排查问题。</div>';
      detail += '<button class="btn btn-sm btn-secondary" onclick="copyAllLogSource(\'' + gameId + '\')">复制全部源码</button>';
      detail += '</div>';
      detail += '<div class="log-source-grid">';
      detail += sourceSections.map(section => {
        return '<div class="log-source-block">' +
          '<div class="log-source-head">' +
            '<div class="log-source-meta">' +
              '<div class="log-source-name">' + section.label + '</div>' +
              '<div class="log-source-size">' + formatTextSize(section.code) + '</div>' +
            '</div>' +
            '<button class="btn btn-sm btn-secondary" onclick="copyLogSourceSection(\'' + gameId + '\', \'' + section.key + '\')">复制</button>' +
          '</div>' +
          '<pre class="log-code">' + escHtml(section.code) + '</pre>' +
        '</div>';
      }).join('');
      detail += '</div>';
    } else {
      detail += '<div class="log-empty">当前版本没有保存可查看的 HTML、CSS 或 JavaScript 源码。</div>';
    }
    detail += '</div>';

    detail += '</div>';
    detail += '<div class="log-column">';

    detail += '<div class="log-card">';
    detail += '<div class="log-card-title"><h4>记录摘要</h4><span class="log-card-sub">和主页面卡片相同的信息层级</span></div>';
    detail += '<div class="log-kv-grid">';
    detail += '<div class="log-kv"><div class="k">状态</div><div class="v"><span class="badge badge-' + g.status + '">' + escHtml(statusText) + '</span></div></div>';
    detail += '<div class="log-kv"><div class="k">最终阶段</div><div class="v">' + escHtml(finalStageLabel) + '</div></div>';
    detail += '<div class="log-kv"><div class="k">作者</div><div class="v">' + escHtml(author) + '</div></div>';
    detail += '<div class="log-kv"><div class="k">更新时间</div><div class="v">' + (g.updatedAt ? new Date(g.updatedAt).toLocaleString('zh-CN') : '-') + '</div></div>';
    detail += '<div class="log-kv"><div class="k">游戏 ID</div><div class="v"><code>' + escHtml(gameId) + '</code></div></div>';
    detail += '<div class="log-kv"><div class="k">版本</div><div class="v">v' + (bundle?.version || 0) + '</div></div>';
    detail += '</div>';
    detail += '</div>';

    detail += '<div class="log-card">';
    detail += '<div class="log-card-title"><h4>生成指标</h4><span class="log-card-sub">来自 bundle metadata / generationMeta</span></div>';
    detail += '<div class="log-kv-grid">';
    detail += '<div class="log-kv"><div class="k">生成策略</div><div class="v">' + escHtml(strategyText) + '</div></div>';
    detail += '<div class="log-kv"><div class="k">耗时</div><div class="v">' + genSeconds + '</div></div>';
    detail += '<div class="log-kv"><div class="k">QA 结果</div><div class="v">' + qaText + '</div></div>';
    detail += '<div class="log-kv"><div class="k">QA 重试</div><div class="v">' + qaRetries + '</div></div>';
    detail += '<div class="log-kv"><div class="k">迭代重试</div><div class="v">' + iterationRetries + '</div></div>';
    detail += '<div class="log-kv"><div class="k">代码大小</div><div class="v">' + codeSize + '</div></div>';
    detail += '<div class="log-kv"><div class="k">质量分</div><div class="v">' + qualityText + '</div></div>';
    detail += '<div class="log-kv"><div class="k">发布时间</div><div class="v">' + (g.publishedAt ? new Date(g.publishedAt).toLocaleString('zh-CN') : '-') + '</div></div>';
    detail += '</div>';
    detail += '</div>';

    detail += '<div class="log-card">';
    detail += '<div class="log-card-title"><h4>前台入口</h4><span class="log-card-sub">便于直接验证线上展示</span></div>';
    detail += '<div class="log-linkbox">';
    detail += '<div class="log-linkicon">↗</div>';
    detail += '<div class="log-linkmeta"><div class="label">预览地址</div><a href="' + previewUrl + '" target="_blank">' + previewUrl + '</a></div>';
    detail += '</div>';
    detail += '</div>';

    detail += '</div>';
    detail += '</div>';
    detail += '</div>';

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = '<div class="modal" style="max-width:1120px"><div class="modal-header"><h3>生成记录详情</h3><button class="modal-close" onclick="this.closest(\'.modal-overlay\').remove()">&times;</button></div><div class="modal-body">' + detail + '</div></div>';
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    if (focusSection === 'source') {
      const sourceCard = overlay.querySelector('[data-log-source-card="true"]');
      if (sourceCard) {
        requestAnimationFrame(() => sourceCard.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      }
    }
  } catch(e) {
    showDetailErrorOverlay('加载生成记录失败', e.message, {
      overlayKey: 'generation-log:error:' + gameId,
      maxWidth: '560px',
    });
  }
}

function showDetailOverlay(title, detailHtml, options = {}) {
  if (options.overlayKey) {
    document.querySelectorAll('.modal-overlay[data-overlay-key]').forEach(node => {
      if (node.dataset.overlayKey === options.overlayKey) node.remove();
    });
  }
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  if (options.overlayKey) overlay.dataset.overlayKey = options.overlayKey;
  overlay.innerHTML = '<div class="modal" style="max-width:' + escAttr(options.maxWidth || '1120px') + '"><div class="modal-header"><h3>' + escHtml(title) + '</h3><button class="modal-close" onclick="this.closest(\'.modal-overlay\').remove()">&times;</button></div><div class="modal-body">' + detailHtml + '</div></div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  if (options.focusSelector) {
    const target = overlay.querySelector(options.focusSelector);
    if (target) requestAnimationFrame(() => target.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }
  return overlay;
}

function showDetailErrorOverlay(title, message, options = {}) {
  const detailHtml = `
    <div class="workspace-empty" style="color:#dc2626;text-align:left">
      <div style="font-size:14px;font-weight:600;margin-bottom:8px">${escHtml(title)}</div>
      <div>${escHtml(message || '未知错误')}</div>
    </div>
  `;
  return showDetailOverlay(title, detailHtml, options);
}

async function showTaskDetailModal(taskId, options = {}) {
  try {
    const task = await api('/tasks/' + taskId);
    const refreshAction = options.focusSection === 'source'
      ? `showTaskDetailModal('${task.id}', { focusSection: 'source' })`
      : `showTaskDetailModal('${task.id}')`;
    const detail = buildTaskDetailMarkup(task, {
      title: options.title || '生成记录详情',
      refreshAction,
    });
    showDetailOverlay(options.title || '生成记录详情', detail, {
      overlayKey: 'task-detail:' + task.id,
      maxWidth: '1120px',
      focusSelector: options.focusSection === 'source' ? '[data-task-source-card="true"]' : null,
    });
  } catch (e) {
    showDetailErrorOverlay('加载任务详情失败', e.message, {
      overlayKey: 'task-detail:error:' + taskId,
      maxWidth: '560px',
    });
  }
}

function renderLogs(d) {
  setHtml('logsSummary', `<span class="llm-pill">共 ${fmtNum(d.total || 0)} 条记录</span><span class="llm-pill">第 ${fmtNum(d.page || 1)} / ${fmtNum(d.totalPages || 1)} 页</span>`);
  const search = document.getElementById('log-search')?.value?.trim() || '';
  const status = document.getElementById('log-status')?.value || 'all';
  const summaryPill = document.getElementById('logsSummaryPill');
  if (summaryPill) {
    summaryPill.textContent = `${status === 'all' ? '全部状态' : statusLabel(status)} · ${search ? `关键词「${search}」` : '默认排序'}`;
  }
  const statusLabels = { generating: '生成中', draft: '草稿', published: '已发布', failed: '失败', banned: '已封禁', review: '审核中' };
  const statusColors = { generating: 'generating', draft: 'draft', published: 'published', failed: 'failed', banned: 'banned', review: 'review' };
  const gameBaseUrl = window.location.origin;

  let html = '<div class="table-wrap"><table><thead><tr>' +
    '<th>用户</th><th>提示词</th><th>游戏标题</th><th>状态</th>' +
    '<th>失败信息</th><th>策略</th><th>耗时</th><th>大小</th><th>QA</th><th>创建时间</th><th>操作</th>' +
    '</tr></thead><tbody>';

  if (!d.items.length) {
    html += '<tr><td colspan="11" style="text-align:center;color:#94a3b8;padding:40px">暂无记录</td></tr>';
  }

  for (const g of d.items) {
    const desc = g.description ? (g.description.length > 50 ? g.description.slice(0, 50) + '...' : g.description) : '-';
    const author = g.author ? (g.author.displayName || g.author.username) : '-';
    const st = statusLabels[g.status] || g.status;
    const stCls = statusColors[g.status] || 'draft';
    const strategy = g.strategy || '-';
    const strategySummary = [
      `<div>${escHtml(strategy)}</div>`,
      g.generationTier ? `<div style="font-size:11px;color:#64748b;margin-top:4px">层级 ${escHtml(g.generationTier)}</div>` : '',
    ].filter(Boolean).join('');
    const timeMs = g.genTimeMs ? (g.genTimeMs / 1000).toFixed(1) + 's' : '-';
    const size = g.codeSizeBytes ? (g.codeSizeBytes / 1024).toFixed(1) + 'KB' : '-';
    const qa = g.qaPassed === true ? '<span style="color:#16a34a">通过</span>' : g.qaPassed === false ? '<span style="color:#dc2626">失败</span>' : '-';
    const retrySummaryParts = [];
    if (g.retryCount) retrySummaryParts.push('总重试 ' + g.retryCount);
    if (Number(g.qaRetries || 0) > 0) retrySummaryParts.push('QA ' + g.qaRetries);
    if (Number(g.iterationRetries || 0) > 0) retrySummaryParts.push('迭代 ' + g.iterationRetries);
    if (Number(g.qaWarningCount || 0) > 0) retrySummaryParts.push('告警 ' + g.qaWarningCount);
    if (g.runtimeQaUnavailable) {
      retrySummaryParts.push('Runtime QA 不可用' + (g.runtimeQaUnavailableKind ? `（${g.runtimeQaUnavailableKind}）` : ''));
    }
    const qaSummary = retrySummaryParts.length
      ? qa + '<div style="font-size:11px;color:#64748b;margin-top:4px">' + retrySummaryParts.join(' / ') + '</div>'
      : qa;
    const time = new Date(g.createdAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    const previewUrl = g.previewUrl || (gameBaseUrl + '/games/' + g.gameId + '/preview');
    const failureReason = g.failedReason ? (g.failedReason.length > 36 ? g.failedReason.slice(0, 36) + '...' : g.failedReason) : '-';
    const failureSummary = g.failedStage
      ? (`<div style="font-size:12px;color:#dc2626">${escHtml(g.failedStage)}</div><div style="font-size:11px;color:#64748b" title="${escAttr(g.failedReason || '')}">${escHtml(failureReason)}</div>`)
      : '-';
    const titleMeta = [];
    if (g.intentFingerprint) titleMeta.push('Intent ' + formatFingerprint(g.intentFingerprint));
    if (g.specFingerprint) titleMeta.push('Spec ' + formatFingerprint(g.specFingerprint));
    const sourceAction = g.taskId
      ? `showTaskDetailModal('${g.taskId}', { focusSection: 'source' })`
      : `showLogDetail('${g.gameId}', 'source')`;
    const detailAction = g.taskId
      ? `showTaskDetailModal('${g.taskId}')`
      : `showLogDetail('${g.gameId}')`;

    html += '<tr>' +
      '<td><strong>' + escHtml(author) + '</strong></td>' +
      '<td title="' + escAttr(g.description || '') + '">' + escHtml(desc) + '</td>' +
      '<td><div>' + escHtml(g.title) + '</div>' + (titleMeta.length ? '<div style="font-size:11px;color:#64748b;margin-top:4px">' + escHtml(titleMeta.join(' · ')) + '</div>' : '') + '</td>' +
      '<td><span class="badge badge-' + stCls + '">' + st + '</span></td>' +
      '<td>' + failureSummary + '</td>' +
      '<td>' + strategySummary + '</td>' +
      '<td>' + timeMs + '</td>' +
      '<td>' + size + '</td>' +
      '<td>' + qaSummary + '</td>' +
      '<td>' + time + '</td>' +
      '<td>' +
        '<a href="' + previewUrl + '" target="_blank" class="btn btn-sm btn-secondary" style="margin-right:4px">预览</a>' +
        '<button class="btn btn-sm btn-secondary" style="margin-right:4px" onclick="' + escAttr(sourceAction) + '">源码</button>' +
        '<button class="btn btn-sm btn-secondary" onclick="' + escAttr(detailAction) + '">详情</button>' +
      '</td>' +
      '</tr>';
  }
  html += '</tbody></table></div>';
  html += '<div class="pagination"><div class="info">共 ' + d.total + ' 条，第 ' + d.page + '/' + d.totalPages + ' 页</div><div class="pages">';
  html += '<button ' + (d.page <= 1 ? 'disabled' : '') + ' onclick="logPage=' + (d.page-1) + ';loadLogs()">上一页</button>';
  for (let i = Math.max(1, d.page-2); i <= Math.min(d.totalPages, d.page+2); i++) {
    html += '<button class="' + (i===d.page?'active':'') + '" onclick="logPage=' + i + ';loadLogs()">' + i + '</button>';
  }
  html += '<button ' + (d.page >= d.totalPages ? 'disabled' : '') + ' onclick="logPage=' + (d.page+1) + ';loadLogs()">下一页</button>';
  html += '</div></div>';
  document.getElementById('logTableWrap').innerHTML = html;
}

