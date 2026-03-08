/**
 * 真实数据库集成测试
 * 使用 MySQL 中的种子数据进行全量验证
 * 运行前需确保已执行: npx ts-node --project tsconfig.base.json prisma/seed.ts
 */
import { PrismaClient } from '@prisma/client';
import * as bcrypt from 'bcryptjs';

const prisma = new PrismaClient({
  datasources: { db: { url: process.env.DATABASE_URL } },
});

beforeAll(async () => {
  await prisma.$connect();
});

afterAll(async () => {
  await prisma.$disconnect();
});

// ============================================================
// 数据库基础数据验证
// ============================================================
describe('数据库种子数据验证', () => {
  test('应存在 5 个用户', async () => {
    const count = await prisma.user.count();
    expect(count).toBeGreaterThanOrEqual(5);
  });

  test('应存在 6 个已发布游戏', async () => {
    const count = await prisma.game.count({ where: { status: 'published' } });
    expect(count).toBeGreaterThanOrEqual(6);
  });

  test('应存在 6 个游戏代码包 (GameBundle)', async () => {
    const count = await prisma.gameBundle.count();
    expect(count).toBeGreaterThanOrEqual(6);
  });

  test('应存在评论、通知、社交互动记录', async () => {
    const [comments, notifications, interactions] = await Promise.all([
      prisma.comment.count(),
      prisma.notification.count(),
      prisma.socialInteraction.count(),
    ]);
    expect(comments).toBeGreaterThanOrEqual(1);
    expect(notifications).toBeGreaterThanOrEqual(1);
    expect(interactions).toBeGreaterThanOrEqual(1);
  });
});

// ============================================================
// 用户账号验证
// ============================================================
describe('用户数据验证', () => {
  test('alice 是 creator 角色，有正确的数据', async () => {
    const user = await prisma.user.findUnique({ where: { username: 'alice' } });
    expect(user).not.toBeNull();
    expect(user!.role).toBe('creator');
    expect(user!.email).toBe('alice@gamevallies.com');
    expect(user!.followerCount).toBeGreaterThan(0);
    expect(user!.gameCount).toBeGreaterThan(0);
  });

  test('alice 的密码 password123 验证通过', async () => {
    const user = await prisma.user.findUnique({ where: { username: 'alice' } });
    expect(user!.passwordHash).not.toBeNull();
    const valid = await bcrypt.compare('password123', user!.passwordHash!);
    expect(valid).toBe(true);
  });

  test('错误密码验证失败', async () => {
    const user = await prisma.user.findUnique({ where: { username: 'alice' } });
    const invalid = await bcrypt.compare('wrongpassword', user!.passwordHash!);
    expect(invalid).toBe(false);
  });

  test('bob 是普通 user 角色', async () => {
    const user = await prisma.user.findUnique({ where: { username: 'bobgamer' } });
    expect(user!.role).toBe('user');
  });

  test('通过 email 或 username 均可查找用户 (login 场景)', async () => {
    const account = 'alice@gamevallies.com';
    const user = await prisma.user.findFirst({
      where: {
        OR: [{ email: account }, { username: account }],
      },
    });
    expect(user).not.toBeNull();
    expect(user!.username).toBe('alice');
  });
});

