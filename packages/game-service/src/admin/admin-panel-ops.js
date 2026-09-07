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

async function showTaskDetail(taskId, options = {}) {
  currentTaskDetailId = taskId;
  const wrap = document.getElementById('taskDetailWrap');
  if (!options.silent) {
    wrap.innerHTML = '<div class="loading"><div class="spinner"></div><div style="margin-top:8px">加载详情...</div></div>';
  }
  try {
    const task = await api('/tasks/' + taskId);
    if (currentTaskDetailId !== taskId) return;
    wrap.innerHTML = buildTaskDetailMarkup(task, { title: '任务详情' });
    scheduleTaskDetailRefresh(task.id, task.status);
  } catch (e) {
    if (options.silent) return;
    clearTaskDetailRefresh();
    wrap.innerHTML = `<div class="loading" style="color:#dc2626">详情加载失败: ${escHtml(e.message)}</div>`;
  }
}

// ===================== LLM Gateway =====================

const LLM_BINDING_SLOTS = [
  {
    id: 'create_generate',
    label: '创建生成',
    stepKeys: ['code_generate.full'],
    required: true,
    allowFallback: true,
    followSlot: null,
  },
  {
    id: 'create_aux',
    label: '创建辅助',
    stepKeys: ['intent_parse', 'code_review', 'creative_anchors', 'quality_gate.patch_fix'],
    required: false,
    allowFallback: false,
    followSlot: 'create_generate',
  },
  {
    id: 'iterate',
    label: '迭代',
    stepKeys: ['iterate.classify', 'iterate.param_adjust', 'iterate.element_change', 'iterate.mechanic_change'],
    required: true,
    allowFallback: true,
    followSlot: null,
  },
  {
    id: 'qa_fix',
    label: 'QA 修复',
    stepKeys: ['qa_fix.syntax_structural'],
    required: true,
    allowFallback: false,
    followSlot: 'create_generate',
  },
];

function getRegionProviders() {
  return llmProviders.filter(provider => provider.region === currentExecutionRegion && provider.enabled !== false);
}

function getRouteByStepKey(stepKey) {
  return llmRoutes.find(route => route.stepKey === stepKey);
}

function readSlotSelectValue(slotId, kind = 'provider') {
  const element = document.getElementById(`llm-slot-${kind}-${slotId}`);
  return (element?.value || '').trim();
}

function buildProviderSelectOptions(regionProviders, selectedId, placeholder, options = {}) {
  const { includeFollow = false, excludeId = '' } = options;
  const items = [`<option value="" ${selectedId ? '' : 'selected'}>${escHtml(placeholder)}</option>`];
  if (includeFollow) {
    items.push(`<option value="__follow__" ${selectedId === '__follow__' ? 'selected' : ''}>跟随创建生成</option>`);
  }
  if (selectedId && selectedId !== '__follow__' && !regionProviders.some(provider => provider.id === selectedId)) {
    items.push(`<option value="${escAttr(selectedId)}" selected>[不可用] ${escHtml(selectedId)}</option>`);
  }
  regionProviders.forEach(provider => {
    if (excludeId && provider.id === excludeId) return;
    items.push(`<option value="${escAttr(provider.id)}" ${provider.id === selectedId ? 'selected' : ''}>${escHtml(provider.name)} · ${escHtml(provider.model || '-')}</option>`);
  });
  return items.join('');
}

function resolveSlotProviderId(slotId) {
  const slot = LLM_BINDING_SLOTS.find(item => item.id === slotId);
  if (!slot) return '';
  const selected = readSlotSelectValue(slotId, 'provider');
  if (selected === '__follow__' && slot.followSlot) {
    return resolveSlotProviderId(slot.followSlot);
  }
  if (selected) return selected;
  return inferSlotProviderSelection(slot).providerId;
}

function inferSlotProviderSelection(slot) {
  if (slot.followSlot) {
    const followProviderId = inferSlotProviderSelection(
      LLM_BINDING_SLOTS.find(item => item.id === slot.followSlot) || slot,
    ).providerId;
    const explicitRoutes = slot.stepKeys
      .map(stepKey => getRouteByStepKey(stepKey))
      .filter(route => route?.providerId);
    if (!explicitRoutes.length) {
      return { providerId: followProviderId ? '__follow__' : '', fallbackId: '' };
    }
    const primaryId = explicitRoutes[0].providerId;
    const allSame = explicitRoutes.every(route => route.providerId === primaryId);
    if (allSame && followProviderId && primaryId === followProviderId) {
      return { providerId: '__follow__', fallbackId: '' };
    }
    return { providerId: allSame ? primaryId : primaryId, fallbackId: '' };
  }

  const routes = slot.stepKeys
    .map(stepKey => getRouteByStepKey(stepKey))
    .filter(route => route?.providerId);
  if (!routes.length) {
    const effectiveRoute = slot.stepKeys.map(stepKey => getRouteByStepKey(stepKey)).find(Boolean);
    return {
      providerId: effectiveRoute?.providerId || '',
      fallbackId: effectiveRoute?.fallbackProviderIds?.[0] || '',
    };
  }
  const providerId = routes[0].providerId;
  const fallbackId = routes[0].fallbackProviderIds?.[0] || '';
  const allSame = routes.every(route => route.providerId === providerId);
  return {
    providerId: allSame ? providerId : routes[0].providerId,
    fallbackId: allSame ? fallbackId : '',
  };
}

function summarizeSlotStatus(slot) {
  const blockingStates = new Set(['missing', 'invalid_provider']);
  let tone = 'ok';
  for (const stepKey of slot.stepKeys) {
    const route = getRouteByStepKey(stepKey);
    if (!route) {
      if (slot.required) tone = 'error';
      continue;
    }
    if (slot.required && blockingStates.has(route.routeBindingState)) {
      return { tone: 'error', label: '阻塞' };
    }
    if (['inherited', 'provider_pool_fallback', 'fallback_active'].includes(route.routeBindingState)) {
      tone = tone === 'ok' ? 'warn' : tone;
    }
    if (route.effectiveProviderReadiness?.state === 'risk') {
      return { tone: 'error', label: '能力不匹配' };
    }
  }
  if (tone === 'warn') return { tone: 'warn', label: '继承/兜底' };
  return { tone: 'ok', label: '就绪' };
}

