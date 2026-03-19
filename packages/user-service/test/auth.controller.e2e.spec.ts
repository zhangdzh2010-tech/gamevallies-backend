import { Test, TestingModule } from '@nestjs/testing';
import { INestApplication, ValidationPipe } from '@nestjs/common';
import { CanActivate, ExecutionContext } from '@nestjs/common';
import request from 'supertest';
import { AuthController } from '../src/auth/auth.controller';
import { AuthService } from '../src/auth/auth.service';
import { JwtAuthGuard } from '../src/auth/jwt-auth.guard';
import { ConflictException, UnauthorizedException } from '@nestjs/common';

class MockJwtAuthGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const req = context.switchToHttp().getRequest();
    const authHeader = req.headers['authorization'];
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

  beforeAll(async () => {
    const module: TestingModule = await Test.createTestingModule({
      controllers: [AuthController],
      providers: [
        { provide: AuthService, useValue: mockAuthService },
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

  describe('POST /auth/register', () => {
    it('should register a new user', async () => {
      mockAuthService.register.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/auth/register')
        .send({
          username: 'testuser',
          email: 'test@example.com',
          password: 'password123',
          displayName: 'Test User',
        })
        .expect(201);

      expect(response.body.code).toBe(0);
      expect(response.body.data).toHaveProperty('user');
    });

    it('should not register duplicate username', async () => {
      mockAuthService.register.mockRejectedValueOnce(
        new ConflictException('Username already taken'),
      );

      await request(app.getHttpServer())
        .post('/auth/register')
        .send({
          username: 'testuser',
          email: 'test2@example.com',
          password: 'password123',
          displayName: 'Test',
        })
        .expect(409);
    });
  });

  describe('POST /auth/login', () => {
    it('should login with username', async () => {
      mockAuthService.login.mockResolvedValueOnce(mockAuthResponse);

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
      mockAuthService.login.mockResolvedValueOnce(mockAuthResponse);

      await request(app.getHttpServer())
        .post('/auth/login')
        .send({
          account: 'test@example.com',
          password: 'password123',
        })
        .expect(200);
    });

    it('should not login with invalid password', async () => {
      mockAuthService.login.mockRejectedValueOnce(
        new UnauthorizedException('Invalid email/username or password'),
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

    it('should not refresh with invalid token', async () => {
      mockAuthService.refreshToken.mockRejectedValueOnce(
        new UnauthorizedException('Refresh token not found'),
      );

      await request(app.getHttpServer())
        .post('/auth/refresh')
        .send({ refreshToken: 'invalid-token' })
        .expect(401);
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
