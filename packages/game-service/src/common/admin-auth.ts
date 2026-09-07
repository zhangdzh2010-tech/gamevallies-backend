import { HttpException, HttpStatus } from '@nestjs/common';

export function getAdminUsername(): string {
  return (process.env.ADMIN_USERNAME || 'admin').trim();
}

export function getAdminPassword(): string {
  return (process.env.ADMIN_PASSWORD || 'admin123').trim();
}

export function getAdminToken(): string {
  return (process.env.ADMIN_TOKEN || '').trim();
}

export function validateAdminLogin(
  username: string | undefined,
  password: string | undefined,
): void {
  const configuredUsername = getAdminUsername();
  const configuredPassword = getAdminPassword();
  if (
    !username ||
    !password ||
    username.trim() !== configuredUsername ||
    password !== configuredPassword
  ) {
    throw new HttpException(
      'Unauthorized: Invalid username or password',
      HttpStatus.UNAUTHORIZED,
    );
  }
}

export function checkAdminToken(token: string | undefined): void {
  const adminToken = getAdminToken();
  if (!adminToken) {
    throw new HttpException(
      'Admin token is not configured',
      HttpStatus.SERVICE_UNAVAILABLE,
    );
  }
  if (!token || token !== adminToken) {
    throw new HttpException(
      'Unauthorized: Invalid admin token',
      HttpStatus.UNAUTHORIZED,
    );
  }
}
