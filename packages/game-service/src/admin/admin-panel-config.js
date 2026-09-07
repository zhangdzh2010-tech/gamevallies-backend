// ===================== System Config (Prompts) =====================
let promptPipeline = { steps: [], extras: [], summary: null };
let modalCurrent = null;


function promptSections() {
  const steps = Array.isArray(promptPipeline.steps)
    ? promptPipeline.steps.map((section) => ({ ...section, isExtra: false }))
    : [];
  const extras = Array.isArray(promptPipeline.extras)
    ? promptPipeline.extras.map((section) => ({ ...section, isExtra: true }))
    : [];
  return steps.concat(extras);
}

function findPromptItemByKey(key) {
  for (const section of promptSections()) {
    const prompts = Array.isArray(section.prompts) ? section.prompts : [];
    const item = prompts.find((entry) => entry && entry.configKey === key);
    if (item) {
      return { section, item };
    }
  }
  return null;
}

function findBundleSupportItem(kind, id, version) {
  for (const section of Array.isArray(promptPipeline.steps) ? promptPipeline.steps : []) {
    const support = Array.isArray(section.support) ? section.support : [];
    for (const block of support) {
      if (!block || block.kind !== kind || !Array.isArray(block.items)) continue;
      const item = block.items.find((entry) => entry && entry.id === id && Number(entry.version) === Number(version));
      if (item) {
        return { section, block, item };
      }
    }
  }
  return null;
}

function findRuntimeProfileItem(id) {
  for (const section of Array.isArray(promptPipeline.steps) ? promptPipeline.steps : []) {
    const support = Array.isArray(section.support) ? section.support : [];
    for (const block of support) {
      if (!block || block.kind !== 'runtime_profiles' || !Array.isArray(block.items)) continue;
      const item = block.items.find((entry) => entry && entry.id === id);
      if (item) {
        return { section, block, item };
      }
    }
  }
  return null;
}

function promptPreviewText(value, limit = 160) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function renderPromptVariableChips(variables, section) {
  const list = Array.isArray(variables) ? variables.filter(Boolean) : [];
  if (!list.length) return '';
  return list.map((variable) => `
    <code style="font-size:10px;color:${section.color};background:${section.bg};border:1px solid ${section.border};padding:1px 6px;border-radius:4px">${escHtml(variable)}</code>
  `).join('');
}

function renderSourcePill(item, section) {
  const source = String(item?.source || 'db').toLowerCase() === 'catalog' ? 'Catalog' : 'DB';
  const isDefault = item?.isDefault
    ? '<span style="font-size:10px;color:#475569;background:#f8fafc;border:1px solid #cbd5e1;padding:1px 6px;border-radius:999px">Default</span>'
    : '';
  return `
    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
      <span style="font-size:10px;color:${section.color};background:${section.bg};border:1px solid ${section.border};padding:1px 6px;border-radius:999px">${source}</span>
      ${isDefault}
    </div>
  `;
}

function renderPromptCard(item, section) {
  const preview = promptPreviewText(item.configValue);
  const note = item.note ? `<div style="font-size:11px;color:#94a3b8;margin-top:6px">${escHtml(item.note)}</div>` : '';
  return `
    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;border-left:3px solid ${section.color}">
      <div style="padding:14px 18px">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
          <div style="flex:1;min-width:0">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <strong style="font-size:13px;color:#1e293b">${escHtml(item.displayName || item.shortKey || item.configKey)}</strong>
              <code style="font-size:11px;color:#475569;background:#f8fafc;border:1px solid #e2e8f0;padding:2px 8px;border-radius:5px">${escHtml(item.shortKey || item.configKey)}</code>
              ${renderPromptVariableChips(item.variables, section)}
            </div>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:6px">
              ${renderSourcePill(item, section)}
              ${item.updatedAt ? `<span style="font-size:11px;color:#94a3b8">Updated ${escHtml(new Date(item.updatedAt).toLocaleString())}</span>` : ''}
            </div>
            <div style="font-size:12px;color:#64748b;margin-top:8px">${escHtml(item.description || '')}</div>
            ${note}
            <div style="font-size:12px;color:#94a3b8;margin-top:8px;line-height:1.6;font-family:monospace">${escHtml(preview || '(empty)')}</div>
          </div>
          <button onclick="openPromptModal('${escAttr(item.configKey)}')" style="flex-shrink:0;padding:6px 14px;background:${section.bg};border:1.5px solid ${section.border};border-radius:7px;font-size:12px;font-weight:600;color:${section.color};cursor:pointer;white-space:nowrap">Edit</button>
        </div>
      </div>
    </div>
  `;
}

function renderRuntimeProfileCard(item, section) {
  const summary = item.contractSummary || {};
  const states = Array.isArray(summary.requiredStates) ? summary.requiredStates.join(', ') : '';
  const modes = Array.isArray(summary.inputModes) ? summary.inputModes.join(', ') : '';
  const gestures = Array.isArray(summary.gestures) ? summary.gestures.join(', ') : '';
  return `
    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 18px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <strong style="font-size:13px;color:#1e293b">${escHtml(item.displayName || item.id)}</strong>
            <code style="font-size:11px;color:#475569;background:#f8fafc;border:1px solid #e2e8f0;padding:2px 8px;border-radius:5px">${escHtml(item.id)}</code>
            <span style="font-size:10px;color:${item.enabled ? '#065f46' : '#92400e'};background:${item.enabled ? '#ecfdf5' : '#fffbeb'};border:1px solid ${item.enabled ? '#a7f3d0' : '#fde68a'};padding:1px 6px;border-radius:999px">${item.enabled ? 'Enabled' : 'Disabled'}</span>
            ${renderSourcePill(item, section)}
          </div>
          <div style="font-size:12px;color:#64748b;margin-top:8px">Skeleton: ${escHtml(item.skeletonVersion || 'v1')}</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px;font-size:11px;color:#64748b">
            ${states ? `<span>States: ${escHtml(states)}</span>` : ''}
            ${modes ? `<span>Inputs: ${escHtml(modes)}</span>` : ''}
            ${gestures ? `<span>Gestures: ${escHtml(gestures)}</span>` : ''}
          </div>
          <div style="font-size:12px;color:#94a3b8;margin-top:8px;line-height:1.6;font-family:monospace">${escHtml(promptPreviewText(item.fewShotPrompt || '(empty)'))}</div>
        </div>
        <button onclick="openRuntimeProfileModal('${escAttr(item.id)}')" style="flex-shrink:0;padding:6px 14px;background:${section.bg};border:1.5px solid ${section.border};border-radius:7px;font-size:12px;font-weight:600;color:${section.color};cursor:pointer;white-space:nowrap">Edit</button>
      </div>
    </div>
  `;
}

function renderPromptBundlePolicyCard(item, section) {
  return `
    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 18px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <strong style="font-size:13px;color:#1e293b">${escHtml(item.id)}</strong>
            <code style="font-size:11px;color:#475569;background:#f8fafc;border:1px solid #e2e8f0;padding:2px 8px;border-radius:5px">v${escHtml(String(item.version))}</code>
            <span style="font-size:10px;color:${String(item.status).toLowerCase() === 'active' ? '#065f46' : '#92400e'};background:${String(item.status).toLowerCase() === 'active' ? '#ecfdf5' : '#fffbeb'};border:1px solid ${String(item.status).toLowerCase() === 'active' ? '#a7f3d0' : '#fde68a'};padding:1px 6px;border-radius:999px">${escHtml(item.status || 'draft')}</span>
            ${renderSourcePill(item, section)}
          </div>
          <div style="font-size:12px;color:#64748b;margin-top:8px">Product policy</div>
          <div style="font-size:12px;color:#94a3b8;margin-top:4px;line-height:1.6;font-family:monospace">${escHtml(promptPreviewText(item.productPolicy || '(empty)'))}</div>
          <div style="font-size:12px;color:#64748b;margin-top:8px">Locked contract override</div>
          <div style="font-size:12px;color:#94a3b8;margin-top:4px;line-height:1.6;font-family:monospace">${escHtml(promptPreviewText(item.lockedContractOverride || '(empty)'))}</div>
        </div>
        <button onclick="openBundlePolicyModal('${escAttr(item.id)}', ${Number(item.version)})" style="flex-shrink:0;padding:6px 14px;background:${section.bg};border:1.5px solid ${section.border};border-radius:7px;font-size:12px;font-weight:600;color:${section.color};cursor:pointer;white-space:nowrap">Edit</button>
      </div>
    </div>
  `;
}

