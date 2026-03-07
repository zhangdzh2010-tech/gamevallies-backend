# Development Guide

## Project Structure

```
user-service/
├── src/
│   ├── main.ts                 # Application entry point
│   ├── app.module.ts           # Root module
│   ├── auth/                   # Authentication module
│   │   ├── auth.module.ts
│   │   ├── auth.service.ts
│   │   ├── auth.controller.ts
│   │   ├── jwt.strategy.ts
│   │   ├── jwt-auth.guard.ts
│   │   └── dto/
│   ├── user/                   # User module
│   │   ├── user.module.ts
│   │   ├── user.service.ts
│   │   ├── user.controller.ts
│   │   └── dto/
│   ├── prisma/                 # Database
│   │   ├── prisma.module.ts
│   │   └── prisma.service.ts
│   ├── common/                 # Shared utilities
│   │   ├── decorators/
│   │   ├── filters/
│   │   ├── interceptors/
│   │   ├── utils/
│   │   └── constants/
│   └── config/                 # Configuration
├── test/                        # Tests
├── Dockerfile                   # Docker configuration
├── package.json
├── tsconfig.json
├── jest.config.js
└── README.md

## Setup

1. Install dependencies:
```bash
npm install
```

2. Create `.env.local`:
```bash
cp .env.example .env.local
```

3. Configure database:
```env
DATABASE_URL=postgresql://user:password@localhost:5432/playforge_user_db
JWT_SECRET=your-secret-key
```

4. Run Prisma migrations:
```bash
npx prisma migrate dev
```

## Development Server

```bash
npm run dev
```

Server runs on `http://localhost:3001`

## Building

```bash
npm run build
npm start
```

## Testing

```bash
npm test                # Run tests once
npm run test:watch    # Run tests in watch mode
npm run test:cov      # Generate coverage report
```

## Code Style

Project uses ESLint and Prettier. Format code:
```bash
npm run format
```

## Adding New Features

### 1. Create a New Module

```bash
mkdir -p src/feature/{controllers,services,dto}
```

### 2. Create Service

```typescript
import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class FeatureService {
  constructor(private prisma: PrismaService) {}

  // Implementation
}
```

### 3. Create Controller

```typescript
import { Controller, Get } from '@nestjs/common';
import { FeatureService } from './feature.service';

@Controller('feature')
export class FeatureController {
  constructor(private featureService: FeatureService) {}

  @Get()
  async getAll() {
    // Implementation
  }
}
```

### 4. Create Module

```typescript
import { Module } from '@nestjs/common';
import { FeatureController } from './feature.controller';
import { FeatureService } from './feature.service';

@Module({
  controllers: [FeatureController],
  providers: [FeatureService],
  exports: [FeatureService],
})
export class FeatureModule {}
```

### 5. Register in App Module

```typescript
import { FeatureModule } from './feature/feature.module';

@Module({
  imports: [
    // ... other imports
    FeatureModule,
  ],
})
export class AppModule {}
```

## Database Migrations

### Create Migration
```bash
npx prisma migrate dev --name migration_name
```

### Apply Migrations
```bash
npx prisma migrate deploy
```

### Rollback
```bash
npx prisma migrate resolve --rolled-back migration_name
```

### View Database
```bash
npx prisma studio
```

## Debugging

### Enable Debug Logging

Add to `.env.local`:
```env
DEBUG=*
```

### VS Code Debug Configuration

Create `.vscode/launch.json`:
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "type": "node",
      "request": "launch",
      "name": "Debug",
      "skipFiles": ["<node_internals>/**"],
      "program": "${workspaceFolder}/dist/main.js",
      "preLaunchTask": "tsc: build",
      "outFiles": ["${workspaceFolder}/dist/**/*.js"]
    }
  ]
}
```

## Environment Variables

### Development
```env
NODE_ENV=development
PORT=3001
DATABASE_URL=postgresql://user:password@localhost:5432/playforge_user_db
JWT_SECRET=dev-secret-key
JWT_REFRESH_SECRET=dev-refresh-secret-key
CORS_ORIGIN=http://localhost:3000
```

### Testing
```env
NODE_ENV=test
DATABASE_URL=postgresql://user:password@localhost:5432/playforge_user_test_db
JWT_SECRET=test-secret-key
```

### Production
```env
NODE_ENV=production
PORT=3001
DATABASE_URL=postgresql://prod-user:prod-password@prod-db:5432/playforge_user_db
JWT_SECRET=<strong-random-key>
JWT_REFRESH_SECRET=<strong-random-key>
CORS_ORIGIN=https://playforge.com
```

## Troubleshooting

### Database Connection Issues
1. Check `DATABASE_URL` format
2. Ensure PostgreSQL is running
3. Verify credentials and permissions
4. Check firewall settings

### Port Already in Use
```bash
# Find process using port 3001
lsof -i :3001

# Kill process
kill -9 <PID>
```

### Module Not Found
1. Check import paths
2. Ensure module is exported
3. Clear `dist` folder: `rm -rf dist`
4. Rebuild: `npm run build`

### Tests Failing
1. Check test environment setup
2. Verify mock implementations
3. Check database fixtures
4. Review test logs

## Performance Optimization

### Database
- Add proper indexes in Prisma schema
- Use select to only fetch needed fields
- Implement caching with Redis
- Use pagination for large datasets

### Code
- Use lazy loading for modules
- Implement request compression
- Use clustering for multi-core
- Monitor memory usage

## Security Best Practices

1. **Environment Variables**
   - Never commit `.env` files
   - Use strong JWT secrets
   - Rotate secrets regularly

2. **Authentication**
   - Implement rate limiting
   - Add password strength requirements
   - Use HTTPS in production
   - Implement refresh token rotation

3. **Database**
   - Use connection pooling
   - Validate all inputs
   - Use prepared statements (Prisma does this)
   - Encrypt sensitive data

4. **API**
   - Validate request size limits
   - Implement CORS properly
   - Add request validation
   - Log security events

## Useful Commands

```bash
# Development
npm run dev

# Build
npm run build

# Start production
npm start

# Run tests
npm test

# Generate coverage
npm run test:cov

# Format code
npm run format

# View database
npx prisma studio

# Create migration
npx prisma migrate dev --name name

# Reset database (dev only)
npx prisma migrate reset
```

## References

- [NestJS Documentation](https://docs.nestjs.com)
- [Prisma Documentation](https://www.prisma.io/docs)
- [JWT Documentation](https://jwt.io)
- [Passport.js Documentation](https://www.passportjs.org)

## Contributing

1. Create feature branch: `git checkout -b feature/new-feature`
2. Make changes and commit: `git commit -am 'Add new feature'`
3. Push to branch: `git push origin feature/new-feature`
4. Open pull request

## Support

For issues and questions, please open an issue on the project repository.
