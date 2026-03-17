import { IsString, IsNotEmpty, IsOptional, MinLength, MaxLength, ValidateIf } from 'class-validator';

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
}
