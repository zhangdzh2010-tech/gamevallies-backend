import {
  Controller,
  Post,
  Get,
  Delete,
  Patch,
  Param,
  Body,
  Query,
  UseGuards,
  Req,
  HttpCode,
  HttpStatus,
  BadRequestException,
  HttpException,
  Logger,
} from '@nestjs/common';
import { GameService } from './game.service';
import { CreatorReputationService } from './creator-reputation.service';
import { CreateGameDto, PublishGameDto, IterateGameDto } from './dto';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok, toPage } from '../common/api-response';
import { presentGame } from '../common/game-presenter';

@Controller('games')
export class GameController {
  private readonly logger = new Logger(GameController.name);

  constructor(
    private gameService: GameService,
    private reputationService: CreatorReputationService,
  ) {}

  @Post('/expand-prompt')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async expandPrompt(@Body() body: any) {
    const description = body.description || body.prompt || '';
    if (!description) {
      throw new BadRequestException('description is required');
    }

    try {
      const aiEngineUrl = await this.gameService.getAiEngineBaseUrl(body.regionHint);
      const timeoutMs = await this.gameService.getExpandPromptRequestTimeoutMs();
      const response = await require('axios').post(
        `${aiEngineUrl}/api/v1/ai/expand-prompt`,
        { description },
        { timeout: timeoutMs },
      );
      return ok(response.data);
    } catch (error) {
      this.logger.error(`Expand prompt failed: ${error.message}`);
      const status = error?.response?.status;
      const detail = error?.response?.data?.detail
        || error?.response?.data?.message
        || error?.message
        || 'Expand prompt failed';
      if (typeof status === 'number') {
        throw new HttpException(detail, status);
      }
      throw error;
    }
  }

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
        title: dto.title,
        description: dto.description || dto.prompt || '',
        timeoutS: dto.timeoutS,
        regionHint: dto.regionHint,
      });

      return ok(result);
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
    @Query('search') search?: string,
  ) {
    try {
      const pageNum = Math.max(1, parseInt(page) || 1);
      const limitNum = Math.min(100, Math.max(1, parseInt(limit) || 10));

      const result = await this.gameService.getGamesByStatus('published', pageNum, limitNum, search);
      return ok(toPage({
        data: result.data.map((game: any) => presentGame(game)),
        pagination: result.pagination,
      }));
    } catch (error) {
      this.logger.error(`Error getting published games: ${error.message}`);
      throw error;
    }
  }

  @Get(':id/generation-status')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getGenerationStatus(@Param('id') id: string, @Req() req: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      return ok(await this.gameService.getGenerationStatus(id, userId));
    } catch (error) {
      this.logger.error(`Error getting generation status: ${error.message}`);
      throw error;
    }
  }

  @Get('/tasks/:taskId')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getTask(@Param('taskId') taskId: string, @Req() req: any) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.getTask(taskId, userId));
  }

  @Get('/tasks/:taskId/events')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getTaskEvents(
    @Param('taskId') taskId: string,
    @Req() req: any,
    @Query('limit') limit?: string,
  ) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.getTaskEvents(taskId, userId, limit ? Number.parseInt(limit, 10) : undefined));
  }

  @Get('/tasks/:taskId/artifacts')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async getTaskArtifacts(
    @Param('taskId') taskId: string,
    @Req() req: any,
    @Query('limit') limit?: string,
  ) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.getTaskArtifacts(taskId, userId, limit ? Number.parseInt(limit, 10) : undefined));
  }

  @Post('/tasks/:taskId/cancel')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async cancelTask(@Param('taskId') taskId: string, @Req() req: any) {
    const userId = req.user?.sub || req.user?.id;
    if (!userId) {
      throw new BadRequestException('Invalid token');
    }

    return ok(await this.gameService.cancelTask(taskId, userId));
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
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async playGame(@Param('id') id: string, @Req() req: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }
      const htmlCode = await this.gameService.getPlayableHtml(id, userId);
      return ok({
        htmlCode,
        gameId: id,
      });
    } catch (error) {
      this.logger.error(`Error playing game: ${error.message}`);
      throw error;
    }
  }

  @Post(':id/unlock')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async unlockGame(@Param('id') id: string, @Req() req: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) {
        throw new BadRequestException('Invalid token');
      }

      return ok(await this.gameService.unlock(id, userId));
    } catch (error) {
      this.logger.error(`Error unlocking game: ${error.message}`);
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
        ...result,
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

  @Get('/creator/:creatorId/reputation')
  @HttpCode(HttpStatus.OK)
  async getCreatorReputation(@Param('creatorId') creatorId: string) {
    try {
      const reputation = await this.reputationService.getReputation(creatorId);
      return ok(reputation);
    } catch (error) {
      this.logger.error(`Error getting creator reputation: ${error.message}`);
      throw error;
    }
  }

  @Patch(':id/settings')
  @UseGuards(JwtAuthGuard)
  async updateGameSettings(@Param('id') id: string, @Req() req: any, @Body() body: any) {
    try {
      const userId = req.user?.sub || req.user?.id;
      if (!userId) throw new BadRequestException('Invalid token');
      const result = await this.gameService.updateSettings(id, userId, body);
      return ok(result);
    } catch (error) {
      this.logger.error(`Error updating game settings: ${error.message}`);
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
