// ===================== Dashboard =====================
function localDateInputValue(date) {
  const d = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(d.getTime())) return '';
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function getRangeInputIds(scope) {
  if (scope === 'dashboard') {
    return { from: 'dashboardRangeFrom', to: 'dashboardRangeTo' };
  }
  if (scope === 'subscriptions') {
    return { from: 'subscriptionRangeFrom', to: 'subscriptionRangeTo' };
  }
  throw new Error('Unknown range scope: ' + scope);
}

function applyRangePreset(scope, preset, shouldLoad = true) {
  const { from, to } = getRangeInputIds(scope);
  const fromInput = document.getElementById(from);
  const toInput = document.getElementById(to);
  if (!fromInput || !toInput) return;

  const today = new Date();
  const end = localDateInputValue(today);
  const startDate = new Date(today);

  if (preset === 'all') {
    fromInput.value = '';
    toInput.value = '';
  } else if (preset === '7d') {
    startDate.setDate(startDate.getDate() - 6);
    fromInput.value = localDateInputValue(startDate);
    toInput.value = end;
  } else if (preset === '30d') {
    startDate.setDate(startDate.getDate() - 29);
    fromInput.value = localDateInputValue(startDate);
    toInput.value = end;
  } else if (preset === 'month') {
    startDate.setDate(1);
    fromInput.value = localDateInputValue(startDate);
    toInput.value = end;
  }

  if (shouldLoad) {
    if (scope === 'dashboard') loadStats();
    if (scope === 'subscriptions') loadSubscriptionPlans();
  }
}

function buildRangeQuery(scope) {
  const { from, to } = getRangeInputIds(scope);
  const fromValue = document.getElementById(from)?.value?.trim() || '';
  const toValue = document.getElementById(to)?.value?.trim() || '';
  const params = new URLSearchParams();
  if (fromValue) params.set('from', fromValue);
  if (toValue) params.set('to', toValue);
  const query = params.toString();
  return query ? `?${query}` : '';
}

function getRangeLabel(scope) {
  const { from, to } = getRangeInputIds(scope);
  const fromValue = document.getElementById(from)?.value?.trim() || '';
  const toValue = document.getElementById(to)?.value?.trim() || '';
  if (!fromValue && !toValue) return '全部时间';
  if (fromValue && toValue) return `${fromValue} 至 ${toValue}`;
  if (fromValue) return `${fromValue} 起`;
  return `截至 ${toValue}`;
}