function renderPromptBundleRepairCard(item, section) {
  return `
    <div style="background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 18px">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <strong style="font-size:13px;color:#1e293b">${escHtml(item.id)}</strong>
            <code style="font-size:11px;color:#475569;background:#f8fafc;border:1px solid #e2e8f0;padding:2px 8px;border-radius:5px">v${escHtml(String(item.version))}</code>
            <span style="font-size:10px;color:${String(item.status).toLowerCase() === 'active' ? '#065f46' : '#92400e'};background:${String(item.status).toLowerCase() === 'active' ? '#ecfdf5' : '#fffbeb'};border:1px solid ${String(item.status).toLowerCase() === 'active' ? '#a7f3d0' : '#fde68a'};padding:1px 6px;border-radius:999px">${escHtml(item.status || 'draft')}</span>
            ${renderSourcePill(item, section)}
          </div>
          <div style="font-size:12px;color:#64748b;margin-top:8px">Repair playbook</div>
          <div style="font-size:12px;color:#94a3b8;margin-top:4px;line-height:1.6;font-family:monospace">${escHtml(promptPreviewText(item.repairPlaybook || '(empty)'))}</div>
        </div>
        <button onclick="openBundleRepairModal('${escAttr(item.id)}', ${Number(item.version)})" style="flex-shrink:0;padding:6px 14px;background:${section.bg};border:1.5px solid ${section.border};border-radius:7px;font-size:12px;font-weight:600;color:${section.color};cursor:pointer;white-space:nowrap">Edit</button>
      </div>
    </div>
  `;
}

function renderSupportBlock(block, section) {
  const description = block?.description ? `<div style="font-size:12px;color:#64748b;margin-top:4px">${escHtml(block.description)}</div>` : '';
  let content = '';
  if (block.kind === 'runtime_profiles') {
    content = Array.isArray(block.items) && block.items.length
      ? block.items.map((item) => renderRuntimeProfileCard(item, section)).join('')
      : '<div style="color:#94a3b8;background:#fff;border:1px dashed #e5e7eb;border-radius:12px;padding:18px;text-align:center">No runtime profiles</div>';
  } else if (block.kind === 'prompt_bundle_policy') {
    content = Array.isArray(block.items) && block.items.length
      ? block.items.map((item) => renderPromptBundlePolicyCard(item, section)).join('')
      : '<div style="color:#94a3b8;background:#fff;border:1px dashed #e5e7eb;border-radius:12px;padding:18px;text-align:center">No prompt bundles</div>';
  } else if (block.kind === 'prompt_bundle_repair') {
    content = Array.isArray(block.items) && block.items.length
      ? block.items.map((item) => renderPromptBundleRepairCard(item, section)).join('')
      : '<div style="color:#94a3b8;background:#fff;border:1px dashed #e5e7eb;border-radius:12px;padding:18px;text-align:center">No repair bundles</div>';
  } else {
    content = `<div style="background:#fff;border:1px dashed ${section.border};border-radius:12px;padding:18px;font-size:12px;color:#64748b">${escHtml(block.description || 'This stage is deterministic and does not have an independent prompt layer.')}</div>`;
  }
  return `
    <div style="display:grid;gap:12px;margin-top:14px">
      <div>
        <div style="font-size:13px;font-weight:700;color:#1e293b">${escHtml(block.title || 'Support')}</div>
        ${description}
      </div>
      <div style="display:grid;gap:12px">${content}</div>
    </div>
  `;
}

function renderPipelineSummary() {
  const wrap = document.getElementById('promptPipelineSummary');
  const summary = promptPipeline.summary || {};
  const cards = [
    { label: 'Prompt Configs', value: summary.promptConfigCount || 0, color: '#7c3aed', bg: '#f5f3ff', border: '#ddd6fe' },
    { label: 'Prompt Bundles', value: summary.promptBundleCount || 0, color: '#2563eb', bg: '#eff6ff', border: '#bfdbfe' },
    { label: 'Active Bundles', value: summary.activePromptBundleCount || 0, color: '#0f766e', bg: '#ecfeff', border: '#99f6e4' },
    { label: 'Runtime Profiles', value: summary.runtimeProfileCount || 0, color: '#d97706', bg: '#fffbeb', border: '#fde68a' },
    { label: 'Enabled Profiles', value: summary.enabledRuntimeProfileCount || 0, color: '#e11d48', bg: '#fff1f2', border: '#fecdd3' },
  ];
  wrap.innerHTML = cards.map((card) => `
    <div style="min-width:150px;background:${card.bg};border:1px solid ${card.border};border-radius:12px;padding:12px 14px">
      <div style="font-size:11px;font-weight:700;letter-spacing:.04em;color:${card.color};text-transform:uppercase">${card.label}</div>
      <div style="font-size:24px;font-weight:700;color:#1e293b;margin-top:6px">${escHtml(String(card.value))}</div>
    </div>
  `).join('');
}

async function loadConfigs() {
  const nav = document.getElementById('pipelineNav');
  const list = document.getElementById('promptList');
  nav.innerHTML = '<div style="color:#94a3b8;text-align:center;width:100%">Loading...</div>';
  list.innerHTML = '<div style="color:#94a3b8;padding:40px;text-align:center;background:#fff;border-radius:12px;border:1px dashed #e5e7eb">Loading prompt pipeline...</div>';
  try {
    promptPipeline = await api('/prompt-pipeline');
    renderPipelineSummary();
    renderPipelineNav();
    renderConfigs();
  } catch (e) {
    console.error('Load prompt pipeline error:', e);
    nav.innerHTML = '<div style="color:#dc2626;text-align:center;width:100%">Load failed</div>';
    list.innerHTML = `<div style="color:#dc2626;padding:40px;text-align:center;background:#fff;border-radius:12px;border:1px dashed #fecaca">Load failed: ${escHtml(e.message)}</div>`;
  }
}

function renderPipelineNav() {
  const nav = document.getElementById('pipelineNav');
  const steps = Array.isArray(promptPipeline.steps) ? promptPipeline.steps : [];
  if (!steps.length) {
    nav.innerHTML = '<div style="color:#94a3b8;text-align:center;width:100%">No pipeline metadata</div>';
    return;
  }
  nav.innerHTML = steps.map((stage, index) => `
    <div style="display:flex;align-items:center;flex:1;min-width:0">
      <div onclick="scrollToStage('${escAttr(stage.id)}')" style="flex:1;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:6px;padding:14px 10px;border-radius:12px;border:1.5px solid ${stage.border};background:${stage.bg};transition:box-shadow .15s,transform .15s" onmouseover="this.style.boxShadow='0 6px 20px rgba(0,0,0,.1)';this.style.transform='translateY(-2px)'" onmouseout="this.style.boxShadow='none';this.style.transform='none'">
        <div style="font-size:11px;font-weight:700;color:${stage.color};letter-spacing:.03em">${escHtml(stage.tag || `Step ${index + 1}`)}</div>
        <div style="font-size:13px;font-weight:700;color:#1e293b;text-align:center">${escHtml(stage.title || stage.id)}</div>
        <div style="font-size:11px;color:#94a3b8">${escHtml(String((Array.isArray(stage.prompts) ? stage.prompts.length : 0) + (Array.isArray(stage.support) ? stage.support.length : 0)))} items</div>
      </div>
      ${index < steps.length - 1 ? `
        <div style="display:flex;align-items:center;padding:0 6px;flex-shrink:0">
          <div style="width:32px;height:2px;background:linear-gradient(90deg,${stage.color}88,${steps[index + 1].color}88);border-radius:1px"></div>
          <div style="font-size:14px;color:#cbd5e1;margin-left:-2px">></div>
        </div>` : ''}
    </div>
  `).join('');
}

