import { BadRequestException, Injectable, InternalServerErrorException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  createDecipheriv,
  createPrivateKey,
  createPublicKey,
  createSign,
  createVerify,
  KeyObject,
  randomBytes,
  createHash,
} from 'crypto';
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';

type UnifiedOrderInput = {
  appId: string;
  openId?: string;
  description: string;
  outTradeNo: string;
  amount: number;
  notifyUrl: string;
  clientIp: string;
  tradeType?: 'jsapi' | 'h5';
  h5Info?: {
    type?: 'Wap';
    appName?: string;
    appUrl?: string;
  };
};

type WxRequestPayment = {
  timeStamp: string;
  nonceStr: string;
  package: string;
  signType: 'RSA';
  paySign: string;
};

type WechatH5Payment = {
  mwebUrl: string;
};

type UnifiedOrderResult = {
  payment: WxRequestPayment | WechatH5Payment;
  prepayId: string | null;
  rawResponse: Record<string, unknown>;
};

@Injectable()
export class WechatPayService {
  private privateKey?: KeyObject;
  private publicKey?: KeyObject;

  constructor(private readonly configService: ConfigService) {}

  isMockMode(): boolean {
    return (this.configService.get<string>('WECHAT_PAY_MODE') || 'mock').toLowerCase() !== 'real';
  }

  async createPayment(input: UnifiedOrderInput): Promise<UnifiedOrderResult> {
    if (this.isMockMode()) {
      return this.createMockPayment(input);
    }

    return this.createRealPayment(input);
  }

  buildPaymentFromPrepayId(appId: string, prepayId: string): WxRequestPayment {
    return this.buildRequestPayment(appId, prepayId);
  }

  async parsePaidNotification(
    headers: Record<string, string | string[] | undefined>,
    rawBody: string,
    parsedBody: any,
  ): Promise<Record<string, any>> {
    if (!parsedBody?.resource) {
      throw new BadRequestException('Invalid WeChat Pay notification payload');
    }

    if (this.isMockMode() && parsedBody.mock === true) {
      return parsedBody.resource;
    }

    this.verifyNotificationSignature(headers, rawBody);

    const apiV3Key = this.getRequiredConfig('WECHAT_PAY_API_V3_KEY');
    if (apiV3Key.length !== 32) {
      throw new InternalServerErrorException('WECHAT_PAY_API_V3_KEY must be 32 bytes');
    }

    const ciphertextBuffer = Buffer.from(parsedBody.resource.ciphertext, 'base64');
    const authTag = ciphertextBuffer.subarray(ciphertextBuffer.length - 16);
    const encryptedData = ciphertextBuffer.subarray(0, ciphertextBuffer.length - 16);
    const decipher = createDecipheriv(
      'aes-256-gcm',
      Buffer.from(apiV3Key, 'utf8'),
      Buffer.from(parsedBody.resource.nonce, 'utf8'),
    );

    if (parsedBody.resource.associated_data) {
      decipher.setAAD(Buffer.from(parsedBody.resource.associated_data, 'utf8'));
    }

    decipher.setAuthTag(authTag);
    const plaintext = Buffer.concat([
      decipher.update(encryptedData),
      decipher.final(),
    ]).toString('utf8');

    return JSON.parse(plaintext);
  }

  private async createMockPayment(input: UnifiedOrderInput): Promise<UnifiedOrderResult> {
    if (input.tradeType === 'h5') {
      return {
        prepayId: null,
        rawResponse: {
          mock: true,
          h5_url: `https://pay.mock.gamevallies.com/wechat/${input.outTradeNo}`,
        },
        payment: {
          mwebUrl: `https://pay.mock.gamevallies.com/wechat/${input.outTradeNo}`,
        },
      };
    }

    const timeStamp = `${Math.floor(Date.now() / 1000)}`;
    const nonceStr = randomBytes(12).toString('hex');
    const prepayId = `mock_${input.outTradeNo}`;
    const packageValue = `prepay_id=${prepayId}`;

    return {
      prepayId,
      rawResponse: {
        mock: true,
        prepay_id: prepayId,
      },
      payment: {
        timeStamp,
        nonceStr,
        package: packageValue,
        signType: 'RSA',
        paySign: createHash('sha256')
          .update([input.appId, timeStamp, nonceStr, packageValue].join(':'))
          .digest('hex'),
      },
    };
  }

