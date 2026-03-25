import { INestApplication, RequestMethod, ValidationPipe } from '@nestjs/common';
import { registerUnifiedApiProxy } from './edge/unified-api-proxy';

const CORS_ALLOWED_HEADERS = [
  'Content-Type',
  'Authorization',
  'x-refresh-token',
  'x-admin-token',
];
const WECHAT_DOMAIN_VERIFICATIONS = [
  {
    path: '/33zqDBay4T.txt',
    content: '5142b16983df09708831078604fbcfeb',
  },
  {
    path: '/9002299597e76e062cb56930926ed40c.txt',
    content: '1f4c0e10a205fa9a13b66a4d87eab953ba98f63b',
  },
] as const;

export function configureApp(app: INestApplication): void {
  app.enableCors({
    origin: process.env.CORS_ORIGIN || '*',
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: CORS_ALLOWED_HEADERS,
  });

  const expressApp = app.getHttpAdapter().getInstance();
  for (const verification of WECHAT_DOMAIN_VERIFICATIONS) {
    expressApp.get(verification.path, (_req: unknown, res: any) => {
      res.type('text/plain').send(verification.content);
    });
  }

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
      {
        path: '33zqDBay4T.txt',
        method: RequestMethod.GET,
      },
      {
        path: '9002299597e76e062cb56930926ed40c.txt',
        method: RequestMethod.GET,
      },
    ],
  });
}
