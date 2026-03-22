import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  GameAccessGrantSource,
  Prisma,
  SubscriptionPeriod,
  SubscriptionOrderStatus,
  UserSubscriptionStatus,
} from '@prisma/client';
import { ConfigService } from '@nestjs/config';
import { randomBytes } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';
import { WechatPayService } from './wechat-pay.service';
import { CreateSubscriptionOrderDto } from './dto/create-subscription-order.dto';

const DEFAULT_PLANS = [
  {
    id: 'plan_monthly_basic',
    name: '基础月卡',
    description: '每月 10 次可试玩解锁额度',
    price: 990,
    currency: 'CNY',
    period: SubscriptionPeriod.monthly,
    quota: 10,
    features: ['每月10次创建', 'AI迭代优化', '优先生成'],
    recommended: false,
    badge: null,
    sortOrder: 10,
  },
  {
    id: 'plan_monthly_pro',
    name: '专业月卡',
    description: '每月 30 次可试玩解锁额度',
    price: 1990,
    currency: 'CNY',
    period: SubscriptionPeriod.monthly,
    quota: 30,
    features: ['每月30次创建', '无限AI迭代', '优先生成', '专属客服'],
    recommended: true,
    badge: '推荐',
    sortOrder: 20,
  },
] as const;

type BillingDbClient = PrismaService | Prisma.TransactionClient;

