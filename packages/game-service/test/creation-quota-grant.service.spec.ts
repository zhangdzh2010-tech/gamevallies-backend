import { CreationQuotaGrantService } from '../src/admin/creation-quota-grant.service';
import { AdminController } from '../src/admin/admin.controller';
import { computeQuotaRemaining } from '../src/game/game-access.policy';

describe('administrator creation quota grants', () => {
  const request = { requestId: '11111111-1111-4111-8111-111111111111', userId: 'user-1', amount: 100, reason: '种子作品生成' };
  let db: any;
  let balance: any;
  let service: CreationQuotaGrantService;
  const env = process.env;
  beforeEach(() => {
    process.env = { ...env, ADMIN_USERNAME: 'quota-operator', ADMIN_TOKEN: 'quota-test-token' };
    delete process.env.BILLING_DEFAULT_FREE_QUOTA;
    balance = { userId: 'user-1', totalFreeQuota: 35, usedFreeQuota: 35 };
    const history = new Map();
    db = {
      $queryRaw: jest.fn().mockResolvedValue([{ id: 'user-1' }]),
      user: { findUnique: jest.fn().mockResolvedValue({ id: 'user-1', username: 'tester' }), update: jest.fn() },
      systemConfig: { findUnique: jest.fn().mockResolvedValue({ configValue: '5' }) },
      userSubscription: { findFirst: jest.fn().mockResolvedValue({ quotaThisPeriod: 30, usedThisPeriod: 30 }), update: jest.fn(), create: jest.fn() },
      userQuota: {
        upsert: jest.fn().mockImplementation(({ create }) => { balance ||= create; return { ...balance }; }),
        findUnique: jest.fn().mockImplementation(() => balance && { ...balance }),
        findUniqueOrThrow: jest.fn().mockImplementation(() => ({ ...balance })),
        update: jest.fn().mockImplementation(({ data }) => { balance.totalFreeQuota += data.totalFreeQuota.increment; return { ...balance }; }),
      },
      adminCreationQuotaGrant: {
        findUnique: jest.fn().mockImplementation(({ where }) => history.get(where.id) || null),
        findMany: jest.fn().mockImplementation(() => [...history.values()]),
        create: jest.fn().mockImplementation(({ data }) => { history.set(data.id, data); return data; }),
      },
    };
    db.$transaction = jest.fn((fn: any) => fn(db));
    service = new CreationQuotaGrantService(db);
  });
  afterEach(() => { process.env = env; });

  it('adds usable quota to an exhausted account and preserves usage and membership', async () => {
    const result = await service.grant(request);
    expect(result).toMatchObject({ amount: 100, totalBefore: 35, totalAfter: 135, usedAtGrant: 35, operator: 'quota-operator' });
    expect(computeQuotaRemaining(balance, { quotaThisPeriod: 30, usedThisPeriod: 30 })).toBe(100);
    expect(balance.usedFreeQuota).toBe(35);
    expect(db.user.update).not.toHaveBeenCalled();
    expect(db.userSubscription.update).not.toHaveBeenCalled();
    expect(db.userSubscription.create).not.toHaveBeenCalled();
    expect((await service.options('user-1')).current).toMatchObject({ available: 100, remaining: 100, subscriptionRemaining: 0 });
  });
  it('deduplicates an acknowledged or uncertain request and allows a deliberate additional grant', async () => {
    const first = await service.grant(request);
    expect(await service.grant({ ...request, reason: ` ${request.reason} ` })).toEqual(first);
    expect(db.userQuota.update).toHaveBeenCalledTimes(1);
    await service.grant({ ...request, requestId: '22222222-2222-4222-8222-222222222222', amount: 50 });
    expect(balance).toMatchObject({ totalFreeQuota: 185, usedFreeQuota: 35 });
  });
  it.each([{ userId: 'another' }, { amount: 101 }, { reason: '另一次授予' }])('rejects reuse of an ID with changed input %j', async change => {
    await service.grant(request);
    await expect(service.grant({ ...request, ...change })).rejects.toThrow('另一项授予');
    expect(db.userQuota.update).toHaveBeenCalledTimes(1);
  });
  it.each([0, -1, 0.5, 10001, NaN, Infinity, '100', null])('rejects an invalid amount %p', async amount => {
    await expect(service.grant({ ...request, amount } as any)).rejects.toThrow('整数次数');
    expect(db.$transaction).not.toHaveBeenCalled();
  });
  it.each([{ requestId: '------------------------------------' }, { reason: ' ' }, { reason: 'a'.repeat(256) }, { userId: '' }])('rejects malformed audit input %j', async change => {
    await expect(service.grant({ ...request, ...change })).rejects.toThrow('授予原因');
    expect(db.$transaction).not.toHaveBeenCalled();
  });
  it('rejects missing users before initializing or increasing a balance', async () => {
    db.user.findUnique.mockResolvedValue(null);
    await expect(service.grant(request)).rejects.toThrow('用户不存在');
    await expect(service.options('missing')).rejects.toThrow('用户不存在');
    expect(db.userQuota.upsert).not.toHaveBeenCalled();
  });
  it('initializes a new account with its configured default before appending the grant', async () => {
    balance = null;
    db.systemConfig.findUnique.mockResolvedValue({ configValue: '7' });
    expect((await service.options('user-1')).current?.remaining).toBe(7);
    const result = await service.grant(request);
    expect(result).toMatchObject({ totalBefore: 7, totalAfter: 107, usedAtGrant: 0 });
  });
  it('honors the existing environment override including zero', async () => {
    balance = null;
    process.env.BILLING_DEFAULT_FREE_QUOTA = '0';
    expect((await service.grant(request)).totalBefore).toBe(0);
  });
  it('blocks balance overflow before mutating or writing an audit', async () => {
    balance.totalFreeQuota = 2147483647;
    await expect(service.grant(request)).rejects.toThrow('累计额度超过上限');
    expect(db.userQuota.update).not.toHaveBeenCalled();
    expect(db.adminCreationQuotaGrant.create).not.toHaveBeenCalled();
  });
  it('propagates audit failure to the transaction and only retries known conflicts', async () => {
    db.adminCreationQuotaGrant.create.mockRejectedValue(new Error('audit unavailable'));
    await expect(service.grant(request)).rejects.toThrow('audit unavailable');
    expect(db.$transaction).toHaveBeenCalledTimes(1);
  });
  it('retries a deadlock but bounds conflict retries', async () => {
    db.$transaction.mockRejectedValueOnce({ code: 'P2034' });
    expect((await service.grant(request)).amount).toBe(100);
    db.$transaction.mockRejectedValue({ code: 'P2034' });
    await expect(service.grant(request)).rejects.toThrow('同一请求编号重试');
    expect(db.$transaction).toHaveBeenCalledTimes(5);
  });
  it('requires admin authentication on both reads and writes', async () => {
    const controller = new AdminController({} as any, {} as any, service);
    await expect(controller.grantCreationQuota('invalid', request)).rejects.toThrow('Unauthorized');
    await expect(controller.quotaGrantOptions('', 'user-1')).rejects.toThrow('Unauthorized');
    expect(db.$transaction).not.toHaveBeenCalled();
    const result = await controller.grantCreationQuota('quota-test-token', request);
    expect(result.data.amount).toBe(100);
  });
});
