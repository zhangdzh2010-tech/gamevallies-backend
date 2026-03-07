import { NestFactory } from '@nestjs/core';
import { RequestMethod, ValidationPipe } from '@nestjs/common';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  // Get port from environment variables, default to 3001
  const port = parseInt(process.env.PORT || '3001', 10);

  // Enable CORS
  app.enableCors({
    origin: process.env.CORS_ORIGIN || '*',
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization'],
  });

  // Global ValidationPipe
  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
      transformOptions: {
        enableImplicitConversion: true,
      },
    }),
  );

  // Set global prefix for API routes
  app.setGlobalPrefix('api/v1', {
    exclude: [
      {
        path: 'users/search',
        method: RequestMethod.GET,
      },
      {
        path: 'users/:id/followers',
        method: RequestMethod.GET,
      },
      {
        path: 'users/:id/following',
        method: RequestMethod.GET,
      },
      {
        path: 'users/:id/follow',
        method: RequestMethod.POST,
      },
      {
        path: 'users/:id/follow',
        method: RequestMethod.DELETE,
      },
      {
        path: 'users/:id/stats',
        method: RequestMethod.GET,
      },
    ],
  });

  // Start listening
  await app.listen(port);

  console.log(`[${new Date().toISOString()}] User Service listening on port ${port}`);
}

bootstrap().catch((err) => {
  console.error('Failed to start application:', err);
  process.exit(1);
});
