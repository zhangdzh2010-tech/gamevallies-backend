# PlayForge User Service - Quick Start Guide

## 5-Minute Setup

### 1. Install Dependencies
```bash
npm install
```

### 2. Configure Environment
```bash
cp .env.example .env.local
```

Edit `.env.local`:
```env
DATABASE_URL=postgresql://user:password@localhost:5432/gamevallies_user_db
JWT_SECRET=your-secret-key-here
JWT_REFRESH_SECRET=your-refresh-secret-key
```

### 3. Start Database (Docker)
```bash
docker-compose up postgres redis -d
```

Or locally:
```bash
# Create database if using local PostgreSQL
createdb gamevallies_user_db
```

### 4. Run Migrations
```bash
npx prisma migrate dev
```

### 5. Start Development Server
```bash
npm run dev
```

Server runs on http://localhost:3001

## Testing the API

### 1. Register User
```bash
curl -X POST http://localhost:3001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "johndoe",
    "email": "john@example.com",
    "password": "password123",
    "displayName": "John Doe"
  }'
```

Response:
```json
{
  "accessToken": "eyJ...",
  "refreshToken": "550e8400...",
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

### 2. Login
```bash
curl -X POST http://localhost:3001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "account": "johndoe",
    "password": "password123"
  }'
```

### 3. Get Current User Profile
```bash
curl -X GET http://localhost:3001/api/v1/users/me \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### 4. Update Profile
```bash
curl -X PATCH http://localhost:3001/api/v1/users/profile \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "displayName": "John Updated",
    "bio": "Game developer"
  }'
```

### 5. Search Users
```bash
curl -X GET "http://localhost:3001/api/v1/users/search?q=john&page=1&limit=20"
```

## Common Commands

### Development
```bash
npm run dev              # Start dev server with watch
npm run build           # Build for production
npm start               # Start production build
npm test                # Run tests
npm run test:watch     # Run tests in watch mode
npm run test:cov       # Generate coverage report
npm run format          # Format code with Prettier
npm run lint            # Check code with ESLint
```

### Database
```bash
npx prisma studio                    # View database UI
npx prisma migrate dev              # Create and run migrations
npx prisma migrate deploy           # Run migrations (prod)
npx prisma db push                  # Push schema changes (dev)
npx prisma db seed                  # Run seed script
npx prisma migrate reset            # Reset database (dev only)
npx prisma generate                 # Generate Prisma client
```

### Docker
```bash
docker-compose up -d                # Start all services
docker-compose down                 # Stop all services
docker-compose logs -f              # View logs
docker-compose ps                   # List services
```

## Project Structure Quick Reference

```
src/
├── main.ts              # Entry point
├── app.module.ts        # Root module
├── auth/                # Authentication (register, login, jwt)
├── user/                # User management (profile, search)
├── prisma/              # Database (PrismaClient)
├── health/              # Health checks
├── common/              # Shared utils, decorators, filters
└── config/              # Configuration
```

## Environment Variables Explained

| Variable | Purpose | Example |
|----------|---------|---------|
| DATABASE_URL | PostgreSQL connection | postgresql://user:pass@localhost:5432/db |
| JWT_SECRET | JWT signing key | your-32-char-secret-key |
| JWT_REFRESH_SECRET | Refresh token key | your-refresh-secret-key |
| PORT | Server port | 3001 |
| NODE_ENV | Environment | development, production |
| REDIS_URL | Redis connection (optional) | redis://localhost:6379 |
| CORS_ORIGIN | Allowed origins | *, http://localhost:3000 |

## File Locations Reference

| File | Purpose |
|------|---------|
| `src/main.ts` | Application bootstrap |
| `src/app.module.ts` | Root module |
| `src/auth/auth.service.ts` | Login, register, tokens |
| `src/user/user.service.ts` | Profile, search, stats |
| `src/prisma/prisma.service.ts` | Database connection |
| `package.json` | Dependencies & scripts |
| `.env.local` | Environment variables |
| `Dockerfile` | Docker build config |
| `docker-compose.yml` | Local services setup |

## API Endpoints Cheat Sheet

### Auth
```
POST   /api/v1/auth/register    - Register user
POST   /api/v1/auth/login       - Login user
POST   /api/v1/auth/refresh     - Refresh token
POST   /api/v1/auth/logout      - Logout user
GET    /api/v1/auth/profile     - Get profile (protected)
```

### Users
```
GET    /api/v1/users/me                - Current user (protected)
GET    /api/v1/users/search           - Search users
GET    /api/v1/users/:id              - Get user
GET    /api/v1/users/:id/profile      - Get profile with stats
PATCH  /api/v1/users/profile          - Update profile (protected)
PATCH  /api/v1/users/:id/deactivate   - Deactivate (protected)
```

### Health
```
GET    /health       - Health status
GET    /health/ready - Readiness probe
GET    /health/live  - Liveness probe
```

## Troubleshooting

### Port 3001 Already in Use
```bash
# Find and kill process
lsof -i :3001
kill -9 <PID>
```

### Database Connection Error
```bash
# Check connection string in .env.local
# Ensure PostgreSQL is running
# Test connection:
psql $DATABASE_URL -c "SELECT 1"
```

### Module Not Found Error
```bash
# Clear cache and rebuild
rm -rf dist
npm run build
```

### Tests Failing
```bash
# Ensure test database is configured
# Run migrations
npx prisma migrate dev
# Run tests
npm test
```

## Next Steps

1. **Read Full Documentation**
   - API.md - Complete API reference
   - DEVELOPMENT.md - Development guide
   - DEPLOYMENT.md - Deployment options

2. **Add Features**
   - Create new modules in `src/`
   - Add DTOs for validation
   - Write unit tests in `test/`

3. **Deploy**
   - Build image: `docker build -t gamevallies-user-service .`
   - Push to registry
   - Deploy to cloud (AWS, K8s, etc.)

4. **Monitor**
   - Check logs: `npm run dev` or `docker-compose logs -f`
   - Use health endpoints for monitoring
   - Set up alerting

## Resources

- [NestJS Docs](https://docs.nestjs.com)
- [Prisma Docs](https://www.prisma.io/docs)
- [JWT Explained](https://jwt.io)
- [Passport.js](https://www.passportjs.org)

## Support

- Check logs: `docker-compose logs user-service`
- Review API.md for endpoint details
- Check DEVELOPMENT.md for setup issues
- Review error responses for specifics

Happy coding!
