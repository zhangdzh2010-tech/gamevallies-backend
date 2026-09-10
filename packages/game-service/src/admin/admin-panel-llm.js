// Three public concepts only: connections, models, business stages.
let llmConfigTab = 'providers';
let llmConfigModels = [];
let llmConfigStages = [];
let llmConfigLoadId = 0;
let llmConfigDraft = null;
let llmBusinessDirty = false;
let llmBusinessSaving = false;
const llmModelTestsPending = new Set();
const llmConfigApi = (path, options = {}) => api(path, { ...options, retry: false });

async function loadLlmGateway() {
  const loadId = ++llmConfigLoadId;
  const region = currentExecutionRegion;
  const root = document.getElementById('llm3App');
  try {
    const [providers, models, stages, regions] = await Promise.all([
      llmConfigApi('/llm/providers'), llmConfigApi('/llm/models'),
      llmConfigApi('/llm/business-bindings?region=' + encodeURIComponent(region)),
      llmConfigApi('/cloud/ai-engine-region-targets?providerSelectableOnly=true'),
    ]);
    if (loadId !== llmConfigLoadId) return;
    llmProviders = providers; llmConfigModels = models; llmConfigStages = stages; llmRegionTargets = regions;
    llmBusinessDirty = false;
    renderLlmConfig();
  } catch (error) {
    if (loadId !== llmConfigLoadId) return;
    root.innerHTML = `<div class="llm-surface llm-config-empty">模型设置加载失败：${escHtml(error.message)}<p>新版本需先完成模型配置表迁移。未加载成功时不会覆盖旧路由。</p><button class="btn btn-secondary" onclick="loadLlmGateway()">重试加载</button></div>`;
  }
}

async function switchLlmConfigTab(tab) {
  if (llmBusinessDirty && !await confirmDialog('离开当前配置？', '业务环节尚未保存，离开将放弃这些选择。')) return;
  llmBusinessDirty = false; llmConfigTab = tab; renderLlmConfig();
}

async function changeLlmConfigRegion(value) {
  if (llmBusinessDirty && !await confirmDialog('切换区域？', '当前业务选择尚未保存，切换将放弃这些选择。')) {
    document.getElementById('llm3Region').value = currentExecutionRegion; return;
  }
  currentExecutionRegion = value; await loadLlmGateway();
}

function renderLlmConfig() {
  const regionValues = [...new Set([currentExecutionRegion, ...llmRegionTargets.map(row => row.executionRegion || row.region)])].filter(Boolean);
  document.getElementById('llm3App').innerHTML = `
    <div class="llm-hero"><div><h2>LLM 网关</h2><p class="sheet-muted">连接服务 → 配置并测试模型 → 分配业务环节</p></div>
      <div class="llm-tools"><label for="llm3Region">区域</label><select id="llm3Region" onchange="changeLlmConfigRegion(this.value)">${regionValues.map(region => `<option ${region === currentExecutionRegion ? 'selected' : ''}>${escHtml(region)}</option>`).join('')}</select></div></div>
    <div class="llm-config-tabs" role="tablist" aria-label="模型配置模块">${[['providers', '1. Provider 配置'], ['models', '2. Model 配置与测试'], ['business', '3. 业务环节配置']].map(([id, label]) => `<button role="tab" aria-selected="${llmConfigTab === id}" class="${llmConfigTab === id ? 'active' : ''}" onclick="switchLlmConfigTab('${id}')">${label}</button>`).join('')}</div>
    <section class="llm-surface llm-config-content" role="tabpanel">${llmConfigTab === 'providers' ? renderLlmConnections() : llmConfigTab === 'models' ? renderLlmModels() : renderLlmBusiness()}</section>`;
}

