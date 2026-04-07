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
      type: 'assistant.phase';
      sessionId: string;
      phase: 'analyzing' | 'replying';
      label: string;
      details?: Record<string, unknown>;
      timestamp: number;
    }
  | {
      type: 'assistant.reply.delta';
      sessionId: string;
      delta: string;
      accumulated: string;
      kind: 'question' | 'summary';
      chunkIndex: number;
      done: boolean;
      timestamp: number;
    }
  | {
      type: 'assistant.reply.done';
      sessionId: string;
      message: string;
      kind: 'question' | 'summary';
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

  publishPhase(
    userId: string,
    sessionId: string,
    phase: 'analyzing' | 'replying',
    label: string,
    details?: Record<string, unknown>,
  ): void {
    this.getStream(this.streamKey(userId, sessionId))?.next({
      type: 'assistant.phase',
      sessionId,
      phase,
      label,
      details,
      timestamp: Date.now(),
    });
  }

  publishReply(
    userId: string,
    sessionId: string,
    message: string,
    kind: 'question' | 'summary' = 'question',
  ): void {
    const stream = this.getStream(this.streamKey(userId, sessionId));
    const normalized = String(message || '').trim();
    if (!stream || !normalized) {
      return;
    }

    const chunks = this.chunkReply(normalized);
    let accumulated = '';
    chunks.forEach((delta, index) => {
      accumulated += delta;
      this.publishReplyDelta(
        userId,
        sessionId,
        delta,
        accumulated,
        kind,
        index,
        index === chunks.length - 1,
      );
    });

    this.publishReplyDone(
      userId,
      sessionId,
      normalized,
      kind,
    );
  }

  publishReplyDelta(
    userId: string,
    sessionId: string,
    delta: string,
    accumulated: string,
    kind: 'question' | 'summary' = 'question',
    chunkIndex = 0,
    done = false,
  ): void {
    this.getStream(this.streamKey(userId, sessionId))?.next({
      type: 'assistant.reply.delta',
      sessionId,
      delta,
      accumulated,
      kind,
      chunkIndex,
      done,
      timestamp: Date.now(),
    });
  }

  publishReplyDone(
    userId: string,
    sessionId: string,
    message: string,
    kind: 'question' | 'summary' = 'question',
  ): void {
    this.getStream(this.streamKey(userId, sessionId))?.next({
      type: 'assistant.reply.done',
      sessionId,
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
