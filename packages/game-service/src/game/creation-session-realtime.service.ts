import { Injectable, MessageEvent } from '@nestjs/common';
import { Observable, Subject } from 'rxjs';
import {
  CreationSessionHeartbeatEvent,
  CreationSessionPublicEvent,
  CreationSessionReplyKind,
  CreationSessionSnapshot,
  CreationSessionStreamEvent,
} from './types/creation-session.types';

type CreationSessionRealtimePayload = CreationSessionPublicEvent | CreationSessionHeartbeatEvent;

@Injectable()
export class CreationSessionRealtimeService {
  private readonly streams = new Map<string, Subject<CreationSessionRealtimePayload>>();
  private readonly subscriberCounts = new Map<string, number>();
  private readonly activeReplyMessageIds = new Map<string, string>();
  private readonly latestSnapshots = new Map<string, CreationSessionSnapshot>();
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
      const subscription = subject.subscribe({
        next: (payload) => subscriber.next(this.toMessageEvent(payload)),
        error: (error) => subscriber.error(error),
      });
      const bootstrapSnapshot = this.latestSnapshots.get(key) || initialSnapshot;
      subscriber.next(
        this.toMessageEvent({
          type: 'bootstrap',
          sessionId,
          session: bootstrapSnapshot,
          timestamp: Date.now(),
        }),
      );

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
          this.latestSnapshots.delete(key);
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
    const key = this.streamKey(userId, sessionId);
    this.latestSnapshots.set(key, snapshot);
    this.getStream(key)?.next({
      type: 'snapshot',
      sessionId,
      session: snapshot,
      timestamp: Date.now(),
    });
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
    this.getStream(key)?.next({
      type: 'delta',
      sessionId,
      messageId: this.beginReplyMessage(key, sessionId, messageId),
      delta,
      accumulated,
      kind,
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
