export const JWT_EXPIRATION_TIMES = {
  ACCESS_TOKEN: '15m',
  REFRESH_TOKEN: '7d',
};

export const PASSWORD_CONFIG = {
  MIN_LENGTH: 6,
  MAX_LENGTH: 128,
  SALT_ROUNDS: 10,
};

export const USERNAME_CONFIG = {
  MIN_LENGTH: 3,
  MAX_LENGTH: 30,
};

export const EMAIL_CONFIG = {
  MAX_LENGTH: 255,
};

export const USER_ROLES = {
  ADMIN: 'ADMIN',
  MODERATOR: 'MODERATOR',
  USER: 'USER',
};

export const HTTP_MESSAGES = {
  SUCCESS: 'Operation completed successfully',
  CREATED: 'Resource created successfully',
  UPDATED: 'Resource updated successfully',
  DELETED: 'Resource deleted successfully',
  USER_NOT_FOUND: 'User not found',
  INVALID_CREDENTIALS: 'Invalid credentials',
  UNAUTHORIZED: 'Unauthorized access',
  FORBIDDEN: 'Forbidden access',
  BAD_REQUEST: 'Bad request',
  CONFLICT: 'Resource conflict',
};

export const PAGINATION_CONFIG = {
  DEFAULT_PAGE: 1,
  DEFAULT_LIMIT: 20,
  MAX_LIMIT: 100,
};
