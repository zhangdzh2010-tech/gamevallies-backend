import { Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Job, Processor, Queue, RepeatableJob, Worker } from 'bullmq';
import IORedis, { Redis } from 'ioredis';
import {
  GENERATION_EXECUTION_QUEUE_NAME,
  GENERATION_QUEUE_JOB_RECONCILE_ACTIVE_TASKS,
  GENERATION_QUEUE_REPEATABLE_RECONCILE_JOB_ID,
  GenerationExecutionJobData,
  GenerationExecutionJobName,
} from './generation-queue.types';

@Injectable()
export class GenerationQueueService implements OnModuleDestroy {
  private readonly logger = new Logger(GenerationQueueService.name);
  private readonly queueName = GENERATION_EXECUTION_QUEUE_NAME;
  private queue: Queue<GenerationExecutionJobData, void, GenerationExecutionJobName> | null = null;
  private queueConnection: Redis | null = null;
  private workerConnection: Redis | null = null;
  private worker: Worker<GenerationExecutionJobData, void, GenerationExecutionJobName> | null = null;
  private workerProcessor: Processor<GenerationExecutionJobData, void, GenerationExecutionJobName> | null = null;
  private initPromise: Promise<boolean> | null = null;
  private queueEnabled = false;

  constructor(private readonly configService: ConfigService) {}

  async onModuleDestroy(): Promise<void> {
    await this.closeWorker();
    await this.closeQueue();
  }

  isEnabled(): boolean {
    return this.queueEnabled;
  }

  isWorkerActive(): boolean {
    return Boolean(this.worker);
  }

  async ensureReady(): Promise<boolean> {
    if (this.queueEnabled && this.queue) {
      await this.ensureWorkerIfPossible();
      return true;
    }
    if (this.initPromise) {
      return this.initPromise;
    }

    this.initPromise = this.initializeQueue()
      .then(async (ready) => {
        if (ready) {
          await this.ensureWorkerIfPossible();
        }
        return ready;
      })
      .finally(() => {
      this.initPromise = null;
    });
    return this.initPromise;
  }

  async ensureOperational(): Promise<boolean> {
    const ready = await this.ensureReady();
    if (!ready || !this.workerProcessor) {
      return false;
    }
    return this.ensureWorkerIfPossible();
  }

  async registerWorkerProcessor(
    processor: Processor<GenerationExecutionJobData, void, GenerationExecutionJobName>,
  ): Promise<boolean> {
    this.workerProcessor = processor;
    return this.ensureOperational();
  }

  async enqueueJob(
    name: GenerationExecutionJobName,
    taskId: string,
  ): Promise<boolean> {
    if (!(await this.ensureOperational()) || !this.queue) {
      return false;
    }

    try {
      await this.queue.add(
        name,
        { taskId },
        {
          jobId: taskId,
          removeOnComplete: { count: 200 },
          removeOnFail: { count: 1000 },
          attempts: 1,
        },
      );
      return true;
    } catch (error) {
      this.logger.warn(`Failed to enqueue ${name} for ${taskId}: ${this.extractErrorMessage(error)}`);
      return false;
    }
  }

  async ensureActiveTaskSweepScheduler(intervalMs: number): Promise<boolean> {
    if (!(await this.ensureOperational()) || !this.queue) {
      return false;
    }

    const normalizedIntervalMs = Math.max(1000, Math.floor(intervalMs));
    try {
      const repeatableJobs = await this.queue.getRepeatableJobs();
      const matchingJob = repeatableJobs.find((job) => (
        job.name === GENERATION_QUEUE_JOB_RECONCILE_ACTIVE_TASKS
        && Number(job.every || 0) === normalizedIntervalMs
      ));
      if (matchingJob) {
        return true;
      }

      await this.removeActiveTaskSweepScheduler(repeatableJobs);
      await this.queue.add(
        GENERATION_QUEUE_JOB_RECONCILE_ACTIVE_TASKS,
        { taskId: GENERATION_QUEUE_REPEATABLE_RECONCILE_JOB_ID },
        {
          jobId: GENERATION_QUEUE_REPEATABLE_RECONCILE_JOB_ID,
          repeat: { every: normalizedIntervalMs },
          removeOnComplete: { count: 20 },
          removeOnFail: { count: 100 },
          attempts: 1,
        },
      );
      return true;
    } catch (error) {
      this.logger.warn(
        `Failed to schedule repeatable active-task sweep (${normalizedIntervalMs}ms): ${this.extractErrorMessage(error)}`,
      );
      return false;
    }
  }