function renderLlmConnections() {
  const providers = llmProviders.filter(provider => provider.region === currentExecutionRegion);
  return `<div class="llm-panel-header"><div><h3>Provider 配置</h3><p class="sheet-muted">只管理 API 连接，不选择模型。</p></div><button class="btn btn-primary" onclick="openLlmConnection()">添加 Provider</button></div>
    ${providers.length ? `<div class="llm-config-cards">${providers.map(provider => `<article class="llm-config-card"><div class="llm-inline-head"><h4>${escHtml(provider.name)}</h4><span class="llm-table-status ${provider.enabled ? 'ok' : 'off'}">${provider.enabled ? '启用' : '停用'}</span></div><p class="sheet-muted">${escHtml(provider.providerType)}</p><p class="llm-endpoint">${escHtml(provider.baseUrl)}</p><p class="sheet-muted">${llmConfigModels.filter(model => model.providerId === provider.id).length} 个模型 · Key ${provider.apiKeySet ? '已配置' : '未配置'}</p><button class="btn btn-secondary btn-sm" onclick="openLlmConnection('${escAttr(provider.id)}')">编辑连接</button></article>`).join('')}</div>` : '<div class="llm-config-empty">先添加一个 Provider，再到 Model 配置中添加模型。</div>'}`;
}

function llmModelLabel(model) {
  return model ? `${model.name} · ${model.modelId} / ${model.provider?.name || '服务不可用'}` : '模型不存在';
}

function llmModelTestMarkup(model) {
  if (llmModelTestsPending.has(model.id)) return '<span>正在测试此模型…</span>';
  const test = model.latestTest;
  if (!test) return '<span class="sheet-muted">尚未测试</span>';
  const stale = test.stale || test.modelConfigVersion !== model.configurationVersion ||
    test.providerUpdatedAt !== model.provider?.updatedAt;
  const label = stale ? '旧配置结果，请重新测试' : test.success ? '连通测试成功' : providerTestFailureLabel(test);
  const diagnostic = { model: test.model, testedAt: test.testedAt, latencyMs: test.latencyMs,
    httpStatus: test.httpStatus, errorCode: test.errorCode, errorMessage: test.errorMessage,
    modelConfigVersion: test.modelConfigVersion, transportEvidence: test.transportEvidence };
  return `<div class="${stale ? 'sheet-muted' : test.success ? 'llm-test-ok' : 'llm-test-error'}">${escHtml(label)}</div><div class="sheet-muted">${escHtml(String(test.latencyMs ?? '-'))} ms · ${escHtml(test.testedAt || '')}</div><details><summary>诊断记录</summary><pre class="llm-test-diagnostics">${escHtml(JSON.stringify(diagnostic, null, 2))}</pre></details>`;
}

function llmReadinessMarkup(model) {
  const stages = model.businessReadiness || [];
  const legacy = model.capabilityState?.legacyUnchecked;
  const rows = stages.map(stage => `${stage.name}：${stage.ready ? '可路由' : '受限'}`);
  const reasons = [...new Set(stages.flatMap(stage => stage.steps.filter(step => !step.ready)
    .flatMap(step => step.candidates.flatMap(candidate => candidate.reasons))))];
  return `<div class="sheet-muted">${escHtml(rows.join(' · '))}</div>
    ${legacy ? '<p class="sheet-muted">旧表单未验证标记按未知能力处理，实际能力以生成验收为准。</p>' : ''}
    ${reasons.length ? `<p class="llm-test-error">${escHtml(reasons.join('；'))}</p>` : ''}`;
}

function renderLlmModels() {
  const models = llmConfigModels.filter(model => model.provider?.region === currentExecutionRegion);
  return `<div class="llm-panel-header"><div><h3>Model 配置与测试</h3><p class="sheet-muted">每个模型独立配置、独立测试；连通成功不等于作品生成通过。</p></div><button class="btn btn-primary" onclick="openLlmModel()">添加 Model</button></div>
    ${models.length ? `<div class="llm-config-cards">${models.map(model => `<article class="llm-config-card"><h4>${escHtml(model.name)}</h4><p class="sheet-muted">${escHtml(model.modelId)} · ${escHtml(model.provider.name)}</p><p class="sheet-muted">${model.enabled && model.provider.enabled ? '已启用' : '模型或服务已停用'}</p>${llmReadinessMarkup(model)}<div id="llm3Test-${escAttr(model.id)}">${llmModelTestMarkup(model)}</div><div class="llm-simple-actions"><button class="btn btn-secondary btn-sm" onclick="openLlmModel('${escAttr(model.id)}')">编辑模型</button><button id="llm3TestButton-${escAttr(model.id)}" class="btn btn-primary btn-sm" onclick="testLlmModel('${escAttr(model.id)}')" ${llmModelTestsPending.has(model.id) || !model.enabled || !model.provider.enabled ? 'disabled' : ''}>测试此模型</button></div></article>`).join('')}</div>` : '<div class="llm-config-empty">添加模型时选择已有 Provider，填写服务商提供的模型 ID。</div>'}`;
}

