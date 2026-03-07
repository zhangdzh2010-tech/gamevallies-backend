import { Controller, Get, HttpCode, HttpStatus } from '@nestjs/common';

@Controller('health')
export class HealthController {
  @Get()
  @HttpCode(HttpStatus.OK)
  health() {
    return {
      status: 'ok',
      service: 'game-service',
      timestamp: new Date().toISOString(),
      uptime: process.uptime(),
    };
  }
}
