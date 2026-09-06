import { NestExpressApplication } from '@nestjs/platform-express';
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import { configureApp } from '../../user-service/dist/bootstrap';

async function bootstrap() {
  const app = await NestFactory.create<NestExpressApplication>(AppModule, { rawBody: true });
  app.useBodyParser('json', { limit: '6mb' });
  app.useBodyParser('urlencoded', { extended: true, limit: '6mb' });
  configureApp(app, { proxy: false, unified: true });
  app.enableShutdownHooks();
  await app.listen(parseInt(process.env.PORT || '3002', 10));
  console.log('Unified business API listening');
}

bootstrap().catch((err) => {
  console.error('Failed to start unified business API:', err);
  process.exit(1);
});
