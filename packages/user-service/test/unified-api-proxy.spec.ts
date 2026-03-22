import { getProxyTargets, resolveAiEngineProxyBaseUrl } from '../src/edge/unified-api-proxy';

describe('resolveAiEngineProxyBaseUrl', () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env.AI_ENGINE_DEFAULT_REGION;
    delete process.env.SERVICE_REGION;
    delete process.env.AI_ENGINE_URL;
    delete process.env.AI_ENGINE_URL_CN_SHANGHAI;
    delete process.env.AI_ENGINE_URL_AP_SOUTHEAST_JOHOR;
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  it('prefers the China region-specific AI engine URL by default', () => {
    process.env.AI_ENGINE_URL = 'https://legacy-ai.example.com';
    process.env.AI_ENGINE_URL_CN_SHANGHAI = 'https://ai-cn.example.com';

    expect(resolveAiEngineProxyBaseUrl()).toBe('https://ai-cn.example.com');
  });

  it('uses the Johor region-specific AI engine URL when configured as default region', () => {
    process.env.AI_ENGINE_DEFAULT_REGION = 'ap_southeast_johor';
    process.env.AI_ENGINE_URL_CN_SHANGHAI = 'https://ai-cn.example.com';
    process.env.AI_ENGINE_URL_AP_SOUTHEAST_JOHOR = 'https://ai-global.example.com';

    expect(resolveAiEngineProxyBaseUrl()).toBe('https://ai-global.example.com');
  });

  it('falls back to the legacy AI engine URL when region-specific values are absent', () => {
    process.env.AI_ENGINE_URL = 'https://legacy-ai.example.com/';

    expect(resolveAiEngineProxyBaseUrl()).toBe('https://legacy-ai.example.com');
  });

  it('returns null when no AI engine upstream is configured', () => {
    expect(resolveAiEngineProxyBaseUrl()).toBeNull();
  });

  it('keeps supporting the stripped /ai proxy path after the global prefix is removed upstream', () => {
    process.env.AI_ENGINE_URL_CN_SHANGHAI = 'https://ai-cn.example.com';

    const aiTarget = getProxyTargets().find((target) => target.name === 'ai-engine');
    expect(aiTarget?.prefixes).toContain('/ai');
  });
});