function fmtCurrencyYuan(value) {
  const amount = Number(value || 0);
  return `¥${amount.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function setHtml(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function renderDashboardChips(containerId, rows, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) return;
  const list = Array.isArray(rows) ? rows : Object.entries(rows || {});
  if (!list.length) {
    container.innerHTML = `<div class="dashboard-empty">${escHtml(options.emptyText || '暂无数据')}</div>`;
    return;
  }
  container.innerHTML = list.map(([key, value]) => {
    const item = options.mapItem ? options.mapItem(key, value) : {
      label: key,
      value: fmtNum(value),
      className: 'badge-draft'
    };
    return `<div class="dashboard-chip ${item.className || ''}"><span class="k">${escHtml(item.label || key)}</span><span class="v">${escHtml(String(item.value ?? value))}</span></div>`;
  }).join('');
}

async function loadStats() {
  try {
    const d = await api('/stats' + buildRangeQuery('dashboard'));
    const subscription = d.subscriptionOverview || {};
    const rangeLabel = getRangeLabel('dashboard');
    document.getElementById('s-games').textContent = fmtNum(d.totalGames);
    document.getElementById('s-plays').textContent = fmtNum(d.totalPlays);
    document.getElementById('s-likes').textContent = fmtNum(d.totalLikes);
    document.getElementById('s-users').textContent = fmtNum(d.totalUsers);
    document.getElementById('s-forks').textContent = fmtNum(d.totalForks);
    document.getElementById('s-quality').textContent = Number(d.avgQualityScore || 0).toFixed(1);
    document.getElementById('s-retries').textContent = Number(d.avgRetryCount || 0).toFixed(1);
    document.getElementById('s-sub-plans').textContent = fmtNum(subscription.totalPlans || 0);
    document.getElementById('s-revenue').textContent = fmtCurrencyYuan(subscription.totalRevenueYuan || 0);
    document.getElementById('s-revenue-sub').textContent = `${rangeLabel} 已支付 ${fmtNum(subscription.paidOrderCount || 0)} 单`;
    document.getElementById('dashboardRangeSummary').textContent = rangeLabel;
    document.getElementById('dashboardActivePlans').textContent = fmtNum(subscription.activePlans || 0);
    document.getElementById('dashboardActiveSubscribers').textContent = fmtNum(subscription.activeSubscribers || 0);
    document.getElementById('dashboardPaidOrders').textContent = fmtNum(subscription.paidOrderCount || 0);
    const refreshedAt = document.getElementById('dashboardLastRefreshed');
    if (refreshedAt) refreshedAt.textContent = new Date().toLocaleString('zh-CN', { hour12: false });

    const statusLabels = { published: '已发布', draft: '草稿', banned: '已封禁', review: '审核中', generating: '生成中', failed: '失败' };
    renderDashboardChips('statusBars', Object.entries(d.byStatus || {}), {
      emptyText: '暂无状态数据',
      mapItem: (k, v) => ({ label: statusLabels[k] || k, value: fmtNum(v), className: `badge-${k}` })
    });

    renderDashboardChips('failureStageBars', Object.entries(d.failedByStage || {}), {
      emptyText: '暂无失败阶段数据',
      mapItem: (k, v) => ({ label: k, value: fmtNum(v), className: 'badge-failed' })
    });

    renderDashboardChips('retryBars', Object.entries(d.retryBuckets || {}), {
      emptyText: '暂无重试分布数据',
      mapItem: (k, v) => ({ label: k, value: fmtNum(v), className: 'badge-review' })
    });

    renderDashboardChips('qualityBars', Object.entries(d.qualityBuckets || {}), {
      emptyText: '暂无质量分数据',
      mapItem: (k, v) => ({ label: k, value: fmtNum(v), className: 'badge-published' })
    });

    const failureReasonList = document.getElementById('failureReasonList');
    failureReasonList.innerHTML = '';
    const topFailureReasons = d.topFailureReasons || [];
    if (!topFailureReasons.length) {
      failureReasonList.innerHTML = '<div class="dashboard-empty">暂无高频失败原因</div>';
    } else {
      failureReasonList.innerHTML = topFailureReasons.map(item => (
        `<div class="dashboard-reason-item">` +
        `<div class="dashboard-reason-top">` +
        `<div>` +
        `<div class="dashboard-reason-stage">${escHtml(item.stage || 'unknown')}</div>` +
        `<div class="dashboard-reason-text">${escHtml(item.reason || 'unknown error')}</div>` +
        `</div>` +
        `<div class="dashboard-reason-count">x${fmtNum(Number(item.count || 0))}</div>` +
        `</div>` +
        `</div>`
      )).join('');
    }
  } catch (e) {
    toast('加载统计失败: ' + e.message, 'error');
  }
}

// ===================== Game List =====================
function debouncedSearch() {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => { currentPage = 1; loadGames(); }, 300);
}

async function loadGames() {
  const search = document.getElementById('searchInput').value.trim();
  const status = document.getElementById('statusFilter').value;
  const wrap = document.getElementById('gameTableWrap');
  wrap.innerHTML = '<div class="workspace-empty"><div class="spinner"></div><div style="margin-top:8px">加载中...</div></div>';

  try {
    const d = await api(`/games?page=${currentPage}&limit=20&search=${encodeURIComponent(search)}&status=${status}`);
    totalPages = d.totalPages || 1;
    lastGameListResponse = d;
    currentGameListItems = Array.isArray(d.items) ? d.items : [];
    syncSelectedGameIdsWithCurrentList();
    renderGameTable(d);
  } catch (e) {
    wrap.innerHTML = `<div class="loading" style="color:#dc2626">加载失败: ${escHtml(e.message)}</div>`;
    lastGameListResponse = null;
    currentGameListItems = [];
    selectedGameIds = new Set();
    renderGameBatchBar();
  }
}

async function refreshGameTypes() {
  const yes = await confirmDialog('刷新游戏类型', '将历史游戏和生成记录的类型统一刷新为 4 个运营分类，是否继续？');
  if (!yes) return;

  try {
    const result = await api('/games/refresh-types', { method: 'POST', body: {} });
    toast(`类型刷新完成：游戏 ${result.gamesUpdated} 条，Bundle ${result.bundlesUpdated} 条，任务 ${result.tasksUpdated} 条`);
    loadGames();
  } catch (e) {
    toast('刷新类型失败: ' + e.message, 'error');
  }
}

function renderGameTable(d) {
  const wrap = document.getElementById('gameTableWrap');
  setHtml('gamesSummary', `<span class="llm-pill">共 ${fmtNum(d.total || 0)} 款作品</span><span class="llm-pill">第 ${fmtNum(d.page || 1)} / ${fmtNum(d.totalPages || 1)} 页</span>`);
  const search = document.getElementById('searchInput')?.value?.trim() || '';
  const status = document.getElementById('statusFilter')?.value || 'all';
  const summaryPill = document.getElementById('gamesSummaryPill');
  if (summaryPill) {
    summaryPill.textContent = `${status === 'all' ? '全部状态' : statusLabel(status)} · ${search ? `关键词「${search}」` : '默认排序'}`;
  }
  renderGameBatchBar();
  if (!d.items || d.items.length === 0) {
    wrap.innerHTML = '<div class="workspace-empty">暂无游戏数据</div>';
    return;
  }
  let html = '<div class="table-wrap"><table><thead><tr><th style="width:44px"><input id="gameSelectAllInput" type="checkbox" onchange="toggleAllGameSelection(this.checked)" ' + (isAllGamesSelected() ? 'checked' : '') + '></th><th>标题</th><th>类型</th><th>状态</th><th>播放</th><th>点赞</th><th>质量分</th><th>大小</th><th style="min-width:280px">操作</th></tr></thead><tbody>';
  for (const g of d.items) {
    const bundle = g.bundles && g.bundles[0];
    const size = bundle && bundle.codeSizeBytes ? fmtSize(bundle.codeSizeBytes) : '-';
    const isPub = g.status === 'published';
    const isSelected = selectedGameIds.has(g.id);
    const coverThumb = g.coverUrl
      ? `<button class="games-cover-thumb" title="预览封面" onclick="event.stopPropagation();previewGameCover('${g.id}','${escAttr(g.title)}','${escAttr(g.coverUrl)}')"><img src="${escAttr(g.coverUrl)}" alt="${escAttr(g.title)}"></button>`
      : `<div class="games-cover-thumb" title="暂无封面"><span class="games-cover-empty">无封面</span></div>`;
    html += `<tr>
      <td><input type="checkbox" ${isSelected ? 'checked' : ''} onchange="toggleGameSelection('${g.id}', this.checked)"></td>
      <td><div class="games-cover-cell">${coverThumb}<div style="min-width:0"><strong style="cursor:pointer" onclick="showGameDetail('${g.id}')">${escHtml(g.title)}</strong><div style="font-size:11px;color:#94a3b8;margin-top:2px">${escHtml(g.author?.displayName || g.author?.username || '')}</div></div></div></td>
      <td>${escHtml(g.gameType || '-')}</td>
      <td><span class="badge badge-${g.status}">${statusLabel(g.status)}</span></td>
      <td>${fmtNum(g.playCount)}</td>
      <td>${fmtNum(g.likeCount)}</td>
      <td>${Number(g.qualityScore || 0).toFixed(1)}</td>
      <td>${size}</td>
      <td><div class="action-cell">
        <button class="abtn abtn-preview" onclick="event.stopPropagation();previewGameCover('${g.id}','${escAttr(g.title)}','${escAttr(g.coverUrl || '')}')">封面</button>
        <button class="abtn abtn-preview" onclick="event.stopPropagation();previewGame('${g.id}','${escAttr(g.title)}','${escAttr(g.previewUrl || '')}')">预览</button>
        <button class="abtn abtn-play" onclick="event.stopPropagation();playGame('${g.id}','${escAttr(g.title)}','${escAttr(g.previewUrl || '')}')">试玩</button>
        ${isPub
          ? `<button class="abtn abtn-unpublish" onclick="event.stopPropagation();quickToggle('${g.id}','draft')">下架</button>`
          : `<button class="abtn abtn-publish" onclick="event.stopPropagation();quickToggle('${g.id}','published')">上架</button>`
        }
        <button class="abtn abtn-edit" onclick="event.stopPropagation();showGameDetail('${g.id}')">编辑</button>
        <button class="abtn abtn-delete" onclick="event.stopPropagation();quickDelete('${g.id}','${escAttr(g.title)}')">删除</button>
      </div></td>
    </tr>`;
  }
  html += '</tbody></table>';
  // Pagination
  html += '<div class="pagination"><div class="info">';
  html += `共 ${d.total} 条，第 ${d.page}/${d.totalPages} 页`;
  html += '</div><div class="pages">';
  html += `<button ${d.page <= 1 ? 'disabled' : ''} onclick="currentPage=${d.page - 1};loadGames()">上一页</button>`;
  for (let i = Math.max(1, d.page - 2); i <= Math.min(d.totalPages, d.page + 2); i++) {
    html += `<button class="${i === d.page ? 'active' : ''}" onclick="currentPage=${i};loadGames()">${i}</button>`;
  }
  html += `<button ${d.page >= d.totalPages ? 'disabled' : ''} onclick="currentPage=${d.page + 1};loadGames()">下一页</button>`;
  html += '</div></div></div>';
  wrap.innerHTML = html;
  refreshGameSelectionUi();
}

function syncSelectedGameIdsWithCurrentList() {
  const currentIdSet = new Set((currentGameListItems || []).map(item => item.id));
  selectedGameIds = new Set(Array.from(selectedGameIds).filter(id => currentIdSet.has(id)));
}

function isAllGamesSelected() {
  return currentGameListItems.length > 0
    && currentGameListItems.every(item => selectedGameIds.has(item.id));
}

function renderGameBatchBar() {
  const wrap = document.getElementById('gamesBatchBar');
  if (!wrap) return;
  const total = currentGameListItems.length;
  const selectedCount = selectedGameIds.size;
  wrap.innerHTML = `
    <div class="games-batch-summary">
      当前页 ${fmtNum(total)} 条，已选择 <strong style="color:#0f172a">${fmtNum(selectedCount)}</strong> 条
    </div>
    <div class="games-batch-actions">
      <button class="btn btn-secondary" onclick="clearGameSelection()" ${selectedCount ? '' : 'disabled'}>清空选择</button>
      <button class="btn btn-success" onclick="batchUpdateGameStatus('published')" ${selectedCount ? '' : 'disabled'}>批量上线</button>
      <button class="btn btn-warning" onclick="batchUpdateGameStatus('draft')" ${selectedCount ? '' : 'disabled'}>批量下线</button>
      <button class="btn btn-danger" onclick="batchDeleteSelectedGames()" ${selectedCount ? '' : 'disabled'}>批量删除</button>
    </div>
  `;
}

function refreshGameSelectionUi() {
  const selectAllInput = document.getElementById('gameSelectAllInput');
  if (!selectAllInput) return;
  const selectedCount = selectedGameIds.size;
  const total = currentGameListItems.length;
  selectAllInput.checked = total > 0 && selectedCount === total;
  selectAllInput.indeterminate = selectedCount > 0 && selectedCount < total;
}

function toggleGameSelection(id, checked) {
  if (checked) {
    selectedGameIds.add(id);
  } else {
    selectedGameIds.delete(id);
  }
  renderGameBatchBar();
  refreshGameSelectionUi();
}

function toggleAllGameSelection(checked) {
  if (checked) {
    currentGameListItems.forEach(item => selectedGameIds.add(item.id));
  } else {
    currentGameListItems.forEach(item => selectedGameIds.delete(item.id));
  }
  renderGameTable(lastGameListResponse || {
    items: currentGameListItems,
    total: currentGameListItems.length,
    page: currentPage,
    totalPages,
  });
}

function clearGameSelection() {
  selectedGameIds = new Set();
  renderGameTable(lastGameListResponse || {
    items: currentGameListItems,
    total: currentGameListItems.length,
    page: currentPage,
    totalPages,
  });
}

async function batchUpdateGameStatus(status) {
  const ids = Array.from(selectedGameIds);
  if (!ids.length) {
    toast('请先选择要处理的游戏', 'error');
    return;
  }
  const label = status === 'published' ? '批量上线' : '批量下线';
  const yes = await confirmDialog(label, `确定要对已选择的 ${ids.length} 条游戏执行${label}吗？`);
  if (!yes) return;
  try {
    const result = await api('/games/batch-status', {
      method: 'POST',
      body: { ids, status },
    });
    toast(`${label}完成：成功 ${result.updated} 条${result.missingIds?.length ? `，缺失 ${result.missingIds.length} 条` : ''}`);
    clearGameSelection();
    loadGames();
  } catch (e) {
    toast(`${label}失败: ${e.message}`, 'error');
  }
}

async function batchDeleteSelectedGames() {
  const ids = Array.from(selectedGameIds);
  if (!ids.length) {
    toast('请先选择要删除的游戏', 'error');
    return;
  }
  const yes = await confirmDialog('批量删除', `删除后将无法恢复，确定要删除已选择的 ${ids.length} 条游戏吗？`);
  if (!yes) return;
  try {
    const result = await api('/games/batch-delete', {
      method: 'POST',
      body: { ids },
    });
    toast(`批量删除完成：成功 ${result.deleted} 条${result.missingIds?.length ? `，缺失 ${result.missingIds.length} 条` : ''}`);
    clearGameSelection();
    loadGames();
  } catch (e) {
    toast('批量删除失败: ' + e.message, 'error');
  }
}

// ===================== Quick Actions (from table) =====================
async function quickToggle(id, status) {
  const label = status === 'published' ? '上架' : '下架';
  try {
    await api(`/game-status/${id}`, { method: 'POST', body: { status } });
    toast(`${label}成功`);
    loadGames();
  } catch (e) {
    toast(`${label}失败: ` + e.message, 'error');
  }
}

async function quickDelete(id, title) {
  const yes = await confirmDialog('确认删除', `确定要删除游戏「${title}」吗？此操作无法撤销。`);
  if (!yes) return;
  try {
    await api('/games/' + id, { method: 'DELETE' });
    toast('游戏已删除');
    loadGames();
  } catch (e) {
    toast('删除失败: ' + e.message, 'error');
  }
}

// ===================== Preview & Play =====================
let currentPlayGameId = null;
let currentPlayPreviewUrl = null;

function resolvePreviewUrl(id, previewUrl) {
  return previewUrl || (window.location.origin + '/games/' + id + '/preview');
}

function previewGameCover(id, title, coverUrl) {
  const resolvedUrl = (coverUrl || '').trim();
  if (!resolvedUrl) {
    toast('当前游戏暂无可预览封面', 'error');
    return;
  }
  currentCoverPreviewUrl = resolvedUrl;
  document.getElementById('coverPreviewTitle').textContent = '封面预览: ' + title;
  document.getElementById('coverPreviewImage').src = resolvedUrl;
  document.getElementById('coverPreviewOverlay').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closeCoverPreview() {
  document.getElementById('coverPreviewOverlay').style.display = 'none';
  document.getElementById('coverPreviewImage').src = '';
  currentCoverPreviewUrl = null;
  document.body.style.overflow = '';
}

function openCoverPreviewNewWindow() {
  if (currentCoverPreviewUrl) {
    window.open(currentCoverPreviewUrl, '_blank');
  }
}

function previewGame(id, title, previewUrl) {
  currentPlayGameId = id;
  const url = resolvePreviewUrl(id, previewUrl);
  currentPlayPreviewUrl = url;
  document.getElementById('previewTitle').textContent = '预览: ' + title;
  document.getElementById('previewFrame2').src = url;
  document.getElementById('previewOverlay').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closePreview() {
  document.getElementById('previewOverlay').style.display = 'none';
  document.getElementById('previewFrame2').src = 'about:blank';
  currentPlayPreviewUrl = null;
  document.body.style.overflow = '';
}

function openPreviewNewWindow() {
  if (currentPlayPreviewUrl) {
    window.open(currentPlayPreviewUrl, '_blank');
  }
}

function playGame(id, title, previewUrl) {
  currentPlayGameId = id;
  const url = resolvePreviewUrl(id, previewUrl);
  currentPlayPreviewUrl = url;
  document.getElementById('playTitle').textContent = '试玩: ' + title;
  document.getElementById('playFrame').src = url;
  document.getElementById('playOverlay').style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closePlay() {
  document.getElementById('playOverlay').style.display = 'none';
  document.getElementById('playFrame').src = 'about:blank';
  currentPlayPreviewUrl = null;
  document.body.style.overflow = '';
}

function openPlayNewWindow() {
  if (currentPlayPreviewUrl) {
    window.open(currentPlayPreviewUrl, '_blank');
  }
}

// ===================== Game Detail =====================
function showGameList() {
  document.getElementById('gameListView').style.display = 'block';
  document.getElementById('gameDetailView').style.display = 'none';
}

async function showGameDetail(id) {
  document.getElementById('gameListView').style.display = 'none';
  const dv = document.getElementById('gameDetailView');
  dv.style.display = 'block';
  dv.innerHTML = '<div class="loading"><div class="spinner"></div><div style="margin-top:8px">加载中...</div></div>';

  try {
    const g = await api('/games/' + id);
    renderGameDetail(g);
  } catch (e) {
    dv.innerHTML = `<div class="loading" style="color:#dc2626">加载失败: ${escHtml(e.message)}</div>`;
  }
}

function renderGameDetail(g) {
  const bundle = g.bundles && g.bundles[0];
  const dv = document.getElementById('gameDetailView');
  const coverUrl = (g.coverUrl || g.thumbnailUrl || '').trim();
  pendingGameCoverDataUrl = null;
  pendingGameCoverFileName = '';

  let html = '<div class="workspace-detail-shell">';
  html += `<div class="back-btn" onclick="showGameList()">&#8592; 返回列表</div>`;
  html += `<div class="detail-header"><h2>${escHtml(g.title)}</h2><div style="display:flex;gap:8px;flex-wrap:wrap">`;
  html += `<button class="btn btn-secondary btn-sm" onclick="previewGameCover('${g.id}','${escAttr(g.title)}','${escAttr(coverUrl)}')" ${coverUrl ? '' : 'disabled'}>封面预览</button>`;
  html += `<button class="btn btn-secondary btn-sm" onclick="previewGame('${g.id}','${escAttr(g.title)}','${escAttr(g.previewUrl || '')}')">预览</button>`;
  html += `<button class="btn btn-success btn-sm" onclick="playGame('${g.id}','${escAttr(g.title)}','${escAttr(g.previewUrl || '')}')">试玩</button>`;
  if (g.status === 'published') {
    html += `<button class="btn btn-warning btn-sm" onclick="changeStatus('${g.id}','draft')">下架</button>`;
  } else {
    html += `<button class="btn btn-success btn-sm" onclick="changeStatus('${g.id}','published')">上架</button>`;
  }
  html += `<button class="btn btn-danger btn-sm" onclick="deleteGame('${g.id}')">删除</button>`;
  html += `</div></div>`;

  // Stats
  html += '<div class="detail-stats">';
  html += `<div class="detail-stat"><div class="v">${fmtNum(g.playCount)}</div><div class="l">播放</div></div>`;
  html += `<div class="detail-stat"><div class="v">${fmtNum(g.likeCount)}</div><div class="l">点赞</div></div>`;
  html += `<div class="detail-stat"><div class="v">${fmtNum(g.forkCount)}</div><div class="l">Fork</div></div>`;
  html += `<div class="detail-stat"><div class="v">${Number(g.qualityScore || 0).toFixed(1)}</div><div class="l">质量分</div></div>`;
  html += `<div class="detail-stat"><div class="v">${Number(g.avgPlayTime || 0).toFixed(0)}s</div><div class="l">平均时长</div></div>`;
  html += `<div class="detail-stat"><div class="v">v${g.version}</div><div class="l">版本</div></div>`;
  html += '</div>';

  // Status controls
  html += '<div class="section-title">状态管理</div>';
  html += '<div class="status-btns">';
  const statuses = ['draft', 'published', 'banned'];
  for (const s of statuses) {
    const cls = g.status === s ? 'btn-primary' : 'btn-secondary';
    html += `<button class="btn ${cls}" onclick="changeStatus('${g.id}','${s}')">${statusLabel(s)}</button>`;
  }
  html += `<span style="margin-left:8px;font-size:13px;color:#94a3b8">当前: <span class="badge badge-${g.status}">${statusLabel(g.status)}</span></span>`;
  html += '</div>';

  html += '<div class="section-title">封面</div>';
  if (coverUrl) {
    html += `<div class="cover-detail-card">
      <img src="${escAttr(coverUrl)}" alt="${escAttr(g.title)}" onclick="previewGameCover('${g.id}','${escAttr(g.title)}','${escAttr(coverUrl)}')">
      <div class="cover-detail-meta">
        <div style="font-size:13px;color:#475569;line-height:1.8">当前后台将优先展示这张封面。你可以点击图片查看大图，或在新窗口打开原始封面地址。</div>
        <div class="cover-detail-actions">
          <button class="btn btn-secondary btn-sm" onclick="previewGameCover('${g.id}','${escAttr(g.title)}','${escAttr(coverUrl)}')">查看大图</button>
          <button class="btn btn-secondary btn-sm" onclick="window.open('${escAttr(coverUrl)}','_blank')">新窗口打开</button>
        </div>
        <div style="font-size:12px;color:#94a3b8;word-break:break-all">${escHtml(coverUrl)}</div>
      </div>
    </div>`;
  } else {
    html += '<div class="workspace-empty">当前还没有可用封面</div>';
  }

  html += '<div class="section-title">封面维护</div>';
  html += `<div class="cover-editor-card">
    <div class="cover-editor-grid">
      <div>
        <div class="upload-area" onclick="document.getElementById('coverFileInput').click()">
          <input type="file" id="coverFileInput" accept="image/png,image/jpeg,image/webp,image/avif,image/gif" onchange="handleCoverFileSelect(event)">
          <div class="icon">&#128247;</div>
          <div class="text">点击上传新的封面图片，支持 PNG / JPG / WEBP / AVIF / GIF</div>
          <div class="filename" id="coverFileName" style="display:none"></div>
        </div>
        <div class="form-group" style="margin-top:12px">
          <label>外部图片地址</label>
          <input class="cover-url-input" type="text" id="e-coverUrl" placeholder="https://example.com/cover.jpg" oninput="previewPendingCoverUrl()">
        </div>
        <input type="hidden" id="e-currentCoverUrl" value="${escAttr(coverUrl)}">
        <input type="hidden" id="coverEditorGameTitle" value="${escAttr(g.title)}">
        <div class="cover-editor-actions">
          <button class="btn btn-primary" id="saveGameCoverBtn" onclick="saveGameCover('${g.id}')">保存封面</button>
          <button class="btn btn-secondary" onclick="clearPendingGameCoverSelection()">清空已选图片</button>
        </div>
      </div>
      <div>
        <div class="cover-editor-preview" id="coverEditorPreviewBox">
          ${coverUrl
            ? `<img src="${escAttr(coverUrl)}" alt="${escAttr(g.title)}">`
            : '<div class="cover-editor-placeholder">暂无预览</div>'}
        </div>
        <div class="cover-editor-note" id="coverEditorPreviewLabel">${coverUrl ? '当前封面预览' : '等待新的封面内容'}</div>
      </div>
    </div>
  </div>`;

  // Edit form
  html += '<div class="section-title">编辑信息</div>';
  html += '<div class="form-grid">';
  html += `<div class="form-group"><label>标题</label><input type="text" id="e-title" value="${escAttr(g.title)}"></div>`;
  html += `<div class="form-group"><label>Slug</label><input type="text" id="e-slug" value="${escAttr(g.slug || '')}"></div>`;
  html += `<div class="form-group"><label>类型</label><select id="e-gameType">
    <option value="">选择</option>
    <option value="casual" ${g.gameType==='casual'?'selected':''}>休闲</option>
    <option value="puzzle" ${g.gameType==='puzzle'?'selected':''}>益智</option>
    <option value="educational" ${g.gameType==='educational'?'selected':''}>教育</option>
    <option value="funny" ${g.gameType==='funny'?'selected':''}>搞笑</option>
  </select></div>`;
  const tagsStr = Array.isArray(g.tags) ? g.tags.join(', ') : '';
  html += `<div class="form-group"><label>标签</label><input type="text" id="e-tags" value="${escAttr(tagsStr)}"></div>`;
  html += `<div class="form-group full"><label>描述</label><textarea id="e-desc" rows="3">${escHtml(g.description || '')}</textarea></div>`;
  html += '</div>';

  // Code editor
  html += '<div class="section-title">HTML 源码</div>';
  html += '<div class="form-group full" style="margin-bottom:12px">';
  html += '<div class="upload-area" onclick="document.getElementById(\'editFileInput\').click()">';
  html += '<input type="file" id="editFileInput" accept=".html,.htm" onchange="handleEditFileSelect(event)">';
  html += '<div class="text">点击上传新的 .html 文件替换</div>';
  html += '</div>';
  html += `<textarea id="e-htmlCode" class="code-editor" rows="16">${escHtml(bundle?.htmlCode || '')}</textarea>`;
  html += '</div>';

  html += '<div style="display:flex;gap:10px;margin-top:16px">';
  html += `<button class="btn btn-secondary" onclick="previewEdit()">预览</button>`;
  html += `<button class="btn btn-primary" onclick="saveEdit('${g.id}')">保存修改</button>`;
  html += '</div>';

  // Preview
  html += '<div id="editPreviewWrap" style="display:none;margin-top:16px"><div class="section-title">预览</div><iframe id="editPreviewFrame" class="preview-frame" sandbox="allow-scripts allow-same-origin"></iframe></div>';
  html += '</div>';

  dv.innerHTML = html;
}

function handleEditFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    document.getElementById('e-htmlCode').value = ev.target.result;
    toast('文件已加载: ' + file.name, 'info');
  };
  reader.readAsText(file);
}

function renderPendingCoverPreview(url, label) {
  const previewBox = document.getElementById('coverEditorPreviewBox');
  const previewLabel = document.getElementById('coverEditorPreviewLabel');
  const gameTitle = document.getElementById('coverEditorGameTitle')?.value || '封面预览';
  if (!previewBox || !previewLabel) return;

  const resolvedUrl = (url || '').trim();
  if (resolvedUrl) {
    previewBox.innerHTML = `<img src="${escAttr(resolvedUrl)}" alt="${escAttr(gameTitle)}">`;
  } else {
    previewBox.innerHTML = '<div class="cover-editor-placeholder">暂无预览</div>';
  }
  previewLabel.textContent = label;
}

function previewPendingCoverUrl() {
  if (pendingGameCoverDataUrl) return;
  const pendingUrl = document.getElementById('e-coverUrl')?.value?.trim() || '';
  const currentUrl = document.getElementById('e-currentCoverUrl')?.value?.trim() || '';
  if (pendingUrl) {
    renderPendingCoverPreview(pendingUrl, '待保存的外链封面预览');
    return;
  }
  renderPendingCoverPreview(currentUrl, currentUrl ? '当前封面预览' : '等待新的封面内容');
}