// ============================================================
// 游戏数据验证
// ============================================================
describe('游戏数据验证', () => {
  test('6 个游戏均包含完整 HTML 代码包', async () => {
    const bundles = await prisma.gameBundle.findMany({
      select: { htmlCode: true, codeSizeBytes: true, gameId: true },
    });
    expect(bundles.length).toBeGreaterThanOrEqual(6);
    bundles.forEach((b) => {
      expect(b.htmlCode).toContain('<!DOCTYPE html>');
      expect(b.htmlCode.length).toBeGreaterThan(500);
      expect(b.codeSizeBytes).toBeGreaterThan(0);
    });
  });

  test('贪吃蛇游戏数据正确', async () => {
    const game = await prisma.game.findUnique({
      where: { slug: 'snake-evolution' },
      include: { bundles: true },
    });
    expect(game).not.toBeNull();
    expect(game!.status).toBe('published');
    expect(game!.gameType).toBe('arcade');
    expect(Number(game!.playCount)).toBeGreaterThan(10000);
    expect(game!.bundles.length).toBe(1);
    expect(game!.bundles[0].htmlCode).toContain('贪吃蛇');
  });

  test('飞翔小鸟是最高游玩数的游戏', async () => {
    const topGame = await prisma.game.findFirst({
      where: { status: 'published' },
      orderBy: { playCount: 'desc' },
    });
    expect(topGame!.slug).toBe('flappy-bird-clone');
    expect(Number(topGame!.playCount)).toBeGreaterThanOrEqual(44000);
  });

  test('游戏标签是有效的 JSON 数组', async () => {
    const games = await prisma.game.findMany({ select: { tags: true, title: true } });
    games.forEach((g) => {
      const tags = g.tags as string[];
      expect(Array.isArray(tags)).toBe(true);
      expect(tags.length).toBeGreaterThan(0);
    });
  });

  test('游戏按质量分数降序排列可得热门游戏', async () => {
    const games = await prisma.game.findMany({
      where: { status: 'published' },
      orderBy: { qualityScore: 'desc' },
      take: 3,
      select: { title: true, qualityScore: true },
    });
    expect(games[0].qualityScore).toBeGreaterThanOrEqual(games[1].qualityScore);
    expect(games[1].qualityScore).toBeGreaterThanOrEqual(games[2].qualityScore);
  });

  test('alice 的游戏可以通过 authorId 查询', async () => {
    const alice = await prisma.user.findUnique({ where: { username: 'alice' } });
    const aliceGames = await prisma.game.findMany({
      where: { authorId: alice!.id, status: 'published' },
    });
    expect(aliceGames.length).toBeGreaterThanOrEqual(2);
  });

  test('游戏关联查询 (include author)', async () => {
    const game = await prisma.game.findFirst({
      where: { status: 'published' },
      include: { author: true, bundles: true },
    });
    expect(game!.author).not.toBeNull();
    expect(game!.author.username).toBeTruthy();
    expect(game!.bundles.length).toBeGreaterThan(0);
  });
});

// ============================================================
// 社交功能验证
// ============================================================
describe('社交互动数据验证', () => {
  test('bob 关注了 alice', async () => {
    const alice = await prisma.user.findUnique({ where: { username: 'alice' } });
    const bob = await prisma.user.findUnique({ where: { username: 'bobgamer' } });
    const follow = await prisma.userFollow.findUnique({
      where: {
        unique_follow: { followerId: bob!.id, followingId: alice!.id },
      },
    });
    expect(follow).not.toBeNull();
  });

  test('可以查询用户的所有粉丝 (alice 的粉丝)', async () => {
    const alice = await prisma.user.findUnique({ where: { username: 'alice' } });
    const followers = await prisma.userFollow.findMany({
      where: { followingId: alice!.id },
      include: { follower: true },
    });
    expect(followers.length).toBeGreaterThanOrEqual(3);
    const names = followers.map((f) => f.follower.username);
    expect(names).toContain('bobgamer');
  });

  test('点赞交互记录存在', async () => {
    const likeCount = await prisma.socialInteraction.count({
      where: { action: 'like', targetType: 'game' },
    });
    expect(likeCount).toBeGreaterThanOrEqual(5);
  });

  test('重复点赞唯一约束正确工作', async () => {
    const bob = await prisma.user.findUnique({ where: { username: 'bobgamer' } });
    const game = await prisma.game.findUnique({ where: { slug: 'snake-evolution' } });

    // Try to create duplicate like (should throw unique constraint error)
    await expect(
      prisma.socialInteraction.create({
        data: {
          userId: bob!.id,
          targetType: 'game',
          targetId: game!.id,
          action: 'like',
        },
      }),
    ).rejects.toThrow();
  });
});

// ============================================================
// 评论系统验证
// ============================================================
describe('评论数据验证', () => {
  test('游戏评论可以按创建时间排序查询', async () => {
    const game = await prisma.game.findUnique({ where: { slug: 'snake-evolution' } });
    const comments = await prisma.comment.findMany({
      where: { gameId: game!.id },
      orderBy: { createdAt: 'desc' },
      include: { user: true },
    });
    expect(comments.length).toBeGreaterThanOrEqual(2);
    comments.forEach((c) => {
      expect(c.content.length).toBeGreaterThan(0);
      expect(c.user.username).toBeTruthy();
      expect(c.status).toBe('visible');
    });
  });

  test('评论按时间排序 (desc 顺序)', async () => {
    const comments = await prisma.comment.findMany({
      orderBy: { createdAt: 'desc' },
      take: 5,
    });
    for (let i = 0; i < comments.length - 1; i++) {
      expect(comments[i].createdAt.getTime()).toBeGreaterThanOrEqual(
        comments[i + 1].createdAt.getTime(),
      );
    }
  });
});

