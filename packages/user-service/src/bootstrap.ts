import { INestApplication, RequestMethod, ValidationPipe } from '@nestjs/common';
import { registerUnifiedApiProxy } from './edge/unified-api-proxy';

const CORS_ALLOWED_HEADERS = [
  'Content-Type',
  'Authorization',
  'x-refresh-token',
  'x-admin-token',
];

export function configureApp(app: INestApplication): void {
  app.enableCors({
    origin: process.env.CORS_ORIGIN || '*',
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: CORS_ALLOWED_HEADERS,
  });

  registerUnifiedApiProxy(app);

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
}
