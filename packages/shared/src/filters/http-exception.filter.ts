import {
  ExceptionFilter,
  Catch,
  ArgumentsHost,
  HttpException,
  HttpStatus,
  BadRequestException,
  NotFoundException,
  UnauthorizedException,
  ForbiddenException,
  ConflictException,
} from '@nestjs/common';
import { Response } from 'express';
import { ResponseDto } from '../types/api.types';

/**
 * HTTP Exception Filter
 * Catches all HttpExceptions and formats them as standard ResponseDto
 */
@Catch(HttpException)
export class HttpExceptionFilter implements ExceptionFilter {
  catch(exception: HttpException, host: ArgumentsHost) {
    const ctx = host.switchToHttp();
    const response = ctx.getResponse<Response>();
    const status = exception.getStatus();
    const exceptionResponse = exception.getResponse();

    // Extract error message
    let message = exception.message || 'Internal server error';
    if (typeof exceptionResponse === 'object' && 'message' in exceptionResponse) {
      message = (exceptionResponse as any).message || message;
    }

    // Format response
    const formattedResponse: ResponseDto = {
      code: status,
      data: null,
      message: message,
      timestamp: new Date().toISOString(),
    };

    response.status(status).json(formattedResponse);
  }
}

/**
 * Map of HTTP exceptions to error codes
 */
export const HTTP_EXCEPTION_MAP = {
  [BadRequestException.name]: HttpStatus.BAD_REQUEST,
  [NotFoundException.name]: HttpStatus.NOT_FOUND,
  [UnauthorizedException.name]: HttpStatus.UNAUTHORIZED,
  [ForbiddenException.name]: HttpStatus.FORBIDDEN,
  [ConflictException.name]: HttpStatus.CONFLICT,
};
