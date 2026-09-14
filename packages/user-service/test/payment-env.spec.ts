import {
  applyPaymentEnvAliases,
  resolvePaymentEnv,
} from '../src/billing/payment-env';

describe('payment env aliases', () => {
  it('copies zltokens pack keys onto canonical WECHAT_PAY_* and app-id names', () => {
    const env: NodeJS.ProcessEnv = {
      WECHAT_MCH_ID: 'mch-1',
      WECHAT_API_V3_KEY: 'k'.repeat(32),
      WECHAT_CERT_SERIAL: 'serial-1',
      WECHAT_APP_ID: 'wx-shared-app',
    };

    applyPaymentEnvAliases(env);

    expect(env.WECHAT_PAY_MERCHANT_ID).toBe('mch-1');
    expect(env.WECHAT_PAY_API_V3_KEY).toBe('k'.repeat(32));
    expect(env.WECHAT_PAY_SERIAL_NO).toBe('serial-1');
    expect(env.WECHAT_H5_APP_ID).toBe('wx-shared-app');
    expect(env.WECHAT_MINIAPP_APP_ID).toBe('wx-shared-app');
  });

  it('does not override canonical values that are already set', () => {
    const env: NodeJS.ProcessEnv = {
      WECHAT_PAY_MERCHANT_ID: 'canonical-mch',
      WECHAT_MCH_ID: 'alias-mch',
      WECHAT_H5_APP_ID: 'wx-h5',
      WECHAT_APP_ID: 'wx-generic',
    };

    applyPaymentEnvAliases(env);

    expect(env.WECHAT_PAY_MERCHANT_ID).toBe('canonical-mch');
    expect(env.WECHAT_H5_APP_ID).toBe('wx-h5');
    expect(env.WECHAT_MINIAPP_APP_ID).toBe('wx-generic');
  });

  it('resolves aliases through a ConfigService-style getter', () => {
    const values: Record<string, string> = {
      WECHAT_MCH_ID: 'mch-alias',
      WECHAT_APP_ID: 'wx-alias',
    };

    expect(resolvePaymentEnv((key) => values[key], 'WECHAT_PAY_MERCHANT_ID')).toBe('mch-alias');
    expect(resolvePaymentEnv((key) => values[key], 'WECHAT_H5_APP_ID')).toBe('wx-alias');
    expect(resolvePaymentEnv((key) => values[key], 'WECHAT_MINIAPP_APP_ID')).toBe('wx-alias');
    expect(resolvePaymentEnv((key) => values[key], 'WECHAT_PAY_NOTIFY_URL')).toBeUndefined();
  });
});