function scrollToStage(id) {
  const el = document.getElementById('stage-' + id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderSection(section, isExtra = false) {
  const prompts = Array.isArray(section.prompts) ? section.prompts : [];
  const support = Array.isArray(section.support) ? section.support : [];
  const items = [];

  if (prompts.length) {
    items.push(`<div style="display:grid;gap:12px">${prompts.map((item) => renderPromptCard(item, section)).join('')}</div>`);
  }
  if (support.length) {
    items.push(support.map((block) => renderSupportBlock(block, section)).join(''));
  }
  if (!items.length) {
    items.push('<div style="color:#94a3b8;background:#fff;border:1px dashed #e5e7eb;border-radius:12px;padding:18px;text-align:center">No editable prompt assets in this section</div>');
  }

  return `
    <div id="stage-${escAttr(section.id)}" style="margin-bottom:28px">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px">
        <div style="width:3px;height:36px;background:${section.color};border-radius:2px;flex-shrink:0"></div>
        <div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span style="font-size:15px;font-weight:700;color:#1e293b">${escHtml(section.title || section.id)}</span>
            <span style="font-size:11px;font-weight:600;color:${section.color};background:${section.bg};border:1px solid ${section.border};padding:1px 8px;border-radius:20px">${escHtml(section.tag || (isExtra ? 'Extra' : 'Step'))}</span>
          </div>
          <div style="font-size:12px;color:#64748b;margin-top:4px">${escHtml(section.description || '')}</div>
        </div>
      </div>
      <div style="display:grid;gap:12px;padding-left:18px">${items.join('')}</div>
    </div>
  `;
}

function renderConfigs() {
  const el = document.getElementById('promptList');
  const steps = Array.isArray(promptPipeline.steps) ? promptPipeline.steps : [];
  const extras = Array.isArray(promptPipeline.extras) ? promptPipeline.extras : [];
  if (!steps.length && !extras.length) {
    el.innerHTML = '<div style="color:#94a3b8;padding:40px;text-align:center;background:#fff;border-radius:12px;border:1px dashed #e5e7eb">No prompt pipeline data</div>';
    return;
  }

  const stepHtml = steps.map((section) => renderSection(section, false)).join('');
  const extraHtml = extras.length
    ? `
      <div style="margin:24px 0 16px;padding-top:20px;border-top:1px solid #e5e7eb">
        <div style="font-size:14px;font-weight:700;color:#1e293b;margin-bottom:12px">Extras</div>
        ${extras.map((section) => renderSection(section, true)).join('')}
      </div>
    `
    : '';

  el.innerHTML = stepHtml + extraHtml;
}

function openPromptModal(key) {
  const found = findPromptItemByKey(key);
  if (!found) return;
  setPromptModalState({
    mode: 'prompt',
    key,
    section: found.section,
    title: found.item.displayName || found.item.shortKey || found.item.configKey,
    description: found.item.description || '',
    value: String(found.item.configValue || ''),
    resetValue: found.item.defaultValue !== undefined ? String(found.item.defaultValue || '') : String(found.item.configValue || ''),
    resetLabel: found.item.defaultValue !== undefined ? 'Reset to catalog default' : 'Reset to current value',
    editorMode: 'Plain text prompt',
    varsHint: Array.isArray(found.item.variables) && found.item.variables.length
      ? `Variables: ${found.item.variables.map((variable) => `<code style="background:#f1f5f9;padding:1px 5px;border-radius:3px;color:#475569">${escHtml(variable)}</code>`).join(' ')}`
      : '',
    saveLabel: 'Save prompt',
  });
}

function openBundlePolicyModal(id, version) {
  const found = findBundleSupportItem('prompt_bundle_policy', id, version);
  if (!found) return;
  setPromptModalState({
    mode: 'bundle_policy',
    id,
    version,
    section: found.section,
    title: `${found.item.id} v${found.item.version}`,
    description: 'Edit live prompt bundle policy payload.',
    value: JSON.stringify({
      status: found.item.status,
      productPolicy: found.item.productPolicy,
      lockedContractOverride: found.item.lockedContractOverride,
      profileOverrides: found.item.profileOverrides,
      metadata: found.item.metadata,
    }, null, 2),
    resetLabel: 'Reset to current payload',
    editorMode: 'JSON payload',
    varsHint: 'Allowed fields: status, productPolicy, lockedContractOverride, profileOverrides, metadata',
    saveLabel: 'Save policy',
  });
}

function openBundleRepairModal(id, version) {
  const found = findBundleSupportItem('prompt_bundle_repair', id, version);
  if (!found) return;
  setPromptModalState({
    mode: 'bundle_repair',
    id,
    version,
    section: found.section,
    title: `${found.item.id} v${found.item.version}`,
    description: 'Edit live repair playbook payload.',
    value: JSON.stringify({
      status: found.item.status,
      repairPlaybook: found.item.repairPlaybook,
      metadata: found.item.metadata,
    }, null, 2),
    resetLabel: 'Reset to current payload',
    editorMode: 'JSON payload',
    varsHint: 'Allowed fields: status, repairPlaybook, metadata',
    saveLabel: 'Save repair',
  });
}

function openRuntimeProfileModal(id) {
  const found = findRuntimeProfileItem(id);
  if (!found) return;
  setPromptModalState({
    mode: 'runtime_profile',
    id,
    section: found.section,
    title: found.item.displayName || found.item.id,
    description: 'Edit runtime profile catalog payload.',
    value: JSON.stringify({
      displayName: found.item.displayName,
      enabled: found.item.enabled,
      skeletonVersion: found.item.skeletonVersion,
      fewShotPrompt: found.item.fewShotPrompt,
      metadata: found.item.metadata,
    }, null, 2),
    resetLabel: 'Reset to current payload',
    editorMode: 'JSON payload',
    varsHint: 'Allowed fields: displayName, enabled, skeletonVersion, fewShotPrompt, metadata',
    saveLabel: 'Save profile',
  });
}

function setPromptModalState(state) {
  modalCurrent = {
    ...state,
    resetValue: state.resetValue !== undefined ? String(state.resetValue) : String(state.value || ''),
  };
  const section = state.section || { bg: '#f1f5f9', color: '#64748b', border: '#e5e7eb', tag: 'Prompt' };
  document.getElementById('modalStageTag').textContent = `${section.tag || 'Prompt'} / ${section.title || section.id || ''}`;
  document.getElementById('modalStageTag').style.background = section.bg || '#f1f5f9';
  document.getElementById('modalStageTag').style.color = section.color || '#64748b';
  document.getElementById('modalStageTag').style.border = `1px solid ${section.border || '#e5e7eb'}`;
  document.getElementById('modalKey').textContent = state.title || '';
  document.getElementById('modalDesc').textContent = state.description || '';
  document.getElementById('modalEditorMode').textContent = state.editorMode || '';
  document.getElementById('modalTextarea').value = state.value || '';
  document.getElementById('modalVarsHint').innerHTML = state.varsHint || '';
  document.getElementById('modalResetBtn').textContent = state.resetLabel || 'Reset';
  document.getElementById('modalSaveBtn').textContent = state.saveLabel || 'Save';
  updateModalCharCount();
  const modal = document.getElementById('promptModal');
  modal.style.display = 'flex';
  setTimeout(() => document.getElementById('modalTextarea').focus(), 50);
}

function closePromptModal() {
  document.getElementById('promptModal').style.display = 'none';
  modalCurrent = null;
}

function updateModalCharCount() {
  const ta = document.getElementById('modalTextarea');
  document.getElementById('modalCharCount').textContent = `${ta.value.length} chars`;
}

function handlePromptRefreshToast(refreshResult, successMessage) {
  if (!refreshResult) {
    toast(successMessage);
    return;
  }
  if (refreshResult.partialFailure) {
    toast(`${successMessage}; refreshed ${refreshResult.refreshed}, failed ${refreshResult.failed}`, 'warning');
    return;
  }
  toast(`${successMessage}; refreshed ${refreshResult.refreshed}`, 'success');
}

function resetModalToDefault() {
  if (!modalCurrent) return;
  document.getElementById('modalTextarea').value = modalCurrent.resetValue || '';
  updateModalCharCount();
}

async function saveModalConfig() {
  if (!modalCurrent) return;
  const textarea = document.getElementById('modalTextarea');
  const value = textarea.value;
  const btn = document.getElementById('modalSaveBtn');
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    let result;
    if (modalCurrent.mode === 'prompt') {
      result = await api('/configs/' + encodeURIComponent(modalCurrent.key), {
        method: 'PUT',
        body: { value },
      });
      handlePromptRefreshToast(result?.refreshResult, 'Prompt saved');
    } else if (modalCurrent.mode === 'bundle_policy') {
      const payload = value.trim() ? JSON.parse(value) : {};
      result = await api(`/prompt-bundles/${encodeURIComponent(modalCurrent.id)}/${encodeURIComponent(modalCurrent.version)}`, {
        method: 'PUT',
        body: {
          section: 'policy',
          payload,
        },
      });
      handlePromptRefreshToast(result?.refreshResult, 'Bundle policy saved');
    } else if (modalCurrent.mode === 'bundle_repair') {
      const payload = value.trim() ? JSON.parse(value) : {};
      result = await api(`/prompt-bundles/${encodeURIComponent(modalCurrent.id)}/${encodeURIComponent(modalCurrent.version)}`, {
        method: 'PUT',
        body: {
          section: 'repair',
          payload,
        },
      });
      handlePromptRefreshToast(result?.refreshResult, 'Repair playbook saved');
    } else if (modalCurrent.mode === 'runtime_profile') {
      const payload = value.trim() ? JSON.parse(value) : {};
      result = await api(`/runtime-profiles/${encodeURIComponent(modalCurrent.id)}`, {
        method: 'PUT',
        body: { payload },
      });
      handlePromptRefreshToast(result?.refreshResult, 'Runtime profile saved');
    }
    await loadConfigs();
    closePromptModal();
  } catch (e) {
    toast('Save failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

async function saveConfig(key) {
  const textarea = document.getElementById('cfg-' + key);
  if (!textarea) {
    openPromptModal(key);
    return;
  }
  try {
    const result = await api('/configs/' + encodeURIComponent(key), { method: 'PUT', body: { value: textarea.value } });
    handlePromptRefreshToast(result?.refreshResult, 'Prompt saved');
    await loadConfigs();
  } catch (e) {
    toast('Save failed: ' + e.message, 'error');
  }
}

async function refreshPromptPipeline() {
  try {
    const result = await api('/configs/refresh-prompts', { method: 'POST' });
    handlePromptRefreshToast(result, 'Prompt cache refreshed');
    await loadConfigs();
  } catch (e) {
    toast('Refresh failed: ' + e.message, 'error');
  }
}

async function initPrompts() {
  if (!confirm('Initialize missing default prompt configs and refresh AI prompt caches?')) return;
  try {
    const result = await api('/configs/init-prompts', { method: 'POST' });
    handlePromptRefreshToast(result?.refreshResult, `Initialized ${result.created} prompt configs`);
    await loadConfigs();
  } catch (e) {
    toast('Initialize failed: ' + e.message, 'error');
  }
}

// Close modal on backdrop click
document.addEventListener('click', function(e) {
  const modal = document.getElementById('promptModal');
  if (modal && e.target === modal) closePromptModal();
});

// Close modal on Escape
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape' && document.getElementById('promptModal').style.display === 'flex') closePromptModal();
});

// ===================== App Distribution =====================
function appReleaseStatusBadge(status) {
  if (status === 'published') return 'badge-published';
  if (status === 'archived') return 'badge-banned';
  return 'badge-draft';
}

function appReleasePlatformLabel(platform) {
  return platform === 'ios' ? 'iOS' : 'Android';
}

function appReleaseSourceLabel(sourceType) {
  return ({
    upload: 'APK 上传',
    external_url: '外部链接',
    app_store: '商店链接',
  })[sourceType] || sourceType || '-';
}

function parseNumberInput(id, fallback = 0) {
  const value = Number.parseInt(String(document.getElementById(id)?.value || '').trim(), 10);
  return Number.isFinite(value) ? value : fallback;
}

function getInputValue(id) {
  return String(document.getElementById(id)?.value || '').trim();
}

function getCheckedValue(id) {
  return Boolean(document.getElementById(id)?.checked);
}

function formatBytes(size) {
  const numeric = Number(size);
  if (!Number.isFinite(numeric) || numeric <= 0) return '-';
  if (numeric >= 1024 * 1024 * 1024) return (numeric / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
  if (numeric >= 1024 * 1024) return (numeric / (1024 * 1024)).toFixed(2) + ' MB';
  if (numeric >= 1024) return (numeric / 1024).toFixed(1) + ' KB';
  return numeric + ' B';
}

function loadAppDistribution() {
  if (!currentAppReleaseId) resetAppReleaseForm();
  loadAppPromoConfig();
  loadAppReleases();
  loadAppPromoEvents();
}

async function loadAppPromoConfig() {
  const wrap = document.getElementById('appPromoConfigWrap');
  if (wrap) {
    wrap.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:40px">加载中...</div>';
  }
  try {
    appPromoConfig = await api('/app-promo/config');
    renderAppPromoConfig();
  } catch (e) {
    if (wrap) {
      wrap.innerHTML = `<div style="color:#dc2626;text-align:center;padding:40px">加载失败: ${escHtml(e.message)}</div>`;
    }
  }
}

function renderAppPromoConfig() {
  const wrap = document.getElementById('appPromoConfigWrap');
  const config = appPromoConfig || {};
  const scenes = config.scenes || {};
  const createComplete = scenes.createComplete || {};
  const playNudge = scenes.playNudge || {};
  const links = config.links || {};
  const copy = config.copy || {};
  const createCopy = copy.createComplete || {};
  const playCopy = copy.playNudge || {};
  const wechatCopy = copy.wechatGuide || {};

  wrap.innerHTML = `
    <div class="form-grid">
      <div class="form-group">
        <label style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="app-promo-enabled" style="width:auto" ${config.enabled ? 'checked' : ''}>
          启用 H5 下载弹窗
        </label>
      </div>
      <div class="form-group">
        <label>微信内策略</label>
        <select id="app-promo-wechatMode">
          <option value="guide_to_browser" ${config.wechatMode === 'guide_to_browser' ? 'selected' : ''}>guide_to_browser</option>
          <option value="direct_link" ${config.wechatMode === 'direct_link' ? 'selected' : ''}>direct_link</option>
        </select>
      </div>

      <div class="form-group">
        <label style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="app-promo-create-enabled" style="width:auto" ${createComplete.enabled ? 'checked' : ''}>
          启用 create_complete
        </label>
      </div>
      <div class="form-group">
        <label>Create 冷却时间（小时）</label>
        <input type="number" id="app-promo-create-cooldown" value="${escAttr(String(createComplete.cooldownHours ?? 168))}">
      </div>
      <div class="form-group">
        <label>Create 30 天最大曝光</label>
        <input type="number" id="app-promo-create-max30d" value="${escAttr(String(createComplete.maxImpressions30d ?? 1))}">
      </div>
      <div class="form-group">
        <label style="display:flex;align-items:center;gap:8px">
          <input type="checkbox" id="app-promo-play-enabled" style="width:auto" ${playNudge.enabled ? 'checked' : ''}>
          启用 play_nudge
        </label>
      </div>
      <div class="form-group">
        <label>试玩最少会话数</label>
        <input type="number" id="app-promo-play-minSessions" value="${escAttr(String(playNudge.minSessions ?? 3))}">
      </div>
      <div class="form-group">
        <label>试玩最少秒数</label>
        <input type="number" id="app-promo-play-minSeconds" value="${escAttr(String(playNudge.minSeconds ?? 180))}">
      </div>
      <div class="form-group">
        <label>试玩冷却时间（小时）</label>
        <input type="number" id="app-promo-play-cooldown" value="${escAttr(String(playNudge.cooldownHours ?? 72))}">
      </div>
      <div class="form-group">
        <label>试玩 30 天最大曝光</label>
        <input type="number" id="app-promo-play-max30d" value="${escAttr(String(playNudge.maxImpressions30d ?? 3))}">
      </div>
      <div class="form-group full">
        <label>Universal URL</label>
        <input type="text" id="app-promo-universalUrl" value="${escAttr(links.universalUrl || '')}" placeholder="https://app.gamevallies.com/open">
      </div>

      <div class="form-group">
        <label>Create 标题</label>
        <input type="text" id="app-promo-copy-create-title" value="${escAttr(createCopy.title || '')}">
      </div>
      <div class="form-group">
        <label>Create 主按钮</label>
        <input type="text" id="app-promo-copy-create-primary" value="${escAttr(createCopy.primaryCta || '')}">
      </div>
      <div class="form-group full">
        <label>Create 文案</label>
        <textarea id="app-promo-copy-create-body" rows="3">${escHtml(createCopy.body || '')}</textarea>
      </div>
      <div class="form-group">
        <label>Create 次按钮</label>
        <input type="text" id="app-promo-copy-create-secondary" value="${escAttr(createCopy.secondaryCta || '')}">
      </div>
      <div class="form-group">
        <label>试玩标题</label>
        <input type="text" id="app-promo-copy-play-title" value="${escAttr(playCopy.title || '')}">
      </div>
      <div class="form-group">
        <label>试玩主按钮</label>
        <input type="text" id="app-promo-copy-play-primary" value="${escAttr(playCopy.primaryCta || '')}">
      </div>
      <div class="form-group full">
        <label>试玩文案</label>
        <textarea id="app-promo-copy-play-body" rows="3">${escHtml(playCopy.body || '')}</textarea>
      </div>
      <div class="form-group">
        <label>试玩次按钮</label>
        <input type="text" id="app-promo-copy-play-secondary" value="${escAttr(playCopy.secondaryCta || '')}">
      </div>
      <div class="form-group">
        <label>微信标题</label>
        <input type="text" id="app-promo-copy-wechat-title" value="${escAttr(wechatCopy.title || '')}">
      </div>
      <div class="form-group full">
        <label>微信文案</label>
        <textarea id="app-promo-copy-wechat-body" rows="3">${escHtml(wechatCopy.body || '')}</textarea>
      </div>
    </div>
  `;
}

async function saveAppPromoConfig() {
  const btn = document.getElementById('appPromoSaveBtn');
  const originalLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = '保存中...';

  try {
    const payload = {
      enabled: getCheckedValue('app-promo-enabled'),
      wechatMode: getInputValue('app-promo-wechatMode'),
      scenes: {
        createComplete: {
          enabled: getCheckedValue('app-promo-create-enabled'),
          cooldownHours: parseNumberInput('app-promo-create-cooldown', 168),
          maxImpressions30d: parseNumberInput('app-promo-create-max30d', 1),
        },
        playNudge: {
          enabled: getCheckedValue('app-promo-play-enabled'),
          minSessions: parseNumberInput('app-promo-play-minSessions', 3),
          minSeconds: parseNumberInput('app-promo-play-minSeconds', 180),
          cooldownHours: parseNumberInput('app-promo-play-cooldown', 72),
          maxImpressions30d: parseNumberInput('app-promo-play-max30d', 3),
        },
      },
      links: {
        universalUrl: getInputValue('app-promo-universalUrl'),
      },
      copy: {
        createComplete: {
          title: getInputValue('app-promo-copy-create-title'),
          body: document.getElementById('app-promo-copy-create-body')?.value || '',
          primaryCta: getInputValue('app-promo-copy-create-primary'),
          secondaryCta: getInputValue('app-promo-copy-create-secondary'),
        },
        playNudge: {
          title: getInputValue('app-promo-copy-play-title'),
          body: document.getElementById('app-promo-copy-play-body')?.value || '',
          primaryCta: getInputValue('app-promo-copy-play-primary'),
          secondaryCta: getInputValue('app-promo-copy-play-secondary'),
        },
        wechatGuide: {
          title: getInputValue('app-promo-copy-wechat-title'),
          body: document.getElementById('app-promo-copy-wechat-body')?.value || '',
        },
      },
    };

    appPromoConfig = await api('/app-promo/config', {
      method: 'PUT',
      body: payload,
    });
    renderAppPromoConfig();
    toast('弹窗策略已保存');
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

function resetAppReleaseForm() {
  currentAppReleaseId = null;
  document.getElementById('appReleaseFormTitle').textContent = '创建版本';
  document.getElementById('appReleaseSubmitBtn').textContent = '创建版本';
  document.getElementById('app-release-platform').value = 'android';
  document.getElementById('app-release-channel').value = 'production';
  document.getElementById('app-release-versionName').value = '';
  document.getElementById('app-release-buildNumber').value = '';
  document.getElementById('app-release-sourceType').value = 'external_url';
  document.getElementById('app-release-status').value = 'draft';
  document.getElementById('app-release-downloadUrl').value = '';
  document.getElementById('app-release-qrCodeUrl').value = '';
  document.getElementById('app-release-releaseNotes').value = '';
  document.getElementById('app-release-isActive').checked = false;
  handleAppReleasePlatformChange();
}

function populateAppReleaseForm(id) {
  const release = appReleaseItems.find(item => item.id === id);
  if (!release) {
    toast('版本不存在或已被刷新移除', 'error');
    return;
  }

  currentAppReleaseId = release.id;
  document.getElementById('appReleaseFormTitle').textContent = '编辑版本';
  document.getElementById('appReleaseSubmitBtn').textContent = '保存版本';
  document.getElementById('app-release-platform').value = release.platform || 'android';
  document.getElementById('app-release-channel').value = release.channel || 'production';
  document.getElementById('app-release-versionName').value = release.versionName || '';
  document.getElementById('app-release-buildNumber').value = release.buildNumber || '';
  document.getElementById('app-release-sourceType').value = release.sourceType || 'external_url';
  document.getElementById('app-release-status').value = release.status || 'draft';
  document.getElementById('app-release-downloadUrl').value = release.downloadUrl || '';
  document.getElementById('app-release-qrCodeUrl').value = release.qrCodeUrl || '';
  document.getElementById('app-release-releaseNotes').value = release.releaseNotes || '';
  document.getElementById('app-release-isActive').checked = Boolean(release.isActive);
  handleAppReleasePlatformChange();
}

function handleAppReleasePlatformChange() {
  const platform = getInputValue('app-release-platform');
  const sourceSelect = document.getElementById('app-release-sourceType');
  const uploadOption = sourceSelect.querySelector('option[value="upload"]');
  const downloadInput = document.getElementById('app-release-downloadUrl');
  const hint = document.getElementById('appReleaseSourceHint');

  if (uploadOption) {
    uploadOption.disabled = platform !== 'android';
  }

  if (platform === 'ios' && sourceSelect.value === 'upload') {
    sourceSelect.value = 'app_store';
  }

  if (platform === 'ios' && !sourceSelect.value) {
    sourceSelect.value = 'app_store';
  }

  const sourceType = sourceSelect.value;
  downloadInput.disabled = sourceType === 'upload';

  if (sourceType === 'upload') {
    downloadInput.value = '';
    hint.textContent = '创建后请从右侧列表上传 APK，上传成功后系统会自动生成下载地址。';
    return;
  }

  if (platform === 'ios') {
    hint.textContent = 'iOS 一期建议填写 App Store 链接或 Universal Link。';
    return;
  }

  hint.textContent = 'Android 可填写 CDN 下载页、应用商店页或其他外部下载入口。';
}

async function loadAppReleases() {
  const wrap = document.getElementById('appReleaseTableWrap');
  const preservedId = currentAppReleaseId;
  document.getElementById('appReleaseSummary').textContent = '加载中...';
  wrap.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:40px">加载中...</div>';

  try {
    const data = await api('/app-releases?page=1&limit=50');
    appReleaseItems = Array.isArray(data?.items) ? data.items : [];
    renderAppReleaseTable(data);
    if (preservedId && appReleaseItems.some(item => item.id === preservedId)) {
      populateAppReleaseForm(preservedId);
    } else if (!currentAppReleaseId) {
      resetAppReleaseForm();
    }
  } catch (e) {
    wrap.innerHTML = `<div style="color:#dc2626;text-align:center;padding:40px">加载失败: ${escHtml(e.message)}</div>`;
    document.getElementById('appReleaseSummary').textContent = '加载失败';
  }
}

function renderAppReleaseTable(data) {
  const wrap = document.getElementById('appReleaseTableWrap');
  const items = Array.isArray(appReleaseItems) ? appReleaseItems : [];
  document.getElementById('appReleaseSummary').textContent = `共 ${items.length} 个版本，最近更新时间 ${items[0]?.updatedAt ? fmtDateTime(items[0].updatedAt) : '-'}`;

  if (!items.length) {
    wrap.innerHTML = '<div class="workspace-empty">暂无版本记录</div>';
    return;
  }

  let html = '<div class="table-wrap"><table><thead><tr><th>平台 / 版本</th><th>渠道</th><th>来源</th><th>状态</th><th>包体</th><th>下载</th><th>更新时间</th><th style="min-width:220px">操作</th></tr></thead><tbody>';
  for (const item of items) {
    const packageStatus = item.sourceType === 'upload'
      ? (item.storageKey ? `已上传 · ${escHtml(formatBytes(item.fileSize))}` : '待上传 APK')
      : '-';
    const downloadHtml = item.downloadUrl
      ? `<a class="abtn abtn-preview" href="${escAttr(item.downloadUrl)}" target="_blank" rel="noreferrer">打开链接</a>`
      : '<span style="color:#94a3b8">未配置</span>';

    html += `<tr>
      <td>
        <div class="llm-row-title">${escHtml(appReleasePlatformLabel(item.platform))}</div>
        <div class="llm-row-sub">${escHtml(item.versionName || '-')} ${item.buildNumber ? `(${escHtml(item.buildNumber)})` : ''}</div>
      </td>
      <td>${escHtml(item.channel || 'production')}</td>
      <td>
        <div>${escHtml(appReleaseSourceLabel(item.sourceType))}</div>
        <div class="llm-row-sub">${item.fileName ? escHtml(item.fileName) : '-'}</div>
      </td>
      <td>
        <span class="badge ${appReleaseStatusBadge(item.status)}">${escHtml(item.status || '-')}</span>
        ${item.isActive ? '<div class="llm-row-sub">当前激活版本</div>' : '<div class="llm-row-sub">未激活</div>'}
      </td>
      <td>${packageStatus}</td>
      <td>${downloadHtml}</td>
      <td>${escHtml(fmtDateTime(item.updatedAt))}</td>
      <td>
        <div class="action-cell">
          <button class="abtn abtn-edit" onclick="populateAppReleaseForm('${escAttr(item.id)}')">编辑</button>
          <button class="abtn abtn-preview" onclick="publishAppReleaseRow('${escAttr(item.id)}')">发布</button>
          ${item.platform === 'android' ? `<button class="abtn abtn-edit" onclick="triggerAppReleaseUpload('${escAttr(item.id)}')">上传 APK</button>` : ''}
        </div>
      </td>
    </tr>`;
  }
  html += '</tbody></table></div>';
  if (data && data.total > items.length) {
    html += `<div class="workspace-empty" style="padding-top:14px">当前仅展示前 ${items.length} 条版本记录，可按需继续扩展分页。</div>`;
  }
  wrap.innerHTML = html;
}

async function saveAppRelease() {
  const submitBtn = document.getElementById('appReleaseSubmitBtn');
  const originalLabel = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent = currentAppReleaseId ? '保存中...' : '创建中...';

  try {
    const sourceType = getInputValue('app-release-sourceType');
    const payload = {
      platform: getInputValue('app-release-platform'),
      channel: getInputValue('app-release-channel') || 'production',
      versionName: getInputValue('app-release-versionName'),
      buildNumber: getInputValue('app-release-buildNumber') || null,
      releaseNotes: document.getElementById('app-release-releaseNotes')?.value || '',
      downloadUrl: sourceType === 'upload' ? null : (getInputValue('app-release-downloadUrl') || null),
      qrCodeUrl: getInputValue('app-release-qrCodeUrl') || null,
      sourceType,
      status: getInputValue('app-release-status'),
      isActive: getCheckedValue('app-release-isActive'),
    };

    const result = await api(
      currentAppReleaseId ? '/app-releases/' + encodeURIComponent(currentAppReleaseId) : '/app-releases',
      {
        method: currentAppReleaseId ? 'PUT' : 'POST',
        body: payload,
      },
    );

    toast(currentAppReleaseId ? '版本已更新' : '版本已创建');
    currentAppReleaseId = result.id;
    await loadAppReleases();
    if (result.id) {
      populateAppReleaseForm(result.id);
    }
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = currentAppReleaseId ? '保存版本' : originalLabel;
  }
}

async function publishAppReleaseRow(id) {
  const yes = await confirmDialog('发布版本', '确认发布该版本吗？同平台同渠道的其他激活版本会被停用。');
  if (!yes) return;
  try {
    await api('/app-releases/' + encodeURIComponent(id) + '/publish', {
      method: 'POST',
      body: {},
    });
    toast('版本已发布');
    await loadAppReleases();
    await loadAppPromoConfig();
  } catch (e) {
    toast('发布失败: ' + e.message, 'error');
  }
}

function triggerAppReleaseUpload(id) {
  pendingAppReleaseUploadId = id;
  const input = document.getElementById('appReleaseFileInput');
  if (input) {
    input.value = '';
    input.click();
  }
}

async function handleAppReleasePackageSelect(event) {
  const file = event?.target?.files?.[0];
  const targetId = pendingAppReleaseUploadId;
  event.target.value = '';
  pendingAppReleaseUploadId = null;

  if (!targetId || !file) {
    return;
  }
  if (!String(file.name || '').toLowerCase().endsWith('.apk')) {
    toast('请选择 .apk 文件', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  try {
    await apiForm('/app-releases/' + encodeURIComponent(targetId) + '/upload', formData, {
      method: 'POST',
    });
    toast('APK 上传成功');
    await loadAppReleases();
  } catch (e) {
    toast('上传失败: ' + e.message, 'error');
  }
}

async function loadAppPromoEvents() {
  const wrap = document.getElementById('appPromoEventWrap');
  wrap.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:40px">加载中...</div>';

  try {
    const data = await api('/app-promo/events?page=1&limit=20');
    appPromoEventItems = Array.isArray(data?.items) ? data.items : [];
    renderAppPromoEvents();
  } catch (e) {
    wrap.innerHTML = `<div style="color:#dc2626;text-align:center;padding:40px">加载失败: ${escHtml(e.message)}</div>`;
  }
}

function renderAppPromoEvents() {
  const wrap = document.getElementById('appPromoEventWrap');
  const items = Array.isArray(appPromoEventItems) ? appPromoEventItems : [];

  if (!items.length) {
    wrap.innerHTML = '<div class="workspace-empty">暂无事件数据</div>';
    return;
  }

  let html = '<div class="table-wrap"><table><thead><tr><th>时间</th><th>场景</th><th>事件</th><th>平台</th><th>游戏</th><th>设备</th></tr></thead><tbody>';
  for (const item of items) {
    html += `<tr>
      <td>${escHtml(fmtDateTime(item.createdAt))}</td>
      <td>${escHtml(item.scene || '-')}</td>
      <td>${escHtml(item.eventType || '-')}</td>
      <td>${escHtml(item.platform || '-')}</td>
      <td>${escHtml(item.gameId || '-')}</td>
      <td><code>${escHtml(item.deviceId || '-')}</code></td>
    </tr>`;
  }
  html += '</tbody></table></div>';
  wrap.innerHTML = html;
}

// ===================== Timeout Configs =====================
function timeoutInputId(key) {
  return 'timeout-' + String(key || '').replace(/[^a-zA-Z0-9_-]/g, '-');
}

function timeoutServiceLabel(service) {
  return ({
    'shared': '共享链路',
    'game-service': 'game-service',
    'ai-engine': 'ai-engine',
  })[service] || service || '未分组';
}

function timeoutGroupLabel(group) {
  return ({
    pipeline: 'Pipeline',
    tasking: 'Tasking',
    routing: 'Routing',
    http: 'HTTP',
    relay: 'Relay',
    llm: 'LLM',
    qa: 'QA',
    runtime_qa: 'Runtime QA',
    config: 'Config IO',
    custom: 'Custom',
  })[group] || group || 'Other';
}

function timeoutUnitSuffix(entry) {
  if (!entry || !entry.unit || entry.unit === 'ratio') return '';
  return entry.unit;
}

async function loadTimeoutConfigs() {
  const wrap = document.getElementById('timeoutConfigWrap');
  wrap.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:40px">加载中...</div>';
  try {
    timeoutConfigList = await api('/configs?category=timeout');
    renderTimeoutConfigs();
  } catch (e) {
    wrap.innerHTML = `<div style="color:#dc2626;text-align:center;padding:40px">加载失败: ${escHtml(e.message)}</div>`;
  }
}

function renderTimeoutConfigs() {
  const wrap = document.getElementById('timeoutConfigWrap');
  if (!timeoutConfigList.length) {
    wrap.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:40px;background:#fff;border:1px dashed #e5e7eb;border-radius:12px">暂无超时配置</div>';
    return;
  }

  const serviceOrder = ['shared', 'game-service', 'ai-engine'];
  const grouped = new Map();
  timeoutConfigList.forEach((entry) => {
    const service = entry.service || 'custom';
    const group = entry.group || 'custom';
    if (!grouped.has(service)) grouped.set(service, new Map());
    const serviceMap = grouped.get(service);
    if (!serviceMap.has(group)) serviceMap.set(group, []);
    serviceMap.get(group).push(entry);
  });

  wrap.innerHTML = serviceOrder
    .filter((service) => grouped.has(service))
    .concat(Array.from(grouped.keys()).filter((service) => !serviceOrder.includes(service)))
    .map((service) => {
      const serviceGroups = grouped.get(service);
      const groups = Array.from(serviceGroups.keys()).sort();
      return `
        <div style="margin-bottom:20px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <h3 style="font-size:16px;font-weight:700;color:#1e293b">${escHtml(timeoutServiceLabel(service))}</h3>
            <span style="font-size:12px;color:#94a3b8">${groups.reduce((sum, group) => sum + serviceGroups.get(group).length, 0)} 项</span>
          </div>
          <div style="display:grid;gap:14px">
            ${groups.map((group) => `
              <div style="background:#fff;border-radius:12px;border:1px solid #e5e7eb;padding:18px 20px">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px">
                  <div>
                    <div style="font-size:14px;font-weight:700;color:#1e293b">${escHtml(timeoutGroupLabel(group))}</div>
                    <div style="font-size:12px;color:#94a3b8;margin-top:4px">${serviceGroups.get(group).length} 项</div>
                  </div>
                </div>
                <div style="display:grid;gap:12px">
                  ${serviceGroups.get(group).map((entry) => `
                    <div style="border:1px solid #eef2f7;border-radius:12px;padding:14px 16px;background:${entry.source === 'db' ? '#ffffff' : '#f8fafc'}">
                      <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap">
                        <div style="min-width:0;flex:1">
                          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                            <code style="font-size:12px;font-weight:700;color:#1e293b;background:#f8fafc;border:1px solid #e2e8f0;padding:2px 8px;border-radius:6px">${escHtml(entry.configKey)}</code>
                            <span class="badge ${entry.source === 'db' ? 'badge-published' : 'badge-draft'}">${escHtml(entry.source === 'db' ? 'DB' : 'Catalog')}</span>
                            <span style="font-size:11px;color:#64748b">${escHtml(entry.valueType || 'int')} / ${escHtml(entry.unit || '-')}</span>
                          </div>
                          <div style="font-size:12px;color:#64748b;line-height:1.6;margin-top:8px">${escHtml(entry.description || '')}</div>
                          <div style="font-size:11px;color:#94a3b8;margin-top:8px">默认值: ${escHtml(String(entry.defaultValue ?? '-'))}${escHtml(timeoutUnitSuffix(entry))}</div>
                        </div>
                        <div style="display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap">
                          <div>
                            <label style="display:block;font-size:11px;color:#94a3b8;margin-bottom:4px">当前值</label>
                            <div style="display:flex;align-items:center;gap:6px">
                              <input type="number" step="${entry.valueType === 'float' ? '0.01' : '1'}" id="${timeoutInputId(entry.configKey)}" value="${escAttr(String(entry.configValue ?? ''))}" style="width:140px;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;font-size:13px">
                              ${entry.unit && entry.unit !== 'ratio' ? `<span style="font-size:12px;color:#64748b">${escHtml(entry.unit)}</span>` : ''}
                            </div>
                          </div>
                          <button class="btn btn-primary btn-sm" onclick="saveTimeoutConfig('${escAttr(entry.configKey)}')">保存</button>
                        </div>
                      </div>
                    </div>
                  `).join('')}
                </div>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }).join('');
}

async function saveTimeoutConfig(key) {
  const input = document.getElementById(timeoutInputId(key));
  if (!input) return;
  const value = String(input.value || '').trim();
  if (!value) {
    toast('请输入超时值', 'error');
    return;
  }
  try {
    const result = await api('/configs/' + encodeURIComponent(key), {
      method: 'PUT',
      body: { value, category: 'timeout' },
    });
    if (result && result.refreshResult && result.refreshResult.partialFailure) {
      toast(`超时配置已保存，但仍有 ${result.refreshResult.failed} 个节点未刷新`, 'warning');
    } else {
      toast('超时配置已保存');
    }
    await loadTimeoutConfigs();
    return;
  } catch (e) {
    toast('保存失败: ' + e.message, 'error');
  }
}

async function initTimeoutConfigs() {
  if (!confirm('将初始化缺失的超时配置项，已存在的项不会被覆盖，确认吗？')) return;
  try {
    const result = await api('/configs/init-timeouts', { method: 'POST' });
    toast(`已初始化: 新增 ${result.created}，跳过 ${result.skipped}`);
    await loadTimeoutConfigs();
  } catch (e) {
    toast('初始化失败: ' + e.message, 'error');
  }
}

async function refreshTimeoutConfigs() {
  try {
    const result = await api('/configs/refresh-timeouts', { method: 'POST' });
    if (result.partialFailure) {
      toast(`已刷新 ${result.refreshed} 个服务缓存，${result.failed} 个节点刷新失败`, 'error');
    } else {
      toast(`已刷新 ${result.refreshed} 个服务缓存`);
    }
    await loadTimeoutConfigs();
  } catch (e) {
    toast('刷新失败: ' + e.message, 'error');
  }
}

const TIMEOUT_FLOW_SECTION_FALLBACKS = {
  step1_intent_freeze: { id: 'step1_intent_freeze', kind: 'flow', order: 10, tag: 'Step 1', title: 'Intent Freeze', description: 'Creation-session extraction and entry watchdogs.', color: '#7c3aed', bg: '#f5f3ff', border: '#ddd6fe' },
  step2_spec_contract: { id: 'step2_spec_contract', kind: 'flow', order: 20, tag: 'Step 2', title: 'Spec & Contract', description: 'Intent parse and spec compilation budgets.', color: '#0f766e', bg: '#ecfeff', border: '#99f6e4' },
  step3_code_synthesis: { id: 'step3_code_synthesis', kind: 'flow', order: 30, tag: 'Step 3', title: 'Code Synthesis', description: 'Primary create-generation timeout ceilings.', color: '#2563eb', bg: '#eff6ff', border: '#bfdbfe' },
  step4_issue_repair: { id: 'step4_issue_repair', kind: 'flow', order: 40, tag: 'Step 4', title: 'Issue Repair & Runtime QA', description: 'QA repair windows and runtime validation watchdogs.', color: '#d97706', bg: '#fffbeb', border: '#fde68a' },
  step5_finalize: { id: 'step5_finalize', kind: 'flow', order: 50, tag: 'Step 5', title: 'Finalize', description: 'Finalize is deterministic for now, so there is no standalone timeout key yet.', color: '#e11d48', bg: '#fff1f2', border: '#fecdd3' },
  extra_iterate: { id: 'extra_iterate', kind: 'extra', order: 60, tag: 'Extra', title: 'Iterate Flow', description: 'Iterate classify and rewrite budgets outside the main create chain.', color: '#7c2d12', bg: '#fff7ed', border: '#fdba74' },
  extra_tools: { id: 'extra_tools', kind: 'extra', order: 70, tag: 'Extra', title: 'Auxiliary Tools', description: 'Auxiliary sidecar tools.', color: '#475569', bg: '#f8fafc', border: '#cbd5e1' },
  infra_pipeline: { id: 'infra_pipeline', kind: 'infra', order: 80, tag: 'Infra', title: 'Pipeline Guardrails', description: 'Cross-service total deadlines and buffers.', color: '#1d4ed8', bg: '#f8fbff', border: '#dbeafe' },
  infra_transport: { id: 'infra_transport', kind: 'infra', order: 90, tag: 'Infra', title: 'Upstream Transport', description: 'game-service to ai-engine requests, polling, and cancellation.', color: '#1d4ed8', bg: '#f8fbff', border: '#dbeafe' },
  infra_relay: { id: 'infra_relay', kind: 'infra', order: 100, tag: 'Infra', title: 'Relay Backchannel', description: 'ai-engine callbacks back into game-service.', color: '#1d4ed8', bg: '#f8fbff', border: '#dbeafe' },
  infra_config: { id: 'infra_config', kind: 'infra', order: 110, tag: 'Infra', title: 'Config & Gateway Cache', description: 'Admin refresh, config-store reads, and gateway cache windows.', color: '#1d4ed8', bg: '#f8fbff', border: '#dbeafe' },
  infra_tasking: { id: 'infra_tasking', kind: 'infra', order: 120, tag: 'Infra', title: 'Background Tasking', description: 'Sweep loops, heartbeat cadence, and completed-task retention.', color: '#1d4ed8', bg: '#f8fbff', border: '#dbeafe' },
  custom: { id: 'custom', kind: 'infra', order: 999, tag: 'Custom', title: 'Custom Timeout Keys', description: 'DB keys that are not yet mapped into the five-step timeout catalog.', color: '#475569', bg: '#f8fafc', border: '#cbd5e1' },
};

const TIMEOUT_FLOW_EMPTY_HINTS = {
  step5_finalize: 'Current Step 5 finalize remains deterministic, so there is no separate timeout switch yet.',
};

function timeoutServiceLabel(service) {
  return ({
    'shared': 'shared',
    'game-service': 'game-service',
    'ai-engine': 'ai-engine',
  })[service] || service || 'unknown';
}

function timeoutGroupLabel(group) {
  return ({
    admin: 'Admin',
    config: 'Config IO',
    dialogue: 'Dialogue',
    http: 'HTTP',
    intent: 'Intent Parse',
    iterate: 'Iterate',
    llm: 'LLM',
    pipeline: 'Pipeline',
    qa: 'QA',
    relay: 'Relay',
    routing: 'Routing',
    runtime_qa: 'Runtime QA',
    tasking: 'Tasking',
    tooling: 'Tools',
    custom: 'Custom',
  })[group] || group || 'Other';
}

function timeoutPromptSectionMeta(sectionId) {
  if (!timeoutPipelineData) return null;
  const steps = Array.isArray(timeoutPipelineData.steps) ? timeoutPipelineData.steps : [];
  const extras = Array.isArray(timeoutPipelineData.extras) ? timeoutPipelineData.extras : [];
  const stepIndex = steps.findIndex(item => item && item.id === sectionId);
  if (stepIndex >= 0) {
    const step = steps[stepIndex];
    const fallback = TIMEOUT_FLOW_SECTION_FALLBACKS[sectionId] || {};
    return {
      id: sectionId,
      kind: 'flow',
      order: Number(step.stepNumber || 0) > 0 ? Number(step.stepNumber) * 10 : (fallback.order || ((stepIndex + 1) * 10)),
      tag: step.tag || fallback.tag || ('Step ' + String(step.stepNumber || stepIndex + 1)),
      title: step.title || fallback.title || sectionId,
      description: step.description || fallback.description || '',
      color: step.color || fallback.color || '#2563eb',
      bg: step.bg || fallback.bg || '#eff6ff',
      border: step.border || fallback.border || '#bfdbfe',
    };
  }
  const extraIndex = extras.findIndex(item => item && item.id === sectionId);
  if (extraIndex >= 0) {
    const extra = extras[extraIndex];
    const fallback = TIMEOUT_FLOW_SECTION_FALLBACKS[sectionId] || {};
    return {
      id: sectionId,
      kind: 'extra',
      order: fallback.order || (60 + extraIndex * 10),
      tag: extra.tag || fallback.tag || 'Extra',
      title: extra.title || fallback.title || sectionId,
      description: extra.description || fallback.description || '',
      color: extra.color || fallback.color || '#475569',
      bg: extra.bg || fallback.bg || '#f8fafc',
      border: extra.border || fallback.border || '#cbd5e1',
    };
  }
  return null;
}

function timeoutResolveSectionMeta(sectionId, entry) {
  const pipelineMeta = timeoutPromptSectionMeta(sectionId);
  if (pipelineMeta) return pipelineMeta;
  const fallback = TIMEOUT_FLOW_SECTION_FALLBACKS[sectionId] || null;
  if (fallback) return fallback;
  return {
    id: sectionId || 'custom',
    kind: entry?.sectionKind || 'infra',
    order: Number(entry?.sectionOrder || 999),
    tag: entry?.sectionTag || 'Custom',
    title: entry?.sectionTitle || 'Custom Timeout Keys',
    description: entry?.sectionDescription || 'DB keys that are not yet mapped into the five-step timeout catalog.',
    color: '#475569',
    bg: '#f8fafc',
    border: '#cbd5e1',
  };
}

function timeoutSectionEntries(entries, sectionId) {
  return (Array.isArray(entries) ? entries : [])
    .filter(entry => (entry.sectionId || 'custom') === sectionId)
    .sort((a, b) => Number(a.itemOrder || 999) - Number(b.itemOrder || 999) || String(a.configKey || '').localeCompare(String(b.configKey || '')));
}

function timeoutBuildSections(entries) {
  const items = Array.isArray(entries) ? entries : [];
  const sections = new Map();

  items.forEach((entry) => {
    const sectionId = entry.sectionId || 'custom';
    if (!sections.has(sectionId)) {
      sections.set(sectionId, timeoutResolveSectionMeta(sectionId, entry));
    }
  });

  const promptSteps = Array.isArray(timeoutPipelineData?.steps) ? timeoutPipelineData.steps : [];
  promptSteps.forEach((step) => {
    if (step && step.id && !sections.has(step.id)) {
      sections.set(step.id, timeoutResolveSectionMeta(step.id, null));
    }
  });

  const promptExtras = Array.isArray(timeoutPipelineData?.extras) ? timeoutPipelineData.extras : [];
  promptExtras.forEach((extra) => {
    if (!extra || !extra.id) return;
    if ((extra.id === 'extra_iterate' || items.some(item => item.sectionId === extra.id)) && !sections.has(extra.id)) {
      sections.set(extra.id, timeoutResolveSectionMeta(extra.id, null));
    }
  });

  if (!sections.has('step5_finalize')) {
    sections.set('step5_finalize', timeoutResolveSectionMeta('step5_finalize', null));
  }

  return Array.from(sections.values())
    .filter(section => items.some(item => (item.sectionId || 'custom') === section.id) || Boolean(TIMEOUT_FLOW_EMPTY_HINTS[section.id]))
    .sort((a, b) => Number(a.order || 999) - Number(b.order || 999) || String(a.id).localeCompare(String(b.id)));
}

function renderTimeoutOverview(sections, entries) {
  return `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:18px">
      ${sections.map((section) => {
        const count = timeoutSectionEntries(entries, section.id).length;
        return `
          <div style="background:${escHtml(section.bg || '#fff')};border:1px solid ${escHtml(section.border || '#e2e8f0')};border-radius:14px;padding:14px 16px">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
              <span style="font-size:11px;font-weight:700;color:${escHtml(section.color || '#2563eb')};letter-spacing:0.04em;text-transform:uppercase">${escHtml(section.tag || 'Section')}</span>
              <span style="font-size:12px;color:#475569">${escHtml(String(count))} 项</span>
            </div>
            <div style="font-size:15px;font-weight:700;color:#0f172a;margin-top:8px">${escHtml(section.title || section.id)}</div>
            <div style="font-size:12px;line-height:1.5;color:#64748b;margin-top:6px">${escHtml(section.description || '')}</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

async function loadTimeoutConfigs() {
  const wrap = document.getElementById('timeoutConfigWrap');
  wrap.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:40px">Loading timeout layout...</div>';
  try {
    const configs = await api('/configs?category=timeout');
    timeoutConfigList = Array.isArray(configs) ? configs : [];
    timeoutPipelineData = null;
    renderTimeoutConfigs();
  } catch (e) {
    wrap.innerHTML = `<div style="color:#dc2626;text-align:center;padding:40px">Failed to load timeout config: ${escHtml(e.message)}</div>`;
  }
}

function renderTimeoutConfigs() {
  const wrap = document.getElementById('timeoutConfigWrap');
  if (!timeoutConfigList.length) {
    wrap.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:40px;background:#fff;border:1px dashed #e5e7eb;border-radius:12px">No timeout configs found.</div>';
    return;
  }

  const sectionMap = new Map();
  timeoutConfigList.forEach((entry) => {
    const sectionId = entry.sectionId || 'business_global';
    if (!sectionMap.has(sectionId)) {
      sectionMap.set(sectionId, {
        id: sectionId,
        title: entry.sectionTitle || 'Timeouts',
        description: entry.sectionDescription || '',
        tag: entry.sectionTag || 'Section',
        order: Number(entry.sectionOrder || 999),
        items: [],
      });
    }
    sectionMap.get(sectionId).items.push(entry);
  });

  const sections = Array.from(sectionMap.values())
    .map((section) => ({
      ...section,
      items: section.items.sort((a, b) => Number(a.itemOrder || 999) - Number(b.itemOrder || 999)),
    }))
    .sort((a, b) => a.order - b.order);

  wrap.innerHTML = `
    ${sections.map((section) => `
      <div style="margin-bottom:18px;background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:18px">
        <div style="margin-bottom:14px">
          <div style="font-size:11px;font-weight:700;color:#2563eb;letter-spacing:0.04em;text-transform:uppercase">${escHtml(section.tag)}</div>
          <div style="font-size:17px;font-weight:700;color:#0f172a;margin-top:6px">${escHtml(section.title)}</div>
        </div>
        <div style="display:grid;gap:12px">
          ${section.items.map((entry) => {
            const backingKeys = Array.isArray(entry.backingKeys) ? entry.backingKeys : [];
            return `
              <div style="border:1px solid #e5e7eb;border-radius:14px;padding:16px;background:${entry.source === 'db' ? '#ffffff' : '#f8fafc'}">
                <div style="display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap">
                  <div style="min-width:0;flex:1">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                      <div style="font-size:15px;font-weight:700;color:#0f172a">${escHtml(entry.displayName || entry.configKey)}</div>
                      ${entry.optional ? '<span class="badge badge-draft">Optional</span>' : ''}
                      <span class="badge ${entry.source === 'db' ? 'badge-published' : 'badge-draft'}">${escHtml(entry.source === 'db' ? 'Custom' : 'Default')}</span>
                    </div>
                    <div style="font-size:11px;color:#94a3b8;margin-top:10px">
                      默认值: ${escHtml(String(entry.defaultValue ?? '-'))}${escHtml(timeoutUnitSuffix(entry))}
                      ${backingKeys.length ? ` · 关联 ${escHtml(String(backingKeys.length))} 个内部键` : ''}
                    </div>
                    <div style="font-size:11px;color:#cbd5e1;margin-top:6px"><code>${escHtml(entry.configKey)}</code></div>
                  </div>
                  <div style="display:flex;align-items:flex-end;gap:8px;flex-wrap:wrap">
                    <div>
                      <label style="display:block;font-size:11px;color:#94a3b8;margin-bottom:4px">当前值</label>
                      <div style="display:flex;align-items:center;gap:6px">
                        <input type="number" step="${entry.valueType === 'float' ? '0.01' : '1'}" id="${timeoutInputId(entry.configKey)}" value="${escAttr(String(entry.configValue ?? ''))}" style="width:140px;padding:8px 10px;border:1px solid #d1d5db;border-radius:8px;font-size:13px">
                        ${entry.unit && entry.unit !== 'ratio' ? `<span style="font-size:12px;color:#64748b">${escHtml(entry.unit)}</span>` : ''}
                      </div>
                    </div>
                    <button class="btn btn-primary btn-sm" onclick="saveTimeoutConfig('${escAttr(entry.configKey)}')">Save</button>
                  </div>
                </div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `).join('')}
  `;
}

async function saveTimeoutConfig(key) {
  const input = document.getElementById(timeoutInputId(key));
  if (!input) return;
  const value = String(input.value || '').trim();
  if (!value) {
    toast('Please enter a timeout value.', 'error');
    return;
  }
  try {
    const result = await api('/configs/' + encodeURIComponent(key), {
      method: 'PUT',
      body: { value, category: 'timeout' },
    });
    if (result && result.refreshResult && result.refreshResult.partialFailure) {
      toast(`Saved, but ${result.refreshResult.failed} node(s) did not refresh.`, 'warning');
    } else {
      toast('Timeout config saved.');
    }
    await loadTimeoutConfigs();
  } catch (e) {
    toast('Failed to save timeout config: ' + e.message, 'error');
  }
}

async function initTimeoutConfigs() {
  if (!confirm('Initialize missing timeout keys only? Existing values will be kept.')) return;
  try {
    const result = await api('/configs/init-timeouts', { method: 'POST' });
    toast(`Initialized timeout keys: created ${result.created}, skipped ${result.skipped}.`);
    await loadTimeoutConfigs();
  } catch (e) {
    toast('Failed to initialize timeout configs: ' + e.message, 'error');
  }
}

async function refreshTimeoutConfigs() {
  try {
    const result = await api('/configs/refresh-timeouts', { method: 'POST' });
    if (result.partialFailure) {
      toast(`Refreshed ${result.refreshed} cache target(s), but ${result.failed} node(s) failed.`, 'error');
    } else {
      toast(`Refreshed ${result.refreshed} cache target(s).`);
    }
    await loadTimeoutConfigs();
  } catch (e) {
    toast('Failed to refresh timeout configs: ' + e.message, 'error');
  }
}

