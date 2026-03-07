import {
  Controller,
  Post,
  Get,
  Delete,
  Body,
  Param,
  Query,
  UseGuards,
  Request,
  HttpCode,
  HttpStatus,
} from '@nestjs/common';
import { IsString, IsOptional, IsNumber, Min, Max } from 'class-validator';
import { Type } from 'class-transformer';
import { CommentService } from './comment.service';
import { JwtAuthGuard } from '../common/jwt-auth.guard';
import { ok, toPage } from '../common/api-response';
import { presentComment } from '../common/social-presenter';

class CreateCommentDto {
  @IsString()
  gameId: string;

  @IsString()
  content: string;

  @IsOptional()
  @IsString()
  parentId?: string;
}

class PaginationDto {
  @Type(() => Number)
  @IsNumber()
  @Min(1)
  page: number = 1;

  @Type(() => Number)
  @IsNumber()
  @Min(1)
  @Max(100)
  limit: number = 20;
}

@Controller('comments')
export class CommentController {
  constructor(private readonly commentService: CommentService) {}

  @Post()
  @UseGuards(JwtAuthGuard)
  async createComment(
    @Body() dto: CreateCommentDto,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id || 'anonymous';
    const comment = await this.commentService.createComment(userId, dto);
    return ok(presentComment(comment));
  }

  @Get('games/:gameId')
  async getGameComments(
    @Param('gameId') gameId: string,
    @Query() pagination: PaginationDto,
  ) {
    const result = await this.commentService.getGameComments(gameId, pagination.page, pagination.limit);
    return ok(
      toPage(
        result.data.map((comment: any) => presentComment(comment)),
        result.pagination.page,
        result.pagination.limit,
        result.pagination.total,
      ),
    );
  }

  @Get(':commentId/replies')
  async getReplies(
    @Param('commentId') commentId: string,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '5',
  ) {
    const result = await this.commentService.getCommentReplies(
      commentId,
      Math.max(1, parseInt(page, 10) || 1),
      Math.min(100, Math.max(1, parseInt(limit, 10) || 5)),
    );

    return ok(
      toPage(
        result.data.map((comment: any) => presentComment(comment)),
        result.pagination.page,
        result.pagination.limit,
        result.pagination.total,
      ),
    );
  }

  @Delete(':id')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async deleteComment(
    @Param('id') commentId: string,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id || 'anonymous';
    await this.commentService.deleteComment(commentId, userId);
    return ok(null);
  }

  @Post(':id/like')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async likeComment(
    @Param('id') commentId: string,
    @Request() req: any,
  ) {
    const userId = req.user?.sub || req.user?.id || 'anonymous';
    const result = await this.commentService.likeComment(commentId, userId);
    return ok({
      liked: result.liked,
      likes: result.likeCount,
    });
  }
}
