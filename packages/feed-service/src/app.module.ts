import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { HealthController } from './health.controller';
import { PrismaModule } from './prisma/prisma.module';
// Feed 模块
import { FeedModule } from './feed/feed.module';
import { SearchModule } from './search/search.module';
import { TagModule } from './tag/tag.module';
import { ChallengeModule } from './challenge/challenge.module';
import { CreatorsModule } from './creators/creators.module';
// Social 模块 (从 social-service 合入)
import { LikeModule } from './like/like.module';
import { FollowModule } from './follow/follow.module';
import { CommentModule } from './comment/comment.module';
import { NotificationModule } from './notification/notification.module';
import { ShareModule } from './share/share.module';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: '.env',
    }),
    PrismaModule,
    // Feed
    FeedModule,
    SearchModule,
    TagModule,
    ChallengeModule,
    CreatorsModule,
    // Social
    LikeModule,
    FollowModule,
    CommentModule,
    NotificationModule,
    ShareModule,
  ],
  controllers: [HealthController],
})
export class AppModule {}
