import { BadRequestException, ConflictException, NotFoundException } from '@nestjs/common';
import { randomUUID } from 'crypto';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

// One mapping authority. Persisted stepKeys are consumed by the Python runtime;
// the browser and runtime do not maintain separate business-step dictionaries.
export const LLM_BUSINESS_STAGES = [
  { id: 'generate', name: '作品生成', description: '首次生成与生成后的代码修复', stepKeys: ['code_generate.full', 'quality_gate.patch_fix', 'qa_fix.syntax_structural'] },
  { id: 'modify', name: '作品修改', description: '修改分类、参数、内容和玩法调整', stepKeys: ['iterate.classify', 'iterate.param_adjust', 'iterate.element_change', 'iterate.mechanic_change'] },
  { id: 'assist', name: '辅助处理', description: '需求解析、创意整理与质量评审', stepKeys: ['intent_parse', 'creative_anchors', 'code_review'] },
];

const providerView = { id: true, name: true, region: true, enabled: true, updatedAt: true };

export async function listGatewayModels(db: PrismaService) {
  return db.llmGatewayModel.findMany({ include: { provider: { select: providerView } }, orderBy: { createdAt: 'asc' } });
}

function positiveLimit(value: unknown): number | null {
  if (value === undefined || value === null || value === '') return null;
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value <= 0 || value > 2147483647) {
    throw new BadRequestException('Token 上限必须为正整数或留空');
  }
  return value;
}

export async function saveGatewayModel(db: PrismaService, id: string | undefined, body: any) {
  const existing = id ? await db.llmGatewayModel.findUnique({ where: { id } }) : null;
  if (id && !existing) throw new NotFoundException('模型不存在');
  const providerId = String(body?.providerId || existing?.providerId || '');
  const modelId = String(body?.modelId || '').trim();
  const name = String(body?.name || modelId).trim();
  if (!providerId || !modelId || modelId.length > 128 || name.length > 128) throw new BadRequestException('请选择服务并填写有效模型 ID');
  if (existing && existing.providerId !== providerId) throw new BadRequestException('更换服务请新增模型，避免改变已绑定模型的身份');
  if (!await db.llmGatewayProvider.findUnique({ where: { id: providerId } })) throw new BadRequestException('服务不存在');
  const enabled = body.enabled !== false;
  const data = {
    providerId, modelId, name, enabled,
    contextWindow: positiveLimit(body.contextWindow), maxOutputTokens: positiveLimit(body.maxOutputTokens),
    // Legacy constraints are retained, not guessed or silently removed.
    capabilityFlags: (existing?.capabilityFlags || {}) as Prisma.InputJsonValue,
    latestTest: Prisma.DbNull,
  };
  try {
    return await db.$transaction(async tx => {
      if (existing && !enabled && await tx.llmBusinessBinding.count({ where: { OR: [{ primaryModelId: id }, { fallbackModelId: id }] } })) {
        throw new BadRequestException('此模型仍被业务环节使用，请先更换业务模型');
      }
      if (existing) {
        if (body.configurationVersion !== existing.configurationVersion) throw new ConflictException('模型配置已更新，请刷新后重试');
        const updated = await tx.llmGatewayModel.updateMany({
          where: { id, configurationVersion: body.configurationVersion },
          data: { ...data, configurationVersion: { increment: 1 } },
        });
        if (updated.count !== 1) throw new ConflictException('模型配置已更新，请刷新后重试');
        return tx.llmGatewayModel.findUnique({ where: { id }, include: { provider: { select: providerView } } });
      }
      return tx.llmGatewayModel.create({ data: { id: randomUUID(), ...data }, include: { provider: { select: providerView } } });
    });
  } catch (error: any) {
    if (error.code === 'P2002') throw new ConflictException('该服务下已存在同名模型 ID');
    throw error;
  }
}

export async function listBusinessBindings(db: PrismaService, region: string, legacyRoutes: any[]) {
  const bindings = await db.llmBusinessBinding.findMany({ where: { region } });
  return LLM_BUSINESS_STAGES.map(stage => {
    const binding = bindings.find(item => item.stage === stage.id);
    const legacy = stage.stepKeys.map(stepKey => {
      const route = legacyRoutes.find(item => item.stepKey === stepKey);
      return { stepKey, providerId: route?.providerId || null, model: route?.effectiveModelDefault || route?.modelDefault || null };
    });
    return { ...stage, region, binding: binding || null, legacy: binding ? [] : legacy,
      status: binding ? 'configured' : legacy.some(item => item.providerId) ? 'legacy' : 'missing' };
  });
}

export async function saveBusinessBindings(db: PrismaService, body: any) {
  const region = String(body?.region || '').trim();
  const rows = body?.bindings;
  if (!region || !Array.isArray(rows) || rows.length !== 3 || new Set(rows.map(row => row.stage)).size !== 3) {
    throw new BadRequestException('请完整配置生成、修改、辅助三个环节');
  }
  return db.$transaction(async tx => {
    const saved = [];
    for (const row of rows) {
      const stage = LLM_BUSINESS_STAGES.find(item => item.id === row.stage);
      if (!stage || !row.primaryModelId) throw new BadRequestException('每个环节都需要主模型');
      if (row.primaryModelId === row.fallbackModelId) throw new BadRequestException('备用模型不能与主模型相同');
      const ids = [row.primaryModelId, ...(row.fallbackModelId ? [row.fallbackModelId] : [])];
      const models = await tx.llmGatewayModel.findMany({ where: { id: { in: ids } }, include: { provider: { select: providerView } } });
      if (models.length !== ids.length || models.some(model => !model.enabled || !model.provider.enabled || model.provider.region !== region)) {
        throw new BadRequestException('所选模型或服务不可用，或不属于当前区域');
      }
      const current = await tx.llmBusinessBinding.findUnique({ where: { llm_business_region_stage_key: { region, stage: stage.id } } });
      const data = { primaryModelId: row.primaryModelId, fallbackModelId: row.fallbackModelId || null, stepKeys: stage.stepKeys };
      if (current) {
        if (row.revision !== current.revision) throw new ConflictException('业务配置已更新，请刷新后重试');
        const updated = await tx.llmBusinessBinding.updateMany({ where: { id: current.id, revision: row.revision }, data: { ...data, revision: { increment: 1 } } });
        if (updated.count !== 1) throw new ConflictException('业务配置已更新，请刷新后重试');
      } else {
        if (row.revision !== null && row.revision !== undefined) throw new ConflictException('业务配置已变化，请刷新');
        await tx.llmBusinessBinding.create({ data: { id: randomUUID(), region, stage: stage.id, ...data } });
      }
      saved.push(stage.id);
    }
    return { saved: true, stages: saved };
  }, { timeout: 15000 });
}
