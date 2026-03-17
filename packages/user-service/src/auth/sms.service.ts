/**
 * 阿里云短信服务
 * 文档: https://help.aliyun.com/document_detail/101414.html
 * 签名算法: HMAC-SHA1 RPC 风格
 */
import { Injectable, Logger } from '@nestjs/common';
import * as crypto from 'crypto';
import * as https from 'https';
import { URL } from 'url';

function percentEncode(str: string): string {
  return encodeURIComponent(str)
    .replace(/!/g, '%21')
    .replace(/'/g, '%27')
    .replace(/\(/g, '%28')
    .replace(/\)/g, '%29')
    .replace(/\*/g, '%2A');
}

function buildSignature(
  method: string,
  params: Record<string, string>,
  accessKeySecret: string,
): string {
  const sortedKeys = Object.keys(params).sort();
  const canonicalQueryString = sortedKeys
    .map((k) => `${percentEncode(k)}=${percentEncode(params[k])}`)
    .join('&');
  const stringToSign = `${method}&${percentEncode('/')}&${percentEncode(canonicalQueryString)}`;
  return crypto
    .createHmac('sha1', accessKeySecret + '&')
    .update(stringToSign)
    .digest('base64');
}

@Injectable()
export class SmsService {
  private readonly logger = new Logger(SmsService.name);
  private readonly ak: string;
  private readonly sk: string;
  private readonly regionId: string;
  private readonly endpoint: string;
  private readonly signName: string;
  private readonly templateCode: string;
  private readonly templateParamCode: string;
  private readonly templateParamMin: string;
  private readonly mockMode: boolean;

  constructor() {
    this.ak             = process.env.ALIYUN_ACCESS_KEY_ID || '';
    this.sk             = process.env.ALIYUN_ACCESS_KEY_SECRET || '';
    this.regionId       = process.env.ALIYUN_REGION_ID || 'cn-hangzhou';
    this.endpoint       = process.env.ALIYUN_ENDPOINT || 'dypnsapi.aliyuncs.com';
    this.signName       = process.env.ALIYUN_SMS_SIGN_NAME || '';
    this.templateCode   = process.env.ALIYUN_SMS_TEMPLATE_CODE || '';
    this.templateParamCode = process.env.ALIYUN_SMS_TEMPLATE_PARAM_CODE || 'code';
    this.templateParamMin  = process.env.ALIYUN_SMS_TEMPLATE_PARAM_MIN || 'min';

    this.mockMode = !this.ak || !this.sk || !this.signName || !this.templateCode;
    if (this.mockMode) {
      this.logger.warn('SMS_MOCK_MODE: 阿里云短信未配置 — 验证码仅打印到日志');
    }
  }

  async sendCode(phone: string, code: string): Promise<void> {
    if (this.mockMode) {
      this.logger.log(`[SMS MOCK] phone=${phone} code=${code}`);
      return;
    }

    const templateParam = JSON.stringify({
      [this.templateParamCode]: code,
      [this.templateParamMin]: '10',
    });

    const timestamp = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
    const nonce = crypto.randomUUID().replace(/-/g, '');

    const params: Record<string, string> = {
      AccessKeyId:       this.ak,
      Action:            'SendSmsVerifyCode',
      Format:            'JSON',
      PhoneNumber:       phone,
      RegionId:          this.regionId,
      SignName:          this.signName,
      SignatureMethod:   'HMAC-SHA1',
      SignatureNonce:    nonce,
      SignatureVersion:  '1.0',
      TemplateCode:      this.templateCode,
      TemplateParam:     templateParam,
      Timestamp:         timestamp,
      Version:           '2017-05-25',
    };

    const signature = buildSignature('POST', params, this.sk);
    params['Signature'] = signature;

    const body = Object.keys(params)
      .map((k) => `${percentEncode(k)}=${percentEncode(params[k])}`)
      .join('&');

    await this.httpPost(`https://${this.endpoint}/`, body);
  }

  private httpPost(url: string, body: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const urlObj = new URL(url);
      const options = {
        hostname: urlObj.hostname,
        path: urlObj.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'Content-Length': Buffer.byteLength(body),
        },
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => (data += chunk));
        res.on('end', () => {
          this.logger.debug(`SMS response ${res.statusCode}: ${data}`);
          try {
            const parsed = JSON.parse(data);
            const code = parsed?.Code;
            if (code && code !== 'OK') {
              let msg = '短信发送失败，请稍后重试';
              if (code === 'isv.BUSINESS_LIMIT_CONTROL') msg = '发送过于频繁，请稍后再试';
              else if (code === 'isv.INVALID_PARAMETERS') msg = '短信参数错误';
              else if (code === 'isv.SMS_SIGNATURE_ILLEGAL') msg = '短信签名不合法';
              else if (code === 'isv.SMS_TEMPLATE_ILLEGAL') msg = '短信模板不合法';
              else if (parsed?.Message) msg = parsed.Message;
              reject(new Error(msg));
            } else {
              resolve();
            }
          } catch {
            if (res.statusCode && res.statusCode >= 400) {
              reject(new Error(`SMS HTTP ${res.statusCode}`));
            } else {
              resolve();
            }
          }
        });
      });

      req.on('error', reject);
      req.write(body);
      req.end();
    });
  }
}
