import {
  Injectable,
  NestInterceptor,
  ExecutionContext,
  CallHandler,
} from '@nestjs/common';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';
import { ResponseDto } from '../types/api.types';

/**
 * Transform Interceptor
 * Wraps all responses in a standard ResponseDto format
 */
@Injectable()
export class TransformInterceptor implements NestInterceptor {
  intercept(
    context: ExecutionContext,
    next: CallHandler,
  ): Observable<ResponseDto> {
    return next.handle().pipe(
      map((data) => {
        // If the response is already in ResponseDto format, return it as is
        if (data && typeof data === 'object' && 'code' in data && 'data' in data) {
          return data;
        }

        // Wrap the response in ResponseDto format
        return {
          code: 0, // 0 means success
          data: data || null,
          message: 'success',
          timestamp: new Date().toISOString(),
        } as ResponseDto;
      }),
    );
  }
}
