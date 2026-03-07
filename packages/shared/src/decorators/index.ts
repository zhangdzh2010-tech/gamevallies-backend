import {
  createParamDecorator,
  ExecutionContext,
  SetMetadata,
} from '@nestjs/common';
import { Request } from 'express';

/**
 * CurrentUser decorator
 * Extracts the user from the request object
 * Usage: @CurrentUser() user: JwtPayload
 */
export const CurrentUser = createParamDecorator(
  (data: unknown, ctx: ExecutionContext) => {
    const request = ctx.switchToHttp().getRequest<Request>();
    return (request as any).user;
  },
);

/**
 * Public decorator
 * Marks a route as public (no authentication required)
 * Usage: @Public()
 */
export const Public = () => SetMetadata('isPublic', true);

/**
 * Roles decorator
 * Marks required roles for a route
 * Usage: @Roles('admin', 'moderator')
 */
export const Role = (...roles: string[]) => SetMetadata('roles', roles);
