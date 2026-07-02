#!/usr/bin/env bash
set -euo pipefail

compose_project="${COMPOSE_PROJECT_NAME:-janusgate-phase3-smoke}"
health_url="${JANUSGATE_HEALTH_URL:-http://localhost:8000/health}"

export COMPOSE_PROJECT_NAME="$compose_project"

created_env_file="0"

cleanup() {
  docker compose down -v --remove-orphans
  if [ "$created_env_file" = "1" ]; then
    rm -f .env
  fi
}

trap cleanup EXIT

if [ ! -f .env ]; then
  created_env_file="1"
  {
    echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-janusgate-smoke-postgres}"
    echo "SECRET_KEY=${SECRET_KEY:-janusgate-smoke-secret-key-janusgate-smoke-secret-key-32}"
  } > .env
fi

docker compose up --build -d backend

for _ in $(seq 1 30); do
  if curl -fsS "$health_url"; then
    printf '\nPhase 3 Compose health smoke passed: %s\n' "$health_url"
    exit 0
  fi
  sleep 2
done

docker compose ps
docker compose logs --no-color backend
printf 'Phase 3 Compose health smoke failed: %s\n' "$health_url" >&2
exit 1
