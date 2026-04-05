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
import { AlipayPayService, AlipayTradeFlow } from './alipay-pay.service';
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

const PLAN_BOOTSTRAP_MARKER_KEY = 'billing.subscription_plans_bootstrapped_at';

type BillingDbClient = PrismaService | Prisma.TransactionClient;

type PaymentProvider = 'wechat_pay' | 'alipay_wap' | 'alipay_page';

type CreateOrderOptions = {
  provider?: PaymentProvider;
  clientPlatform?: 'weapp' | 'h5' | 'wechat_h5';
  wechatPayFlow?: 'jsapi' | 'mweb' | 'native';
  returnUrl?: string;
  authContext?: {
    wechatPlatform?: 'miniapp' | 'h5';
    wechatOpenId?: string;
    wechatAppId?: string;
  };
};

type WechatPaymentRoutingDecision = {
  provider: 'wechat_pay';
  tradeType: 'jsapi' | 'h5';
  appId: string;
  openId?: string;
  clientPlatform: 'weapp' | 'h5' | 'wechat_h5';
  wechatPayFlow: 'jsapi' | 'mweb' | 'native';
  returnUrl?: string;
};

type AlipayPaymentRoutingDecision = {
  provider: 'alipay_wap' | 'alipay_page';
  flow: AlipayTradeFlow;
  returnUrl?: string;
};

type PaymentRoutingDecision = WechatPaymentRoutingDecision | AlipayPaymentRoutingDecision;

