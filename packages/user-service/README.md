# PlayForge User Service

User authentication and profile management microservice for PlayForge backend.

## Features

- User registration and authentication with JWT
- Refresh token management
- User profile management
- User search functionality
- Password hashing with bcryptjs
- Prisma ORM for database operations

## Prerequisites

- Node.js 20+
- PostgreSQL database
- Redis (optional, for caching)

## Installation

```bash
npm install
# or
yarn install
```

## Environment Setup

Copy `.env.example` to `.env.local` and configure:

```bash
cp .env.example .env.local
```

Required environment variables:
- `DATABASE_URL` - PostgreSQL connection string
- `JWT_SECRET` - Secret key for JWT signing
- `JWT_REFRESH_SECRET` - Secret key for refresh token signing
- `PORT` - Server port (default: 3001)

## Running the Application

### Development

```bash
npm run dev
# or
yarn dev
```

### Production

```bash
npm run build
npm start
# or
yarn build
yarn start
```

## Testing

```bash
npm test
npm run test:watch
npm run test:cov
```

## API Endpoints

### Authentication

- `POST /api/v1/auth/register` - Register new user
- `POST /api/v1/auth/login` - Login user
- `POST /api/v1/auth/refresh` - Refresh access token
- `POST /api/v1/auth/logout` - Logout user

### Users

- `GET /api/v1/users/me` - Get current user profile
- `GET /api/v1/users/search?q=query` - Search users
- `GET /api/v1/users/:id/profile` - Get user profile
- `GET /api/v1/users/:id` - Get user by ID
- `PATCH /api/v1/users/profile` - Update current user profile

## Database Schema

The service uses Prisma with the following main tables:
- `users` - User accounts
- `refreshTokens` - Refresh token storage
- `userGames` - User game relations
- `gameSessions` - Game session tracking
- `follows` - User follows

## Architecture

```
src/
├── main.ts              # Application bootstrap
├── app.module.ts        # Root module
├── auth/                # Authentication module
│   ├── auth.service.ts
│   ├── auth.controller.ts
│   ├── jwt.strategy.ts
│   └── dto/
├── user/                # User module
│   ├── user.service.ts
│   ├── user.controller.ts
│   └── dto/
└── prisma/              # Prisma configuration
    ├── prisma.service.ts
    └── prisma.module.ts
```

## Error Handling

All errors are returned in the following format:

```json
{
  "statusCode": 400,
  "message": "Error message",
  "error": "BadRequest"
}
```

## Security

- Passwords are hashed using bcryptjs with salt rounds of 10
- JWTs are signed with configurable secret keys
- CORS is enabled with configurable origins
- Global validation pipe validates all inputs

## Docker

Build and run with Docker:

```bash
docker build -t gamevallies-user-service .
docker run -p 3001:3001 --env-file .env.local gamevallies-user-service
```

## Contributing

Follow the project's code style and write tests for new features.

## License

MIT
