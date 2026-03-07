import {
  Controller,
  Get,
  Patch,
  Query,
  Param,
  Body,
  UseGuards,
  Req,
  HttpCode,
  HttpStatus,
  Post,
  Delete,
} from '@nestjs/common';
import { IsString } from 'class-validator';
import { UserService } from './user.service';
import { UpdateProfileDto } from './dto';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { ok, okAlt, presentUser, toPage } from '../common/api-response';

class UpdateAvatarDto {
  @IsString()
  filePath: string;
}

@Controller('users')
export class UserController {
  constructor(private userService: UserService) {}

  @Get('me')
  @UseGuards(JwtAuthGuard)
  async getCurrentUser(@Req() req: any) {
    return ok(presentUser(await this.userService.findById(req.user.userId)));
  }

  @Get('search')
  async searchUsers(
    @Query('q') query: string,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '20',
  ) {
    const pageNum = parseInt(page, 10) || 1;
    const limitNum = parseInt(limit, 10) || 20;
    const result = await this.userService.searchUsers(query, pageNum, limitNum);
    return okAlt({
      items: result.data.map((user) => presentUser(user)),
      hasMore: result.pagination.page < result.pagination.pages,
      page: result.pagination.page,
      limit: result.pagination.limit,
      total: result.pagination.total,
    });
  }

  @Get(':id/followers')
  async getFollowers(
    @Param('id') id: string,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '20',
  ) {
    const result = await this.userService.getFollowers(
      id,
      Math.max(1, parseInt(page, 10) || 1),
      Math.min(100, Math.max(1, parseInt(limit, 10) || 20)),
    );

    return okAlt(
      toPage(
        result.data.map((user) => presentUser(user)),
        result.pagination.page,
        result.pagination.limit,
        result.pagination.total,
      ),
    );
  }

  @Get(':id/following')
  async getFollowing(
    @Param('id') id: string,
    @Query('page') page: string = '1',
    @Query('limit') limit: string = '20',
  ) {
    const result = await this.userService.getFollowing(
      id,
      Math.max(1, parseInt(page, 10) || 1),
      Math.min(100, Math.max(1, parseInt(limit, 10) || 20)),
    );

    return okAlt(
      toPage(
        result.data.map((user) => presentUser(user)),
        result.pagination.page,
        result.pagination.limit,
        result.pagination.total,
      ),
    );
  }

  @Post(':id/follow')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async followUser(@Param('id') id: string, @Req() req: any) {
    await this.userService.followUser(req.user.userId, id);
    return okAlt(null);
  }

  @Delete(':id/follow')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async unfollowUser(@Param('id') id: string, @Req() req: any) {
    await this.userService.unfollowUser(req.user.userId, id);
    return okAlt(null);
  }

  @Get(':id/stats')
  async getUserStats(@Param('id') id: string) {
    return okAlt(await this.userService.getUserStats(id));
  }

  @Get(':id/profile')
  @HttpCode(HttpStatus.OK)
  async getUserProfile(@Param('id') id: string) {
    return ok(await this.userService.getProfile(id));
  }

  @Get(':id')
  @HttpCode(HttpStatus.OK)
  async getUserById(@Param('id') id: string) {
    return ok(presentUser(await this.userService.findById(id)));
  }

  @Patch('profile')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async updateProfile(@Req() req: any, @Body() dto: UpdateProfileDto) {
    return ok(presentUser(await this.userService.updateProfile(req.user.userId, dto)));
  }

  @Post('avatar')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async updateAvatar(@Req() req: any, @Body() dto: UpdateAvatarDto) {
    return ok(await this.userService.updateAvatar(req.user.userId, dto.filePath));
  }

  @Patch(':id/deactivate')
  @UseGuards(JwtAuthGuard)
  @HttpCode(HttpStatus.OK)
  async deactivateUser(@Param('id') id: string, @Req() req: any) {
    // Only allow users to deactivate themselves
    if (req.user.userId !== id) {
      throw new Error('Unauthorized: Can only deactivate own account');
    }
    return this.userService.deactivateUser(id);
  }
}
