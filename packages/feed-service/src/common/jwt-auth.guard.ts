import { verifyAccessPayload } from '../../../user-service/dist/common/verified-jwt';
import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';


@Injectable()
export class JwtAuthGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const request = context.switchToHttp().getRequest();
    const authHeader = request.headers.authorization;

    if (typeof authHeader !== 'string' || !authHeader.startsWith('Bearer ')) {
      throw new UnauthorizedException('Authentication required');
    }

    const decoded = verifyAccessPayload(authHeader.substring(7));
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

