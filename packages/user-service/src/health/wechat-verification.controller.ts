import { Controller, Get, Header, HttpCode, HttpStatus } from '@nestjs/common';

const WECHAT_DOMAIN_VERIFICATIONS = {
  '33zqDBay4T.txt': '5142b16983df09708831078604fbcfeb',
  '9002299597e76e062cb56930926ed40c.txt': '1f4c0e10a205fa9a13b66a4d87eab953ba98f63b',
} as const;

@Controller()
export class WechatVerificationController {
  @Get('33zqDBay4T.txt')
  @HttpCode(HttpStatus.OK)
  @Header('Content-Type', 'text/plain; charset=utf-8')
  getLegacyVerificationFile(): string {
    return WECHAT_DOMAIN_VERIFICATIONS['33zqDBay4T.txt'];
  }

  @Get('9002299597e76e062cb56930926ed40c.txt')
  @HttpCode(HttpStatus.OK)
  @Header('Content-Type', 'text/plain; charset=utf-8')
  getVerificationFile(): string {
    return WECHAT_DOMAIN_VERIFICATIONS['9002299597e76e062cb56930926ed40c.txt'];
  }
}
