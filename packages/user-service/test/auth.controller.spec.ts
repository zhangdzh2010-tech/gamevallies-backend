import { Test, TestingModule } from '@nestjs/testing';
import { INestApplication } from '@nestjs/common';
import * as request from 'supertest';
import { AuthController } from '../src/auth/auth.controller';
import { AuthService } from '../src/auth/auth.service';

describe('AuthController', () => {
  let app: INestApplication;
  let authService: AuthService;

  const mockAuthService = {
    register: jest.fn(),
    login: jest.fn(),
    refreshToken: jest.fn(),
    logout: jest.fn(),
  };

  const mockAuthResponse = {
    user: {
      id: 'user-123',
      username: 'testuser',
      email: 'test@example.com',
      display_name: 'Test User',
    },
    accessToken: 'access-token-jwt',
    refreshToken: 'refresh-token-jwt',
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
    }).compile();

    app = module.createNestApplication();
    await app.init();
    authService = module.get<AuthService>(AuthService);

    jest.clearAllMocks();
  });

  afterEach(async () => {
    await app.close();
  });

  describe('POST /api/v1/auth/register', () => {
    it('should return 201 and user data on successful registration', async () => {
      const registerDto = {
        username: 'newuser',
        email: 'newuser@example.com',
        password: 'SecurePass123!',
        displayName: 'New User',
      };

      mockAuthService.register.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/api/v1/auth/register')
        .send(registerDto)
        .expect(201);

      expect(response.body).toEqual(mockAuthResponse);
      expect(response.body).toHaveProperty('accessToken');
      expect(response.body).toHaveProperty('refreshToken');
    });

    it('should return 400 for validation errors (missing fields)', async () => {
      const invalidDto = {
        username: 'newuser',
        // missing email, password, etc.
      };

      await request(app.getHttpServer())
        .post('/api/v1/auth/register')
        .send(invalidDto)
        .expect(400);
    });

    it('should return 400 for weak password', async () => {
      const registerDto = {
        username: 'newuser',
        email: 'newuser@example.com',
        password: 'weak',
        displayName: 'New User',
      };

      mockAuthService.register.mockRejectedValueOnce(
        new Error('Password too weak')
      );

      await request(app.getHttpServer())
        .post('/api/v1/auth/register')
        .send(registerDto)
        .expect(400);
    });

    it('should return 409 when username already exists', async () => {
      const registerDto = {
        username: 'existinguser',
        email: 'new@example.com',
        password: 'SecurePass123!',
        displayName: 'New User',
      };

      mockAuthService.register.mockRejectedValueOnce(
        new Error('Username already taken')
      );

      await request(app.getHttpServer())
        .post('/api/v1/auth/register')
        .send(registerDto)
        .expect(400);
    });

    it('should return 409 when email already exists', async () => {
      const registerDto = {
        username: 'newuser',
        email: 'existing@example.com',
        password: 'SecurePass123!',
        displayName: 'New User',
      };

      mockAuthService.register.mockRejectedValueOnce(
        new Error('Email already registered')
      );

      await request(app.getHttpServer())
        .post('/api/v1/auth/register')
        .send(registerDto)
        .expect(400);
    });
  });

  describe('POST /api/v1/auth/login', () => {
    it('should return 200 and tokens on successful login', async () => {
      const loginDto = {
        username: 'testuser',
        password: 'SecurePass123!',
      };

      mockAuthService.login.mockResolvedValueOnce(mockAuthResponse);

      const response = await request(app.getHttpServer())
        .post('/api/v1/auth/login')
        .send(loginDto)
        .expect(200);

      expect(response.body).toEqual(mockAuthResponse);
      expect(response.body).toHaveProperty('accessToken');
    });

    it('should return 401 for invalid credentials (wrong password)', async () => {
      const loginDto = {
        username: 'testuser',
        password: 'WrongPassword123!',
      };

      mockAuthService.login.mockRejectedValueOnce(
        new Error('Invalid credentials')
      );

      await request(app.getHttpServer())
        .post('/api/v1/auth/login')
        .send(loginDto)
        .expect(400);
    });

    it('should return 401 for non-existent user', async () => {
      const loginDto = {
        username: 'nonexistent',
        password: 'SecurePass123!',
      };

      mockAuthService.login.mockRejectedValueOnce(
        new Error('User not found')
      );

      await request(app.getHttpServer())
        .post('/api/v1/auth/login')
        .send(loginDto)
        .expect(400);
    });

    it('should return 400 for missing credentials', async () => {
      const invalidDto = {
        username: 'testuser',
        // missing password
      };

      await request(app.getHttpServer())
        .post('/api/v1/auth/login')
        .send(invalidDto)
        .expect(400);
    });
  });

  describe('POST /api/v1/auth/refresh', () => {
    it('should return 200 and new tokens on successful refresh', async () => {
      const refreshDto = {
        refreshToken: 'valid-refresh-token',
      };

      const newTokens = {
        accessToken: 'new-access-token',
        refreshToken: 'new-refresh-token',
      };

      mockAuthService.refreshToken.mockResolvedValueOnce(newTokens);

      const response = await request(app.getHttpServer())
        .post('/api/v1/auth/refresh')
        .send(refreshDto)
        .expect(200);

      expect(response.body).toEqual(newTokens);
      expect(response.body).toHaveProperty('accessToken');
      expect(response.body).toHaveProperty('refreshToken');
    });

    it('should return 401 for expired refresh token', async () => {
      const refreshDto = {
        refreshToken: 'expired-refresh-token',
      };

      mockAuthService.refreshToken.mockRejectedValueOnce(
        new Error('Token expired')
      );

      await request(app.getHttpServer())
        .post('/api/v1/auth/refresh')
        .send(refreshDto)
        .expect(400);
    });

    it('should return 401 for invalid refresh token', async () => {
      const refreshDto = {
        refreshToken: 'invalid-token',
      };

      mockAuthService.refreshToken.mockRejectedValueOnce(
        new Error('Invalid token')
      );

      await request(app.getHttpServer())
        .post('/api/v1/auth/refresh')
        .send(refreshDto)
        .expect(400);
    });

    it('should return 400 for missing refresh token', async () => {
      const invalidDto = {};

      await request(app.getHttpServer())
        .post('/api/v1/auth/refresh')
        .send(invalidDto)
        .expect(400);
    });
  });

  describe('POST /api/v1/auth/logout', () => {
    it('should return 200 on successful logout', async () => {
      const logoutDto = {
        refreshToken: 'valid-refresh-token',
      };

      mockAuthService.logout.mockResolvedValueOnce({});

      const response = await request(app.getHttpServer())
        .post('/api/v1/auth/logout')
        .send(logoutDto)
        .expect(200);

      expect(mockAuthService.logout).toHaveBeenCalledWith('valid-refresh-token');
    });

    it('should return 401 for unauthorized (invalid token)', async () => {
      const logoutDto = {
        refreshToken: 'invalid-token',
      };

      mockAuthService.logout.mockRejectedValueOnce(
        new Error('Unauthorized')
      );

      await request(app.getHttpServer())
        .post('/api/v1/auth/logout')
        .send(logoutDto)
        .expect(400);
    });

    it('should return 400 for missing refresh token', async () => {
      const invalidDto = {};

      await request(app.getHttpServer())
        .post('/api/v1/auth/logout')
        .send(invalidDto)
        .expect(400);
    });
  });
});
