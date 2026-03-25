import { Controller, Get, Header, Param, Query } from '@nestjs/common';
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
}
