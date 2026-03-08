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

  app.setGlobalPrefix('api/v1', {
    exclude: [
      { path: 'games/:id/preview', method: RequestMethod.GET },
      { path: 'games/:id/index.html', method: RequestMethod.GET },
    ],
  });

  app.enableCors({
    origin: process.env.CORS_ORIGIN || '*',
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: ['Content-Type', 'Authorization'],
  });

  app.useGlobalPipes(
    new ValidationPipe({ whitelist: true, forbidNonWhitelisted: true, transform: true }),
  );

  await app.init();
  const expressApp = app.getHttpAdapter().getInstance();
  return serverlessExpress({ app: expressApp });
}

export const main = async (event: any, context: any) => {
  handler = handler ?? (await bootstrap());
  return handler(event, context);
};
