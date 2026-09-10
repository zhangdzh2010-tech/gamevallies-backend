import { BadRequestException, ConflictException, Injectable, NotFoundException } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { getAdminUsername } from '../common/admin-auth';

const MAX_GRANT = 10000;
const MAX_BALANCE = 2147483647;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type GrantInput = { requestId?: string; userId?: string; amount?: number; reason?: string };

@Injectable()
export class CreationQuotaGrantService {
  constructor(private readonly prisma: PrismaService) {}

  private async defaultQuota(client: PrismaService | Prisma.TransactionClient) {
    const configured = Number.parseInt(process.env.BILLING_DEFAULT_FREE_QUOTA || '', 10);
    if (Number.isFinite(configured) && configured >= 0) return configured;
    const config = await client.systemConfig.findUnique({
      where: { configKey: 'billing.default_free_quota' }, select: { configValue: true },
    });
    const value = Number.parseInt(config?.configValue || '', 10);
    return Number.isFinite(value) && value >= 0 ? value : 5;
  }

  async options(userId?: string) {
    let current = null;
    if (userId) {
      const user = await this.prisma.user.findUnique({ where: { id: userId }, select: { id: true } });
      if (!user) throw new NotFoundException('用户不存在');
      const [quota, subscription] = await Promise.all([
        this.prisma.userQuota.findUnique({ where: { userId } }),
        this.prisma.userSubscription.findFirst({
          where: { userId, status: 'active', expiresAt: { gt: new Date() } },
          orderBy: { expiresAt: 'desc' },
        }),
      ]);
      const total = quota?.totalFreeQuota ?? await this.defaultQuota(this.prisma);
      const used = quota?.usedFreeQuota ?? 0;
      const subscriptionRemaining = subscription ? Math.max(0, subscription.quotaThisPeriod - subscription.usedThisPeriod) : 0;
      current = { total, used, remaining: Math.max(0, total - used), subscriptionRemaining,
        available: Math.max(0, total - used) + subscriptionRemaining };
    }
    const history = await this.prisma.adminCreationQuotaGrant.findMany({
      where: userId ? { userId } : {}, orderBy: { createdAt: 'desc' }, take: 50,
    });
    return { current, history, maxAmount: MAX_GRANT };
  }

  async grant(body: GrantInput) {
    const { requestId, userId, amount } = body || {};
    const reason = typeof body?.reason === 'string' ? body.reason.trim() : '';
    if (typeof requestId !== 'string' || !UUID.test(requestId) || typeof userId !== 'string' ||
        !userId.trim() || userId.length > 36 || typeof amount !== 'number' || !Number.isInteger(amount) || amount < 1 || amount > MAX_GRANT ||
        !reason || reason.length > 255) {
      throw new BadRequestException('请选择用户，输入 1–10000 的整数次数，并填写 1–255 字授予原因');
    }
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        return await this.prisma.$transaction(async tx => {
          // Share the account lock with subscription grants. Quota and audit commit together.
          await tx.$queryRaw`SELECT id FROM users WHERE id = ${userId} FOR UPDATE`;
          const previous = await tx.adminCreationQuotaGrant.findUnique({ where: { id: requestId } });
          if (previous) {
            if (previous.userId !== userId || previous.amount !== amount || previous.reason !== reason) {
              throw new ConflictException('该请求编号已用于另一项授予，请重新确认');
            }
            return previous;
          }
          const user = await tx.user.findUnique({ where: { id: userId } });
          if (!user) throw new NotFoundException('用户不存在');
          await tx.userQuota.upsert({
            where: { userId }, update: {},
            create: { userId, totalFreeQuota: await this.defaultQuota(tx), usedFreeQuota: 0 },
          });
          await tx.$queryRaw`SELECT user_id FROM user_quotas WHERE user_id = ${userId} FOR UPDATE`;
          const before = await tx.userQuota.findUniqueOrThrow({ where: { userId } });
          if (before.totalFreeQuota > MAX_BALANCE - amount) throw new BadRequestException('累计额度超过上限');
          const after = await tx.userQuota.update({
            where: { userId }, data: { totalFreeQuota: { increment: amount } },
          });
          // Uses the existing creation/failed-task-refund balance; never resets usage or membership.
          return tx.adminCreationQuotaGrant.create({ data: {
            id: requestId, userId, amount, reason, operator: getAdminUsername(),
            userLabel: `${user.displayName || user.username} (${user.phone || user.email || user.id})`.slice(0, 255),
            totalBefore: before.totalFreeQuota, totalAfter: after.totalFreeQuota,
            usedAtGrant: before.usedFreeQuota,
          } });
        }, { isolationLevel: 'ReadCommitted' });
      } catch (error) {
        if (!['P2034', 'P2002'].includes(error?.code)) throw error;
        if (attempt === 2) throw new ConflictException('额度正在更新，请使用同一请求编号重试');
      }
    }
    throw new ConflictException('额度正在更新，请使用同一请求编号重试');
  }
}
