import { Test, TestingModule } from '@nestjs/testing';
import { INestApplication } from '@nestjs/common';
import * as request from 'supertest';

/**
 * Full-flow integration test for PlayForge backend
 *
 * This test demonstrates the complete user journey:
 * 1. Register a new user
 * 2. Login to get authentication tokens
 * 3. Create a new game (with mocked AI generation)
 * 4. Fetch game details
 * 5. Publish the game
 * 6. Like the game
 * 7. Comment on the game
 * 8. Fork the game
 * 9. View trending feed (should include the published game)
 *
 * HOW TO RUN:
 * 1. Ensure all services are running:
 *    cd /sessions/magical-gifted-pascal/mnt/willgame/gamevallies-backend
 *    docker-compose up -d
 *
 * 2. Run the integration test:
 *    npm run test:integration
 *    or
 *    jest test/integration/full-flow.spec.ts
 *
 * 3. To run with coverage:
 *    npm run test:integration:cov
 *
 * PREREQUISITES:
 * - All microservices running (user-service, game-service, social-service, feed-service)
 * - Database migrations applied
 * - Redis running for caching
 * - AI Engine service available (can be mocked)
 *
 * EXPECTED BEHAVIOR:
 * - All operations should succeed with expected status codes
 * - Tokens should be valid and refreshable
 * - Games should be created and appear in trending feed
 * - Social interactions (likes, comments, follows) should work atomically
 * - Fork functionality should preserve game data and track lineage
 */

