import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { capabilityState, stepReadiness, businessReadiness, LLM_BUSINESS_STAGES } from '../src/admin/llm-readiness';

const cases = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../contracts/llm/readiness-cases.json'), 'utf8'));
const provider = { region: 'cn_shanghai', enabled: true };
describe('model readiness agrees with runtime routing', () => {
  it.each(cases)('$id', (item: any) => {
    const original = JSON.stringify(item.flags);
    const state = capabilityState(item.flags);
    const result = stepReadiness({ enabled: true, provider, capabilityFlags: item.flags }, provider.region, item.step);
    expect(state.legacyUnchecked).toBe(item.legacy);
    expect(result.ready).toBe(item.blocked.length === 0);
    expect(result.reasons).toHaveLength(item.blocked.length);
    expect(JSON.stringify(item.flags)).toBe(original);
  });
  it('requires coverage of every step, with complementary primary and backup', () => {
    const stage = LLM_BUSINESS_STAGES[0];
    const models = [
      { id: 'full', enabled: true, provider, capabilityFlags: { supports_patch_generation: false } },
      { id: 'patch', enabled: true, provider, capabilityFlags: { supports_full_html_rewrite: false } },
    ];
    expect(businessReadiness(stage, models, provider.region).ready).toBe(true);
    expect(businessReadiness(stage, models.slice(0, 1), provider.region).ready).toBe(false);
  });
  it('distinguishes connectivity and routing in the admin view', () => {
    const context = vm.createContext({ escHtml: (s: any) => String(s), api: () => undefined });
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../src/admin/admin-panel-llm.js'), 'utf8'), context);
    const markup = context.llmReadinessMarkup({ capabilityState: { legacyUnchecked: true },
      businessReadiness: [{ name: '作品生成', ready: true, steps: [] }] });
    expect(markup).toContain('作品生成：可路由');
    expect(markup).toContain('未知能力');
    expect(context.llmReadinessMarkup({ businessReadiness: [{ name: '辅助处理', ready: false,
      steps: [{ ready: false, candidates: [{ reasons: ['能力限制：supports_dialogue'] }] }] }] })).toContain('辅助处理：受限');
  });
});
