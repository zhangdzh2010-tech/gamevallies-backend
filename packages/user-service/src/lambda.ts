/**
 * 火山引擎函数服务入口
 * 将 NestJS 应用适配为 Serverless HTTP Handler
 */
import { NestFactory } from '@nestjs/core';
import { RequestMethod, ValidationPipe } from '@nestjs/common';
import serverlessExpress from '@codegenie/serverless-express';
import { AppModule } from './app.module';

let handler: any;

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  app.enableCors({
    origin: process.env.CORS_ORIGIN || '*',
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization'],
  });

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
      transformOptions: { enableImplicitConversion: true },
    }),
  );

  app.setGlobalPrefix('api/v1', {
    exclude: [
      { path: 'users/search', method: RequestMethod.GET },
      { path: 'users/:id/followers', method: RequestMethod.GET },
      { path: 'users/:id/following', method: RequestMethod.GET },
      { path: 'users/:id/follow', method: RequestMethod.POST },
      { path: 'users/:id/follow', method: RequestMethod.DELETE },
      { path: 'users/:id/stats', method: RequestMethod.GET },
    ],
  });

  await app.init();
  const expressApp = app.getHttpAdapter().getInstance();
  return serverlessExpress({ app: expressApp });
}

// 火山引擎函数服务 Handler
export const main = async (event: any, context: any) => {
  handler = handler ?? (await bootstrap());
  return handler(event, context);
};
