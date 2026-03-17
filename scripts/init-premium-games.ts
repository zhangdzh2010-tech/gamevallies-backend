/**
 * 精品手机游戏初始化脚本
 *
 * 将 5 款精品 HTML5 手机游戏写入 MySQL 数据库，
 * 适配当前 Prisma / MySQL 架构（GameBundle 存储在 MySQL）。
 *
 * 特性：
 *  - 以 slug 为 key 做 upsert，可重复执行（幂等）
 *  - 自动创建 seed_creator 系统用户（若不存在）
 *  - 同步写入 Game + GameBundle 两张表
 *  - 从 scripts/games/ 目录读取 HTML 文件
 *
 * 运行：
 *   DATABASE_URL=... npx ts-node scripts/init-premium-games.ts
 */

import { PrismaClient } from '@prisma/client';
import bcrypt from 'bcryptjs';
import { randomUUID } from 'crypto';
import { readFileSync } from 'fs';
import { join } from 'path';

const prisma = new PrismaClient({
  datasources: { db: { url: process.env.DATABASE_URL } },
});

// ============================================================
// 读取游戏 HTML 文件
// ============================================================
const GAMES_DIR = join(__dirname, 'games');

function loadGameHTML(filename: string): string {
  return readFileSync(join(GAMES_DIR, filename), 'utf-8');
}

// ============================================================
// 精品手机游戏定义
// ============================================================
const PREMIUM_GAMES = [
  {
    slug: 'premium-pixel-dungeon',
    title: '像素地牢',
    description:
      'Roguelike地牢探险！程序生成地牢地图，回合制策略战斗。5层地牢逐层深入，收集武器装备，' +
      '使用药水恢复生命，升级角色属性，最终击败第5层的远古巨龙！支持方向键、滑动和D-pad操控。',
    gameType: 'puzzle',
    tags: ['策略', 'RPG', 'Roguelike', '回合制', '地牢'],
    platform: 'mobile' as const,
    qualityScore: 9.5,
    avgPlayTime: 15.0,
    playCount: BigInt(12800),
    likeCount: BigInt(2340),
    forkCount: BigInt(456),
    htmlFile: 'pixel-dungeon.html',
  },
  {
    slug: 'premium-tower-defense',
    title: '塔防战线',
    description:
      '经典策略塔防！4种防御塔（箭塔/炮塔/冰塔/雷塔）各具特色，3级升级系统深度策略搭配。' +
      '20波敌人逐渐增强，每5波出现BOSS挑战。合理布局、升级路线决定胜负！支持触屏操作。',
    gameType: 'puzzle',
    tags: ['策略', '塔防', '回合制', '升级'],
    platform: 'mobile' as const,
    qualityScore: 9.4,
    avgPlayTime: 12.0,
    playCount: BigInt(10500),
    likeCount: BigInt(1870),
    forkCount: BigInt(380),
    htmlFile: 'tower-defense.html',
  },
  {
    slug: 'premium-gravity-flip',
    title: '重力翻转',
    description:
      '赛博朋克风格重力翻转平台跳跃！点击屏幕切换重力方向，躲避尖刺、活动平台、碾压器和传送门。' +
      '10个精心设计的关卡，3星收集系统，连击加分，检查点存档。挑战最快通关时间！',
    gameType: 'action',
    tags: ['动作', '平台跳跃', '物理', '关卡', '挑战'],
    platform: 'mobile' as const,
    qualityScore: 9.3,
    avgPlayTime: 8.0,
    playCount: BigInt(15200),
    likeCount: BigInt(2650),
    forkCount: BigInt(520),
    htmlFile: 'gravity-flip.html',
  },
  {
    slug: 'premium-space-miner',
    title: '星际矿工',
    description:
      '太空采矿资源管理游戏！驾驶飞船采集4种稀有矿石，返回基地出售资源并升级飞船。' +
      '4大升级系统（引擎/货舱/激光/护盾），躲避陨石雨、太空海盗和黑洞。日夜交替影响资源分布！',
    gameType: 'action',
    tags: ['动作', '资源管理', '太空', '升级', '生存'],
    platform: 'mobile' as const,
    qualityScore: 9.2,
    avgPlayTime: 10.0,
    playCount: BigInt(8900),
    likeCount: BigInt(1520),
    forkCount: BigInt(310),
    htmlFile: 'space-miner.html',
  },
  {
    slug: 'premium-sudoku-master',
    title: '数独大师',
    description:
      '精品数独解谜！算法生成唯一解谜题，4个难度等级（入门/普通/困难/专家）。' +
      '完整笔记系统、3次提示、撤销重做、自动检查、冲突高亮、相同数字高亮、计时统计。' +
      '清新优雅的现代UI设计，挑战你的逻辑思维极限！',
    gameType: 'puzzle',
    tags: ['益智', '数独', '逻辑', '经典', '解谜'],
    platform: 'mobile' as const,
    qualityScore: 9.6,
    avgPlayTime: 20.0,
    playCount: BigInt(18500),
    likeCount: BigInt(3200),
    forkCount: BigInt(680),
    htmlFile: 'sudoku-master.html',
  },
];

// ============================================================
// 业务初始化主逻辑
// ============================================================