@Injectable()
export class BillingService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly configService: ConfigService,
    private readonly wechatPayService: WechatPayService,
    private readonly alipayPayService: AlipayPayService,
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
    options: CreateOrderOptions = {},
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

    if (dto.gameId) {
      await this.assertGameOwnership(userId, dto.gameId);
    }

    const now = new Date();
    const paymentDecision = this.resolvePaymentRouting(user, options);
    const orderId = `order_${this.formatTimestamp(now)}_${randomBytes(4).toString('hex')}`;
    const initialOutTradeNo = `gv${this.formatTimestamp(now)}${randomBytes(5).toString('hex')}`.slice(0, 32);
    const notifyUrl = this.resolveNotifyUrl(paymentDecision.provider);
    await this.expireStalePendingOrders(
      userId,
      plan.id,
      dto.gameId || null,
      paymentDecision.provider,
      now,
    );

    const reusableOrder = await this.findReusablePendingOrder(
      userId,
      plan.id,
      dto.gameId || null,
      paymentDecision.provider,
      now,
    );
    const outTradeNo = paymentDecision.provider !== 'wechat_pay' && reusableOrder?.outTradeNo
      ? reusableOrder.outTradeNo
      : initialOutTradeNo;
    if (paymentDecision.provider === 'wechat_pay'
      && paymentDecision.tradeType === 'jsapi'
      && reusableOrder?.prepayId) {
      return {
        orderId: reusableOrder.id,
        payment: this.wechatPayService.buildPaymentFromPrepayId(
          paymentDecision.appId,
          reusableOrder.prepayId,
        ),
      };
    }

    const reusableH5Url = this.extractReusableH5Url(reusableOrder?.paymentResponse);
    if (paymentDecision.provider === 'wechat_pay'
      && paymentDecision.tradeType === 'h5'
      && reusableH5Url
      && reusableOrder) {
      return {
        orderId: reusableOrder.id,
        payment: {
          mwebUrl: reusableH5Url,
        },
      };
    }

    const paymentResult = paymentDecision.provider === 'wechat_pay'
      ? await this.wechatPayService.createPayment({
        appId: paymentDecision.appId,
        openId: paymentDecision.openId,
        description: `GameVallies ${plan.name}`,
        outTradeNo,
        amount: plan.price,
        notifyUrl,
        clientIp,
        tradeType: paymentDecision.tradeType,
        h5Info: paymentDecision.tradeType === 'h5'
          ? {
              type: 'Wap',
              appName: 'GameVallies',
              appUrl: this.resolveWebBaseUrl(),
            }
          : undefined,
      })
      : await this.alipayPayService.createPayment({
        flow: paymentDecision.flow,
        description: `GameVallies ${plan.name}`,
        outTradeNo,
        amount: plan.price,
        notifyUrl,
        returnUrl: paymentDecision.returnUrl,
      });

    if (paymentDecision.provider !== 'wechat_pay' && reusableOrder) {
      await this.prisma.subscriptionOrder.update({
        where: { id: reusableOrder.id },
        data: {
          paymentPayload: this.buildPaymentPayload(paymentDecision, notifyUrl, clientIp),
          paymentResponse: paymentResult.rawResponse as unknown as Prisma.InputJsonValue,
          expiresAt: new Date(now.getTime() + 2 * 60 * 60 * 1000),
        },
      });

      return {
        orderId: reusableOrder.id,
        payment: paymentResult.payment,
      };
    }

    await this.prisma.subscriptionOrder.create({
      data: {
        id: orderId,
        userId,
        planId: plan.id,
        amount: plan.price,
        currency: plan.currency,
        status: SubscriptionOrderStatus.pending,
        provider: paymentDecision.provider,
        outTradeNo,
        prepayId: 'prepayId' in paymentResult ? paymentResult.prepayId : null,
        gameIdToUnlock: dto.gameId || null,
        description: `GameVallies ${plan.name}`,
        paymentPayload: this.buildPaymentPayload(paymentDecision, notifyUrl, clientIp),
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
      provider: effectiveOrder.provider,
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
    const order = await this.prisma.subscriptionOrder.findUnique({
      where: { id: orderId },
      select: {
        id: true,
        userId: true,
        provider: true,
      },
    });

    if (!order) {
      throw new NotFoundException('Subscription order not found');
    }

    if (order.userId !== userId) {
      throw new ForbiddenException('You do not have permission to pay this order');
    }

    const mockEnabled = order.provider === 'wechat_pay'
      ? this.wechatPayService.isMockMode()
      : this.alipayPayService.isMockMode();
    if (!mockEnabled) {
      throw new BadRequestException('Mock payment is only available in mock payment mode');
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

  async handleAlipayNotification(
    payload: Record<string, string | string[] | undefined>,
  ) {
    const notification = await this.alipayPayService.parsePaidNotification(payload);
    const outTradeNo = notification.out_trade_no;
    if (!outTradeNo) {
      throw new BadRequestException('Alipay notification missing out_trade_no');
    }

    const order = await this.prisma.subscriptionOrder.findUnique({
      where: { outTradeNo },
      include: {
        plan: true,
      },
    });

    if (!order) {
      throw new NotFoundException('Subscription order not found');
    }

    this.assertAlipayNotificationMatchesOrder(order, notification);

    if (notification.trade_status === 'TRADE_SUCCESS' || notification.trade_status === 'TRADE_FINISHED') {
      await this.completeOrderPaymentByOutTradeNo(
        outTradeNo,
        notification.trade_no,
        notification,
      );
    } else {
      await this.updateOrderStatusFromAlipayState(outTradeNo, notification);
    }

    return 'success';
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

  private async updateOrderStatusFromAlipayState(
    outTradeNo: string,
    paymentResponse: Record<string, string>,
  ) {
    const nextStatus = this.mapAlipayTradeStateToOrderStatus(
      paymentResponse.trade_status,
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
        paymentId: paymentResponse.trade_no || undefined,
        paymentResponse: paymentResponse as unknown as Prisma.InputJsonValue,
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

  private mapAlipayTradeStateToOrderStatus(
    tradeState: unknown,
  ): SubscriptionOrderStatus | null {
    switch (tradeState) {
      case 'TRADE_SUCCESS':
      case 'TRADE_FINISHED':
        return SubscriptionOrderStatus.paid;
      case 'TRADE_CLOSED':
        return SubscriptionOrderStatus.canceled;
      case 'WAIT_BUYER_PAY':
        return SubscriptionOrderStatus.pending;
      default:
        return null;
    }
  }

  private async expireStalePendingOrders(
    userId: string,
    planId: string,
    gameIdToUnlock: string | null,
    provider: PaymentProvider,
    now: Date,
  ) {
    await this.prisma.subscriptionOrder.updateMany({
      where: {
        userId,
        planId,
        gameIdToUnlock,
        provider,
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
    provider: PaymentProvider,
    now: Date,
  ) {
    return this.prisma.subscriptionOrder.findFirst({
      where: {
        userId,
        planId,
        gameIdToUnlock,
        provider,
        status: SubscriptionOrderStatus.pending,
        expiresAt: {
          gt: now,
        },
      },
      select: {
        id: true,
        outTradeNo: true,
        prepayId: true,
        paymentResponse: true,
      },
      orderBy: {
        createdAt: 'desc',
      },
    });
  }

  private assertAlipayNotificationMatchesOrder(
    order: {
      amount: number;
      provider: string;
    },
    notification: Record<string, string>,
  ) {
    if (!order.provider.startsWith('alipay')) {
      throw new BadRequestException('Order provider does not match Alipay notification');
    }

    const isSuccessfulPayment = notification.trade_status === 'TRADE_SUCCESS'
      || notification.trade_status === 'TRADE_FINISHED';
    const appId = notification.app_id;
    if (isSuccessfulPayment && !appId) {
      throw new BadRequestException('Alipay notification missing app_id');
    }
    const expectedAppId = this.configService.get<string>('ALIPAY_APP_ID');
    if (expectedAppId && appId && appId !== expectedAppId) {
      throw new BadRequestException('Alipay notification app_id mismatch');
    }

    const totalAmount = notification.total_amount;
    if (isSuccessfulPayment && !totalAmount) {
      throw new BadRequestException('Alipay notification missing total_amount');
    }
    if (totalAmount && this.convertYuanToCents(totalAmount) !== order.amount) {
      throw new BadRequestException('Alipay notification total_amount mismatch');
    }

    const sellerId = notification.seller_id;
    const expectedSellerId = this.configService.get<string>('ALIPAY_SELLER_ID');
    if (expectedSellerId && isSuccessfulPayment && !sellerId) {
      throw new BadRequestException('Alipay notification missing seller_id');
    }
    if (expectedSellerId && sellerId && sellerId !== expectedSellerId) {
      throw new BadRequestException('Alipay notification seller_id mismatch');
    }
  }

  private convertYuanToCents(amount: string): number {
    if (!/^\d+(?:\.\d{1,2})?$/.test(amount.trim())) {
      throw new BadRequestException('Invalid Alipay total_amount');
    }

    return Math.round(Number.parseFloat(amount) * 100);
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
    const marker = await this.prisma.systemConfig.findUnique({
      where: { configKey: PLAN_BOOTSTRAP_MARKER_KEY },
      select: { id: true },
    });
    if (marker) {
      return;
    }

    await this.prisma.$transaction(async (tx) => {
      const existingMarker = await tx.systemConfig.findUnique({
        where: { configKey: PLAN_BOOTSTRAP_MARKER_KEY },
        select: { id: true },
      });
      if (existingMarker) {
        return;
      }

      const existingPlanCount = await tx.subscriptionPlan.count();
      if (existingPlanCount === 0) {
        await tx.subscriptionPlan.createMany({
          data: DEFAULT_PLANS.map((plan) => ({
            ...plan,
            features: plan.features as unknown as Prisma.InputJsonValue,
            active: true,
          })),
          skipDuplicates: true,
        });
      }

      await tx.systemConfig.upsert({
        where: { configKey: PLAN_BOOTSTRAP_MARKER_KEY },
        update: {
          configValue: new Date().toISOString(),
          description: 'Subscription plans bootstrap marker',
          category: 'billing',
        },
        create: {
          configKey: PLAN_BOOTSTRAP_MARKER_KEY,
          configValue: new Date().toISOString(),
          description: 'Subscription plans bootstrap marker',
          category: 'billing',
        },
      });
    });
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

  private resolvePaymentRouting(
    user: { wxOpenId: string | null },
    options: CreateOrderOptions,
  ): PaymentRoutingDecision {
    const provider = options.provider || 'wechat_pay';
    if (provider === 'alipay_wap' || provider === 'alipay_page') {
      return {
        provider,
        flow: provider === 'alipay_page' ? 'page' : 'wap',
        returnUrl: options.returnUrl,
      };
    }

    const clientPlatform = options.clientPlatform || 'weapp';
    const wechatPayFlow = options.wechatPayFlow || (clientPlatform === 'h5' ? 'mweb' : 'jsapi');

    if (clientPlatform === 'h5') {
      return {
        provider: 'wechat_pay',
        tradeType: 'h5',
        appId: this.configService.get<string>('WECHAT_H5_APP_ID') || '',
        clientPlatform,
        wechatPayFlow,
        returnUrl: options.returnUrl,
      };
    }

    if (clientPlatform === 'wechat_h5') {
      if (wechatPayFlow !== 'jsapi') {
        throw new BadRequestException('微信内 H5 支付必须使用 JSAPI，请先完成微信授权登录后再发起支付');
      }

      const h5OpenId = options.authContext?.wechatPlatform === 'h5'
        ? options.authContext?.wechatOpenId
        : undefined;
      const h5AppId = this.configService.get<string>('WECHAT_H5_APP_ID') || '';

      if (!h5AppId || !h5OpenId) {
        throw new BadRequestException('当前微信内 H5 支付需要先完成微信授权登录');
      }

      return {
        provider: 'wechat_pay',
        tradeType: 'jsapi',
        appId: h5AppId,
        openId: h5OpenId,
        clientPlatform,
        wechatPayFlow,
        returnUrl: options.returnUrl,
      };
    }

    const miniappAppId = this.configService.get<string>('WECHAT_MINIAPP_APP_ID') || '';
    const miniappOpenId = options.authContext?.wechatPlatform === 'miniapp'
      ? options.authContext?.wechatOpenId
      : user.wxOpenId || undefined;

    if (!this.wechatPayService.isMockMode() && !miniappOpenId) {
      throw new BadRequestException('当前账号未绑定微信小程序 OpenID，无法调起微信支付');
    }

    return {
      provider: 'wechat_pay',
      tradeType: 'jsapi',
      appId: miniappAppId,
      openId: miniappOpenId,
      clientPlatform: 'weapp',
      wechatPayFlow,
      returnUrl: options.returnUrl,
    };
  }

  private extractReusableH5Url(rawResponse: Prisma.JsonValue | null | undefined): string | null {
    if (!rawResponse || typeof rawResponse !== 'object' || Array.isArray(rawResponse)) {
      return null;
    }

    const response = rawResponse as Record<string, unknown>;
    const h5Url = response.h5_url;
    return typeof h5Url === 'string' && h5Url.trim() ? h5Url.trim() : null;
  }

  private buildPaymentPayload(
    paymentDecision: PaymentRoutingDecision,
    notifyUrl: string,
    clientIp: string,
  ): Prisma.InputJsonValue {
    if (paymentDecision.provider !== 'wechat_pay') {
      return {
        provider: paymentDecision.provider,
        flow: paymentDecision.flow,
        notifyUrl,
        returnUrl: paymentDecision.returnUrl || null,
      } as unknown as Prisma.InputJsonValue;
    }

    return {
      provider: paymentDecision.provider,
      appId: paymentDecision.appId,
      notifyUrl,
      clientIp,
      clientPlatform: paymentDecision.clientPlatform,
      wechatPayFlow: paymentDecision.wechatPayFlow,
      tradeType: paymentDecision.tradeType,
      returnUrl: paymentDecision.returnUrl || null,
    } as unknown as Prisma.InputJsonValue;
  }

  private resolveWebBaseUrl() {
    return (
      this.configService.get<string>('PUBLIC_WEB_BASE_URL')
      || process.env.PUBLIC_WEB_BASE_URL
      || 'https://gamevallies.com'
    ).replace(/\/$/, '');
  }

  private resolveNotifyUrl(provider: PaymentProvider) {
    const configured = provider === 'wechat_pay'
      ? this.configService.get<string>('WECHAT_PAY_NOTIFY_URL')
        || process.env.WECHAT_PAY_NOTIFY_URL
      : this.configService.get<string>('ALIPAY_NOTIFY_URL')
        || process.env.ALIPAY_NOTIFY_URL;
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
      const path = provider === 'wechat_pay'
        ? '/api/v1/subscription/wechat/notify'
        : '/api/v1/subscription/alipay/notify';
      return `http://127.0.0.1:${port}${path}`;
    }

    const path = provider === 'wechat_pay'
      ? '/api/v1/subscription/wechat/notify'
      : '/api/v1/subscription/alipay/notify';
    return `${publicBase}${path}`;
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
