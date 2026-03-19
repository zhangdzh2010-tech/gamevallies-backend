import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import { configureApp } from './bootstrap';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  // Get port from environment variables, default to 3001
  const port = parseInt(process.env.PORT || '3001', 10);

  configureApp(app);

  // Start listening
  await app.listen(port);

  console.log(`[${new Date().toISOString()}] User Service listening on port ${port}`);
}

bootstrap().catch((err) => {
  console.error('Failed to start application:', err);
  process.exit(1);
});
