import { Type } from 'class-transformer';
import {
  IsIn,
  IsInt,
  IsOptional,
  IsString,
  Max,
  MaxLength,
  Min,
  MinLength,
} from 'class-validator';
import {
  CREATE_GAME_GENERATION_TIERS,
  CREATE_GAME_ORIENTATIONS,
  CreateGameGenerationTier,
  CreateGameOrientation,
} from './create-game.dto';
import { CREATION_SESSION_ENTRY_MODES, CreationSessionEntryMode } from '../creation-session.constants';

export class CreateCreationSessionDto {
  @IsString()
  @MinLength(5)
  prompt!: string;

  @IsOptional()
  @IsString()
  @MaxLength(50)
  title?: string;

  @IsOptional()
  @IsString()
  regionHint?: string;

  @IsOptional()
  @IsString()
  @IsIn(CREATE_GAME_ORIENTATIONS)
  orientation?: CreateGameOrientation;

  @IsOptional()
  @IsString()
  @IsIn(CREATE_GAME_GENERATION_TIERS)
  generationTier?: CreateGameGenerationTier;

  @IsOptional()
  @IsString()
  @IsIn(CREATION_SESSION_ENTRY_MODES)
  entryMode?: CreationSessionEntryMode;

  @IsOptional()
  @IsString()
  @MaxLength(36)
  sourceGameId?: string;
}

export class CreateCreationSessionMessageDto {
  @IsString()
  @MinLength(1)
  content!: string;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  revision?: number;
}

export class SkipCreationSessionQuestionDto {
  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  revision?: number;
}

export class GenerateCreationSessionDto {
  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  revision?: number;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(30)
  @Max(3600)
  timeoutS?: number;
}
