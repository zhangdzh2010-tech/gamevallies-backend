import { Module } from '@nestjs/common';
import { BundleStorageModule } from '../bundle-storage/bundle-storage.module';
import { BundleService } from './bundle.service';

@Module({
  imports: [BundleStorageModule],
  providers: [BundleService],
  exports: [BundleService],
})
export class BundleModule {}