  private async createRealPayment(input: UnifiedOrderInput): Promise<UnifiedOrderResult> {
    const tradeType = input.tradeType || 'jsapi';
    const appId = tradeType === 'h5'
      ? (input.appId || '')
      : (input.appId || this.getRequiredConfig('WECHAT_MINIAPP_APP_ID'));
    const mchid = this.getRequiredConfig('WECHAT_PAY_MERCHANT_ID');
    const notifyUrl = input.notifyUrl || this.getRequiredConfig('WECHAT_PAY_NOTIFY_URL');
    const path = tradeType === 'h5' ? '/v3/pay/transactions/h5' : '/v3/pay/transactions/jsapi';
    const body: Record<string, unknown> = {
      mchid,
      description: input.description,
      out_trade_no: input.outTradeNo,
      notify_url: notifyUrl,
      amount: {
        total: input.amount,
        currency: 'CNY',
      },
      scene_info: {
        payer_client_ip: input.clientIp || '127.0.0.1',
      },
    };

    if (appId) {
      body.appid = appId;
    }

    if (tradeType === 'jsapi') {
      if (!input.openId) {
        throw new InternalServerErrorException('JSAPI payment requires openId');
      }

      body.payer = {
        openid: input.openId,
      };
    } else {
      body.scene_info = {
        payer_client_ip: input.clientIp || '127.0.0.1',
        h5_info: {
          type: input.h5Info?.type || 'Wap',
          app_name: input.h5Info?.appName || 'GameVallies',
          app_url: input.h5Info?.appUrl || this.configService.get<string>('PUBLIC_WEB_BASE_URL') || 'https://gamevallies.com',
        },
      };
    }

    const response = await this.callWechatApi(path, 'POST', body);
    if (tradeType === 'h5') {
      const mwebUrl = response.h5_url;

      if (!mwebUrl) {
        throw new InternalServerErrorException('WeChat Pay response missing h5_url');
      }

      return {
        prepayId: null,
        rawResponse: response,
        payment: {
          mwebUrl,
        },
      };
    }

    const prepayId = response.prepay_id;

    if (!prepayId) {
      throw new InternalServerErrorException('WeChat Pay response missing prepay_id');
    }

    return {
      prepayId,
      rawResponse: response,
      payment: this.buildRequestPayment(appId, prepayId),
    };
  }

  private buildRequestPayment(appId: string, prepayId: string): WxRequestPayment {
    const timeStamp = `${Math.floor(Date.now() / 1000)}`;
    const nonceStr = randomBytes(16).toString('hex');
    const packageValue = `prepay_id=${prepayId}`;
    const message = `${appId}\n${timeStamp}\n${nonceStr}\n${packageValue}\n`;
    const paySign = createSign('RSA-SHA256')
      .update(message)
      .end()
      .sign(this.getPrivateKey(), 'base64');

    return {
      timeStamp,
      nonceStr,
      package: packageValue,
      signType: 'RSA',
      paySign,
    };
  }

