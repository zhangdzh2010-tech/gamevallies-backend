# PlayForge Backend - Startup Guide

This guide explains the fixes applied to the PlayForge backend and how to use the new one-click startup script.

## Problem Analysis & Fixes

### Issue 1: Inconsistent Dockerfile Build Contexts

The original docker-compose.yml had inconsistent build patterns:
- `user-service`: `context: .` + `dockerfile: packages/user-service/Dockerfile` ✓ Correct
- `game-service`: `context: .` + `dockerfile: packages/game-service/Dockerfile` ✗ Dockerfile expected local context
- `social-service`: `context: .` + `dockerfile: packages/social-service/Dockerfile` ✗ Dockerfile expected local context
- `feed-service`: `context: .` + `dockerfile: packages/feed-service/Dockerfile` ✗ Dockerfile expected local context

The game, social, and feed service Dockerfiles expected to be built from their own directories (they copied `package*.json ./` expecting local package.json).

### Solution

All Dockerfiles have been rewritten to support monorepo root as the build context:

1. **Copy root files**: `package.json`, `package-lock.json`, `tsconfig.base.json`, `prisma/`
2. **Copy shared package**: `COPY packages/shared ./packages/shared`
3. **Copy specific service**: `COPY packages/{service-name} ./packages/{service-name}`
4. **Install & build**: Install from root, then build within service directory
5. **Runtime stage**: Copy only necessary artifacts, include Prisma client

### Issue 2: Missing Prisma Configuration

The original user-service Dockerfile was missing:
- Copy of `packages/shared` (needed for shared types/utilities)
- Prisma client generation (`npx prisma generate`)
- Prisma schema directory in runtime

### Solution

All service Dockerfiles now include:
- Multi-stage build with proper shared package setup
- `RUN npx prisma generate` in builder stage
- `.prisma` client directory copied to runtime stage
- Full prisma directory for migrations

### Issue 3: Missing Environment Configuration

Services lacked proper `.env` files for local and Docker execution.

### Solution

Created comprehensive `.env` files:
- **Root `.env`**: Global configuration for Docker Compose
- **Service `.env`**: Individual service configurations for local development
- All environment variables properly configured for localhost access

## File Structure

```
playforge-backend/
├── .env                           # Root environment variables
├── start.sh                       # One-click startup script (EXECUTABLE)
├── docker-compose.yml             # Docker Compose configuration (unchanged)
├── packages/
│   ├── user-service/
│   │   ├── Dockerfile            # FIXED: Now supports monorepo root context
│   │   └── .env                  # NEW: Local development environment
│   ├── game-service/
│   │   ├── Dockerfile            # REWRITTEN: Monorepo root context support
│   │   └── .env                  # NEW: Local development environment
│   ├── social-service/
│   │   ├── Dockerfile            # REWRITTEN: Monorepo root context support
│   │   └── .env                  # NEW: Local development environment
│   ├── feed-service/
│   │   ├── Dockerfile            # REWRITTEN: Monorepo root context support
│   │   └── .env                  # NEW: Local development environment
│   ├── shared/                    # Now properly included in Docker builds
│   │   ├── package.json
│   │   └── src/
│   └── ai-engine/
│       ├── Dockerfile            # (unchanged)
│       └── .env                  # NEW: Local development environment
└── prisma/
    ├── schema.prisma             # Now properly available in Docker runtime
    └── migrations/
```

## Quick Start

### Option 1: Full Docker Compose (Default)

Everything runs in containers:

```bash
./start.sh docker
# or simply
./start.sh
```

This will:
1. Check prerequisites (Docker, Docker Compose)
2. Setup environment variables
3. Start databases (PostgreSQL, MongoDB, Redis)
4. Build and start all microservices and API gateway
5. Display access endpoints

### Option 2: Local Development (Recommended)

Databases run in Docker, services run locally on your machine:

```bash
./start.sh local
```

Perfect for development because:
- Services run with `npm run dev` for hot reload
- Direct access to Node.js for debugging
- Faster iteration cycles
- Easier to attach debuggers

### Option 3: Database Only

Just start the databases:

```bash
./start.sh db-only
```

