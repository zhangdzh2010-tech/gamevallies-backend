import { Test, TestingModule } from '@nestjs/testing';
import { INestApplication, ValidationPipe } from '@nestjs/common';
import { CanActivate, ExecutionContext } from '@nestjs/common';
import request from 'supertest';
import { AuthController } from '../src/auth/auth.controller';
import { AuthService } from '../src/auth/auth.service';
import { JwtAuthGuard } from '../src/auth/jwt-auth.guard';
import { ConflictException, UnauthorizedException } from '@nestjs/common';
import { SmsService } from '../src/auth/sms.service';

class MockJwtAuthGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const req = context.switchToHttp().getRequest();
    req.user = { userId: 'user-123', username: 'testuser' };
    return true;
  }
}

describe('AuthController', () => {
  let app: INestApplication;

  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    phone: '13800138000',
    displayName: 'Test User',
    role: 'user',
  };

  const mockAuthResponse = {
    user: mockUser,
    accessToken: 'access-token-jwt',
    refreshToken: 'refresh-token-uuid',
    expiresIn: 86400,
  };

  const mockAuthService = {
    loginByPassword: jest.fn(),
    refreshToken: jest.fn(),
    revokeRefreshToken: jest.fn(),
    sendSmsCode: jest.fn(),
    registerByPhone: jest.fn(),
    loginByPhone: jest.fn(),
    loginByWechatMiniapp: jest.fn(),
    validateUser: jest.fn(),
  };

  const mockSmsService = {
    queryDetails: jest.fn(),
  };

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      controllers: [AuthController],
      providers: [
        { provide: AuthService, useValue: mockAuthService },
        { provide: SmsService, useValue: mockSmsService },
      ],
    })
      .overrideGuard(JwtAuthGuard)
      .useClass(MockJwtAuthGuard)
      .compile();

    app = module.createNestApplication();
    app.useGlobalPipes(new ValidationPipe({ whitelist: true }));
    await app.init();

    jest.clearAllMocks();
  });

  afterEach(async () => {
    await app.close();
  });

  describe('POST /auth/login', () => {
    it('should return 200 and tokens on successful password login', async () => {
      mockAuthService.loginByPassword.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/login')
        .send({
          account: 'testuser',
          password: 'SecurePass123!',
        })
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('token');
      expect(response.body.data).toHaveProperty('refreshToken');
      expect(response.body.data).toHaveProperty('user');
    });

    it('should return 401 for invalid credentials', async () => {
      mockAuthService.loginByPassword.mockRejectedValueOnce(
        new UnauthorizedException('账号或密码错误'),
      );

      await request(app.getHttpServer())
        .post('/auth/login')
        .send({
          account: 'testuser',
          password: 'WrongPassword!',
        })
        .expect(401);
    });
  });

  describe('POST /auth/sms/send-code', () => {
    it('should return 200 on successful sms send', async () => {
      mockAuthService.sendSmsCode.mockResolvedValueOnce(undefined);

      const response = await request(app.getHttpServer())
        .post('/auth/sms/send-code')
        .send({
          phone: '13800138000',
          type: 'login',
        })
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(mockAuthService.sendSmsCode).toHaveBeenCalledWith('13800138000', 'login');
    });
  });

  describe('POST /auth/sms/register', () => {
    it('should return 201 and user data on successful phone registration', async () => {
      mockAuthService.registerByPhone.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/sms/register')
        .send({
          phone: '13800138000',
          smsCode: '123456',
          nickname: 'New User',
          password: 'SecurePass123!',
        })
        .expect(201);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('user');
    });

    it('should return 409 when phone already exists', async () => {
      mockAuthService.registerByPhone.mockRejectedValueOnce(
        new ConflictException('该手机号已注册，请直接登录'),
      );

      await request(app.getHttpServer())
        .post('/auth/sms/register')
        .send({
          phone: '13800138000',
          smsCode: '123456',
          nickname: 'New User',
          password: 'SecurePass123!',
        })
        .expect(409);
    });
  });

  describe('POST /auth/sms/login', () => {
    it('should return 200 on successful sms login', async () => {
      mockAuthService.loginByPhone.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/sms/login')
        .send({
          phone: '13800138000',
          smsCode: '123456',
        })
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('token');
      expect(response.body.data).toHaveProperty('user');
    });

    it('should return 401 for invalid sms login', async () => {
      mockAuthService.loginByPhone.mockRejectedValueOnce(
        new UnauthorizedException('验证码错误'),
      );

      await request(app.getHttpServer())
        .post('/auth/sms/login')
        .send({
          phone: '13800138000',
          smsCode: '123456',
        })
        .expect(401);
    });
  });

  describe('POST /auth/wechat/miniapp-login', () => {
    it('should return 200 on successful wechat login', async () => {
      mockAuthService.loginByWechatMiniapp.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/wechat/miniapp-login')
        .send({
          code: 'wechat-login-code',
          nickname: 'Test User',
          avatarUrl: 'https://example.com/avatar.png',
        })
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('token');
    });
  });

  describe('POST /auth/refresh', () => {
    it('should return 200 and new tokens on successful refresh', async () => {
      mockAuthService.refreshToken.mockResolvedValueOnce({
        accessToken: 'new-access-token',
        refreshToken: 'new-refresh-uuid',
        expiresIn: 86400,
      });

      const response = await request(app.getHttpServer())
        .post('/auth/refresh')
        .send({ refreshToken: 'valid-refresh-token' })
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('token');
      expect(response.body.data).toHaveProperty('refreshToken');
    });
  });

  describe('POST /auth/logout', () => {
    it('should return 200 on successful logout with refresh token header', async () => {
      mockAuthService.revokeRefreshToken.mockResolvedValueOnce(undefined);

      const response = await request(app.getHttpServer())
        .post('/auth/logout')
        .set('x-refresh-token', 'valid-refresh-token')
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(mockAuthService.revokeRefreshToken).toHaveBeenCalledWith('valid-refresh-token');
    });
  });
});
