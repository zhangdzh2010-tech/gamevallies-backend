import { Test, TestingModule } from '@nestjs/testing';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { ConflictException, UnauthorizedException } from '@nestjs/common';
import { AuthService } from '../src/auth/auth.service';
import { PrismaService } from '../src/prisma/prisma.service';
import { SmsService } from '../src/auth/sms.service';

jest.mock('bcryptjs', () => ({
  hash: jest.fn().mockResolvedValue('$hashed_password'),
  compare: jest.fn(),
}));

jest.mock('ioredis', () => {
  return jest.fn().mockImplementation(() => ({
    exists: jest.fn(),
    ttl: jest.fn(),
    setex: jest.fn(),
    get: jest.fn(),
    del: jest.fn(),
  }));
});

import * as bcrypt from 'bcryptjs';

describe('AuthService', () => {
  let service: AuthService;

  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    phone: '13800138000',
    passwordHash: '$hashed_password',
    displayName: 'Test User',
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
    sign: jest.fn().mockReturnValue('mock-token'),
  };

  const mockConfigService = {
    get: jest.fn().mockImplementation((key: string, defaultVal?: any) => {
      const config: Record<string, any> = {
        JWT_SECRET: 'test-secret',
        JWT_EXPIRES_IN: '24h',
        JWT_REFRESH_SECRET: 'test-refresh-secret',
        JWT_REFRESH_EXPIRES_IN: '7d',
        'wechat.miniappAppId': 'wx-test-app-id',
        'wechat.miniappAppSecret': 'wx-test-app-secret',
      };
      return config[key] ?? defaultVal;
    }),
  };

  const mockSmsService = {
    sendCode: jest.fn(),
    queryDetails: jest.fn(),
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

    (mockJwtService.sign as jest.Mock).mockReturnValue('mock-token');
    (mockConfigService.get as jest.Mock).mockImplementation((key: string, defaultVal?: any) => {
      const config: Record<string, any> = {
        JWT_SECRET: 'test-secret',
        JWT_EXPIRES_IN: '24h',
        JWT_REFRESH_SECRET: 'test-refresh-secret',
        JWT_REFRESH_EXPIRES_IN: '7d',
        'wechat.miniappAppId': 'wx-test-app-id',
        'wechat.miniappAppSecret': 'wx-test-app-secret',
      };
      return config[key] ?? defaultVal;
    });
    (bcrypt.hash as jest.Mock).mockResolvedValue('$hashed_password');
  });

  describe('loginByPassword', () => {
    it('should successfully login user with correct credentials', async () => {
      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);
      (bcrypt.compare as jest.Mock).mockResolvedValueOnce(true);
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.loginByPassword('test@example.com', 'SecurePass123!');

      expect(result).toHaveProperty('user');
      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
      expect(mockPrismaService.user.findFirst).toHaveBeenCalledWith({
        where: {
          OR: [
            { phone: 'test@example.com' },
            { username: 'test@example.com' },
            { email: 'test@example.com' },
          ],
        },
      });
    });

    it('should throw UnauthorizedException for wrong password', async () => {
      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);
      (bcrypt.compare as jest.Mock).mockResolvedValueOnce(false);

      await expect(service.loginByPassword('testuser', 'WrongPassword123!')).rejects.toThrow(
        UnauthorizedException,
      );
    });

    it('should throw UnauthorizedException when user not found', async () => {
      mockPrismaService.user.findFirst.mockResolvedValueOnce(null);

      await expect(service.loginByPassword('nonexistent', 'SecurePass123!')).rejects.toThrow(
        UnauthorizedException,
      );
    });
  });

  describe('registerByPhone', () => {
    it('should successfully register a new phone user', async () => {
      jest.spyOn(service as any, 'verifySmsCode').mockResolvedValueOnce(undefined);
      mockPrismaService.user.findFirst
        .mockResolvedValueOnce(null)
        .mockResolvedValueOnce(null);
      mockPrismaService.user.create.mockResolvedValueOnce({
        id: 'user-234',
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

      expect(result).toHaveProperty('user');
      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
      expect(mockPrismaService.user.create).toHaveBeenCalled();
      expect(bcrypt.hash).toHaveBeenCalledWith('SecurePass123!', 10);
    });

    it('should throw ConflictException when phone already exists', async () => {
      jest.spyOn(service as any, 'verifySmsCode').mockResolvedValueOnce(undefined);
      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);

      await expect(
        service.registerByPhone('13800138000', '123456', 'New User', 'SecurePass123!'),
      ).rejects.toThrow(ConflictException);
      expect(mockPrismaService.user.create).not.toHaveBeenCalled();
    });
  });

  describe('loginByPhone', () => {
    it('should successfully login with phone and sms code', async () => {
      jest.spyOn(service as any, 'verifySmsCode').mockResolvedValueOnce(undefined);
      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.loginByPhone('13800138000', '123456');

      expect(result).toHaveProperty('user');
      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
    });

    it('should throw UnauthorizedException when phone user not found', async () => {
      jest.spyOn(service as any, 'verifySmsCode').mockResolvedValueOnce(undefined);
      mockPrismaService.user.findFirst.mockResolvedValueOnce(null);

      await expect(service.loginByPhone('13800138000', '123456')).rejects.toThrow(
        UnauthorizedException,
      );
    });
  });

  describe('refreshToken', () => {
    it('should successfully refresh valid token', async () => {
      const refreshTokenValue = 'valid-refresh-token';

      mockPrismaService.refreshToken.findUnique.mockResolvedValueOnce({
        id: 'token-id',
        token: refreshTokenValue,
        revokedAt: null,
        expiresAt: new Date(Date.now() + 86400000),
        user: mockUser,
      });
      mockPrismaService.refreshToken.update.mockResolvedValueOnce({});
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.refreshToken(refreshTokenValue);

      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
    });

    it('should throw UnauthorizedException when token not found', async () => {
      mockPrismaService.refreshToken.findUnique.mockResolvedValueOnce(null);

      await expect(service.refreshToken('nonexistent-token')).rejects.toThrow(UnauthorizedException);
    });

    it('should throw UnauthorizedException for revoked token', async () => {
      mockPrismaService.refreshToken.findUnique.mockResolvedValueOnce({
        id: 'token-id',
        revokedAt: new Date(),
      });

      await expect(service.refreshToken('revoked-token')).rejects.toThrow(UnauthorizedException);
    });
  });

  describe('generateTokens', () => {
    it('should generate access token and UUID refresh token', async () => {
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.generateTokens({
        id: mockUser.id,
        username: mockUser.username,
        email: mockUser.email,
        displayName: mockUser.displayName,
        role: mockUser.role,
      });

      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
      expect(result).toHaveProperty('expiresIn');
      expect(mockJwtService.sign).toHaveBeenCalledTimes(1);
    });
  });

  describe('revokeRefreshToken', () => {
    it('should revoke a refresh token by setting revokedAt', async () => {
      mockPrismaService.refreshToken.update.mockResolvedValueOnce({});

      await service.revokeRefreshToken('some-token');

      expect(mockPrismaService.refreshToken.update).toHaveBeenCalledWith({
        where: { token: 'some-token' },
        data: { revokedAt: expect.any(Date) },
      });
    });
  });
});