function clearPendingGameCoverSelection() {
  pendingGameCoverDataUrl = null;
  pendingGameCoverFileName = '';
  const fileInput = document.getElementById('coverFileInput');
  const fileName = document.getElementById('coverFileName');
  if (fileInput) {
    fileInput.value = '';
  }
  if (fileName) {
    fileName.textContent = '';
    fileName.style.display = 'none';
  }
  previewPendingCoverUrl();
}

function handleCoverFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    pendingGameCoverDataUrl = String(ev.target.result || '');
    pendingGameCoverFileName = file.name || '';
    const fileName = document.getElementById('coverFileName');
    if (fileName) {
      fileName.textContent = pendingGameCoverFileName;
      fileName.style.display = 'block';
    }
    renderPendingCoverPreview(pendingGameCoverDataUrl, '待保存的本地图片预览');
    toast('封面图片已加载: ' + pendingGameCoverFileName, 'info');
  };
  reader.readAsDataURL(file);
}

async function saveGameCover(id) {
  const saveBtn = document.getElementById('saveGameCoverBtn');
  const imageUrl = document.getElementById('e-coverUrl')?.value?.trim() || '';
  if (!pendingGameCoverDataUrl && !imageUrl) {
    toast('请先上传图片或填写图片地址', 'error');
    return;
  }

  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中...';
  }

  try {
    await api('/games/' + id + '/cover', {
      method: 'PUT',
      body: pendingGameCoverDataUrl
        ? {
            imageDataUrl: pendingGameCoverDataUrl,
            fileName: pendingGameCoverFileName || undefined,
          }
        : {
            imageUrl,
          },
    });
    toast('封面已更新');
    showGameDetail(id);
  } catch (e) {
    toast('封面更新失败: ' + e.message, 'error');
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = '保存封面';
    }
  }
}

