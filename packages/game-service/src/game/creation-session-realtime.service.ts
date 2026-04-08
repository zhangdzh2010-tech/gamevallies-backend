import { Injectable, MessageEvent } from '@nestjs/common';
import { Observable, Subject } from 'rxjs';
import {
  CreationSessionReplyKind,
  CreationSessionSnapshot,
  CreationSessionStreamEvent,
} from './types/creation-session.types';

type CreationSessionRealtimePayload =
  | {
      type: 'bootstrap' | 'snapshot';
      sessionId: string;
      session: CreationSessionSnapshot;
      legacyEventType: 'session.bootstrap' | 'session.updated';
      timestamp: number;
    }
  | {
      type: 'delta';
      sessionId: string;
      messageId: string;
      delta: string;
      accumulated: string;
      kind: CreationSessionReplyKind;
      legacyEventType: 'assistant.reply.delta';
      timestamp: number;
    }
  | {
      type: 'done';
      sessionId: string;
      messageId: string;
      message: string;
      kind: CreationSessionReplyKind;
      legacyEventType: 'assistant.reply.done';
      timestamp: number;
    }
  | {
      type: 'error';
      sessionId: string;
      code: string;
      message: string;
      retryable: boolean;
      details: Record<string, unknown>;
      legacyEventType: 'session.error';
      timestamp: number;
    }
  | {
      type: 'heartbeat';
      sessionId: string;
      timestamp: number;
    };

@Injectable()
export class CreationSessionRealtimeService {
  private readonly streams = new Map<string, Subject<CreationSessionRealtimePayload>>();
  private readonly subscriberCounts = new Map<string, number>();
  private readonly activeReplyMessageIds = new Map<string, string>();
  private readonly heartbeatIntervalMs = 15_000;
  private replySequence = 0;

  streamSession(
    userId: string,
    sessionId: string,
    initialSnapshot: CreationSessionSnapshot,
  ): Observable<MessageEvent> {
    const key = this.streamKey(userId, sessionId);
    const subject = this.ensureStream(key);

    return new Observable<MessageEvent>((subscriber) => {
      this.bumpSubscribers(key, 1);
      subscriber.next(
        this.toMessageEvent({
          type: 'bootstrap',
          sessionId,
          session: initialSnapshot,
          legacyEventType: 'session.bootstrap',
          timestamp: Date.now(),
        }),
      );

      const subscription = subject.subscribe({
        next: (payload) => subscriber.next(this.toMessageEvent(payload)),
        error: (error) => subscriber.error(error),
      });

      const heartbeat = setInterval(() => {
        subscriber.next(
          this.toMessageEvent({
            type: 'heartbeat',
            sessionId,
            timestamp: Date.now(),
          }),
        );
      }, this.heartbeatIntervalMs);

      return () => {
        clearInterval(heartbeat);
        subscription.unsubscribe();
        this.bumpSubscribers(key, -1);
        if ((this.subscriberCounts.get(key) || 0) <= 0) {
          this.subscriberCounts.delete(key);
          this.activeReplyMessageIds.delete(key);
          const existing = this.streams.get(key);
          if (existing === subject) {
            this.streams.delete(key);
            existing.complete();
          }
        }
      };
    });
  }

  publishSnapshot(userId: string, sessionId: string, snapshot: CreationSessionSnapshot): void {
    this.getStream(this.streamKey(userId, sessionId))?.next({
      type: 'snapshot',
      sessionId,
      session: snapshot,
      legacyEventType: 'session.updated',
      timestamp: Date.now(),
    });
  }

  publishPhase(
    userId: string,
    sessionId: string,
    phase: 'analyzing' | 'replying',
    label: string,
    details?: Record<string, unknown>,
  ): void {
    void userId;
    void sessionId;
    void phase;
    void label;
    void details;
    // PR1: public creation-session SSE no longer exposes display-only phase events.
  }

  publishReply(
    userId: string,
    sessionId: string,
    message: string,
    kind: CreationSessionReplyKind = 'question',
  ): void {
    const stream = this.getStream(this.streamKey(userId, sessionId));
    const normalized = String(message || '').trim();
    if (!stream || !normalized) {
      return;
    }

    const chunks = this.chunkReply(normalized);
    const messageId = this.beginReplyMessage(this.streamKey(userId, sessionId), sessionId);
    let accumulated = '';
    chunks.forEach((delta) => {
      accumulated += delta;
      this.publishReplyDelta(
        userId,
        sessionId,
        delta,
        accumulated,
        kind,
        messageId,
      );
    });

    this.publishReplyDone(
      userId,
      sessionId,
      normalized,
      kind,
      messageId,
    );
  }

