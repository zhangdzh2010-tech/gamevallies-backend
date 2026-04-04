export const GENERATION_EXECUTION_QUEUE_NAME = 'generation-execution';
export const GENERATION_QUEUE_JOB_PIPELINE_RUN = 'pipeline_run';
export const GENERATION_QUEUE_JOB_PIPELINE_ITERATE = 'pipeline_iterate';
export const GENERATION_QUEUE_JOB_RECONCILE_ACTIVE_TASKS = 'reconcile_active_tasks';
export const GENERATION_QUEUE_REPEATABLE_RECONCILE_JOB_ID = 'reconcile-active-generation-tasks';

export type GenerationExecutionJobName =
  | typeof GENERATION_QUEUE_JOB_PIPELINE_RUN
  | typeof GENERATION_QUEUE_JOB_PIPELINE_ITERATE
  | typeof GENERATION_QUEUE_JOB_RECONCILE_ACTIVE_TASKS;

export type GenerationExecutionJobData = {
  taskId: string;
};
