import { IsString, IsNotEmpty, MinLength } from 'class-validator';

export class IterateGameDto {
  @IsString()
  @IsNotEmpty()
  @MinLength(5)
  feedback: string;
}
