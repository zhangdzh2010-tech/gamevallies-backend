import { PrismaClient } from '@prisma/client';
import * as bcrypt from 'bcrypt';

const prisma = new PrismaClient();

async function main() {
  console.log('Starting database seeding...');

  try {
    // Clean up existing data (use with caution)
    // await prisma.user.deleteMany({});
    // await prisma.game.deleteMany({});

    // ============================================================
    // Create Test Users
    // ============================================================
    console.log('\n📝 Creating test users...');

    const users = await Promise.all([
      prisma.user.upsert({
        where: { email: 'alice@playforge.com' },
        update: {},
        create: {
          email: 'alice@playforge.com',
          username: 'alice',
          displayName: 'Alice Chen',
          passwordHash: await bcrypt.hash('password123', 10),
          bio: 'Game developer and indie creator',
          avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=alice',
          isVerified: true,
          isCreator: true,
          creatorRating: 4.8,
          totalEarnings: 15000,
          followerCount: 1250,
          followingCount: 180,
          lastLoginAt: new Date(),
          createdAt: new Date(Date.now() - 180 * 24 * 60 * 60 * 1000), // 6 months ago
        },
      }),
      prisma.user.upsert({
        where: { email: 'bob@playforge.com' },
        update: {},
        create: {
          email: 'bob@playforge.com',
          username: 'bobgamer',
          displayName: 'Bob Johnson',
          passwordHash: await bcrypt.hash('password123', 10),
          bio: 'Casual gamer, love puzzle games',
          avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=bob',
          isVerified: true,
          isCreator: false,
          createdAt: new Date(Date.now() - 120 * 24 * 60 * 60 * 1000), // 4 months ago
        },
      }),
      prisma.user.upsert({
        where: { email: 'carol@playforge.com' },
        update: {},
        create: {
          email: 'carol@playforge.com',
          username: 'caroldev',
          displayName: 'Carol Adams',
          passwordHash: await bcrypt.hash('password123', 10),
          bio: 'Game designer, AI enthusiast',
          avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=carol',
          isVerified: true,
          isCreator: true,
          creatorRating: 4.5,
          totalEarnings: 8500,
          followerCount: 680,
          followingCount: 250,
          lastLoginAt: new Date(),
          createdAt: new Date(Date.now() - 90 * 24 * 60 * 60 * 1000), // 3 months ago
        },
      }),
      prisma.user.upsert({
        where: { email: 'david@playforge.com' },
        update: {},
        create: {
          email: 'david@playforge.com',
          username: 'davidy',
          displayName: 'David Lee',
          passwordHash: await bcrypt.hash('password123', 10),
          bio: 'Action game lover',
          avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=david',
          isVerified: false,
          isCreator: false,
          createdAt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000), // 1 month ago
        },
      }),
      prisma.user.upsert({
        where: { email: 'emma@playforge.com' },
        update: {},
        create: {
          email: 'emma@playforge.com',
          username: 'emmacraft',
          displayName: 'Emma Wilson',
          passwordHash: await bcrypt.hash('password123', 10),
          bio: 'Strategy game creator',
          avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=emma',
          isVerified: true,
          isCreator: true,
          creatorRating: 4.6,
          totalEarnings: 12300,
          followerCount: 890,
          followingCount: 160,
          lastLoginAt: new Date(),
          createdAt: new Date(Date.now() - 60 * 24 * 60 * 60 * 1000), // 2 months ago
        },
      }),
    ]);

    console.log(`✅ Created ${users.length} users`);

    // ============================================================
    // Create Sample Games
    // ============================================================
    console.log('\n🎮 Creating sample games...');

    const games = await Promise.all([
      // JSX Demo Game 1: 星际躲避球
      prisma.game.upsert({
        where: { slug: 'interstellar-dodgeball' },
        update: {},
        create: {
          title: '星际躲避球 (Interstellar Dodgeball)',
          slug: 'interstellar-dodgeball',
          description: '在浩瀚的星际中展开激烈的躲避球对战。快速反应，躲避敌方的能量球，摧毁对手的飞船！',
          content: 'A thrilling space-themed dodgeball game with multiplayer support and real-time combat mechanics.',
          gameType: 'action',
          tags: ['action', 'space', 'multiplayer', 'arcade'],
          creatorId: users[0].id,
          status: 'published',
          isPublic: true,
          version: '1.0.0',
          rating: 4.7,
          ratingCount: 342,
          playCount: 15680,
          downloadCount: 8900,
          likeCount: 1245,
          commentCount: 87,
          thumbnailUrl: 'https://via.placeholder.com/400x300?text=Interstellar+Dodgeball',
          createdAt: new Date(Date.now() - 150 * 24 * 60 * 60 * 1000),
        },
      }),

      // JSX Demo Game 2: 像素美食家
      prisma.game.upsert({
        where: { slug: 'pixel-gourmet' },
        update: {},
        create: {
          title: '像素美食家 (Pixel Gourmet)',
          slug: 'pixel-gourmet',
          description: '成为一名像素艺术美食家！烹饪、配方、美食评比。从简单的汉堡到复杂的多层蛋糕，挑战你的烹饪技能！',
          content: 'A delightful pixel-art cooking game where you prepare and serve dishes to customers.',
          gameType: 'puzzle',
          tags: ['cooking', 'puzzle', 'pixel-art', 'time-management'],
          creatorId: users[2].id,
          status: 'published',
          isPublic: true,
          version: '1.2.1',
          rating: 4.5,
          ratingCount: 287,
          playCount: 12450,
          downloadCount: 6800,
          likeCount: 980,
          commentCount: 65,
          thumbnailUrl: 'https://via.placeholder.com/400x300?text=Pixel+Gourmet',
          createdAt: new Date(Date.now() - 120 * 24 * 60 * 60 * 1000),
        },
      }),

      // JSX Demo Game 3: 彩虹方块消消乐
      prisma.game.upsert({
        where: { slug: 'rainbow-blocks' },
        update: {},
        create: {
          title: '彩虹方块消消乐 (Rainbow Blocks Crush)',
          slug: 'rainbow-blocks',
          description: '经典的彩虹方块消除游戏。交换、消除、获得高分。单人模式、竞技模式和合作模式等你来挑战！',
          content: 'Match colorful blocks in this addictive puzzle game with multiple game modes.',
          gameType: 'puzzle',
          tags: ['puzzle', 'match3', 'casual', 'colorful'],
          creatorId: users[0].id,
          status: 'published',
          isPublic: true,
          version: '2.1.0',
          rating: 4.6,
          ratingCount: 512,
          playCount: 25300,
          downloadCount: 14200,
          likeCount: 2100,
          commentCount: 142,
          thumbnailUrl: 'https://via.placeholder.com/400x300?text=Rainbow+Blocks',
          createdAt: new Date(Date.now() - 140 * 24 * 60 * 60 * 1000),
        },
      }),

      // JSX Demo Game 4: 疯狂农场经营
      prisma.game.upsert({
        where: { slug: 'crazy-farm-tycoon' },
        update: {},
        create: {
          title: '疯狂农场经营 (Crazy Farm Tycoon)',
          slug: 'crazy-farm-tycoon',
          description: '经营你自己的农场帝国！种植作物、养殖动物、升级设施、与朋友合作。从小农场到农业巨头！',
          content: 'Build and expand your farm in this engaging simulation game with resource management.',
          gameType: 'simulation',
          tags: ['simulation', 'farm', 'tycoon', 'management'],
          creatorId: users[4].id,
          status: 'published',
          isPublic: true,
          version: '1.5.2',
          rating: 4.3,
          ratingCount: 198,
          playCount: 9650,
          downloadCount: 5400,
          likeCount: 720,
          commentCount: 54,
          thumbnailUrl: 'https://via.placeholder.com/400x300?text=Crazy+Farm+Tycoon',
          createdAt: new Date(Date.now() - 100 * 24 * 60 * 60 * 1000),
        },
      }),

      // JSX Demo Game 5: 迷宫冒险
      prisma.game.upsert({
        where: { slug: 'maze-adventure' },
        update: {},
        create: {
          title: '迷宫冒险 (Maze Adventure)',
          slug: 'maze-adventure',
          description: '探索无尽的迷宫，逃离怪物的追捕。收集宝藏、解开谜题、发现隐藏的出口。冒险在等待！',
          content: 'Navigate through procedurally generated mazes with increasing difficulty and hidden secrets.',
          gameType: 'adventure',
          tags: ['adventure', 'maze', 'exploration', 'procedural'],
          creatorId: users[2].id,
          status: 'published',
          isPublic: true,
          version: '1.0.5',
          rating: 4.4,
          ratingCount: 156,
          playCount: 8230,
          downloadCount: 4600,
          likeCount: 620,
          commentCount: 42,
          thumbnailUrl: 'https://via.placeholder.com/400x300?text=Maze+Adventure',
          createdAt: new Date(Date.now() - 110 * 24 * 60 * 60 * 1000),
        },
      }),

      // JSX Demo Game 6: 小球大冒险
      prisma.game.upsert({
        where: { slug: 'ball-adventure' },
        update: {},
        create: {
          title: '小球大冒险 (Ball Quest)',
          slug: 'ball-adventure',
          description: '控制小球通过各种物理挑战。翻滚、跳跃、滚动。收集星星、打败BOSS、解锁新世界！',
          content: 'A physics-based adventure game with challenging levels and creative puzzle designs.',
          gameType: 'platformer',
          tags: ['platformer', 'physics', 'adventure', 'challenge'],
          creatorId: users[0].id,
          status: 'published',
          isPublic: true,
          version: '1.3.0',
          rating: 4.5,
          ratingCount: 189,
          playCount: 10450,
          downloadCount: 5900,
          likeCount: 845,
          commentCount: 48,
          thumbnailUrl: 'https://via.placeholder.com/400x300?text=Ball+Quest',
          createdAt: new Date(Date.now() - 95 * 24 * 60 * 60 * 1000),
        },
      }),

      // Additional games
      prisma.game.upsert({
        where: { slug: 'space-shooter' },
        update: {},
        create: {
          title: 'Space Shooter Pro',
          slug: 'space-shooter',
          description: 'Ultimate space shooting action game with stunning graphics and intense gameplay.',
          content: 'Blast your way through waves of enemies in this classic arcade-style shooter.',
          gameType: 'action',
          tags: ['action', 'shooter', 'space', 'arcade'],
          creatorId: users[0].id,
          status: 'published',
          isPublic: true,
          version: '1.0.0',
          rating: 4.2,
          ratingCount: 234,
          playCount: 5600,
          downloadCount: 2800,
          likeCount: 450,
          commentCount: 32,
          thumbnailUrl: 'https://via.placeholder.com/400x300?text=Space+Shooter',
          createdAt: new Date(Date.now() - 75 * 24 * 60 * 60 * 1000),
        },
      }),

      prisma.game.upsert({
        where: { slug: 'story-quest' },
        update: {},
        create: {
          title: 'Story Quest',
          slug: 'story-quest',
          description: 'An immersive story-driven adventure game with multiple endings and choices.',
          content: 'Your decisions matter in this branching narrative game.',
          gameType: 'adventure',
          tags: ['story', 'adventure', 'narrative', 'choices'],
          creatorId: users[4].id,
          status: 'draft',
          isPublic: false,
          version: '0.9.0',
          rating: 0,
          ratingCount: 0,
          playCount: 0,
          downloadCount: 0,
          likeCount: 0,
          commentCount: 0,
          thumbnailUrl: 'https://via.placeholder.com/400x300?text=Story+Quest',
          createdAt: new Date(),
        },
      }),
    ]);

    console.log(`✅ Created ${games.length} games`);

    // ============================================================
    // Create Social Interactions
    // ============================================================
    console.log('\n👥 Creating social interactions...');

    // Follows
    await prisma.follow.createMany({
      data: [
        { followerId: users[1].id, followingId: users[0].id }, // bob follows alice
        { followerId: users[1].id, followingId: users[2].id }, // bob follows carol
        { followerId: users[3].id, followingId: users[0].id }, // david follows alice
        { followerId: users[3].id, followingId: users[4].id }, // david follows emma
        { followerId: users[2].id, followingId: users[0].id }, // carol follows alice
      ],
      skipDuplicates: true,
    });

    console.log('✅ Created follow relationships');

    // Game Likes
    await prisma.gameLike.createMany({
      data: [
        { userId: users[1].id, gameId: games[0].id }, // bob likes interstellar-dodgeball
        { userId: users[3].id, gameId: games[0].id }, // david likes interstellar-dodgeball
        { userId: users[1].id, gameId: games[1].id }, // bob likes pixel-gourmet
        { userId: users[3].id, gameId: games[2].id }, // david likes rainbow-blocks
        { userId: users[4].id, gameId: games[0].id }, // emma likes interstellar-dodgeball
        { userId: users[1].id, gameId: games[3].id }, // bob likes crazy-farm
        { userId: users[0].id, gameId: games[1].id }, // alice likes pixel-gourmet
      ],
      skipDuplicates: true,
    });

    console.log('✅ Created game likes');

    // ============================================================
    // Create Comments
    // ============================================================
    console.log('\n💬 Creating comments...');

    const comments = await Promise.all([
      prisma.comment.create({
        data: {
          content: 'This game is absolutely amazing! The gameplay is so smooth and addictive.',
          userId: users[1].id,
          gameId: games[0].id,
          likes: 12,
        },
      }),
      prisma.comment.create({
        data: {
          content: 'Great mechanics, but I wish there were more levels.',
          userId: users[3].id,
          gameId: games[0].id,
          likes: 8,
        },
      }),
      prisma.comment.create({
        data: {
          content: 'Pixel art is so charming! Cooking gameplay is really fun.',
          userId: users[1].id,
          gameId: games[1].id,
          likes: 15,
        },
      }),
      prisma.comment.create({
        data: {
          content: 'Perfect for casual gaming. Highly recommend!',
          userId: users[4].id,
          gameId: games[2].id,
          likes: 9,
        },
      }),
    ]);

    console.log(`✅ Created ${comments.length} comments`);

    // ============================================================
    // Create Notifications
    // ============================================================
    console.log('\n📬 Creating notifications...');

    await Promise.all([
      prisma.notification.create({
        data: {
          userId: users[0].id,
          type: 'follow',
          message: `${users[1].displayName} started following you`,
          relatedUserId: users[1].id,
          isRead: false,
        },
      }),
      prisma.notification.create({
        data: {
          userId: users[0].id,
          type: 'game_like',
          message: `${users[3].displayName} liked your game "${games[0].title}"`,
          relatedGameId: games[0].id,
          isRead: false,
        },
      }),
      prisma.notification.create({
        data: {
          userId: users[2].id,
          type: 'comment',
          message: `${users[1].displayName} commented on your game "${games[1].title}"`,
          relatedGameId: games[1].id,
          isRead: true,
        },
      }),
      prisma.notification.create({
        data: {
          userId: users[4].id,
          type: 'earnings',
          message: 'You earned $125.50 from game plays this week',
          isRead: false,
        },
      }),
    ]);

    console.log('✅ Created notifications');

    // ============================================================
    // Create Game Ratings
    // ============================================================
    console.log('\n⭐ Creating game ratings...');

    await prisma.gameRating.createMany({
      data: [
        { userId: users[1].id, gameId: games[0].id, rating: 5 },
        { userId: users[3].id, gameId: games[0].id, rating: 4 },
        { userId: users[1].id, gameId: games[1].id, rating: 5 },
        { userId: users[4].id, gameId: games[2].id, rating: 5 },
      ],
      skipDuplicates: true,
    });

    console.log('✅ Created game ratings');

    // ============================================================
    // Create Creator Earnings
    // ============================================================
    console.log('\n💰 Creating creator earnings...');

    await Promise.all([
      prisma.creatorEarning.create({
        data: {
          creatorId: users[0].id,
          gameId: games[0].id,
          amount: 1250.50,
          source: 'game_plays',
          period: 'week',
          weekStartDate: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000),
        },
      }),
      prisma.creatorEarning.create({
        data: {
          creatorId: users[2].id,
          gameId: games[1].id,
          amount: 850.25,
          source: 'game_plays',
          period: 'week',
          weekStartDate: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000),
        },
      }),
      prisma.creatorEarning.create({
        data: {
          creatorId: users[4].id,
          gameId: games[3].id,
          amount: 650.75,
          source: 'game_plays',
          period: 'week',
          weekStartDate: new Date(Date.now() - 7 * 24 * 60 * 60 * 1000),
        },
      }),
    ]);

    console.log('✅ Created creator earnings');

    // ============================================================
    // Summary
    // ============================================================
    console.log('\n✨ Database seeding completed successfully!');
    console.log(`
Database Summary:
- Users: ${users.length}
- Games: ${games.length}
- Comments: ${comments.length}
- Test Accounts:
  - alice@playforge.com / password123
  - bob@playforge.com / password123
  - carol@playforge.com / password123
  - david@playforge.com / password123
  - emma@playforge.com / password123
    `);
  } catch (error) {
    console.error('Error seeding database:', error);
    throw error;
  } finally {
    await prisma.$disconnect();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
