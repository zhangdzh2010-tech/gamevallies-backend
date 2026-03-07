# PlayForge Deployment Package

This repository contains **complete deployment infrastructure** for PlayForge, a comprehensive game development and streaming platform.

## 📦 What's Included

This deployment package contains everything needed to run PlayForge from local development through production:

### 1. **Comprehensive Deployment SOP** (2100+ lines)
- Chinese language guide for ops team
- 9 major sections covering all deployment scenarios
- Troubleshooting guide with 5 detailed problem-solving flows
- Quick reference commands

**📄 Location:** `docs/DEPLOYMENT_SOP.md`

### 2. **Kubernetes Production Infrastructure** (12 manifest files)
- Namespace, ConfigMap, Secret templates
- 5 microservice deployments (user, game, social, feed, AI)
- Database layer (PostgreSQL, MongoDB, Redis)
- Ingress with TLS and WebSocket support
- Horizontal Pod Autoscaler (HPA) for all services

**📁 Location:** `infrastructure/k8s/`

### 3. **CI/CD Pipeline** (850 lines)
- 8-stage GitHub Actions workflow
- Full test suite (unit, integration, e2e)
- Docker image building and pushing
- Canary deployment to production
- Automatic rollback on failures

**📄 Location:** `.github/workflows/ci.yml`

### 4. **Seed Data & Test Accounts** (280 lines TypeScript)
- 5 test users with different roles
- 10 sample games (6 JSX demo games included)
- Social relationships, comments, ratings
- Creator earnings records

**📄 Location:** `prisma/seed.ts`

### 5. **Environment Configuration Templates** (6 files)
- Root configuration with 70+ variables
- Service-specific configs for all 5 microservices
- Detailed comments for each variable

**📁 Location:** `.env.example` and `packages/*/`.env.example`

## 🚀 Quick Start

### Local Development (5 minutes)
```bash
# Setup
cp .env.example .env
docker compose up -d postgres mongo redis
npm install && npx prisma migrate dev
npx ts-node prisma/seed.ts

# Run
npm run dev:all

# Test
curl http://localhost:3000/health
```

### Docker Stack (10 minutes)
```bash
docker compose build
docker compose up -d
docker compose exec user-service npx ts-node prisma/seed.ts
open http://localhost:3000
```

### Kubernetes Production
```bash
kubectl apply -f infrastructure/k8s/
# ... then configure secrets and wait for rollout
```

See `docs/DEPLOYMENT_SOP.md` for detailed instructions.

## 📚 Documentation

| Document | Purpose | Length |
|----------|---------|--------|
| `DEPLOYMENT_SOP.md` | Complete ops guide (Chinese) | 2100 lines |
| `DEPLOYMENT_INDEX.md` | File inventory & reference | 500+ lines |
| `DEPLOYMENT_SUMMARY.md` | Quick overview | 200+ lines |
| `.env.example` | Config template | 160 lines |

## 🏗️ Architecture

```
┌─────────────────────────────────────────┐
│  Client Layer                           │
│  (Web, Mobile, Games)                   │
└────────────────┬────────────────────────┘
                 │
┌─────────────────▼────────────────────────┐
│  Ingress / Load Balancer                │
│  (Nginx, TLS, Rate Limiting)            │
└────────────────┬────────────────────────┘
                 │
     ┌───────────┼───────────┐
     │           │           │
┌────▼────┐ ┌────▼────┐ ┌───▼──────┐
│ User    │ │ Game    │ │ Social   │ ...
│ Service │ │ Service │ │ Service  │
└────┬────┘ └────┬────┘ └───┬──────┘
     │           │           │
┌────▼──────────┬▼──────────┬▼─────┐
│ PostgreSQL    │ MongoDB   │Redis │
│ (Primary DB)  │ (Game DB) │Cache │
└───────────────┴───────────┴──────┘
```

## 🎯 Services

| Service | Port | Role | Technology |
|---------|------|------|------------|
| user-service | 3000 | Auth, profiles | Node.js + Express |
| game-service | 3001 | Game catalog | Node.js + Express |
| social-service | 3002 | Social features | Node.js + Express |
| feed-service | 3003 | Notifications | Node.js + Express |
| ai-engine | 3005 | AI features | Python + FastAPI |

## 🗄️ Databases

| Database | Port | Data | Status |
|----------|------|------|--------|
| PostgreSQL | 5432 | Users, games metadata | K8s StatefulSet |
| MongoDB | 27017 | Game assets, analytics | K8s Deployment |
| Redis | 6379 | Cache, sessions | K8s Deployment |

## 🧪 Test Accounts

```
alice@playforge.com / password123   (Creator)
bob@playforge.com / password123     (User)
carol@playforge.com / password123   (Creator)
david@playforge.com / password123   (User)
emma@playforge.com / password123    (Creator)
```

## 📊 Deployment Strategies

### Development
- Local services with hot reload
- Shared database containers
- Seed data for testing

### Staging
- Kubernetes (3 replicas)
- Auto-deploy on git push
- Full feature testing

### Production
- Kubernetes (auto-scaling 2-10 replicas)
- Canary deployment (10% → 50% → 100%)
- Manual approval required
- Automatic rollback on failure

## 🔄 CI/CD Pipeline

```
Commit → Lint → Test Backend → Test AI Engine → Build Docker
                                                       ↓
                                    Security Scan → Push Images
                                                       ↓
                              ┌──────────────────────┴──────────────────┐
                              ↓                                         ↓
                        Deploy Staging                          Deploy Production
                     (develop branch)                           (main branch)
                                                            (requires approval)
