import { Module } from '@nestjs/common';
import { BundleStorageService } from './bundle-storage.service';

@Module({
  providers: [BundleStorageService],
  exports: [BundleStorageService],
})
export class BundleStorageModule {}
