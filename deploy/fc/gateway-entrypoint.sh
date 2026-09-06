#!/bin/sh
set -eu
: "${FC_INTERNAL_TOKEN:?Missing internal token}" "${GAME_UPSTREAM:?Missing game upstream}"
case "$FC_INTERNAL_TOKEN" in *[!a-f0-9]*|'') exit 2;; esac
[ "${#FC_INTERNAL_TOKEN}" -eq 64 ] || exit 2
for key in GAME_UPSTREAM USER_UPSTREAM AI_UPSTREAM FRONTEND_UPSTREAM; do
  eval "value=\${$key:-}"
  [ -z "$value" ] && continue
  echo "$value" | grep -Eq '^https://[a-zA-Z0-9.-]+$' || exit 2
done
export FC_DNS_RESOLVER=$(awk '/^nameserver / { print $2; exit }' /etc/resolv.conf)
case "${GATEWAY_MODE:-app}" in
  app) : "${USER_UPSTREAM:?}" "${AI_UPSTREAM:?}" "${FRONTEND_UPSTREAM:?}"; template=app;;
  content) template=content;;
  *) exit 2;;
esac
# Only expand deployment fields, preserving Nginx request variables.
envsubst '${FC_INTERNAL_TOKEN} ${GAME_UPSTREAM} ${USER_UPSTREAM} ${AI_UPSTREAM} ${FRONTEND_UPSTREAM} ${FC_DNS_RESOLVER}' < /opt/fc/$template.template > /etc/nginx/conf.d/default.conf
nginx -t
