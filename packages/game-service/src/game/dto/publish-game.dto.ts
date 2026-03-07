import { IsString, IsArray, IsOptional, MinLength } from 'class-validator';

export class PublishGameDto {
  @IsString()
  @IsOptional()
  @MinLength(3)
  title?: string;

  @IsString()
  @IsOptional()
  @MinLength(10)
  description?: string;

  @IsArray()
  @IsOptional()
  tags?: string[];

  @IsString()
  @IsOptional()
  gameType?: string;
}
