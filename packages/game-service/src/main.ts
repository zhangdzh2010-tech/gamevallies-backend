import { NestFactory } from '@nestjs/core';
import { RequestMethod, ValidationPipe } from '@nestjs/common';
import { json, urlencoded } from 'express';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.use(json({ limit: '6mb' }));
  app.use(urlencoded({ extended: true, limit: '6mb' }));

  const port = parseInt(process.env.PORT || '3002', 10);

  // Set global prefix for API routes
  app.setGlobalPrefix('api/v1', {
    exclude: [
      { path: 'admin', method: RequestMethod.GET },
      { path: 'admin/assets/:fileName', method: RequestMethod.GET },
      { path: 'games/:id/preview', method: RequestMethod.GET },
      { path: 'games/:id/index.html', method: RequestMethod.GET },
      { path: 'game-shell/index.html', method: RequestMethod.GET },
    ],
  });

  app.enableCors({
    origin: process.env.CORS_ORIGIN || '*',
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization', 'x-admin-token'],
  });

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
    }),
  );

  await app.listen(port);
  console.log(`[${new Date().toISOString()}] Game Service listening on port ${port}`);
}

bootstrap().catch((err) => {
  console.error('Failed to start Game Service:', err);
  process.exit(1);
});
