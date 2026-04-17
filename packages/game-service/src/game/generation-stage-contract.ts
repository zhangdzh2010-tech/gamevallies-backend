export type PublicGenerationStageKey =
  | "understanding"
  | "designing"
  | "generating"
  | "validating"
  | "finalizing";

type PublicGenerationStageDefinition = {
  key: PublicGenerationStageKey;
  label: string;
  pct: number;
};

export const PUBLIC_GENERATION_STAGES: readonly PublicGenerationStageDefinition[] =
  [
    { key: "understanding", label: "理解游戏需求", pct: 15 },
    { key: "designing", label: "构建游戏设计", pct: 35 },
    { key: "generating", label: "生成游戏代码", pct: 60 },
    { key: "validating", label: "质量校验与修复", pct: 85 },
    { key: "finalizing", label: "发布生成结果", pct: 95 },
  ] as const;

export const PUBLIC_GENERATION_STAGE_TOTAL = PUBLIC_GENERATION_STAGES.length;

const PUBLIC_GENERATION_STAGE_BY_KEY = new Map(
  PUBLIC_GENERATION_STAGES.map((stage, index) => [
    stage.key,
    { ...stage, index },
  ]),
);

const RAW_STAGE_TO_PUBLIC_STAGE: Record<string, PublicGenerationStageKey> = {
  queued: "understanding",
  running: "understanding",
  started: "understanding",
  submitting: "understanding",
  request_normalized: "understanding",
  intent_parse: "understanding",
  intent_parsing: "understanding",
  spec_build: "understanding",
  runtime_profile_select: "designing",
  template_match: "designing",
  template_matching: "designing",
  designing: "designing",
  contract_compose: "designing",
  code_generate: "generating",
  code_generating: "generating",
  "code_generate.full": "generating",
  logic_generate: "generating",
  pipeline_run: "generating",
  iteration: "generating",
  generating: "generating",
  qa_fix: "validating",
  "qa_fix.syntax_structural": "validating",
  qa_checking: "validating",
  contract_qa: "validating",
  targeted_remediation: "validating",
  runtime_qa: "validating",
  runtime_qa_unavailable: "validating",
  runtime_simulation_qa: "validating",
  code_review: "validating",
  failed: "validating",
  timed_out: "validating",
  publishing: "finalizing",
  completed: "finalizing",
  succeeded: "finalizing",
  canceled: "finalizing",
};

function fallbackStageKeyFromPct(progressPct: number): PublicGenerationStageKey {
  if (progressPct >= 95) return "finalizing";
  if (progressPct >= 80) return "validating";
  if (progressPct >= 55) return "generating";
  if (progressPct >= 25) return "designing";
  return "understanding";
}

export function resolvePublicGenerationStage(
  rawStage?: string | null,
  progressPct?: number | null,
): {
  displayStageKey: PublicGenerationStageKey;
  displayStageLabel: string;
  displayStageIndex: number;
  displayStagePct: number;
  displayStageTotal: number;
  rawStage: string;
} {
  const normalizedRawStage = String(rawStage || "").trim() || "unknown";
  const normalizedPct = Number(progressPct ?? 0) || 0;
  const displayStageKey =
    RAW_STAGE_TO_PUBLIC_STAGE[normalizedRawStage] ||
    fallbackStageKeyFromPct(normalizedPct);
  const stage =
    PUBLIC_GENERATION_STAGE_BY_KEY.get(displayStageKey) ||
    PUBLIC_GENERATION_STAGE_BY_KEY.get("understanding")!;
  const isCompleted =
    normalizedRawStage === "completed" || normalizedRawStage === "succeeded";

  return {
    displayStageKey,
    displayStageLabel: stage.label,
    displayStageIndex: stage.index,
    displayStagePct: isCompleted ? 100 : stage.pct,
    displayStageTotal: PUBLIC_GENERATION_STAGE_TOTAL,
    rawStage: normalizedRawStage,
  };
}

export function buildPublicGenerationStageDetails(
  rawStage?: string | null,
  progressPct?: number | null,
  details?: Record<string, unknown>,
): Record<string, unknown> {
  const stage = resolvePublicGenerationStage(rawStage, progressPct);

  return {
    ...(details || {}),
    rawStage: stage.rawStage,
    stage: stage.displayStageKey,
    displayStageKey: stage.displayStageKey,
    displayStageLabel: stage.displayStageLabel,
    displayStageIndex: stage.displayStageIndex,
    displayStagePct: stage.displayStagePct,
    displayStageTotal: stage.displayStageTotal,
  };
}
