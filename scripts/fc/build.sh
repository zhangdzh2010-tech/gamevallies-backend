#!/usr/bin/env bash
set -euo pipefail
# BuildKit exports ZIP files only. No registry login or push.
mkdir -p fc-packages
docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.node-package --build-arg SERVICE=game-service --output type=local,dest=fc-packages .
docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.ai-package --output type=local,dest=fc-packages .
docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.gateway-package --output type=local,dest=fc-packages .
mv fc-packages/gateway.zip fc-packages/content.zip
