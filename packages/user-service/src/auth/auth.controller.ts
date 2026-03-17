import { Controller, Post, Body, UseGuards, Get, Req, HttpCode, HttpStatus } from '@nestjs/common';
import { Request } from 'express';
import { IsIn, IsString, MaxLength, MinLength, Matches, IsOptional } from 'class-validator';
import { AuthService } from './auth.service';
import { RefreshDto } from './dto';
import { JwtAuthGuard } from './jwt-auth.guard';
import { ok, presentUser } from '../common/api-response';

// ── DTO ────────────────────────────────────────────────────────

class PasswordLoginDto {
  @IsString()
  account: string; // 手机号或用户名

  @IsString()
  @MinLength(6)
  @MaxLength(128)
  password: string;
}

class SendSmsCodeDto {
  @IsString()
  @Matches(/^1[3-9]\d{9}$/, { message: '请输入正确的手机号码' })
  phone: string;

  @IsIn(['register', 'login', 'reset_password'])
  type: 'register' | 'login' | 'reset_password';
}

class PhoneRegisterDto {
  @IsString()
  @Matches(/^1[3-9]\d{9}$/, { message: '请输入正确的手机号码' })
  phone: string;

  @IsString()
  @MinLength(6)
  @MaxLength(6)
  smsCode: string;

  @IsString()
  @IsOptional()
  @MaxLength(20)
  nickname?: string;

  @IsString()
  @IsOptional()
  @MinLength(6)
  @MaxLength(128)
  password?: string;
}

class PhoneLoginDto {
  @IsString()
  @Matches(/^1[3-9]\d{9}$/, { message: '请输入正确的手机号码' })
  phone: string;

  @IsString()
  @MinLength(6)
  @MaxLength(6)
  smsCode: string;
}

// ── Controller ─────────────────────────────────────────────────

@Controller('auth')
export class AuthController {
  constructor(private authService: AuthService) {}

  // 密码登录
  @Post('login')
  @HttpCode(HttpStatus.OK)
  async login(@Body() dto: PasswordLoginDto) {
    const result = await this.authService.loginByPassword(dto.account, dto.password);
    return ok({ token: result.accessToken, refreshToken: result.refreshToken, user: presentUser(result.user) });
  }

  @Post('refresh')
  @HttpCode(HttpStatus.OK)
  async refresh(@Body() dto: RefreshDto) {
    const result = await this.authService.refreshToken(dto.refreshToken);
    return ok({ token: result.accessToken, refreshToken: result.refreshToken });
  }

  @Post('logout')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async logout(@Req() req: Request) {
    const refreshToken = req.headers['x-refresh-token'] as string;
    if (refreshToken) {
      await this.authService.revokeRefreshToken(refreshToken);
    }
    return ok(null);
  }

  // 短信接口
  @Post('sms/send-code')
  @HttpCode(HttpStatus.OK)
  async sendSmsCode(@Body() dto: SendSmsCodeDto) {
    await this.authService.sendSmsCode(dto.phone, dto.type);
    return ok(null);
  }

  @Post('sms/register')
  @HttpCode(HttpStatus.CREATED)
  async registerByPhone(@Body() dto: PhoneRegisterDto) {
    const result = await this.authService.registerByPhone(dto.phone, dto.smsCode, dto.nickname || '', dto.password);
    return ok({ token: result.accessToken, refreshToken: result.refreshToken, user: presentUser(result.user) });
  }

  @Post('sms/login')
  @HttpCode(HttpStatus.OK)
  async loginByPhone(@Body() dto: PhoneLoginDto) {
    const result = await this.authService.loginByPhone(dto.phone, dto.smsCode);
    return ok({ token: result.accessToken, refreshToken: result.refreshToken, user: presentUser(result.user) });
  }

  @Get('profile')
  @UseGuards(JwtAuthGuard)
  async getProfile(@Req() req: any) {
    const user = await this.authService.validateUser(req.user.userId);
    return ok(presentUser(user));
  }
}
