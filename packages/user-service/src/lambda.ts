/**
 * 火山引擎函数服务入口
 * 将 NestJS 应用适配为 Serverless HTTP Handler
 */
import { NestFactory } from '@nestjs/core';
import serverlessExpress from '@codegenie/serverless-express';
import { AppModule } from './app.module';
import { configureApp } from './bootstrap';

let handler: any;

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  configureApp(app);

  await app.init();
  const expressApp = app.getHttpAdapter().getInstance();
  return serverlessExpress({ app: expressApp });
}

// 火山引擎函数服务 Handler
export const main = async (event: any, context: any) => {
  handler = handler ?? (await bootstrap());
  return handler(event, context);
};
