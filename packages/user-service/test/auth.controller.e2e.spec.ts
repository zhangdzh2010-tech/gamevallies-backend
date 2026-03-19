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
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return false;
    }
    req.user = { userId: 'user-123', username: 'testuser' };
    return true;
  }
}

describe('AuthController (E2E)', () => {
  let app: INestApplication;

  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    phone: '13800138000',
    displayName: 'Test User',
    role: 'user',
    createdAt: new Date(),
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

  beforeAll(async () => {
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
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe('POST /auth/sms/register', () => {
    it('should register a new phone user', async () => {
      mockAuthService.registerByPhone.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/sms/register')
        .send({
          phone: '13800138000',
          smsCode: '123456',
          nickname: 'Test User',
          password: 'password123',
        })
        .expect(201);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('user');
    });

    it('should reject duplicate phone registration', async () => {
      mockAuthService.registerByPhone.mockRejectedValueOnce(
        new ConflictException('该手机号已注册，请直接登录'),
      );

      await request(app.getHttpServer())
        .post('/auth/sms/register')
        .send({
          phone: '13800138000',
          smsCode: '123456',
          nickname: 'Test User',
          password: 'password123',
        })
        .expect(409);
    });
  });

  describe('POST /auth/login', () => {
    it('should login with username', async () => {
      mockAuthService.loginByPassword.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/login')
        .send({
          account: 'testuser',
          password: 'password123',
        })
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('token');
      expect(response.body.data.user.username).toBe('testuser');
    });

    it('should login with email', async () => {
      mockAuthService.loginByPassword.mockResolvedValueOnce(mockAuthResponse);

      await request(app.getHttpServer())
        .post('/auth/login')
        .send({
          account: 'test@example.com',
          password: 'password123',
        })
        .expect(200);
    });

    it('should not login with invalid password', async () => {
      mockAuthService.loginByPassword.mockRejectedValueOnce(
        new UnauthorizedException('账号或密码错误'),
      );

      await request(app.getHttpServer())
        .post('/auth/login')
        .send({
          account: 'testuser',
          password: 'wrongpassword',
        })
        .expect(401);
    });
  });

  describe('POST /auth/sms/login', () => {
    it('should login by phone sms code', async () => {
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
    });
  });

  describe('POST /auth/wechat/miniapp-login', () => {
    it('should login by wechat miniapp code', async () => {
      mockAuthService.loginByWechatMiniapp.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/wechat/miniapp-login')
        .send({
          code: 'wechat-code',
          nickname: 'Test User',
          avatarUrl: 'https://example.com/avatar.png',
        })
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('token');
    });
  });

  describe('POST /auth/refresh', () => {
    it('should refresh access token', async () => {
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
    });
  });

  describe('GET /auth/profile', () => {
    it('should get profile with valid token', async () => {
      mockAuthService.validateUser.mockResolvedValueOnce(mockUser);

      const response = await request(app.getHttpServer())
        .get('/auth/profile')
        .set('Authorization', 'Bearer valid-token')
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(response.body.data.username).toBe('testuser');
    });

    it('should reject without token', async () => {
      await request(app.getHttpServer())
        .get('/auth/profile')
        .expect(403);
    });
  });

  describe('POST /auth/logout', () => {
    it('should logout and revoke token', async () => {
      mockAuthService.revokeRefreshToken.mockResolvedValueOnce(undefined);

      const response = await request(app.getHttpServer())
        .post('/auth/logout')
        .set('Authorization', 'Bearer valid-token')
        .set('x-refresh-token', 'refresh-token-uuid')
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(mockAuthService.revokeRefreshToken).toHaveBeenCalledWith('refresh-token-uuid');
    });
  });
});
