import { HttpException, HttpStatus } from '@nestjs/common';

export function checkAdminToken(token: string | undefined): void {
  const adminToken = (process.env.ADMIN_TOKEN || '').trim();
  if (!adminToken) {
    throw new HttpException('Admin token is not configured', HttpStatus.SERVICE_UNAVAILABLE);
  }
  if (!token || token !== adminToken) {
    throw new HttpException('Unauthorized: Invalid admin token', HttpStatus.UNAUTHORIZED);
  }
}