function renderSlotStatusCell(slot) {
  const status = summarizeSlotStatus(slot);
  const firstRoute = slot.stepKeys.map(stepKey => getRouteByStepKey(stepKey)).find(Boolean);
  const model = firstRoute?.modelDefault || firstRoute?.effectiveProvider?.model || '-';
  return `
    <div class="llm-status-dot ${escAttr(status.tone === 'ok' ? '' : status.tone)}">${escHtml(status.label)}</div>
    <div class="llm-row-sub" style="margin-top:6px">${escHtml(model)}</div>
  `;
}

function renderLlmSectionError(wrap, message) {
  if (!wrap) return;
  wrap.innerHTML = `<div class="loading" style="color:#dc2626">加载失败: ${escHtml(message)}</div>`;
}

async function loadLlmGateway() {
  const sidebarWrap = document.getElementById('llm2ProviderSidebarWrap');
  const slotWrap = document.getElementById('llm2SlotTableWrap');
  const advancedWrap = document.getElementById('llm2AdvancedRouteWrap');
  const loadingMarkup = '<div class="loading"><div class="spinner"></div><div style="margin-top:8px">加载中...</div></div>';
  if (sidebarWrap) sidebarWrap.innerHTML = loadingMarkup;
  if (slotWrap) slotWrap.innerHTML = loadingMarkup;
  if (advancedWrap) advancedWrap.innerHTML = loadingMarkup;

  currentExecutionRegion = document.getElementById('llm2-route-execution-region')?.value || currentExecutionRegion || 'cn_shanghai';
  const regionQuery = encodeURIComponent(currentExecutionRegion);
  const errors = [];

  try {
    llmProviders = await api('/llm/providers') || [];
  } catch (e) {
    llmProviders = [];
    errors.push('Provider: ' + e.message);
    renderLlmSectionError(sidebarWrap, e.message);
  }

  try {
    llmRoutes = await api('/llm/routes?executionRegion=' + regionQuery) || [];
  } catch (e) {
    llmRoutes = [];
    errors.push('步骤绑定: ' + e.message);
    renderLlmSectionError(slotWrap, e.message);
    if (advancedWrap) advancedWrap.innerHTML = '';
  }

  try {
    llmRegionTargets = await api('/cloud/ai-engine-region-targets?providerSelectableOnly=true') || [];
  } catch (e) {
    llmRegionTargets = [];
    errors.push('Region: ' + e.message);
  }

  updateCurrentRegionLabel();
  renderProviderRegionTargetOptions();
  renderExecutionRegionOptions();

  if (!errors.length || llmRoutes.length) {
    renderLlmStatusBar();
    renderSlotTable();
    renderAdvancedRouteTable();
  }

  if (!errors.length || llmProviders.length) {
    renderProviderSidebar();
    if (currentProviderId) {
      const provider = llmProviders.find(item => item.id === currentProviderId);
      if (provider) populateProviderForm(provider);
    } else {
      resetProviderForm();
    }
    if (currentTestProviderId) {
      currentTestProvider = llmProviders.find(item => item.id === currentTestProviderId) || null;
      renderProviderTestHeader();
    }
  }

  if (errors.length) {
    toast('加载 LLM 网关部分失败: ' + errors.join(' | '), 'error');
  }
}

function renderLlmStatusBar() {
  const wrap = document.getElementById('llm2StatusBar');
  if (!wrap) return;
  const requiredRoutes = llmRoutes.filter(route => route.bindingRequired);
  const blockingCount = requiredRoutes.filter(route => ['missing', 'invalid_provider'].includes(route.routeBindingState)).length;
  const enabledCount = getRegionProviders().length;
  const tone = blockingCount ? 'is-error' : 'is-ok';
  wrap.className = `llm-status-bar ${tone}`;
  wrap.innerHTML = `
    <div class="llm-status-bar-inner">
      <span class="llm-status-main">${blockingCount ? `${blockingCount} 处阻塞` : '全部就绪'}</span>
      <span class="llm-status-meta">${enabledCount} Provider · ${escHtml(currentExecutionRegion || 'cn_shanghai')}</span>
    </div>
  `;
}

