import { Test, TestingModule } from '@nestjs/testing';
import { INestApplication, ValidationPipe } from '@nestjs/common';
import { CanActivate, ExecutionContext } from '@nestjs/common';
import request from 'supertest';
import { AuthController } from '../src/auth/auth.controller';
import { AuthService } from '../src/auth/auth.service';
import { JwtAuthGuard } from '../src/auth/jwt-auth.guard';
import { ConflictException, UnauthorizedException } from '@nestjs/common';

// Mock guard that bypasses JWT validation and injects a test user
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
    register: jest.fn(),
    login: jest.fn(),
    refreshToken: jest.fn(),
    revokeRefreshToken: jest.fn(),
    sendVerificationCode: jest.fn(),
    verifyCode: jest.fn(),
    resetPassword: jest.fn(),
    changePassword: jest.fn(),
    validateUser: jest.fn(),
  };

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      controllers: [AuthController],
      providers: [
        {
          provide: AuthService,
          useValue: mockAuthService,
        },
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

  describe('POST /auth/register', () => {
    it('should return 201 and user data on successful registration', async () => {
      mockAuthService.register.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/register')
        .send({
          username: 'newuser',
          email: 'newuser@example.com',
          password: 'SecurePass123!',
          displayName: 'New User',
        })
        .expect(201);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('user');
    });

    it('should return 409 when username already exists', async () => {
      mockAuthService.register.mockRejectedValueOnce(
        new ConflictException('Username already taken'),
      );

      await request(app.getHttpServer())
        .post('/auth/register')
        .send({
          username: 'existinguser',
          email: 'new@example.com',
          password: 'SecurePass123!',
          displayName: 'New User',
        })
        .expect(409);
    });

    it('should return 409 when email already exists', async () => {
      mockAuthService.register.mockRejectedValueOnce(
        new ConflictException('Email already registered'),
      );

      await request(app.getHttpServer())
        .post('/auth/register')
        .send({
          username: 'newuser',
          email: 'existing@example.com',
          password: 'SecurePass123!',
          displayName: 'New User',
        })
        .expect(409);
    });
  });

  describe('POST /auth/login', () => {
    it('should return 200 and tokens on successful login', async () => {
      mockAuthService.login.mockResolvedValueOnce(mockAuthResponse);

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
      mockAuthService.login.mockRejectedValueOnce(
        new UnauthorizedException('Invalid email/username or password'),
      );

      await request(app.getHttpServer())
        .post('/auth/login')
        .send({
          account: 'testuser',
          password: 'WrongPassword!',
        })
        .expect(401);
    });

    it('should return 401 for non-existent user', async () => {
      mockAuthService.login.mockRejectedValueOnce(
        new UnauthorizedException('Invalid email/username or password'),
      );

      await request(app.getHttpServer())
        .post('/auth/login')
        .send({
          account: 'nonexistent',
          password: 'SecurePass123!',
        })
        .expect(401);
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

    it('should return 401 for expired/invalid refresh token', async () => {
      mockAuthService.refreshToken.mockRejectedValueOnce(
        new UnauthorizedException('Refresh token has been revoked'),
      );

      await request(app.getHttpServer())
        .post('/auth/refresh')
        .send({ refreshToken: 'expired-token' })
        .expect(401);
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

    it('should return 200 even without refresh token header', async () => {
      const response = await request(app.getHttpServer())
        .post('/auth/logout')
        .expect(200);

      expect(response.body.code).toBe(0);
      expect(mockAuthService.revokeRefreshToken).not.toHaveBeenCalled();
    });
  });
});
