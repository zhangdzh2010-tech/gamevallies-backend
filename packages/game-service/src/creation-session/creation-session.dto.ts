import { Type } from 'class-transformer';
import {
  IsBoolean,
  IsIn,
  IsInt,
  IsNotEmpty,
  IsOptional,
  IsString,
  MaxLength,
  MinLength,
} from 'class-validator';

export type EntryMode = 'fresh' | 'fork';
export type CreationQuestionAnswerSource = 'option' | 'text';

export class CreateCreationSessionDto {
  @IsString()
  @IsNotEmpty()
  @MinLength(5)
  @MaxLength(5000)
  prompt!: string;

  @IsOptional()
  @IsString()
  @MaxLength(64)
  title?: string;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  regionHint?: string;

  @IsOptional()
  @IsString()
  @MaxLength(16)
  locale?: string;

  @IsOptional()
  @IsIn(['fresh', 'fork'])
  entryMode?: EntryMode;

  @IsOptional()
  @IsString()
  sourceGameId?: string;

  @IsOptional()
  @IsString()
  @MaxLength(64)
  clientRequestId?: string;
}

export class CreationMessageDto {
  @IsString()
  @IsNotEmpty()
  @MinLength(1)
  @MaxLength(1000)
  content!: string;

  @IsOptional()
  @IsString()
  questionId?: string;

  @IsInt()
  expectedRevision!: number;

  @IsOptional()
  @IsIn(['option', 'text'])
  source?: CreationQuestionAnswerSource;

  @IsOptional()
  @IsString()
  @MaxLength(64)
  clientMessageId?: string;
}

export class CreationSkipDto {
  @IsOptional()
  @IsString()
  questionId?: string;

  @IsInt()
  expectedRevision!: number;
}

export class CreationGenerateDto {
  @IsInt()
  expectedRevision!: number;

  @IsOptional()
  @IsBoolean()
  force?: boolean;

  @IsOptional()
  @IsString()
  @MaxLength(64)
  idempotencyKey?: string;
}

export class CreationAbandonDto {
  @IsInt()
  expectedRevision!: number;
}

export function asIsoString(value: Date | undefined | null): string | null {
  if (!value) return null;
  return value.toISOString();
}

