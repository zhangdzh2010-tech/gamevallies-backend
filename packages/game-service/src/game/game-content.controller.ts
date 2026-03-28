import { Controller, Get, Header, Param, Query, Res } from '@nestjs/common';
import { Response } from 'express';
import { GameService } from './game.service';

@Controller('games')
export class GameContentController {
  constructor(private readonly gameService: GameService) {}

  @Get(':id/preview')
  @Header('Content-Type', 'text/html; charset=utf-8')
  @Header('Cache-Control', 'no-store')
  async previewGame(
    @Param('id') id: string,
    @Query('previewToken') previewToken?: string,
  ) {
    return this.gameService.getPlayData(id, previewToken);
  }

  @Get(':id/index.html')
  @Header('Content-Type', 'text/html; charset=utf-8')
  @Header('Cache-Control', 'no-store')
  async loadGameHtml(
    @Param('id') id: string,
    @Query('previewToken') previewToken?: string,
  ) {
    return this.gameService.getPlayData(id, previewToken);
  }

  @Get(':id/cover')
  async loadGameCover(
    @Param('id') id: string,
    @Res() res: Response,
    @Query('previewToken') previewToken?: string,
    @Query('taskId') taskId?: string,
  ) {
    const cover = await this.gameService.getGameCoverContent(id, { previewToken, taskId });
    res.setHeader('Content-Type', cover.contentType);
    res.setHeader('Cache-Control', cover.cacheControl);
    return res.send(cover.buffer);
  }
}
