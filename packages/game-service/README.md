# Game Service - PlayForge Backend

The Game Service is the core service for managing game generation, iteration, forking, and playback in PlayForge.

## Features

- **Game Generation**: AI-powered game code generation from descriptions
- **Game Iteration**: Iterative refinement of generated games with feedback
- **Game Forking**: Create forks of existing games with fork tracking
- **Game Publishing**: Publish games to the public marketplace
- **Real-time Updates**: WebSocket support for generation progress and notifications
- **Game Statistics**: Track plays, likes, forks, and comments
- **Bundle Management**: Version control for game code bundles

## Architecture

The service is built on NestJS with the following modules:

- **GameModule**: Core game creation, retrieval, publishing, and iteration
- **ForkModule**: Game forking functionality with lineage tracking
- **BundleModule**: Game code bundle storage and versioning
- **StatsModule**: Game statistics and metrics tracking
- **WebSocketModule**: Real-time communication with clients
- **PrismaModule**: Prisma/MySQL integration
- **BundleStorageModule**: Shared bundle storage abstraction backed by Prisma

## Getting Started

### Prerequisites

- Node.js 20+
- MySQL 8+
- Redis (optional, for caching)

### Installation

```bash
npm install
```

### Configuration

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
```

Key variables:
- `DATABASE_URL`: MySQL connection string
- `JWT_SECRET`: JWT signing secret
- `AI_ENGINE_URL`: AI Engine URL (default `http://localhost:8000`)
- `APP_URL`: This service's base URL, used to construct game preview links
- `CORS_ORIGIN`: Allowed CORS origin (default `*`)

### Running the Application

Development mode with hot reload:

```bash
npm run start:dev
```

Production mode:

```bash
npm run build
npm run start:prod
```

## API Endpoints

### Game Management

#### Create Game
```
POST /api/v1/games/generate
Authorization: Bearer <token>
Content-Type: application/json

{
  "description": "A platformer game with jumping mechanics"
}
```

Response:
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "gameId": "uuid"
  }
}
```

Notes:
- 生成进度默认通过 `game-service` 的 `/ws?token=<jwt_token>` 推送，不再返回单独的 `wsChannel`
- 客户端创建成功后，用当前登录用户的 JWT 订阅同一个 `/ws` 连接即可收到 `gen:progress` / `gen:complete`

#### Get Game Details
```
GET /api/v1/games/:id
```

#### Get Game for Playing
```
GET /api/v1/games/:id/play
```

Returns the HTML code for the game.

#### Publish Game
```
POST /api/v1/games/:id/publish
Authorization: Bearer <token>
Content-Type: application/json

{
  "title": "My Awesome Game",
  "description": "A fun platformer game",
  "tags": ["platformer", "jump"],
  "gameType": "2d"
}
```

#### Iterate Game
```
POST /api/v1/games/:id/iterate
Authorization: Bearer <token>
Content-Type: application/json

{
  "feedback": "Make the jump mechanic more responsive"
}
```

#### Delete Game
```
DELETE /api/v1/games/:id
Authorization: Bearer <token>
```

#### Get My Games
```
GET /api/v1/games/my?page=1&limit=10
Authorization: Bearer <token>
```

### Game Forking

#### Fork a Game
```
POST /api/v1/games/:id/fork
Authorization: Bearer <token>
```

#### Get Game Forks
```
GET /api/v1/games/:id/forks?page=1&limit=10
```

#### Get Fork Tree
```
GET /api/v1/games/:id/fork-tree
```

Returns the game with its parent and direct children.

#### Get Fork Lineage
```
GET /api/v1/games/:id/fork-lineage
```

Returns the complete ancestry chain.

## WebSocket Events

Connect to `ws://localhost:3002/ws?token=<jwt_token>`

### Server Events

- **gen:progress**: Generation progress update
  ```json
  {
    "type": "gen:progress",
    "gameId": "uuid",
    "data": {
      "progress": 60,
      "message": "代码生成失败，重试中（1/2）",
      "details": {
        "stage": "code_generating",
        "retry": 1,
        "maxRetries": 2,
        "attempt": 2,
        "maxAttempts": 3
      }
    },
    "stage": "code_generating",
    "percentage": 60,
    "details": {
      "stage": "code_generating",
      "retry": 1,
      "maxRetries": 2,
      "attempt": 2,
      "maxAttempts": 3
    },
    "timestamp": 1234567890
  }
  ```

  Public generation stage → percentage mapping:

  | Stage | % |
  |---|---|
  | 理解游戏需求 (understanding) | 15 |
  | 构建游戏设计 (designing) | 35 |
  | 生成游戏代码 (generating) | 60 |
  | 质量校验与修复 (validating) | 85 |
  | 发布生成结果 (finalizing) | 95 |

  Notes:
  - `stage` 对外统一返回这 5 个 display stage key。
  - 更细的内部 raw stage 仍保留在 `details.rawStage` 中，仅供排障使用。

