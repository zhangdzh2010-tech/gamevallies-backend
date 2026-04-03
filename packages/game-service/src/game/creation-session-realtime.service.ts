import { Injectable, MessageEvent } from '@nestjs/common';
import { Observable, Subject } from 'rxjs';
import { CreationSessionSnapshot } from './types/creation-session.types';

type CreationSessionRealtimePayload =
  | {
      type: 'session.bootstrap' | 'session.updated';
      sessionId: string;
      session: CreationSessionSnapshot;
      timestamp: number;
    }
  | {
      type: 'session.error';
      sessionId: string;
      error: string;
      details: Record<string, unknown>;
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
  private readonly heartbeatIntervalMs = 15_000;

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
          type: 'session.bootstrap',
          sessionId,
          session: initialSnapshot,
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
      type: 'session.updated',
      sessionId,
      session: snapshot,
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
      type: 'session.error',
      sessionId,
      error,
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

  private bumpSubscribers(key: string, delta: number): void {
    const next = (this.subscriberCounts.get(key) || 0) + delta;
    if (next <= 0) {
      this.subscriberCounts.delete(key);
      return;
    }
    this.subscriberCounts.set(key, next);
  }

  private toMessageEvent(payload: CreationSessionRealtimePayload): MessageEvent {
    return {
      type: payload.type,
      data: payload,
      id: `${payload.sessionId}:${payload.timestamp}:${payload.type}`,
    };
  }
}
