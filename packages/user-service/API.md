# PlayForge User Service - API Documentation

## Base URL
```
http://localhost:3001/api/v1
```

## Authentication

All protected endpoints require a Bearer token in the Authorization header:
```
Authorization: Bearer <accessToken>
```

## Endpoints

### Authentication Endpoints

#### 1. Register User
**POST** `/auth/register`

Register a new user account.

**Request Body:**
```json
{
  "username": "johndoe",
  "email": "john@example.com",
  "phone": "+1234567890",
  "password": "securePassword123",
  "displayName": "John Doe"
}
```

**Response (201 Created):**
```json
{
  "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refreshToken": "550e8400-e29b-41d4-a716-446655440000",
  "expiresIn": 900,
  "user": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "username": "johndoe",
    "email": "john@example.com",
    "displayName": "John Doe",
    "role": "USER"
  }
}
```

**Validation Rules:**
- `username`: 3-30 chars, alphanumeric/underscore/hyphen only
- `email`: Valid email format (optional)
- `phone`: E.164 format (optional)
- `password`: Min 6 chars, max 128 chars
- `displayName`: Max 100 chars (optional)

**Error Responses:**
- 400: Validation error
- 409: Username or email already exists

---

#### 2. Login User
**POST** `/auth/login`

Authenticate and login a user.

**Request Body:**
```json
{
  "account": "johndoe",
  "password": "securePassword123"
}
```

**Response (200 OK):**
```json
{
  "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refreshToken": "550e8400-e29b-41d4-a716-446655440000",
  "expiresIn": 900,
  "user": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "username": "johndoe",
    "email": "john@example.com",
    "displayName": "John Doe",
    "role": "USER"
  }
}
```

**Notes:**
- `account` can be either username or email
- Password is case-sensitive

**Error Responses:**
- 401: Invalid credentials

---

#### 3. Refresh Token
**POST** `/auth/refresh`

Generate a new access token using a refresh token.

**Request Body:**
```json
{
  "refreshToken": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Response (200 OK):**
```json
{
  "accessToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expiresIn": 900
}
```

**Error Responses:**
- 401: Invalid or expired refresh token

---

#### 4. Logout User
**POST** `/auth/logout`

Logout the current user and revoke their refresh token.

**Headers:**
```
Authorization: Bearer <accessToken>
X-Refresh-Token: <refreshToken>
```

**Response (200 OK):**
```json
{
  "message": "Logged out successfully"
}
```

**Error Responses:**
- 401: Unauthorized

---

### User Endpoints

#### 1. Get Current User Profile
**GET** `/users/me`

Get the authenticated user's profile.

**Headers:**
```
Authorization: Bearer <accessToken>
```

**Response (200 OK):**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "username": "johndoe",
  "email": "john@example.com",
  "displayName": "John Doe",
  "bio": "Game enthusiast",
  "avatarUrl": "https://example.com/avatar.jpg",
  "isActive": true,
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Error Responses:**
- 401: Unauthorized
- 404: User not found

---

#### 2. Get User Profile by ID
**GET** `/users/:id/profile`

Get a user's public profile with statistics.

**Path Parameters:**
- `id` (string, required): User ID

**Response (200 OK):**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "username": "johndoe",
  "displayName": "John Doe",
  "bio": "Game enthusiast",
  "avatarUrl": "https://example.com/avatar.jpg",
  "isActive": true,
  "role": "USER",
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z",
  "stats": {
    "gameCount": 15,
    "totalPlays": 127,
    "followerCount": 42,
    "followingCount": 18
  }
}
```

**Error Responses:**
- 404: User not found

---

#### 3. Get User by ID
**GET** `/users/:id`

Get basic user information by ID.

**Path Parameters:**
- `id` (string, required): User ID

