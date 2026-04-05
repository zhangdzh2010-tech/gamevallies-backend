import {
  BadRequestException,
  Injectable,
  InternalServerErrorException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import {
  KeyObject,
  createPrivateKey,
  createPublicKey,
  createSign,
  createVerify,
} from 'crypto';
import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';

export type AlipayTradeFlow = 'wap' | 'page';

type AlipayCreatePaymentInput = {
  flow: AlipayTradeFlow;
  description: string;
  outTradeNo: string;
  amount: number;
  notifyUrl: string;
  returnUrl?: string;
};

type AlipayCreatePaymentResult = {
  payment: {
    provider: 'alipay';
    flow: AlipayTradeFlow;
    payUrl: string;
  };
  rawResponse: Record<string, unknown>;
};

type AlipayNotificationPayload = Record<string, string>;

const DEFAULT_GATEWAY = 'https://openapi.alipay.com/gateway.do';

@Injectable()
export class AlipayPayService {
  private privateKey?: KeyObject;

  private publicKey?: KeyObject;

  constructor(private readonly configService: ConfigService) {}

  isMockMode(): boolean {
    return (this.configService.get<string>('ALIPAY_MODE') || '').toLowerCase() === 'mock';
  }

  async createPayment(input: AlipayCreatePaymentInput): Promise<AlipayCreatePaymentResult> {
    if (this.isMockMode()) {
      return this.createMockPayment(input);
    }

    return this.createRealPayment(input);
  }

  async parsePaidNotification(
    payload: Record<string, string | string[] | undefined>,
  ): Promise<AlipayNotificationPayload> {
    const normalized = this.normalizeNotificationPayload(payload);

    if (this.isMockMode() && normalized.mock === 'true') {
      return normalized;
    }

    const signature = normalized.sign;
    if (!signature) {
      throw new BadRequestException('Alipay notification missing sign');
    }

    const signContent = this.buildSignContent(normalized);
    const verified = createVerify('RSA-SHA256')
      .update(signContent, 'utf8')
      .end()
      .verify(this.getPublicKey(), signature, 'base64');

    if (!verified) {
      throw new BadRequestException('Invalid Alipay signature');
    }

    return normalized;
  }

  private async createMockPayment(
    input: AlipayCreatePaymentInput,
  ): Promise<AlipayCreatePaymentResult> {
    const payUrl = `https://pay.mock.gamevallies.com/alipay/${input.flow}/${input.outTradeNo}`;
    return {
      payment: {
        provider: 'alipay',
        flow: input.flow,
        payUrl,
      },
      rawResponse: {
        mock: true,
        flow: input.flow,
        pay_url: payUrl,
      },
    };
  }

  private async createRealPayment(
    input: AlipayCreatePaymentInput,
  ): Promise<AlipayCreatePaymentResult> {
    const method = input.flow === 'page'
      ? 'alipay.trade.page.pay'
      : 'alipay.trade.wap.pay';
    const bizContent = {
      out_trade_no: input.outTradeNo,
      total_amount: this.formatAmount(input.amount),
      subject: input.description.slice(0, 128),
      product_code: input.flow === 'page'
        ? 'FAST_INSTANT_TRADE_PAY'
        : 'QUICK_WAP_WAY',
    };

    const params: Record<string, string> = {
      app_id: this.getRequiredConfig('ALIPAY_APP_ID'),
      method,
      format: 'JSON',
      charset: 'utf-8',
      sign_type: this.getRequiredConfig('ALIPAY_SIGN_TYPE', 'RSA2'),
      timestamp: this.formatTimestamp(new Date()),
      version: '1.0',
      notify_url: input.notifyUrl,
      biz_content: JSON.stringify(bizContent),
    };

    if (input.returnUrl) {
      params.return_url = input.returnUrl;
    }

    const signContent = this.buildSignContent(params);
    const signature = createSign('RSA-SHA256')
      .update(signContent, 'utf8')
      .end()
      .sign(this.getPrivateKey(), 'base64');

    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      searchParams.set(key, value);
    }
    searchParams.set('sign', signature);

    const payUrl = `${this.resolveGateway()}?${searchParams.toString()}`;
    return {
      payment: {
        provider: 'alipay',
        flow: input.flow,
        payUrl,
      },
      rawResponse: {
        flow: input.flow,
        gateway: this.resolveGateway(),
        pay_url: payUrl,
        signed_params: {
          ...params,
          sign: signature,
        },
      },
    };
  }

  private normalizeNotificationPayload(
    payload: Record<string, string | string[] | undefined>,
  ): AlipayNotificationPayload {
    const normalized: AlipayNotificationPayload = {};
    for (const [key, value] of Object.entries(payload || {})) {
      if (Array.isArray(value)) {
        if (value[0] != null) {
          normalized[key] = String(value[0]);
        }
        continue;
      }

      if (value != null) {
        normalized[key] = String(value);
      }
    }

    return normalized;
  }

  private buildSignContent(params: Record<string, string>): string {
    return Object.entries(params)
      .filter(([key, value]) => key !== 'sign' && value !== '')
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, value]) => `${key}=${value}`)
      .join('&');
  }

  private formatAmount(amountInCents: number): string {
    return (amountInCents / 100).toFixed(2);
  }

  private formatTimestamp(date: Date): string {
    const year = date.getFullYear();
    const month = `${date.getMonth() + 1}`.padStart(2, '0');
    const day = `${date.getDate()}`.padStart(2, '0');
    const hours = `${date.getHours()}`.padStart(2, '0');
    const minutes = `${date.getMinutes()}`.padStart(2, '0');
    const seconds = `${date.getSeconds()}`.padStart(2, '0');
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
  }

  private resolveGateway(): string {
    return (this.configService.get<string>('ALIPAY_GATEWAY') || DEFAULT_GATEWAY).replace(/\/$/, '');
  }

  private getPrivateKey(): KeyObject {
    if (!this.privateKey) {
      const pem = this.readPemFromConfig(
        'ALIPAY_PRIVATE_KEY',
        'ALIPAY_PRIVATE_KEY_PATH',
        'private',
      );
      this.privateKey = createPrivateKey(pem);
    }

    return this.privateKey;
  }

  private getPublicKey(): KeyObject {
    if (!this.publicKey) {
      const pem = this.readPemFromConfig(
        'ALIPAY_PUBLIC_KEY',
        'ALIPAY_PUBLIC_KEY_PATH',
        'public',
      );
      this.publicKey = createPublicKey(pem);
    }

    return this.publicKey;
  }

  private readPemFromConfig(
    rawKeyEnv: string,
    pathEnv: string,
    kind: 'private' | 'public',
  ): string {
    const inlineValue = this.configService.get<string>(rawKeyEnv);
    if (inlineValue) {
      return this.normalizePem(inlineValue, kind);
    }

    const filePath = this.configService.get<string>(pathEnv);
    if (!filePath) {
      throw new InternalServerErrorException(`${rawKeyEnv} or ${pathEnv} must be configured`);
    }

    const resolvedPath = resolve(process.cwd(), filePath);
    if (!existsSync(resolvedPath)) {
      throw new InternalServerErrorException(`Configured PEM file not found: ${resolvedPath}`);
    }

    return this.normalizePem(readFileSync(resolvedPath, 'utf8'), kind);
  }

  private normalizePem(value: string, kind: 'private' | 'public'): string {
    const normalized = value.replace(/\\n/g, '\n').trim();
    if (normalized.includes('BEGIN')) {
      return normalized;
    }

    const body = normalized.replace(/\s+/g, '');
    const chunks = body.match(/.{1,64}/g)?.join('\n') || body;
    if (kind === 'private') {
      return `-----BEGIN PRIVATE KEY-----\n${chunks}\n-----END PRIVATE KEY-----`;
    }

    return `-----BEGIN PUBLIC KEY-----\n${chunks}\n-----END PUBLIC KEY-----`;
  }

  private getRequiredConfig(key: string, fallback?: string): string {
    const value = this.configService.get<string>(key) || fallback;
    if (!value) {
      throw new InternalServerErrorException(`${key} is not configured`);
    }
    return value;
  }
}
