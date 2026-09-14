import {
  allowedBrowserOrigins,
  parseCorsOriginList,
  resolveCorsOrigin,
} from '../src/common/utils/cors-origin';

describe('CORS origin config', () => {
  const originalOrigin = process.env.CORS_ORIGIN;
  const originalOrigins = process.env.CORS_ORIGINS;

  beforeEach(() => {
    delete process.env.CORS_ORIGIN;
    delete process.env.CORS_ORIGINS;
  });

  afterEach(() => {
    if (originalOrigin === undefined) {
      delete process.env.CORS_ORIGIN;
    } else {
      process.env.CORS_ORIGIN = originalOrigin;
    }
    if (originalOrigins === undefined) {
      delete process.env.CORS_ORIGINS;
    } else {
      process.env.CORS_ORIGINS = originalOrigins;
    }
  });

  it('parses comma-separated and JSON allowlists', () => {
    expect(parseCorsOriginList('https://www.zlspace.ai,https://zlspace.ai')).toEqual([
      'https://www.zlspace.ai',
      'https://zlspace.ai',
    ]);
    expect(parseCorsOriginList('["https://www.zlspace.ai","https://zlspace.ai"]')).toEqual([
      'https://www.zlspace.ai',
      'https://zlspace.ai',
    ]);
    expect(parseCorsOriginList('*')).toEqual(['*']);
  });

  it('prefers CORS_ORIGINS and keeps credentials-safe ACAO reflection', () => {
    expect(
      resolveCorsOrigin(
        'https://www.zlspace.ai',
        '["https://www.zlspace.ai","https://zlspace.ai"]',
      ),
    ).toEqual(['https://www.zlspace.ai', 'https://zlspace.ai']);
    expect(resolveCorsOrigin('*')).toBe('*');
    expect(resolveCorsOrigin()).toBe('*');
  });

  it('collects browser origins for WeChat H5 without treating * as open', () => {
    process.env.CORS_ORIGIN = '*';
    process.env.CORS_ORIGINS = '["https://www.zlspace.ai","https://zlspace.ai"]';
    expect(allowedBrowserOrigins(['https://www.zlspace.ai', '*'])).toEqual([
      'https://www.zlspace.ai',
      'https://zlspace.ai',
    ]);
  });
});
