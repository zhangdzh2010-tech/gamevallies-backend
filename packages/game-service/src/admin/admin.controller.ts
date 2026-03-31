import {
  Controller,
  Get,
  Post,
  Put,
  Delete,
  Query,
  Param,
  Body,
  Res,
  Headers,
  HttpException,
  HttpStatus,
} from '@nestjs/common';
import { Response } from 'express';
import { AdminService } from './admin.service';
import { ok } from '../common/api-response';
import * as fs from 'fs';
import * as path from 'path';

function checkAdminToken(token: string | undefined): void {
  const adminToken = (process.env.ADMIN_TOKEN || '').trim();
  if (!adminToken) {
    throw new HttpException('Admin token is not configured', HttpStatus.SERVICE_UNAVAILABLE);
  }
  if (!token || token !== adminToken) {
    throw new HttpException('Unauthorized: Invalid admin token', HttpStatus.UNAUTHORIZED);
  }
}

@Controller()
export class AdminController {
  constructor(private readonly adminService: AdminService) {}

  /**
   * Serve admin HTML panel (excluded from api/v1 prefix)
   */
  @Get('admin')
  serveAdminPanel(@Res() res: Response) {
    const htmlPath = path.join(__dirname, 'admin-panel.html');
    if (fs.existsSync(htmlPath)) {
      const html = fs.readFileSync(htmlPath, 'utf-8');
      res.type('html').send(html);
    } else {
      res.status(404).send('Admin panel not found');
    }
  }

  @Get('admin/games')
  async listGames(
    @Headers('x-admin-token') token: string,
    @Query('page') page?: string,
    @Query('limit') limit?: string,
    @Query('search') search?: string,
    @Query('status') status?: string,
  ) {
    checkAdminToken(token);
    const p = Math.max(parseInt(page || '1', 10), 1);
    const l = Math.min(Math.max(parseInt(limit || '20', 10), 1), 100);
    const result = await this.adminService.listGames(p, l, search, status);
    return ok(result);
  }

  @Get('admin/games/:id')
  async getGame(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
  ) {
    checkAdminToken(token);
    const game = await this.adminService.getGame(id);
    return ok(game);
  }

  @Post('admin/games')
  async createGame(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const game = await this.adminService.createGame(body);
    return ok(game, 'Game created successfully');
  }

  @Put('admin/games/:id')
  async updateGame(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const game = await this.adminService.updateGame(id, body);
    return ok(game, 'Game updated successfully');
  }

  @Put('admin/games/:id/cover')
  async updateGameCover(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const game = await this.adminService.updateGameCover(id, body || {});
    return ok(game, 'Game cover updated successfully');
  }

  @Delete('admin/games/:id')
  async deleteGame(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
  ) {
    checkAdminToken(token);
    const result = await this.adminService.deleteGame(id);
    return ok(result, 'Game deleted successfully');
  }

  @Post('admin/games/batch-status')
  async batchUpdateGameStatus(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const result = await this.adminService.batchUpdateGameStatus(body?.ids, body?.status);
    return ok(result, 'Batch game status updated successfully');
  }

  @Post('admin/games/batch-delete')
  async batchDeleteGames(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const result = await this.adminService.batchDeleteGames(body?.ids);
    return ok(result, 'Batch games deleted successfully');
  }

  @Post('admin/game-status/:id')
  async toggleStatus(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const game = await this.adminService.toggleStatus(id, body.status);
    return ok(game, 'Status updated successfully');
  }

  @Post('admin/games/repair-legacy-preview-links')
  async repairLegacyPreviewLinks(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const result = await this.adminService.backfillLegacyPreviewGames(body || {});
    return ok(
      result,
      body?.dryRun ? 'Legacy preview link dry run completed' : 'Legacy preview links repaired successfully',
    );
  }

  @Post('admin/games/refresh-types')
  async refreshGameTypes(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const result = await this.adminService.refreshGameTypes(body || {});
    return ok(result, result.dryRun ? 'Game type refresh dry run completed' : 'Game types refreshed successfully');
  }

  @Post('admin/games/backfill-covers')
  async backfillGameCovers(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    const result = await this.adminService.backfillGameCovers(body || {});
    return ok(
      result,
      result.dryRun ? 'Game cover backfill dry run completed' : 'Game covers backfilled successfully',
    );
  }

  @Get('admin/stats')
  async getStats(
    @Headers('x-admin-token') token: string,
    @Query('from') from?: string,
    @Query('to') to?: string,
  ) {
    checkAdminToken(token);
    const stats = await this.adminService.getStats(from, to);
    return ok(stats);
  }