function previewEdit() {
  const code = document.getElementById('e-htmlCode').value;
  if (!code.trim()) { toast('没有代码可预览', 'error'); return; }
  const wrap = document.getElementById('editPreviewWrap');
  wrap.style.display = 'block';
  const frame = document.getElementById('editPreviewFrame');
  frame.srcdoc = code;
}

async function saveEdit(id) {
  const data = {
    title: document.getElementById('e-title').value.trim(),
    slug: document.getElementById('e-slug').value.trim() || undefined,
    gameType: document.getElementById('e-gameType').value || undefined,
    tags: document.getElementById('e-tags').value.split(',').map(t => t.trim()).filter(Boolean),
    description: document.getElementById('e-desc').value.trim(),
    htmlCode: document.getElementById('e-htmlCode').value,
  };
  if (!data.title) { toast('标题不能为空', 'error'); return; }
  try {
    await api('/games/' + id, { method: 'PUT', body: data });
    toast('保存成功');
    showGameDetail(id);
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
  }
}

async function changeStatus(id, status) {
  try {
    await api(`/game-status/${id}`, { method: 'POST', body: { status } });
    toast('状态已更新');
    showGameDetail(id);
  } catch (e) {
    toast('更新失败: ' + e.message, 'error');
  }
}

async function deleteGame(id) {
  const yes = await confirmDialog('确认删除', '删除后将无法恢复，确定要删除这个游戏吗？');
  if (!yes) return;
  try {
    await api('/games/' + id, { method: 'DELETE' });
    toast('游戏已删除');
    showGameList();
    loadGames();
  } catch (e) {
    toast('删除失败: ' + e.message, 'error');
  }
}

