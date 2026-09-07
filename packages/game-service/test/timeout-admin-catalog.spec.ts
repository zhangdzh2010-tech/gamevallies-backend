import { expandBusinessTimeoutConfigUpdates } from '../src/admin/timeout-admin-catalog';

describe('thirty minute business timeouts', () => {
  it.each(['intent_parse', 'create_generation', 'qa_repair', 'runtime_qa', 'iterate_generation', 'pipeline_total'])(
    'preserves 1800 seconds in every backing key for %s', (name) => {
      const updates = expandBusinessTimeoutConfigUpdates(`timeout.business.${name}_s`, '1800');
      expect(updates.length).toBeGreaterThan(0);
      for (const update of updates) expect(update.value).toBe('1800');
    },
  );
});
