import {
  Controller,
  Post,
  Get,
  Delete,
  Param,
  Body,
  Query,
  UseGuards,
  Req,
  HttpCode,
  HttpStatus,
  BadRequestException,
  Logger,
} from '@nestjs/common';
import { GameService } from './game.service';
import { CreateGameDto, PublishGameDto, IterateGameDto } from './dto';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok, toPage } from '../common/api-response';
import { presentGame } from '../common/game-presenter';

@Controller('games')
export class GameController {
  private readonly logger = new Logger(GameController.name);

  constructor(private gameService: GameService) {}

  @Post('/generate')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.CREATED)
  async generateGame(@Req() req: any, @Body() dto: CreateGameDto) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      const result = await this.gameService.create(userId, {
        description: dto.description || dto.prompt || '',
      });

      return ok({ gameId: result.gameId });
    } catch (error) {
      this.logger.error(`Error generating game: ${error.message}`);
      throw error;
    }
  }

  @Get('/my/games')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getMyGames(
    @Req() req: any,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '10',
  ) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      const pageNum = Math.max(1, parseInt(page) || 1);
      const limitNum = Math.min(100, Math.max(1, parseInt(limit) || 10));

      const result = await this.gameService.findByAuthor(userId, pageNum, limitNum);
      return ok(toPage({
        data: result.data.map((game: any) => presentGame(game)),
        pagination: result.pagination,
      }));
    } catch (error) {
      this.logger.error(`Error getting user games: ${error.message}`);
      throw error;
    }
  }

  @Get('/my')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getMyGamesAlias(
    @Req() req: any,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '10',
  ) {
    return this.getMyGames(req, page, limit);
  }

  @Get('/explore/published')
  @HttpCode(HttpStatus.OK)
  async getPublishedGames(
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '10',
  ) {
    try {
      const pageNum = Math.max(1, parseInt(page) || 1);
      const limitNum = Math.min(100, Math.max(1, parseInt(limit) || 10));

      const result = await this.gameService.getGamesByStatus('published', pageNum, limitNum);
      return ok(toPage({
        data: result.data.map((game: any) => presentGame(game)),
        pagination: result.pagination,
      }));
    } catch (error) {
      this.logger.error(`Error getting published games: ${error.message}`);
      throw error;
    }
  }

  @Get(':id')
  @HttpCode(HttpStatus.OK)
  async getGame(@Param('id') id: string) {
    try {
      const game = await this.gameService.findById(id);
      return ok(presentGame(game));
    } catch (error) {
      this.logger.error(`Error getting game: ${error.message}`);
      throw error;
    }
  }

  @Get(':id/play')
  @HttpCode(HttpStatus.OK)
  async playGame(@Param('id') id: string) {
    try {
      const htmlCode = await this.gameService.getPlayData(id);
      return ok({
        htmlCode,
        gameId: id,
      });
    } catch (error) {
      this.logger.error(`Error playing game: ${error.message}`);
      throw error;
    }
  }

  @Post(':id/publish')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async publishGame(
    @Param('id') id: string,
    @Req() req: any,
    @Body() dto: PublishGameDto,
  ) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      const game = await this.gameService.publish(id, userId, dto);
      return ok(presentGame(game));
    } catch (error) {
      this.logger.error(`Error publishing game: ${error.message}`);
      throw error;
    }
  }

  @Post(':id/iterate')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async iterateGame(
    @Param('id') id: string,
    @Req() req: any,
    @Body() dto: IterateGameDto,
  ) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      const result = await this.gameService.iterate(id, userId, dto);
      return ok({
        iterationId: `${result.gameId}:v${result.version}`,
        gameId: result.gameId,
        version: result.version,
      });
    } catch (error) {
      this.logger.error(`Error iterating game: ${error.message}`);
      throw error;
    }
  }

  @Get(':id/share-data')
  @HttpCode(HttpStatus.OK)
  async getShareData(@Param('id') id: string) {
    try {
      return ok(await this.gameService.getShareData(id));
    } catch (error) {
      this.logger.error(`Error getting share data: ${error.message}`);
      throw error;
    }
  }

  @Delete(':id')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.NO_CONTENT)
  async deleteGame(@Param('id') id: string, @Req() req: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      await this.gameService.delete(id, userId);
    } catch (error) {
      this.logger.error(`Error deleting game: ${error.message}`);
      throw error;
    }
  }
}