function businessModelOptions(selected, optional = false) {
  const models = llmConfigModels.filter(model => model.enabled && model.provider?.enabled && model.provider.region === currentExecutionRegion);
  return `<option value="">${optional ? '不使用备用模型' : '请选择主模型'}</option>${selected && !models.some(model => model.id === selected) ? `<option value="${escAttr(selected)}" selected>当前模型不可用，请更换</option>` : ''}${models.map(model => `<option value="${escAttr(model.id)}" ${model.id === selected ? 'selected' : ''}>${escHtml(llmModelLabel(model))}</option>`).join('')}`;
}

function renderLlmBusiness() {
  return `<div class="llm-panel-header"><div><h3>业务环节配置</h3><p class="sheet-muted">只配置三个业务环节，内部步骤自动归属。主备可来自同一个 Provider。</p></div></div>
    <div class="llm-business-cards">${llmConfigStages.map(stage => `<article class="llm-config-card"><h4>${escHtml(stage.name)}</h4><p class="sheet-muted">${escHtml(stage.description)}</p><div class="llm-business-state">${stage.status === 'configured' ? stage.readiness?.ready === false ? '业务配置被阻断' : '使用业务配置 · 可路由' : stage.status === 'legacy' ? '保留旧路由，尚未接管' : '尚未配置'}</div>
      ${stage.readiness?.ready === false ? `<p class="llm-test-error">${escHtml([...new Set(stage.readiness.steps.filter(step => !step.ready).flatMap(step => step.candidates.flatMap(candidate => candidate.reasons)))].join('；'))}</p>` : ''}
      <label>主模型<select id="llm3Primary-${escAttr(stage.id)}" onchange="llmBusinessDirty=true">${businessModelOptions(stage.binding?.primaryModelId)}</select></label>
      <label>备用模型 <span class="sheet-muted">可选</span><select id="llm3Fallback-${escAttr(stage.id)}" onchange="llmBusinessDirty=true">${businessModelOptions(stage.binding?.fallbackModelId, true)}</select></label>
      ${stage.legacy?.length ? `<details><summary>旧配置（只读）</summary><ul>${stage.legacy.map(row => `<li>${escHtml(row.stepKey)}：${escHtml(row.model || '未绑定')}</li>`).join('')}</ul></details>` : ''}</article>`).join('')}</div>
    <div class="llm-config-footer"><button id="llm3BusinessSave" class="btn btn-primary" onclick="saveLlmBusiness()" ${llmBusinessSaving ? 'disabled' : ''}>保存业务配置</button><span class="sheet-muted">三个环节一起保存；不会修改其他区域。</span></div>`;
}

function llmInput(id, label, value = '', type = 'text') {
  return `<label class="llm-field">${label}<input id="${id}" type="${type}" value="${escAttr(String(value ?? ''))}" /></label>`;
}

