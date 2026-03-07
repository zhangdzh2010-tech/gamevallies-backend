import { IsString, MinLength, MaxLength } from 'class-validator';

export class LoginDto {
  @IsString()
  @MinLength(3)
  @MaxLength(128)
  account: string; // Can be username or email

  @IsString()
  @MinLength(6)
  @MaxLength(128)
  password: string;
}
