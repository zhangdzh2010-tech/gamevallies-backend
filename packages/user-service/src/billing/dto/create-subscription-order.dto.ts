import { IsOptional, IsString } from 'class-validator';

export class CreateSubscriptionOrderDto {
  @IsString()
  planId: string;

  @IsOptional()
  @IsString()
  gameId?: string;
}