  @Get('admin/subscription/plans')
  async listSubscriptionPlans(
    @Headers('x-admin-token') token: string,
    @Query('from') from?: string,
    @Query('to') to?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.listSubscriptionPlans(from, to));
  }

  @Post('admin/subscription/plans')
  async createSubscriptionPlan(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertSubscriptionPlan(undefined, body), 'Subscription plan saved');
  }

  @Put('admin/subscription/plans/:id')
  async updateSubscriptionPlan(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertSubscriptionPlan(id, body), 'Subscription plan saved');
  }

  @Delete('admin/subscription/plans/:id')
  async deleteSubscriptionPlan(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.deleteSubscriptionPlan(id), 'Subscription plan removed');
  }

  @Get('admin/genlog')
  async listGenerationLogs(
    @Headers('x-admin-token') token: string,
    @Query('page') page?: string,
    @Query('limit') limit?: string,
    @Query('status') status?: string,
    @Query('search') search?: string,
  ) {
    checkAdminToken(token);
    const p = Math.max(parseInt(page || '1', 10), 1);
    const l = Math.min(Math.max(parseInt(limit || '20', 10), 1), 100);
    return ok(await this.adminService.listGenerationLogs(p, l, status, search));
  }

  @Get('admin/tasks')
  async listGenerationTasks(
    @Headers('x-admin-token') token: string,
    @Query('page') page?: string,
    @Query('limit') limit?: string,
    @Query('status') status?: string,
    @Query('search') search?: string,
  ) {
    checkAdminToken(token);
    const p = Math.max(parseInt(page || '1', 10), 1);
    const l = Math.min(Math.max(parseInt(limit || '20', 10), 1), 100);
    return ok(await this.adminService.listGenerationTasks(p, l, status, search));
  }

  @Get('admin/tasks/:id')
  async getGenerationTask(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.getGenerationTask(id));
  }

  @Post('admin/tasks/:id/terminate')
  async terminateGenerationTask(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(
      await this.adminService.terminateGenerationTask(id, body?.reason),
      'Task terminated successfully',
    );
  }

  @Get('admin/tasks/:id/events')
  async getGenerationTaskEvents(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Query('limit') limit?: string,
  ) {
    checkAdminToken(token);
    const l = Math.min(Math.max(parseInt(limit || '100', 10), 1), 500);
    return ok(await this.adminService.listGenerationTaskEvents(id, l));
  }

  @Get('admin/tasks/:id/artifacts')
  async getGenerationTaskArtifacts(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Query('limit') limit?: string,
  ) {
    checkAdminToken(token);
    const l = Math.min(Math.max(parseInt(limit || '100', 10), 1), 500);
    return ok(await this.adminService.listGenerationTaskArtifacts(id, l));
  }

  @Get('admin/llm/providers')
  async listLlmProviders(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.listLlmProviders());
  }

  @Get('admin/cloud/accounts')
  async listCloudAccounts(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.listCloudAccounts());
  }

  @Get('admin/cloud/regions')
  async listCloudRegions(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.listCloudRegions());
  }

  @Get('admin/cloud/ai-engine-region-targets')
  async listAiEngineRegionTargets(
    @Headers('x-admin-token') token: string,
    @Query('providerSelectableOnly') providerSelectableOnly?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.listAiEngineRegionTargets({
      providerSelectableOnly: providerSelectableOnly === 'true',
    }));
  }

  @Post('admin/cloud/ai-engine-region-targets')
  async createAiEngineRegionTarget(@Headers('x-admin-token') token: string, @Body() body: any) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertAiEngineRegionTarget(undefined, body), 'Region target saved');
  }

  @Put('admin/cloud/ai-engine-region-targets/:id')
  async updateAiEngineRegionTarget(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertAiEngineRegionTarget(id, body), 'Region target saved');
  }

  @Post('admin/cloud/ai-engine-region-targets/sync-deploy')
  async syncAiEngineRegionTargetDeployState(
    @Headers('x-admin-token') token: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.syncAiEngineRegionTargetDeployState(body), 'Region target deploy state synced');
  }

  @Get('admin/llm/steps')
  async listLlmSteps(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.listLlmSteps());
  }

  @Post('admin/llm/providers')
  async createLlmProvider(@Headers('x-admin-token') token: string, @Body() body: any) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertLlmProvider(undefined, body), 'Provider saved');
  }

  @Put('admin/llm/providers/:id')
  async updateLlmProvider(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertLlmProvider(id, body), 'Provider saved');
  }

  @Delete('admin/llm/providers/:id')
  async deleteLlmProvider(@Headers('x-admin-token') token: string, @Param('id') id: string) {
    checkAdminToken(token);
    return ok(await this.adminService.deleteLlmProvider(id), 'Provider deleted');
  }

  @Post('admin/llm/providers/:id/test')
  async testLlmProvider(@Headers('x-admin-token') token: string, @Param('id') id: string) {
    checkAdminToken(token);
    return ok(await this.adminService.testLlmProvider(id));
  }

  @Post('admin/llm/providers/catalog/preview')
  async previewLlmProviderCatalog(@Headers('x-admin-token') token: string, @Body() body: any) {
    checkAdminToken(token);
    return ok(await this.adminService.previewLlmProviderCatalog(body));
  }

  @Post('admin/llm/providers/:id/test-chat')
  async testLlmProviderChat(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.testLlmProviderChat(id, body));
  }

  @Get('admin/llm/providers/:id/test-records')
  async listLlmProviderTestRecords(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Query('limit') limit?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.listLlmProviderTestRecords(id, Number(limit) || 20));
  }

  @Get('admin/llm/routes')
  async listLlmRoutes(
    @Headers('x-admin-token') token: string,
    @Query('executionRegion') executionRegion?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.listLlmRoutes(executionRegion));
  }

  @Get('admin/llm/routes/:id')
  async getLlmRoute(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.getLlmRoute(id));
  }

  @Post('admin/llm/routes')
  async createLlmRoute(@Headers('x-admin-token') token: string, @Body() body: any) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertLlmRoute(undefined, body), 'Route saved');
  }

  @Put('admin/llm/routes/:id')
  async updateLlmRoute(
    @Headers('x-admin-token') token: string,
    @Param('id') id: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertLlmRoute(id, body), 'Route saved');
  }

  @Delete('admin/llm/routes/:id')
  async deleteLlmRoute(@Headers('x-admin-token') token: string, @Param('id') id: string) {
    checkAdminToken(token);
    return ok(await this.adminService.deleteLlmRoute(id), 'Route deleted');
  }

  @Post('admin/llm/refresh')
  async refreshLlmGateway(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.refreshLlmGateway(), 'LLM gateway refreshed');
  }

  // ===================== User Management =====================

  @Get('admin/users')
  async listUsers(
    @Headers('x-admin-token') token: string,
    @Query('page') page?: string,
    @Query('limit') limit?: string,
    @Query('search') search?: string,
    @Query('role') role?: string,
  ) {
    checkAdminToken(token);
    const p = Math.max(parseInt(page || '1', 10), 1);
    const l = Math.min(Math.max(parseInt(limit || '20', 10), 1), 100);
    return ok(await this.adminService.listUsers(p, l, search, role));
  }

  @Get('admin/users/:id')
  async getUser(@Headers('x-admin-token') token: string, @Param('id') id: string) {
    checkAdminToken(token);
    return ok(await this.adminService.getUser(id));
  }

  @Post('admin/users')
  async createUser(@Headers('x-admin-token') token: string, @Body() body: any) {
    checkAdminToken(token);
    return ok(await this.adminService.createUser(body), 'User created');
  }

  @Put('admin/users/:id')
  async updateUser(@Headers('x-admin-token') token: string, @Param('id') id: string, @Body() body: any) {
    checkAdminToken(token);
    return ok(await this.adminService.updateUser(id, body), 'User updated');
  }

  @Delete('admin/users/:id')
  async deleteUser(@Headers('x-admin-token') token: string, @Param('id') id: string) {
    checkAdminToken(token);
    return ok(await this.adminService.deleteUser(id), 'User deleted');
  }

  @Post('admin/user-password/:id')
  async resetPassword(@Headers('x-admin-token') token: string, @Param('id') id: string, @Body() body: any) {
    checkAdminToken(token);
    return ok(await this.adminService.resetUserPassword(id, body.password), 'Password reset');
  }

  @Post('admin/change-token')
  async changeAdminToken(@Headers('x-admin-token') token: string, @Body() body: any) {
    checkAdminToken(token);
    return ok(await this.adminService.changeAdminToken(token, body.newToken), 'Token changed');
  }

  // ===================== System Config =====================

  @Get('admin/configs')
  async listConfigs(
    @Headers('x-admin-token') token: string,
    @Query('category') category?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.listConfigs(category));
  }

  @Get('admin/configs/:key')
  async getConfig(
    @Headers('x-admin-token') token: string,
    @Param('key') key: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.getConfig(key));
  }

  @Put('admin/configs/:key')
  async upsertConfig(
    @Headers('x-admin-token') token: string,
    @Param('key') key: string,
    @Body() body: any,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.upsertConfig(key, body), 'Config saved');
  }

  @Post('admin/configs/init-prompts')
  async initPrompts(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.initDefaultPrompts(), 'Prompts initialized');
  }

  @Post('admin/configs/init-timeouts')
  async initTimeouts(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.initDefaultTimeouts(), 'Timeout configs initialized');
  }

  @Post('admin/configs/refresh-timeouts')
  async refreshTimeouts(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.refreshTimeoutConfigs(), 'Timeout configs refreshed');
  }

  @Get('admin/prompt-bundles')
  async listPromptBundles(
    @Headers('x-admin-token') token: string,
    @Query('status') status?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.listPromptBundles(status));
  }

  @Get('admin/runtime-profiles')
  async listRuntimeProfiles(
    @Headers('x-admin-token') token: string,
    @Query('enabledOnly') enabledOnly?: string,
  ) {
    checkAdminToken(token);
    return ok(await this.adminService.listRuntimeProfiles(enabledOnly === 'true'));
  }

  @Post('admin/migrate')
  async runMigration(@Headers('x-admin-token') token: string) {
    checkAdminToken(token);
    return ok(await this.adminService.runMigration(), 'Migration completed');
  }
}
