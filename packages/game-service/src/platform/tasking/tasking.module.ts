import { Module } from '@nestjs/common';
import { PrismaModule } from '../../prisma/prisma.module';
import { GenerationQueueService } from '../../game/generation-queue.service';
import { GenerationTaskService } from '../../game/generation-task.service';

@Module({
  imports: [PrismaModule],
  providers: [
    GenerationTaskService,
    GenerationQueueService,
  ],
  exports: [
    GenerationTaskService,
    GenerationQueueService,
  ],
})
export class TaskingModule {}
