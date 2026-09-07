#!/usr/bin/env bash
# JanusGate Cloud Agent start: per-boot reconciliation of local services.
# Brings up PostgreSQL + Redis, ensures the app role/database exist, and applies
# database migrations. Idempotent: safe to run on every boot / re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

# ── PostgreSQL 16 ──────────────────────────────────────────────────────────
echo "[start] starting PostgreSQL"
sudo pg_ctlcluster 16 main start 2>/dev/null || sudo service postgresql start 2>/dev/null || true
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q 2>/dev/null; then break; fi
  sleep 1
done
sudo -u postgres pg_isready || { echo "[start] PostgreSQL failed to become ready" >&2; exit 1; }

echo "[start] ensuring role and database"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='janusgate'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE janusgate LOGIN PASSWORD 'janusgate';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='janusgate'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE janusgate OWNER janusgate;"

# ── Redis ──────────────────────────────────────────────────────────────────
echo "[start] starting Redis"
sudo service redis-server start 2>/dev/null || redis-server --daemonize yes 2>/dev/null || true
for _ in $(seq 1 15); do
  if redis-cli ping >/dev/null 2>&1; then break; fi
  sleep 1
done
redis-cli ping >/dev/null 2>&1 || { echo "[start] Redis failed to become ready" >&2; exit 1; }

# ── Database migrations ────────────────────────────────────────────────────
echo "[start] applying Alembic migrations"
cd "$REPO_ROOT/backend"
ln -sf ../.env .env
uv run alembic upgrade head

echo "[start] ready"
