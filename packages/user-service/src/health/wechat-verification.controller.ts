import { Controller, Get, Header, HttpCode, HttpStatus } from '@nestjs/common';

const WECHAT_DOMAIN_VERIFICATIONS = {
  '33zqDBay4T.txt': '5142b16983df09708831078604fbcfeb',
  '8e70db656271ec4f59fc23aecfecf727.txt': 'b57d2fe8ec3015f6dac218e7f96401b033210a57',
} as const;

@Controller()
export class WechatVerificationController {
  @Get('33zqDBay4T.txt')
  @HttpCode(HttpStatus.OK)
  @Header('Content-Type', 'text/plain; charset=utf-8')
  getLegacyVerificationFile(): string {
    return WECHAT_DOMAIN_VERIFICATIONS['33zqDBay4T.txt'];
  }

  @Get('8e70db656271ec4f59fc23aecfecf727.txt')
  @HttpCode(HttpStatus.OK)
  @Header('Content-Type', 'text/plain; charset=utf-8')
  getVerificationFile(): string {
    return WECHAT_DOMAIN_VERIFICATIONS['8e70db656271ec4f59fc23aecfecf727.txt'];
  }
}
