import {
  Controller,
  Get,
  Param,
  Query,
} from '@nestjs/common';
import { Type } from 'class-transformer';
import { IsNumber, Min, Max } from 'class-validator';
import { ChallengeService } from './challenge.service';
import { ok, toPage } from '../common/api-response';
import { presentChallenge, presentGame } from '../common/feed-presenter';

class PaginationDto {
  @Type(() => Number)
  @IsNumber()
  @Min(1)
  page: number = 1;

  @Type(() => Number)
  @IsNumber()
  @Min(1)
  @Max(100)
  limit: number = 20;
}

@Controller('challenges')
export class ChallengeController {
  constructor(private readonly challengeService: ChallengeService) {}

  @Get('current')
  async getCurrentChallenge() {
    return ok(presentChallenge(await this.challengeService.getCurrentChallenge()));
  }

  @Get(':id/games')
  async getChallengeGames(
    @Param('id') challengeId: string,
    @Query() pagination: PaginationDto,
  ) {
    const result = await this.challengeService.getChallengeGames(
      challengeId,
      pagination.page,
      pagination.limit,
    );

    return ok(toPage({
      data: result.data.map((game: any) => presentGame(game)),
      pagination: result.pagination,
    }));
  }
}