function renderSlotTable() {
  const wrap = document.getElementById('llm2SlotTableWrap');
  if (!wrap) return;
  const regionProviders = getRegionProviders();
  let html = '<table class="llm-slot-table"><thead><tr><th>环节</th><th>Provider</th><th>状态</th></tr></thead><tbody>';
  for (const slot of LLM_BINDING_SLOTS) {
    const selection = inferSlotProviderSelection(slot);
    const providerOptions = buildProviderSelectOptions(
      regionProviders,
      selection.providerId,
      slot.required ? '请选择 Provider' : '可选',
      { includeFollow: Boolean(slot.followSlot) },
    );
    const fallbackOptions = slot.allowFallback
      ? buildProviderSelectOptions(
          regionProviders,
          selection.fallbackId,
          '无备用',
          { excludeId: selection.providerId === '__follow__' ? resolveSlotProviderId(slot.followSlot || slot.id) : selection.providerId },
        )
      : '';
    html += `<tr>
      <td>
        <div class="llm-slot-label">${escHtml(slot.label)}</div>
        <div class="llm-slot-steps">${escHtml(slot.stepKeys.join(' · '))}</div>
      </td>
      <td>
        <div class="llm-slot-bindings">
          <select id="llm-slot-provider-${escAttr(slot.id)}">${providerOptions}</select>
          ${slot.allowFallback ? `<select id="llm-slot-fallback-${escAttr(slot.id)}">${fallbackOptions}</select>` : ''}
        </div>
      </td>
      <td>${renderSlotStatusCell(slot)}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

function renderProviderSidebar() {
  const wrap = document.getElementById('llm2ProviderSidebarWrap');
  if (!wrap) return;
  const regionProviders = llmProviders.filter(provider => provider.region === currentExecutionRegion);
  if (!regionProviders.length) {
    wrap.innerHTML = '<div class="loading" style="padding:24px">当前区域暂无 Provider</div>';
    return;
  }
  wrap.innerHTML = `<div class="llm-provider-sidebar">${regionProviders.map(provider => {
    const latest = summarizeLatestTest(provider.latestTest);
    const statusClass = provider.enabled === false ? 'off' : 'ok';
    return `<div class="llm-provider-card">
      <div class="llm-provider-card-head">
        <div>
          <div class="llm-provider-card-name">${escHtml(provider.name)}</div>
          <div class="llm-provider-card-model">${escHtml(provider.model || '-')}${provider.fastModel ? ` · fast ${escHtml(provider.fastModel)}` : ''}</div>
        </div>
        <span class="llm-table-status ${statusClass}">${provider.enabled !== false ? '启用' : '停用'}</span>
      </div>
      <div class="llm-row-sub" style="margin-top:8px">${escHtml(latest)}</div>
      <div class="llm-provider-card-actions">
        <button class="abtn abtn-edit" onclick="openProviderSheet('${provider.id}')">编辑</button>
        <button class="abtn abtn-preview" onclick="openProviderTestSheet('${provider.id}')">测试</button>
        <button class="abtn abtn-delete" onclick="deleteProvider('${provider.id}')">删除</button>
      </div>
    </div>`;
  }).join('')}</div>`;
}

async function upsertRouteForStep(stepKey, providerId, fallbackProviderIds = [], enabled = true) {
  const route = getRouteByStepKey(stepKey);
  if (!providerId) return;
  await api(route?.id ? '/llm/routes/' + route.id : '/llm/routes', {
    method: route?.id ? 'PUT' : 'POST',
    body: {
      stepKey,
      executionRegion: currentExecutionRegion,
      providerId,
      fallbackProviderIds,
      enabled,
    },
  });
}

async function saveAllSlotBindings() {
  try {
    for (const slot of LLM_BINDING_SLOTS) {
      let providerId = readSlotSelectValue(slot.id, 'provider');
      if (providerId === '__follow__' && slot.followSlot) {
        providerId = resolveSlotProviderId(slot.followSlot);
        if (!providerId) {
          toast('请先在「创建生成」选择 Provider', 'error');
          return;
        }
      }
      if (!providerId) {
        if (slot.required) {
          toast(`${slot.label} 未选择 Provider`, 'error');
          return;
        }
        continue;
      }
      const fallbackId = slot.allowFallback ? readSlotSelectValue(slot.id, 'fallback') : '';
      const fallbackProviderIds = fallbackId && fallbackId !== providerId ? [fallbackId] : [];
      for (const stepKey of slot.stepKeys) {
        await upsertRouteForStep(stepKey, providerId, fallbackProviderIds, true);
      }
    }
    toast('步骤绑定已保存');
    await loadLlmGateway();
    await refreshGateway();
  } catch (e) {
    toast('保存步骤绑定失败: ' + e.message, 'error');
  }
}

function updateCurrentRegionLabel() {
  const label = document.getElementById('llm2CurrentRegionLabel');
  if (label) {
    label.textContent = currentExecutionRegion || 'cn_shanghai';
  }
}

function renderExecutionRegionOptions() {
  const select = document.getElementById('llm2-route-execution-region');
  if (!select) return;
  const previousValue = currentExecutionRegion || select.value || 'cn_shanghai';
  const regionMap = new Map();
  llmRegionTargets.forEach(target => {
    const region = target.executionRegion || target.region;
    if (region && !regionMap.has(region)) {
      regionMap.set(region, target);
    }
  });
  if (!regionMap.size) {
    select.innerHTML = `<option value="${escAttr(previousValue)}">${escHtml(previousValue)}</option>`;
    select.value = previousValue;
    currentExecutionRegion = previousValue;
    updateCurrentRegionLabel();
    return;
  }
  select.innerHTML = Array.from(regionMap.entries()).map(([region, target]) => {
    const suffix = target.cloudRegionCode ? ` · ${target.cloudRegionCode}` : '';
    return `<option value="${escAttr(region)}">${escHtml(target.displayName || region)}${escHtml(suffix)}</option>`;
  }).join('');
  if (regionMap.has(previousValue)) {
    select.value = previousValue;
  } else {
    select.value = Array.from(regionMap.keys())[0] || 'cn_shanghai';
  }
  currentExecutionRegion = select.value || previousValue;
  updateCurrentRegionLabel();
}

function renderProviderRegionTargetOptions(selectId = 'llm2-provider-region-target') {
  const select = document.getElementById(selectId);
  if (!select) return;
  const previousValue = select.value;
  if (!llmRegionTargets.length) {
    select.innerHTML = '<option value="">暂无可用 Region Target</option>';
    return;
  }
  select.innerHTML = llmRegionTargets.map(target => (
    `<option value="${escAttr(target.id)}">${escHtml(target.displayName || target.executionRegion)} · ${escHtml(target.cloudRegionCode || '')}</option>`
  )).join('');
  if (previousValue && Array.from(select.options).some(option => option.value === previousValue)) {
    select.value = previousValue;
  }
}

function handleExecutionRegionChange() {
  currentExecutionRegion = document.getElementById('llm2-route-execution-region')?.value || 'cn_shanghai';
  updateCurrentRegionLabel();
  loadLlmGateway();
}

function summarizeLatestTest(test) {
  if (!test) return '暂无测试';
  const segments = [test.success ? '成功' : '失败'];
  if (test.latencyMs !== null && test.latencyMs !== undefined) segments.push(`${test.latencyMs}ms`);
  if (test.model) segments.push(test.model);
  if (!test.success && test.errorMessage) segments.push(test.errorMessage);
  return segments.join(' · ');
}

function setProviderTestStatusBanner(type, title, detail = '') {
  const banner = document.getElementById('llm2TestStatusBanner');
  if (!banner) return;
  if (!title) {
    banner.hidden = true;
    banner.className = 'llm-status-banner llm-status-info';
    banner.innerHTML = '';
    return;
  }
  banner.hidden = false;
  banner.className = `llm-status-banner llm-status-${type || 'info'}`;
  banner.innerHTML = `
    <div class="llm-status-title">${escHtml(title)}</div>
    ${detail ? `<div class="llm-status-detail">${escHtml(detail)}</div>` : ''}
  `;
}

function getDefaultCatalogApiUrl(vendorPreset, baseUrl) {
  if ((vendorPreset || 'generic') === 'modelverse') {
    return 'https://api.modelverse.cn/v1/models';
  }
  const trimmed = (baseUrl || '').trim().replace(/\/+$/, '');
  if (!trimmed) return '';
  if (trimmed.endsWith('/chat/completions')) {
    return trimmed.slice(0, -('/chat/completions'.length)) + '/models';
  }
  return trimmed + '/models';
}

function llmCapabilityLabel(capability) {
  const labels = {
    supports_full_html_rewrite: '完整 HTML 重写',
    supports_patch_generation: '补丁生成',
    supports_dialogue: '对话/分类',
    verified: '已验证',
  };
  return labels[capability] || capability || '-';
}

function renderCapabilityList(capabilities) {
  const values = Array.isArray(capabilities) ? capabilities.filter(Boolean) : [];
  if (!values.length) return '无';
  return values.map(llmCapabilityLabel).join(' / ');
}

function renderProviderCapabilitySummary(provider) {
  const summary = provider?.capabilitySummary || {};
  if (summary.compatibilityMode) {
    return '<div class="llm-row-sub">能力未标注：运行时按兼容放行</div>';
  }
  const known = Array.isArray(summary.knownCapabilities) ? summary.knownCapabilities : [];
  const enabled = known.filter(item => item.enabled).map(item => item.key);
  const disabled = known.filter(item => item.enabled === false).map(item => item.key);
  const unsafe = Array.isArray(summary.unsafeForSteps) ? summary.unsafeForSteps : [];
  const lines = [];
  if (enabled.length) {
    lines.push(`能力 ${renderCapabilityList(enabled)}`);
  }
  if (disabled.length) {
    lines.push(`禁用 ${renderCapabilityList(disabled)}`);
  }
  if (unsafe.length) {
    lines.push(`不适用 ${renderCapabilityList(unsafe)}`);
  }
  return `<div class="llm-row-sub">${escHtml(lines.join('；') || '能力未声明')}</div>`;
}

function llmJourneyLabel(journey) {
  const labels = {
    session_generate: '会话生成',
    direct_create: '直接生成',
    iterate: '迭代',
    auxiliary: '辅助工具',
    unclassified: '未分类',
  };
  return labels[journey] || journey;
}

function summarizeRouteState(route) {
  if (route.routeBindingState === 'configured') {
    return { label: '精确绑定', tone: 'ok' };
  }
  if (route.routeBindingState === 'fallback_active') {
    return { label: '显式回退生效', tone: 'fallback' };
  }
  if (route.routeBindingState === 'inherited') {
    return { label: '继承父级', tone: 'inherit' };
  }
  if (route.routeBindingState === 'provider_pool_fallback') {
    return { label: 'Provider 池兜底', tone: 'pool' };
  }
  if (route.routeBindingState === 'invalid_provider') {
    return { label: '绑定失效', tone: 'invalid' };
  }
  if (route.routeBindingState === 'disabled') {
    return { label: route.bindingRequired ? '已禁用（阻塞）' : '已禁用（可选）', tone: 'off' };
  }
  if (route.bindingRequired) {
    return { label: '缺失（阻塞）', tone: 'warn' };
  }
  return { label: '缺失（可选）', tone: 'optional' };
}

function renderRouteStateBadge(route) {
  const state = summarizeRouteState(route);
  if (state.tone === 'ok') {
    return `<span class="llm-table-status ok">${escHtml(state.label)}</span>`;
  }
  if (state.tone === 'fallback') {
    return `<span class="llm-table-badge" style="background:#fffbeb;color:#b45309;border-color:#fde68a">${escHtml(state.label)}</span>`;
  }
  if (state.tone === 'off') {
    return `<span class="llm-table-status off">${escHtml(state.label)}</span>`;
  }
  if (state.tone === 'inherit') {
    return `<span class="llm-table-badge" style="background:#eff6ff;color:#1d4ed8;border-color:#bfdbfe">${escHtml(state.label)}</span>`;
  }
  if (state.tone === 'pool') {
    return `<span class="llm-table-badge" style="background:#fffbeb;color:#b45309;border-color:#fde68a">${escHtml(state.label)}</span>`;
  }
  if (state.tone === 'invalid') {
    return `<span class="llm-table-badge" style="background:#fef2f2;color:#b91c1c;border-color:#fecaca">${escHtml(state.label)}</span>`;
  }
  if (state.tone === 'warn') {
    return `<span class="llm-table-badge" style="background:#fef2f2;color:#b91c1c;border-color:#fecaca">${escHtml(state.label)}</span>`;
  }
  return `<span class="llm-table-badge llm-table-badge-muted">${escHtml(state.label)}</span>`;
}

function renderRouteJourneyBadges(route) {
  const journeys = Array.isArray(route.journeys) ? route.journeys : [];
  if (!journeys.length) {
    return '<span class="llm-table-badge llm-table-badge-muted">未分类</span>';
  }
  return journeys.map(journey => (
    `<span class="llm-table-badge llm-table-badge-muted">${escHtml(llmJourneyLabel(journey))}</span>`
  )).join('');
}

function renderRouteOutputMeta(route) {
  const minTokens = route.minOutputTokens ? String(route.minOutputTokens) : '-';
  const maxTokens = route.maxOutputTokens ? String(route.maxOutputTokens) : '-';
  return [
    `<div class="llm-row-sub">输出 ${escHtml(route.outputClass || 'medium_structured')} · min ${escHtml(minTokens)} · max ${escHtml(maxTokens)}</div>`,
    `<div class="llm-row-sub">需要能力 ${escHtml(renderCapabilityList(route.requiredCapabilities))}</div>`,
  ].join('');
}

function renderRouteReadiness(readiness) {
  if (!readiness) {
    return '<div class="llm-row-sub" style="color:#b91c1c">无有效 Provider，运行会失败或只能依赖环境兜底</div>';
  }
  const state = readiness.state || 'unknown';
  const stateMeta = {
    ok: {
      label: '能力/Token OK',
      style: 'background:#ecfdf5;color:#047857;border-color:#a7f3d0',
    },
    assumed: {
      label: '兼容放行',
      style: 'background:#fffbeb;color:#b45309;border-color:#fde68a',
    },
    risk: {
      label: '能力风险',
      style: 'background:#fef2f2;color:#b91c1c;border-color:#fecaca',
    },
  }[state] || {
    label: '未知',
    style: 'background:#f8fafc;color:#475569;border-color:#e2e8f0',
  };
  const detailParts = [];
  if (Array.isArray(readiness.missingCapabilities) && readiness.missingCapabilities.length) {
    detailParts.push(`缺失 ${renderCapabilityList(readiness.missingCapabilities)}`);
  }
  if (Array.isArray(readiness.unknownCapabilities) && readiness.unknownCapabilities.length) {
    detailParts.push(`未声明 ${renderCapabilityList(readiness.unknownCapabilities)}`);
  }
  if (readiness.tokenState === 'insufficient') {
    detailParts.push(`max_tokens ${readiness.providerMaxTokens || '-'} < ${readiness.requiredOutputTokens || '-'}`);
  } else if (readiness.tokenState === 'unknown' && readiness.requiredOutputTokens) {
    detailParts.push(`max_tokens 未声明，需要 ${readiness.requiredOutputTokens}`);
  }
  if (!detailParts.length && readiness.requiredOutputTokens) {
    detailParts.push(`max_tokens ${readiness.providerMaxTokens || '-'} / 需要 ${readiness.requiredOutputTokens}`);
  }
  if (!detailParts.length) {
    detailParts.push('无硬性输出 token 下限');
  }
  return `<div style="margin-top:8px"><span class="llm-table-badge" style="${stateMeta.style}">${escHtml(stateMeta.label)}</span></div><div class="llm-row-sub">${escHtml(detailParts.join('；'))}</div>`;
}

function readSelectValue(selectId) {
  const select = document.getElementById(selectId);
  return (select?.value || '').trim();
}

function renderAdvancedRouteTable() {
  const wrap = document.getElementById('llm2AdvancedRouteWrap');
  if (!wrap) return;
  if (!llmRoutes.length) {
    wrap.innerHTML = '<div class="loading" style="padding:16px 0">暂无 stepKey</div>';
    return;
  }
  const regionProviders = getRegionProviders();
  let html = '<table class="llm-slot-table"><thead><tr><th>stepKey</th><th>Provider</th><th>状态</th><th></th></tr></thead><tbody>';
  for (const route of llmRoutes) {
    const domKey = route.stepKey.replace(/[^a-zA-Z0-9_-]/g, '-');
    const stepTitle = route.displayName ? `${route.displayName}` : route.stepKey;
    const providerOptions = buildProviderSelectOptions(
      regionProviders,
      route.providerId || '',
      route.providerId ? '更换 Provider' : '未绑定',
    );
    html += `<tr>
      <td>
        <div class="llm-row-title">${escHtml(stepTitle)}</div>
        <div class="llm-row-sub">${escHtml(route.stepKey)}</div>
      </td>
      <td>
        <select id="llm-route-binding-provider-${escAttr(domKey)}">${providerOptions}</select>
      </td>
      <td>${renderRouteStateBadge(route)}</td>
      <td><button class="abtn abtn-preview" onclick="saveRouteBinding('${escAttr(route.stepKey)}')">保存</button></td>
    </tr>`;
  }
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

async function saveRouteBinding(stepKey) {
  const route = llmRoutes.find(item => item.stepKey === stepKey);
  if (!route) {
    toast('未找到步骤绑定', 'error');
    return;
  }
  const domKey = stepKey.replace(/[^a-zA-Z0-9_-]/g, '-');
  const providerId = document.getElementById(`llm-route-binding-provider-${domKey}`)?.value || '';
  const fallbackProviderIds = [];
  const enabled = true;
  if (!providerId) {
    toast('请先选择 Provider', 'error');
    return;
  }
  try {
    await api(route.id ? '/llm/routes/' + route.id : '/llm/routes', {
      method: route.id ? 'PUT' : 'POST',
      body: {
        stepKey: route.stepKey,
        executionRegion: route.executionRegion || currentExecutionRegion,
        providerId,
        fallbackProviderIds,
        enabled,
      },
    });
    toast('步骤绑定已保存');
    await loadLlmGateway();
  } catch (e) {
    toast('保存步骤绑定失败: ' + e.message, 'error');
  }
}

function resetProviderForm() {
  currentProviderId = null;
  llmProviderCatalogOptions = [];
  renderProviderCatalogOptions();
  renderProviderRegionTargetOptions();
  document.getElementById('llm2ProviderSheetTitle').textContent = '新建 Provider';
  document.getElementById('llm2-provider-name').value = '';
  document.getElementById('llm2-provider-type').value = 'openai_compatible';
  document.getElementById('llm2-provider-vendor-preset').value = 'generic';
  document.getElementById('llm2-provider-region-target').value = llmRegionTargets[0]?.id || '';
  document.getElementById('llm2-provider-priority').value = '100';
  document.getElementById('llm2-provider-base-url').value = '';
  document.getElementById('llm2-provider-api-key').value = '';
  document.getElementById('llm2-provider-model').value = '';
  document.getElementById('llm2-provider-fast-model').value = '';
  document.getElementById('llm2-provider-timeout').value = '600';
  document.getElementById('llm2-provider-connect-timeout').value = '15';
  document.getElementById('llm2-provider-context-window').value = '';
  document.getElementById('llm2-provider-max-tokens').value = '';
  document.getElementById('llm2-provider-description').value = '';
  document.getElementById('llm2-provider-enabled').checked = true;
  setProviderCapabilityForm({});
  document.getElementById('llm2-provider-catalog-mode').value = 'auto';
  document.getElementById('llm2-provider-catalog-auth-mode').value = 'inherit_provider';
  document.getElementById('llm2-provider-catalog-api-url').value = '';
  document.getElementById('llm2-provider-catalog-api-key').value = '';
  document.getElementById('llm2-provider-api-key-hint').textContent = '新建 Provider 时必须输入 API Key。';
  document.getElementById('llm2-provider-catalog-api-key-hint').textContent = '默认继承 Provider API Key。';
  document.getElementById('llm2ProviderCatalogStatus').textContent = '尚未加载模型目录。';
  toggleCatalogModeFields();
}

function setProviderCapabilityForm(flags = {}) {
  document.getElementById('llm2-cap-full-html').checked = flags.supports_full_html_rewrite === true;
  document.getElementById('llm2-cap-patch').checked = flags.supports_patch_generation === true;
  document.getElementById('llm2-cap-dialogue').checked = flags.supports_dialogue === true;
  document.getElementById('llm2-cap-verified').checked = flags.verified === true;
}

function readProviderCapabilityForm() {
  return {
    supports_full_html_rewrite: document.getElementById('llm2-cap-full-html').checked,
    supports_patch_generation: document.getElementById('llm2-cap-patch').checked,
    supports_dialogue: document.getElementById('llm2-cap-dialogue').checked,
    verified: document.getElementById('llm2-cap-verified').checked,
  };
}

function populateProviderForm(provider) {
  if (!provider) return;
  currentProviderId = provider.id;
  llmProviderCatalogOptions = [];
  renderProviderCatalogOptions();
  document.getElementById('llm2ProviderSheetTitle').textContent = '编辑 Provider';
  document.getElementById('llm2-provider-name').value = provider.name || '';
  document.getElementById('llm2-provider-type').value = provider.providerType || 'openai_compatible';
  document.getElementById('llm2-provider-vendor-preset').value = provider.vendorPreset || 'generic';
  renderProviderRegionTargetOptions();
  document.getElementById('llm2-provider-region-target').value = provider.regionTargetId || '';
  document.getElementById('llm2-provider-priority').value = provider.priority ?? 100;
  document.getElementById('llm2-provider-base-url').value = provider.baseUrl || '';
  document.getElementById('llm2-provider-api-key').value = '';
  document.getElementById('llm2-provider-model').value = provider.model || '';
  document.getElementById('llm2-provider-fast-model').value = provider.fastModel || '';
  document.getElementById('llm2-provider-timeout').value = provider.requestTimeoutS ?? 600;
  document.getElementById('llm2-provider-connect-timeout').value = provider.connectTimeoutS ?? 15;
  document.getElementById('llm2-provider-context-window').value = provider.contextWindow ?? '';
  document.getElementById('llm2-provider-max-tokens').value = provider.maxTokens ?? '';
  document.getElementById('llm2-provider-description').value = provider.description || '';
  document.getElementById('llm2-provider-enabled').checked = provider.enabled !== false;
  document.getElementById('llm2-provider-catalog-mode').value = provider.catalogMode || 'auto';
  document.getElementById('llm2-provider-catalog-auth-mode').value = provider.catalogAuthMode || 'inherit_provider';
  document.getElementById('llm2-provider-catalog-api-url').value = provider.catalogApiUrl || '';
  document.getElementById('llm2-provider-catalog-api-key').value = '';
  document.getElementById('llm2-provider-api-key-hint').textContent = provider.apiKeySet
    ? `已保存密钥：${provider.apiKeyMasked || '已设置'}；留空表示保留原值。`
    : '尚未保存 API Key。';
  document.getElementById('llm2-provider-catalog-api-key-hint').textContent = provider.catalogApiKeySet
    ? `已保存目录 Token：${provider.catalogApiKeyMasked || '已设置'}；留空表示保留原值。`
    : '默认继承 Provider API Key。';
  document.getElementById('llm2ProviderCatalogStatus').textContent = provider.catalogApiUrl
    ? `当前目录地址：${provider.catalogApiUrl}`
    : '尚未加载模型目录。';
  setProviderCapabilityForm(provider.capabilityFlags || {});
  toggleCatalogModeFields();
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

function openProviderSheet(id) {
  if (id) {
    const provider = llmProviders.find(item => item.id === id);
    if (!provider) {
      toast('未找到 Provider', 'error');
      return;
    }
    populateProviderForm(provider);
  } else {
    resetProviderForm();
  }
  document.getElementById('llmProviderSheet').classList.add('active');
}

function closeProviderSheet() {
  document.getElementById('llmProviderSheet').classList.remove('active');
}

function applyProviderVendorPreset() {
  const vendorPreset = document.getElementById('llm2-provider-vendor-preset').value || 'generic';
  const baseUrlInput = document.getElementById('llm2-provider-base-url');
  if (vendorPreset === 'modelverse') {
    document.getElementById('llm2-provider-type').value = 'openai_compatible';
    if (!baseUrlInput.value.trim()) {
      baseUrlInput.value = 'https://api.modelverse.cn/v1';
    }
  }
  toggleCatalogModeFields();
}

function toggleCatalogModeFields() {
  const mode = document.getElementById('llm2-provider-catalog-mode').value || 'auto';
  const authMode = document.getElementById('llm2-provider-catalog-auth-mode').value || 'inherit_provider';
  const urlInput = document.getElementById('llm2-provider-catalog-api-url');
  const tokenWrap = document.getElementById('llm2ProviderCatalogTokenWrap');
  const tokenInput = document.getElementById('llm2-provider-catalog-api-key');
  if (mode === 'auto') {
    urlInput.readOnly = true;
    urlInput.value = getDefaultCatalogApiUrl(
      document.getElementById('llm2-provider-vendor-preset').value,
      document.getElementById('llm2-provider-base-url').value,
    );
  } else {
    urlInput.readOnly = false;
    if (!urlInput.value.trim()) {
      urlInput.value = getDefaultCatalogApiUrl(
        document.getElementById('llm2-provider-vendor-preset').value,
        document.getElementById('llm2-provider-base-url').value,
      );
    }
  }
  if (tokenWrap) {
    tokenWrap.style.display = authMode === 'bearer_token' ? 'block' : 'none';
  }
  if (tokenInput) {
    tokenInput.disabled = authMode !== 'bearer_token';
  }
}

function renderProviderCatalogOptions() {
  const markup = llmProviderCatalogOptions.map(item => (
    `<option value="${escAttr(item.id)}">${escHtml(item.label || item.id)}</option>`
  )).join('');
  document.getElementById('llm2-provider-model-options').innerHTML = markup;
  document.getElementById('llm2-provider-fast-model-options').innerHTML = markup;
}

async function loadProviderCatalogPreview() {
  const regionTargetId = document.getElementById('llm2-provider-region-target').value;
  if (!regionTargetId) {
    toast('请先选择 Region Target', 'error');
    return;
  }
  try {
    document.getElementById('llm2ProviderCatalogStatus').textContent = '正在加载模型目录...';
    const result = await api('/llm/providers/catalog/preview', {
      method: 'POST',
      body: {
        providerId: currentProviderId,
        regionTargetId,
        providerType: document.getElementById('llm2-provider-type').value,
        vendorPreset: document.getElementById('llm2-provider-vendor-preset').value,
        baseUrl: document.getElementById('llm2-provider-base-url').value.trim(),
        apiKey: document.getElementById('llm2-provider-api-key').value.trim(),
        catalogMode: document.getElementById('llm2-provider-catalog-mode').value,
        catalogApiUrl: document.getElementById('llm2-provider-catalog-api-url').value.trim(),
        catalogAuthMode: document.getElementById('llm2-provider-catalog-auth-mode').value,
        catalogApiKey: document.getElementById('llm2-provider-catalog-api-key').value.trim(),
      },
    });
    llmProviderCatalogOptions = result.models || [];
    renderProviderCatalogOptions();
    document.getElementById('llm2ProviderCatalogStatus').textContent = `已加载 ${llmProviderCatalogOptions.length} 个模型 · ${result.resolvedCatalogApiUrl}`;
    toast(`已加载 ${llmProviderCatalogOptions.length} 个模型`, 'success');
  } catch (e) {
    document.getElementById('llm2ProviderCatalogStatus').textContent = `加载失败：${e.message}`;
    toast('加载模型目录失败: ' + e.message, 'error');
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

async function saveProvider() {
  try {
    const body = {
      name: document.getElementById('llm2-provider-name').value.trim(),
      providerType: document.getElementById('llm2-provider-type').value,
      vendorPreset: document.getElementById('llm2-provider-vendor-preset').value,
      regionTargetId: document.getElementById('llm2-provider-region-target').value,
      priority: Number(document.getElementById('llm2-provider-priority').value || 100),
      baseUrl: document.getElementById('llm2-provider-base-url').value.trim(),
      apiKey: document.getElementById('llm2-provider-api-key').value.trim(),
      model: document.getElementById('llm2-provider-model').value.trim(),
      fastModel: document.getElementById('llm2-provider-fast-model').value.trim(),
      requestTimeoutS: Number(document.getElementById('llm2-provider-timeout').value || 600),
      connectTimeoutS: Number(document.getElementById('llm2-provider-connect-timeout').value || 15),
      contextWindow: readOptionalPositiveIntegerInput('llm2-provider-context-window', 'Context'),
      maxTokens: readOptionalPositiveIntegerInput('llm2-provider-max-tokens', 'MaxTokens'),
      description: document.getElementById('llm2-provider-description').value.trim(),
      enabled: document.getElementById('llm2-provider-enabled').checked,
      catalogMode: document.getElementById('llm2-provider-catalog-mode').value,
      catalogApiUrl: document.getElementById('llm2-provider-catalog-api-url').value.trim(),
      catalogAuthMode: document.getElementById('llm2-provider-catalog-auth-mode').value,
      catalogApiKey: document.getElementById('llm2-provider-catalog-api-key').value.trim(),
      capabilityFlags: readProviderCapabilityForm(),
    };
    await api(currentProviderId ? '/llm/providers/' + currentProviderId : '/llm/providers', {
      method: currentProviderId ? 'PUT' : 'POST',
      body,
    });
    toast('Provider 已保存');
    closeProviderSheet();
    resetProviderForm();
    await loadLlmGateway();
  } catch (e) {
    toast('保存 Provider 失败: ' + e.message, 'error');
  }
}

function renderProviderTestHeader() {
  const meta = document.getElementById('llm2TestProviderMeta');
  const smokeResult = document.getElementById('llm2TestSmokeResult');
  const title = document.getElementById('llm2TestSheetTitle');
  if (!currentTestProvider) {
    if (meta) meta.textContent = '-';
    if (smokeResult) smokeResult.textContent = '尚未执行。';
    if (title) title.textContent = 'Provider 测试台';
    setProviderTestStatusBanner('', '');
    return;
  }
  if (title) {
    title.textContent = `Provider 测试台 · ${currentTestProvider.name}`;
  }
  if (meta) {
    meta.textContent = `${currentTestProvider.providerType} · ${currentTestProvider.regionDisplayName || currentTestProvider.region} · 主模型 ${currentTestProvider.model}${currentTestProvider.fastModel ? ` · 快模型 ${currentTestProvider.fastModel}` : ''}`;
  }
  if (smokeResult) {
    smokeResult.textContent = summarizeLatestTest(currentTestProvider.latestTest);
  }
}

function openProviderTestSheet(id) {
  const provider = llmProviders.find(item => item.id === id);
  if (!provider) {
    toast('未找到 Provider', 'error');
    return;
  }
  currentTestProviderId = id;
  currentTestProvider = provider;
  llmTestMessages = [];
  document.getElementById('llm2-test-chat-input').value = '';
  renderProviderChatThread();
  renderProviderTestHeader();
  setProviderTestStatusBanner('info', '测试台已就绪');
  document.getElementById('llmProviderTestSheet').classList.add('active');
  loadProviderTestRecords();
}

function closeProviderTestSheet() {
  document.getElementById('llmProviderTestSheet').classList.remove('active');
  setProviderTestStatusBanner('', '');
}

async function runProviderSmokeTest() {
  if (!currentTestProviderId) {
    toast('请先选择 Provider', 'error');
    return;
  }
  try {
    setProviderTestStatusBanner('loading', '正在进行连通测试');
    const result = await api('/llm/providers/' + currentTestProviderId + '/test', { method: 'POST' });
    if (currentTestProvider) {
      currentTestProvider.latestTest = result;
    }
    renderProviderTestHeader();
    await loadProviderTestRecords();
    await loadLlmGateway();
    const detail = [
      result.model || '',
      result.latencyMs !== null && result.latencyMs !== undefined ? `${result.latencyMs}ms` : '',
      result.resolvedEndpoint || '',
      result.errorMessage || '',
    ].filter(Boolean).join(' · ');
    setProviderTestStatusBanner(
      result.success ? 'success' : 'error',
      result.success ? '连通测试成功' : '连通测试失败',
      detail,
    );
  } catch (e) {
    setProviderTestStatusBanner('error', '测试 Provider 失败', e.message || '未知错误');
  }
}

function clearProviderChatSession() {
  llmTestMessages = [];
  renderProviderChatThread();
}

function renderProviderChatThread() {
  const wrap = document.getElementById('llm2TestChatThread');
  if (!wrap) return;
  if (!llmTestMessages.length) {
    wrap.innerHTML = '<div class="llm-empty">暂无消息</div>';
    return;
  }
  wrap.innerHTML = llmTestMessages.map(message => {
    const meta = [];
    if (message.model) meta.push(message.model);
    if (message.latencyMs !== undefined && message.latencyMs !== null) meta.push(`${message.latencyMs}ms`);
    if (message.testedAt) meta.push(fmtDateTime(message.testedAt));
    if (message.pending) meta.push('生成中...');
    if (message.errorMessage) meta.push(message.errorMessage);
    return `<div class="llm-chat-row ${escAttr(message.role)}">
      <div class="llm-chat-bubble" style="${message.success === false ? 'border-color:#fecaca;background:#fef2f2' : ''}">
        <div style="font-size:11px;color:#64748b;margin-bottom:6px">${message.role === 'user' ? 'User' : 'Assistant'}</div>
        <div style="white-space:pre-wrap;word-break:break-word">${escHtml(message.content || '')}</div>
        ${meta.length ? `<div style="margin-top:8px;font-size:11px;color:${message.success === false ? '#b91c1c' : '#64748b'}">${escHtml(meta.join(' · '))}</div>` : ''}
      </div>
    </div>`;
  }).join('');
  wrap.scrollTop = wrap.scrollHeight;
}

async function sendProviderChatMessage() {
  if (!currentTestProviderId) {
    toast('请先选择 Provider', 'error');
    return;
  }
  const input = document.getElementById('llm2-test-chat-input');
  const content = input.value.trim();
  if (!content) {
    toast('请输入消息内容', 'error');
    return;
  }
  llmTestMessages.push({ role: 'user', content, testedAt: new Date().toISOString() });
  input.value = '';
  const pendingIndex = llmTestMessages.push({ role: 'assistant', content: '正在请求 Provider...', pending: true }) - 1;
  renderProviderChatThread();
  const requestMessages = llmTestMessages
    .filter(item => (item.role === 'user' || item.role === 'assistant') && !item.pending)
    .map(item => ({ role: item.role, content: item.content }));
  try {
    setProviderTestStatusBanner('loading', '正在请求 Provider 回复');
    const result = await api('/llm/providers/' + currentTestProviderId + '/test-chat', {
      method: 'POST',
      body: {
        messages: requestMessages,
        useFastModel: document.getElementById('llm2-test-use-fast-model').checked,
      },
    });
    llmTestMessages[pendingIndex] = {
      role: 'assistant',
      content: result.reply || '(empty response)',
      testedAt: result.testedAt,
      latencyMs: result.latencyMs,
      model: result.model,
      success: result.success,
      errorMessage: result.errorMessage || '',
    };
    if (currentTestProvider) {
      currentTestProvider.latestTest = result;
    }
    renderProviderChatThread();
    renderProviderTestHeader();
    await loadProviderTestRecords();
    await loadLlmGateway();
    const detail = [
      result.model || '',
      result.latencyMs !== null && result.latencyMs !== undefined ? `${result.latencyMs}ms` : '',
      result.testedAt ? fmtDateTime(result.testedAt) : '',
      result.errorMessage || '',
    ].filter(Boolean).join(' · ');
    setProviderTestStatusBanner(
      result.success === false ? 'error' : 'success',
      result.success === false ? '本轮对话返回失败状态' : '本轮对话已完成',
      detail,
    );
  } catch (e) {
    llmTestMessages[pendingIndex] = {
      role: 'assistant',
      content: '请求失败',
      testedAt: new Date().toISOString(),
      success: false,
      errorMessage: e.message,
    };
    renderProviderChatThread();
    setProviderTestStatusBanner('error', 'Provider 对话测试失败', e.message || '未知错误');
  }
}

async function loadProviderTestRecords() {
  const wrap = document.getElementById('llm2ProviderTestRecords');
  if (!wrap) return;
  if (!currentTestProviderId) {
    wrap.innerHTML = '<div class="llm-empty">请先选择 Provider</div>';
    return;
  }
  wrap.innerHTML = '<div class="loading"><div class="spinner"></div><div style="margin-top:8px">加载测试记录...</div></div>';
  try {
    const records = await api('/llm/providers/' + currentTestProviderId + '/test-records?limit=20');
    if (!records.length) {
      wrap.innerHTML = '<div class="llm-empty">暂无测试记录</div>';
      return;
    }
    wrap.innerHTML = records.map(record => `
      <div class="llm-record-item">
        <div class="llm-record-top">
          <strong style="color:${record.success ? '#166534' : '#b91c1c'}">${record.success ? '成功' : '失败'}</strong>
          <span style="font-size:12px;color:#64748b">${escHtml(fmtDateTime(record.testedAt))}</span>
        </div>
        <div class="llm-record-meta">
          <span>模型 ${escHtml(record.model || '-')}</span>
          <span>延时 ${record.latencyMs ?? '-'}ms</span>
          <span>状态码 ${record.httpStatus ?? '-'}</span>
        </div>
        <div class="llm-record-meta">
          <span>端点 ${escHtml(record.resolvedEndpoint || '-')}</span>
        </div>
        ${record.errorMessage ? `<div style="margin-top:8px;font-size:12px;color:#b91c1c;white-space:pre-wrap">${escHtml(record.errorMessage)}</div>` : ''}
      </div>
    `).join('');
  } catch (e) {
    wrap.innerHTML = `<div class="llm-empty" style="color:#dc2626">加载失败: ${escHtml(e.message)}</div>`;
  }
}

async function deleteProvider(id) {
  const yes = await confirmDialog('确认删除', '确定删除这个 Provider 吗？');
  if (!yes) return;
  try {
    await api('/llm/providers/' + id, { method: 'DELETE' });
    toast('Provider 已删除');
    if (currentProviderId === id) {
      closeProviderSheet();
      resetProviderForm();
    }
    if (currentTestProviderId === id) {
      closeProviderTestSheet();
      currentTestProviderId = null;
      currentTestProvider = null;
      llmTestMessages = [];
    }
    await loadLlmGateway();
  } catch (e) {
    toast('删除 Provider 失败: ' + e.message, 'error');
  }
}

async function refreshGateway() {
  try {
    await api('/llm/refresh', { method: 'POST' });
    toast('LLM 网关已热刷新');
    await loadLlmGateway();
  } catch (e) {
    toast('热刷新失败: ' + e.message, 'error');
  }
}

