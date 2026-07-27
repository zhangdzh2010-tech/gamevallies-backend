import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { BundleStorageModule } from '../bundle-storage/bundle-storage.module';
import { BundleCdnService } from './bundle-cdn.service';

@Module({
  imports: [PrismaModule, BundleStorageModule],
  providers: [BundleCdnService],
  exports: [BundleCdnService],
})
export class BundleCdnModule {}
