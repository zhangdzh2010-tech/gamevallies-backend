import {
  BadRequestException,
  HttpException,
  HttpStatus,
  ServiceUnavailableException,
} from '@nestjs/common';
import { SmsService } from '../src/auth/sms.service';

const mockSendSms = jest.fn();
const mockQuerySendDetails = jest.fn();

jest.mock('@alicloud/dysmsapi20170525', () => ({
  __esModule: true,
  default: jest.fn().mockImplementation(() => ({
    sendSms: mockSendSms,
    querySendDetails: mockQuerySendDetails,
  })),
  SendSmsRequest: class SendSmsRequest {
    constructor(payload: Record<string, unknown>) {
      Object.assign(this, payload);
    }
  },
  QuerySendDetailsRequest: class QuerySendDetailsRequest {
    constructor(payload: Record<string, unknown>) {
      Object.assign(this, payload);
    }
  },
}));

jest.mock('@alicloud/openapi-client', () => ({
  __esModule: true,
  Config: class Config {
    constructor(payload: Record<string, unknown>) {
      Object.assign(this, payload);
    }
  },
}));

describe('SmsService', () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    jest.clearAllMocks();
    process.env.ALIYUN_ACCESS_KEY_ID = 'test-ak';
    process.env.ALIYUN_ACCESS_KEY_SECRET = 'test-sk';
    process.env.ALIYUN_SMS_SIGN_NAME = '测试签名';
    process.env.ALIYUN_SMS_TPL_REGISTER = 'TPL_REGISTER';
    process.env.ALIYUN_SMS_TPL_LOGIN = 'TPL_LOGIN';
    delete process.env.ALIYUN_SMS_TPL_RESET_PASSWORD;
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  it('maps invalid phone errors to 400-level exceptions', async () => {
    mockSendSms.mockResolvedValueOnce({
      body: {
        code: 'isv.MOBILE_NUMBER_ILLEGAL',
        message: 'mobile number illegal',
      },
    });

    const service = new SmsService();

    await expect(service.sendCode('13800138000', '123456', 'login')).rejects.toThrow(
      BadRequestException,
    );
  });

  it('maps provider throttling to 429', async () => {
    mockSendSms.mockResolvedValueOnce({
      body: {
        code: 'isv.BUSINESS_LIMIT_CONTROL',
        message: 'limit control',
      },
    });

    const service = new SmsService();

    try {
      await service.sendCode('13800138000', '123456', 'login');
      fail('expected sendCode to throw');
    } catch (error: any) {
      expect(error).toBeInstanceOf(HttpException);
      expect(error.getStatus()).toBe(HttpStatus.TOO_MANY_REQUESTS);
    }
  });

  it('maps missing configuration to 503', async () => {
    delete process.env.ALIYUN_ACCESS_KEY_ID;
    delete process.env.ALIYUN_ACCESS_KEY_SECRET;

    const service = new SmsService();

    await expect(service.sendCode('13800138000', '123456', 'login')).rejects.toThrow(
      ServiceUnavailableException,
    );
  });

  it('uses a dedicated reset-password template when configured', async () => {
    process.env.ALIYUN_SMS_TPL_RESET_PASSWORD = 'TPL_RESET';
    mockSendSms.mockResolvedValueOnce({
      body: {
        code: 'OK',
        message: 'OK',
      },
    });

    const service = new SmsService();

    await service.sendCode('13800138000', '123456', 'reset_password');

    expect(mockSendSms).toHaveBeenCalledWith(
      expect.objectContaining({
        templateCode: 'TPL_RESET',
      }),
    );
  });

  it('maps SDK transport failures to 503', async () => {
    mockSendSms.mockRejectedValueOnce(new Error('socket hang up'));

    const service = new SmsService();

    await expect(service.sendCode('13800138000', '123456', 'login')).rejects.toThrow(
      ServiceUnavailableException,
    );
  });
});