Then manually start services with:
```bash
npm run dev:all
```

### Stop All Services

```bash
./start.sh stop
```

### Clean Up (Delete Data Volumes)

```bash
./start.sh clean
```

### View Logs

```bash
./start.sh logs
# or
docker compose logs -f
```

## Environment Variables

### Root `.env`
```env
NODE_ENV=development
DATABASE_URL=postgresql://playforge:playforge_dev_2026@localhost:5432/playforge
MONGO_URL=mongodb://playforge:playforge_dev_2026@localhost:27017/playforge?authSource=admin
REDIS_URL=redis://:playforge_dev_2026@localhost:6379
JWT_SECRET=playforge_jwt_secret_dev_2026
JWT_REFRESH_SECRET=playforge_refresh_secret_dev_2026
LLM_MODE=mock
```

### Service-Specific `.env` Examples

**user-service** (port 3001):
```env
PORT=3001
DATABASE_URL=postgresql://playforge:playforge_dev_2026@localhost:5432/playforge
REDIS_URL=redis://:playforge_dev_2026@localhost:6379
JWT_SECRET=playforge_jwt_secret_dev_2026
JWT_REFRESH_SECRET=playforge_refresh_secret_dev_2026
```

**game-service** (port 3002):
```env
PORT=3002
DATABASE_URL=postgresql://playforge:playforge_dev_2026@localhost:5432/playforge
MONGO_URL=mongodb://playforge:playforge_dev_2026@localhost:27017/playforge?authSource=admin
REDIS_URL=redis://:playforge_dev_2026@localhost:6379
AI_ENGINE_URL=http://localhost:8000
JWT_SECRET=playforge_jwt_secret_dev_2026
```

**social-service** (port 3003):
```env
PORT=3003
DATABASE_URL=postgresql://playforge:playforge_dev_2026@localhost:5432/playforge
REDIS_URL=redis://:playforge_dev_2026@localhost:6379
JWT_SECRET=playforge_jwt_secret_dev_2026
```

**feed-service** (port 3004):
```env
PORT=3004
DATABASE_URL=postgresql://playforge:playforge_dev_2026@localhost:5432/playforge
REDIS_URL=redis://:playforge_dev_2026@localhost:6379
JWT_SECRET=playforge_jwt_secret_dev_2026
```

**ai-engine** (port 8000):
```env
ENVIRONMENT=development
MONGO_URL=mongodb://playforge:playforge_dev_2026@localhost:27017/playforge?authSource=admin
REDIS_URL=redis://:playforge_dev_2026@localhost:6379
LLM_MODE=mock
```

## Service Endpoints

Once started, services are available at:

| Service | URL | Port |
|---------|-----|------|
| API Gateway | http://localhost:80 | 80 |
| User Service | http://localhost:3001 | 3001 |
| Game Service | http://localhost:3002 | 3002 |
| Social Service | http://localhost:3003 | 3003 |
| Feed Service | http://localhost:3004 | 3004 |
| AI Engine | http://localhost:8000 | 8000 |
| AI Engine Docs | http://localhost:8000/docs | 8000 |

### Database Connections

| Database | Host | Port | User | Password |
|----------|------|------|------|----------|
| PostgreSQL | localhost | 5432 | playforge | playforge_dev_2026 |
| MongoDB | localhost | 27017 | playforge | playforge_dev_2026 |
| Redis | localhost | 6379 | (auth) | playforge_dev_2026 |

## Dockerfile Changes

### Before (Broken)

**game-service/Dockerfile** (excerpt):
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./          # Wrong! Looking for local package.json
COPY src ./src                 # Wrong! Should be packages/game-service/src
RUN npm ci
COPY . .
RUN npm run build
# ... more issues
```

### After (Fixed)

**game-service/Dockerfile** (excerpt):
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./      # Root package.json
COPY packages/shared ./packages/shared       # Shared package
COPY packages/game-service ./packages/game-service
COPY tsconfig.base.json ./
COPY prisma ./prisma
RUN npm install --legacy-peer-deps
RUN npx prisma generate                      # Generate Prisma client
WORKDIR /app/packages/game-service
RUN npm run build

FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
COPY packages/shared/package.json ./packages/shared/
COPY packages/game-service/package.json ./packages/game-service/
RUN npm install --omit=dev --legacy-peer-deps
COPY --from=builder /app/packages/game-service/dist ./packages/game-service/dist
COPY --from=builder /app/packages/shared/dist ./packages/shared/dist
COPY --from=builder /app/node_modules/.prisma ./node_modules/.prisma
COPY prisma ./prisma
WORKDIR /app/packages/game-service
EXPOSE 3002
HEALTHCHECK ...
CMD ["node", "dist/main.js"]
```

