import { Test, TestingModule } from '@nestjs/testing';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { ConflictException, BadRequestException, UnauthorizedException } from '@nestjs/common';
import { AuthService } from '../src/auth/auth.service';
import { PrismaService } from '../src/prisma/prisma.service';
import { RegisterDto, LoginDto } from '../src/auth/dto';

describe('AuthService', () => {
  let service: AuthService;
  let prismaService: PrismaService;
  let jwtService: JwtService;
  let configService: ConfigService;

  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    password_hash: '$2a$10$hashedpassword',
    display_name: 'Test User',
    role: 'USER',
    created_at: new Date(),
    updated_at: new Date(),
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
      delete: jest.fn(),
      create: jest.fn(),
    },
  };

  const mockJwtService = {
    sign: jest.fn(),
    verify: jest.fn(),
  };

  const mockConfigService = {
    get: jest.fn(),
  };

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      providers: [
        AuthService,
        {
          provide: PrismaService,
          useValue: mockPrismaService,
        },
        {
          provide: JwtService,
          useValue: mockJwtService,
        },
        {
          provide: ConfigService,
          useValue: mockConfigService,
        },
      ],
    }).compile();

    service = module.get<AuthService>(AuthService);
    prismaService = module.get<PrismaService>(PrismaService);
    jwtService = module.get<JwtService>(JwtService);
    configService = module.get<ConfigService>(ConfigService);

    jest.clearAllMocks();
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
      mockJwtService.sign.mockReturnValueOnce('access-token');
      mockJwtService.sign.mockReturnValueOnce('refresh-token');
      mockConfigService.get.mockReturnValueOnce(3600); // accessTokenExpiry
      mockConfigService.get.mockReturnValueOnce(604800); // refreshTokenExpiry
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

    it('should throw BadRequestException for weak password', async () => {
      const registerDto: RegisterDto = {
        username: 'newuser',
        email: 'new@example.com',
        password: 'weak',
        displayName: 'New User',
      };

      mockPrismaService.user.findUnique.mockResolvedValueOnce(null);
      mockPrismaService.user.findUnique.mockResolvedValueOnce(null);

      await expect(service.register(registerDto)).rejects.toThrow(BadRequestException);
    });
  });

  describe('login', () => {
    it('should successfully login user with correct credentials', async () => {
      const loginDto: LoginDto = {
        username: 'testuser',
        password: 'SecurePass123!',
      };

      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser);
      mockJwtService.sign.mockReturnValueOnce('access-token');
      mockJwtService.sign.mockReturnValueOnce('refresh-token');
      mockConfigService.get.mockReturnValueOnce(3600);
      mockConfigService.get.mockReturnValueOnce(604800);
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.login(loginDto);

      expect(result).toHaveProperty('user');
      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
    });

    it('should throw UnauthorizedException for wrong password', async () => {
      const loginDto: LoginDto = {
        username: 'testuser',
        password: 'WrongPassword123!',
      };

      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser);

      await expect(service.login(loginDto)).rejects.toThrow(UnauthorizedException);
    });

    it('should throw UnauthorizedException when user not found', async () => {
      const loginDto: LoginDto = {
        username: 'nonexistent',
        password: 'SecurePass123!',
      };

      mockPrismaService.user.findUnique.mockResolvedValueOnce(null);

      await expect(service.login(loginDto)).rejects.toThrow(UnauthorizedException);
    });
  });

  describe('refreshToken', () => {
    it('should successfully refresh valid token', async () => {
      const refreshToken = 'valid-refresh-token';
      const payload = { sub: 'user-123', username: 'testuser' };

      mockJwtService.verify.mockReturnValueOnce(payload);
      mockPrismaService.refreshToken.findUnique.mockResolvedValueOnce({
        id: 'token-id',
        user_id: 'user-123',
        token: refreshToken,
        revoked: false,
        expires_at: new Date(Date.now() + 86400000),
      });
      mockPrismaService.user.findUnique.mockResolvedValueOnce(mockUser);
      mockJwtService.sign.mockReturnValueOnce('new-access-token');
      mockJwtService.sign.mockReturnValueOnce('new-refresh-token');
      mockConfigService.get.mockReturnValueOnce(3600);
      mockConfigService.get.mockReturnValueOnce(604800);
      mockPrismaService.refreshToken.create.mockResolvedValueOnce({});

      const result = await service.refreshToken(refreshToken);

      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
      expect(jwtService.verify).toHaveBeenCalledWith(
        refreshToken,
        expect.any(String)
      );
    });

    it('should throw UnauthorizedException for expired token', async () => {
      const refreshToken = 'expired-refresh-token';

      mockJwtService.verify.mockImplementationOnce(() => {
        throw new Error('Token expired');
      });

      await expect(service.refreshToken(refreshToken)).rejects.toThrow(UnauthorizedException);
    });

    it('should throw UnauthorizedException for revoked token', async () => {
      const refreshToken = 'revoked-refresh-token';
      const payload = { sub: 'user-123', username: 'testuser' };

      mockJwtService.verify.mockReturnValueOnce(payload);
      mockPrismaService.refreshToken.findUnique.mockResolvedValueOnce({
        id: 'token-id',
        revoked: true,
      });

      await expect(service.refreshToken(refreshToken)).rejects.toThrow(UnauthorizedException);
    });
  });

  describe('generateTokens', () => {
    it('should generate valid JWT tokens with correct payload', async () => {
      const user = mockUser;

      mockJwtService.sign.mockReturnValueOnce('access-token');
      mockJwtService.sign.mockReturnValueOnce('refresh-token');
      mockConfigService.get.mockReturnValueOnce(3600);
      mockConfigService.get.mockReturnValueOnce(604800);

      const result = await service.generateTokens(user);

      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
      expect(jwtService.sign).toHaveBeenCalledTimes(2);

      // Verify JWT signing calls include correct payload
      const firstCall = mockJwtService.sign.mock.calls[0];
      expect(firstCall[0]).toEqual(
        expect.objectContaining({
          sub: user.id,
          username: user.username,
          email: user.email,
        })
      );
    });
  });

  describe('logout', () => {
    it('should revoke refresh token on logout', async () => {
      const refreshToken = 'valid-refresh-token';
      const payload = { sub: 'user-123' };

      mockJwtService.verify.mockReturnValueOnce(payload);
      mockPrismaService.refreshToken.findUnique.mockResolvedValueOnce({
        id: 'token-id',
        user_id: 'user-123',
      });
      mockPrismaService.refreshToken.delete.mockResolvedValueOnce({});

      await service.logout(refreshToken);

      expect(mockPrismaService.refreshToken.delete).toHaveBeenCalled();
    });
  });
});
