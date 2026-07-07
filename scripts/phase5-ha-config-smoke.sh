#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

command -v docker >/dev/null 2>&1 || {
  printf 'docker is required for docker compose config\n' >&2
  exit 1
}
command -v helm >/dev/null 2>&1 || {
  printf 'helm is required for Phase 5 HA config smoke\n' >&2
  exit 1
}

rendered="$(mktemp)"
failure_log="$(mktemp)"
compose_config="$(mktemp)"
created_env_file="0"

cleanup() {
  if [ "$created_env_file" = "1" ]; then
    rm -f .env
  fi
  rm -f "$rendered" "$failure_log" "$compose_config"
}
trap cleanup EXIT

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-janusgate-ha-smoke-postgres}"
export SECRET_KEY="${SECRET_KEY:-janusgate-ha-smoke-secret-key-janusgate-ha-smoke-secret-key-32}"
export DATABASE_READ_REPLICA_URL="${DATABASE_READ_REPLICA_URL:-postgresql+asyncpg://janusgate-reader:janusgate-ha-smoke-postgres@postgres-read:5432/janusgate}"

if [ ! -f .env ]; then
  created_env_file="1"
  {
    echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"
    echo "SECRET_KEY=$SECRET_KEY"
    echo "DATABASE_READ_REPLICA_URL=$DATABASE_READ_REPLICA_URL"
  } >.env
fi

docker compose config >"$compose_config"
grep -q 'DATABASE_READ_REPLICA_URL' "$compose_config"

if helm template janusgate deploy/helm/janusgate \
  --set secret.secretKey="$SECRET_KEY" \
  --set secret.databaseUrl='postgresql+asyncpg://janusgate:janusgate-ha-smoke-postgres@postgres:5432/janusgate' \
  --set autoscaling.enabled=true \
  --set config.sessionConnectionTokenStore=memory \
  >"$rendered" 2>"$failure_log"; then
  printf 'Phase 5 HA config smoke expected memory-token HPA render to fail\n' >&2
  exit 1
fi
grep -q 'autoscaling requires config.sessionConnectionTokenStore=redis' "$failure_log"

helm template janusgate deploy/helm/janusgate \
  --set secret.secretKey="$SECRET_KEY" \
  --set secret.databaseUrl='postgresql+asyncpg://janusgate:janusgate-ha-smoke-postgres@postgres:5432/janusgate' \
  --set secret.databaseReadReplicaUrl="$DATABASE_READ_REPLICA_URL" \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=2 \
  --set autoscaling.maxReplicas=4 \
  --set config.sessionConnectionTokenStore=redis \
  --set config.redisMode=sentinel \
  --set config.redisSentinelUrls='redis://redis-sentinel-0:26379/0\,redis://redis-sentinel-1:26379/0' \
  --set config.redisSentinelMasterName=mymaster \
  --set config.redisUrl='redis://redis-master:6379/0' \
  >"$rendered"

grep -q 'kind: HorizontalPodAutoscaler' "$rendered"
grep -q 'SESSION_CONNECTION_TOKEN_STORE: "redis"' "$rendered"
grep -q 'REDIS_MODE: "sentinel"' "$rendered"
grep -q 'DATABASE_READ_REPLICA_URL:' "$rendered"
grep -q 'postgres-read:5432/janusgate' "$rendered"

printf 'Phase 5 HA config smoke passed: Redis token store, HPA, and read-replica rendering verified.\n'
