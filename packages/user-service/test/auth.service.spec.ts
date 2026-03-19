import { Test, TestingModule } from '@nestjs/testing';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { ConflictException, UnauthorizedException } from '@nestjs/common';
import { AuthService } from '../src/auth/auth.service';
import { PrismaService } from '../src/prisma/prisma.service';
import { RegisterDto, LoginDto } from '../src/auth/dto';

// Mock bcrypt so password operations don't require real computation
jest.mock('bcryptjs', () => ({
  hash: jest.fn().mockResolvedValue('$hashed_password'),
  compare: jest.fn(),
}));

import * as bcrypt from 'bcryptjs';

describe('AuthService', () => {
  let service: AuthService;
  let prismaService: PrismaService;
  let jwtService: JwtService;

  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
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
      updateMany: jest.fn(),
    },
    refreshToken: {
      findUnique: jest.fn(),
      create: jest.fn(),
      update: jest.fn(),
      updateMany: jest.fn(),
      delete: jest.fn(),
    },
  };

  const mockJwtService = {
    sign: jest.fn().mockReturnValue('mock-token'),
    verify: jest.fn(),
  };

  const mockConfigService = {
    get: jest.fn().mockImplementation((key: string, defaultVal?: any) => {
      const config: Record<string, any> = {
        JWT_SECRET: 'test-secret',
        JWT_EXPIRES_IN: '24h',
        JWT_REFRESH_SECRET: 'test-refresh-secret',
        JWT_REFRESH_EXPIRES_IN: '7d',
      };
      return config[key] ?? defaultVal;
    }),
  };

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AuthService,
        { provide: PrismaService, useValue: mockPrismaService },
        { provide: JwtService, useValue: mockJwtService },
        { provide: ConfigService, useValue: mockConfigService },
      ],
    }).compile();

    service = module.get<AuthService>(AuthService);
    prismaService = module.get<PrismaService>(PrismaService);
    jwtService = module.get<JwtService>(JwtService);

    jest.clearAllMocks();

    // Restore default implementations after clearAllMocks
    (mockJwtService.sign as jest.Mock).mockReturnValue('mock-token');
    (mockConfigService.get as jest.Mock).mockImplementation((key: string, defaultVal?: any) => {
      const config: Record<string, any> = {
        JWT_SECRET: 'test-secret',
        JWT_EXPIRES_IN: '24h',
      };
      return config[key] ?? defaultVal;
    });
    (bcrypt.hash as jest.Mock).mockResolvedValue('$hashed_password');
  });

  describe('register', () => {
    it('should successfully register a new user', async () => {
      const registerDto: RegisterDto = {
        username: 'newuser',
        email: 'newuser@example.com',
        password: 'SecurePass123!',
        displayName: 'New User',
        phone: '+1234567890',
      };

      mockPrismaService.user.findUnique.mockResolvedValueOnce(null); // username check
      mockPrismaService.user.findUnique.mockResolvedValueOnce(null); // email check
      mockPrismaService.user.create.mockResolvedValueOnce(mockUser);
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.register(registerDto);

      expect(result).toHaveProperty('user');
      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
      expect(mockPrismaService.user.findUnique).toHaveBeenCalledTimes(2);
      expect(mockPrismaService.user.create).toHaveBeenCalled();
    });

    it('should throw ConflictException when username already exists', async () => {
      const registerDto: RegisterDto = {
        username: 'existinguser',
        email: 'new@example.com',
        password: 'SecurePass123!',
        displayName: 'New User',
      };

      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser);

      await expect(service.register(registerDto)).rejects.toThrow(ConflictException);
      expect(mockPrismaService.user.create).not.toHaveBeenCalled();
    });

    it('should throw ConflictException when email already exists', async () => {
      const registerDto: RegisterDto = {
        username: 'newuser',
        email: 'test@example.com',
        password: 'SecurePass123!',
        displayName: 'New User',
      };

      mockPrismaService.user.findUnique.mockResolvedValueOnce(null); // username check
      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser); // email check

      await expect(service.register(registerDto)).rejects.toThrow(ConflictException);
      expect(mockPrismaService.user.create).not.toHaveBeenCalled();
    });
  });

  describe('login', () => {
    it('should successfully login user with correct credentials', async () => {
      const loginDto: LoginDto = {
        account: 'testuser',
        password: 'SecurePass123!',
      };

      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);
      (bcrypt.compare as jest.Mock).mockResolvedValueOnce(true);
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.login(loginDto);

      expect(result).toHaveProperty('user');
      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
    });

    it('should throw UnauthorizedException for wrong password', async () => {
      const loginDto: LoginDto = {
        account: 'testuser',
        password: 'WrongPassword123!',
      };

      mockPrismaService.user.findFirst.mockResolvedValueOnce(mockUser);
      (bcrypt.compare as jest.Mock).mockResolvedValueOnce(false);

      await expect(service.login(loginDto)).rejects.toThrow(UnauthorizedException);
    });

    it('should throw UnauthorizedException when user not found', async () => {
      const loginDto: LoginDto = {
        account: 'nonexistent',
        password: 'SecurePass123!',
      };

      mockPrismaService.user.findFirst.mockResolvedValueOnce(null);

      await expect(service.login(loginDto)).rejects.toThrow(UnauthorizedException);
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
      // Service only calls jwtService.sign once (access token); refresh token is a UUID
      expect(jwtService.sign).toHaveBeenCalledTimes(1);
      const firstCall = (mockJwtService.sign as jest.Mock).mock.calls[0];
      expect(firstCall[0]).toEqual(
        expect.objectContaining({
          sub: mockUser.id,
          username: mockUser.username,
        }),
      );
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
