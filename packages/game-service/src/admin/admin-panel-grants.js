let grantData = { plans: [], current: null, history: [] };
let grantRequest = null;
let grantLoading = 0;
let grantSubmitting = false;

async function searchGrantUsers() {
  if (grantSubmitting) return;
  const select = document.getElementById('grantUser');
  const search = document.getElementById('grantSearch').value.trim();
  if (!search) { document.getElementById('grantMessage').textContent = '请输入用户搜索条件'; return; }
  try {
    const data = await api('/users?limit=20&search=' + encodeURIComponent(search));
    select.replaceChildren(new Option('请选择用户（最多显示 20 位，请精确搜索）', ''));
    for (const u of data.items || []) select.add(new Option(`${u.displayName || u.username} · ${u.phone || u.email || u.id}`, u.id));
    await loadGrantPage();
  } catch (e) { document.getElementById('grantMessage').textContent = e.message; }
}

async function loadGrantPage() {
  const seq = ++grantLoading;
  grantRequest = null;
  const userId = document.getElementById('grantUser').value;
  document.getElementById('grantSubmit').disabled = true;
  document.getElementById('grantCurrent').textContent = '加载权益信息…';
  try {
    const data = await api('/subscription/grants' + (userId ? '?userId=' + encodeURIComponent(userId) : ''));
    if (seq !== grantLoading) return;
    grantData = data;
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
    document.getElementById('grantMessage').textContent = e.message;
  }
}

function updateGrantPreview() {
  grantRequest = null;
  const plan = grantData.plans.find(p => p.id === document.getElementById('grantPlan').value);
  document.getElementById('grantPreview').textContent = plan
    ? `立即生效，授予 ${plan.quota} 次创作额度，有效期一个${plan.period === 'yearly' ? '年度' : '月'}，不产生付款订单。` : '';
  document.getElementById('grantSubmit').disabled = grantSubmitting || !plan || !document.getElementById('grantUser').value || Boolean(grantData.current);
}

async function submitSubscriptionGrant() {
  if (grantSubmitting) return;
  const user = document.getElementById('grantUser');
  const plan = grantData.plans.find(p => p.id === document.getElementById('grantPlan').value);
  const reason = document.getElementById('grantReason').value.trim();
  const message = document.getElementById('grantMessage');
  if (!user.value || !plan || !reason || grantData.current) { message.textContent = '请选择可授予的用户、套餐并填写原因'; return; }
  if (!confirm(`授予用户：${user.selectedOptions[0].textContent}\n套餐：${plan.name}，${plan.quota} 次\n原因：${reason}\n立即生效，不收取费用。确认授予？`)) return;
  grantRequest ||= crypto.randomUUID();
  const body = { requestId: grantRequest, userId: user.value, planId: plan.id, reason };
  grantSubmitting = true;
  for (const id of ['grantSearch', 'grantUser', 'grantPlan', 'grantReason', 'grantSubmit']) document.getElementById(id).disabled = true;
  try {
    const result = await api('/subscription/grants', { method: 'POST', body });
    message.textContent = `授予成功：${result.planName}，${result.quota} 次，到期 ${fmtDate(result.expiresAt)}。用户刷新页面即可查看。`;
    await loadGrantPage();
  } catch (e) { message.textContent = e.message + '；如网络中断，可再次确认重试，同一请求不会重复发放。'; }
  finally {
    grantSubmitting = false;
    for (const id of ['grantSearch', 'grantUser', 'grantPlan', 'grantReason']) document.getElementById(id).disabled = false;
    document.getElementById('grantSubmit').disabled = Boolean(grantData.current) || !user.value || !plan;
  }
}