## Prerequisites

- **Docker**: https://docs.docker.com/get-docker/
- **Docker Compose**: Included with Docker Desktop
- **Node.js 20+**: https://nodejs.org/ (for local mode only)
- **Python 3**: https://www.python.org/downloads/ (for AI Engine in local mode)

## Troubleshooting

### Docker Engine Not Running
```bash
# macOS
open /Applications/Docker.app

# Linux
sudo systemctl start docker
```

### Port Already in Use

If a port is already in use, modify the port mappings in `docker-compose.yml`:

```yaml
services:
  user-service:
    ports:
      - "3101:3001"  # Change 3101 to desired external port
```

### Database Connection Issues

Ensure databases are healthy:
```bash
docker compose ps
# Check HEALTH column - should show "healthy"

# View logs
docker compose logs postgres
docker compose logs mongo
docker compose logs redis
```

### Service Startup Errors

Check service logs:
```bash
docker compose logs user-service
docker compose logs game-service
# etc.
```

### Node Modules Issues

Clear and reinstall:
```bash
rm -rf node_modules
npm install --legacy-peer-deps
```

For Docker containers:
```bash
docker compose down -v  # Remove volumes
./start.sh docker       # Rebuild and restart
```

## Development Workflow

### 1. Start Infrastructure

```bash
./start.sh db-only
```

### 2. Start Services Locally

In one terminal:
```bash
npm run dev:user-service
```

In another:
```bash
npm run dev:game-service
```

Or start all at once:
```bash
npm run dev:all
```

### 3. Make Changes

Edit source code in `packages/{service}/src`

### 4. Services Auto-Reload

Services should reload automatically with hot-reload enabled.

### 5. Debug with Inspector

Start a single service with Node inspector:
```bash
node --inspect dist/main.js
```

Then attach your debugger (VS Code, Chrome DevTools, etc.)

## Production Considerations

### Security

- Change all default credentials in `.env`
- Use strong JWT secrets
- Enable HTTPS on API Gateway
- Use environment-specific `.env` files

### Performance

- Increase database resource limits in docker-compose.yml
- Enable Redis persistence
- Configure PostgreSQL for production workloads
- Add reverse proxy caching

### Monitoring

- Add health check endpoints
- Configure logging aggregation
- Set up alerting
- Monitor database performance

## Additional Resources

- Docker Compose Docs: https://docs.docker.com/compose/
- NestJS Docs: https://docs.nestjs.com/
- Prisma Docs: https://www.prisma.io/docs/
- PostgreSQL Docs: https://www.postgresql.org/docs/
- MongoDB Docs: https://docs.mongodb.com/
- Redis Docs: https://redis.io/docs/

## Summary of Changes

| File | Change | Status |
|------|--------|--------|
| packages/user-service/Dockerfile | Added shared package + prisma | FIXED |
| packages/game-service/Dockerfile | Full rewrite for monorepo context | REWRITTEN |
| packages/social-service/Dockerfile | Full rewrite for monorepo context | REWRITTEN |
| packages/feed-service/Dockerfile | Full rewrite for monorepo context | REWRITTEN |
| .env | Created root environment config | NEW |
| packages/user-service/.env | Created service env | NEW |
| packages/game-service/.env | Created service env | NEW |
| packages/social-service/.env | Created service env | NEW |
| packages/feed-service/.env | Created service env | NEW |
| packages/ai-engine/.env | Created service env | NEW |
| start.sh | One-click startup script | NEW |
| STARTUP_GUIDE.md | This documentation | NEW |

All services now build correctly from the monorepo root context with proper dependency management and Prisma client generation!
