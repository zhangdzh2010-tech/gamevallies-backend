import { ConfigService } from '@nestjs/config';
import {
  GameAccessGrantSource,
  SubscriptionOrderStatus,
  SubscriptionPeriod,
  UserSubscriptionStatus,
} from '@prisma/client';
import { BillingService } from '../src/billing/billing.service';

describe('BillingService', () => {
  let service: BillingService;
  let prisma: any;
  let configService: ConfigService;
  let wechatPayService: any;

  beforeEach(() => {
    prisma = {
      $transaction: jest.fn(),
      user: {
        findUnique: jest.fn(),
        update: jest.fn(),
      },
      game: {
        findUnique: jest.fn(),
        update: jest.fn(),
      },
      systemConfig: {
        findUnique: jest.fn(),
      },
      userQuota: {
        upsert: jest.fn(),
      },
      subscriptionPlan: {
        findFirst: jest.fn(),
        findMany: jest.fn(),
        upsert: jest.fn(),
      },
      userSubscription: {
        count: jest.fn(),
        findFirst: jest.fn(),
        updateMany: jest.fn(),
        create: jest.fn(),
      },
      subscriptionOrder: {
        create: jest.fn(),
        findFirst: jest.fn(),
        findUnique: jest.fn(),
        update: jest.fn(),
        updateMany: jest.fn(),
      },
    };

    prisma.$transaction.mockImplementation(async (callback: (tx: any) => any) => callback(prisma));

    configService = {
      get: jest.fn((key: string) => {
        const values: Record<string, string> = {
          BILLING_DEFAULT_FREE_QUOTA: '5',
          WECHAT_MINIAPP_APP_ID: 'wx-miniapp',
          WECHAT_PAY_NOTIFY_URL: 'https://example.com/api/v1/subscription/wechat/notify',
        };
        return values[key];
      }),
    } as unknown as ConfigService;

    wechatPayService = {
      isMockMode: jest.fn(() => true),
      buildPaymentFromPrepayId: jest.fn(),
      createPayment: jest.fn(),
      parsePaidNotification: jest.fn(),
    };

    service = new BillingService(prisma, configService, wechatPayService);
  });

  it('initializes user quota and reports inactive subscription status', async () => {
    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userSubscription.count.mockResolvedValue(0);
    prisma.user.update.mockResolvedValue(undefined);
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-1',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);

    const result = await service.getQuota('user-1');

    expect(result).toEqual({
      freeQuota: 4,
      totalFreeQuota: 5,
      subscription: {
        active: false,
        planId: null,
        planName: null,
        expiresAt: null,
        usedThisPeriod: 0,
        quotaThisPeriod: 0,
      },
    });
    expect(prisma.userQuota.upsert).toHaveBeenCalledWith({
      where: { userId: 'user-1' },
      update: {},
      create: {
        userId: 'user-1',
        totalFreeQuota: 5,
        usedFreeQuota: 0,
      },
    });
  });

  it('creates a pending subscription order with mock payment params', async () => {
    prisma.subscriptionPlan.upsert.mockResolvedValue(undefined);
    prisma.subscriptionOrder.updateMany.mockResolvedValue({ count: 0 });
    prisma.subscriptionOrder.findFirst.mockResolvedValue(null);
    prisma.user.findUnique.mockResolvedValue({
      id: 'user-1',
      wxOpenId: 'openid-1',
    });
    prisma.subscriptionPlan.findFirst.mockResolvedValue({
      id: 'plan_monthly_pro',
      name: '专业月卡',
      price: 1990,
      currency: 'CNY',
      period: SubscriptionPeriod.monthly,
      quota: 30,
    });
    wechatPayService.createPayment.mockResolvedValue({
      prepayId: 'mock_prepay',
      rawResponse: { mock: true },
      payment: {
        timeStamp: '1742534400',
        nonceStr: 'nonce',
        package: 'prepay_id=mock_prepay',
        signType: 'RSA',
        paySign: 'mock-sign',
      },
    });
    prisma.subscriptionOrder.create.mockResolvedValue(undefined);

    const result = await service.createOrder(
      'user-1',
      { planId: 'plan_monthly_pro' },
      '127.0.0.1',
    );

    expect(result).toEqual({
      orderId: expect.stringMatching(/^order_/),
      payment: {
        timeStamp: '1742534400',
        nonceStr: 'nonce',
        package: 'prepay_id=mock_prepay',
        signType: 'RSA',
        paySign: 'mock-sign',
      },
    });
    expect(prisma.subscriptionOrder.create).toHaveBeenCalledWith({
      data: expect.objectContaining({
        userId: 'user-1',
        planId: 'plan_monthly_pro',
        amount: 1990,
        status: SubscriptionOrderStatus.pending,
        prepayId: 'mock_prepay',
      }),
    });
  });

  it('reuses an unexpired pending order instead of creating duplicates', async () => {
    prisma.subscriptionPlan.upsert.mockResolvedValue(undefined);
    prisma.subscriptionOrder.updateMany.mockResolvedValue({ count: 0 });
    prisma.user.findUnique.mockResolvedValue({
      id: 'user-1',
      wxOpenId: 'openid-1',
    });
    prisma.subscriptionPlan.findFirst.mockResolvedValue({
      id: 'plan_monthly_basic',
      name: '基础月卡',
      price: 990,
      currency: 'CNY',
      period: SubscriptionPeriod.monthly,
      quota: 10,
    });
    prisma.subscriptionOrder.findFirst.mockResolvedValue({
      id: 'order_existing',
      prepayId: 'wx_prepay_existing',
    });
    wechatPayService.buildPaymentFromPrepayId.mockReturnValue({
      timeStamp: '1742534400',
      nonceStr: 'nonce-existing',
      package: 'prepay_id=wx_prepay_existing',
      signType: 'RSA',
      paySign: 'existing-sign',
    });

    const result = await service.createOrder(
      'user-1',
      { planId: 'plan_monthly_basic' },
      '127.0.0.1',
    );

    expect(wechatPayService.createPayment).not.toHaveBeenCalled();
    expect(prisma.subscriptionOrder.create).not.toHaveBeenCalled();
    expect(result).toEqual({
      orderId: 'order_existing',
      payment: {
        timeStamp: '1742534400',
        nonceStr: 'nonce-existing',
        package: 'prepay_id=wx_prepay_existing',
        signType: 'RSA',
        paySign: 'existing-sign',
      },
    });
  });

  it('mock payment activates subscription and unlocks the linked game', async () => {
    prisma.subscriptionOrder.findUnique
      .mockResolvedValueOnce({
        id: 'order_1',
        userId: 'user-1',
      })
      .mockResolvedValueOnce({
        id: 'order_1',
        userId: 'user-1',
        planId: 'plan_monthly_basic',
        gameIdToUnlock: 'game-1',
        status: SubscriptionOrderStatus.pending,
        plan: {
          id: 'plan_monthly_basic',
          name: '基础月卡',
          quota: 10,
          period: SubscriptionPeriod.monthly,
        },
      });

    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userSubscription.count.mockResolvedValue(0);
    prisma.game.findUnique.mockResolvedValue({
      id: 'game-1',
      authorId: 'user-1',
      canPlay: false,
    });
    prisma.userSubscription.create.mockResolvedValue({
      id: 'sub-1',
      userId: 'user-1',
      planId: 'plan_monthly_basic',
      status: UserSubscriptionStatus.active,
      quotaThisPeriod: 10,
      usedThisPeriod: 1,
      expiresAt: new Date('2026-04-21T00:00:00.000Z'),
      autoRenew: false,
      plan: {
        name: '基础月卡',
      },
    });
    prisma.user.update.mockResolvedValue(undefined);
    prisma.game.update.mockResolvedValue(undefined);
    prisma.subscriptionOrder.update.mockResolvedValue(undefined);
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-1',
      totalFreeQuota: 5,
      usedFreeQuota: 0,
    });

    const result = await service.mockPayOrder('user-1', 'order_1');

    expect(prisma.game.update).toHaveBeenCalledWith({
      where: { id: 'game-1' },
      data: {
        canPlay: true,
        requireSubscription: false,
        accessGrantSource: GameAccessGrantSource.subscription_unlock,
        accessGrantSubscriptionId: 'sub-1',
      },
    });
    expect(prisma.subscriptionOrder.update).toHaveBeenCalledWith({
      where: { id: 'order_1' },
      data: expect.objectContaining({
        status: SubscriptionOrderStatus.paid,
        paymentId: 'mock_order_1',
        paidAt: expect.any(Date),
      }),
    });
    expect(result).toEqual({
      orderId: 'order_1',
      status: SubscriptionOrderStatus.paid,
      unlockedGameId: 'game-1',
      quotaRemaining: 14,
    });
  });

  it('returns order status and auto-cancels expired pending orders', async () => {
    prisma.userSubscription.updateMany.mockResolvedValue({ count: 0 });
    prisma.userSubscription.count.mockResolvedValue(0);
    prisma.user.update.mockResolvedValue(undefined);
    prisma.subscriptionOrder.findUnique.mockResolvedValue({
      id: 'order_expired',
      userId: 'user-1',
      planId: 'plan_monthly_basic',
      amount: 990,
      currency: 'CNY',
      status: SubscriptionOrderStatus.pending,
      paidAt: null,
      expiresAt: new Date(Date.now() - 60_000),
      gameIdToUnlock: null,
      plan: {
        name: '基础月卡',
      },
    });
    prisma.subscriptionOrder.update.mockResolvedValue({
      id: 'order_expired',
      userId: 'user-1',
      planId: 'plan_monthly_basic',
      amount: 990,
      currency: 'CNY',
      status: SubscriptionOrderStatus.canceled,
      paidAt: null,
      expiresAt: new Date(Date.now() - 60_000),
      gameIdToUnlock: null,
      plan: {
        name: '基础月卡',
      },
    });
    prisma.userQuota.upsert.mockResolvedValue({
      userId: 'user-1',
      totalFreeQuota: 5,
      usedFreeQuota: 1,
    });
    prisma.userSubscription.findFirst.mockResolvedValue(null);

    const result = await service.getOrderStatus('user-1', 'order_expired');

    expect(prisma.subscriptionOrder.update).toHaveBeenCalledWith({
      where: { id: 'order_expired' },
      data: {
        status: SubscriptionOrderStatus.canceled,
      },
      include: {
        plan: true,
      },
    });
    expect(result).toEqual({
      orderId: 'order_expired',
      status: SubscriptionOrderStatus.canceled,
      planId: 'plan_monthly_basic',
      planName: '基础月卡',
      amount: 990,
      currency: 'CNY',
      gameIdToUnlock: null,
      paidAt: null,
      expiresAt: expect.any(Date),
      quotaRemaining: 4,
      subscriptionActive: false,
    });
  });

  it('marks closed WeChat orders as canceled on notification', async () => {
    wechatPayService.parsePaidNotification.mockResolvedValue({
      out_trade_no: 'gv202603210001',
      trade_state: 'CLOSED',
      transaction_id: 'wx_txn_closed',
    });
    prisma.subscriptionOrder.updateMany.mockResolvedValue({ count: 1 });

    const result = await service.handleWechatNotification(
      {},
      {
        resource: {
          ciphertext: 'ignored',
        },
      },
      JSON.stringify({ resource: { ciphertext: 'ignored' } }),
    );

    expect(prisma.subscriptionOrder.updateMany).toHaveBeenCalledWith({
      where: {
        outTradeNo: 'gv202603210001',
        status: SubscriptionOrderStatus.pending,
      },
      data: {
        status: SubscriptionOrderStatus.canceled,
        paymentId: 'wx_txn_closed',
        paymentResponse: {
          out_trade_no: 'gv202603210001',
          trade_state: 'CLOSED',
          transaction_id: 'wx_txn_closed',
        },
      },
    });
    expect(result).toEqual({
      code: 'SUCCESS',
      message: '成功',
    });
  });
});
