import { Test, TestingModule } from '@nestjs/testing';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import {
  BadRequestException,
  ConflictException,
  UnauthorizedException,
} from '@nestjs/common';
import { AuthService } from '../src/auth/auth.service';
import { PrismaService } from '../src/prisma/prisma.service';
import { SmsService } from '../src/auth/sms.service';

const mockHash = jest.fn().mockResolvedValue('$hashed_password');
const mockCompare = jest.fn();
const mockRedisExists = jest.fn();
const mockRedisTtl = jest.fn();
const mockRedisSetex = jest.fn();
const mockRedisGet = jest.fn();
const mockRedisDel = jest.fn();

jest.mock('bcryptjs', () => ({
  hash: (...args: unknown[]) => mockHash(...args),
  compare: (...args: unknown[]) => mockCompare(...args),
}));

jest.mock('ioredis', () =>
  jest.fn().mockImplementation(() => ({
    exists: mockRedisExists,
    ttl: mockRedisTtl,
    setex: mockRedisSetex,
    get: mockRedisGet,
    del: mockRedisDel,
  })),
);

describe('AuthService', () => {
  let service: AuthService;

  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    phone: '13800138000',
    passwordHash: '$hashed_password',
    displayName: 'Test User',
    avatarUrl: null,
    wxUnionId: null,
    role: 'user',
    createdAt: new Date(),
    updatedAt: new Date(),
  };

  const mockPrismaService = {
    user: {
      findUnique: jest.fn(),
      findFirst: jest.fn(),
      create: jest.fn(),
      update: jest.fn(),
    },
    refreshToken: {
      findUnique: jest.fn(),
      create: jest.fn(),
      update: jest.fn(),
    },
  };

  const mockJwtService = {
    sign: jest.fn().mockReturnValue('mock-access-token'),
  };

  const mockConfigService = {
    get: jest.fn().mockImplementation((key: string, defaultValue?: unknown) => {
      const config: Record<string, unknown> = {
        JWT_SECRET: 'test-secret',
        JWT_EXPIRES_IN: '24h',
        'wechat.miniappAppId': 'wx-app-id',
        'wechat.miniappAppSecret': 'wx-app-secret',
      };
      return config[key] ?? defaultValue;
    }),
  };

  const mockSmsService = {
    sendCode: jest.fn(),
  };

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AuthService,
        { provide: PrismaService, useValue: mockPrismaService },
        { provide: JwtService, useValue: mockJwtService },
        { provide: ConfigService, useValue: mockConfigService },
        { provide: SmsService, useValue: mockSmsService },
      ],
    }).compile();

    service = module.get<AuthService>(AuthService);

    jest.clearAllMocks();
    mockJwtService.sign.mockReturnValue('mock-access-token');
    mockConfigService.get.mockImplementation((key: string, defaultValue?: unknown) => {
      const config: Record<string, unknown> = {
        JWT_SECRET: 'test-secret',
        JWT_EXPIRES_IN: '24h',
        'wechat.miniappAppId': 'wx-app-id',
        'wechat.miniappAppSecret': 'wx-app-secret',
      };
      return config[key] ?? defaultValue;
    });
    mockHash.mockResolvedValue('$hashed_password');
    mockRedisExists.mockResolvedValue(0);
    mockRedisTtl.mockResolvedValue(0);
    mockRedisSetex.mockResolvedValue('OK');
    mockRedisGet.mockResolvedValue(null);
    mockRedisDel.mockResolvedValue(1);
  });

  describe('loginByPassword', () => {
    it('logs in with a normalized username', async () => {
      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);
      mockCompare.mockResolvedValueOnce(true);
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.loginByPassword('TestUser', 'SecurePass123!');

      expect(mockPrismaService.user.findFirst).toHaveBeenCalledWith({
        where: {
          OR: [
            { phone: 'TestUser' },
            { username: 'testuser' },
            { email: 'testuser' },
          ],
        },
      });
      expect(result.accessToken).toBe('mock-access-token');
      expect(result.user.username).toBe('testuser');
    });

    it('throws when password is invalid', async () => {
      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);
      mockCompare.mockResolvedValueOnce(false);

      await expect(service.loginByPassword('testuser', 'wrong')).rejects.toThrow(
        UnauthorizedException,
      );
    });
  });

  describe('sendSmsCode', () => {
    it('sends a verification code and records cooldown', async () => {
      jest.spyOn(Math, 'random').mockReturnValueOnce(0.123456);

      await service.sendSmsCode('13800138000', 'register');

      expect(mockRedisSetex).toHaveBeenNthCalledWith(
        1,
        'sms:vcode:13800138000',
        600,
        JSON.stringify({ code: '211110', type: 'register' }),
      );
      expect(mockSmsService.sendCode).toHaveBeenCalledWith(
        '13800138000',
        '211110',
        'register',
      );
      expect(mockRedisSetex).toHaveBeenNthCalledWith(
        2,
        'sms:cd:13800138000',
        60,
        '1',
      );
    });

    it('rejects when requesting a code during cooldown', async () => {
      mockRedisExists.mockResolvedValueOnce(1);
      mockRedisTtl.mockResolvedValueOnce(42);

      await expect(service.sendSmsCode('13800138000', 'login')).rejects.toThrow(
        BadRequestException,
      );
      expect(mockSmsService.sendCode).not.toHaveBeenCalled();
    });

    it('cleans up the verification code when SMS delivery fails', async () => {
      jest.spyOn(Math, 'random').mockReturnValueOnce(0.123456);
      mockSmsService.sendCode.mockRejectedValueOnce(new BadRequestException('手机号格式不正确'));

      await expect(service.sendSmsCode('13800138000', 'login')).rejects.toThrow(
        BadRequestException,
      );

      expect(mockRedisSetex).toHaveBeenNthCalledWith(
        1,
        'sms:vcode:13800138000',
        600,
        JSON.stringify({ code: '211110', type: 'login' }),
      );
      expect(mockRedisDel).toHaveBeenCalledWith('sms:vcode:13800138000');
      expect(mockRedisSetex).toHaveBeenCalledTimes(1);
    });

    it('maps redis failures to service unavailable instead of a raw 500', async () => {
      mockRedisExists.mockRejectedValueOnce(new Error('redis unavailable'));

      await expect(service.sendSmsCode('13800138000', 'login')).rejects.toThrow(
        '验证码服务暂不可用，请稍后重试',
      );

      expect(mockSmsService.sendCode).not.toHaveBeenCalled();
    });
  });

  describe('registerByPhone', () => {
    it('registers a new phone user and returns tokens', async () => {
      mockRedisGet.mockResolvedValueOnce(JSON.stringify({ code: '123456', type: 'register' }));
      mockPrismaService.user.findFirst
        .mockResolvedValueOnce(null)
        .mockResolvedValueOnce(null);
      mockPrismaService.user.create.mockResolvedValueOnce({
        id: 'user-456',
        username: 'u8000',
        phone: '13800138000',
        displayName: 'New User',
        role: 'user',
      });
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.registerByPhone(
        '13800138000',
        '123456',
        'New User',
        'SecurePass123!',
      );

      expect(mockHash).toHaveBeenCalledWith('SecurePass123!', 10);
      expect(mockPrismaService.user.create).toHaveBeenCalled();
      expect(mockPrismaService.user.create.mock.calls[0][0].data.phone).toBe('13800138000');
      expect(result.accessToken).toBe('mock-access-token');
    });

    it('throws when phone is already registered', async () => {
      mockRedisGet.mockResolvedValueOnce(JSON.stringify({ code: '123456', type: 'register' }));
      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);

      await expect(
        service.registerByPhone('13800138000', '123456', 'Existing User'),
      ).rejects.toThrow(ConflictException);
    });
  });

  describe('loginByPhone', () => {
    it('logs in an existing phone user after code verification', async () => {
      mockRedisGet.mockResolvedValueOnce(JSON.stringify({ code: '123456', type: 'login' }));
      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.loginByPhone('13800138000', '123456');

      expect(mockRedisDel).toHaveBeenCalledWith('sms:vcode:13800138000');
      expect(result.user.username).toBe('testuser');
    });

    it('throws when phone user does not exist', async () => {
      mockRedisGet.mockResolvedValueOnce(JSON.stringify({ code: '123456', type: 'login' }));
      mockPrismaService.user.findFirst.mockResolvedValueOnce(null);

      await expect(service.loginByPhone('13800138000', '123456')).rejects.toThrow(
        UnauthorizedException,
      );
    });
  });

  describe('loginByWechatMiniapp', () => {
    it('creates a new wechat user when first login succeeds', async () => {
      const mockFetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          openid: 'wx-open-id-001',
          session_key: 'session-key',
        }),
      });
      (global as any).fetch = mockFetch;

      mockPrismaService.user.findFirst
        .mockResolvedValueOnce(null)
        .mockResolvedValueOnce(null);
      mockPrismaService.user.create.mockResolvedValueOnce({
        ...mockUser,
        id: 'user-wx-1',
        username: 'wx_open-id-001',
        displayName: '微信用户',
      });
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.loginByWechatMiniapp('wx-login-code', '微信用户');

      expect(mockFetch).toHaveBeenCalledTimes(1);
      expect(mockPrismaService.user.create).toHaveBeenCalled();
      expect(result.user.id).toBe('user-wx-1');
    });
  });

  describe('refreshToken', () => {
    it('rotates a valid refresh token', async () => {
      mockPrismaService.refreshToken.findUnique.mockResolvedValueOnce({
        id: 'token-id',
        token: 'valid-refresh-token',
        revokedAt: null,
        expiresAt: new Date(Date.now() + 60_000),
        user: mockUser,
      });
      mockPrismaService.refreshToken.update.mockResolvedValueOnce({});
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.refreshToken('valid-refresh-token');

      expect(result.accessToken).toBe('mock-access-token');
      expect(result.refreshToken).toBeDefined();
      expect(mockPrismaService.refreshToken.update).toHaveBeenCalled();
    });

    it('rejects a revoked refresh token', async () => {
      mockPrismaService.refreshToken.findUnique.mockResolvedValueOnce({
        token: 'revoked-token',
        revokedAt: new Date(),
        expiresAt: new Date(Date.now() + 60_000),
        user: mockUser,
      });

      await expect(service.refreshToken('revoked-token')).rejects.toThrow(
        UnauthorizedException,
      );
    });
  });

  describe('validateUser', () => {
    it('returns the selected user profile', async () => {
      mockPrismaService.user.findUnique.mockResolvedValueOnce({
        id: 'user-123',
        username: 'testuser',
        email: 'test@example.com',
        displayName: 'Test User',
        role: 'user',
      });

      const result = await service.validateUser('user-123');

      expect(result.username).toBe('testuser');
    });

    it('throws when the user is missing', async () => {
      mockPrismaService.user.findUnique.mockResolvedValueOnce(null);

      await expect(service.validateUser('missing-user')).rejects.toThrow(
        UnauthorizedException,
      );
    });
  });
});
