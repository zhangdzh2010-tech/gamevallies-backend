import { Controller, Post, Body, UseGuards, Get, Req, HttpCode, HttpStatus } from '@nestjs/common';
import { Request } from 'express';
import { IsIn, IsString, MaxLength, MinLength } from 'class-validator';
import { AuthService } from './auth.service';
import { RegisterDto, LoginDto, RefreshDto, AuthResponse, AuthRefreshResponse } from './dto';
import { JwtAuthGuard } from './jwt-auth.guard';
import { ok, presentUser } from '../common/api-response';

class SendCodeDto {
  @IsString()
  target: string;

  @IsIn(['register', 'reset_password', 'verify'])
  type: 'register' | 'reset_password' | 'verify';
}

class VerifyCodeDto {
  @IsString()
  target: string;

  @IsString()
  code: string;
}

class ResetPasswordDto {
  @IsString()
  target: string;

  @IsString()
  code: string;

  @IsString()
  @MinLength(8)
  @MaxLength(128)
  newPassword: string;
}

class ChangePasswordDto {
  @IsString()
  currentPassword: string;

  @IsString()
  @MinLength(8)
  @MaxLength(128)
  newPassword: string;
}

@Controller('auth')
export class AuthController {
  constructor(private authService: AuthService) {}

  @Post('register')
  @HttpCode(HttpStatus.CREATED)
  async register(@Body() dto: RegisterDto) {
    const result = await this.authService.register(dto);
    return ok({
      user: presentUser(result.user),
    });
  }

  @Post('login')
  @HttpCode(HttpStatus.OK)
  async login(@Body() dto: LoginDto) {
    const result = await this.authService.login(dto);
    return ok({
      token: result.accessToken,
      refreshToken: result.refreshToken,
      user: presentUser(result.user),
    });
  }

  @Post('refresh')
  @HttpCode(HttpStatus.OK)
  async refresh(@Body() dto: RefreshDto) {
    const result = await this.authService.refreshToken(dto.refreshToken);
    return ok({
      token: result.accessToken,
      refreshToken: result.refreshToken,
    });
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

  @Post('send-code')
  @HttpCode(HttpStatus.OK)
  async sendCode(@Body() dto: SendCodeDto) {
    await this.authService.sendVerificationCode(dto.target, dto.type);
    return ok(null);
  }

  @Post('verify-code')
  @HttpCode(HttpStatus.OK)
  async verifyCode(@Body() dto: VerifyCodeDto) {
    return ok(await this.authService.verifyCode(dto.target, dto.code));
  }

  @Post('reset-password')
  @HttpCode(HttpStatus.OK)
  async resetPassword(@Body() dto: ResetPasswordDto) {
    await this.authService.resetPassword(dto.target, dto.code, dto.newPassword);
    return ok(null);
  }

  @Post('change-password')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async changePassword(@Req() req: any, @Body() dto: ChangePasswordDto) {
    await this.authService.changePassword(req.user.userId, dto.currentPassword, dto.newPassword);
    return ok(null);
  }

  @Get('profile')
  @UseGuards(JwtAuthGuard)
  async getProfile(@Req() req: any) {
    const user = await this.authService.validateUser(req.user.userId);
    return ok(presentUser(user));
  }
}
