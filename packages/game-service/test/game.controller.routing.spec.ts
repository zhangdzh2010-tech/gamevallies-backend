import { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import request from 'supertest';
import { CreatorReputationService } from '../src/game/creator-reputation.service';
import { GameController } from '../src/game/game.controller';
import { GameService } from '../src/game/game.service';

describe('GameController routing', () => {
  let app: INestApplication;
  let gameService: {
    findById: jest.Mock;
  };

  beforeAll(async () => {
    gameService = {
      findById: jest.fn(),
    };

    const moduleRef = await Test.createTestingModule({
      controllers: [GameController],
      providers: [
        {
          provide: GameService,
          useValue: gameService,
        },
        {
          provide: CreatorReputationService,
          useValue: {
            getReputation: jest.fn(),
          },
        },
      ],
    }).compile();

    app = moduleRef.createNestApplication();
    app.setGlobalPrefix('api/v1');
    await app.init();
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('serves game types from the static route instead of treating it as a game id', async () => {
    const response = await request(app.getHttpServer())
      .get('/api/v1/games/game-types')
      .expect(200);

    expect(response.body).toEqual(
      expect.objectContaining({
        code: 0,
        data: ['casual', 'puzzle', 'education'],
      }),
    );
    expect(gameService.findById).not.toHaveBeenCalled();
  });
});
