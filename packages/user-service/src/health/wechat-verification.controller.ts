import { Controller, Get, Header, HttpCode, HttpStatus } from '@nestjs/common';

const WECHAT_DOMAIN_VERIFICATION_CONTENT = '5142b16983df09708831078604fbcfeb';

@Controller()
export class WechatVerificationController {
  @Get('33zqDBay4T.txt')
  @HttpCode(HttpStatus.OK)
  @Header('Content-Type', 'text/plain; charset=utf-8')
  getVerificationFile(): string {
    return WECHAT_DOMAIN_VERIFICATION_CONTENT;
  }
}
