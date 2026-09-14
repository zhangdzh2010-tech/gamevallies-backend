import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import { AppModule } from './app.module';

function resolveCorsOrigin(): string | string[] {
  const raw = (process.env.CORS_ORIGINS || process.env.CORS_ORIGIN || '*').trim();
  let listed: string[] = [];
  if (raw.startsWith('[')) {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (Array.isArray(parsed)) {
        listed = parsed.map((item) => String(item).trim()).filter(Boolean);
      }
    } catch {
      listed = [];
    }
  }
  if (listed.length === 0) {
    listed = raw.split(',').map((item) => item.trim()).filter(Boolean);
  }
  if (listed.length === 0 || listed.includes('*')) {
    return '*';
  }
  return listed.length === 1 ? listed[0] : listed;
}

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  const port = parseInt(process.env.PORT || '3003', 10);

  // Set global prefix for API routes
  app.setGlobalPrefix('api/v1');

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
    }),
  );

  app.enableCors({
    origin: resolveCorsOrigin(),
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization'],
  });

  await app.listen(port);
  console.log(`[${new Date().toISOString()}] Social Service listening on port ${port}`);
}

bootstrap().catch((err) => {
  console.error('Failed to start Social Service:', err);
  process.exit(1);
});