// ===================== Create Game =====================
function autoSlug() {
  const title = document.getElementById('c-title').value.trim();
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-|-$/g, '');
  document.getElementById('c-slug').value = slug;
}

function handleFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    document.getElementById('c-htmlCode').value = ev.target.result;
    document.getElementById('fileName').textContent = file.name;
    document.getElementById('fileName').style.display = 'block';
    toast('文件已加载: ' + file.name, 'info');
  };
  reader.readAsText(file);
}

function handleDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('dragover');
}
function handleDragLeave(e) {
  e.currentTarget.classList.remove('dragover');
}
function handleDrop(e) {
  e.preventDefault();
  e.currentTarget.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file && (file.name.endsWith('.html') || file.name.endsWith('.htm'))) {
    const reader = new FileReader();
    reader.onload = (ev) => {
      document.getElementById('c-htmlCode').value = ev.target.result;
      document.getElementById('fileName').textContent = file.name;
      document.getElementById('fileName').style.display = 'block';
      toast('文件已加载: ' + file.name, 'info');
    };
    reader.readAsText(file);
  } else {
    toast('请上传 .html 文件', 'error');
  }
}

function previewCreate() {
  const code = document.getElementById('c-htmlCode').value;
  if (!code.trim()) { toast('没有代码可预览', 'error'); return; }
  document.getElementById('createPreviewWrap').style.display = 'block';
  document.getElementById('createPreviewFrame').srcdoc = code;
}

