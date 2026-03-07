# Deployment Guide

## Prerequisites

- Node.js 20+
- PostgreSQL 13+
- Docker & Docker Compose (for containerized deployment)
- Kubernetes cluster (for K8s deployment)

## Environment Variables

### Required
```env
DATABASE_URL=postgresql://user:password@host:5432/playforge_user_db
JWT_SECRET=<strong-random-32-char-string>
JWT_REFRESH_SECRET=<strong-random-32-char-string>
```

### Optional
```env
NODE_ENV=production
PORT=3001
JWT_EXPIRES_IN=15m
JWT_REFRESH_EXPIRES_IN=7d
REDIS_URL=redis://localhost:6379
CORS_ORIGIN=https://playforge.com
CORS_CREDENTIALS=true
```

## Local Deployment

### 1. Setup Database

```bash
# Create database
createdb playforge_user_db

# Run migrations
npx prisma migrate deploy

# Seed data (if available)
npx prisma db seed
```

### 2. Install Dependencies

```bash
npm install --omit=dev
```

### 3. Build Application

```bash
npm run build
```

### 4. Start Service

```bash
NODE_ENV=production npm start
```

## Docker Deployment

### Build Image

```bash
docker build -t playforge-user-service:1.0.0 .
```

### Run Container

```bash
docker run -d \
  --name playforge-user-service \
  -p 3001:3001 \
  -e DATABASE_URL="postgresql://user:password@db:5432/playforge_user_db" \
  -e JWT_SECRET="your-secret-key" \
  -e JWT_REFRESH_SECRET="your-refresh-secret-key" \
  -e NODE_ENV=production \
  playforge-user-service:1.0.0
```

### Using Docker Compose

```bash
# Copy environment file
cp .env.example .env

# Edit .env with production values

# Start services
docker-compose -f docker-compose.yml up -d

# View logs
docker-compose logs -f user-service

# Stop services
docker-compose down
```

## Kubernetes Deployment

### 1. Build and Push Image

```bash
# Build image
docker build -t your-registry/playforge-user-service:1.0.0 .

# Push to registry
docker push your-registry/playforge-user-service:1.0.0
```

### 2. Create Namespace

```bash
kubectl create namespace playforge
```

### 3. Create ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: user-service-config
  namespace: playforge
data:
  NODE_ENV: production
  PORT: "3001"
  JWT_EXPIRES_IN: 15m
  JWT_REFRESH_EXPIRES_IN: 7d
  CORS_ORIGIN: https://playforge.com
  CORS_CREDENTIALS: "true"
```

### 4. Create Secrets

```bash
kubectl create secret generic user-service-secrets \
  --from-literal=DATABASE_URL="postgresql://user:password@postgres:5432/playforge_user_db" \
  --from-literal=JWT_SECRET="your-secret-key" \
  --from-literal=JWT_REFRESH_SECRET="your-refresh-secret-key" \
  -n playforge
```

### 5. Create Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: playforge-user-service
  namespace: playforge
spec:
  replicas: 3
  selector:
    matchLabels:
      app: playforge-user-service
  template:
    metadata:
      labels:
        app: playforge-user-service
    spec:
      containers:
      - name: user-service
        image: your-registry/playforge-user-service:1.0.0
        imagePullPolicy: Always
        ports:
        - containerPort: 3001
        envFrom:
        - configMapRef:
            name: user-service-config
        - secretRef:
            name: user-service-secrets
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health/live
            port: 3001
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 3001
          initialDelaySeconds: 10
          periodSeconds: 5
          timeoutSeconds: 3
          failureThreshold: 3
```

### 6. Create Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: user-service
  namespace: playforge
spec:
  type: ClusterIP
  ports:
  - port: 3001
    targetPort: 3001
    protocol: TCP
  selector:
    app: playforge-user-service
```

### 7. Deploy

```bash
# Apply configurations
kubectl apply -f configmap.yaml
kubectl apply -f secrets.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# Check deployment status
kubectl get deployment -n playforge
kubectl get pods -n playforge
kubectl logs -n playforge -l app=playforge-user-service

# Scale deployment
kubectl scale deployment playforge-user-service --replicas=5 -n playforge
```

## AWS ECS Deployment

### 1. Create ECR Repository

```bash
aws ecr create-repository --repository-name playforge-user-service
```

### 2. Push Image

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t playforge-user-service:1.0.0 .
docker tag playforge-user-service:1.0.0 <account-id>.dkr.ecr.us-east-1.amazonaws.com/playforge-user-service:1.0.0
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/playforge-user-service:1.0.0
```

