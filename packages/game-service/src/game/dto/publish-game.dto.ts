import { IsString, IsArray, IsOptional, MinLength, IsIn } from 'class-validator';

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

  @IsString()
  @IsOptional()
  @IsIn(['public', 'private'])
  visibility?: 'public' | 'private';
}
