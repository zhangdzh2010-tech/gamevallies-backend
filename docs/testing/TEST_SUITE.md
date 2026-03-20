# PlayForge Backend Comprehensive Test Suite

This document describes the complete test suite for PlayForge backend services.

## Overview

The test suite covers all backend services with comprehensive unit tests, service tests, and integration tests:

- **User Service Tests** (3 files) - Authentication, user management
- **Game Service Tests** (3 files) - Game creation, publishing, forking, WebSocket
- **Social Service Tests** (4 files) - Likes, follows, comments, notifications
- **Feed Service Tests** (2 files) - Trending, latest, search functionality
- **AI Engine Tests** (4 files - Python) - Intent parsing, code generation, QA validation
- **Integration Tests** (1 file) - Full user flow across all services

## Test Files Location

```
/sessions/magical-gifted-pascal/mnt/willgame/gamevallies-backend/

packages/
├── user-service/test/
│   ├── auth.service.spec.ts         # Auth service tests
│   ├── user.service.spec.ts         # User CRUD tests
│   └── auth.controller.spec.ts      # Auth endpoints tests
├── game-service/test/
│   ├── game.service.spec.ts         # Game CRUD tests
│   ├── fork.service.spec.ts         # Game forking tests
│   └── websocket.gateway.spec.ts    # WebSocket tests
├── social-service/test/
│   ├── like.service.spec.ts         # Like/unlike tests
│   ├── follow.service.spec.ts       # Follow/unfollow tests
│   ├── comment.service.spec.ts      # Comment CRUD tests
│   └── notification.service.spec.ts # Notification tests
├── feed-service/test/
│   ├── feed.service.spec.ts         # Trending/Latest/Following feed
│   └── search.service.spec.ts       # Search functionality
└── ai-engine/tests/
    ├── test_intent_parser.py        # Game type detection
    ├── test_template_engine.py      # Code template matching
    ├── test_qa_pipeline.py          # Code validation
    └── test_code_generator.py       # Code generation tests

test/
└── integration/
    └── full-flow.spec.ts             # End-to-end flow test
```

## Test Summary

### 1. User Service Tests

#### auth.service.spec.ts (6 test suites, 20+ tests)
Tests JWT authentication, registration, login, token refresh, and logout.

**Key Test Cases:**
- ✓ Register: successful, duplicate username, duplicate email, weak password
- ✓ Login: successful, wrong password, user not found
- ✓ RefreshToken: valid refresh, expired token, revoked token
- ✓ GenerateTokens: JWT payload verification
- ✓ Logout: token revocation

**Mocking:**
- PrismaService (database operations)
- JwtService (token generation/verification)
- ConfigService (environment variables)

**Run:**
```bash
cd packages/user-service
npm test -- test/auth.service.spec.ts
```

#### user.service.spec.ts (4 test suites, 12+ tests)
Tests user CRUD operations and profile management.

**Key Test Cases:**
- ✓ FindById: found user, not found
- ✓ GetProfile: with stats, user not found
- ✓ UpdateProfile: success, user not found
- ✓ SearchUsers: matching results, no results, pagination

**Run:**
```bash
cd packages/user-service
npm test -- test/user.service.spec.ts
```

#### auth.controller.spec.ts (4 test suites, 16+ tests)
Tests authentication HTTP endpoints with proper status codes.

**Key Test Cases:**
- ✓ POST /api/v1/auth/register: 201 success, 400 validation, 409 conflict
- ✓ POST /api/v1/auth/login: 200 success, 401 invalid
- ✓ POST /api/v1/auth/refresh: 200 success, 401 expired
- ✓ POST /api/v1/auth/logout: 200 success, 401 unauthorized

**Run:**
```bash
cd packages/user-service
npm test -- test/auth.controller.spec.ts
```

### 2. Game Service Tests

#### game.service.spec.ts (5 test suites, 15+ tests)
Tests game creation, publishing, iteration, and play tracking.

**Key Test Cases:**
- ✓ Create: successful game creation, draft status
- ✓ FindById: found, not found
- ✓ Publish: successful publish, not owner (403), invalid status
- ✓ Iterate: successful iteration, ownership check
- ✓ GetPlayData: increment play count

