import { BadRequestException, ConflictException, Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { getAdminUsername } from '../common/admin-auth';

@Injectable()
export class SubscriptionGrantService {
  constructor(private readonly prisma: PrismaService) {}

  async options(userId?: string) {
    const [plans, current, history] = await Promise.all([
      this.prisma.subscriptionPlan.findMany({ where: { active: true }, orderBy: { sortOrder: 'asc' } }),
      userId ? this.prisma.userSubscription.findFirst({
        where: { userId, status: 'active', expiresAt: { gt: new Date() } }, include: { plan: true },
        orderBy: { expiresAt: 'desc' },
      }) : null,
      this.prisma.adminSubscriptionGrant.findMany({
        where: userId ? { userId } : {}, orderBy: { createdAt: 'desc' }, take: 50,
      }),
    ]);
    return { plans, current, history };
  }

  async grant(body: { requestId?: string; userId?: string; planId?: string; reason?: string }) {
    const { requestId, userId, planId } = body || {};
    const reason = typeof body?.reason === 'string' ? body.reason.trim() : '';
    if (!requestId || !/^[0-9a-f-]{36}$/i.test(requestId) ||
        typeof userId !== 'string' || !userId || typeof planId !== 'string' || !planId ||
        !reason || reason.length > 255) {
      throw new BadRequestException('请选择用户和套餐，填写 1–255 字授予原因');
    }
    return this.prisma.$transaction(async tx => {
      // Serializes grants to the same account, including retries with different request IDs.
      await tx.$queryRaw`SELECT id FROM users WHERE id = ${userId} FOR UPDATE`;
      const previous = await tx.adminSubscriptionGrant.findUnique({ where: { id: requestId } });
      if (previous) {
        if (previous.userId !== userId || previous.planId !== planId || previous.reason !== reason) {
          throw new ConflictException('该请求编号已用于另一项授予，请刷新后重试');
        }
        return previous;
      }
      const user = await tx.user.findUnique({ where: { id: userId } });
      if (!user) throw new NotFoundException('用户不存在');
      const plan = await tx.subscriptionPlan.findUnique({ where: { id: planId } });
      if (!plan?.active || !Number.isInteger(plan.quota) || plan.quota <= 0) {
        throw new BadRequestException('套餐已停用或额度无效');
      }
      const now = new Date();
      const active = await tx.userSubscription.findFirst({
        where: { userId, status: 'active', expiresAt: { gt: now } },
      });
      if (active) throw new ConflictException('用户已有有效套餐，本次未授予，避免覆盖现有权益');
      const expiresAt = new Date(now);
      // Clamp month ends rather than allowing e.g. Jan 31 to overflow into March.
      const day = now.getUTCDate();
      expiresAt.setUTCDate(1);
      expiresAt.setUTCMonth(expiresAt.getUTCMonth() + (plan.period === 'yearly' ? 12 : 1));
      const lastDay = new Date(Date.UTC(expiresAt.getUTCFullYear(), expiresAt.getUTCMonth() + 1, 0)).getUTCDate();
      expiresAt.setUTCDate(Math.min(day, lastDay));
      await tx.userSubscription.updateMany({
        where: { userId, status: 'active', expiresAt: { lte: now } }, data: { status: 'expired' },
      });
      const subscription = await tx.userSubscription.create({ data: {
        userId, planId, startedAt: now, expiresAt, status: 'active',
        quotaThisPeriod: plan.quota, usedThisPeriod: 0, autoRenew: false,
      } });
      await tx.user.update({ where: { id: userId }, data: { isPro: true, proExpires: expiresAt } });
      // Dedicated audit record: no fake paid order, revenue or automatic renewal.
      return tx.adminSubscriptionGrant.create({ data: {
        id: requestId, userId, planId, subscriptionId: subscription.id,
        userLabel: `${user.displayName || user.username} (${user.phone || user.email || user.id})`.slice(0, 255),
        planName: plan.name, quota: plan.quota, startedAt: now, expiresAt, reason,
        operator: getAdminUsername(),
      } });
    }, { isolationLevel: 'Serializable' });
  }
}
