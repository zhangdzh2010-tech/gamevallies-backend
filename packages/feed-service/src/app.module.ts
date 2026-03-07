import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { HealthController } from './health.controller';
import { PrismaModule } from './prisma/prisma.module';
import { FeedModule } from './feed/feed.module';
import { SearchModule } from './search/search.module';
import { TagModule } from './tag/tag.module';
import { ChallengeModule } from './challenge/challenge.module';
import { CreatorsModule } from './creators/creators.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: '.env',
    }),
    PrismaModule,
    FeedModule,
    SearchModule,
    TagModule,
    ChallengeModule,
    CreatorsModule,
  ],
  controllers: [HealthController],
})
export class AppModule {}
