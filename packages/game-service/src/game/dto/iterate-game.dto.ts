import { Type } from 'class-transformer';
import { IsString, IsNotEmpty, MinLength, IsOptional, IsInt, Min, Max } from 'class-validator';

export class IterateGameDto {
  @IsString()
  @IsNotEmpty()
  @MinLength(5)
  feedback: string;

  @IsOptional()
  @IsString()
  regionHint?: string;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(30)
  @Max(3600)
  timeoutS?: number;
}