function openLlmConnection(id) {
  const provider = llmProviders.find(item => item.id === id);
  llmConfigDraft = { kind: 'provider', id, source: provider, saving: false };
  const regionTargetId = provider?.regionTargetId || llmRegionTargets.find(item => (item.executionRegion || item.region) === currentExecutionRegion)?.id || '';
  document.getElementById('llm3SheetTitle').textContent = provider ? '编辑 Provider' : '添加 Provider';
  document.getElementById('llm3Form').innerHTML = `<div class="sheet-grid">
    ${llmInput('llm3Name', '名称', provider?.name)}${llmInput('llm3Url', 'API 地址', provider?.baseUrl)}
    ${llmInput('llm3Key', 'API Key（编辑时留空保留）', '', 'password')}
    <label class="llm-field">接口协议<select id="llm3Protocol"><option value="openai_compatible">OpenAI 兼容</option><option value="anthropic">Anthropic</option></select></label>
    <label class="llm-toggle-line"><input id="llm3Enabled" type="checkbox" ${provider?.enabled !== false ? 'checked' : ''} />启用连接</label></div>
    <details class="llm-advanced-block"><summary>连接选项</summary><label>部署区域<select id="llm3Target">${llmRegionTargets.map(target => `<option value="${escAttr(target.id)}" ${target.id === regionTargetId ? 'selected' : ''}>${escHtml(target.displayName || target.executionRegion)}</option>`).join('')}</select></label>${llmInput('llm3Timeout', '请求超时（秒）', provider?.requestTimeoutS || 600, 'number')}${llmInput('llm3ConnectTimeout', '连接超时（秒）', provider?.connectTimeoutS || 15, 'number')}</details>`;
  document.getElementById('llm3Protocol').value = provider?.providerType || 'openai_compatible';
  document.getElementById('llm3Sheet').classList.add('active');
}

function openLlmModel(id) {
  const model = llmConfigModels.find(item => item.id === id);
  llmConfigDraft = { kind: 'model', id, source: model, saving: false };
  const providers = llmProviders.filter(provider => provider.region === currentExecutionRegion);
  document.getElementById('llm3SheetTitle').textContent = model ? '编辑 Model' : '添加 Model';
  document.getElementById('llm3Form').innerHTML = `<div class="sheet-grid"><label class="llm-field">所属 Provider<select id="llm3Provider" ${model ? 'disabled' : ''}><option value="">请选择服务</option>${providers.map(provider => `<option value="${escAttr(provider.id)}" ${model?.providerId === provider.id ? 'selected' : ''}>${escHtml(provider.name)}</option>`).join('')}</select></label>
    ${llmInput('llm3ModelId', '模型 ID', model?.modelId)}${llmInput('llm3Name', '显示名称（可选）', model?.name)}
    <label class="llm-toggle-line"><input id="llm3Enabled" type="checkbox" ${model?.enabled !== false ? 'checked' : ''} />启用模型</label></div>
    <details class="llm-advanced-block"><summary>模型限制（可选）</summary><p class="sheet-muted">按服务商实际限制填写；留空表示未知，不会判为“不支持”。</p>${llmInput('llm3Context', '上下文 Token 上限', model?.contextWindow, 'number')}${llmInput('llm3Output', '输出 Token 上限', model?.maxOutputTokens, 'number')}
    ${llmReadinessMarkup(model || {})}
    ${Object.keys(model?.capabilityFlags || {}).length ? `<details><summary>原始能力标记</summary><p class="sheet-muted">${escHtml(JSON.stringify(model.capabilityFlags))}</p></details>` : ''}</details>`;
  document.getElementById('llm3Sheet').classList.add('active');
}

function closeLlmConfigSheet() {
  if (llmConfigDraft?.saving) return;
  document.getElementById('llm3Sheet').classList.remove('active'); llmConfigDraft = null;
}

