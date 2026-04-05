import { ConfigService } from '@nestjs/config';
import { createSign, generateKeyPairSync } from 'crypto';
import { AlipayPayService } from '../src/billing/alipay-pay.service';

describe('AlipayPayService', () => {
  let configService: ConfigService;
  let service: AlipayPayService;
  let privateKeyBase64: string;
  let publicKeyBase64: string;

  beforeEach(() => {
    const { privateKey, publicKey } = generateKeyPairSync('rsa', {
      modulusLength: 2048,
    });
    const privatePem = privateKey.export({ type: 'pkcs8', format: 'pem' }).toString();
    const publicPem = publicKey.export({ type: 'spki', format: 'pem' }).toString();
    privateKeyBase64 = privatePem
      .replace('-----BEGIN PRIVATE KEY-----', '')
      .replace('-----END PRIVATE KEY-----', '')
      .replace(/\s+/g, '');
    publicKeyBase64 = publicPem
      .replace('-----BEGIN PUBLIC KEY-----', '')
      .replace('-----END PUBLIC KEY-----', '')
      .replace(/\s+/g, '');

    configService = {
      get: jest.fn((key: string) => {
        const values: Record<string, string> = {
          ALIPAY_MODE: 'real',
          ALIPAY_APP_ID: '2021006144605202',
          ALIPAY_PRIVATE_KEY: privateKeyBase64,
          ALIPAY_PUBLIC_KEY: publicKeyBase64,
          ALIPAY_GATEWAY: 'https://openapi.alipay.com/gateway.do',
          ALIPAY_SIGN_TYPE: 'RSA2',
        };
        return values[key];
      }),
    } as unknown as ConfigService;

    service = new AlipayPayService(configService);
  });

  it('builds a signed pay url for wap orders', async () => {
    const result = await service.createPayment({
      flow: 'wap',
      description: 'GameVallies 月卡',
      outTradeNo: 'gv202604050001',
      amount: 990,
      notifyUrl: 'https://example.com/api/v1/subscription/alipay/notify',
      returnUrl: 'https://gamevallies.com/payment/result',
    });

    expect(result.payment.provider).toBe('alipay');
    expect(result.payment.flow).toBe('wap');
    expect(result.payment.payUrl).toContain('https://openapi.alipay.com/gateway.do?');
    expect(result.payment.payUrl).toContain('method=alipay.trade.wap.pay');
    expect(result.payment.payUrl).toContain('sign=');
  });

  it('verifies a signed paid notification', async () => {
    const payload: Record<string, string> = {
      app_id: '2021006144605202',
      notify_time: '2026-04-05 18:20:01',
      out_trade_no: 'gv202604050001',
      trade_no: '20260405000001',
      total_amount: '9.90',
      trade_status: 'TRADE_SUCCESS',
      seller_id: '2088100000000000',
      sign_type: 'RSA2',
    };
    const signContent = Object.entries(payload)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, value]) => `${key}=${value}`)
      .join('&');
    const sign = createSign('RSA-SHA256')
      .update(signContent, 'utf8')
      .end()
      .sign(`-----BEGIN PRIVATE KEY-----\n${privateKeyBase64.match(/.{1,64}/g)?.join('\n')}\n-----END PRIVATE KEY-----`, 'base64');

    const result = await service.parsePaidNotification({
      ...payload,
      sign,
    });

    expect(result.out_trade_no).toBe('gv202604050001');
    expect(result.trade_status).toBe('TRADE_SUCCESS');
    expect(result.trade_no).toBe('20260405000001');
  });
});
