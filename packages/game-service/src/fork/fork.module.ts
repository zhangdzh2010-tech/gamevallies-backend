import { Module } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { ForkService } from './fork.service';
import { ForkController } from './fork.controller';
import { PrismaModule } from '../prisma/prisma.module';
import { BundleModule } from '../bundle/bundle.module';
import { StatsModule } from '../stats/stats.module';

@Module({
  imports: [
    PrismaModule,
    BundleModule,
    StatsModule,
    JwtModule.registerAsync({
      useFactory: (configService: ConfigService) => ({
        secret: configService.get<string>('JWT_SECRET', 'your-secret-key'),
        signOptions: { expiresIn: 86400 },
      }),
      inject: [ConfigService],
    }),
  ],
  providers: [ForkService],
  controllers: [ForkController],
  exports: [ForkService],
})
export class ForkModule {}
