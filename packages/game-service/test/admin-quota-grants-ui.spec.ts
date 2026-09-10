import * as fs from 'fs';
import * as path from 'path';
import * as vm from 'vm';
import { randomUUID } from 'crypto';

const source = fs.readFileSync(path.join(__dirname, '../src/admin/admin-panel-grants.js'), 'utf8');
function page(storage = new Map<string, string>()) {
  const elements: Record<string, any> = {};
  for (const id of ['grantUser', 'grantPlan', 'grantSearch', 'grantSearchSubmit', 'grantReason', 'grantCurrent', 'grantPreview', 'grantSubmit', 'grantMessage', 'grantHistory', 'quotaGrantCurrent', 'quotaGrantHistory', 'quotaGrantAmount', 'quotaGrantPreview', 'quotaGrantSubmit']) {
    elements[id] = { value: '', disabled: false, textContent: '', innerHTML: '', selectedOptions: [{ textContent: '测试账号' }],
      replaceChildren() { this.value = ''; }, add() {} };
  }
  elements.grantUser.value = 'user-1';
  elements.grantReason.value = '种子作品配置';
  elements.quotaGrantAmount.value = '100';
  const api = jest.fn(async (url: string, options?: any): Promise<any> => {
    if (options?.method === 'POST') return { ...options.body, totalBefore: 35, totalAfter: 135, usedAtGrant: 35 };
    if (url.startsWith('/quota/grants')) return { current: { available: 0, remaining: 0, total: 35, used: 35, subscriptionRemaining: 0 }, history: [], maxAmount: 10000 };
    return { plans: [{ id: 'plan', name: '月套餐', quota: 30, period: 'monthly' }], current: { plan: { name: '月套餐' }, quotaThisPeriod: 30, usedThisPeriod: 30 }, history: [] };
  });
  const context: any = vm.createContext({
    document: { getElementById: (id: string) => elements[id] }, api,
    Option: class { constructor(public text: string, public value: string) {} },
    escHtml: (s: unknown) => String(s), fmtDate: () => '日期', confirm: jest.fn(() => true),
    crypto: { randomUUID },
    sessionStorage: { getItem: (k: string) => storage.get(k) || null, setItem: (k: string, v: string) => storage.set(k, v) },
  });
  vm.runInContext(source, context);
  return { context, api, elements, storage };
}
const posts = (api: jest.Mock) => api.mock.calls.filter(([, options]) => options?.method === 'POST');

describe('admin quota grant interactions', () => {
  it('permits quota additions when an active exhausted subscription blocks another plan', async () => {
    const { context, elements } = page();
    await context.loadGrantPage();
    elements.grantPlan.value = 'plan';
    context.updateGrantPreview();
    expect(elements.grantSubmit.disabled).toBe(true);
    expect(elements.quotaGrantSubmit.disabled).toBe(false);
    expect(elements.quotaGrantPreview.textContent).toContain('预计可用 100 次');
    elements.quotaGrantAmount.value = '1.5'; context.updateGrantPreview();
    expect(elements.quotaGrantSubmit.disabled).toBe(true);
    elements.quotaGrantAmount.value = '100'; elements.grantReason.value = ' '; context.updateGrantPreview();
    expect(elements.quotaGrantSubmit.disabled).toBe(true);
  });
  it('uses one request ID after a lost response, including a page refresh', async () => {
    const first = page();
    await first.context.loadGrantPage();
    first.api.mockImplementationOnce(async () => { throw new Error('connection lost'); });
    await first.context.submitCreationQuotaGrant();
    const original = posts(first.api)[0][1].body;
    expect(first.elements.quotaGrantAmount.value).toBe('100');
    const refreshed = page(first.storage);
    await refreshed.context.loadGrantPage();
    await refreshed.context.submitCreationQuotaGrant();
    expect(posts(refreshed.api)[0][1].body.requestId).toBe(original.requestId);
    expect(refreshed.elements.quotaGrantAmount.value).toBe('');
    expect(refreshed.elements.quotaGrantSubmit.disabled).toBe(true);
    // After confirmed success an explicit new grant gets a new request ID.
    refreshed.elements.quotaGrantAmount.value = '100';
    await refreshed.context.submitCreationQuotaGrant();
    expect(posts(refreshed.api)[1][1].body.requestId).not.toBe(original.requestId);
  });
  it('blocks double clicks while awaiting the grant result and locks the target fields', async () => {
    const { context, api, elements } = page();
    await context.loadGrantPage();
    let finish: (value: any) => void = () => {};
    api.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const pending = context.submitCreationQuotaGrant();
    expect(elements.grantUser.disabled).toBe(true);
    expect(elements.quotaGrantAmount.disabled).toBe(true);
    await context.submitCreationQuotaGrant();
    expect(posts(api)).toHaveLength(1);
    finish({ amount: 100 });
    await pending;
    expect(elements.grantUser.disabled).toBe(false);
  });
  it('disables grants after an account load failure and refuses stale target data', async () => {
    const { context, api, elements } = page();
    await context.loadGrantPage();
    elements.grantUser.value = 'user-2';
    api.mockImplementationOnce(async () => { throw new Error('load failed'); });
    await context.loadGrantPage();
    context.updateGrantPreview();
    expect(elements.quotaGrantSubmit.disabled).toBe(true);
    await context.submitCreationQuotaGrant();
    expect(posts(api)).toHaveLength(0);
  });
  it('does not issue a grant if a request ID cannot be saved for safe retries', async () => {
    const { context, api } = page();
    await context.loadGrantPage();
    context.sessionStorage.setItem = () => { throw new Error('storage disabled'); };
    await context.submitCreationQuotaGrant();
    expect(posts(api)).toHaveLength(0);
  });
});
