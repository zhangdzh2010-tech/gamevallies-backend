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
          <div class="log-card-sub">${escHtml(options.subtitle || '提示词准入阶段的去重指标')}</div>
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
        subtitle: '本次路由快照记录的提示词去重指标',
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
          subtitle: '当前已采集 LLM 调用的聚合去重指标',
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
        subtitle: '当前已采集 LLM 调用的聚合去重指标',
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

async function loadLlmGateway() {
  const providerWrap = document.getElementById('llm2ProviderTableWrap');
  const routeWrap = document.getElementById('llm2RouteTableWrap');
  if (providerWrap) {
    providerWrap.innerHTML = '<div class="loading"><div class="spinner"></div><div style="margin-top:8px">加载中...</div></div>';
  }
  if (routeWrap) {
    routeWrap.innerHTML = '<div class="loading"><div class="spinner"></div><div style="margin-top:8px">加载中...</div></div>';
  }
  try {
    currentExecutionRegion = document.getElementById('llm2-route-execution-region')?.value || currentExecutionRegion || 'cn_shanghai';
    const [providers, routes, regionTargets] = await Promise.all([
      api('/llm/providers'),
      api('/llm/routes?executionRegion=' + encodeURIComponent(currentExecutionRegion)),
      api('/cloud/ai-engine-region-targets?providerSelectableOnly=true'),
    ]);
    llmProviders = providers || [];
    llmRoutes = routes || [];
    llmRegionTargets = regionTargets || [];
    updateCurrentRegionLabel();
    renderProviderRegionTargetOptions();
    renderProviderTable();
    renderRouteTable();
    if (currentProviderId) {
      const provider = llmProviders.find(item => item.id === currentProviderId);
      if (provider) {
        populateProviderForm(provider);
      }
    } else {
      resetProviderForm();
    }
    if (currentTestProviderId) {
      currentTestProvider = llmProviders.find(item => item.id === currentTestProviderId) || null;
      renderProviderTestHeader();
    }
  } catch (e) {
    if (providerWrap) {
      providerWrap.innerHTML = `<div class="loading" style="color:#dc2626">加载失败: ${escHtml(e.message)}</div>`;
    }
    if (routeWrap) {
      routeWrap.innerHTML = `<div class="loading" style="color:#dc2626">加载失败: ${escHtml(e.message)}</div>`;
    }
    toast('加载 LLM 网关失败: ' + e.message, 'error');
  }
}