async function initPremiumGames() {
  console.log('🎮 精品手机游戏初始化\n' + '═'.repeat(50));

  // Step 1: 确保系统种子创作者存在
  const SEED_USER = {
    username: 'seed_creator',
    email: 'seed@gamevallies.com',
    displayName: '平台精选游戏',
    bio: '平台官方精选游戏，汇聚多款经典 HTML5 小游戏。',
  };

  let seedUser = await prisma.user.findUnique({
    where: { username: SEED_USER.username },
  });

  if (!seedUser) {
    const passwordHash = await bcrypt.hash('SeedCreator@2026!', 12);
    seedUser = await prisma.user.create({
      data: {
        id: randomUUID(),
        username: SEED_USER.username,
        email: SEED_USER.email,
        displayName: SEED_USER.displayName,
        bio: SEED_USER.bio,
        passwordHash,
        role: 'creator',
        followerCount: 9999,
        followingCount: 0,
        gameCount: 0,
        totalPlays: BigInt(0),
      },
    });
    console.log(`✅ 创建种子用户: ${seedUser.username} (${seedUser.id})`);
  } else {
    console.log(`ℹ️  种子用户已存在: ${seedUser.username} (${seedUser.id})`);
  }

  // Step 2: 读取 HTML 并写入游戏（upsert 幂等）
  console.log(`\n📦 写入 ${PREMIUM_GAMES.length} 款精品手机游戏...\n`);

  const results: Array<{ title: string; slug: string; id: string; isNew: boolean }> = [];

  for (const def of PREMIUM_GAMES) {
    // 读取 HTML 文件
    const htmlCode = loadGameHTML(def.htmlFile);
    console.log(`  📄 读取 ${def.htmlFile} (${Buffer.byteLength(htmlCode, 'utf8')} bytes)`);

    const publishedAt = new Date(
      Date.now() - Math.floor(Math.random() * 30 + 7) * 86400000,
    );

    // 查找是否已存在
    const existing = await prisma.game.findUnique({ where: { slug: def.slug } });

    const gameId = existing?.id ?? randomUUID();
    const bundleId = randomUUID();

    const game = await prisma.game.upsert({
      where: { slug: def.slug },
      update: {
        playCount: def.playCount,
        likeCount: def.likeCount,
        forkCount: def.forkCount,
        qualityScore: def.qualityScore,
        avgPlayTime: def.avgPlayTime,
      },
      create: {
        id: gameId,
        authorId: seedUser.id,
        title: def.title,
        description: def.description,
        slug: def.slug,
        status: 'published',
        gameType: def.gameType,
        tags: def.tags,
        codeBundleId: bundleId,
        version: 1,
        playCount: def.playCount,
        likeCount: def.likeCount,
        forkCount: def.forkCount,
        qualityScore: def.qualityScore,
        avgPlayTime: def.avgPlayTime,
        publishedAt,
        createdAt: publishedAt,
      },
    });

    // 写入或更新 GameBundle
    await prisma.gameBundle.upsert({
      where: { uk_game_version: { gameId: game.id, version: 1 } },
      update: {
        htmlCode,
        codeSizeBytes: Buffer.byteLength(htmlCode, 'utf8'),
      },
      create: {
        id: bundleId,
        gameId: game.id,
        version: 1,
        htmlCode,
        codeSizeBytes: Buffer.byteLength(htmlCode, 'utf8'),
        spec: { gameType: def.gameType, tags: def.tags, platform: def.platform },
        generationMeta: { method: 'premium_seed', aiAssisted: false, seedVersion: '2.0.0' },
        metadata: { title: def.title, description: def.description, platform: def.platform },
      },
    });

    const isNew = !existing;
    const icon = isNew ? '✅' : '🔄';
    const label = isNew ? '新建' : '更新';
    console.log(
      `${icon} [${label}][${def.platform}] ${def.title}\n` +
        `   slug: ${def.slug}\n` +
        `   id: ${game.id}\n` +
        `   质量分: ${def.qualityScore}  游玩: ${Number(def.playCount).toLocaleString()}  点赞: ${Number(def.likeCount).toLocaleString()}\n`,
    );

    results.push({ title: def.title, slug: def.slug, id: game.id, isNew });
  }

  // Step 3: 更新种子用户统计
  const existingGames = await prisma.game.count({
    where: { authorId: seedUser.id },
  });
  const totalPlays = await prisma.game.aggregate({
    where: { authorId: seedUser.id },
    _sum: { playCount: true },
  });

  await prisma.user.update({
    where: { id: seedUser.id },
    data: {
      gameCount: existingGames,
      totalPlays: totalPlays._sum.playCount ?? BigInt(0),
    },
  });

  // Step 4: 汇总
  const newCount = results.filter((r) => r.isNew).length;
  const updateCount = results.filter((r) => !r.isNew).length;

  console.log('═'.repeat(50));
  console.log(`
╔══════════════════════════════════════════════════╗
║       🎉 精品手机游戏初始化完成                    ║
╠══════════════════════════════════════════════════╣
║  新建游戏:    ${String(newCount).padEnd(34)}║
║  更新游戏:    ${String(updateCount).padEnd(34)}║
║  种子用户:    ${seedUser.username.padEnd(34)}║
╠══════════════════════════════════════════════════╣
║  游戏列表:                                        ║`);
  results.forEach((r) => {
    console.log(`║    ${r.title.padEnd(44)}  ║`);
  });
  console.log(`╚══════════════════════════════════════════════════╝
`);
}

initPremiumGames()
  .catch((e) => {
    console.error('❌ 精品游戏初始化失败:', e.message);
    console.error(e.stack);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
