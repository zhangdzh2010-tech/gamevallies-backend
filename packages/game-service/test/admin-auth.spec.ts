import {
  checkAdminToken,
  getAdminPassword,
  getAdminUsername,
  validateAdminLogin,
} from '../src/common/admin-auth';
import { HttpException } from '@nestjs/common';

describe('admin-auth', () => {
  const env = process.env;

  beforeEach(() => {
    process.env = { ...env };
    process.env.ADMIN_USERNAME = 'admin';
    process.env.ADMIN_PASSWORD = 'admin123';
    process.env.ADMIN_TOKEN = 'session-token';
  });

  afterAll(() => {
    process.env = env;
  });

  it('uses default admin credentials when env vars are missing', () => {
    delete process.env.ADMIN_USERNAME;
    delete process.env.ADMIN_PASSWORD;
    expect(getAdminUsername()).toBe('admin');
    expect(getAdminPassword()).toBe('admin123');
  });

  it('accepts valid username and password', () => {
    expect(() => validateAdminLogin('admin', 'admin123')).not.toThrow();
  });

  it('rejects invalid username or password', () => {
    expect(() => validateAdminLogin('admin', 'wrong')).toThrow(HttpException);
    expect(() => validateAdminLogin('root', 'admin123')).toThrow(HttpException);
  });

  it('checks admin session token', () => {
    expect(() => checkAdminToken('session-token')).not.toThrow();
    expect(() => checkAdminToken('wrong')).toThrow(HttpException);
  });
});