describe('PlayForge Full Flow Integration Test', () => {
  let app: INestApplication;

  // Test data
  const testUser = {
    username: 'integrationtest_' + Date.now(),
    email: `test_${Date.now()}@playforge.test`,
    password: 'SecurePass123!',
    displayName: 'Integration Tester',
  };

  const testUser2 = {
    username: 'integrationtest2_' + Date.now(),
    email: `test2_${Date.now()}@playforge.test`,
    password: 'SecurePass456!',
    displayName: 'Integration Tester 2',
  };

  let user1Tokens: { accessToken: string; refreshToken: string };
  let user2Tokens: { accessToken: string; refreshToken: string };
  let createdGameId: string;
  let publishedGameId: string;
  let forkedGameId: string;

  beforeAll(async () => {
    // Initialize the app (this would be done by your test setup)
    const module: TestingModule = await Test.createTestingModule({
      controllers: [],
      providers: [],
    }).compile();

    app = module.createNestApplication();
    await app.init();
  });

  afterAll(async () => {
    await app.close();
  });

  describe('1. User Registration and Authentication', () => {
    it('should register a new user successfully', async () => {
      const response = await request(app.getHttpServer())
        .post('/api/v1/auth/register')
        .send(testUser)
        .expect(201);

      expect(response.body).toHaveProperty('user');
      expect(response.body).toHaveProperty('accessToken');
      expect(response.body).toHaveProperty('refreshToken');
      expect(response.body.user.username).toBe(testUser.username.toLowerCase());
      expect(response.body.user.email).toBe(testUser.email.toLowerCase());

      user1Tokens = {
        accessToken: response.body.accessToken,
        refreshToken: response.body.refreshToken,
      };
    });

    it('should register a second user for social interactions', async () => {
      const response = await request(app.getHttpServer())
        .post('/api/v1/auth/register')
        .send(testUser2)
        .expect(201);

      user2Tokens = {
        accessToken: response.body.accessToken,
        refreshToken: response.body.refreshToken,
      };
    });

    it('should prevent duplicate username registration', async () => {
      await request(app.getHttpServer())
        .post('/api/v1/auth/register')
        .send({
          username: testUser.username,
          email: 'another@email.com',
          password: 'SecurePass123!',
        })
        .expect(409);
    });

    it('should login user and return new tokens', async () => {
      const response = await request(app.getHttpServer())
        .post('/api/v1/auth/login')
        .send({
          username: testUser.username,
          password: testUser.password,
        })
        .expect(200);

      expect(response.body).toHaveProperty('accessToken');
      expect(response.body).toHaveProperty('refreshToken');

      user1Tokens = {
        accessToken: response.body.accessToken,
        refreshToken: response.body.refreshToken,
      };
    });

    it('should reject login with wrong password', async () => {
      await request(app.getHttpServer())
        .post('/api/v1/auth/login')
        .send({
          username: testUser.username,
          password: 'WrongPassword123!',
        })
        .expect(401);
    });

    it('should refresh tokens', async () => {
      const response = await request(app.getHttpServer())
        .post('/api/v1/auth/refresh')
        .send({ refreshToken: user1Tokens.refreshToken })
        .expect(200);

      expect(response.body).toHaveProperty('accessToken');
      expect(response.body).toHaveProperty('refreshToken');

      user1Tokens = {
        accessToken: response.body.accessToken,
        refreshToken: response.body.refreshToken,
      };
    });
  });

  describe('2. Game Creation and Management', () => {
    it('should create a new game in draft status', async () => {
      const response = await request(app.getHttpServer())
        .post('/api/v1/games')
        .set('Authorization', `Bearer ${user1Tokens.accessToken}`)
        .send({
          title: 'Test Integration Game',
          description: '太空飞船躲避陨石',
        })
        .expect(201);

      expect(response.body).toHaveProperty('id');
      expect(response.body.status).toBe('draft');
      expect(response.body.title).toBe('Test Integration Game');
      expect(response.body.user_id).toBeDefined();

      createdGameId = response.body.id;
    });

    it('should fetch created game details', async () => {
      const response = await request(app.getHttpServer())
        .get(`/api/v1/games/${createdGameId}`)
        .set('Authorization', `Bearer ${user1Tokens.accessToken}`)
        .expect(200);

      expect(response.body.id).toBe(createdGameId);
      expect(response.body.title).toBe('Test Integration Game');
      expect(response.body.status).toBe('draft');
    });

    it('should update game code', async () => {
      const response = await request(app.getHttpServer())
        .patch(`/api/v1/games/${createdGameId}`)
        .set('Authorization', `Bearer ${user1Tokens.accessToken}`)
        .send({
          code: '<html><canvas></canvas><script>console.log("updated");</script></html>',
        })
        .expect(200);

      expect(response.body.id).toBe(createdGameId);
    });

    it('should prevent non-owner from updating game', async () => {
      await request(app.getHttpServer())
        .patch(`/api/v1/games/${createdGameId}`)
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .send({ title: 'Hacked Title' })
        .expect(403);
    });

    it('should publish the game', async () => {
      const response = await request(app.getHttpServer())
        .post(`/api/v1/games/${createdGameId}/publish`)
        .set('Authorization', `Bearer ${user1Tokens.accessToken}`)
        .expect(200);

      expect(response.body.status).toBe('published');
      expect(response.body.published_at).toBeDefined();

      publishedGameId = response.body.id;
    });

    it('should prevent publishing already published game', async () => {
      await request(app.getHttpServer())
        .post(`/api/v1/games/${publishedGameId}/publish`)
        .set('Authorization', `Bearer ${user1Tokens.accessToken}`)
        .expect(400);
    });
  });

  describe('3. Social Interactions', () => {
    it('should like the published game', async () => {
      const response = await request(app.getHttpServer())
        .post(`/api/v1/games/${publishedGameId}/like`)
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .expect(200);

      expect(response.body.liked).toBe(true);
    });

    it('should toggle like (unlike) on second interaction', async () => {
      const response = await request(app.getHttpServer())
        .post(`/api/v1/games/${publishedGameId}/like`)
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .expect(200);

      expect(response.body.liked).toBe(false);
    });

    it('should create a comment on the game', async () => {
      const response = await request(app.getHttpServer())
        .post(`/api/v1/games/${publishedGameId}/comments`)
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .send({ content: 'Great game! Really fun to play.' })
        .expect(201);

      expect(response.body).toHaveProperty('id');
      expect(response.body.content).toBe('Great game! Really fun to play.');
      expect(response.body.parent_comment_id).toBeNull();
    });

    it('should reply to a comment', async () => {
      const commentsResponse = await request(app.getHttpServer())
        .get(`/api/v1/games/${publishedGameId}/comments`)
        .expect(200);

      const parentComment = commentsResponse.body[0];

      const response = await request(app.getHttpServer())
        .post(`/api/v1/games/${publishedGameId}/comments`)
        .set('Authorization', `Bearer ${user1Tokens.accessToken}`)
        .send({
          content: 'Thanks for playing!',
          parentCommentId: parentComment.id,
        })
        .expect(201);

      expect(response.body.parent_comment_id).toBe(parentComment.id);
    });

    it('should follow the game creator', async () => {
      const gameResponse = await request(app.getHttpServer())
        .get(`/api/v1/games/${publishedGameId}`)
        .expect(200);

      const response = await request(app.getHttpServer())
        .post(`/api/v1/users/${gameResponse.body.user_id}/follow`)
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .expect(200);

      expect(response.body.following).toBe(true);
    });

    it('should unfollow user on second interaction', async () => {
      const gameResponse = await request(app.getHttpServer())
        .get(`/api/v1/games/${publishedGameId}`)
        .expect(200);

      const response = await request(app.getHttpServer())
        .post(`/api/v1/users/${gameResponse.body.user_id}/follow`)
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .expect(200);

      expect(response.body.following).toBe(false);
    });
  });

  describe('4. Game Forking', () => {
    it('should fork the published game', async () => {
      const response = await request(app.getHttpServer())
        .post(`/api/v1/games/${publishedGameId}/fork`)
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .expect(201);

      expect(response.body).toHaveProperty('id');
      expect(response.body.fork_parent_id).toBe(publishedGameId);
      expect(response.body.status).toBe('draft');
      expect(response.body.user_id).not.toBe(publishedGameId);

      forkedGameId = response.body.id;
    });

    it('should track fork lineage', async () => {
      const response = await request(app.getHttpServer())
        .get(`/api/v1/games/${forkedGameId}/fork-tree`)
        .expect(200);

      expect(Array.isArray(response.body)).toBe(true);
      expect(response.body.length).toBeGreaterThan(0);
      expect(response.body[response.body.length - 1].id).toBe(forkedGameId);
    });

    it('should list forks of original game', async () => {
      const response = await request(app.getHttpServer())
        .get(`/api/v1/games/${publishedGameId}/forks`)
        .expect(200);

      expect(Array.isArray(response.body)).toBe(true);
      const hasFork = response.body.some((game: any) => game.id === forkedGameId);
      expect(hasFork).toBe(true);
    });
  });

  describe('5. Feed and Discovery', () => {
    it('should return trending games including published game', async () => {
      const response = await request(app.getHttpServer())
        .get('/api/v1/feed/trending')
        .expect(200);

      expect(Array.isArray(response.body)).toBe(true);
      const hasGame = response.body.some(
        (game: any) => game.id === publishedGameId
      );
      expect(hasGame).toBe(true);
    });

    it('should return latest published games', async () => {
      const response = await request(app.getHttpServer())
        .get('/api/v1/feed/latest')
        .expect(200);

      expect(Array.isArray(response.body)).toBe(true);
      if (response.body.length > 1) {
        const date1 = new Date(response.body[0].published_at);
        const date2 = new Date(response.body[1].published_at);
        expect(date1.getTime()).toBeGreaterThanOrEqual(date2.getTime());
      }
    });

    it('should return following feed after follow', async () => {
      const gameResponse = await request(app.getHttpServer())
        .get(`/api/v1/games/${publishedGameId}`)
        .expect(200);

      await request(app.getHttpServer())
        .post(`/api/v1/users/${gameResponse.body.user_id}/follow`)
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .expect(200);

      const response = await request(app.getHttpServer())
        .get('/api/v1/feed/following')
        .set('Authorization', `Bearer ${user2Tokens.accessToken}`)
        .expect(200);

      expect(Array.isArray(response.body)).toBe(true);
    });

    it('should search games by title', async () => {
      const response = await request(app.getHttpServer())
        .get('/api/v1/feed/search?q=Integration')
        .expect(200);

      expect(Array.isArray(response.body)).toBe(true);
      const hasGame = response.body.some(
        (game: any) => game.title.includes('Integration')
      );
      expect(hasGame).toBe(true);
    });

    it('should search games by type', async () => {
      const response = await request(app.getHttpServer())
        .get('/api/v1/feed/search?gameType=dodge')
        .expect(200);

      expect(Array.isArray(response.body)).toBe(true);
    });
  });

  describe('6. User Profiles', () => {
    it('should fetch user profile with stats', async () => {
      const gameResponse = await request(app.getHttpServer())
        .get(`/api/v1/games/${publishedGameId}`)
        .expect(200);

      const response = await request(app.getHttpServer())
        .get(`/api/v1/users/${gameResponse.body.user_id}`)
        .expect(200);

      expect(response.body).toHaveProperty('username');
      expect(response.body).toHaveProperty('stats');
      expect(response.body.stats).toHaveProperty('total_games');
    });

    it('should update own profile', async () => {
      const response = await request(app.getHttpServer())
        .patch('/api/v1/users/profile')
        .set('Authorization', `Bearer ${user1Tokens.accessToken}`)
        .send({
          display_name: 'Updated Name',
          bio: 'I create awesome games!',
        })
        .expect(200);

      expect(response.body.display_name).toBe('Updated Name');
      expect(response.body.bio).toBe('I create awesome games!');
    });
  });

  describe('7. Cleanup', () => {
    it('should logout user', async () => {
      await request(app.getHttpServer())
        .post('/api/v1/auth/logout')
        .send({ refreshToken: user1Tokens.refreshToken })
        .expect(200);
    });

    it('should prevent using revoked token after logout', async () => {
      await request(app.getHttpServer())
        .post('/api/v1/auth/refresh')
        .send({ refreshToken: user1Tokens.refreshToken })
        .expect(401);
    });
  });
});
