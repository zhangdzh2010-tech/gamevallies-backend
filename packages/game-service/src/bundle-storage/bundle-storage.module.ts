import { PrismaModule } from '../prisma/prisma.module';
import { Module } from '@nestjs/common';
import { BundleStorageService } from './bundle-storage.service';

@Module({
  imports: [PrismaModule],
  providers: [BundleStorageService],
  exports: [BundleStorageService],
})
export class BundleStorageModule {}

