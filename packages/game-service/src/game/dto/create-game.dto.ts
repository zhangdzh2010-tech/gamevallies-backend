import { Type } from 'class-transformer';
import {
  IsString,
  IsNotEmpty,
  IsOptional,
  MinLength,
  MaxLength,
  ValidateIf,
  IsInt,
  Min,
  Max,
  IsIn,
} from 'class-validator';

export const CREATE_GAME_ORIENTATIONS = ['portrait', 'landscape'] as const;
export type CreateGameOrientation = (typeof CREATE_GAME_ORIENTATIONS)[number];
export const CREATE_GAME_GENERATION_TIERS = ['safe', 'standard', 'showcase'] as const;
export type CreateGameGenerationTier = (typeof CREATE_GAME_GENERATION_TIERS)[number];

export class CreateGameDto {
  @ValidateIf((dto) => !dto.prompt)
  @IsString()
  @IsNotEmpty()
  @MinLength(10)
  description?: string;

  @ValidateIf((dto) => !dto.description)
  @IsString()
  @IsNotEmpty()
  @MinLength(10)
  @IsOptional()
  prompt?: string;

  @IsString()
  @IsOptional()
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
  @Type(() => Number)
  @IsInt()
  @Min(30)
  @Max(3600)
  timeoutS?: number;
}
