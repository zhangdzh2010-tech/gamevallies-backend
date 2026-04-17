import { Body, Controller, Get, HttpCode, HttpStatus, Param, Post, Req, Res, UseGuards } from '@nestjs/common';
import { ok } from '../common/api-response';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { CreateCreationSessionDto, CreationAbandonDto, CreationGenerateDto, CreationMessageDto, CreationSkipDto } from './creation-session.dto';
import { CreationSessionService } from './creation-session.service';
import { Response } from 'express';

type ConflictResponse = {
  code: number;
  message: string;
  data: any;
};

function unwrapUserId(req: any): string {
  return req.user?.sub || req.user?.id;
}

function httpStatusFromCode(code: number): number {
  // In our service contract, `code` is aligned with HTTP status code.
  if (typeof code !== 'number') return HttpStatus.INTERNAL_SERVER_ERROR;
  if (code === 400) return HttpStatus.BAD_REQUEST;
  if (code === 401) return HttpStatus.UNAUTHORIZED;
  if (code === 403) return HttpStatus.FORBIDDEN;
  if (code === 404) return HttpStatus.NOT_FOUND;
  if (code === 409) return HttpStatus.CONFLICT;
  if (code === 422) return HttpStatus.UNPROCESSABLE_ENTITY;
  return code;
}

@Controller('games')
export class CreationSessionController {
  constructor(private readonly creationSessionService: CreationSessionService) {}

  @Post('/creation-sessions')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async createSession(@Req() req: any, @Body() dto: CreateCreationSessionDto, @Res() res: Response) {
    const userId = unwrapUserId(req);
    const result = await this.creationSessionService.createSession(userId, dto);
    if (result.ok) return res.status(HttpStatus.OK).json(ok(result.snapshot));
    return res.status(httpStatusFromCode(result.code)).json(result as ConflictResponse);
  }

  @Get('/creation-sessions/active')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getActive(@Req() req: any, @Res() res: Response) {
    const userId = unwrapUserId(req);
    const result = await this.creationSessionService.getActiveSession(userId);
    return res.status(HttpStatus.OK).json(ok(result));
  }

  @Post('/creation-sessions/:sessionId/messages')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async submitMessage(
    @Req() req: any,
    @Param('sessionId') sessionId: string,
    @Body() dto: CreationMessageDto,
    @Res() res: Response,
  ) {
    const userId = unwrapUserId(req);
    const result = await this.creationSessionService.submitMessage(userId, sessionId, dto);
    if (result.ok) return res.status(HttpStatus.OK).json(ok(result.snapshot));
    return res.status(httpStatusFromCode(result.code)).json(result as ConflictResponse);
  }

  @Post('/creation-sessions/:sessionId/skip')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async skipQuestion(
    @Req() req: any,
    @Param('sessionId') sessionId: string,
    @Body() dto: CreationSkipDto,
    @Res() res: Response,
  ) {
    const userId = unwrapUserId(req);
    const result = await this.creationSessionService.skipQuestion(userId, sessionId, dto);
    if (result.ok) return res.status(HttpStatus.OK).json(ok(result.snapshot));
    return res.status(httpStatusFromCode(result.code)).json(result as ConflictResponse);
  }

  @Get('/creation-sessions/:sessionId')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getSession(@Req() req: any, @Param('sessionId') sessionId: string, @Res() res: Response) {
    const userId = unwrapUserId(req);
    const snapshot = await this.creationSessionService.getSession(userId, sessionId);
    return res.status(HttpStatus.OK).json(ok(snapshot));
  }

  @Post('/creation-sessions/:sessionId/generate')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async generate(
    @Req() req: any,
    @Param('sessionId') sessionId: string,
    @Body() dto: CreationGenerateDto,
    @Res() res: Response,
  ) {
    const userId = unwrapUserId(req);
    const result = await this.creationSessionService.generateGame(userId, sessionId, dto);
    if (result.ok) return res.status(HttpStatus.OK).json(ok(result.payload));
    return res.status(httpStatusFromCode(result.code)).json(result as ConflictResponse);
  }

  @Post('/creation-sessions/:sessionId/abandon')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async abandon(
    @Req() req: any,
    @Param('sessionId') sessionId: string,
    @Body() dto: CreationAbandonDto,
    @Res() res: Response,
  ) {
    const userId = unwrapUserId(req);
    const result = await this.creationSessionService.abandonSession(userId, sessionId, dto);
    if (result.ok) return res.status(HttpStatus.OK).json(ok(result.payload));
    return res.status(httpStatusFromCode(result.code)).json(result as ConflictResponse);
  }
}