- **gen:complete**: Generation completed
  ```json
  {
    "type": "gen:complete",
    "gameId": "uuid",
    "data": {
      "success": true,
      "game": {
        "id": "uuid",
        "gameUrl": "http://.../games/uuid/index.html",
        "previewUrl": "http://.../games/uuid/preview",
        "status": "ready"
      },
      "error": null
    },
    "previewUrl": "http://...",
    "status": "success",
    "timestamp": 1234567890
  }
  ```

- **gen:error**: Terminal generation failure
  ```json
  {
    "type": "gen:error",
    "gameId": "uuid",
    "data": {
      "success": false,
      "error": "Generated code failed QA",
      "details": {
        "stage": "qa_checking",
        "retryCount": 3
      }
    },
    "stage": "qa_checking",
    "details": {
      "stage": "qa_checking",
      "retryCount": 3
    },
    "status": "error",
    "timestamp": 1234567890
  }
  ```

- **notification**: User notifications
  ```json
  {
    "type": "error|success|info|warning",
    "message": "Description",
    "gameId": "uuid",
    "timestamp": 1234567890
  }
  ```

  Generation failures still emit `notification` with `type: "error"` for backward compatibility, but the canonical terminal event is now `gen:error`.

- **game:update**: Real-time game updates
- **game:stats**: Broadcasted game statistics

### Client Events

- **ping**: Health check (server responds with **pong**)

## Database Schema

### Games Table (MySQL)
- `id` (UUID, Primary Key)
- `authorId` (UUID, Foreign Key to users)
- `title` (String)
- `description` (String)
- `tags` (String array)
- `gameType` (String)
- `status` (String: draft, published, banned, failed, generating)
- `currentVersion` (Integer)
- `forkedFrom` (UUID, nullable)
- `forkDepth` (Integer)
- `playCount` (Integer)
- `likeCount` (Integer)
- `forkCount` (Integer)
- `commentCount` (Integer)
- `avgPlayTime` (Float)
- `totalPlayTime` (Float)
- `publishedAt` (DateTime, nullable)
- `deletedAt` (DateTime, nullable)
- `createdAt` (DateTime)
- `updatedAt` (DateTime)
- `failedStage` (String, nullable)
- `failedReason` (Text, nullable)
- `retryCount` (Integer)
- `lastErrorAt` (DateTime, nullable)

### Game Bundles Table (MySQL)
- `id` (UUID, Primary Key)
- `gameId` (String, indexed)
- `version` (Integer)
- `htmlCode` (String)
- `cssCode` (String)
- `jsCode` (String)
- `metadata` (Mixed)
- `previewUrl` (String)
- `generatedAt` (DateTime)
- `createdAt` (DateTime)
- `updatedAt` (DateTime)

## Error Handling

The service returns standard HTTP error codes:

- `400 Bad Request`: Invalid input or request format
- `401 Unauthorized`: Missing or invalid authentication
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource not found
- `500 Internal Server Error`: Server error

Error responses include:
```json
{
  "statusCode": 400,
  "message": "Error description",
  "error": "Bad Request"
}
```

## Performance Considerations

- Game generation and iteration are asynchronous (via `setImmediate`); clients track progress via WebSocket
- Pipeline calls go to `AI_ENGINE_URL/api/v1/ai/pipeline/run` (generate) and `/pipeline/iterate` (iterate)
- Bundle retrieval uses MySQL indexes on `gameId` and a unique key on `(gameId, version)`
- Pagination limits enforced (max 100 items per page)
- WebSocket connections validated on connection using JWT Base64 decode

## Security

- JWT authentication required for protected endpoints
- Token validation on WebSocket connection
- CORS enabled for cross-origin requests
- Input validation with class-validator
- No sensitive data exposed in responses
- Soft deletes for game records (status = 'banned')

## Testing

```bash
npm run test
npm run test:watch
npm run test:cov
npm run test:debug
```

## Building for Production

```bash
npm run build
npm run start:prod
```

Docker build:
```bash
docker build -t gamevallies-game-service .
docker run -p 3002:3002 --env-file .env gamevallies-game-service
```

## Logging

Logs are output to console with the following levels:
- `error`: Errors and failures
- `warn`: Warnings and notices
- `log`: General application logs
- `debug`: Detailed debug information

Each log includes context (module name, operation, timestamp).

## Contributing

Follow the NestJS style guide and ensure all code is properly typed with TypeScript.

## License

UNLICENSED - Internal PlayForge Project
