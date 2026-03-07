/**
 * PlayForge Shared Package
 * Central export point for all shared utilities, types, guards, and decorators
 */

// ============================================================================
// Types and DTOs
// ============================================================================
export {
  ResponseDto,
  PaginationQuery,
  PaginatedResponse,
  RegisterDto,
  LoginDto,
  AuthTokens,
  UserProfileDto,
  UpdateProfileDto,
  GameDto,
  CreateGameDto,
  IterateGameDto,
  PublishGameDto,
  GameSpecDto,
  GenerateProgressDto,
  LikeDto,
  FollowDto,
  CreateCommentDto,
  CommentDto,
  NotificationDto,
  FeedQuery,
  SearchQuery,
  WsMessage,
  ErrorCodes,
} from './types/api.types';

// ============================================================================
// Guards
// ============================================================================
export { JwtAuthGuard, JwtStrategy, JwtPayload } from './guards/jwt-auth.guard';
export { RolesGuard, Roles } from './guards/roles.guard';

// ============================================================================
// Interceptors
// ============================================================================
export { TransformInterceptor } from './interceptors/transform.interceptor';

// ============================================================================
// Filters
// ============================================================================
export { HttpExceptionFilter, HTTP_EXCEPTION_MAP } from './filters/http-exception.filter';

// ============================================================================
// Decorators
// ============================================================================
export { CurrentUser, Public, Role } from './decorators/index';

// ============================================================================
// Utils
// ============================================================================
export {
  generateSlug,
  formatNumber,
  calculateWilsonScore,
  generateId,
  sleep,
  isValidEmail,
  isValidUsername,
  truncate,
} from './utils/index';