**Response (200 OK):**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "username": "johndoe",
  "email": "john@example.com",
  "displayName": "John Doe",
  "bio": "Game enthusiast",
  "avatarUrl": "https://example.com/avatar.jpg",
  "isActive": true,
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-20T15:45:00Z"
}
```

**Error Responses:**
- 404: User not found

---

#### 4. Update User Profile
**PATCH** `/users/profile`

Update the current user's profile.

**Headers:**
```
Authorization: Bearer <accessToken>
```

**Request Body:**
```json
{
  "displayName": "John Doe Updated",
  "bio": "Passionate game developer",
  "avatarUrl": "https://example.com/new-avatar.jpg"
}
```

**Response (200 OK):**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "username": "johndoe",
  "email": "john@example.com",
  "displayName": "John Doe Updated",
  "bio": "Passionate game developer",
  "avatarUrl": "https://example.com/new-avatar.jpg",
  "isActive": true,
  "createdAt": "2024-01-15T10:30:00Z",
  "updatedAt": "2024-01-21T12:00:00Z"
}
```

**Validation Rules:**
- `displayName`: 1-100 chars (optional)
- `bio`: Max 500 chars (optional)
- `avatarUrl`: Valid URL (optional)

**Error Responses:**
- 400: Validation error
- 401: Unauthorized
- 404: User not found

---

#### 5. Search Users
**GET** `/users/search`

Search for users by username or display name.

**Query Parameters:**
- `q` (string, required): Search query
- `page` (number, optional): Page number (default: 1)
- `limit` (number, optional): Results per page (default: 20, max: 100)

**Example Request:**
```
GET /users/search?q=john&page=1&limit=20
```

**Response (200 OK):**
```json
{
  "data": [
    {
      "id": "123e4567-e89b-12d3-a456-426614174000",
      "username": "johndoe",
      "displayName": "John Doe",
      "avatarUrl": "https://example.com/avatar.jpg",
      "bio": "Game enthusiast"
    },
    {
      "id": "223e4567-e89b-12d3-a456-426614174001",
      "username": "johnsmith",
      "displayName": "John Smith",
      "avatarUrl": "https://example.com/avatar2.jpg",
      "bio": "Game developer"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 2,
    "pages": 1
  }
}
```

**Error Responses:**
- 400: Empty search query

---

#### 6. Deactivate User
**PATCH** `/users/:id/deactivate`

Deactivate a user account (only own account).

**Headers:**
```
Authorization: Bearer <accessToken>
```

**Path Parameters:**
- `id` (string, required): User ID (must be own ID)

**Response (200 OK):**
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "username": "johndoe",
  "isActive": false
}
```

**Error Responses:**
- 401: Unauthorized
- 403: Can only deactivate own account
- 404: User not found

---

## Error Response Format

All errors follow this format:

```json
{
  "statusCode": 400,
  "message": "Error description",
  "error": "BadRequest"
}
```

### Common HTTP Status Codes

- **200**: Success
- **201**: Created
- **400**: Bad Request - Validation error
- **401**: Unauthorized - Invalid or missing token
- **403**: Forbidden - Insufficient permissions
- **404**: Not Found - Resource not found
- **409**: Conflict - Resource already exists
- **500**: Internal Server Error

---

## Authentication Flow

1. User registers or logs in
2. Server returns `accessToken` and `refreshToken`
3. Client stores tokens securely
4. Client includes `accessToken` in Authorization header
5. When `accessToken` expires, use `refreshToken` to get new one
6. On logout, refresh token is revoked

---

## Token Details

### Access Token
- **Type**: JWT
- **Expiration**: 15 minutes
- **Payload**: `{sub: userId, username, role}`

### Refresh Token
- **Type**: UUID
- **Expiration**: 7 days
- **Stored**: Database (can be revoked)
- **Usage**: Obtain new access token

---

## Rate Limiting

Currently no rate limiting is implemented. This should be added in production.

---

## CORS Headers

Default CORS is enabled for all origins. Configure `CORS_ORIGIN` environment variable.

---

## Pagination

Search endpoints support pagination:
- Default page: 1
- Default limit: 20
- Maximum limit: 100

Example:
```
GET /users/search?q=test&page=2&limit=10
```
