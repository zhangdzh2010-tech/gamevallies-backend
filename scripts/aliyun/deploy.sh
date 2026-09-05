#!/usr/bin/env bash
# Execute on a dedicated ECS runner. Credentials and runtime.env stay on the host.
set -Eeuo pipefail
umask 077
: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
: "${IMAGE_PREFIX:?Set IMAGE_PREFIX}"
: "${IMAGE_TAG:?Set IMAGE_TAG to a commit SHA}"
[[ "$DEPLOY_ROOT" =~ ^/[a-zA-Z0-9_/-]+$ && "$DEPLOY_ROOT" != / ]] || { echo 'Invalid deployment directory' >&2; exit 2; }
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{40}$ ]] || { echo 'Only full commit SHA image tags are accepted' >&2; exit 2; }
[[ "$IMAGE_PREFIX" =~ ^[a-zA-Z0-9.-]+(:[0-9]+)?/[a-z0-9_-]+$ ]] || { echo 'Invalid image registry/namespace' >&2; exit 2; }
script_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
project=gamevallies-backend
mkdir -p "$DEPLOY_ROOT/releases"
exec 9>"$DEPLOY_ROOT/deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }
export RUNTIME_ENV_FILE="$DEPLOY_ROOT/runtime.env"
[[ -s "$RUNTIME_ENV_FILE" ]] || { echo 'Provision runtime.env on ECS first' >&2; exit 2; }
# Reject the supplied template; never print values from the runtime file.
for key in DATABASE_URL REDIS_URL JWT_SECRET JWT_REFRESH_SECRET ADMIN_TOKEN; do
  grep -Eq "^${key}=.+" "$RUNTIME_ENV_FILE" || { echo "Missing runtime key: $key" >&2; exit 2; }
done
if grep -Eq 'REPLACE_WITH_|RDS_PRIVATE_HOST|REDIS_PRIVATE_HOST' "$RUNTIME_ENV_FILE"; then
  echo 'Replace placeholder runtime values before deployment' >&2; exit 2
fi
# Network creation is idempotent even when both repositories deploy concurrently.
docker network inspect gamevallies >/dev/null 2>&1 || docker network create gamevallies >/dev/null || docker network inspect gamevallies >/dev/null
release=$(mktemp -d "$DEPLOY_ROOT/releases/${IMAGE_TAG}.XXXXXX")
cp "$script_root/deploy/aliyun/compose.yml" "$release/compose.yml"
printf 'IMAGE_PREFIX=%s\nIMAGE_TAG=%s\nRUNTIME_ENV_FILE=%s\nGATEWAY_BIND=%s\n' "$IMAGE_PREFIX" "$IMAGE_TAG" "$RUNTIME_ENV_FILE" "${GATEWAY_BIND:-127.0.0.1}" > "$release/images.env"
# Unset exported interpolation variables so rollback reads the previous manifest.
unset IMAGE_PREFIX IMAGE_TAG RUNTIME_ENV_FILE GATEWAY_BIND
compose() { local target=$1; shift; docker compose --project-name "$project" --env-file "$target/images.env" -f "$target/compose.yml" "$@"; }
compose "$release" config --quiet
compose "$release" pull
previous=$(readlink -f "$DEPLOY_ROOT/current" || true)
rollback() {
  echo 'Deployment failed; restoring previous release when available.' >&2
  if [[ -n "$previous" && -f "$previous/compose.yml" ]]; then
    if compose "$previous" up -d --wait --wait-timeout 240; then
      echo 'Previous release restored.' >&2
    else
      echo 'ROLLBACK FAILED: inspect ECS containers immediately.' >&2
    fi
  else
    # First install has no previous release. Keep containers for diagnosis.
    echo 'No previous release exists; failed containers retained for diagnosis.' >&2
  fi
}
trap 'rollback; exit 1' ERR
compose "$release" up -d --wait --wait-timeout 240
ln -sfn "$release" "$DEPLOY_ROOT/current.next"
mv -Tf "$DEPLOY_ROOT/current.next" "$DEPLOY_ROOT/current"
trap - ERR
echo "Deployment healthy: $release"
