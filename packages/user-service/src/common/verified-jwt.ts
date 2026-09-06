import { UnauthorizedException } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';

const jwt = new JwtService();
export function verifyAccessPayload(token: string): Record<string, any> {
  const secret = process.env.JWT_SECRET;
  if (!secret) throw new UnauthorizedException('Authentication unavailable');
  try {
    const payload = jwt.verify<Record<string, any>>(token, { secret, algorithms: ['HS256'] });
    if (!payload.sub && !payload.id) throw new Error('Missing identity');
    return payload;
  } catch {
    throw new UnauthorizedException('Invalid or expired token');
  }
}