@Injectable()
export class BillingService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly configService: ConfigService,
    private readonly wechatPayService: WechatPayService,
  ) {}

  async getQuota(userId: string) {
    await this.markExpiredSubscriptions(this.prisma, userId);
    const quota = await this.ensureUserQuota(this.prisma, userId);
    const subscription = await this.findActiveSubscription(this.prisma, userId);

    return this.buildQuotaResponse(quota, subscription);
  }

  async listPlans() {
    await this.ensurePlansSeeded();

    const [plans, subscriberCount] = await Promise.all([
      this.prisma.subscriptionPlan.findMany({
        where: { active: true },
        orderBy: [{ sortOrder: 'asc' }, { price: 'asc' }],
      }),
      this.prisma.userSubscription.count({
        where: {
          status: UserSubscriptionStatus.active,
          expiresAt: { gt: new Date() },
        },
      }),
    ]);

    return {
      plans: plans.map((plan) => ({
        id: plan.id,
        name: plan.name,
        price: plan.price,
        priceDisplay: (plan.price / 100).toFixed(1),
        currency: plan.currency,
        period: plan.period,
        periodLabel: plan.period === SubscriptionPeriod.yearly ? '年' : '月',
        quota: plan.quota,
        quotaLabel: `${plan.quota}次/${plan.period === SubscriptionPeriod.yearly ? '年' : '月'}`,
        features: Array.isArray(plan.features) ? plan.features : [],
        recommended: plan.recommended,
        badge: plan.badge,
      })),
      subscriberCount,
    };
  }

  async createOrder(
    userId: string,
    dto: CreateSubscriptionOrderDto,
    clientIp: string,
  ) {
    await this.ensurePlansSeeded();

    const [user, plan] = await Promise.all([
      this.prisma.user.findUnique({
        where: { id: userId },
        select: {
          id: true,
          wxOpenId: true,
        },
      }),
      this.prisma.subscriptionPlan.findFirst({
        where: {
          id: dto.planId,
          active: true,
        },
      }),
    ]);

    if (!user) {
      throw new NotFoundException('User not found');
    }

    if (!plan) {
      throw new NotFoundException('Subscription plan not found');
    }

    if (!this.wechatPayService.isMockMode() && !user.wxOpenId) {
      throw new BadRequestException('当前账号未绑定微信小程序 OpenID，无法调起微信支付');
    }

    if (dto.gameId) {
      await this.assertGameOwnership(userId, dto.gameId);
    }

    const now = new Date();
    const appId = this.configService.get<string>('WECHAT_MINIAPP_APP_ID') || '';
    const orderId = `order_${this.formatTimestamp(now)}_${randomBytes(4).toString('hex')}`;
    const outTradeNo = `gv${this.formatTimestamp(now)}${randomBytes(5).toString('hex')}`.slice(0, 32);
    const notifyUrl = this.resolveNotifyUrl();
    await this.expireStalePendingOrders(userId, plan.id, dto.gameId || null, now);

    const reusableOrder = await this.findReusablePendingOrder(
      userId,
      plan.id,
      dto.gameId || null,
      now,
    );
    if (reusableOrder?.prepayId) {
      return {
        orderId: reusableOrder.id,
        payment: this.wechatPayService.buildPaymentFromPrepayId(
          appId,
          reusableOrder.prepayId,
        ),
      };
    }

    const paymentResult = await this.wechatPayService.createPayment({
      appId,
      openId: user.wxOpenId || '',
      description: `GameVallies ${plan.name}`,
      outTradeNo,
      amount: plan.price,
      notifyUrl,
      clientIp,
    });

    await this.prisma.subscriptionOrder.create({
      data: {
        id: orderId,
        userId,
        planId: plan.id,
        amount: plan.price,
        currency: plan.currency,
        status: SubscriptionOrderStatus.pending,
        outTradeNo,
        prepayId: paymentResult.prepayId,
        gameIdToUnlock: dto.gameId || null,
        description: `GameVallies ${plan.name}`,
        paymentPayload: {
          appId,
          notifyUrl,
          clientIp,
        } as unknown as Prisma.InputJsonValue,
        paymentResponse: paymentResult.rawResponse as unknown as Prisma.InputJsonValue,
        expiresAt: new Date(now.getTime() + 2 * 60 * 60 * 1000),
      },
    });

    return {
      orderId,
      payment: paymentResult.payment,
    };
  }

  async getOrderStatus(userId: string, orderId: string) {
    await this.markExpiredSubscriptions(this.prisma, userId);

    const order = await this.prisma.subscriptionOrder.findUnique({
      where: { id: orderId },
      include: {
        plan: true,
      },
    });

    if (!order || order.userId !== userId) {
      throw new NotFoundException('Subscription order not found');
    }

    let effectiveOrder = order;
    if (
      order.status === SubscriptionOrderStatus.pending
      && order.expiresAt
      && order.expiresAt <= new Date()
    ) {
      effectiveOrder = await this.prisma.subscriptionOrder.update({
        where: { id: orderId },
        data: {
          status: SubscriptionOrderStatus.canceled,
        },
        include: {
          plan: true,
        },
      });
    }

    const [quota, subscription] = await Promise.all([
      this.ensureUserQuota(this.prisma, userId),
      this.findActiveSubscription(this.prisma, userId),
    ]);

    return {
      orderId: effectiveOrder.id,
      status: effectiveOrder.status,
      planId: effectiveOrder.planId,
      planName: effectiveOrder.plan.name,
      amount: effectiveOrder.amount,
      currency: effectiveOrder.currency,
      gameIdToUnlock: effectiveOrder.gameIdToUnlock,
      paidAt: effectiveOrder.paidAt,
      expiresAt: effectiveOrder.expiresAt,
      quotaRemaining: this.computeQuotaRemaining(quota, subscription),
      subscriptionActive: Boolean(subscription),
    };
  }

  async getSubscriptionStatus(userId: string) {
    await this.markExpiredSubscriptions(this.prisma, userId);
    const subscription = await this.findActiveSubscription(this.prisma, userId);

    if (!subscription) {
      return {
        active: false,
        planId: null,
        planName: null,
        expiresAt: null,
        usedThisPeriod: 0,
        quotaThisPeriod: 0,
        autoRenew: false,
      };
    }

    return {
      active: true,
      planId: subscription.planId,
      planName: subscription.plan.name,
      expiresAt: subscription.expiresAt,
      usedThisPeriod: subscription.usedThisPeriod,
      quotaThisPeriod: subscription.quotaThisPeriod,
      autoRenew: subscription.autoRenew,
    };
  }

  async mockPayOrder(userId: string, orderId: string) {
    if (!this.wechatPayService.isMockMode()) {
      throw new BadRequestException('Mock payment is only available in WECHAT_PAY_MODE=mock');
    }

    const order = await this.prisma.subscriptionOrder.findUnique({
      where: { id: orderId },
      select: {
        id: true,
        userId: true,
      },
    });

    if (!order) {
      throw new NotFoundException('Subscription order not found');
    }

    if (order.userId !== userId) {
      throw new ForbiddenException('You do not have permission to pay this order');
    }

    return this.completeOrderPaymentById(orderId, `mock_${orderId}`);
  }

  async handleWechatNotification(
    headers: Record<string, string | string[] | undefined>,
    body: unknown,
    rawBody?: string,
  ) {
    const normalizedRawBody = rawBody || (Buffer.isBuffer(body)
      ? body.toString('utf8')
      : typeof body === 'string'
        ? body
        : JSON.stringify(body || {}));
    const parsedBody = Buffer.isBuffer(body) ? JSON.parse(normalizedRawBody) : body;
    const resource = await this.wechatPayService.parsePaidNotification(
      headers,
      normalizedRawBody,
      parsedBody,
    );

    const outTradeNo = resource.out_trade_no;
    if (!outTradeNo) {
      throw new BadRequestException('WeChat Pay notification missing out_trade_no');
    }

    if (resource.trade_state === 'SUCCESS') {
      await this.completeOrderPaymentByOutTradeNo(
        outTradeNo,
        resource.transaction_id,
        resource,
      );
    } else {
      await this.updateOrderStatusFromWechatState(outTradeNo, resource);
    }

    return {
      code: 'SUCCESS',
      message: '成功',
    };
  }

  private async completeOrderPaymentById(orderId: string, paymentId: string) {
    return this.prisma.$transaction(async (tx) => {
      const order = await tx.subscriptionOrder.findUnique({
        where: { id: orderId },
        include: {
          plan: true,
        },
      });

      if (!order) {
        throw new NotFoundException('Subscription order not found');
      }

      if (order.status === SubscriptionOrderStatus.paid) {
        const quota = await this.ensureUserQuota(tx, order.userId);
        const subscription = await this.findActiveSubscription(tx, order.userId);

        return {
          orderId: order.id,
          status: order.status,
          quotaRemaining: this.computeQuotaRemaining(quota, subscription),
          unlockedGameId: order.gameIdToUnlock || null,
        };
      }

      return this.activateOrder(tx, order, paymentId, { source: 'mock' });
    });
  }

  private async completeOrderPaymentByOutTradeNo(
    outTradeNo: string,
    paymentId?: string,
    paymentResponse?: unknown,
  ) {
    return this.prisma.$transaction(async (tx) => {
      const order = await tx.subscriptionOrder.findUnique({
        where: { outTradeNo },
        include: {
          plan: true,
        },
      });

      if (!order) {
        throw new NotFoundException('Subscription order not found');
      }

      if (order.status === SubscriptionOrderStatus.paid) {
        return order;
      }

      return this.activateOrder(tx, order, paymentId || order.paymentId || undefined, paymentResponse);
    });
  }

  private async activateOrder(
    tx: Prisma.TransactionClient,
    order: {
      id: string;
      userId: string;
      planId: string;
      gameIdToUnlock: string | null;
      plan: {
        id: string;
        quota: number;
        period: SubscriptionPeriod;
        name: string;
      };
    },
    paymentId?: string,
    paymentResponse?: unknown,
  ) {
    const now = new Date();
    await this.markExpiredSubscriptions(tx, order.userId);

    await tx.userSubscription.updateMany({
      where: {
        userId: order.userId,
        status: UserSubscriptionStatus.active,
      },
      data: {
        status: UserSubscriptionStatus.cancelled,
      },
    });

    let shouldUnlockGame = false;
    if (order.gameIdToUnlock) {
      const game = await tx.game.findUnique({
        where: { id: order.gameIdToUnlock },
        select: {
          id: true,
          authorId: true,
          canPlay: true,
        },
      });

      shouldUnlockGame = Boolean(
        game && game.authorId === order.userId && game.canPlay === false,
      );
    }

    const subscription = await tx.userSubscription.create({
      data: {
        userId: order.userId,
        planId: order.planId,
        status: UserSubscriptionStatus.active,
        startedAt: now,
        expiresAt: this.addSubscriptionPeriod(now, order.plan.period),
        usedThisPeriod: shouldUnlockGame ? 1 : 0,
        quotaThisPeriod: order.plan.quota,
        autoRenew: false,
      },
      include: {
        plan: true,
      },
    });

    await tx.user.update({
      where: { id: order.userId },
      data: {
        isPro: true,
        proExpires: subscription.expiresAt,
      },
    });

    if (shouldUnlockGame && order.gameIdToUnlock) {
      await tx.game.update({
        where: { id: order.gameIdToUnlock },
        data: {
          canPlay: true,
          requireSubscription: false,
          accessGrantSource: GameAccessGrantSource.subscription_unlock,
          accessGrantSubscriptionId: subscription.id,
        },
      });
    }

    await tx.subscriptionOrder.update({
      where: { id: order.id },
      data: {
        status: SubscriptionOrderStatus.paid,
        paymentId: paymentId || null,
        paidAt: now,
        paymentResponse: paymentResponse ? (paymentResponse as Prisma.InputJsonValue) : undefined,
      },
    });

    const quota = await this.ensureUserQuota(tx, order.userId);

    return {
      orderId: order.id,
      status: SubscriptionOrderStatus.paid,
      unlockedGameId: shouldUnlockGame ? order.gameIdToUnlock : null,
      quotaRemaining: this.computeQuotaRemaining(quota, subscription),
    };
  }

  private async updateOrderStatusFromWechatState(
    outTradeNo: string,
    paymentResponse: Record<string, unknown>,
  ) {
    const nextStatus = this.mapWechatTradeStateToOrderStatus(
      paymentResponse.trade_state,
    );

    if (!nextStatus || nextStatus === SubscriptionOrderStatus.pending) {
      return;
    }

    await this.prisma.subscriptionOrder.updateMany({
      where: {
        outTradeNo,
        status: SubscriptionOrderStatus.pending,
      },
      data: {
        status: nextStatus,
        paymentId: typeof paymentResponse.transaction_id === 'string'
          ? paymentResponse.transaction_id
          : undefined,
        paymentResponse: paymentResponse as Prisma.InputJsonValue,
      },
    });
  }

  private mapWechatTradeStateToOrderStatus(
    tradeState: unknown,
  ): SubscriptionOrderStatus | null {
    switch (tradeState) {
      case 'SUCCESS':
        return SubscriptionOrderStatus.paid;
      case 'REFUND':
        return SubscriptionOrderStatus.refunded;
      case 'CLOSED':
      case 'REVOKED':
        return SubscriptionOrderStatus.canceled;
      case 'PAYERROR':
        return SubscriptionOrderStatus.failed;
      case 'NOTPAY':
      case 'USERPAYING':
        return SubscriptionOrderStatus.pending;
      default:
        return null;
    }
  }

  private async expireStalePendingOrders(
    userId: string,
    planId: string,
    gameIdToUnlock: string | null,
    now: Date,
  ) {
    await this.prisma.subscriptionOrder.updateMany({
      where: {
        userId,
        planId,
        gameIdToUnlock,
        status: SubscriptionOrderStatus.pending,
        expiresAt: {
          lte: now,
        },
      },
      data: {
        status: SubscriptionOrderStatus.canceled,
      },
    });
  }

  private async findReusablePendingOrder(
    userId: string,
    planId: string,
    gameIdToUnlock: string | null,
    now: Date,
  ) {
    return this.prisma.subscriptionOrder.findFirst({
      where: {
        userId,
        planId,
        gameIdToUnlock,
        status: SubscriptionOrderStatus.pending,
        expiresAt: {
          gt: now,
        },
      },
      select: {
        id: true,
        prepayId: true,
      },
      orderBy: {
        createdAt: 'desc',
      },
    });
  }

  private async assertGameOwnership(userId: string, gameId: string) {
    const game = await this.prisma.game.findUnique({
      where: { id: gameId },
      select: {
        id: true,
        authorId: true,
      },
    });

    if (!game) {
      throw new NotFoundException('Game not found');
    }

    if (game.authorId !== userId) {
      throw new ForbiddenException('You do not have permission to unlock this game');
    }
  }

  private async ensurePlansSeeded() {
    await Promise.all(
      DEFAULT_PLANS.map((plan) => this.prisma.subscriptionPlan.upsert({
        where: { id: plan.id },
        update: {
          name: plan.name,
          description: plan.description,
          price: plan.price,
          currency: plan.currency,
          period: plan.period,
          quota: plan.quota,
          features: plan.features as unknown as Prisma.InputJsonValue,
          recommended: plan.recommended,
          badge: plan.badge,
          sortOrder: plan.sortOrder,
          active: true,
        },
        create: {
          ...plan,
          features: plan.features as unknown as Prisma.InputJsonValue,
          active: true,
        },
      })),
    );
  }

  private async ensureUserQuota(client: BillingDbClient, userId: string) {
    const totalFreeQuota = await this.resolveDefaultFreeQuota(client);
    return client.userQuota.upsert({
      where: { userId },
      update: {},
      create: {
        userId,
        totalFreeQuota,
        usedFreeQuota: 0,
      },
    });
  }

  private async resolveDefaultFreeQuota(client: BillingDbClient): Promise<number> {
    const envValue = Number.parseInt(
      this.configService.get<string>('BILLING_DEFAULT_FREE_QUOTA') || '',
      10,
    );
    if (Number.isFinite(envValue) && envValue >= 0) {
      return envValue;
    }

    const config = await client.systemConfig.findUnique({
      where: { configKey: 'billing.default_free_quota' },
      select: { configValue: true },
    });
    const configValue = Number.parseInt(config?.configValue || '', 10);

    if (Number.isFinite(configValue) && configValue >= 0) {
      return configValue;
    }

    return 5;
  }

  private async markExpiredSubscriptions(client: BillingDbClient, userId: string) {
    const now = new Date();
    await client.userSubscription.updateMany({
      where: {
        userId,
        status: UserSubscriptionStatus.active,
        expiresAt: { lte: now },
      },
      data: {
        status: UserSubscriptionStatus.expired,
      },
    });

    const activeCount = await client.userSubscription.count({
      where: {
        userId,
        status: UserSubscriptionStatus.active,
        expiresAt: { gt: now },
      },
    });

    if (activeCount === 0) {
      await client.user.update({
        where: { id: userId },
        data: {
          isPro: false,
          proExpires: null,
        },
      }).catch(() => undefined);
    }
  }

  private async findActiveSubscription(client: BillingDbClient, userId: string) {
    return client.userSubscription.findFirst({
      where: {
        userId,
        status: UserSubscriptionStatus.active,
        expiresAt: { gt: new Date() },
      },
      orderBy: {
        expiresAt: 'desc',
      },
      include: {
        plan: true,
      },
    });
  }

  private buildQuotaResponse(
    quota: {
      totalFreeQuota: number;
      usedFreeQuota: number;
    },
    subscription: {
      active?: boolean;
      planId?: string;
      expiresAt?: Date;
      usedThisPeriod?: number;
      quotaThisPeriod?: number;
      plan?: { name: string };
    } | null,
  ) {
    return {
      freeQuota: Math.max(quota.totalFreeQuota - quota.usedFreeQuota, 0),
      totalFreeQuota: quota.totalFreeQuota,
      subscription: {
        active: Boolean(subscription),
        planId: subscription?.planId || null,
        planName: subscription?.plan?.name || null,
        expiresAt: subscription?.expiresAt || null,
        usedThisPeriod: subscription?.usedThisPeriod || 0,
        quotaThisPeriod: subscription?.quotaThisPeriod || 0,
      },
    };
  }

  private computeQuotaRemaining(
    quota: { totalFreeQuota: number; usedFreeQuota: number },
    subscription?: { quotaThisPeriod: number; usedThisPeriod: number } | null,
  ) {
    const freeRemaining = Math.max(quota.totalFreeQuota - quota.usedFreeQuota, 0);
    const subscriptionRemaining = subscription
      ? Math.max(subscription.quotaThisPeriod - subscription.usedThisPeriod, 0)
      : 0;
    return freeRemaining + subscriptionRemaining;
  }

  private addSubscriptionPeriod(startedAt: Date, period: SubscriptionPeriod) {
    const expiresAt = new Date(startedAt);
    if (period === SubscriptionPeriod.yearly) {
      expiresAt.setUTCFullYear(expiresAt.getUTCFullYear() + 1);
      return expiresAt;
    }

    expiresAt.setUTCMonth(expiresAt.getUTCMonth() + 1);
    return expiresAt;
  }

  private resolveNotifyUrl() {
    const configured = this.configService.get<string>('WECHAT_PAY_NOTIFY_URL')
      || process.env.WECHAT_PAY_NOTIFY_URL;
    if (configured) {
      return configured;
    }

    const publicBase = (
      this.configService.get<string>('PUBLIC_API_BASE_URL')
      || process.env.PUBLIC_API_BASE_URL
      || ''
    ).replace(/\/$/, '');
    if (!publicBase) {
      const port = process.env.PORT || '3001';
      return `http://127.0.0.1:${port}/api/v1/subscription/wechat/notify`;
    }

    return `${publicBase}/api/v1/subscription/wechat/notify`;
  }

  private formatTimestamp(date: Date) {
    const year = date.getUTCFullYear();
    const month = `${date.getUTCMonth() + 1}`.padStart(2, '0');
    const day = `${date.getUTCDate()}`.padStart(2, '0');
    const hours = `${date.getUTCHours()}`.padStart(2, '0');
    const minutes = `${date.getUTCMinutes()}`.padStart(2, '0');
    const seconds = `${date.getUTCSeconds()}`.padStart(2, '0');
    return `${year}${month}${day}${hours}${minutes}${seconds}`;
  }
}
