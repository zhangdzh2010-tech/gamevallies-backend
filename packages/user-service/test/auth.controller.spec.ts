import { AuthController } from '../src/auth/auth.controller';

describe('AuthController', () => {
  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    phone: '13800138000',
    displayName: 'Test User',
    role: 'user',
    createdAt: new Date('2026-03-20T00:00:00.000Z'),
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

  let controller: AuthController;

  beforeEach(() => {
    jest.clearAllMocks();
    controller = new AuthController(mockAuthService as any, mockSmsService as any);
  });

  it('login delegates to password login service and returns wrapped tokens', async () => {
    mockAuthService.loginByPassword.mockResolvedValueOnce(mockAuthResponse);

    const result = await controller.login({
      account: 'testuser',
      password: 'SecurePass123!',
    } as any);

    expect(mockAuthService.loginByPassword).toHaveBeenCalledWith(
      'testuser',
      'SecurePass123!',
    );
    expect(result).toMatchObject({
      code: 0,
      data: {
        token: 'access-token-jwt',
        refreshToken: 'refresh-token-uuid',
        user: expect.objectContaining({
          username: 'testuser',
          phone: '13800138000',
        }),
      },
    });
  });

  it('sendSmsCode delegates phone and purpose to auth service', async () => {
    const result = await controller.sendSmsCode({
      phone: '13800138000',
      type: 'login',
    } as any);

    expect(mockAuthService.sendSmsCode).toHaveBeenCalledWith('13800138000', 'login');
    expect(result).toEqual({
      code: 0,
      message: 'success',
      data: null,
    });
  });

  it('registerByPhone returns wrapped user payload', async () => {
    mockAuthService.registerByPhone.mockResolvedValueOnce(mockAuthResponse);

    const result = await controller.registerByPhone({
      phone: '13800138000',
      smsCode: '123456',
      nickname: 'Test User',
      password: 'SecurePass123!',
    } as any);

    expect(mockAuthService.registerByPhone).toHaveBeenCalledWith(
      '13800138000',
      '123456',
      'Test User',
      'SecurePass123!',
    );
    expect(result).toMatchObject({
      data: {
        user: {
          displayName: 'Test User',
        },
      },
    });
  });

  it('loginByPhone returns tokens for SMS login', async () => {
    mockAuthService.loginByPhone.mockResolvedValueOnce(mockAuthResponse);

    const result = await controller.loginByPhone({
      phone: '13800138000',
      smsCode: '123456',
    } as any);

    expect(mockAuthService.loginByPhone).toHaveBeenCalledWith('13800138000', '123456');
    expect(result.data.token).toBe('access-token-jwt');
  });

  it('loginByWechatMiniapp returns wrapped tokens and user', async () => {
    mockAuthService.loginByWechatMiniapp.mockResolvedValueOnce(mockAuthResponse);

    const result = await controller.loginByWechatMiniapp({
      code: 'wx-code',
      nickname: 'Wechat User',
      avatarUrl: 'https://example.com/avatar.png',
    } as any);

    expect(mockAuthService.loginByWechatMiniapp).toHaveBeenCalledWith(
      'wx-code',
      'Wechat User',
      'https://example.com/avatar.png',
    );
    expect(result.data.refreshToken).toBe('refresh-token-uuid');
  });

  it('refresh returns rotated tokens', async () => {
    mockAuthService.refreshToken.mockResolvedValueOnce({
      accessToken: 'new-access-token',
      refreshToken: 'new-refresh-uuid',
      expiresIn: 86400,
    });

    const result = await controller.refresh({ refreshToken: 'valid-refresh-token' } as any);

    expect(mockAuthService.refreshToken).toHaveBeenCalledWith('valid-refresh-token');
    expect(result).toEqual({
      code: 0,
      message: 'success',
      data: {
        token: 'new-access-token',
        refreshToken: 'new-refresh-uuid',
      },
    });
  });

  it('logout revokes refresh token when header exists', async () => {
    const result = await controller.logout({
      headers: {
        'x-refresh-token': 'valid-refresh-token',
      },
    } as any);

    expect(mockAuthService.revokeRefreshToken).toHaveBeenCalledWith('valid-refresh-token');
    expect(result.data).toBeNull();
  });

  it('logout skips revoke when refresh token header is absent', async () => {
    const result = await controller.logout({ headers: {} } as any);

    expect(mockAuthService.revokeRefreshToken).not.toHaveBeenCalled();
    expect(result.data).toBeNull();
  });

  it('getProfile loads current user from request context', async () => {
    mockAuthService.validateUser.mockResolvedValueOnce(mockUser);

    const result = await controller.getProfile({
      user: { userId: 'user-123' },
    });

    expect(mockAuthService.validateUser).toHaveBeenCalledWith('user-123');
    expect(result).toMatchObject({
      data: {
        username: 'testuser',
      },
    });
  });

  it('querySmsDetails proxies SMS query results', async () => {
    mockSmsService.queryDetails.mockResolvedValueOnce([
      { phoneNum: '13800138000', content: '验证码：123456' },
    ]);

    const result = await controller.querySmsDetails('13800138000', '20260320');

    expect(mockSmsService.queryDetails).toHaveBeenCalledWith('13800138000', '20260320');
    expect(result.data).toHaveLength(1);
  });

  it('querySmsDetails returns an inline error payload when params are missing', async () => {
    const result = await controller.querySmsDetails('', '');

    expect(mockSmsService.queryDetails).not.toHaveBeenCalled();
    expect(result).toEqual({
      code: 0,
      message: 'success',
      data: { error: 'phone and date (YYYYMMDD) required' },
    });
  });
});
