import { SubscriptionGrantService } from '../src/admin/subscription-grant.service';

describe('administrator subscription grants', () => {
  const request = { requestId: '11111111-1111-4111-8111-111111111111', userId: 'user-1', planId: 'monthly', reason: '种子测试' };
  let tx: any;
  let service: SubscriptionGrantService;
  beforeEach(() => {
    tx = {
      $queryRaw: jest.fn().mockResolvedValue([{ id: 'user-1' }]),
      adminSubscriptionGrant: { findUnique: jest.fn().mockResolvedValue(null), create: jest.fn().mockImplementation(({ data }) => data) },
      user: { findUnique: jest.fn().mockResolvedValue({ id: 'user-1', username: 'tester' }), update: jest.fn() },
      subscriptionPlan: { findUnique: jest.fn().mockResolvedValue({ id: 'monthly', name: '测试月套餐', active: true, quota: 100, period: 'monthly' }) },
      userSubscription: { findFirst: jest.fn().mockResolvedValue(null), updateMany: jest.fn(), create: jest.fn().mockResolvedValue({ id: 'subscription-1' }) },
    };
    service = new SubscriptionGrantService({ $transaction: (fn: any) => fn(tx) } as any);
  });
  afterEach(() => jest.useRealTimers());
  it('grants real quota and membership, without a paid order', async () => {
    const result = await service.grant(request);
    expect(tx.userSubscription.create).toHaveBeenCalledWith({ data: expect.objectContaining({ quotaThisPeriod: 100, usedThisPeriod: 0, autoRenew: false }) });
    expect(tx.user.update).toHaveBeenCalledWith({ where: { id: 'user-1' }, data: expect.objectContaining({ isPro: true }) });
    expect(result).toMatchObject({ reason: '种子测试', subscriptionId: 'subscription-1', quota: 100 });
  });
  it('returns the previous result for a retry without issuing more quota', async () => {
    tx.adminSubscriptionGrant.findUnique.mockResolvedValue({ ...request, id: request.requestId });
    await service.grant(request);
    expect(tx.userSubscription.create).not.toHaveBeenCalled();
  });
  it('rejects reuse of a request ID for another user', async () => {
    tx.adminSubscriptionGrant.findUnique.mockResolvedValue({ ...request, userId: 'another' });
    await expect(service.grant(request)).rejects.toThrow('另一项授予');
  });
  it('preserves an existing active subscription even if its quota is exhausted', async () => {
    tx.userSubscription.findFirst.mockResolvedValue({ quotaThisPeriod: 100, usedThisPeriod: 100 });
    await expect(service.grant(request)).rejects.toThrow('已有有效套餐');
    expect(tx.userSubscription.create).not.toHaveBeenCalled();
    expect(tx.user.update).not.toHaveBeenCalled();
  });
  it('rejects inactive plans and missing users', async () => {
    tx.subscriptionPlan.findUnique.mockResolvedValue({ active: false });
    await expect(service.grant(request)).rejects.toThrow('停用');
    tx.user.findUnique.mockResolvedValue(null);
    await expect(service.grant(request)).rejects.toThrow('用户不存在');
  });
  it('requires an audit reason', async () => {
    await expect(service.grant({ ...request, reason: ' ' })).rejects.toThrow('授予原因');
    expect(tx.$queryRaw).not.toHaveBeenCalled();
  });
  it('clamps month-end expiry to the last day of February', async () => {
    jest.useFakeTimers().setSystemTime(new Date('2028-01-31T12:30:00Z'));
    const result = await service.grant(request);
    expect(result.expiresAt.toISOString()).toBe('2028-02-29T12:30:00.000Z');
  });
  it('rolls back subscription changes if the audit write fails', async () => {
    tx.adminSubscriptionGrant.create.mockRejectedValue(new Error('audit write failed'));
    await expect(service.grant(request)).rejects.toThrow('audit write failed');
    // The rejected callback is returned to Prisma's transaction, never swallowed.
  });
});
