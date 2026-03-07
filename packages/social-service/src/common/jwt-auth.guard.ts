import {
  BadRequestException,
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';

function decodeJwtPayload(token: string) {
  const parts = token.split('.');
  if (parts.length < 2) {
    throw new BadRequestException('Invalid token');
  }

  const payload = parts[1]
    .replace(/-/g, '+')
    .replace(/_/g, '/')
    .padEnd(Math.ceil(parts[1].length / 4) * 4, '=');

  return JSON.parse(Buffer.from(payload, 'base64').toString('utf8'));
}

@Injectable()
export class JwtAuthGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const request = context.switchToHttp().getRequest();
    const authHeader = request.headers.authorization;

    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      throw new UnauthorizedException('Authentication required');
    }

    const decoded = decodeJwtPayload(authHeader.substring(7));
    if (!decoded?.sub && !decoded?.id) {
      throw new UnauthorizedException('Invalid token');
    }

    if (decoded.exp && decoded.exp * 1000 < Date.now()) {
      throw new UnauthorizedException('Token expired');
    }

    request.user = decoded;
    return true;
  }
}
