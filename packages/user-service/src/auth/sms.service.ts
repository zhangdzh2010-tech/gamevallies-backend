/**
 * 阿里云短信服务（Dysmsapi）
 *
 * 使用官方 SDK @alicloud/dysmsapi20170525
 *
 * 环境变量:
 *   ALIYUN_ACCESS_KEY_ID       - AccessKey ID
 *   ALIYUN_ACCESS_KEY_SECRET   - AccessKey Secret
 *   ALIYUN_SMS_SIGN_NAME       - 短信签名（如"智了科技"）
 *   ALIYUN_SMS_REGION_ID       - 区域（默认 cn-hangzhou）
 *   ALIYUN_SMS_TPL_REGISTER    - 注册模板 Code
 *   ALIYUN_SMS_TPL_LOGIN       - 登录模板 Code
 */
import { Injectable, Logger } from '@nestjs/common';

import Dysmsapi20170525, {
  SendSmsRequest,
} from '@alicloud/dysmsapi20170525';
import * as OpenApi from '@alicloud/openapi-client';

type SmsPurpose = 'register' | 'login' | 'reset_password';

@Injectable()
export class SmsService {
  private readonly logger = new Logger(SmsService.name);
  private client: Dysmsapi20170525 | null = null;
  private readonly signName: string;
  private readonly tplRegister: string;
  private readonly tplLogin: string;
  private readonly mockMode: boolean;

  constructor() {
    const ak = process.env.ALIYUN_ACCESS_KEY_ID || '';
    const sk = process.env.ALIYUN_ACCESS_KEY_SECRET || '';
    this.signName = process.env.ALIYUN_SMS_SIGN_NAME || '智了科技';
    this.tplRegister = process.env.ALIYUN_SMS_TPL_REGISTER || 'SMS_503430059';
    this.tplLogin = process.env.ALIYUN_SMS_TPL_LOGIN || 'SMS_503470064';

    this.mockMode = !ak || !sk;
    if (this.mockMode) {
      this.logger.warn('SMS_MOCK_MODE: 阿里云短信未配置 — 验证码仅打印到日志');
    } else {
      this.client = new Dysmsapi20170525(
        new OpenApi.Config({
          accessKeyId: ak,
          accessKeySecret: sk,
          endpoint: 'dysmsapi.aliyuncs.com',
          regionId: process.env.ALIYUN_SMS_REGION_ID || 'cn-hangzhou',
        }),
      );
    }
  }

  /**
   * 根据用途选择模板 Code
   *   register      → 注册模板
   *   login / 其他  → 登录模板（复用）
   */
  private templateFor(purpose: SmsPurpose): string {
    return purpose === 'register' ? this.tplRegister : this.tplLogin;
  }

  async sendCode(phone: string, code: string, purpose: SmsPurpose = 'login'): Promise<void> {
    if (this.mockMode) {
      this.logger.log(`[SMS MOCK] phone=${phone} code=${code} purpose=${purpose}`);
      return;
    }

    const templateCode = this.templateFor(purpose);
    this.logger.log(`SMS send → phone=${phone} tpl=${templateCode} purpose=${purpose}`);

    const request = new SendSmsRequest({
      phoneNumbers: phone,
      signName: this.signName,
      templateCode,
      templateParam: JSON.stringify({ code }),
    });

    try {
      const response = await this.client!.sendSms(request);
      const body = response.body;

      if (body?.code !== 'OK') {
        const msg = this.mapError(body?.code, body?.message);
        this.logger.error(`SMS failed: code=${body?.code} message=${body?.message}`);
        throw new Error(msg);
      }

      this.logger.log(`SMS sent OK → bizId=${body.bizId} requestId=${body.requestId}`);
    } catch (err: any) {
      if (err.message && !err.message.startsWith('短信')) {
        this.logger.error(`SMS SDK error: ${err.message}`);
        throw new Error('短信发送失败，请稍后重试');
      }
      throw err;
    }
  }

  private mapError(code?: string, message?: string): string {
    switch (code) {
      case 'isv.BUSINESS_LIMIT_CONTROL':
        return '发送过于频繁，请稍后再试';
      case 'isv.MOBILE_NUMBER_ILLEGAL':
        return '手机号格式不正确';
      case 'isv.SMS_SIGNATURE_ILLEGAL':
        return '短信签名不合法';
      case 'isv.SMS_TEMPLATE_ILLEGAL':
        return '短信模板不合法';
      case 'isv.INVALID_PARAMETERS':
        return '短信参数错误';
      default:
        return message || '短信发送失败，请稍后重试';
    }
  }
}
