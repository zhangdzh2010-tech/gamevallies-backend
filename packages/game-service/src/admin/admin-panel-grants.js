let grantData = { plans: [], current: null, history: [] };
let grantRequest = null;
let grantLoading = 0;
let grantSubmitting = false;
let grantSearching = false;
let grantReadyUser = null;
let quotaGrantData = { current: null, history: [], maxAmount: 10000 };

async function searchGrantUsers() {
  if (grantSubmitting || grantSearching) return;
  const select = document.getElementById('grantUser');
  const search = document.getElementById('grantSearch').value.trim();
  if (!search) { document.getElementById('grantMessage').textContent = '请输入用户搜索条件'; return; }
  grantSearching = true;
  grantReadyUser = null;
  setGrantControls(true);
  try {
    const data = await api('/users?limit=20&search=' + encodeURIComponent(search));
    select.replaceChildren(new Option('请选择用户（最多显示 20 位，请精确搜索）', ''));
    for (const u of data.items || []) select.add(new Option(`${u.displayName || u.username} · ${u.phone || u.email || u.id}`, u.id));
    await loadGrantPage();
  } catch (e) { document.getElementById('grantMessage').textContent = e.message; }
  finally { grantSearching = false; setGrantControls(false); updateGrantPreview(); }
}

async function loadGrantPage() {
  const seq = ++grantLoading;
  grantRequest = null;
  grantReadyUser = null;
  const userId = document.getElementById('grantUser').value;
  document.getElementById('grantSubmit').disabled = true;
  document.getElementById('quotaGrantSubmit').disabled = true;
  document.getElementById('grantCurrent').textContent = '加载权益信息…';
  try {
    const suffix = userId ? '?userId=' + encodeURIComponent(userId) : '';
    const [data, quota] = await Promise.all([api('/subscription/grants' + suffix), api('/quota/grants' + suffix)]);
    if (seq !== grantLoading) return;
    grantData = data;
    quotaGrantData = quota;
    grantReadyUser = userId;
    document.getElementById('quotaGrantCurrent').textContent = quota.current
      ? `当前可用 ${quota.current.available} 次：基础及赠送剩余 ${quota.current.remaining} 次（累计 ${quota.current.total}，已用 ${quota.current.used}），有效套餐剩余 ${quota.current.subscriptionRemaining} 次。`
      : '请先选择目标用户。';
    document.getElementById('quotaGrantHistory').innerHTML = quota.history.length ? '<div class="table-wrap"><table><thead><tr><th>用户</th><th>追加次数</th><th>累计额度变化 / 已用</th><th>原因</th><th>操作员 / 时间</th></tr></thead><tbody>' + quota.history.map(g =>
      `<tr><td>${escHtml(g.userLabel)}</td><td>+${g.amount}</td><td>${g.totalBefore} → ${g.totalAfter} / ${g.usedAtGrant}</td><td>${escHtml(g.reason)}</td><td>${escHtml(g.operator)}<br>${fmtDate(g.createdAt)}</td></tr>`).join('') + '</tbody></table></div>' : '<p>暂无额度授予记录</p>';
    const plan = document.getElementById('grantPlan');
    plan.replaceChildren(new Option('请选择套餐', ''));
    for (const p of data.plans) plan.add(new Option(`${p.name} · ${p.quota} 次 / ${p.period === 'yearly' ? '年' : '月'}`, p.id));
    document.getElementById('grantCurrent').textContent = data.current
      ? `已有有效套餐：${data.current.plan.name}，到期 ${fmtDate(data.current.expiresAt)}，剩余 ${Math.max(0, data.current.quotaThisPeriod - data.current.usedThisPeriod)} 次。本页不会覆盖。`
      : (userId ? '该用户暂无有效套餐，可授予。' : '请先选择目标用户。');
    document.getElementById('grantHistory').innerHTML = data.history.length ? '<div class="table-wrap"><table><thead><tr><th>用户</th><th>套餐 / 额度</th><th>生效 / 到期</th><th>原因</th><th>操作员</th></tr></thead><tbody>' + data.history.map(g =>
      `<tr><td>${escHtml(g.userLabel)}</td><td>${escHtml(g.planName)} / ${g.quota} 次</td><td>${fmtDate(g.startedAt)}<br>${fmtDate(g.expiresAt)}</td><td>${escHtml(g.reason)}</td><td>${escHtml(g.operator)}</td></tr>`).join('') + '</tbody></table></div>' : '<p>暂无授予记录</p>';
    updateGrantPreview();
  } catch (e) {
    if (seq !== grantLoading) return;
    document.getElementById('grantCurrent').textContent = '权益信息加载失败，暂不能授予。';
    document.getElementById('quotaGrantCurrent').textContent = '额度信息加载失败，请重新选择用户。';
    document.getElementById('grantMessage').textContent = e.message;
  }
}

