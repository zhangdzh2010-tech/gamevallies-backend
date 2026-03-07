import { Module } from '@nestjs/common';
import { MongoModule } from '../mongo/mongo.module';
import { BundleService } from './bundle.service';

@Module({
  imports: [MongoModule],
  providers: [BundleService],
  exports: [BundleService],
})
export class BundleModule {}
