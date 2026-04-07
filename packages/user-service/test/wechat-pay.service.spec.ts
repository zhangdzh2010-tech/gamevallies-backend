import { ConfigService } from '@nestjs/config';
import {
  createCipheriv,
  createSign,
  generateKeyPairSync,
} from 'crypto';
import { WechatPayService } from '../src/billing/wechat-pay.service';

describe('WechatPayService', () => {
  let configService: ConfigService;
  let service: WechatPayService;
  let fetchMock: jest.Mock;
  let privateKeyPem: string;
  let publicKeyPem: string;

  beforeEach(() => {
    const { privateKey, publicKey } = generateKeyPairSync('rsa', {
      modulusLength: 2048,
    });
    privateKeyPem = privateKey.export({ type: 'pkcs1', format: 'pem' }).toString();
    publicKeyPem = publicKey.export({ type: 'pkcs1', format: 'pem' }).toString();

    configService = {
      get: jest.fn((key: string) => {
        const values: Record<string, string> = {
          WECHAT_PAY_MODE: 'real',
          WECHAT_PAY_MERCHANT_ID: '1900001111',
          WECHAT_PAY_NOTIFY_URL: 'https://example.com/api/v1/subscription/wechat/notify',
          WECHAT_PAY_SERIAL_NO: 'SERIAL123',
          WECHAT_PAY_PRIVATE_KEY: privateKeyPem,
          WECHAT_PAY_PUBLIC_KEY: publicKeyPem,
          WECHAT_PAY_API_V3_KEY: '12345678901234567890123456789012',
          WECHAT_PAY_API_BASE: 'https://api.mch.weixin.qq.com',
          WECHAT_MINIAPP_APP_ID: 'wx1234567890',
        };
        return values[key];
      }),
    } as unknown as ConfigService;

    service = new WechatPayService(configService);
    fetchMock = jest.fn();
    (global as any).fetch = fetchMock;
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('formats the real-mode authorization header correctly for JSAPI orders', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ prepay_id: 'wx_prepay_123' }),
    });

    const result = await service.createPayment({
      appId: 'wx1234567890',
      openId: 'openid-123',
      description: 'GameVallies 基础月卡',
      outTradeNo: 'gv202603210001',
      amount: 990,
      notifyUrl: 'https://example.com/api/v1/subscription/wechat/notify',
      clientIp: '127.0.0.1',
    });

    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.mch.weixin.qq.com/v3/pay/transactions/jsapi',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: expect.stringMatching(
            /^WECHATPAY2-SHA256-RSA2048 mchid="1900001111",nonce_str="[^"]+",signature="[^"]+",timestamp="\d+",serial_no="SERIAL123"$/,
          ),
        }),
      }),
    );
    expect('package' in result.payment).toBe(true);
    if (!('package' in result.payment) || !('signType' in result.payment)) {
      throw new Error('Expected JSAPI payment payload');
    }
    expect(result.prepayId).toBe('wx_prepay_123');
    expect(result.payment.package).toBe('prepay_id=wx_prepay_123');
    expect(result.payment.signType).toBe('RSA');
  });

  it('verifies and decrypts real paid notifications using the raw body', async () => {
    const apiV3Key = '12345678901234567890123456789012';
    const resourcePayload = JSON.stringify({
      out_trade_no: 'gv202603210001',
      trade_state: 'SUCCESS',
      transaction_id: 'wx_txn_123',
    });
    const nonce = '0123456789ab';
    const associatedData = 'transaction';
    const cipher = createCipheriv(
      'aes-256-gcm',
      Buffer.from(apiV3Key, 'utf8'),
      Buffer.from(nonce, 'utf8'),
    );
    cipher.setAAD(Buffer.from(associatedData, 'utf8'));
    const ciphertext = Buffer.concat([
      cipher.update(resourcePayload, 'utf8'),
      cipher.final(),
    ]);
    const authTag = cipher.getAuthTag();
    const body = {
      id: 'notify_123',
      resource: {
        algorithm: 'AEAD_AES_256_GCM',
        ciphertext: Buffer.concat([ciphertext, authTag]).toString('base64'),
        nonce,
        associated_data: associatedData,
      },
    };
    const rawBody = JSON.stringify(body);
    const timestamp = '1774076800';
    const signature = createSign('RSA-SHA256')
      .update(`${timestamp}\nnonce_header\n${rawBody}\n`)
      .end()
      .sign(privateKeyPem, 'base64');

    const result = await service.parsePaidNotification(
      {
        'wechatpay-signature': signature,
        'wechatpay-timestamp': timestamp,
        'wechatpay-nonce': 'nonce_header',
      },
      rawBody,
      body,
    );

    expect(result).toEqual({
      out_trade_no: 'gv202603210001',
      trade_state: 'SUCCESS',
      transaction_id: 'wx_txn_123',
    });
  });

  it('surfaces non-json WeChat API failures without throwing a JSON parse error', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      text: async () => '<html>bad gateway</html>',
    });

    await expect(service.createPayment({
      appId: 'wx1234567890',
      openId: 'openid-123',
      description: 'GameVallies 基础月卡',
      outTradeNo: 'gv202603210002',
      amount: 990,
      notifyUrl: 'https://example.com/api/v1/subscription/wechat/notify',
      clientIp: '127.0.0.1',
    })).rejects.toThrow('WeChat Pay request failed: HTTP 502 <html>bad gateway</html>');
  });
});
