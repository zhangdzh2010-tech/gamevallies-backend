import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { Job } from 'bullmq';
import { GameService } from './game.service';
import { GenerationQueueService } from './generation-queue.service';
import {
  GENERATION_QUEUE_JOB_PIPELINE_ITERATE,
  GENERATION_QUEUE_JOB_PIPELINE_RUN,
  GENERATION_QUEUE_JOB_RECONCILE_ACTIVE_TASKS,
  GenerationExecutionJobData,
  GenerationExecutionJobName,
} from './generation-queue.types';

@Injectable()
export class GenerationQueueWorkerService implements OnModuleInit {
  private readonly logger = new Logger(GenerationQueueWorkerService.name);

  constructor(
    private readonly generationQueueService: GenerationQueueService,
    private readonly gameService: GameService,
  ) {}

  async onModuleInit(): Promise<void> {
    const started = await this.generationQueueService.registerWorkerProcessor(
      async (job: Job<GenerationExecutionJobData, void, GenerationExecutionJobName>) => {
        if (!job?.data?.taskId) {
          return;
        }

        switch (job.name) {
          case GENERATION_QUEUE_JOB_PIPELINE_RUN:
            await this.gameService.processQueuedPipelineRunTask(job.data.taskId);
            return;
          case GENERATION_QUEUE_JOB_PIPELINE_ITERATE:
            await this.gameService.processQueuedIterationTask(job.data.taskId);
            return;
          case GENERATION_QUEUE_JOB_RECONCILE_ACTIVE_TASKS:
            await this.gameService.processQueuedActiveTaskSweep();
            return;
          default:
            this.logger.warn(`Ignoring unknown generation queue job: ${job.name}`);
        }
      },
    );

    if (!started) {
      this.logger.warn('BullMQ worker unavailable; production generation requires queue recovery');
    }
  }
}