function updateGrantPreview() {
  const plan = grantData.plans.find(p => p.id === document.getElementById('grantPlan').value);
  document.getElementById('grantPreview').textContent = plan
    ? `立即生效，授予 ${plan.quota} 次创作额度，有效期一个${plan.period === 'yearly' ? '年度' : '月'}，不产生付款订单。` : '';
  const userId = document.getElementById('grantUser').value;
  const ready = !grantSubmitting && !grantSearching && userId && grantReadyUser === userId && document.getElementById('grantReason').value.trim();
  document.getElementById('grantSubmit').disabled = !ready || !plan || Boolean(grantData.current);
  const amount = Number(document.getElementById('quotaGrantAmount').value);
  const validAmount = Number.isInteger(amount) && amount > 0 && amount <= quotaGrantData.maxAmount;
  document.getElementById('quotaGrantSubmit').disabled = !ready || !validAmount;
  document.getElementById('quotaGrantPreview').textContent = grantReadyUser === userId && quotaGrantData.current && validAmount
    ? `追加 ${amount} 次后，预计可用 ${quotaGrantData.current.available + amount} 次；实际余额以同时发生的创作扣减为准。` : '';
}

function setGrantControls(disabled) {
  for (const id of ['grantSearch', 'grantSearchSubmit', 'grantUser', 'grantPlan', 'grantReason', 'grantSubmit', 'quotaGrantAmount', 'quotaGrantSubmit']) {
    document.getElementById(id).disabled = disabled;
  }
}

function quotaGrantRequestFor(body) {
  // Retain uncertain requests across refreshes; never replay automatically.
  const key = 'admin.creationQuota.pending';
  const pending = JSON.parse(sessionStorage.getItem(key) || '{}');
  const signature = JSON.stringify(body);
  pending[signature] ||= crypto.randomUUID();
  sessionStorage.setItem(key, JSON.stringify(pending));
  return { requestId: pending[signature], clear() {
    const latest = JSON.parse(sessionStorage.getItem(key) || '{}');
    delete latest[signature];
    sessionStorage.setItem(key, JSON.stringify(latest));
  } };
}

async function submitCreationQuotaGrant() {
  if (grantSubmitting || grantSearching) return;
  const user = document.getElementById('grantUser');
  const amount = Number(document.getElementById('quotaGrantAmount').value);
  const reason = document.getElementById('grantReason').value.trim();
  const message = document.getElementById('grantMessage');
  if (grantReadyUser !== user.value || !user.value || !Number.isInteger(amount) || amount < 1 || amount > quotaGrantData.maxAmount || !reason) {
    message.textContent = '请选择用户、填写追加次数和授予原因'; return;
  }
  if (!confirm(`用户：${user.selectedOptions[0].textContent}\n追加 ${amount} 次创作额度\n原因：${reason}\n长期有效，不改变现有套餐。确认追加？`)) return;
  grantSubmitting = true;
  setGrantControls(true);
  try {
    const body = { userId: user.value, amount, reason };
    const request = quotaGrantRequestFor(body);
    const result = await api('/quota/grants', { method: 'POST', body: { ...body, requestId: request.requestId } });
    // Clear inputs only on an acknowledged success, so a second grant is deliberate.
    document.getElementById('quotaGrantAmount').value = '';
    message.textContent = `额度授予成功：追加 ${result.amount} 次。用户刷新创作页面即可查看。`;
    try { request.clear(); } catch (_) { /* Keeping the ID is safer than issuing twice. */ }
    await loadGrantPage();
  } catch (e) {
    message.textContent = e.message + '；未确认成功时请保留相同用户、次数和原因重试，同一请求不会重复发放。';
  } finally {
    grantSubmitting = false;
    setGrantControls(false);
    updateGrantPreview();
  }
}

async function submitSubscriptionGrant() {
  if (grantSubmitting || grantSearching) return;
  const user = document.getElementById('grantUser');
  const plan = grantData.plans.find(p => p.id === document.getElementById('grantPlan').value);
  const reason = document.getElementById('grantReason').value.trim();
  const message = document.getElementById('grantMessage');
  if (!user.value || grantReadyUser !== user.value || !plan || !reason || grantData.current) { message.textContent = '请选择可授予的用户、套餐并填写原因'; return; }
  if (!confirm(`授予用户：${user.selectedOptions[0].textContent}\n套餐：${plan.name}，${plan.quota} 次\n原因：${reason}\n立即生效，不收取费用。确认授予？`)) return;
  grantRequest ||= crypto.randomUUID();
  const body = { requestId: grantRequest, userId: user.value, planId: plan.id, reason };
  grantSubmitting = true;
  setGrantControls(true);
  try {
    const result = await api('/subscription/grants', { method: 'POST', body });
    message.textContent = `授予成功：${result.planName}，${result.quota} 次，到期 ${fmtDate(result.expiresAt)}。用户刷新页面即可查看。`;
    await loadGrantPage();
  } catch (e) { message.textContent = e.message + '；如网络中断，可再次确认重试，同一请求不会重复发放。'; }
  finally {
    grantSubmitting = false;
    setGrantControls(false);
    updateGrantPreview();
  }
}