async function loadCreateAuthorOptions(force = false) {
  if (createAuthorOptionsLoaded && !force) return;

  const select = document.getElementById('c-authorId');
  if (!select) return;

  const currentValue = select.value;
  select.disabled = true;

  try {
    const result = await api('/users?page=1&limit=100&role=all');
    const items = Array.isArray(result?.items) ? result.items : [];
    const options = ['<option value="">系统默认作者</option>'];
    for (const user of items) {
      const label = `${escHtml(user.displayName || user.username)} (${escHtml(user.username)})`;
      options.push(`<option value="${escAttr(user.id)}">${label}</option>`);
    }
    select.innerHTML = options.join('');
    if (currentValue) {
      select.value = currentValue;
    }
    createAuthorOptionsLoaded = true;
  } catch (e) {
    toast('加载用户列表失败: ' + e.message, 'error');
  } finally {
    select.disabled = false;
  }
}

async function submitCreate() {
  const title = document.getElementById('c-title').value.trim();
  const htmlCode = document.getElementById('c-htmlCode').value;
  if (!title) { toast('请输入游戏标题', 'error'); return; }
  if (!htmlCode.trim()) { toast('请上传或粘贴 HTML 代码', 'error'); return; }

  const data = {
    title,
    slug: document.getElementById('c-slug').value.trim() || undefined,
    gameType: document.getElementById('c-gameType').value || undefined,
    authorId: document.getElementById('c-authorId').value || undefined,
    tags: document.getElementById('c-tags').value.split(',').map(t => t.trim()).filter(Boolean),
    description: document.getElementById('c-desc').value.trim(),
    htmlCode,
  };

  const btn = document.getElementById('createBtn');
  btn.disabled = true;
  btn.textContent = '保存中...';

  try {
    await api('/games', { method: 'POST', body: data });
    toast('游戏创建成功');
    // Reset form
    document.getElementById('c-title').value = '';
    document.getElementById('c-slug').value = '';
    document.getElementById('c-gameType').value = '';
    document.getElementById('c-authorId').value = '';
    document.getElementById('c-tags').value = '';
    document.getElementById('c-desc').value = '';
    document.getElementById('c-htmlCode').value = '';
    document.getElementById('fileName').style.display = 'none';
    document.getElementById('createPreviewWrap').style.display = 'none';
    switchTab('games');
  } catch (e) {
    toast('创建失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '保存游戏';
  }
}

