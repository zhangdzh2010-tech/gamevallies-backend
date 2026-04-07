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

  try {
    const payload = parts[1]
      .replace(/-/g, '+')
      .replace(/_/g, '/')
      .padEnd(Math.ceil(parts[1].length / 4) * 4, '=');

    return JSON.parse(Buffer.from(payload, 'base64').toString('utf8'));
  } catch {
    throw new BadRequestException('Invalid token');
  }
}

function extractBearerToken(authHeader: unknown): string | null {
  if (typeof authHeader !== 'string' || !authHeader.startsWith('Bearer ')) {
    return null;
  }
  const token = authHeader.substring(7).trim();
  return token || null;
}

function extractSseQueryToken(request: any): string | null {
  const queryToken = request?.query?.token;
  if (typeof queryToken !== 'string' || !queryToken.trim()) {
    return null;
  }

  const method = String(request?.method || '').toUpperCase();
  const acceptHeader = request?.headers?.accept;
  const accepts = Array.isArray(acceptHeader) ? acceptHeader.join(',') : String(acceptHeader || '');

  if (method !== 'GET' || !accepts.toLowerCase().includes('text/event-stream')) {
    return null;
  }

  return queryToken.trim();
}

@Injectable()
export class JwtAuthGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const request = context.switchToHttp().getRequest();
    const token =
      extractBearerToken(request.headers.authorization) || extractSseQueryToken(request);

    if (!token) {
      throw new UnauthorizedException('Authentication required');
    }

    const decoded = decodeJwtPayload(token);
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
