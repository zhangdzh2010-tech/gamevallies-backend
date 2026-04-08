import { CreateGameGenerationTier, CreateGameOrientation } from '../dto/create-game.dto';
import {
  CreationSessionEntryMode,
  CreationSessionStatus,
} from '../creation-session.constants';

export interface CreationSessionConversationMessage {
  role: 'user' | 'assistant';
  content: string;
  kind?: 'prompt' | 'answer' | 'question' | 'system';
  createdAt?: string;
}

export interface CreationSessionQuestion {
  slotKey: string;
  label: string;
  prompt: string;
  skippable: boolean;
}

export interface CreationSessionPlanDraft {
  title: string;
  summary: string;
  concept: string;
  interaction: string;
  objective: string;
  pacing: string;
  visualDirection: string;
  signatureMoment: string;
}

export interface CreationSessionConfidenceSummary {
  overallConfidence: number;
  strongestSlots: string[];
  weakestSlots: string[];
  ambiguityFlags: string[];
  missingCriticalSlots: string[];
}

export interface CreationSessionQuestionStrategy {
  mode: 'missing_required' | 'low_confidence' | 'ambiguity_resolution' | string;
  slotKey: string | null;
  reason: string;
  impact: number;
  confidence: number;
  ambiguityWeight: number;
}

export interface CreationSessionIntentPreview {
  brief: string | null;
}

export interface CreationSessionPublicMetadata {
  initError?: string | null;
  abandonedAt?: string | null;
}

export interface CreationSessionSnapshot {
  id: string;
  streamPath: string;
  status: CreationSessionStatus;
  entryMode: CreationSessionEntryMode;
  initialPrompt: string;
  titleDraft: string | null;
  revision: number;
  slotState: Record<string, unknown>;
  missingRequired: string[];
  skippedSlots: string[];
  currentQuestion: CreationSessionQuestion | null;
  conversation: CreationSessionConversationMessage[];
  slotFillPct: number;
  readyToGenerate: boolean;
  generatedGameId: string | null;
  generationTaskId: string | null;
  sourceGameId: string | null;
  orientation: CreateGameOrientation | null;
  generationTier: CreateGameGenerationTier;
  questionBudget: number;
  planDraft: CreationSessionPlanDraft | null;
  confidenceSummary: CreationSessionConfidenceSummary | null;
  questionStrategy: CreationSessionQuestionStrategy | null;
  intentBuild: CreationSessionIntentPreview | null;
  metadata: CreationSessionPublicMetadata | null;
}

export type CreationSessionReplyKind = 'question' | 'summary';

export type CreationSessionLegacyEventType =
  | 'session.bootstrap'
  | 'session.updated'
  | 'assistant.reply.delta'
  | 'assistant.reply.done'
  | 'session.error';

export type CreationSessionPublicEventType =
  | 'bootstrap'
  | 'delta'
  | 'done'
  | 'snapshot'
  | 'error';

interface CreationSessionStreamEventBase {
  sessionId: string;
  timestamp: number;
}

interface CreationSessionLegacyMappedEvent extends CreationSessionStreamEventBase {
  legacyEventType: CreationSessionLegacyEventType;
}

export interface CreationSessionBootstrapEvent extends CreationSessionLegacyMappedEvent {
  type: 'bootstrap';
  session: CreationSessionSnapshot;
}

export interface CreationSessionSnapshotEvent extends CreationSessionLegacyMappedEvent {
  type: 'snapshot';
  session: CreationSessionSnapshot;
}

export interface CreationSessionDeltaEvent extends CreationSessionLegacyMappedEvent {
  type: 'delta';
  messageId: string;
  delta: string;
  accumulated: string;
  kind: CreationSessionReplyKind;
}

export interface CreationSessionDoneEvent extends CreationSessionLegacyMappedEvent {
  type: 'done';
  messageId: string;
  message: string;
  kind: CreationSessionReplyKind;
}

export interface CreationSessionErrorEvent extends CreationSessionLegacyMappedEvent {
  type: 'error';
  code: string;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
}

export interface CreationSessionHeartbeatEvent extends CreationSessionStreamEventBase {
  type: 'heartbeat';
}

export type CreationSessionPublicEvent =
  | CreationSessionBootstrapEvent
  | CreationSessionSnapshotEvent
  | CreationSessionDeltaEvent
  | CreationSessionDoneEvent
  | CreationSessionErrorEvent;

export type CreationSessionStreamEvent =
  | CreationSessionPublicEvent
  | CreationSessionHeartbeatEvent;