async function saveLlmConfigSheet() {
  const draft = llmConfigDraft;
  if (!draft || draft.saving) return;
  const value = id => document.getElementById(id).value.trim();
  draft.saving = true;
  document.getElementById('llm3FormSave').disabled = true;
  try {
    let url, body;
    if (draft.kind === 'provider') {
      url = '/llm/providers';
      body = { connectionOnly: true, name: value('llm3Name'), baseUrl: value('llm3Url'), apiKey: value('llm3Key'),
        providerType: value('llm3Protocol'), regionTargetId: value('llm3Target'), enabled: document.getElementById('llm3Enabled').checked,
        requestTimeoutS: Number(value('llm3Timeout')), connectTimeoutS: Number(value('llm3ConnectTimeout')) };
      if (!body.name || !body.baseUrl || (!draft.id && !body.apiKey)) throw new Error('请填写名称、API 地址和 Key');
    } else {
      url = '/llm/models';
      body = { providerId: value('llm3Provider'), modelId: value('llm3ModelId'), name: value('llm3Name'),
        enabled: document.getElementById('llm3Enabled').checked, configurationVersion: draft.source?.configurationVersion,
        contextWindow: value('llm3Context') ? Number(value('llm3Context')) : null,
        maxOutputTokens: value('llm3Output') ? Number(value('llm3Output')) : null };
      if (!body.providerId || !body.modelId) throw new Error('请选择 Provider 并填写模型 ID');
    }
    const result = await llmConfigApi(url + (draft.id ? '/' + draft.id : ''), { method: draft.id ? 'PUT' : 'POST', body });
    draft.saving = false; closeLlmConfigSheet();
    toast(result.refreshWarning ? '已保存，网关刷新待重试' : '已保存', result.refreshWarning ? 'error' : 'success');
    await loadLlmGateway();
  } catch (error) { toast(error.message, 'error'); }
  finally { draft.saving = false; document.getElementById('llm3FormSave').disabled = false; }
}

async function testLlmModel(id) {
  if (llmModelTestsPending.has(id)) return;
  const model = llmConfigModels.find(item => item.id === id);
  if (!model?.enabled || !model.provider?.enabled) return;
  llmModelTestsPending.add(id);
  const update = () => {
    const result = document.getElementById('llm3Test-' + id);
    if (result) result.innerHTML = llmModelTestMarkup(llmConfigModels.find(item => item.id === id) || model);
    const button = document.getElementById('llm3TestButton-' + id);
    if (button) button.disabled = llmModelTestsPending.has(id);
  };
  update();
  try {
    const result = await llmConfigApi('/llm/models/' + id + '/test', { method: 'POST' });
    const current = llmConfigModels.find(item => item.id === id);
    if (current?.configurationVersion === model.configurationVersion && current.provider?.updatedAt === model.provider?.updatedAt) current.latestTest = result;
  } catch (error) { toast('测试结果未确认，请查看日志后再重试：' + error.message, 'error'); }
  finally { llmModelTestsPending.delete(id); update(); }
}

async function saveLlmBusiness() {
  if (llmBusinessSaving) return;
  const region = currentExecutionRegion;
  const bindings = llmConfigStages.map(stage => ({ stage: stage.id,
    primaryModelId: document.getElementById('llm3Primary-' + stage.id).value,
    fallbackModelId: document.getElementById('llm3Fallback-' + stage.id).value || null,
    revision: stage.binding?.revision ?? null }));
  if (bindings.some(row => !row.primaryModelId || row.primaryModelId === row.fallbackModelId)) return toast('每个环节请选择主模型，主备模型不能相同', 'error');
  llmBusinessSaving = true;
  document.getElementById('llm3BusinessSave').disabled = true;
  try {
    if (llmConfigStages.some(stage => stage.status === 'legacy') && !await confirmDialog('接管旧业务路由？', '三个业务环节将使用所选模型。旧逐步骤路由保留备查，不再覆盖这些环节。')) return;
    if (region !== currentExecutionRegion) throw new Error('区域已变化，请重新选择');
    const result = await llmConfigApi('/llm/business-bindings', { method: 'PUT', body: { region, bindings } });
    toast(result.refreshWarning ? '已保存，网关刷新待重试' : '业务配置已保存', result.refreshWarning ? 'error' : 'success');
    llmBusinessDirty = false; await loadLlmGateway();
  } catch (error) { toast('保存未完成或结果待确认：' + error.message, 'error'); }
  finally { llmBusinessSaving = false; const button = document.getElementById('llm3BusinessSave'); if (button) button.disabled = false; }
}