  async ensureWorker(
    processor: Processor<GenerationExecutionJobData, void, GenerationExecutionJobName>,
  ): Promise<boolean> {
    this.workerProcessor = processor;
    return this.ensureOperational();
  }

  async closeWorker(): Promise<void> {
    if (this.worker) {
      await this.worker.close().catch(() => undefined);
      this.worker = null;
    }
    if (this.workerConnection) {
      await this.workerConnection.quit().catch(() => this.workerConnection?.disconnect());
      this.workerConnection = null;
    }
  }

  private async initializeQueue(): Promise<boolean> {
    if (!this.isQueueFeatureEnabled()) {
      this.queueEnabled = false;
      return false;
    }

    try {
      this.queueConnection = this.buildRedisConnection();
      await this.queueConnection.ping();
      this.queue = new Queue<GenerationExecutionJobData, void, GenerationExecutionJobName>(
        this.queueName,
        {
          connection: this.queueConnection,
          defaultJobOptions: {
            removeOnComplete: { count: 200 },
            removeOnFail: { count: 1000 },
            attempts: 1,
          },
        },
      );
      this.queueEnabled = true;
      return true;
    } catch (error) {
      this.queueEnabled = false;
      this.logger.warn(`BullMQ disabled, falling back to in-process execution: ${this.extractErrorMessage(error)}`);
      await this.closeQueue();
      return false;
    }
  }

  private async closeQueue(): Promise<void> {
    if (this.queue) {
      await this.queue.close().catch(() => undefined);
      this.queue = null;
    }
    if (this.queueConnection) {
      await this.queueConnection.quit().catch(() => this.queueConnection?.disconnect());
      this.queueConnection = null;
    }
  }

  private async removeActiveTaskSweepScheduler(
    repeatableJobs?: RepeatableJob[],
  ): Promise<void> {
    if (!this.queue) {
      return;
    }
    const jobs = repeatableJobs ?? await this.queue.getRepeatableJobs();
    for (const job of jobs) {
      if (job.name !== GENERATION_QUEUE_JOB_RECONCILE_ACTIVE_TASKS) {
        continue;
      }
      await this.queue.removeRepeatableByKey(job.key).catch(() => undefined);
    }
  }

  private buildRedisConnection(): Redis {
    const redisUrl = this.configService.get<string>('REDIS_URL', '').trim();
    return new IORedis(redisUrl, {
      maxRetriesPerRequest: null,
      enableReadyCheck: false,
    });
  }

  private isQueueFeatureEnabled(): boolean {
    const redisUrl = this.configService.get<string>('REDIS_URL', '').trim();
    if (!redisUrl) {
      return false;
    }
    const raw = this.configService.get<string>('GENERATION_QUEUE_ENABLED', 'true').trim().toLowerCase();
    return raw !== '0' && raw !== 'false' && raw !== 'off';
  }

  private getWorkerConcurrency(): number {
    const raw = Number.parseInt(
      this.configService.get<string>('GENERATION_QUEUE_WORKER_CONCURRENCY', '6'),
      10,
    );
    if (!Number.isFinite(raw) || raw <= 0) {
      return 6;
    }
    return Math.min(raw, 16);
  }

  private extractErrorMessage(error: unknown): string {
    if (!error) {
      return 'unknown error';
    }
    if (error instanceof Error) {
      return error.message;
    }
    return String(error);
  }

  private async ensureWorkerIfPossible(): Promise<boolean> {
    if (!this.queueEnabled || !this.queue || !this.workerProcessor) {
      return false;
    }
    if (this.worker) {
      return true;
    }

    try {
      this.workerConnection = this.buildRedisConnection();
      this.worker = new Worker<GenerationExecutionJobData, void, GenerationExecutionJobName>(
        this.queueName,
        this.workerProcessor,
        {
          connection: this.workerConnection,
          concurrency: this.getWorkerConcurrency(),
        },
      );
      this.worker.on('completed', (job: Job<GenerationExecutionJobData, void, GenerationExecutionJobName>) => {
        this.logger.debug(`Generation queue job completed: ${job.name}#${job.id}`);
      });
      this.worker.on('failed', (job, error) => {
        this.logger.warn(
          `Generation queue job failed: ${job?.name || 'unknown'}#${job?.id || 'n/a'} - ${this.extractErrorMessage(error)}`,
        );
      });
      this.worker.on('closed', () => {
        this.worker = null;
      });
      return true;
    } catch (error) {
      this.logger.warn(`Failed to start BullMQ worker: ${this.extractErrorMessage(error)}`);
      await this.closeWorker();
      return false;
    }
  }
}