  publishReplyDelta(
    userId: string,
    sessionId: string,
    delta: string,
    accumulated: string,
    kind: CreationSessionReplyKind = 'question',
    messageId?: string,
  ): void {
    const key = this.streamKey(userId, sessionId);
    this.getStream(this.streamKey(userId, sessionId))?.next({
      type: 'delta',
      sessionId,
      messageId: this.beginReplyMessage(key, sessionId, messageId),
      delta,
      accumulated,
      kind,
      legacyEventType: 'assistant.reply.delta',
      timestamp: Date.now(),
    });
  }

  publishReplyDone(
    userId: string,
    sessionId: string,
    message: string,
    kind: CreationSessionReplyKind = 'question',
    messageId?: string,
  ): void {
    const key = this.streamKey(userId, sessionId);
    const resolvedMessageId = this.completeReplyMessage(key, sessionId, messageId);
    this.getStream(key)?.next({
      type: 'done',
      sessionId,
      messageId: resolvedMessageId,
      message: String(message || '').trim(),
      kind,
      legacyEventType: 'assistant.reply.done',
      timestamp: Date.now(),
    });
  }

  publishError(
    userId: string,
    sessionId: string,
    error: string,
    details?: Record<string, unknown>,
  ): void {
    this.getStream(this.streamKey(userId, sessionId))?.next({
      type: 'error',
      sessionId,
      code: this.resolveErrorCode(details),
      message: error,
      retryable: this.isRetryable(details),
      details: details || {},
      legacyEventType: 'session.error',
      timestamp: Date.now(),
    });
  }

  private streamKey(userId: string, sessionId: string): string {
    return `${userId}:${sessionId}`;
  }

  private ensureStream(key: string): Subject<CreationSessionRealtimePayload> {
    const existing = this.streams.get(key);
    if (existing) {
      return existing;
    }
    const subject = new Subject<CreationSessionRealtimePayload>();
    this.streams.set(key, subject);
    return subject;
  }

  private getStream(key: string): Subject<CreationSessionRealtimePayload> | undefined {
    return this.streams.get(key);
  }

  private beginReplyMessage(key: string, sessionId: string, preferred?: string): string {
    const existing = this.activeReplyMessageIds.get(key);
    if (existing) {
      return existing;
    }
    const next = preferred || this.buildReplyMessageId(sessionId);
    this.activeReplyMessageIds.set(key, next);
    return next;
  }

  private completeReplyMessage(key: string, sessionId: string, preferred?: string): string {
    const existing = this.activeReplyMessageIds.get(key);
    const resolved = preferred || existing || this.buildReplyMessageId(sessionId);
    this.activeReplyMessageIds.delete(key);
    return resolved;
  }

  private buildReplyMessageId(sessionId: string): string {
    this.replySequence += 1;
    return `${sessionId}:reply:${this.replySequence}`;
  }

  private chunkReply(message: string): string[] {
    const normalized = String(message || '').trim();
    if (!normalized) {
      return [];
    }

    const sentenceChunks = normalized
      .split(/(?<=[。！？!?\.])\s*/u)
      .map((item) => item.trim())
      .filter(Boolean);
    const chunks = sentenceChunks.length ? sentenceChunks : [normalized];
    const flattened: string[] = [];

    for (const chunk of chunks) {
      if (chunk.length <= 48) {
        flattened.push(chunk);
        continue;
      }

      for (let index = 0; index < chunk.length; index += 32) {
        flattened.push(chunk.slice(index, index + 32));
      }
    }

    return flattened.filter(Boolean);
  }

  private bumpSubscribers(key: string, delta: number): void {
    const next = (this.subscriberCounts.get(key) || 0) + delta;
    if (next <= 0) {
      this.subscriberCounts.delete(key);
      this.activeReplyMessageIds.delete(key);
      return;
    }
    this.subscriberCounts.set(key, next);
  }

  private toMessageEvent(payload: CreationSessionRealtimePayload): MessageEvent {
    const normalized = payload as CreationSessionStreamEvent;
    return {
      type: normalized.type,
      data: normalized,
      id: `${payload.sessionId}:${payload.timestamp}:${payload.type}`,
    };
  }

  private resolveErrorCode(details?: Record<string, unknown>): string {
    const reason = String(details?.reason || '').trim();
    return reason || 'session_error';
  }

  private isRetryable(details?: Record<string, unknown>): boolean {
    const reason = String(details?.reason || '').trim();
    return reason === 'init_failed' || reason === 'init_timeout';
  }
}
