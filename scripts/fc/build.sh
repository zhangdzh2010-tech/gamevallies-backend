#!/usr/bin/env bash
set -euo pipefail
# BuildKit exports ZIP files only. No registry login or push.
# Usage: build.sh [all|ai-engine|game-service|content]...
# FC_SERVICES=all|comma-separated is used when no positional targets are given.
# FC_BUILD_LIST_ONLY=1 prints resolved targets and exits (no Docker).
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"
if [ "$#" -gt 0 ]; then
  SELECTION=$(IFS=,; echo "$*")
else
  SELECTION="${FC_SERVICES:-all}"
fi
mapfile -t SERVICES < <(python3 -c 'import sys
from pathlib import Path
sys.path.insert(0, str(Path("scripts/fc").resolve()))
from deploy import parse_services
print("\n".join(parse_services(sys.argv[1])))
' "$SELECTION")
if [ "${#SERVICES[@]}" -eq 0 ]; then
  echo "No FC package targets selected" >&2
  exit 1
fi
echo "FC selected services: $(IFS=,; echo "${SERVICES[*]}")" >&2
if [ "${FC_BUILD_LIST_ONLY:-}" = 1 ]; then
  printf '%s\n' "${SERVICES[@]}"
  exit 0
fi
mkdir -p fc-packages
for service in "${SERVICES[@]}"; do
  case "$service" in
    game-service)
      docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.node-package --build-arg SERVICE=game-service --output type=local,dest=fc-packages .
      ;;
    ai-engine)
      docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.ai-package --output type=local,dest=fc-packages .
      ;;
    content)
      docker buildx build --platform linux/amd64 -f deploy/fc/Dockerfile.gateway-package --output type=local,dest=fc-packages .
      mv fc-packages/gateway.zip fc-packages/content.zip
      ;;
    *)
      echo "Unknown FC package target: $service" >&2
      exit 1
      ;;
  esac
done
