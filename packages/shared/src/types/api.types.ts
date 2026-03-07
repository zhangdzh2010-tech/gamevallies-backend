/**
 * API Types and DTOs for PlayForge
 */

// ============================================================================
// Response Wrappers
// ============================================================================

/**
 * Generic API response wrapper
 */
export interface ResponseDto<T = any> {
  code: number;
  data: T;
  message: string;
  timestamp: string;
}

/**
 * Pagination query parameters
 */
export interface PaginationQuery {
  page?: number;
  limit?: number;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
}

/**
 * Paginated response wrapper
 */
export interface PaginatedResponse<T = any> {
  items: T[];
  total: number;
  page: number;
  limit: number;
  totalPages: number;
}

// ============================================================================
// Authentication DTOs
// ============================================================================

/**
 * User registration data
 */
export interface RegisterDto {
  username: string;
  email?: string;
  phone?: string;
  password: string;
  displayName?: string;
}

/**
 * User login data
 */
export interface LoginDto {
  account: string; // username or email
  password: string;
}

/**
 * Authentication tokens response
 */
export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}

// ============================================================================
// User DTOs
// ============================================================================

/**
 * User profile data
 */
export interface UserProfileDto {
  id: string;
  username: string;
  displayName: string;
  avatarUrl: string;
  bio: string;
  role: string;
  isPro: boolean;
  followerCount: number;
  followingCount: number;
  gameCount: number;
  totalPlays: number;
  createdAt: string;
}

/**
 * User profile update data
 */
export interface UpdateProfileDto {
  displayName?: string;
  bio?: string;
  avatarUrl?: string;
}

// ============================================================================
// Game DTOs
// ============================================================================

/**
 * Game data (read-only)
 */
export interface GameDto {
  id: string;
  authorId: string;
  title: string;
  description: string;
  slug: string;
  status: string; // draft, published, archived
  gameType: string;
  tags: string[];
  forkedFrom: string | null;
  forkDepth: number;
  version: number;
  thumbnailUrl: string;
  playCount: number;
  likeCount: number;
  forkCount: number;
  commentCount: number;
  avgPlayTime: number;
  qualityScore: number;
  createdAt: string;
  publishedAt: string | null;
  author?: UserProfileDto;
}

/**
 * Create game data
 */
export interface CreateGameDto {
  description: string;
}

/**
 * Iterate on existing game
 */
export interface IterateGameDto {
  feedback: string;
}

/**
 * Publish game data
 */
export interface PublishGameDto {
  title: string;
  description: string;
  tags: string[];
  gameType: string;
}

/**
 * Game specification for AI generation
 */
export interface GameSpecDto {
  game_type: string;
  core_mechanics: any[];
  visual_style: any;
  entities: any[];
  rules: any;
  difficulty_curve: string;
  audio_style: string;
}

/**
 * Game generation progress data
 */
export interface GenerateProgressDto {
  type: 'gen:progress' | 'gen:complete' | 'gen:error';
  data: any;
}

// ============================================================================
// Social DTOs
// ============================================================================

/**
 * Like action data
 */
export interface LikeDto {
  targetType: 'game' | 'comment';
  targetId: string;
}

/**
 * Follow action data
 */
export interface FollowDto {
  targetId: string;
}

/**
 * Create comment data
 */
export interface CreateCommentDto {
  gameId: string;
  content: string;
  parentId?: string;
}

/**
 * Comment data (read-only)
 */
export interface CommentDto {
  id: string;
  gameId: string;
  userId: string;
  parentId: string | null;
  content: string;
  likeCount: number;
  status: string;
  createdAt: string;
  user?: UserProfileDto;
  replies?: CommentDto[];
}

/**
 * Notification data
 */
export interface NotificationDto {
  id: string;
  type: string; // like, follow, comment, mention, publish
  actorId: string;
  targetId: string;
  content: string;
  isRead: boolean;
  createdAt: string;
  actor?: UserProfileDto;
}

// ============================================================================
// Query DTOs
// ============================================================================

/**
 * Feed query parameters
 */
export interface FeedQuery extends PaginationQuery {
  gameType?: string;
  tags?: string[];
}

/**
 * Search query parameters
 */
export interface SearchQuery extends PaginationQuery {
  q: string;
  gameType?: string;
  tags?: string[];
}

// ============================================================================
// WebSocket DTOs
// ============================================================================

/**
 * WebSocket message format
 */
export interface WsMessage {
  type: string;
  data: any;
  ts: number;
}

// ============================================================================
// Error Codes
// ============================================================================

/**
 * Error code enumeration for API responses
 */
export enum ErrorCodes {
  // User errors (10000-19999)
  USER_NOT_FOUND = 10001,
  USER_EXISTS = 10002,
  INVALID_CREDENTIALS = 10003,
  TOKEN_EXPIRED = 10004,

  // Game errors (20000-29999)
  GAME_NOT_FOUND = 20001,
  GAME_GENERATION_FAILED = 20002,
  GAME_PUBLISH_FAILED = 20003,

  // Social errors (30000-39999)
  SOCIAL_ACTION_FAILED = 30001,
  COMMENT_NOT_FOUND = 30002,

  // AI errors (40000-49999)
  AI_ENGINE_ERROR = 40001,
  AI_GENERATION_TIMEOUT = 40002,
}
