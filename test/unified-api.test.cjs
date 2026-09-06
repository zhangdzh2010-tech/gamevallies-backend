const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Test } = require('@nestjs/testing');
const { Controller, Post, Req } = require('@nestjs/common');
const request = require('supertest');
const { JwtService } = require('@nestjs/jwt');
process.env.ADMIN_TOKEN = 'test-only-admin-token';
process.env.NODE_ENV = 'test';
process.env.JWT_SECRET = 'test-only-unified-api-secret';
process.env.DATABASE_URL = 'mysql://test:test@127.0.0.1:3306/test';
process.env.GENERATION_QUEUE_ENABLED = 'false';
class RawBodyProbe { receive(req) { return { raw: req.rawBody.toString('utf8') }; } }
Controller('__test')(RawBodyProbe);
Post('raw')(RawBodyProbe.prototype, 'receive', Object.getOwnPropertyDescriptor(RawBodyProbe.prototype, 'receive'));
Req()(RawBodyProbe.prototype, 'receive', 0);
const from = (pkg, file) => require(`../packages/${pkg}/dist/${file}`);

test('unified API mounts all features once, shares Prisma, and verifies access tokens', async () => {
  const { AppModule } = from('game-service', 'app.module');
  const { PrismaService } = from('user-service', 'prisma/prisma.service');
  const { FeedService } = from('feed-service', 'feed/feed.service');
  const { AuthService } = from('user-service', 'auth/auth.service');
  const { LikeService } = from('feed-service', 'like/like.service');
  const { configureApp } = from('user-service', 'bootstrap');
  const prisma = {};
  let likedBy;
  const feed = {
    getTrendingFeed: async () => ({ data: [], pagination: { page: 1, limit: 10, total: 0, hasMore: false } }),
    invalidateGameFeedCache: async () => ({ success: true }),
  };
  const module = await Test.createTestingModule({ imports: [AppModule], controllers: [RawBodyProbe] })
    .overrideProvider(PrismaService).useValue(prisma)
    .overrideProvider(AuthService).useValue({})
    .overrideProvider(FeedService).useValue(feed)
    .overrideProvider(LikeService).useValue({ toggleLike: async (id) => { likedBy = id; return { liked: true, likeCount: 1 }; } })
    .compile();
  // Suppress only infrastructure lifecycle work: no real DB, Redis or AI calls.
  for (const [pkg, file, name] of [
    ['user-service','billing/billing-schema-bootstrap.service','BillingSchemaBootstrapService'],
    ['game-service','game/game-schema-bootstrap.service','GameSchemaBootstrapService'],
    ['game-service','game/generation-queue.worker','GenerationQueueWorkerService'],
    ['game-service','game/game.service','GameService'],
    ['game-service','growth/growth.service','GrowthService'],
  ]) module.get(from(pkg,file)[name]).onModuleInit = async () => {};
  const app = module.createNestApplication({ rawBody: true });
  app.useBodyParser('json', { limit: '6mb' });
  app.useBodyParser('urlencoded', { extended: true, limit: '6mb' });
  configureApp(app, { proxy: false, unified: true });
  await app.init();
  try {
    assert.equal(from('game-service','prisma/prisma.service').PrismaService, PrismaService);
    assert.equal(from('feed-service','prisma/prisma.service').PrismaService, PrismaService);
    assert.equal(module.get(PrismaService), prisma);
    assert.equal(module.get(from('game-service','game/game.service').GameService).localFeedService, feed);
    assert.equal(module.get(from('game-service','admin/admin.service').AdminService).localFeedService, feed);
    const routes = app.getHttpAdapter().getInstance()._router.stack.filter(x => x.route).map(x => x.route);
    const keys = routes.flatMap(r => Object.keys(r.methods).map(m => `${m} ${r.path}`));
    assert.equal(new Set(keys).size, keys.length, 'duplicate routes');
    for (const key of ['get /api/v1/users/me','get /api/v1/feed/trending','post /api/v1/social/like','post /api/v1/games/creation-sessions','get /admin','get /games/:id/preview']) assert(keys.includes(key), key);
    const paymentBody = '{ \"signatureInput\": \"original whitespace\" }';
    const raw = await request(app.getHttpServer()).post('/api/v1/__test/raw').set('Content-Type','application/json').send(paymentBody).expect(201);
    assert.equal(raw.body.raw, paymentBody);
    await request(app.getHttpServer()).get('/api/v1/health').expect(200);
    await request(app.getHttpServer()).get('/api/v1/feed/trending').expect(200);
    await request(app.getHttpServer()).get('/api/v1/users/me').expect(401);
    const jwt = new JwtService();
    for (const token of ['broken', jwt.sign({sub:'attacker'}, { secret:'wrong-secret' }), jwt.sign({sub:'expired'}, {secret:process.env.JWT_SECRET, expiresIn:-1})]) {
      await request(app.getHttpServer()).post('/api/v1/social/like').set('Authorization', `Bearer ${token}`).send({targetType:'game',targetId:'1'}).expect(401);
      await request(app.getHttpServer()).get('/api/v1/games/my').set('Authorization', `Bearer ${token}`).expect(401);
    }
    const token = jwt.sign({ sub:'member-1' }, { secret:process.env.JWT_SECRET, expiresIn:60 });
    await request(app.getHttpServer()).post('/api/v1/social/like').set('Authorization', `Bearer ${token}`).send({targetType:'game',targetId:'1'}).expect(200);
    assert.equal(likedBy, 'member-1');
    await request(app.getHttpServer()).post('/api/v1/social/like').set('Authorization', `Bearer ${token}`).send({targetType:'invalid',targetId:'1'}).expect(400);
  } finally { await app.close(); }
});
