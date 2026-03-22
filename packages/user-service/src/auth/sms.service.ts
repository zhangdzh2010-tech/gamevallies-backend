/**
 * Aliyun SMS service.
 */
import {
  BadRequestException,
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';

import Dysmsapi20170525, {
  SendSmsRequest,
  QuerySendDetailsRequest,
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
  private readonly tplResetPassword: string;

  constructor() {
    const ak = process.env.ALIYUN_ACCESS_KEY_ID || '';
    const sk = process.env.ALIYUN_ACCESS_KEY_SECRET || '';
    this.signName = process.env.ALIYUN_SMS_SIGN_NAME || '智了科技';
    this.tplRegister = process.env.ALIYUN_SMS_TPL_REGISTER || 'SMS_503430059';
    this.tplLogin = process.env.ALIYUN_SMS_TPL_LOGIN || 'SMS_503470064';
    this.tplResetPassword = process.env.ALIYUN_SMS_TPL_RESET_PASSWORD || this.tplLogin;

    if (!ak || !sk) {
      this.logger.error(
        'SMS DISABLED: ALIYUN_ACCESS_KEY_ID / ALIYUN_ACCESS_KEY_SECRET not configured.',
      );
    } else {
      this.client = new Dysmsapi20170525(
        new OpenApi.Config({
          accessKeyId: ak,
          accessKeySecret: sk,
          endpoint: 'dysmsapi.aliyuncs.com',
          regionId: process.env.ALIYUN_SMS_REGION_ID || 'cn-hangzhou',
        }),
      );
      this.logger.log(
        `SMS READY: sign="${this.signName}" register=${this.tplRegister} login=${this.tplLogin} reset=${this.tplResetPassword}`,
      );
    }
  }

  private templateFor(purpose: SmsPurpose): string {
    switch (purpose) {
      case 'register':
        return this.tplRegister;
      case 'reset_password':
        return this.tplResetPassword;
      case 'login':
      default:
        return this.tplLogin;
    }
  }

  async sendCode(phone: string, code: string, purpose: SmsPurpose = 'login'): Promise<void> {
    if (!this.client) {
      this.logger.error(`SMS NOT CONFIGURED - cannot send to ${phone}`);
      throw new ServiceUnavailableException('短信服务暂不可用，请稍后重试');
    }

    const templateCode = this.templateFor(purpose);
    this.logger.log(
      `SMS sending -> phone=${phone} sign="${this.signName}" tpl=${templateCode} purpose=${purpose}`,
    );

    const request = new SendSmsRequest({
      phoneNumbers: phone,
      signName: this.signName,
      templateCode,
      templateParam: JSON.stringify({ code }),
    });

    try {
      const response = await this.client.sendSms(request);
      const body = response.body;

      this.logger.log(
        `SMS response -> Code=${body?.code} Message=${body?.message} BizId=${body?.bizId} RequestId=${body?.requestId}`,
      );

      if (body?.code !== 'OK') {
        this.logger.error(
          `SMS FAILED: code=${body?.code} message=${body?.message} requestId=${body?.requestId}`,
        );
        throw this.toHttpException(body?.code, body?.message);
      }

      this.logger.log(`SMS sent OK -> bizId=${body?.bizId}`);
    } catch (err: any) {
      if (err instanceof HttpException) {
        throw err;
      }

      const sdkCode = err?.data?.Code || err?.code;
      const sdkMessage = err?.data?.Message || err?.message;
      if (sdkCode || sdkMessage) {
        this.logger.error(
          `SMS SDK upstream error: code=${sdkCode || 'unknown'} message=${sdkMessage || 'unknown'}`,
          err?.stack,
        );
        throw this.toHttpException(sdkCode, sdkMessage);
      }

      this.logger.error(`SMS SDK error: ${err?.message || err}`, err?.stack);
      throw new ServiceUnavailableException('短信发送失败，请稍后重试');
    }
  }

  async queryDetails(phone: string, sendDate: string) {
    if (!this.client) return [];

    try {
      const response = await this.client.querySendDetails(
        new QuerySendDetailsRequest({
          phoneNumber: phone,
          sendDate,
          pageSize: 10,
          currentPage: 1,
        }),
      );
      return response.body?.smsSendDetailDTOs?.smsSendDetailDTO || [];
    } catch (err: any) {
      this.logger.error(`QuerySendDetails error: ${err.message}`);
      return [];
    }
  }

  private toHttpException(code?: string, message?: string): HttpException {
    switch (code) {
      case 'isv.BUSINESS_LIMIT_CONTROL':
        return new HttpException('发送过于频繁，请稍后再试', HttpStatus.TOO_MANY_REQUESTS);
      case 'isv.MOBILE_NUMBER_ILLEGAL':
        return new BadRequestException('手机号格式不正确');
      case 'isv.INVALID_PARAMETERS':
        return new BadRequestException('短信参数错误');
      case 'isv.SMS_SIGNATURE_ILLEGAL':
      case 'isv.SMS_TEMPLATE_ILLEGAL':
      case 'isv.AMOUNT_NOT_ENOUGH':
      case 'isv.PRODUCT_UN_SUBSCRIPT':
      case 'isp.RAM_PERMISSION_DENY':
        return new ServiceUnavailableException(message || '短信服务暂不可用，请稍后重试');
      default:
        return new ServiceUnavailableException(message || '短信发送失败，请稍后重试');
    }
  }
}
