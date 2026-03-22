import { AuthController } from '../src/auth/auth.controller';

describe('AuthController response contracts', () => {
  const mockUser = {
    id: 'user-123',
    username: 'testuser',
    phone: '13800138000',
    displayName: 'Test User',
    role: 'user',
    createdAt: new Date('2026-03-20T00:00:00.000Z'),
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

  it('keeps public login response shape stable', async () => {
    mockAuthService.loginByPassword.mockResolvedValueOnce({
      accessToken: 'access-token-jwt',
      refreshToken: 'refresh-token-uuid',
      expiresIn: 86400,
      user: mockUser,
    });

    const result = await controller.login({
      account: 'testuser',
      password: 'password123',
    } as any);

    expect(result.code).toBe(0);
    expect(result.message).toBe('success');
    expect(result.data).toEqual({
      token: 'access-token-jwt',
      refreshToken: 'refresh-token-uuid',
      user: expect.objectContaining({
        id: 'user-123',
        username: 'testuser',
        phone: '13800138000',
      }),
    });
  });

  it('keeps SMS registration response shape stable', async () => {
    mockAuthService.registerByPhone.mockResolvedValueOnce({
      accessToken: 'access-token-jwt',
      refreshToken: 'refresh-token-uuid',
      expiresIn: 86400,
      user: mockUser,
    });

    const result = await controller.registerByPhone({
      phone: '13800138000',
      smsCode: '123456',
      nickname: 'Test User',
      password: 'password123',
    } as any);

    expect(result.data).toEqual({
      token: 'access-token-jwt',
      refreshToken: 'refresh-token-uuid',
      user: expect.objectContaining({
        username: 'testuser',
        phone: '13800138000',
      }),
    });
  });

  it('reads request user context for profile lookups', async () => {
    mockAuthService.validateUser.mockResolvedValueOnce(mockUser);

    const result = await controller.getProfile({
      user: { userId: 'user-123' },
    });

    expect(mockAuthService.validateUser).toHaveBeenCalledWith('user-123');
    expect(result).toMatchObject({
      data: {
        displayName: 'Test User',
      },
    });
  });

  it('does not revoke refresh token when logout request omits the header', async () => {
    const result = await controller.logout({
      headers: {},
    } as any);

    expect(mockAuthService.revokeRefreshToken).not.toHaveBeenCalled();
    expect(result).toEqual({
      code: 0,
      message: 'success',
      data: null,
    });
  });

  it('returns a helpful inline error payload for incomplete SMS queries', async () => {
    const result = await controller.querySmsDetails(undefined as any, '20260320');

    expect(result).toEqual({
      code: 0,
      message: 'success',
      data: { error: 'phone and date (YYYYMMDD) required' },
    });
  });
});