**Mocking:**
- PrismaService
- HttpService (AI Engine calls)

**Run:**
```bash
cd packages/game-service
npm test -- test/game.service.spec.ts
```

#### fork.service.spec.ts (3 test suites, 10+ tests)
Tests game forking functionality and fork tree tracking.

**Key Test Cases:**
- ✓ ForkGame: successful fork, source not found, depth tracking
- ✓ GetForks: paginated results
- ✓ GetForkTree: recursive lineage traversal

**Run:**
```bash
cd packages/game-service
npm test -- test/fork.service.spec.ts
```

#### websocket.gateway.spec.ts (3 test suites, 12+ tests)
Tests WebSocket authentication and real-time events.

**Key Test Cases:**
- ✓ HandleConnection: valid JWT, invalid JWT rejection
- ✓ EmitToUser: message delivery to specific user
- ✓ EmitGenerationProgress: progress event formatting

**Run:**
```bash
cd packages/game-service
npm test -- test/websocket.gateway.spec.ts
```

### 3. Social Service Tests

#### like.service.spec.ts (1 test suite, 5 tests)
Tests like/unlike functionality with atomic counter updates.

**Key Test Cases:**
- ✓ Like: first like creates interaction
- ✓ Like: second like removes (toggle)
- ✓ Atomic counter update on games table

**Run:**
```bash
cd packages/social-service
npm test -- test/like.service.spec.ts
```

#### follow.service.spec.ts (4 test suites, 12+ tests)
Tests follow/unfollow and follower/following counts.

**Key Test Cases:**
- ✓ Follow/Unfollow: toggle relationship
- ✓ Follower/Following: count updates
- ✓ GetFollowers: paginated results with user data

**Run:**
```bash
cd packages/social-service
npm test -- test/follow.service.spec.ts
```

#### comment.service.spec.ts (3 test suites, 10+ tests)
Tests comment creation, retrieval, and deletion.

**Key Test Cases:**
- ✓ CreateComment: root comment, nested reply
- ✓ GetComments: with nested structure
- ✓ DeleteComment: owner can delete, non-owner forbidden

**Run:**
```bash
cd packages/social-service
npm test -- test/comment.service.spec.ts
```

#### notification.service.spec.ts (4 test suites, 15+ tests)
Tests notification creation and management.

**Key Test Cases:**
- ✓ CreateNotification: various types (like, comment, follow, fork)
- ✓ MarkAsRead: single and batch operations
- ✓ GetUnreadCount: accurate counts
- ✓ GetNotifications: paginated with relationships

**Run:**
```bash
cd packages/social-service
npm test -- test/notification.service.spec.ts
```

### 4. Feed Service Tests

#### feed.service.spec.ts (3 test suites, 15+ tests)
Tests feed generation with Wilson score and time decay.

**Key Test Cases:**
- ✓ GetTrending: Wilson score calculation, time decay, pagination
- ✓ GetLatest: sorted by publishedAt descending
- ✓ GetFollowing: only followed users' games

**Advanced Features Tested:**
- Wilson score confidence interval (p-hat formula)
- Time decay factor (24-hour half-life)
- Pagination with offset/limit

**Run:**
```bash
cd packages/feed-service
npm test -- test/feed.service.spec.ts
```

#### search.service.spec.ts (3 test suites, 10+ tests)
Tests full-text search and filtering.

**Key Test Cases:**
- ✓ Search: by query, by tags, by gameType, pagination
- ✓ SearchByGameType: filter by type
- ✓ SearchByTags: filter by all tags
- ✓ Empty results: proper empty array handling

**Run:**
```bash
cd packages/feed-service
npm test -- test/search.service.spec.ts
```

### 5. AI Engine Tests (Python)

#### test_intent_parser.py (10+ tests)
Tests game description parsing for type detection.

