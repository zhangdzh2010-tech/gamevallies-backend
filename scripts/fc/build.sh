#!/usr/bin/env bash
set -euo pipefail
# BuildKit exports ZIP files only. No registry login or push.
mkdir -p fc-packages
for service in user-service game-service social-service feed-service; do
  docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.node-package --build-arg SERVICE="$service" --output type=local,dest=fc-packages .
done
docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.ai-package --output type=local,dest=fc-packages .
docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.gateway-package --output type=local,dest=fc-packages .
cp fc-packages/gateway.zip fc-packages/content.zip