function updateCurrentRegionLabel() {
  const label = document.getElementById('llm2CurrentRegionLabel');
  if (label) {
    label.textContent = currentExecutionRegion || 'cn_shanghai';
  }
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

function renderProviderSummary() {
  const wrap = document.getElementById('llm2ProviderSummary');
  if (!wrap) return;
  const enabledCount = llmProviders.filter(item => item.enabled !== false).length;
  const disabledCount = llmProviders.length - enabledCount;
  const failedCount = llmProviders.filter(item => item.latestTest && item.latestTest.success === false).length;
  wrap.innerHTML = [
    `<span class="llm-pill">Provider ${llmProviders.length}</span>`,
    `<span class="llm-pill">启用 ${enabledCount}</span>`,
    `<span class="llm-pill">停用 ${disabledCount}</span>`,
    `<span class="llm-pill">最近失败 ${failedCount}</span>`,
  ].join('');
}

function renderProviderTable() {
  const wrap = document.getElementById('llm2ProviderTableWrap');
  if (!wrap) return;
  renderProviderSummary();
  if (!llmProviders.length) {
    wrap.innerHTML = '<div class="loading">暂无 Provider</div>';
    return;
  }
  let html = '<table><thead><tr><th>名称</th><th>类型 / 预设</th><th>Region</th><th>模型</th><th>超时</th><th>优先级</th><th>最近测试</th><th>状态</th><th>操作</th></tr></thead><tbody>';
  for (const provider of llmProviders) {
    const contextWindowLabel = provider.contextWindow ? String(provider.contextWindow) : '-';
    const maxTokensLabel = provider.maxTokens ? String(provider.maxTokens) : '-';
    const latestTest = provider.latestTest;
    const latestTestHtml = latestTest
      ? `<div class="${latestTest.success ? 'llm-badge-ok' : 'llm-badge-err'}">${latestTest.success ? '成功' : '失败'}</div><div class="llm-row-sub" style="margin-top:8px">${escHtml(summarizeLatestTest(latestTest))}</div><div class="llm-row-sub">${escHtml(fmtDateTime(latestTest.testedAt))}</div>`
      : '<span class="llm-row-sub">暂无测试</span>';
    html += `<tr>
      <td><div class="llm-row-title">${escHtml(provider.name)}</div><div class="llm-row-sub">${escHtml(provider.apiKeyMasked || '已设置密钥')}</div></td>
      <td><div><span class="llm-table-badge">${escHtml(provider.providerType)}</span></div><div class="llm-row-sub">${escHtml(provider.vendorPreset || 'generic')}</div></td>
      <td><div class="llm-row-title">${escHtml(provider.regionDisplayName || provider.region)}</div><div class="llm-row-sub">${escHtml(provider.cloudRegionCode || '')}</div></td>
      <td><div class="llm-row-title">${escHtml(provider.model || '-')}</div>${provider.fastModel ? `<div class="llm-row-sub">fast · ${escHtml(provider.fastModel)}</div>` : '<div class="llm-row-sub">无快模型</div>'}<div class="llm-row-sub">context ${escHtml(contextWindowLabel)}</div><div class="llm-row-sub">max_tokens ${escHtml(maxTokensLabel)}</div></td>
      <td><div class="llm-row-title">${provider.requestTimeoutS}s</div><div class="llm-row-sub">connect ${provider.connectTimeoutS}s</div></td>
      <td><span class="llm-table-badge llm-table-badge-muted">P${escHtml(String(provider.priority))}</span></td>
      <td>${latestTestHtml}</td>
      <td><span class="llm-table-status ${provider.enabled ? 'ok' : 'off'}">${provider.enabled ? '启用' : '停用'}</span></td>
      <td>
        <button class="abtn abtn-edit" onclick="openProviderSheet('${provider.id}')">编辑</button>
        <button class="abtn abtn-preview" onclick="openProviderTestSheet('${provider.id}')">测试台</button>
        <button class="abtn abtn-delete" onclick="deleteProvider('${provider.id}')">删除</button>
      </td>
    </tr>`;
  }
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

function llmJourneyLabel(journey) {
  const labels = {
    creation_session: '创建会话访谈',
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
    return { label: 'Provider 池回退', tone: 'pool' };
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

function readMultiSelectValues(selectId) {
  const select = document.getElementById(selectId);
  if (!select) return [];
  return Array.from(select.selectedOptions || [])
    .map(option => option.value || '')
    .map(value => value.trim())
    .filter(Boolean);
}

function renderRouteTopSummary() {
  const wrap = document.getElementById('llm2RouteTopSummary');
  if (!wrap) return;
  const requiredRoutes = llmRoutes.filter(route => route.bindingRequired);
  const configuredCount = llmRoutes.filter(route => route.routeBindingState === 'configured').length;
  const fallbackActiveCount = llmRoutes.filter(route => route.routeBindingState === 'fallback_active').length;
  const inheritedCount = llmRoutes.filter(route => route.routeBindingState === 'inherited').length;
  const providerPoolCount = llmRoutes.filter(route => route.routeBindingState === 'provider_pool_fallback').length;
  const invalidCount = llmRoutes.filter(route => route.routeBindingState === 'invalid_provider').length;
  const disabledCount = llmRoutes.filter(route => route.exactRouteEnabled === false).length;
  const blockingMissingCount = requiredRoutes.filter(route => (
    ['missing', 'invalid_provider', 'provider_pool_fallback'].includes(route.routeBindingState)
  )).length;
  const optionalMissingCount = llmRoutes.filter(route => !route.bindingRequired && route.routeBindingState === 'missing').length;
  wrap.innerHTML = [
    `<span class="llm-pill">当前区域：<strong id="llm2CurrentRegionLabel" style="color:#0f172a">${escHtml(currentExecutionRegion || 'cn_shanghai')}</strong></span>`,
    `<span class="llm-pill">步骤 ${escHtml(String(llmRoutes.length))}</span>`,
    `<span class="llm-pill">精确绑定 ${escHtml(String(configuredCount))}</span>`,
    `<span class="llm-pill">显式回退 ${escHtml(String(fallbackActiveCount))}</span>`,
    `<span class="llm-pill">继承 ${escHtml(String(inheritedCount))}</span>`,
    `<span class="llm-pill">Provider 池 ${escHtml(String(providerPoolCount))}</span>`,
    `<span class="llm-pill">失效绑定 ${escHtml(String(invalidCount))}</span>`,
    `<span class="llm-pill">阻塞缺口 ${escHtml(String(blockingMissingCount))}</span>`,
    `<span class="llm-pill">可选未配 ${escHtml(String(optionalMissingCount))}</span>`,
    `<span class="llm-pill">精确路由已禁用 ${escHtml(String(disabledCount))}</span>`,
    '<span class="llm-pill">复刻 = 复制 bundle，不直接调用 LLM</span>',
  ].join('');
}

function renderRouteTable() {
  const wrap = document.getElementById('llm2RouteTableWrap');
  if (!wrap) return;
  renderRouteTopSummary();
  if (!llmRoutes.length) {
    wrap.innerHTML = '<div class="loading">暂无步骤绑定</div>';
    return;
  }
  const regionProviders = llmProviders.filter(provider => provider.region === currentExecutionRegion && provider.enabled !== false);
  const groups = [];
  for (const route of llmRoutes) {
    const stageLabel = route.stageLabel || '未分组';
    const lastGroup = groups[groups.length - 1];
    if (!lastGroup || lastGroup.stageLabel !== stageLabel) {
      groups.push({ stageLabel, routes: [route] });
    } else {
      lastGroup.routes.push(route);
    }
  }
  let html = '<table><thead><tr><th>流程步骤</th><th>真实用途</th><th>覆盖流程</th><th>Provider 绑定</th><th>当前模型</th><th>路由状态</th><th>启用</th><th>操作</th></tr></thead><tbody>';

  for (const group of groups) {
    const firstRoute = group.routes[0];
    const journeyNames = Array.from(new Set(group.routes.flatMap(route => Array.isArray(route.journeys) ? route.journeys : [])))
      .map(llmJourneyLabel)
      .join(' / ');
    const stageConfigured = group.routes.filter(route => route.routeBindingState === 'configured').length;
    const stageFallbackActive = group.routes.filter(route => route.routeBindingState === 'fallback_active').length;
    const stageInherited = group.routes.filter(route => route.routeBindingState === 'inherited').length;
    const stageBlockingMissing = group.routes.filter(route => route.bindingRequired && ['missing', 'invalid_provider', 'provider_pool_fallback'].includes(route.routeBindingState)).length;
    html += `<tr class="llm-stage-row"><td colspan="8">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
        <div>
          <div style="font-weight:600">${escHtml(firstRoute.stageLabel || '未分组')}</div>
          <div class="llm-row-sub" style="margin-top:4px">${escHtml(firstRoute.flowSummary || '')}</div>
          <div class="llm-row-sub">${escHtml(journeyNames || firstRoute.journeySummary || '-')}</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <span class="llm-table-badge llm-table-badge-muted">精确绑定 ${escHtml(String(stageConfigured))}</span>
          <span class="llm-table-badge llm-table-badge-muted">显式回退 ${escHtml(String(stageFallbackActive))}</span>
          <span class="llm-table-badge llm-table-badge-muted">继承 ${escHtml(String(stageInherited))}</span>
          <span class="llm-table-badge llm-table-badge-muted">阻塞缺口 ${escHtml(String(stageBlockingMissing))}</span>
        </div>
      </div>
    </td></tr>`;

    for (const route of group.routes) {
      const domKey = route.stepKey.replace(/[^a-zA-Z0-9_-]/g, '-');
      const stepTitle = route.displayName ? `${route.displayName} · ${route.stepKey}` : route.stepKey;
      const stepHint = route.triggerSummary || route.description || '';
      const providerOptions = [];
      const fallbackOptions = [];
      const placeholderLabel = route.providerId
        ? '选择新的精确绑定 Provider'
        : (route.routeMatchStrategy === 'parent_step'
          ? `跟随父级步骤 ${route.matchedStepKey || '-'}`
          : route.routeMatchStrategy === 'provider_pool'
            ? '当前由 Provider 池兜底'
            : '请选择精确绑定 Provider');
      providerOptions.push(`<option value="" ${route.providerId ? '' : 'selected'}>${escHtml(placeholderLabel)}</option>`);
      if (route.providerId && !regionProviders.some(provider => provider.id === route.providerId)) {
        providerOptions.push(
          `<option value="${escAttr(route.providerId)}" selected>[当前绑定但不可用] ${escHtml(route.providerDisplayName || route.providerId)}</option>`,
        );
      }
      providerOptions.push(...regionProviders.map(provider => (
        `<option value="${escAttr(provider.id)}" ${provider.id === route.providerId ? 'selected' : ''}>${escHtml(provider.name)} · ${escHtml(provider.model)}${provider.fastModel ? ' / fast ' + escHtml(provider.fastModel) : ''}</option>`
      )));
      const fallbackProviderIds = Array.isArray(route.fallbackProviderIds) ? route.fallbackProviderIds : [];
      if (fallbackProviderIds.length) {
        fallbackProviderIds.forEach(fallbackId => {
          if (!regionProviders.some(provider => provider.id === fallbackId)) {
            const fallbackLabel = Array.isArray(route.fallbackProviders)
              ? route.fallbackProviders.find(provider => provider.id === fallbackId)?.name
              : null;
            fallbackOptions.push(
              `<option value="${escAttr(fallbackId)}" selected>[当前 fallback 但不可用] ${escHtml(fallbackLabel || fallbackId)}</option>`,
            );
          }
        });
      }
      fallbackOptions.push(...regionProviders.map(provider => (
        `<option value="${escAttr(provider.id)}" ${fallbackProviderIds.includes(provider.id) ? 'selected' : ''}>${escHtml(provider.name)} · ${escHtml(provider.model)}${provider.fastModel ? ' / fast ' + escHtml(provider.fastModel) : ''}</option>`
      )));
      html += `<tr>
        <td>
          <div class="llm-row-title">${escHtml(stepTitle)}</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
            ${route.optional ? '<span class="llm-table-badge llm-table-badge-muted">可选</span>' : '<span class="llm-table-badge">必配</span>'}
          </div>
        </td>
        <td>
          <div class="llm-row-title">${escHtml(route.flowSummary || '-')}</div>
          ${stepHint ? `<div class="llm-row-sub">${escHtml(stepHint)}</div>` : ''}
          ${route.bindingNote ? `<div class="llm-row-sub" style="margin-top:6px;color:#92400e">${escHtml(route.bindingNote)}</div>` : ''}
        </td>
        <td>
          <div style="display:flex;gap:6px;flex-wrap:wrap">${renderRouteJourneyBadges(route)}</div>
          <div class="llm-row-sub" style="margin-top:6px">${escHtml(route.journeySummary || '-')}</div>
        </td>
        <td>
          <select id="llm-route-binding-provider-${escAttr(domKey)}">
            ${providerOptions.join('') || '<option value="">暂无可用 Provider</option>'}
          </select>
          <div class="llm-row-sub" style="margin-top:6px">显式 Fallback（按顺序生效，可多选）</div>
          <select id="llm-route-binding-fallback-${escAttr(domKey)}" multiple size="${Math.max(2, Math.min(4, regionProviders.length || 2))}" style="margin-top:6px">
            ${fallbackOptions.join('') || '<option value="">暂无可用 Fallback Provider</option>'}
          </select>
          <div class="llm-row-sub" style="margin-top:6px">Region: ${escHtml(route.executionRegion || currentExecutionRegion || '-')}</div>
          <div class="llm-row-sub">当前 fallback: ${escHtml(route.fallbackProviderSummary || '无')}</div>
          <div class="llm-row-sub">解析来源: ${escHtml(route.routeMatchStrategy || 'none')}${route.matchedStepKey ? ` · ${escHtml(route.matchedStepKey)}` : ''}</div>
        </td>
        <td>
          <div class="llm-row-title">${escHtml(route.effectiveProviderDisplayName || route.providerDisplayName || '-')}</div>
          <div class="llm-row-sub">${escHtml(route.modelDefault || '-')}</div>
          ${route.modelFast ? `<div class="llm-row-sub">fast · ${escHtml(route.modelFast)}</div>` : '<div class="llm-row-sub">无 fast override</div>'}
        </td>
        <td>${renderRouteStateBadge(route)}</td>
        <td><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="llm-route-binding-enabled-${escAttr(domKey)}" ${route.enabled ? 'checked' : ''}> 启用</label></td>
        <td><button class="abtn abtn-preview" onclick="saveRouteBinding('${escAttr(route.stepKey)}')">保存</button></td>
      </tr>`;
    }
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
  const fallbackProviderIds = readMultiSelectValues(`llm-route-binding-fallback-${domKey}`)
    .filter(value => value && value !== providerId);
  const enabled = document.getElementById(`llm-route-binding-enabled-${domKey}`)?.checked !== false;
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
  document.getElementById('llm2ProviderSheetDesc').textContent = '支持供应商预设、模型目录自动/自定义配置，以及下拉方式选择模型。';
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
  document.getElementById('llm2-provider-catalog-mode').value = 'auto';
  document.getElementById('llm2-provider-catalog-auth-mode').value = 'inherit_provider';
  document.getElementById('llm2-provider-catalog-api-url').value = '';
  document.getElementById('llm2-provider-catalog-api-key').value = '';
  document.getElementById('llm2-provider-api-key-hint').textContent = '新建 Provider 时必须输入 API Key。';
  document.getElementById('llm2-provider-catalog-api-key-hint').textContent = '默认继承 Provider API Key。';
  document.getElementById('llm2ProviderCatalogStatus').textContent = '尚未加载模型目录。';
  toggleCatalogModeFields();
}

function populateProviderForm(provider) {
  if (!provider) return;
  currentProviderId = provider.id;
  llmProviderCatalogOptions = [];
  renderProviderCatalogOptions();
  document.getElementById('llm2ProviderSheetTitle').textContent = '编辑 Provider';
  document.getElementById('llm2ProviderSheetDesc').textContent = '编辑基础配置、模型目录来源和默认模型，保存后会刷新网关缓存。';
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
  document.getElementById('subscriptionPlanSheetDesc').textContent = '配置价格、额度、推荐标签和启用状态。历史上已经成交的套餐删除时会自动归档。';
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
    document.getElementById('subscriptionPlanSheetDesc').textContent = `正在编辑 ${plan.name}，保存后会立即作为用户侧套餐配置生效。`;
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
  setProviderTestStatusBanner(
    'info',
    '测试台已就绪',
    '你可以先运行一键连通测试，或直接发起多轮对话来观察回复质量与延时。',
  );
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
    setProviderTestStatusBanner('loading', '正在进行连通测试', '会按当前 Region 的可用 ai-engine 节点顺序尝试。');
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
    wrap.innerHTML = '<div class="llm-empty">从一条消息开始，观察回复内容和每轮延时。</div>';
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
    setProviderTestStatusBanner('loading', '正在请求 Provider 回复', '这次会带上当前会话上下文，并记录本轮延时。');
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