### 3. Create ECS Task Definition

```json
{
  "family": "playforge-user-service",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "containerDefinitions": [
    {
      "name": "user-service",
      "image": "<account-id>.dkr.ecr.us-east-1.amazonaws.com/playforge-user-service:1.0.0",
      "portMappings": [
        {
          "containerPort": 3001,
          "hostPort": 3001,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "NODE_ENV",
          "value": "production"
        },
        {
          "name": "PORT",
          "value": "3001"
        }
      ],
      "secrets": [
        {
          "name": "DATABASE_URL",
          "valueFrom": "arn:aws:secretsmanager:region:account:secret:user-service-db-url"
        },
        {
          "name": "JWT_SECRET",
          "valueFrom": "arn:aws:secretsmanager:region:account:secret:jwt-secret"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/playforge-user-service",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:3001/health || exit 1"],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ],
  "executionRoleArn": "arn:aws:iam::account:role/ecsTaskExecutionRole"
}
```

### 4. Create ECS Service

```bash
aws ecs create-service \
  --cluster playforge \
  --service-name user-service \
  --task-definition playforge-user-service:1 \
  --desired-count 3 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=arn:aws:elasticloadbalancing:...,containerName=user-service,containerPort=3001"
```

## Database Migrations in Production

### Before Deployment
```bash
# Test migrations locally
npm run build
npx prisma migrate deploy
```

### Deployment Steps
```bash
# 1. Backup database
pg_dump playforge_user_db > backup.sql

# 2. Run migrations
npx prisma migrate deploy

# 3. Verify migrations
npx prisma migrate status

# 4. Redeploy application
npm run build
npm start
```

## Monitoring & Logging

### Application Logs

```bash
# Docker
docker logs playforge-user-service

# Kubernetes
kubectl logs -f deployment/playforge-user-service -n playforge

# AWS ECS
aws logs tail /ecs/playforge-user-service --follow
```

### Health Checks

```bash
# Service health
curl http://localhost:3001/health

# Readiness check
curl http://localhost:3001/health/ready

# Liveness check
curl http://localhost:3001/health/live
```

### Metrics

Monitor these metrics in production:
- Request latency
- Error rate
- Database connection pool usage
- Memory and CPU usage
- Token validation latency

## Backup Strategy

### Daily Backups
```bash
# Automated backup script
*/0 2 * * * pg_dump playforge_user_db | gzip > /backups/db_$(date +\%Y\%m\%d).sql.gz
```

### Verify Backups
```bash
# Test restore
createdb playforge_user_db_test
pg_restore -d playforge_user_db_test backup.sql
dropdb playforge_user_db_test
```

## Rollback Procedure

```bash
# 1. Identify good version
docker images playforge-user-service

# 2. Rollback deployment
docker service update --image playforge-user-service:previous playforge-user-service

# Or for Kubernetes
kubectl rollout undo deployment/playforge-user-service -n playforge

# 3. Verify
curl http://localhost:3001/health
```

## Performance Tuning

### Database Connection Pool
```typescript
// src/prisma/prisma.service.ts
const url = `${DATABASE_URL}?schema=public&connection_limit=20`;
```

### Node.js Clustering
```bash
# Use PM2
pm2 start dist/main.js -i max --name user-service
```

### Caching Strategy
- Implement Redis for session caching
- Cache user profile data
- Cache search results

## Security Checklist

- [ ] Rotate JWT secrets regularly
- [ ] Use HTTPS/TLS for all connections
- [ ] Enable database encryption
- [ ] Implement rate limiting
- [ ] Add request size limits
- [ ] Validate all input data
- [ ] Use strong passwords
- [ ] Enable CORS selectively
- [ ] Monitor for security threats
- [ ] Keep dependencies updated

## Troubleshooting

### Service Won't Start
```bash
# Check logs
docker logs user-service
kubectl logs -f pod/playforge-user-service

# Check environment variables
env | grep DATABASE_URL
```

### Database Connection Failed
```bash
# Test connection
psql $DATABASE_URL -c "SELECT 1"

# Check service health
curl http://localhost:3001/health/ready
```

### High Memory Usage
```bash
# Monitor memory
docker stats playforge-user-service

# Check for memory leaks
node --inspect dist/main.js
```

## Support

For deployment issues:
1. Check logs
2. Verify environment variables
3. Test database connectivity
4. Review health endpoints
5. Check resource limits
6. Contact DevOps team
