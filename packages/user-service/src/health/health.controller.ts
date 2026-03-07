import { Controller, Get, HttpCode, HttpStatus } from '@nestjs/common';
import { HealthService } from './health.service';

@Controller('health')
export class HealthController {
  constructor(private readonly healthService: HealthService) {}

  @Get()
  @HttpCode(HttpStatus.OK)
  async health() {
    return this.healthService.check();
  }

  @Get('ready')
  @HttpCode(HttpStatus.OK)
  async ready() {
    return this.healthService.checkReady();
  }

  @Get('live')
  @HttpCode(HttpStatus.OK)
  async live() {
    return this.healthService.checkLive();
  }
}
