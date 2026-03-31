import { Type } from 'class-transformer';
import { IsString, IsNotEmpty, MinLength, IsOptional, IsInt, Min, Max, IsIn } from 'class-validator';
import { CREATE_GAME_GENERATION_TIERS, CreateGameGenerationTier } from './create-game.dto';

export class IterateGameDto {
  @IsString()
  @IsNotEmpty()
  @MinLength(5)
  feedback: string;

  @IsOptional()
  @IsString()
  regionHint?: string;

  @IsOptional()
  @IsString()
  @IsIn(CREATE_GAME_GENERATION_TIERS)
  generationTier?: CreateGameGenerationTier;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(30)
  @Max(3600)
  timeoutS?: number;
}