// ============================================================
// 通知系统验证
// ============================================================
describe('通知数据验证', () => {
  test('通知包含 like/follow/comment/system/earning 各类型', async () => {
    const types = await prisma.notification.findMany({
      select: { type: true },
      distinct: ['type'],
    });
    const typeNames = types.map((t) => t.type);
    expect(typeNames).toContain('like');
    expect(typeNames).toContain('follow');
  });

  test('创建未读通知，标记已读，验证状态变化（自清理）', async () => {
    const { randomUUID } = await import('crypto');
    const alice = await prisma.user.findUnique({ where: { username: 'alice' } });
    const notifId = randomUUID();

    // Create test notification
    await prisma.notification.create({
      data: {
        id: notifId,
        userId: alice!.id,
        type: 'system',
        content: '集成测试通知',
        isRead: false,
      },
    });

    // Verify unread
    const before = await prisma.notification.count({ where: { id: notifId, isRead: false } });
    expect(before).toBe(1);

    // Mark as read
    await prisma.notification.updateMany({ where: { id: notifId }, data: { isRead: true } });

    // Verify read
    const after = await prisma.notification.count({ where: { id: notifId, isRead: false } });
    expect(after).toBe(0);

    // Cleanup
    await prisma.notification.delete({ where: { id: notifId } });
  });
});

// ============================================================
// 收益系统验证
// ============================================================
describe('创作者收益数据验证', () => {
  test('alice 有广告收益记录', async () => {
    const alice = await prisma.user.findUnique({ where: { username: 'alice' } });
    const earnings = await prisma.creatorEarning.findMany({
      where: { creatorId: alice!.id },
    });
    expect(earnings.length).toBeGreaterThanOrEqual(1);
    earnings.forEach((e) => {
      expect(Number(e.amount)).toBeGreaterThan(0);
    });
  });

  test('可以计算创作者总收益', async () => {
    const alice = await prisma.user.findUnique({ where: { username: 'alice' } });
    const earnings = await prisma.creatorEarning.findMany({
      where: { creatorId: alice!.id, status: 'settled' },
      select: { amount: true },
    });
    const total = earnings.reduce((sum, e) => sum + Number(e.amount), 0);
    expect(total).toBeGreaterThan(1000);
  });
});

// ============================================================
// 聚合查询 (Feed/Trending 场景)
// ============================================================
describe('Feed 聚合查询验证', () => {
  test('热门游戏：按 playCount 排序，取前 3', async () => {
    const trending = await prisma.game.findMany({
      where: { status: 'published' },
      orderBy: { playCount: 'desc' },
      take: 3,
      include: { author: { select: { username: true, displayName: true } } },
    });
    expect(trending.length).toBe(3);
    expect(Number(trending[0].playCount)).toBeGreaterThan(Number(trending[2].playCount));
    trending.forEach((g) => {
      expect(g.author.username).toBeTruthy();
    });
  });

  test('按游戏类型筛选: arcade 类型游戏', async () => {
    const arcadeGames = await prisma.game.findMany({
      where: { status: 'published', gameType: 'arcade' },
    });
    expect(arcadeGames.length).toBeGreaterThanOrEqual(2);
    arcadeGames.forEach((g) => expect(g.gameType).toBe('arcade'));
  });

  test('按 publishedAt 查询最近发布', async () => {
    const recent = await prisma.game.findMany({
      where: { status: 'published', publishedAt: { not: null } },
      orderBy: { publishedAt: 'desc' },
      take: 6,
    });
    expect(recent.length).toBe(6);
    for (let i = 0; i < recent.length - 1; i++) {
      expect(recent[i].publishedAt!.getTime()).toBeGreaterThanOrEqual(
        recent[i + 1].publishedAt!.getTime(),
      );
    }
  });

  test('搜索游戏标题 (contains)', async () => {
    const results = await prisma.game.findMany({
      where: {
        status: 'published',
        title: { contains: '贪吃蛇' },
      },
    });
    expect(results.length).toBeGreaterThanOrEqual(1);
    expect(results[0].title).toContain('贪吃蛇');
  });

  test('游玩次数递增操作 (atomic increment)', async () => {
    const game = await prisma.game.findUnique({ where: { slug: 'snake-evolution' } });
    const before = Number(game!.playCount);

    await prisma.game.update({
      where: { id: game!.id },
      data: { playCount: { increment: 1 } },
    });

    const after = await prisma.game.findUnique({ where: { id: game!.id } });
    expect(Number(after!.playCount)).toBe(before + 1);

    // Restore
    await prisma.game.update({
      where: { id: game!.id },
      data: { playCount: { decrement: 1 } },
    });
  });
});
