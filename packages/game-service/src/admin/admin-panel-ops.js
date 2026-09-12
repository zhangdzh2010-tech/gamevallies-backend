// ===================== Async Tasks =====================
async function loadTasks() {
  const search = document.getElementById('task-search').value.trim();
  const status = document.getElementById('task-status').value;
  const params = new URLSearchParams({ page: taskPage, limit: 20, status });
  if (search) params.set('search', search);

  const wrap = document.getElementById('taskTableWrap');
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div><div style="margin-top:8px">加载中...</div></div>';
  try {
    const data = await api('/tasks?' + params.toString());
    renderTaskTable(data);
  } catch (e) {
    wrap.innerHTML = `<div class="loading" style="color:#dc2626">加载失败: ${escHtml(e.message)}</div>`;
  }
}

function renderTaskTable(data) {
  const wrap = document.getElementById('taskTableWrap');
  if (!data.items || !data.items.length) {
    wrap.innerHTML = '<div class="loading">暂无任务数据</div>';
    return;
  }

  let html = '<table><thead><tr><th>Task ID</th><th>游戏</th><th>用户</th><th>类型</th><th>状态</th><th>阶段</th><th>进度</th><th>更新时间</th><th>操作</th></tr></thead><tbody>';
  for (const task of data.items) {
    html += `<tr>
      <td><code>${escHtml(task.id)}</code></td>
      <td>${escHtml(task.game?.title || task.gameId)}</td>
      <td>${escHtml(task.user?.displayName || task.user?.username || task.userId)}</td>
      <td>${escHtml(task.taskType)}</td>
      <td><span class="badge badge-${task.status === 'succeeded' ? 'published' : task.status === 'running' ? 'generating' : task.status === 'failed' || task.status === 'timed_out' ? 'failed' : 'draft'}">${escHtml(task.status)}</span></td>
      <td>${escHtml(task.displayStageLabel || task.progressStage || task.failedStage || '-')}</td>
      <td>${task.progressPct ?? '-'}%</td>
      <td>${fmtDate(task.updatedAt)}</td>
      <td><button class="abtn abtn-edit" onclick="showTaskDetail('${task.id}')">详情</button>${canTerminateTask(task.status) ? ` <button class="abtn abtn-delete" onclick="event.stopPropagation();terminateTask('${task.id}')">结束</button>` : ''}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  html += '<div class="pagination"><div class="info">共 ' + data.total + ' 条，第 ' + data.page + '/' + data.totalPages + ' 页</div><div class="pages">';
  html += '<button ' + (data.page <= 1 ? 'disabled' : '') + ' onclick="taskPage=' + (data.page-1) + ';loadTasks()">上一页</button>';
  for (let i = Math.max(1, data.page - 2); i <= Math.min(data.totalPages, data.page + 2); i++) {
    html += '<button class="' + (i===data.page?'active':'') + '" onclick="taskPage=' + i + ';loadTasks()">' + i + '</button>';
  }
  html += '<button ' + (data.page >= data.totalPages ? 'disabled' : '') + ' onclick="taskPage=' + (data.page+1) + ';loadTasks()">下一页</button>';
  html += '</div></div>';
  wrap.innerHTML = html;
}

function clearTaskDetailRefresh() {
  if (taskDetailRefreshTimer) {
    clearTimeout(taskDetailRefreshTimer);
    taskDetailRefreshTimer = null;
  }
}

function scheduleTaskDetailRefresh(taskId, status) {
  clearTaskDetailRefresh();
  if (status === 'queued' || status === 'running') {
    taskDetailRefreshTimer = setTimeout(() => showTaskDetail(taskId, { silent: true }), 10000);
  }
}

function taskStatusBadge(status) {
  if (status === 'succeeded') return 'badge-published';
  if (status === 'running') return 'badge-generating';
  if (status === 'failed' || status === 'timed_out' || status === 'canceled') return 'badge-failed';
  return 'badge-draft';
}

function canTerminateTask(status) {
  return status === 'queued' || status === 'running';
}

async function terminateTask(taskId) {
  const yes = await confirmDialog('结束任务', `确定结束任务 ${taskId} 吗？`);
  if (!yes) return;
  try {
    await api('/tasks/' + taskId + '/terminate', {
      method: 'POST',
      body: JSON.stringify({}),
    });
    toast('任务已结束');
    await loadTasks();
    if (currentTaskDetailId === taskId) {
      await showTaskDetail(taskId);
    }
  } catch (e) {
    toast('结束任务失败: ' + e.message, 'error');
  }
}

function eventTypeLabel(type) {
  const labels = { status: '状态', progress: '进度', llm_call: 'LLM', note: '说明', error: '错误' };
  return labels[type] || type;
}

function renderTaskEventDetail(details) {
  if (!details || (typeof details === 'object' && !Object.keys(details).length)) return '';
  return `<pre class="log-code" style="max-height:180px;margin-top:10px">${escHtml(JSON.stringify(details, null, 2))}</pre>`;
}

function toPlainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function hasObjectKeys(value) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length);
}

function formatFingerprint(value) {
  if (!value && value !== 0) return '-';
  const text = String(value);
  if (text.length <= 18) return text;
  return `${text.slice(0, 8)}...${text.slice(-6)}`;
}

function summarizePromptDedup(source) {
  const queue = [toPlainObject(source)];
  while (queue.length) {
    const current = queue.shift();
    if (!hasObjectKeys(current)) continue;
    const promptFingerprint = current.promptFingerprint ?? current.prompt_fingerprint ?? null;
    const savedTokens = current.savedTokens
      ?? current.saved_tokens
      ?? current.savedTokensEstimate
      ?? current.saved_tokens_estimate
      ?? null;
    const removedBlocks = current.removedBlocks ?? current.removed_blocks ?? null;
    const requestedInputTokens = current.requestedInputTokens
      ?? current.requested_input_tokens
      ?? current.inputTokensBefore
      ?? current.input_tokens_before
      ?? null;
    const effectiveInputTokens = current.effectiveInputTokens
      ?? current.effective_input_tokens
      ?? current.inputTokensAfter
      ?? current.input_tokens_after
      ?? null;

    if (
      promptFingerprint
      || savedTokens !== null
      || removedBlocks !== null
      || requestedInputTokens !== null
      || effectiveInputTokens !== null
    ) {
      return {
        promptFingerprint,
        savedTokens,
        removedBlocks,
        requestedInputTokens,
        effectiveInputTokens,
      };
    }

    Object.values(current).forEach((value) => {
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        queue.push(value);
      }
    });
  }

  return null;
}

function aggregatePromptDedup(logs) {
  const summaries = toArray(logs)
    .map(log => summarizePromptDedup(log))
    .filter(Boolean);
  if (!summaries.length) return null;

  const totals = summaries.reduce((acc, summary) => {
    if (Number.isFinite(Number(summary.savedTokens))) acc.savedTokens += Number(summary.savedTokens);
    if (Number.isFinite(Number(summary.removedBlocks))) acc.removedBlocks += Number(summary.removedBlocks);
    if (Number.isFinite(Number(summary.requestedInputTokens))) acc.requestedInputTokens += Number(summary.requestedInputTokens);
    if (Number.isFinite(Number(summary.effectiveInputTokens))) acc.effectiveInputTokens += Number(summary.effectiveInputTokens);
    if (!acc.promptFingerprint && summary.promptFingerprint) acc.promptFingerprint = summary.promptFingerprint;
    return acc;
  }, {
    count: summaries.length,
    savedTokens: 0,
    removedBlocks: 0,
    requestedInputTokens: 0,
    effectiveInputTokens: 0,
    promptFingerprint: null,
  });

  return totals;
}

function renderPromptDedupPanel(summary, options = {}) {
  if (!summary) {
    return options.emptyText || '';
  }

  return `
    <div class="log-card" style="padding:14px 16px;${options.compact ? 'margin-top:10px;' : ''}">
      <div class="log-card-title" style="margin-bottom:8px">
        <div>
          <strong style="font-size:14px;color:#0f172a">${escHtml(options.title || 'Prompt 去重')}</strong>
          ${options.subtitle ? `<div class="log-card-sub">${escHtml(options.subtitle)}</div>` : ''}
        </div>
        <span class="badge badge-generating">${escHtml(String(summary.count || 1))} 次样本</span>
      </div>
      <div class="log-kv-grid">
        <div class="log-kv"><div class="k">Prompt 指纹</div><div class="v"><code>${escHtml(formatFingerprint(summary.promptFingerprint))}</code></div></div>
        <div class="log-kv"><div class="k">节省 Tokens</div><div class="v">${escHtml(fmtNum(summary.savedTokens || 0))}</div></div>
        <div class="log-kv"><div class="k">移除块数</div><div class="v">${escHtml(fmtNum(summary.removedBlocks || 0))}</div></div>
        <div class="log-kv"><div class="k">请求 Tokens</div><div class="v">${escHtml(fmtNum(summary.requestedInputTokens || 0))}</div></div>
        <div class="log-kv"><div class="k">生效 Tokens</div><div class="v">${escHtml(fmtNum(summary.effectiveInputTokens || 0))}</div></div>
      </div>
    </div>
  `;
}

function renderIntentBuildPanel(intentBuild) {
  const snapshot = toPlainObject(intentBuild);
  if (!hasObjectKeys(snapshot)) {
    return '<div class="log-empty">当前任务没有保存意图构建快照。</div>';
  }

  const brief = snapshot.brief || snapshot.summary || snapshot.intentBrief || '';
  const frozenSpec = snapshot.frozenSpec ?? snapshot.frozen_spec ?? null;

  return `
    <div class="log-card" style="padding:14px 16px">
      <div class="log-kv-grid">
        <div class="log-kv"><div class="k">Intent Fingerprint</div><div class="v"><code>${escHtml(formatFingerprint(snapshot.intentFingerprint || snapshot.intent_fingerprint))}</code></div></div>
        <div class="log-kv"><div class="k">Spec Fingerprint</div><div class="v"><code>${escHtml(formatFingerprint(snapshot.specFingerprint || snapshot.spec_fingerprint))}</code></div></div>
      </div>
      ${brief ? `<div class="log-textbox" style="margin-top:10px">${escHtml(brief)}</div>` : '<div class="log-empty" style="margin-top:10px">没有保存意图摘要。</div>'}
      ${frozenSpec ? `<pre class="log-code" style="max-height:220px;margin-top:10px">${escHtml(JSON.stringify(frozenSpec, null, 2))}</pre>` : '<div class="log-empty" style="margin-top:10px">没有保存冻结后的 spec。</div>'}
    </div>
  `;
}

function renderTaskGenerationSummary(task) {
  const summary = toPlainObject(task?.resultSummary);
  const runtimeQaReport = toPlainObject(task?.runtimeQaReport);
  const warnings = toArray(task?.qaWarnings);
  const runtimeQaState = task?.runtimeQaUnavailable
    ? `不可用${task.runtimeQaUnavailableKind ? ` (${task.runtimeQaUnavailableKind})` : ''}`
    : (hasObjectKeys(runtimeQaReport) ? '已采集' : '-');

  return `
    <div class="log-card" style="padding:14px 16px">
      <div class="log-kv-grid">
        <div class="log-kv"><div class="k">生成层级</div><div class="v">${escHtml(task?.generationTier || summary.generationTier || summary.generation_tier || '-')}</div></div>
        <div class="log-kv"><div class="k">生成策略</div><div class="v">${escHtml(summary.strategy || '-')}</div></div>
        <div class="log-kv"><div class="k">QA 告警</div><div class="v">${escHtml(fmtNum(warnings.length))}</div></div>
        <div class="log-kv"><div class="k">Runtime QA</div><div class="v">${escHtml(runtimeQaState)}</div></div>
        <div class="log-kv"><div class="k">Runtime QA Phase</div><div class="v">${escHtml(task?.runtimeQaUnavailablePhase || runtimeQaReport.unavailablePhase || '-')}</div></div>
        <div class="log-kv"><div class="k">Runtime QA Reason</div><div class="v">${escHtml(task?.runtimeQaUnavailableReason || runtimeQaReport.unavailableReason || '-')}</div></div>
      </div>
      ${warnings.length
        ? `<div style="display:flex;flex-direction:column;gap:8px;margin-top:10px">${warnings.map((warning) => {
            const item = toPlainObject(warning);
            const label = item.type || item.family || item.severity || 'warning';
            const message = item.message || item.reason || item.title || '-';
            return `<div class="log-note"><strong>${escHtml(String(label))}</strong> · ${escHtml(String(message))}</div>`;
          }).join('')}</div>`
        : '<div class="log-empty" style="margin-top:10px">没有记录 QA 告警。</div>'}
    </div>
  `;
}

function renderIssueListPanel(issueList, options = {}) {
  const summary = toPlainObject(issueList);
  const items = toArray(summary.items);
  if (!items.length) {
    return options.emptyText || '<div class="log-empty">没有结构化问题记录。</div>';
  }

  return `
    <div class="log-card" style="padding:14px 16px">
      <div class="log-kv-grid">
        <div class="log-kv"><div class="k">问题数</div><div class="v">${escHtml(fmtNum(items.length))}</div></div>
        <div class="log-kv"><div class="k">错误</div><div class="v">${escHtml(fmtNum(summary.errorCount || 0))}</div></div>
        <div class="log-kv"><div class="k">告警</div><div class="v">${escHtml(fmtNum(summary.warningCount || 0))}</div></div>
        <div class="log-kv"><div class="k">阻塞</div><div class="v">${escHtml(fmtNum(summary.blockingCount || 0))}</div></div>
      </div>
      <div style="display:flex;flex-direction:column;gap:10px;margin-top:10px">
        ${items.map((issue) => {
          const item = toPlainObject(issue);
          const location = hasObjectKeys(item.location) ? JSON.stringify(item.location) : '';
          return `
            <div class="log-card" style="padding:12px 14px;background:#f8fafc">
              <div class="log-card-title" style="margin-bottom:8px">
                <div>
                  <strong style="font-size:13px;color:#0f172a">${escHtml(String(item.family || item.type || '问题'))}</strong>
                  <div class="log-card-sub">${escHtml(String(item.message || '-'))}</div>
                </div>
                <span class="badge ${String(item.severity || '').toLowerCase() === 'warning' ? 'badge-review' : 'badge-failed'}">${escHtml(String(item.severity || 'error'))}</span>
              </div>
              ${item.repairHint ? `<div class="log-note"><strong>修复建议：</strong>${escHtml(String(item.repairHint))}</div>` : ''}
              ${location ? `<pre class="log-code" style="max-height:120px;margin-top:10px">${escHtml(location)}</pre>` : ''}
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function renderQaArtifactsPanel(artifacts) {
  const items = toArray(artifacts);
  if (!items.length) {
    return '<div class="log-empty">当前任务没有保存 QA 报告。</div>';
  }

  return `
    <div style="display:flex;flex-direction:column;gap:10px">
      ${items.map((artifact) => {
        const report = toPlainObject(artifact?.report);
        const issueList = toPlainObject(artifact?.issueList);
        const issueCount = toArray(issueList.items).length;
        return `
          <div class="log-card" style="padding:14px 16px">
            <div class="log-card-title" style="margin-bottom:8px">
              <div>
                <strong style="font-size:14px;color:#0f172a">${escHtml(String(artifact?.artifactType || '-'))}</strong>
                <div class="log-card-sub">${escHtml(fmtDateTime(artifact?.createdAt))}</div>
              </div>
              <span class="badge badge-draft">${escHtml(fmtNum(issueCount))} issues</span>
            </div>
            ${hasObjectKeys(issueList) ? renderIssueListPanel(issueList) : ''}
            ${hasObjectKeys(report) ? `<pre class="log-code" style="max-height:220px;margin-top:${hasObjectKeys(issueList) ? '10px' : '0'}">${escHtml(JSON.stringify(report, null, 2))}</pre>` : '<div class="log-empty">No artifact payload was available.</div>'}
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function routeMatchStrategyLabel(strategy) {
  const labels = {
    exact: '精确匹配',
    parent_step: '父级步骤回退',
    provider_pool: '区域候选池回退',
  };
  return labels[strategy] || strategy || '-';
}

function renderRouteSnapshotPanel(snapshot, options = {}) {
  if (!snapshot || (typeof snapshot === 'object' && !Object.keys(snapshot).length)) {
    return options.emptyText || '<div class="log-empty">暂无路由快照</div>';
  }
  const requestedStep = snapshot.requested_step_key || snapshot.step_key || '-';
  const matchedStep = snapshot.matched_step_key || snapshot.step_key || '-';
  const providerName = snapshot.provider_name || '-';
  const strategy = routeMatchStrategyLabel(snapshot.route_match_strategy);
  const routeId = snapshot.route_id || '-';
  const configVersion = snapshot.config_version ?? '-';
  const fallbackCount = Array.isArray(snapshot.fallback_provider_ids) ? snapshot.fallback_provider_ids.length : 0;
  const promptDedupSummary = summarizePromptDedup(snapshot);
  return `
    <div class="log-card" style="padding:14px 16px">
      <div class="log-kv-grid">
        <div class="log-kv"><div class="k">请求步骤</div><div class="v"><code>${escHtml(requestedStep)}</code></div></div>
        <div class="log-kv"><div class="k">命中步骤</div><div class="v"><code>${escHtml(matchedStep)}</code></div></div>
        <div class="log-kv"><div class="k">匹配策略</div><div class="v">${escHtml(strategy)}</div></div>
        <div class="log-kv"><div class="k">Provider</div><div class="v">${escHtml(providerName)}</div></div>
        <div class="log-kv"><div class="k">Route ID</div><div class="v"><code>${escHtml(routeId)}</code></div></div>
        <div class="log-kv"><div class="k">Fallback 数</div><div class="v">${escHtml(String(fallbackCount))}</div></div>
        <div class="log-kv"><div class="k">配置版本</div><div class="v">${escHtml(String(configVersion))}</div></div>
      </div>
      ${renderPromptDedupPanel(promptDedupSummary, {
        compact: true,
        title: 'Prompt 去重',
      })}
      <pre class="log-code" style="max-height:180px;margin-top:10px">${escHtml(JSON.stringify(snapshot, null, 2))}</pre>
    </div>
  `;
}

function renderTaskEvents(events) {
  if (!events || !events.length) return '<div class="log-empty">暂无阶段事件</div>';
  return events.map(event => `
    <div class="log-card" style="padding:14px 16px">
      <div class="log-card-title" style="margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span class="badge ${event.eventType === 'error' ? 'badge-failed' : event.eventType === 'llm_call' ? 'badge-generating' : 'badge-draft'}">${escHtml(eventTypeLabel(event.eventType))}</span>
          <strong style="font-size:13px;color:#0f172a">${escHtml(event.stage || '-')}</strong>
          <span class="log-card-sub">${escHtml(fmtDateTime(event.createdAt))}</span>
        </div>
        <span class="log-card-sub">${event.percentage ?? '-'}%</span>
      </div>
      <div class="log-textbox" style="margin:0">${escHtml(event.message || '')}</div>
      ${renderTaskEventDetail(event.details)}
    </div>
  `).join('');
}

function renderLlmLogs(logs) {
  if (!logs || !logs.length) return '<div class="log-empty">暂无 LLM 调用日志</div>';
  return logs.map(log => `
    <div class="log-card" style="padding:14px 16px">
      <div class="log-card-title" style="margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span class="badge ${log.success ? 'badge-published' : 'badge-failed'}">${log.success ? 'success' : 'failed'}</span>
          <strong style="font-size:13px;color:#0f172a">${escHtml(log.stepKey)}</strong>
          <span class="log-card-sub">${escHtml(fmtDateTime(log.createdAt))}</span>
        </div>
        <span class="log-card-sub">${escHtml(fmtDurationMs(log.latencyMs))}</span>
      </div>
      <div class="log-kv-grid">
        <div class="log-kv"><div class="k">Provider</div><div class="v">${escHtml(log.providerName || log.providerType || '-')}</div></div>
        <div class="log-kv"><div class="k">Model</div><div class="v"><code>${escHtml(log.model || '-')}</code></div></div>
        <div class="log-kv"><div class="k">Region</div><div class="v">${escHtml(log.region || '-')}</div></div>
        <div class="log-kv"><div class="k">Timeout</div><div class="v">${escHtml(String(log.requestTimeoutS || '-'))}s / connect ${escHtml(String(log.connectTimeoutS || '-'))}s</div></div>
        <div class="log-kv"><div class="k">HTTP</div><div class="v">${log.httpStatus ?? '-'}</div></div>
        <div class="log-kv"><div class="k">Request ID</div><div class="v"><code>${escHtml(log.upstreamRequestId || '-')}</code></div></div>
        <div class="log-kv"><div class="k">Input Tokens</div><div class="v">${escHtml(fmtTokenCount(getLlmLogTokenStats(log).inputTokens))}</div></div>
        <div class="log-kv"><div class="k">Output Tokens</div><div class="v">${escHtml(fmtTokenCount(getLlmLogTokenStats(log).outputTokens))}</div></div>
        <div class="log-kv"><div class="k">Total Tokens</div><div class="v">${escHtml(fmtTokenCount(getLlmLogTokenStats(log).totalTokens))}</div></div>
      </div>
      ${log.errorMessage ? `<div class="log-alert" style="margin-top:10px">${escHtml(log.errorMessage)}</div>` : ''}
      ${log.errorBodyExcerpt ? `<pre class="log-code" style="max-height:180px;margin-top:10px">${escHtml(log.errorBodyExcerpt)}</pre>` : ''}
      ${log.routeSnapshot ? `<div style="margin-top:10px">${renderRouteSnapshotPanel(log.routeSnapshot)}</div>` : ''}
    </div>
  `).join('');
}

function normalizeTokenCount(value) {
  if (value === null || value === undefined || value === '') return null;
  const num = Number(value);
  if (!Number.isFinite(num) || num < 0) return null;
  return Math.round(num);
}

function fmtTokenCount(value) {
  const normalized = normalizeTokenCount(value);
  if (normalized === null) return '-';
  return new Intl.NumberFormat('en-US').format(normalized);
}

function getLlmLogTokenStats(log) {
  const inputTokens = normalizeTokenCount(log?.inputTokens);
  const outputTokens = normalizeTokenCount(log?.outputTokens);
  const totalTokens = normalizeTokenCount(log?.totalTokens);
  if (inputTokens === null && outputTokens === null && totalTokens === null) {
    return { inputTokens: null, outputTokens: null, totalTokens: null, hasUsage: false };
  }
  return {
    inputTokens,
    outputTokens,
    totalTokens: totalTokens !== null
      ? totalTokens
      : (inputTokens !== null && outputTokens !== null ? inputTokens + outputTokens : null),
    hasUsage: true,
  };
}

function summarizeLlmLogs(logs) {
  const summary = { callCount: Array.isArray(logs) ? logs.length : 0, usageCount: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0 };
  (logs || []).forEach(log => {
    const usage = getLlmLogTokenStats(log);
    if (!usage.hasUsage) return;
    summary.usageCount += 1;
    if (usage.inputTokens !== null) summary.inputTokens += usage.inputTokens;
    if (usage.outputTokens !== null) summary.outputTokens += usage.outputTokens;
    if (usage.totalTokens !== null) summary.totalTokens += usage.totalTokens;
  });
  return summary;
}

function renderLlmUsageSummary(logs) {
  const summary = summarizeLlmLogs(logs);
  const promptDedupSummary = aggregatePromptDedup(logs);
  if (!summary.callCount) return '<div class="log-empty">暂无 LLM Token 统计</div>';
  if (!summary.usageCount) {
    return `
      <div class="log-card" style="padding:14px 16px;margin-bottom:12px">
        <div class="log-empty">当前这些 Provider 没有返回精确 token usage，后台暂时无法汇总。</div>
        ${renderPromptDedupPanel(promptDedupSummary, {
          compact: true,
          title: 'Prompt 去重',
        })}
      </div>
    `;
  }
  return `
    <div class="log-card" style="padding:14px 16px;margin-bottom:12px">
      <div class="log-card-title" style="margin-bottom:8px">
        <div>
          <strong style="font-size:14px;color:#0f172a">Token 汇总</strong>
          <div class="log-card-sub">已覆盖 ${escHtml(String(summary.usageCount))} / ${escHtml(String(summary.callCount))} 次 LLM 调用</div>
        </div>
        <span class="badge badge-generating">精确 Usage</span>
      </div>
      <div class="log-kv-grid">
        <div class="log-kv"><div class="k">输入 Tokens</div><div class="v">${escHtml(fmtTokenCount(summary.inputTokens))}</div></div>
        <div class="log-kv"><div class="k">输出 Tokens</div><div class="v">${escHtml(fmtTokenCount(summary.outputTokens))}</div></div>
        <div class="log-kv"><div class="k">总 Tokens</div><div class="v">${escHtml(fmtTokenCount(summary.totalTokens))}</div></div>
      </div>
      ${renderPromptDedupPanel(promptDedupSummary, {
        compact: true,
        title: 'Prompt 去重',
      })}
    </div>
  `;
}

function taskDetailOverlayKey(taskId) {
  return 'task-detail:' + taskId;
}

function handleTaskDetailOverlayClose() {
  currentTaskDetailId = null;
  currentTaskDetailMeta = { title: '任务详情', focusSection: null };
  clearTaskDetailRefresh();
}

async function showTaskDetail(taskId, options = {}) {
  const isNewOpen = !options.silent && taskId !== currentTaskDetailId;
  if (isNewOpen) {
    currentTaskDetailMeta = {
      title: options.title || '任务详情',
      focusSection: options.focusSection || null,
    };
  } else if (!options.silent) {
    if (options.title) currentTaskDetailMeta.title = options.title;
    if (options.focusSection !== undefined) currentTaskDetailMeta.focusSection = options.focusSection;
  }

  currentTaskDetailId = taskId;
  const title = currentTaskDetailMeta.title || '任务详情';
  const focusSection = options.silent ? null : currentTaskDetailMeta.focusSection;
  const overlayKey = taskDetailOverlayKey(taskId);
  const refreshAction = currentTaskDetailMeta.focusSection === 'source'
    ? `showTaskDetail('${taskId}', { focusSection: 'source' })`
    : `showTaskDetail('${taskId}')`;

  if (!options.silent && !getDetailOverlay(overlayKey)) {
    showDetailOverlay(title, '<div class="loading"><div class="spinner"></div><div style="margin-top:8px">加载详情...</div></div>', {
      overlayKey,
      maxWidth: '1120px',
      onClose: handleTaskDetailOverlayClose,
    });
  }

  try {
    const task = await api('/tasks/' + taskId);
    if (currentTaskDetailId !== taskId) return;
    if (options.silent && !getDetailOverlay(overlayKey)) return;
    const detail = buildTaskDetailMarkup(task, { title, refreshAction });
    showDetailOverlay(title, detail, {
      overlayKey,
      maxWidth: '1120px',
      focusSelector: focusSection === 'source' ? '[data-task-source-card="true"]' : null,
      onClose: handleTaskDetailOverlayClose,
    });
    scheduleTaskDetailRefresh(task.id, task.status);
  } catch (e) {
    if (options.silent) return;
    if (currentTaskDetailId !== taskId) return;
    clearTaskDetailRefresh();
    showDetailOverlay(title, `<div class="loading" style="color:#dc2626">详情加载失败: ${escHtml(e.message)}</div>`, {
      overlayKey,
      maxWidth: '1120px',
      onClose: handleTaskDetailOverlayClose,
    });
  }
}

function providerTestFailureLabel(result) {
  const status = result.httpStatus;
  if (status === 401 || status === 403) return '认证或权限被拒绝，请检查 Key';
  if (status === 429) return '服务限流，请稍后重试或使用备用';
  if (status >= 500) return `上游返回 HTTP ${status}，请查看诊断记录`;
  if (result.errorCode?.includes('Timeout')) return '客户端等待超时，未收到 HTTP 响应';
  return '请求未完成，请展开测试记录查看原因';
}

function closeSheetOnBackdrop(event, sheetId) {
  if (event.target?.id !== sheetId) return;
  document.getElementById(sheetId)?.classList.remove('active');
}

// ===================== Subscription Plans =====================
function resetSubscriptionPlanForm() {
  currentSubscriptionPlanId = null;
  document.getElementById('subscriptionPlanSheetTitle').textContent = '新建订阅套餐';
  document.getElementById('subscriptionPlanName').value = '';
  document.getElementById('subscriptionPlanPeriod').value = 'monthly';
  document.getElementById('subscriptionPlanPriceYuan').value = '';
  document.getElementById('subscriptionPlanQuota').value = '';
  document.getElementById('subscriptionPlanBadge').value = '';
  document.getElementById('subscriptionPlanSortOrder').value = '0';
  document.getElementById('subscriptionPlanRecommended').checked = false;
  document.getElementById('subscriptionPlanActive').checked = true;
  document.getElementById('subscriptionPlanDescription').value = '';
  document.getElementById('subscriptionPlanFeatures').value = '';
}

function openSubscriptionPlanSheet(id) {
  if (!id) {
    resetSubscriptionPlanForm();
  } else {
    const plan = subscriptionPlans.find(item => item.id === id);
    if (!plan) {
      toast('未找到套餐', 'error');
      return;
    }
    currentSubscriptionPlanId = plan.id;
    document.getElementById('subscriptionPlanSheetTitle').textContent = '编辑订阅套餐';
    document.getElementById('subscriptionPlanName').value = plan.name || '';
    document.getElementById('subscriptionPlanPeriod').value = plan.period || 'monthly';
    document.getElementById('subscriptionPlanPriceYuan').value = Number(plan.priceYuan || 0).toFixed(2);
    document.getElementById('subscriptionPlanQuota').value = String(plan.quota ?? '');
    document.getElementById('subscriptionPlanBadge').value = plan.badge || '';
    document.getElementById('subscriptionPlanSortOrder').value = String(plan.sortOrder ?? 0);
    document.getElementById('subscriptionPlanRecommended').checked = Boolean(plan.recommended);
    document.getElementById('subscriptionPlanActive').checked = Boolean(plan.active);
    document.getElementById('subscriptionPlanDescription').value = plan.description || '';
    document.getElementById('subscriptionPlanFeatures').value = Array.isArray(plan.features) ? plan.features.join('\n') : '';
  }
  document.getElementById('subscriptionPlanSheet').classList.add('active');
}

function closeSubscriptionPlanSheet() {
  document.getElementById('subscriptionPlanSheet').classList.remove('active');
}

function renderSubscriptionPlanSummary(summary) {
  const totalPlans = Number(summary?.totalPlans || 0);
  const activePlans = Number(summary?.activePlans || 0);
  const activeSubscribers = Number(summary?.activeSubscribers || 0);
  const paidOrders = Number(summary?.paidOrderCount || 0);
  const revenueYuan = Number(summary?.totalRevenueYuan || 0);
  const rangeLabel = getRangeLabel('subscriptions');

  document.getElementById('subscriptionTotalPlans').textContent = fmtNum(totalPlans);
  document.getElementById('subscriptionActivePlans').textContent = fmtNum(activePlans);
  document.getElementById('subscriptionActiveSubscribers').textContent = fmtNum(activeSubscribers);
  document.getElementById('subscriptionTotalRevenue').textContent = fmtCurrencyYuan(revenueYuan);
  document.getElementById('subscriptionRevenueHint').textContent = `${rangeLabel} 已支付 ${fmtNum(paidOrders)} 单`;
  setHtml('subscriptionTableSummary', `<span class="llm-pill">时间段：${escHtml(rangeLabel)}</span><span class="llm-pill">已支付订单 ${fmtNum(paidOrders)} 笔</span><span class="llm-pill">收入 ${escHtml(fmtCurrencyYuan(revenueYuan))}</span>`);
  const summaryPill = document.getElementById('subscriptionSummaryPill');
  if (summaryPill) summaryPill.textContent = `${rangeLabel} · 收入 ${fmtCurrencyYuan(revenueYuan)}`;
}

function renderSubscriptionPlanTable(items) {
  const wrap = document.getElementById('subscriptionPlanTableWrap');
  if (!items || !items.length) {
    wrap.innerHTML = '<div class="workspace-empty">暂无订阅套餐</div>';
    return;
  }

  let html = '<div class="table-wrap"><table><thead><tr>' +
    '<th>套餐</th><th>价格 / 周期</th><th>额度</th><th>有效订阅</th><th>收入</th><th>状态</th><th>特性</th><th style="min-width:220px">操作</th>' +
    '</tr></thead><tbody>';

  for (const plan of items) {
    const featureHtml = Array.isArray(plan.features) && plan.features.length
      ? `<div class="subscription-plan-features">${plan.features.slice(0, 3).map((feature) => `<span class="llm-pill">${escHtml(feature)}</span>`).join('')}</div>`
      : '<span style="color:#94a3b8">-</span>';
    const statusBits = [
      `<span class="badge badge-${plan.active ? 'published' : 'draft'}">${plan.active ? '启用中' : '已归档'}</span>`,
      plan.recommended ? '<span class="badge badge-review">推荐</span>' : '',
    ].filter(Boolean).join(' ');
    html += `<tr>
      <td>
        <strong>${escHtml(plan.name)}</strong>
        <div style="font-size:11px;color:#94a3b8;margin-top:4px">${escHtml(plan.description || '暂无描述')}</div>
        <div style="font-size:11px;color:#94a3b8;margin-top:4px">ID: ${escHtml(plan.id)}</div>
      </td>
      <td>
        <div style="font-weight:800;color:#0f172a">${escHtml(fmtCurrencyYuan(plan.priceYuan || 0))}</div>
        <div style="font-size:11px;color:#64748b;margin-top:4px">${escHtml(plan.periodLabel || plan.period || '-')} / ${escHtml(plan.currency || 'CNY')}</div>
      </td>
      <td>
        <div style="font-weight:700;color:#0f172a">${fmtNum(plan.quota || 0)} 次</div>
        <div style="font-size:11px;color:#64748b;margin-top:4px">${escHtml(plan.quotaLabel || '-')}</div>
      </td>
      <td>
        <div style="font-weight:700;color:#0f172a">${fmtNum(plan.activeSubscribers || 0)}</div>
        <div style="font-size:11px;color:#64748b;margin-top:4px">当前有效订阅</div>
      </td>
      <td>
        <div style="font-weight:700;color:#0f172a">${escHtml(fmtCurrencyYuan(plan.revenueYuan || 0))}</div>
        <div style="font-size:11px;color:#64748b;margin-top:4px">订单 ${fmtNum(plan.orderCount || 0)} 笔</div>
      </td>
      <td>${statusBits}</td>
      <td>${featureHtml}</td>
      <td>
        <div class="action-cell">
          <button class="abtn abtn-edit" onclick="openSubscriptionPlanSheet('${plan.id}')">编辑</button>
          <button class="abtn ${plan.active ? 'abtn-warning' : 'abtn-success'}" onclick="toggleSubscriptionPlanActive('${plan.id}')">${plan.active ? '归档' : '启用'}</button>
          <button class="abtn abtn-delete" onclick="deleteSubscriptionPlanConfirm('${plan.id}','${escAttr(plan.name)}')">删除</button>
        </div>
      </td>
    </tr>`;
  }

  html += '</tbody></table></div>';
  wrap.innerHTML = html;
}

async function loadSubscriptionPlans() {
  const wrap = document.getElementById('subscriptionPlanTableWrap');
  if (wrap) {
    wrap.innerHTML = '<div class="workspace-empty"><div class="spinner"></div><div style="margin-top:8px">加载中...</div></div>';
  }
  try {
    const data = await api('/subscription/plans' + buildRangeQuery('subscriptions'));
    subscriptionPlans = Array.isArray(data?.items) ? data.items : [];
    renderSubscriptionPlanSummary(data?.summary || {});
    renderSubscriptionPlanTable(subscriptionPlans);
  } catch (e) {
    if (wrap) {
      wrap.innerHTML = `<div class="workspace-empty" style="color:#dc2626">加载失败: ${escHtml(e.message)}</div>`;
    }
  }
}

function buildSubscriptionPlanPayload() {
  return {
    name: document.getElementById('subscriptionPlanName').value.trim(),
    period: document.getElementById('subscriptionPlanPeriod').value,
    priceYuan: document.getElementById('subscriptionPlanPriceYuan').value.trim(),
    quota: document.getElementById('subscriptionPlanQuota').value.trim(),
    badge: document.getElementById('subscriptionPlanBadge').value.trim(),
    sortOrder: document.getElementById('subscriptionPlanSortOrder').value.trim(),
    recommended: document.getElementById('subscriptionPlanRecommended').checked,
    active: document.getElementById('subscriptionPlanActive').checked,
    description: document.getElementById('subscriptionPlanDescription').value.trim(),
    features: document.getElementById('subscriptionPlanFeatures').value,
  };
}

async function saveSubscriptionPlan() {
  const payload = buildSubscriptionPlanPayload();
  if (!payload.name) {
    toast('套餐名称不能为空', 'error');
    return;
  }
  if (payload.priceYuan === '') {
    toast('请填写价格（元）', 'error');
    return;
  }
  if (payload.quota === '') {
    toast('请填写额度', 'error');
    return;
  }

  try {
    if (currentSubscriptionPlanId) {
      await api('/subscription/plans/' + currentSubscriptionPlanId, { method: 'PUT', body: payload });
    } else {
      await api('/subscription/plans', { method: 'POST', body: payload });
    }
    toast('订阅套餐已保存');
    closeSubscriptionPlanSheet();
    loadSubscriptionPlans();
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
  }
}

async function toggleSubscriptionPlanActive(id) {
  const plan = subscriptionPlans.find(item => item.id === id);
  if (!plan) {
    toast('未找到套餐', 'error');
    return;
  }
  const actionLabel = plan.active ? '归档' : '启用';
  try {
    await api('/subscription/plans/' + id, {
      method: 'PUT',
      body: {
        name: plan.name,
        description: plan.description || '',
        priceYuan: plan.priceYuan,
        period: plan.period,
        quota: plan.quota,
        features: Array.isArray(plan.features) ? plan.features : [],
        recommended: plan.recommended,
        badge: plan.badge || '',
        sortOrder: plan.sortOrder,
        active: !plan.active,
      },
    });
    toast(`套餐已${actionLabel}`);
    loadSubscriptionPlans();
  } catch (e) {
    toast(`${actionLabel}失败: ` + e.message, 'error');
  }
}

async function deleteSubscriptionPlanConfirm(id, name) {
  const yes = await confirmDialog('确认删除套餐', `确定要删除套餐「${name}」吗？如果已有历史订单或订阅，系统会自动改为归档。`);
  if (!yes) return;
  try {
    const result = await api('/subscription/plans/' + id, { method: 'DELETE' });
    if (result?.deactivated) {
      toast('套餐存在历史订单，已自动归档');
    } else {
      toast('套餐已删除');
    }
    loadSubscriptionPlans();
  } catch (e) {
    toast('删除失败: ' + e.message, 'error');
  }
}

function readOptionalPositiveIntegerInput(id, fieldLabel) {
  const rawValue = document.getElementById(id)?.value?.trim() || '';
  if (!rawValue) return null;
  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed) || parsed <= 0 || Math.floor(parsed) !== parsed) {
    throw new Error(`${fieldLabel} must be a positive integer`);
  }
  return Math.floor(parsed);
}