**Key Test Cases:**
- ✓ Parse Chinese: "太空飞船躲避陨石" → dodge
- ✓ Parse Chinese: "接水果的休闲游戏" → catcher
- ✓ Parse Chinese: "节奏方块音乐游戏" → rhythm
- ✓ Parse Chinese: "迷宫逃脱限时" → maze
- ✓ Parse Chinese: "平台跳跃弹跳" → platformer
- ✓ Unknown description: reasonable default
- ✓ Confidence range: always 0-1

**Implementation:**
- Pattern matching for keywords in Chinese/English
- Keyword scoring system
- Confidence calculation

**Run:**
```bash
cd packages/ai-engine
pytest tests/test_intent_parser.py -v
```

#### test_template_engine.py (10+ tests)
Tests template selection and code generation.

**Key Test Cases:**
- ✓ Match: correct template for each game_type
- ✓ Generate: parameter substitution (title)
- ✓ No match: unknown types
- ✓ HTML validity: DOCTYPE, canvas, script tags

**Run:**
```bash
cd packages/ai-engine
pytest tests/test_template_engine.py -v
```

#### test_qa_pipeline.py (15+ tests)
Tests code validation and security checks.

**Key Test Cases:**
- ✓ ValidateSyntax: valid HTML/JS structure
- ✓ ValidateSecurity: detect eval(), fetch(), localStorage
- ✓ ValidateSize: under/over 500KB limit
- ✓ ValidateStructure: canvas init, game loop, events
- ✓ ValidateAll: comprehensive validation

**Security Checks:**
- eval() detection
- fetch() and XMLHttpRequest detection
- localStorage usage warnings
- Code size validation

**Run:**
```bash
cd packages/ai-engine
pytest tests/test_qa_pipeline.py -v
```

#### test_code_generator.py (12+ tests)
Tests end-to-end code generation pipeline.

**Key Test Cases:**
- ✓ Generate: returns valid HTML for each type
- ✓ GenerateWithFeedback: code modification
- ✓ Confidence scores: included in result
- ✓ Validation: included in result

**Feedback Modifications:**
- Speed: faster/slower
- Difficulty: easier/harder
- Colors: dark/bright theme
- Controls: keyboard/touch

**Run:**
```bash
cd packages/ai-engine
pytest tests/test_code_generator.py -v
```

### 6. Integration Test

#### full-flow.spec.ts (7 test suites, 40+ tests)
End-to-end test covering complete user journey.

**Complete Flow Tested:**
1. Register two users
2. Login and get tokens
3. Create game with AI description
4. Fetch game details
5. Update game code
6. Publish game
7. Like/unlike game
8. Create and reply to comments
9. Follow/unfollow user
10. Fork game
11. View trending feed
12. View following feed
13. Search games
14. Update profile
15. Logout and token revocation

**Run:**
```bash
npm run test:integration
```

## Running Tests

### Run All Tests
```bash
npm test
```

### Run Tests by Service
```bash
# User Service
cd packages/user-service
npm test

# Game Service
cd packages/game-service
npm test

# Social Service
cd packages/social-service
npm test

# Feed Service
cd packages/feed-service
npm test

# AI Engine
cd packages/ai-engine
pytest tests/ -v
```

### Run Specific Test File
```bash
# TypeScript
jest packages/user-service/test/auth.service.spec.ts

# Python
pytest packages/ai-engine/tests/test_intent_parser.py -v
```

### Run with Coverage
```bash
# All tests with coverage
npm run test:cov

# Specific service
cd packages/user-service
npm run test:cov

# Python
cd packages/ai-engine
pytest tests/ --cov=src --cov-report=html
```

### Watch Mode
```bash
npm run test:watch
```

## Test Setup Requirements

### Prerequisites
- Node.js 18+
- Python 3.9+
- npm or yarn
- pytest (for Python tests)

### Install Dependencies
```bash
# Root level
npm install

# Each service (if not using workspace)
cd packages/user-service && npm install
cd packages/game-service && npm install
cd packages/social-service && npm install
cd packages/feed-service && npm install

# AI Engine
cd packages/ai-engine
pip install -r requirements.txt
pip install pytest pytest-cov
```

## Test Coverage Goals