```

## 🆘 Troubleshooting

For detailed troubleshooting steps:
1. See section 九 in `docs/DEPLOYMENT_SOP.md`
2. Common issues: Service startup, DB connection, WebSocket, AI engine timeout
3. Each issue has 3-5 diagnostic steps

## 🔒 Security

**Before Production Deployment:**
- Replace all `CHANGE_ME_*` values in K8s secrets
- Generate strong JWT secrets
- Configure TLS certificates
- Set up firewall rules
- Enable pod security policies
- Use private container registry

**Implemented Security Features:**
- TLS/HTTPS on all endpoints
- JWT authentication
- Rate limiting (1000 rps)
- CORS restrictions
- Network policies
- Pod security policies
- Secret encryption at rest

## 📈 Monitoring

Configured metrics for:
- HTTP requests (rate, errors, latency)
- Database connections and queries
- Cache hit ratio
- WebSocket connections
- AI API calls
- Custom business metrics (logins, game plays, earnings)

Integration with:
- Prometheus (metrics collection)
- Grafana (dashboards)
- Sentry (error tracking)

## 💡 Key Features

✅ **Multi-tier deployment** - Dev, Staging, Production
✅ **Infrastructure as Code** - All K8s configs included
✅ **Automated CI/CD** - Full GitHub Actions pipeline
✅ **Canary deployments** - Gradual rollout with monitoring
✅ **Auto-scaling** - HPA configured for all services
✅ **Database migrations** - Prisma + seed data
✅ **Comprehensive docs** - Chinese SOP + English guides
✅ **Security focused** - Secrets, TLS, rate limiting
✅ **Monitoring ready** - Prometheus + Grafana configs
✅ **Rollback capability** - Automatic on failure

## 📞 Support

- **Deployment Questions:** See `docs/DEPLOYMENT_SOP.md`
- **File Reference:** See `DEPLOYMENT_INDEX.md`
- **Quick Overview:** See `DEPLOYMENT_SUMMARY.md`
- **Team Email:** devops@playforge.com
- **Slack:** #playforge-deployments

## 📅 File Manifest

```
Root Directory:
├── .env.example                                    # Main config template
├── .github/workflows/ci.yml                        # CI/CD pipeline
├── docs/DEPLOYMENT_SOP.md                          # Complete ops guide (2100 lines)
├── DEPLOYMENT_INDEX.md                             # File index
├── DEPLOYMENT_SUMMARY.md                           # Quick overview
├── README_DEPLOYMENT.md                            # This file
├── infrastructure/k8s/                             # K8s manifests
│   ├── namespace.yaml, configmap.yaml, secret.yaml
│   ├── user-service.yaml, game-service.yaml, etc.
│   ├── postgres.yaml, mongo.yaml, redis.yaml
│   └── ingress.yaml
├── prisma/seed.ts                                  # Seed data
└── packages/*/                                     # Service-specific configs
    ├── user-service/.env.example
    ├── game-service/.env.example
    ├── social-service/.env.example
    ├── feed-service/.env.example
    └── ai-engine/.env.example
```

## ✅ Pre-Deployment Checklist

- [ ] Review `docs/DEPLOYMENT_SOP.md` section 五
- [ ] Copy and configure all .env.example files
- [ ] Test locally with Docker Compose
- [ ] Run seed.ts and verify data
- [ ] Check all services start successfully
- [ ] Verify health endpoints
- [ ] Test main features with test accounts
- [ ] Review K8s manifests for your environment
- [ ] Configure secrets before K8s deployment
- [ ] Set up monitoring (Prometheus/Grafana)
- [ ] Document any customizations

---

**Version:** 1.0.0 (January 15, 2024)
**Status:** Production Ready
**Maintained by:** DevOps Team

For comprehensive deployment guide, see: `docs/DEPLOYMENT_SOP.md`