  private async callWechatApi(
    path: string,
    method: 'GET' | 'POST',
    body?: Record<string, unknown>,
  ): Promise<Record<string, any>> {
    const apiBase = (this.configService.get<string>('WECHAT_PAY_API_BASE')
      || 'https://api.mch.weixin.qq.com').replace(/\/$/, '');
    const requestBody = body ? JSON.stringify(body) : '';
    const nonceStr = randomBytes(16).toString('hex');
    const timestamp = `${Math.floor(Date.now() / 1000)}`;
    const message = `${method}\n${path}\n${timestamp}\n${nonceStr}\n${requestBody}\n`;
    const signature = createSign('RSA-SHA256')
      .update(message)
      .end()
      .sign(this.getPrivateKey(), 'base64');

    const authorization = `WECHATPAY2-SHA256-RSA2048 ${[
      `mchid="${this.getRequiredConfig('WECHAT_PAY_MERCHANT_ID')}"`,
      `nonce_str="${nonceStr}"`,
      `signature="${signature}"`,
      `timestamp="${timestamp}"`,
      `serial_no="${this.getRequiredConfig('WECHAT_PAY_SERIAL_NO')}"`,
    ].join(',')}`;

    const response = await fetch(`${apiBase}${path}`, {
      method,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        Authorization: authorization,
      },
      body: requestBody || undefined,
      signal: AbortSignal.timeout(15000),
    });

    const text = await response.text();
    const parsed = this.parseJsonSafely(text);

    if (!response.ok) {
      throw new BadRequestException(
        `WeChat Pay request failed: HTTP ${response.status} ${parsed?.message || text || ''}`.trim(),
      );
    }

    return parsed;
  }

  private parseJsonSafely(text: string): Record<string, any> {
    if (!text) {
      return {};
    }

    try {
      return JSON.parse(text);
    } catch {
      return {};
    }
  }

  private verifyNotificationSignature(
    headers: Record<string, string | string[] | undefined>,
    rawBody: string,
  ): void {
    const signature = this.getHeader(headers, 'wechatpay-signature');
    const timestamp = this.getHeader(headers, 'wechatpay-timestamp');
    const nonce = this.getHeader(headers, 'wechatpay-nonce');

    if (!signature || !timestamp || !nonce) {
      throw new BadRequestException('Missing WeChat Pay signature headers');
    }

    const publicKey = this.getPublicKey();
    const message = `${timestamp}\n${nonce}\n${rawBody}\n`;
    const verified = createVerify('RSA-SHA256')
      .update(message)
      .end()
      .verify(publicKey, signature, 'base64');

    if (!verified) {
      throw new BadRequestException('Invalid WeChat Pay signature');
    }
  }

  private getHeader(
    headers: Record<string, string | string[] | undefined>,
    key: string,
  ): string | undefined {
    const raw = headers[key] || headers[key.toLowerCase()] || headers[key.toUpperCase()];
    return Array.isArray(raw) ? raw[0] : raw;
  }

  private getPrivateKey(): KeyObject {
    if (!this.privateKey) {
      const pem = this.readPemFromConfig(
        'WECHAT_PAY_PRIVATE_KEY',
        'WECHAT_PAY_PRIVATE_KEY_PATH',
      );
      this.privateKey = createPrivateKey(pem);
    }

    return this.privateKey;
  }

  private getPublicKey(): KeyObject {
    if (!this.publicKey) {
      const pem = this.readPemFromConfig(
        'WECHAT_PAY_PUBLIC_KEY',
        'WECHAT_PAY_PUBLIC_KEY_PATH',
      );
      this.publicKey = createPublicKey(pem);
    }

    return this.publicKey;
  }

  private readPemFromConfig(rawKeyEnv: string, pathEnv: string): string {
    const inlineValue = this.configService.get<string>(rawKeyEnv);
    if (inlineValue) {
      return inlineValue.replace(/\\n/g, '\n');
    }

    const filePath = this.configService.get<string>(pathEnv);
    if (!filePath) {
      throw new InternalServerErrorException(`${rawKeyEnv} or ${pathEnv} must be configured`);
    }

    const resolvedPath = resolve(process.cwd(), filePath);
    if (!existsSync(resolvedPath)) {
      throw new InternalServerErrorException(`Configured PEM file not found: ${resolvedPath}`);
    }

    return readFileSync(resolvedPath, 'utf8');
  }

  private getRequiredConfig(key: string): string {
    const value = this.configService.get<string>(key);
    if (!value) {
      throw new InternalServerErrorException(`${key} is not configured`);
    }
    return value;
  }
}