- **User Service**: 90%+ coverage
- **Game Service**: 85%+ coverage
- **Social Service**: 88%+ coverage
- **Feed Service**: 85%+ coverage
- **AI Engine**: 90%+ coverage

## Mocking Strategy

### TypeScript Tests
- **PrismaService**: Mocked with jest.fn() for all database operations
- **JwtService**: Mocked for token operations
- **HttpService**: Mocked for external API calls
- **ConfigService**: Mocked for environment variables
- **Socket.IO**: Mocked for WebSocket tests

### Python Tests
- Direct implementation of classes for testing
- No external dependencies mocked
- Focus on algorithm and logic verification

## Common Test Patterns

### Service Test Pattern
```typescript
describe('ServiceName', () => {
  let service: ServiceName;
  let dependency: DependencyService;

  const mockDependency = {
    method: jest.fn(),
  };

  beforeEach(async () => {
    const module = await Test.createTestingModule({
      providers: [
        ServiceName,
        { provide: DependencyService, useValue: mockDependency },
      ],
    }).compile();

    service = module.get<ServiceName>(ServiceName);
  });

  it('should test case', async () => {
    mockDependency.method.mockResolvedValueOnce(expectedValue);
    const result = await service.method();
    expect(result).toEqual(expectedValue);
  });
});
```

### Controller Test Pattern
```typescript
describe('ControllerName', () => {
  let app: INestApplication;
  let service: ServiceName;

  beforeEach(async () => {
    const module = await Test.createTestingModule({
      controllers: [ControllerName],
      providers: [{ provide: ServiceName, useValue: mockService }],
    }).compile();

    app = module.createNestApplication();
    await app.init();
  });

  it('should test endpoint', async () => {
    const response = await request(app.getHttpServer())
      .post('/api/endpoint')
      .send(data)
      .expect(200);
    expect(response.body).toEqual(expectedBody);
  });
});
```

## Continuous Integration

Add to your CI/CD pipeline:

```yaml
# .github/workflows/tests.yml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-node@v2
      - uses: actions/setup-python@v2
      
      - name: Install dependencies
        run: npm install
        
      - name: Run TypeScript tests
        run: npm test
        
      - name: Run Python tests
        run: |
          cd packages/ai-engine
          pytest tests/ -v
          
      - name: Upload coverage
        uses: codecov/codecov-action@v2
```

## Debugging Tests

### Run Single Test
```bash
jest --testNamePattern="should register a new user"
```

### Run with Debug Output
```bash
DEBUG=* npm test
```

### Run in Band (Sequential)
```bash
jest --runInBand
```

### Test Timeout
```typescript
it('slow test', async () => {
  // test code
}, 30000); // 30 second timeout
```

## Contributing Tests

When adding new features:

1. Write tests first (TDD approach recommended)
2. Ensure all tests pass: `npm test`
3. Check coverage: `npm run test:cov`
4. Follow existing test patterns
5. Update this documentation

## Test Maintenance

- Review failing tests immediately
- Update snapshots with `jest -u`
- Refactor tests when patterns change
- Keep test data realistic but minimal
- Use descriptive test names

## Troubleshooting

### Tests Timeout
- Increase timeout: `jest.setTimeout(10000)`
- Check for unresolved promises
- Verify mock implementations

### Import Errors
- Ensure path aliases configured in tsconfig
- Check for circular dependencies
- Verify file exists

### Mock Not Working
- Clear jest cache: `jest --clearCache`
- Verify mock is defined before test
- Check mock method names match

## Support

For test-related issues:
1. Check this documentation
2. Review existing test examples
3. Verify dependencies installed
4. Run `npm test -- --verbose`
5. Check error messages carefully

## Summary Statistics

- **Total Test Files**: 17
- **Total Test Cases**: 200+
- **Lines of Test Code**: 5000+
- **Services Covered**: 5 (user, game, social, feed, ai-engine)
- **Languages**: TypeScript (13 files), Python (4 files)
- **Target Coverage**: 85-90%

---

Last Updated: 2024-01-01
Maintained by: PlayForge Team
